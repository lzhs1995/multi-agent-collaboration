import copy
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import collaboration_profile as profile
import collaboration_progress as progress
import federated_progress_adapter as adapter
from test_federated_collaboration import make_fixture, write_fixture
from test_collaboration_progress import Clock


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manifest, self.originals = make_fixture(self.root, 2)
        self.path, self.output = self.root/'four-chapters.json', self.root/'out'
        self.clock = Clock()
        self.pins = {}
        for index, (actor, binding) in enumerate(self.originals['bindings'].items()):
            pin = {'pid': 1000+index, 'birth': [1700000000, index], 'executable': '/synthetic/agent'}
            binding['process'] = pin
            self.pins[pin['pid']] = pin
        self.refresh()

    def refresh(self):
        write_fixture(self.root, self.manifest, self.originals)

    def process(self, pid, *, timeout):
        self.assertGreater(timeout, 0)
        return dict(self.pins.get(pid, {'pid': pid, 'birth': [1700000000, 999],
                                       'executable': '/synthetic/observer'}))

    def herdr(self, binding, *, timeout):
        self.assertGreater(timeout, 0)
        return {'identity_matches': True, 'state': 'working', 'input_sent': False}

    def run_adapter(self, **kwargs):
        return adapter.run(self.path, self.output, local_host='synthetic-host',
            mode=kwargs.pop('mode', 'snapshot'), duration=kwargs.pop('duration', None),
            process_reader=kwargs.pop('process_reader', self.process),
            herdr_reader=kwargs.pop('herdr_reader', self.herdr), wall=self.clock.now,
            monotonic=self.clock.now, waiter=self.clock.wait, **kwargs)

    def test_projection_keeps_all_actors_business_tasks_controllers_and_shared_budget(self):
        before = copy.deepcopy(self.manifest)
        derived, mapping = adapter.prepare(self.path, 'synthetic-host')
        self.assertEqual(len(derived['tasks']), 5)
        self.assertEqual(mapping['actor_count'], 5)
        self.assertTrue(mapping['coverage_sufficient'])
        self.assertTrue(derived['new_tasks_only'])
        self.assertIn('not_business_registration', mapping['source'])
        self.assertEqual(self.manifest, before)
        self.assertEqual(mapping['actors'][0]['business_tasks'][0]['controller'],
                         self.manifest['tasks'][0]['controller'])

    def test_missing_process_retains_actor_as_unknown(self):
        del self.originals['bindings']['chapter-1-executor']['process']
        self.refresh()
        view = self.run_adapter()
        self.assertEqual(view['actor_count'], 5)
        row = next(r for r in view['actors'] if r['actor_id'] == 'chapter-1-executor')
        self.assertEqual((row['coverage'], row['runtime_state']), ('UNKNOWN', 'UNKNOWN'))
        self.assertEqual(row['reason'], 'ORIGINAL_PROCESS_PIN_MISSING')
        self.assertEqual(view['unknown_actor_count'], 1)

    def test_all_missing_process_does_not_start_runtime_and_cli_reports_partial(self):
        for binding in self.originals['bindings'].values():
            del binding['process']
        self.refresh()
        with patch('collaboration_progress.run', side_effect=AssertionError('no observer input')):
            view = self.run_adapter(mode='watch', duration=120)
        self.assertEqual(view['unknown_actor_count'], 5)
        self.assertEqual(view['observer_status'], 'NOT_STARTED')
        self.assertFalse((self.output/'progress').exists())
        with patch('sys.stdout', io.StringIO()):
            self.assertEqual(adapter.main(['prepare', '--manifest', str(self.path),
                                          '--local-host', 'synthetic-host']), 2)

    def test_invalid_process_and_remote_host_are_unknown_without_fallback(self):
        self.originals['bindings']['chapter-1-executor']['process']['birth'] = [1, -1]
        self.originals['bindings']['senior']['endpoint']['host_identity'] = 'another-host'
        self.refresh()
        _, mapping = adapter.prepare(self.path, 'synthetic-host')
        rows = {r['actor_id']: r for r in mapping['actors']}
        self.assertEqual(rows['senior']['reason'], 'REMOTE_HOST_NOT_OBSERVED')
        self.assertEqual(rows['chapter-1-executor']['error_code'], 'INVALID_BIRTH')
        self.assertIsNone(rows['senior']['observation_task_id'])

    def test_herdr_timeout_exact_reason_is_preserved_as_unknown_no_stop(self):
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(['herdr', '--session', 'chapter-1'], 10)
        view = self.run_adapter(herdr_reader=timeout)
        row = view['actors'][1]
        self.assertEqual(row['runtime_state'], 'UNKNOWN')
        self.assertEqual(row['native_read_evidence']['error_type'], 'TimeoutExpired')
        self.assertIn('chapter-1', row['native_read_evidence']['error_code'])
        events = (self.output/'progress'/'events.jsonl').read_text()
        self.assertNotIn('"type": "STOP"', events)

    def test_identity_change_is_unknown_and_never_task_acceptance(self):
        def changed(*args, **kwargs):
            raise profile.ProfileError('NATIVE_SESSION_CHANGED')
        view = self.run_adapter(herdr_reader=changed)
        row = view['actors'][1]
        self.assertEqual(row['native_read_evidence']['error_code'], 'NATIVE_SESSION_CHANGED')
        self.assertEqual(row['coverage'], 'UNKNOWN')
        self.assertFalse(row['native_identity_verified'])
        self.assertFalse(view['task_completed'])

    def test_cross_service_uses_original_identity_helper_with_each_selector(self):
        calls = []
        def check(target, *, session, runner):
            calls.append((session, target['endpoint']['socket'], target['pane_id'],
                          target['native_session_id']))
            return {'identity_matches': True, 'state': 'blocked', 'input_sent': False}
        with patch('collaboration_profile.check_herdr', side_effect=check):
            view = self.run_adapter(herdr_reader=progress.native_herdr)
        self.assertEqual({c[0] for c in calls}, {'chapter-1', 'chapter-2'})
        self.assertEqual(len({c[3] for c in calls}), 4)
        self.assertEqual({c[2] for c in calls}, {'w1:p2', 'w1:p3'})
        self.assertEqual(view['actors'][1]['runtime_state'], 'blocked')
        self.assertFalse(view['accepted'])

    def test_identity_match_does_not_make_unknown_runtime_observed(self):
        for index, state in enumerate(('UNKNOWN', 'unknown')):
            with self.subTest(state=state):
                self.output = self.root / ('out-runtime-' + str(index))
                view = self.run_adapter(herdr_reader=lambda *a, **k: {
                    'identity_matches': True, 'state': state})
                self.assertEqual(view['unknown_actor_count'], 4)
                self.assertEqual(view['actors'][0]['coverage'], 'OBSERVED')
                for row in view['actors'][1:]:
                    self.assertTrue(row['native_identity_verified'])
                    self.assertEqual(row['runtime_state'], 'UNKNOWN')
                    self.assertEqual(row['coverage'], 'UNKNOWN')
                    self.assertEqual(row['reason'], 'HERDR_RUNTIME_UNKNOWN')
                    self.assertFalse(row['accepted'])

    def test_one_unknown_runtime_keeps_partial_coverage_and_cli_exit_two(self):
        unknown_id = self.originals['bindings']['chapter-1-supervisor']['native_session_id']
        def mixed(binding, *, timeout):
            return {'identity_matches': True, 'state': 'UNKNOWN' if
                    binding['target']['native_session_id'] == unknown_id else 'working'}
        view = self.run_adapter(herdr_reader=mixed)
        self.assertEqual(view['unknown_actor_count'], 1)
        self.assertEqual(sum(r['coverage'] == 'OBSERVED' for r in view['actors']), 4)
        stream = io.StringIO()
        with patch.object(adapter, 'run', return_value=view), patch('sys.stdout', stream):
            code = adapter.main(['snapshot', '--manifest', str(self.path),
                                 '--local-host', 'synthetic-host', '--output', str(self.output)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stream.getvalue())['unknown_actor_count'], 1)

    def test_stale_checkpoint_remains_unverified_and_is_not_progress(self):
        cp = self.root/'checkpoint.json'
        cp.write_text(json.dumps({'updated_at': progress.utc(self.clock.now()-600),
                                  'status': 'completed', 'current_work': 'claimed done'}))
        os.utime(cp, (self.clock.now()-600, self.clock.now()-600))
        self.originals['bindings']['chapter-1-executor']['checkpoint'] = {'path': str(cp)}
        self.refresh()
        view = self.run_adapter()
        row = next(r for r in view['actors'] if r['actor_id'] == 'chapter-1-executor')
        checkpoint = row['original_observation']['checkpoint']
        self.assertTrue(checkpoint['stale'])
        self.assertFalse(checkpoint['self_report_verified'])
        self.assertFalse(row['accepted'])

    def test_stale_native_sample_projects_unknown_but_preserves_original(self):
        self.run_adapter()
        mapping = json.loads((self.output/'actor-map.json').read_text())
        snapshot = json.loads((self.output/'progress'/'snapshot.json').read_text())
        view = adapter.project(mapping, snapshot, {'coverage': 'UNKNOWN'}, {},
                               now=self.clock.now()+181)
        self.assertEqual(view['unknown_actor_count'], 5)
        self.assertEqual(view['actors'][1]['reason'], 'SNAPSHOT_STALE')
        self.assertEqual(view['actors'][1]['original_observation']['herdr']['state'], 'working')

    def test_watch_reuses_original_loop_and_deduplicates_unchanged_absence(self):
        def absent(pid, *, timeout):
            if pid in self.pins:
                raise progress.ObservedAbsent()
            return self.process(pid, timeout=timeout)
        view = self.run_adapter(mode='watch', duration=180, process_reader=absent)
        events = [json.loads(x) for x in (self.output/'progress'/'events.jsonl').read_text().splitlines()]
        self.assertEqual(len([x for x in events if x['type'] == 'STOP']), 5)
        self.assertEqual(self.clock.waits, [60, 60, 60])
        self.assertFalse(view['task_completed'])
        self.assertEqual(view['active_monitoring_coverage'], 'UNKNOWN')

    def test_working_to_blocked_uses_upstream_event_and_unchanged_state_is_deduplicated(self):
        calls = 0
        def state(*args, **kwargs):
            nonlocal calls
            calls += 1
            return {'identity_matches': True, 'state': 'working' if calls <= 4 else 'blocked'}
        self.run_adapter(mode='watch', duration=180, herdr_reader=state)
        snapshot = json.loads((self.output/'progress'/'snapshot.json').read_text())
        self.assertEqual(snapshot['tasks'][1]['herdr']['state'], 'blocked')
        kinds = [json.loads(x)['type'] for x in (self.output/'progress'/'events.jsonl').read_text().splitlines()]
        self.assertEqual(kinds.count('HERDR_BASELINE'), 4)
        self.assertEqual(kinds.count('HERDR_STATE_CHANGED'), 4)
        self.assertEqual(kinds.count('OBSERVER_STARTED'), 1)
        self.assertEqual(kinds.count('OBSERVER_STOPPED'), 1)

    def test_cmux_process_live_does_not_claim_native_session_identity(self):
        row = self.run_adapter()['actors'][0]
        self.assertEqual(row['runtime_state'], 'PROCESS_LIVE')
        self.assertFalse(row['native_identity_verified'])
        self.assertEqual(row['reason'], 'PROCESS_ONLY_NATIVE_SESSION_NOT_RECHECKED')

    def test_duration_output_reuse_and_input_overlap_are_rejected(self):
        with self.assertRaisesRegex(adapter.AdapterError, 'BOUNDED_DURATION_REQUIRED'):
            self.run_adapter(mode='watch', duration=None)
        self.run_adapter()
        with self.assertRaisesRegex(adapter.AdapterError, 'OUTPUT_ALREADY_USED'):
            self.run_adapter()
        with self.assertRaisesRegex(adapter.AdapterError, 'INPUT_INSIDE_ADAPTER_OUTPUT'):
            adapter.run(self.path, self.root, local_host='synthetic-host', mode='snapshot')

    def test_registration_and_source_bytes_are_never_mutated(self):
        before = {str(p): p.read_bytes() for p in (self.path, self.root/'synthetic-originals.json')}
        self.run_adapter()
        self.assertEqual(before, {name: Path(name).read_bytes() for name in before})


if __name__ == '__main__':
    unittest.main()
