"""Real hook entrypoint tests using temporary markers and the original journal."""
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

import executor_closeout as closeout
import cmux_idle_pull as idle_pull_mod
import offline_test_hook


class CloseoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='closeout-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace, self.surface, self.supervisor = [str(uuid.uuid4()) for _ in range(3)]
        self.marker = dict(task_id='test-task', workspace_uuid=self.workspace,
                           artifact_root=str(self.root), participants=[
                               dict(role='supervisor', surface_ref='surface:1',
                                    surface_uuid=self.supervisor),
                               dict(role='executor', surface_uuid=self.surface)])
        self.active = self.root / 'active'
        self.marker_path = self.active / (self.workspace + '.json')
        self.marker_path.parent.mkdir(parents=True, exist_ok=True)
        self.write(self.marker_path, self.marker)
        self.addCleanup(self.marker_path.unlink, missing_ok=True)
        self.report = self.root / 'report.md'
        self.report.write_text('Bounded findings; known gaps remain.\n')
        self.pack_path = self.root / 'task-pack.json'
        self.receipt = self.root / 'receipt.json'
        self.pack = dict(draft=False, task_id='test-task', executor_uuid=self.surface,
                         completion_nonce='nonce', callback_target='surface:1',
                         completion_callback='DONE|test-task|nonce', report=str(self.report),
                         completion_receipt=str(self.receipt))
        self.write(self.pack_path, self.pack)
        self.journal = self.root / 'receipt-attempts'
        self.journal.mkdir()
        self.lock = self.journal / 'delivery.lock'
        self.lock.touch()
        self.attempt_path = self.journal / 'attempt-0001.json'
        self.attempt = dict(
            binding={**{k: self.pack[k] for k in ('task_id', 'completion_nonce',
                     'completion_callback', 'callback_target', 'report')},
                     'task_pack_sha256': self.sha(self.pack_path),
                     'report_sha256': self.sha(self.report), 'report_bytes': self.report.stat().st_size,
                     'identity': dict(workspace_uuid=self.workspace, caller_surface_uuid=self.surface,
                                      target_surface_uuid=self.supervisor, target_pane_uuid='pane-uuid')},
            phase='POST_ENTER_OBSERVATION', started_at_epoch=1.0, ended_at_epoch=3.0,
            events=[dict(phase='PASTE_INTENT', at_epoch=1.5),
                    dict(phase='POST_ENTER_OBSERVATION', at_epoch=2.0)],
            error='delivery detector unconfirmed', delivery_state='QUEUED')
        self.write(self.attempt_path, self.attempt)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), CMUX_WORKSPACE_ID=self.workspace,
                        CMUX_SURFACE_ID=self.surface)
        self.line = closeout.handoff_line(dict(task_id='test-task', report=str(self.report)))

    def write(self, path, value):
        path.write_text(json.dumps(value))

    def sha(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def evidence(self):
        return closeout.terminal_report(self.marker, self.workspace, self.surface)

    def hook(self, name, event, final=None, env=None):
        data = dict(hook_event_name=event, tool_name='Bash',
                    tool_input=dict(command='touch MUST_NOT_RUN'),
                    last_assistant_message=final or self.line, stop_hook_active=False)
        return subprocess.run(offline_test_hook.command(Path(__file__).with_name(name), self.active),
                              input=json.dumps(data), text=True, capture_output=True,
                              env=env or self.env, timeout=5)

    def idle_pull(self):
        # The exact command the closeout guard admits, run through the real
        # PreToolUse guard first, then executed with the same private HOME.
        cmd = idle_pull_mod.command_for(self.pack_path)
        data = dict(hook_event_name='PreToolUse', tool_name='Bash', tool_input=dict(command=cmd))
        gate = subprocess.run(offline_test_hook.command(Path(__file__).with_name('cmux_executor_closeout_guard.py'), self.active),
                              input=json.dumps(data), text=True, capture_output=True, env=self.env, timeout=5)
        self.assertEqual(gate.returncode, 0, gate.stderr)
        return subprocess.run(offline_test_hook.command(Path(__file__).with_name('cmux_idle_pull.py'), self.active,
                                                        '--task-pack', self.pack_path),
                              text=True, capture_output=True, env=self.env, timeout=5)

    def pre(self, **kwargs):
        return self.hook('cmux_executor_closeout_guard.py', 'PreToolUse', **kwargs)

    def stop(self, **kwargs):
        return self.hook('cmux_consensus_stop_guard.py', 'Stop', **kwargs)

    def test_terminal_unknown_seals_tools_allows_honest_stop_without_writing(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertIsNotNone(self.evidence())
        self.assertEqual(self.pre().returncode, 2)
        self.assertIn('EXECUTOR_CLOSEOUT', self.pre().stderr)
        # No silent wait: the honest handoff first requires a file-only idle request.
        r = self.stop()
        self.assertEqual(r.returncode, 2)
        self.assertIn('EXECUTOR_IDLE_PULL_REQUIRED', r.stderr)
        self.assertFalse(self.receipt.exists())
        self.assertTrue(self.marker_path.exists())
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertEqual(self.idle_pull().returncode, 0)
        self.assertEqual(self.stop().returncode, 0)
        self.assertFalse(self.receipt.exists())

    def test_unknown_cannot_claim_delivery_or_consensus(self):
        for text in ('DONE|test-task|nonce', 'callback confirmed', self.line + ' consensus-validation PASS'):
            with self.subTest(text=text):
                self.assertEqual(self.stop(final=text).returncode, 2)

    def test_no_input_return_can_handoff_without_mandatory_retry(self):
        self.attempt.update(phase='NO_INPUT', events=[])
        self.write(self.attempt_path, self.attempt)
        self.assertEqual(self.stop().returncode, 2)
        self.assertEqual(self.idle_pull().returncode, 0)
        self.assertEqual(self.stop().returncode, 0)
        self.assertEqual(self.pre().returncode, 2)

    def test_confirmed_return_still_prevents_new_work(self):
        self.attempt.update(phase='CONFIRMED', result=dict(confirmed=True))
        self.write(self.attempt_path, self.attempt)
        receipt = {k: v for k, v in self.attempt['binding'].items() if k != 'identity'}
        self.write(self.receipt, dict(receipt, confirmed=True))
        self.assertEqual(self.pre().returncode, 2)
        self.assertEqual(self.stop(final='DONE|test-task|nonce').returncode, 0)

    def test_inflight_lock_never_settled_by_old_end_timestamp(self):
        with self.lock.open('rb') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertIsNone(self.evidence())
            self.assertEqual(self.pre().returncode, 0)
            self.assertEqual(self.stop().returncode, 2)
        self.assertIsNotNone(self.evidence())

    def test_confirmed_receipt_does_not_accept_changed_or_unpinned_pack(self):
        self.attempt.update(phase='CONFIRMED', result=dict(confirmed=True))
        self.write(self.attempt_path, self.attempt)
        receipt = {k: v for k, v in self.attempt['binding'].items() if k != 'identity'}
        self.write(self.receipt, dict(receipt, confirmed=True))
        self.assertEqual(self.stop(final='DONE|test-task|nonce').returncode, 0)
        self.pack['scope'] = 'changed without a new callback'
        self.write(self.pack_path, self.pack)
        self.assertEqual(self.pre().returncode, 0)
        self.assertEqual(self.stop(final='DONE|test-task|nonce').returncode, 2)
        del receipt['task_pack_sha256']
        self.write(self.receipt, dict(receipt, confirmed=True))
        self.assertEqual(self.stop(final='DONE|test-task|nonce').returncode, 2)

    def test_missing_and_nonterminal_evidence(self):
        original = copy.deepcopy(self.attempt)
        cases = [dict(phase='PREPARED'), dict(ended_at_epoch=None), dict(ended_at_epoch=True),
                 dict(ended_at_epoch=float('nan')), dict(ended_at_epoch=1.2),
                 dict(phase='ENTER_INTENT'), dict(error=''), dict(events=None)]
        for delta in cases:
            with self.subTest(delta=delta):
                self.write(self.attempt_path, dict(original, **delta))
                self.assertIsNone(self.evidence())
                self.assertEqual(self.pre().returncode, 0)
                self.assertEqual(self.stop().returncode, 2)
        self.attempt_path.unlink()
        self.assertIsNone(self.evidence())

    def test_changed_report_and_pack_do_not_close(self):
        self.report.write_text('Changed after callback')
        self.assertIsNone(self.evidence())
        self.assertEqual(self.stop().returncode, 2)
        self.report.write_text('Bounded findings; known gaps remain.\n')
        self.pack['new_scope'] = 'unbound'
        self.write(self.pack_path, self.pack)
        self.assertIsNone(self.evidence())

    def test_wrong_binding_identity_and_task(self):
        for key in ('task_id', 'completion_nonce', 'completion_callback', 'callback_target',
                    'report', 'report_sha256', 'report_bytes', 'task_pack_sha256'):
            with self.subTest(key=key):
                bad = copy.deepcopy(self.attempt)
                bad['binding'][key] = 'wrong'
                self.write(self.attempt_path, bad)
                self.assertIsNone(self.evidence())
        for key in ('workspace_uuid', 'caller_surface_uuid', 'target_surface_uuid'):
            bad = copy.deepcopy(self.attempt)
            bad['binding']['identity'][key] = 'wrong'
            self.write(self.attempt_path, bad)
            self.assertIsNone(self.evidence())

    def test_supervisor_and_other_executor_are_not_sealed(self):
        for surface in (self.supervisor, 'other'):
            env = dict(self.env, CMUX_SURFACE_ID=surface)
            self.assertEqual(self.pre(env=env).returncode, 0)
            self.assertEqual(self.stop(env=env).returncode, 0)

    def test_stale_and_disarmed_markers_do_not_seal_tools(self):
        self.marker.update(armed_at='2000-01-01T00:00:00+00:00', ttl_seconds=1)
        self.write(self.marker_path, self.marker)
        self.assertEqual(self.pre().returncode, 0)
        self.marker_path.unlink()
        self.assertEqual(self.pre().returncode, 0)

    def test_latest_attempt_must_be_terminal(self):
        newer = dict(self.attempt, phase='PREPARED', ended_at_epoch=None)
        self.write(self.journal / 'attempt-0002.json', newer)
        self.assertIsNone(self.evidence())

    def test_screen_hash_and_resume_timestamp_validated(self):
        bad = copy.deepcopy(self.attempt)
        bad['events'][-1].update(screen='queued', screen_sha256='bad')
        self.write(self.attempt_path, bad)
        self.assertIsNone(self.evidence())
        bad['events'][-1]['screen_sha256'] = hashlib.sha256(b'queued').hexdigest()[:16]
        self.write(self.attempt_path, bad)
        self.assertIsNotNone(self.evidence())
        bad['events'][-1]['at_epoch'] = 4.0  # resumed after old ended_at
        self.write(self.attempt_path, bad)
        self.assertIsNone(self.evidence())

    def test_missing_lock_does_not_create_it(self):
        self.lock.unlink()
        self.assertIsNone(self.evidence())
        self.assertFalse(self.lock.exists())

    def test_wrong_hook_event_does_not_deny(self):
        result = self.hook('cmux_executor_closeout_guard.py', 'Stop')
        self.assertEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
