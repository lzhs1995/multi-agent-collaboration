"""Authenticate an official TUI selection through its live writable rollout FD.

This reads kernel-owned descriptors and complete native records. It creates no
foreground observation, changes no environment, and never uses resume argv or
mtime to select a thread. The caller separately authenticates the signed native
client and tool ancestry.
"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import uuid

import cmux_identity_budget as budget

MAX_TAIL = 8 * 1024**2
MAX_RECORD = 1024**2
MAX_LSOF = 512 * 1024
_ROLLOUT = re.compile(r"rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-([0-9a-f-]{36})\.jsonl")
_seen = {}


def need(ok, reason):
    if not ok:
        raise ValueError(reason)


def _pairs(items):
    result = {}
    for key, value in items:
        need(key not in result, "duplicate native rollout key")
        result[key] = value
    return result


def _json(raw):
    def invalid(_):
        raise ValueError("invalid native rollout constant")
    value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=invalid)
    need(isinstance(value, dict), "native rollout object required")
    return value


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink)


def descriptors(client):
    result = subprocess.run(
        ["/usr/sbin/lsof", "-nP", "-a", "-p", str(client["pid"]), "-Fpfain"],
        capture_output=True, text=True, timeout=budget.timeout(5.0), check=False)
    budget.check()
    need(result.returncode == 0 and not result.stderr and len(result.stdout.encode()) <= MAX_LSOF,
         "official native rollout descriptors unavailable")
    frames, frame, process = [], None, None
    for line in result.stdout.splitlines():
        if line.startswith("p"):
            need(process is None and line[1:] == str(client["pid"]), "native descriptor process changed")
            process = line[1:]
        elif line.startswith("f"):
            if frame is not None:
                frames.append(frame)
            frame = {"f": line[1:]}
        elif line and line[0] in "ain":
            need(frame is not None and line[0] not in frame, "ambiguous native descriptor fields")
            frame[line[0]] = line[1:]
    if frame is not None:
        frames.append(frame)
    need(process is not None, "native descriptor process absent")
    candidates = [item for item in frames
                  if item.get("a") in ("u", "w")
                  and Path(item.get("n", "")).name.startswith("rollout-")
                  and item.get("n", "").endswith(".jsonl")]
    need(len(candidates) == 1, "official native client requires one writable rollout")
    item = candidates[0]
    need(item["f"].isdigit() and item.get("i", "").isdigit(), "regular native rollout descriptor required")
    return item


def _home(client):
    env = client["env"]
    value = env.get("CODEX_HOME")
    if value is None:
        need(bool(env.get("HOME")), "official native rollout home absent")
        value = str(Path(env["HOME"]) / ".codex")
    home = Path(value)
    need(home.is_absolute() and home.resolve(strict=True) == home, "canonical native rollout home required")
    for path in (home, home / "sessions"):
        info = path.lstat()
        need(stat.S_ISDIR(info.st_mode) and not path.is_symlink()
             and info.st_uid == os.getuid() and not info.st_mode & 0o022,
             "trusted native rollout directory required")
    return home


def _latest(stream, size, birth_ms, thread_id=None):
    start = max(0, size - MAX_TAIL)
    stream.seek(start)
    raw = stream.read(size - start)
    need(len(raw) == size - start, "native rollout truncated during read")
    if start:
        boundary = raw.find(b"\n")
        need(boundary >= 0, "complete native rollout record unavailable")
        start += boundary + 1
        raw = raw[boundary + 1:]
    offset, latest = start, None
    for line in raw.splitlines(keepends=True):
        if line.endswith(b"\n") and line.strip():
            latest = (offset, line)
        offset += len(line)
    need(latest is not None and len(latest[1]) <= MAX_RECORD, "bounded complete native rollout record required")
    entry = _json(latest[1])
    if entry.get("type") == "token_usage_record":
        payload = entry.get("payload")
        need(isinstance(thread_id, str) and isinstance(payload, dict)
             and payload.get("thread_id") == thread_id
             and payload.get("session_id") == thread_id,
             "native token usage record session mismatch")
    else:
        need(entry.get("type") in ("session_meta", "response_item", "event_msg", "turn_context", "compacted"),
             "supported native rollout record required")
    stamp = entry.get("timestamp")
    need(isinstance(stamp, str), "native rollout timestamp required")
    when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    need(when.tzinfo is not None, "absolute native rollout timestamp required")
    milliseconds = int(when.timestamp() * 1000)
    need(birth_ms <= milliseconds <= int(time.time() * 1000) + 1000,
         "official native rollout lacks a valid post-birth record")
    return dict(offset=latest[0], bytes=len(latest[1]),
                sha256=hashlib.sha256(latest[1]).hexdigest(), timestamp=stamp), milliseconds


def read(client):
    need(type(client.get("pid")) is int and client["pid"] > 1, "native client pid required")
    birth = client.get("birth")
    need(isinstance(birth, (list, tuple)) and len(birth) == 2
         and all(type(value) is int and value >= 0 for value in birth), "native client birth required")
    need(client["env"].get("CODEX_CLIENT_THREAD_OBSERVER") is None,
         "required original foreground observer cannot fall back")
    home = _home(client)
    descriptor = descriptors(client)
    path = Path(descriptor["n"])
    match = _ROLLOUT.fullmatch(path.name)
    need(match is not None and path.is_absolute() and path.resolve(strict=True) == path
         and path.is_relative_to(home / "sessions"), "canonical native rollout path required")
    thread = str(uuid.UUID(match[1]))
    need(thread == match[1], "canonical native rollout thread required")
    before = path.lstat()
    need(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
         and not before.st_mode & 0o022 and before.st_nlink >= 1
         and before.st_ino == int(descriptor["i"]), "native descriptor file identity changed")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        need(_signature(opened) == _signature(before), "native rollout replaced before open")
        header = stream.readline(MAX_RECORD + 1)
        need(header.endswith(b"\n") and len(header) <= MAX_RECORD, "complete native session metadata required")
        meta = _json(header)
        payload = meta.get("payload", {})
        need(meta.get("type") == "session_meta" and isinstance(payload, dict)
             and payload.get("source") == "cli" and payload.get("thread_source") == "user",
             "original user CLI session metadata required")
        aliases = [payload[key] for key in ("session_id", "id") if key in payload]
        need(bool(aliases) and all(value == thread for value in aliases),
             "native session metadata thread mismatch")
        current, when = _latest(stream, opened.st_size, birth[0] * 1000 + birth[1] / 1000, thread)
        key = (client["pid"], tuple(birth), descriptor["f"], str(path),
               _signature(opened), hashlib.sha256(header).hexdigest())
        if key in _seen:
            selected = _seen[key]
            old = selected["native_record"]
            stream.seek(old["offset"])
            need(hashlib.sha256(stream.read(old["bytes"])).hexdigest() == old["sha256"],
                 "original observed native record changed")
        else:
            need(len(_seen) < 128, "native rollout observation capacity exceeded")
            selected = {
                "thread_id": thread, "source": "official-native-rollout-fd-v1",
                "client_epoch": str(uuid.uuid5(uuid.NAMESPACE_URL, repr(key))),
                "published_at_ms": when, "path": str(path),
                "signature": list(_signature(opened)),
                "metadata_sha256": hashlib.sha256(header).hexdigest(),
                "descriptor": {"fd": int(descriptor["f"]), "access": descriptor["a"],
                               "inode": int(descriptor["i"])},
                "native_record": current,
            }
        after = os.fstat(stream.fileno())
    fresh = path.lstat()
    need(_signature(after) == _signature(fresh) == _signature(before)
         and after.st_size >= opened.st_size and fresh.st_size >= after.st_size,
         "native rollout identity changed during observation")
    need(descriptors(client) == descriptor, "native writable rollout selection changed")
    need(_signature(path.lstat()) == _signature(before), "native rollout changed after descriptor recheck")
    budget.check()
    _seen[key] = selected
    return dict(selected)

