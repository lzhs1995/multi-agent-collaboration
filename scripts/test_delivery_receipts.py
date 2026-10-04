import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import delivery_receipts as D
from cmux_delivery_evidence import digest


class LateReceiptTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        sessions = self.root / '.codex/sessions'
        sessions.mkdir(parents=True)
        self.log = sessions / 'test.jsonl'
        self.report = self.root / 'report.md'
        self.report.write_text('review\n')
        self.gate = self.root / 'gate.json'
        self.gate.write_text('{}')
        self.path = self.root / 'task-pack.json'
        self.pack = dict(task_id='task-test', completion_nonce='nonce-123',
                         report=str(self.report), identity_gate=str(self.gate),
                         completion_receipt=str(self.root/'receipt.json'),
                         completion_callback='DONE|task-test|nonce-123|REPORT='+str(self.report),
                         callback_target='surface:1', finalized_at='2026-10-04T10:00:00Z')
        self.path.write_text(json.dumps(self.pack))
        self.event = dict(type='response_item', timestamp='2026-10-04T10:01:00Z',
                          payload=dict(type='message', role='user', content=[
                              dict(type='input_text', text=self.pack['completion_callback'])]))
        self.write_log()
        self.bridge = SimpleNamespace(validate_task_pack_contract=lambda _: dict(self.pack))
        for patch in [mock.patch.object(Path, 'home', return_value=self.root),
                      mock.patch.dict(os.environ, {'CODEX_THREAD_ID':'native-session'}),
                      mock.patch('availability_contract.require_action'),
                      mock.patch.object(D, 'bind', return_value=dict(workspace_uuid='ws', caller_surface_uuid='supervisor', target_surface_uuid='executor'))]:
            patch.start(); self.addCleanup(patch.stop)

    def write_log(self, session='native-session'):
        self.log.write_text(json.dumps(dict(type='session_meta', payload=dict(id=session)))+'\n'+json.dumps(self.event)+'\n')

    def ack(self):
        return D.acknowledge_received_callback(self.path, self.bridge, transcript=self.log,
                    line=2, received_callback=self.pack['completion_callback'])

    def prepare(self):
        root = self.root/'.local/state/multi-agent-collaboration/deliveries-v1'
        root.mkdir(parents=True, exist_ok=True)
        self.attempt_path = root/(digest(json.dumps(['executor',self.pack['completion_nonce']],sort_keys=True))+'.json')
        self.attempt = dict(version=1, marker=self.pack['completion_nonce'],
                            identity=dict(workspace_uuid='ws', caller_surface_uuid='executor', target_surface_uuid='supervisor', target_pane_uuid='pane'),
                            payload_sha256=digest(self.pack['completion_callback']), paste_intent=True,
                            enter_attempts=2, queue_key_attempted=True,
                            binding={str(self.path.resolve()):D.snapshot(self.path)['sha256'],str(self.report):D.snapshot(self.report)['sha256']})
        self.attempt_path.write_text(json.dumps(self.attempt))

    def test_real_user_record_closes_legacy_once_without_send(self):
        receipt = self.ack()
        self.assertEqual(receipt['confirmation_source'], 'supervisor_native_user_record')
        self.assertTrue(receipt['evidence']['legacy_without_attempt'])
        self.assertEqual(self.ack(), receipt)

    def test_assistant_or_tool_echo_cannot_close(self):
        for role in ['assistant', 'tool']:
            self.event['payload']['role'] = role; self.write_log()
            with self.assertRaises(D.ReceiptError): self.ack()

    def test_queued_event_is_not_user_receipt(self):
        self.event['type'] = 'event_msg'; self.write_log()
        with self.assertRaises(D.ReceiptError): self.ack()

    def test_quoted_callback_is_not_exact_receipt(self):
        self.event['payload']['content'][0]['text'] += '\nquoted earlier callback'
        self.write_log()
        with self.assertRaises(D.ReceiptError): self.ack()

    def test_wrong_session_refused(self):
        self.write_log('other-session')
        with self.assertRaises(D.ReceiptError): self.ack()

    def test_old_record_refused(self):
        self.event['timestamp'] = '2026-10-03T10:00:00Z'; self.write_log()
        with self.assertRaises(D.ReceiptError): self.ack()

    def test_changed_report_after_attempt_refused(self):
        self.prepare()
        self.report.write_text('changed')
        with self.assertRaises(D.ReceiptError): self.ack()

    def test_valid_original_attempt_closes(self):
        self.prepare()
        self.assertFalse(self.ack()['evidence']['legacy_without_attempt'])

    def test_boolean_version_refused(self):
        self.prepare()
        self.attempt['version'] = True
        self.attempt_path.write_text(json.dumps(self.attempt))
        with self.assertRaises(D.ReceiptError): self.ack()
        self.assertFalse(Path(self.pack['completion_receipt']).exists())

    def test_other_journal_formats_not_silently_ignored(self):
        receipt = Path(self.pack['completion_receipt'])
        for path in [receipt.with_name(receipt.stem + '-attempts'),
                     Path(str(receipt) + '.pending.json')]:
            path.write_text('preserve original evidence')
            with self.assertRaisesRegex(D.ReceiptError, 'original reconciliation controller'):
                self.ack()
            self.assertFalse(receipt.exists())
            self.assertEqual(path.read_text(), 'preserve original evidence')
            path.unlink()

    def test_ack_never_resets_sender_budget(self):
        self.prepare()
        before = self.attempt_path.read_bytes()
        self.ack(); self.ack()
        self.assertEqual(self.attempt_path.read_bytes(), before)

    def test_native_ack_waits_for_original_sender_lock(self):
        self.prepare()
        before = self.attempt_path.read_bytes()
        with D.delivery_attempt_lock(self.attempt_path):
            with self.assertRaisesRegex(D.ReceiptError, 'in progress'):
                self.ack()
        self.assertFalse(Path(self.pack['completion_receipt']).exists())
        self.assertEqual(self.attempt_path.read_bytes(), before)
        self.assertTrue(self.ack()['confirmed'])

    def test_wrong_sender_and_nonce_refused(self):
        for field, value in [('marker','other'), ('payload_sha256','other'), ('binding',{})]:
            self.prepare()
            self.attempt[field] = value
            self.attempt_path.write_text(json.dumps(self.attempt))
            with self.assertRaises(D.ReceiptError): self.ack()

    def test_final_revocation_refuses_publication(self):
        with mock.patch('availability_contract.require_action', side_effect=[None, PermissionError('revoked')]):
            with self.assertRaises(PermissionError): self.ack()
        self.assertFalse(Path(self.pack['completion_receipt']).exists())

    def test_identity_drift_at_final_check_refused(self):
        with mock.patch.object(D, 'bind', side_effect=[dict(workspace_uuid='ws', caller_surface_uuid='supervisor', target_surface_uuid='executor'), {'caller':'other'}]):
            with self.assertRaises(D.ReceiptError): self.ack()
        self.assertFalse(Path(self.pack['completion_receipt']).exists())

    def test_existing_foreign_receipt_preserved(self):
        receipt = Path(self.pack['completion_receipt']); receipt.write_text('{}')
        with self.assertRaises(D.ReceiptError): self.ack()
        self.assertEqual(receipt.read_text(), '{}')

    def test_partial_write_never_publishes(self):
        with mock.patch.object(D.os, 'fsync', side_effect=OSError('disk')):
            with self.assertRaises(OSError): self.ack()
        self.assertFalse(Path(self.pack['completion_receipt']).exists())

    def test_publish_exclusive_preserves_old_bytes(self):
        path = self.root/'new.json'; D.publish(path, {'one':1})
        with self.assertRaises(FileExistsError): D.publish(path, {'two':2})
        self.assertEqual(json.loads(path.read_text()), {'one':1})


if __name__ == '__main__':
    unittest.main()
