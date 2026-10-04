"""Callback retry must observe the original attempt, never paste it again."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import cmux_bridge as b


class CallbackObservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.path = root / 'task-pack.json'
        self.path.write_text('{}')
        report = root / 'report.md'
        report.write_text('actual report')
        self.pack = dict(task_id='test-task', completion_nonce='nonce123456',
                         completion_callback='DONE|test-task|nonce123456|REPORT=' + str(report),
                         callback_target='surface:7', report=str(report),
                         completion_receipt=str(root / 'receipt.json'))
        self.binding = dict(caller_surface_uuid='caller', workspace_uuid='workspace',
                            target_surface_uuid='target')
        for patcher in [patch.object(b, 'validate_task_pack_contract', return_value=self.pack),
                        patch('availability_contract.require_action'),
                        patch.object(b, 'require_same_workspace', return_value=self.binding)]:
            patcher.start()
            self.addCleanup(patcher.stop)

    def queue(self):
        with patch.object(b, 'submit_text', side_effect=b.DispatchUnconfirmed(
                'queued', state=b.DELIVERY_QUEUED_AT_RECEIVER)) as send:
            with self.assertRaises(b.DispatchUnconfirmed):
                b.submit_completion_callback(self.path)
            send.assert_called_once()
        self.assertFalse(Path(self.pack['completion_receipt']).exists())

    def test_queue_then_consume_is_one_send(self):
        self.queue()
        screen = 'DONE|test-task|nonce123456\n⏺ Received report\n› Ask Codex to do anything\nGPT-6 high'
        with patch.object(b, 'submit_text') as send, patch.object(b, 'read_screen', return_value=screen):
            receipt = b.submit_completion_callback(self.path)
            self.assertTrue(receipt['confirmed'])
            self.assertEqual(receipt['confirmation_method'], 'observe_existing_attempt')
            send.assert_not_called()

    def test_queued_compose_and_echo_do_not_confirm(self):
        self.queue()
        for screen in ['Messages to be submitted after current tool\nnonce123456\n⏺ old output',
                       '› nonce123456\nGPT-6 high', 'nonce123456\nno assistant activity']:
            with self.subTest(screen=screen), patch.object(b, 'submit_text') as send, \
                 patch.object(b, 'read_screen', return_value=screen):
                with self.assertRaises(b.DispatchUnconfirmed):
                    b.submit_completion_callback(self.path)
                send.assert_not_called()
        self.assertFalse(Path(self.pack['completion_receipt']).exists())

    def test_report_change_denies_without_send(self):
        self.queue()
        Path(self.pack['report']).write_text('changed')
        with patch.object(b, 'submit_text') as send:
            with self.assertRaisesRegex(b.TaskPackContractError, 'REPORT_CHANGED'):
                b.submit_completion_callback(self.path)
            send.assert_not_called()

    def test_pack_change_denies_without_send(self):
        self.queue()
        self.path.write_text('{"changed":true}')
        with patch.object(b, 'submit_text') as send:
            with self.assertRaisesRegex(b.TaskPackContractError, 'PACK_CHANGED'):
                b.submit_completion_callback(self.path)
            send.assert_not_called()

    def test_moved_peer_denies_without_send(self):
        self.queue()
        with patch.object(b, 'require_same_workspace', return_value={'workspace_uuid':'other'}), \
             patch.object(b, 'submit_text') as send:
            with self.assertRaisesRegex(b.TaskPackContractError, 'WORKSPACE_CHANGED'):
                b.submit_completion_callback(self.path)
            send.assert_not_called()

    def test_crash_preserves_attempt_and_never_resends(self):
        with patch.object(b, 'submit_text', side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError):
                b.submit_completion_callback(self.path)
        with patch.object(b, 'submit_text') as send, patch.object(b, 'read_screen', return_value=''):
            with self.assertRaises(b.DispatchUnconfirmed):
                b.submit_completion_callback(self.path)
            send.assert_not_called()

    def test_hint_truncation_and_unknown_suffix(self):
        head = '› Ask Codex to do anything\nGPT-6 high\n'
        for suffix in ['', ' ⚠', ' ⚠ 5 warnings', ' ⚠ 5 warnings · f2 to view']:
            self.assertEqual(b.receiver_input_kind(head + '? for shortcuts' + suffix), 'AGENT_TUI')
        for suffix in [' arbitrary command', ' ⚠ 5 warnings · f3 to view']:
            self.assertEqual(b.receiver_input_kind(head + '? for shortcuts' + suffix), 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
