"""Task-scoped Claude Stop continuation; no input, waits, or implicit enrollment.

Only an original, explicitly authorized current-release inquiry participates.
The foreground runner owns transport and its one-attempt journal. This hook
keeps the executor working until a real reply is read or authority ends.
"""
import copy
import os
from pathlib import Path
import shlex
import sys
import time
import uuid

import executor_ready as ready
import executor_reask as reask
import executor_reask_lifecycle as lifecycle
import executor_reask_protocol as protocol
import executor_reask_scheduler as scheduler


def command(state, verb, *args):
    original = protocol.state_controller(state)
    return shlex.join(['rtk', 'proxy', original['python'], '-B',
                      str(Path(original['source']) / 'scripts/executor_reask.py'),
                      verb, '--caller-uuid', state['caller_surface_uuid'], *args])


def active(state, now):
    value = state.get('continuation') or {}
    if (value.get('proactive_hook') is not True or value.get('status') != 'ACTIVE'
            or value.get('enabled') is not True
            or value.get('reservation_inquiries') is not True
            or state.get('state') not in (reask.WAITING, reask.ANSWERED)
            or reask.stop_file(state['caller_surface_uuid']).exists()
            or protocol.state_controller(state) != reask.controller()):
        return False
    preview = copy.deepcopy(state)
    lifecycle.poll_authorization(preview, now, publish=False)
    return (preview.get('state') != reask.STOPPED
            and scheduler.registration_observation(state) == 'REGISTERED')


def instruction(state):
    if state['state'] == reask.ANSWERED:
        reply = state.get('reply') or {}
        digest = reply.get('body_sha256')
        if not digest:
            return None
        return ('[MAC task continuation] Supervisor reply detected. Read the COMPLETE '
                f'body in {reask.state_path(state["caller_surface_uuid"])}; then run '
                + command(state, 'consume', '--reply-sha256', digest)
                + '. Follow the reply and continue the original task. This hook has '
                'not read, consumed or accepted it for you.')
    return ('[MAC task continuation] Your explicitly authorized task is still waiting '
            'for the supervisor. Continue useful assigned work; when only its reply '
            'remains, run ' + command(state, 'run', '--max-seconds', '60')
            + '. Each due round asks for review, progress or the next task. An '
            'unconfirmed original input is reconciled without repasting; file '
            'inquiries continue. Busy/Goal/reservation is not a communication ban. '
            'If still WAITING_REPLY, repeat this bounded runner instead of ending. '
            'If supervisor failure is verified, coordinate with the original '
            'same-task Claude peer and keep independent work moving. Never replay '
            'a callback or claim queue/Enter as reception. Explicit user stop, '
            'revocation, completed task or an actual reply ends this obligation.')


def decide(payload, *, now=None):
    if (not isinstance(payload, dict) or payload.get('hook_event_name') != 'Stop'
            or payload.get('agent_id') or payload.get('agent_type')):
        return None
    # Cheap selector only locates the candidate file. Original process/session
    # and parent ancestry below confer authority; inherited UUIDs alone do not.
    try:
        caller = str(uuid.UUID(os.environ.get('CMUX_SURFACE_ID', ''))).upper()
    except (ValueError, TypeError):
        return None
    with reask.episode_lock(caller):
        state = reask.load(caller) or {}
        if not state or not active(state, time.time() if now is None else now):
            return None
        from executor_reply_advisory import check_executor
        import cmux_identity_budget as budget
        with budget.limit(2.5):
            check_executor(state.get('executor_identity'), payload)
        reason = instruction(state)
        # stop_hook_active is deliberately not an escape for this explicit
        # continuing obligation. Every invocation rechecks stop and authority.
        return dict(decision='block', reason=reason) if reason else None
