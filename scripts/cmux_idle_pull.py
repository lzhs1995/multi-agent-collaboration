#!/usr/bin/env python3
"""Executor idle pull: never wait silently for a busy supervisor.

After the original completion callback returns (confirmed or not), the executor
records one durable idle request in a per-workspace inbox. The request is
file-only: it sends no terminal input, so a busy or occupied supervisor compose
cannot lose it and it cannot overwrite a draft. The supervisor's Stop hook
refuses to end a turn while a request is pending, so Codex being busy delays the
next dispatch instead of silently ending the collaboration.

A request is settled by either a CONFIRMED task dispatch from that supervisor
to that executor started after the request time, or an explicit supervisor ack
with a reason (e.g. WAITING_DEPENDENCY). The ack is bound to the exact request
sha and is written only when the live CLI caller is the addressed supervisor.

Recording also starts cmux_idle_push, which re-asks the supervisor with new
marked STATUS messages on a non-decreasing ladder until it answers; the
supervisor Stop block only fires at turn end, so a long Codex turn still hears
the executor.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile

ACTIVE_DIR = Path("/tmp/multi-agent-collaboration/_active")


def state_root():
    # Resolved per call so a private HOME isolates tests and the hook alike.
    return Path.home() / ".local/state/multi-agent-collaboration"


def inbox_root():
    return state_root() / "idle-requests-v1"


def _uuid(value):
    if not isinstance(value, str) or len(value) != 36 or value.count("-") != 4:
        raise ValueError("uuid required")
    return value.upper()


def _atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def request_path(workspace, executor):
    return inbox_root() / _uuid(workspace) / (_uuid(executor) + ".json")


def ack_path(workspace, executor):
    return inbox_root() / _uuid(workspace) / (_uuid(executor) + ".ack.json")


def command_for(task_pack):
    """The one exact command the closeout guard admits after a returned callback."""
    return shlex.join(["rtk", "proxy", str(Path(__file__).resolve()), "--task-pack", str(task_pack)])


def _roles(task_pack):
    pack_path = Path(task_pack)
    if not pack_path.is_absolute() or pack_path.is_symlink():
        raise ValueError("absolute task pack required")
    raw = pack_path.read_bytes()
    pack = json.loads(raw)
    root = pack_path.parent
    marker = None
    for path in sorted(list(ACTIVE_DIR.glob("*/*.json")) + list(ACTIVE_DIR.glob("*.json"))):
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if value.get("task_id") == pack.get("task_id") and value.get("artifact_root") == str(root):
            marker = value
            break
    role_map = root / "role-map.json"
    if marker is None and role_map.is_file():
        marker = json.loads(role_map.read_text())
    if not isinstance(marker, dict):
        raise ValueError("no marker or role map binds this task")
    sups = [p for p in marker.get("participants", []) if p.get("role") == "supervisor"]
    if len(sups) != 1:
        raise ValueError("exactly one supervisor required")
    return pack, raw, _uuid(marker.get("workspace_uuid")), _uuid(pack["executor_uuid"]), sups[0]


def record(task_pack, now=None):
    pack, raw, workspace, executor, sup = _roles(task_pack)
    root = Path(task_pack).parent
    receipt = Path(pack["completion_receipt"])
    report = Path(pack["report"])
    if receipt.parent != root or report.parent != root:
        raise ValueError("report/receipt must be bound under artifact root")
    if not report.is_file() or not report.read_bytes().strip():
        raise ValueError("write the report and call the original callback first")
    attempts = receipt.with_name(receipt.stem + "-attempts")
    if not os.path.lexists(receipt) and not sorted(attempts.glob("attempt-*.json")):
        raise ValueError("original callback attempt required before idle pull")
    confirmed = False
    if receipt.is_file():
        try:
            confirmed = json.loads(receipt.read_text()).get("confirmed") is True
        except ValueError:
            confirmed = False
    at = time_now(now)
    value = dict(
        kind="EXECUTOR_IDLE_TASK_REQUEST", schema=1, at_epoch=at,
        at=dt.datetime.fromtimestamp(at, dt.timezone.utc).isoformat(),
        workspace_uuid=workspace, executor_uuid=executor,
        supervisor_uuid=_uuid(sup.get("surface_uuid")), supervisor_ref=sup.get("surface_ref"),
        last_task_id=pack["task_id"], task_pack_sha256=_sha(raw),
        report=str(report), report_sha256=_sha(report.read_bytes()),
        callback_receipt_confirmed=confirmed,
        request="Executor idle; dispatch next bounded task pack or ack with a reason.",
        terminal_input_sent=False)
    path = request_path(workspace, executor)
    _atomic(path, value)
    # Durable copy in the task evidence directory (the documented notify channel).
    _atomic(root / "executor-idle-request.json", value)
    return dict(value, path=str(path))


def time_now(now=None):
    import time
    return float(now) if now is not None else time.time()


def _attempt_started(value):
    """Task attempts carry started_at_epoch; message attempts only their first paste."""
    if "started_at_epoch" in value:
        return float(value["started_at_epoch"])
    return min(float(e["at_epoch"]) for e in value["events"] if e.get("phase") == "PASTE_INTENT")


def journal_answered(folder, req):
    """A CONFIRMED journaled delivery from this supervisor to this executor after the request.

    Unconfirmed attempts (NO_INPUT, POST_ENTER_OBSERVATION) may never have reached
    the executor, so they leave the request pending; the journal promotes a late
    delivery to CONFIRMED on reconcile.
    """
    root = state_root() / folder
    if not root.is_dir():
        return False
    for attempt in root.glob("*/attempt-*.json"):
        try:
            value = json.loads(attempt.read_text())
            ident = value["binding"]["identity"]
            if (value.get("phase") == "CONFIRMED"
                    and str(ident.get("target_surface_uuid", "")).upper() == req["executor_uuid"]
                    and str(ident.get("caller_surface_uuid", "")).upper() == req["supervisor_uuid"]
                    and str(ident.get("workspace_uuid", "")).upper() == req["workspace_uuid"]
                    and _attempt_started(value) > float(req["at_epoch"])):
                return True
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return False


def _dispatched_after(req):
    return journal_answered("task-dispatch-v1", req)


def pending(workspace, supervisor):
    """Unsettled requests addressed to this supervisor. Malformed requests stay pending."""
    out = []
    folder = inbox_root() / _uuid(workspace)
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("*.json")):
        if path.name.endswith(".ack.json") or path.name.startswith(".tmp-"):
            continue
        raw = path.read_bytes()
        try:
            req = json.loads(raw)
            if _uuid(req.get("supervisor_uuid")) != _uuid(supervisor):
                continue
        except ValueError:
            out.append(dict(path=str(path), malformed=True))
            continue
        ack = path.with_name(path.stem + ".ack.json")
        try:
            done = json.loads(ack.read_text()).get("request_sha256") == _sha(raw)
        except (OSError, ValueError):
            done = False
        if not done and not _dispatched_after(req):
            out.append(dict(req, path=str(path), request_sha256=_sha(raw)))
    return out


def any_requests():
    """Cheap jurisdiction check before caller discovery."""
    root = inbox_root()
    try:
        return root.is_dir() and any(p for p in root.glob("*/*.json")
                                     if not p.name.endswith(".ack.json"))
    except OSError:
        return False


def fresh_for(terminal, workspace, executor):
    """Executor side: an idle request exists for exactly this frozen report."""
    try:
        req = json.loads(request_path(workspace, executor).read_text())
        return (req.get("last_task_id") == terminal["task_id"]
                and req.get("report_sha256") == terminal["report_sha256"]
                and req.get("executor_uuid") == _uuid(executor)
                and req.get("workspace_uuid") == _uuid(workspace))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def ready_for(terminal, workspace, executor):
    """Handoff allowed: fresh request AND someone keeps asking (live pusher or answered)."""
    if not fresh_for(terminal, workspace, executor):
        return False
    import cmux_idle_push
    return cmux_idle_push.alive(_uuid(workspace), _uuid(executor))


def supervisor_message(waiting):
    me = str(Path(__file__).resolve())
    lines = ["EXECUTOR_IDLE_REQUEST_PENDING: an executor is idle and waiting for you. "
             "Do not end the turn leaving it stranded. For each request, either dispatch "
             "the next bounded task pack via cmux_bridge.submit_task_pack (a new dispatch "
             "attempt settles it), or ack with a concrete reason:"]
    for req in waiting:
        if req.get("malformed"):
            lines.append("  malformed request (inspect, do not delete): " + req["path"])
            continue
        lines.append("  executor %s (last task %s, report %s)" % (
            req["executor_uuid"], req.get("last_task_id"), req.get("report")))
        lines.append("    " + shlex.join(["rtk", "proxy", me, "--ack", req["executor_uuid"],
                                          "--workspace", req["workspace_uuid"],
                                          "--supervisor", req["supervisor_uuid"],
                                          "--reason", "<WAITING_DEPENDENCY: ...>"]))
    return "\n".join(lines)


def _authenticated_caller(executor):
    """Live (workspace, surface) of the CLI caller, resolved like bridge transport.

    The executor is the binding target, so the caller must be another terminal in
    the same workspace; CLI arguments never supply identity.
    """
    import cmux_workspace_guard as guard
    try:
        identity, tree, env, _proof = guard.caller_snapshot()
        binding = guard.resolve_snapshot(identity, tree, _uuid(executor), env=env)
    except guard.WorkspaceScopeError as exc:
        raise ValueError("ACK_CALLER_UNRESOLVED: " + str(exc)) from exc
    return binding["workspace_uuid"], binding["caller_surface_uuid"]


def ack(workspace, supervisor, executor, reason, now=None):
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("ack reason required")
    path = request_path(workspace, executor)
    raw = path.read_bytes()
    req = json.loads(raw)
    if _uuid(req["supervisor_uuid"]) != _uuid(supervisor):
        raise ValueError("request is addressed to another supervisor")
    # Only the addressed supervisor's own surface may clear its Stop gate.
    if _authenticated_caller(executor) != (_uuid(req["workspace_uuid"]), _uuid(req["supervisor_uuid"])):
        raise ValueError("ACK_CALLER_MISMATCH: caller is not the addressed supervisor")
    at = time_now(now)
    value = dict(request_sha256=_sha(raw), supervisor_uuid=_uuid(supervisor), reason=reason.strip(),
                 at_epoch=at, at=dt.datetime.fromtimestamp(at, dt.timezone.utc).isoformat())
    _atomic(ack_path(workspace, executor), value)
    return value


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--task-pack")
    p.add_argument("--list", action="store_true")
    p.add_argument("--ack", metavar="EXECUTOR_UUID")
    p.add_argument("--workspace")
    p.add_argument("--supervisor")
    p.add_argument("--reason")
    a = p.parse_args(argv)
    try:
        if a.task_pack:
            result = record(a.task_pack)
            # 文件请求之外再起后台催办器：反复问主管，直到派发/消息/ack
            import cmux_idle_push
            result["pusher"] = cmux_idle_push.spawn(result["workspace_uuid"], result["executor_uuid"])
        elif a.list:
            result = pending(a.workspace, a.supervisor)
        elif a.ack:
            result = ack(a.workspace, a.supervisor, a.ack, a.reason)
        else:
            p.error("choose --task-pack, --list or --ack")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("IDLE_PULL_FAILED: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
