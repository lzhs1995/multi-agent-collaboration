#!/usr/bin/env python3
"""Read-only routing and Herdr identity checks; never send, enroll, or accept work.

Existing task registrations belong to their original controller. Environment
variables only suggest a default for an unregistered NEW task.
"""
import sys

# Protect subsequent imports. Callers importing this module must start Python
# with -B: importlib can cache this module before executing its first statement.
sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


class ProfileError(ValueError):
    pass


def receiver_profile(target=None, explicit=None):
    """A receiver hint chooses a NEW route; it proves no live identity."""
    if explicit is not None and explicit not in ('herdr', 'cmux'):
        raise ProfileError('INVALID_RECEIVER_PROFILE')
    observed = None
    if target is not None:
        endpoint = target.get('endpoint') if isinstance(target, dict) else None
        observed = endpoint.get('transport') if isinstance(endpoint, dict) else None
        if observed not in ('herdr', 'cmux'):
            raise ProfileError('INVALID_RECEIVER_ENDPOINT')
    if observed and explicit and observed != explicit:
        raise ProfileError('RECEIVER_PROFILE_CONFLICT')
    return observed or explicit


def select_profile(registration=None, environ=None, *, target=None, requested_profile=None):
    env = os.environ if environ is None else environ
    requested = receiver_profile(target, requested_profile)
    if registration is not None:
        if not isinstance(registration, dict):
            raise ProfileError('INVALID_TASK_REGISTRATION')
        # An absent new schema means existing, not automatically migrated.
        if registration.get('schema') != 'collaboration-profile-v1':
            result = {'profile': 'original', 'source': 'registration',
                      'controller': registration.get('controller'), 'migrate': False}
            if requested:
                result.update(requested_profile=requested,
                              route_note='USE_ORIGINAL_CONTROLLER_NOT_RECEIVER_OVERRIDE')
            return result
        profile = registration.get('profile')
        if profile not in ('herdr', 'cmux') or not registration.get('task_id'):
            raise ProfileError('INVALID_TASK_PROFILE')
        result = {'profile': profile, 'source': 'registration', 'migrate': False}
        if requested and requested != profile:
            result.update(requested_profile=requested,
                          conflict='REGISTERED_PROFILE_DIFFERS_FROM_RECEIVER')
        return result
    if requested:
        return {'profile': requested, 'source': 'receiver',
                'enrolled': False, 'migrate': False, 'identity_verified': False}
    return {'profile': 'herdr' if env.get('HERDR_ENV') == '1' else 'cmux',
            'source': 'new_task_default', 'enrolled': False, 'migrate': False}


def endpoint_key(endpoint):
    if not isinstance(endpoint, dict) or endpoint.get('transport') != 'herdr':
        raise ProfileError('NOT_A_HERDR_ENDPOINT')
    socket = endpoint.get('socket')
    if not isinstance(socket, str) or not Path(socket).is_absolute():
        raise ProfileError('ABSOLUTE_SERVER_SOCKET_REQUIRED')
    return str(Path(socket).resolve())


def target_key(target):
    if not isinstance(target, dict):
        raise ProfileError('INCOMPLETE_TARGET_IDENTITY')
    values = [target.get(k) for k in ('pane_id', 'terminal_id', 'native_session_id')]
    if not all(isinstance(v, str) and v for v in values):
        raise ProfileError('INCOMPLETE_TARGET_IDENTITY')
    return (endpoint_key(target.get('endpoint')), *values)


def verify_target(target, live_response, observed_endpoint):
    pinned = target_key(target)
    if endpoint_key(observed_endpoint) != pinned[0]:
        raise ProfileError('SERVER_CHANGED')
    result = live_response.get('result') if isinstance(live_response, dict) else None
    if not isinstance(result, dict):
        raise ProfileError('INVALID_LIVE_RESPONSE')
    rows = result.get('agents')
    if rows is None and isinstance(result.get('agent'), dict):
        rows = [result['agent']]
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ProfileError('INVALID_LIVE_RESPONSE')
    matches = [r for r in rows if r.get('pane_id') == pinned[1]]
    if len(matches) != 1:
        raise ProfileError('TARGET_NOT_UNIQUE')
    row = matches[0]
    if row.get('terminal_id') != pinned[2]:
        raise ProfileError('TERMINAL_CHANGED')
    session = row.get('agent_session') or {}
    if (not isinstance(session, dict) or session.get('kind') != 'id'
            or session.get('value') != pinned[3]):
        raise ProfileError('NATIVE_SESSION_CHANGED')
    return {'identity_matches': True, 'target_key': pinned,
            'state': row.get('agent_status', 'unknown'),
            'revision': row.get('revision'), 'state_change_seq': row.get('state_change_seq'),
            'submission_proven': False, 'task_completed': False,
            'atomic_send_guard': False}


def check_herdr(target, *, session=None, environ=None, runner=None):
    """Inspect one local Herdr server. No start, prompt, enrollment or retry."""
    pinned = target_key(target)
    env = dict(os.environ if environ is None else environ)
    run = subprocess.run if runner is None else runner
    prefix = ['herdr']
    inherited_socket = None
    if session is not None:
        if (not isinstance(session, str) or not session or session.startswith('-')
                or any(c in session for c in '\0\r\n')):
            raise ProfileError('INVALID_SESSION_SELECTOR')
        prefix += ['--session', session]
    else:
        if env.get('HERDR_ENV') != '1':
            raise ProfileError('HERDR_ENV_OR_EXPLICIT_SESSION_REQUIRED')
        inherited_socket = endpoint_key(
            {'transport': 'herdr', 'socket': env.get('HERDR_SOCKET_PATH', '')})
        if inherited_socket != pinned[0]:
            raise ProfileError('SELECT_PINNED_SERVER_FIRST')

    def inspect(arguments):
        call = run(prefix + arguments, capture_output=True, text=True,
                   timeout=10, check=True, env=env)
        return json.loads(call.stdout)

    status = inspect(['status', '--json'])
    server = status.get('server') if isinstance(status, dict) else None
    if (not isinstance(server, dict) or server.get('running') is not True
            or server.get('status') != 'running'):
        raise ProfileError('HERDR_SERVER_NOT_RUNNING')
    if server.get('compatible') is not True or server.get('endpoint_compatible') is not True:
        raise ProfileError('HERDR_SERVER_INCOMPATIBLE')
    if session is not None and server.get('session') != session:
        raise ProfileError('SESSION_SELECTOR_CHANGED')
    endpoint = {'transport': 'herdr', 'socket': server.get('socket')}
    observed_socket = endpoint_key(endpoint)
    if observed_socket != pinned[0] or (inherited_socket and observed_socket != inherited_socket):
        raise ProfileError('SERVER_CHANGED')
    result = verify_target(target, inspect(['agent', 'list']), endpoint)
    result.update(selector=({'kind': 'session', 'name': session} if session is not None
                            else {'kind': 'inherited'}),
                  observed_endpoint=endpoint, input_sent=False)
    return result


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    route = sub.add_parser('route')
    route.add_argument('--registration', type=Path)
    route.add_argument('--target', type=Path,
                       help='Receiver endpoint hint; does not authenticate or enroll it')
    route.add_argument('--receiver-profile', choices=('herdr', 'cmux'))
    check = sub.add_parser('check-herdr')
    check.add_argument('--target', required=True, type=Path)
    check.add_argument('--session', help='Explicit local named Herdr service; no --machine forwarding')
    args = parser.parse_args(argv)
    try:
        if args.command == 'route':
            reg = json.loads(args.registration.read_text()) if args.registration else None
            target = json.loads(args.target.read_text()) if args.target else None
            if args.registration is not None and not isinstance(reg, dict):
                raise ProfileError('INVALID_TASK_REGISTRATION')
            if args.target is not None and not isinstance(target, dict):
                raise ProfileError('INVALID_RECEIVER_ENDPOINT')
            result = select_profile(reg, target=target, requested_profile=args.receiver_profile)
        else:
            target = json.loads(args.target.read_text())
            result = check_herdr(target, session=args.session)
        print(json.dumps(result, ensure_ascii=False))
        if result.get('conflict'):
            return 1
    except (ProfileError, OSError, ValueError, subprocess.SubprocessError) as exc:
        reason = str(exc) if isinstance(exc, ProfileError) else type(exc).__name__
        print(json.dumps({'error': reason, 'input_sent': False}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
