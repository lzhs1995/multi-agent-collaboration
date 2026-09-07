#!/usr/bin/env python3
"""Fail-closed executor availability and solo-takeover state machine."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re


STATES = ("ACTIVE", "UNAVAILABLE_BILLING", "SOLO_TAKEOVER", "HANDOFF_READY")
TRANSITIONS = {
    "ACTIVE": {"UNAVAILABLE_BILLING"},
    "UNAVAILABLE_BILLING": {"SOLO_TAKEOVER", "HANDOFF_READY"},
    "SOLO_TAKEOVER": {"HANDOFF_READY"},
    "HANDOFF_READY": {"ACTIVE"},
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def state_path(task_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", task_id)
    root = Path("/tmp/multi-agent-collaboration/executor-availability")
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{safe}.json"


def load(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, state: dict) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def transition(current: dict, target: str, *, executor_surface: str,
               reason: str, handoff: str = "",
               recovery_confirmed: bool = False) -> dict:
    if target not in STATES:
        raise ValueError(f"unknown state: {target}")
    source = current.get("state", "ACTIVE")
    if target not in TRANSITIONS.get(source, set()):
        raise ValueError(f"invalid transition: {source} -> {target}")
    bound = current.get("executor_surface") or executor_surface
    if not bound:
        raise ValueError("executor_surface is required")
    if executor_surface and executor_surface != bound:
        raise ValueError(f"executor continuity violation: bound={bound} requested={executor_surface}")
    if target == "UNAVAILABLE_BILLING" and not reason.strip():
        raise ValueError("billing unavailability requires a reason")
    if target == "HANDOFF_READY" and not handoff.strip():
        raise ValueError("HANDOFF_READY requires an absolute handoff path")
    if target == "HANDOFF_READY" and not Path(handoff).is_absolute():
        raise ValueError("handoff path must be absolute")
    if (source == "UNAVAILABLE_BILLING" and target == "HANDOFF_READY"
            and not recovery_confirmed):
        raise ValueError("direct recovery requires explicit user confirmation")
    if target == "ACTIVE" and not current.get("handoff_path"):
        raise ValueError("ACTIVE restoration requires a prior HANDOFF_READY artifact")

    now = utc_now()
    history = list(current.get("history") or [])
    history.append({"at": now, "from": source, "to": target,
                    "reason": reason or None, "handoff": handoff or None,
                    "recovery_confirmed": recovery_confirmed or None})
    result = {**current, "state": target, "executor_surface": bound,
              "updated_at": now, "history": history}
    if target in {"UNAVAILABLE_BILLING", "SOLO_TAKEOVER"}:
        result.update({"sentinel_allowed": False, "executor_dispatch_allowed": False,
                       "api_retry_allowed": False, "model_switch_allowed": False,
                       "replacement_session_allowed": False})
    elif target == "HANDOFF_READY":
        result.update({"handoff_path": handoff, "sentinel_allowed": False,
                       "executor_dispatch_allowed": False})
    elif target == "ACTIVE":
        result.update({"sentinel_allowed": True, "executor_dispatch_allowed": True,
                       "api_retry_allowed": True, "model_switch_allowed": False,
                       "replacement_session_allowed": False})
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["show", "transition"])
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--to", choices=STATES)
    parser.add_argument("--executor-surface", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--handoff", default="")
    parser.add_argument("--recovery-confirmed", action="store_true")
    args = parser.parse_args()
    path = state_path(args.task_id)
    current = load(path)
    if args.command == "show":
        print(json.dumps(current, indent=2, sort_keys=True))
        return 0
    if not args.to:
        parser.error("transition requires --to")
    updated = transition(current, args.to, executor_surface=args.executor_surface,
                         reason=args.reason, handoff=args.handoff,
                         recovery_confirmed=args.recovery_confirmed)
    updated["task_id"] = args.task_id
    save(path, updated)
    print(json.dumps(updated, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
