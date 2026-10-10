"""Bounded file-request discovery by the existing authenticated supervisor hook.

No terminal input. A discovery record is not native receipt or task acceptance.
One current request and one discovery cursor per caller keep storage bounded.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import time
import uuid

import cmux_hook_identity as identity
import executor_ready as ready
import executor_reply as replies

MAX_ENTRIES = 256
MAX_NOTICES = 8


def request_bytes(path):
    """Hash and parse the same bounded regular-file snapshot, never a second read."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > ready.MAX_JSON_BYTES:
                return None, None
            raw = handle.read(ready.MAX_JSON_BYTES + 1)
            after = os.fstat(handle.fileno())
            if (len(raw) > ready.MAX_JSON_BYTES or before.st_size != after.st_size
                    or before.st_mtime_ns != after.st_mtime_ns):
                return None, None
        body = json.loads(raw)
        return (raw, body) if isinstance(body, dict) else (None, None)
    except (OSError, ValueError):
        return None, None


def _uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value.lower()
    except (ValueError, AttributeError):
        return False


def discover(payload):
    if not isinstance(payload, dict) or payload.get('hook_event_name') != 'PostToolUse':
        return []
    with identity.evaluation(payload):
        workspace, supervisor = identity.identity(payload)
        if not _uuid(workspace) or not _uuid(supervisor):
            return []
        return discover_bound(workspace, supervisor)


def discover_bound(workspace, supervisor):
    if not _uuid(workspace) or not _uuid(supervisor):
        return []
    inbox = ready.state_root() / 'supervisor-inbox-v1' / ready.digest(supervisor)
    if not inbox.is_dir() or inbox.is_symlink():
        return []
    cursor = ready.state_root() / 'supervisor-inbox-discovery-v1' / ready.digest(supervisor)
    cursor.mkdir(parents=True, exist_ok=True, mode=0o700)
    if cursor.is_symlink():
        return []
    fd = os.open(cursor / 'sweep.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return []
        paths = []
        with os.scandir(inbox) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_ENTRIES:
                    return []
                if entry.is_file(follow_symlinks=False) and entry.name.endswith('.json'):
                    paths.append(Path(entry.path))
        notices = []
        for path in sorted(paths):
            raw, body = request_bytes(path)
            if (not body or body.get('supervisor') != supervisor
                    or body.get('workspace_uuid') != workspace
                    or not _uuid(body.get('caller_surface_uuid'))
                    or path.name != ready.digest(body['caller_surface_uuid']) + '.json'
                    or body.get('status') != 'WAITING_REPLY'
                    or body.get('schema') not in ('executor-reask-current-v2', 'coordination-request-v1')):
                continue
            marker = body.get('marker')
            if not isinstance(marker, str) or not marker or len(marker) > 160:
                continue
            template = body.get('reply_template')
            if not replies.valid(template, [marker], caller=body['caller_surface_uuid'],
                                 supervisor=supervisor, task_id=body.get('task_id', ''),
                                 episode_id=body.get('episode_id'), template_only=True):
                continue
            # Only the canonical mailbox is offered as a write target.
            reply_path = ready.mailbox_dir(body['caller_surface_uuid']) / (marker + '.json')
            if body.get('reply_mailbox') != str(reply_path):
                continue
            if replies.valid(ready.read_json(reply_path), [marker], caller=body['caller_surface_uuid'],
                             supervisor=supervisor, task_id=body.get('task_id', ''),
                             episode_id=body['episode_id']):
                continue
            seen_path = cursor / path.name
            seen = ready.read_json(seen_path) or {}
            token = ready.digest(json.dumps([marker, body['episode_id'], body.get('task_id', '')]))
            if seen.get('token') == token:
                continue
            notice = dict(state='REQUEST_DISCOVERED', request=str(path),
                          request_sha256=hashlib.sha256(raw).hexdigest(), marker=marker,
                          caller_surface_uuid=body['caller_surface_uuid'], supervisor_uuid=supervisor,
                          workspace_uuid=workspace, task_id=body.get('task_id', ''),
                          episode_id=body['episode_id'], reply_mailbox=str(reply_path),
                          reply_template=template, discovered_epoch=time.time(),
                          token=token, input_operations=0, native_delivery_confirmed=False)
            ready.write_json(seen_path, notice)
            notices.append(notice)
            if len(notices) >= MAX_NOTICES:
                break
        return notices


def context(notices):
    lines = ['REQUEST_DISCOVERED: read the exact file request and reply to its bound mailbox. '
             'File discovery is not native transport, a new task pack or business acceptance.']
    for notice in notices:
        lines.append('REQUEST=' + notice['request'] + ' SHA256=' + notice['request_sha256']
                     + ' REPLY=' + notice['reply_mailbox'] + ' TEMPLATE='
                     + json.dumps(notice['reply_template'], ensure_ascii=False))
    return '\n'.join(lines)
