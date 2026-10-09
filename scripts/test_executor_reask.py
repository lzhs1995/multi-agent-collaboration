#!/usr/bin/env python3
"""空闲 executor 60 秒主动求派与其 Stop hook 的离线回归（无真实 bridge 输入）。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import executor_ready as ready  # noqa: E402
import executor_reask as reask  # noqa: E402
import cmux_executor_reask_stop_guard as guard  # noqa: E402

CALLER = 'E7C1C83C-946C-4E59-8753-F506F2069A12'
SUP = '2A2CDBE8-DD07-4185-A972-2E56BB3135DF'


class Sender:
    def __init__(self, outcome='CONFIRMED', terminal=True):
        self.calls, self.outcome, self.terminal = [], outcome, terminal

    def __call__(self, state, marker, text):
        self.calls.append((marker, text))
        return dict(outcome=self.outcome, terminal=self.terminal)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        patcher = mock.patch.object(Path, 'home', return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.channel = self.home / 'channel'
        self.channel.mkdir()
        self.transcript = self.home / 't.jsonl'
        self.transcript.write_text('{"type":"user","message":{"role":"user","content":"old"}}\n')

    def begin(self, now=1000.0):
        return reask.start(CALLER, SUP, 'r24', str(self.transcript), [str(self.channel)],
                           now=now)

    def user(self, text):
        with self.transcript.open('a') as fh:
            fh.write(json.dumps(dict(type='user', message=dict(role='user', content=text))) + '\n')


class ReaskTest(Base):
    def test_first_tick_asks_on_both_channels(self):
        self.begin()
        send = Sender()
        state = reask.tick(CALLER, 1001.0, send)
        self.assertEqual(state['ask_count'], 1)
        self.assertEqual(len(send.calls), 1)
        current = json.loads((self.channel / reask.current_name(CALLER)).read_text())
        self.assertEqual(current['marker'], send.calls[0][0])
        self.assertEqual(current['status'], reask.WAITING)

    def test_no_second_ask_inside_interval_and_fresh_marker_after(self):
        self.begin()
        send = Sender()
        reask.tick(CALLER, 1001.0, send)
        reask.tick(CALLER, 1001.0 + reask.INTERVAL - 1, send)
        self.assertEqual(len(send.calls), 1)
        state = reask.tick(CALLER, 1001.0 + reask.INTERVAL, send)
        self.assertEqual(len(send.calls), 2)
        self.assertNotEqual(send.calls[0][0], send.calls[1][0])
        self.assertNotEqual(send.calls[0][1], send.calls[1][1])
        self.assertEqual(state['ask_count'], 2)

    def test_keeps_asking_without_any_upper_bound(self):
        self.begin()
        send = Sender(outcome='SKIPPED_COMPOSE', terminal=False)
        for i in range(30):
            state = reask.tick(CALLER, 1001.0 + i * reask.INTERVAL, send)
        self.assertEqual(state['state'], reask.WAITING)
        self.assertEqual(state['ask_count'], 30)
        self.assertEqual(state['terminal_sends'], 0)
        self.assertLessEqual(len(state['asks']), reask.HISTORY)

    def test_text_stays_under_codex_paste_summary_limit(self):
        state = self.begin()
        text = reask.build_text(state, 'EXECUTOR_READY_' + 'f' * 16, 9999)
        self.assertLessEqual(len(text), reask.MAX_TEXT)
        self.assertNotIn('will not be repeated', text)

    def test_transcript_user_turn_is_a_reply(self):
        self.begin()
        reask.tick(CALLER, 1001.0, Sender())
        self.user('TASK r24 pack at /x')
        state = reask.tick(CALLER, 1002.0, Sender())
        self.assertEqual(state['state'], reask.ANSWERED)
        self.assertEqual(state['reply']['source'], 'transcript')

    def test_hook_feedback_and_queued_are_not_replies(self):
        self.begin()
        reask.tick(CALLER, 1001.0, Sender())
        self.user('Stop hook feedback: keep going')
        with self.transcript.open('a') as fh:
            fh.write(json.dumps(dict(type='user', isMeta=True,
                                     message=dict(role='user', content='meta'))) + '\n')
        state = reask.tick(CALLER, 1002.0, Sender())
        self.assertEqual(state['state'], reask.WAITING)

    def test_mailbox_reply_ends_episode(self):
        state = self.begin(now=0.0)
        reask.tick(CALLER, 1.0, Sender())
        marker = reask.load(CALLER)['asks'][-1]['marker']
        Path(state['mailbox'], marker + '.json').write_text(json.dumps(
            dict(marker=marker, caller_surface_uuid=CALLER, status='TASK')))
        state = reask.tick(CALLER, 2.0, Sender())
        self.assertEqual(state['state'], reask.ANSWERED)
        self.assertEqual(state['reply']['source'], 'mailbox')

    def test_channel_file_citing_marker_is_reply_but_own_current_is_not(self):
        self.begin(now=0.0)
        reask.tick(CALLER, 1.0, Sender())
        self.assertEqual(reask.tick(CALLER, 2.0, Sender())['state'], reask.WAITING)
        marker = reask.load(CALLER)['asks'][-1]['marker']
        (self.channel / 'ROOT_REPLY.json').write_text(json.dumps(dict(answers=marker)))
        state = reask.tick(CALLER, 3.0, Sender())
        self.assertEqual(state['state'], reask.ANSWERED)
        self.assertEqual(state['reply']['source'], 'channel')
        current = json.loads((self.channel / reask.current_name(CALLER)).read_text())
        self.assertEqual(current['status'], reask.ANSWERED)

    def test_operator_stop_is_the_only_manual_exit(self):
        self.begin()
        reask.tick(CALLER, 1001.0, Sender())
        ready.write_json(reask.stop_file(CALLER), dict(reason='OPERATOR'))
        state = reask.tick(CALLER, 2000.0, Sender())
        self.assertEqual(state['state'], reask.STOPPED)

    def test_terminal_error_still_counts_file_round(self):
        self.begin()

        def boom(*_a):
            raise RuntimeError('bridge down')
        state = reask.tick(CALLER, 1001.0, boom)
        self.assertEqual(state['asks'][-1]['outcome'], 'TERMINAL_ERROR')
        self.assertTrue((self.channel / reask.current_name(CALLER)).exists())

    def test_held_compose_or_queue_skips_terminal_paste(self):
        bridge = mock.Mock()
        bridge.read_screen.return_value = 'screen'
        bridge.pending_queue_holds.return_value = True
        self.assertEqual(reask._held(bridge, SUP, [dict(marker='M', terminal=True)])[0], 'QUEUED')
        bridge.pending_queue_holds.return_value = False
        bridge.compose_block_is_empty.return_value = False
        self.assertEqual(reask._held(bridge, SUP, [])[0], 'COMPOSE')
        bridge.compose_block_is_empty.return_value = True
        bridge.receiver_cannot_submit_now.return_value = False
        self.assertIsNone(reask._held(bridge, SUP, [])[0])

    # 2026-10-09 实测屏幕形状：本版 Codex 横幅 "Queued follow-up inputs" 不被
    # bridge._PENDING_QUEUE_RE 识别，#9 排队时 #10 仍被贴出。marker 故意折行。
    LIVE_QUEUE = (
        '• Compacting context (13s • esc to interrupt)\n'
        '  └ Making room to continue.\n'
        ' \n'
        '• Queued follow-up inputs\n'
        '  ↳ EXECUTOR_READY|E7C1C83C|EXECUTOR_READY_6480c9d5\n'
        '    fd4912cb ask #9 (every 60s until you reply): executor E7C1C83C is\n'
        '    shift+← edit last queued message\n'
        ' \n'
        ' \n'
        '› Ask Codex to do anything\n')

    def _real_bridge(self, screen):
        bridge = ready._bridge()
        self.assertFalse(bridge.pending_queue_holds(screen, 'EXECUTOR_READY_6480c9d5fd4912cb'),
                         'fixture must exercise the banner the bridge regex misses')
        return mock.Mock(wraps=bridge, read_screen=mock.Mock(return_value=screen))

    def test_unreceived_queued_ask_holds_despite_unknown_banner(self):
        marker = 'EXECUTOR_READY_6480c9d5fd4912cb'
        ready.write_record(CALLER, marker, f'ask {marker}', SUP, 'r24', 'ep')
        bridge = self._real_bridge(self.LIVE_QUEUE)
        with mock.patch.object(ready, '_receipt', return_value=None):
            held = reask._held(bridge, SUP, [dict(marker=marker, terminal=True)], CALLER)[0]
        self.assertEqual(held, 'QUEUED')
        # 对照：同一屏幕，但该请求已有原生回执（留在历史区）→ 不再拦，并记 CONFIRMED。
        ask = dict(marker=marker, terminal=True)
        with mock.patch.object(ready, '_receipt', return_value=dict(ok=1)):
            held = reask._held(bridge, SUP, [ask], CALLER)[0]
        self.assertIsNone(held)
        self.assertEqual(ask['outcome'], 'CONFIRMED')
        # 对照：marker 不在屏上 → 不拦。
        with mock.patch.object(ready, '_receipt', return_value=None):
            held = reask._held(bridge, SUP, [dict(marker='EXECUTOR_READY_ffff', terminal=True)],
                               CALLER)[0]
        self.assertIsNone(held)

    def test_default_send_skips_when_previous_ask_still_queued(self):
        marker = 'EXECUTOR_READY_6480c9d5fd4912cb'
        ready.write_record(CALLER, marker, f'ask {marker}', SUP, 'r24', 'ep')
        state = dict(supervisor=SUP, caller_surface_uuid=CALLER, task_id='r24', episode_id='ep',
                     asks=[dict(marker=marker, terminal=True)])
        bridge = self._real_bridge(self.LIVE_QUEUE)
        with mock.patch.object(ready, '_bridge', return_value=bridge), \
                mock.patch.object(ready, '_receipt', return_value=None), \
                mock.patch.object(ready, 'advance_ask') as advance:
            result = reask.default_send(state, 'EXECUTOR_READY_new', 'new ask')
        self.assertEqual(result, dict(outcome='SKIPPED_QUEUED', terminal=False))
        advance.assert_not_called()


class StopGuardTest(Base):
    def payload(self, active=False):
        return dict(hook_event_name='Stop', stop_hook_active=active,
                    transcript_path=str(self.transcript))

    def test_waiting_blocks_even_on_reentry(self):
        self.begin()
        for active in (False, True):
            block, reason = guard.decide(self.payload(active), CALLER, 1001.0)
            self.assertTrue(block)
            self.assertIn('executor_reask.py', reason)
            self.assertIn(' run ', reason)

    def test_hook_does_not_consume_ask_rounds(self):
        self.begin()
        guard.decide(self.payload(), CALLER, 1001.0)
        self.assertEqual(reask.load(CALLER)['ask_count'], 0)

    def test_answered_blocks_once_then_allows(self):
        self.begin()
        reask.tick(CALLER, 1001.0, Sender())
        self.user('STATUS: WAITING_DEPENDENCY until install')
        block, reason = guard.decide(self.payload(), CALLER, 1002.0)
        self.assertTrue(block)
        self.assertIn('WAITING_DEPENDENCY until install', reason)
        self.assertEqual(guard.decide(self.payload(), CALLER, 1003.0), (False, ''))

    def test_stopped_allows(self):
        self.begin()
        ready.write_json(reask.stop_file(CALLER), dict(reason='OPERATOR'))
        self.assertEqual(guard.decide(self.payload(), CALLER, 1001.0), (False, ''))

    def test_no_episode_no_idle_binding_allows(self):
        self.assertEqual(guard.decide(self.payload(), CALLER, 1.0), (False, ''))

    def test_fresh_idle_binding_auto_enrolls(self):
        ready.write_json(ready.idle_root() / 'bindings' / 'x.json', dict(
            surface_uuid=CALLER, state='IDLE_WAITING_DEPENDENCY', supervisor_uuid=SUP,
            task_id='r22', idle_since_epoch=500.0))
        block, _reason = guard.decide(self.payload(), CALLER, 600.0)
        self.assertTrue(block)
        state = reask.load(CALLER)
        self.assertEqual((state['supervisor'], state['reason']), (SUP, 'AUTO_IDLE'))
        # 已消费的答复之后，同一旧 idle 不会重新开段。
        state['state'] = reask.CONSUMED
        reask.save(state)
        self.assertEqual(guard.decide(self.payload(), CALLER, 700.0), (False, ''))

    def test_cli_entry_prints_block_without_internal_error(self):
        self.begin()
        env = dict(os.environ, HOME=str(self.home), CMUX_SURFACE_ID=CALLER)
        # 子进程 HOME 不同于 mock，需在其 HOME 下重建等待段。
        code = ('import sys; sys.path.insert(0, %r); import executor_reask as r;'
                'r.start(%r, %r, "r24")' % (str(HERE), CALLER, SUP))
        subprocess.run([sys.executable, '-B', '-c', code], env=env, check=True)
        run = subprocess.run([sys.executable, '-B', str(HERE / 'cmux_executor_reask_stop_guard.py')],
                             input=json.dumps(self.payload()), capture_output=True, text=True,
                             env=env, check=True)
        self.assertEqual(json.loads(run.stdout)['decision'], 'block')
        self.assertNotIn('INTERNAL_ERROR', run.stderr)


if __name__ == '__main__':
    unittest.main()
