"""Temporary DB/home, original pinned Python service, fake Word/validators.

Only owned Python fixture children may start. Native actor and application
identity are mocked. Real flock and Python file publication are exercised.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

import resource_broker as rb
import resource_execution as ex
import word_execution as word


def ref(path):
    path=Path(path).resolve()
    raw=path.read_bytes()
    return dict(path=str(path),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())


def identity(path):
    s=path.stat()
    return dict(path=str(path),dev=s.st_dev,ino=s.st_ino)


class WordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source=Path(rb.__file__).resolve().parent.parent
        cls.controller=dict(source=str(source),files=[dict(relative=str(p.relative_to(source)),
                            bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
                            for p in sorted((source/'scripts').glob('*.py'))])
        cls.original_popen=subprocess.Popen

    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve()
        self.assertEqual(word.OSASCRIPT,'/usr/bin/osascript')
        fixture=Path(__file__).resolve().parent.parent/'verification/fixtures/word-dev5'
        self.patch(patch.object(word,'SERVICE_ROOT',fixture))
        self.osascript=self.root/'osascript';self.osascript.write_text('fixture only');self.osascript.chmod(0o700)
        self.patch(patch.object(word,'OSASCRIPT',str(self.osascript)))
        self.patch(patch.object(Path,'home',return_value=self.root))
        for name in ['.cache/wordrev/word-automation.lock','.cmuxterm/locks/desktop-focus.lock']:
            p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('')
        self.word_lock=self.root/'.cache/wordrev/word-automation.lock'
        self.desktop_lock=self.root/'.cmuxterm/locks/desktop-focus.lock'
        self.b=rb.Broker(self.root/'db');self.addCleanup(self.b.db.close)
        self.b.configure('word-zotero',[self.word_lock,self.desktop_lock])
        self.input=self.root/'input.docx';self.input.write_bytes(b'fixture docx bytes')
        self.output=self.root/'output.pdf'
        self.pdfinfo=self.root/'pdfinfo';self.pdfinfo.write_text('fixture pdfinfo');self.pdfinfo.chmod(0o700)
        self.pdftotext=self.root/'pdftotext';self.pdftotext.write_text('fixture pdftotext');self.pdftotext.chmod(0o700)
        self.actor={k:str(uuid.uuid4()) for k in ['workspace_uuid','surface_uuid','pane_uuid','session_id']}
        self.plan=dict(schema=word.PLAN_SCHEMA,input=ref(self.input),output=str(self.output),output_directory=identity(self.root),
                       services={name:ref(word.SERVICE_ROOT/name) for name in word.SERVICE_PINS},
                       executables=dict(osascript=ref(self.osascript),pdfinfo=ref(self.pdfinfo),pdftotext=ref(self.pdftotext)),
                       locks=[identity(self.word_lock),identity(self.desktop_lock)],budget_seconds=120,
                       required_free_bytes=word.FLOOR)
        self.plan_path=self.root/'plan.json';self.plan_path.write_text(json.dumps(self.plan))
        self.contract=dict(schema=ex.WORD_SCHEMA,task_id='word-fixture',resource='word-zotero',actor=self.actor,
                           controller=self.controller,operation='owned_word_pdf_export',request_revision='a'*64,
                           attempt_id=str(uuid.uuid4()),cwd=str(self.root),environment={},word_plan=ref(self.plan_path),
                           inputs=[ref(self.input),ref(self.plan_path)],
                           transport_artifacts=list(self.plan['services'].values())+list(self.plan['executables'].values()))
        self.patch(patch.object(ex,'native_identity',return_value=self.actor))
        self.patch(patch.object(word,'app_identity',return_value=dict(pid=12345,birth=[1,2],executable='fixture-word')))
        self.patch(patch.object(word,'process_identity',side_effect=lambda contract,pid:
            dict(pid=pid,ppid=os.getpid() if pid!=os.getpid() else os.getppid(),birth=[1,pid],executable='fixture')))
        self.patch(patch.object(word.shutil,'disk_usage',return_value=shutil._ntuple_diskusage(2**40,0,2**40)))
        # This only supplies the original service's installation-presence check;
        # the actual runner maps both validators to their exact plan refs.
        self.patch(patch.object(word.shutil,'which',side_effect=lambda n:str(self.root/n)))
        self.prior_modules={name:sys.modules.pop(name,None) for name in ('word_runtime','word_pdf_service')}
        self.addCleanup(self.restore_modules)
        self.starts=[];self.stage_hook=None;self.children=[];self.stall_stage=None;self.fail_stage=None
        self.patch(patch.object(word.subprocess,'Popen',side_effect=self.fake_spawn))
        self.addCleanup(self.cleanup_children)

    def patch(self,p):
        value=p.start();self.addCleanup(p.stop);return value

    def restore_modules(self):
        for name,module in self.prior_modules.items():
            sys.modules.pop(name,None)
            if module is not None:sys.modules[name]=module

    def cleanup_children(self):
        for proc in self.children:
            if proc.poll() is None:
                proc.kill();proc.wait(timeout=3)
            for stream in (proc.stdout,proc.stderr):
                if stream:stream.close()

    def peer_lock_state(self):
        code='''import fcntl,sys
f=open(sys.argv[1],'a+b')
try: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);print('AVAILABLE')
except BlockingIOError:print('BLOCKED')
'''
        p=self.original_popen([sys.executable,'-B','-c',code,str(self.word_lock)],stdout=subprocess.PIPE,text=True)
        out,_=p.communicate(timeout=5);return out.strip()

    def fake_spawn(self,argv,**kwargs):
        # No supplied command is executed; only this fixed local Python fixture.
        self.assertTrue(argv[0] in [str(self.osascript),str(self.pdfinfo),str(self.pdftotext)])
        stage=Path(argv[1]).stem.upper() if argv[0]==str(self.osascript) else Path(argv[0]).name
        self.starts.append(stage)
        if self.stage_hook:self.stage_hook(stage,argv,kwargs)
        db=sqlite3.connect(self.b.root/'queue.sqlite3')
        try:
            self.assertEqual(db.execute('SELECT invocations FROM tickets').fetchone()[0],1)
            row=db.execute('SELECT state,intent FROM resource_execution_word_stages ORDER BY ordinal DESC LIMIT 1').fetchone()
            self.assertEqual(row[0],'UNKNOWN');self.assertEqual(json.loads(row[1])['argv'],argv)
        finally:db.close()
        if stage=='OPEN' and self.fail_stage=='OPEN_EXCEPTION':raise OSError('open result unknown')
        if stage=='EXPORT':
            body=Path(argv[1]).read_text()
            import re
            output=Path(json.loads(re.search(r'set outputFile to (".*")',body).group(1)))
            output.write_bytes(b'%PDF-fixture payload')
        stdout={'PROBE':'16, {}','PREFLIGHT':'READY','OPEN':'','RESOLVE_PATH':'owned',
                'EXPORT':'','pdfinfo':'Pages: 1\n','pdftotext':'fixture text\n','CLOSE':''}[stage]
        rc=1 if self.fail_stage==stage else 0
        code='import sys,time;time.sleep(float(sys.argv[1]));sys.stdout.write(sys.argv[2]);sys.exit(int(sys.argv[3]))'
        delay='1' if self.stall_stage==stage else '0'
        proc=self.original_popen([sys.executable,'-B','-c',code,delay,stdout,str(rc)],**kwargs)
        self.children.append(proc)
        if self.stall_stage==stage:
            proc.communicate=lambda **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired(argv,0.01))
        return proc

    def lease(self):
        ticket=self.b.request_enforced(self.contract)
        return self.b.grant_enforced(ticket['id'])

    def run_pdf(self,lease):
        return self.b.run_word_pdf_enforced(lease['id'],lease['token'],
            **{k:lease['execution'][k] for k in ('generation','operation','request_revision','attempt_id')})

    def proof(self,lease,result,**changes):
        observation=dict(task_id=self.contract['task_id'],documents=0,windows=0,modal=False,zotero_current_doc=False,
                         zotero_current_window=False,pending=False,execution=lease['execution'],
                         observed_at=time.time(),result_sha256=result['execution']['result_sha256'],
                         stage_digest=result['result']['stage_digest'])
        observation.update(changes)
        path=self.root/'observed.json';path.write_text(json.dumps(observation))
        return dict(pending=False,own_processes_empty=True,execution=result['execution'],observed_receipt=ref(path))

    def test_preflight_no_db_and_generic_paths_refuse_typed(self):
        with patch('sqlite3.connect',side_effect=AssertionError('no DB')):
            self.assertEqual(rb.Broker.validate_word_pdf_contract(self.contract),self.contract)
        lease=self.lease()
        with self.assertRaisesRegex(ex.ExecutionError,'WORD_TYPED_RUN_REQUIRED'):
            self.b.run_enforced(lease['id'],lease['token'],**{k:lease['execution'][k] for k in ('generation','operation','request_revision','attempt_id')})
        with self.assertRaisesRegex(ex.ExecutionError,'WORD_TYPED_RELEASE_REQUIRED'):
            # Native resource observation remains required before typed dispatch.
            path=self.root/'zero.json';path.write_text(json.dumps(dict(task_id='word-fixture',documents=0,windows=0,modal=False,zotero_current_doc=False,zotero_current_window=False,pending=False)))
            self.b.release_enforced(lease['id'],lease['token'],dict(pending=False,own_processes_empty=True,observed_receipt=ref(path),execution=lease['execution'],no_request_submitted=True),generation=1)
        self.assertEqual(self.starts,[])

    def test_plan_cannot_lower_s40_disk_floor_or_spoof_service(self):
        for field in ('floor','service'):
            plan=copy.deepcopy(self.plan)
            if field=='floor':plan['required_free_bytes']=40*1024**3-1
            else:plan['services']['word_runtime.py']['sha256']='0'*64
            self.plan_path.write_text(json.dumps(plan))
            contract=copy.deepcopy(self.contract)
            contract['word_plan']=ref(self.plan_path)
            contract['inputs'][1]=ref(self.plan_path)
            with self.assertRaises(ex.ExecutionError):rb.Broker.validate_word_pdf_contract(contract)
        self.assertEqual(self.starts,[])

    def test_live_capacity_below_40gib_refuses_before_intent_and_launch(self):
        lease=self.lease()
        with patch.object(word.shutil,'disk_usage',return_value=shutil._ntuple_diskusage(2**40,0,40*1024**3-1)):
            with self.assertRaisesRegex(ex.ExecutionError,'WORD_DISK_ADMISSION_CHANGED'):self.run_pdf(lease)
        self.assertEqual(self.starts,[])
        self.assertEqual(self.b.get(lease['id'])['invocations'],0)

    def new_broker_with_requirements(self,requirements):
        self.b=rb.Broker(self.root/('configured-db-'+uuid.uuid4().hex))
        self.addCleanup(self.b.db.close)
        self.b.configure('word-zotero',[self.word_lock,self.desktop_lock],release_requirements=requirements)

    def test_public_weak_configuration_refuses_before_intent_or_launch(self):
        minimum=word.MINIMUM_RELEASE_REQUIREMENTS
        cases=[{},[]]
        for key,value in minimum.items():
            missing=dict(minimum);missing.pop(key);cases.append(missing)
            cases.append(dict(minimum,**{key:1 if type(value) is int else True}))
            cases.append(dict(minimum,**{key:False if type(value) is int else 0}))
        for requirements in cases:
            with self.subTest(requirements=requirements):
                self.new_broker_with_requirements(requirements)
                lease=self.lease()
                with self.assertRaisesRegex(ex.ExecutionError,'WORD_MINIMUM_RELEASE_REQUIREMENTS_REQUIRED'):
                    self.run_pdf(lease)
                self.assertEqual(self.starts,[])
                self.assertEqual(self.b.get(lease['id'])['invocations'],0)
                self.assertIsNone(self.b.inspect_word_pdf_enforced(lease['id'])['attempt'])

    def test_pre_fix_completed_weak_configuration_cannot_release(self):
        self.new_broker_with_requirements({})
        lease=self.lease()
        # Simulate the previously accepted candidate through its public API;
        # only the new admission guard is disabled to create that old state.
        with patch.object(word,'check_release_requirements'):
            result=self.run_pdf(lease)
        self.assertEqual(self.starts,list(word.STAGES))
        proof=self.proof(lease,result,documents=1,windows=1)
        with self.assertRaisesRegex(ex.ExecutionError,'WORD_MINIMUM_RELEASE_REQUIREMENTS_REQUIRED'):
            self.b.release_word_pdf_enforced(lease['id'],lease['token'],proof,generation=1)
        self.assertEqual(self.b.get(lease['id'])['status'],'ACTIVE')
        self.assertIsNone(self.b.get(lease['id'])['release_proof'])

    def test_additional_requirements_still_enforced_and_mapping_drift_refused(self):
        self.new_broker_with_requirements(dict(word.MINIMUM_RELEASE_REQUIREMENTS,extra_drain=True))
        lease=self.lease()
        with self.assertRaisesRegex(rb.ResourceBusy,'RESOURCE_LOCK_MAPPING_IS_PINNED'):
            self.b.configure('word-zotero',[self.word_lock,self.desktop_lock],release_requirements={})
        result=self.run_pdf(lease)
        with self.assertRaisesRegex(rb.ResourceBusy,'NATIVE_RELEASE_CONDITION_FAILED: extra_drain'):
            self.b.release_word_pdf_enforced(lease['id'],lease['token'],self.proof(lease,result),generation=1)
        released=self.b.release_word_pdf_enforced(lease['id'],lease['token'],
                    self.proof(lease,result,extra_drain=True),generation=1)
        self.assertEqual(released['status'],'RELEASED')

    def test_complete_actual_service_and_real_cross_process_locks(self):
        lease=self.lease();observations=[]
        def hook(stage,argv,kwargs):
            if stage in ('PROBE','OPEN','CLOSE'):
                observations.append(self.peer_lock_state())
            self.assertEqual(kwargs['env'],{})
            self.assertEqual(kwargs['cwd'],str(self.root))
        self.stage_hook=hook
        result=self.run_pdf(lease)
        self.assertEqual(self.starts,list(word.STAGES))
        self.assertEqual(observations,['BLOCKED']*3)
        self.assertEqual(self.peer_lock_state(),'AVAILABLE')
        self.assertTrue(self.output.exists());self.assertFalse(self.root.joinpath('.cache/wordrev/word-automation.pending.json').exists())
        state=self.b.inspect_word_pdf_enforced(lease['id'])
        self.assertEqual(state['attempt']['state'],'TERMINAL');self.assertEqual(len(state['stages']),8)
        self.assertEqual(state['ticket']['invocations'],1)
        self.assertEqual({json.loads(s['intent'])['parent']['pid'] for s in state['stages']},{os.getpid()})
        self.assertEqual({json.loads(s['intent'])['parent']['thread_id'] for s in state['stages']},{threading.get_ident()})
        proof=self.proof(lease,result)
        released=self.b.release_word_pdf_enforced(lease['id'],lease['token'],proof,generation=1)
        self.assertEqual(released['status'],'RELEASED')

    def test_pending_before_start_zero_launch_and_original_bytes_retained(self):
        lease=self.lease();pending=self.root/'.cache/wordrev/word-automation.pending.json';pending.write_bytes(b'original pending')
        with self.assertRaisesRegex(ex.ExecutionError,'WORD_PENDING_PRESERVED'):self.run_pdf(lease)
        self.assertEqual(self.starts,[]);self.assertEqual(pending.read_bytes(),b'original pending')
        self.assertEqual(self.b.get(lease['id'])['invocations'],0)

    def test_broken_pending_symlink_refuses_without_start_or_overwrite(self):
        lease=self.lease();pending=self.root/'.cache/wordrev/word-automation.pending.json'
        pending.symlink_to(self.root/'missing-pending-original')
        with self.assertRaisesRegex(ex.ExecutionError,'WORD_PENDING_PRESERVED'):self.run_pdf(lease)
        self.assertTrue(pending.is_symlink());self.assertEqual(self.starts,[])
        self.assertEqual(self.b.get(lease['id'])['invocations'],0)

    def test_pending_after_open_stops_next_native_and_preserves_foreign_bytes(self):
        lease=self.lease();pending=self.root/'.cache/wordrev/word-automation.pending.json'
        def hook(stage,*args):
            if stage=='OPEN':pending.write_bytes(b'new original pending')
        self.stage_hook=hook
        with self.assertRaisesRegex(ex.ExecutionError,'UNKNOWN_INSPECT_ONLY'):self.run_pdf(lease)
        self.assertEqual(self.starts,['PROBE','PREFLIGHT','OPEN'])
        self.assertEqual(pending.read_bytes(),b'new original pending')
        self.assertEqual(self.b.inspect_word_pdf_enforced(lease['id'])['attempt']['state'],'UNKNOWN')

    def test_pending_arriving_between_runner_prechecks_keeps_original_bytes(self):
        lease=self.lease();pending=self.root/'.cache/wordrev/word-automation.pending.json'
        foreign=b'original foreign pending must survive'
        fresh=word.WordExecution.fresh
        injected=[]
        def arriving(driver,*,stage=False):
            if stage and driver.next_stage==3 and not injected:
                injected.append(True);pending.write_bytes(foreign)
            return fresh(driver,stage=stage)
        with patch.object(word.WordExecution,'fresh',arriving):
            with self.assertRaisesRegex(ex.ExecutionError,'UNKNOWN_INSPECT_ONLY'):self.run_pdf(lease)
        self.assertTrue(injected)
        self.assertEqual(self.starts,['PROBE','PREFLIGHT','OPEN'])
        self.assertEqual(pending.read_bytes(),foreign)
        state=self.b.inspect_word_pdf_enforced(lease['id'])
        self.assertEqual(state['attempt']['state'],'UNKNOWN')
        self.assertEqual(state['ticket']['status'],'ACTIVE')
        self.assertTrue(all(proc.poll() is not None for proc in self.children))
        self.assertEqual(self.peer_lock_state(),'AVAILABLE')

    def test_open_spawn_exception_and_timeout_are_pending_without_replay(self):
        lease=self.lease();self.fail_stage='OPEN_EXCEPTION'
        with self.assertRaisesRegex(ex.ExecutionError,'UNKNOWN_INSPECT_ONLY'):self.run_pdf(lease)
        state=self.b.inspect_word_pdf_enforced(lease['id'])
        self.assertEqual(state['stages'][-1]['stage'],'OPEN');self.assertEqual(state['stages'][-1]['state'],'UNKNOWN')
        self.assertIsNone(state['stages'][-1]['pgid'])
        self.assertTrue((self.root/'.cache/wordrev/word-automation.pending.json').exists())
        before=list(self.starts)
        with self.assertRaises((RuntimeError,ValueError)):self.run_pdf(lease)
        self.assertEqual(self.starts,before)
        with self.assertRaises((RuntimeError,ValueError)):
            self.b.release_word_pdf_enforced(lease['id'],lease['token'],dict(pending=False,own_processes_empty=True),generation=1,reconcile=True)

    def test_open_timeout_retains_owned_pgid_and_unknown(self):
        lease=self.lease();self.stall_stage='OPEN'
        with self.assertRaisesRegex(ex.ExecutionError,'UNKNOWN_INSPECT_ONLY'):self.run_pdf(lease)
        state=self.b.inspect_word_pdf_enforced(lease['id'])
        self.assertEqual(self.starts,['PROBE','PREFLIGHT','OPEN'])
        self.assertEqual(state['stages'][-1]['state'],'UNKNOWN')
        self.assertIsNotNone(state['stages'][-1]['pgid'])
        self.assertIsNotNone(state['stages'][-1]['process'])
        self.assertTrue((self.root/'.cache/wordrev/word-automation.pending.json').exists())

    def test_stage_group_remaining_prevents_next_stage_and_release(self):
        lease=self.lease()
        real=rb.group_alive
        def alive(pgid):
            if len(self.starts)>=3 and pgid==self.children[-1].pid:return True
            return real(pgid)
        with patch.object(rb,'group_alive',side_effect=alive):
            with self.assertRaisesRegex(ex.ExecutionError,'UNKNOWN_INSPECT_ONLY'):self.run_pdf(lease)
        self.assertEqual(self.starts,['PROBE','PREFLIGHT','OPEN'])
        state=self.b.inspect_word_pdf_enforced(lease['id'])
        self.assertEqual(state['stages'][-1]['state'],'UNKNOWN')
        self.assertEqual(state['ticket']['status'],'ACTIVE')

    def test_exact_script_tamper_before_open_has_zero_open_launch(self):
        lease=self.lease();fresh=word.WordExecution.fresh
        def tamper(driver,*,stage=False):
            fresh(driver,stage=stage)
            if stage and driver.next_stage==2:
                (driver.session.evidence/'open.applescript').write_text('unapproved script')
        with patch.object(word.WordExecution,'fresh',tamper):
            with self.assertRaisesRegex(ex.ExecutionError,'UNKNOWN_INSPECT_ONLY'):self.run_pdf(lease)
        self.assertEqual(self.starts,['PROBE','PREFLIGHT'])

    def test_native_checks_never_hold_write_transaction(self):
        lease=self.lease();observed=[]
        def native(_):
            observed.append(self.b.db.in_transaction)
            self.assertFalse(self.b.db.in_transaction)
            peer=sqlite3.connect(self.b.root/'queue.sqlite3',timeout=0,isolation_level=None)
            try:peer.execute('BEGIN IMMEDIATE');peer.execute('ROLLBACK')
            finally:peer.close()
            return self.actor
        with patch.object(ex,'native_identity',side_effect=native):self.run_pdf(lease)
        self.assertTrue(observed);self.assertFalse(any(observed))

    def test_inspect_unknown_never_calls_native_or_spawns_and_no_unknown_release(self):
        lease=self.lease()
        # Fixed-service constructor can fail before any application submission;
        # aggregate intent is nevertheless durable and not automatically reset.
        runtime=word.external_module('word_runtime',self.plan['services']['word_runtime.py'])
        service=word.external_module('word_pdf_service',self.plan['services']['word_pdf_service.py'])
        with patch.object(service,'ExportSession',side_effect=OSError('fixture constructor failure')):
            with self.assertRaisesRegex(ex.ExecutionError,'UNKNOWN_INSPECT_ONLY'):self.run_pdf(lease)
        with patch.object(ex,'native_identity',side_effect=AssertionError('inspect must not probe')):
            state=self.b.inspect_word_pdf_enforced(lease['id'])
        self.assertEqual(state['attempt']['state'],'UNKNOWN');self.assertEqual(self.starts,[])
        observation=dict(task_id='word-fixture',documents=0,windows=0,modal=False,zotero_current_doc=False,zotero_current_window=False,pending=False)
        path=self.root/'observation.json';path.write_text(json.dumps(observation))
        proof=dict(pending=False,own_processes_empty=True,execution=lease['execution'],observed_receipt=ref(path))
        with self.assertRaisesRegex(ex.ExecutionError,'WORD_UNKNOWN_RELEASE_UNSUPPORTED_INSPECT_ONLY'):
            self.b.release_word_pdf_enforced(lease['id'],lease['token'],proof,generation=1,reconcile=True)

    def test_close_failure_is_not_complete_even_if_pdf_published(self):
        lease=self.lease();self.fail_stage='CLOSE'
        with self.assertRaisesRegex(ex.ExecutionError,'UNKNOWN_INSPECT_ONLY'):self.run_pdf(lease)
        self.assertTrue(self.output.exists())
        state=self.b.inspect_word_pdf_enforced(lease['id']);self.assertEqual(state['attempt']['state'],'UNKNOWN')
        service=list(self.root.glob('output.word-run-*/result.json'))[0]
        raw=json.loads(service.read_text());self.assertTrue(raw['success']);self.assertEqual(raw['cleanup'],'PENDING')

    def test_native_and_input_drift_before_stage_zero_next_launch(self):
        lease=self.lease()
        def hook(stage,*args):
            if stage=='PREFLIGHT':self.input.write_bytes(b'changed original input')
        self.stage_hook=hook
        with self.assertRaisesRegex(ex.ExecutionError,'UNKNOWN_INSPECT_ONLY'):self.run_pdf(lease)
        self.assertEqual(self.starts,['PROBE','PREFLIGHT'])

    def test_global_nonzero_wrong_attempt_stale_observation_and_actual_pending_refuse_release(self):
        lease=self.lease();result=self.run_pdf(lease)
        for changes,pattern in [({'documents':1},'NATIVE_RELEASE_CONDITION_FAILED'),
                                ({'execution':dict(lease['execution'],attempt_id='wrong')},'FRESH_WORD_GLOBAL'),
                                ({'observed_at':time.time()-60},'FRESH_WORD_GLOBAL')]:
            proof=self.proof(lease,result,**changes)
            with self.assertRaisesRegex((rb.ResourceBusy,ex.ExecutionError),pattern):
                self.b.release_word_pdf_enforced(lease['id'],lease['token'],proof,generation=1)
        proof=self.proof(lease,result)
        pending=self.root/'.cache/wordrev/word-automation.pending.json';pending.write_text('actual pending')
        with self.assertRaisesRegex(ex.ExecutionError,'WORD_PENDING_PRESERVED'):
            self.b.release_word_pdf_enforced(lease['id'],lease['token'],proof,generation=1)
        self.assertEqual(self.b.get(lease['id'])['status'],'ACTIVE')

    def test_wrong_actor_foreign_service_and_generation_never_launch(self):
        lease=self.lease()
        with patch.object(ex,'native_identity',return_value={}):
            with self.assertRaisesRegex(ex.ExecutionError,'NATIVE_EXECUTION_ACTOR_MISMATCH'):self.run_pdf(lease)
        changed=copy.deepcopy(lease);changed['execution']['generation']=2
        with self.assertRaisesRegex(ex.ExecutionError,'GENERATION_MISMATCH'):self.run_pdf(changed)
        with patch.dict(sys.modules,{'word_runtime':type('Bad',(),{'__file__':'/foreign'})()}):
            with self.assertRaisesRegex(ex.ExecutionError,'FOREIGN_OR_CHANGED_WORD_SERVICE_MODULE'):self.run_pdf(lease)
        self.assertEqual(self.starts,[])


if __name__=='__main__':unittest.main()
