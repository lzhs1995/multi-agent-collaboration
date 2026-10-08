"""Public message dispatch: crash/retry, stale identity, and zero-input recovery."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cmux_bridge as b
from native_test_support import NativeFixture


class MessageJournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        p = patch.object(Path, 'home', return_value=self.home)
        p.start(); self.addCleanup(p.stop)
        self.identity = dict(workspace_uuid='workspace', caller_surface_uuid='caller',
                             target_surface_uuid='target', target_pane_uuid='pane')
        self.native = NativeFixture.attach(self, home=self.home, identity=self.identity)
        self.home = self.native.home
        self.pin = self.native.pin
        p = patch.object(b.time, 'sleep')
        p.start(); self.addCleanup(p.stop)
        self.text = 'STATUS: message-nonce-20261005'
        self.marker = 'message-nonce-20261005'
        self.idle = '› \nGPT-6 high'
        self.done = '› ' + self.text + '\n• Read evidence\n' + self.idle

    def call(self, **kwargs):
        return b.submit_text('peer', self.text, marker=self.marker, **kwargs)

    def test_success_blocks_duplicate_and_changed_payload(self):
        with patch.object(b, 'read_screen', side_effect=self.native.ready_screens(
                self.idle, self.native.draft(self.text), self.done)), \
                patch.object(b, 'send_text') as paste, \
                patch.object(b, 'send_key', side_effect=self.native.receipt_on_key(self.text)) as key:
            result = self.call()
            self.assertTrue(result['confirmed'])
            with self.assertRaisesRegex(b.TaskPackContractError, 'RECEIPT_EXISTS'):
                self.call()
            self.text += ' modified'
            with self.assertRaisesRegex(b.TaskPackContractError, 'BINDING_CHANGED'):
                self.call()
            self.assertEqual(paste.call_count, 1)
            self.assertEqual(key.call_count, 1)

    def test_lost_paste_ack_never_repeats_then_reconciles(self):
        with patch.object(b, 'read_screen', return_value=self.idle), \
                patch.object(b, 'send_text', side_effect=OSError('lost ACK')) as paste, \
                patch.object(b, 'send_key') as key:
            with self.assertRaises(OSError):
                self.call()
            with self.assertRaisesRegex(b.TaskPackContractError, 'ATTEMPT_EXISTS'):
                self.call()
            paste.assert_called_once(); key.assert_not_called()
        self.native.append_user(self.text)
        with patch.object(b, 'read_screen', return_value=self.done), \
                patch.object(b, 'send_text') as paste, patch.object(b, 'send_key') as key:
            result = self.call(reconcile_only=True)
            self.assertTrue(result['reconciled_read_only'])
            self.assertEqual(json.loads(Path(result['observation']).read_text())['input_operations'], 0)
            paste.assert_not_called(); key.assert_not_called()

    def test_occupied_or_stale_marker_never_mutates(self):
        for screen in ('› continue\nGPT-6 high', self.done):
            with self.subTest(screen=screen), patch.object(b, 'read_screen', return_value=screen), \
                    patch.object(b, 'send_text') as paste, patch.object(b, 'send_key') as key:
                with self.assertRaises((b.DispatchUnconfirmed, b.TaskPackContractError)):
                    self.call()
                paste.assert_not_called(); key.assert_not_called()

    def test_identity_changes_before_paste(self):
        def move_after_observation(*args, **kwargs):
            self.identity['target_pane_uuid'] = 'moved'
            return self.idle
        with patch.object(b, 'read_screen', side_effect=move_after_observation), \
                patch.object(b, 'send_text') as paste, patch.object(b, 'send_key') as key:
            with self.assertRaisesRegex(b.TaskPackContractError, 'IDENTITY_CHANGED'):
                self.call()
            paste.assert_not_called(); key.assert_not_called()

    def test_marker_and_force_are_required_to_be_safe(self):
        with patch.object(b, 'send_text') as paste, patch.object(b, 'send_key') as key:
            with self.assertRaisesRegex(b.TaskPackContractError, 'MARKER_REQUIRED'):
                b.submit_text('peer', 'STATUS: no marker')
            with self.assertRaisesRegex(b.TaskPackContractError, 'PRESERVE_COMPOSE'):
                self.call(force_compose=True)
            with self.assertRaisesRegex(b.TaskPackContractError, 'NO_SUBMITTED_MESSAGE'):
                self.call(reconcile_only=True)
            paste.assert_not_called(); key.assert_not_called()

    def test_posthook_revalidates_message_and_rejects_modified_evidence(self):
        from cmux_submit_confirmation_guard import _attempt_evidence
        with patch.object(b, 'read_screen', side_effect=self.native.ready_screens(
                self.idle, self.native.draft(self.text), self.done)), \
                patch.object(b, 'send_text'), \
                patch.object(b, 'send_key', side_effect=self.native.receipt_on_key(self.text)):
            result = self.call()
        call = dict(kind='text', surface='peer', text=self.text, marker=self.marker)
        with patch.object(b, 'send_text') as paste, patch.object(b, 'send_key') as key:
            proof = _attempt_evidence(call, 'peer', b)
            self.assertEqual(proof['source'], 'revalidated_message_dispatch_v1')
            path = Path(result['attempt'])
            attempt = json.loads(path.read_text())
            attempt['events'][0]['screen_sha256'] = 'changed'
            path.write_text(json.dumps(attempt))
            self.assertIsNone(_attempt_evidence(call, 'peer', b))
            paste.assert_not_called(); key.assert_not_called()

    def test_task_target_lock_prevents_message_input(self):
        import fcntl
        from cmux_message_journal import digest
        root = self.home / '.local/state/multi-agent-collaboration/task-dispatch-v1'
        root.mkdir(parents=True)
        with (root / ('target-' + digest('target') + '.lock')).open('a+b') as lock, \
                patch.object(b, 'send_text') as paste, patch.object(b, 'send_key') as key:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(b.TaskPackContractError, 'MESSAGE_IN_PROGRESS'):
                self.call()
            paste.assert_not_called(); key.assert_not_called()


if __name__ == '__main__':
    unittest.main()
