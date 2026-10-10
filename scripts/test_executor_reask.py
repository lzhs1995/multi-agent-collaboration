#!/usr/bin/env python3
"""空闲 executor 60 秒主动求派与其 Stop hook 的离线回归（无真实 bridge 输入）。"""
import json
import contextlib
import io
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
import executor_reply as replies

CALLER = 'E7C1C83C-946C-4E59-8753-F506F2069A12'
SUP = '2A2CDBE8-DD07-4185-A972-2E56BB3135DF'
WORKSPACE = '26E665BA-7E98-49DE-B0EA-DDE37AFBBB88'


class Sender:
    def __init__(self, outcome='CONFIRMED', terminal=True):
        self.calls, self.outcome, self.terminal = [], outcome, terminal

    def __call__(self, state, marker, text):
        self.calls.append((marker, text))
        return dict(outcome=self.outcome, terminal=self.terminal,
                    native_received=self.outcome == 'CONFIRMED', terminal_attempted=self.terminal)


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

    def reply(self, **overrides):
        state = reask.load(CALLER)
        body = replies.template(state, state['asks'][-1]['marker'])
        body.update(trigger='successor release installed', **overrides)
        return body


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
        wire = ready.prompt_reference.plan(text, 'EXECUTOR_READY_' + 'f' * 16,
                                           ready.state_root() / 'message-bodies-v1')['text']
        self.assertLessEqual(len(wire.encode('utf-8')), reask.MAX_TEXT)
        self.assertNotIn('will not be repeated', text)

    def test_transcript_requires_exact_reply_not_unrelated_user_turn(self):
        self.begin()
        reask.tick(CALLER, 1001.0, Sender())
        self.user('TASK r24 pack at /x')
        self.assertEqual(reask.tick(CALLER, 1002.0, Sender())['state'], reask.WAITING)
        self.user(json.dumps(self.reply()))
        state = reask.tick(CALLER, 1002.0, Sender())
        self.assertEqual(state['state'], reask.ANSWERED)
        self.assertEqual(state['reply']['source'], 'transcript')

    def test_multibyte_reask_keeps_exact_body_and_notice_only_receipt(self):
        state = self.begin()
        state['task_id'] = '论文审阅' * 80
        marker = 'EXECUTOR_READY_multibyte'
        body = reask.build_text(state, marker, 1)
        self.assertGreater(len(body.encode('utf-8')), reask.MAX_TEXT)
        bridge = mock.Mock()

        def receive(_bridge, supervisor, caller, ask, **_kwargs):
            original = ready._original_request(caller, supervisor, ask['marker'])
            self.assertLessEqual(len(original['text'].encode('utf-8')), reask.MAX_TEXT)
            stored, _pin = ready.prompt_reference.read_body(original['body_reference'])
            self.assertEqual(stored, body)
            ask.update(outcome='CONFIRMED', confirmation_scope='reference_notice',
                       body_read_confirmed=False)

        with mock.patch.object(ready, '_bridge', return_value=bridge), \
                mock.patch.object(reask, '_held', return_value=(None, '')), \
                mock.patch.object(ready, 'advance_ask', side_effect=receive):
            result = reask.default_send(state, marker, body)
        self.assertEqual(result['confirmation_scope'], 'reference_notice')
        self.assertIs(result['body_read_confirmed'], False)

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
            self.reply(status='TASK')))
        state = reask.tick(CALLER, 2.0, Sender())
        self.assertEqual(state['state'], reask.ANSWERED)
        self.assertEqual(state['reply']['source'], 'mailbox')

    def test_channel_reply_requires_exact_binding_not_citation_or_own_current(self):
        self.begin(now=0.0)
        reask.tick(CALLER, 1.0, Sender())
        self.assertEqual(reask.tick(CALLER, 2.0, Sender())['state'], reask.WAITING)
        marker = reask.load(CALLER)['asks'][-1]['marker']
        (self.channel / 'ROOT_REPLY.json').write_text(json.dumps(dict(answers=marker)))
        self.assertEqual(reask.tick(CALLER, 3.0, Sender())['state'], reask.WAITING)
        (self.channel / 'ROOT_REPLY.json').write_text(json.dumps(self.reply()))
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

    # 真实 Codex 队列样本，marker 故意折行。同版 bridge 必须识别实际横幅。
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
        return mock.Mock(wraps=bridge, read_screen=mock.Mock(return_value=screen))

    def test_live_queue_and_historical_marker_have_distinct_evidence(self):
        marker = 'EXECUTOR_READY_6480c9d5fd4912cb'
        ready.write_record(CALLER, marker, f'ask {marker}', SUP, 'r24', 'ep')
        bridge = self._real_bridge(self.LIVE_QUEUE)
        self.assertTrue(bridge.pending_queue_holds(self.LIVE_QUEUE, marker))
        with mock.patch.object(ready, '_receipt', return_value=None):
            held = reask._held(bridge, SUP, [dict(marker=marker, terminal=True)], CALLER)[0]
        self.assertEqual(held, 'QUEUED')
        # 对照：实际队列消失，只剩历史正文；不能用旧 marker 断言仍排队。
        historical = '• Previous request ' + marker + '\n\n› Ask Codex to do anything\n'
        bridge = self._real_bridge(historical)
        with mock.patch.object(ready, '_receipt', return_value=None):
            held = reask._held(bridge, SUP, [dict(marker=marker, terminal=True)], CALLER)[0]
        self.assertEqual(held, 'UNVERIFIED')
        # 原次完整 native receipt 成立，才结束对历史 marker 的未核实状态。
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


class ReaskDurabilityTest(Base):
    def test_wrong_reply_bindings_rejected_in_every_channel(self):
        self.begin(now=0.0)
        reask.tick(CALLER, 1.0, Sender())
        changes = [dict(task_id='other'), dict(episode_id='old'), dict(supervisor_uuid=CALLER),
                   dict(caller_surface_uuid=SUP), dict(marker='EXECUTOR_READY_unknown'),
                   dict(queued=True), dict(received=False), dict(trigger=''),
                   dict(trigger='REPLACE_WITH_CONCRETE_TRIGGER')]
        for source in ('mailbox', 'channel', 'transcript'):
            for change in changes:
                with self.subTest(source=source, change=change):
                    body = self.reply()
                    body.update(change)
                    state = reask.load(CALLER)
                    path = (Path(state['mailbox']) if source == 'mailbox' else self.channel) / 'reply.json'
                    if source == 'transcript':
                        self.user(json.dumps(body))
                    else:
                        ready.write_json(path, body)
                    self.assertEqual(reask.tick(CALLER, 2.0, Sender())['state'], reask.WAITING)
                    if source != 'transcript':
                        path.unlink()

    def test_no_channels_and_no_input_never_count_as_delivered(self):
        self.begin()
        failure = dict(written=[], errors=[dict(error_type='OSError')])
        with mock.patch.object(reask, 'write_channels', return_value=failure):
            state = reask.tick(CALLER, 1001.0, Sender('SKIPPED_COMPOSE', False))
        for field in ('ask_count', 'file_posts', 'terminal_sends', 'terminal_attempts', 'native_receipts'):
            self.assertEqual(state[field], 0, field)
        self.assertEqual((state['check_count'], state['attempt_count']), (1, 1))
        self.assertEqual(state['asks'][0]['channels']['errors'], failure['errors'])

    def test_cli_and_stop_share_nonblocking_episode_lock(self):
        self.begin()
        send = Sender()
        with reask.episode_lock(CALLER):
            self.assertEqual(reask.tick(CALLER, 1001.0, send)['outcome'], 'EPISODE_BUSY')
            block, reason = guard.decide(dict(hook_event_name='Stop'), CALLER, 1001.0)
            self.assertTrue(block)
            self.assertIn('状态锁', reason)
        self.assertEqual(send.calls, [])
        self.assertEqual(reask.load(CALLER)['ask_count'], 0)

    def test_explicit_stop_does_not_reopen_without_operator_resume(self):
        self.begin()
        reask.tick(CALLER, 1001.0, Sender())
        reask.stop(CALLER)
        self.assertEqual(reask.start(CALLER, SUP, now=2000.0)['state'], reask.STOPPED)
        self.assertEqual(ready.read_json(reask.inbox_path(reask.load(CALLER)))['status'], reask.STOPPED)
        self.assertEqual(reask.start(CALLER, SUP, now=2001.0, resume_stopped=True)['state'], reask.WAITING)

    def test_only_fresh_same_supervisor_bound_task_ends_episode(self):
        self.begin()
        state = reask.tick(CALLER, 1001.0, Sender())
        binding = dict(surface_uuid=CALLER, supervisor_uuid=SUP, task_id='next', state='BOUND',
                       last_bound_epoch=2000.0)
        for delta in (dict(supervisor_uuid=CALLER), dict(last_bound_epoch=500.0), dict(task_id='r24')):
            with mock.patch.object(reask, 'idle_binding', return_value=dict(binding, **delta)):
                self.assertEqual(reask.tick(CALLER, 1002.0, Sender())['state'], reask.WAITING)
        with mock.patch.object(reask, 'idle_binding', return_value=binding):
            state = reask.tick(CALLER, 1003.0, Sender())
        self.assertEqual(state['exit_reason'], 'AUTHENTICATED_NEW_DISPATCH')
        self.assertEqual(ready.read_json(reask.inbox_path(state))['status'], reask.CONSUMED)

    def test_late_file_reply_survives_history_rollover_future_marker_does_not(self):
        state = self.begin(now=0.0)
        reask.tick(CALLER, 1.0, Sender('SKIPPED_COMPOSE', False))
        first_reply = self.reply()
        for index in range(1, reask.HISTORY + 5):
            state = reask.tick(CALLER, 1.0 + index * reask.INTERVAL, Sender('SKIPPED_COMPOSE', False))
        self.assertNotIn(first_reply['marker'], [a['marker'] for a in state['asks']])
        future = dict(first_reply, marker=reask.issue_marker(state, state['attempt_count'] + 1))
        path = Path(state['mailbox']) / 'reply.json'
        ready.write_json(path, future)
        self.assertIsNone(reask.find_reply(state))
        ready.write_json(path, first_reply)
        self.assertEqual(reask.find_reply(state)['source'], 'mailbox')
        self.assertLessEqual(len(state['asks']), reask.HISTORY)

    def test_uncertain_original_survives_rollover_and_only_reconciles(self):
        self.begin()
        first = reask.tick(CALLER, 1001.0, Sender('QUEUED', True))['asks'][0]['marker']
        bridge = mock.Mock()
        with mock.patch.object(ready, '_bridge', return_value=bridge), \
                mock.patch.object(ready, 'advance_ask') as advance, \
                mock.patch.object(ready, 'write_record') as write:
            for index in range(1, reask.HISTORY + 5):
                state = reask.tick(CALLER, 1001.0 + index * reask.INTERVAL)
            self.assertTrue(advance.called)
            for call in advance.call_args_list:
                self.assertNotIn('allow_send', call.kwargs)
                self.assertEqual(call.args[3]['marker'], first)
            write.assert_not_called()
        self.assertEqual(state['terminal_pending']['marker'], first)
        self.assertEqual(state['terminal_sends'], 1)
        self.assertEqual(state['native_receipts'], 0)


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
        self.user(json.dumps(self.reply()))
        block, reason = guard.decide(self.payload(), CALLER, 1002.0)
        self.assertTrue(block)
        self.assertIn('successor release installed', reason)
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

    def test_cli_entry_does_not_trust_raw_daemon_environment(self):
        self.begin()
        env = dict(os.environ, HOME=str(self.home), CMUX_SURFACE_ID=CALLER)
        # 子进程 HOME 不同于 mock，需在其 HOME 下重建等待段。
        code = ('import sys; sys.path.insert(0, %r); import executor_reask as r;'
                'r.start(%r, %r, "r24")' % (str(HERE), CALLER, SUP))
        subprocess.run([sys.executable, '-B', '-c', code], env=env, check=True)
        run = subprocess.run([sys.executable, '-B', str(HERE / 'cmux_executor_reask_stop_guard.py')],
                             input=json.dumps(self.payload()), capture_output=True, text=True,
                             env=env, check=True)
        self.assertEqual(run.stdout, '')
        self.assertNotIn('INTERNAL_ERROR', run.stderr)

    def test_authenticated_hook_blocks_without_terminal_input(self):
        self.begin()
        with mock.patch.object(guard.sys, 'stdin', io.StringIO(json.dumps(self.payload()))), \
             mock.patch.object(guard.hook_identity, 'evaluation', return_value=contextlib.nullcontext()), \
             mock.patch.object(guard.hook_identity, 'identity', return_value=(WORKSPACE, CALLER)), \
             mock.patch.object(reask, 'default_send') as send, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(guard.main(), 0)
        self.assertEqual(json.loads(output.getvalue())['decision'], 'block')
        send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
