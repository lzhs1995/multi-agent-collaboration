"""Temporary DBs and offline native actors; no live applications or queues."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

import resource_broker as rb
import resource_execution as ex


def ref(path):
    path = Path(path).resolve()
    raw = path.read_bytes()
    return {'path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


class ExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(ex.__file__).resolve().parent.parent
        cls.controller = {'source': str(source), 'files': [
            dict(relative=str(p.relative_to(source)), bytes=p.stat().st_size,
                 sha256=hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted((source/'scripts').glob('*.py'))]}

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.b = rb.Broker(self.root/'resources')
        self.addCleanup(self.b.db.close)
        self.external = self.root/'external.lock'
        self.b.configure('test-resource', [self.external])
        self.input = self.root/'input.txt'
        self.input.write_text('original input')
        self.script = self.root/'transport.py'
        self.script.write_text('print("fixture complete")\n')
        self.actor = dict(zip(('workspace_uuid', 'surface_uuid', 'pane_uuid', 'session_id'),
                              (str(uuid.uuid4()) for _ in range(4))))
        self.contract = {
            'schema': ex.SCHEMA, 'task_id': 'test-task', 'resource': 'test-resource',
            'actor': self.actor, 'controller': self.controller, 'operation': 'fixture_write',
            'request_revision': 'a'*64, 'attempt_id': str(uuid.uuid4()),
            'argv': [str(Path(sys.executable).resolve()), '-B', str(self.script)],
            'cwd': str(self.root), 'environment': {},
            'transport_artifacts': [ref(sys.executable), ref(self.script)], 'inputs': [ref(self.input)]}
        self.native = patch.object(ex, 'native_identity', side_effect=lambda _: dict(self.actor))
        self.native.start()
        self.addCleanup(self.native.stop)

    def lease(self, contract=None):
        ticket = self.b.request_enforced(contract or self.contract)
        return self.b.grant_enforced(ticket['id'])

    def run_lease(self, lease, **changes):
        args = {k: lease['execution'][k] for k in (
            'generation', 'operation', 'request_revision', 'attempt_id')}
        args.update(changes)
        return self.b.run_enforced(lease['id'], lease['token'], **args,
                                   text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def proof(self, lease, result=None):
        return {'pending': False, 'own_processes_empty': True,
                'execution': (result or lease)['execution'],
                **({'no_request_submitted': True} if result is None else {})}

    def release(self, lease, result=None, **extra):
        proof = dict(self.proof(lease, result), **extra)
        return self.b.release_enforced(lease['id'], lease['token'], proof,
                                       generation=lease['execution']['generation'])

    def test_public_preflight_opens_no_database_and_requires_native_actor(self):
        with patch('sqlite3.connect', side_effect=AssertionError('no DB allowed')):
            self.assertEqual(rb.Broker.validate_execution_contract(self.contract), self.contract)
            with patch.object(ex, 'native_identity', return_value={}):
                with self.assertRaisesRegex(ex.ExecutionError, 'NATIVE_EXECUTION_ACTOR'):
                    rb.Broker.validate_execution_contract(self.contract)

    def test_legacy_row_hash_and_fifo_remain_unchanged(self):
        ticket = self.b.request('test-resource', 'old', self.actor['workspace_uuid'], self.actor['surface_uuid'])
        old = self.b.get(ticket['id'])
        resource = dict(self.b.db.execute('SELECT * FROM resources').fetchone())
        new = self.b.request_enforced(self.contract)
        self.assertEqual(self.b.get(ticket['id']), old)
        self.assertEqual(dict(self.b.db.execute('SELECT * FROM resources').fetchone()), resource)
        with self.assertRaisesRegex(rb.ResourceBusy, 'WAIT_YOUR_TURN'):
            self.b.grant_enforced(new['id'])
        self.assertEqual(self.b.db.execute('SELECT count(*) FROM resource_execution_generations').fetchone()[0], 0)
        old = self.b.grant(ticket['id'])
        self.b.release(old['id'], old['token'], {'pending': False, 'own_processes_empty': True})
        self.assertEqual(self.b.grant_enforced(new['id'])['execution']['generation'], 1)

    def test_generic_api_cannot_downgrade_bound_ticket_or_task(self):
        ticket = self.b.request_enforced(self.contract)
        with self.assertRaisesRegex(rb.ResourceBusy, 'ENFORCED_TASK_CANNOT_DOWNGRADE'):
            self.b.request('test-resource', 'test-task', str(uuid.uuid4()), str(uuid.uuid4()))
        with self.assertRaisesRegex(rb.ResourceBusy, 'ENFORCED_API_REQUIRED'):
            self.b.grant(ticket['id'])
        lease = self.b.grant_enforced(ticket['id'])
        for call in (lambda: self.b.require(lease['id'], lease['token']),
                     lambda: self.b.run(lease['id'], lease['token'], self.contract['argv']),
                     lambda: self.b.release(lease['id'], lease['token'], self.proof(lease))):
            with self.assertRaisesRegex(rb.ResourceBusy, 'ENFORCED_API_REQUIRED'):
                call()

    def test_existing_ordinary_ticket_cannot_upgrade(self):
        self.b.request('test-resource', 'test-task', self.actor['workspace_uuid'], self.actor['surface_uuid'])
        with self.assertRaisesRegex(ex.ExecutionError, 'LEGACY_TICKET_CANNOT_BE_UPGRADED'):
            self.b.request_enforced(self.contract)

    def test_same_attempt_exact_only_and_terminal_operation_cannot_replay(self):
        ticket = self.b.request_enforced(self.contract)
        self.assertEqual(ticket, self.b.request_enforced(self.contract))
        changed = dict(self.contract, operation='different')
        with self.assertRaisesRegex(ex.ExecutionError, 'ORIGINAL_ATTEMPT_BINDING_CHANGED'):
            self.b.request_enforced(changed)
        lease = self.b.grant_enforced(ticket['id'])
        result = self.run_lease(lease)
        self.release(lease, result)
        with self.assertRaisesRegex(ex.ExecutionError, 'ORIGINAL_OPERATION_EXISTS_NO_REPLAY'):
            self.b.request_enforced(dict(self.contract, attempt_id=str(uuid.uuid4())))

    def test_generations_increase_and_old_generation_refused(self):
        first = self.lease()
        self.release(first)
        second = self.lease(dict(self.contract, attempt_id=str(uuid.uuid4()), request_revision='b'*64))
        self.assertEqual(second['execution']['generation'], 2)
        with patch.object(ex.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ex.ExecutionError, 'GENERATION_MISMATCH'):
                self.run_lease(second, generation=1)
            spawn.assert_not_called()

    def test_actor_or_operation_or_revision_change_never_spawns(self):
        lease = self.lease()
        with patch.object(ex.subprocess, 'Popen') as spawn:
            for args in ({'operation': 'wrong'}, {'request_revision': 'b'*64}, {'attempt_id': 'wrong'}):
                with self.assertRaisesRegex(ex.ExecutionError, 'OPERATION_BINDING_CHANGED'):
                    self.run_lease(lease, **args)
            with patch.object(ex, 'native_identity', return_value=dict(self.actor, session_id=str(uuid.uuid4()))):
                with self.assertRaisesRegex(ex.ExecutionError, 'NATIVE_EXECUTION_ACTOR'):
                    self.run_lease(lease)
            with patch.object(ex, 'native_identity', side_effect=RuntimeError('multiple rollout refusal')):
                with self.assertRaisesRegex(RuntimeError, 'multiple rollout'):
                    self.run_lease(lease)
            spawn.assert_not_called()

    def test_input_transport_and_closure_changes_refuse(self):
        lease = self.lease()
        self.input.write_text('changed')
        with patch.object(ex.subprocess, 'Popen') as spawn:
            with self.assertRaises(ex.ExecutionError):
                self.run_lease(lease)
            spawn.assert_not_called()
        incomplete = copy.deepcopy(self.contract)
        incomplete['controller']['files'].pop()
        with self.assertRaisesRegex(ex.ExecutionError, 'COMPLETE_SCRIPTS_CLOSURE'):
            rb.Broker.validate_execution_contract(incomplete)

    def test_unbound_popen_options_refused(self):
        lease = self.lease()
        for key in ('env', 'cwd', 'shell', 'executable', 'pass_fds', 'preexec_fn'):
            with self.subTest(key=key), self.assertRaisesRegex(ex.ExecutionError, 'UNBOUND_POPEN_OPTIONS'):
                self.run_lease(lease, **{key: None})

    def test_intent_is_committed_before_popen_and_all_locks_held_in_child(self):
        locks = [str(self.b.lock_path('test-resource')), str(self.external)]
        self.script.write_text('import fcntl,json\nblocked=[]\n'
            'for path in '+repr(locks)+':\n'
            ' with open(path,"a+b") as f:\n'
            '  try: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\n'
            '  except BlockingIOError: blocked.append(path)\n'
            'print(json.dumps(blocked))\n')
        self.contract['transport_artifacts'][1] = ref(self.script)
        lease = self.lease()
        popen = subprocess.Popen
        def spawn(*args, **kwargs):
            db = sqlite3.connect(self.b.root/'queue.sqlite3')
            try:
                self.assertEqual(db.execute('SELECT state FROM resource_execution_attempts').fetchone()[0], 'UNKNOWN')
                self.assertEqual(db.execute('SELECT invocations FROM tickets WHERE id=?', (lease['id'],)).fetchone()[0], 1)
            finally:
                db.close()
            self.assertEqual(kwargs['env'], {})
            self.assertEqual(kwargs['cwd'], str(self.root))
            return popen(*args, **kwargs)
        with patch.object(ex.subprocess, 'Popen', side_effect=spawn):
            result = self.run_lease(lease)
        self.assertEqual(json.loads(result['stdout']), locks)
        self.assertEqual(result['execution']['state'], 'TERMINAL')
        self.assertEqual(result['enforcement_scope'], 'PRELAUNCH_ONLY')
        self.release(lease, result)

    def test_spawn_failure_retains_unknown_and_refuses_replay_or_release(self):
        lease = self.lease()
        with patch.object(ex.subprocess, 'Popen', side_effect=OSError('spawn failed')) as spawn:
            with self.assertRaises(OSError):
                self.run_lease(lease)
            with self.assertRaisesRegex(ex.ExecutionError, 'ORIGINAL_ATTEMPT_EXISTS_NO_REPLAY'):
                self.run_lease(lease)
            self.assertEqual(spawn.call_count, 1)
        self.assertEqual(self.b.get(lease['id'])['status'], 'ACTIVE')
        with self.assertRaisesRegex(ex.ExecutionError, 'UNKNOWN_ORIGINAL_ATTEMPT_READBACK_REQUIRED'):
            self.release(lease)

    def test_change_after_intent_stays_unknown_without_spawn(self):
        lease = self.lease()
        calls = []
        def native(_):
            calls.append(1)
            if len(calls) == 3:
                self.input.write_text('changed after initial validation')
            return dict(self.actor)
        with patch.object(ex, 'native_identity', side_effect=native), patch.object(ex.subprocess, 'Popen') as spawn:
            with self.assertRaises(ex.ExecutionError):
                self.run_lease(lease)
            spawn.assert_not_called()
        self.assertEqual(self.b.db.execute('SELECT state FROM resource_execution_attempts').fetchone()[0], 'UNKNOWN')

    def test_changes_during_native_preflight_and_grant_refuse_before_mutation(self):
        def changed(_):
            self.input.write_text('changed during native lookup')
            return self.actor
        with patch.object(ex, 'native_identity', side_effect=changed):
            with self.assertRaises(ex.ExecutionError):
                self.b.request_enforced(self.contract)
        self.assertEqual(self.b.status(), [])
        self.input.write_text('original input')
        ticket = self.b.request_enforced(self.contract)
        calls = []
        def changed_at_grant(_):
            calls.append(1)
            if len(calls) == 1:
                self.input.write_text('changed just before grant commit')
            return self.actor
        with patch.object(ex, 'native_identity', side_effect=changed_at_grant):
            with self.assertRaises(ex.ExecutionError):
                self.b.grant_enforced(ticket['id'])
        self.assertEqual(self.b.get(ticket['id'])['status'], 'WAITING')
        self.assertEqual(self.b.db.execute('SELECT count(*) FROM resource_execution_generations').fetchone()[0], 0)

    def test_native_reads_outside_sqlite_write_transaction_under_original_locks(self):
        ticket = self.b.request_enforced(self.contract)
        observations = []
        def native(_):
            observations.append(self.b.db.in_transaction)
            self.assertFalse(self.b.db.in_transaction)
            other = sqlite3.connect(self.b.root/'queue.sqlite3', timeout=0, isolation_level=None)
            try:
                other.execute('BEGIN IMMEDIATE')
                other.execute('ROLLBACK')
            finally:
                other.close()
            for path in (self.b.lock_path('test-resource'), self.external):
                with self.assertRaisesRegex(rb.ResourceBusy, 'OS_LOCK_BUSY'):
                    with rb.os_lock(path):
                        pass
            return dict(self.actor)
        with patch.object(ex, 'native_identity', side_effect=native):
            lease = self.b.grant_enforced(ticket['id'])
            self.release(lease)
            self.b.requeue_unsubmitted_enforced(lease['id'], lease['token'], generation=1)
        self.assertEqual(observations, [False, False, False])

    def test_native_check_use_change_to_row_refuses_grant(self):
        ticket = self.b.request_enforced(self.contract)
        def native(_):
            self.b.db.execute('UPDATE tickets SET requested=requested+1 WHERE id=?', (ticket['id'],))
            return self.actor
        with patch.object(ex, 'native_identity', side_effect=native):
            with self.assertRaisesRegex(ex.ExecutionError, 'PREFLIGHT_TICKET_CHANGED'):
                self.b.grant_enforced(ticket['id'])
        self.assertEqual(self.b.get(ticket['id'])['status'], 'WAITING')

    def withdraw(self, ticket):
        return self.b.withdraw_enforced(ticket['id'], attempt_id=ticket['execution']['attempt_id'],
                                        contract_sha256=ticket['execution']['contract_sha256'])

    def test_waiting_withdraw_ignores_invalidated_inputs_and_preserves_original_evidence(self):
        first = self.b.request_enforced(self.contract)
        original = self.b.get(first['id'])
        binding = dict(self.b.db.execute('SELECT * FROM resource_execution_bindings').fetchone())
        next_input = self.root/'next-input.txt'
        next_input.write_text('independent successor')
        successor_contract = dict(self.contract, task_id='next-task',attempt_id=str(uuid.uuid4()),inputs=[ref(next_input)])
        second = self.b.request_enforced(successor_contract)
        self.input.unlink()
        with patch.object(ex.subprocess,'Popen') as spawn:
            with self.assertRaises(OSError):
                self.b.grant_enforced(first['id'])
            with self.assertRaisesRegex(rb.ResourceBusy,'WAIT_YOUR_TURN'):
                self.b.grant_enforced(second['id'])
            with rb.os_lock(self.external):
                cancelled = self.withdraw(first)
            self.assertEqual(cancelled['status'],'WITHDRAWN')
            self.assertEqual(cancelled['invocations'],0)
            self.assertIsNone(cancelled['execution']['generation'])
            self.assertEqual(dict(self.b.db.execute('SELECT * FROM resource_execution_bindings WHERE ticket_id=?',(first['id'],)).fetchone()),binding)
            saved = dict(self.b.db.execute('SELECT * FROM resource_execution_cancellations').fetchone())
            body=json.loads(saved['cancellation'])
            self.assertEqual(body['ticket'],original)
            self.assertEqual(body['binding'],binding)
            self.assertEqual(saved['cancellation_sha256'],ex.sha(body))
            with self.assertRaisesRegex(ex.ExecutionError,'WAITING_UNSTARTED_WITHDRAWAL_REQUIRED'):
                self.withdraw(first)
            lease=self.b.grant_enforced(second['id'])
            self.assertEqual(lease['execution']['generation'],1)
            self.release(lease)
            spawn.assert_not_called()

    def test_waiting_withdraw_does_not_need_transport_or_cwd_but_requires_original_actor_controller(self):
        ticket=self.b.request_enforced(self.contract)
        self.script.unlink()
        with patch.object(ex,'native_identity',return_value={}):
            with self.assertRaisesRegex(ex.ExecutionError,'NATIVE_EXECUTION_ACTOR_MISMATCH'):
                self.withdraw(ticket)
        _,original=self.b._execution().binding(ticket['id'])
        changed=copy.deepcopy(original)
        changed['controller']['source']='/tmp'
        self.b.db.execute('UPDATE resource_execution_bindings SET contract=?,contract_sha256=? WHERE ticket_id=?',
                          (ex.canonical(changed),ex.sha(changed),ticket['id']))
        with self.assertRaises((ex.ExecutionError,OSError)):
            self.withdraw(ticket)
        self.b.db.execute('UPDATE resource_execution_bindings SET contract=?,contract_sha256=? WHERE ticket_id=?',
                          (ex.canonical(original),ex.sha(original),ticket['id']))
        with self.assertRaisesRegex(ex.ExecutionError,'ORIGINAL_WITHDRAWAL_BINDING_REQUIRED'):
            self.b.withdraw_enforced(ticket['id'],attempt_id='wrong',contract_sha256=ticket['execution']['contract_sha256'])
        self.assertEqual(self.withdraw(ticket)['status'],'WITHDRAWN')

    def test_waiting_withdraw_refuses_active_unknown_and_row_binding_corruption(self):
        ticket=self.b.request_enforced(self.contract)
        self.b.db.execute('UPDATE tickets SET task_id=? WHERE id=?',('other-task',ticket['id']))
        with self.assertRaisesRegex(ex.ExecutionError,'EXECUTION_TICKET_BINDING_CHANGED'):
            self.withdraw(ticket)
        self.b.db.execute('UPDATE tickets SET task_id=? WHERE id=?',('test-task',ticket['id']))
        self.b.db.execute('INSERT INTO resource_execution_attempts(ticket_id,attempt_id,generation,state,intent,intent_sha256) VALUES(?,?,?,?,?,?)',
                          (ticket['id'],ticket['execution']['attempt_id'],1,'UNKNOWN','{}',ex.sha({})))
        with self.assertRaisesRegex(ex.ExecutionError,'WAITING_UNSTARTED_WITHDRAWAL_REQUIRED'):
            self.withdraw(ticket)
        # Independent active original operation uses a separate resource.
        self.b.configure('active-test')
        active=self.lease(dict(self.contract,resource='active-test',task_id='active-task',attempt_id=str(uuid.uuid4())))
        with self.assertRaisesRegex(ex.ExecutionError,'WAITING_UNSTARTED_WITHDRAWAL_REQUIRED'):
            self.withdraw(active)
        self.assertEqual(self.b.db.execute('SELECT count(*) FROM resource_execution_cancellations').fetchone()[0],0)

    def test_explicit_zero_submission_requeue_preserves_history_and_fifo(self):
        lease = self.lease()
        original_binding = dict(self.b.db.execute('SELECT * FROM resource_execution_bindings').fetchone())
        self.release(lease)
        original_row = self.b.get(lease['id'])
        self.assertEqual(self.b.request_enforced(self.contract)['status'], 'RELEASED')
        waiting = self.b.request('test-resource', 'ordinary-next', str(uuid.uuid4()), str(uuid.uuid4()))
        queued = self.b.requeue_unsubmitted_enforced(lease['id'], lease['token'], generation=1)
        self.assertEqual(queued['id'], lease['id'])
        self.assertEqual(queued['status'], 'WAITING')
        self.assertIsNone(queued['token'])
        self.assertIsNone(queued['expires'])
        self.assertIsNone(queued['release_proof'])
        self.assertIsNone(queued['execution']['generation'])
        self.assertEqual(queued['execution']['attempt_id'], lease['execution']['attempt_id'])
        history = dict(self.b.db.execute('SELECT * FROM resource_execution_unsubmitted_history').fetchone())
        self.assertEqual(json.loads(history['lease']), original_row)
        self.assertEqual(history['lease_sha256'], ex.sha(original_row))
        self.assertEqual(json.loads(history['binding']), original_binding)
        self.assertEqual(history['binding_sha256'], ex.sha(original_binding))
        self.assertEqual(history['release_proof_sha256'], ex.sha(json.loads(original_row['release_proof'])))
        with self.assertRaisesRegex(rb.ResourceBusy, 'WAIT_YOUR_TURN'):
            self.b.grant_enforced(queued['id'])
        other = self.b.grant(waiting['id'])
        self.b.release(other['id'], other['token'], {'pending':False,'own_processes_empty':True})
        next_lease = self.b.grant_enforced(queued['id'])
        self.assertEqual(next_lease['execution']['generation'], 2)
        with self.assertRaisesRegex(rb.ResourceBusy, 'LEASE_OWNER_MISMATCH'):
            self.run_lease(lease)
        result = self.run_lease(next_lease)
        self.release(next_lease, result)
        with self.assertRaisesRegex(ex.ExecutionError, 'UNSUBMITTED_REQUEUE_REQUIRED'):
            self.b.requeue_unsubmitted_enforced(next_lease['id'], next_lease['token'], generation=2)

    def test_unsubmitted_requeue_refuses_stale_lease_invalid_proof_and_existing_intent(self):
        lease = self.lease()
        with self.assertRaisesRegex(ex.ExecutionError, 'UNSUBMITTED_REQUEUE_REQUIRED'):
            self.b.requeue_unsubmitted_enforced(lease['id'], lease['token'], generation=1)
        self.release(lease)
        proof = self.b.get(lease['id'])['release_proof']
        for token,generation in [('wrong',1),(lease['token'],2),(lease['token'],True)]:
            with self.assertRaisesRegex(rb.ResourceBusy, 'ORIGINAL_UNSUBMITTED_LEASE_REQUIRED'):
                self.b.requeue_unsubmitted_enforced(lease['id'],token,generation=generation)
        changed = json.loads(proof)
        changed['no_request_submitted'] = False
        self.b.db.execute('UPDATE tickets SET release_proof=? WHERE id=?',(json.dumps(changed),lease['id']))
        with self.assertRaisesRegex(ex.ExecutionError, 'ORIGINAL_NO_SUBMISSION_RELEASE_REQUIRED'):
            self.b.requeue_unsubmitted_enforced(lease['id'],lease['token'],generation=1)
        self.b.db.execute('UPDATE tickets SET release_proof=? WHERE id=?',(proof,lease['id']))
        self.b.db.execute('INSERT INTO resource_execution_attempts(ticket_id,attempt_id,generation,state,intent,intent_sha256) VALUES(?,?,?,?,?,?)',
                          (lease['id'],lease['execution']['attempt_id'],1,'UNKNOWN','{}',ex.sha({})))
        with self.assertRaisesRegex(ex.ExecutionError, 'UNSUBMITTED_REQUEUE_REQUIRED'):
            self.b.requeue_unsubmitted_enforced(lease['id'],lease['token'],generation=1)
        self.assertEqual(self.b.db.execute('SELECT count(*) FROM resource_execution_unsubmitted_history').fetchone()[0],0)

    def test_external_lock_busy_leaves_no_attempt_and_restricted_word_stays_blocked(self):
        lease = self.lease()
        with rb.os_lock(self.external), patch.object(ex.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(rb.ResourceBusy, 'OS_LOCK_BUSY'):
                self.run_lease(lease)
            spawn.assert_not_called()
        self.assertEqual(self.b.db.execute('SELECT count(*) FROM resource_execution_attempts').fetchone()[0], 0)
        self.b.configure('word-zotero')
        word = self.b.request_enforced(dict(self.contract, resource='word-zotero', attempt_id=str(uuid.uuid4())))
        self.b.db.execute('CREATE TABLE word_restricted_tickets(id TEXT,status TEXT)')
        self.b.db.execute("INSERT INTO word_restricted_tickets VALUES('old-word','PENDING')")
        with self.assertRaisesRegex(rb.ResourceBusy, 'RESTRICTED_WORD_UNRESOLVED'):
            self.b.grant_enforced(word['id'])

    def test_communicate_failure_drains_owned_child_under_original_locks(self):
        self.script.write_text('import time\ntime.sleep(.3)\n')
        self.contract['transport_artifacts'][1] = ref(self.script)
        lease = self.lease()
        popen = subprocess.Popen
        children = []
        def spawn(*args, **kwargs):
            proc = popen(*args, **kwargs)
            children.append(proc)
            proc.communicate = lambda: (_ for _ in ()).throw(OSError('fixture read error'))
            original_wait = proc.wait
            def waiting(*a, **k):
                for lock in (self.external, self.b.lock_path('test-resource')):
                    with self.assertRaisesRegex(rb.ResourceBusy, 'OS_LOCK_BUSY'):
                        with rb.os_lock(lock):
                            pass
                return original_wait(*a, **k)
            proc.wait = waiting
            return proc
        try:
            with patch.object(ex.subprocess, 'Popen', side_effect=spawn):
                with self.assertRaisesRegex(OSError, 'fixture read error'):
                    self.run_lease(lease)
            self.assertIsNotNone(children[0].poll())
            self.assertEqual(self.b.db.execute('SELECT state FROM resource_execution_attempts').fetchone()[0], 'UNKNOWN')
            self.assertEqual(self.b.get(lease['id'])['status'], 'ACTIVE')
            with patch.object(ex.subprocess, 'Popen') as again:
                with self.assertRaisesRegex(ex.ExecutionError, 'NO_REPLAY'):
                    self.run_lease(lease)
                again.assert_not_called()
        finally:
            for proc in children:
                if proc.poll() is None:
                    proc.kill()
                    subprocess.Popen.wait(proc)
                if proc.stdout:
                    proc.stdout.close()
                if proc.stderr:
                    proc.stderr.close()

    def test_unknown_readback_must_bind_original_attempt_and_zero_effects(self):
        lease = self.lease()
        # Fixture simulates a recorded owned child whose terminal readback was
        # interrupted. No real process is signalled or selected by this test.
        result = self.run_lease(lease)
        self.b.db.execute("UPDATE resource_execution_attempts SET state='UNKNOWN'")
        attempt = dict(self.b.db.execute('SELECT * FROM resource_execution_attempts').fetchone())
        path = self.root/'readback.json'
        body = dict(schema='resource-execution-zero-effects-readback-v1', ticket_id=lease['id'],
                    **{k: lease['execution'][k] for k in ex.KEYS},
                    intent_sha256=attempt['intent_sha256'], pgid=attempt['pgid'],
                    zero_effects=True, process_group_empty=True, remote_terminal=True)
        body['attempt_id'] = 'other-attempt'
        path.write_text(json.dumps(body))
        proof = dict(self.proof(lease, result), execution_readback=ref(path))
        with self.assertRaisesRegex(ex.ExecutionError, 'EXACT_ZERO_EFFECTS'):
            self.b.release_enforced(lease['id'], lease['token'], proof, generation=lease['execution']['generation'], reconcile=True)
        body['attempt_id'] = lease['execution']['attempt_id']
        body['zero_effects'] = False
        path.write_text(json.dumps(body))
        proof['execution_readback'] = ref(path)
        with self.assertRaisesRegex(ex.ExecutionError, 'EXACT_ZERO_EFFECTS'):
            self.b.release_enforced(lease['id'], lease['token'], proof, generation=lease['execution']['generation'], reconcile=True)
        body['zero_effects'] = True
        path.write_text(json.dumps(body))
        proof['execution_readback'] = ref(path)
        released = self.b.release_enforced(lease['id'], lease['token'], proof, generation=lease['execution']['generation'], reconcile=True)
        self.assertEqual(released['status'], 'RELEASED')
        self.assertEqual(released['execution']['state'], 'UNKNOWN')

    def test_loaded_source_change_and_stale_code_never_accept_new_disk_pins(self):
        source = self.root/'copied-source'
        scripts = source/'scripts'
        scripts.mkdir(parents=True)
        for module in (rb, ex):
            (scripts/Path(module.__file__).name).write_bytes(Path(module.__file__).read_bytes())
        code = '''import importlib.util,sys
from pathlib import Path
p=Path(sys.argv[1]); kind=sys.argv[2]
spec=importlib.util.spec_from_file_location('copied_broker',p/'resource_broker.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
helper=module.Broker._execution_module()
target=p/('resource_broker.py' if kind=='broker' else 'resource_execution.py')
target.write_bytes(target.read_bytes()+b'\\n# newer disk source\\n')
module.Broker._execution_module()
'''
        for kind in ('helper', 'broker'):
            for module in (rb, ex):
                (scripts/Path(module.__file__).name).write_bytes(Path(module.__file__).read_bytes())
            run = subprocess.run([sys.executable, '-B', '-c', code, str(scripts), kind],
                                 cwd=self.root, capture_output=True, text=True, timeout=10, env={})
            self.assertNotEqual(run.returncode, 0)
            self.assertIn('LOADED_'+('BROKER' if kind == 'broker' else 'EXECUTION')+'_SOURCE_CHANGED', run.stderr)
        # Same on-disk bytes and claimed SHA do not bless a different loaded code
        # object (for example a stale loader/pyc with altered executable logic).
        with patch.object(ex, '_EXECUTION_LOADED_CODE', compile('pass', ex.__file__, 'exec')):
            with self.assertRaisesRegex(rb.ResourceBusy, 'LOADED_EXECUTION_SOURCE_CHANGED'):
                rb.Broker.validate_execution_contract(self.contract)

    def test_terminal_release_requires_original_result_digest(self):
        lease = self.lease()
        result = self.run_lease(lease)
        bad = copy.deepcopy(result)
        bad['execution']['result_sha256'] = '0'*64
        with self.assertRaisesRegex(ex.ExecutionError, 'ORIGINAL_ATTEMPT_TERMINAL_REQUIRED'):
            self.release(lease, bad)
        with patch.object(ex, 'native_identity', return_value={}):
            with self.assertRaisesRegex(ex.ExecutionError, 'NATIVE_EXECUTION_ACTOR'):
                self.release(lease, result)
        with self.assertRaisesRegex(ex.ExecutionError, 'GENERATION_MISMATCH'):
            self.b.release_enforced(lease['id'], lease['token'], self.proof(lease, result), generation=2)
        with patch.object(rb, 'group_alive', side_effect=lambda pgid: pgid is not None):
            with self.assertRaisesRegex(ex.ExecutionError, 'ATTEMPT_DRAIN_REQUIRED'):
                self.release(lease, result)
        self.assertEqual(self.release(lease, result)['status'], 'RELEASED')

    def test_nlm_release_preserves_remote_receipt_and_execution_binding(self):
        self.b.configure('nlm-account:fixture')
        self.contract['resource'] = 'nlm-account:fixture'
        lease = self.lease()
        result = self.run_lease(lease)
        terminal = self.root/'terminal.json'
        receipt = {'remote_terminal': True, 'process_group_empty': True, 'status': 'COMPLETE',
                   'request_id': 'request-fixture', 'resource_lease': lease, 'operation': 'fixture_write'}
        receipt['operation'] = 'wrong'
        terminal.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ex.ExecutionError, 'NLM_TERMINAL_EXECUTION_MISMATCH'):
            self.release(lease, result, in_flight_requests=[], terminal_receipt=ref(terminal))
        receipt['operation'] = 'fixture_write'
        receipt['resource_lease'] = copy.deepcopy(lease)
        receipt['resource_lease']['execution']['attempt_id'] = 'wrong'
        terminal.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ex.ExecutionError, 'NLM_TERMINAL_EXECUTION_MISMATCH'):
            self.release(lease, result, in_flight_requests=[], terminal_receipt=ref(terminal))
        receipt['resource_lease'] = lease
        terminal.write_text(json.dumps(receipt))
        self.assertEqual(self.release(lease, result, in_flight_requests=[], terminal_receipt=ref(terminal))['status'], 'RELEASED')

    def test_dynamic_broker_import_works_without_scripts_path_and_rejects_foreign_helper(self):
        with patch.dict(sys.modules, {'resource_execution': type('Foreign', (), {'__file__': '/foreign/resource_execution.py'})()}):
            with self.assertRaisesRegex(rb.ResourceBusy, 'FOREIGN_RESOURCE_EXECUTION_MODULE'):
                rb.Broker.validate_execution_contract(self.contract)
        # Real fresh interpreter loads only the pinned broker from an explicit path;
        # malformed contract fails before native I/O or any DB access.
        code = ('import importlib.util,sys; '
                's=importlib.util.spec_from_file_location("thesis_resource_broker",sys.argv[1]); '
                'm=importlib.util.module_from_spec(s);s.loader.exec_module(m); '
                'm.Broker.validate_execution_contract({})')
        run = subprocess.run([sys.executable, '-B', '-c', code, rb.__file__], cwd=self.root,
                             capture_output=True, text=True, timeout=10, env={})
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('EXACT_EXECUTION_CONTRACT_REQUIRED', run.stderr)
        self.assertNotIn('ModuleNotFoundError', run.stderr)


if __name__ == '__main__':
    unittest.main()
