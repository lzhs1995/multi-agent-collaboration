"""Explicit original-executor successor enrollment, preserving old evidence.

Preview is read-only. Apply requires its exact plan hash, user authority and the
same original native executor. This grants inquiries only, never callback,
shared-write or resource ownership. Current same-workspace pair rules remain.
"""
import hashlib
import json
import math
import re
from pathlib import Path
import time
from contextlib import ExitStack

from cmux_evidence_io import snapshot
import cmux_native_delivery as native
import executor_reask as reask
import executor_ready as ready
import executor_reask_protocol as protocol
import executor_reask_scheduler as scheduler

AUTH_SCHEMA = 'executor-successor-inquiry-authority-v1'
PLAN_SCHEMA = 'executor-successor-inquiry-plan-v2'
MAX_REVIEW_COMPONENTS = 32
MAX_REVIEW_FIELDS = 128
MAX_REVIEW_BYTES = 262144
FIELD_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_.-]{0,95}')


class SuccessorDiagnosticError(ValueError):
    """A refusal, with bounded zero-input details; never recovery authority."""
    def __init__(self, reason, **details):
        super().__init__(reason)
        self.details = dict(reason=reason, input_operations=0,
                            automatic_retry_authorized=False, **details)


def _review_components(value):
    """Hash top-level semantic fields without exposing pending payload bodies."""
    if (len(value) > MAX_REVIEW_FIELDS
            or any(not FIELD_NAME.fullmatch(key) for key in value)):
        return dict(complete=False, fields={})
    return dict(complete=True, fields={key: digest(item) for key, item in value.items()})


def plan_difference(reviewed, current):
    """Field names and hashes only, capped before traversing diagnostics."""
    changes = []
    for key in ('caller_surface_uuid', 'original_episode_id',
                'original_state', 'original_enrollment', 'original_stop', 'target',
                'live_pair', 'executor_identity', 'authorization',
                'authorization_until_epoch', 'current_controller', 'original_controller',
                'task_id', 'overall_task', 'resume_stopped', 'file_only',
                'proactive_hook', 'reservation_inquiries'):
        before, after = reviewed.get(key), current.get(key)
        if before == after:
            continue
        item = dict(component=key)
        if isinstance(before, dict) and isinstance(after, dict):
            a, b = before.get('semantic_fields') or {}, after.get('semantic_fields') or {}
            if (isinstance(a, dict) and isinstance(b, dict)
                    and a.get('complete') is True and b.get('complete') is True
                    and isinstance(a.get('fields'), dict) and isinstance(b.get('fields'), dict)
                    and len(a['fields']) <= MAX_REVIEW_FIELDS and len(b['fields']) <= MAX_REVIEW_FIELDS):
                aa, bb = a.get('fields', {}), b.get('fields', {})
                fields = [k for k in sorted(set(aa) | set(bb)) if aa.get(k) != bb.get(k)]
                item['changed_fields'] = [k if FIELD_NAME.fullmatch(k)
                    else 'FIELD_SHA256_' + digest(k) for k in fields[:MAX_REVIEW_COMPONENTS]]
                item['changed_fields_complete'] = len(fields) <= MAX_REVIEW_COMPONENTS
            if 'semantic_sha256' in before or 'semantic_sha256' in after:
                item.update(reviewed_semantic_sha256=before.get('semantic_sha256'),
                            current_semantic_sha256=after.get('semantic_sha256'))
        changes.append(item)
    return changes

# A scheduler observation must not invalidate a reviewed transition. Everything
# else, including replies, input fences and authority, remains in the plan hash.
STATE_COUNTERS = ('check_count', 'reply_scan_ordinal')
STATE_POLL_FIELDS = ('at_epoch', 'duration_seconds')
OBSERVATION_CLOCK_TOLERANCE = 5.0


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('SUCCESSOR_DUPLICATE_JSON_KEY')
        value[key] = item
    return value


def _observed_epoch(value, *, now=None):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('SUCCESSOR_MALFORMED_OBSERVATION')
    if now is not None and value > now + OBSERVATION_CLOCK_TOLERANCE:
        raise ValueError('SUCCESSOR_FUTURE_OBSERVATION')


def _nonfinite_constant(_value):
    raise ValueError('SUCCESSOR_NONFINITE_JSON')


def _review_pin(pin, raw, *, state=False, episode=None, now=None):
    """Hash the full JSON except the narrow, validated polling observations."""
    if raw is None:
        return pin
    value = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_nonfinite_constant)
    if not isinstance(value, dict):
        raise ValueError('SUCCESSOR_ORIGINAL_OBJECT_REQUIRED')
    if state:
        for key in STATE_COUNTERS:
            if key in value:
                if type(value[key]) is not int or value[key] < 0:
                    raise ValueError('SUCCESSOR_MALFORMED_OBSERVATION')
                if key == 'reply_scan_ordinal':
                    count = value.get('attempt_count', 0)
                    if (type(count) is not int or count < 1
                            or not 1 <= value[key] <= count):
                        raise ValueError('SUCCESSOR_MALFORMED_REPLY_SCAN')
                del value[key]
        if 'last_poll' in value:
            poll = value['last_poll']
            if not isinstance(poll, dict) or not set(STATE_POLL_FIELDS).issubset(poll):
                raise ValueError('SUCCESSOR_MALFORMED_OBSERVATION')
            for key in STATE_POLL_FIELDS:
                if key in poll:
                    _observed_epoch(poll.pop(key), now=now if key == 'at_epoch' else None)
            if not poll:
                del value['last_poll']
    else:
        execution = value.get('last_execution')
        producer = scheduler.provenance()
        source = str(Path(producer['path']).parents[1])
        # Only this approved scheduler's zero-post sweep is observational.
        # A post changes episode.file_posts/asks and remains a semantic change.
        # Retain the producer in the semantic pin, including before its first
        # observation, so a changed source/provenance never disappears.
        neutral = dict(source=source, scheduler=producer,
                       state=(episode or {}).get('state'), file_posts=0, input_operations=0)
        if 'last_execution' not in value:
            value['last_execution'] = neutral
        else:
            if not isinstance(execution, dict):
                raise ValueError('SUCCESSOR_MALFORMED_OBSERVATION')
            if 'at_epoch' in execution:
                _observed_epoch(execution['at_epoch'], now=now)
            if (set(execution) == set(neutral) | {'at_epoch'}
                    and all(execution[key] == item and type(execution[key]) is type(item)
                            for key, item in neutral.items())):
                value['last_execution'] = neutral
    return dict(path=pin['path'], semantic_sha256=digest(value),
        semantic_fields=_review_components(value),
        comparison='validated-poll-observations-v1')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _pin(path, *, absent=False, limit=ready.MAX_JSON_BYTES):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path or path.is_symlink():
        raise ValueError('SUCCESSOR_CANONICAL_FILE_REQUIRED')
    try:
        raw, _identity = snapshot(path, limit=limit)
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


def build_plan(caller, supervisor, auth_file, auth_sha, *, now=None, resume_stopped=False,
               observations=None):
    now = time.time() if now is None else now
    from executor_reask_lifecycle import require_epoch
    require_epoch(now, 'successor_registration_epoch')
    state_pin, raw = _pin(reask.state_path(caller))
    state_review = _review_pin(state_pin, raw, state=True, now=now)
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
    enrollment_review = _review_pin(enrollment_pin, enrollment_raw, episode=state, now=now)
    stop_pin, _stop_raw = _pin(reask.stop_file(caller), absent=True)
    if enrollment_raw and json.loads(enrollment_raw).get('schema') == scheduler.TRANSACTION_SCHEMA:
        raise ValueError('SUCCESSOR_PRESERVE_UNRESOLVED_ORIGINAL_REGISTRATION')
    plan = dict(schema=PLAN_SCHEMA, caller_surface_uuid=caller, original_episode_id=state['episode_id'],
        task_id=state['task_id'], overall_task=value['overall_task'], original_state=state_review,
        original_enrollment=enrollment_review, original_stop=stop_pin,
        original_controller=state_controller(state),
        authorization=auth_pin, authorization_until_epoch=value['until_epoch'],
        target=_target_identity(target), live_pair=_live_pair(live),
        executor_identity=dict(session_id=proof['session_id'], process=proof['process'],
            identity_sha256=proof['identity_sha256']), current_controller=reask.controller(),
        terminal_pending_preserved=True, original_callbacks_transferred=False,
        shared_write_authority_transferred=False, input_operations=0,
        resume_stopped=resume_stopped, file_only=True, proactive_hook=True,
        reservation_inquiries=True)
    if observations is not None:
        observations.update(original_state=state_pin, original_enrollment=enrollment_pin,
                            original_stop=stop_pin)
    return plan


def prepare_transition(plan, previous, live, *, now):
    """Called under the original registry/episode locks before any commit."""
    if not isinstance(plan, dict) or plan.get('schema') != PLAN_SCHEMA:
        raise ValueError('SUCCESSOR_REVIEWED_PLAN_REQUIRED')
    preimages = {}
    fresh = build_plan(plan['caller_surface_uuid'], plan['target']['identity']['target_surface_uuid'],
        plan['authorization']['path'], plan['authorization']['sha256'], now=now,
        resume_stopped=plan['resume_stopped'], observations=preimages)
    if (fresh != plan or not previous or previous.get('episode_id') != plan['original_episode_id']
            or _live_pair(live) != plan['live_pair']):
        raise SuccessorDiagnosticError('SUCCESSOR_REVIEWED_PLAN_CHANGED',
            changes=plan_difference(plan, fresh), next_action='REVIEW_NEW_EXACT_PREVIEW_NO_AUTOMATIC_APPLY')
    state_raw = snapshot(reask.state_path(plan['caller_surface_uuid']), limit=ready.MAX_JSON_BYTES)[0]
    enrollment_raw = scheduler._raw(scheduler.enrollment_path(plan['caller_surface_uuid']))
    stop_raw = scheduler._raw(reask.stop_file(plan['caller_surface_uuid']))
    for key, raw in (('original_state', state_raw), ('original_enrollment', enrollment_raw),
                     ('original_stop', stop_raw)):
        observed = preimages[key]
        sha = hashlib.sha256(raw).hexdigest() if raw is not None else 'ABSENT'
        if observed['sha256'] != sha or observed['bytes'] != (len(raw) if raw is not None else 0):
            raise ValueError('SUCCESSOR_PREPARATION_PREIMAGE_CHANGED')
    directory = (reask.root() / 'successor-preimages' / ready.digest(plan['caller_surface_uuid'])
                 / digest(plan) / digest(preimages))
    reask.snapshot_files(directory, {'state.json': state_raw, 'enrollment.json': enrollment_raw, 'stop.json': stop_raw},
        dict(schema='executor-successor-original-files-v1', plan_sha256=digest(plan),
            original_episode_id=plan['original_episode_id']))
    return dict(plan_sha256=digest(plan), authorization=plan['authorization'], commit_preimages=preimages,
        original_episode_id=plan['original_episode_id'], original_controller=plan['original_controller'],
        original_files=str(directory), target=plan['target'], input_operations=0,
        original_supervisor_uuid=previous['supervisor'],
        inherited_reservations=previous.get('paused_tasks', []),
        original_callbacks_transferred=False, shared_write_authority_transferred=False)


def validate_commit(plan, previous, *, now, preimages):
    """Last read-only endpoint/authentication check before the state commit."""
    state_pin, _raw = _pin(reask.state_path(plan['caller_surface_uuid']))
    stop_pin, _raw_stop = _pin(reask.stop_file(plan['caller_surface_uuid']), absent=True)
    if (state_pin != preimages['original_state'] or stop_pin != preimages['original_stop']
            or reask.controller() != plan['current_controller']):
        raise ValueError('SUCCESSOR_ORIGINAL_STATE_OR_CONTROLLER_CHANGED')
    # The enrollment now contains the transaction envelope. Its immutable
    # preimages must be precisely those captured under the original locks.
    path = scheduler.enrollment_path(plan['caller_surface_uuid'])
    enrollment_raw = scheduler._raw(path)
    envelope = json.loads(enrollment_raw, object_pairs_hook=_unique_object,
                          parse_constant=_nonfinite_constant)
    transaction, _directory = scheduler._transaction(envelope, path)
    if (transaction['previous_state_sha256'] != preimages['original_state']['sha256']
            or transaction['previous_enrollment_sha256'] != preimages['original_enrollment']['sha256']
            or any(envelope.get(key) != transaction[key] for key in
                   ('previous_state_sha256', 'previous_enrollment_sha256'))):
        raise ValueError('SUCCESSOR_REGISTRATION_PREIMAGE_CHANGED')
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
    # Endpoint/authentication I/O may take time. Cooperating writers share our
    # locks; also reject raw file drift during those final checks.
    if (_pin(reask.state_path(plan['caller_surface_uuid']))[0] != preimages['original_state']
            or _pin(reask.stop_file(plan['caller_surface_uuid']), absent=True)[0] != preimages['original_stop']
            or scheduler._raw(path) != enrollment_raw):
        raise ValueError('SUCCESSOR_COMMIT_PREIMAGE_CHANGED_DURING_AUTHENTICATION')


def register(caller, supervisor, auth_file, auth_sha, *, apply=False, plan_sha256='', now=None, resume_stopped=False,
             reviewed_preview=None, reviewed_preview_sha256=''):
    realtime = now is None
    now = time.time() if now is None else now
    caller = scheduler.canonical_uuid(caller)
    if reviewed_preview is None and reviewed_preview_sha256:
        raise SuccessorDiagnosticError('SUCCESSOR_REVIEWED_PREVIEW_FILE_REQUIRED')
    reviewed = None
    if reviewed_preview is not None:
        pin, raw = _pin(reviewed_preview, limit=MAX_REVIEW_BYTES)
        if pin['sha256'] != reviewed_preview_sha256:
            raise SuccessorDiagnosticError('SUCCESSOR_REVIEWED_PREVIEW_EXACT_SHA_REQUIRED')
        preview = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_nonfinite_constant)
        if (not isinstance(preview, dict) or preview.get('outcome') != 'SUCCESSOR_READ_ONLY_PLAN'
                or not isinstance(preview.get('plan'), dict)
                or preview['plan'].get('schema') != PLAN_SCHEMA
                or preview['plan'].get('caller_surface_uuid') != caller
                or digest(preview['plan']) != preview.get('plan_sha256')
                or apply and preview['plan_sha256'] != plan_sha256):
            raise SuccessorDiagnosticError('SUCCESSOR_REVIEWED_PREVIEW_PLAN_REQUIRED')
        reviewed = preview['plan']
    observations = {}
    with ExitStack() as stack:
        try:
            stack.enter_context(reask.episode_lock(caller))
        except reask.EpisodeBusy:
            return dict(outcome='EPISODE_BUSY', input_operations=0)
        plan = build_plan(caller, supervisor, auth_file, auth_sha, now=now,
                          resume_stopped=resume_stopped, observations=observations)
    sha = digest(plan)
    if not apply:
        return dict(outcome='SUCCESSOR_READ_ONLY_PLAN', plan=plan, plan_sha256=sha,
            original_observations={key: dict(pin, role='LIVE_OBSERVATION', immutable_input=False)
                                   for key, pin in observations.items()},
            input_operations=0, registration_changed=False)
    if sha != plan_sha256:
        raise SuccessorDiagnosticError('SUCCESSOR_REVIEWED_PLAN_SHA_REQUIRED',
            supplied_plan_sha256=plan_sha256 if protocol.sha256_shape(plan_sha256) else 'INVALID',
            current_plan_sha256=sha,
            changes=plan_difference(reviewed, plan) if reviewed else [],
            change_details='EXACT_REVIEWED_PREVIEW_REQUIRED' if reviewed is None else 'SEMANTIC_COMPONENT_HASHES',
            next_action='REVIEW_NEW_EXACT_PREVIEW_NO_AUTOMATIC_APPLY')
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
