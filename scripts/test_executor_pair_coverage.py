"""Exact two-executor scopes with real files and simulated native identity."""
from contextlib import ExitStack
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import Mock, patch

import executor_pair_coverage as coverage
import executor_reask as reask
import executor_reask_scheduler as scheduler

SUP = '11111111-1111-4111-8111-111111111111'
A = '22222222-2222-4222-8222-222222222222'
B = '33333333-3333-4333-8333-333333333333'
WS = '44444444-4444-4444-8444-444444444444'
PANE = '55555555-5555-4555-8555-555555555555'
SSESSION = '66666666-6666-4666-8666-666666666666'
ASESSION = '77777777-7777-4777-8777-777777777777'
BSESSION = '88888888-8888-4888-8888-888888888888'


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.home = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.stack.enter_context(patch.object(Path, 'home', return_value=self.home))
        self.scope = dict(schema=coverage.SCHEMA, supervisor_surface_uuid=SUP,
                          supervisor_session_id=SSESSION, workspace_uuid=WS,
                          executors=[dict(surface_uuid=c, session_id=s, task_id=t, overall_task='overall')
                                     for c, s, t in [(A, ASESSION, 'A-task'), (B, BSESSION, '')]])
        self.path = self.home / 'scope.json'
        self.write_scope()
        resolved = dict(caller=dict(surface_id=SUP, workspace_id=WS, surface_type='terminal'))
        proof = dict(session=SSESSION, foreground=dict(thread_id=SSESSION))
        self.stack.enter_context(patch.object(coverage.workspace, 'caller_snapshot',
                                              return_value=(resolved, {}, {}, proof)))
        self.bridge = Mock()
        self.bridge.pin_workspace.side_effect = lambda c, **kw: dict(workspace_uuid=WS,
            caller_surface_uuid=kw['caller_uuid'], target_surface_uuid=c, target_pane_uuid=PANE)
        self.stack.enter_context(patch.object(coverage.ready, '_bridge', return_value=self.bridge))
        self.bind = self.stack.enter_context(patch.object(coverage.native, 'bind_target',
                                                         side_effect=lambda _b, c, _t: self.target(c)))
        self.match = self.stack.enter_context(patch.object(scheduler, 'matches', return_value=True))
        for name in ('tick', 'save', 'write_channels'):
            self.stack.enter_context(patch.object(reask, name, side_effect=AssertionError('read-only coverage')))
        self.stack.enter_context(patch.object(scheduler, 'start', side_effect=AssertionError('no registration')))
        self.stack.enter_context(patch.object(coverage.native, 'receipt_received_at', side_effect=AssertionError('no receipt')))
        self.create(A)
        self.create(B)

    def write_scope(self):
        self.path.write_text(json.dumps(self.scope))
        self.sha = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def target(self, caller):
        peer = next(p for p in self.scope['executors'] if p['surface_uuid'] == caller)
        return dict(provider='claude', session_id=peer['session_id'], tty='ttys001',
                    process=dict(pid=123 if caller == A else 456, birth=[1, 2], executable='/original/claude'))

    def create(self, caller):
        peer = next(p for p in self.scope['executors'] if p['surface_uuid'] == caller)
        auth = self.home / (caller + '-auth.txt')
        auth.write_text('Explicit original authority')
        raw = auth.read_bytes()
        state = dict(caller_surface_uuid=caller, supervisor=SUP, workspace_uuid=WS,
                     task_id=peer['task_id'], state=reask.WAITING, episode_id=caller[:8],
                     executor_identity=dict(session_id=peer['session_id'],
                         process=self.target(caller)['process'], tty='ttys001', pane_uuid=PANE),
                     continuation=dict(enabled=True, status='ACTIVE', overall_task='overall', owner=SUP,
                         until_epoch=2000., authorization=dict(path=str(auth), bytes=len(raw),
                         sha256=hashlib.sha256(raw).hexdigest())), controller={})
        self.write_state(caller, state)
        path = scheduler.enrollment_path(caller)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(schema=scheduler.SCHEMA, status='ENROLLED')))

    def write_state(self, caller, state):
        path = reask.state_path(caller)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state))

    def state(self, caller):
        return json.loads(reask.state_path(caller).read_bytes())

    def run_coverage(self):
        return coverage.coverage(self.path, self.sha, SUP, now=1100.)

    def test_two_distinct_task_scopes_including_empty_task_are_exactly_covered(self):
        before = {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        result = self.run_coverage()
        self.assertEqual(result['outcome'], 'EXACT_PAIR_COVERED', result)
        self.assertEqual(result['covered_count'], 2)
        self.assertEqual(result['scope']['role'], 'IMMUTABLE_SCOPE_INPUT')
        self.assertTrue(all(p['role'] == 'LIVE_OBSERVATION' and not p['immutable_input']
                            for r in result['executors'] for p in r['observations'].values()))
        self.assertEqual(before, {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file()})
        self.bridge._run.assert_not_called()

    def test_missing_second_episode_is_not_inferred_from_first_enrollment(self):
        reask.state_path(B).unlink()
        scheduler.enrollment_path(B).unlink()
        result = self.run_coverage()
        self.assertEqual(result['covered_count'], 1)
        self.assertEqual(result['executors'][1]['status'], 'NO_EPISODE')
        self.assertFalse(result['registration_authorized'])

    def test_duplicate_or_supervisor_executor_session_refuses_before_native_reads(self):
        for session in (self.scope['executors'][0]['session_id'], SSESSION):
            with self.subTest(session=session):
                self.scope['executors'][1]['session_id'] = session
                self.write_scope()
                before = {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
                with self.assertRaisesRegex(ValueError, 'DISTINCT_ORIGINAL_EXECUTOR_SESSIONS'):
                    self.run_coverage()
                self.bind.assert_not_called()
                self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_old_stopped_and_expired_episode_remain_uncovered_and_unchanged(self):
        for status in ('STOPPED', 'AUTHORITY_EXPIRED'):
            with self.subTest(status=status):
                state = self.state(B)
                state['state'] = reask.STOPPED if status == 'STOPPED' else reask.WAITING
                state['continuation']['until_epoch'] = 1000. if status == 'AUTHORITY_EXPIRED' else 2000.
                self.write_state(B, state)
                before = reask.state_path(B).read_bytes()
                result = self.run_coverage()
                self.assertEqual(result['executors'][1]['status'], status)
                self.assertEqual(before, reask.state_path(B).read_bytes())

    def test_wrong_task_supervisor_and_session_are_not_coverage(self):
        for key, value, status in [('task_id', 'another-task', 'TASK_OR_SUPERVISOR_SCOPE_MISMATCH'),
                                   ('supervisor', A, 'TASK_OR_SUPERVISOR_SCOPE_MISMATCH')]:
            with self.subTest(key=key):
                original = self.state(B)
                changed = dict(original, **{key: value})
                self.write_state(B, changed)
                self.assertEqual(self.run_coverage()['executors'][1]['status'], status)
                self.write_state(B, original)
        original = self.target
        self.bind.side_effect = lambda _b, c, _t: dict(original(c), session_id=ASESSION)
        self.assertEqual(self.run_coverage()['executors'][1]['status'], 'ORIGINAL_SESSION_MISMATCH')

    def test_duplicate_peer_is_refused_before_native_identity_or_file_reads(self):
        self.scope['executors'][1] = copy.deepcopy(self.scope['executors'][0])
        self.write_scope()
        with self.assertRaisesRegex(ValueError, 'DISTINCT'):
            self.run_coverage()
        self.bind.assert_not_called()

    def test_reply_unconsumed_is_covered_without_claiming_reading_or_consuming(self):
        state = self.state(B)
        state['state'] = reask.ANSWERED
        self.write_state(B, state)
        row = self.run_coverage()['executors'][1]
        self.assertEqual(row['status'], 'COVERED_REPLY_UNCONSUMED')
        self.assertEqual(row['body_read'], 'NOT_EVALUATED')
        self.assertEqual(row['consume'], 'NOT_EVALUATED')
        self.assertIn('ORIGINAL_CONTROLLER', row['next_action'])

    def test_raw_observation_drift_is_not_a_stable_coverage_result(self):
        def change(_record, state, _path, **_kw):
            self.write_state(state['caller_surface_uuid'], dict(state, check_count=99))
            return True
        self.match.side_effect = change
        result = self.run_coverage()
        self.assertEqual(result['covered_count'], 0)
        self.assertTrue(all(r['status'] == 'OBSERVATION_DRIFT_RECHECK_REQUIRED' for r in result['executors']))

    def test_current_configured_files_do_not_replace_original_record_verification(self):
        self.match.return_value = False
        result = self.run_coverage()
        self.assertEqual(result['covered_count'], 0)
        self.assertTrue(all(r['status'] == 'BINDING_OR_CONTROLLER_UNVERIFIED' for r in result['executors']))

    def test_foreground_thread_change_refuses_even_same_surface(self):
        with patch.object(coverage.workspace, 'caller_snapshot', return_value=(
                dict(caller=dict(surface_id=SUP, workspace_id=WS, surface_type='terminal')),
                {}, {}, dict(session=SSESSION, foreground=dict(thread_id=ASESSION)))):
            with self.assertRaisesRegex(ValueError, 'FOREGROUND_SUPERVISOR'):
                self.run_coverage()
        self.bind.assert_not_called()

    def test_slow_first_native_observation_does_not_skip_second_peer(self):
        original = self.bind.side_effect
        def slow_first(bridge, caller, text):
            if caller == A:
                raise subprocess.TimeoutExpired('read-only native identity', 10.)
            return original(bridge, caller, text)
        self.bind.side_effect = slow_first
        result = self.run_coverage()
        self.assertEqual(result['executors'][0]['status'], 'OBSERVATION_UNAVAILABLE')
        self.assertTrue(result['executors'][1]['covered'])
        self.assertEqual(result['covered_count'], 1)

    def test_process_change_between_native_observations_is_uncovered(self):
        count = {}
        def moving(_bridge, caller, _text):
            count[caller] = count.get(caller, 0) + 1
            target = self.target(caller)
            if count[caller] > 1:
                target['process']['birth'] = [99, 2]
            return target
        self.bind.side_effect = moving
        result = self.run_coverage()
        self.assertEqual(result['covered_count'], 0)
        self.assertTrue(all(r['status'] == 'ORIGINAL_EXECUTOR_IDENTITY_CHANGED'
                            for r in result['executors']))

    def test_authority_change_while_checking_is_not_coverage(self):
        def change(_record, state, _path, **_kwargs):
            Path(state['continuation']['authorization']['path']).write_text('Changed authority')
            return True
        self.match.side_effect = change
        result = self.run_coverage()
        self.assertEqual(result['covered_count'], 0)
        self.assertTrue(all(r['status'] == 'AUTHORITY_CHANGED' for r in result['executors']))

    def test_realtime_authority_expiry_during_observation_is_not_coverage(self):
        with patch.object(coverage.time, 'time', side_effect=[1100., 1100., 2001., 2001.]):
            result = coverage.coverage(self.path, self.sha, SUP)
        self.assertEqual(result['covered_count'], 0)
        self.assertTrue(all(r['status'] == 'AUTHORITY_EXPIRED' for r in result['executors']))

    def test_changed_scope_sha_and_duplicate_keys_refuse_before_native_reads(self):
        with self.assertRaisesRegex(ValueError, 'EXACT_SHA'):
            coverage.coverage(self.path, '0' * 64, SUP, now=1100.)
        self.path.write_text('{"schema":"x","schema":"y"}')
        self.sha = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'DUPLICATE_JSON'):
            self.run_coverage()
        self.bind.assert_not_called()

    def test_cli_returns_one_json_document_and_rc2_for_incomplete_pair(self):
        reask.state_path(B).unlink()
        with patch('sys.stdout', new_callable=io.StringIO) as output, \
             patch.object(coverage.time, 'time', return_value=1100.):
            rc = reask.main(['coverage', '--caller-uuid', SUP, '--scope-file', str(self.path),
                             '--scope-sha256', self.sha])
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(output.getvalue())['outcome'], 'EXACT_PAIR_PARTIAL')


if __name__ == '__main__':
    unittest.main()
