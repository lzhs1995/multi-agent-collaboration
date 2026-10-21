"""Work checkpoints retain real original-executor scope and monotonic evidence."""
from contextlib import ExitStack
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import executor_ready as ready
import executor_reask as reask
import executor_reask_identity as identity
import executor_work_checkpoint as work

CALLER = '11111111-1111-4111-8111-111111111111'
SUP = '22222222-2222-4222-8222-222222222222'
WORKSPACE = '33333333-3333-4333-8333-333333333333'
SESSION = '55555555-5555-4555-8555-555555555555'


class WorkCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.home = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.stack.enter_context(patch.object(Path, 'home', return_value=self.home))
        self.auth = self.stack.enter_context(patch.object(identity, 'authenticate',
            return_value=dict(session_id=SESSION, process=dict(pid=123), identity_sha256='a' * 64)))
        self.state = reask.start(CALLER, SUP, 'business-task', workspace=WORKSPACE, now=1000.)
        self.state['executor_identity'] = dict(session_id=SESSION)
        self.state['terminal_pending'] = dict(marker='old', outcome='UNKNOWN')
        self.state['paused_tasks'] = [dict(task_id='business-task', collaboration_id='original')]
        reask.save(self.state)
        self.order = self.home / 'work-order.json'
        self.order.write_text('{"objective":"Review the bounded candidate"}\n')
        self.evidence = self.home / 'result.json'
        self.evidence.write_text('{"negative_control":"REJECT","positive":"PASS"}\n')
        self.record_path = self.home / 'checkpoint-record.json'

    def pin(self, path):
        raw = path.read_bytes()
        return dict(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())

    def record(self, **changes):
        result = dict(schema=work.SCHEMA, caller_surface_uuid=CALLER, supervisor_uuid=SUP,
            executor_session_id=SESSION, episode_id=self.state['episode_id'], task_id='business-task',
            overall_task='business-task', work_id='candidate-review', work_order=self.pin(self.order),
            evidence=[self.pin(self.evidence)], status='IN_PROGRESS', parent_acceptance=None,
            completed_unit_ids=['case-a', 'case-b'], closed_issue_ids=['F1'],
            next_action='Send exact result to the original parent.', previous_checkpoint_sha256='ABSENT')
        result.update(changes)
        return result

    def submit(self, record=None, now=1001.):
        self.record_path.write_text(json.dumps(record or self.record(), ensure_ascii=False, indent=2) + '\n')
        sha = hashlib.sha256(self.record_path.read_bytes()).hexdigest()
        return work.checkpoint(CALLER, self.record_path, sha, now=now), sha

    def test_checkpoint_reports_unique_work_without_accepting_parent_or_changing_inquiry(self):
        before = reask.load(CALLER)
        result, sha = self.submit()
        self.assertEqual(result['outcome'], 'WORK_CHECKPOINT_RECORDED')
        self.assertEqual(result['progress']['unique_completed_units_reported'], 2)
        self.assertEqual(result['progress']['unique_closed_issues_reported'], 1)
        self.assertEqual(result['progress']['parent_acceptance'], 'UNMEASURED')
        state = reask.load(CALLER)
        for key in ('episode_id', 'state', 'ask_count', 'terminal_pending', 'paused_tasks', 'reply', 'next_due_epoch'):
            self.assertEqual(state[key], before[key])
        archive = ready.read_json(result['checkpoint'])
        self.assertEqual(archive['record_sha256'], sha)
        self.assertEqual(archive['record']['work_order'], self.pin(self.order))
        self.assertEqual(archive['input_operations'], 0)
        self.assertEqual(reask.summary(state)['work_progress'], result['progress'])

    def test_exact_duplicate_is_idempotent_and_progress_chain_counts_unique_units(self):
        first, sha = self.submit()
        before = reask.state_path(CALLER).read_bytes()
        again = work.checkpoint(CALLER, self.record_path, sha, now=1002.)
        self.assertEqual(again['outcome'], 'WORK_CHECKPOINT_ALREADY_RECORDED')
        self.assertEqual(reask.state_path(CALLER).read_bytes(), before)
        second, sha2 = self.submit(self.record(previous_checkpoint_sha256=sha,
            completed_unit_ids=['case-a', 'case-b', 'case-c'], closed_issue_ids=['F1', 'F2'],
            status='READY_FOR_PARENT_REVIEW'), now=1003.)
        self.assertEqual(second['progress']['unique_completed_units_reported'], 3)
        self.assertEqual(second['progress']['unique_closed_issues_reported'], 2)
        third, _sha = self.submit(self.record(work_id='another-work', completed_unit_ids=['case-b', 'case-d'],
            closed_issue_ids=['F1']), now=1004.)
        self.assertEqual(third['progress']['unique_completed_units_reported'], 4)
        self.assertEqual(third['progress']['work_orders_checkpointed'], 2)
        self.assertNotEqual(sha, sha2)

    def test_bad_binding_parent_acceptance_and_duplicate_progress_never_write_state(self):
        original = reask.state_path(CALLER).read_bytes()
        variants = [
            dict(executor_session_id='foreign'), dict(episode_id='foreign'), dict(supervisor_uuid=CALLER),
            dict(task_id='foreign'), dict(overall_task='foreign'), dict(parent_acceptance=True),
            dict(completed_unit_ids=['x', 'x']), dict(closed_issue_ids=['F1', 'F1']),
            dict(evidence=[dict(self.pin(self.evidence), sha256='b' * 64)])
        ]
        for changed in variants:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.submit(self.record(**changed))
            self.assertEqual(reask.state_path(CALLER).read_bytes(), original)

    def test_original_work_cannot_change_and_closed_coverage_cannot_regress(self):
        _, sha = self.submit()
        before = reask.state_path(CALLER).read_bytes()
        variants = [
            dict(previous_checkpoint_sha256='bad'),
            dict(previous_checkpoint_sha256=sha, completed_unit_ids=['case-a']),
            dict(previous_checkpoint_sha256=sha, closed_issue_ids=[]),
            dict(previous_checkpoint_sha256=sha, status='ACCEPTED_FOR_EXECUTION'),
            dict(previous_checkpoint_sha256=sha, work_order=self.pin(self.evidence))
        ]
        for changed in variants:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.submit(self.record(**changed), now=1002.)
            self.assertEqual(reask.state_path(CALLER).read_bytes(), before)
        with self.assertRaisesRegex(ValueError, 'EPOCH_REGRESSED'):
            self.submit(self.record(previous_checkpoint_sha256=sha), now=1000.)

    def test_changed_input_or_wrong_hash_and_foreign_controller_leave_work_unaccepted(self):
        record = self.record()
        self.order.write_bytes(self.order.read_bytes() + b' ')
        before = reask.state_path(CALLER).read_bytes()
        with self.assertRaisesRegex(ValueError, 'PIN_CHANGED'):
            self.submit(record)
        self.assertEqual(reask.state_path(CALLER).read_bytes(), before)
        self.record_path.write_text(json.dumps(self.record()))
        with self.assertRaisesRegex(ValueError, 'RECORD_SHA'):
            work.checkpoint(CALLER, self.record_path, 'b' * 64, now=1001.)
        state = reask.load(CALLER)
        state['controller']['source'] = '/foreign'
        reask.save(state)
        self.auth.reset_mock()
        result, _sha = self.submit()
        self.assertEqual(result['outcome'], 'ORIGINAL_CONTROLLER_REQUIRED')
        self.auth.assert_not_called()

    def test_original_executor_authentication_failure_precedes_all_checkpoint_writes(self):
        before = reask.state_path(CALLER).read_bytes()
        with patch.object(identity, 'authenticate', side_effect=ValueError('foreign original executor')):
            with self.assertRaisesRegex(ValueError, 'foreign'):
                self.submit()
        self.assertEqual(reask.state_path(CALLER).read_bytes(), before)
        self.assertFalse((reask.root() / 'work-checkpoints').exists())

    def test_archive_sync_failure_retries_same_record_and_never_replaces_original_receipt(self):
        before = reask.state_path(CALLER).read_bytes()
        with patch.object(ready, 'sync_directory', side_effect=OSError('sync failed')):
            with self.assertRaises(OSError):
                self.submit()
        self.assertEqual(reask.state_path(CALLER).read_bytes(), before)
        sha = hashlib.sha256(self.record_path.read_bytes()).hexdigest()
        with patch.object(ready, 'sync_directory', wraps=ready.sync_directory) as sync:
            result = work.checkpoint(CALLER, self.record_path, sha, now=1002.)
        sync.assert_called()
        self.assertEqual(result['outcome'], 'WORK_CHECKPOINT_RECORDED')
        self.assertEqual(reask.load(CALLER)['work_checkpoint_epoch'], 1001.)
        self.assertEqual(len(list((reask.root() / 'work-checkpoints').rglob('*.json'))), 1)

    def test_orphaned_archive_cannot_regress_a_newer_work_checkpoint_epoch(self):
        with patch.object(ready, 'sync_directory', side_effect=OSError('sync failed')):
            with self.assertRaises(OSError):
                self.submit(now=1001.)
        original_raw = self.record_path.read_bytes()
        original_sha = hashlib.sha256(original_raw).hexdigest()
        self.submit(self.record(work_id='another-work'), now=1005.)
        before = reask.state_path(CALLER).read_bytes()
        self.record_path.write_bytes(original_raw)
        with self.assertRaisesRegex(ValueError, 'EPOCH_REGRESSED'):
            work.checkpoint(CALLER, self.record_path, original_sha, now=1006.)
        self.assertEqual(reask.state_path(CALLER).read_bytes(), before)

    def test_source_change_during_publication_never_commits_unread_record(self):
        before = reask.state_path(CALLER).read_bytes()
        def change(_directory):
            self.record_path.write_bytes(self.record_path.read_bytes() + b' ')
        with patch.object(ready, 'sync_directory', side_effect=change):
            with self.assertRaisesRegex(ValueError, 'DURING_WRITE'):
                self.submit()
        self.assertEqual(reask.state_path(CALLER).read_bytes(), before)

    def test_cli_checkpoint_requires_explicit_pin_and_exposes_no_parent_acceptance(self):
        self.record_path.write_text(json.dumps(self.record()))
        sha = hashlib.sha256(self.record_path.read_bytes()).hexdigest()
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            result = reask.main(['work-checkpoint', '--caller-uuid', CALLER,
                '--record-file', str(self.record_path), '--record-sha256', sha])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())['progress']['parent_acceptance'], 'UNMEASURED')


if __name__ == '__main__':
    unittest.main()
