"""Executor idle pull: real hook subprocesses, private HOME and marker dir."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid

import cmux_idle_pull as idle
import offline_test_hook

HERE = Path(__file__).resolve().parent


class IdlePullTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='idle-pull-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.workspace, self.executor, self.supervisor = [str(uuid.uuid4()).upper() for _ in range(3)]
        self.art = self.root / 'task'
        self.art.mkdir()
        self.active = self.root / 'active'
        self.active.mkdir()
        self.marker = dict(task_id='idle-task', workspace_uuid=self.workspace, artifact_root=str(self.art),
                           participants=[dict(role='supervisor', surface_ref='surface:1',
                                              surface_uuid=self.supervisor),
                                         dict(role='executor', surface_uuid=self.executor)])
        (self.active / self.workspace).mkdir()
        (self.active / self.workspace / 'm.json').write_text(json.dumps(self.marker))
        self.report = self.art / 'executor-report.md'
        self.receipt = self.art / 'completion-callback-receipt.json'
        self.pack = self.art / 'task-pack.json'
        self.pack.write_text(json.dumps(dict(draft=False, task_id='idle-task', executor_uuid=self.executor,
                                             report=str(self.report), completion_receipt=str(self.receipt))))

    def env(self, surface):
        return dict(os.environ, HOME=str(self.home), CMUX_WORKSPACE_ID=self.workspace,
                    CMUX_SURFACE_ID=surface, PYTHONDONTWRITEBYTECODE='1')

    def run_hook(self, name, data, surface):
        return subprocess.run(offline_test_hook.command(HERE / name, self.active), input=json.dumps(data),
                              text=True, capture_output=True, env=self.env(surface), timeout=10)

    def cli(self, *args, surface=None):
        return subprocess.run(offline_test_hook.command(HERE / 'cmux_idle_pull.py', self.active, *args),
                              text=True, capture_output=True, env=self.env(surface or self.executor), timeout=10)

    def callback_attempted(self):
        self.report.write_text('bounded result\n')
        (self.art / 'completion-callback-receipt-attempts').mkdir()
        (self.art / 'completion-callback-receipt-attempts' / 'attempt-0001.json').write_text('{}')

    def sup_stop(self, surface=None, **extra):
        data = dict(hook_event_name='Stop', last_assistant_message='status', stop_hook_active=False)
        data.update(extra)
        return self.run_hook('cmux_consensus_stop_guard.py', data, surface or self.supervisor)

    def inbox(self):
        return self.home / '.local/state/multi-agent-collaboration/idle-requests-v1' / self.workspace

    # executor recording ---------------------------------------------------
    def test_record_requires_report_and_original_callback_attempt(self):
        r = self.cli('--task-pack', self.pack)
        self.assertEqual(r.returncode, 2)
        self.report.write_text('bounded\n')
        r = self.cli('--task-pack', self.pack)
        self.assertEqual(r.returncode, 2)
        self.assertIn('original callback attempt required', r.stderr)
        self.assertFalse(self.inbox().exists())

    def test_record_is_file_only_and_bound(self):
        self.callback_attempted()
        r = self.cli('--task-pack', self.pack)
        self.assertEqual(r.returncode, 0, r.stderr)
        req = json.loads((self.inbox() / (self.executor + '.json')).read_text())
        self.assertEqual((req['executor_uuid'], req['supervisor_uuid'], req['workspace_uuid'], req['last_task_id']),
                         (self.executor, self.supervisor, self.workspace, 'idle-task'))
        self.assertFalse(req['terminal_input_sent'])
        self.assertTrue((self.art / 'executor-idle-request.json').is_file())

    # closeout guard admission ----------------------------------------------
    def test_closeout_command_is_exact(self):
        good = idle.command_for(self.pack)
        self.assertTrue(good.startswith('rtk proxy ') and str(self.pack) in good)
        for bad in (good + ' ; touch X', good + ' && true', good.replace(str(self.pack), str(self.art / 'x.json')),
                    good.replace('rtk proxy ', '')):
            with self.subTest(bad=bad):
                self.assertNotEqual(bad, good)

    # supervisor Stop ----------------------------------------------------------
    def test_supervisor_stop_blocks_until_ack(self):
        self.callback_attempted()
        self.assertEqual(self.cli('--task-pack', self.pack).returncode, 0)
        r = self.sup_stop()
        self.assertEqual(r.returncode, 2)
        self.assertIn('EXECUTOR_IDLE_REQUEST_PENDING', r.stderr)
        self.assertIn('--ack', r.stderr)
        empty = self.cli('--ack', self.executor, '--workspace', self.workspace,
                         '--supervisor', self.supervisor, '--reason', ' ', surface=self.supervisor)
        self.assertEqual(empty.returncode, 2)
        ok = self.cli('--ack', self.executor, '--workspace', self.workspace, '--supervisor', self.supervisor,
                      '--reason', 'WAITING_DEPENDENCY: root owned save', surface=self.supervisor)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(self.sup_stop().returncode, 0)

    def test_new_request_reopens_after_old_ack(self):
        self.callback_attempted()
        self.cli('--task-pack', self.pack)
        self.cli('--ack', self.executor, '--workspace', self.workspace, '--supervisor', self.supervisor,
                 '--reason', 'later', surface=self.supervisor)
        self.assertEqual(self.sup_stop().returncode, 0)
        self.report.write_text('newer bounded result\n')
        self.cli('--task-pack', self.pack)
        self.assertEqual(self.sup_stop().returncode, 2)

    def test_dispatch_after_request_settles_but_older_does_not(self):
        self.callback_attempted()
        self.cli('--task-pack', self.pack)
        at = json.loads((self.inbox() / (self.executor + '.json')).read_text())['at_epoch']
        journal = self.home / '.local/state/multi-agent-collaboration/task-dispatch-v1/k'
        journal.mkdir(parents=True)
        ident = dict(workspace_uuid=self.workspace, caller_surface_uuid=self.supervisor,
                     target_surface_uuid=self.executor, target_pane_uuid='p')
        (journal / 'attempt-0001.json').write_text(json.dumps(dict(binding=dict(identity=ident),
                                                                    started_at_epoch=at - 10)))
        self.assertEqual(self.sup_stop().returncode, 2)
        (journal / 'attempt-0002.json').write_text(json.dumps(dict(binding=dict(identity=ident),
                                                                    started_at_epoch=at + 1)))
        self.assertEqual(self.sup_stop().returncode, 0)

    def test_other_supervisor_and_executor_are_not_blocked(self):
        self.callback_attempted()
        self.cli('--task-pack', self.pack)
        os.unlink(self.active / self.workspace / 'm.json')  # isolate the inbox rule
        self.assertEqual(self.sup_stop(surface=str(uuid.uuid4()).upper()).returncode, 0)
        self.assertEqual(self.sup_stop(surface=self.executor).returncode, 0)

    def test_malformed_request_stays_pending_and_reentry_passes(self):
        self.inbox().mkdir(parents=True)
        (self.inbox() / (self.executor + '.json')).write_text('{')
        r = self.sup_stop()
        self.assertEqual(r.returncode, 2)
        self.assertIn('malformed', r.stderr)
        self.assertEqual(self.sup_stop(stop_hook_active=True).returncode, 0)

    def test_ack_by_other_supervisor_refused(self):
        self.callback_attempted()
        self.cli('--task-pack', self.pack)
        other = str(uuid.uuid4()).upper()
        r = self.cli('--ack', self.executor, '--workspace', self.workspace, '--supervisor', other,
                     '--reason', 'x', surface=other)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.sup_stop().returncode, 2)


if __name__ == '__main__':
    unittest.main()
