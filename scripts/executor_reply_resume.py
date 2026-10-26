"""Explicit exact-reply consumption by a resumed original Claude session.

No identity or enrollment is rebound. The complete original controller, native
session anchor and raw ANSWERED state survive in the consumption receipt.
Only the actual resumed foreground executor can preview/apply this operation.
"""
from contextlib import contextmanager
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time

import cmux_evidence_io as evidence
import cmux_native_delivery as native
import executor_ready as ready
import executor_reask as reask
import executor_reask_identity as identity
import executor_reask_lifecycle as lifecycle
import executor_reask_protocol as protocol

PLAN_SCHEMA = 'resumed-original-claude-exact-reply-plan-v1'
AUTH_SOURCE = 'EXPLICIT_RESUMED_ORIGINAL_CLAUDE_REPLY'
STATE_BYTES = 256 * 1024


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def approved_controller():
    """File compatibility alone cannot authorize this foreground operation."""
    import configuration_guardian as guardian
    current, pins = guardian.verify_release(Path.home(), ready.state_root() / 'CURRENT.json')
    source = Path(current['source'])
    member = 'scripts/executor_reply_resume.py'
    if source / member != Path(__file__).resolve() or member not in pins['files']:
        raise ValueError('resumed reply entrypoint is not in the complete approved CURRENT release')
    raw, _ = evidence.snapshot(ready.state_root() / 'CURRENT.json', limit=ready.MAX_JSON_BYTES)
    if json.loads(raw) != current:
        raise ValueError('CURRENT changed during complete release verification')
    entrypoint, _ = evidence.snapshot(source / member, limit=guardian.MAX_RELEASE_BYTES)
    if (len(entrypoint) != pins['files'][member]['bytes']
            or hashlib.sha256(entrypoint).hexdigest() != pins['files'][member]['sha256']):
        raise ValueError('approved resumed reply entrypoint changed during verification')
    invoked = reask.controller()
    if invoked['source'] != str(source):
        raise ValueError('resumed reply command is not from the same approved CURRENT controller')
    return dict(source=str(source), current_sha256=hashlib.sha256(raw).hexdigest(),
                entrypoint_sha256=hashlib.sha256(entrypoint).hexdigest(), controller=invoked)


@contextmanager
def existing_episode_lock(caller):
    """A preview never creates an episode directory, state, or lock."""
    path = reask.root() / (ready.digest(caller) + '.lock')
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    with os.fdopen(fd, 'r+') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise reask.EpisodeBusy('original episode lock is busy') from exc
        yield


def _answered_state(caller, original_source, reply_sha256, now):
    raw, stamp = evidence.snapshot(reask.state_path(caller), limit=STATE_BYTES)
    state = json.loads(raw)
    original = protocol.state_controller(state)
    if (not isinstance(state, dict) or state.get('caller_surface_uuid') != caller
            or original is None or original['source'] != original_source
            or not protocol.supports_state(state)):
        raise ValueError('complete original episode/controller pins are required')
    if state.get('state') != reask.ANSWERED:
        raise ValueError('only the exact original ANSWERED reply can be consumed')
    if reask.stop_file(caller).exists():
        raise ValueError('operator stop forbids resumed consumption')
    authorized = copy.deepcopy(state)
    lifecycle.poll_authorization(authorized, now, publish=False)
    if authorized.get('state') == reask.STOPPED:
        raise ValueError('original continuing authorization is stopped, expired or changed')
    reply = state.get('reply')
    if (not isinstance(reply, dict) or not protocol.sha256_shape(reply_sha256)
            or reply.get('body_sha256') != reply_sha256
            or reask.reply_record(reply.get('body'), reply.get('source'))['body_sha256'] != reply_sha256):
        raise ValueError('FULL-read the original reply and supply its exact canonical --reply-sha256')
    body = reply['body']
    for key, expected in (('caller_surface_uuid', caller), ('supervisor_uuid', state['supervisor']),
                          ('task_id', state['task_id']), ('episode_id', state['episode_id'])):
        if body.get(key) != expected:
            raise ValueError('original reply scope changed: ' + key)
    if (body.get('marker') != reply.get('marker')
            or state.get('continuation') and not lifecycle.valid_wait_contract(body, state)):
        raise ValueError('original reply marker or waiting scope changed')
    for key in ('next_due_epoch', 'deadline_epoch'):
        if key in body:
            lifecycle.require_epoch(body[key], 'reply.' + key)
    if state.get('continuation'):
        lifecycle.require_epoch(state.get('next_due_epoch', now), 'state.next_due_epoch')
    source = Path(reply.get('path', ''))
    if (reply.get('source') not in ('mailbox', 'channel') or not source.is_absolute()
            or source.is_symlink() or source.resolve() != source):
        raise ValueError('only a recheckable exact original file reply is supported')
    reply_raw, reply_stamp = evidence.snapshot(source, limit=reask.CHANNEL_FILE_BYTES)
    if (reply.get('raw_sha256') != hashlib.sha256(reply_raw).hexdigest()
            or reply.get('raw_bytes') != len(reply_raw)
            or list(reply_stamp) != reply.get('source_identity')):
        raise ValueError('original reply file bytes or identity changed')
    checked, checked_sha = lifecycle._recheck_source(reply)
    if checked != 'PARSED' or checked_sha != reply_sha256:
        raise ValueError('original reply source no longer supplies the exact canonical body')
    return state, raw, stamp


def _resumed_authentication(state):
    original = state.get('executor_identity')
    if (not isinstance(original, dict) or original.get('schema') != identity.SCHEMA
            or original.get('provider') != 'claude'
            or original.get('episode_binding') != identity._episode(state)
            or original.get('caller_surface_uuid') != state['caller_surface_uuid']
            or original.get('workspace_uuid') != state['workspace_uuid']):
        raise ValueError('the original native Claude consumer identity is required')
    # Only affirmative kernel absence qualifies. A timeout, permission failure,
    # recycled PID, another provider or an alive original client grants nothing.
    try:
        native.identity.process(original['process']['pid'], validate_argv=False)
    except native.identity.ProcessExited:
        pass
    else:
        raise ValueError('original consumer PID is still present; no resumed-consumer authority')
    row = native._tree_row(ready._bridge(), original['caller_surface_uuid'])
    if (row['tty'] != original['tty']
            or not identity._same_uuid(row['workspace_uuid'], original['workspace_uuid'])
            or not identity._same_uuid(row['target_pane_uuid'], original['pane_uuid'])):
        raise ValueError('original workspace, pane or TTY changed')
    process = native._target_process(row)
    public = native._public_process(process)
    if (native._provider(process) != 'claude'
            or public['pid'] == original['process']['pid']
            or tuple(public['birth']) <= tuple(original['process']['birth'])
            or public['executable'] != original['process']['executable']
            or public['file_identity'] != original['process']['file_identity']):
        raise ValueError('only a later process of the same original native Claude executable is supported')
    # Keep the original journal anchor; no fabricated replacement session,
    # transcript inode, boundary, birth or inherited-selector fallback.
    resumed = dict(copy.deepcopy(original), process=public)
    identity.check_live(resumed)
    caller_proof = identity._claude_ancestry(resumed)
    identity.check_live(resumed)
    proof = dict(schema=identity.AUTH_SCHEMA, source=AUTH_SOURCE, provider='claude',
                 session_id=original['session_id'], process=public,
                 episode_id=state['episode_id'], caller_proof=caller_proof,
                 original_identity=copy.deepcopy(original),
                 scope='EXACT_ANSWERED_FILE_REPLY_ONLY')
    proof['identity_sha256'] = digest(dict(original_identity=original, resumed_process=public,
                                         source=AUTH_SOURCE))
    return proof


def _plan(caller, original_source, reply_sha256, now):
    current = approved_controller()
    state, raw, stamp = _answered_state(caller, original_source, reply_sha256, now)
    authentication = _resumed_authentication(state)
    original = state['executor_identity']
    plan = dict(schema=PLAN_SCHEMA, current=current, original_controller=protocol.state_controller(state),
                caller_surface_uuid=caller, workspace_uuid=state['workspace_uuid'],
                pane_uuid=original['pane_uuid'], tty=original['tty'],
                session_id=original['session_id'], task_id=state['task_id'],
                episode_id=state['episode_id'], marker=state['reply']['marker'],
                reply_sha256=reply_sha256, state_sha256=hashlib.sha256(raw).hexdigest(),
                state_identity=list(stamp), original_identity_sha256=digest(original),
                resumed_process=authentication['process'],
                operation='CONSUME_ONE_EXACT_REPLY_WITHOUT_REBINDING',
                original_controller_preserved=True, enrollment_preserved=True,
                reservations_preserved=True, input_operations=0, new_asks=0)
    return plan, state, raw, authentication


def evaluate(caller, original_source, reply_sha256, *, apply=False, plan_sha256='', now=None):
    wall_clock = now is None
    now = time.time() if now is None else now
    lifecycle.require_epoch(now, 'resumed_consumed_epoch')
    committing = False
    try:
        with existing_episode_lock(caller):
            plan, state, raw, authentication = _plan(caller, original_source, reply_sha256, now)
            expected = digest(plan)
            if not apply:
                return dict(outcome='RESUMED_REPLY_CONSUMPTION_PREVIEW', plan=plan,
                            plan_sha256=expected, input_operations=0,
                            state_changed=False, consumed=False, reservation_released=False)
            if not protocol.sha256_shape(plan_sha256) or plan_sha256 != expected:
                raise ValueError('exact reviewed --plan-sha256 required; preserve the old preview and re-evaluate')
            # Repeat the original-source and actual-caller checks at the commit
            # boundary, after the complete reviewed semantic plan was matched.
            commit_now = time.time() if wall_clock else now
            again, again_raw, again_stamp = _answered_state(caller, original_source, reply_sha256, commit_now)
            repeated = _resumed_authentication(again)
            if (again_raw != raw or list(again_stamp) != plan['state_identity']
                    or repeated['identity_sha256'] != authentication['identity_sha256']):
                raise ValueError('original state, reply or resumed process changed before consumption')
            if approved_controller() != plan['current']:
                raise ValueError('approved CURRENT changed before consumption')
            authentication = repeated
            # The first durable receipt also preserves the exact entire prior
            # state, including pending/UNKNOWN input and original process pins.
            authentication['pre_state'] = dict(sha256=hashlib.sha256(raw).hexdigest(),
                                                bytes=len(raw), utf8=raw.decode('utf-8'))
            authentication['reviewed_plan_sha256'] = expected
            committing = True
            result = lifecycle._consume_authenticated_locked(
                caller, again, authentication, reply_sha256, commit_now)
            consumed = result.get('consume_result', {}).get('consumed') is True
            return dict(outcome='RESUMED_ORIGINAL_REPLY_CONSUMED' if consumed else 'RESUMED_REPLY_NOT_CONSUMED',
                        consumed=consumed, plan_sha256=expected,
                        result=reask.summary(result), input_operations=0,
                        original_controller_preserved=True, original_identity_preserved=True,
                        enrollment_preserved=True, reservation_released=False, new_asks=0)
    except reask.CommittedSyncUnknown as exc:
        return dict(outcome='COMMITTED_SYNC_UNKNOWN', path=str(exc.path),
                    candidate_sha256=exc.candidate_sha256, input_operations=0,
                    next_action='VERIFY_EXACT_VISIBLE_CANDIDATE; RECEIPT_RETRY_USES_SAME_PLAN_AND_ORIGINAL_TIMESTAMP',
                    consumed='UNKNOWN', state_changed='UNKNOWN')
    except reask.EpisodeBusy:
        return dict(outcome='EPISODE_BUSY', input_operations=0, consumed=False,
                    state_changed=False, retry_same_arguments=True)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        return dict(outcome='RESUMED_CONSUMPTION_STATE_UNKNOWN' if committing else 'RESUMED_CONSUMPTION_REFUSED',
                    error=f'{type(exc).__name__}: {exc}'[:800], input_operations=0,
                    consumed='UNKNOWN' if committing else False,
                    state_changed='UNKNOWN' if committing else False,
                    reservation_released=False, new_asks=0)
