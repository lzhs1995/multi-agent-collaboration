#!/usr/bin/env python3
"""Executor-side ready request: ask the bound supervisor for the next task once per idle episode.

An executor whose task was disarmed must not wait silently for the supervisor to
notice it. This entrypoint sends one ordinary journaled message (never a task
pack, never force-compose) and records the exact payload so the idle Stop guard
can bind the request to real dispatch evidence instead of a claim.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time

PREFIX = 'EXECUTOR_READY'


def state_root():
    return Path.home() / '.local/state/multi-agent-collaboration'


def requests_dir():
    return state_root() / 'executor-idle-v1' / 'requests'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def request_key(caller_uuid, marker):
    return digest(json.dumps([caller_uuid, marker], sort_keys=True))


def build_text(executor_ref, marker, task_id='', note=''):
    """One line, marker-bound; the first token can never look like a task dispatch."""
    text = (f'{PREFIX}|{executor_ref}|{marker} '
            f'previous task {task_id or "(unknown)"} is no longer armed; this executor is idle. '
            'Please review this request and dispatch the next independent task pack '
            '(handshake + task-pack). If none is available, reply WAITING_DEPENDENCY or SOLO '
            'with the trigger condition. Dispatch after this executor turn ends: a running '
            'turn makes the bridge-test pre-read refuse.')
    note = ' '.join(str(note).split())
    return text + (' ' + note if note else '')


def write_record(caller_uuid, marker, text, target_surface):
    path = requests_dir() / (request_key(caller_uuid, marker) + '.json')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    record = dict(marker=marker, caller_surface_uuid=caller_uuid, target_surface=target_surface,
                  payload_sha256=digest(text), text=text, created_at_epoch=time.time())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(record, handle, ensure_ascii=False)
    return path, record


def _bridge():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import cmux_bridge
    return cmux_bridge


def request(supervisor, task_id='', note=''):
    bridge = _bridge()
    proof = bridge.pin_workspace(supervisor)
    caller = proof['caller_surface_uuid']
    executor_ref = os.environ.get('CMUX_SURFACE_ID') or caller
    marker = f'{PREFIX}_{secrets.token_hex(4)}'
    text = build_text(executor_ref, marker, task_id, note)
    if bridge._looks_like_task_dispatch(text):
        raise RuntimeError('ready request must never look like a task dispatch')
    path, _record = write_record(caller, marker, text, supervisor)
    return dict(marker=marker, record=str(path), **_submit(bridge, supervisor, text, marker))


def reconcile(supervisor, marker):
    bridge = _bridge()
    caller = bridge.pin_workspace(supervisor)['caller_surface_uuid']
    record = json.loads((requests_dir() / (request_key(caller, marker) + '.json')).read_text())
    return dict(marker=marker, **_submit(bridge, supervisor, record['text'], marker, reconcile_only=True))


def _submit(bridge, supervisor, text, marker, reconcile_only=False):
    try:
        result = bridge.submit_text(supervisor, text, marker=marker, reconcile_only=reconcile_only)
        return dict(outcome='CONFIRMED', result=result)
    except bridge.DispatchUnconfirmed as exc:
        # Queued or unverified: the original attempt stands. Never resend it.
        return dict(outcome='UNCONFIRMED_DO_NOT_RESEND', detail=str(exc)[:400])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    req = sub.add_parser('request')
    req.add_argument('--supervisor', required=True)
    req.add_argument('--task', default='')
    req.add_argument('--note', default='')
    rec = sub.add_parser('reconcile')
    rec.add_argument('--supervisor', required=True)
    rec.add_argument('--marker', required=True)
    args = parser.parse_args(argv)
    if args.mode == 'request':
        out = request(args.supervisor, args.task, args.note)
    else:
        out = reconcile(args.supervisor, args.marker)
    print(json.dumps(out, ensure_ascii=False, default=str))
    print('End this turn now; the supervisor owns the next dispatch or disposition.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
