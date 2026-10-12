"""File inquiries remain discoverable without arming a research task."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import uuid

import executor_ready as ready
import executor_reask as reask
import executor_reply as replies
import supervisor_inbox as inbox
import cmux_supervisor_report_guard as guard
from offline_hook_thread_fixture import native_thread

CALLER = 'FD51FB34-5C79-4013-BD6B-9E10CFC743CD'
SUP = '41F454EB-0DDD-42BD-A529-BC79589F6DD5'
WORKSPACE = '6FB20312-9E66-43DB-AF09-92C3B18E1296'


class InboxTest(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.home = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.stack.enter_context(mock.patch.object(Path, 'home', return_value=self.home))
        self.payload, self.proof = native_thread(self.home)
        self.emissions = []

    def discover(self, *, now=100.0, emit=None):
        return inbox.discover_bound(WORKSPACE, SUP, thread_id=self.payload['session_id'],
                                    emit=self.emissions.append if emit is None else emit, now=now)

    def request(self, caller=CALLER, marker='EXECUTOR_READY_one'):
        state = reask.start(caller, SUP, 'completed-task', now=1.0, workspace=WORKSPACE)
        result = reask.write_channels(state, marker, 'request next fixed pack', 2.0)
        self.assertEqual(result['errors'], [])
        return state, reask.inbox_path(state)

    def test_discovery_exact_snapshot_no_delivery_deduplicates_and_renotifies_new_round(self):
        state, path = self.request()
        raw = path.read_bytes()
        notices = self.discover()
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]['request_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertFalse(notices[0]['native_delivery_confirmed'])
        self.assertEqual(notices[0]['input_operations'], 0)
        self.assertEqual(self.discover(), [])
        reask.write_channels(state, 'EXECUTOR_READY_two', 'request', 62.0)
        self.assertEqual(len(self.discover()), 1)
        self.assertEqual(len(list(path.parent.glob('*.json'))), 1)

    def test_wrong_workspace_supervisor_paths_templates_and_inactive_requests_refused(self):
        _state, path = self.request()
        body = ready.read_json(path)
        changes = [dict(workspace_uuid=str(uuid.uuid4())), dict(supervisor=CALLER),
                   dict(caller_surface_uuid=str(uuid.uuid4())), dict(status='ANSWERED'),
                   dict(reply_mailbox='/tmp/unrelated.json'), dict(reply_template={}),
                   dict(marker='different')]
        for change in changes:
            with self.subTest(change=change):
                ready.write_json(path, dict(body, **change))
                self.assertEqual(self.discover(), [])
        ready.write_json(path, body)
        self.assertEqual(len(self.discover()), 1)

    def test_complete_reply_skips_notice_placeholder_is_not_a_reply(self):
        state, path = self.request()
        body = ready.read_json(path)
        reply_path = Path(body['reply_mailbox'])
        ready.write_json(reply_path, body['reply_template'])
        self.assertEqual(len(self.discover()), 1)
        reask.write_channels(state, 'EXECUTOR_READY_two', 'request', 62.0)
        body = ready.read_json(path)
        reply = dict(body['reply_template'], trigger='original chapter review completes')
        ready.write_json(Path(body['reply_mailbox']), reply)
        self.assertEqual(self.discover(), [])

    def test_symlink_and_oversized_request_are_not_read(self):
        _state, path = self.request()
        target = path.with_suffix('.source')
        path.rename(target)
        path.symlink_to(target)
        self.assertEqual(self.discover(), [])
        self.assertEqual(inbox.request_bytes(path), (None, None))
        path.unlink()
        path.write_bytes(b' ' * (ready.MAX_JSON_BYTES + 1))
        self.assertEqual(self.discover(), [])

    def test_more_than_notice_budget_progresses_next_hook(self):
        for _ in range(inbox.MAX_NOTICES + 1):
            self.request(str(uuid.uuid4()))
        self.assertEqual(len(self.discover()), inbox.MAX_NOTICES)
        self.assertEqual(len(self.discover()), 1)

    def test_sparse_tool_events_and_changing_markers_do_not_starve_later_callers(self):
        states = {}
        for _ in range(inbox.MAX_NOTICES * 3):
            caller = str(uuid.uuid4()).upper()
            states[caller], _path = self.request(caller)
        first = self.discover(now=100.0)
        # Repeatedly asking callers do not overtake those never shown yet.
        for item in first:
            reask.write_channels(states[item['caller_surface_uuid']],
                                 'EXECUTOR_READY_new', 'still waiting', 159.0)
        second = self.discover(now=160.0)
        third = self.discover(now=220.0)
        shown = [item['caller_surface_uuid'] for batch in (first, second, third)
                 for item in batch]
        self.assertEqual(len(shown), len(states))
        self.assertEqual(set(shown), set(states))
        self.assertEqual(len(set(shown)), len(shown))

    def test_emit_failure_and_read_only_observation_do_not_consume_notice(self):
        self.request()
        with self.assertRaises(BrokenPipeError):
            self.discover(emit=mock.Mock(side_effect=BrokenPipeError('client closed')))
        read_only = inbox.discover_bound(WORKSPACE, SUP,
                                        thread_id=self.payload['session_id'], now=100.0)
        self.assertEqual(len(read_only), 1)
        self.assertEqual(len(self.discover()), 1)
        self.assertEqual(self.discover(), [])

    def test_unanswered_request_is_reminded_after_sixty_seconds(self):
        self.request()
        self.assertEqual(len(self.discover(now=100.0)), 1)
        self.assertEqual(self.discover(now=159.999), [])
        self.assertEqual(len(self.discover(now=160.0)), 1)
        self.assertEqual(self.discover(now=160.001), [])

    def test_discovery_cursors_are_isolated_by_actual_thread(self):
        self.request()
        self.assertEqual(len(self.discover()), 1)
        other = inbox.discover_bound(WORKSPACE, SUP, thread_id=str(uuid.uuid4()),
                                     emit=self.emissions.append, now=100.0)
        self.assertEqual(len(other), 1)
        self.assertEqual(self.discover(), [])

    def test_automatic_entry_contract_without_active_task_marker(self):
        self.request()
        output = io.StringIO()
        payload = self.payload
        with mock.patch.object(inbox.identity, 'evaluation', return_value=contextlib.nullcontext()) as evaluation, \
                mock.patch.object(inbox.identity, 'identity', return_value=(WORKSPACE, SUP)), \
                mock.patch.object(inbox.identity, 'native_proof', return_value=self.proof), \
                mock.patch.object(guard.stop_guard, 'ACTIVE_DIR', self.home / 'absent'), \
                mock.patch.object(guard.sys, 'stdin', io.StringIO(json.dumps(payload))), \
                contextlib.redirect_stdout(output):
            self.assertEqual(guard.main(), 0)
        evaluation.assert_called_once_with(payload)
        result = json.loads(output.getvalue())['hookSpecificOutput']
        self.assertEqual(result['hookEventName'], 'PostToolUse')
        self.assertIn('REQUEST_DISCOVERED', result['additionalContext'])
        self.assertIn('REPLY=', result['additionalContext'])
        self.assertEqual(inbox.discover(dict(hook_event_name='Stop')), [])


if __name__ == '__main__':
    unittest.main()
