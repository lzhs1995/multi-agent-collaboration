"""Suppress automatic inquiries after distinct, authenticated provider failures.

This never changes a provider, resumes a Goal, sends a status message, consumes a
reply, or interprets queued input as an API outcome. File inquiries stay live.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

import cmux_native_delivery as native
from cmux_transcript_tail import TranscriptTail

THRESHOLD = 3
HISTORY = 64
SCAN_BYTES = 256 * 1024
SCHEMA = 'executor-provider-circuit-v1'


def endpoint(binding):
    return dict(provider=binding['provider'], session_id=binding['session_id'],
        surface_uuid=binding['identity']['target_surface_uuid'],
        workspace_uuid=binding['identity']['workspace_uuid'], process=copy.deepcopy(binding['process']),
        transcript=dict(path=binding['transcript']['path'], device=binding['transcript']['device'],
            inode=binding['transcript']['inode']))


def classify(error):
    """Classify actual structured error fields; prose is never an HTTP status."""
    if not isinstance(error, dict) or not error:
        return None
    text = str(error.get('message', ''))[:1500]
    try:
        nested = json.loads(text)
    except (ValueError, TypeError):
        nested = None
    if isinstance(nested, dict):
        candidate = nested.get('error', nested)
        if isinstance(candidate, dict):
            text = str(candidate.get('message', text))[:1500]
            error = dict(error, **candidate)
    info = error.get('codex_error_info', error.get('codexErrorInfo'))
    code = error.get('code', error.get('type', ''))
    statuses = set()
    for alias in ('http_status', 'statusCode', 'http_status_code', 'httpStatusCode'):
        value = error.get(alias)
        if type(value) is int:
            statuses.add(value)
    if isinstance(info, dict):
        for value in info.values():
            if isinstance(value, dict):
                for alias in ('httpStatusCode', 'http_status_code'):
                    status = value.get(alias)
                    if type(status) is int:
                        statuses.add(status)
    # Conflicting structured aliases do not establish an HTTP outcome. A
    # concrete billing/authentication code or message remains independently
    # classifiable; an unknown error must not rearm an already-open circuit.
    status = next(iter(statuses)) if len(statuses) == 1 else None
    folded = text.lower()
    if 'created under a different azure openai resource' in folded:
        return 'AZURE_DIFFERENT_RESOURCE'
    if ('insufficient account balance' in folded or 'insufficient balance' in folded
            or 'user quota is not enough' in folded
            or status == 402 or code in ('billing_error', 'insufficient_quota')):
        return 'BILLING'
    if ('api key is disabled' in folded or 'invalid api key' in folded
            or status in (401, 403) or code in ('authentication_error', 'invalid_api_key')):
        return 'AUTHENTICATION'
    if (status == 429 or code in ('rate_limit_exceeded', 'rate_limit_error')
            or info in ('usageLimitExceeded', 'rateLimit')
            or 'rate limit exceeded' in folded):
        return 'RATE_LIMIT'
    if type(status) is int and status in (500, 502, 503, 504, 524, 529):
        return 'UPSTREAM_' + str(status)
    if 'stream disconnected before completion' in folded:
        return 'STREAM_DISCONNECTED'
    return None


def _attempt(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def apply_observation(state, binding, observation):
    """The caller authenticates the read through require_bound on both sides."""
    target = endpoint(binding)
    old = state.get('provider_circuit')
    if old and (old.get('schema') != SCHEMA or old.get('endpoint') != target):
        return dict(suppress=True, status='PROVIDER_TARGET_CHANGED', input_operations=0,
            next_action='EXPLICIT_SUCCESSOR_REGISTRATION_REQUIRED')
    circuit = copy.deepcopy(old) if old else dict(schema=SCHEMA, endpoint=target,
        state='CLOSED', failure_class=None, consecutive_failures=0, seen_attempts=[],
        open_count=0, operator_status_reported=None)
    outcomes = observation.get('outcomes', [])
    if (observation.get('thread_id') != target['session_id']
            or observation.get('transcript_path') != target['transcript']['path']
            or observation.get('source') not in ('codex-native-terminal-outcomes-v1', 'codex-native-transcript-outcomes-v1')
            or not isinstance(outcomes, list) or len(outcomes) > HISTORY):
        raise ValueError('PROVIDER_ORIGINAL_OUTCOME_PROOF_REQUIRED')
    notice = None
    for row in outcomes:
        if (not isinstance(row, dict) or not _attempt(row.get('attempt_id'))
                or row.get('terminal') is not True or row.get('kind') not in ('API_ERROR', 'API_SUCCESS')):
            continue
        attempt = row['attempt_id']
        if attempt in circuit['seen_attempts']:
            continue
        circuit['seen_attempts'] = (circuit['seen_attempts'] + [attempt])[-HISTORY:]
        circuit['last_outcome'] = copy.deepcopy(row)
        if row['kind'] == 'API_SUCCESS':
            circuit.update(state='CLOSED', failure_class=None, consecutive_failures=0,
                last_success_attempt=attempt)
            continue
        failure = classify(row.get('error'))
        if failure is None:
            # A distinct unknown terminal error breaks a same-class series, but
            # cannot rearm a circuit that needs genuine native success.
            if circuit['state'] != 'OPEN':
                circuit.update(failure_class=None, consecutive_failures=0)
            continue
        if circuit['state'] != 'OPEN':
            count = circuit['consecutive_failures'] + 1 if failure == circuit['failure_class'] else 1
            circuit.update(failure_class=failure, consecutive_failures=count)
            if count >= THRESHOLD or failure in ('AUTHENTICATION', 'BILLING'):
                circuit['state'] = 'OPEN'
                circuit['open_count'] += 1
                key = str(circuit['open_count']) + ':' + attempt
                if circuit['operator_status_reported'] != key:
                    notice = dict(status='AUTOMATIC_TERMINAL_INQUIRIES_SUPPRESSED',
                        failure_class=failure, distinct_failures=count,
                        original_surface_uuid=target['surface_uuid'], original_session_id=target['session_id'],
                        next_action='Inspect original provider/session; authenticated API success rearms. File inquiries continue.',
                        input_operations=0)
                    circuit['operator_status_reported'] = key
    circuit['last_observation'] = dict(source=observation['source'],
        observed_at_epoch=observation.get('observed_at_epoch'), outcomes_read=len(outcomes), input_operations=0)
    state['provider_circuit'] = circuit
    return dict(suppress=circuit['state'] == 'OPEN', status=circuit['state'],
        failure_class=circuit['failure_class'], distinct_failures=circuit['consecutive_failures'],
        operator_status=notice, input_operations=0)


def _scan_start(binding):
    path = Path(binding['transcript']['path'])
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        size = native._check_file(handle, binding)
        start = max(0, size - SCAN_BYTES)
        if start:
            handle.seek(start - 1)
            raw = handle.read(SCAN_BYTES + 1)
            if raw[:1] != b'\n':
                end = raw.find(b'\n')
                start = size if end < 0 else start + end
        native._check_file(handle, binding)
    return dict(identity=[binding['transcript']['device'], binding['transcript']['inode']], offset=start)


def transcript_outcomes(state, binding):
    """Bounded original task_complete records; metadata and images are ignored."""
    target = endpoint(binding)
    cursor = state.get('provider_transcript_cursor')
    if cursor and cursor.get('endpoint') != target:
        raise ValueError('PROVIDER_TRANSCRIPT_ORIGINAL_ENDPOINT_CHANGED')
    position = cursor['position'] if cursor else _scan_start(binding)
    tail = TranscriptTail(binding['transcript']['path'], position)
    outcomes = []
    try:
        for index, raw in enumerate(tail.lines()):
            pin = tail.record_observations[index]
            try:
                row = json.loads(raw)
            except (ValueError, TypeError):
                continue
            body = row.get('payload', {}) if isinstance(row, dict) else {}
            if (not isinstance(row, dict) or row.get('type') != 'event_msg' or not isinstance(body, dict)
                    or body.get('type') != 'task_complete' or not _attempt(body.get('turn_id'))):
                continue
            error = body.get('error')
            kind = ('API_ERROR' if isinstance(error, dict) and error else
                    'API_SUCCESS' if error is None and isinstance(body.get('last_agent_message'), str)
                    and body['last_agent_message'].strip() else None)
            if kind:
                outcomes.append(dict(attempt_id=body['turn_id'], kind=kind, terminal=True,
                    error=copy.deepcopy(error) if kind == 'API_ERROR' else None,
                    source='authenticated-native-task-complete', evidence=dict(
                        path=binding['transcript']['path'], device=binding['transcript']['device'],
                        inode=binding['transcript']['inode'], offset=pin['offset'], bytes=pin['bytes'],
                        sha256=pin['sha256'])))
    finally:
        state['provider_transcript_cursor'] = dict(endpoint=target, position=tail.position(), observation=tail.observation)
    # Bound state growth even when a single scan has many small outcomes.
    return dict(source='codex-native-transcript-outcomes-v1', thread_id=target['session_id'],
        transcript_path=target['transcript']['path'], observed_at_epoch=time.time(),
        outcomes=outcomes[-HISTORY:], input_operations=0)


def terminal_gate(state, bridge, binding, text):
    if binding.get('provider') != 'codex':
        return dict(suppress=False, status='PROVIDER_NOT_OBSERVED', input_operations=0)
    if binding.get('schema') != native.SCHEMA:
        raise native.NativeDeliveryError('PROVIDER_ORIGINAL_NATIVE_BINDING_REQUIRED')
    native.require_bound(bridge, state['supervisor'], binding, text)
    try:
        # Keep one chronological native source across socket outages/recovery.
        # Mixing a retrospective RPC page with an incremental transcript cursor
        # can replay an unseen historical failure after genuine success. The
        # separate read_terminal_outcomes API is diagnostic only here.
        observation = transcript_outcomes(state, binding)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as scan_error:
        state['provider_observation_error'] = dict(error_type=type(scan_error).__name__,
            error=str(scan_error)[:200], input_operations=0)
        known = state.get('provider_circuit') or {}
        return dict(suppress=known.get('state') == 'OPEN',
            status='PROVIDER_OUTCOME_UNMEASURED', input_operations=0)
    native.require_bound(bridge, state['supervisor'], binding, text)
    return apply_observation(state, binding, observation)
