"""Fixed broker child: export one new owned staging document, preserve foreign state."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import types


def checked_bytes(ref):
    p = Path(ref['path'])
    if not p.is_absolute() or p.resolve(strict=True) != p or p.is_symlink():
        raise RuntimeError('BOOTSTRAP_CANONICAL_PIN_REQUIRED')
    before = p.stat()
    raw = p.read_bytes()
    after = p.stat()
    if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns) != (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
        raise RuntimeError('BOOTSTRAP_PIN_IDENTITY_CHANGED')
    if len(raw) != ref['bytes'] or hashlib.sha256(raw).hexdigest() != ref['sha256']:
        raise RuntimeError('BOOTSTRAP_PIN_CHANGED')
    return raw


class PinnedSourceFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Compile checked source bytes; never consult an unchecked pyc cache."""
    def __init__(self, refs):
        self.refs = {Path(ref['path']).stem: ref for ref in refs.values()}

    def find_spec(self, fullname, path=None, target=None):
        if fullname in self.refs:
            return importlib.util.spec_from_loader(fullname,self,origin=self.refs[fullname]['path'])
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        ref = self.refs[module.__name__]
        module.__file__ = ref['path']
        exec(compile(checked_bytes(ref),ref['path'],'exec'),module.__dict__)


def load_external(name, ref):
    module = types.ModuleType(name)
    module.__file__ = ref['path']
    sys.modules[name] = module
    exec(compile(checked_bytes(ref),ref['path'],'exec'),module.__dict__)
    return module


def bootstrap(args):
    if not sys.flags.ignore_environment or not sys.dont_write_bytecode:
        raise RuntimeError('RESTRICTED_CHILD_REQUIRES_BE')
    root = Path(args.root).resolve(strict=True)
    db = sqlite3.connect((root/'queue.sqlite3').as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    try:
        deadline=time.monotonic()+3
        while True:
            value=db.execute('SELECT * FROM word_restricted_tickets WHERE id=?',(args.ticket,)).fetchone()
            if value is None:
                raise RuntimeError('ORIGINAL_RESTRICTED_TICKET_REQUIRED')
            row=dict(value)
            if row['pgid']==os.getpid(): break
            if time.monotonic()>=deadline: raise RuntimeError('ORIGINAL_CHILD_REGISTRATION_UNAVAILABLE')
            time.sleep(.025)
        if not (row['status']=='ACTIVE' and row['token']==args.token and row['nonce']==args.nonce
                and row['invocations']==1 and os.getpid()==os.getpgrp() and row['parent_pid']==os.getppid()):
            raise RuntimeError('ORIGINAL_BROKER_CHILD_REQUIRED')
        plan_ref=json.loads(row['plan'])
        plan=json.loads(checked_bytes(plan_ref).decode('utf-8'))
    finally:
        db.close()
    refs=plan['controller_pins']
    if Path(refs['word_restricted_pdf.py']['path'])!=Path(__file__).resolve():
        raise RuntimeError('ORIGINAL_RESTRICTED_ENTRYPOINT_REQUIRED')
    for ref in refs.values(): checked_bytes(ref)
    # This process has imported only stdlib modules before installing the
    # complete reviewed source loader. Environment prefixes are ignored by -E.
    sys.meta_path.insert(0,PinnedSourceFinder(refs))
    import word_restricted_broker as protocol
    broker=protocol.RestrictedWordBroker(root)
    row, verified=broker.require(args.ticket,args.token,child=True)
    if row['nonce']!=args.nonce or verified!=plan:
        raise RuntimeError('ORIGINAL_CHILD_PLAN_CHANGED')
    return protocol,broker,plan_ref,plan


def app_bindings():
    import cmux_daemon_identity as identity
    result={}
    for name in ('Microsoft Word','Zotero'):
        found=subprocess.run(['/usr/bin/pgrep','-x',name],capture_output=True,text=True,timeout=10)
        pids=found.stdout.split()
        if found.returncode!=0 or len(pids)!=1 or not pids[0].isdigit():
            raise RuntimeError('RUNNING_ORIGINAL_APPLICATION_REQUIRED:'+name)
        fact=identity.process(int(pids[0]))
        result[name]={k:fact[k] for k in ('pid','birth','executable','file_identity')}
    return result


def expected_script(session, stage):
    quote=lambda v:'"'+str(v).replace('\\','\\\\').replace('"','\\"')+'"'
    binding=('set docRef to document '+quote(session.owned.name)+'\n'
             'if (posix full name of docRef) is not '+quote(session.owned)+' then error "Owned document path mismatch"\n')
    bodies={
        'PROBE':('return {version, name of every document}',20),
        'PREFLIGHT':('if exists document '+quote(session.owned.name)+' then error "TARGET_ALREADY_OPEN"\nreturn "READY"',20),
        'OPEN':('open (POSIX file '+quote(session.owned)+') read only true add to recent files false',180),
        'RESOLVE_PATH':(binding+'return posix full name of docRef',20),
        'EXPORT':(binding+'set show revisions of docRef to false\nset print revisions of docRef to false\n'
                  'set outputFile to '+quote(session.temporary)+'\nsave as docRef file name outputFile file format format PDF add to recent files false',180),
        'CLOSE':(binding+'close docRef saving no',30),
    }
    if stage not in bodies:
        raise RuntimeError('UNKNOWN_RESTRICTED_NATIVE_STAGE')
    body,seconds=bodies[stage]
    return ('with timeout of '+str(seconds)+' seconds\ntell application "Microsoft Word"\n'+body+'\nend tell\nend timeout\n').encode()


def validate_stage(session, stage, path):
    path=Path(path)
    expected_root=Path.home()/'Library/Containers/com.microsoft.Word/Data/Documents/wordrev-pdf'
    if session.staging.parent!=expected_root or session.owned.parent!=session.staging or session.temporary.parent!=session.staging:
        raise RuntimeError('OWNED_STAGING_PATH_REQUIRED')
    if session.owned.name!='wordrev_'+session.run_id[:12]+'.docx' or session.staging.name!=session.run_id:
        raise RuntimeError('NEW_UNIQUE_OWNED_DOCUMENT_REQUIRED')
    if path!=session.evidence/(stage.lower()+'.applescript') or path.is_symlink() or path.read_bytes()!=expected_script(session,stage):
        raise RuntimeError('EXACT_OWNED_NATIVE_SCRIPT_REQUIRED')


class Driver:
    def __init__(self, protocol,broker,args,plan_ref,plan):
        self.protocol,self.broker,self.args,self.plan_ref,self.plan=protocol,broker,args,plan_ref,plan
        self.root=Path(plan['evidence_root'])
        self.session=None
        self.apps=None

    def save(self,name,value):
        path=self.root/name
        raw=(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode()
        with path.open('xb') as f:
            f.write(raw);f.flush();os.fsync(f.fileno())
        return self.protocol.pin(path)

    def guard(self):
        row,plan=self.broker.require(self.args.ticket,self.args.token,child=True)
        self.protocol.need(row['nonce']==self.args.nonce and plan==self.plan,'ORIGINAL_NATIVE_RUN_CHANGED')
        self.protocol.need(shutil.disk_usage(self.root).free>=plan['required_free_bytes'],'WORD_DISK_ADMISSION_CHANGED')
        if self.apps is not None:
            self.protocol.need(app_bindings()==self.apps,'ORIGINAL_APPLICATION_PID_OR_BIRTH_CHANGED')
        return row

    def runner(self,argv,**kwargs):
        row=self.guard()
        session=self.session
        if len(argv)==2 and argv[0]=='/usr/bin/osascript' and session is not None:
            stage=Path(argv[1]).stem.upper()
            validate_stage(session,stage,argv[1])
        elif session is not None and argv in (['pdfinfo',str(session.temporary)],['pdftotext','-layout',str(session.temporary),'-']):
            ref=self.plan['validator_pins'][argv[0]]
            self.protocol.read_pin(ref)
            argv=[ref['path'],*argv[1:]]
        else:
            raise RuntimeError('ARBITRARY_NATIVE_ARGV_REFUSED')
        remaining=row['expires']-time.time()-5
        self.protocol.need(remaining>0,'ORIGINAL_NATIVE_BUDGET_EXHAUSTED')
        kwargs['timeout']=min(kwargs.get('timeout',30),remaining)
        return subprocess.run(argv,**kwargs)

    def observe(self,tag):
        from word_restricted_inventory import SCRIPT,parse
        row=self.guard()
        path=self.root/(tag.lower()+'.applescript')
        with path.open('x') as f: f.write(SCRIPT)
        self.protocol.need(path.read_text()==SCRIPT,'NATIVE_INVENTORY_SOURCE_CHANGED')
        result=subprocess.run(['/usr/bin/osascript',str(path),str(self.apps['Microsoft Word']['pid']),str(self.apps['Zotero']['pid'])],
                              capture_output=True,text=True,timeout=min(25,row['expires']-time.time()-5))
        receipt=self.save(tag+'-inventory.json',dict(returncode=result.returncode,stdout=result.stdout,stderr=result.stderr,apps=self.apps))
        self.protocol.need(result.returncode==0,'NATIVE_INVENTORY_UNAVAILABLE')
        observed=parse(result.stdout)
        self.guard()
        return observed,receipt

    def execute(self):
        self.root.mkdir(exist_ok=False)
        self.guard()
        for ref in self.plan['service_pins'].values(): self.protocol.read_pin(ref)
        runtime=load_external('word_runtime',self.plan['service_pins']['word_runtime.py'])
        service=load_external('word_pdf_service',self.plan['service_pins']['word_pdf_service.py'])
        with ExitStack() as stack:
            stack.enter_context(runtime.word_lock('restricted-word:'+self.args.ticket,timeout=30))
            stack.enter_context(self.protocol.os_lock(Path.home()/'.cmuxterm/locks/desktop-focus.lock'))
            self.apps=app_bindings()
            before,pre=self.observe('PRE')
            self.session=service.ExportSession(self.plan['input']['path'],self.plan['output'],runner=self.runner)
            self.save('native-intent.json',dict(ticket=self.args.ticket,nonce=self.args.nonce,plan=self.plan_ref,input=self.plan['input'],
                                              owned_document=str(self.session.owned),before=before,pre=pre))
            result=self.session.export()
            service_ref=self.save('service-result.json',result)
            self.protocol.need(result.get('success') is True and result.get('cleanup')=='PASS'
                               and result.get('input_unchanged') is True and result.get('staged_docx_unchanged') is True,'NATIVE_OWNED_EXPORT_INCOMPLETE_PRESERVED')
            self.protocol.need(not self.session.staging.exists(),'OWNED_STAGING_CLEANUP_REQUIRED')
            after,post=self.observe('POST')
            self.protocol.need(after==before,'FOREIGN_OBSERVATION_CHANGED_PRESERVED')
            self.save('restricted-result.json',dict(ticket=self.args.ticket,nonce=self.args.nonce,plan=self.plan_ref,
                owned_cleanup=True,foreign_observation_preserved=True,foreign_saved='UNKNOWN',pending=False,
                legacy_released=False,zotero_internal_idle_verified=False,input=self.plan['input'],pdf=self.protocol.pin(self.plan['output']),
                pages=result['pages'],service=service_ref,pre=pre,post=post))
            return dict(status='OWNED_PDF_COMPLETE_WITH_FOREIGN_UNKNOWN',ticket=self.args.ticket,result=self.protocol.pin(self.root/'restricted-result.json'))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('root','ticket','token','nonce'): parser.add_argument('--'+name,required=True)
    args=parser.parse_args(argv)
    broker=None
    try:
        protocol,broker,ref,plan=bootstrap(args)
        result=Driver(protocol,broker,args,ref,plan).execute()
        print(json.dumps(result,ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps(dict(status='RESTRICTED_NATIVE_PENDING',error_type=type(exc).__name__,reason=str(exc),no_retry=True,
                              no_foreign_save_or_close=True,legacy_released=False),ensure_ascii=False),file=sys.stderr)
        return 2
    finally:
        if broker: broker.close()


if __name__=='__main__':
    raise SystemExit(main())
