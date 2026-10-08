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

Bounds: tiers fire at 10/30/60 minutes of idle time, with non-decreasing gaps
(10, 20, 30 minutes) and at most three escalations per idle clock. A tier is
never due earlier than its gap after the previous record, so a late first
escalation cannot cascade into a burst. After the last tier the guard allows
turn-end; the executor reports the block to the user instead of sending more.

Escalations are recorded whatever the transport outcome (CONFIRMED,
SUBMITTED_UNCONFIRMED, NO_INPUT, TRANSPORT_ERROR). The record documents that
the executor acted; it never claims the supervisor read the message.
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

# Cumulative idle seconds at which tier 1, 2, 3 become due.
LADDER_SECONDS = (600, 1800, 3600)
MAX_TIERS = len(LADDER_SECONDS)
ACTIVE_DIR = Path("/tmp/multi-agent-collaboration/_active")


def gaps(ladder=LADDER_SECONDS) -> tuple[int, ...]:
    """Minimum spacing before each tier; the first gap is its own threshold."""
    return tuple(b - a for a, b in zip((0,) + tuple(ladder), ladder))


def _state_root() -> Path:
    return Path.home() / ".local/state/multi-agent-collaboration/executor-idle-escalation-v1"


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
    for tier in range(1, MAX_TIERS + 1):
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
    try:
        if pack_path.is_file():
            starts.append(pack_path.stat().st_mtime)
    except OSError:
        pass
    starts = [s for s in starts if s is not None]
    if not starts:
        return dict(status, reason="no timezone-aware supervisor activity timestamp")
    clock_start = max(starts)
    directory = _record_dir(marker, executor_uuid, clock_start)
    records = _records(directory)
    status.update(applicable=True, reason="armed executor awaiting dispatch",
                  clock_start=clock_start, idle_seconds=max(0.0, now - clock_start),
                  record_dir=str(directory), records=records, exhausted=False,
                  due_tier=None, next_due_at=None)
    if len(records) >= MAX_TIERS:
        status["exhausted"] = True
        return status
    tier = len(records) + 1
    due_at = clock_start + LADDER_SECONDS[tier - 1]
    if records:
        due_at = max(due_at, float(records[-1]["recorded_at"]) + gaps()[tier - 1])
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
            f"no finalized task pack for {minutes} min. Escalation tier "
            f"{status['due_tier']}/{MAX_TIERS} (new message, not a resend). Please either "
            "dispatch the handshake and task pack, or reply with a new scope or cancel. "
            f"Notice file: {status['notice']}")


def escalate(marker: dict[str, Any], executor_uuid: str, bridge: Any,
             now: float | None = None) -> dict[str, Any]:
    """Send exactly one escalation for the currently due tier, then record it."""
    status = idle_status(marker, executor_uuid, now)
    if not status["applicable"]:
        return dict(result="NOT_APPLICABLE", reason=status["reason"])
    if status["exhausted"]:
        return dict(result="EXHAUSTED", records=len(status["records"]))
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
                  idle_seconds=status["idle_seconds"], recorded_at=time.time(),
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
        if status["exhausted"]:
            return 5, dict(result="EXHAUSTED_REPORT_BLOCKED_TO_USER")
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
    for name in ("status", "escalate", "wait"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--task-id", required=True)
        cmd.add_argument("--executor-uuid", default=None)
        if name == "wait":
            cmd.add_argument("--max-seconds", type=float, default=1800)
            cmd.add_argument("--poll-seconds", type=float, default=30)
    args = parser.parse_args(argv)
    executor = (args.executor_uuid or _caller_uuid()).upper()
    if args.command == "wait":
        code, result = wait(args.task_id, executor, args.max_seconds, args.poll_seconds)
        print(json.dumps(result, default=str))
        return code
    marker = find_marker(args.task_id, executor)
    if marker is None:
        print(json.dumps(dict(result="NO_UNIQUE_MARKER", task_id=args.task_id,
                              executor_uuid=executor)))
        return 3
    if args.command == "status":
        print(json.dumps(idle_status(marker, executor), indent=2, default=str))
        return 0
    result = escalate(marker, executor, _load_bridge())
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["result"] in ("ESCALATED", "EXHAUSTED") else 4


if __name__ == "__main__":
    raise SystemExit(main())
