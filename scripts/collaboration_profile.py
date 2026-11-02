#!/usr/bin/env python3
"""Read-only routing and Herdr identity checks; never send, enroll, or accept work.

Existing task registrations belong to their original controller. Environment
variables only suggest a default for an unregistered NEW task.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


class ProfileError(ValueError):
    pass


def select_profile(registration=None, environ=None):
    env = os.environ if environ is None else environ
    if registration is not None:
        # An absent new schema means existing, not automatically migrated.
        if registration.get('schema') != 'collaboration-profile-v1':
            return {'profile': 'original', 'source': 'registration',
                    'controller': registration.get('controller'), 'migrate': False}
        profile = registration.get('profile')
        if profile not in ('herdr', 'cmux') or not registration.get('task_id'):
            raise ProfileError('INVALID_TASK_PROFILE')
        return {'profile': profile, 'source': 'registration', 'migrate': False}
    return {'profile': 'herdr' if env.get('HERDR_ENV') == '1' else 'cmux',
            'source': 'new_task_default', 'enrolled': False, 'migrate': False}


def endpoint_key(endpoint):
    if endpoint.get('transport') != 'herdr':
        raise ProfileError('NOT_A_HERDR_ENDPOINT')
    socket = endpoint.get('socket')
    if not isinstance(socket, str) or not Path(socket).is_absolute():
        raise ProfileError('ABSOLUTE_SERVER_SOCKET_REQUIRED')
    return str(Path(socket).resolve())


def target_key(target):
    values = [target.get(k) for k in ('pane_id', 'terminal_id', 'native_session_id')]
    if not all(isinstance(v, str) and v for v in values):
        raise ProfileError('INCOMPLETE_TARGET_IDENTITY')
    return (endpoint_key(target['endpoint']), *values)


def verify_target(target, live_response, observed_endpoint):
    pinned = target_key(target)
    if endpoint_key(observed_endpoint) != pinned[0]:
        raise ProfileError('SERVER_CHANGED')
    result = live_response.get('result', {})
    rows = result.get('agents')
    if rows is None and isinstance(result.get('agent'), dict):
        rows = [result['agent']]
    if not isinstance(rows, list):
        raise ProfileError('INVALID_LIVE_RESPONSE')
    matches = [r for r in rows if r.get('pane_id') == pinned[1]]
    if len(matches) != 1:
        raise ProfileError('TARGET_NOT_UNIQUE')
    row = matches[0]
    if row.get('terminal_id') != pinned[2]:
        raise ProfileError('TERMINAL_CHANGED')
    session = row.get('agent_session') or {}
    if session.get('kind') != 'id' or session.get('value') != pinned[3]:
        raise ProfileError('NATIVE_SESSION_CHANGED')
    return {'identity_matches': True, 'target_key': pinned,
            'state': row.get('agent_status', 'unknown'),
            'revision': row.get('revision'), 'state_change_seq': row.get('state_change_seq'),
            'submission_proven': False, 'task_completed': False,
            'atomic_send_guard': False}


def artifact_matches(path, expected_sha256):
    """Integrity is distinct from authorship, delivery, and business acceptance."""
    actual = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return {'sha256': actual, 'integrity_matches': actual == expected_sha256,
            'accepted': False}


def observation(submission=None, native_received=False, body_read=False,
                artifact_verified=False, accepted=False):
    # A timeout says nothing about whether bytes were already submitted.
    # Acceptance is an explicit supervisor decision, never inferred here.
    return dict(submission=submission or 'UNKNOWN', native_received=native_received,
                body_read=body_read, artifact_verified=artifact_verified,
                accepted=accepted, resend_authorized=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    route = sub.add_parser('route')
    route.add_argument('--registration', type=Path)
    check = sub.add_parser('check-herdr')
    check.add_argument('--target', required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'route':
            reg = json.loads(args.registration.read_text()) if args.registration else None
            result = select_profile(reg)
        else:
            if os.environ.get('HERDR_ENV') != '1':
                raise ProfileError('HERDR_ENV_REQUIRED')
            target = json.loads(args.target.read_text())
            # v1 deliberately uses only the current explicit server. Cross-server
            # discovery uses the official CLI with the SAME selector at each step.
            socket = os.environ.get('HERDR_SOCKET_PATH', '')
            endpoint = {'transport': 'herdr', 'socket': socket}
            if endpoint_key(target['endpoint']) != endpoint_key(endpoint):
                raise ProfileError('SELECT_PINNED_SERVER_FIRST')
            call = subprocess.run(['herdr', 'agent', 'list'], capture_output=True,
                                  text=True, timeout=10, check=True)
            result = verify_target(target, json.loads(call.stdout), endpoint)
        print(json.dumps(result, ensure_ascii=False))
    except (ProfileError, OSError, ValueError, subprocess.SubprocessError) as exc:
        reason = str(exc) if isinstance(exc, ProfileError) else type(exc).__name__
        print(json.dumps({'error': reason, 'input_sent': False}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
