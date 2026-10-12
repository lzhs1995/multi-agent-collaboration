"""Durable inquiries continue without a foreground turn or any transport input."""
from contextlib import ExitStack, contextmanager
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import executor_ready as ready
import executor_reask as reask
import executor_reask_scheduler as scheduler

CALLER = '11111111-1111-4111-8111-111111111111'
SUP = '22222222-2222-4222-8222-222222222222'
WORKSPACE = '33333333-3333-4333-8333-333333333333'
PANE = '44444444-4444-4444-8444-444444444444'


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.home = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.stack.enter_context(patch.object(Path, 'home', return_value=self.home))
        self.current = dict(source=str(Path(reask.__file__).resolve().parents[1]),
                            python=str(Path(scheduler.sys.executable).resolve()))
        self.approved = self.stack.enter_context(patch.object(
            scheduler, 'approved_release', return_value=self.current))
        self.transport = self.stack.enter_context(patch.object(
            ready, '_bridge', side_effect=AssertionError('background transport forbidden')))

    def begin(self, caller=CALLER, **kwargs):
        return reask.start(caller, SUP, 'original-task', workspace=WORKSPACE, now=1000., **kwargs)

    def live(self, caller=CALLER):
        return dict(caller_surface_uuid=caller, target_surface_uuid=SUP,
                    workspace_uuid=WORKSPACE, target_pane_uuid=PANE)

    def enroll(self, caller=CALLER):
        state = self.begin(caller)
        scheduler.enroll(state, self.live(caller), now=1000.)
        return state

    def test_schedule_continues_after_finite_foreground_run_and_respects_due_time(self):
        self.enroll()
        clock = [1001.]
        def sleep(seconds):
            clock[0] += seconds
        send = lambda *_: dict(outcome='SKIPPED_UNVERIFIED', terminal=False)
        state = reask.run(CALLER, 5, send=send, clock=lambda: clock[0], sleep=sleep)
        self.assertEqual((state['state'], state['ask_count']), (reask.WAITING, 1))
        self.assertEqual(scheduler.sweep(now=1060.)['file_posts'], 0)
        self.assertEqual(scheduler.sweep(now=1061.)['file_posts'], 1)
        self.assertEqual(scheduler.sweep(now=2000.)['file_posts'], 1)
        state = reask.load(CALLER)
        self.assertEqual((state['ask_count'], state['next_due_epoch']), (3, 2060.))
        self.assertEqual(state['asks'][-1]['outcome'], 'FILE_ONLY_SCHEDULED')
        self.transport.assert_not_called()

    def test_exact_reply_finishes_registration_wrong_task_does_not(self):
        self.enroll()
        scheduler.sweep(now=1001.)
        state = reask.load(CALLER)
        current = ready.read_json(reask.inbox_path(state))
        reply = dict(current['reply_template'], trigger='Existing review finished.')
        path = Path(current['reply_mailbox'])
        ready.write_json(path, dict(reply, task_id='foreign'))
        self.assertEqual(scheduler.sweep(now=1002.)['episodes'][0]['status'], reask.WAITING)
        ready.write_json(path, reply)
        result = scheduler.sweep(now=1003.)
        self.assertEqual((result['file_posts'], result['episodes'][0]['status']), (0, reask.ANSWERED))
        self.assertEqual(ready.read_json(scheduler.enrollment_path(CALLER))['status'], 'FINISHED')
        self.assertEqual(scheduler.sweep(now=2000.)['file_posts'], 0)

    def test_operator_stop_survives_schedule_and_cannot_reopen(self):
        self.enroll()
        ready.write_json(reask.stop_file(CALLER), dict(reason='USER_STOP'))
        self.assertEqual(scheduler.sweep(now=1001.)['episodes'][0]['status'], reask.STOPPED)
        self.assertEqual(self.begin()['state'], reask.STOPPED)
        self.assertEqual(scheduler.sweep(now=2000.)['file_posts'], 0)

    def test_unenrolled_busy_or_mismatched_episode_never_posts(self):
        self.begin()
        self.assertEqual(scheduler.sweep(now=1001.)['episodes'], [])
        self.enroll()
        with reask.episode_lock(CALLER):
            result = scheduler.sweep(now=1001.)
        self.assertEqual(result['episodes'][0]['status'], 'EPISODE_BUSY')
        path = scheduler.enrollment_path(CALLER)
        record = ready.read_json(path)
        for key in ('binding', 'controller', 'scheduler', 'live_binding'):
            changed = copy.deepcopy(record)
            changed[key] = {}
            ready.write_json(path, changed)
            result = scheduler.sweep(now=1001.)
            self.assertEqual(result['file_posts'], 0)
            self.assertEqual(reask.load(CALLER)['ask_count'], 0)
        self.transport.assert_not_called()

    def test_file_only_schedule_preserves_uncertain_original_and_skips_transcript_and_binding(self):
        state = self.enroll()
        pending = dict(marker='UNKNOWN_original', outcome='INTENT_UNRESOLVED',
                       origin=dict(controller='old-controller'), terminal=True)
        state['terminal_pending'] = copy.deepcopy(pending)
        state['transcript'] = '/must-not-read'
        state['tail_position'] = dict(offset=0)
        reask.save(state)
        with patch.object(ready, 'Tail', side_effect=AssertionError('no transcript')), \
                patch.object(reask, 'idle_binding', side_effect=AssertionError('no idle binding')):
            result = scheduler.sweep(now=1001.)
        self.assertEqual(result['file_posts'], 1)
        self.assertEqual(reask.load(CALLER)['terminal_pending'], pending)
        self.transport.assert_not_called()

    def test_enrollment_requires_matching_authenticated_pair_and_canonical_channels(self):
        state = self.begin()
        for changed in (dict(target_surface_uuid=CALLER), dict(caller_surface_uuid=SUP),
                        dict(workspace_uuid=SUP)):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                scheduler.enroll(state, dict(self.live(), **changed))
        for field, value in [('channels', ['/tmp/../tmp']), ('channels', ['relative']),
                             ('channels', ['/tmp'] * (scheduler.MAX_CHANNELS + 1)),
                             ('supervisor', 'surface:28'), ('mailbox', '/unrelated')]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                scheduler.binding(dict(state, **{field: value}))
        self.assertFalse(scheduler.enrollment_path(CALLER).exists())

    def test_existing_controller_registration_cannot_be_replaced_for_same_episode(self):
        state = self.enroll()
        path = scheduler.enrollment_path(CALLER)
        old = ready.read_json(path)
        old['controller']['source'] = '/old-release'
        ready.write_json(path, old)
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            scheduler.enroll(state, self.live())
        self.assertEqual(path.read_bytes(), before)

    def test_cli_persists_authenticated_uuid_instead_of_surface_alias(self):
        self.transport.side_effect = None
        self.transport.return_value.pin_workspace.return_value = self.live()
        with patch.object(reask, 'tick', side_effect=lambda caller: reask.load(caller)), \
                patch.object(reask.sys, 'stdout', io.StringIO()):
            self.assertEqual(reask.main(['start', '--supervisor', 'surface:28',
                                         '--caller-uuid', CALLER, '--task', 'next']), 0)
        state = reask.load(CALLER)
        self.assertEqual((state['supervisor'], state['workspace_uuid']), (SUP, WORKSPACE))
        self.assertEqual(ready.read_json(scheduler.enrollment_path(CALLER))['status'], 'ENROLLED')

    def test_capacity_enumeration_is_bounded(self):
        visited = []
        @contextmanager
        def huge(_directory):
            def entries():
                for index in range(100000):
                    visited.append(index)
                    yield object()
            yield entries()
        with patch.object(scheduler.os, 'scandir', side_effect=huge), self.assertRaises(ValueError):
            scheduler.check_capacity(self.home)
        self.assertEqual(len(visited), scheduler.MAX_ENROLLMENTS)

    def test_deadline_rotation_makes_progress_past_slow_first_caller(self):
        for _ in range(3):
            self.enroll(str(uuid.uuid4()).upper())
        visited = []
        def deadline(caller, *_args, **_kwargs):
            visited.append(caller)
            raise ready.WaitBudgetExpired()
        with patch.object(reask, 'tick_locked', side_effect=deadline):
            for _ in range(3):
                self.assertEqual(scheduler.sweep(now=1001.)['status'], 'SWEEP_BUDGET_EXHAUSTED')
        self.assertEqual(len(set(visited)), 3)
        self.assertTrue(ready.read_json(scheduler.root() / 'STATUS.json')['last_enrollment'])

    def test_release_failure_is_observed_without_any_episode_work(self):
        self.enroll()
        self.approved.side_effect = ValueError('manifest changed')
        result = scheduler.sweep(now=1001.)
        self.assertEqual(result['status'], 'SCHEDULER_FAILED')
        self.assertEqual(reask.load(CALLER)['ask_count'], 0)
        self.transport.assert_not_called()

    def test_separate_launchd_job_has_no_transport_or_guardian_invocation(self):
        spec = scheduler.launchd_spec(self.home, self.current['source'], '/python', '/CURRENT.json')
        self.assertEqual(spec['Label'], scheduler.LABEL)
        self.assertEqual(spec['StartInterval'], 60)
        self.assertEqual(spec['ProgramArguments'][-3:], ['sweep', '--manifest', '/CURRENT.json'])
        self.assertNotIn('configuration_guardian.py', str(spec))
        self.assertNotIn('run', spec['ProgramArguments'])

    def test_atomic_state_failure_leaves_no_temporary_files(self):
        path = self.home / 'state.json'
        ready.write_json(path, {'original': True})
        with patch.object(ready.os, 'fsync', side_effect=ready.WaitBudgetExpired), \
                self.assertRaises(ready.WaitBudgetExpired):
            ready.write_json(path, {'replacement': True})
        self.assertEqual(ready.read_json(path), {'original': True})
        self.assertEqual(list(self.home.glob('.*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
