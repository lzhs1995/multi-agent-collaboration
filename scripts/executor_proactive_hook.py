"""Task-scoped Claude Stop continuation; no input or implicit enrollment.

Only an original, explicitly authorized and fully verified inquiry participates.
The foreground runner owns transport and its one-attempt journal. This hook
keeps the executor working until a real reply is read or authority ends.
"""
import copy
from contextlib import contextmanager
import fcntl
import math
import json
import os
from pathlib import Path
import shlex
import re
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
    return shlex.join([original['python'], '-B',
                      str(Path(original['source']) / 'scripts/executor_reask.py'),
                      verb, '--caller-uuid', state['caller_surface_uuid'], *args])


def active(state, now):
    value = state.get('continuation') or {}
    if (value.get('proactive_hook') is not True or value.get('status') != 'ACTIVE'
            or value.get('enabled') is not True
            or value.get('reservation_inquiries') is not True
            or state.get('state') not in (reask.WAITING, reask.ANSWERED)
            or reask.stop_file(state['caller_surface_uuid']).exists()
            or not protocol.supports_state(state)):
        return False
    preview = copy.deepcopy(state)
    lifecycle.poll_authorization(preview, now, publish=False)
    return (preview.get('state') != reask.STOPPED
            and scheduler.registration_observation(state) == 'REGISTERED')


def confirmed_standby(state, now):
    """A read+consumed, task-bound WAIT reply from the original supervisor.

    STANDBY exists only as this exact evidence: the episode is WAITING with no
    unconsumed reply and no formal reservation; its last reply is a WAIT status
    whose body SHA, marker and episode match an EXPLICIT_FOREGROUND_EXECUTOR_ACK
    receipt; and the reply's own next_due_epoch is still in the future. Never
    inferred from silence, an error, a scheduled wakeup or state.next_due_epoch.
    """
    if (state.get('state') != reask.WAITING or state.get('reply')
            or state.get('paused_tasks')
            or state.get('replies_closed_through') != state.get('attempt_count')):
        return None
    last = state.get('last_reply')
    history = state.get('reply_history') or []
    if not isinstance(last, dict) or not history or not isinstance(history[-1], dict):
        return None
    body, row = last.get('body'), history[-1]
    try:
        if (not isinstance(body, dict) or body.get('status') not in lifecycle.WAIT_STATUSES
                or row.get('marker') != last.get('marker')
                or row.get('body_sha256') != last.get('body_sha256')
                or reask.reply_record(body, last.get('source'))['body_sha256'] != last.get('body_sha256')):
            return None
    except (KeyError, TypeError):
        return None
    path = (reask.root() / 'consumed' / ready.digest(state['caller_surface_uuid'])
            / (ready.digest(state['episode_id'] + ':' + str(last.get('marker'))) + '.json'))
    if row.get('receipt') != str(path):
        return None
    receipt = ready.read_json(path) or {}
    expected = dict(schema='executor-reply-consumed-v1', consumption='EXPLICIT_FOREGROUND_EXECUTOR_ACK',
                    caller_surface_uuid=state['caller_surface_uuid'], supervisor=state['supervisor'],
                    task_id=state.get('task_id', ''), episode_id=state['episode_id'],
                    marker=last.get('marker'), body_sha256=last.get('body_sha256'), body=body)
    if (any(receipt.get(k) != v for k, v in expected.items())
            or (receipt.get('executor_authentication') or {}).get('episode_id') != state['episode_id']
            or body.get('supervisor_uuid') != state['supervisor']
            or body.get('owner') != state['supervisor']
            or body.get('episode_id') != state['episode_id']
            or body.get('task_id') != state.get('task_id', '')
            or body.get('caller_surface_uuid') != state['caller_surface_uuid']):
        return None
    due = body.get('next_due_epoch')
    if not lifecycle.valid_epoch(due) or not lifecycle.valid_epoch(now) or now >= due:
        return None
    return dict(status=body['status'], marker=last['marker'], body_sha256=last['body_sha256'],
                next_due_epoch=due, trigger=body.get('trigger'), receipt=str(path))


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
                'not read, consumed or accepted it for you. An unconsumed reply is not '
                'STANDBY and releases no reservation.')
    if state.get('paused_tasks'):
        # apply_boundary only records reservations owned by the original
        # supervisor; a foreign owner stops the episode instead.
        return ('[MAC task continuation] Formally assigned work from your original '
                'supervisor is reserved: ' + json.dumps(state['paused_tasks'], ensure_ascii=False)
                + '. Continue that task through its own harness and callback. Status '
                'inquiries are files only and never release it; only a formal RELEASED '
                'event does. Silence, an error or a scheduled wakeup is neither STANDBY '
                'nor release. When only the supervisor\'s status reply remains, run '
                + command(state, 'run', '--max-seconds', '60') + '. Explicit user stop, '
                'revocation or an actual reply ends this obligation.')
    files = state.get('file_only_preference') is True
    return ('[MAC task continuation] Your explicitly authorized task is still waiting '
            'for the supervisor. Continue useful assigned work; when only its reply '
            'remains, run ' + command(state, 'run', '--max-seconds', '60')
            + '. Each due round asks for review, progress or the next task. '
            + ('This episode is file-only: each round writes a file inquiry and sends '
               'no terminal or native input, so it cannot wake an idle supervisor. '
               if files else '')
            + 'An '
            'unconfirmed original input is reconciled without repasting; file '
            'inquiries continue. Busy/Goal/reservation is not a communication ban. '
            'If still WAITING_REPLY, repeat this bounded runner instead of ending. '
            'If supervisor failure is verified, coordinate with the original '
            'same-task Claude peer and keep independent work moving. Never replay '
            'a callback or claim queue/Enter as reception. Explicit user stop, '
            'revocation, completed task or an actual reply ends this obligation.')


class CadenceActive(BlockingIOError):
    pass


def cadence_path(caller):
    return ready.state_root() / 'executor-proactive-v1' / (ready.digest(caller) + '.json')


@contextmanager
def cadence_lock(caller):
    path = cadence_path(caller)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path.with_suffix('.lock'), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as handle:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CadenceActive('another task continuation hook is active') from exc
        yield path


def authorized_snapshot(caller, payload, now):
    with reask.episode_lock(caller):
        state = reask.load(caller) or {}
        if not state or not active(state, now):
            return None
        from executor_reply_advisory import check_executor
        import cmux_identity_budget as budget
        with budget.limit(2.5):
            check_executor(state.get('executor_identity'), payload)
        return state


def pending_handshakes(state):
    """Small file hints only: reading one never sends an ACK or proves receipt."""
    from cmux_evidence_io import read_bytes
    root = lifecycle.ACTIVE_DIR / state['workspace_uuid']
    hints = []
    try:
        with os.scandir(root) as entries:
            for index, entry in enumerate(entries):
                if index >= 256 or len(hints) >= 8:
                    break
                if not entry.name.endswith('.json') or entry.is_symlink():
                    continue
                try:
                    marker = json.loads(read_bytes(entry.path, limit=65536))
                    if marker.get('workspace_uuid') != state['workspace_uuid']:
                        continue
                    peers = marker.get('participants', [])
                    owners = [p for p in peers if p.get('role') == 'supervisor']
                    workers = [p for p in peers if p.get('role') == 'executor'
                        and p.get('surface_uuid') == state['caller_surface_uuid']
                        and p.get('native_session_id') == state['executor_identity']['session_id']]
                    if len(owners) != 1 or owners[0].get('surface_uuid') != state['supervisor'] or len(workers) != 1:
                        continue
                    artifact = Path(marker['artifact_root'])
                    if not artifact.is_absolute() or artifact.resolve() != artifact:
                        continue
                    path = artifact / 'handshake-receipt.json'
                    receipt = json.loads(read_bytes(path, limit=1024 * 1024))
                    worker = workers[0]
                    for row in receipt.get('executors', [receipt]):
                        if (row.get('task_id') == marker.get('task_id')
                                and row.get('executor') in (worker['surface_uuid'], worker.get('surface_ref'))
                                and row.get('executor_ack') is False
                                and re.fullmatch('[0-9a-f]{8}', row.get('ack_nonce', ''))):
                            hints.append(dict(path=str(path), task_id=row['task_id'], nonce=row['ack_nonce']))
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    continue
    except FileNotFoundError:
        pass
    return hints


def urgent_hint(state, previous):
    # Check the issued mailbox directly; a scheduler has not necessarily
    # promoted a newly written reply into ANSWERED yet. No mutation/consumption.
    reply = reask.mailbox_reply(copy.deepcopy(state))
    if reply and reply.get('body_sha256') != previous.get('mailbox_sha256'):
        return ('A bound supervisor mailbox reply appeared. Run ' + command(state, 'tick')
                + ', then read the complete reply and consume its exact SHA. ',
                dict(mailbox_sha256=reply.get('body_sha256')))
    for hint in pending_handshakes(state):
        token = ready.digest(json.dumps(hint, sort_keys=True))
        if token not in previous.get('handshake_hints', []):
            return ('A new pending handshake file requires prompt attention: '
                    + json.dumps(hint, ensure_ascii=False)
                    + '. Verify the original notice/body and respond to that nonce; '
                    'this file hint does not prove reception or authorize replay. ',
                    dict(handshake_hints=(previous.get('handshake_hints', []) + [token])[-32:]))
    return '', {}


def decide(payload, *, now=None, clock=None, sleep=None, monotonic=None):
    if (not isinstance(payload, dict) or payload.get('hook_event_name') != 'Stop'
            or payload.get('agent_id') or payload.get('agent_type')):
        return None
    # Cheap selector only locates the candidate file. Original process/session
    # and parent ancestry below confer authority; inherited UUIDs alone do not.
    try:
        caller = str(uuid.UUID(os.environ.get('CMUX_SURFACE_ID', ''))).upper()
    except (ValueError, TypeError):
        return None
    clock, sleep, monotonic = clock or time.time, sleep or time.sleep, monotonic or time.monotonic
    now = clock() if now is None else now
    state = authorized_snapshot(caller, payload, now)
    if state is None:
        return None
    with cadence_lock(caller) as path:
        previous = ready.read_json(path) or {}
        same = previous.get('episode_id') == state['episode_id']
        last = previous.get('prompted_at_epoch')
        reply_sha = (state.get('reply') or {}).get('body_sha256')
        new_reply = reply_sha and reply_sha != previous.get('reply_sha256')
        urgent, urgent_state = urgent_hint(state, previous if same else {})
        # An authentic read+consumed STANDBY lets the turn end without a delay;
        # a genuinely new urgent item is still surfaced.
        if confirmed_standby(state, now) and not urgent:
            return None
        delay = (min(55., max(0., float(last) + 60. - now))
                 if same and type(last) in (int, float) and math.isfinite(last)
                 and not new_reply and not urgent else 0.)
        # No episode lock is held during this bounded delay. The scheduler,
        # foreground runner and exact reply consumer remain free to progress.
        deadline = monotonic() + delay
        while monotonic() < deadline:
            sleep(min(1., deadline - monotonic()))
            current = reask.load(caller) or {}
            if (current.get('episode_id') != state['episode_id']
                    or reask.stop_file(caller).exists()):
                return None
            preview = copy.deepcopy(current)
            lifecycle.poll_authorization(preview, clock(), publish=False)
            if preview.get('state') == reask.STOPPED:
                return None
            urgent, urgent_state = urgent_hint(current, previous)
            if urgent:
                break
            if ((current.get('reply') or {}).get('body_sha256') or None) != (reply_sha or None):
                break
        if delay:
            state = authorized_snapshot(caller, payload, clock())
            if state is None:
                return None
        standby = confirmed_standby(state, clock())
        if standby and not urgent:
            return None
        if standby:
            reason = ('[MAC task continuation] A read and consumed '
                      f'{standby["status"]} reply (body {standby["body_sha256"]}) remains in '
                      'effect until its next_due_epoch; it does not cover the new item above.')
        else:
            reason = instruction(state)
        if not reason:
            return None
        ready.write_json(path, dict(schema='executor-proactive-cadence-v1',
            episode_id=state['episode_id'], prompted_at_epoch=clock(),
            reply_sha256=(state.get('reply') or {}).get('body_sha256'),
            mailbox_sha256=urgent_state.get('mailbox_sha256', previous.get('mailbox_sha256') if same else None),
            handshake_hints=urgent_state.get('handshake_hints', previous.get('handshake_hints', []) if same else []),
            prompt_count=(previous.get('prompt_count', 0) if same else 0) + 1))
        # Retain the host's native continuation cap. This prevents empty rapid
        # reentry, not API errors or an exited client; it never ends the task.
        return dict(decision='block', reason=urgent + reason)
