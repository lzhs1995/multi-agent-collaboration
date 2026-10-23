"""Read-only coverage of two explicitly named original executor scopes.

An enrollment is not proof of provider health, body reading or useful work.
This diagnostic never starts, ticks, consumes or repairs an inquiry.
"""
import hashlib
import json
import subprocess
import time
from pathlib import Path

import cmux_identity_budget as budget
import cmux_native_delivery as native
import cmux_workspace_guard as workspace
from cmux_evidence_io import snapshot
import executor_ready as ready
import executor_reask as reask
import executor_reask_protocol as protocol
import executor_reask_scheduler as scheduler
from executor_reask_lifecycle import authorization_pin, valid_epoch

SCHEMA = 'executor-pair-coverage-scope-v1'
MAX_SECONDS = 30.0
MAX_SCOPE_BYTES = 65536
MAX_SCOPE_TEXT = 4096
MAX_PEER_SECONDS = 10.0


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('COVERAGE_DUPLICATE_JSON_KEY')
        value[key] = item
    return value


def _json(raw):
    def reject(_value):
        raise ValueError('COVERAGE_NONFINITE_JSON')
    value = json.loads(raw, object_pairs_hook=_object, parse_constant=reject)
    if not isinstance(value, dict):
        raise ValueError('COVERAGE_OBJECT_REQUIRED')
    return value


def _uuid(value):
    if not isinstance(value, str) or scheduler.canonical_uuid(value) != value:
        raise ValueError('COVERAGE_CANONICAL_UUID_REQUIRED')
    return value


def _read(path, *, limit=ready.MAX_JSON_BYTES):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path or path.is_symlink():
        raise ValueError('COVERAGE_CANONICAL_FILE_REQUIRED')
    try:
        raw, identity = snapshot(path, limit=limit)
    except FileNotFoundError:
        return None, dict(path=str(path), sha256='ABSENT', bytes=0,
                          role='LIVE_OBSERVATION', immutable_input=False)
    value = _json(raw)
    return value, dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(),
                       bytes=len(raw), file_identity=list(identity),
                       role='LIVE_OBSERVATION', immutable_input=False)


def read_scope(path, expected_sha):
    value, pin = _read(path, limit=MAX_SCOPE_BYTES)
    if value is None or pin['sha256'] != expected_sha:
        raise ValueError('COVERAGE_SCOPE_EXACT_SHA_REQUIRED')
    if value.get('schema') != SCHEMA:
        raise ValueError('COVERAGE_SCOPE_SCHEMA_REQUIRED')
    for key in ('supervisor_surface_uuid', 'workspace_uuid'):
        _uuid(value.get(key))
    # Native session IDs are canonical lower-case UUIDs.
    import uuid
    session = value.get('supervisor_session_id')
    if not isinstance(session, str) or str(uuid.UUID(session)) != session:
        raise ValueError('COVERAGE_SUPERVISOR_SESSION_REQUIRED')
    peers = value.get('executors')
    if not isinstance(peers, list) or len(peers) != 2:
        raise ValueError('COVERAGE_EXACT_TWO_EXECUTORS_REQUIRED')
    seen = set()
    for peer in peers:
        if not isinstance(peer, dict):
            raise ValueError('COVERAGE_EXECUTOR_SCOPE_REQUIRED')
        surface = _uuid(peer.get('surface_uuid'))
        if surface in seen or surface == value['supervisor_surface_uuid']:
            raise ValueError('COVERAGE_DISTINCT_ORIGINAL_EXECUTORS_REQUIRED')
        seen.add(surface)
        session = peer.get('session_id')
        if not isinstance(session, str) or str(uuid.UUID(session)) != session:
            raise ValueError('COVERAGE_EXECUTOR_SESSION_REQUIRED')
        for key in ('task_id', 'overall_task'):
            text = peer.get(key)
            if (not isinstance(text, str) or len(text) > MAX_SCOPE_TEXT
                    or key == 'overall_task' and not text.strip()):
                raise ValueError('COVERAGE_EXACT_TASK_SCOPE_REQUIRED')
    pin.update(role='IMMUTABLE_SCOPE_INPUT', immutable_input=True)
    return value, pin


def _authentic_supervisor(scope, caller):
    if _uuid(caller) != scope['supervisor_surface_uuid']:
        raise ValueError('COVERAGE_ORIGINAL_SUPERVISOR_REQUIRED')
    resolved, _tree, _env, proof = workspace.caller_snapshot()
    row = resolved.get('caller', {})
    if (not isinstance(proof, dict)
            or proof.get('session') != scope['supervisor_session_id']
            or (proof.get('foreground') or {}).get('thread_id') != scope['supervisor_session_id']
            or row.get('surface_id') != caller
            or row.get('workspace_id') != scope['workspace_uuid']
            or row.get('surface_type') != 'terminal'):
        raise ValueError('COVERAGE_AUTHENTICATED_FOREGROUND_SUPERVISOR_REQUIRED')


def _next_action(status, peer):
    if status == 'NO_EPISODE':
        return 'ISSUE_EXPLICIT_SCOPE_AUTHORITY_TO_ORIGINAL_EXECUTOR_THEN_SUPPORTED_START'
    if status == 'NO_ENROLLMENT':
        return 'ORIGINAL_EXECUTOR_REVIEW_EXACT_ENROLL_EXISTING_PRECONDITIONS'
    if status in ('STOPPED', 'FINISHED', 'AUTHORITY_EXPIRED', 'AUTHORITY_INACTIVE'):
        return 'PRESERVE_OLD_EPISODE_NEW_EXPLICIT_AUTHORITY_AND_REVIEWED_SUCCESSOR_PREVIEW'
    if status == 'COVERED_REPLY_UNCONSUMED':
        return 'ORIGINAL_EXECUTOR_FULL_READ_AND_EXACT_SHA_CONSUME_USING_ORIGINAL_CONTROLLER'
    if status.startswith('COVERED_'):
        return 'CONTINUE_ORIGINAL_TASK_NO_REGISTRATION_NEEDED'
    return 'REVIEW_REPORTED_BINDING_AND_ORIGINAL_EVIDENCE_NO_AUTOMATIC_REPAIR'


def _observe(scope, peer, now, verification, epoch):
    caller = peer['surface_uuid']
    row = dict(surface_uuid=caller, expected_session_id=peer['session_id'],
               expected_task_id=peer['task_id'], expected_overall_task=peer['overall_task'],
               covered=False, input_operations=0, state_changes=0,
               provider_health='NOT_EVALUATED', body_read='NOT_EVALUATED',
               consume='NOT_EVALUATED', useful_work='NOT_EVALUATED')
    bridge = ready._bridge()
    live = bridge.pin_workspace(caller, caller_uuid=scope['supervisor_surface_uuid'])
    if (live.get('workspace_uuid') != scope['workspace_uuid']
            or live.get('caller_surface_uuid') != scope['supervisor_surface_uuid']
            or live.get('target_surface_uuid') != caller):
        raise ValueError('COVERAGE_LIVE_PAIR_SCOPE_MISMATCH')
    target = native.bind_target(bridge, caller, 'STATUS: COVERAGE_READ_ONLY_IDENTITY')
    row['observed_session_id'] = target['session_id']
    if target['provider'] != 'claude' or target['session_id'] != peer['session_id']:
        return dict(row, status='ORIGINAL_SESSION_MISMATCH')
    state, state_pin = _read(reask.state_path(caller))
    record, enrollment_pin = _read(scheduler.enrollment_path(caller))
    row['observations'] = dict(state=state_pin, enrollment=enrollment_pin)
    if state is None:
        return dict(row, status='NO_EPISODE')
    row.update(episode_id=state.get('episode_id'), episode_state=state.get('state'),
               controller_source=(protocol.state_controller(state) or {}).get('source'))
    continuation = state.get('continuation') or {}
    executor = state.get('executor_identity') or {}
    if (not isinstance(continuation, dict) or not isinstance(executor, dict)
            or any(state.get(k) != v for k, v in dict(caller_surface_uuid=caller,
                supervisor=scope['supervisor_surface_uuid'], workspace_uuid=scope['workspace_uuid'],
                task_id=peer['task_id']).items())
            or continuation.get('overall_task') != peer['overall_task']):
        return dict(row, status='TASK_OR_SUPERVISOR_SCOPE_MISMATCH')
    if executor.get('session_id') != peer['session_id']:
        return dict(row, status='ORIGINAL_SESSION_MISMATCH')
    if (executor.get('process') != target['process'] or executor.get('tty') != target['tty']
            or executor.get('pane_uuid') != live.get('target_pane_uuid')):
        return dict(row, status='ORIGINAL_EXECUTOR_IDENTITY_CHANGED')
    stop, stop_pin = _read(reask.stop_file(caller))
    row['observations']['stop'] = stop_pin
    if stop is not None or state.get('state') == reask.STOPPED:
        return dict(row, status='STOPPED', reason=state.get('exit_reason'))
    if record is None:
        return dict(row, status='NO_ENROLLMENT')
    if record.get('schema') == scheduler.TRANSACTION_SCHEMA:
        return dict(row, status='REGISTRATION_UNCOMMITTED')
    if record.get('status') == 'FINISHED':
        return dict(row, status='FINISHED')
    if (continuation.get('enabled') is not True
            or continuation.get('owner') != scope['supervisor_surface_uuid']):
        return dict(row, status='CONTINUING_AUTHORITY_MISSING')
    if continuation.get('status') != 'ACTIVE':
        return dict(row, status='AUTHORITY_INACTIVE')
    until = continuation.get('until_epoch')
    if until is not None and (not valid_epoch(until) or now >= until):
        return dict(row, status='AUTHORITY_EXPIRED')
    try:
        expected = continuation['authorization']
        if authorization_pin(expected['path']) != expected:
            return dict(row, status='AUTHORITY_CHANGED')
    except (OSError, ValueError, KeyError, TypeError):
        return dict(row, status='AUTHORITY_UNAVAILABLE')
    if not scheduler.matches(record, state, scheduler.enrollment_path(caller), verification=verification):
        return dict(row, status='BINDING_OR_CONTROLLER_UNVERIFIED')
    fresh_target = native.bind_target(bridge, caller, 'STATUS: COVERAGE_READ_ONLY_IDENTITY')
    if any(fresh_target.get(key) != target.get(key) for key in
           ('provider', 'session_id', 'process', 'tty', 'identity')):
        return dict(row, status='ORIGINAL_EXECUTOR_IDENTITY_CHANGED')
    # A scheduler poll is a live observation, never a frozen work-order input.
    # Do not claim coverage from two different raw state/enrollment snapshots.
    if (_read(reask.state_path(caller))[1] != state_pin
            or _read(scheduler.enrollment_path(caller))[1] != enrollment_pin
            or _read(reask.stop_file(caller))[1] != stop_pin):
        return dict(row, status='OBSERVATION_DRIFT_RECHECK_REQUIRED')
    if authorization_pin(expected['path']) != expected:
        return dict(row, status='AUTHORITY_CHANGED')
    if until is not None and epoch() >= until:
        return dict(row, status='AUTHORITY_EXPIRED')
    if state.get('state') not in (reask.WAITING, reask.ANSWERED):
        return dict(row, status='EPISODE_NOT_CONTINUING')
    status = ('COVERED_REPLY_UNCONSUMED' if state['state'] == reask.ANSWERED
              else 'COVERED_RESERVATION' if state.get('paused_tasks') else 'COVERED_WAITING')
    return dict(row, status=status, covered=True,
                reservation_inquiries=continuation.get('reservation_inquiries') is True,
                proactive_hook_configured=continuation.get('proactive_hook') is True,
                automatic_hook_loading='NOT_EVALUATED')


def coverage(scope_file, scope_sha, caller, *, now=None):
    """Two exact scopes only; bounded failure remains an incomplete observation."""
    started = time.monotonic()
    realtime = now is None
    now = time.time() if now is None else now
    epoch = time.time if realtime else lambda: now
    scope, scope_pin = read_scope(scope_file, scope_sha)
    if not valid_epoch(now):
        raise ValueError('COVERAGE_FINITE_EPOCH_REQUIRED')
    result = dict(schema='executor-pair-coverage-result-v1', observed_epoch=now,
                   scope=scope_pin, input_operations=0, state_changes=0,
                   registration_authorized=False, exact_executor_count=2,
                   all_sessions_adoption=False, overall_efficiency='NOT_EVALUATED')
    rows, verification = [], protocol.ReleaseVerification()
    with budget.limit(MAX_SECONDS):
        _authentic_supervisor(scope, caller)
        for peer in scope['executors']:
            try:
                budget.check()
                with budget.limit(MAX_PEER_SECONDS):
                    row = _observe(scope, peer, epoch(), verification, epoch)
                budget.check()
            except (OSError, ValueError, RuntimeError, KeyError, TypeError,
                    AttributeError, subprocess.SubprocessError) as exc:
                row = dict(surface_uuid=peer['surface_uuid'], status='OBSERVATION_UNAVAILABLE',
                           covered=False, error_type=type(exc).__name__, reason=str(exc)[:240],
                           input_operations=0, state_changes=0)
            row['next_action'] = _next_action(row['status'], peer)
            rows.append(row)
        # Reauthenticate before labeling the observation; same total budget.
        _authentic_supervisor(scope, caller)
    fresh, fresh_pin = read_scope(scope_file, scope_sha)
    if fresh != scope or fresh_pin != scope_pin:
        raise ValueError('COVERAGE_SCOPE_CHANGED_DURING_OBSERVATION')
    result.update(executors=rows, covered_count=sum(r['covered'] for r in rows),
                   outcome='EXACT_PAIR_COVERED' if all(r['covered'] for r in rows) else 'EXACT_PAIR_PARTIAL',
                   elapsed_seconds=time.monotonic() - started,
                   acceptance_scope='ENROLLMENT_AND_RECORDED_AUTHORITY_ONLY')
    return result
