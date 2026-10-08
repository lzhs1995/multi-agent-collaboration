#!/usr/bin/env python3
"""Stop: an idle executor must keep asking its supervisor, not wait silently.

Jurisdiction is positive and bounded. Only a surface this guard has itself seen
bound as the executor of an armed task can be obligated; the idle episode starts
when that binding disappears. The obligation ends only when a reply actually
reached this executor, when the supervisor dispatched again, or when an operator
stopped the loop -- never because a budget ran out, and never because the model
re-entered Stop. Satisfying it is always one printed command away: the loop
writes its own record before any bridge call, so a refusing bridge still leaves a
live loop. Identity failures fail open; this is a liveness guard, not a gate.
"""
import json
from pathlib import Path
import shlex
import sys
import time

import cmux_consensus_stop_guard as stop_guard
import cmux_hook_identity as hook_identity
import executor_ready

OPEN_STATES = ('BOUND', 'IDLE_ASKING')


def bindings_dir():
    return executor_ready.idle_root() / 'bindings'


def _binding_path(workspace, surface):
    return bindings_dir() / (executor_ready.digest(json.dumps([workspace, surface])) + '.json')


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


def loop_for(surface, since_epoch, now):
    """Classify this surface's own ask loop against the current idle episode."""
    record = executor_ready.read_json(executor_ready.loop_path(surface))
    if not record or record.get('caller_surface_uuid') != surface:
        return None, None
    ended = float(record.get('ended_epoch', record.get('heartbeat_epoch', 0)))
    state = record.get('state')
    if state == executor_ready.ANSWERED and ended >= since_epoch:
        return 'IDLE_ANSWERED', record
    if state == executor_ready.STOPPED and ended >= since_epoch:
        reason = str((record.get('stop') or {}).get('reason', 'OPERATOR'))
        if reason != 'DISPATCHED':
            return 'IDLE_STOPPED_BY_OPERATOR', record
        return None, record
    if executor_ready.loop_live(record, now):
        return 'IDLE_ASKING', record
    return None, record


def remedy(state, payload):
    script = Path(__file__).with_name('executor_ready.py')
    command = [sys.executable, '-B', str(script), 'persist',
               '--supervisor', str(state.get('supervisor_ref') or '<supervisor surface>'),
               '--caller-uuid', str(state.get('surface_uuid') or ''),
               '--task', str(state.get('task_id') or '')]
    transcript = payload.get('transcript_path')
    if transcript:
        command += ['--transcript', str(transcript)]
    return 'nohup ' + ' '.join(shlex.quote(part) for part in command) + ' >/dev/null 2>&1 &'


def _evaluate_resolved(payload):
    workspace, surface = hook_identity.identity(payload)
    if not surface:
        return True, ''
    path = _binding_path(workspace, surface)
    state = executor_ready.read_json(path) or {}
    now = time.time()
    bound = _executor_binding(stop_guard._active_markers(payload), surface)
    if bound:
        if (executor_ready.read_json(executor_ready.loop_path(surface)) or {}).get('state') \
                == executor_ready.ASKING:
            executor_ready.stop_loop(surface, 'DISPATCHED')
        executor_ready.write_json(path, dict(bound, surface_uuid=surface, state='BOUND',
                                             last_bound_epoch=now))
        return True, ''
    if state.get('state') not in OPEN_STATES:
        return True, ''
    since = float(state.get('last_bound_epoch', now))
    verdict, record = loop_for(surface, since, now)
    if verdict:
        executor_ready.write_json(path, dict(state, state=verdict, observed_epoch=now,
                                             loop=dict(pid=(record or {}).get('pid'),
                                                       asks=(record or {}).get('ask_count'))))
        return True, ''
    executor_ready.write_json(path, dict(state, blocked_epoch=now,
                                         blocks=int(state.get('blocks', 0)) + 1))
    return False, (
        'EXECUTOR_IDLE_ASK_LOOP_REQUIRED: task ' + str(state.get('task_id') or '(unknown)')
        + ' is no longer armed and no live ask loop is covering this executor. Do not wait '
        'silently, do not ask the user to relay, and do not end this turn into a dead wait. '
        'Start the out-of-turn loop that re-asks the supervisor every '
        + str(int(executor_ready.ASK_INTERVAL)) + 's until a reply reaches this surface, then '
        'end the turn so the supervisor can dispatch:\n  ' + remedy(state, payload)
        + '\nThe loop stops only on a real reply (bridge message or mailbox file), a new '
        'dispatch, or an operator stop (`executor_ready.py stop --caller-uuid '
        + str(state.get('surface_uuid') or '') + '`). Check it with `status`; never resend a '
        'marker it recorded as UNCONFIRMED.')


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
