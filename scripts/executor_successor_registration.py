"""Explicit original-executor successor enrollment, preserving old evidence.

Preview is read-only. Apply requires its exact plan hash, user authority and the
same original native executor. This grants inquiries only, never callback,
shared-write or resource ownership. Current same-workspace pair rules remain.
"""
import hashlib
import json
from pathlib import Path
import time
from contextlib import ExitStack

from cmux_evidence_io import snapshot
import cmux_native_delivery as native
import executor_reask as reask
import executor_ready as ready
import executor_reask_scheduler as scheduler

AUTH_SCHEMA = 'executor-successor-inquiry-authority-v1'
PLAN_SCHEMA = 'executor-successor-inquiry-plan-v1'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()


def _pin(path, *, absent=False):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path or path.is_symlink():
        raise ValueError('SUCCESSOR_CANONICAL_FILE_REQUIRED')
    try:
        raw, _identity = snapshot(path, limit=ready.MAX_JSON_BYTES)
    except FileNotFoundError:
        if absent:
            return dict(path=str(path), bytes=0, sha256='ABSENT'), None
        raise
    return dict(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()), raw


def authority(path, expected_sha, state, supervisor, session, now, *, resume_stopped=False):
    pin, raw = _pin(path)
    if pin['bytes'] > 65536 or pin['sha256'] != expected_sha:
        raise ValueError('SUCCESSOR_AUTHORIZATION_EXACT_SHA_REQUIRED')
    value = json.loads(raw)
    old = state.get('executor_identity') or {}
    expected = dict(schema=AUTH_SCHEMA, authorization_source='user_message',
        scope='continuing_inquiries_only', caller_surface_uuid=state['caller_surface_uuid'],
        executor_session_id=old.get('session_id'), workspace_uuid=state['workspace_uuid'],
        original_episode_id=state['episode_id'], original_supervisor_uuid=state['supervisor'],
        successor_supervisor_uuid=supervisor, successor_session_id=session,
        overall_task=(state.get('continuation') or {}).get('overall_task', state['task_id']),
        allow_task_replay=False, allow_shared_writes=False, allow_callback_transfer=False,
        allow_resume_stopped=resume_stopped)
    if (not isinstance(value, dict) or any(value.get(k) != v for k, v in expected.items())
            or not isinstance(value.get('user_instruction'), str) or not value['user_instruction'].strip()
            or type(value.get('until_epoch')) not in (int, float) or not now < value['until_epoch'] < 253402300799):
        raise ValueError('SUCCESSOR_AUTHORIZATION_ORIGINAL_SCOPE_REQUIRED')
    return pin, value


def _target_identity(target):
    return dict(provider=target['provider'], session_id=target['session_id'],
        identity=target['identity'], tty=target['tty'], process=target['process'],
        transcript={key: target['transcript'][key] for key in ('path', 'device', 'inode')})


def _live_pair(live):
    return {key: live[key] for key in (*scheduler.LIVE_KEYS, 'caller_pane_uuid') if key in live}


def build_plan(caller, supervisor, auth_file, auth_sha, *, now=None, resume_stopped=False):
    now = time.time() if now is None else now
    from executor_reask_lifecycle import require_epoch
    require_epoch(now, 'successor_registration_epoch')
    state_pin, raw = _pin(reask.state_path(caller))
    state = json.loads(raw)
    if state.get('caller_surface_uuid') != caller or state.get('state') not in (reask.WAITING, reask.ANSWERED, reask.STOPPED):
        raise ValueError('SUCCESSOR_ORIGINAL_EPISODE_REQUIRED')
    if (state.get('state') == reask.STOPPED or reask.stop_file(caller).exists()) and not resume_stopped:
        raise ValueError('SUCCESSOR_EXPLICIT_STOP_RESUME_AUTHORIZATION_REQUIRED')
    from executor_reask_protocol import supports_state, state_controller
    if not supports_state(state):
        raise ValueError('SUCCESSOR_ORIGINAL_CONTROLLER_UNVERIFIED')
    from executor_reask_identity import authenticate
    proof = authenticate(state)
    bridge = ready._bridge()
    live = bridge.pin_workspace(supervisor, caller_uuid=caller)
    if (live['caller_surface_uuid'] != caller or live['workspace_uuid'] != state['workspace_uuid']):
        raise ValueError('SUCCESSOR_CURRENT_ORIGINAL_PAIR_REQUIRED')
    text = 'STATUS: SUCCESSOR_INQUIRY_IDENTITY_ONLY'
    target = native.bind_target(bridge, live['target_surface_uuid'], text)
    if target['provider'] != 'codex':
        raise ValueError('SUCCESSOR_NATIVE_CODEX_SUPERVISOR_REQUIRED')
    auth_pin, value = authority(auth_file, auth_sha, state, live['target_surface_uuid'], target['session_id'], now,
        resume_stopped=resume_stopped)
    enrollment_pin, enrollment_raw = _pin(scheduler.enrollment_path(caller), absent=True)
    stop_pin, _stop_raw = _pin(reask.stop_file(caller), absent=True)
    if enrollment_raw and json.loads(enrollment_raw).get('schema') == scheduler.TRANSACTION_SCHEMA:
        raise ValueError('SUCCESSOR_PRESERVE_UNRESOLVED_ORIGINAL_REGISTRATION')
    plan = dict(schema=PLAN_SCHEMA, caller_surface_uuid=caller, original_episode_id=state['episode_id'],
        task_id=state['task_id'], overall_task=value['overall_task'], original_state=state_pin,
        original_enrollment=enrollment_pin, original_stop=stop_pin,
        original_controller=state_controller(state),
        authorization=auth_pin, authorization_until_epoch=value['until_epoch'],
        target=_target_identity(target), live_pair=_live_pair(live),
        executor_identity=dict(session_id=proof['session_id'], process=proof['process'],
            identity_sha256=proof['identity_sha256']), current_controller=reask.controller(),
        terminal_pending_preserved=True, original_callbacks_transferred=False,
        shared_write_authority_transferred=False, input_operations=0,
        resume_stopped=resume_stopped, file_only=True, proactive_hook=True,
        reservation_inquiries=True)
    return plan


def prepare_transition(plan, previous, live, *, now):
    """Called under the original registry/episode locks before any commit."""
    if not isinstance(plan, dict) or plan.get('schema') != PLAN_SCHEMA:
        raise ValueError('SUCCESSOR_REVIEWED_PLAN_REQUIRED')
    fresh = build_plan(plan['caller_surface_uuid'], plan['target']['identity']['target_surface_uuid'],
        plan['authorization']['path'], plan['authorization']['sha256'], now=now,
        resume_stopped=plan['resume_stopped'])
    if (fresh != plan or not previous or previous.get('episode_id') != plan['original_episode_id']
            or _live_pair(live) != plan['live_pair']):
        raise ValueError('SUCCESSOR_REVIEWED_PLAN_CHANGED')
    state_raw = snapshot(reask.state_path(plan['caller_surface_uuid']), limit=ready.MAX_JSON_BYTES)[0]
    enrollment_raw = scheduler._raw(scheduler.enrollment_path(plan['caller_surface_uuid']))
    stop_raw = scheduler._raw(reask.stop_file(plan['caller_surface_uuid']))
    directory = reask.root() / 'successor-preimages' / ready.digest(plan['caller_surface_uuid']) / digest(plan)
    reask.snapshot_files(directory, {'state.json': state_raw, 'enrollment.json': enrollment_raw, 'stop.json': stop_raw},
        dict(schema='executor-successor-original-files-v1', plan_sha256=digest(plan),
            original_episode_id=plan['original_episode_id']))
    return dict(plan_sha256=digest(plan), authorization=plan['authorization'],
        original_episode_id=plan['original_episode_id'], original_controller=plan['original_controller'],
        original_files=str(directory), target=plan['target'], input_operations=0,
        original_supervisor_uuid=previous['supervisor'],
        inherited_reservations=previous.get('paused_tasks', []),
        original_callbacks_transferred=False, shared_write_authority_transferred=False)


def validate_commit(plan, previous, *, now):
    """Last read-only endpoint/authentication check before the state commit."""
    state_pin, _raw = _pin(reask.state_path(plan['caller_surface_uuid']))
    stop_pin, _raw_stop = _pin(reask.stop_file(plan['caller_surface_uuid']), absent=True)
    if (state_pin != plan['original_state'] or stop_pin != plan['original_stop']
            or reask.controller() != plan['current_controller']):
        raise ValueError('SUCCESSOR_ORIGINAL_STATE_OR_CONTROLLER_CHANGED')
    from executor_reask_identity import authenticate
    proof = authenticate(previous)
    expected = dict(session_id=proof['session_id'], process=proof['process'], identity_sha256=proof['identity_sha256'])
    if expected != plan['executor_identity']:
        raise ValueError('SUCCESSOR_ORIGINAL_EXECUTOR_CHANGED_BEFORE_COMMIT')
    bridge = ready._bridge()
    live = bridge.pin_workspace(plan['target']['identity']['target_surface_uuid'],
        caller_uuid=plan['caller_surface_uuid'])
    target = native.bind_target(bridge, live['target_surface_uuid'], 'STATUS: SUCCESSOR_INQUIRY_IDENTITY_ONLY')
    if _live_pair(live) != plan['live_pair'] or _target_identity(target) != plan['target']:
        raise ValueError('SUCCESSOR_NATIVE_TARGET_CHANGED_BEFORE_COMMIT')
    pin, _value = authority(plan['authorization']['path'], plan['authorization']['sha256'], previous,
        live['target_surface_uuid'], target['session_id'], now, resume_stopped=plan['resume_stopped'])
    if pin != plan['authorization']:
        raise ValueError('SUCCESSOR_AUTHORIZATION_CHANGED_BEFORE_COMMIT')


def register(caller, supervisor, auth_file, auth_sha, *, apply=False, plan_sha256='', now=None, resume_stopped=False):
    realtime = now is None
    now = time.time() if now is None else now
    caller = scheduler.canonical_uuid(caller)
    with ExitStack() as stack:
        try:
            stack.enter_context(reask.episode_lock(caller))
        except reask.EpisodeBusy:
            return dict(outcome='EPISODE_BUSY', input_operations=0)
        plan = build_plan(caller, supervisor, auth_file, auth_sha, now=now, resume_stopped=resume_stopped)
    sha = digest(plan)
    if not apply:
        return dict(outcome='SUCCESSOR_READ_ONLY_PLAN', plan=plan, plan_sha256=sha,
            input_operations=0, registration_changed=False)
    if sha != plan_sha256:
        raise ValueError('SUCCESSOR_REVIEWED_PLAN_SHA_REQUIRED')
    continuation = dict(enabled=True, overall_task=plan['overall_task'], owner=plan['target']['identity']['target_surface_uuid'],
        authorization_file=plan['authorization']['path'], until_epoch=plan['authorization_until_epoch'],
        reservation_inquiries=True, proactive_hook=True)
    state = scheduler.start(plan['live_pair'], now=None if realtime else now, authenticate_executor=True,
        task_id=plan['task_id'], continuing=True, continuation=continuation,
        file_only=True, executor_ref=caller, resume_stopped=resume_stopped,
        successor_transition=plan)
    return dict(outcome='SUCCESSOR_INQUIRY_REGISTERED' if state.get('caller_surface_uuid') and scheduler.registered(state) else
        state.get('outcome', state.get('registration_observation', 'REGISTRATION_UNCOMMITTED')),
        registration_state=state, plan_sha256=sha, input_operations=0,
        original_callbacks_transferred=False, original_pending_replayed=False)
