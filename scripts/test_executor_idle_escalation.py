"""Executor idle escalation: an armed executor may not dead-wait for dispatch.

Hook cases use real stdin/exit codes through offline_test_hook with a private
HOME and marker directory. Escalation cases use a fake bridge that records
calls; no terminal input is ever sent.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest import mock
import uuid

import cmux_bridge
import executor_idle_escalation as idle
import offline_test_hook

HOOK = Path(os.environ.get('STOP_GUARD_UNDER_TEST',
                           Path(__file__).with_name('cmux_consensus_stop_guard.py')))


def iso(seconds_ago):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


class FakeBridge:
    def __init__(self, caller, fail=None):
        self.caller, self.fail, self.sent = caller, fail, []

    def pin_workspace(self, surface):
        return dict(caller_surface_uuid=self.caller)

    def submit_text(self, surface, text, marker=None):
        self.sent.append((surface, text, marker))
        if self.fail:
            raise self.fail


class IdleEscalationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='idle-escalation-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.artifacts = self.root / 'artifacts'
        self.artifacts.mkdir()
        self.active = self.root / 'active'
        self.workspace, self.executor, self.supervisor = [
            str(uuid.uuid4()).upper() for _ in range(3)]
        self.env_patch = mock.patch.dict(os.environ, HOME=str(self.home))
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.arm(660)

    def arm(self, seconds_ago, **extra):
        self.marker = dict(
            marker_version=2, collaboration_id='collab-1', task_id='idle-task',
            artifact_root=str(self.artifacts), workspace_uuid=self.workspace,
            participants=[
                dict(role='supervisor', ordinal=0, surface_ref='surface:1',
                     surface_uuid=self.supervisor, provider='codex'),
                dict(role='executor', ordinal=1, surface_ref='surface:2',
                     surface_uuid=self.executor, provider='claude')],
            armed_at=iso(seconds_ago), ttl_seconds=21600, **extra)
        path = self.active / self.workspace / 'collab-1.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.marker))

    def hook(self, surface=None, **payload):
        data = dict(hook_event_name='Stop', last_assistant_message='Waiting for dispatch.')
        data.update(payload)
        env = dict(os.environ, HOME=str(self.home), CMUX_WORKSPACE_ID=self.workspace,
                   CMUX_SURFACE_ID=surface or self.executor, PYTHONDONTWRITEBYTECODE='1')
        return subprocess.run(offline_test_hook.command(HOOK, self.active),
                              input=json.dumps(data), text=True, capture_output=True,
                              env=env, timeout=10)

    def record(self, tier, recorded_at):
        status = idle.idle_status(self.marker, self.executor)
        directory = Path(status['record_dir'])
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f'tier-{tier}.json').write_text(json.dumps(dict(
            tier=tier, recorded_at=recorded_at, outcome='NO_INPUT')))

    # --- ladder shape -------------------------------------------------------
    def test_ladder_is_increasing_with_non_decreasing_gaps(self):
        self.assertTrue(all(a < b for a, b in zip(idle.LADDER_SECONDS, idle.LADDER_SECONDS[1:])))
        g = idle.gaps()
        self.assertTrue(all(a <= b for a, b in zip(g, g[1:])), g)
        self.assertEqual(sum(g), idle.LADDER_SECONDS[-1])

    # --- Stop hook ----------------------------------------------------------
    def test_fresh_arm_allows_turn_end(self):
        self.arm(120)
        r = self.hook()
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_idle_past_first_tier_blocks_with_escalate_command(self):
        r = self.hook()
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn('executor idle escalation due', r.stderr)
        self.assertIn('tier 1/3', r.stderr)
        self.assertIn('executor_idle_escalation.py escalate --task-id idle-task', r.stderr)
        self.assertNotIn('Produce real evidence', r.stderr)

    def test_recorded_tier_allows_until_next_gap(self):
        self.record(1, time.time())
        r = self.hook()
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_second_tier_blocks_after_its_gap(self):
        self.arm(2400)
        self.record(1, time.time() - 1800)
        r = self.hook()
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn('tier 2/3', r.stderr)

    def test_late_first_record_cannot_cascade(self):
        """Idle 70 min but tier 1 just recorded: tier 2 waits its own gap."""
        self.arm(4200)
        self.record(1, time.time())
        self.assertEqual(self.hook().returncode, 0)
        status = idle.idle_status(self.marker, self.executor)
        self.assertGreaterEqual(status['next_due_at'], time.time() + idle.gaps()[1] - 5)

    def test_exhausted_ladder_allows_turn_end(self):
        self.arm(9000)
        for tier in (1, 2, 3):
            self.record(tier, time.time() - 5000 + tier)
        r = self.hook()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(idle.idle_status(self.marker, self.executor)['exhausted'])

    def test_supervisor_is_not_subject_to_executor_idle_rule(self):
        self.assertEqual(self.hook(surface=self.supervisor).returncode, 0)

    def test_stop_reentry_still_terminates(self):
        self.assertEqual(self.hook(stop_hook_active=True).returncode, 0)

    def test_recent_supervisor_activity_resets_the_clock(self):
        self.arm(4000, last_activity_at=iso(60))
        self.assertEqual(self.hook().returncode, 0)

    def test_draft_pack_write_resets_the_clock(self):
        (self.artifacts / 'task-pack.json').write_text(json.dumps(dict(draft=True)))
        self.assertEqual(self.hook().returncode, 0)

    def test_handshake_receipt_resets_the_clock(self):
        (self.artifacts / 'handshake-receipt.json').write_text(json.dumps(dict(
            executors=[dict(executor='surface:2', created_at=iso(30))])))
        self.assertEqual(self.hook().returncode, 0)

    # --- status --------------------------------------------------------------
    def test_finalized_pack_is_not_idle(self):
        (self.artifacts / 'task-pack.json').write_text(json.dumps(dict(draft=False)))
        status = idle.idle_status(self.marker, self.executor)
        self.assertFalse(status['applicable'])
        self.assertIn('finalized', status['reason'])

    def test_bare_timestamp_is_not_guessed(self):
        marker = dict(self.marker, armed_at='2026-10-08T04:16:47')
        status = idle.idle_status(marker, self.executor)
        self.assertFalse(status['applicable'])

    # --- escalate ------------------------------------------------------------
    def test_escalate_sends_once_and_records_journal_outcome(self):
        bridge = FakeBridge(self.supervisor)
        result = idle.escalate(self.marker, self.executor, bridge)
        self.assertEqual(result['result'], 'ESCALATED')
        self.assertEqual(len(bridge.sent), 1)
        surface, text, tag = bridge.sent[0]
        self.assertEqual(surface, self.supervisor)
        self.assertTrue(text.startswith(tag) and tag.startswith('EXECUTOR_IDLE_ESCALATION_T1_'))
        self.assertFalse(cmux_bridge._looks_like_task_dispatch(text))
        self.assertEqual(result['outcome'], 'NO_INPUT')
        notice = json.loads(Path(result['notice']).read_text())
        self.assertEqual(notice['text'], text)
        again = idle.escalate(self.marker, self.executor, bridge)
        self.assertEqual(again['result'], 'NOT_DUE')
        self.assertEqual(len(bridge.sent), 1)

    def test_not_due_sends_nothing(self):
        self.arm(60)
        bridge = FakeBridge(self.supervisor)
        self.assertEqual(idle.escalate(self.marker, self.executor, bridge)['result'], 'NOT_DUE')
        self.assertEqual(bridge.sent, [])

    def test_transport_exception_is_recorded_not_retried(self):
        bridge = FakeBridge(self.supervisor, fail=RuntimeError('AGENT_INPUT_REQUIRED'))
        result = idle.escalate(self.marker, self.executor, bridge)
        self.assertEqual(len(bridge.sent), 1)
        self.assertIn('AGENT_INPUT_REQUIRED', result['error'])
        self.assertEqual(idle.idle_status(self.marker, self.executor)['records'][0]['tier'], 1)

    def test_unavailable_bridge_is_recorded_as_transport_error(self):
        bridge = idle._UnavailableBridge(ImportError('no bridge'))
        result = idle.escalate(self.marker, self.executor, bridge)
        self.assertEqual(result['outcome'], 'TRANSPORT_ERROR')
        self.assertFalse(idle.idle_status(self.marker, self.executor)['due_tier'])

    def test_existing_tier_file_blocks_a_second_send(self):
        status = idle.idle_status(self.marker, self.executor)
        Path(status['record_dir']).mkdir(parents=True)
        (Path(status['record_dir']) / 'tier-1.json').write_text('{"tier": 1}')
        bridge = FakeBridge(self.supervisor)
        with self.assertRaises(FileExistsError):
            idle.escalate(self.marker, self.executor, bridge)
        self.assertEqual(bridge.sent, [])

    def test_outcome_reads_the_message_journal(self):
        tag = 'EXECUTOR_IDLE_ESCALATION_T1_TEST'
        key = hashlib.sha256(json.dumps([self.supervisor, tag], sort_keys=True).encode()).hexdigest()
        journal = self.home / '.local/state/multi-agent-collaboration/message-dispatch-v1' / key
        journal.mkdir(parents=True)
        bridge = FakeBridge(self.supervisor)
        (journal / 'attempt-0001.json').write_text(json.dumps(dict(phase='POST_ENTER_OBSERVATION')))
        self.assertEqual(idle._message_outcome(bridge, self.supervisor, tag), 'SUBMITTED_UNCONFIRMED')
        (journal / 'receipt.json').write_text('{}')
        self.assertEqual(idle._message_outcome(bridge, self.supervisor, tag), 'CONFIRMED')

    # --- wait ----------------------------------------------------------------
    def test_wait_returns_when_escalation_is_due(self):
        with mock.patch.object(idle, 'ACTIVE_DIR', self.active):
            code, result = idle.wait('idle-task', self.executor, 30, 5, sleep=lambda s: None)
        self.assertEqual((code, result['result']), (6, 'ESCALATION_DUE'))

    def test_wait_returns_when_dispatch_arrives(self):
        self.arm(60)
        def arrive(_seconds):
            (self.artifacts / 'task-pack.json').write_text(json.dumps(dict(draft=False)))
        with mock.patch.object(idle, 'ACTIVE_DIR', self.active):
            code, result = idle.wait('idle-task', self.executor, 300, 5, sleep=arrive)
        self.assertEqual(code, 0, result)

    def test_wait_is_bounded(self):
        self.arm(60)
        ticks = iter(range(0, 10_000, 100))
        clock = lambda: time.time() + next(ticks)
        with mock.patch.object(idle, 'ACTIVE_DIR', self.active):
            code, result = idle.wait('idle-task', self.executor, 150, 50,
                                     clock=clock, sleep=lambda s: None)
        self.assertIn(code, (6, 7), result)


if __name__ == '__main__':
    unittest.main()
