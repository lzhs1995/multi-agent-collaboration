"""Stage-2 regressions: persisted file-only preference, zero-input observe-reply,
lock-acquisition-only busy, honest post-replace sync, visible channel errors,
and the proactive hook's STANDBY / formal-work precedence. Real episode files;
only transport and identity are substituted, each with a paired negative case.
"""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import executor_proactive_hook as hook
import executor_ready as ready
import executor_reask as reask
import executor_reask_lifecycle as lifecycle
import executor_reask_scheduler as scheduler
import test_executor_reask_scheduler as fixtures
from test_executor_reask_scheduler import CALLER, SUP, WORKSPACE


def tree():
    """Every byte under the isolated state root, keyed by relative path."""
    base = ready.state_root()
    return {str(p.relative_to(base)): p.read_bytes()
            for p in sorted(base.rglob('*')) if p.is_file() and p.suffix != '.lock'}


class Base(unittest.TestCase):
    def setUp(self):
        fixtures.SchedulerTests.setUp(self)
        self.consumer_auth = self.stack.enter_context(patch(
            'executor_reask_identity.authenticate', side_effect=fixtures.consumer_fixture))

    live = fixtures.SchedulerTests.live

    def begin(self, caller=CALLER, **kwargs):
        auth = self.home / 'explicit-user-authorization.txt'
        if not auth.exists():
            auth.write_text('Continue requests every 60 seconds until the overall task finishes.')
        value = dict(enabled=True, owner=SUP, overall_task='overall-work', authorization_file=str(auth))
        return reask.start(caller, SUP, 'original-task', workspace=WORKSPACE, now=1000.,
                           continuing=True, continuation=value, **kwargs)

    def enroll(self, caller=CALLER, **kwargs):
        state = self.begin(caller, **kwargs)
        scheduler.enroll(state, self.live(caller), now=1000.)
        return state

    def issue(self, now=1001.):
        scheduler.sweep(now=now)
        return ready.read_json(reask.inbox_path(reask.load(CALLER)))

    def write_reply(self, current, **changes):
        body = dict(current['reply_template'], trigger='Await the next scoped result.',
                    next_due_epoch=1061., deadline_epoch=1121., **changes)
        ready.write_json(Path(current['reply_mailbox']), body)
        return body

    def answered(self):
        self.write_reply(self.issue())
        scheduler.sweep(now=1002.)
        state = reask.load(CALLER)
        self.assertEqual(state['state'], reask.ANSWERED)
        return state


class FileOnlyPreferenceTests(Base):
    def test_new_file_only_episode_persists_and_foreground_tick_and_run_send_nothing(self):
        state = self.begin(file_only=True)
        self.assertIs(state['file_only_preference'], True)
        sender = Mock(side_effect=AssertionError('file-only episode sent input'))
        state = reask.tick(CALLER, 1001., sender)          # caller did not pass file_only
        self.assertEqual(state['asks'][-1]['outcome'], 'FILE_ONLY_SCHEDULED')
        self.assertEqual(state['asks'][-1]['file_only_source'], 'EPISODE_PREFERENCE')
        self.assertIsNone(state['terminal_pending'])
        self.assertEqual(state['file_posts'], 1)
        clock = [1061.]
        def sleep(seconds):
            clock[0] += seconds
        result = reask.run(CALLER, 70, send=sender, clock=lambda: clock[0], sleep=sleep)
        self.assertEqual(result['state'], reask.WAITING)
        self.assertGreaterEqual(result['file_posts'], 2)
        self.assertTrue(all(a['outcome'] == 'FILE_ONLY_SCHEDULED' for a in result['asks']))
        sender.assert_not_called()
        self.transport.assert_not_called()

    def test_negative_control_default_new_episode_still_uses_its_sender(self):
        state = self.begin()
        self.assertIs(state['file_only_preference'], False)
        sender = Mock(return_value=dict(outcome='SKIPPED_UNVERIFIED', terminal=False))
        state = reask.tick(CALLER, 1001., sender)
        sender.assert_called_once()
        self.assertNotIn('file_only_source', state['asks'][-1])

    def test_old_episode_without_preference_is_not_migrated(self):
        state = self.begin()
        del state['file_only_preference']
        reask.save(state)
        before = reask.state_path(CALLER).read_bytes()
        # Reusing the waiting historical episode with --file-only neither adds
        # the key nor raises; the flag only narrows that one call.
        again = self.begin(file_only=True)
        self.assertEqual(again['episode_id'], state['episode_id'])
        self.assertNotIn('file_only_preference', again)
        self.assertEqual(reask.state_path(CALLER).read_bytes(), before)
        self.assertEqual(reask.summary(again)['file_only_preference'], 'NOT_RECORDED')
        sender = Mock(return_value=dict(outcome='SKIPPED_UNVERIFIED', terminal=False))
        state = reask.tick(CALLER, 1001., sender)
        sender.assert_called_once()
        self.assertNotIn('file_only_preference', reask.load(CALLER))

    def test_conflicting_preference_on_reused_episode_must_close_first(self):
        state = self.begin(file_only=True)
        with self.assertRaises(ValueError):
            self.begin(file_only=False)
        self.assertEqual(self.begin(file_only=True)['episode_id'], state['episode_id'])
        with self.assertRaises(ValueError):
            reask.start(CALLER, SUP, 'other-task', workspace=WORKSPACE, now=1000., file_only='yes')

    def test_hook_instruction_names_file_only_limit_only_for_that_preference(self):
        state = self.begin(file_only=True)
        self.assertIn('cannot wake an idle supervisor', hook.instruction(state))
        state['file_only_preference'] = False
        self.assertNotIn('cannot wake an idle supervisor', hook.instruction(state))


class ObserveReplyTests(Base):
    def test_no_reply_observation_is_byte_identical_and_zero_input(self):
        self.enroll()
        self.issue()
        before = tree()
        result = lifecycle.observe_reply(CALLER, now=1002.)['observe_result']
        self.assertEqual(result['status'], 'NO_REPLY')
        self.assertEqual((result['input_operations'], result['asks_issued'], result['consumed']), (0, 0, False))
        self.assertEqual(tree(), before)
        self.transport.assert_not_called()

    def test_negative_control_a_tick_does_change_counters_and_files(self):
        self.enroll()
        self.issue()
        before = tree()
        scheduler.sweep(now=1061.)
        self.assertNotEqual(tree(), before)
        self.assertEqual(reask.load(CALLER)['ask_count'], 2)

    def test_waiting_mailbox_reply_is_observed_with_exact_sha_without_promotion(self):
        self.enroll()
        self.write_reply(self.issue())
        before = tree()
        result = lifecycle.observe_reply(CALLER, now=1002.)['observe_result']
        self.assertEqual(result['status'], 'REPLY_OBSERVED')
        self.assertEqual(tree(), before)
        self.assertEqual(reask.load(CALLER)['state'], reask.WAITING)
        scheduler.sweep(now=1002.)
        self.assertEqual(reask.load(CALLER)['reply']['body_sha256'], result['body_sha256'])

    def test_answered_unconsumed_reports_exact_sha_and_consume_still_required(self):
        self.enroll()
        state = self.answered()
        before = tree()
        result = lifecycle.observe_reply(CALLER, now=1003.)['observe_result']
        self.assertEqual((result['status'], result['source_check']), ('REPLY_ANSWERED_UNCONSUMED', 'UNCHANGED'))
        self.assertEqual(result['body_sha256'], state['reply']['body_sha256'])
        self.assertTrue(result['stored_body_consistent'])
        self.assertFalse(result['consumed'])
        self.assertEqual(tree(), before)
        self.assertEqual(reask.load(CALLER)['state'], reask.ANSWERED)
        consumed = lifecycle.consume(CALLER, result['body_sha256'], now=1004.)
        self.assertTrue(consumed['consume_result']['consumed'])
        prior = lifecycle.observe_reply(CALLER, now=1005.)['observe_result']['prior_consumption']
        self.assertEqual(prior['count'], 1)
        self.assertTrue(prior['last']['receipt_verified'])
        self.assertEqual(prior['last']['body_sha256'], result['body_sha256'])

    def test_changed_source_preserves_answered_body_and_keeps_stored_reply(self):
        self.enroll()
        state = self.answered()
        source = Path(state['reply']['path'])
        ready.write_json(source, dict(ready.read_json(source), trigger='rewritten later'))
        state_bytes = reask.state_path(CALLER).read_bytes()
        result = lifecycle.observe_reply(CALLER, now=1003.)['observe_result']
        self.assertEqual((result['status'], result['source_check']), ('REPLY_CHANGED', 'CHANGED'))
        self.assertNotEqual(result['current_source_body_sha256'], state['reply']['body_sha256'])
        preserved = json.loads(Path(result['preserved_answered']['path']).read_bytes())
        self.assertEqual(preserved['body'], state['reply']['body'])
        self.assertEqual(preserved['body_sha256'], state['reply']['body_sha256'])
        self.assertEqual(reask.state_path(CALLER).read_bytes(), state_bytes)
        again = lifecycle.observe_reply(CALLER, now=1004.)['observe_result']
        self.assertEqual(again['preserved_answered'], result['preserved_answered'])
        source.unlink()
        self.assertEqual(lifecycle.observe_reply(CALLER, now=1005.)['observe_result']['source_check'],
                         'SOURCE_UNREADABLE')
        consumed = lifecycle.consume(CALLER, state['reply']['body_sha256'], now=1006.)
        self.assertTrue(consumed['consume_result']['consumed'])

    def test_stop_and_revocation_are_reported_first_without_any_write(self):
        self.enroll()
        state = self.answered()
        ready.write_json(reask.stop_file(CALLER), dict(reason='USER_STOP'))
        before = tree()
        result = lifecycle.observe_reply(CALLER, now=1003.)['observe_result']
        self.assertEqual((result['status'], result['reason']), ('STOPPED', 'OPERATOR_STOP'))
        self.assertNotIn('body_sha256', result)
        self.assertEqual(tree(), before)
        reask.stop_file(CALLER).unlink()
        Path(state['continuation']['authorization']['path']).write_text('revoked')
        before = tree()
        result = lifecycle.observe_reply(CALLER, now=1003.)['observe_result']
        self.assertEqual((result['status'], result['reason']), ('STOPPED', 'AUTHORIZATION_CHANGED'))
        self.assertEqual(tree(), before)
        self.assertEqual(reask.load(CALLER)['state'], reask.ANSWERED)

    def test_busy_is_lock_acquisition_only_and_executor_identity_is_required(self):
        self.enroll()
        self.answered()
        with reask.episode_lock(CALLER):
            self.assertEqual(lifecycle.observe_reply(CALLER, now=1003.)['observe_result']['status'],
                             'EPISODE_BUSY')
        failure = BlockingIOError('identity I/O, not the episode lock')
        self.consumer_auth.side_effect = failure
        before = tree()
        with self.assertRaises(BlockingIOError) as raised:
            lifecycle.observe_reply(CALLER, now=1003.)
        self.assertIs(raised.exception, failure)
        self.consumer_auth.side_effect = ValueError('foreign executor')
        with self.assertRaises(ValueError):
            lifecycle.observe_reply(CALLER, now=1003.)
        self.assertEqual(tree(), before)

    def test_foreign_controller_and_cli_exit_codes(self):
        self.enroll()
        self.answered()
        with patch.object(reask, 'controller', return_value=dict(source='/elsewhere', python='/x', files={})):
            self.assertEqual(lifecycle.observe_reply(CALLER, now=1003.)['observe_result']['status'],
                             'ORIGINAL_CONTROLLER_REQUIRED')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(reask.main(['observe-reply', '--caller-uuid', CALLER]), 0)
        printed = json.loads(out.getvalue())
        self.assertEqual(printed['observe_result']['status'], 'REPLY_ANSWERED_UNCONSUMED')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(reask.main(['observe-reply', '--caller-uuid',
                                         '00000000-0000-4000-8000-000000000000']), 2)


class LockAndSyncTests(Base):
    def test_tick_busy_only_at_lock_acquisition_and_body_blocking_error_propagates(self):
        self.begin()
        with reask.episode_lock(CALLER):
            busy = reask.tick(CALLER, 1001., Mock())
        self.assertEqual((busy['outcome'], busy['input_operations']), ('EPISODE_BUSY', 0))
        failure = BlockingIOError('body I/O, not lock acquisition')
        with patch.object(reask, 'load', side_effect=failure):
            with self.assertRaises(BlockingIOError) as raised:
                reask.tick(CALLER, 1001., Mock())
        self.assertIs(raised.exception, failure)

    def test_task_boundary_busy_only_at_lock_and_body_error_propagates(self):
        self.begin()
        marker = dict(workspace_uuid=WORKSPACE, collaboration_id='c', task_id='t',
                      participants=[dict(role='supervisor', surface_uuid=SUP),
                                    dict(role='executor', surface_uuid=CALLER)])
        with reask.episode_lock(CALLER):
            with self.assertRaises(RuntimeError):
                lifecycle.task_boundary(marker, 'RESERVED', now=1001.)
        failure = BlockingIOError('state read, not lock acquisition')
        with patch.object(reask, 'load', side_effect=failure):
            with self.assertRaises(BlockingIOError) as raised:
                lifecycle.task_boundary(marker, 'RESERVED', now=1001.)
        self.assertIs(raised.exception, failure)

    def fail_dir_sync_once(self):
        real, calls = ready.sync_directory, []
        def sync(path):
            calls.append(path)
            if len(calls) == 1:
                raise OSError('injected directory fsync after replace')
            return real(path)
        return sync, calls

    def test_post_replace_sync_failure_is_committed_unknown_then_confirmed(self):
        state = self.begin()
        state['note'] = 'candidate'
        sync, calls = self.fail_dir_sync_once()
        with patch.object(ready, 'sync_directory', side_effect=sync), \
                patch.object(ready, 'write_json', wraps=ready.write_json) as writes:
            with self.assertRaises(reask.CommittedSyncUnknown) as raised:
                reask.save(state)
        self.assertEqual(writes.call_count, 1)              # no automatic rewrite
        current = reask.state_path(CALLER).read_bytes()
        self.assertEqual(raised.exception.candidate_sha256, hashlib.sha256(current).hexdigest())
        self.assertEqual(json.loads(current)['note'], 'candidate')
        with patch.object(ready, 'write_json', side_effect=AssertionError('confirm rewrote')):
            result = reask.confirm_sync(CALLER, raised.exception.candidate_sha256)
        self.assertEqual(result['outcome'], 'SYNC_CONFIRMED')
        self.assertEqual(reask.state_path(CALLER).read_bytes(), current)

    def test_confirm_sync_verifies_current_candidate_before_any_fsync(self):
        state = self.begin()
        state['note'] = 'candidate'
        sync, _ = self.fail_dir_sync_once()
        with patch.object(ready, 'sync_directory', side_effect=sync):
            with self.assertRaises(reask.CommittedSyncUnknown) as raised:
                reask.save(state)
        state['note'] = 'a later writer'
        reask.save(state)
        later = reask.state_path(CALLER).read_bytes()
        with patch.object(ready, 'sync_directory', side_effect=AssertionError('blind fsync')):
            result = reask.confirm_sync(CALLER, raised.exception.candidate_sha256)
        self.assertEqual(result['outcome'], 'CANDIDATE_MISMATCH')
        self.assertEqual(reask.state_path(CALLER).read_bytes(), later)
        with patch.object(ready, 'sync_directory', side_effect=OSError('still failing')):
            again = reask.confirm_sync(CALLER, hashlib.sha256(later).hexdigest())
        self.assertEqual(again['outcome'], 'COMMITTED_SYNC_UNKNOWN')

    def test_negative_control_pre_replace_failure_is_the_original_error(self):
        state = self.begin()
        before = reask.state_path(CALLER).read_bytes()
        state['note'] = 'never visible'
        with patch.object(Path, 'replace', side_effect=OSError('injected before replace')):
            with self.assertRaises(OSError) as raised:
                reask.save(state)
        self.assertNotIsInstance(raised.exception, reask.CommittedSyncUnknown)
        self.assertEqual(reask.state_path(CALLER).read_bytes(), before)
        self.assertEqual(list(reask.root().glob('.*.tmp')), [])

    def test_cli_reports_committed_sync_unknown_without_claiming_zero_input(self):
        failure = reask.CommittedSyncUnknown('/state.json', 'b' * 64, OSError('fsync'))
        out = io.StringIO()
        with patch.object(reask, '_main', side_effect=failure), contextlib.redirect_stdout(out):
            self.assertEqual(reask.main(['tick', '--caller-uuid', CALLER]), 2)
        printed = json.loads(out.getvalue())
        self.assertEqual((printed['outcome'], printed['candidate_sha256']), ('COMMITTED_SYNC_UNKNOWN', 'b' * 64))
        self.assertIsNone(printed['input_operations'])
        self.assertFalse(printed['input_operations_known'])

    def test_partial_closing_channel_failure_is_visible_and_clean_close_is_not(self):
        channel = self.home / 'channel'
        channel.mkdir()
        self.begin(channels=[str(channel)])
        reask.tick(CALLER, 1001., Mock(return_value=dict(outcome='SKIPPED_UNVERIFIED', terminal=False)))
        clean = reask.summary(reask.load(CALLER))
        self.assertFalse(clean['partial_channel_failure'])
        inbox = reask.inbox_path(reask.load(CALLER))
        real = ready.write_json
        def write(path, value):
            if Path(path) == inbox and value.get('status') == reask.STOPPED:
                raise OSError('injected inbox failure')
            return real(path, value)
        with patch.object(ready, 'write_json', side_effect=write):
            state = reask.stop(CALLER, 'USER_STOP')
        summary = reask.summary(state)
        self.assertTrue(summary['partial_channel_failure'])
        self.assertEqual([e['path'] for e in summary['channel_errors']['last_closing']], [str(inbox)])
        self.assertEqual(json.loads((channel / reask.current_name(CALLER)).read_text())['status'], reask.STOPPED)


import test_executor_proactive_hook as proactive_fixtures  # noqa: E402  (module import: no test duplication)


class ProactiveStandbyTests(unittest.TestCase):
    """Real registration, identity, mailbox and consume; only native I/O is a fixture."""
    _P = proactive_fixtures.ProactiveHookTests
    setUp, select_provider, write_registration = _P.setUp, _P.select_provider, _P.write_registration
    process, ps, tree, live, snapshot, files = _P.process, _P.ps, _P.tree, _P.live, _P.snapshot, _P.files
    reserve, begin, payload = _P.reserve, _P.begin, _P.payload

    def answer(self, *, status='WAITING_DEPENDENCY', due=2000.):
        scheduler.sweep(now=1001.)
        request = ready.read_json(reask.inbox_path(reask.load(proactive_fixtures.CALLER)))
        body = dict(request['reply_template'], status=status, trigger='Stand by for the merged review.',
                    next_due_epoch=due, deadline_epoch=due + 60)
        ready.write_json(Path(request['reply_mailbox']), body)
        scheduler.sweep(now=1002.)
        state = reask.load(proactive_fixtures.CALLER)
        self.assertEqual(state['state'], reask.ANSWERED)
        return state

    def decide_at(self, now):
        clock = [now]
        def sleep(seconds):
            clock[0] += seconds
        with patch.dict(os.environ, CMUX_SURFACE_ID=proactive_fixtures.CALLER):
            return hook.decide(self.payload(), now=now, clock=lambda: clock[0],
                               monotonic=lambda: clock[0], sleep=sleep)

    def test_read_and_consumed_wait_reply_is_standby_until_its_own_due(self):
        caller = proactive_fixtures.CALLER
        self.begin()
        state = self.answer(due=2000.)
        blocked = self.decide_at(1003.)
        self.assertIn('not STANDBY', blocked['reason'])             # unconsumed ANSWERED
        lifecycle.consume(caller, state['reply']['body_sha256'], now=1004.)
        self.assertIsNotNone(hook.confirmed_standby(reask.load(caller), 1100.))
        self.assertIsNone(self.decide_at(1100.))                     # consumed WAIT, due ahead
        resumed = self.decide_at(2001.)                              # the reply's own due passed
        self.assertEqual(resumed['decision'], 'block')
        self.assertIn('run --caller-uuid', resumed['reason'])
        self.assertFalse((ready.state_root() / 'message-dispatch-v1').exists())

    def test_missing_receipt_is_not_standby_and_restored_receipt_is(self):
        caller = proactive_fixtures.CALLER
        self.begin()
        state = self.answer(due=2000.)
        lifecycle.consume(caller, state['reply']['body_sha256'], now=1004.)
        receipt = Path(reask.load(caller)['reply_history'][-1]['receipt'])
        saved = receipt.read_bytes()
        receipt.unlink()
        self.assertEqual(self.decide_at(1100.)['decision'], 'block')
        receipt.write_bytes(saved)
        self.assertIsNone(self.decide_at(1101.))

    def test_present_but_altered_receipt_is_not_standby(self):
        caller = proactive_fixtures.CALLER
        self.begin()
        state = self.answer(due=2000.)
        lifecycle.consume(caller, state['reply']['body_sha256'], now=1004.)
        receipt = Path(reask.load(caller)['reply_history'][-1]['receipt'])
        original = json.loads(receipt.read_bytes())
        for field, value in (('consumption', 'SCHEDULER_INFERRED'), ('body_sha256', '0' * 64),
                             ('supervisor', '00000000-0000-4000-8000-000000000000')):
            ready.write_json(receipt, dict(original, **{field: value}))
            self.assertIsNone(hook.confirmed_standby(reask.load(caller), 1100.), field)
        ready.write_json(receipt, original)
        self.assertIsNotNone(hook.confirmed_standby(reask.load(caller), 1100.))

    def test_standby_follows_the_replys_own_due_not_state_schedule(self):
        caller = proactive_fixtures.CALLER
        self.begin()
        state = self.answer(due=2000.)
        lifecycle.consume(caller, state['reply']['body_sha256'], now=1004.)
        state = reask.load(caller)
        state['next_due_epoch'] = 5000.          # a runner schedule, not the supervisor's reply
        reask.save(state)
        self.assertIsNotNone(hook.confirmed_standby(reask.load(caller), 1999.))
        self.assertIsNone(hook.confirmed_standby(reask.load(caller), 2001.))
        self.assertEqual(self.decide_at(2001.)['decision'], 'block')

    def test_consumed_ack_reply_is_not_standby(self):
        caller = proactive_fixtures.CALLER
        self.begin()
        state = self.answer(status='ACK', due=2000.)
        lifecycle.consume(caller, state['reply']['body_sha256'], now=1004.)
        self.assertIsNone(hook.confirmed_standby(reask.load(caller), 1100.))
        self.assertEqual(self.decide_at(1100.)['decision'], 'block')

    def test_future_state_due_without_any_reply_is_not_standby(self):
        caller = proactive_fixtures.CALLER
        self.begin()
        scheduler.sweep(now=1001.)
        state = reask.load(caller)
        state['next_due_epoch'] = 5000.
        reask.save(state)
        self.assertIsNone(hook.confirmed_standby(reask.load(caller), 1100.))
        self.assertEqual(self.decide_at(1100.)['decision'], 'block')

    def test_authenticated_scheduler_start_persists_file_only_for_new_enrollment(self):
        caller = proactive_fixtures.CALLER
        state = scheduler.start(self.live(), task_id='file-only', now=1000.,
                                authenticate_executor=True, file_only=True)
        self.assertIs(reask.load(caller)['file_only_preference'], True)
        self.assertTrue(scheduler.registered(reask.load(caller)), state)
        swept = scheduler.sweep(now=1001.)
        self.assertEqual((swept['file_posts'], swept['input_operations']), (1, 0))
        sender = Mock(side_effect=AssertionError('file-only foreground tick sent input'))
        self.assertEqual(reask.tick(caller, 1061., sender)['asks'][-1]['outcome'], 'FILE_ONLY_SCHEDULED')
        sender.assert_not_called()

    def test_formal_reservation_directs_to_assigned_work_not_idle_reask(self):
        marker, _ = self.reserve()
        self.begin()
        scheduler.sweep(now=1001.)
        self.assertEqual(len(reask.load(proactive_fixtures.CALLER)['paused_tasks']), 1)
        result = self.decide_at(1002.)
        self.assertIn('Formally assigned work', result['reason'])
        self.assertIn(marker['task_id'], result['reason'])
        self.assertNotIn('Continue useful assigned work', result['reason'])


if __name__ == '__main__':
    unittest.main()
