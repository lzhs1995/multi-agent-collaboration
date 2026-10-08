#!/usr/bin/env python3
"""Stop: an executor whose bound task was disarmed must ask for the next task once.

Jurisdiction is positive and bounded. Only a surface this guard has seen bound as
an executor of an armed task can be obligated; an idle episode starts when that
binding disappears. Evidence is a ready request recorded by executor_ready.py
whose own message-dispatch journal shows a paste after the binding was last
seen. Reentry passes, the reminder budget is finite, and identity failures fail
open: this is a liveness guard, never a reason to wedge a session.
"""
import json
from pathlib import Path
import sys
import time

import cmux_consensus_stop_guard as stop_guard
import cmux_hook_identity as hook_identity
import executor_ready

MAX_REMINDERS = 2
PASTED = ('PASTE_INTENT', 'POST_ENTER_OBSERVATION', 'POST_QUEUE_TAB_OBSERVATION', 'CONFIRMED')


def bindings_dir():
    return executor_ready.state_root() / 'executor-idle-v1' / 'bindings'


def _binding_path(workspace, surface):
    return bindings_dir() / (executor_ready.digest(json.dumps([workspace, surface])) + '.json')


def _read(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name('.' + path.name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False))
    tmp.replace(path)


def _executor_binding(markers, surface):
    for marker in markers:
        for row in marker.get('participants', []):
            if (isinstance(row, dict) and str(row.get('role', '')).startswith('executor')
                    and row.get('surface_uuid') == surface):
                supervisor = next((p for p in marker.get('participants', []) if isinstance(p, dict)
                                   and p.get('role') == 'supervisor'), {})
                return dict(task_id=marker.get('task_id', ''),
                            supervisor_ref=supervisor.get('surface_ref', ''),
                            supervisor_uuid=supervisor.get('surface_uuid', ''))
    return None


def ready_evidence(surface, since_epoch):
    """A recorded ready request whose original journal attempt pasted after `since`."""
    root = executor_ready.requests_dir()
    if not root.is_dir():
        return None
    for path in sorted(root.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True):
        record = _read(path)
        if (not record or record.get('caller_surface_uuid') != surface
                or float(record.get('created_at_epoch', 0)) < since_epoch):
            continue
        marker, text = record.get('marker', ''), record.get('text', '')
        if (not marker.startswith(executor_ready.PREFIX) or marker not in text
                or record.get('payload_sha256') != executor_ready.digest(text)):
            continue
        journal = (executor_ready.state_root() / 'message-dispatch-v1'
                   / executor_ready.request_key(surface, marker))
        for attempt_path in sorted(journal.glob('attempt-*.json')):
            attempt = _read(attempt_path) or {}
            binding = attempt.get('binding') or {}
            identity = binding.get('identity') or {}
            pasted = [e for e in attempt.get('events', []) if isinstance(e, dict)
                      and e.get('phase') == 'PASTE_INTENT'
                      and float(e.get('at_epoch', 0)) >= since_epoch]
            if (binding.get('marker') == marker and identity.get('caller_surface_uuid') == surface
                    and binding.get('payload_sha256') == record['payload_sha256']
                    and pasted and attempt.get('phase') in PASTED):
                return dict(marker=marker, attempt=str(attempt_path), phase=attempt['phase'])
    return None


def _evaluate_resolved(payload):
    workspace, surface = hook_identity.identity(payload)
    if not surface:
        return True, ''
    path = _binding_path(workspace, surface)
    state = _read(path)
    now = time.time()
    bound = _executor_binding(stop_guard._active_markers(payload), surface)
    if bound:
        _write(path, dict(bound, state='BOUND', last_bound_epoch=now, reminders=0))
        return True, ''
    if not state or state.get('state') != 'BOUND':
        return True, ''
    evidence = ready_evidence(surface, float(state.get('last_bound_epoch', now)))
    if evidence:
        _write(path, dict(state, state='IDLE_REQUESTED', request=evidence, requested_epoch=now))
        return True, ''
    if payload.get('stop_hook_active') is True:
        return True, ''
    if int(state.get('reminders', 0)) >= MAX_REMINDERS:
        _write(path, dict(state, state='IDLE_RELEASED_WITHOUT_REQUEST', released_epoch=now))
        return True, ''
    _write(path, dict(state, reminders=int(state.get('reminders', 0)) + 1))
    ready = Path(__file__).with_name('executor_ready.py')
    return False, (
        'EXECUTOR_IDLE_READY_REQUIRED: task ' + str(state.get('task_id') or '(unknown)')
        + ' is no longer armed and no ready request reached the supervisor. Do not wait '
        'silently or ask the user to relay. Send exactly one journaled ready request, '
        'then end this turn:\n  ' + sys.executable + ' -B ' + str(ready) + ' request --supervisor '
        + str(state.get('supervisor_ref') or '<supervisor surface>') + ' --task '
        + str(state.get('task_id') or '') + '\nIf it reports UNCONFIRMED_DO_NOT_RESEND, do not '
        'resend; later use `reconcile --marker <marker>`. A supervisor WAITING_DEPENDENCY/SOLO '
        'reply is its decision; the next task arrives as a new handshake.')


def evaluate(payload):
    if payload.get('hook_event_name') != 'Stop':
        return True, ''
    try:
        if not stop_guard._has_active_markers() and not bindings_dir().is_dir():
            return True, ''
        with hook_identity.evaluation(payload):
            return _evaluate_resolved(payload)
    except hook_identity.ERRORS:
        return True, ''


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    if not isinstance(payload, dict):
        return 0
    ok, reason = evaluate(payload)
    if not ok:
        print(reason, file=sys.stderr)
    return 0 if ok else 2


if __name__ == '__main__':
    raise SystemExit(main())
