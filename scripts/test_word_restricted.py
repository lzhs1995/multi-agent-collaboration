"""Real isolated queue/guard controls; no live applications or shared state."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import tempfile
import types
import unittest
from unittest.mock import patch
import uuid

from resource_broker import Broker, ResourceBusy, os_lock
import word_restricted_broker as protocol
import word_restricted_pdf as native
import word_restricted_inventory as inventory


class RestrictedWordTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        self.user=self.root/'user'; self.user.mkdir()
        home=patch.object(Path,'home',return_value=self.user);home.start();self.addCleanup(home.stop)
        groups=patch.object(protocol,'group_alive',return_value=False);groups.start();self.addCleanup(groups.stop)
        self.admin=self.actor();self.successor=self.actor();self.owner_session=str(uuid.uuid4())
        who=patch.object(protocol,'native_identity',return_value=self.admin);self.identity=who.start();self.addCleanup(who.stop)
        self.lock_paths=[self.user/'.cache/wordrev/word-automation.lock',self.user/'.cmuxterm/locks/desktop-focus.lock']
        for path in self.lock_paths:path.parent.mkdir(parents=True,exist_ok=True);path.touch()
        self.legacy=Broker(self.root);self.addCleanup(self.legacy.db.close)
        self.legacy.configure('word-zotero',[self.lock_paths[0]])
        original=self.legacy.request('word-zotero','original-owned-word',str(uuid.uuid4()),str(uuid.uuid4()))
        self.legacy.grant(original['id'])
        self.legacy.db.execute('UPDATE tickets SET expires=1,invocations=2 WHERE id=?',(original['id'],))
        self.original=self.legacy.get(original['id'])
        self.resource=dict(self.legacy.db.execute("SELECT * FROM resources WHERE name='word-zotero'").fetchone())
        self.successor['workspace_uuid']=self.original['workspace_uuid']
        self.admin=dict(self.successor);self.identity.return_value=self.admin
        plan_record=dict(type='response_item',payload=dict(type='message',role='assistant',content=[dict(type='output_text',text=self.owner_session+' '+self.original['workspace_uuid']+'：核收旧 Word 资源票后继任；不保存或关闭归属不明的“文件1”。')]))
        approval=dict(type='response_item',payload=dict(type='message',role='user',content=[dict(type='input_text',text='Implement the plan.')]))
        path=self.root/('rollout-'+self.successor['session_id']+'.jsonl')
        a=(json.dumps(plan_record,ensure_ascii=False)+'\n').encode();b=(json.dumps(approval)+'\n').encode();path.write_bytes(a+b)
        identity=[path.stat().st_dev,path.stat().st_ino]
        loc=lambda raw,offset:dict(path=str(path),offset=offset,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),identity=identity)
        old_input=self.root/'accepted.docx';old_input.write_bytes(b'accepted docx')
        old_pdf=self.root/'accepted.pdf';old_pdf.write_bytes(b'%PDF-accepted')
        terminals=[self.write('terminal'+str(i)+'.json',dict(process_group_empty=True,pgid=100000000+i,exit_code=1)) for i in range(2)]
        service=self.write('accepted-service.json',dict(success=True,cleanup='PASS',input_unchanged=True,staged_docx_unchanged=True,staging_dir=str(self.root/'old-removed-stage'),
            input=str(old_input),input_sha256=protocol.pin(old_input)['sha256'],pdfPath=str(old_pdf),pdf_sha256=protocol.pin(old_pdf)['sha256']))
        self.contract=dict(schema='word-restricted-disposition-v1',scope='OWNED_PDF_ONLY',original_ticket=self.original['id'],
            original_row_sha256=protocol.digest(self.original),original_resource_sha256=protocol.digest(self.resource),
            original_owner=dict(workspace_uuid=self.original['workspace_uuid'],surface_uuid=self.original['surface_uuid'],session_id=self.owner_session),
            original_owner_session_id=self.owner_session,administrator=self.admin,successor=self.successor,
            approval=loc(b,len(a)),approved_plan=loc(a,0),terminal_receipts=terminals,
            accepted_export=service,accepted_export_input=protocol.pin(old_input),accepted_export_pdf=protocol.pin(old_pdf))
        self.contract_ref=self.write('disposition.json',self.contract)
        self.broker=protocol.RestrictedWordBroker(self.root);self.addCleanup(self.broker.close)
        service_dir=self.root/'service';service_dir.mkdir()
        service_refs={}
        for name in protocol.SERVICE_PINS:
            p=service_dir/name;p.write_text('fixture = True\n');service_refs[name]=protocol.pin(p)
        p=patch.dict(protocol.SERVICE_PINS,{k:r['sha256'] for k,r in service_refs.items()},clear=True);p.start();self.addCleanup(p.stop)
        self.services=service_refs
        self.validators={}
        for name in protocol.VALIDATOR_PINS:
            p=self.root/name;p.write_text('#!/bin/sh\nexit 0\n');p.chmod(0o700);self.validators[name]=protocol.pin(p)
        p=patch.dict(protocol.VALIDATOR_PINS,{k:r['sha256'] for k,r in self.validators.items()},clear=True);p.start();self.addCleanup(p.stop)

    def actor(self):
        return dict(workspace_uuid=str(uuid.uuid4()),surface_uuid=str(uuid.uuid4()),session_id=str(uuid.uuid4()))

    def write(self,name,value):
        p=self.root/name;p.write_text(json.dumps(value,ensure_ascii=False)+'\n');return protocol.pin(p)

    def apply(self):
        preview=self.broker.preview(self.contract_ref)
        return self.broker.apply(self.contract_ref,preview['plan_sha256'])['disposition_id']

    def plan(self,name):
        source=Path(protocol.__file__).resolve().parent.parent
        p=self.root/(name+'.docx');p.write_bytes(b'new source')
        locks={str(p):[p.stat().st_dev,p.stat().st_ino] for p in self.lock_paths}
        value=dict(operation='OWNED_PDF_ONLY',actor=self.successor,controller_source=str(source),
            controller_pins={p.name:protocol.pin(p) for p in (source/'scripts').glob('*.py')},
            service_pins=self.services,validator_pins=self.validators,input=protocol.pin(p),output=str(self.root/(name+'.pdf')),
            evidence_root=str(self.root/(name+'-evidence')),required_free_bytes=protocol.FLOOR,locks=locks)
        return value,self.write(name+'-plan.json',value)

    def request(self,disposition,name='new'):
        self.identity.return_value=self.successor
        value,ref=self.plan(name)
        return self.broker.request(disposition,ref),value,ref

    def test_readonly_preview_has_no_schema_or_legacy_mutation(self):
        before=list(self.legacy.db.iterdump())
        preview=self.broker.preview(self.contract_ref)
        self.assertEqual(preview['input_operations'],0)
        self.assertEqual(list(self.legacy.db.iterdump()),before)

    def test_apply_is_once_and_preserves_every_legacy_column(self):
        self.apply()
        self.assertEqual(self.legacy.get(self.original['id']),self.original)
        self.assertEqual(dict(self.legacy.db.execute("SELECT * FROM resources WHERE name='word-zotero'").fetchone()),self.resource)
        with self.assertRaisesRegex(ResourceBusy,'EXISTS_NO_REPLAY'):self.apply()

    def test_wrong_approval_and_plan_order_refused_before_schema(self):
        for field,value in (('original_owner_session_id',str(uuid.uuid4())),('scope','CITATION_REFRESH')):
            c={**self.contract,field:value};ref=self.write('bad-contract.json',c)
            with self.assertRaises(ResourceBusy):self.broker.preview(ref)
        c=copy.deepcopy(self.contract);c['approval']['offset']=0
        with self.assertRaises(ResourceBusy):self.broker.preview(self.write('bad-order.json',c))
        self.assertIsNone(self.legacy.db.execute("SELECT name FROM sqlite_master WHERE name='word_restricted_dispositions'").fetchone())

    def test_wrong_admin_and_changed_legacy_preimage_refused(self):
        self.identity.return_value=self.actor()
        with self.assertRaisesRegex(ResourceBusy,'ADMINISTRATOR'):self.apply()
        self.identity.return_value=self.admin
        self.legacy.db.execute('UPDATE tickets SET invocations=3 WHERE id=?',(self.original['id'],))
        with self.assertRaisesRegex(ResourceBusy,'PREIMAGE_CHANGED'):self.broker.preview(self.contract_ref)

    def test_group_or_word_pending_preserved_and_refused(self):
        with patch.object(protocol,'group_alive',return_value=True):
            with self.assertRaisesRegex(ResourceBusy,'GROUP_ALIVE'):self.broker.preview(self.contract_ref)
        pending=self.user/'.cache/wordrev/word-automation.pending.json';pending.write_text('{"original":"keep"}')
        with self.assertRaisesRegex(ResourceBusy,'PENDING_PRESERVED'):self.broker.preview(self.contract_ref)
        self.assertEqual(pending.read_text(),'{"original":"keep"}')

    def test_new_queue_never_unblocks_generic_broker(self):
        disposition=self.apply();ticket,_plan,_ref=self.request(disposition)
        self.broker.grant(ticket['id'])
        with self.assertRaisesRegex(ResourceBusy,'EXPIRED_LEASE'):self.legacy.grant(self.original['id'])
        with self.assertRaisesRegex(ValueError,'TICKET_NOT_FOUND'):self.legacy.get(ticket['id'])
        self.assertEqual(self.legacy.get(self.original['id']),self.original)

    def test_fifo_and_real_external_lock_are_enforced(self):
        disposition=self.apply();a,_p,_r=self.request(disposition,'a');b,_p,_r=self.request(disposition,'b')
        with self.assertRaisesRegex(ResourceBusy,'FIFO'):self.broker.grant(b['id'])
        with os_lock(self.lock_paths[1]):
            with self.assertRaisesRegex(ResourceBusy,'OS_LOCK_BUSY'):self.broker.grant(a['id'])
        self.assertEqual(self.broker.get(a['id'])['status'],'WAITING')
        self.assertEqual(self.broker.grant(a['id'])['status'],'ACTIVE')

    def test_scope_service_validator_closure_and_floor_cannot_be_relaxed(self):
        disposition=self.apply();self.identity.return_value=self.successor
        for change in ('scope','service','validator','closure','floor','input'):
            value,_ref=self.plan('bad-'+change)
            if change=='scope':value['operation']='REFRESH'
            elif change=='service':value['service_pins']['word_pdf_service.py']={**value['service_pins']['word_pdf_service.py'],'sha256':'0'*64}
            elif change=='validator':value['validator_pins']['pdfinfo']={**value['validator_pins']['pdfinfo'],'sha256':'0'*64}
            elif change=='closure':value['controller_pins'].pop('cmux_workspace_guard.py')
            elif change=='floor':value['required_free_bytes']=10
            else:Path(value['input']['path']).write_bytes(b'drift')
            with self.subTest(change=change),self.assertRaises(ResourceBusy):
                self.broker.request(disposition,self.write('changed-'+change+'.json',value))

    def test_wrong_successor_and_old_ticket_reuse_are_refused(self):
        disposition=self.apply();ticket,_p,_r=self.request(disposition)
        self.identity.return_value=self.actor()
        with self.assertRaisesRegex(ResourceBusy,'SUCCESSOR'):self.broker.grant(ticket['id'])
        with self.assertRaisesRegex(ResourceBusy,'RESTRICTED_TICKET_REQUIRED'):self.broker.get(self.original['id'])

    def test_arbitrary_argv_is_unrepresentable_and_spawn_failure_is_not_retried(self):
        disposition=self.apply();ticket,_p,_r=self.request(disposition);grant=self.broker.grant(ticket['id'])
        with self.assertRaises(TypeError):self.broker.run(ticket['id'],grant['token'],['anything'])
        self.assertEqual(self.broker.get(ticket['id'])['invocations'],0)
        with patch.object(protocol.subprocess,'Popen',side_effect=OSError('fixture spawn failed')) as popen:
            result=self.broker.run(ticket['id'],grant['token'])
        self.assertEqual(result['status'],'PENDING');self.assertEqual(popen.call_count,1)
        self.assertEqual(popen.call_args.args[0][1],'-BE')
        self.assertEqual(Path(popen.call_args.args[0][2]).name,'word_restricted_pdf.py')
        self.assertEqual(self.broker.get(ticket['id'])['invocations'],1)
        with self.assertRaises(ResourceBusy):self.broker.run(ticket['id'],grant['token'])
        b,_p,_r=self.request(disposition,'later')
        with self.assertRaisesRegex(ResourceBusy,'WRITER_BUSY'):self.broker.grant(b['id'])
        self.assertEqual(self.legacy.get(self.original['id']),self.original)

    def complete_child(self,ticket,plan,ref,*,wrong_input=False):
        outer=self
        class CompletedChild:
            pid=100000500
            returncode=0
            def __init__(self,argv,**kwargs):
                outer.assertEqual(argv[1],'-BE')
                outer.assertEqual(Path(argv[2]).name,'word_restricted_pdf.py')
                row=outer.broker.get(ticket['id'])
                root=Path(plan['evidence_root']);root.mkdir()
                output=Path(plan['output']);output.write_bytes(b'%PDF-fixture-child')
                report=dict(ticket=ticket['id'],nonce=row['nonce'],plan=ref,owned_cleanup=True,
                    foreign_observation_preserved=True,pending=False,input=plan['input'],pdf=protocol.pin(output))
                if wrong_input:report['input']={**plan['input'],'sha256':'0'*64}
                (root/'restricted-result.json').write_text(json.dumps(report))
            def communicate(self,**kwargs):return b'child output',b'child diagnostic'
        return CompletedChild

    def test_completed_owned_scope_admits_next_fifo_without_releasing_legacy(self):
        disposition=self.apply();ticket,plan,ref=self.request(disposition);grant=self.broker.grant(ticket['id'])
        with patch.object(protocol.subprocess,'Popen',self.complete_child(ticket,plan,ref)):
            result=self.broker.run(ticket['id'],grant['token'])
        self.assertEqual(result['status'],'COMPLETE_WITH_FOREIGN_UNKNOWN')
        self.assertFalse(result['legacy_released']);self.assertFalse(result['documents_verified'])
        self.assertEqual(result['result']['stderr'],'child diagnostic')
        later,_p,_r=self.request(disposition,'next')
        self.assertEqual(self.broker.grant(later['id'])['status'],'ACTIVE')
        self.assertEqual(self.legacy.get(self.original['id']),self.original)

    def test_child_report_input_drift_preserves_pending_and_blocks_next(self):
        disposition=self.apply();ticket,plan,ref=self.request(disposition);grant=self.broker.grant(ticket['id'])
        with patch.object(protocol.subprocess,'Popen',self.complete_child(ticket,plan,ref,wrong_input=True)):
            result=self.broker.run(ticket['id'],grant['token'])
        self.assertEqual(result['status'],'PENDING')
        self.assertEqual(self.broker.get(ticket['id'])['invocations'],1)
        with self.assertRaises(ResourceBusy):self.broker.run(ticket['id'],grant['token'])

    def session(self):
        run=uuid.uuid4().hex;stage=self.user/'Library/Containers/com.microsoft.Word/Data/Documents/wordrev-pdf'/run
        return types.SimpleNamespace(run_id=run,staging=stage,owned=stage/('wordrev_'+run[:12]+'.docx'),temporary=stage/'export.pdf',evidence=self.root/'stage-evidence')

    def test_all_native_templates_bind_the_unique_owned_path_and_reject_injection(self):
        session=self.session();session.evidence.mkdir()
        for stage in ('PROBE','PREFLIGHT','OPEN','RESOLVE_PATH','EXPORT','CLOSE'):
            path=session.evidence/(stage.lower()+'.applescript');raw=native.expected_script(session,stage);path.write_bytes(raw)
            native.validate_stage(session,stage,path)
            if stage in ('OPEN','RESOLVE_PATH','EXPORT','CLOSE'):self.assertIn(str(session.owned).encode(),raw)
            for extra in (b'\nactivate\n',b'\nrun VB macro macro name "ZoteroRefresh"\n',b'\nclose document "foreign" saving yes\n'):
                path.write_bytes(raw+extra)
                with self.assertRaisesRegex(RuntimeError,'EXACT_OWNED'):native.validate_stage(session,stage,path)
            path.write_bytes(raw)
        with self.assertRaisesRegex(RuntimeError,'UNKNOWN_RESTRICTED'):native.expected_script(session,'REFRESH')

    def test_foreign_path_and_nonunique_staging_are_refused(self):
        session=self.session();session.evidence.mkdir();path=session.evidence/'export.applescript'
        path.write_bytes(native.expected_script(session,'EXPORT').replace(str(session.owned).encode(),b'/foreign.docx'))
        with self.assertRaisesRegex(RuntimeError,'EXACT_OWNED'):native.validate_stage(session,'EXPORT',path)
        session.owned=self.root/'foreign.docx'
        with self.assertRaisesRegex(RuntimeError,'OWNED_STAGING'):native.validate_stage(session,'EXPORT',path)

    def metadata(self):
        return 'WORD_RESTRICTED_INVENTORY_V1\t1\t1\nDOC\t1\tMISSING\tMISSING\tUNKNOWN\nWIN\t1\tMISSING\t文件1\ttrue\tminimized\nACTIVE\tMISSING\nAXAPP\tMicrosoft Word\t1\nAXAPP\tZotero\t0\nAXWIN\tMicrosoft Word\t文件1\tAXWindow\tAXDialog\tfalse\ttrue\t0\nEND'

    def test_missing_foreign_metadata_stays_unknown_and_modals_fail(self):
        value=inventory.parse(self.metadata());self.assertEqual(value['documents'][0][-1],'UNKNOWN')
        self.assertEqual(len(value['documents']),1)
        for text in (self.metadata().replace('AXDialog\tfalse','AXDialog\ttrue'),self.metadata().replace('\ttrue\t0\nEND','\ttrue\t1\nEND'),self.metadata().replace('V1\t1\t1','V1\t0\t1'),self.metadata().replace('UNKNOWN','yes'),self.metadata()[:-4]):
            with self.subTest(text=text[-80:]),self.assertRaises((ResourceBusy,ValueError)):inventory.parse(text)

    def test_external_source_load_ignores_real_unchecked_pyc(self):
        p=self.root/'fixture_source.py';p.write_text('answer = 9\n')
        py_compile.compile(str(p),invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
        p.write_text('answer = 1\n')
        module=native.load_external('word_restricted_test_fixture',protocol.pin(p))
        self.assertEqual(module.answer,1)


if __name__=='__main__':unittest.main()
