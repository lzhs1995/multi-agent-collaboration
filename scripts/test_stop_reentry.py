"""Real stdin/exit-code tests; no native session or transport is invoked."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

HOOK = Path(os.environ.get('STOP_GUARD_UNDER_TEST', Path(__file__).with_name('cmux_consensus_stop_guard.py')))


class StopReentryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='stop-reentry-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace = 'stop-reentry-test-' + uuid.uuid4().hex
        self.surface = uuid.uuid4().hex
        self.marker = Path('/tmp/multi-agent-collaboration/_active') / (self.workspace + '.json')
        self.marker.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(self.marker.unlink, missing_ok=True)
        self.marker.write_text(json.dumps(dict(task_id='offline-test', artifact_root=str(self.root), participants=[dict(role='executor', surface_uuid=self.surface)])))
        self.report = self.root / 'report.md'
        self.report.write_text('Offline report.\n')
        self.pack = dict(draft=False, task_id='offline-test', completion_nonce='test-nonce', completion_callback='DONE|offline-test', callback_target='supervisor', report=str(self.report), completion_receipt=str(self.root / 'receipt.json'))
        (self.root / 'task-pack.json').write_text(json.dumps(self.pack))
        self.env = dict(os.environ, CMUX_WORKSPACE_ID=self.workspace, CMUX_SURFACE_ID=self.surface, PYTHONDONTWRITEBYTECODE='1')

    def call(self, payload, check_file=False):
        data = dict(hook_event_name='Stop', last_assistant_message='Report written; callback unconfirmed.')
        data.update(payload)
        command = [sys.executable, '-B', str(HOOK)]
        if check_file:
            path = self.root / 'input.json'
            path.write_text(json.dumps(data))
            command += ['--check-file', str(path)]
        return subprocess.run(command, input=json.dumps(data), text=True, capture_output=True, env=self.env, timeout=5)

    def snapshot(self):
        return {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()} | {str(self.marker): self.marker.read_bytes()}

    def test_first_stop_missing_callback_still_blocks(self):
        r = self.call(dict(stop_hook_active=False))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn('completion callback receipt missing', r.stderr)

    def test_legacy_first_stop_missing_flag_still_blocks(self):
        self.assertEqual(self.call({}).returncode, 2)

    def test_stop_reentry_returns_success_without_mutating_evidence(self):
        before = self.snapshot()
        r = self.call(dict(stop_hook_active=True))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((r.stdout, r.stderr), ('', ''))
        self.assertEqual(self.snapshot(), before)

    def test_subagent_stop_reentry(self):
        r = self.call(dict(stop_hook_active=True, hook_event_name='SubagentStop'))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_wrong_types_cannot_claim_reentry(self):
        for flag in ('true', 'false', 1, 0, None, [], {}):
            with self.subTest(flag=flag):
                self.assertEqual(self.call(dict(stop_hook_active=flag)).returncode, 2)

    def test_other_events_do_not_get_reentry_exemption(self):
        for event in ('PreToolUse', '', None):
            with self.subTest(event=event):
                self.assertEqual(self.call(dict(stop_hook_active=True, hook_event_name=event)).returncode, 2)

    def test_next_normal_turn_is_checked_again(self):
        self.assertEqual(self.call(dict(stop_hook_active=True)).returncode, 0)
        self.assertEqual(self.call(dict(stop_hook_active=False)).returncode, 2)

    def test_valid_callback_passes_but_report_drift_blocks(self):
        receipt = {k: self.pack[k] for k in ('task_id', 'completion_nonce', 'completion_callback', 'callback_target', 'report')}
        receipt.update(confirmed=True, report_sha256=hashlib.sha256(self.report.read_bytes()).hexdigest(), report_bytes=self.report.stat().st_size)
        Path(self.pack['completion_receipt']).write_text(json.dumps(receipt))
        self.assertEqual(self.call(dict(stop_hook_active=False)).returncode, 0)
        self.report.write_text('Changed report\n')
        self.assertEqual(self.call(dict(stop_hook_active=False)).returncode, 2)

    def test_false_consensus_still_blocks_first_stop(self):
        (self.root / 'task-pack.json').unlink()
        self.assertEqual(self.call(dict(stop_hook_active=False, last_assistant_message='consensus-validation PASS')).returncode, 2)

    def test_unarmed_passes(self):
        self.marker.unlink()
        self.assertEqual(self.call(dict(stop_hook_active=False)).returncode, 0)

    def test_check_file_uses_same_reentry_rule(self):
        r = self.call(dict(stop_hook_active=True), check_file=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('ALLOW:', r.stdout)


if __name__ == '__main__':
    unittest.main()
