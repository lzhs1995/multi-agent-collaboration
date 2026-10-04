import contextlib
import fcntl
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import cmux_bridge as b

IDLE = '› Ask Codex to do anything\nGPT-6-Astra high\n? for shortcuts  ⚠ 5 warnings · f2 to view'


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        report = root / 'REPORT.md'
        report.write_text('real report')
        self.packpath = root / 'task-pack.json'
        self.receipt = root / 'completion-callback-receipt.json'
        self.pack = dict(task_id='test', completion_nonce='nonce12345',
                         report=str(report), callback_target='surface:46',
                         completion_receipt=str(self.receipt), executor_uuid='EXECUTOR')
        self.pack['completion_callback'] = f'DONE|test|nonce12345|REPORT={report}'
        self.packpath.write_text(json.dumps(self.pack))
        self.proof = dict(workspace_uuid='WS', caller_surface_uuid='EXECUTOR',
                          target_surface_uuid='SUPERVISOR', target_pane_uuid='PANE')
        for p in (patch.object(b, 'validate_task_pack_contract', return_value=self.pack),
                  patch('availability_contract.require_action'),
                  patch.object(b, 'pin_workspace', return_value=self.proof),
                  patch.object(b.time, 'sleep')):
            p.start()
            self.addCleanup(p.stop)
        self.journal = self.receipt.with_name(self.receipt.stem + '-attempts')

    def confirmed_screen(self):
        return '› ' + self.pack['completion_callback'] + '\n• Read report\n' + IDLE

    def call(self, **kw):
        return b.submit_completion_callback(str(self.packpath), **kw)

    def queued(self):
        screen = 'Messages to be submitted after current tool\n' + self.pack['completion_callback'] + '\n' + IDLE
        with patch.object(b, 'read_screen', side_effect=[IDLE, screen]), \
                patch.object(b, 'send_text') as send, patch.object(b, 'send_key') as key:
            with self.assertRaises(b.DispatchUnconfirmed):
                self.call()
            send.assert_called_once()
            key.assert_called_once()

    def test_confirmed_report_hash_and_duplicate_refusal(self):
        with patch.object(b, 'read_screen', side_effect=[IDLE, self.confirmed_screen()]), \
                patch.object(b, 'send_text') as send, patch.object(b, 'send_key') as key:
            result = self.call()
            self.assertTrue(result['confirmed'])
            self.assertEqual(result['report_sha256'], b._sha256_file(self.pack['report']))
            with self.assertRaises(b.TaskPackContractError):
                self.call()
            send.assert_called_once()
            key.assert_called_once()

    def test_zero_input_persists_and_one_explicit_retry(self):
        with patch.object(b, 'read_screen', return_value='host@mac ~ %'), \
                patch.object(b, 'send_text') as send, patch.object(b, 'send_key') as key:
            for _ in range(2):
                with self.assertRaises(b.DispatchUnconfirmed):
                    self.call()
            with self.assertRaises(b.TaskPackContractError):
                self.call()
            self.assertEqual(json.loads((self.journal / 'attempt-0001.json').read_text())['phase'], 'NO_INPUT')
            send.assert_not_called()
            key.assert_not_called()

    def test_queued_never_repasted_and_read_only_reconcile(self):
        self.queued()
        with patch.object(b, 'read_screen', return_value=self.confirmed_screen()), \
                patch.object(b, 'send_text') as send, patch.object(b, 'send_key') as key:
            with self.assertRaises(b.TaskPackContractError):
                self.call()
            result = self.call(reconcile_only=True)
            self.assertTrue(result['reconciled_read_only'])
            send.assert_not_called()
            key.assert_not_called()

    def test_pending_compose_unknown_and_queue_cannot_reconcile(self):
        self.queued()
        for screen in ['› '+self.pack['completion_callback']+'\nGPT-6 high',
                       'Messages to be submitted after current tool\n'+self.confirmed_screen(),
                       'render missing', IDLE, '• Read nonce12345 unrelated output\n'+IDLE]:
            with self.subTest(screen=screen), patch.object(b, 'read_screen', return_value=screen), \
                    patch.object(b, 'send_text') as send, patch.object(b, 'send_key') as key:
                with self.assertRaises(b.DispatchUnconfirmed):
                    self.call(reconcile_only=True)
                send.assert_not_called()
                key.assert_not_called()
        self.assertFalse(self.receipt.exists())

    def test_partial_or_cross_block_callback_cannot_reconcile(self):
        self.queued()
        callback = self.pack['completion_callback']
        screens = [
            '› nonce12345\n• Read report\n' + IDLE,
            '› ' + callback + '\n› unrelated\n• Read report\n' + IDLE,
            '› ' + callback + ' extra\n• Read report\n' + IDLE,
        ]
        for screen in screens:
            with self.subTest(screen=screen), patch.object(b, 'read_screen', return_value=screen), \
                    patch.object(b, 'send_text') as send, patch.object(b, 'send_key') as key:
                with self.assertRaises(b.DispatchUnconfirmed):
                    self.call(reconcile_only=True)
                send.assert_not_called()
                key.assert_not_called()
        self.assertFalse(self.receipt.exists())

    def test_report_change_refuses_reconcile(self):
        self.queued()
        Path(self.pack['report']).write_text('different report')
        with patch.object(b, 'send_text') as send:
            with self.assertRaises(b.TaskPackContractError):
                self.call(reconcile_only=True)
            send.assert_not_called()

    def test_original_executor_only(self):
        self.proof['caller_surface_uuid'] = 'OTHER'
        with patch.object(b, 'send_text') as send:
            with self.assertRaises(b.TaskPackContractError):
                self.call()
            send.assert_not_called()

    def test_reconcile_cannot_invent_legacy_attempt(self):
        with patch.object(b, 'read_screen', return_value=self.confirmed_screen()), \
                patch.object(b, 'send_text') as send:
            with self.assertRaises(b.TaskPackContractError):
                self.call(reconcile_only=True)
            send.assert_not_called()

    def test_concurrent_callback_lock_refuses_input(self):
        self.journal.mkdir()
        with (self.journal / 'delivery.lock').open('a+b') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(b, 'send_text') as send:
                with self.assertRaises(b.TaskPackContractError):
                    self.call()
                send.assert_not_called()

    def test_crash_after_paste_intent_requires_reconciliation(self):
        with patch.object(b, 'read_screen', return_value=IDLE), \
                patch.object(b, 'send_text', side_effect=RuntimeError('lost reply')):
            with self.assertRaises(RuntimeError):
                self.call()
        with patch.object(b, 'send_text') as send:
            with self.assertRaises(b.TaskPackContractError):
                self.call()
            send.assert_not_called()

    def test_legacy_pending_never_resends_or_fabricates_receipt(self):
        legacy = Path(str(self.receipt) + '.pending.json')
        for content in ('{}', '{truncated'):
            legacy.write_text(content)
            for reconcile in (False, True):
                with self.subTest(content=content, reconcile=reconcile), \
                        patch.object(b, 'send_text') as send, \
                        patch.object(b, 'send_key') as key:
                    with self.assertRaisesRegex(b.TaskPackContractError, 'LEGACY_CALLBACK_PENDING'):
                        self.call(reconcile_only=reconcile)
                    send.assert_not_called()
                    key.assert_not_called()
                    self.assertFalse(self.receipt.exists())
                    self.assertEqual(legacy.read_text(), content)

    def test_cli_read_only_flag(self):
        with patch.object(b, 'submit_completion_callback', return_value={}) as call, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(b._cli_main(['submit-completion-callback', '--task-pack', str(self.packpath), '--reconcile-only']), 0)
            self.assertTrue(call.call_args.kwargs['reconcile_only'])


if __name__ == '__main__':
    unittest.main()
