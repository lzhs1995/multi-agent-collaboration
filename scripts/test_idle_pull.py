"""Executor idle pull: real hook subprocesses, private HOME and marker dir."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid

from unittest.mock import patch

import cmux_idle_pull as idle
import cmux_idle_push as push
import idle_push_fixture
import offline_test_hook

HERE = Path(__file__).resolve().parent


def fake_cmux(workspace, surfaces, caller):
    """Replace only cmux identify/tree I/O; snapshot validation and ack stay real."""
    panes = [dict(id=str(uuid.uuid4()), ref='pane:%d' % i,
                  surfaces=[dict(id=s, ref='surface:%d' % i, type='terminal')])
             for i, s in enumerate(surfaces, 1)]
    tree = dict(windows=[dict(ref='window:1', workspaces=[dict(id=workspace, ref='workspace:1', panes=panes)])])
    index = surfaces.index(caller) + 1
    ident = dict(caller=dict(surface_ref='surface:%d' % index, workspace_ref='workspace:1',
                             pane_ref='pane:%d' % index))
    import cmux_workspace_guard as guard
    return patch.object(guard, '_read_json_command',
                        side_effect=lambda *a: ident if a[0] == 'identify' else tree)


class IdlePullTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='idle-pull-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        # 后台催办器只能打到假 bridge；测试结束先杀掉它再删临时目录
        self.extra_env = {}
        self.calls_log = idle_push_fixture.install(self.root, self.extra_env)
        self.addCleanup(idle_push_fixture.kill_pushers, self.home)
        self.workspace, self.executor, self.supervisor, self.intruder = [
            str(uuid.uuid4()).upper() for _ in range(4)]
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
                    CMUX_SURFACE_ID=surface, PYTHONDONTWRITEBYTECODE='1', **self.extra_env)

    def run_hook(self, name, data, surface):
        return subprocess.run(offline_test_hook.command(HERE / name, self.active), input=json.dumps(data),
                              text=True, capture_output=True, env=self.env(surface), timeout=10)

    def cli(self, *args, surface=None):
        return subprocess.run(offline_test_hook.command(HERE / 'cmux_idle_pull.py', self.active, *args),
                              text=True, capture_output=True, env=self.env(surface or self.executor), timeout=10)

    def ack_as(self, surface, supervisor=None, reason='WAITING_DEPENDENCY: x', workspace=None):
        """Real CLI main in-process; the live caller is `surface`, never the arguments."""
        workspace = workspace or self.workspace
        env = {k: v for k, v in self.env(surface).items() if k != 'CODEX_THREAD_ID'}
        env['CMUX_WORKSPACE_ID'] = workspace  # 与假 tree 一致，只让工作区本身不符
        others = [x for x in (self.supervisor, self.executor, self.intruder) if x != surface]
        with patch.dict(os.environ, env, clear=True), fake_cmux(workspace, [surface] + others, surface), \
                patch('sys.stdout', new_callable=__import__('io').StringIO), \
                patch('sys.stderr', new_callable=__import__('io').StringIO) as err:
            code = idle.main(['--ack', self.executor, '--workspace', self.workspace,
                              '--supervisor', supervisor or self.supervisor, '--reason', reason])
        self.last_ack_error = err.getvalue()
        return code

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
        self.assertEqual(self.ack_as(self.supervisor, reason=' '), 2)
        self.assertEqual(self.ack_as(self.supervisor, reason='WAITING_DEPENDENCY: root owned save'), 0,
                         self.last_ack_error)
        self.assertEqual(self.sup_stop().returncode, 0)

    def test_new_request_reopens_after_old_ack(self):
        self.callback_attempted()
        self.cli('--task-pack', self.pack)
        self.assertEqual(self.ack_as(self.supervisor, reason='later'), 0, self.last_ack_error)
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
                                                                    phase='CONFIRMED', started_at_epoch=at - 10)))
        self.assertEqual(self.sup_stop().returncode, 2)
        # 未确认送达（排队/无输入）的新派发不能清 pending
        for n, phase in ((2, 'POST_ENTER_OBSERVATION'), (3, 'NO_INPUT'), (4, None)):
            row = dict(binding=dict(identity=ident), started_at_epoch=at + n)
            if phase:
                row['phase'] = phase
            (journal / ('attempt-%04d.json' % n)).write_text(json.dumps(row))
            with self.subTest(phase=phase):
                self.assertEqual(self.sup_stop().returncode, 2)
        (journal / 'attempt-0005.json').write_text(json.dumps(dict(binding=dict(identity=ident),
                                                                    phase='CONFIRMED', started_at_epoch=at + 5)))
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
        self.assertEqual(self.ack_as(self.intruder, supervisor=self.intruder), 2)
        self.assertIn('another supervisor', self.last_ack_error)
        self.assertEqual(self.sup_stop().returncode, 2)

    def test_ack_caller_must_be_the_addressed_supervisor(self):
        self.callback_attempted()
        self.cli('--task-pack', self.pack)
        ack = self.inbox() / (self.executor + '.ack.json')
        # 冒名：参数写对了主管 UUID，但真实调用方是别的 surface / 执行者自己
        for surface in (self.intruder, self.executor):
            with self.subTest(surface=surface):
                self.assertEqual(self.ack_as(surface), 2)
                self.assertIn('ACK_CALLER_', self.last_ack_error)
                self.assertFalse(ack.exists())
                self.assertEqual(self.sup_stop().returncode, 2)
        # 跨工作区的同 UUID 也不行
        self.assertEqual(self.ack_as(self.supervisor, workspace=str(uuid.uuid4()).upper()), 2)
        self.assertIn('ACK_CALLER_MISMATCH', self.last_ack_error)
        self.assertFalse(ack.exists())
        # 身份解析失败即拒绝，不回落到参数
        import cmux_workspace_guard as guard
        with patch.object(guard, '_read_json_command', side_effect=guard.WorkspaceScopeError('down')):
            with self.assertRaisesRegex(ValueError, 'ACK_CALLER_UNRESOLVED'):
                with patch.dict(os.environ, self.env(self.supervisor), clear=True):
                    idle.ack(self.workspace, self.supervisor, self.executor, 'x')
        self.assertFalse(ack.exists())
        self.assertEqual(self.ack_as(self.supervisor), 0, self.last_ack_error)
        self.assertEqual(self.sup_stop().returncode, 0)

    # detached pusher (real spawn, fake bridge) -------------------------------
    def test_record_spawns_pusher_that_asks_now_and_stops_on_ack(self):
        self.callback_attempted()
        r = self.cli('--task-pack', self.pack)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)['pusher'], 'STARTED')
        rows = idle_push_fixture.calls(self.calls_log)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['surface'], 'surface:1')
        self.assertTrue(rows[0]['text'].startswith('STATUS: ' + rows[0]['marker']))
        self.assertIn('--ack ' + self.executor, rows[0]['text'])
        with patch.dict(os.environ, {'HOME': str(self.home)}):
            self.assertTrue(push.alive(self.workspace, self.executor))
        # 二次记录不重复起催办器
        self.assertEqual(json.loads(self.cli('--task-pack', self.pack).stdout)['pusher'], 'RUNNING')
        self.assertEqual(self.ack_as(self.supervisor), 0, self.last_ack_error)
        status = self.home / '.local/state/multi-agent-collaboration/idle-push-v1' / self.workspace / self.executor / 'status.json'
        deadline = __import__('time').time() + push.POLL + 10
        while __import__('time').time() < deadline and json.loads(status.read_text()).get('state') != 'ANSWERED':
            __import__('time').sleep(0.2)
        self.assertEqual(json.loads(status.read_text())['answer'], 'ACKED')
        self.assertEqual(len(idle_push_fixture.calls(self.calls_log, wait=0)), 1)


class IdlePushLadderTests(unittest.TestCase):
    """run() in-process with a fake clock: ladder shape, answers, restarts."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='idle-push-')
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)
        env = patch.dict(os.environ, {'HOME': str(home)})
        env.start()
        self.addCleanup(env.stop)
        self.ws, self.ex, self.sup = [str(uuid.uuid4()).upper() for _ in range(3)]
        caller = patch.object(idle, '_authenticated_caller', return_value=(self.ws, self.sup))
        caller.start()
        self.addCleanup(caller.stop)
        self.t = 1000.0
        self.write_request('r1')
        self.sent = []
        self.hooks = {}  # 假时钟到点时执行的动作

    def write_request(self, report_sha):
        idle._atomic(idle.request_path(self.ws, self.ex), dict(
            at_epoch=self.t, executor_uuid=self.ex, supervisor_uuid=self.sup, workspace_uuid=self.ws,
            supervisor_ref='surface:9', last_task_id='t', report='/r.md', report_sha256=report_sha))

    def submit_text(self, surface, text, marker=None):
        self.sent.append((self.t, surface, marker, text))
        return {'confirmed': False}

    def sleep(self, seconds):
        self.t += seconds
        for at in sorted(k for k in self.hooks if k <= self.t):
            self.hooks.pop(at)()

    def run_push(self):
        return push.run(self.ws, self.ex, bridge=self, clock=lambda: self.t, sleep=self.sleep, poll=60)

    def ack(self):
        idle.ack(self.ws, self.sup, self.ex, 'WAITING_DEPENDENCY: test', now=self.t)

    def test_schedule_is_non_decreasing_and_unbounded_within_horizon(self):
        s = push.schedule()
        gaps = [b - a for a, b in zip(s, s[1:])]
        # 用户要求：60 秒没回复就再问一次
        self.assertEqual(s[:3], [0, 60, 120])
        self.assertEqual(set(gaps), {60})
        self.assertEqual(gaps, sorted(gaps))
        self.assertEqual(s[-1], push.HORIZON)
        self.assertEqual(len(s), push.HORIZON // 60 + 1)

    def before(self, seconds):
        return len([x for x in push.schedule() if x < seconds])

    def test_keeps_asking_with_new_markers_until_horizon(self):
        self.assertEqual(self.run_push(), 'EXHAUSTED')
        self.assertEqual(len(self.sent), len(push.schedule()))
        self.assertEqual(len({m for _, _, m, _ in self.sent}), len(self.sent))
        self.assertTrue(all(s == 'surface:9' for _, s, _, _ in self.sent))
        offsets = [round(t - 1000.0) for t, *_ in self.sent]
        self.assertEqual(offsets[:3], [0, 60, 120])

    def test_ack_stops_pushing(self):
        self.hooks[1000.0 + 700] = self.ack
        self.assertEqual(self.run_push(), 'ACKED')
        self.assertEqual(len(self.sent), self.before(700))
        self.assertEqual(len(self.sent), 12)

    def test_task_dispatch_and_supervisor_message_answer(self):
        for kind, folder, record in (
                ('TASK_DISPATCHED', 'task-dispatch-v1', lambda at: dict(started_at_epoch=at)),
                ('SUPERVISOR_MESSAGED', 'message-dispatch-v1',
                 lambda at: dict(events=[dict(phase='PASTE_INTENT', at_epoch=at)]))):
            done = lambda at, r=record: dict(r(at), phase='CONFIRMED')
            with self.subTest(kind=kind):
                self.sent.clear()
                self.t = 1000.0 + len(kind)
                self.write_request(kind)
                journal = idle.state_root() / folder / kind
                journal.mkdir(parents=True)
                ident = dict(workspace_uuid=self.ws, caller_surface_uuid=self.sup,
                             target_surface_uuid=self.ex, target_pane_uuid='p')
                # 早于请求的旧派发不算回复
                (journal / 'attempt-0001.json').write_text(json.dumps(dict(binding=dict(identity=ident),
                                                                            **done(self.t - 5))))
                # 请求之后但未确认送达的不算回复，继续催
                (journal / 'attempt-0002.json').write_text(json.dumps(dict(
                    binding=dict(identity=ident), phase='POST_ENTER_OBSERVATION', **record(self.t + 1))))
                at = self.t + 900
                self.hooks[at] = lambda j=journal, i=ident, a=at, d=done: (j / 'attempt-0003.json').write_text(
                    json.dumps(dict(binding=dict(identity=i), **d(a))))
                self.assertEqual(self.run_push(), kind)
                self.assertEqual(len(self.sent), self.before(900))
                for p in journal.glob('*'):
                    p.unlink()
                journal.rmdir()

    def test_new_request_restarts_ladder_in_same_pusher(self):
        self.hooks[1000.0 + 2000] = lambda: self.write_request('r2')
        self.hooks[1000.0 + 2700] = self.ack
        self.assertEqual(self.run_push(), 'ACKED')
        offsets = [round(t - 1000.0) for t, *_ in self.sent]
        # 旧请求每 60 秒一次共 34 次（0..1980）；2040 发现新请求，从第 1 次重来
        first = self.before(2000)
        self.assertEqual(offsets[first - 1], 1980)
        self.assertEqual(offsets[first], 2040)
        self.assertIn('-1-', self.sent[first][2])
        self.assertIn('-%d-' % first, self.sent[first - 1][2])
        self.assertEqual(len(offsets), first + self.before(2700 - 2040))

    def test_bridge_failure_is_recorded_not_fatal(self):
        def boom(surface, text, marker=None):
            self.sent.append((self.t, surface, marker, text))
            raise RuntimeError('COMPOSE_OCCUPIED')
        self.submit_text = boom
        self.hooks[1000.0 + 1900] = self.ack
        self.assertEqual(self.run_push(), 'ACKED')
        status = json.loads(push.status_path(self.ws, self.ex).read_text())
        self.assertEqual(len(status['pushes']), self.before(1900))
        self.assertTrue(all(p['outcome'].startswith('RuntimeError') for p in status['pushes']))

    def test_alive_requires_lock_holder_or_answer(self):
        import fcntl
        self.assertFalse(push.alive(self.ws, self.ex))
        folder = push.push_dir(self.ws, self.ex)
        folder.mkdir(parents=True)
        holder = open(folder / 'push.lock', 'a')
        self.addCleanup(holder.close)
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertTrue(push.alive(self.ws, self.ex))
        self.assertEqual(push.run(self.ws, self.ex, bridge=self, clock=lambda: self.t,
                                  sleep=self.sleep), 'ALREADY_RUNNING')
        fcntl.flock(holder, fcntl.LOCK_UN)
        self.assertFalse(push.alive(self.ws, self.ex))
        self.ack()
        self.assertTrue(push.alive(self.ws, self.ex))


if __name__ == '__main__':
    unittest.main()
