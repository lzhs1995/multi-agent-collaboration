import hashlib
import json
import tempfile
import unittest
import fcntl
from unittest.mock import patch, Mock
from pathlib import Path

from callback_native_evidence import validate, reconcile_received
from delivery_receipts import ReceiptError


class NativeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.packfile = self.root / 'pack.json'
        self.report = self.root / 'report.md'
        self.report.write_text('frozen report')
        self.gatefile = self.root / 'gate.json'
        self.gate = dict(status='PASS', task_id='task', supervisor_surface_uuid='SUP', workspace_uuid='WS')
        self.write(self.gatefile, self.gate)
        self.pack = dict(task_id='task', completion_nonce='nonce', callback_target='surface:1',
                         completion_callback='DONE|task|nonce|REPORT=' + str(self.report),
                         completion_receipt=str(self.root / 'receipt.json'), report=str(self.report),
                         identity_gate=str(self.gatefile), executor_uuid='EXEC',
                         finalized_at='2026-01-01T00:00:00+00:00')
        self.write(self.packfile, self.pack)
        journal = self.root / 'receipt-attempts'
        journal.mkdir()
        self.attemptfile = journal / 'attempt-0001.json'
        binding = {k: self.pack[k] for k in ('task_id', 'completion_nonce', 'callback_target',
                                            'completion_callback', 'report')}
        binding.update(task_pack_sha256=self.sha(self.packfile), report_sha256=self.sha(self.report),
                       report_bytes=self.report.stat().st_size,
                       identity=dict(caller_surface_uuid='EXEC', target_surface_uuid='SUP',
                                     workspace_uuid='WS', target_pane_uuid='PANE'))
        self.attempt = dict(binding=binding, events=[
            dict(phase='PASTE_INTENT', screen='idle', screen_sha256=hashlib.sha256(b'idle').hexdigest()[:16]),
            dict(phase='ENTER_INTENT', at_epoch=1767225601)])
        self.write(self.attemptfile, self.attempt)
        self.transcript = self.root / 'native.jsonl'
        self.record = dict(timestamp='2026-01-01T00:00:02Z', type='response_item',
                           payload=dict(type='message', role='user', content=[
                               dict(type='input_text', text=self.pack['completion_callback'])]))
        self.transcribe()

    def write(self, p, v):
        p.write_text(json.dumps(v))

    def sha(self, p):
        return hashlib.sha256(p.read_bytes()).hexdigest()

    def transcribe(self):
        self.transcript.write_text(json.dumps(dict(type='session_meta', payload=dict(id='session'))) +
                                   '\n' + json.dumps(self.record) + '\n')

    def check(self):
        return validate(self.packfile, self.attemptfile, self.transcript, 2, 'session')

    def test_exact_native_record_needs_no_visible_screen_and_creates_no_receipt(self):
        self.assertEqual(self.check()['status'], 'EXACT_NATIVE_RECEPTION')
        self.assertFalse(Path(self.pack['completion_receipt']).exists())

    def test_quote_or_tool_output_is_not_reception(self):
        for role in ('assistant', 'tool'):
            self.record['payload']['role'] = role
            self.transcribe()
            with self.assertRaises(ReceiptError): self.check()

    def test_truncated_or_extended_payload_is_not_exact(self):
        for text in ('DONE|task|nonce|…', self.pack['completion_callback'] + ' extra'):
            self.record['payload']['content'][0]['text'] = text
            self.transcribe()
            with self.assertRaises(ReceiptError): self.check()

    def test_pre_send_record_rejected(self):
        self.record['timestamp'] = '2026-01-01T00:00:00Z'
        self.transcribe()
        with self.assertRaises(ReceiptError): self.check()

    def test_changed_report_rejected(self):
        self.report.write_text('changed report')
        with self.assertRaises(ReceiptError): self.check()

    def test_changed_pack_rejected(self):
        self.packfile.write_text(self.packfile.read_text() + ' ')
        with self.assertRaises(ReceiptError): self.check()

    def test_wrong_original_sender_rejected(self):
        self.attempt['binding']['identity']['caller_surface_uuid'] = 'OTHER'
        self.write(self.attemptfile, self.attempt)
        with self.assertRaises(ReceiptError): self.check()

    def test_no_enter_or_duplicate_paste_rejected(self):
        original = self.attempt['events'][:]
        for events in ([original[0]], [original[0], original[0], original[1]]):
            self.attempt['events'] = events
            self.write(self.attemptfile, self.attempt)
            with self.assertRaises(ReceiptError): self.check()

    def test_newer_attempt_rejected(self):
        self.write(self.attemptfile.with_name('attempt-0002.json'), self.attempt)
        with self.assertRaises(ReceiptError): self.check()


class NativeSettlementTests(NativeEvidenceTests):
    def setUp(self):
        super().setUp()
        sessions = self.root / '.codex/sessions'
        sessions.mkdir(parents=True)
        self.transcript = sessions / 'native.jsonl'
        self.transcribe()
        self.lockfile = self.attemptfile.parent / 'delivery.lock'
        self.lockfile.touch()
        self.bridge = Mock()
        self.bridge.validate_task_pack_contract.return_value = self.pack
        self.live = dict(caller_surface_uuid='SUP', target_surface_uuid='EXEC', workspace_uuid='WS')
        for mock in (patch('pathlib.Path.home', return_value=self.root),
                     patch.dict('os.environ', {'CODEX_THREAD_ID': 'session'}),
                     patch('delivery_receipts.bind', return_value=self.live),
                     patch('availability_contract.require_action')):
            mock.start()
            self.addCleanup(mock.stop)

    def settle(self):
        return reconcile_received(self.packfile, self.bridge, transcript=self.transcript, line=2)

    def test_publish_preserves_attempt_and_lock(self):
        before = self.attemptfile.read_bytes()
        inode = self.lockfile.stat().st_ino
        receipt = self.settle()
        self.assertTrue(receipt['confirmed'])
        self.assertEqual(receipt['input_operations'], 0)
        self.assertEqual(before, self.attemptfile.read_bytes())
        self.assertEqual(inode, self.lockfile.stat().st_ino)
        self.assertEqual(self.bridge.method_calls, [unittest.mock.call.validate_task_pack_contract(self.packfile)])

    def test_active_sender_lock_rejects(self):
        with self.lockfile.open('rb') as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(ReceiptError): self.settle()
        self.assertFalse(Path(self.pack['completion_receipt']).exists())

    def test_wrong_receiver_rejects(self):
        self.live['caller_surface_uuid'] = 'OTHER'
        with self.assertRaises(ReceiptError): self.settle()

    def test_replaced_lock_during_validation_rejects_without_receipt(self):
        original = validate
        def replace_lock(*args, **kwargs):
            result = original(*args, **kwargs)
            self.lockfile.unlink()
            self.lockfile.touch()
            return result
        with patch('callback_native_evidence.validate', side_effect=replace_lock):
            with self.assertRaisesRegex(ReceiptError, 'lock was replaced'):
                self.settle()
        self.assertFalse(Path(self.pack['completion_receipt']).exists())

    def test_duplicate_settlement_preserves_receipt(self):
        self.settle()
        p = Path(self.pack['completion_receipt'])
        before = p.read_bytes()
        with self.assertRaises(ReceiptError): self.settle()
        self.assertEqual(before, p.read_bytes())

    def test_wrong_session_rejects(self):
        with patch.dict('os.environ', {'CODEX_THREAD_ID': 'other'}):
            with self.assertRaises(ReceiptError): self.settle()

    def test_missing_original_lock_never_creates_one(self):
        self.lockfile.unlink()
        with self.assertRaises(FileNotFoundError): self.settle()
        self.assertFalse(self.lockfile.exists())

    def read_receipt(self):
        from cmux_callback_journal import verified_receipt
        self.bridge.pin_workspace.return_value = self.attempt['binding']['identity']
        self.bridge.screen_hash.side_effect = lambda s: hashlib.sha256(s.encode()).hexdigest()[:16]
        return verified_receipt(self.bridge, 'surface:1', self.packfile)

    def test_reader_accepts_native_receipt_without_screen_or_input(self):
        self.settle()
        result = self.read_receipt()
        self.assertEqual(result['source'], 'revalidated_native_callback_journal')
        self.bridge.read_screen.assert_not_called()
        self.bridge.send_text.assert_not_called()
        self.bridge.send_key.assert_not_called()

    def test_reader_rejects_changed_native_record(self):
        self.settle()
        self.record['payload']['role'] = 'assistant'
        self.transcribe()
        self.assertIsNone(self.read_receipt())

    def test_reader_rejects_changed_report(self):
        self.settle()
        self.report.write_text('new report')
        self.assertIsNone(self.read_receipt())

    def test_reader_rejects_receipt_identity_or_evidence_tampering(self):
        self.settle()
        p = Path(self.pack['completion_receipt'])
        original = p.read_text()
        for field in ('receiver', 'hash', 'input'):
            r = json.loads(original)
            if field == 'receiver': r['receiver_identity']['caller_surface_uuid'] = 'OTHER'
            elif field == 'hash': r['native_evidence']['native']['record_sha256'] = '0' * 64
            else: r['input_operations'] = True
            self.write(p, r)
            with self.subTest(field=field): self.assertIsNone(self.read_receipt())


if __name__ == '__main__':
    unittest.main()
