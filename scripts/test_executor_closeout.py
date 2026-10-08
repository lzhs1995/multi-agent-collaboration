"""Real hook entrypoint tests using temporary markers and the original journal."""
import copy
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

import executor_closeout as closeout
import offline_test_hook

# The user's exact sentence, spelled independently of the module under test.
COMPLETION = '完成，建议检查 usage: /context'


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
        self.env = dict(os.environ, CMUX_WORKSPACE_ID=self.workspace, CMUX_SURFACE_ID=self.surface)
        self.line = closeout.handoff_line(dict(task_id='test-task', report=str(self.report)))

    def write(self, path, value):
        path.write_text(json.dumps(value))

    def sha(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def evidence(self):
        return closeout.terminal_report(self.marker, self.workspace, self.surface)

    def hook(self, name, event, final=None, env=None, reentry=False):
        data = dict(hook_event_name=event, tool_name='Bash',
                    tool_input=dict(command='touch MUST_NOT_RUN'),
                    last_assistant_message=final or self.line, stop_hook_active=reentry)
        return subprocess.run(offline_test_hook.command(Path(__file__).with_name(name), self.active),
                              input=json.dumps(data), text=True, capture_output=True,
                              env=env or self.env, timeout=5)

    def pre(self, **kwargs):
        return self.hook('cmux_executor_closeout_guard.py', 'PreToolUse', **kwargs)

    def stop(self, **kwargs):
        return self.hook('cmux_consensus_stop_guard.py', 'Stop', **kwargs)

    def test_terminal_unknown_seals_tools_allows_honest_stop_without_writing(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertIsNotNone(self.evidence())
        self.assertEqual(self.pre().returncode, 2)
        self.assertIn('EXECUTOR_CLOSEOUT', self.pre().stderr)
        self.assertEqual(self.stop().returncode, 0)
        self.assertFalse(self.receipt.exists())
        self.assertTrue(self.marker_path.exists())
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_unknown_cannot_claim_delivery_or_consensus(self):
        for text in ('DONE|test-task|nonce', 'callback confirmed', self.line + ' consensus-validation PASS'):
            with self.subTest(text=text):
                self.assertEqual(self.stop(final=text).returncode, 2)

    def test_no_input_return_can_handoff_without_mandatory_retry(self):
        self.attempt.update(phase='NO_INPUT', events=[])
        self.write(self.attempt_path, self.attempt)
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

    def snapshot(self):
        return {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def later_task(self, **changes):
        # A fresh v2 marker the same supervisor armed after the original attempt.
        armed = datetime.now(timezone.utc) - timedelta(seconds=5)
        marker = dict(task_id='next-task', workspace_uuid=self.workspace,
                      artifact_root=str(self.root / 'next'), ttl_seconds=21600,
                      armed_at=armed.isoformat(),
                      participants=copy.deepcopy(self.marker['participants']))
        marker.update(changes)
        # None drops the field: a missing field, not a JSON null.
        marker = {k: v for k, v in marker.items() if v is not None}
        path = self.active / self.workspace / 'later.json'
        path.parent.mkdir(exist_ok=True)
        self.write(path, marker)
        return path

    def test_completion_sentence_is_users_exact_text(self):
        self.assertEqual(closeout.COMPLETION_SENTENCE, COMPLETION)

    def test_first_stop_accepts_only_the_two_exact_closeouts(self):
        before = self.snapshot()
        for final in (self.line, self.line + '\n' + COMPLETION, '\n' + self.line + '\n'):
            with self.subTest(final=final):
                result = self.stop(final=final)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
        confirmed = self.line.replace('CALLBACK_UNCONFIRMED', 'CALLBACK_CONFIRMED')
        rejected = [
            self.line + ' ' + COMPLETION, self.line + '\n\n' + COMPLETION,
            self.line + '\r\n' + COMPLETION, COMPLETION + '\n' + self.line, COMPLETION,
            self.line + '\n' + COMPLETION + '\nextra', self.line + '\n' + COMPLETION + ' extra',
            self.line + '\n' + COMPLETION + '。', self.line + '\n完成,建议检查 usage: /context',
            self.line + '\n建议检查 usage: /context', self.line + '\n完成，建议检查usage:/context',
            'Summary\n' + self.line + '\n' + COMPLETION, '```\n' + self.line + '\n```',
            self.line + '\nBLOCKED: work incomplete\n' + COMPLETION,
            confirmed, confirmed + '\n' + COMPLETION, 'DONE|test-task|nonce\n' + COMPLETION,
            self.line.replace('report.md', 'other.md') + '\n' + COMPLETION,
            self.line.replace('TASK_ID=test-task', 'TASK_ID=other-task') + '\n' + COMPLETION,
        ]
        for final in rejected:
            with self.subTest(final=final):
                result = self.stop(final=final)
                self.assertEqual(result.returncode, 2)
                self.assertIn('EXECUTOR_CLOSEOUT', result.stderr)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(sorted(self.journal.glob('attempt-*.json')), [self.attempt_path])
        self.assertFalse(self.receipt.exists())

    def test_hint_is_exactly_what_first_stop_accepts(self):
        pre = self.pre()
        self.assertEqual(pre.returncode, 2)
        status = [line for line in pre.stderr.splitlines() if line.startswith('STATUS:')]
        self.assertEqual(status, [self.line])
        quoted = re.findall(r'put "([^"]+)" alone on the very next line', pre.stderr)
        self.assertEqual(quoted, [COMPLETION])
        for final in (status[0], status[0] + '\n' + quoted[0]):
            with self.subTest(final=final):
                self.assertEqual(self.stop(final=final).returncode, 0)
        blocked = self.stop(final='still working')
        self.assertEqual(blocked.returncode, 2)
        instructions = closeout.closeout_instructions(self.evidence())
        self.assertIn(instructions, pre.stderr)
        self.assertIn(instructions, blocked.stderr)
        self.assertNotIn('Inspect its submission', blocked.stderr)
        self.assertNotIn('Produce real evidence', blocked.stderr)

    def test_hook_never_supplies_completion_for_the_executor(self):
        # The hook only allows or blocks; no output line is the sentence itself.
        outputs = [self.pre().stderr]
        for final in ('Work incomplete; BLOCKED on review', self.line + '\nBLOCKED',
                      'BLOCKED\n' + COMPLETION):
            with self.subTest(final=final):
                result = self.stop(final=final)
                self.assertEqual(result.returncode, 2)
                outputs.append(result.stderr)
        for text in outputs:
            self.assertNotIn(COMPLETION, [line.strip() for line in text.splitlines()])
            self.assertFalse(text.rstrip().endswith('/context'))
        allowed = self.stop(final=self.line)
        self.assertEqual((allowed.returncode, allowed.stdout, allowed.stderr), (0, '', ''))

    def test_first_stop_passes_without_reentry_exception_and_reentry_kept(self):
        complete = self.line + '\n' + COMPLETION
        self.assertEqual(self.stop(final=complete, reentry=False).returncode, 0)
        self.assertEqual(self.stop(final='still working', reentry=False).returncode, 2)
        for final in ('still working', complete):
            with self.subTest(final=final):
                result = self.stop(final=final, reentry=True)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
        self.assertFalse(self.receipt.exists())

    def test_completion_line_needs_the_same_frozen_evidence(self):
        complete = self.line + '\n' + COMPLETION
        with self.lock.open('rb') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.stop(final=complete).returncode, 2)
        original = copy.deepcopy(self.attempt)
        for key in ('completion_nonce', 'report_sha256', 'task_pack_sha256'):
            with self.subTest(key=key):
                bad = copy.deepcopy(original)
                bad['binding'][key] = 'wrong'
                self.write(self.attempt_path, bad)
                self.assertEqual(self.stop(final=complete).returncode, 2)
        self.write(self.attempt_path, original)
        self.assertEqual(self.stop(final=complete).returncode, 0)
        self.report.write_text('Changed after callback')
        self.assertEqual(self.stop(final=complete).returncode, 2)
        self.report.write_text('Bounded findings; known gaps remain.\n')
        self.attempt_path.unlink()
        self.assertEqual(self.stop(final=complete).returncode, 2)

    def test_later_task_from_same_supervisor_unseals_without_settling(self):
        before = self.snapshot()
        self.assertEqual(self.pre().returncode, 2)
        later = self.later_task()
        self.assertEqual(self.pre().returncode, 0)
        self.assertEqual(self.stop(final='Starting the new bounded task.').returncode, 0)
        self.assertIsNotNone(self.evidence())  # original evidence is untouched
        self.assertFalse(self.receipt.exists())
        self.assertTrue(self.marker_path.exists())
        after = self.snapshot()
        self.assertEqual(after.pop(later), later.read_bytes())
        self.assertEqual(before, after)

    def test_later_task_keeps_its_own_callback_obligation(self):
        self.later_task()
        next_root = self.root / 'next'
        next_root.mkdir()
        self.write(next_root / 'task-pack.json', dict(
            self.pack, task_id='next-task', report=str(next_root / 'report.md'),
            completion_receipt=str(next_root / 'receipt.json')))
        result = self.stop(final='Starting the new bounded task.')
        self.assertEqual(result.returncode, 2)
        self.assertIn('completion callback receipt missing', result.stderr)
        self.assertNotIn('EXECUTOR_CLOSEOUT', result.stderr)

    def test_supersession_requires_same_identity_and_later_arming(self):
        now = time.time()
        self.attempt.update(started_at_epoch=now - 60, ended_at_epoch=now - 20, events=[
            dict(phase='PASTE_INTENT', at_epoch=now - 50),
            dict(phase='POST_ENTER_OBSERVATION', at_epoch=now - 25)])
        self.write(self.attempt_path, self.attempt)
        self.assertIsNotNone(self.evidence())

        def stamp(offset, naive=False):
            # Naive means local wall time: the only reading a lenient parser has.
            zone = None if naive else timezone.utc
            return datetime.fromtimestamp(now + offset, zone).isoformat()

        supervisor, executor = copy.deepcopy(self.marker['participants'])
        cases = dict(
            other_executor=dict(participants=[supervisor, dict(executor, surface_uuid='other')]),
            other_supervisor=dict(participants=[dict(supervisor, surface_uuid='other'), executor]),
            no_supervisor=dict(participants=[executor]),
            two_supervisors=dict(participants=[supervisor, supervisor, executor]),
            two_executor_rows=dict(participants=[supervisor, executor, executor]),
            other_workspace=dict(workspace_uuid='other'),
            missing_workspace=dict(workspace_uuid=None),
            same_task=dict(task_id='test-task'),
            same_root=dict(artifact_root=str(self.root)),
            relative_root=dict(artifact_root='next'),
            armed_before_attempt_end=dict(armed_at=stamp(-30)),
            naive_armed_at=dict(armed_at=stamp(-5, naive=True)),
            missing_armed_at=dict(armed_at=None),
            future_armed_at=dict(armed_at=stamp(3600)),
            stale=dict(armed_at=stamp(-10), ttl_seconds=1),
            missing_ttl=dict(ttl_seconds=None),
            zero_ttl=dict(ttl_seconds=0),
            invalid_ttl=dict(ttl_seconds='unknown'),
            boolean_ttl=dict(ttl_seconds=True),
            nonfinite_ttl=dict(ttl_seconds=float('nan')),
            root_alias=dict(artifact_root=str(self.root / 'child' / '..')),
        )
        for name, change in cases.items():
            with self.subTest(case=name):
                self.later_task(**change)
                self.assertEqual(self.pre().returncode, 2)
                self.assertEqual(self.stop(final='Starting the new bounded task.').returncode, 2)
        self.later_task(armed_at=stamp(-5))  # positive control on the same journal
        self.assertEqual(self.pre().returncode, 0)
        self.assertEqual(self.stop(final='Starting the new bounded task.').returncode, 0)

    def test_queue_resume_effective_end_prevents_premature_new_task_scope(self):
        now = time.time()
        self.attempt.update(
            started_at_epoch=now - 70, ended_at_epoch=now - 40,
            phase='POST_QUEUE_TAB_OBSERVATION', events=[
                dict(phase='PASTE_INTENT', at_epoch=now - 60),
                dict(phase='ENTER_INTENT', at_epoch=now - 55),
                dict(phase='POST_ENTER_OBSERVATION', at_epoch=now - 50),
                dict(phase='QUEUE_TAB_INTENT', at_epoch=now - 20),
                dict(phase='POST_QUEUE_TAB_OBSERVATION', at_epoch=now - 15)])
        self.write(self.attempt_path, self.attempt)
        self.assertEqual(self.evidence()['ended_at_epoch'], now - 15)
        self.later_task(armed_at=datetime.fromtimestamp(now - 30, timezone.utc).isoformat())
        self.assertEqual(self.pre().returncode, 2)
        self.assertEqual(self.stop(final='Starting the new bounded task.').returncode, 2)
        before = self.snapshot()
        self.assertEqual(self.stop(final=self.line + '\n' + COMPLETION).returncode, 0)
        self.assertEqual(before, self.snapshot())
        self.later_task(armed_at=datetime.fromtimestamp(now - 5, timezone.utc).isoformat())
        self.assertEqual(self.pre().returncode, 0)
        self.assertEqual(self.stop(final='Starting the new bounded task.').returncode, 0)
        self.assertFalse(self.receipt.exists())

    def test_old_consensus_evidence_does_not_override_later_task_evidence(self):
        self.later_task()
        root = self.root / 'next'
        root.mkdir()
        for name in ('validation.json', 'consensus-validation.json'):
            self.write(root / name, dict(task_id='next-task', status='PASS'))
        before = self.snapshot()
        self.assertEqual(self.stop(final='validation PASS').returncode, 0)
        self.assertEqual(before, self.snapshot())
        self.write(root / 'validation.json', dict(task_id='next-task', status='FAIL'))
        result = self.stop(final='validation PASS')
        self.assertEqual(result.returncode, 2)
        self.assertIn('next-task', result.stderr)
        self.assertFalse(self.receipt.exists())


if __name__ == '__main__':
    unittest.main()
