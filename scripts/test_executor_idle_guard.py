"""Real Stop-hook entrypoint tests for the executor idle ask-loop guard."""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

import executor_ready
import offline_test_hook

GUARD = Path(__file__).with_name('cmux_executor_idle_guard.py')
DRIVER = Path(__file__).with_name('offline_ask_loop_driver.py')


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

    def stop(self, reentry=False, event='Stop', transcript=''):
        data = dict(hook_event_name=event, stop_hook_active=reentry, last_assistant_message='idle')
        if transcript:
            data['transcript_path'] = transcript
        return subprocess.run(offline_test_hook.command(GUARD, self.active), input=json.dumps(data),
                              text=True, capture_output=True, env=self.env, timeout=30)

    def binding(self):
        files = list((self.state / 'executor-idle-v1' / 'bindings').glob('*.json'))
        return json.loads(files[0].read_text()) if files else None

    def disarmed(self):
        self.arm()
        self.assertEqual(self.stop().returncode, 0)
        self.assertEqual(self.binding()['state'], 'BOUND')
        self.marker_path.unlink()

    def spawn_loop(self, interval='60'):
        """A real ask loop: the guard proves liveness with ps, not with a claim."""
        proc = subprocess.Popen(
            [os.sys.executable, '-B', str(DRIVER), str(DRIVER.parent), self.surface, interval],
            env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self._kill, proc)
        path = executor_ready.loop_path(self.surface)
        for _ in range(100):
            record = executor_ready.read_json(path)
            if record and record.get('state') == executor_ready.ASKING:
                return proc, record
            time.sleep(0.1)
        self.fail('fixture ask loop never recorded ASKING')

    def _kill(self, proc):
        if proc.poll() is None:
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=10)

    def test_unbound_executor_and_other_events_pass_without_state(self):
        for event in ('Stop', 'PreToolUse', 'SubagentStop'):
            self.assertEqual(self.stop(event=event).returncode, 0)
        self.arm(executor=str(uuid.uuid4()))
        self.assertEqual(self.stop().returncode, 0)
        self.assertIsNone(self.binding())
        run = subprocess.run(offline_test_hook.command(GUARD, self.active), input='{}', text=True,
                             capture_output=True, env=self.env, timeout=30)
        self.assertEqual(run.returncode, 0)

    def test_idle_executor_without_a_live_loop_is_blocked_with_the_exact_command(self):
        self.disarmed()
        transcript = str(self.root / 'session.jsonl')
        run = self.stop(transcript=transcript)
        self.assertEqual(run.returncode, 2)
        self.assertIn('EXECUTOR_IDLE_ASK_LOOP_REQUIRED', run.stderr)
        self.assertIn('executor_ready.py', run.stderr)
        self.assertIn('persist --supervisor surface:1', run.stderr)
        self.assertIn('--caller-uuid ' + self.surface, run.stderr)
        self.assertIn('--task test-task', run.stderr)
        self.assertIn('--transcript ' + transcript, run.stderr)
        self.assertIn('nohup', run.stderr)
        self.assertEqual(self.binding()['blocks'], 1)

    def test_no_budget_releases_the_obligation_and_reentry_does_not_either(self):
        self.disarmed()
        for _ in range(6):
            self.assertEqual(self.stop().returncode, 2)
        self.assertEqual(self.stop(reentry=True).returncode, 2)
        self.assertEqual(self.binding()['state'], 'BOUND')

    def test_a_live_ask_loop_releases_the_turn(self):
        self.disarmed()
        self.assertEqual(self.stop().returncode, 2)
        proc, record = self.spawn_loop()
        self.assertGreaterEqual(record['ask_count'], 1)
        self.assertEqual(self.stop().returncode, 0)
        self.assertEqual(self.binding()['state'], 'IDLE_ASKING')
        self.assertEqual(self.binding()['loop']['pid'], proc.pid)
        self._kill(proc)
        self.assertEqual(self.stop().returncode, 2)  # a dead loop is a dead wait again

    def test_stale_heartbeat_and_stretched_interval_do_not_pass(self):
        self.disarmed()
        proc, _record = self.spawn_loop()
        self.assertEqual(self.stop().returncode, 0)
        path = executor_ready.loop_path(self.surface)
        # SIGSTOP so the live loop cannot rewrite the record under the assertion;
        # the pid stays alive, so only the patched field can explain a refusal.
        proc.send_signal(signal.SIGSTOP)
        self.addCleanup(lambda: proc.poll() is None and proc.send_signal(signal.SIGCONT))
        pristine = executor_ready.read_json(path)
        for name, patch_value in (('stale', dict(heartbeat_epoch=time.time() - 3600)),
                                  ('stretched', dict(interval_seconds=1800.0)),
                                  ('foreign_caller', dict(caller_surface_uuid=str(uuid.uuid4()))),
                                  ('forged_argv', dict(argv='python3 -B other.py persist'))):
            with self.subTest(name):
                # Each case starts from the live record, so one cause is isolated.
                executor_ready.write_json(path, dict(pristine, **patch_value))
                self.assertEqual(self.stop().returncode, 2)
                executor_ready.write_json(path, pristine)
                self.assertEqual(self.stop().returncode, 0)

    def test_a_reply_ends_the_obligation(self):
        self.disarmed()
        record = dict(caller_surface_uuid=self.surface, state=executor_ready.ANSWERED,
                      ended_epoch=time.time(), ask_count=3, pid=os.getpid(),
                      reply=dict(source='mailbox', status='SOLO'))
        executor_ready.write_json(executor_ready.loop_path(self.surface), record)
        self.assertEqual(self.stop().returncode, 0)
        self.assertEqual(self.binding()['state'], 'IDLE_ANSWERED')
        self.assertEqual(self.stop().returncode, 0)

    def test_a_reply_from_an_older_episode_does_not_carry_over(self):
        self.disarmed()
        executor_ready.write_json(executor_ready.loop_path(self.surface), dict(
            caller_surface_uuid=self.surface, state=executor_ready.ANSWERED,
            ended_epoch=time.time() - 7200, reply=dict(source='mailbox', status='SOLO')))
        self.assertEqual(self.stop().returncode, 2)

    def test_operator_stop_passes_but_a_dispatch_stop_does_not(self):
        for reason, code in (('OPERATOR', 0), ('DISPATCHED', 2)):
            with self.subTest(reason):
                self.setUp()
                self.disarmed()
                executor_ready.write_json(executor_ready.loop_path(self.surface), dict(
                    caller_surface_uuid=self.surface, state=executor_ready.STOPPED,
                    ended_epoch=time.time(), stop=dict(reason=reason)))
                self.assertEqual(self.stop().returncode, code)

    def test_rearm_stops_the_loop_and_starts_a_new_episode(self):
        self.disarmed()
        proc, _record = self.spawn_loop()
        self.assertEqual(self.stop().returncode, 0)
        self.arm()
        self.assertEqual(self.stop().returncode, 0)
        self.assertEqual(self.binding()['state'], 'BOUND')
        stop = executor_ready.read_json(executor_ready.stop_path(self.surface))
        self.assertEqual(stop['reason'], 'DISPATCHED')
        proc.wait(timeout=30)
        self.marker_path.unlink()
        self.assertEqual(self.stop().returncode, 2)

    def test_claimed_loop_with_a_foreign_live_pid_does_not_pass(self):
        self.disarmed()
        victim = subprocess.Popen([os.sys.executable, '-c', 'import time; time.sleep(60)'])
        self.addCleanup(self._kill, victim)
        executor_ready.write_json(executor_ready.loop_path(self.surface), dict(
            caller_surface_uuid=self.surface, state=executor_ready.ASKING, pid=victim.pid,
            argv='python3 -B executor_ready.py persist --supervisor surface:1',
            heartbeat_epoch=time.time(), interval_seconds=60.0, ask_count=9))
        self.assertEqual(self.stop().returncode, 2)


if __name__ == '__main__':
    unittest.main()
