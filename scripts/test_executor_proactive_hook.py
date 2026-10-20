"""Explicit task Stop enforcement over real registration and reply files."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

import cmux_workflow_advisory as wrapper
import executor_proactive_hook as hook
import executor_ready as ready
import executor_reask as reask
import executor_reask_lifecycle as lifecycle
import executor_reask_scheduler as scheduler
import test_executor_reask_reservation_inquiries as fixtures
from test_executor_reask_identity import CALLER, SUP, SESSION


class ProactiveHookTests(unittest.TestCase):
    setUp = fixtures.ReservationInquiryIntegrationTests.setUp
    select_provider = fixtures.ReservationInquiryIntegrationTests.select_provider
    write_registration = fixtures.ReservationInquiryIntegrationTests.write_registration
    process = fixtures.ReservationInquiryIntegrationTests.process
    ps = fixtures.ReservationInquiryIntegrationTests.ps
    tree = fixtures.ReservationInquiryIntegrationTests.tree
    live = fixtures.ReservationInquiryIntegrationTests.live
    snapshot = fixtures.ReservationInquiryIntegrationTests.snapshot
    files = fixtures.ReservationInquiryIntegrationTests.files
    reserve = fixtures.ReservationInquiryIntegrationTests.reserve

    def begin(self, proactive=True):
        auth = self.home / 'authorization.txt'
        auth.write_text('Continue this task; ask every60seconds until actual reply, '
                        'including reservations. Enforce using a task-scoped hook.')
        continuation = dict(enabled=True, owner=SUP, overall_task='original task',
                            authorization_file=str(auth), reservation_inquiries=True)
        if proactive:
            continuation['proactive_hook'] = True
        return scheduler.start(self.live(), task_id='proactive', now=1000.,
                               authenticate_executor=True, continuing=True,
                               continuation=continuation)

    def payload(self):
        return dict(hook_event_name='Stop', session_id=SESSION,
                    transcript_path=str(self.log), stop_hook_active=True)

    def call(self, payload=None):
        with patch.dict(os.environ, CMUX_SURFACE_ID=CALLER):
            return hook.decide(self.payload() if payload is None else payload, now=1001.)

    def test_current_authorized_original_hook_enforces_reentrant_stop_read_only(self):
        self.begin()
        before = self.files()
        result = self.call()
        self.assertEqual(result['decision'], 'block')
        self.assertIn('run --caller-uuid', result['reason'])
        self.assertIn('--max-seconds 60', result['reason'])
        self.assertEqual(self.files(), before)
        self.assertFalse((ready.state_root() / 'message-dispatch-v1').exists())

    def test_legacy_registration_never_acquires_hook_authority(self):
        state = self.begin(False)
        self.assertNotIn('proactive_hook', lifecycle.binding(state['continuation']))
        self.assertIsNone(self.call())

    def test_user_stop_and_changed_authorization_end_enforcement(self):
        state = self.begin()
        Path(state['continuation']['authorization']['path']).write_text('revoked')
        self.assertIsNone(self.call())
        ready.write_json(reask.stop_file(CALLER), dict(reason='USER_STOP'))
        self.assertIsNone(self.call())

    def test_wrong_session_child_or_other_event_gets_no_enforcement(self):
        self.begin()
        for update in ({'agent_id': 'child'}, {'hook_event_name': 'PostToolUse'}):
            self.assertIsNone(self.call(dict(self.payload(), **update)))
        with self.assertRaises(ValueError):
            self.call(dict(self.payload(), session_id='another-session'))

    def test_real_mailbox_reply_requires_original_full_read_consume(self):
        self.begin()
        scheduler.sweep(now=1001.)
        request = ready.read_json(reask.inbox_path(reask.load(CALLER)))
        body = dict(request['reply_template'], trigger='Read and continue',
                    next_due_epoch=1062., deadline_epoch=1122.)
        ready.write_json(Path(request['reply_mailbox']), body)
        scheduler.sweep(now=1002.)
        state = reask.load(CALLER)
        before = self.files()
        result = self.call()
        self.assertIn(state['reply']['body_sha256'], result['reason'])
        self.assertIn('consume', result['reason'])
        self.assertEqual(self.files(), before)

    def test_reservation_allows_authorized_foreground_status_only(self):
        self.reserve()
        self.begin()
        sender = Mock(return_value=dict(outcome='TEST_ONLY', terminal=False))
        state = reask.tick(CALLER, 1001., send=sender)
        sender.assert_called_once()
        self.assertEqual(state['asks'][-1]['inquiry_kind'], 'RESERVATION_STATUS')
        self.assertEqual(len(state['paused_tasks']), 1)
        # The launchd scheduler never becomes a sender, even for new authority.
        with patch.object(reask, 'default_send', side_effect=AssertionError('scheduler sent')):
            result = scheduler.sweep(now=1061.)
        self.assertEqual(result['input_operations'], 0)
        self.assertEqual(result['file_posts'], 1)

    def test_installed_wrapper_enforces_only_the_single_scoped_stop_entry(self):
        self.begin()
        with patch.dict(os.environ, CMUX_SURFACE_ID=CALLER), \
                patch.object(sys, 'argv', ['hook', '--hook', 'cmux_executor_idle_guard.py']), \
                patch.object(sys, 'stdin', io.StringIO(json.dumps(self.payload()))), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(wrapper.main(), 0)
        self.assertEqual(json.loads(output.getvalue())['decision'], 'block')


if __name__ == '__main__':
    unittest.main()
