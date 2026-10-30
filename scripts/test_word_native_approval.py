"""Native-successor trust and queue controls; only isolated files/SQLite are used.

The earlier native-message recognizer controls are retained separately. Broker
tests stub that recognizer so each test exercises the new branch plus the real
parent ledger, original queue, locks and invocation gates. Real frozen native
records are additionally checked by the source-publication integration probe.
"""
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid

import word_native_approval as native
import word_restricted_broker as broker
from resource_broker import ResourceBusy, os_lock
import test_word_restricted_revision as old_tests


class ParentFixture:
    def __init__(self, root):
        self.root = root
        self.db = sqlite3.connect(root / 'queue.sqlite3', isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE word_restricted_revision_dispositions '
            '(id TEXT PRIMARY KEY, original_ticket TEXT, job_sha256 TEXT, contract TEXT, applied REAL)')
        self.db.execute('CREATE TABLE word_restricted_tickets '
            '(id TEXT PRIMARY KEY, disposition TEXT, plan TEXT, invocations INTEGER, status TEXT)')
        self.old_job = self.write('old-job.json', dict(task_id='old', input='unchanged'))
        self.new_job = self.write('new-job.json', dict(task_id='successor', input='unchanged'))
        self.old = dict(schema='word-restricted-disposition-v2', scope='OWNED_REVISION_ROUNDTRIP',
            original_ticket='ordinary-owned', initial_job=self.old_job,
            original_owner=dict(session_id=str(uuid.uuid4())),
            original_owner_session_id='original-owner',
            approval_copy=self.write('old-approval-copy.json', dict(old=True)),
            approved_plan_copy=self.write('old-plan-copy.json', dict(old=True)))
        self.old_ref = self.write('parent-contract.json', self.old)
        self.member = self.write('word_restricted_broker.py', dict(source='original'))
        self.executed = dict(operation='OWNED_REVISION_ROUNDTRIP', initial_job=self.old_job,
            controller_source=str(root), controller_pins={'word_restricted_broker.py': self.member},
            service_pins={'word_runtime.py': {'sha256': 'old-service'}},
            validator_pins={'pdfinfo': {'sha256': 'old-validator'}}, actor={'session_id': 'old'},
            locks={'lock': [1, 2]}, input={'sha256': 'same-input'})
        self.executed_ref = self.write('executed-plan.json', self.executed)
        self.db.execute('INSERT INTO word_restricted_revision_dispositions VALUES(?,?,?,?,?)',
            ('applied-parent', 'ordinary-owned', self.old_job['sha256'], json.dumps(self.old_ref), 1.0))
        self.db.execute('INSERT INTO word_restricted_tickets VALUES(?,?,?,?,?)',
            ('consumed-original', 'applied-parent', json.dumps(self.executed_ref), 1, 'PENDING'))
        self.candidate = copy.deepcopy(self.executed)
        self.candidate.update(initial_job=self.new_job, actor={'session_id': 'new'})
        self.candidate_ref = self.write('candidate.json', self.candidate)
        self.proposal = dict(original_applied_disposition=dict(self.db.execute(
            'SELECT * FROM word_restricted_revision_dispositions').fetchone()),
            original_contract=self.old_ref, current_plan=self.candidate_ref,
            current_job=self.new_job, controller_broker_source=self.member,
            actor={'session_id': 'current'},
            native_references=dict(current_approval={'record': 'current approval'},
                current_plan={'record': 'current plan'}),
            native_record_copies=dict(current_approval=self.write('current-approval.json', dict(current=True)),
                current_plan=self.write('current-plan.json', dict(current=True))))
        self.refresh()

    def write(self, name, value):
        p = self.root / name
        p.write_text(json.dumps(value) + '\n')
        return native.pin(p)

    def refresh(self):
        self.proposal_ref = self.write('proposal.json', self.proposal)
        self.contract = native.make_native_disposition(self.proposal_ref)

    def rows(self):
        return list(self.db.iterdump())


class NativeParentSourceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.f = ParentFixture(Path(tmp.name).resolve())
        self.addCleanup(self.f.db.close)

    def verify(self):
        before = self.f.rows()
        native.verify_applied_native_parent(self.f.db, self.f.contract)
        self.assertEqual(self.f.rows(), before)

    def refused(self, reason):
        before = self.f.rows()
        with self.assertRaisesRegex(native.AdmissionRefused, reason):
            native.verify_applied_native_parent(self.f.db, self.f.contract)
        self.assertEqual(self.f.rows(), before)

    def test_actual_consumed_parent_and_failure_are_preserved(self):
        self.verify()
        self.assertEqual(self.f.db.execute('SELECT invocations,status FROM '
            'word_restricted_tickets').fetchone()[:], (1, 'PENDING'))

    def test_absent_or_changed_applied_row_cannot_be_replaced_by_proposal(self):
        for sql in ('UPDATE word_restricted_revision_dispositions SET applied=2',
                    'DELETE FROM word_restricted_revision_dispositions'):
            with self.subTest(sql=sql):
                self.f.db.execute('SAVEPOINT negative')
                self.f.db.execute(sql)
                self.refused('APPLIED_ORIGINAL_DISPOSITION_CHANGED')
                self.f.db.execute('ROLLBACK TO negative'); self.f.db.execute('RELEASE negative')

    def test_original_contract_cannot_be_substituted_even_with_updated_proposal(self):
        wrong = self.f.write('foreign-contract.json', {**self.f.old, 'original_ticket': 'foreign'})
        self.f.proposal['original_contract'] = wrong; self.f.refresh()
        self.refused('APPLIED_ORIGINAL_CONTRACT_BINDING_CHANGED')

    def test_unconsumed_or_refunded_or_duplicate_original_tickets_are_refused(self):
        for count in (0, 2):
            with self.subTest(invocations=count):
                self.f.db.execute('UPDATE word_restricted_tickets SET invocations=?', (count,))
                self.refused('EXACT_CONSUMED_ORIGINAL_CONTROLLER_REQUIRED')
        self.f.db.execute('UPDATE word_restricted_tickets SET invocations=1')
        self.f.db.execute('INSERT INTO word_restricted_tickets SELECT '
            "'duplicate',disposition,plan,invocations,status FROM word_restricted_tickets")
        self.refused('EXACT_CONSUMED_ORIGINAL_CONTROLLER_REQUIRED')

    def test_executed_operation_and_input_must_bind_the_actual_parent(self):
        for key, value in (('operation', 'OWNED_PDF_ONLY'), ('initial_job', self.f.new_job)):
            with self.subTest(key=key):
                ref = self.f.write('wrong-executed.json', {**self.f.executed, key: value})
                self.f.db.execute('UPDATE word_restricted_tickets SET plan=?', (json.dumps(ref),))
                self.refused('ORIGINAL_EXECUTED_PLAN_BINDING_CHANGED')

    def test_candidate_cannot_choose_another_original_controller_or_service(self):
        for key in ('controller_source', 'controller_pins', 'service_pins', 'validator_pins'):
            with self.subTest(key=key):
                candidate = {**self.f.candidate, key: 'foreign' if key == 'controller_source' else {}}
                self.f.proposal['current_plan'] = self.f.write('wrong-candidate.json', candidate)
                self.f.refresh(); self.refused('APPLIED_ORIGINAL_CONTROLLER_CHANGED:' + key)

    def test_broker_pin_must_be_from_the_original_executed_closure(self):
        self.f.proposal['controller_broker_source'] = self.f.write('foreign-broker.json', {})
        self.f.refresh(); self.refused('APPLIED_ORIGINAL_BROKER_CHANGED')

    def test_successor_keeps_owner_and_uses_current_native_copies(self):
        c = self.f.contract
        self.assertEqual(c['original_owner'], self.f.old['original_owner'])
        self.assertEqual(c['original_owner_session_id'], self.f.old['original_owner_session_id'])
        self.assertEqual(c['approval_copy'], self.f.proposal['native_record_copies']['current_approval'])
        self.assertEqual(c['approved_plan_copy'], self.f.proposal['native_record_copies']['current_plan'])
        self.assertEqual(json.loads(native.read_pin(self.f.old_ref)), self.f.old)

    def test_execution_can_upgrade_only_the_complete_controller(self):
        plan = copy.deepcopy(self.f.candidate)
        plan.update(controller_source='/new/complete/source',
            controller_pins={'word_native_approval.py': self.f.contract['native_approval_source']})
        native.verify_native_execution_plan(self.f.contract, plan)
        for key, value in (('actor', {'session_id': 'foreign'}), ('locks', {}),
                           ('service_pins', {}), ('validator_pins', {}),
                           ('operation', 'REFRESH'), ('input', {'sha256': 'other'})):
            with self.subTest(key=key):
                with self.assertRaisesRegex(native.AdmissionRefused, 'SCOPE_OR_INPUT_CHANGED'):
                    native.verify_native_execution_plan(self.f.contract, {**plan, key: value})

    def test_extra_execution_fields_or_missing_native_source_are_refused(self):
        with self.assertRaisesRegex(native.AdmissionRefused, 'FIELDS_CHANGED'):
            native.verify_native_execution_plan(self.f.contract, {**self.f.candidate, 'argv': ['unsafe']})
        with self.assertRaisesRegex(native.AdmissionRefused, 'COMPLETE_NATIVE_EXECUTION_SOURCE_REQUIRED'):
            native.verify_native_execution_plan(self.f.contract, self.f.candidate)

    def test_borrowed_source_is_refused_before_child_launch(self):
        ref = self.f.write('borrowed-source.json', {})
        with patch.object(subprocess, 'run') as child:
            with self.assertRaisesRegex(native.AdmissionRefused, 'EXACT_NATIVE_APPROVAL_SOURCE_REQUIRED'):
                native.verify_native_disposition({**self.f.contract, 'native_approval_source': ref}, root=self.f.root)
            child.assert_not_called()

    def test_verification_timeout_is_explicit_and_does_not_grant_execution(self):
        before = self.f.rows()
        with patch.object(subprocess, 'run', side_effect=subprocess.TimeoutExpired('verifier', 30)) as child:
            with self.assertRaisesRegex(native.AdmissionRefused, 'NATIVE_VERIFICATION_TIMEOUT_NO_EXECUTION'):
                native.verify_native_disposition(self.f.contract, root=self.f.root)
            self.assertEqual(child.call_count, 1)
        self.assertEqual(self.f.rows(), before)

    def test_edited_contract_refused_before_original_controller_import(self):
        with patch.object(native, 'original_controller') as controller:
            with self.assertRaisesRegex(native.AdmissionRefused, 'NATIVE_DISPOSITION_SCOPE_OR_SOURCE_CHANGED'):
                native._verify_native_disposition_child({**self.f.contract, 'successor': {'session_id': 'foreign'}}, root=self.f.root)
            controller.assert_not_called()


class NativeBrokerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.r = old_tests.OwnedRevisionTests(); self.r.setUp()
        self.addCleanup(self.r.doCleanups)
        self.f = self.r.f
        parent, _plan, _plan_ref, _contract_ref = self.r.request('parent')
        self.parent_id = parent['disposition']; self.parent_ticket = parent['id']
        self.f.broker.db.execute("UPDATE word_restricted_tickets SET invocations=1,status='COMPLETE_WITH_FOREIGN_UNKNOWN' WHERE id=?", (self.parent_ticket,))
        self.parent_row = dict(self.f.broker.db.execute('SELECT * FROM '
            'word_restricted_revision_dispositions WHERE id=?', (self.parent_id,)).fetchone())
        self.current = {**self.f.successor, 'session_id': str(uuid.uuid4())}
        self.f.identity.return_value = self.current
        recognizer = patch.object(native, 'verify_native_disposition', return_value={
            'outcome': 'NATIVE_SUCCESSOR_DISPOSITION_VERIFIED', 'application_calls': 0, 'queue_writes': 0})
        recognizer.start(); self.addCleanup(recognizer.stop)
        self.make_successor('current')

    def make_successor(self, name):
        job, _ref = self.r.job(name); job['actor'] = self.current
        ref = self.f.write(name + '-job.json', job)
        plan, _plan_ref = self.r.plan(job, ref, name)
        plan['actor'] = self.current
        plan_ref = self.f.write(name + '-native-plan.json', plan)
        original_ref = json.loads(self.parent_row['contract'])
        old = json.loads(native.read_pin(original_ref))
        proposal = dict(original_applied_disposition=self.parent_row,
            original_contract=original_ref, current_plan=plan_ref, current_job=ref,
            controller_broker_source=plan['controller_pins']['word_restricted_broker.py'],
            actor=self.current, native_references=dict(current_approval=old['approval'], current_plan=old['approved_plan']),
            native_record_copies=dict(current_approval=old['terminal_receipts'][0], current_plan=old['terminal_receipts'][1]))
        proposal_ref = self.f.write(name + '-chain.json', proposal)
        contract = native.make_native_disposition(proposal_ref)
        contract_ref = self.f.write(name + '-native-disposition.json', contract)
        self.contract, self.contract_ref, self.plan, self.plan_ref = contract, contract_ref, plan, plan_ref
        return contract_ref, plan_ref

    def apply(self):
        preview = self.f.broker.preview(self.contract_ref)
        return self.f.broker.apply(self.contract_ref, preview['plan_sha256'])['disposition_id']

    def request(self):
        return self.f.broker.request(self.apply(), self.plan_ref)

    def test_native_preview_changes_neither_schema_nor_parent_or_ordinary_owner(self):
        before = list(self.f.broker.db.iterdump())
        result = self.f.broker.preview(self.contract_ref)
        self.assertEqual((result['input_operations'], result['queue_changes']), (0, 0))
        self.assertEqual(list(self.f.broker.db.iterdump()), before)
        self.assertEqual(self.f.legacy.get(self.f.original['id']), self.f.original)

    def test_native_contract_is_source_bound_when_loaded_from_its_applied_row(self):
        disposition = self.apply()
        loaded = self.f.broker.contract(disposition)
        self.assertEqual(loaded, self.contract)
        self.f.broker.db.execute('UPDATE word_restricted_revision_dispositions SET applied=0 WHERE id=?', (self.parent_id,))
        with self.assertRaisesRegex(native.AdmissionRefused, 'APPLIED_ORIGINAL_DISPOSITION_CHANGED'):
            self.f.broker.contract(disposition)

    def test_apply_rechecks_actual_parent_inside_the_write_transaction(self):
        transaction = self.f.broker.transaction
        @contextmanager
        def changed():
            with transaction():
                self.f.broker.db.execute('UPDATE word_restricted_revision_dispositions SET applied=2 WHERE id=?', (self.parent_id,))
                yield
        before = list(self.f.broker.db.iterdump())
        with patch.object(self.f.broker, 'transaction', changed):
            with self.assertRaisesRegex(native.AdmissionRefused, 'APPLIED_ORIGINAL_DISPOSITION_CHANGED'):
                self.apply()
        self.assertEqual(list(self.f.broker.db.iterdump()), before)

    def test_request_rechecks_parent_under_its_original_transaction(self):
        disposition = self.apply(); transaction = self.f.broker.transaction
        @contextmanager
        def changed():
            with transaction():
                self.f.broker.db.execute('UPDATE word_restricted_tickets SET invocations=0 WHERE id=?', (self.parent_ticket,))
                yield
        before = list(self.f.broker.db.iterdump())
        with patch.object(self.f.broker, 'transaction', changed):
            with self.assertRaisesRegex(native.AdmissionRefused, 'EXACT_CONSUMED_ORIGINAL_CONTROLLER_REQUIRED'):
                self.f.broker.request(disposition, self.plan_ref)
        self.assertEqual(list(self.f.broker.db.iterdump()), before)

    def test_native_pending_file_is_preserved_and_still_blocks_admission(self):
        pending = self.f.user / '.cache/wordrev/word-automation.pending.json'
        pending.write_bytes(b'{"original":"retain all bytes"}\n')
        before = list(self.f.broker.db.iterdump())
        with self.assertRaisesRegex(ResourceBusy, 'WORD_PENDING_PRESERVED'):
            self.f.broker.preview(self.contract_ref)
        self.assertEqual(pending.read_bytes(), b'{"original":"retain all bytes"}\n')
        self.assertEqual(list(self.f.broker.db.iterdump()), before)

    def test_native_disposition_is_create_only_and_original_owner_never_released(self):
        self.apply()
        with self.assertRaisesRegex(ResourceBusy, 'INITIAL_JOB_AUTHORIZATION_EXISTS_NO_REPLAY'):
            self.apply()
        self.assertEqual(self.f.legacy.get(self.f.original['id']), self.f.original)
        self.assertEqual(dict(self.f.broker.db.execute('SELECT * FROM word_restricted_revision_dispositions WHERE id=?', (self.parent_id,)).fetchone()), self.parent_row)

    def test_wrong_current_actor_cannot_apply_request_or_grant(self):
        self.f.identity.return_value = self.f.actor()
        with self.assertRaisesRegex(ResourceBusy, 'ORIGINAL_ADMINISTRATOR_REQUIRED'):
            self.apply()
        self.f.identity.return_value = self.current; disposition = self.apply()
        self.f.identity.return_value = self.f.actor()
        with self.assertRaisesRegex(ResourceBusy, 'ORIGINAL_AUTHORIZED_SUCCESSOR_REQUIRED'):
            self.f.broker.request(disposition, self.plan_ref)
        self.f.identity.return_value = self.current
        ticket = self.f.broker.request(disposition, self.plan_ref)
        self.f.identity.return_value = self.f.actor()
        with self.assertRaisesRegex(ResourceBusy, 'ORIGINAL_SUCCESSOR_REQUIRED'):
            self.f.broker.grant(ticket['id'])
        self.assertEqual(self.f.broker.get(ticket['id'])['status'], 'WAITING')

    def test_original_pending_restricted_ticket_still_blocks_native_grant(self):
        self.f.broker.db.execute("UPDATE word_restricted_tickets SET status='PENDING' WHERE id=?", (self.parent_ticket,))
        ticket = self.request()
        with self.assertRaisesRegex(ResourceBusy, 'RESTRICTED_WORD_WRITER_BUSY'):
            self.f.broker.grant(ticket['id'])
        self.assertEqual(self.f.broker.get(self.parent_ticket)['invocations'], 1)
        self.assertEqual(self.f.broker.get(ticket['id'])['status'], 'WAITING')

    def test_original_word_and_focus_locks_protect_the_new_native_branch(self):
        ticket = self.request()
        for path in self.f.lock_paths:
            with self.subTest(path=path):
                with os_lock(path):
                    with self.assertRaisesRegex(ResourceBusy, 'OS_LOCK_BUSY'):
                        self.f.broker.grant(ticket['id'])
        self.assertEqual(self.f.broker.get(ticket['id'])['status'], 'WAITING')

    def test_new_native_jobs_keep_fifo_and_do_not_unblock_the_ordinary_queue(self):
        first = self.request()
        self.make_successor('later'); later = self.request()
        with self.assertRaisesRegex(ResourceBusy, 'RESTRICTED_WORD_FIFO_REQUIRED'):
            self.f.broker.grant(later['id'])
        self.assertEqual(self.f.broker.grant(first['id'])['status'], 'ACTIVE')
        self.assertEqual(self.f.legacy.get(self.f.original['id']), self.f.original)
        with self.assertRaisesRegex(ResourceBusy, 'EXPIRED_LEASE'):
            self.f.legacy.grant(self.f.original['id'])

    def test_consumed_native_invocation_is_refused_before_any_child_launch(self):
        ticket = self.request(); granted = self.f.broker.grant(ticket['id'])
        self.f.broker.db.execute('UPDATE word_restricted_tickets SET invocations=1,nonce=? WHERE id=?', (str(uuid.uuid4()), ticket['id']))
        with patch.object(subprocess, 'Popen') as child:
            with self.assertRaisesRegex(ResourceBusy, 'ORIGINAL_RUN_EXISTS_NO_REPLAY'):
                self.f.broker.run(ticket['id'], granted['token'])
            child.assert_not_called()

    def test_foreign_child_pid_and_parent_are_refused_before_native_action(self):
        ticket = self.request(); granted = self.f.broker.grant(ticket['id'])
        self.f.broker.db.execute('UPDATE word_restricted_tickets SET pgid=999999,parent_pid=999998 WHERE id=?', (ticket['id'],))
        with self.assertRaisesRegex(ResourceBusy, 'ORIGINAL_BROKER_CHILD_REQUIRED'):
            self.f.broker.require(ticket['id'], granted['token'], child=True)


if __name__ == '__main__':
    unittest.main()
