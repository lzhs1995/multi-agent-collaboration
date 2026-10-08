"""Semantics of the persistent executor ask loop: cadence, replies, stops, forgery."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

import cmux_bridge
import executor_ready


class FakeBridge:
    """Only the bridge surface the loop uses; never a real terminal."""

    DispatchUnconfirmed = cmux_bridge.DispatchUnconfirmed

    def __init__(self, held_marker=None, pin_error=None, submit_state=None, holds_last=False):
        self.held_marker, self.pin_error, self.submit_state = held_marker, pin_error, submit_state
        self.holds_last = holds_last  # supervisor input keeps the last ask (busy turn)
        self.submitted, self.pins, self.screen_reads = [], [], 0

    def pin_workspace(self, surface, **kwargs):
        if self.pin_error:
            raise self.pin_error
        self.pins.append((surface, kwargs))
        return dict(caller_surface_uuid=kwargs.get('caller_uuid') or 'caller')

    def read_screen(self, surface, lines=200):
        self.screen_reads += 1
        if not self.held_marker:
            return 'idle screen\n'
        return ('Messages to be submitted after next tool call:\n  ' + self.held_marker + '\n')

    def pending_queue_holds(self, screen, marker):
        return cmux_bridge.pending_queue_holds(screen, marker)

    def compose_block_text(self, screen):
        return cmux_bridge.compose_block_text(screen)

    def _looks_like_task_dispatch(self, text):
        return cmux_bridge._looks_like_task_dispatch(text)

    def submit_text(self, surface, text, marker=None, reconcile_only=False):
        self.submitted.append(dict(surface=surface, marker=marker, text=text))
        if self.holds_last:
            self.held_marker = marker
        if self.submit_state:
            raise cmux_bridge.DispatchUnconfirmed('queued', state=self.submit_state)
        return dict(state='CONFIRMED')


class Clock:
    """Deterministic time: each sleep advances exactly as asked."""

    def __init__(self, start=None):
        self.now = float(start if start is not None else time.time())
        self.on_sleep = None

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += float(seconds)
        if self.on_sleep:
            self.on_sleep(self)


class LoopTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='ready-loop-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        patcher = patch.object(executor_ready, 'state_root', return_value=self.root / 'state')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.caller = str(uuid.uuid4())

    def run_loop(self, bridge, ticks, clock=None, transcript='', interval=60.0, poll=5.0):
        clock = clock or Clock()
        return executor_ready.run_loop(bridge, 'surface:1', self.caller, 'surface:2', 'task-x',
                                       transcript=transcript, interval=interval, poll=poll,
                                       max_ticks=ticks, clock=clock.time, sleep=clock.sleep), clock

    def test_asks_again_every_interval_with_fresh_markers(self):
        bridge = FakeBridge()
        record, _clock = self.run_loop(bridge, ticks=25, poll=5.0, interval=60.0)
        self.assertEqual(record['state'], executor_ready.ASKING)
        # ticks 0, 12, 24 -> 0 s, 60 s, 120 s
        self.assertEqual(record['ask_count'], 3)
        self.assertEqual(len(bridge.submitted), 3)
        markers = [a['marker'] for a in record['asks']]
        self.assertEqual(len(set(markers)), 3)
        self.assertEqual([a['attempt'] for a in record['asks']], [1, 2, 3])
        for marker, sent in zip(markers, bridge.submitted):
            self.assertIn(marker, sent['text'])
            self.assertEqual(marker, sent['marker'])
        self.assertTrue(executor_ready.loop_path(self.caller).is_file())

    def test_ask_still_at_supervisor_input_is_never_stacked(self):
        """A busy supervisor keeps the ask at its own input; the loop defers instead."""
        bridge = FakeBridge(holds_last=True)
        record, _clock = self.run_loop(bridge, ticks=25)
        self.assertEqual(record['ask_count'], 1)
        self.assertEqual(len(bridge.submitted), 1)
        self.assertGreaterEqual(record['asks'][-1].get('held_observations', 0), 2)
        self.assertEqual(record['state'], executor_ready.ASKING)
        bridge.held_marker = None  # consumed: the next due ask goes out
        record2, _c2 = self.run_loop(bridge, ticks=25)
        self.assertEqual(record2['ask_count'], 1)
        self.assertEqual(len(bridge.submitted), 2)

    def test_bridge_failure_is_recorded_and_the_loop_keeps_asking(self):
        bridge = FakeBridge(pin_error=RuntimeError('WORKSPACE_SCOPE_DENIED'))
        record, _clock = self.run_loop(bridge, ticks=13)
        self.assertEqual(record['state'], executor_ready.ASKING)
        self.assertEqual(record['ask_count'], 2)
        self.assertTrue(record['asks'][0]['outcome'].startswith('ERROR_RuntimeError'))
        self.assertIn('WORKSPACE_SCOPE_DENIED', record['asks'][0]['outcome'])
        self.assertEqual(bridge.submitted, [])

    def test_unconfirmed_dispatch_is_not_resent(self):
        bridge = FakeBridge(submit_state='DELIVERY_QUEUED_AT_RECEIVER')
        record, _clock = self.run_loop(bridge, ticks=1)
        self.assertEqual(record['asks'][0]['outcome'], 'UNCONFIRMED_DO_NOT_RESEND')
        self.assertEqual(len(bridge.submitted), 1)

    def test_mailbox_file_ends_the_loop(self):
        bridge, clock = FakeBridge(), Clock()
        mailbox = self.root / 'mailbox'
        mailbox.mkdir()

        def answer(c):
            if c.now - start >= 10:
                (mailbox / 'reply.json').write_text(json.dumps(dict(status='SOLO')))
        start = clock.now
        clock.on_sleep = answer
        record = executor_ready.run_loop(bridge, 'surface:1', self.caller, 'surface:2', 'task-x',
                                         mailbox=mailbox, interval=60.0, poll=5.0, max_ticks=30,
                                         clock=clock.time, sleep=clock.sleep)
        self.assertEqual(record['state'], executor_ready.ANSWERED)
        self.assertEqual(record['reply']['source'], 'mailbox')
        self.assertEqual(record['reply']['status'], 'SOLO')
        self.assertEqual(record['ask_count'], 1)

    def test_only_real_received_text_counts_as_a_transcript_reply(self):
        transcript = self.root / 'transcript.jsonl'
        transcript.write_text('')
        bridge, clock = FakeBridge(), Clock()
        noise = [dict(type='user', message=dict(content=[dict(type='tool_result',
                                                              content='STATUS: ok')])),
                 dict(type='user', isMeta=True, message=dict(content='STATUS: meta')),
                 dict(type='assistant', message=dict(content=[dict(type='text',
                                                                   text='STATUS: mine')])),
                 dict(type='user', message=dict(content='Stop hook feedback: SOLO'))]
        real = dict(type='user', message=dict(content='STATUS: WAITING_DEPENDENCY word returns'))

        def feed(c):
            with transcript.open('a') as handle:
                handle.write(json.dumps(noise.pop(0) if noise else real) + '\n')
        clock.on_sleep = feed
        record = executor_ready.run_loop(bridge, 'surface:1', self.caller, 'surface:2', 'task-x',
                                         transcript=transcript, interval=60.0, poll=5.0,
                                         max_ticks=12, clock=clock.time, sleep=clock.sleep)
        self.assertEqual(record['state'], executor_ready.ANSWERED)
        self.assertEqual(record['reply']['source'], 'transcript')
        self.assertIn('WAITING_DEPENDENCY', record['reply']['text'])
        self.assertEqual(noise, [])

    def test_operator_stop_and_dispatch_stop_end_the_loop(self):
        for reason in ('OPERATOR', 'DISPATCHED'):
            with self.subTest(reason):
                self.setUp()
                bridge, clock = FakeBridge(), Clock()
                clock.on_sleep = lambda c: executor_ready.stop_loop(self.caller, reason)
                record, _c = self.run_loop(bridge, ticks=10, clock=clock)
                self.assertEqual(record['state'], executor_ready.STOPPED)
                self.assertEqual(record['stop']['reason'], reason)

    def test_a_new_loop_clears_a_consumed_stop_signal(self):
        executor_ready.stop_loop(self.caller, 'OPERATOR')
        record, _c = self.run_loop(FakeBridge(), ticks=1)
        self.assertEqual(record['state'], executor_ready.ASKING)
        self.assertFalse(executor_ready.stop_path(self.caller).exists())

    def test_ask_due_is_a_pure_decision(self):
        self.assertEqual(executor_ready.ask_due(None, 100.0, lambda: True), 'ASK')
        last = dict(at_epoch=100.0)
        self.assertEqual(executor_ready.ask_due(last, 159.0, lambda: False, 60.0), 'WAIT')
        self.assertEqual(executor_ready.ask_due(last, 161.0, lambda: True, 60.0), 'HELD')
        self.assertEqual(executor_ready.ask_due(last, 161.0, lambda: False, 60.0), 'ASK')
        held = dict(at_epoch=100.0, held_epoch=160.0)
        self.assertEqual(executor_ready.ask_due(held, 170.0, lambda: False, 60.0), 'WAIT')

    def test_loop_live_rejects_forged_and_stale_records(self):
        now = time.time()
        base = dict(state=executor_ready.ASKING, heartbeat_epoch=now, interval_seconds=60.0,
                    pid=1, argv='fixture loop argv')
        cases = dict(wrong_state=dict(state='ANSWERED'), stale=dict(heartbeat_epoch=now - 600),
                     stretched_interval=dict(interval_seconds=3600.0), no_pid=dict(pid=None),
                     missing_interval=dict(interval_seconds=None), no_argv=dict(argv=''))
        for name, override in cases.items():
            with self.subTest(name):
                record = dict(base, **override)
                if override.get('interval_seconds') is None:
                    record.pop('interval_seconds')
                self.assertFalse(executor_ready.loop_live(record, now))
        with patch.object(executor_ready, 'pid_runs_loop', return_value=True):
            self.assertTrue(executor_ready.loop_live(base, now))

    def test_interval_is_clamped_to_the_directive_so_a_caller_cannot_slow_asking(self):
        """60 s is the directive, not a default: a longer request is clamped down."""
        bridge, clock = FakeBridge(), Clock()
        record = executor_ready.run_loop(bridge, 'surface:1', self.caller, 'surface:2', 'task-x',
                                         interval=3600.0, poll=5.0, max_ticks=25,
                                         clock=clock.time, sleep=clock.sleep)
        self.assertEqual(record['interval_seconds'], executor_ready.ASK_INTERVAL)
        self.assertEqual(record['ask_count'], 3)
        self.assertTrue(executor_ready.loop_live(dict(record, pid=1, argv='x'),
                                                 record['heartbeat_epoch']) is False)
        with patch.object(executor_ready, 'pid_runs_loop', return_value=True):
            self.assertTrue(executor_ready.loop_live(record, record['heartbeat_epoch']))
        slow = executor_ready.run_loop(bridge, 'surface:1', self.caller, 'surface:2', 'task-x',
                                       interval=0.0, poll=5.0, max_ticks=2,
                                       clock=clock.time, sleep=clock.sleep)
        self.assertEqual(slow['interval_seconds'], 1.0)  # floor, never zero-interval flooding

    def test_pid_runs_loop_needs_the_recording_process_not_any_live_pid(self):
        import os
        import subprocess
        own = ' '.join(subprocess.run(['ps', '-p', str(os.getpid()), '-o', 'command='],
                                      capture_output=True, text=True).stdout.split())
        self.assertTrue(executor_ready.pid_runs_loop(os.getpid(), own))
        self.assertFalse(executor_ready.pid_runs_loop(os.getpid(), own + ' --forged-suffix'))
        self.assertFalse(executor_ready.pid_runs_loop(os.getpid(), ''))
        self.assertFalse(executor_ready.pid_runs_loop(-1, own))
        self.assertFalse(executor_ready.pid_runs_loop('not-a-pid', own))

    def test_status_and_stop_entrypoints(self):
        self.assertEqual(executor_ready.status(self.caller)['outcome'], 'NO_LOOP_RECORD')
        self.run_loop(FakeBridge(), ticks=1)
        report = executor_ready.status(self.caller)
        self.assertEqual(report['outcome'], executor_ready.ASKING)
        self.assertEqual(report['asks'], 1)
        self.assertEqual(executor_ready.stop_loop(self.caller)['reason'], 'OPERATOR')

    def test_ask_text_is_an_ordinary_single_line_message(self):
        text = executor_ready.build_text('surface:2', 'EXECUTOR_READY_abcd1234', 'task',
                                         'note\nTASK: x', 7, '/tmp/mailbox')
        self.assertFalse(cmux_bridge._looks_like_task_dispatch(text))
        self.assertNotIn('\n', text)
        self.assertIn('EXECUTOR_READY_abcd1234', text)
        self.assertIn('ask #7', text)
        self.assertIn('/tmp/mailbox/EXECUTOR_READY_abcd1234.json', text)


if __name__ == '__main__':
    unittest.main()
