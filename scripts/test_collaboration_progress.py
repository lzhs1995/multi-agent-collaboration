"""Run with python3 -B; temporary fixtures stay in explicitly selected TMPDIR."""
import sys

sys.dont_write_bytecode = True

import copy
import io
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import collaboration_progress as p


def native_lifecycle_probe():
    """Fresh interpreter: OfflineResult's ancestry mock must stay in its parent."""
    child = subprocess.Popen(
        [sys.executable, '-B', '-c',
         'import sys; print("READY", flush=True); command = sys.stdin.readline(); '
         'print("EXITING", flush=True); sys.exit(0 if command == "EXIT\\n" else 1)'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def receive(expected):
        readable, _, _ = select.select([child.stdout], [], [], 10)
        if not readable or child.stdout.readline() != expected:
            raise RuntimeError('fixture handshake failed')

    try:
        receive(b'READY\n')
        if child.poll() is not None:
            raise RuntimeError('fixture exited before observation')
        pin = p.native_process(child.pid, timeout=2)
        manifest = {'schema': p.SCHEMA, 'new_tasks_only': True,
                    'project_id': 'native-fixture',
                    'tasks': [{'task_id': 'fixture', 'process': pin}]}
        p.validate_manifest(manifest)
        live, _ = p.snapshot(manifest, process_reader=p.native_process)
        child.stdin.write(b'EXIT\n')
        child.stdin.flush()
        receive(b'EXITING\n')
    finally:
        # Closing stdin also releases the fixture after any failed assertion.
        # No sleep or peer signal is used to make process lifetime predictable.
        child.stdin.close()
        child.wait(timeout=10)
        child.stdout.close()
    if child.returncode != 0:
        raise RuntimeError('fixture did not acknowledge natural exit')
    absent, _ = p.snapshot(manifest, process_reader=p.native_process)
    print(json.dumps({'ready': True, 'natural_exit': child.returncode,
                      'live': live['tasks'][0]['handle']['state'],
                      'after_exit': absent['tasks'][0]['handle']['state'],
                      'accepted': absent['tasks'][0]['accepted']}))


class Clock:
    def __init__(self):
        self.value = 1800000000.0
        self.waits = []

    def now(self):
        return self.value

    def wait(self, seconds):
        self.waits.append(seconds)
        self.value += seconds


class IndependentClock(Clock):
    def __init__(self):
        super().__init__()
        self.monotonic_value = 10000.0

    def monotonic(self):
        return self.monotonic_value

    def wait(self, seconds):
        super().wait(seconds)
        self.monotonic_value += seconds


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pin = {'pid': 111, 'birth': [1700000000, 321], 'executable': '/example/agent'}
        self.manifest = {'schema': p.SCHEMA, 'new_tasks_only': True,
                         'project_id': 'dissertation',
                         'tasks': [{'task_id': 'chapter-a', 'process': self.pin}]}
        self.path = self.root / 'tasks.json'
        self.output = self.root / 'out'
        self.clock = Clock()
        self.boot = {'source': 'darwin-kern.bootsessionuuid',
                     'uuid': '11111111-1111-4111-8111-111111111111'}
        self.save_manifest()

    def save_manifest(self):
        self.path.write_text(json.dumps(self.manifest))

    def live(self, pid, *, timeout):
        self.assertGreater(timeout, 0)
        return dict(self.pin, pid=pid)

    def absent(self, pid, *, timeout):
        if pid == os.getpid():
            return self.live(pid, timeout=timeout)
        raise p.ObservedAbsent()

    def take(self, **kwargs):
        return p.snapshot(self.manifest, process_reader=kwargs.pop('process_reader', self.live),
                          wall=self.clock.now, monotonic=self.clock.now, **kwargs)

    def run_observer(self, **kwargs):
        return p.run(self.path, self.output, mode=kwargs.pop('mode', 'watch'),
                     duration=kwargs.pop('duration', 120),
                     process_reader=kwargs.pop('process_reader', self.live),
                     wall=kwargs.pop('wall', self.clock.now),
                     monotonic=kwargs.pop('monotonic', self.clock.now),
                     boot_reader=kwargs.pop('boot_reader', lambda: dict(self.boot)),
                     waiter=kwargs.pop('waiter', self.clock.wait), **kwargs)

    def events(self):
        return [json.loads(line) for line in (self.output / 'events.jsonl').read_text().splitlines()]

    def bind_herdr(self):
        self.manifest['tasks'][0]['herdr'] = {
            'selector': {'kind': 'session', 'name': 'chapter-a'},
            'target': {'endpoint': {'transport': 'herdr', 'socket': '/tmp/chapter-a.sock'},
                       'pane_id': 'w1:p2', 'terminal_id': 'term_bound',
                       'native_session_id': 'session-bound'}}
        self.save_manifest()

    def checkpoint(self, data, age=0):
        path = self.root / 'checkpoint.json'
        path.write_text(json.dumps(data))
        os.utime(path, (self.clock.value - age, self.clock.value - age))
        self.manifest['tasks'][0]['checkpoint'] = {'path': str(path)}
        self.save_manifest()
        return path

    def test_requires_explicit_new_task_schema_and_bounded_membership(self):
        for mutate in (lambda m: m.pop('new_tasks_only'),
                       lambda m: m.update(schema='legacy-task'),
                       lambda m: m.update(tasks=[]),
                       lambda m: m.update(tasks=m['tasks'] * (p.MAX_TASKS + 1)),
                       lambda m: m['tasks'][0]['process'].update(pid=True),
                       lambda m: m['tasks'][0]['process'].update(birth=[1, 1000000]),
                       lambda m: m['tasks'][0]['process'].update(executable='agent')):
            manifest = copy.deepcopy(self.manifest)
            mutate(manifest)
            with self.subTest(manifest=manifest), self.assertRaises(p.ProgressError):
                p.validate_manifest(manifest)

    def test_parent_must_exist_and_not_form_cycle(self):
        self.manifest['tasks'][0]['parent_task_id'] = 'missing'
        with self.assertRaisesRegex(p.ProgressError, 'PARENT_GRAPH'):
            p.validate_manifest(self.manifest)
        self.manifest['tasks'][0]['parent_task_id'] = 'chapter-a'
        with self.assertRaisesRegex(p.ProgressError, 'PARENT_GRAPH'):
            p.validate_manifest(self.manifest)

    def test_manifest_duplicate_keys_refused(self):
        self.path.write_text('{"schema":"old","schema":"new"}')
        with self.assertRaisesRegex(p.ProgressError, 'DUPLICATE_JSON_KEY'):
            p.load_manifest(self.path)

    def test_pid_birth_and_executable_replacement_are_not_live(self):
        for key, value in [('birth', [1800000000, 123]), ('executable', '/other/agent')]:
            actual = dict(self.pin, **{key: value})
            snap, _ = self.take(process_reader=lambda *a, **k: actual)
            self.assertEqual(snap['tasks'][0]['handle']['state'], 'REPLACED')
            self.assertFalse(snap['tasks'][0]['accepted'])

    def test_planned_state_does_not_override_runtime_or_acceptance(self):
        for expected in ('working', 'standby', 'waiting_input', 'finished'):
            self.manifest['tasks'][0]['expected_state'] = expected
            p.validate_manifest(self.manifest)
            snap, _ = self.take()
            row = snap['tasks'][0]
            self.assertEqual(row['expected_state'], expected)
            self.assertEqual(row['handle']['state'], 'LIVE')
            self.assertFalse(row['accepted'])
            self.assertFalse(row['task_completed'])
            self.assertEqual(row['failure_cause'], 'NOT_DETERMINED')
        self.manifest['tasks'][0]['expected_state'] = 'failed'
        with self.assertRaisesRegex(p.ProgressError, 'INVALID_EXPECTED_STATE'):
            p.validate_manifest(self.manifest)

    def test_absence_does_not_classify_standby_as_failure(self):
        self.manifest['tasks'][0]['expected_state'] = 'standby'
        snap, _ = self.take(process_reader=self.absent)
        row = snap['tasks'][0]
        self.assertEqual(row['handle']['state'], 'ABSENT')
        self.assertEqual(row['expected_state'], 'standby')
        self.assertEqual(row['failure_cause'], 'NOT_DETERMINED')
        self.assertFalse(row['task_completed'])

    def test_timeout_permission_and_invalid_reads_are_unknown_not_absent(self):
        for exc in (subprocess.TimeoutExpired('read', 2), PermissionError(), ValueError()):
            def read(*args, **kwargs):
                raise exc
            snap, _ = self.take(process_reader=read)
            self.assertEqual(snap['tasks'][0]['handle']['state'], 'UNKNOWN')
        snap, _ = self.take(process_reader=lambda *a, **k: {})
        self.assertEqual(snap['tasks'][0]['handle']['state'], 'UNKNOWN')

    def test_absent_task_stop_is_persisted_once_across_ticks_and_stdout(self):
        output = io.StringIO()
        self.run_observer(process_reader=self.absent, stream=output)
        stops = [e for e in self.events() if e['type'] == 'STOP']
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0]['reason'], 'ABSENT')
        self.assertFalse(stops[0]['native_notification'])
        self.assertFalse(stops[0]['supervisor_consumed'])
        self.assertEqual(len([json.loads(line) for line in output.getvalue().splitlines()
                              if json.loads(line)['type'] == 'STOP']), 1)
        self.assertEqual(self.clock.waits, [60, 60])

    def test_replaced_handle_generates_stop_but_unknown_does_not(self):
        def replaced(pid, **kwargs):
            return dict(self.pin, pid=pid, birth=[1800000000, 999])
        self.run_observer(process_reader=replaced)
        self.assertEqual([e['reason'] for e in self.events() if e['type'] == 'STOP'],
                         ['REPLACED'])
        self.output = self.root / 'unknown'
        def unknown(*args, **kwargs):
            raise subprocess.TimeoutExpired('read', 2)
        self.run_observer(process_reader=unknown)
        self.assertFalse([e for e in self.events() if e['type'] == 'STOP'])

    def test_checkpoint_stale_and_touch_cannot_reset_content_age(self):
        path = self.checkpoint({'updated_at': p.utc(self.clock.value - 600),
                                'status': 'completed', 'completed_increment': 'claimed done'}, age=600)
        first, prior = self.take()
        row = first['tasks'][0]
        self.assertTrue(row['checkpoint']['stale'])
        self.assertEqual(row['handle']['state'], 'LIVE')
        self.assertFalse(row['accepted'])
        self.assertFalse(row['task_completed'])
        self.assertFalse(row['checkpoint']['native_proof'])
        self.clock.value += 60
        os.utime(path, (self.clock.value, self.clock.value))
        second, _ = self.take(prior=prior)
        cp = second['tasks'][0]['checkpoint']
        self.assertTrue(cp['stale'])
        self.assertEqual(cp['freshness_age_seconds'], 660)
        self.assertFalse(cp['content_changed_since_last_observation'])

    def test_checkpoint_actual_change_and_self_report_are_separate(self):
        path = self.checkpoint({'current_work': 'first'}, age=600)
        _, prior = self.take()
        self.clock.value += 60
        path.write_text(json.dumps({'current_work': 'second', 'updated_at': 'bad timestamp'}))
        second, _ = self.take(prior=prior)
        cp = second['tasks'][0]['checkpoint']
        self.assertTrue(cp['content_changed_since_last_observation'])
        self.assertFalse(cp['stale'])
        self.assertEqual(cp['last_content_change_observed_at'], p.utc(self.clock.value))
        self.assertEqual(cp['content_updated_at'], 'bad timestamp')
        self.assertFalse(cp['self_report_verified'])

    def test_checkpoint_size_symlink_and_nonregular_are_not_read_as_proof(self):
        path = self.checkpoint({'work': 'x' * 100})
        self.manifest['tasks'][0]['checkpoint']['max_bytes'] = 8
        self.assertEqual(self.take()[0]['tasks'][0]['checkpoint']['state'], 'UNKNOWN')
        link = self.root / 'link'
        link.symlink_to(path)
        self.manifest['tasks'][0]['checkpoint'] = {'path': str(link)}
        self.assertEqual(self.take()[0]['tasks'][0]['checkpoint']['state'], 'UNKNOWN')
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        self.manifest['tasks'][0]['checkpoint'] = {'path': str(fifo)}
        self.assertEqual(self.take()[0]['tasks'][0]['checkpoint']['state'], 'UNKNOWN')

    def test_herdr_is_read_only_same_selector_and_native_session_verified(self):
        self.bind_herdr()
        binding = self.manifest['tasks'][0]['herdr']
        responses = [
            {'server': {'running': True, 'status': 'running', 'compatible': True,
                        'endpoint_compatible': True, 'session': 'chapter-a',
                        'socket': '/tmp/chapter-a.sock'}},
            {'result': {'agents': [{'pane_id': 'w1:p2', 'terminal_id': 'term_bound',
                                    'agent_session': {'kind': 'id', 'value': 'session-bound'},
                                    'agent_status': 'blocked'}]}}]
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, json.dumps(responses[len(calls)-1]))
        with patch('collaboration_progress.subprocess.run', side_effect=run), \
                patch('os.kill', side_effect=AssertionError('must not signal')):
            result = p.native_herdr(binding, timeout=1)
        self.assertEqual(result['state'], 'blocked')
        self.assertEqual(calls, [['herdr', '--session', 'chapter-a', 'status', '--json'],
                                 ['herdr', '--session', 'chapter-a', 'agent', 'list']])
        self.assertFalse(result['input_sent'])

    def test_herdr_timeout_is_unknown_without_process_stop(self):
        self.bind_herdr()
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired('herdr', 20)
        self.run_observer(herdr_reader=timeout)
        snap = json.loads((self.output / 'snapshot.json').read_text())
        self.assertEqual(snap['tasks'][0]['handle']['state'], 'LIVE')
        self.assertEqual(snap['tasks'][0]['herdr']['state'], 'UNKNOWN')
        self.assertFalse([e for e in self.events() if e['type'] == 'STOP'])

    def test_herdr_unavailable_status_is_partial_unknown_with_verified_identity(self):
        self.bind_herdr()
        binding = self.manifest['tasks'][0]['herdr']
        for value in ('missing', None, 'unknown', 'UNKNOWN', ' Unknown ', '', 1, [], {}):
            with self.subTest(status=value):
                row = {'pane_id': 'w1:p2', 'terminal_id': 'term_bound',
                       'agent_session': {'kind': 'id', 'value': 'session-bound'}}
                if value != 'missing':
                    row['agent_status'] = value
                replies = [
                    {'server': {'running': True, 'status': 'running', 'compatible': True,
                                'endpoint_compatible': True, 'session': 'chapter-a',
                                'socket': binding['target']['endpoint']['socket']}},
                    {'result': {'agents': [row]}}]
                calls = []
                def read(argv, **kwargs):
                    calls.append(argv)
                    return subprocess.CompletedProcess(argv, 0, json.dumps(replies.pop(0)))
                with patch('collaboration_progress.subprocess.run', side_effect=read):
                    snap, _ = self.take()
                observed = snap['tasks'][0]
                self.assertEqual(observed['handle']['state'], 'LIVE')
                self.assertEqual(observed['herdr']['state'], 'UNKNOWN')
                self.assertTrue(observed['herdr']['identity_matches'])
                self.assertTrue(snap['partial_coverage'])
                self.assertEqual(calls, [
                    ['herdr', '--session', 'chapter-a', 'status', '--json'],
                    ['herdr', '--session', 'chapter-a', 'agent', 'list']])

    def test_known_herdr_status_preserves_complete_observation(self):
        self.bind_herdr()
        for value in ('working', 'idle', 'done', 'blocked'):
            with self.subTest(status=value):
                snap, _ = self.take(herdr_reader=lambda *a, **k: {
                    'identity_matches': True, 'state': value})
                self.assertEqual(snap['tasks'][0]['herdr']['state'], value)
                self.assertFalse(snap['partial_coverage'])
                self.assertFalse(snap['tasks'][0]['task_completed'])

    def test_snapshot_budget_skips_unobserved_tasks_as_unknown(self):
        self.manifest['tasks'].append({'task_id': 'chapter-b', 'process': dict(self.pin, pid=222)})
        calls = []
        def slow(pid, **kwargs):
            calls.append(pid)
            self.clock.value += 2
            return dict(self.pin, pid=pid)
        snap, _ = self.take(process_reader=slow, budget_seconds=1)
        self.assertEqual(calls, [111])
        self.assertEqual([r['handle']['state'] for r in snap['tasks']], ['LIVE', 'UNKNOWN'])
        self.assertTrue(snap['partial_coverage'])
        self.assertTrue(snap['scan_budget_exhausted'])

    def test_scan_uses_remaining_watch_budget_and_records_overrun_without_catchup(self):
        budgets = []
        def slow(pid, *, timeout):
            budgets.append(timeout)
            if pid == 111:
                self.clock.value += 65
            return dict(self.pin, pid=pid)
        state = self.run_observer(duration=60, process_reader=slow)
        snap = json.loads((self.output / 'snapshot.json').read_text())
        self.assertEqual(snap['scan_budget_seconds'], 50)
        self.assertTrue(snap['sampling_overrun'])
        self.assertTrue(snap['scan_budget_exhausted'])
        self.assertEqual(state['stop_reason'], 'DEADLINE_REACHED')
        self.assertEqual(self.clock.waits, [0])

    def test_delayed_tick_reports_cadence_gap(self):
        def delayed(seconds):
            self.clock.wait(seconds + 10)
        self.run_observer(duration=120, waiter=delayed)
        snap = json.loads((self.output / 'snapshot.json').read_text())
        self.assertEqual(snap['cadence_gap_seconds'], 10)
        # First delayed tick is 70 seconds after the initial observation.
        self.assertEqual(self.clock.waits[:2], [70, 60])

    def test_all_herdr_tasks_share_scan_deadline_not_per_task_full_timeout(self):
        self.bind_herdr()
        template = self.manifest['tasks'][0]
        self.manifest['tasks'] = [dict(copy.deepcopy(template), task_id=f'task-{i}')
                                  for i in range(p.MAX_TASKS)]
        calls = []
        def herdr(binding, *, timeout):
            calls.append(timeout)
            self.clock.value += timeout
            raise subprocess.TimeoutExpired('herdr', timeout)
        snap, _ = self.take(herdr_reader=herdr)
        self.assertEqual(calls, [20, 20, 10])
        self.assertTrue(snap['partial_coverage'])
        self.assertEqual(len(snap['tasks']), p.MAX_TASKS)
        self.assertEqual(snap['tasks'][-1]['handle']['state'], 'UNKNOWN')

    def test_manifest_change_during_read_discards_snapshot_and_stops_observer(self):
        def changed(pid, **kwargs):
            if pid == 111:
                self.manifest['project_id'] = 'different'
                self.save_manifest()
            return dict(self.pin, pid=pid)
        with self.assertRaisesRegex(p.ProgressError, 'MANIFEST_CHANGED'):
            self.run_observer(process_reader=changed)
        self.assertFalse((self.output / 'snapshot.json').exists())
        self.assertEqual(json.loads((self.output / 'observer.json').read_text())['stop_reason'],
                         'MANIFEST_CHANGED')
        self.assertFalse([e for e in self.events() if e['type'] == 'STOP'])

    def test_single_writer_rejected_without_mutating_existing_outputs(self):
        with p.Output(self.output):
            with self.assertRaisesRegex(p.ProgressError, 'ACTIVE_WRITER'):
                self.run_observer()
            self.assertFalse((self.output / 'observer.json').exists())

    def test_manifest_or_checkpoint_cannot_be_overwritten_by_observer_output(self):
        self.output.mkdir()
        inside = self.output / 'observer.json'
        inside.write_text('preserve checkpoint')
        self.manifest['tasks'][0]['checkpoint'] = {'path': str(inside)}
        self.save_manifest()
        with self.assertRaisesRegex(p.ProgressError, 'INPUT_INSIDE'):
            self.run_observer()
        self.assertEqual(inside.read_text(), 'preserve checkpoint')

    def test_output_symlink_and_truncated_journal_fail_closed(self):
        real = self.root / 'real'
        real.mkdir()
        self.output.symlink_to(real, target_is_directory=True)
        with self.assertRaises(OSError):
            self.run_observer()
        self.output.unlink()
        self.run_observer(duration=60)
        journal = self.output / 'events.jsonl'
        with journal.open('ab') as stream:
            stream.write(b'{"type":"STOP"')
        before = journal.read_bytes()
        with self.assertRaisesRegex(p.ProgressError, 'INCOMPLETE_EVENT_JOURNAL'):
            self.run_observer(duration=None, resume=True)
        self.assertEqual(journal.read_bytes(), before)

    def test_graceful_signal_handler_only_requests_observer_stop(self):
        stop = threading.Event()
        original = signal.getsignal(signal.SIGTERM)
        with patch('os.kill', side_effect=AssertionError('must not signal peers')):
            with p.stop_signals(stop):
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                self.assertTrue(stop.is_set())
        self.assertEqual(signal.getsignal(signal.SIGTERM), original)

    def test_resume_preserves_deadline_and_stop_dedup(self):
        stop = threading.Event()
        def stop_after_tick(seconds):
            self.clock.wait(seconds)
            stop.set()
        first = self.run_observer(duration=180, process_reader=self.absent,
                                  stop=stop, waiter=stop_after_tick)
        self.assertEqual(first['stop_reason'], 'STOP_REQUESTED')
        original_deadline = first['deadline_at']
        second = self.run_observer(duration=None, resume=True, process_reader=self.absent)
        self.assertEqual(second['deadline_at'], original_deadline)
        self.assertEqual(second['stop_reason'], 'DEADLINE_REACHED')
        self.assertEqual(len([e for e in self.events() if e['type'] == 'STOP']), 1)
        self.assertEqual(second['coverage'], 'UNKNOWN')

    def test_resume_refuses_changed_manifest_and_expired_deadline(self):
        self.run_observer(duration=60)
        with self.assertRaisesRegex(p.ProgressError, 'DEADLINE_EXPIRED'):
            self.run_observer(duration=None, resume=True)
        self.manifest['project_id'] = 'changed'
        self.save_manifest()
        with self.assertRaisesRegex(p.ProgressError, 'BINDING_CHANGED'):
            self.run_observer(duration=None, resume=True)

    def interrupted_watch(self, *, rollback=0):
        self.clock = IndependentClock()
        stop = threading.Event()
        def stop_tick(seconds):
            self.clock.wait(seconds)
            self.clock.value -= rollback
            stop.set()
        return self.run_observer(duration=180, process_reader=self.absent,
                                 stop=stop, waiter=stop_tick,
                                 monotonic=self.clock.monotonic)

    def test_resume_wall_rollback_cannot_extend_monotonic_deadline(self):
        first = self.interrupted_watch(rollback=50)
        self.assertEqual(self.clock.monotonic_value, 10060)
        second = self.run_observer(duration=None, resume=True, process_reader=self.absent,
                                   monotonic=self.clock.monotonic)
        self.assertEqual(self.clock.monotonic_value, 10180)
        self.assertEqual(first['deadline_at'], second['deadline_at'])
        self.assertEqual(first['deadline_monotonic'], second['deadline_monotonic'])
        self.assertEqual(second['boot_identity'], self.boot)
        self.assertEqual(len([e for e in self.events() if e['type'] == 'STOP']), 1)

    def test_resume_same_boot_retains_elapsed_downtime(self):
        first = self.interrupted_watch()
        self.clock.wait(30)
        second = self.run_observer(duration=None, resume=True, process_reader=self.absent,
                                   monotonic=self.clock.monotonic)
        self.assertEqual(self.clock.monotonic_value, 10180)
        self.assertEqual(first['deadline_monotonic'], second['deadline_monotonic'])
        self.assertEqual(len([e for e in self.events() if e['type'] == 'STOP']), 1)

    def assert_resume_refused_without_output_change(self, expression, **kwargs):
        before = {p.name: p.read_bytes() for p in self.output.iterdir() if p.is_file()}
        with self.assertRaisesRegex(p.ProgressError, expression):
            self.run_observer(duration=None, resume=True,
                              monotonic=self.clock.monotonic, **kwargs)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.output.iterdir()
                                  if p.is_file()})

    def test_resume_changed_boot_is_refused_without_reset(self):
        self.interrupted_watch()
        self.boot['uuid'] = '22222222-2222-4222-8222-222222222222'
        self.assert_resume_refused_without_output_change('RESUME_BOOT_IDENTITY_CHANGED')

    def test_resume_missing_clock_binding_is_refused_without_reset(self):
        original = self.interrupted_watch()
        for missing in ('boot_identity', 'started_monotonic', 'deadline_monotonic',
                        'last_recorded_monotonic'):
            with self.subTest(missing=missing):
                state = dict(original)
                del state[missing]
                (self.output/'observer.json').write_text(json.dumps(state))
                self.assert_resume_refused_without_output_change('RESUME_CLOCK_BINDING_REQUIRED')

    def test_resume_expired_monotonic_budget_refused_despite_wall_rollback(self):
        self.interrupted_watch(rollback=50)
        self.clock.wait(120)
        self.assert_resume_refused_without_output_change('ORIGINAL_MONOTONIC_DEADLINE_EXPIRED')

    def test_resume_monotonic_regression_is_refused(self):
        self.interrupted_watch()
        self.clock.monotonic_value -= 1
        self.assert_resume_refused_without_output_change('CLOCK_REGRESSED')

    def test_resume_invalid_boot_identity_is_refused_without_reset(self):
        self.interrupted_watch()
        for invalid in ({}, {'source': 'cpu-uptime', 'uuid': self.boot['uuid']},
                        dict(self.boot, uuid='not-a-uuid')):
            with self.subTest(boot=invalid):
                self.assert_resume_refused_without_output_change(
                    'INVALID_BOOT_IDENTITY', boot_reader=lambda: invalid)

    def test_native_boot_identity_is_stable_kernel_uuid(self):
        first = p.native_boot_identity()
        second = p.native_boot_identity()
        self.assertEqual(first, second)
        self.assertEqual(p.boot_identity(first), first)
        self.assertIn(first['source'], ('darwin-kern.bootsessionuuid', 'linux-proc-boot-id'))

    def test_unbounded_watch_and_implicit_output_reuse_refused(self):
        for value in (None, 0, 59, 86401, True):
            with self.subTest(value=value), self.assertRaisesRegex(p.ProgressError, 'BOUNDED'):
                self.run_observer(duration=value)
        self.run_observer(mode='snapshot', duration=None)
        with self.assertRaisesRegex(p.ProgressError, 'ALREADY_USED'):
            self.run_observer(mode='snapshot', duration=None)

    def test_observer_records_lifecycle_and_coverage_is_unknown_on_exit(self):
        state = self.run_observer(mode='snapshot', duration=None)
        self.assertEqual(state['status'], 'STOPPED')
        self.assertEqual(state['coverage'], 'UNKNOWN')
        self.assertEqual(state['stop_reason'], 'SNAPSHOT_COMPLETE')
        self.assertEqual([e['type'] for e in self.events()],
                         ['OBSERVER_STARTED', 'OBSERVER_STOPPED'])
        self.assertEqual(state['observer_handle']['process']['pid'], os.getpid())

    def test_native_reader_reuses_original_identity_without_argv_leak(self):
        import cmux_daemon_identity as identity
        native = dict(self.pin, argv=['sensitive arguments'], env={'secret': 'private'})
        with patch.object(identity, 'process', return_value=native) as read:
            actual = p.native_process(111, timeout=2)
        read.assert_called_once_with(111)
        self.assertEqual(actual, self.pin)
        with patch.object(identity, 'process', side_effect=identity.ProcessExited('absent')):
            with self.assertRaises(p.ObservedAbsent):
                p.native_process(111, timeout=2)
        with patch.object(identity, 'process', side_effect=identity.IdentityError('inaccessible')):
            with self.assertRaises(identity.IdentityError):
                p.native_process(111, timeout=2)

    def invalid_process_observations(self):
        return [None, [], {},
                *[{key: value for key, value in self.pin.items() if key != missing}
                  for missing in self.pin],
                *[dict(self.pin, **{key: value}) for key, value in (
                    ('pid', True), ('pid', '111'), ('pid', 0),
                    ('birth', None), ('birth', []), ('birth', [1]),
                    ('birth', [True, 0]), ('birth', [1, False]),
                    ('birth', [0, 0]), ('birth', [1, -1]), ('birth', [1, 1000000]),
                    ('executable', None), ('executable', ''),
                    ('executable', 'relative/path'), ('executable', '/bad\0path'))]]

    def test_native_incomplete_or_invalid_pin_is_structured_unknown_not_stop(self):
        import cmux_daemon_identity as identity
        for index, observed in enumerate(self.invalid_process_observations()):
            with self.subTest(observed=observed), patch.object(
                    identity, 'process', return_value=observed):
                with self.assertRaisesRegex(p.ProgressError, '^INVALID_PROCESS_OBSERVATION$'):
                    p.native_process(111, timeout=2)
                self.output = self.root / f'invalid-native-{index}'
                state = self.run_observer(mode='snapshot', duration=None,
                                          process_reader=p.native_process)
                snap = json.loads((self.output / 'snapshot.json').read_text())
                handle = snap['tasks'][0]['handle']
                self.assertEqual(handle['state'], 'UNKNOWN')
                self.assertEqual(handle['error_code'], 'INVALID_PROCESS_OBSERVATION')
                self.assertEqual(state['observer_handle']['state'], 'UNKNOWN')
                self.assertEqual(state['observer_handle']['error_code'],
                                 'INVALID_PROCESS_OBSERVATION')
                self.assertFalse([event for event in self.events() if event['type'] == 'STOP'])

    def test_injected_incomplete_or_invalid_pin_cannot_be_replacement(self):
        for observed in self.invalid_process_observations():
            with self.subTest(observed=observed):
                snap, _ = self.take(process_reader=lambda *a, **k: observed)
                handle = snap['tasks'][0]['handle']
                self.assertEqual(handle['state'], 'UNKNOWN')
                self.assertEqual(handle['error_code'], 'INVALID_PROCESS_OBSERVATION')

    @unittest.skipUnless(sys.platform == 'darwin', 'native process reader requires Darwin')
    def test_native_live_handle_then_natural_exit(self):
        probe = subprocess.run(
            [sys.executable, '-B', '-c',
             'import sys; sys.path.insert(0, sys.argv[1]); '
             'from test_collaboration_progress import native_lifecycle_probe; '
             'native_lifecycle_probe()', str(Path(__file__).resolve().parent)],
            cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertEqual(json.loads(probe.stdout), {
            'ready': True, 'natural_exit': 0, 'live': 'LIVE',
            'after_exit': 'ABSENT', 'accepted': False})


if __name__ == '__main__':
    unittest.main()
