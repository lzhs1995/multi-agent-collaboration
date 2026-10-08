"""Real Stop-hook entrypoint tests for the executor idle ready guard."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

import cmux_bridge
import executor_ready
import offline_test_hook

GUARD = Path(__file__).with_name('cmux_executor_idle_guard.py')


class IdleGuardTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='idle-guard-test-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / 'home'
        self.state = self.home / '.local/state/multi-agent-collaboration'
        self.active = self.root / 'active'
        self.workspace, self.surface, self.supervisor = [str(uuid.uuid4()) for _ in range(3)]
        self.marker_path = self.active / self.workspace / 'collab.json'
        self.env = dict(os.environ, HOME=str(self.home), CMUX_WORKSPACE_ID=self.workspace,
                        CMUX_SURFACE_ID=self.surface)
        patcher = patch.object(executor_ready, 'state_root', return_value=self.state)
        patcher.start()
        self.addCleanup(patcher.stop)

    def arm(self, executor=None):
        self.marker_path.parent.mkdir(parents=True, exist_ok=True)
        self.marker_path.write_text(json.dumps(dict(
            task_id='test-task', workspace_uuid=self.workspace, participants=[
                dict(role='supervisor', surface_ref='surface:1', surface_uuid=self.supervisor),
                dict(role='executor', surface_uuid=executor or self.surface)])))

    def stop(self, reentry=False, event='Stop'):
        data = dict(hook_event_name=event, stop_hook_active=reentry, last_assistant_message='idle')
        return subprocess.run(offline_test_hook.command(GUARD, self.active), input=json.dumps(data),
                              text=True, capture_output=True, env=self.env, timeout=10)

    def binding(self):
        files = list((self.state / 'executor-idle-v1' / 'bindings').glob('*.json'))
        return json.loads(files[0].read_text()) if files else None

    def ready(self, caller=None, paste_offset=1.0, phase='POST_ENTER_OBSERVATION',
              marker=None, tamper=False):
        marker = marker or executor_ready.PREFIX + '_' + uuid.uuid4().hex[:8]
        text = executor_ready.build_text('surface:2', marker, 'test-task')
        path, record = executor_ready.write_record(self.surface, marker, text, 'surface:1')
        journal = self.state / 'message-dispatch-v1' / executor_ready.request_key(self.surface, marker)
        journal.mkdir(parents=True, exist_ok=True)
        identity = dict(workspace_uuid=self.workspace, caller_surface_uuid=caller or self.surface,
                        target_surface_uuid=self.supervisor, target_pane_uuid='pane')
        sha = record['payload_sha256'] if not tamper else '0' * 64
        attempt = dict(binding=dict(identity=identity, marker=marker, payload_sha256=sha), phase=phase,
                       events=[dict(phase='PASTE_INTENT', at_epoch=time.time() + paste_offset)])
        (journal / 'attempt-0001.json').write_text(json.dumps(attempt))
        return marker

    def disarmed(self):
        self.arm()
        self.assertEqual(self.stop().returncode, 0)
        self.assertEqual(self.binding()['state'], 'BOUND')
        self.marker_path.unlink()

    def test_unbound_executor_and_other_events_pass_without_state(self):
        for event in ('Stop', 'PreToolUse', 'SubagentStop'):
            self.assertEqual(self.stop(event=event).returncode, 0)
        self.arm(executor=str(uuid.uuid4()))
        self.assertEqual(self.stop().returncode, 0)
        self.assertIsNone(self.binding())
        run = subprocess.run(offline_test_hook.command(GUARD, self.active), input='{}', text=True,
                             capture_output=True, env=self.env, timeout=10)
        self.assertEqual(run.returncode, 0)

    def test_disarmed_executor_is_told_to_request_with_finite_budget(self):
        self.disarmed()
        for _ in range(executor_ready_budget()):
            run = self.stop()
            self.assertEqual(run.returncode, 2)
            self.assertIn('EXECUTOR_IDLE_READY_REQUIRED', run.stderr)
            self.assertIn('executor_ready.py request --supervisor surface:1 --task test-task', run.stderr)
        self.assertEqual(self.stop().returncode, 0)
        self.assertEqual(self.binding()['state'], 'IDLE_RELEASED_WITHOUT_REQUEST')
        self.assertEqual(self.stop().returncode, 0)

    def test_reentry_always_terminates(self):
        self.disarmed()
        self.assertEqual(self.stop(reentry=True).returncode, 0)
        self.assertEqual(self.binding()['reminders'], 0)

    def test_journaled_request_after_binding_satisfies_guard(self):
        self.disarmed()
        marker = self.ready()
        self.assertEqual(self.stop().returncode, 0)
        self.assertEqual(self.binding()['state'], 'IDLE_REQUESTED')
        self.assertEqual(self.binding()['request']['marker'], marker)

    def test_claims_without_matching_dispatch_evidence_do_not_satisfy_guard(self):
        cases = dict(wrong_caller=dict(caller=str(uuid.uuid4())), pasted_before_binding=dict(paste_offset=-3600),
                     never_pasted=dict(phase='PREPARED'), payload_mismatch=dict(tamper=True),
                     not_a_ready_marker=dict(marker='HELLO_1234'))
        for name, kwargs in cases.items():
            with self.subTest(name):
                self.setUp()
                self.disarmed()
                if name == 'never_pasted':
                    marker = self.ready(**kwargs)
                    journal = self.state / 'message-dispatch-v1' / executor_ready.request_key(self.surface, marker)
                    doc = json.loads((journal / 'attempt-0001.json').read_text())
                    doc['events'] = []
                    (journal / 'attempt-0001.json').write_text(json.dumps(doc))
                else:
                    self.ready(**kwargs)
                self.assertEqual(self.stop().returncode, 2)

    def test_rearm_starts_a_new_episode(self):
        self.disarmed()
        self.ready()
        self.assertEqual(self.stop().returncode, 0)
        self.disarmed()
        self.assertEqual(self.stop().returncode, 2)

    def test_ready_text_is_an_ordinary_message(self):
        text = executor_ready.build_text('surface:2', 'EXECUTOR_READY_abcd1234', 'task', 'note\nTASK: x')
        self.assertFalse(cmux_bridge._looks_like_task_dispatch(text))
        self.assertNotIn('\n', text)
        self.assertIn('EXECUTOR_READY_abcd1234', text)


def executor_ready_budget():
    import cmux_executor_idle_guard
    return cmux_executor_idle_guard.MAX_REMINDERS


if __name__ == '__main__':
    unittest.main()
