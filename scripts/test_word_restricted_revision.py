"""Private queue and native-template controls. These never operate real apps."""
import copy
import hashlib
import json
import os
from pathlib import Path
import py_compile
import subprocess
import time
import types
import unittest
from unittest.mock import patch
import uuid
import xml.etree.ElementTree as ET
import zipfile

from resource_broker import ResourceBusy, os_lock
import word_restricted_broker as protocol
import word_restricted_pdf as pdf
import word_restricted_revision as revision
import test_word_restricted as legacy_tests


class OwnedRevisionTests(unittest.TestCase):
    def setUp(self):
        self.f = legacy_tests.RestrictedWordTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.root
        pane = str(uuid.uuid4())
        self.f.successor['pane_uuid'] = pane
        self.f.admin = dict(self.f.successor)
        self.f.identity.return_value = self.f.admin
        self.f.contract.update(administrator=self.f.admin, successor=self.f.successor)
        self.stage_parent = self.f.user / 'Library/Containers/com.microsoft.Word/Data/Documents/wordrev-pdf'
        self.stage_parent.mkdir(parents=True)
        self.input = self.root / 'input-tracked.docx'
        xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
               '<w:body><w:p><w:r><w:rPr><w:rPrChange w:id="10" w:author="Original"><w:rPr/>'
               '</w:rPrChange></w:rPr><w:t>正文</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:tcPr>'
               '<w:tcPrChange w:id="11" w:author="Original"><w:tcPr/></w:tcPrChange>'
               '</w:tcPr><w:p/></w:tc></w:tr></w:tbl></w:body></w:document>')
        with zipfile.ZipFile(self.input, 'w') as z:
            z.writestr('word/document.xml', xml)
        self.approval('修订记录稿、干净 DOCX、Word 原生导出 PDF')

    def approval(self, extra):
        plan = {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [
            {'type': 'output_text', 'text': self.f.owner_session + ' ' + self.f.original['workspace_uuid'] +
             '：核收旧 Word 资源票后继任；不保存或关闭归属不明的“文件1”。' + extra}]}}
        user = {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [
            {'type': 'input_text', 'text': 'Implement the plan.'}]}}
        a = (json.dumps(plan, ensure_ascii=False) + '\n').encode()
        b = (json.dumps(user) + '\n').encode()
        p = Path(self.f.contract['approval']['path']); p.write_bytes(a + b)
        s = p.stat()
        locator = lambda raw, offset: {'path': str(p), 'identity': [s.st_dev, s.st_ino],
                  'offset': offset, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        self.f.contract.update(approved_plan=locator(a, 0), approval=locator(b, len(a)))

    def job(self, name='one'):
        run = self.root / ('run-' + name); run.mkdir()
        (run / 'artifacts').mkdir(); (run / 'evidence').mkdir()
        job = {'schema': 'owned-initial-word-job-v1', 'task_id': 'private-owned-' + name,
               'actor': self.f.successor, 'run_dir': str(run), 'input': protocol.pin(self.input),
               'outputs': {label: str(run / 'artifacts' / (label + '.docx')) for label in revision.LABELS},
               'pdf_output': str(run / 'evidence' / 'accepted.pdf'),
               'native_actions': ['PRESERVE_TRACKED', 'ACCEPT_ALL', 'REJECT_ALL', 'PDF_EXPORT'],
               'zotero_refresh': False, 'final_paper_acceptance': False,
               'validation_inputs': {label: protocol.pin(self.input) for label in ('accepted', 'rejected')}}
        return job, self.f.write(name + '-initial-job.json', job)

    def disposition(self, ref, name='one', *, pdf_only=False):
        contract = copy.deepcopy(self.f.contract)
        if not pdf_only:
            contract.update(schema='word-restricted-disposition-v2', scope=protocol.REVISION_SCOPE, initial_job=ref)
        pinned = self.f.write(name + '-disposition.json', contract)
        preview = self.f.broker.preview(pinned)
        result = self.f.broker.apply(pinned, preview['plan_sha256'])
        return result['disposition_id'], pinned

    def plan(self, job, ref, name='one'):
        source = Path(protocol.__file__).resolve().parent.parent
        directories = [Path(job['run_dir']), Path(job['run_dir']) / 'artifacts',
                       Path(job['run_dir']) / 'evidence', self.stage_parent]
        plan = {'operation': protocol.REVISION_SCOPE, 'actor': self.f.successor,
                'controller_source': str(source),
                'controller_pins': {p.name: protocol.pin(p) for p in (source / 'scripts').glob('*.py')},
                'service_pins': self.f.services, 'validator_pins': self.f.validators,
                'input': job['input'], 'output': job['pdf_output'],
                'evidence_root': str(Path(job['run_dir']) / 'evidence' / 'restricted-native'),
                'required_free_bytes': protocol.FLOOR,
                'locks': {str(p): [p.stat().st_dev, p.stat().st_ino] for p in self.f.lock_paths},
                'initial_job': ref, 'revision_outputs': job['outputs'], 'stage_id': uuid.uuid4().hex,
                'start_word_if_absent': False,
                'directory_identities': {str(p): [p.stat().st_dev, p.stat().st_ino] for p in directories}}
        return plan, self.f.write(name + '-plan.json', plan)

    def request(self, name='one'):
        job, ref = self.job(name)
        disposition, contract = self.disposition(ref, name)
        plan, plan_ref = self.plan(job, ref, name)
        ticket = self.f.broker.request(disposition, plan_ref)
        return ticket, plan, plan_ref, contract

    def child(self, ticket, plan, ref, *, corrupt=None):
        outer = self
        class Child:
            pid = 100000700
            returncode = 0
            def __init__(self, argv, **kwargs):
                outer.assertEqual(argv[1], '-BE')
                outer.assertEqual(Path(argv[2]).name, 'word_restricted_revision.py')
                row = outer.f.broker.get(ticket['id'])
                root = Path(plan['evidence_root']); root.mkdir()
                output = Path(plan['output']); output.write_bytes(b'%PDF-private-fixture')
                docs = {}
                for label, path in plan['revision_outputs'].items():
                    Path(path).write_bytes(b'private mock native output')
                    docs[label] = protocol.pin(path)
                report = {'ticket': ticket['id'], 'nonce': row['nonce'], 'plan': ref,
                          'owned_cleanup': True, 'foreign_observation_preserved': True, 'pending': False,
                          'input': plan['input'], 'pdf': protocol.pin(output), 'operation': protocol.REVISION_SCOPE,
                          'initial_job': plan['initial_job'], 'revision_outputs': docs, 'zotero_refreshed': False}
                if corrupt:
                    corrupt(report)
                (root / 'restricted-result.json').write_text(json.dumps(report))
            def communicate(self, **kwargs):
                return b'private mock only', b''
        return Child

    def test_preview_never_writes_schema_or_old_resource(self):
        job, ref = self.job(); contract = {**self.f.contract, 'schema': 'word-restricted-disposition-v2',
                                         'scope': protocol.REVISION_SCOPE, 'initial_job': ref}
        before = list(self.f.legacy.db.iterdump())
        preview = self.f.broker.preview(self.f.write('preview-only.json', contract))
        self.assertEqual(preview['queue_changes'], 0)
        self.assertEqual(list(self.f.legacy.db.iterdump()), before)

    def test_pdf_approval_cannot_authorize_revision(self):
        self.approval('只允许导出PDF')
        _job, ref = self.job()
        with self.assertRaisesRegex(ResourceBusy, 'REVISION_DELIVERABLES'):
            self.disposition(ref)
        self.assertIsNone(self.f.legacy.db.execute("SELECT 1 FROM sqlite_master WHERE name='word_restricted_revision_dispositions'").fetchone())

    def test_pdf_disposition_cannot_request_new_write_capability(self):
        job, ref = self.job(); disposition, _contract = self.disposition(ref, pdf_only=True)
        _plan, plan_ref = self.plan(job, ref)
        with self.assertRaisesRegex(ResourceBusy, 'DISPOSITION_OPERATION'):
            self.f.broker.request(disposition, plan_ref)
        self.assertEqual(self.f.broker.db.execute('SELECT COUNT(*) FROM word_restricted_tickets').fetchone()[0], 0)

    def test_wrong_original_pane_thread_or_surface_cannot_grant(self):
        ticket, _plan, _ref, _contract = self.request()
        for key in ('pane_uuid', 'session_id', 'surface_uuid'):
            self.f.identity.return_value = {**self.f.successor, key: str(uuid.uuid4())}
            with self.subTest(key=key), self.assertRaisesRegex(ResourceBusy, 'SUCCESSOR_REQUIRED'):
                self.f.broker.grant(ticket['id'])
        self.assertEqual(self.f.broker.get(ticket['id'])['status'], 'WAITING')

    def test_complete_closure_exact_directories_and_no_injection_fields(self):
        job, ref = self.job(); disposition, _contract = self.disposition(ref)
        plan, _plan_ref = self.plan(job, ref)
        changes = {
            'command': lambda p: p.update(command=['/usr/bin/osascript', 'anything']),
            'refresh': lambda p: p.update(operation='CITATION_REFRESH'),
            'closure': lambda p: p['controller_pins'].pop('word_restricted_revision.py'),
            'floor': lambda p: p.update(required_free_bytes=10),
            'foreign_output': lambda p: p['revision_outputs'].update(accepted=str(self.root / 'foreign.docx')),
            'directory': lambda p: p['directory_identities'][job['run_dir']].__setitem__(1, 0),
        }
        for name, change in changes.items():
            candidate = copy.deepcopy(plan); change(candidate)
            with self.subTest(name=name), self.assertRaises(ResourceBusy):
                self.f.broker.request(disposition, self.f.write(name + '-bad-plan.json', candidate))
        self.assertEqual(self.f.legacy.get(self.f.original['id']), self.f.original)

    def test_later_jobs_share_fifo_without_mutating_frozen_legacy(self):
        first, plan, ref, _contract = self.request('first')
        second, _p2, _r2, _c2 = self.request('second')
        with self.assertRaisesRegex(ResourceBusy, 'FIFO'):
            self.f.broker.grant(second['id'])
        grant = self.f.broker.grant(first['id'])
        with self.assertRaisesRegex(ResourceBusy, 'WRITER_BUSY'):
            self.f.broker.grant(second['id'])
        with patch.object(protocol.subprocess, 'Popen', self.child(first, plan, ref)):
            result = self.f.broker.run(first['id'], grant['token'])
        self.assertEqual(result['status'], 'COMPLETE_WITH_FOREIGN_UNKNOWN')
        self.assertEqual(self.f.broker.grant(second['id'])['status'], 'ACTIVE')
        self.assertEqual(self.f.legacy.get(self.f.original['id']), self.f.original)
        with self.assertRaises(ResourceBusy):
            self.f.legacy.grant(self.f.original['id'])

    def test_same_job_authority_and_completed_output_cannot_be_replayed(self):
        ticket, plan, ref, contract_ref = self.request()
        with self.assertRaisesRegex(ResourceBusy, 'AUTHORIZATION_EXISTS_NO_REPLAY'):
            self.f.broker.apply(contract_ref, self.f.broker.preview(contract_ref)['plan_sha256'])
        grant = self.f.broker.grant(ticket['id'])
        with patch.object(protocol.subprocess, 'Popen', self.child(ticket, plan, ref)):
            self.f.broker.run(ticket['id'], grant['token'])
        with self.assertRaises(ResourceBusy):
            self.f.broker.run(ticket['id'], grant['token'])

    def test_second_plan_for_same_initial_job_cannot_block_fifo(self):
        ticket, plan, _ref, _contract = self.request()
        other = copy.deepcopy(plan)
        other['stage_id'] = uuid.uuid4().hex
        with self.assertRaisesRegex(ResourceBusy, 'INITIAL_JOB_ALREADY_REQUESTED_NO_REPLAY'):
            self.f.broker.request(ticket['disposition'], self.f.write('second-stage-plan.json', other))
        self.assertEqual(self.f.broker.db.execute('SELECT COUNT(*) FROM word_restricted_tickets').fetchone()[0], 1)
        self.assertEqual(self.f.legacy.get(self.f.original['id']), self.f.original)

    def test_spawn_timeout_or_missing_doc_proof_stays_pending(self):
        ticket, plan, ref, _contract = self.request()
        grant = self.f.broker.grant(ticket['id'])
        with patch.object(protocol.subprocess, 'Popen', side_effect=OSError('private spawn refusal')) as spawned:
            result = self.f.broker.run(ticket['id'], grant['token'])
        self.assertEqual(spawned.call_count, 1)
        self.assertEqual(result['status'], 'PENDING')
        self.assertEqual(self.f.broker.get(ticket['id'])['invocations'], 1)
        next_ticket, _plan, _ref, _contract = self.request('later')
        with self.assertRaisesRegex(ResourceBusy, 'WRITER_BUSY'):
            self.f.broker.grant(next_ticket['id'])

    def test_missing_revision_output_report_cannot_complete(self):
        ticket, plan, ref, _contract = self.request()
        grant = self.f.broker.grant(ticket['id'])
        corrupt = lambda report: report['revision_outputs'].pop('rejected')
        with patch.object(protocol.subprocess, 'Popen', self.child(ticket, plan, ref, corrupt=corrupt)):
            result = self.f.broker.run(ticket['id'], grant['token'])
        self.assertEqual(result['status'], 'PENDING')
        self.assertEqual(self.f.legacy.get(self.f.original['id']), self.f.original)

    def test_each_native_template_binds_only_its_owned_document(self):
        job, ref = self.job(); plan, _ref = self.plan(job, ref)
        root = Path(plan['evidence_root']); root.mkdir()
        for label in revision.LABELS:
            (root / label).mkdir()
            for stage in revision.STAGES:
                path = root / label / (stage.lower() + '.applescript')
                raw = revision.expected_revision_script(plan, label, stage); path.write_bytes(raw)
                self.assertIn(str(revision.owned_path(plan, label)).encode(), raw)
                revision.validate_revision_stage(plan, root, label, stage, path)
                for extra in (b'\nactivate\n', b'\nrun VB macro macro name "ZoteroRefresh"\n',
                              b'\nclose document "foreign" saving yes\n'):
                    path.write_bytes(raw + extra)
                    with self.assertRaisesRegex(RuntimeError, 'EXACT_OWNED_REVISION'):
                        revision.validate_revision_stage(plan, root, label, stage, path)
                path.write_bytes(raw)
        with self.assertRaisesRegex(RuntimeError, 'UNKNOWN_OWNED_REVISION_STAGE'):
            revision.expected_revision_script(plan, 'tracked', 'REFRESH')

    def test_real_unchecked_pyc_is_ignored_by_new_source_loader(self):
        path = self.root / 'private_revision_fixture.py'; path.write_text('answer = 9\n')
        py_compile.compile(str(path), invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
        path.write_text('answer = 1\n')
        loader = revision.PinnedSourceFinder({'fixture': protocol.pin(path)})
        module = types.ModuleType(path.stem); loader.exec_module(module)
        self.assertEqual(module.answer, 1)

    def driver(self, plan, ref):
        args = types.SimpleNamespace(ticket='private-ticket', token='private-token', nonce='private-nonce')
        driver = revision.Driver(protocol, pdf, self.f.broker, args, ref, plan)
        driver.base.guard = lambda: {'expires': time.time() + 1800}
        return driver

    def test_word_start_requires_explicit_policy_before_launch(self):
        job, ref = self.job(); plan, plan_ref = self.plan(job, ref)
        driver = self.driver(plan, plan_ref)
        with patch.object(driver, '_word_pid', return_value=None), patch.object(revision.subprocess, 'run') as native_call:
            with self.assertRaisesRegex(ResourceBusy, 'START_NOT_AUTHORIZED'):
                driver.bind_apps()
        self.assertEqual(native_call.call_count, 0)

    def test_mock_save_close_reopen_preserves_original_headers_and_clears_accepted(self):
        job, ref = self.job(); plan, plan_ref = self.plan(job, ref)
        driver = self.driver(plan, plan_ref); driver.root.mkdir()
        revision.owned_path(plan, 'tracked').parent.mkdir()
        calls = []
        def native_call(argv, **kwargs):
            path = Path(argv[1]); label = path.parent.name; stage = path.stem.upper()
            revision.validate_revision_stage(plan, driver.root, label, stage, path)
            self.assertEqual(argv[0], '/usr/bin/osascript'); calls.append((label, stage))
            if stage == 'SAVE' and label != 'tracked':
                owned = revision.owned_path(plan, label)
                with zipfile.ZipFile(owned) as archive:
                    tree = ET.fromstring(archive.read('word/document.xml'))
                for parent in tree.iter():
                    for node in list(parent):
                        if node.tag in (revision.W + 'rPrChange', revision.W + 'tcPrChange'):
                            parent.remove(node)
                with zipfile.ZipFile(owned, 'w') as archive:
                    archive.writestr('word/document.xml', ET.tostring(tree))
            return types.SimpleNamespace(returncode=0, stdout=stage + '\n', stderr='')
        before = revision.revision_headers(self.input)
        self.assertEqual(sum(map(len, before.values())), 2)
        with patch.object(revision.subprocess, 'run', side_effect=native_call):
            tracked, _saved = driver.roundtrip('tracked', protocol.pin(self.input))
            accepted, _saved = driver.roundtrip('accepted', tracked)
        self.assertEqual(revision.revision_headers(tracked['path']), before)
        self.assertEqual(revision.revision_headers(accepted['path']), {})
        self.assertEqual(calls, [('tracked', s) for s in revision.STAGES] + [('accepted', s) for s in revision.STAGES])
        self.assertFalse(driver.may_be_open)

    def test_unconfirmed_open_preserves_stage_and_pending_without_close_or_retry(self):
        job, ref = self.job(); plan, plan_ref = self.plan(job, ref)
        driver = self.driver(plan, plan_ref); driver.root.mkdir()
        revision.owned_path(plan, 'tracked').parent.mkdir()
        pending = self.f.user / '.cache/wordrev/word-automation.pending.json'
        driver.runtime = types.SimpleNamespace(mark_word_pending=lambda value: pending.write_text(json.dumps(value)))
        with patch.object(revision.subprocess, 'run', side_effect=subprocess.TimeoutExpired('private-native', 1)) as native_call:
            with self.assertRaises(subprocess.TimeoutExpired) as failure:
                driver.roundtrip('tracked', protocol.pin(self.input))
        driver.preserve_failure(failure.exception)
        self.assertEqual(native_call.call_count, 1)
        self.assertTrue(revision.owned_path(plan, 'tracked').is_file())
        self.assertTrue(pending.is_file())
        self.assertFalse(Path(plan['revision_outputs']['tracked']).exists())
        self.assertEqual(json.loads(pending.read_text())['retry_permitted'], False)

    def test_pdf_timeout_marks_only_owned_document_pending_without_retry(self):
        job, ref = self.job(); plan, plan_ref = self.plan(job, ref)
        driver = self.driver(plan, plan_ref); driver.root.mkdir()
        pending = self.f.user / '.cache/wordrev/word-automation.pending.json'
        driver.runtime = types.SimpleNamespace(mark_word_pending=lambda value: pending.write_text(json.dumps(value)))
        owned = self.stage_parent / 'private-pdf-stage' / 'owned.docx'
        exporter = unittest.mock.Mock(side_effect=subprocess.TimeoutExpired('private-pdf', 1))
        session = types.SimpleNamespace(owned=owned, export=exporter)
        service = types.SimpleNamespace(ExportSession=lambda *args, **kwargs: session)
        with self.assertRaises(subprocess.TimeoutExpired) as failure:
            driver.export_pdf(service, job['input'])
        driver.preserve_failure(failure.exception)
        self.assertEqual(exporter.call_count, 1)
        value = json.loads(pending.read_text())
        self.assertEqual(value['owned_document'], str(owned))
        self.assertFalse(value['retry_permitted'])
        self.assertFalse(Path(plan['output']).exists())


if __name__ == '__main__':
    unittest.main()
