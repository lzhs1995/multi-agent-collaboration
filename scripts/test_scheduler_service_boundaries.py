"""Untrusted sort hints never bypass the original locked inquiry boundaries."""
import copy
import unittest
from unittest.mock import patch

import executor_ready as ready
import executor_reask as reask
import executor_reask_scheduler as scheduler
import test_executor_reask_scheduler as fixtures
import test_executor_reask_historical_inquiries as historical


class ServiceBoundaryTests(unittest.TestCase):
    setUp = fixtures.SchedulerTests.setUp
    begin = fixtures.SchedulerTests.begin
    live = fixtures.SchedulerTests.live
    enroll = fixtures.SchedulerTests.enroll

    def hints(self):
        path = scheduler.enrollment_path(fixtures.CALLER)
        ready.write_json(scheduler.root() / 'STATUS.json', dict(
            at_epoch=1000., service=dict(schema=scheduler.SERVICE_SCHEMA,
                authority='BOGUS_ALLOW_ALL',
                enrollments={path.name: dict(last_attempt_epoch=0.,
                    last_completed_epoch=0., next_due_epoch=0.)})))

    def test_hint_cannot_override_stop_or_binding_controller_workspace(self):
        self.enroll()
        path = scheduler.enrollment_path(fixtures.CALLER)
        original = ready.read_json(path)
        for changed in (
                dict(binding=dict(original['binding'], workspace_uuid=fixtures.SUP)),
                dict(controller={}), dict(scheduler={}), dict(live_binding={})):
            with self.subTest(changed=changed):
                ready.write_json(path, dict(copy.deepcopy(original), **changed))
                self.hints()
                result = scheduler.sweep(now=1001.)
                self.assertEqual(result['file_posts'], 0)
                self.assertEqual(reask.load(fixtures.CALLER)['ask_count'], 0)
        ready.write_json(path, original)
        ready.write_json(reask.stop_file(fixtures.CALLER), dict(reason='USER_STOP'))
        self.hints()
        result = scheduler.sweep(now=1001.)
        self.assertEqual(result['file_posts'], 0)
        self.assertEqual(reask.load(fixtures.CALLER)['state'], reask.STOPPED)
        self.transport.assert_not_called()

    def test_future_hint_cannot_delay_actual_due_and_unknown_is_preserved(self):
        state = self.enroll()
        pending = dict(marker='ORIGINAL_UNKNOWN', outcome='UNKNOWN', terminal=True,
                       origin=dict(controller='original'))
        state['terminal_pending'] = copy.deepcopy(pending)
        reask.save(state)
        path = scheduler.enrollment_path(fixtures.CALLER)
        ready.write_json(scheduler.root() / 'STATUS.json', dict(at_epoch=1000.,
            service=dict(schema=scheduler.SERVICE_SCHEMA, enrollments={
                path.name: dict(last_attempt_epoch=1000., next_due_epoch=1119.,
                                status='FINISHED')})))
        with patch.object(ready, 'Tail', side_effect=AssertionError('no transcript')):
            result = scheduler.sweep(now=1001.)
        self.assertEqual(result['file_posts'], 1)
        self.assertEqual(reask.load(fixtures.CALLER)['terminal_pending'], pending)
        self.transport.assert_not_called()


class HistoricalServiceBoundaries(unittest.TestCase):
    setUp = historical.HistoricalInquiryTests.setUp
    live = historical.HistoricalInquiryTests.live
    make_release = historical.HistoricalInquiryTests.make_release
    write_pin = historical.HistoricalInquiryTests.write_pin
    historical_wait = historical.HistoricalInquiryTests.historical_wait
    assert_original = historical.HistoricalInquiryTests.assert_original

    def hint(self):
        scheduler.secure_directory(scheduler.root())
        path = scheduler.enrollment_path(fixtures.CALLER)
        ready.write_json(scheduler.root() / 'STATUS.json', dict(at_epoch=1001.,
            service=dict(schema=scheduler.SERVICE_SCHEMA, enrollments={
                path.name: dict(last_attempt_epoch=0., next_due_epoch=0.)})))

    def test_revoked_and_expired_authority_remain_stopped(self):
        for expired in (False, True):
            with self.subTest(expired=expired):
                # The test uses a fresh independent private home per case.
                case = historical.HistoricalInquiryTests('test_operator_stop_still_wins_over_historical_authority')
                case.setUp()
                try:
                    case.historical_wait(until_epoch=1003. if expired else None)
                    if not expired:
                        case.auth.write_text('Authorization revoked.\n')
                    scheduler.secure_directory(scheduler.root())
                    path = scheduler.enrollment_path(fixtures.CALLER)
                    ready.write_json(scheduler.root() / 'STATUS.json', dict(at_epoch=1001.,
                        service=dict(schema=scheduler.SERVICE_SCHEMA,
                                     enrollments={path.name: dict(last_attempt_epoch=0.)})))
                    result = scheduler.sweep(now=1003.)
                    self.assertEqual(result['file_posts'], 0)
                    self.assertEqual(reask.load(fixtures.CALLER)['exit_reason'],
                                     'AUTHORIZATION_EXPIRED' if expired else 'AUTHORIZATION_CHANGED')
                    case.transport.assert_not_called()
                finally:
                    case.doCleanups()

    def test_reservation_file_capability_remains_exact(self):
        original, record, _ = self.historical_wait(capability=None)
        self.hint()
        result = scheduler.sweep(now=1002.)
        self.assertEqual(result['file_posts'], 0)
        self.assert_original(original, record)

    def test_explicit_reservation_fallback_and_pending_remain_authorized(self):
        original, record, _ = self.historical_wait('0.4.61-')
        self.hint()
        result = scheduler.sweep(now=1002.)
        self.assertEqual(result['file_posts'], 1)
        self.assertTrue(result['episodes'][0]['reservation_inquiries_authorized'])
        self.assert_original(original, record)

    def test_release_member_change_invalidates_before_new_inquiry(self):
        _, _, source = self.historical_wait()
        self.hint()
        member = source / 'README.md'
        raw = member.read_bytes()
        self.write_pin(member, b'X' + raw[1:])
        result = scheduler.sweep(now=1002.)
        self.assertEqual(result['file_posts'], 0)
        self.assertEqual(result['episodes'][0]['status'], 'BINDING_OR_CONTROLLER_MISMATCH')
        self.transport.assert_not_called()


if __name__ == '__main__':
    unittest.main()
