"""Private database fixtures; no broker constructor or application is executed."""
import sys
sys.dont_write_bytecode = True
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

import federated_resource_preflight as p


class ResourcePreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / 'global-resources'
        self.root.mkdir()
        self.source = self.base / 'original-controller'
        (self.source / 'scripts').mkdir(parents=True)
        self.executor = {key: str(uuid.uuid4()) for key in
                         ('workspace_uuid', 'surface_uuid', 'pane_uuid', 'session_id')}
        self.ticket = str(uuid.uuid4())
        self.disposition = str(uuid.uuid4())
        self.input = self.base / 'tracked.docx'
        self.input.write_bytes(b'offline input fixture; never opened in an application')
        names = ('word_restricted_broker.py', 'word_restricted_pdf.py', 'resource_broker.py',
                 'cmux_workspace_guard.py', 'word_restricted_revision.py')
        closure = {}
        for name in names:
            path = self.source / 'scripts' / name
            path.write_text('raise AssertionError("preflight must never import controller code")\n')
            closure[name] = self.pin(path)
        self.job = {'schema': 'owned-initial-word-job-v1', 'task_id': 'original-owned-task',
                    'actor': self.executor, 'input': self.pin(self.input),
                    'zotero_refresh': False, 'final_paper_acceptance': False}
        self.job_ref = self.save('job.json', self.job)
        self.contract = {'schema': 'word-restricted-disposition-v2',
                         'scope': 'OWNED_REVISION_ROUNDTRIP', 'successor': self.executor,
                         'initial_job': self.job_ref, 'original_ticket': 'old-ticket',
                         'original_resource_sha256': p.digest({'name': 'word-zotero',
                             'external_locks': '[]', 'release_requirements': '{}'})}
        self.contract_ref = self.save('contract.json', self.contract)
        self.plan = {'operation': 'OWNED_REVISION_ROUNDTRIP', 'actor': self.executor,
                     'controller_source': str(self.source), 'controller_pins': closure,
                     'input': self.pin(self.input), 'initial_job': self.job_ref}
        self.plan_ref = self.save('plan.json', self.plan)
        self.database = self.root / 'queue.sqlite3'
        with sqlite3.connect(self.database) as db:
            db.executescript('''
              CREATE TABLE resources(name TEXT PRIMARY KEY, external_locks TEXT,
                release_requirements TEXT);
              CREATE TABLE word_restricted_tickets(id TEXT PRIMARY KEY, disposition TEXT,
                plan TEXT, status TEXT, requested REAL, token TEXT, expires REAL, nonce TEXT,
                parent_pid INTEGER, pgid INTEGER, invocations INTEGER, result TEXT);
              CREATE TABLE word_restricted_revision_dispositions(id TEXT PRIMARY KEY,
                original_ticket TEXT, job_sha256 TEXT, contract TEXT, applied REAL);
              CREATE TABLE word_restricted_dispositions(id TEXT PRIMARY KEY,
                original_ticket TEXT, contract TEXT, applied REAL);
            ''')
            db.execute('INSERT INTO resources VALUES(?,?,?)', ('word-zotero', '[]', '{}'))
            db.execute('INSERT INTO word_restricted_revision_dispositions VALUES(?,?,?,?,?)',
                (self.disposition, 'old-ticket', self.job_ref['sha256'], json.dumps(self.contract_ref), 1.0))
            db.execute('INSERT INTO word_restricted_tickets VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (self.ticket, self.disposition, json.dumps(self.plan_ref), 'WAITING', 1.0,
                 None, None, None, None, None, 0, None))
        self.request = {'schema': p.SCHEMA, 'resource_root': str(self.root), 'resource': 'word-zotero',
                        'operation': 'OWNED_REVISION_ROUNDTRIP', 'task_id': self.job['task_id'],
                        'executor': self.executor}
        self.refresh_request()

    def pin(self, path):
        raw = path.read_bytes()
        return {'path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}

    def save(self, name, value):
        path = self.base / name
        path.write_text(json.dumps(value))
        return self.pin(path)

    def refresh_request(self):
        observed = p.inspect(self.ticket, expected_root=self.root)
        self.request['existing'] = {key: observed[key] for key in
            ('ticket_id', 'ticket_sha256', 'plan_ref', 'disposition_ref', 'controller_ref')}

    def update(self, **values):
        with sqlite3.connect(self.database) as db:
            db.execute('UPDATE word_restricted_tickets SET ' + ','.join(k+'=?' for k in values) +
                       ' WHERE id=?', [*values.values(), self.ticket])
        self.refresh_request()

    def files(self):
        return {str(x.relative_to(self.base)): (x.stat().st_mtime_ns, x.read_bytes())
                for x in self.base.rglob('*') if x.is_file()}

    def check(self, request=None):
        return p.preflight(self.request if request is None else request, expected_root=self.root)

    def test_matching_existing_job_is_only_a_route_to_original_controller(self):
        before = self.files()
        with patch('subprocess.Popen', side_effect=AssertionError('no subprocess')):
            observed = self.check()
        self.assertEqual(observed['status'], 'ORIGINAL_CONTROLLER_RECHECK_REQUIRED')
        self.assertEqual(observed['original_executor'], self.executor)
        self.assertEqual(observed['original_task_id'], 'original-owned-task')
        self.assertTrue(observed['task_binding_matched'])
        for key in ('input_sent', 'application_invoked', 'queue_changed', 'lease_granted',
                    'execution_authorized', 'native_executor_verified', 'original_approval_verified',
                    'global_root_authenticated', 'accepted', 'service_and_live_guards_checked'):
            self.assertIs(observed[key], False)
        self.assertEqual(self.files(), before)

    def test_readonly_uri_query_only_and_schema_and_mtime_remain_unchanged(self):
        original = sqlite3.connect
        connections, statements = [], []
        def connect(database_uri, **kwargs):
            self.assertTrue(database_uri.endswith('?mode=ro'))
            self.assertNotIn(str(self.root), database_uri)
            self.assertTrue(kwargs['uri'])
            db = original(database_uri, **kwargs)
            db.set_trace_callback(statements.append)
            connections.append(db)
            return db
        before = self.files()
        with patch.object(p.sqlite3, 'connect', side_effect=connect):
            self.check()
        self.assertEqual(len(connections), 1)
        self.assertIn('PRAGMA query_only=ON', statements)
        self.assertFalse(any(s.lstrip().split()[0].upper() in
                             ('INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER', 'DROP') for s in statements))
        self.assertEqual(self.files(), before)

    def test_committed_uncheckpointed_wal_is_read_without_source_shm_changes(self):
        db = sqlite3.connect(self.database, isolation_level=None)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA wal_autocheckpoint=0')
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            main_before = self.database.read_bytes()
            db.execute("UPDATE word_restricted_tickets SET status='PENDING',invocations=1 WHERE id=?", (self.ticket,))
            self.assertEqual(self.database.read_bytes(), main_before)
            self.assertGreater(Path(str(self.database)+'-wal').stat().st_size, 0)
            before = self.files()
            observed = p.inspect(self.ticket, expected_root=self.root)
            self.assertEqual(observed['ticket_status'], 'PENDING')
            self.assertEqual(observed['invocations'], 1)
            self.assertEqual(observed['status'], 'ORIGINAL_RECONCILIATION_REQUIRED')
            self.assertEqual(self.files(), before)
        finally:
            db.close()

    def test_concurrent_source_change_is_unknown_without_retry(self):
        images = p.queue_images(self.database)
        changed = copy.deepcopy(images)
        changed['']['sha256'] = '0' * 64
        with patch.object(p, 'queue_images', side_effect=[images, changed]):
            with self.assertRaisesRegex(p.PreflightError, 'QUEUE_CHANGED_DURING_SNAPSHOT'):
                p.inspect(self.ticket, expected_root=self.root)

    def test_native_successor_disposition_schema_is_supported_without_approval_claim(self):
        self.contract['schema'] = 'word-restricted-native-successor-disposition-v1'
        self.contract_ref = self.save('contract.json', self.contract)
        with sqlite3.connect(self.database) as db:
            db.execute('UPDATE word_restricted_revision_dispositions SET contract=? WHERE id=?',
                       (json.dumps(self.contract_ref), self.disposition))
        self.refresh_request()
        self.assertFalse(self.check()['original_approval_verified'])
        self.assertEqual(self.check()['status'], 'ORIGINAL_CONTROLLER_RECHECK_REQUIRED')

    def test_declared_new_task_without_existing_contract_is_binding_required(self):
        request = {k: v for k, v in self.request.items() if k != 'existing'}
        with patch.object(p.sqlite3, 'connect', side_effect=AssertionError('no DB for unsupported declarations')):
            observed = self.check(request)
        self.assertEqual(observed['status'], 'BINDING_REQUIRED')
        self.assertFalse(observed['execution_authorized'])

    def test_other_actor_task_ticket_and_root_are_rejected(self):
        cases = []
        value = copy.deepcopy(self.request); value['executor']['session_id'] = str(uuid.uuid4())
        cases.append((value, 'EXECUTOR_BINDING_MISMATCH'))
        value = copy.deepcopy(self.request); value['task_id'] = 'invented-new-task'
        cases.append((value, 'ORIGINAL_TASK_BINDING_MISMATCH'))
        value = copy.deepcopy(self.request); value['existing']['ticket_id'] = str(uuid.uuid4())
        cases.append((value, 'EXISTING_RESTRICTED_TICKET_REQUIRED'))
        alternate = self.base / 'second-queue'; alternate.mkdir()
        value = copy.deepcopy(self.request); value['resource_root'] = str(alternate)
        cases.append((value, 'GLOBAL_RESOURCE_ROOT_MISMATCH'))
        for request, reason in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(p.PreflightError, reason):
                self.check(request)
        self.assertEqual(list(alternate.iterdir()), [])

    def test_stale_ticket_record_and_wrong_original_refs_are_rejected(self):
        for key in ('ticket_sha256', 'plan_ref', 'disposition_ref', 'controller_ref'):
            request = copy.deepcopy(self.request)
            if key == 'ticket_sha256':
                request['existing'][key] = '0' * 64
            else:
                request['existing'][key]['sha256'] = '0' * 64
            with self.subTest(key=key), self.assertRaisesRegex(p.PreflightError, 'MISMATCH'):
                self.check(request)

    def test_original_plan_or_contract_byte_change_is_rejected(self):
        for ref in (self.plan_ref, self.contract_ref):
            path = Path(ref['path']); original = path.read_bytes()
            path.write_bytes(original + b' ')
            with self.subTest(path=path), self.assertRaisesRegex(p.PreflightError, 'FILE_PIN_CHANGED'):
                self.check()
            path.write_bytes(original)

    def test_original_controller_is_hashed_not_imported_and_changed_member_rejected(self):
        path = self.source / 'scripts/resource_broker.py'
        path.write_text('raise AssertionError("changed")\n')
        with self.assertRaisesRegex(p.PreflightError, 'FILE_PIN_CHANGED'):
            self.check()

    def test_pending_unknown_and_any_previous_invocation_only_route_to_reconciliation(self):
        for state in ('PENDING', 'UNKNOWN', 'COMPLETE_WITH_FOREIGN_UNKNOWN'):
            self.update(status=state, invocations=1, nonce='original-nonce', result='{"pending":true}')
            observed = self.check()
            self.assertEqual(observed['status'], 'ORIGINAL_RECONCILIATION_REQUIRED')
            self.assertEqual(observed['next_action'], 'READ_BACK_ORIGINAL_ATTEMPT_WITH_ORIGINAL_CONTROLLER')
            self.assertFalse(observed['execution_authorized'])
        self.update(status='ACTIVE', invocations=1, nonce=None, result=None, expires=time.time()+300)
        self.assertEqual(self.check()['status'], 'ORIGINAL_RECONCILIATION_REQUIRED')

    def test_expired_or_unconfirmed_active_lease_does_not_become_a_new_run(self):
        for expires in (time.time()-1, None):
            self.update(status='ACTIVE', expires=expires)
            self.assertEqual(self.check()['status'], 'ORIGINAL_RECONCILIATION_REQUIRED')

    def test_pdf_record_match_does_not_fabricate_business_task_binding(self):
        self.plan.update(operation='OWNED_PDF_ONLY')
        self.plan.pop('initial_job')
        self.plan_ref = self.save('plan.json', self.plan)
        self.contract.update(schema='word-restricted-disposition-v1', scope='OWNED_PDF_ONLY')
        self.contract.pop('initial_job')
        self.contract_ref = self.save('contract.json', self.contract)
        with sqlite3.connect(self.database) as db:
            db.execute('DELETE FROM word_restricted_revision_dispositions')
            db.execute('INSERT INTO word_restricted_dispositions VALUES(?,?,?,?)',
                       (self.disposition, 'old-ticket', json.dumps(self.contract_ref), 1))
            db.execute('UPDATE word_restricted_tickets SET plan=? WHERE id=?', (json.dumps(self.plan_ref), self.ticket))
        self.request['operation'] = 'OWNED_PDF_ONLY'
        self.refresh_request()
        observed = p.inspect(self.ticket, expected_root=self.root)
        self.assertEqual(observed['status'], 'ORIGINAL_RECORDS_MATCH')
        self.assertIsNone(observed['original_task_id'])
        self.assertEqual(self.check()['status'], 'BINDING_REQUIRED')
        self.assertFalse(self.check()['task_binding_matched'])

    def test_mcp_zotero_and_arbitrary_argv_are_rejected_without_db_access(self):
        with patch.object(p.sqlite3, 'connect', side_effect=AssertionError('no queue reads')):
            for operation in ('ZOTERO_REFRESH', 'MCP_CALL', 'RUN_ARGV'):
                request = dict(self.request, operation=operation)
                self.assertEqual(self.check(request)['status'], 'UNSUPPORTED_ENTRYPOINT')
            with self.assertRaisesRegex(p.PreflightError, 'UNSUPPORTED_ENTRYPOINT'):
                self.check(dict(self.request, argv=['osascript', '-e', 'anything']))

    def test_missing_queue_cannot_be_created_by_preflight(self):
        self.database.unlink()
        before = self.files()
        with self.assertRaises(OSError):
            self.check()
        self.assertFalse(self.database.exists())
        self.assertEqual(before, self.files())

    def test_duplicate_disposition_cannot_select_a_different_contract(self):
        with sqlite3.connect(self.database) as db:
            db.execute('INSERT INTO word_restricted_dispositions VALUES(?,?,?,?)',
                       (self.disposition, 'different-ticket', json.dumps(self.contract_ref), 1))
        with self.assertRaisesRegex(p.PreflightError, 'ONE_EXISTING_DISPOSITION_REQUIRED'):
            self.check()

    def test_disposition_record_must_keep_original_ticket_job_and_resource_mapping(self):
        for column, replacement, reason in (
                ('original_ticket', 'another-original', 'ORIGINAL_DISPOSITION_TICKET_MISMATCH'),
                ('job_sha256', '0' * 64, 'ORIGINAL_DISPOSITION_JOB_MISMATCH')):
            with sqlite3.connect(self.database) as db:
                old = db.execute('SELECT '+column+' FROM word_restricted_revision_dispositions').fetchone()[0]
                db.execute('UPDATE word_restricted_revision_dispositions SET '+column+'=?', (replacement,))
            with self.subTest(column=column), self.assertRaisesRegex(p.PreflightError, reason):
                self.check()
            with sqlite3.connect(self.database) as db:
                db.execute('UPDATE word_restricted_revision_dispositions SET '+column+'=?', (old,))
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE resources SET external_locks='[\"different-lock\"]'")
        with self.assertRaisesRegex(p.PreflightError, 'ORIGINAL_RESOURCE_MAPPING_CHANGED'):
            self.check()

    def test_cli_has_no_root_override_and_reports_non_authorization(self):
        path = self.base / 'request.json'
        path.write_text(json.dumps(dict(self.request, operation='ZOTERO_REFRESH')))
        stream = io.StringIO()
        with patch('sys.stdout', stream):
            self.assertEqual(p.main(['--request', str(path)]), 2)
        self.assertEqual(json.loads(stream.getvalue())['status'], 'UNSUPPORTED_ENTRYPOINT')
        with patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit):
            p.main(['--ticket', self.ticket, '--root', str(self.root)])

    def test_cli_rejects_real_fifo_without_blocking_and_still_reads_regular_request(self):
        fifo = self.base / 'request.fifo'
        os.mkfifo(fifo)
        request = self.base / 'regular-request.json'
        request.write_text(json.dumps(dict(self.request, operation='ZOTERO_REFRESH')))
        before = self.files()
        fifo_before = fifo.stat()
        metadata = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        for path, status, reason in (
                (fifo, 'REJECTED', 'BOUNDED_REGULAR_FILE_REQUIRED'),
                (request, 'UNSUPPORTED_ENTRYPOINT', 'ONLY_EXISTING_RESTRICTED_WORD_OPERATIONS_ARE_INSPECTED')):
            with self.subTest(kind=path.name):
                completed = subprocess.run([sys.executable, '-B', str(Path(p.__file__).resolve()),
                    '--request', str(path)], capture_output=True, text=True, timeout=3)
                self.assertEqual(completed.returncode, 2, completed.stderr)
                observed = json.loads(completed.stdout)
                self.assertEqual((observed['status'], observed['reason']), (status, reason))
                self.assertFalse(observed['queue_changed'])
                self.assertFalse(observed['application_invoked'])
                self.assertFalse(observed['execution_authorized'])
        self.assertEqual(before, self.files())
        self.assertEqual(metadata(fifo_before), metadata(fifo.stat()))

    def test_pinned_fifo_is_rejected_without_blocking_and_regular_pin_still_reads(self):
        fifo = self.base / 'pinned.fifo'
        os.mkfifo(fifo)
        reference = {'path': str(fifo), 'bytes': 0, 'sha256': hashlib.sha256(b'').hexdigest()}
        code = ('import sys,json; sys.path.insert(0,sys.argv[1]); '
                'import federated_resource_preflight as p; '
                'ref=json.loads(sys.argv[2]); '\
                '\ntry: p.read_pin(ref)\n'
                'except p.PreflightError as exc: print(str(exc)); sys.exit(2)\n'
                'sys.exit(0)\n')
        before = self.files()
        fifo_before = fifo.stat()
        completed = subprocess.run([sys.executable, '-B', '-c', code,
            str(Path(p.__file__).resolve().parent), json.dumps(reference)],
            capture_output=True, text=True, timeout=3)
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertEqual(completed.stdout.strip(), 'BOUNDED_REGULAR_FILE_REQUIRED')
        self.assertEqual(p.read_pin(self.pin(self.input)), self.input.read_bytes())
        self.assertEqual(before, self.files())
        self.assertEqual((fifo_before.st_ino, fifo_before.st_mtime_ns, fifo_before.st_ctime_ns),
                         (fifo.stat().st_ino, fifo.stat().st_mtime_ns, fifo.stat().st_ctime_ns))


if __name__ == '__main__':
    unittest.main(verbosity=2)
