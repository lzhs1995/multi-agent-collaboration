"""Explicit continuing authorization; file state only, never input or hook loops."""
import copy
import hashlib
import json
import math
from pathlib import Path
import secrets
import time

import executor_ready as ready
from cmux_evidence_io import read_bytes

WAIT_STATUSES = {'WAITING_DEPENDENCY', 'SOLO', 'BLOCKED'}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def authorization_pin(path):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or path.resolve() != path:
        raise ValueError('authorization must be a canonical absolute regular file')
    raw = read_bytes(path, limit=65536)
    if not raw.strip():
        raise ValueError('empty continuation authorization')
    return dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))


def validate_authorization(value, controller, now):
    if not isinstance(value, dict) or value.get('enabled') is not True:
        raise ValueError('explicit continuing authorization required')
    task = value.get('overall_task')
    if not isinstance(task, str) or not task.strip() or len(task) > 4096:
        raise ValueError('overall task is required for continuing inquiries')
    until = value.get('until_epoch')
    if until is not None and (not finite(until) or until <= now):
        raise ValueError('authorization expiry must be a finite future time')
    pin = authorization_pin(value.get('authorization_file', ''))
    return dict(enabled=True, id=secrets.token_hex(16), status='ACTIVE',
                overall_task=task, owner=value['owner'], authorization=pin,
                authorized_epoch=now, until_epoch=until, controller=controller)


def binding(value):
    """Immutable authorization identity participates in scheduler enrollment."""
    if not value:
        return None
    keys = ('enabled', 'id', 'overall_task', 'owner', 'authorization',
            'authorized_epoch', 'until_epoch', 'controller')
    return {key: copy.deepcopy(value[key]) for key in keys}


def valid_wait_contract(body, state):
    if body['status'] not in WAIT_STATUSES:
        return True
    due, deadline = body.get('next_due_epoch'), body.get('deadline_epoch')
    return (body.get('owner') == state['supervisor']
            and finite(due) and finite(deadline) and 0 < due <= deadline)


def poll_authorization(state, now):
    import executor_reask as reask
    value = state.get('continuation')
    if not value or state['state'] == reask.STOPPED:
        return
    reason = None
    if value.get('status') != 'ACTIVE':
        reason = 'AUTHORIZATION_INACTIVE'
    elif value.get('until_epoch') is not None and now >= value['until_epoch']:
        reason = 'AUTHORIZATION_EXPIRED'
    else:
        try:
            if authorization_pin(value['authorization']['path']) != value['authorization']:
                reason = 'AUTHORIZATION_CHANGED'
        except (OSError, ValueError, KeyError):
            reason = 'AUTHORIZATION_UNAVAILABLE'
    if reason:
        value['status'] = reason
        state.update(state=reask.STOPPED, stopped_epoch=now, exit_reason=reason)
        marker = state['asks'][-1]['marker'] if state['asks'] else ''
        reask.write_channels(state, marker, reason, now, status=reask.STOPPED)


def consume(caller, reply_sha256='', *, now=None):
    """The foreground executor acknowledges the complete pinned reply body.

    Scheduler detection never calls this function. The CLI authenticates the
    original executor before this state mutation and requires its body hash.
    """
    import executor_reask as reask
    now = time.time() if now is None else now
    with reask.episode_lock(caller):
        state = reask.load(caller) or {}
        if not state:
            return dict(outcome='NO_EPISODE')
        if reask.stop_file(caller).exists():
            return reask.tick_locked(caller, now, file_only=True)
        poll_authorization(state, now)
        if state.get('state') != reask.ANSWERED:
            reask.save(state)
            return state
        reply = state['reply']
        continuing = bool(state.get('continuation'))
        if continuing and (not reply_sha256 or reply_sha256 != reply.get('body_sha256')):
            raise ValueError('read the complete reply body and supply its exact --reply-sha256')
        body = reply.get('body', {})
        if continuing and reask.reply_record(body, reply['source'])['body_sha256'] != reply_sha256:
            raise ValueError('stored reply body changed')
        marker = reply.get('marker') or body.get('marker', '')
        receipt = dict(schema='executor-reply-consumed-v1', caller_surface_uuid=caller,
                       supervisor=state['supervisor'], task_id=state['task_id'],
                       episode_id=state['episode_id'], marker=marker,
                       detected_epoch=reply.get('at_epoch'), consumed_epoch=now,
                       body_sha256=reply.get('body_sha256'), body=body,
                       consumption='EXPLICIT_FOREGROUND_EXECUTOR_ACK')
        path = reask.root() / 'consumed' / ready.digest(caller) / (ready.digest(
            state['episode_id'] + ':' + marker) + '.json')
        prior = ready.read_json(path)
        if prior:
            if any(prior.get(k) != receipt[k] for k in ('episode_id', 'marker', 'body_sha256')):
                raise ValueError('original consumption receipt mismatch')
            receipt = prior
            now = prior['consumed_epoch']
        else:
            ready.write_json(path, receipt)
        state.update(state=reask.CONSUMED, reply_consumed_epoch=now)
        state['reply_history'] = (state.get('reply_history', []) + [dict(
            marker=marker, status=reply['status'], detected_epoch=reply.get('at_epoch'),
            consumed_epoch=now, body_sha256=reply.get('body_sha256'), receipt=str(path))])[-reask.HISTORY:]
        state['replies_closed_through'] = state.get('attempt_count', 0)
        if continuing:
            # TASK/DISPATCHED text is only a reply. The formal task reservation
            # below pauses inquiries; a mailbox assertion cannot dispatch work.
            state['state'] = reask.WAITING
            state['rearm_count'] = state.get('rearm_count', 0) + 1
            state['next_due_epoch'] = max(now, state.get('next_due_epoch', now),
                                          body.get('next_due_epoch', now))
            state['last_reply'] = reply
            state['reply'] = None
        reask.write_channels(state, marker, 'reply explicitly consumed', now, status=reask.CONSUMED)
        reask.save(state)
        return state


def task_boundary(marker, event, *, now=None):
    """Reserve/release tasks from the formal harness, not from mailbox prose.

    A reservation pauses inquiries before input; it is not dispatch evidence.
    This helper never enables continuation, changes controllers, or sends input.
    """
    import executor_reask as reask
    now = time.time() if now is None else now
    participants = marker.get('participants', [])
    supervisors = [p for p in participants if p.get('role') == 'supervisor']
    if len(supervisors) != 1 or event not in ('RESERVED', 'RELEASED'):
        return []
    results = []
    for participant in participants:
        caller = participant.get('surface_uuid')
        if participant.get('role') != 'executor' or not caller:
            continue
        try:
            with reask.episode_lock(caller):
                state = reask.load(caller)
                if (not state or not state.get('continuation')
                        or state['continuation'].get('controller') != reask.controller()
                        or state.get('supervisor') != supervisors[0].get('surface_uuid')
                        or state.get('workspace_uuid') != marker.get('workspace_uuid')):
                    continue
                poll_authorization(state, now)
                if state['state'] == reask.STOPPED or reask.stop_file(caller).exists():
                    continue
                paused = state.setdefault('paused_tasks', [])
                reservation = dict(collaboration_id=marker['collaboration_id'], task_id=marker['task_id'])
                if event == 'RESERVED' and reservation not in paused:
                    paused.append(reservation)
                elif event == 'RELEASED' and reservation in paused:
                    paused.remove(reservation)
                    state['next_due_epoch'] = max(now, state.get('next_due_epoch', now))
                state['last_task_boundary'] = dict(event=event, at_epoch=now, **reservation)
                reask.save(state)
                results.append(dict(caller=caller, event=event, paused_tasks=len(paused)))
        except BlockingIOError:
            # Failure is visible to the explicit harness caller, never silently
            # reported as a successful reservation or used to authorize input.
            raise RuntimeError('inquiry state busy; task boundary was not recorded')
    return results
