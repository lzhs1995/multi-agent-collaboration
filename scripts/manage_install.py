#!/usr/bin/env python3
"""Manage only this checkout's skill links and hook commands. Dry-run by default."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
GUARDS = {
    "cmux_executor_closeout_guard": "PreToolUse",
    "cmux_workspace_guard": "PreToolUse",
    "cmux_agent_panel_guard": "PreToolUse",
    "cmux_handshake_receipt_guard": "PreToolUse",
    "cmux_consensus_round_guard": "PreToolUse",
    "cmux_lease_guard": "PreToolUse",
    "cmux_consensus_stop_guard": "Stop",
    "cmux_submit_confirmation_guard": "PostToolUse",
}


def command(name):
    return shlex.join([sys.executable, "-B", str(ROOT / "scripts" / (name + ".py"))])


def transform(doc, uninstall=False):
    result = copy.deepcopy(doc)
    hooks = result.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks must be an object")
    for name, event in GUARDS.items():
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            raise ValueError("hook event must be a list")
        cmd = command(name)
        if uninstall:
            cleaned = []
            for entry in entries:
                entry = copy.deepcopy(entry)
                old = entry.get("hooks", [])
                kept = [h for h in old if h.get("command") != cmd]
                if len(kept) == len(old) or kept:
                    entry["hooks"] = kept
                    cleaned.append(entry)
            hooks[event] = cleaned
        elif not any(h.get("command") == cmd for e in entries for h in e.get("hooks", [])):
            entries.append({"matcher": "*", "hooks": [{"type": "command", "command": cmd}]})
    return result


def read(path):
    if path.is_symlink():
        raise ValueError("refusing symlinked configuration: " + str(path))
    raw = path.read_bytes() if path.exists() else None
    doc = json.loads(raw) if raw is not None else {}
    if not isinstance(doc, dict):
        raise ValueError("configuration must be an object")
    return raw, doc


def replace(path, old, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    if (path.read_bytes() if path.exists() else None) != old:
        raise RuntimeError("configuration changed concurrently; rerun at a quiet boundary")
    if old is not None:
        backup = path.with_name(path.name + ".multi-agent-backup-" + uuid.uuid4().hex)
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(old)
            handle.flush()
            os.fsync(handle.fileno())
    raw = (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode()
    fd, pending = tempfile.mkstemp(prefix=".multi-agent-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if (path.read_bytes() if path.exists() else None) != old:
            raise RuntimeError("configuration changed during write; refused replacement")
        os.replace(pending, path)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)


def manage(home, mode, apply=False):
    if sys.version_info < (3, 10):
        raise RuntimeError("Python 3.10+ required; do not wire macOS Python 3.9")
    home = Path(home).expanduser().resolve()
    plans = []
    for client, filename in (("codex", "hooks.json"), ("claude", "settings.json")):
        config = home / ("." + client) / filename
        link = home / ("." + client) / "skills/multi-agent-collaboration"
        owned = link.is_symlink() and link.resolve() == ROOT
        if (link.exists() or link.is_symlink()) and not owned:
            raise RuntimeError("foreign skill installation preserved: " + str(link))
        old, doc = read(config)
        new = transform(doc, mode == "uninstall")
        plans.append((config, link, owned, old, doc, new))
    checks = {}
    for config, link, owned, old, doc, new in plans:
        checks[str(config)] = {"linked": owned, "configured": doc == transform(doc),
                               "changeNeeded": doc != new}
        if mode != "doctor" and apply:
            if doc != new:
                replace(config, old, new)
            if mode == "install" and not owned:
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(ROOT, target_is_directory=True)
            elif mode == "uninstall" and owned:
                link.unlink()
    if mode == "doctor":
        for name in GUARDS:
            run = subprocess.run(shlex.split(command(name)), input="{}", text=True,
                                 capture_output=True, timeout=15)
            checks[name] = {"benignExitZero": run.returncode == 0}
        ok = all(v.get("benignExitZero", v.get("linked") and v.get("configured")) for v in checks.values())
    else:
        ok = True
    return {"ok": bool(ok), "mode": mode, "applied": apply and mode != "doctor",
            "checkout": str(ROOT), "checks": checks,
            "scope": "configuration and benign execution, not live client reload proof"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("install", "doctor", "uninstall"))
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        result = manage(args.home, args.mode, args.apply)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["ok"] else 2)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(2)
