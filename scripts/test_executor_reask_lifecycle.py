"""Continuing file requests preserve authority, consumption and task boundaries."""
import copy
import json
from pathlib import Path
from unittest.mock import patch
import unittest

import executor_ready as ready
import executor_reask as reask
import executor_reask_lifecycle as lifecycle
import executor_reask_scheduler as scheduler
import test_executor_reask_scheduler as fixtures
from test_executor_reask_scheduler import CALLER, SUP, WORKSPACE


class LifecycleTests(unittest.TestCase):
    setUp = fixtures.SchedulerTests.setUp
    live = fixtures.SchedulerTests.live
    enroll = fixtures.SchedulerTests.enroll
    def begin(self, caller=CALLER, **kwargs):
        auth = self.home / 'explicit-user-authorization.txt'
        if not auth.exists():
            auth.write_text('Continue requests every 60 seconds until the overall task finishes.')
        value = dict(enabled=True, owner=SUP, overall_task='overall-work',
                     authorization_file=str(auth))
        value.update(kwargs.pop('authorization', {}))
        return reask.start(caller, SUP, 'original-task', workspace=WORKSPACE, now=1000.,
                           continuing=True, continuation=value, **kwargs)

    def answered(self, *, status='WAITING_DEPENDENCY', due=1061.):
        scheduler.sweep(now=1001.)
        state = reask.load(CALLER)
        current = ready.read_json(reask.inbox_path(state))
        body = dict(current['reply_template'], status=status, trigger='Await the next scoped result.',
                    next_due_epoch=due, deadline_epoch=due + 60)
        ready.write_json(Path(current['reply_mailbox']), body)
        scheduler.sweep(now=1002.)
        state = reask.load(CALLER)
        self.assertEqual(state['state'], reask.ANSWERED)
        return state

    def test_two_real_state_rounds_require_explicit_consumption_and_new_markers(self):
        original = self.enroll()
        for index in range(2):
            due = 1061. + index * 60
            scheduler.sweep(now=1001. + index * 60)
            current = ready.read_json(reask.inbox_path(reask.load(CALLER)))
            marker = current['marker']
            body = dict(current['reply_template'], trigger='Next result expected.',
                        next_due_epoch=due, deadline_epoch=due + 60)
            ready.write_json(Path(current['reply_mailbox']), body)
            scheduler.sweep(now=1002. + index * 60)
            state = reask.load(CALLER)
            self.assertEqual(state['state'], reask.ANSWERED)
            self.assertEqual(ready.read_json(scheduler.enrollment_path(CALLER))['status'], 'ENROLLED')
            self.assertEqual(scheduler.sweep(now=due + 2)['file_posts'], 0)
            state = lifecycle.consume(CALLER, state['reply']['body_sha256'], now=1003. + index * 60)
            self.assertEqual(state['reply_consumed_epoch'], 1003. + index * 60)
            self.assertEqual(state['reply_detected_epoch'], 1002. + index * 60)
            self.assertEqual(state['episode_id'], original['episode_id'])
            self.assertEqual(scheduler.sweep(now=due - 1)['file_posts'], 0)
            self.assertEqual(scheduler.sweep(now=due)['file_posts'], 1)
            self.assertNotEqual(reask.load(CALLER)['asks'][-1]['marker'], marker)
        self.assertEqual(reask.load(CALLER)['rearm_count'], 2)
        self.transport.assert_not_called()

    def test_no_consumption_on_hash_mismatch_or_timer(self):
        self.enroll()
        state = self.answered()
        for digest in ('', '0' * 64):
            with self.assertRaises(ValueError):
                lifecycle.consume(CALLER, digest, now=1003.)
        self.assertIsNone(reask.load(CALLER)['reply_consumed_epoch'])
        self.assertEqual(scheduler.sweep(now=5000.)['file_posts'], 0)
        self.assertEqual(reask.load(CALLER)['state'], reask.ANSWERED)

    def test_consumed_body_stays_complete_and_late_old_reply_cannot_close_new_round(self):
        self.enroll()
        state = self.answered()
        body = state['reply']['body']
        body['detail'] = '正文' * 2000
        ready.write_json(Path(state['reply']['path']), body)
        # An observed reply is pinned. A changed disk file is not silently read
        # as the body that the foreground executor is acknowledging.
        state = lifecycle.consume(CALLER, state['reply']['body_sha256'], now=1003.)
        receipt = ready.read_json(Path(state['reply_history'][-1]['receipt']))
        self.assertNotIn('detail', receipt['body'])
        scheduler.sweep(now=1061.)
        self.assertEqual(scheduler.sweep(now=1062.)['episodes'][0]['status'], reask.WAITING)

    def test_wait_reply_requires_owner_due_and_deadline(self):
        self.enroll()
        scheduler.sweep(now=1001.)
        state = reask.load(CALLER)
        current = ready.read_json(reask.inbox_path(state))
        valid = dict(current['reply_template'], trigger='Await result.', next_due_epoch=1061., deadline_epoch=1121.)
        for delta in (dict(owner=CALLER), dict(next_due_epoch=None), dict(deadline_epoch=0),
                      dict(deadline_epoch=float('inf')), dict(next_due_epoch=float('nan'))):
            ready.write_json(Path(current['reply_mailbox']), dict(valid, **delta))
            scheduler.sweep(now=1002.)
            self.assertEqual(reask.load(CALLER)['state'], reask.WAITING)

    def test_stop_while_answered_prevents_consume_and_rearm(self):
        self.enroll()
        state = self.answered()
        ready.write_json(reask.stop_file(CALLER), {'reason': 'OPERATOR'})
        stopped = lifecycle.consume(CALLER, state['reply']['body_sha256'], now=1003.)
        self.assertEqual(stopped['state'], reask.STOPPED)
        self.assertIsNone(stopped['reply_consumed_epoch'])
        self.assertEqual(scheduler.sweep(now=1061.)['file_posts'], 0)

    def test_authorization_revocation_and_expiry_stop_without_sending(self):
        self.enroll()
        (self.home / 'explicit-user-authorization.txt').write_text('Authorization revoked.')
        scheduler.sweep(now=1001.)
        self.assertEqual(reask.load(CALLER)['exit_reason'], 'AUTHORIZATION_CHANGED')
        self.transport.assert_not_called()

    def test_authorization_expiry_is_checked_even_with_unconsumed_reply(self):
        state = self.begin(authorization={'until_epoch': 1100.})
        scheduler.enroll(state, self.live(), now=1000.)
        self.answered()
        scheduler.sweep(now=1100.)
        self.assertEqual(reask.load(CALLER)['exit_reason'], 'AUTHORIZATION_EXPIRED')

    def test_changed_authorization_binding_cannot_gain_scheduler_authority(self):
        self.enroll()
        original = reask.load(CALLER)
        for change in (None, dict(original['continuation'], overall_task='other')):
            changed = copy.deepcopy(original)
            changed['continuation'] = change
            reask.save(changed)
            self.assertEqual(scheduler.sweep(now=1001.)['file_posts'], 0)
        reask.save(original)

    def task_marker(self, task='child-task', supervisor=SUP):
        return dict(collaboration_id=task, task_id=task, workspace_uuid=WORKSPACE,
                    participants=[dict(role='supervisor', surface_uuid=supervisor),
                                  dict(role='executor', surface_uuid=CALLER)])

    def test_formal_reservation_pauses_and_child_release_resumes_same_authorization(self):
        self.enroll()
        state = self.answered(status='TASK')
        lifecycle.consume(CALLER, state['reply']['body_sha256'], now=1003.)
        # A mailbox TASK alone did not pause or prove a dispatch.
        self.assertEqual(scheduler.sweep(now=1061.)['file_posts'], 1)
        marker = self.task_marker()
        lifecycle.task_boundary(marker, 'RESERVED', now=1062.)
        self.assertEqual(scheduler.sweep(now=1121.)['file_posts'], 0)
        lifecycle.task_boundary(marker, 'RELEASED', now=1122.)
        self.assertEqual(scheduler.sweep(now=1122.)['file_posts'], 1)
        self.assertEqual(reask.load(CALLER)['continuation']['status'], 'ACTIVE')

    def test_foreign_release_and_one_of_two_children_cannot_unpause(self):
        self.enroll()
        for task in ('a', 'b'):
            lifecycle.task_boundary(self.task_marker(task), 'RESERVED', now=1001.)
        lifecycle.task_boundary(self.task_marker('b', CALLER), 'RELEASED', now=1002.)
        lifecycle.task_boundary(self.task_marker('a'), 'RELEASED', now=1002.)
        self.assertEqual(scheduler.sweep(now=1003.)['file_posts'], 0)
        self.assertEqual(len(reask.load(CALLER)['paused_tasks']), 1)

    def test_unknown_original_survives_reply_consumption_and_reservation(self):
        state = self.enroll()
        pending = dict(marker='original', outcome='UNKNOWN', origin={'controller': 'old'})
        state['terminal_pending'] = copy.deepcopy(pending)
        reask.save(state)
        state = self.answered()
        lifecycle.consume(CALLER, state['reply']['body_sha256'], now=1003.)
        lifecycle.task_boundary(self.task_marker(), 'RESERVED', now=1004.)
        lifecycle.task_boundary(self.task_marker(), 'RELEASED', now=1005.)
        scheduler.sweep(now=1061.)
        self.assertEqual(reask.load(CALLER)['terminal_pending'], pending)
        self.transport.assert_not_called()

    def test_harness_arm_and_disarm_preserve_overall_authorization(self):
        import mac_harness as harness
        self.enroll()
        with patch.object(harness, '_workspace_key', return_value=WORKSPACE), \
                patch.object(harness, '_ACTIVE_DIR', self.home / 'active'):
            marker = harness.arm_task('child', self.home / 'report', supervisor_surface_uuid=SUP,
                                      executor_surface_uuid=CALLER, workspace_uuid=WORKSPACE)
            self.assertEqual(scheduler.sweep(now=1001.)['file_posts'], 0)
            self.assertEqual(harness.disarm_task('child'), 1)
        self.assertEqual(reask.load(CALLER)['paused_tasks'], [])
        self.assertEqual(reask.load(CALLER)['continuation']['status'], 'ACTIVE')

    def test_complete_reply_above_display_limit_is_preserved_in_consumption(self):
        self.enroll()
        scheduler.sweep(now=1001.)
        current = ready.read_json(reask.inbox_path(reask.load(CALLER)))
        body = dict(current['reply_template'], trigger='Wait for complete review.',
                    next_due_epoch=1061., deadline_epoch=1121., detail='审阅正文' * 1000)
        ready.write_json(Path(current['reply_mailbox']), body)
        scheduler.sweep(now=1002.)
        state = reask.load(CALLER)
        self.assertEqual(state['reply']['body'], body)
        state = lifecycle.consume(CALLER, state['reply']['body_sha256'], now=1003.)
        self.assertEqual(ready.read_json(Path(state['reply_history'][-1]['receipt']))['body'], body)



if __name__ == '__main__':
    unittest.main()
