#!/usr/bin/env python3
"""Observe explicitly registered NEW tasks; never send, accept, or control peers.

Run with Python -B (including callers importing this module). The observer only
reads task processes, optional checkpoint JSON and official Herdr status/list.
It writes its own output directory and JSON events to stdout. These are NOT a
native notification or wake-up; the supervisor must consume them and use the
task's original communication route.

Manifest example (process pins must come from existing live identity evidence):
  {"schema":"collaboration-progress-v1", "new_tasks_only":true,
   "project_id":"thesis", "tasks":[{"task_id":"chapter-a",
   "role":"chapter-supervisor", "process":{"pid":1234,
   "birth":[1700000000,123], "executable":"/absolute/real/executable"},
   "checkpoint":{"path":"/absolute/checkpoint.json"}}]}
Optional parent_task_id names another task in this manifest. Optional herdr is
{"selector":{"kind":"session","name":"existing-service"},"target":{...}}
using collaboration_profile's endpoint/pane/terminal/native_session_id format.
Optional expected_state is working/standby/waiting_input/finished: a planning
declaration only, never evidence of the actual runtime or business outcome.
Checkpoint fields updated_at, status, completed_increment, current_work, blocker,
next_action and outstanding_operations are UNVERIFIED self-report, never proof.
Manifest and checkpoints must live outside the observer's output directory.

Commands:
  python3 -B scripts/collaboration_progress.py validate --manifest /abs/tasks.json
  python3 -B scripts/collaboration_progress.py snapshot --manifest /abs/tasks.json --output /abs/new-output
  python3 -B scripts/collaboration_progress.py watch --manifest /abs/tasks.json --output /abs/new-output --duration-seconds 600
  python3 -B scripts/collaboration_progress.py watch --manifest /abs/tasks.json --output /abs/same-output --resume

Watch observes immediately and every 60 seconds, for 60..86400 seconds. No
unbounded mode, daemon installation or automatic restart. Ctrl-C/SIGTERM ONLY
to this observer requests graceful stop; no peer receives a signal. Resume
requires the identical manifest and retains the original deadline and STOP IDs;
it cannot extend coverage or reset a completed budget. Use a new output directory
for a separately authorized new observation period, retaining the old records.
Resume also requires the original local kernel boot UUID and monotonic deadline.
Old state without this clock binding, a different boot, or an unavailable boot
identity is refused without resetting the observation period. Wall-clock rollback
cannot increase the original monotonic budget; CPU uptime is not a boot identity.
Each scan receives at most 50 seconds and never more than the remaining watch
budget. Herdr subprocess timeouts share that scan deadline; tasks not reached
within it remain UNKNOWN. Native kernel/filesystem reads are synchronous and
cooperatively bounded, not forcibly interrupted. An OS stall can overrun; the
snapshot records partial coverage/overrun/gaps instead of guaranteeing 60 seconds.
The task count, target size and remaining duration must fit the bounded event
journal; oversized periods are refused before recording ACTIVE. Event records
retain checkpoint hashes/metadata, while snapshots hold the full self-report.

Output flock is held for the entire run; do not remove its lock file. After a
crash the OS releases it. A hard kill cannot write a final status: an ACTIVE file
alone is never liveness proof. Consumers must verify the observer's real process
pin and snapshot coverage_valid_until. Normal termination writes coverage UNKNOWN.
Events are fsynced before stdout; a crash between those steps can omit stdout,
so events.jsonl is authoritative for consumption. STOP means the pinned process
is absent/replaced, not that the task succeeded, all children ended, or a resource
lease can be released. Read failures/timeouts are UNKNOWN and never STOP.
Verified Herdr runtime states produce one baseline and subsequent state-change
events in that same journal. They do not diagnose why an agent became idle or
blocked, prove a message was read, or send a notification. Replay uses the last
journaled state, so restarting the observer does not repeat unchanged events.
No transcript reader or failure-cause classifier is included. API 429/502,
compaction, planned standby and actual completion require the original native
evidence and supervisor judgment; idle/done/absence cannot distinguish them.
"""
import sys

sys.dont_write_bytecode = True

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import threading
import time
import uuid

SCHEMA = 'collaboration-progress-v1'
CADENCE = 60
MAX_TASKS = 32
MAX_MANIFEST = 256 * 1024
MAX_CHECKPOINT = 64 * 1024
MAX_JOURNAL = 4 * 1024 * 1024
SNAPSHOT_BUDGET = 50


class ProgressError(ValueError):
    pass


class ObservedAbsent(Exception):
    """The existing native reader proved initial process absence."""


def utc(seconds=None):
    return datetime.fromtimestamp(time.time() if seconds is None else seconds,
                                  timezone.utc).isoformat()


def runtime_counter(value):
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def epoch(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('timestamp requires timezone')
    return stamp.timestamp()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def decode(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ProgressError('DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(
                          ProgressError('NONFINITE_JSON')))


def read_regular(path, limit, *, dir_fd=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ProgressError('NOT_BOUNDED_REGULAR_FILE')
        chunks, size = [], 0
        while True:
            chunk = os.read(fd, min(65536, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > limit:
                raise ProgressError('FILE_TOO_LARGE')
        after = os.fstat(fd)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ProgressError('FILE_CHANGED_DURING_READ')
        return b''.join(chunks), after
    finally:
        os.close(fd)


def require(condition, reason):
    if not condition:
        raise ProgressError(reason)


def boot_identity(value):
    require(isinstance(value, dict) and set(value) == {'source', 'uuid'}
            and value['source'] in ('darwin-kern.bootsessionuuid', 'linux-proc-boot-id')
            and isinstance(value['uuid'], str), 'INVALID_BOOT_IDENTITY')
    try:
        parsed = uuid.UUID(value['uuid'])
    except ValueError as exc:
        raise ProgressError('INVALID_BOOT_IDENTITY') from exc
    require(parsed.int != 0 and str(parsed) == value['uuid'], 'INVALID_BOOT_IDENTITY')
    return dict(value)


def native_boot_identity():
    """Kernel-generated local boot UUID; never infer a boot from uptime or wall time."""
    if sys.platform == 'darwin':
        import ctypes
        lib = ctypes.CDLL(None, use_errno=True)
        lib.sysctlbyname.argtypes = [ctypes.c_char_p, ctypes.c_void_p,
                                    ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p,
                                    ctypes.c_size_t]
        lib.sysctlbyname.restype = ctypes.c_int
        buffer = ctypes.create_string_buffer(128)
        size = ctypes.c_size_t(len(buffer))
        if lib.sysctlbyname(b'kern.bootsessionuuid', buffer, ctypes.byref(size), None, 0):
            raise OSError(ctypes.get_errno(), 'kernel boot identity unavailable')
        raw, source = buffer.value.decode('ascii'), 'darwin-kern.bootsessionuuid'
    elif sys.platform.startswith('linux'):
        raw = read_regular('/proc/sys/kernel/random/boot_id', 128)[0].decode('ascii').strip()
        source = 'linux-proc-boot-id'
    else:
        raise ProgressError('BOOT_IDENTITY_UNAVAILABLE')
    return boot_identity({'source': source, 'uuid': raw.lower()})


def validate_manifest(manifest):
    require(isinstance(manifest, dict) and manifest.get('schema') == SCHEMA
            and manifest.get('new_tasks_only') is True, 'NEW_TASK_MANIFEST_REQUIRED')
    require(set(manifest) <= {'schema', 'new_tasks_only', 'project_id', 'tasks'},
            'UNKNOWN_MANIFEST_FIELDS')
    valid_id = lambda x: isinstance(x, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', x)
    require(valid_id(manifest.get('project_id')), 'INVALID_PROJECT_ID')
    tasks = manifest.get('tasks')
    require(isinstance(tasks, list) and 1 <= len(tasks) <= MAX_TASKS, 'INVALID_TASK_COUNT')
    ids, parents = set(), {}
    for task in tasks:
        require(isinstance(task, dict) and set(task) <= {
            'task_id', 'role', 'parent_task_id', 'expected_state', 'process', 'checkpoint', 'herdr'},
            'INVALID_TASK')
        task_id = task.get('task_id')
        require(valid_id(task_id) and task_id not in ids, 'INVALID_OR_DUPLICATE_TASK_ID')
        ids.add(task_id)
        if 'role' in task:
            require(isinstance(task['role'], str) and 0 < len(task['role']) <= 128,
                    'INVALID_ROLE')
        if 'expected_state' in task:
            require(isinstance(task['expected_state'], str) and task['expected_state'] in (
                'working', 'standby', 'waiting_input', 'finished'), 'INVALID_EXPECTED_STATE')
        parents[task_id] = task.get('parent_task_id')
        if parents[task_id] is not None:
            require(valid_id(parents[task_id]), 'INVALID_PARENT')
        pin = task.get('process')
        require(isinstance(pin, dict) and set(pin) == {'pid', 'birth', 'executable'},
                'PROCESS_PIN_REQUIRED')
        require(type(pin['pid']) is int and pin['pid'] > 1, 'INVALID_PID')
        birth = pin['birth']
        require(isinstance(birth, list) and len(birth) == 2
                and all(type(x) is int for x in birth)
                and birth[0] > 0 and 0 <= birth[1] < 1000000, 'INVALID_BIRTH')
        require(isinstance(pin['executable'], str) and '\0' not in pin['executable']
                and Path(pin['executable']).is_absolute(), 'ABSOLUTE_EXECUTABLE_REQUIRED')
        if 'checkpoint' in task:
            cp = task['checkpoint']
            require(isinstance(cp, dict) and set(cp) <= {
                'path', 'max_bytes', 'stale_after_seconds'}, 'INVALID_CHECKPOINT')
            require(isinstance(cp.get('path'), str) and '\0' not in cp['path']
                    and Path(cp['path']).is_absolute(), 'ABSOLUTE_CHECKPOINT_REQUIRED')
            require(type(cp.get('max_bytes', MAX_CHECKPOINT)) is int
                    and 1 <= cp.get('max_bytes', MAX_CHECKPOINT) <= MAX_CHECKPOINT,
                    'INVALID_CHECKPOINT_LIMIT')
            require(type(cp.get('stale_after_seconds', 180)) is int
                    and 60 <= cp.get('stale_after_seconds', 180) <= 86400,
                    'INVALID_STALE_THRESHOLD')
        if 'herdr' in task:
            import collaboration_profile as profile
            binding = task['herdr']
            require(isinstance(binding, dict) and set(binding) == {'selector', 'target'},
                    'INVALID_HERDR_BINDING')
            selector = binding['selector']
            require(isinstance(selector, dict) and set(selector) == {'kind', 'name'}
                    and selector['kind'] == 'session', 'LOCAL_SESSION_SELECTOR_REQUIRED')
            name = selector['name']
            require(isinstance(name, str) and 0 < len(name) <= 128
                    and not name.startswith('-') and not any(c in name for c in '\0\r\n'),
                    'INVALID_SESSION_SELECTOR')
            profile.target_key(binding['target'])
    for task_id in ids:
        seen, current = set(), task_id
        while current is not None:
            require(current in ids and current not in seen, 'INVALID_PARENT_GRAPH')
            seen.add(current)
            current = parents[current]
    return manifest


def load_manifest(path):
    require(Path(path).is_absolute(), 'ABSOLUTE_MANIFEST_REQUIRED')
    data, _ = read_regular(path, MAX_MANIFEST)
    return validate_manifest(decode(data)), digest(data)


def process_observation(result):
    """Reject incomplete reads before comparing identities or claiming liveness."""
    require(isinstance(result, dict) and all(
        key in result for key in ('pid', 'birth', 'executable')),
        'INVALID_PROCESS_OBSERVATION')
    birth, executable = result['birth'], result['executable']
    require(type(result['pid']) is int and result['pid'] > 1
            and isinstance(birth, list) and len(birth) == 2
            and all(type(value) is int for value in birth)
            and birth[0] > 0 and 0 <= birth[1] < 1000000
            and isinstance(executable, str) and '\0' not in executable
            and Path(executable).is_absolute(), 'INVALID_PROCESS_OBSERVATION')
    return {'pid': result['pid'], 'birth': list(birth), 'executable': executable}


def native_process(pid, *, timeout):
    import cmux_daemon_identity as identity
    try:
        with identity.budget.limit(timeout):
            result = identity.process(pid)
    except identity.ProcessExited as exc:
        raise ObservedAbsent() from exc
    return process_observation(result)


def native_herdr(binding, *, timeout):
    import collaboration_profile as profile
    deadline = time.monotonic() + timeout
    def run(argv, **kwargs):
        left = deadline - time.monotonic()
        if left <= 0:
            raise subprocess.TimeoutExpired('herdr observation', timeout)
        kwargs['timeout'] = min(kwargs['timeout'], left)
        return subprocess.run(argv, **kwargs)
    return profile.check_herdr(binding['target'],
                               session=binding['selector']['name'], runner=run)


def observe_checkpoint(config, previous, now):
    result = {'native_proof': False, 'self_report_verified': False, 'observed_at': utc(now)}
    if config is None:
        return dict(result, state='NOT_CONFIGURED'), previous
    try:
        data, metadata = read_regular(config['path'], config.get('max_bytes', MAX_CHECKPOINT))
        body = decode(data)
        require(isinstance(body, dict), 'CHECKPOINT_OBJECT_REQUIRED')
        report = {key: body[key] for key in (
            'updated_at', 'status', 'completed_increment', 'current_work', 'blocker',
            'next_action', 'outstanding_operations') if key in body}
        sha = digest(data)
        changed = bool(previous) and previous['sha256'] != sha
        if previous and not changed:
            retained = previous
        else:
            reference = now if changed else min(now, metadata.st_mtime)
            if not previous and isinstance(report.get('updated_at'), str):
                try:
                    reference = min(reference, epoch(report['updated_at']))
                except (ValueError, OverflowError):
                    pass  # An invalid self-reported date never supplies freshness.
            retained = {'sha256': sha, 'freshness_reference': reference,
                        'last_content_change_observed_at': utc(now) if changed else None}
        age = max(0, now - retained['freshness_reference'])
        result.update(state='READ', path=config['path'], bytes=len(data), sha256=sha,
                      file_mtime=utc(metadata.st_mtime), content_updated_at=report.get('updated_at'),
                      content_updated_at_source='unverified_checkpoint_field',
                      last_content_change_observed_at=retained['last_content_change_observed_at'],
                      content_changed_since_last_observation=changed,
                      freshness_age_seconds=age,
                      stale=age >= config.get('stale_after_seconds', 180), self_report=report)
        return result, retained
    except FileNotFoundError:
        return dict(result, state='MISSING'), previous
    except (OSError, ValueError, OverflowError) as exc:
        return dict(result, state='UNKNOWN', error=type(exc).__name__), previous


def snapshot(manifest, prior=None, *, process_reader=native_process,
             herdr_reader=native_herdr, wall=time.time, monotonic=time.monotonic,
             budget_seconds=SNAPSHOT_BUDGET):
    prior = prior or {}
    started, deadline = wall(), monotonic() + budget_seconds
    rows, checkpoints = [], dict(prior)
    for task in manifest['tasks']:
        pin = task['process']
        observed = wall()
        handle = {'state': 'UNKNOWN', 'observed_at': utc(observed), 'pinned': pin}
        try:
            left = deadline - monotonic()
            if left <= 0:
                raise subprocess.TimeoutExpired('snapshot budget', budget_seconds)
            actual = process_observation(process_reader(pin['pid'], timeout=min(2, left)))
            handle.update(state='LIVE' if all(actual[k] == v for k, v in pin.items())
                          else 'REPLACED', actual={k: actual[k] for k in pin})
        except ObservedAbsent:
            handle['state'] = 'ABSENT'
        except Exception as exc:
            handle['error'] = type(exc).__name__
            if isinstance(exc, ProgressError):
                handle['error_code'] = str(exc)
        herdr = {'state': 'NOT_CONFIGURED', 'native_input_sent': False}
        if 'herdr' in task:
            herdr = {'state': 'UNKNOWN', 'native_input_sent': False}
            try:
                left = deadline - monotonic()
                if left <= 0 or handle['state'] != 'LIVE':
                    raise ProgressError('HERDR_READ_SKIPPED')
                result = herdr_reader(task['herdr'], timeout=min(20, left))
                require(result.get('identity_matches') is True, 'HERDR_IDENTITY_UNCONFIRMED')
                status = result.get('state')
                if (not isinstance(status, str) or not status.strip()
                        or len(status) > 128 or status.strip().lower() == 'unknown'):
                    status = 'UNKNOWN'
                herdr.update(state=status, identity_matches=True,
                             selector=task['herdr']['selector'], observed_at=utc(wall()),
                             target=task['herdr']['target'],
                             revision=runtime_counter(result.get('revision')),
                             state_change_seq=runtime_counter(result.get('state_change_seq')))
            except Exception as exc:
                herdr['error'] = type(exc).__name__
                if isinstance(exc, (ProgressError, ValueError)):
                    herdr['error_code'] = str(exc)
        if monotonic() >= deadline and 'checkpoint' in task:
            checkpoint = {'state': 'UNKNOWN', 'error': 'OBSERVATION_BUDGET_EXHAUSTED',
                          'native_proof': False, 'self_report_verified': False}
            retained = prior.get(task['task_id'])
        else:
            checkpoint, retained = observe_checkpoint(task.get('checkpoint'),
                                                       prior.get(task['task_id']), wall())
        if retained:
            checkpoints[task['task_id']] = retained
        rows.append({'task_id': task['task_id'], 'role': task.get('role'),
                     'parent_task_id': task.get('parent_task_id'),
                     'expected_state': task.get('expected_state'),
                     'expected_state_source': 'manifest_planning_declaration_only',
                     'handle': handle, 'herdr': herdr, 'checkpoint': checkpoint,
                     'accepted': False, 'task_completed': False,
                     'failure_cause': 'NOT_DETERMINED'})
    finished = wall()
    return {'schema': 'collaboration-progress-snapshot-v1',
            'project_id': manifest['project_id'], 'observed_at': utc(started),
            'completed_at': utc(finished), 'coverage_valid_until': utc(started + CADENCE),
            'scan_budget_seconds': budget_seconds,
            'scan_deadline_at': utc(started + budget_seconds),
            'scan_budget_exhausted': monotonic() >= deadline,
            'partial_coverage': any(r['handle']['state'] == 'UNKNOWN'
                                    or r['herdr']['state'] == 'UNKNOWN'
                                    or r['checkpoint']['state'] == 'UNKNOWN' for r in rows),
            'cadence_seconds': CADENCE, 'sampling_overrun': finished - started >= CADENCE,
            'tasks': rows, 'accepted': False, 'native_notification': False}, checkpoints


class Output:
    """One writer per output directory; no stale-lock deletion or lock stealing."""
    def __init__(self, path):
        self.path, self.fd, self.lock = Path(path), None, None

    def __enter__(self):
        require(self.path.is_absolute(), 'ABSOLUTE_OUTPUT_REQUIRED')
        self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            self.lock = os.open('.writer.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                                0o600, dir_fd=self.fd)
            info = os.fstat(self.lock)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'INVALID_LOCK_FILE')
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ProgressError('OUTPUT_HAS_ACTIVE_WRITER') from exc
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.lock is not None:
            os.close(self.lock)
        if self.fd is not None:
            os.close(self.fd)
        self.fd = self.lock = None

    def read(self, name, limit=MAX_JOURNAL):
        try:
            return read_regular(name, limit, dir_fd=self.fd)[0]
        except FileNotFoundError:
            return None

    def write(self, name, value):
        temporary = '.tmp-' + uuid.uuid4().hex
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.fd)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False, allow_nan=False)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass

    def event(self, event, stream):
        data = (json.dumps(event, ensure_ascii=False, allow_nan=False) + '\n').encode()
        fd = os.open('events.jsonl', os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
                     0o600, dir_fd=self.fd)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                    and info.st_size + len(data) <= MAX_JOURNAL, 'EVENT_JOURNAL_LIMIT')
            with os.fdopen(fd, 'ab', closefd=False) as log:
                log.write(data)
                log.flush()
                os.fsync(fd)
        finally:
            os.close(fd)
        if stream is not None:
            stream.write(data.decode())
            stream.flush()


@contextmanager
def stop_signals(stop):
    original = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            original[signum] = signal.signal(signum, lambda *_: stop.set())
    try:
        yield
    finally:
        for signum, handler in original.items():
            signal.signal(signum, handler)


def checkpoint_reference(checkpoint):
    """Events link to checkpoint content; snapshots retain the self-report."""
    return {key: checkpoint[key] for key in (
        'state', 'path', 'bytes', 'sha256', 'file_mtime', 'stale',
        'native_proof', 'self_report_verified') if key in checkpoint}


def runtime_target(target):
    return {'endpoint': {'transport': 'herdr', 'socket': target['endpoint']['socket']},
            **{key: target[key] for key in ('pane_id', 'terminal_id', 'native_session_id')}}


def journal_reservation(manifest, remaining_seconds):
    """Conservative byte bound for remaining samples plus one process STOP each.

    Fixed allowance covers JSON keys, timestamps, state, counters and hashes.
    Unbounded input strings (target, checkpoint path) are charged separately.
    Reject an oversized period before recording ACTIVE; never enlarge the log.
    """
    ticks = max(1, math.ceil(remaining_seconds / CADENCE))
    size = lambda value: len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
    reserve = 8192  # This invocation's lifecycle records and conservative slack.
    for task in manifest['tasks']:
        common = 4096 + size(task.get('checkpoint', {}).get('path', ''))
        reserve += common  # At most one STOP for this exact pinned process.
        if 'herdr' in task:
            reserve += ticks * (common + size(runtime_target(task['herdr']['target'])))
    return reserve


def runtime_state_event(row, previous, *, observer_id, project_id):
    """A literal verified runtime transition, never a business verdict or send."""
    observed = row['herdr']
    if (row['handle']['state'] != 'LIVE' or observed.get('identity_matches') is not True
            or observed.get('state') in ('UNKNOWN', 'NOT_CONFIGURED')):
        return None
    state = observed['state']
    if previous is not None and previous['state'] == state:
        return None
    prior_id = previous['event_id'] if previous is not None else None
    target = runtime_target(observed['target'])
    key = digest(json.dumps([observer_id, project_id, row['task_id'],
                             observed['selector'], target, prior_id, state],
                            sort_keys=True).encode())
    return {'type': 'HERDR_STATE_CHANGED' if previous is not None else 'HERDR_BASELINE',
            'event_id': key, 'task_id': row['task_id'], 'state': state,
            'previous_state': previous['state'] if previous is not None else None,
            'previous_event_id': prior_id,
            'previous_observed_at': previous.get('observed_at') if previous is not None else None,
            'runtime_observed_at': observed['observed_at'],
            'selector': observed['selector'], 'target': target,
            'revision': observed.get('revision'),
            'state_change_seq': observed.get('state_change_seq'),
            'checkpoint': checkpoint_reference(row['checkpoint']),
            'accepted': False, 'task_completed': False,
            'failure_cause': 'NOT_DETERMINED',
            'notification_candidate': previous is not None}


def run(manifest_path, output_path, *, mode, duration=None, resume=False,
        stream=None, process_reader=native_process, herdr_reader=native_herdr,
        wall=time.time, monotonic=time.monotonic, stop=None, waiter=None,
        boot_reader=native_boot_identity):
    require(mode in ('snapshot', 'watch'), 'INVALID_MODE')
    require(not resume or mode == 'watch', 'ONLY_WATCH_CAN_RESUME')
    require((resume and duration is None) or (mode == 'snapshot' and duration is None)
            or (mode == 'watch' and not resume and type(duration) is int
                and CADENCE <= duration <= 86400),
            'BOUNDED_DURATION_REQUIRED')
    manifest, pinned = load_manifest(manifest_path)
    destination = Path(output_path).resolve()
    inputs = [Path(manifest_path)] + [Path(task['checkpoint']['path'])
                                    for task in manifest['tasks'] if 'checkpoint' in task]
    require(all(source.resolve() != destination and destination not in source.resolve().parents
                for source in inputs), 'INPUT_INSIDE_OBSERVER_OUTPUT')
    stop = stop or threading.Event()
    waiter = waiter or stop.wait
    with Output(output_path) as output:
        previous = output.read('observer.json')
        journal = output.read('events.jsonl')
        require(not journal or journal.endswith(b'\n'), 'INCOMPLETE_EVENT_JOURNAL')
        events = [decode(line) for line in (journal or b'').splitlines()]
        require(all(isinstance(e, dict) and isinstance(e.get('type'), str)
                    and (e['type'] not in ('STOP', 'HERDR_BASELINE', 'HERDR_STATE_CHANGED')
                         or isinstance(e.get('event_id'), str))
                    for e in events), 'INVALID_EVENT_JOURNAL')
        stopped_ids = {e['event_id'] for e in events if e.get('type') == 'STOP'}
        runtime_states = {}
        for e in events:
            if e['type'] in ('HERDR_BASELINE', 'HERDR_STATE_CHANGED'):
                require(isinstance(e.get('task_id'), str) and isinstance(e.get('state'), str)
                        and isinstance(e.get('observed_at'), str), 'INVALID_RUNTIME_EVENT')
                runtime_states[e['task_id']] = e
        now, mono_now = wall(), monotonic()
        current_boot = boot_identity(boot_reader())
        if resume:
            require(previous is not None, 'NO_OBSERVER_TO_RESUME')
            state = decode(previous)
            require(isinstance(state, dict)
                    and state.get('schema') == 'collaboration-progress-observer-v1'
                    and all(isinstance(state.get(k), str) for k in (
                        'manifest_sha256', 'mode', 'observer_id', 'project_id',
                        'last_recorded_at', 'deadline_at'))
                    and isinstance(state.get('checkpoints'), dict), 'INVALID_OBSERVER_STATE')
            require(state['manifest_sha256'] == pinned and state['mode'] == mode,
                    'RESUME_BINDING_CHANGED')
            require(state['project_id'] == manifest['project_id']
                    and all(e.get('observer_id') == state['observer_id']
                            and e.get('project_id') == manifest['project_id'] for e in events),
                    'EVENT_JOURNAL_BINDING_CHANGED')
            clock_fields = ('started_monotonic', 'deadline_monotonic',
                            'last_recorded_monotonic')
            require('boot_identity' in state and all(
                type(state.get(k)) in (int, float) and math.isfinite(state[k])
                for k in clock_fields), 'RESUME_CLOCK_BINDING_REQUIRED')
            require(boot_identity(state['boot_identity']) == current_boot,
                    'RESUME_BOOT_IDENTITY_CHANGED')
            require(0 <= state['started_monotonic'] <= state['last_recorded_monotonic']
                    <= mono_now < state['deadline_monotonic']
                    and CADENCE <= state['deadline_monotonic'] - state['started_monotonic']
                    <= 86400, 'ORIGINAL_MONOTONIC_DEADLINE_EXPIRED_OR_CLOCK_REGRESSED')
            require(epoch(state['last_recorded_at']) <= now < epoch(state['deadline_at']),
                    'ORIGINAL_DEADLINE_EXPIRED_OR_CLOCK_REGRESSED')
        else:
            require(previous is None and journal is None and output.read('snapshot.json') is None,
                    'OUTPUT_ALREADY_USED_REQUIRE_RESUME')
            state = {'schema': 'collaboration-progress-observer-v1',
                     'observer_id': uuid.uuid4().hex, 'mode': mode,
                     'project_id': manifest['project_id'], 'manifest_path': str(manifest_path),
                     'manifest_sha256': pinned, 'started_at': utc(now),
                     'deadline_at': utc(now + (duration or SNAPSHOT_BUDGET)),
                     'boot_identity': current_boot, 'started_monotonic': mono_now,
                     'deadline_monotonic': mono_now + (duration or SNAPSHOT_BUDGET),
                     'cadence_seconds': CADENCE, 'checkpoints': {}}
        deadline = min(state['deadline_monotonic'],
                       mono_now + max(0, epoch(state['deadline_at']) - now))
        require(len(journal or b'') + journal_reservation(
                    manifest, max(0, deadline - mono_now)) <= MAX_JOURNAL,
                'JOURNAL_BUDGET_INSUFFICIENT_FOR_PERIOD')
        try:
            self_pin = process_observation(process_reader(os.getpid(), timeout=2))
            state['observer_handle'] = {'state': 'LIVE', 'process': self_pin}
        except Exception as exc:
            state['observer_handle'] = {'state': 'UNKNOWN', 'pid': os.getpid(),
                                        'error': type(exc).__name__}
            if isinstance(exc, ProgressError):
                state['observer_handle']['error_code'] = str(exc)
        state.update(status='ACTIVE', coverage='OBSERVING', last_recorded_at=utc(wall()),
                     last_recorded_monotonic=monotonic(),
                     native_notification=False, accepted=False)
        output.write('observer.json', state)
        reason = 'OBSERVATION_ERROR'
        def event(kind, **extra):
            return dict(type=kind, observer_id=state['observer_id'],
                        project_id=manifest['project_id'], observed_at=utc(wall()),
                        native_notification=False, supervisor_consumed=False, **extra)
        try:
            output.event(event('OBSERVER_STARTED', resumed=resume), stream)
            next_tick = monotonic()
            with stop_signals(stop):
                while True:
                    if stop.is_set():
                        reason = 'STOP_REQUESTED'
                        break
                    if monotonic() >= deadline:
                        reason = 'DEADLINE_REACHED'
                        break
                    require(load_manifest(manifest_path)[1] == pinned, 'MANIFEST_CHANGED')
                    snap, checkpoints = snapshot(
                        manifest, state['checkpoints'], process_reader=process_reader,
                        herdr_reader=herdr_reader, wall=wall, monotonic=monotonic,
                        budget_seconds=min(SNAPSHOT_BUDGET, deadline - monotonic()))
                    require(load_manifest(manifest_path)[1] == pinned, 'MANIFEST_CHANGED')
                    snap.update(observer_id=state['observer_id'], manifest_sha256=pinned)
                    interval = (epoch(snap['observed_at']) - epoch(state['last_snapshot_at'])
                                if state.get('last_snapshot_at') else None)
                    snap.update(seconds_since_previous_snapshot=interval,
                                cadence_gap_seconds=max(0, interval - CADENCE)
                                if interval is not None else 0)
                    snap['coverage_valid_until'] = utc(min(epoch(snap['coverage_valid_until']),
                                                         epoch(state['deadline_at'])))
                    output.write('snapshot.json', snap)
                    for row in snap['tasks']:
                        change = runtime_state_event(row, runtime_states.get(row['task_id']),
                                                     observer_id=state['observer_id'],
                                                     project_id=manifest['project_id'])
                        if change is not None:
                            kind = change.pop('type')
                            observed_event = event(kind, **change)
                            output.event(observed_event, stream)
                            runtime_states[row['task_id']] = observed_event
                        if row['handle']['state'] not in ('ABSENT', 'REPLACED'):
                            continue
                        key = digest(json.dumps([manifest['project_id'], row['task_id'],
                                                 row['handle']['pinned']], sort_keys=True).encode())
                        if key not in stopped_ids:
                            output.event(event('STOP', event_id=key, task_id=row['task_id'],
                                               reason=row['handle']['state'],
                                               checkpoint=checkpoint_reference(row['checkpoint']),
                                               accepted=False), stream)
                            stopped_ids.add(key)
                    state.update(checkpoints=checkpoints, last_recorded_at=utc(wall()),
                                 last_recorded_monotonic=monotonic(),
                                 last_snapshot_at=snap['observed_at'],
                                 coverage_valid_until=snap['coverage_valid_until'])
                    output.write('observer.json', state)
                    if mode == 'snapshot':
                        reason = 'SNAPSHOT_COMPLETE'
                        break
                    next_tick += CADENCE
                    if next_tick <= monotonic():
                        next_tick += CADENCE * (math.floor((monotonic() - next_tick) / CADENCE) + 1)
                    waiter(max(0, min(next_tick, deadline) - monotonic()))
        except ProgressError as exc:
            reason = str(exc)
            raise
        finally:
            state.update(status='STOPPED', coverage='UNKNOWN', stop_reason=reason,
                         stopped_at=utc(wall()), last_recorded_at=utc(wall()),
                         last_recorded_monotonic=monotonic())
            output.write('observer.json', state)
            output.event(event('OBSERVER_STOPPED', reason=reason, coverage='UNKNOWN'), stream)
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('validate', 'snapshot', 'watch'):
        command = commands.add_parser(name)
        command.add_argument('--manifest', type=Path, required=True)
        if name != 'validate':
            command.add_argument('--output', type=Path, required=True)
        if name == 'watch':
            command.add_argument('--duration-seconds', type=int)
            command.add_argument('--resume', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'validate':
            manifest, sha = load_manifest(args.manifest)
            print(json.dumps({'valid': True, 'project_id': manifest['project_id'],
                              'task_count': len(manifest['tasks']), 'manifest_sha256': sha,
                              'live_identity_verified': False, 'accepted': False}))
        else:
            run(args.manifest, args.output, mode=args.command,
                duration=getattr(args, 'duration_seconds', None),
                resume=getattr(args, 'resume', False), stream=sys.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc) if isinstance(exc, ProgressError) else type(exc).__name__,
                          'native_input_sent': False, 'accepted': False}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
