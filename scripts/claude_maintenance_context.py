"""Expose an explicitly enrolled, already queued maintenance body at a tool boundary.

This is a file reader, not a sender or a native-receipt reconciler. It never
changes the original attempt, runs a command from a notice, or blocks a hook.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time
import uuid

import cmux_daemon_identity as identity
import cmux_native_delivery as native

SCHEMA = 'claude-maintenance-context-v1'
MAX_BYTES = 64 * 1024


def root():
    return Path.home() / '.local/state/multi-agent-collaboration' / SCHEMA


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    with native._open(str(path)) as stream:
        before = os.fstat(stream.fileno())
        raw = stream.read(MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
        if (len(raw) > MAX_BYTES or
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or
                Path(path).stat().st_ino != after.st_ino):
            raise ValueError('maintenance file changed or exceeded limit')
        return raw


def pin(path):
    path = Path(path).resolve(strict=True)
    raw = read(path)
    return dict(path=str(path), sha256=digest(raw), bytes=len(raw))


def pinned(value):
    raw = read(value['path'])
    if len(raw) != value['bytes'] or digest(raw) != value['sha256']:
        raise ValueError('maintenance pin changed')
    return raw


def private_root(create=False):
    directory = root()
    if create:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    st = directory.lstat()
    if (directory.is_symlink() or not stat.S_ISDIR(st.st_mode)
            or st.st_uid != os.getuid() or st.st_mode & 0o077):
        raise ValueError('maintenance registry must be private')
    return directory


def material(offer):
    if offer.get('schema') != SCHEMA:
        raise ValueError('unsupported maintenance offer')
    session = str(uuid.UUID(offer['session_id']))
    if session != offer['session_id']:
        raise ValueError('invalid session')
    attempt = json.loads(pinned(offer['attempt']))
    artifact = attempt['artifact']
    binding = attempt['native_binding']
    if (attempt.get('schema') != 'successor-attempt-v2'
            or artifact.get('scope') != 'maintenance_handshake'
            or artifact.get('communication_only') is not True
            or artifact.get('allow_task_replay') is not False
            or artifact.get('allow_shared_writes') is not False
            or binding.get('provider') != 'claude'
            or binding.get('session_id') != session
            or binding['identity']['target_surface_uuid'] != artifact['target_executor_uuid']
            or digest(attempt['wire'].encode()) != attempt['wire_sha256']
            or binding['payload_sha256'] != attempt['wire_sha256']):
        raise ValueError('not an original Claude maintenance attempt')
    authorization = artifact['authorization_record']
    if digest(read(authorization['path'])) != authorization['sha256']:
        raise ValueError('original authorization changed')
    pinned(offer['maintenance_request']).decode('utf-8')
    body = read(attempt['body_reference']['path'])
    if (len(body) != attempt['body_reference']['bytes']
            or digest(body) != attempt['body_sha256']
            or digest(body) != attempt['body_reference']['sha256']
            or body.decode('utf-8') != attempt['body']):
        raise ValueError('original body changed')
    log = Path(binding['transcript']['path'])
    if (Path.home() / '.claude/projects') not in log.parents:
        raise ValueError('not a Claude native transcript')
    queued = offer['queued_record']
    if (type(queued['offset']) is not int or queued['offset'] < binding['transcript']['offset']
            or type(queued['bytes']) is not int or not 0 < queued['bytes'] <= MAX_BYTES):
        raise ValueError('queue must follow original intent')
    with native._open(log) as stream:
        native._check_file(stream, binding)
        stream.seek(queued['offset'])
        raw = stream.read(queued['bytes'])
        if digest(raw) != queued['sha256'] or not raw.endswith(b'\n'):
            raise ValueError('original queue record changed')
        record = json.loads(raw)
        if (record.get('type') != 'attachment' or record.get('isSidechain') is True
                or record.get('sessionId') != session
                or record.get('attachment', {}).get('type') != 'queued_command'
                or record['attachment'].get('prompt') != attempt['wire']):
            raise ValueError('not the original queued command')
        native._check_file(stream, binding)
    return attempt, binding, body.decode('utf-8')


def caller_matches(binding):
    # Automatic hooks normally have Claude as their direct parent. Allow only
    # its short shell/proxy chain, not a manually invoked tool's ancestry.
    pid = os.getppid()
    for _ in range(3):
        row = identity.process(pid, validate_argv=False)
        if row['pid'] == binding['process']['pid']:
            return all(row.get(key) == value for key, value in binding['process'].items())
        if Path(row['executable']).name not in ('sh', 'bash', 'zsh', 'rtk'):
            return False
        pid = row['ppid']
    return False


def enroll(attempt_path, queued_offset, queued_bytes, request_path, reply_path):
    if (type(queued_offset) is not int or queued_offset < 0
            or type(queued_bytes) is not int or not 0 < queued_bytes <= MAX_BYTES):
        raise ValueError('invalid queue record bounds')
    attempt_pin = pin(attempt_path)
    attempt = json.loads(pinned(attempt_pin))
    binding = attempt['native_binding']
    with native._open(binding['transcript']['path']) as stream:
        stream.seek(queued_offset)
        raw = stream.read(queued_bytes)
    reply = Path(reply_path)
    if not reply.is_absolute() or reply.parent.resolve() != reply.parent or reply.exists():
        raise ValueError('reply must be a new absolute file in an existing directory')
    offer = dict(schema=SCHEMA, session_id=binding['session_id'], attempt=attempt_pin,
                 maintenance_request=pin(request_path), reply_path=str(reply),
                 queued_record=dict(offset=queued_offset, bytes=queued_bytes, sha256=digest(raw)))
    material(offer)
    offer['offer_id'] = digest(json.dumps(offer, sort_keys=True).encode())
    target = private_root(create=True) / (offer['session_id'] + '.json')
    encoded = (json.dumps(offer, ensure_ascii=False, indent=2) + '\n').encode()
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(encoded)
    return target, offer


def emit_context(text):
    print(json.dumps({'hookSpecificOutput': {
        'hookEventName': 'PostToolUse', 'additionalContext': text}}, ensure_ascii=False), flush=True)


def discover(payload, *, emit=emit_context, now=None):
    """No enrollment or mismatch => no output. All failures remain advisory."""
    try:
        if payload.get('hook_event_name') != 'PostToolUse':
            return
        session = str(uuid.UUID(payload.get('session_id', '')))
        directory = private_root()
        offer = json.loads(read(directory / (session + '.json')))
        if offer['session_id'] != session:
            return
        attempt, binding, body = material(offer)
        if payload.get('transcript_path') != binding['transcript']['path'] or not caller_matches(binding):
            return
        reply_template = dict(offer_id=offer['offer_id'], session_id=session,
                              marker=attempt['marker'], body_sha256=attempt['body_sha256'])
        try:
            reply = json.loads(read(offer['reply_path']))
            if all(reply.get(k) == v for k, v in reply_template.items()) and reply.get('body_read') is True:
                return  # Suppresses reminders only; never accepts work or a native receipt.
        except (OSError, ValueError):
            pass
        cursor = directory / (session + '.cursor.json')
        fd = os.open(str(cursor), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'r+') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            previous = stream.read(MAX_BYTES + 1)
            previous = json.loads(previous) if previous else {}
            when = time.time() if now is None else now
            last = previous.get('offered_at', 0)
            if previous.get('offer_id') == offer['offer_id'] and 0 <= when - last < 60:
                return
            text = ('An explicitly authorized maintenance notice is already queued in this same '
                    'Claude session. The full verified original body follows at this tool boundary. '
                    'Read it now before starting another supervisor-wait loop. This file context '
                    'is not a new terminal message, native user receipt, task pack or business acceptance. '
                    'Preserve the original controller, callback, queue and all drafts.\n'
                    + body + '\n\nAfter reading, write your own reply to ' + offer['reply_path']
                    + ' with these exact bindings: ' + json.dumps(reply_template, ensure_ascii=False)
                    + '. Add body_read=true and your actual in-flight-write/reservation status; '
                    'do not assert a safe boundary without checking it. The successor will dispatch '
                    'business work separately. Hook exposure alone is not proof you read or accepted it.')
            emit(text)
            # A failed output flush must not consume the notice.
            stream.seek(0)
            json.dump(dict(offer_id=offer['offer_id'], offered_at=when,
                           exposures=previous.get('exposures', 0) + 1,
                           body_read_confirmed=False, native_received=False), stream)
            stream.truncate()
            stream.flush()
    except Exception:
        return


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--attempt', required=True)
    parser.add_argument('--queued-offset', type=int, required=True)
    parser.add_argument('--queued-bytes', type=int, required=True)
    parser.add_argument('--maintenance-request', required=True)
    parser.add_argument('--reply', required=True)
    args = parser.parse_args()
    path, offer = enroll(args.attempt, args.queued_offset, args.queued_bytes,
                         args.maintenance_request, args.reply)
    print(json.dumps(dict(status='ENROLLED_FILE_CONTEXT_ONLY', path=str(path),
                          offer_id=offer['offer_id'], input_operations=0)))


if __name__ == '__main__':
    main()
