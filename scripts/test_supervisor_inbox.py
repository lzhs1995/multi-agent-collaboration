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

CALLER = 'FD51FB34-5C79-4013-BD6B-9E10CFC743CD'
SUP = '41F454EB-0DDD-42BD-A529-BC79589F6DD5'
WORKSPACE = '6FB20312-9E66-43DB-AF09-92C3B18E1296'


class InboxTest(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.home = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.stack.enter_context(mock.patch.object(Path, 'home', return_value=self.home))

    def request(self, caller=CALLER, marker='EXECUTOR_READY_one'):
        state = reask.start(caller, SUP, 'completed-task', now=1.0, workspace=WORKSPACE)
        result = reask.write_channels(state, marker, 'request next fixed pack', 2.0)
        self.assertEqual(result['errors'], [])
        return state, reask.inbox_path(state)

    def test_discovery_exact_snapshot_no_delivery_deduplicates_and_renotifies_new_round(self):
        state, path = self.request()
        raw = path.read_bytes()
        notices = inbox.discover_bound(WORKSPACE, SUP)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]['request_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertFalse(notices[0]['native_delivery_confirmed'])
        self.assertEqual(notices[0]['input_operations'], 0)
        self.assertEqual(inbox.discover_bound(WORKSPACE, SUP), [])
        reask.write_channels(state, 'EXECUTOR_READY_two', 'request', 62.0)
        self.assertEqual(len(inbox.discover_bound(WORKSPACE, SUP)), 1)
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
                self.assertEqual(inbox.discover_bound(WORKSPACE, SUP), [])
        ready.write_json(path, body)
        self.assertEqual(len(inbox.discover_bound(WORKSPACE, SUP)), 1)

    def test_complete_reply_skips_notice_placeholder_is_not_a_reply(self):
        state, path = self.request()
        body = ready.read_json(path)
        reply_path = Path(body['reply_mailbox'])
        ready.write_json(reply_path, body['reply_template'])
        self.assertEqual(len(inbox.discover_bound(WORKSPACE, SUP)), 1)
        reask.write_channels(state, 'EXECUTOR_READY_two', 'request', 62.0)
        body = ready.read_json(path)
        reply = dict(body['reply_template'], trigger='original chapter review completes')
        ready.write_json(Path(body['reply_mailbox']), reply)
        self.assertEqual(inbox.discover_bound(WORKSPACE, SUP), [])

    def test_symlink_and_oversized_request_are_not_read(self):
        _state, path = self.request()
        target = path.with_suffix('.source')
        path.rename(target)
        path.symlink_to(target)
        self.assertEqual(inbox.discover_bound(WORKSPACE, SUP), [])
        self.assertEqual(inbox.request_bytes(path), (None, None))
        path.unlink()
        path.write_bytes(b' ' * (ready.MAX_JSON_BYTES + 1))
        self.assertEqual(inbox.discover_bound(WORKSPACE, SUP), [])

    def test_more_than_notice_budget_progresses_next_hook(self):
        for _ in range(inbox.MAX_NOTICES + 1):
            self.request(str(uuid.uuid4()))
        self.assertEqual(len(inbox.discover_bound(WORKSPACE, SUP)), inbox.MAX_NOTICES)
        self.assertEqual(len(inbox.discover_bound(WORKSPACE, SUP)), 1)

    def test_automatic_entry_contract_without_active_task_marker(self):
        self.request()
        output = io.StringIO()
        payload = dict(hook_event_name='PostToolUse', tool_name='exec_command')
        with mock.patch.object(inbox.identity, 'evaluation', return_value=contextlib.nullcontext()) as evaluation, \
                mock.patch.object(inbox.identity, 'identity', return_value=(WORKSPACE, SUP)), \
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
