"""Stop guard lifecycle: a finalized pack is not a delivered task.

Real hook stdin/exit-code tests. HOME is private so the supervisor's
task-dispatch journal is a fixture written in the producer's exact shape.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid

import executor_closeout as closeout
import offline_test_hook

HOOK = Path(os.environ.get('STOP_GUARD_UNDER_TEST',
                           Path(__file__).with_name('cmux_consensus_stop_guard.py')))


class StopDispatchLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='stop-lifecycle-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        self.workspace, self.surface, self.supervisor = [str(uuid.uuid4()).upper() for _ in range(3)]
        self.marker = dict(task_id='life-task', workspace_uuid=self.workspace,
                           artifact_root=str(self.root), participants=[
                               dict(role='supervisor', surface_ref='surface:1',
                                    surface_uuid=self.supervisor),
                               dict(role='executor', surface_uuid=self.surface)])
        self.active = self.root / 'active'
        self.active.mkdir()
        self.write(self.active / (self.workspace + '.json'), self.marker)
        self.report = self.root / 'report.md'
        self.receipt = self.root / 'receipt.json'
        self.pack_path = self.root / 'task-pack.json'
        self.pack = dict(draft=False, task_id='life-task', executor_uuid=self.surface,
                         completion_nonce='nonce', callback_target='surface:1',
                         completion_callback='DONE|life-task|nonce', report=str(self.report),
                         completion_receipt=str(self.receipt))
        self.write(self.pack_path, self.pack)
        key = hashlib.sha256(json.dumps([self.supervisor, 'life-task']).encode()).hexdigest()
        self.dispatch = self.home / '.local/state/multi-agent-collaboration/task-dispatch-v1' / key
        self.env = dict(os.environ, HOME=str(self.home), CMUX_WORKSPACE_ID=self.workspace,
                        CMUX_SURFACE_ID=self.surface, PYTHONDONTWRITEBYTECODE='1')

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def sha(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def dispatch_attempt(self, phase, events=(), name='attempt-0001.json', **binding):
        """Producer shape from cmux_task_journal.deliver."""
        self.dispatch.mkdir(parents=True, exist_ok=True)
        (self.dispatch / 'delivery.lock').touch()
        value = dict(task_id='life-task', marker='nonce', payload_sha256='p' * 64,
                     task_pack_sha256=self.sha(self.pack_path),
                     identity=dict(workspace_uuid=self.workspace, caller_surface_uuid=self.supervisor,
                                   target_surface_uuid=self.surface, target_pane_uuid='pane'))
        value.update(binding)
        lock_stat = (self.dispatch / 'delivery.lock').stat()
        self.write(self.dispatch / name, dict(binding=value, phase=phase,
                                              delivery_lock_identity=dict(device=lock_stat.st_dev,
                                                                          inode=lock_stat.st_ino),
                                              events=[dict(phase=e, at_epoch=1.0) for e in events],
                                              started_at_epoch=1.0, ended_at_epoch=2.0,
                                              error='COMPOSE_OCCUPIED'))

    def stop(self, final='Status reply before the task arrived.', **extra):
        data = dict(hook_event_name='Stop', last_assistant_message=final, stop_hook_active=False)
        data.update(extra)
        return subprocess.run(offline_test_hook.command(HOOK, self.active), input=json.dumps(data),
                              text=True, capture_output=True, env=self.env, timeout=10)

    def snapshot(self):
        return {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    # finalized, never delivered -------------------------------------------------
    def test_finalized_explicit_no_input_allows_without_writing(self):
        self.dispatch_attempt('NO_INPUT')
        before = self.snapshot()
        r = self.stop()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.receipt.exists())

    def test_two_no_input_attempts_allow(self):
        self.dispatch_attempt('NO_INPUT')
        self.dispatch_attempt('NO_INPUT', name='attempt-0002.json')
        self.assertEqual(self.stop().returncode, 0)

    def test_no_input_does_not_license_evidence_claims(self):
        self.dispatch_attempt('NO_INPUT')
        self.assertEqual(self.stop(final='consensus-validation PASS').returncode, 2)

    # missing record is not "not started" ------------------------------------
    def test_finalized_without_dispatch_journal_blocks_as_unknown(self):
        r = self.stop()
        self.assertEqual(r.returncode, 2)
        self.assertIn('task dispatch state: UNKNOWN', r.stderr)

    def test_empty_journal_or_prepared_blocks(self):
        self.dispatch.mkdir(parents=True)
        (self.dispatch / 'delivery.lock').touch()
        self.assertIn('UNKNOWN', self.stop().stderr)
        self.dispatch_attempt('PREPARED')
        r = self.stop()
        self.assertEqual(r.returncode, 2)
        self.assertIn('SUBMITTED_OR_UNKNOWN', r.stderr)

    def test_missing_lock_and_malformed_attempt_block(self):
        self.dispatch_attempt('NO_INPUT')
        (self.dispatch / 'delivery.lock').unlink()
        self.assertEqual(self.stop().returncode, 2)
        self.assertFalse((self.dispatch / 'delivery.lock').exists())
        (self.dispatch / 'delivery.lock').touch()
        (self.dispatch / 'attempt-0001.json').write_text('{')
        self.assertEqual(self.stop().returncode, 2)

    # pasted / queued / unknown / consumed -----------------------------------
    def test_submitted_queued_or_later_attempt_blocks(self):
        cases = [('PASTED', ['PASTE_INTENT', 'PASTED']),
                 ('POST_ENTER_OBSERVATION', ['PASTE_INTENT', 'PASTED', 'ENTER_INTENT',
                                             'ENTER_SENT', 'POST_ENTER_OBSERVATION']),
                 ('POST_QUEUE_TAB_OBSERVATION', ['PASTE_INTENT', 'QUEUE_TAB_INTENT',
                                                 'POST_QUEUE_TAB_OBSERVATION']),
                 ('NO_INPUT', ['PASTE_INTENT'])]
        for phase, events in cases:
            with self.subTest(phase=phase, events=events):
                self.dispatch_attempt(phase, events)
                r = self.stop()
                self.assertEqual(r.returncode, 2)
                self.assertIn('SUBMITTED_OR_UNKNOWN', r.stderr)

    def test_no_input_then_submitted_retry_blocks(self):
        self.dispatch_attempt('NO_INPUT')
        self.dispatch_attempt('POST_ENTER_OBSERVATION', ['PASTE_INTENT'], name='attempt-0002.json')
        self.assertEqual(self.stop().returncode, 2)

    def test_confirmed_dispatch_receipt_blocks(self):
        self.dispatch_attempt('NO_INPUT')
        self.write(self.dispatch / 'receipt.json', dict(confirmed=True))
        r = self.stop()
        self.assertEqual(r.returncode, 2)
        self.assertIn('CONFIRMED_OR_RECONCILED', r.stderr)

    def test_in_flight_sender_lock_blocks(self):
        self.dispatch_attempt('NO_INPUT')
        with open(self.dispatch / 'delivery.lock', 'a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            r = self.stop()
        self.assertEqual(r.returncode, 2)
        self.assertIn('IN_FLIGHT', r.stderr)

    def test_binding_to_other_pack_executor_or_task_blocks(self):
        for change in (dict(task_pack_sha256='0' * 64), dict(task_id='other-task'),
                       dict(identity=dict(target_surface_uuid=str(uuid.uuid4())))):
            with self.subTest(change=list(change)):
                self.dispatch_attempt('NO_INPUT', **change)
                self.assertEqual(self.stop().returncode, 2)

    def test_pack_change_after_no_input_blocks(self):
        self.dispatch_attempt('NO_INPUT')
        self.write(self.pack_path, dict(self.pack, completion_nonce='new'))
        self.assertEqual(self.stop().returncode, 2)

    # executed but no callback / legal completion / terminal / reentry -------
    def test_executed_business_without_callback_blocks_even_if_dispatch_no_input(self):
        self.dispatch_attempt('NO_INPUT')
        self.report.write_text('work done\n')
        (self.root / 'receipt-attempts').mkdir()
        r = self.stop()
        self.assertEqual(r.returncode, 2)
        self.assertIn('CALLBACK_ATTEMPTED', r.stderr)

    def test_legal_confirmed_completion_receipt_allows(self):
        self.dispatch_attempt('POST_ENTER_OBSERVATION', ['PASTE_INTENT'])
        self.report.write_text('done\n')
        receipt = {k: self.pack[k] for k in ('task_id', 'completion_nonce',
                                              'completion_callback', 'callback_target')}
        receipt.update(report=str(self.report), report_sha256=self.sha(self.report),
                       report_bytes=self.report.stat().st_size, confirmed=True,
                       task_pack_sha256=self.sha(self.pack_path))
        self.write(self.receipt, receipt)
        self.assertEqual(self.stop().returncode, 0)
        self.write(self.receipt, dict(receipt, confirmed=False))
        self.assertEqual(self.stop().returncode, 2)

    def test_terminal_report_handoff_still_allowed_and_exact(self):
        self.dispatch_attempt('POST_ENTER_OBSERVATION', ['PASTE_INTENT'])
        self.report.write_text('bounded result\n')
        journal = self.root / 'receipt-attempts'
        journal.mkdir()
        (journal / 'delivery.lock').touch()
        self.write(journal / 'attempt-0001.json', dict(
            binding={**{k: self.pack[k] for k in ('task_id', 'completion_nonce',
                     'completion_callback', 'callback_target', 'report')},
                     'task_pack_sha256': self.sha(self.pack_path),
                     'report_sha256': self.sha(self.report), 'report_bytes': self.report.stat().st_size,
                     'identity': dict(workspace_uuid=self.workspace, caller_surface_uuid=self.surface,
                                      target_surface_uuid=self.supervisor, target_pane_uuid='pane')},
            phase='NO_INPUT', events=[], started_at_epoch=1.0, ended_at_epoch=2.0,
            error='COMPOSE_OCCUPIED'))
        line = closeout.handoff_line(dict(task_id='life-task', report=str(self.report)))
        self.assertEqual(self.stop(final=line).returncode, 0)
        r = self.stop(final=line + ' extra')
        self.assertEqual(r.returncode, 2)
        self.assertIn('End without more tools using exactly', r.stderr)

    def test_stop_hook_active_reentry_only_for_boolean_true(self):
        self.assertEqual(self.stop(stop_hook_active=True).returncode, 0)
        self.assertEqual(self.stop(stop_hook_active='true').returncode, 2)
        self.assertEqual(self.stop().returncode, 2)


if __name__ == '__main__':
    unittest.main()
