#!/usr/bin/env python3
"""Native delivery proof: did the receiver's OWN session record our message?

Paste is not submission and Enter is not delivery. Measured 2026-10-08 (r23
callback, C2596 preflight): Enter was accepted by the receiver composer as a
newline and the payload stayed there; every screen heuristic then had to guess.

The only ground truth is the receiver's native transcript:
  Codex  ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
         response_item / message / role=user, single input_text
  Claude ~/.claude/projects/*/*.jsonl
         type=user, message.role=user, plain text (not tool_result/meta)
A record counts only when it is a real user turn whose whole text equals the
payload (whitespace at the ends aside) and it is not older than the attempt.
Substrings, quotes inside other messages and tool output never count.

Read only: never sends text or keys, never writes journals or receipts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

RECEIVED = "RECEIVED"
RECEIVED_ALTERED = "RECEIVED_ALTERED"
NOT_RECEIVED = "NOT_RECEIVED"

TAIL_BYTES = 16 * 1024 * 1024
MAX_TAIL_BYTES = 1024 * 1024 * 1024


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _epoch(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def user_turn_text(event):
    """Text of a genuine user turn, else None. Both receiver formats."""
    if not isinstance(event, dict):
        return None
    payload = event.get("payload")
    if event.get("type") == "response_item" and isinstance(payload, dict):
        content = payload.get("content")
        if (payload.get("type") == "message" and payload.get("role") == "user"
                and isinstance(content, list) and len(content) == 1
                and isinstance(content[0], dict)
                and content[0].get("type") == "input_text"
                and isinstance(content[0].get("text"), str)):
            return content[0]["text"]
        return None
    if event.get("type") == "user" and not event.get("isMeta") and not event.get("isSidechain"):
        message = event.get("message")
        if not isinstance(message, dict) or message.get("role") != "user":
            return None
        content = message.get("content")
        return _single_text(message.get("content"))
    # Claude busy mid-turn: the message is injected as a queued_command
    # attachment, not a type=user record (measured 2026-10-08 in this
    # session's own transcript: 100 such records, incl. ROOT dispatches).
    attachment = event.get("attachment")
    if (event.get("type") == "attachment" and isinstance(attachment, dict)
            and attachment.get("type") == "queued_command"
            and not event.get("isSidechain")):
        return _single_text(attachment.get("prompt"))
    return None


def _single_text(content):
    if isinstance(content, str):
        return content
    if (isinstance(content, list) and len(content) == 1
            and isinstance(content[0], dict) and content[0].get("type") == "text"
            and isinstance(content[0].get("text"), str)):
        return content[0]["text"]
    return None


def _matches(record_text, text=None, payload_sha256=None):
    variants = {record_text, record_text.strip(), record_text.rstrip("\n")}
    if text is not None and text.strip() == record_text.strip():
        return True
    if payload_sha256:
        candidates = set()
        for v in variants:
            candidates.update({v, v + "\n", v.strip() + "\n"})
        return any(_sha(c) == payload_sha256 for c in candidates)
    return False


def candidate_transcripts(since_epoch, home=None):
    """Receiver transcripts written since the attempt; newest first.

    A Codex rollout lives under the date its session STARTED and keeps growing
    there. Measured 2026-10-08: ROOT's live session is
    sessions/2026/09/29/rollout-*.jsonl, so a date-directory window found
    nothing and every delivery to ROOT read NOT_RECEIVED. Select by mtime over
    the whole tree instead (~21k files, ~0.1 s).
    """
    home = Path(home or Path.home())
    found = []
    sessions = home / ".codex" / "sessions"
    for root, _dirs, files in os.walk(sessions):
        found.extend(Path(root) / f for f in files
                     if f.startswith("rollout-") and f.endswith(".jsonl"))
    projects = home / ".claude" / "projects"
    if projects.is_dir():
        found.extend(projects.glob("*/*.jsonl"))
    out = []
    for path in found:
        try:
            st = path.stat()
        except OSError:
            continue
        if not path.is_symlink() and st.st_mtime >= since_epoch - 5:
            out.append((st.st_mtime, path))
    return [p for _, p in sorted(out, reverse=True)]


_TS_RE = re.compile(rb'"timestamp":"([^"]+)"')


def _tail_records(path, since_epoch=None):
    """Records from the end back to since_epoch (bounded by MAX_TAIL_BYTES).

    A fixed 16 MB tail covered only ~48 min of ROOT's 3 GB rollout (compaction
    records are large), so a check made later missed a real delivery. Grow the
    window until its first record predates the attempt.
    """
    with path.open("rb") as f:
        size = f.seek(0, os.SEEK_END)
        window = min(size, TAIL_BYTES)
        while True:
            start = size - window
            f.seek(start)
            data = f.read(window)
            first = data.split(b"\n", 2)[1 if start else 0] if b"\n" in data else data
            stamp = _TS_RE.search(first)
            first_at = _epoch(stamp.group(1).decode()) if stamp else None
            if (start == 0 or since_epoch is None or window >= MAX_TAIL_BYTES
                    or (first_at is not None and first_at < since_epoch - 2)):
                break
            window = min(size, window * 4, MAX_TAIL_BYTES)
    lines = data.split(b"\n")
    if start:
        lines = lines[1:]
    for raw in lines:
        if not raw.strip():
            continue
        try:
            yield json.loads(raw), raw
        except ValueError:
            continue


def find_native_user_record(*, marker, since_epoch, text=None, payload_sha256=None, home=None):
    """One scan. RECEIVED needs whole-text equality; a marker alone is nothing."""
    if not marker:
        raise ValueError("marker required")
    altered = None
    for path in candidate_transcripts(since_epoch, home):
        try:
            records = list(_tail_records(path, since_epoch))
        except OSError:
            continue
        for event, raw in records:
            if marker.encode() not in raw:
                continue
            body = user_turn_text(event)
            if body is None or marker not in body:
                continue
            at = _epoch(event.get("timestamp"))
            if at is None or at < since_epoch - 2:
                continue
            hit = {"transcript": str(path), "timestamp": event.get("timestamp"),
                   "record_sha256": hashlib.sha256(raw).hexdigest()}
            if _matches(body, text, payload_sha256):
                return dict(hit, state=RECEIVED)
            # A user quoting our marker inside a longer message is not our
            # delivery (measured 14:28Z: the operator pasted the r23 line into
            # a complaint). ALTERED means same characters, whitespace mangled.
            if text is not None and "".join(body.split()) == "".join(text.split()):
                altered = altered or dict(hit, state=RECEIVED_ALTERED,
                                          received_sha256=_sha(body))
    return altered or {"state": NOT_RECEIVED}


def wait_for_native_user_record(*, marker, since_epoch, text=None, payload_sha256=None,
                                wait_seconds=0.0, interval=2.0, home=None):
    deadline = time.time() + max(0.0, wait_seconds)
    while True:
        result = find_native_user_record(marker=marker, since_epoch=since_epoch, text=text,
                                         payload_sha256=payload_sha256, home=home)
        if result["state"] != NOT_RECEIVED or time.time() >= deadline:
            return result
        time.sleep(interval)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--marker", required=True)
    ap.add_argument("--since-epoch", type=float, required=True)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--text-file")
    group.add_argument("--payload-sha256")
    ap.add_argument("--wait", type=float, default=0.0)
    args = ap.parse_args(argv)
    text = Path(args.text_file).read_text() if args.text_file else None
    result = wait_for_native_user_record(marker=args.marker, since_epoch=args.since_epoch,
                                         text=text, payload_sha256=args.payload_sha256,
                                         wait_seconds=args.wait)
    print(json.dumps(result, ensure_ascii=False))
    return {RECEIVED: 0, NOT_RECEIVED: 3}.get(result["state"], 4)


if __name__ == "__main__":
    sys.exit(main())
