#!/usr/bin/env python3
"""Explicitly enrolled, durable file-only inquiries; no terminal/native input.

This is a separate launchd job, not a configuration guardian or Stop hook.
Enrollment occurs only after executor_reask start authenticates its live pair.
The background job preserves that authorization, never supplies a foreground
identity, and never reconciles or repeats an original transport attempt.
"""
import argparse
from bisect import bisect_right
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys
import tempfile
import time
import uuid

import configuration_guardian as guardian
import executor_ready as ready
import executor_reask as reask

LABEL = 'org.multi-agent-collaboration.executor-reask-scheduler'
SCHEMA = 'executor-reask-enrollment-v1'
MAX_ENROLLMENTS = 256
MAX_CHANNELS = 8
MAX_SWEEP_SECONDS = 10.0
BINDING_KEYS = ('caller_surface_uuid', 'supervisor', 'workspace_uuid', 'task_id',
                'purpose', 'episode_id', 'channels', 'mailbox')
LIVE_KEYS = ('workspace_uuid', 'caller_surface_uuid', 'target_surface_uuid', 'target_pane_uuid')


def root():
    return ready.state_root() / 'executor-reask-scheduler-v1'


def enrollment_path(caller):
    return root() / 'enrolled' / (ready.digest(caller) + '.json')


def secure_directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if (path.resolve() != path or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise ValueError('scheduler state must be a private canonical directory')


@contextmanager
def registry_lock():
    """Serialize registrations so simultaneous callers cannot exceed the cap."""
    secure_directory(root())
    fd = os.open(root() / 'registry.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def check_capacity(directory):
    # Stop reading as soon as the finite cap is reached, even in a corrupt or
    # unexpectedly crowded directory. Never enumerate the complete directory.
    with os.scandir(directory) as entries:
        for index, _entry in enumerate(entries):
            if index + 1 >= MAX_ENROLLMENTS:
                raise ValueError('scheduler enrollment capacity reached')


def canonical_uuid(value):
    return str(uuid.UUID(value)).upper()


def provenance():
    path = Path(__file__).resolve()
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                python=str(Path(sys.executable).resolve()), label=LABEL)


def approved_release(manifest=None):
    """Verify CURRENT and all immutable members, including this scheduler."""
    current, _manager = guardian.load_release(
        Path.home(), manifest or ready.state_root() / 'CURRENT.json')
    path = Path(current['source']) / 'scripts/executor_reask_scheduler.py'
    _raw, pins = guardian.read_json(path.parents[1].parent / 'MANIFEST.json')
    if (path != Path(__file__).resolve()
            or 'scripts/executor_reask_scheduler.py' not in pins['files']):
        raise ValueError('scheduler is not pinned in CURRENT; do not migrate episodes')
    return current


def binding(state):
    value = {key: state.get(key) for key in BINDING_KEYS}
    for key in ('caller_surface_uuid', 'supervisor', 'workspace_uuid'):
        if canonical_uuid(value[key]) != value[key]:
            raise ValueError('episode binding must use canonical UUIDs')
    if (not isinstance(value['task_id'], str) or len(value['task_id']) > 4096
            or not isinstance(value['episode_id'], str) or not value['episode_id']
            or len(value['episode_id']) > 128
            or value['purpose'] not in ('REQUEST_NEXT_TASK', 'PENDING_TASK_REPLY')):
        raise ValueError('invalid inquiry scope')
    channels = value['channels']
    if not isinstance(channels, list) or len(channels) > MAX_CHANNELS:
        raise ValueError('inquiry channel budget exceeded')
    for channel in channels:
        if (not isinstance(channel, str) or not Path(channel).is_absolute()
                or Path(channel).resolve() != Path(channel)):
            raise ValueError('file channels must be canonical absolute directories')
    if value['mailbox'] != str(ready.mailbox_dir(value['caller_surface_uuid'])):
        raise ValueError('noncanonical reply mailbox')
    return value


def enroll(state, live, *, now=None):
    """Called by explicit start, never by workflow hooks or the background job."""
    current = approved_release()
    caller = state['caller_surface_uuid']
    pins = {key: canonical_uuid(live[key]) for key in LIVE_KEYS}
    with registry_lock(), reask.episode_lock(caller):
        latest = reask.load(caller)
        expected = binding(state)
        if (not latest or binding(latest) != expected or latest.get('state') != reask.WAITING
                or pins['caller_surface_uuid'] != caller
                or pins['target_surface_uuid'] != state['supervisor']
                or pins['workspace_uuid'] != state['workspace_uuid']):
            raise ValueError('live pair and waiting episode do not match')
        controller = reask.controller()
        if controller['source'] != current['source']:
            raise ValueError('scheduler and inquiry controller differ')
        record = dict(schema=SCHEMA, status='ENROLLED', authorization='EXPLICIT_REASK_START',
                      binding=expected, live_binding=pins, controller=controller,
                      scheduler=provenance(), uid=os.getuid(), enrolled_epoch=(
                          time.time() if now is None else now), input_operations=0)
        secure_directory(root())
        secure_directory(enrollment_path(caller).parent)
        old = ready.read_json(enrollment_path(caller))
        if old and old.get('binding', {}).get('episode_id') == state['episode_id']:
            keys = ('schema', 'authorization', 'binding', 'live_binding', 'controller', 'scheduler', 'uid')
            if any(old.get(key) != record[key] for key in keys):
                raise ValueError('existing enrollment belongs to its original controller/binding')
            return old
        # One registration per caller; real episode transitions are archived by
        # start_locked, including old pending attempts. No per-minute archives.
        if not enrollment_path(caller).exists():
            check_capacity(enrollment_path(caller).parent)
        ready.write_json(enrollment_path(caller), record)
        latest['file_schedule'] = dict(enrollment=str(enrollment_path(caller)),
                                      registered_epoch=record['enrolled_epoch'],
                                      execution_proven=False, label=LABEL)
        reask.save(latest)
        return record


def matches(record, state, path):
    try:
        expected = binding(state)
        pins = record['live_binding']
        return (record.get('schema') == SCHEMA and record.get('status') == 'ENROLLED'
                and record.get('authorization') == 'EXPLICIT_REASK_START'
                and record.get('uid') == os.getuid() and record.get('binding') == expected
                and path == enrollment_path(expected['caller_surface_uuid'])
                and all(canonical_uuid(pins[key]) == pins[key] for key in LIVE_KEYS)
                and pins['workspace_uuid'] == expected['workspace_uuid']
                and pins['caller_surface_uuid'] == expected['caller_surface_uuid']
                and pins['target_surface_uuid'] == expected['supervisor']
                and record.get('controller') == reask.controller()
                and record.get('scheduler') == provenance())
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def never_send(*_args, **_kwargs):
    raise AssertionError('file scheduler must never invoke transport')


def sweep(*, manifest=None, now=None):
    """A bounded zero-input sweep, skipping busy/mismatched/unenrolled episodes."""
    started = time.monotonic()
    result = dict(status='SWEPT', label=LABEL, at_epoch=time.time() if now is None else now,
                  input_operations=0, file_posts=0, episodes=[],
                  hook_exposure_proven=False, model_reading_proven=False,
                  native_reception_proven=False)
    secure_directory(root())
    fd = os.open(root() / 'sweep.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return dict(result, status='SCHEDULER_BUSY')
        try:
            with ready.runtime_limit(MAX_SWEEP_SECONDS):
                current = approved_release(manifest)
                result['source'] = current['source']
                previous = ready.read_json(root() / 'STATUS.json') or {}
                last = previous.get('last_enrollment', '')
                if not isinstance(last, str):
                    last = ''
                result['last_enrollment'] = last
                directory = root() / 'enrolled'
                paths = []
                if directory.exists():
                    secure_directory(directory)
                    with os.scandir(directory) as entries:
                        for index, entry in enumerate(entries):
                            if index >= MAX_ENROLLMENTS:
                                raise ValueError('scheduler enrollment capacity exceeded')
                            if entry.is_file(follow_symlinks=False) and entry.name.endswith('.json'):
                                paths.append(Path(entry.path))
                paths.sort()
                start = bisect_right([path.name for path in paths], last)
                for path in paths[start:] + paths[:start]:
                    # Advance before work. A deadline inside a busy/slow caller
                    # must not starve everyone behind it on the next sweep.
                    result['last_enrollment'] = path.name
                    record = ready.read_json(path) or {}
                    caller = record.get('binding', {}).get('caller_surface_uuid')
                    if not isinstance(caller, str) or path != enrollment_path(caller):
                        result['episodes'].append(dict(enrollment=path.name, status='INVALID_ENROLLMENT'))
                        continue
                    row = dict(caller_surface_uuid=caller, status='BINDING_OR_CONTROLLER_MISMATCH')
                    try:
                        with reask.episode_lock(caller):
                            state = reask.load(caller)
                            if state and matches(record, state, path):
                                previous_posts = state.get('file_posts', 0)
                                state = reask.tick_locked(caller, now, never_send, file_only=True)
                                posts = state.get('file_posts', 0) - previous_posts
                                result['file_posts'] += posts
                                row.update(status=state['state'], episode_id=state['episode_id'],
                                           file_posts=posts, next_due_epoch=state.get('next_due_epoch'))
                                record['last_execution'] = dict(at_epoch=result['at_epoch'],
                                    state=state['state'], file_posts=posts, input_operations=0)
                                if state['state'] != reask.WAITING:
                                    record['status'] = 'FINISHED'
                                ready.write_json(path, record)
                            elif record.get('status') == 'FINISHED':
                                row['status'] = 'FINISHED'
                    except BlockingIOError:
                        row['status'] = 'EPISODE_BUSY'
                    except (OSError, ValueError, KeyError, TypeError):
                        row['status'] = 'INVALID_EPISODE'
                    result['episodes'].append(row)
        except ready.WaitBudgetExpired:
            result['status'] = 'SWEEP_BUDGET_EXHAUSTED'
        except Exception as exc:
            result.update(status='SCHEDULER_FAILED', error_type=type(exc).__name__)
        result['duration_seconds'] = max(0, time.monotonic() - started)
        ready.write_json(root() / 'STATUS.json', result)
    return result


def launchd_spec(home, source, python, manifest):
    return dict(Label=LABEL, RunAtLoad=True, StartInterval=60, ThrottleInterval=10,
                ProgramArguments=[str(python), '-B', str(Path(source) / 'scripts/executor_reask_scheduler.py'),
                                  'sweep', '--manifest', str(manifest)],
                StandardOutPath='/dev/null', StandardErrorPath='/dev/null', ProcessType='Background')


def install_job(manifest=None, *, apply=False):
    manifest = manifest or ready.state_root() / 'CURRENT.json'
    current = approved_release(manifest)
    path = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
    raw = plistlib.dumps(launchd_spec(Path.home(), current['source'], current['python'], manifest), sort_keys=True)
    result = dict(status='JOB_PLANNED', plist=str(path), label=LABEL, interval_seconds=60,
                  input_operations=0, enrollment_implied=False, execution_proven=False)
    if not apply:
        return result
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError('symlinked launchd job refused')
    old = path.read_bytes() if path.exists() else None
    domain = 'gui/' + str(os.getuid())
    loaded = subprocess.run(['launchctl', 'print', domain + '/' + LABEL],
                            capture_output=True, timeout=10).returncode == 0
    if old != raw:
        fd, pending = tempfile.mkstemp(prefix='.reask-scheduler-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            if path.is_symlink() or (path.read_bytes() if path.exists() else None) != old:
                raise RuntimeError('launchd job changed concurrently')
            os.replace(pending, path)
        finally:
            if os.path.exists(pending):
                os.unlink(pending)
        if loaded:
            subprocess.run(['launchctl', 'bootout', domain + '/' + LABEL],
                           check=True, capture_output=True, timeout=10)
            loaded = False
    if not loaded:
        subprocess.run(['launchctl', 'bootstrap', domain, str(path)],
                       check=True, capture_output=True, timeout=10)
    return dict(result, status='JOB_REGISTERED')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['sweep', 'install-job'])
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--apply', action='store_true', help='install-job only')
    args = parser.parse_args(argv)
    try:
        result = (sweep(manifest=args.manifest) if args.mode == 'sweep'
                  else install_job(args.manifest, apply=args.apply))
    except Exception as exc:
        result = dict(status='SCHEDULER_FAILED', error_type=type(exc).__name__, input_operations=0)
    print(json.dumps(result, ensure_ascii=False))
    return 2 if result['status'] == 'SCHEDULER_FAILED' else 0


if __name__ == '__main__':
    raise SystemExit(main())
