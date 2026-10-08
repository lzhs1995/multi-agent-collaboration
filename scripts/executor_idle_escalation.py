#!/usr/bin/env python3
"""Executor idle escalation: an armed executor may not dead-wait for dispatch.

Incident 2026-10-08 (r20): a marker was armed for an executor, no handshake or
finalized task pack followed for two hours, and the only guard on turn-end
(the Stop hook) allowed every idle turn because "no finalized executor task
pack is active". The executor's single task request sat queued behind the
supervisor's long busy turn, and the protocol forbade resending it, so the
executor waited with no bounded next step.

This module adds that step. While an armed executor has no finalized pack, its
idle clock runs from the latest supervisor-side activity (marker armed_at /
last_activity_at, handshake-receipt created_at, draft pack mtime). When the
clock crosses a ladder threshold, the Stop guard blocks turn-end until this
executor records one escalation for that tier. Each tier is a NEW ordinary
message with its own marker sent through the journaled bridge (never a resend
of an earlier message) plus a notice file in the escalation state directory,
so a supervisor whose input is unavailable can still pull it.

Repeat until the supervisor replies (user directive 2026-10-08): the first
escalation is due after FIRST_SECONDS of idle time and every later one
REPEAT_SECONDS after the previous record. There is no tier cap; the ladder
ends only when the supervisor replies (handshake receipt, draft or finalized
pack, marker activity, or an explicit `ack` from the supervisor surface) or
the marker expires (ttl). A tier is never due earlier than REPEAT_SECONDS after
the previous record, so a late escalation cannot cascade into a burst.

Escalations are recorded whatever the transport outcome (CONFIRMED,
SUBMITTED_UNCONFIRMED, NO_INPUT, TRANSPORT_ERROR). The record documents that
the executor acted; it never claims the supervisor read the message. A
transport-confirmed escalation is not a reply; only supervisor-side activity
stops the repeats.

Two processes, one sender. `watch` is the persistent background sender: it
escalates whenever due, independent of the executor's turns, and keeps going
across replies (an ack only defers it). `pursue` is the executor's foreground
step: it (re)starts a dead watcher, then waits until the supervisor replies or
PURSUE_MAX_SECONDS elapse. It never sends itself.

The session does not end while unanswered: past FIRST_SECONDS of idle time the
Stop guard blocks turn-end, including on Stop-hook reentry, and names the
`pursue` command. The block lifts only on a reply (the idle clock restarts),
dispatch, marker expiry, or a user interrupt. Supervisor writes anywhere in the
artifact root count as a reply, so a supervisor preflight that failed against
the busy executor still releases its turn for the retry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Idle seconds before the first escalation, and the spacing of every repeat
# (user directive 2026-10-08: ask again every 60 s until the supervisor replies).
FIRST_SECONDS = 60
REPEAT_SECONDS = 60
# Record files are read contiguously; this only bounds the directory scan
# (a 6 h ttl at REPEAT_SECONDS needs at most 361).
MAX_RECORD_SCAN = 10_000
# A watcher heartbeat older than this is dead whatever its pid says.
WATCHER_STALE_SECONDS = 90
# One foreground pursue call stays under common 10-minute tool timeouts.
PURSUE_MAX_SECONDS = 540
ACTIVE_DIR = Path("/tmp/multi-agent-collaboration/_active")


def gap(tier: int) -> int:
    """Minimum idle/record spacing before ``tier`` (1-based)."""
    return FIRST_SECONDS if tier == 1 else REPEAT_SECONDS


def _state_root() -> Path:
    return Path.home() / ".local/state/multi-agent-collaboration/executor-idle-escalation-v1"


def _task_key(marker: dict[str, Any], executor_uuid: str) -> str:
    """Stable across idle clocks: names the watcher and supervisor ack files."""
    return hashlib.sha256(json.dumps([
        str(marker.get("task_id") or ""), str(marker.get("collaboration_id") or ""),
        executor_uuid.upper()]).encode()).hexdigest()


def _ack_path(marker: dict[str, Any], executor_uuid: str) -> Path:
    return _state_root() / "ack" / f"{_task_key(marker, executor_uuid)}.json"


def _watcher_path(marker: dict[str, Any], executor_uuid: str) -> Path:
    return _state_root() / "watchers" / f"{_task_key(marker, executor_uuid)}.json"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _epoch(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None  # A bare timestamp has no timezone; never guess one.
    return parsed.timestamp()


def _marker_fresh(marker: dict[str, Any]) -> bool:
    """Same TTL rule as the Stop guard: an expired marker is disarmed."""
    armed, ttl = _epoch(marker.get("armed_at")), marker.get("ttl_seconds")
    if armed is None or type(ttl) not in (int, float):
        return True
    return time.time() - armed <= ttl


def _executor_row(marker: dict[str, Any], executor_uuid: str) -> dict[str, Any] | None:
    rows = [row for row in marker.get("participants", []) if isinstance(row, dict)
            and str(row.get("role", "")).startswith("executor")
            and str(row.get("surface_uuid", "")).upper() == executor_uuid.upper()]
    return rows[0] if len(rows) == 1 else None


def _supervisor_row(marker: dict[str, Any]) -> dict[str, Any] | None:
    rows = [row for row in marker.get("participants", []) if isinstance(row, dict)
            and row.get("role") == "supervisor" and row.get("surface_uuid")]
    return rows[0] if len(rows) == 1 else None


def _record_dir(marker: dict[str, Any], executor_uuid: str, clock_start: float) -> Path:
    """One ladder per (task, collaboration, executor, idle clock start).

    New supervisor activity moves the clock start and so opens a fresh ladder.
    """
    key = hashlib.sha256(json.dumps([
        str(marker.get("task_id") or ""), str(marker.get("collaboration_id") or ""),
        executor_uuid.upper(), int(clock_start)]).encode()).hexdigest()
    return _state_root() / key


def _records(directory: Path) -> list[dict[str, Any]]:
    """Contiguous tier records 1..k; a gap ends the ladder read (fail closed)."""
    out: list[dict[str, Any]] = []
    for tier in range(1, MAX_RECORD_SCAN + 1):
        value = _read_json(directory / f"tier-{tier}.json")
        if not isinstance(value, dict) or value.get("tier") != tier \
                or type(value.get("recorded_at")) not in (int, float):
            break
        out.append(value)
    return out


def idle_status(marker: dict[str, Any], executor_uuid: str,
                now: float | None = None) -> dict[str, Any]:
    """Read-only idle ladder state for one armed executor."""
    now = time.time() if now is None else now
    status: dict[str, Any] = dict(applicable=False, task_id=marker.get("task_id"))
    if not executor_uuid or _executor_row(marker, executor_uuid) is None:
        return dict(status, reason="caller is not this task's executor")
    if _supervisor_row(marker) is None:
        return dict(status, reason="marker has no unique supervisor")
    raw_root = marker.get("artifact_root")
    root = Path(raw_root) if isinstance(raw_root, str) and raw_root else None
    if root is None or not root.is_absolute():
        return dict(status, reason="marker has no absolute artifact_root")
    pack_path = root / "task-pack.json"
    pack = _read_json(pack_path)
    if isinstance(pack, dict) and pack.get("draft") is False:
        return dict(status, reason="finalized task pack present; callback rules apply")
    starts = [_epoch(marker.get("armed_at")), _epoch(marker.get("last_activity_at"))]
    receipt = _read_json(root / "handshake-receipt.json")
    if isinstance(receipt, dict):
        rows = receipt.get("executors", [receipt])
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict):
                starts.append(_epoch(row.get("created_at")))
    # Before dispatch only the supervisor writes the artifact root (preflight
    # evidence, receipts, draft pack), so any new top-level file is activity,
    # including a preflight that failed against this busy executor.
    try:
        for entry in root.iterdir():
            if not entry.name.startswith(".") and entry.is_file():
                starts.append(entry.stat().st_mtime)
    except OSError:
        pass
    ack = _read_json(_ack_path(marker, executor_uuid))
    if isinstance(ack, dict) and type(ack.get("acked_at")) in (int, float):
        # A supervisor reply restarts the clock; an optional hold defers it.
        starts.append(float(ack["acked_at"]) + float(ack.get("hold_seconds") or 0))
    starts = [s for s in starts if s is not None]
    if not starts:
        return dict(status, reason="no timezone-aware supervisor activity timestamp")
    clock_start = max(starts)
    directory = _record_dir(marker, executor_uuid, clock_start)
    records = _records(directory)
    status.update(applicable=True, reason="armed executor awaiting dispatch",
                  clock_start=clock_start, idle_seconds=max(0.0, now - clock_start),
                  record_dir=str(directory), records=records, due_tier=None,
                  next_due_at=None)
    tier = len(records) + 1
    due_at = clock_start + FIRST_SECONDS
    if records:
        due_at = max(due_at, float(records[-1]["recorded_at"]) + gap(tier))
    status["next_due_at"] = due_at
    if now >= due_at:
        status["due_tier"] = tier
    return status


def find_marker(task_id: str, executor_uuid: str,
                active_dir: Path | None = None) -> dict[str, Any] | None:
    """The unique armed marker naming this task and executor (v2 then v1)."""
    active_dir = ACTIVE_DIR if active_dir is None else active_dir
    found = []
    for pattern in ("*/*.json", "*.json"):
        for path in sorted(active_dir.glob(pattern)):
            if path.name.startswith("."):
                continue
            marker = _read_json(path)
            if (isinstance(marker, dict) and marker.get("task_id") == task_id
                    and _marker_fresh(marker)
                    and _executor_row(marker, executor_uuid) is not None):
                found.append(marker)
    return found[0] if len(found) == 1 else None


def _message_outcome(bridge: Any, supervisor_uuid: str, marker: str) -> str:
    """Classify the journaled ordinary message read-only; never resend."""
    try:
        caller = bridge.pin_workspace(supervisor_uuid)["caller_surface_uuid"]
    except Exception:  # identity unavailable: no journal could have been written
        return "TRANSPORT_ERROR"
    key = hashlib.sha256(json.dumps([caller, marker], sort_keys=True).encode()).hexdigest()
    journal = (Path.home() / ".local/state/multi-agent-collaboration"
               / "message-dispatch-v1" / key)
    if (journal / "receipt.json").exists():
        return "CONFIRMED"
    phases = [(_read_json(p) or {}).get("phase") for p in sorted(journal.glob("attempt-*.json"))]
    if not phases or all(p == "NO_INPUT" for p in phases):
        return "NO_INPUT"
    return "SUBMITTED_UNCONFIRMED"


def _write_exclusive(path: Path, value: dict[str, Any]) -> None:
    """Create once; a second writer for the same tier fails instead of resending."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)


def _replace(path: Path, value: dict[str, Any]) -> None:
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True))
    os.replace(tmp, path)


def escalation_text(marker: dict[str, Any], status: dict[str, Any], tag: str) -> str:
    executor = _executor_row(marker, status["executor_uuid"]) or {}
    minutes = int(status["idle_seconds"] // 60)
    return (f"{tag} | executor {executor.get('surface_ref', '?')} "
            f"({status['executor_uuid']}) is idle on armed task {marker.get('task_id')}: "
            f"no finalized task pack for {minutes} min. Escalation #{status['due_tier']} "
            f"(new message, not a resend; repeats every {REPEAT_SECONDS} s until "
            "you reply). Please dispatch the handshake and task pack, or reply with a new "
            "scope or cancel. To pause the repeats without dispatching, run from your "
            f"surface: python3 -B {Path(__file__).resolve()} ack --task-id "
            f"{marker.get('task_id')} --executor-uuid {status['executor_uuid']} "
            f"[--hold-seconds N]. If a preflight against this executor fails as busy "
            "(COMPOSE_OCCUPIED), just retry: any write to the artifact root counts as "
            "your reply and the executor ends its turn within seconds. "
            f"Notice file: {status['notice']}")


def escalate(marker: dict[str, Any], executor_uuid: str, bridge: Any,
             now: float | None = None) -> dict[str, Any]:
    """Send exactly one escalation for the currently due tier, then record it."""
    status = idle_status(marker, executor_uuid, now)
    if not status["applicable"]:
        return dict(result="NOT_APPLICABLE", reason=status["reason"])
    if status["due_tier"] is None:
        return dict(result="NOT_DUE", next_due_at=status["next_due_at"])
    tier = status["due_tier"]
    directory = Path(status["record_dir"])
    stamp = datetime.fromtimestamp(time.time(), timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tag = f"EXECUTOR_IDLE_ESCALATION_T{tier}_{executor_uuid[:8].upper()}_{stamp}"
    notice = directory / f"notice-tier-{tier}.json"
    status.update(executor_uuid=executor_uuid, notice=str(notice))
    text = escalation_text(marker, status, tag)
    record_path = directory / f"tier-{tier}.json"
    record = dict(tier=tier, marker=tag, task_id=marker.get("task_id"),
                  collaboration_id=marker.get("collaboration_id"),
                  executor_uuid=executor_uuid, clock_start=status["clock_start"],
                  idle_seconds=status["idle_seconds"],
                  recorded_at=time.time() if now is None else now,
                  outcome="SENDING", notice=str(notice))
    # The record is the dispatch lock: it exists before any terminal input, so
    # a crash or a concurrent caller can never produce a second send for a tier.
    _write_exclusive(record_path, record)
    _write_exclusive(notice, dict(kind="EXECUTOR_IDLE_ESCALATION", text=text, **record))
    supervisor = _supervisor_row(marker)["surface_uuid"]
    error = None
    try:
        bridge.submit_text(supervisor, text, marker=tag)
    except Exception as exc:  # outcome comes from the journal, not the exception
        error = f"{type(exc).__name__}: {exc}"[:500]
    record.update(outcome=_message_outcome(bridge, supervisor, tag), error=error)
    _replace(record_path, record)
    return dict(result="ESCALATED", **record)


def _caller_uuid() -> str:
    return str(os.environ.get("CMUX_SURFACE_ID") or "").upper()


class _UnavailableBridge:
    """An import failure is a recorded transport outcome, not an endless block."""

    def __init__(self, error: Exception):
        self.error = error

    def pin_workspace(self, surface):
        raise self.error

    def submit_text(self, surface, text, marker=None):
        raise self.error


def _load_bridge() -> Any:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import cmux_bridge
    except Exception as exc:
        return _UnavailableBridge(exc)
    return cmux_bridge


MAX_HOLD_SECONDS = 7200


def ack(marker: dict[str, Any], executor_uuid: str, caller_uuid: str,
        hold_seconds: float = 0, now: float | None = None) -> dict[str, Any]:
    """Supervisor reply: restart the executor's idle clock (optionally later).

    Only the marker's supervisor surface may ack; an executor cannot silence
    its own repeats by declaring that the supervisor answered.
    """
    supervisor = _supervisor_row(marker)
    if supervisor is None or str(supervisor["surface_uuid"]).upper() != caller_uuid.upper():
        return dict(result="REFUSED", reason="only the marker's supervisor surface may ack")
    if _executor_row(marker, executor_uuid) is None:
        return dict(result="REFUSED", reason="executor is not a participant of this marker")
    hold = max(0.0, min(float(hold_seconds), MAX_HOLD_SECONDS))
    value = dict(task_id=marker.get("task_id"), executor_uuid=executor_uuid.upper(),
                 supervisor_uuid=caller_uuid.upper(),
                 acked_at=time.time() if now is None else now, hold_seconds=hold)
    path = _ack_path(marker, executor_uuid)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _replace(path, value)
    return dict(result="ACKED", path=str(path), **value)


def _pid_alive(pid: Any) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def watcher_alive(marker: dict[str, Any], executor_uuid: str,
                  now: float | None = None) -> tuple[bool, str]:
    """A live watcher has a running pid and a heartbeat newer than the stale bound."""
    now = time.time() if now is None else now
    value = _read_json(_watcher_path(marker, executor_uuid))
    if not isinstance(value, dict):
        return False, "no watcher record"
    beat = value.get("heartbeat_at")
    if type(beat) not in (int, float) or now - beat > WATCHER_STALE_SECONDS:
        return False, "watcher heartbeat is stale"
    if not _pid_alive(value.get("pid")):
        return False, f"watcher pid {value.get('pid')} is not running"
    return True, f"watcher pid {value['pid']} alive"


def watch(task_id: str, executor_uuid: str, poll_seconds: float, bridge: Any = None,
          clock=time.time, sleep=time.sleep,
          max_iterations: int | None = None) -> tuple[int, dict[str, Any]]:
    """Persistent loop: escalate whenever due, exit when the supervisor replies.

    Bounded by the marker ttl (find_marker drops an expired marker). A second
    watcher for the same task exits immediately instead of doubling sends.
    """
    marker = find_marker(task_id, executor_uuid)
    if marker is None:
        return 3, dict(result="NO_UNIQUE_MARKER")
    path = _watcher_path(marker, executor_uuid)
    alive, _ = watcher_alive(marker, executor_uuid, clock())
    if alive and (_read_json(path) or {}).get("pid") != os.getpid():
        return 8, dict(result="ALREADY_WATCHING", watcher=str(path))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    started = clock()
    sent: list[dict[str, Any]] = []
    iterations = 0
    bridge = _load_bridge() if bridge is None else bridge
    try:
        while True:
            _replace(path, dict(pid=os.getpid(), task_id=task_id,
                                executor_uuid=executor_uuid.upper(),
                                started_at=started, heartbeat_at=clock(),
                                escalations=len(sent)))
            marker = find_marker(task_id, executor_uuid)
            if marker is None:
                return 3, dict(result="MARKER_GONE_OR_EXPIRED", escalations=sent)
            status = idle_status(marker, executor_uuid, clock())
            if not status["applicable"]:
                return 0, dict(result="DISPATCH_ARRIVED_OR_NOT_APPLICABLE",
                               reason=status["reason"], escalations=sent)
            if status["due_tier"] is not None:
                try:
                    result = escalate(marker, executor_uuid, bridge, clock())
                except FileExistsError:  # a concurrent escalate owns this tier
                    result = dict(result="TIER_LOCKED", tier=status["due_tier"])
                sent.append({k: result.get(k) for k in ("result", "tier", "marker", "outcome")})
                if result["result"] == "ESCALATED":
                    continue
                # Locked or unreadable tier: back off a full poll, never spin.
                sleep(max(1.0, poll_seconds))
                continue
            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                return 7, dict(result="ITERATIONS_ELAPSED", escalations=sent)
            # Heartbeat stays well inside WATCHER_STALE_SECONDS.
            sleep(max(1.0, min(poll_seconds, WATCHER_STALE_SECONDS / 3,
                               status["next_due_at"] - clock())))
    finally:
        if (_read_json(path) or {}).get("pid") == os.getpid():
            path.unlink(missing_ok=True)


def spawn_watcher(task_id: str, executor_uuid: str) -> int:
    """Start a detached background watcher; it outlives this turn and session."""
    import subprocess
    log = _state_root() / "watchers" / f"{task_id}.log"
    log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(log, "a") as handle:
        proc = subprocess.Popen(
            [sys.executable, "-B", str(Path(__file__).resolve()), "watch",
             "--task-id", task_id, "--executor-uuid", executor_uuid],
            stdin=subprocess.DEVNULL, stdout=handle, stderr=handle,
            start_new_session=True)
    return proc.pid


def pursue(task_id: str, executor_uuid: str, max_seconds: float = PURSUE_MAX_SECONDS,
           poll_seconds: float = 10, clock=time.time, sleep=time.sleep,
           spawn=spawn_watcher) -> tuple[int, dict[str, Any]]:
    """Foreground step: keep a watcher alive and return only on a reply.

    Exit 0 = reply or dispatch (end the turn and handle it), 3 = marker gone,
    9 = no reply within max_seconds (the Stop guard will ask for another call).
    """
    deadline = clock() + max(0.0, min(max_seconds, PURSUE_MAX_SECONDS))
    marker = find_marker(task_id, executor_uuid)
    if marker is None:
        return 3, dict(result="NO_UNIQUE_MARKER")
    first = idle_status(marker, executor_uuid, clock())
    if not first["applicable"]:
        return 0, dict(result="DISPATCH_ARRIVED_OR_NOT_APPLICABLE", reason=first["reason"])
    clock_start, spawned = first["clock_start"], []
    while True:
        if not watcher_alive(marker, executor_uuid, clock())[0]:
            spawned.append(spawn(task_id, executor_uuid))
        status = idle_status(marker, executor_uuid, clock())
        if not status["applicable"]:
            return 0, dict(result="DISPATCH_ARRIVED_OR_NOT_APPLICABLE",
                           reason=status["reason"], spawned=spawned)
        if status["clock_start"] > clock_start:
            return 0, dict(result="SUPERVISOR_REPLIED", clock_start=status["clock_start"],
                           spawned=spawned)
        now = clock()
        if now >= deadline:
            return 9, dict(result="NO_REPLY_YET", escalations=len(status["records"]),
                           idle_seconds=status["idle_seconds"], spawned=spawned)
        sleep(max(1.0, min(poll_seconds, deadline - now)))
        marker = find_marker(task_id, executor_uuid)
        if marker is None:
            return 3, dict(result="MARKER_GONE_OR_EXPIRED", spawned=spawned)


def wait(task_id: str, executor_uuid: str, max_seconds: float, poll_seconds: float,
         clock=time.time, sleep=time.sleep) -> tuple[int, dict[str, Any]]:
    """Bounded local polling that returns as soon as the executor must act."""
    deadline = clock() + max(0.0, max_seconds)
    marker = find_marker(task_id, executor_uuid)
    while True:
        if marker is None:
            return 3, dict(result="NO_UNIQUE_MARKER")
        status = idle_status(marker, executor_uuid, clock())
        if not status["applicable"]:
            return 0, dict(result="DISPATCH_ARRIVED_OR_NOT_APPLICABLE", reason=status["reason"])
        if status["due_tier"] is not None:
            return 6, dict(result="ESCALATION_DUE", tier=status["due_tier"])
        now = clock()
        if now >= deadline:
            return 7, dict(result="WAIT_WINDOW_ELAPSED", next_due_at=status["next_due_at"])
        sleep(max(1.0, min(poll_seconds, status["next_due_at"] - now, deadline - now)))
        marker = find_marker(task_id, executor_uuid)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "escalate", "wait", "watch", "pursue", "ack"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--task-id", required=True)
        cmd.add_argument("--executor-uuid", default=None, required=(name == "ack"))
        if name in ("wait", "watch", "pursue"):
            cmd.add_argument("--poll-seconds", type=float,
                             default=10 if name == "pursue" else 20)
        if name in ("wait", "pursue"):
            cmd.add_argument("--max-seconds", type=float,
                             default=PURSUE_MAX_SECONDS if name == "pursue" else 1800)
        if name == "ack":
            cmd.add_argument("--hold-seconds", type=float, default=0)
    args = parser.parse_args(argv)
    executor = (args.executor_uuid or _caller_uuid()).upper()
    if args.command in ("wait", "watch", "pursue"):
        if args.command == "wait":
            code, result = wait(args.task_id, executor, args.max_seconds, args.poll_seconds)
        elif args.command == "pursue":
            code, result = pursue(args.task_id, executor, args.max_seconds, args.poll_seconds)
        else:
            code, result = watch(args.task_id, executor, args.poll_seconds)
        print(json.dumps(result, default=str))
        return code
    marker = find_marker(args.task_id, executor)
    if marker is None:
        print(json.dumps(dict(result="NO_UNIQUE_MARKER", task_id=args.task_id,
                              executor_uuid=executor)))
        return 3
    if args.command == "status":
        status = idle_status(marker, executor)
        status["watcher"] = watcher_alive(marker, executor)[1]
        print(json.dumps(status, indent=2, default=str))
        return 0
    if args.command == "ack":
        result = ack(marker, executor, _caller_uuid(), args.hold_seconds)
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["result"] == "ACKED" else 4
    result = escalate(marker, executor, _load_bridge())
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["result"] == "ESCALATED" else 4


if __name__ == "__main__":
    raise SystemExit(main())
