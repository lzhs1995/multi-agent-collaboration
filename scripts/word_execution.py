"""Opt-in synchronous Word PDF stages; not a worker or a global Word sandbox.

One original broker attempt owns all eight stages. UNKNOWN is inspect-only in
this version. Native identity, local subprocess drain and Word completion are
distinct facts. Installed OfficeCLI source and historical controllers are read
but never changed.
"""
from contextlib import ExitStack
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

import resource_execution as ex

_EXECUTION_LOADED_CODE = sys._getframe().f_code
__execution_pin__ = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
SERVICE_ROOT = Path('/Users/lzhs/.local/share/officecli-word-revision/releases/0.1.0.dev5-c68cc8aec9aa/venv/lib/python3.14/site-packages/officecli_word_revision/scripts')
SERVICE_PINS = {
    'word_runtime.py': 'c700ef743e37532cebf3d10a0a78bb410920ddc1ca6280f10aaedf851a7b8458',
    'word_pdf_service.py': 'cbdee80cc5ac33565001ca221f7086ce2360292c27fa01329b761406f1e9bc77',
}
STAGES = ('PROBE', 'PREFLIGHT', 'OPEN', 'RESOLVE_PATH', 'EXPORT', 'pdfinfo', 'pdftotext', 'CLOSE')
TIMEOUTS = (50, 50, 210, 50, 210, 30, 30, 60)
PLAN_SCHEMA = 'word-pdf-stage-plan-v1'
FLOOR = 40 * 1024**3
OSASCRIPT = '/usr/bin/osascript'
MINIMUM_RELEASE_REQUIREMENTS = {
    'documents': 0, 'windows': 0, 'modal': False,
    'zotero_current_doc': False, 'zotero_current_window': False, 'pending': False,
}


class PendingPreserved(BaseException):
    """Bypass the installed service's overwrite-on-error pending handler."""


def check_release_requirements(resource):
    try:
        requirements = json.loads(resource['release_requirements'])
    except (TypeError, ValueError) as exc:
        raise ex.ExecutionError('WORD_MINIMUM_RELEASE_REQUIREMENTS_REQUIRED') from exc
    ex.need(isinstance(requirements, dict) and all(
        type(requirements.get(key)) is type(value) and requirements[key] == value
        for key, value in MINIMUM_RELEASE_REQUIREMENTS.items()),
        'WORD_MINIMUM_RELEASE_REQUIREMENTS_REQUIRED')


def file_ref(path):
    path = Path(path)
    raw = path.read_bytes()
    return dict(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def directory(ref):
    ex.need(isinstance(ref, dict) and set(ref) == {'path', 'dev', 'ino'}, 'WORD_DIRECTORY_IDENTITY_REQUIRED')
    path = ex.absolute(ref['path'])
    info = path.stat()
    ex.need(path.is_dir() and info.st_uid == os.getuid() and not info.st_mode & 0o022
            and info.st_dev == ref['dev'] and info.st_ino == ref['ino'], 'WORD_DIRECTORY_CHANGED')
    return path


def lock_refs(plan):
    expected = {str(Path.home()/'.cache/wordrev/word-automation.lock'),
                str(Path.home()/'.cmuxterm/locks/desktop-focus.lock')}
    refs = plan['locks']
    ex.need(isinstance(refs, list) and len(refs) == 2 and {x.get('path') for x in refs} == expected,
            'ORIGINAL_WORD_LOCKS_REQUIRED')
    for ref in refs:
        ex.need(set(ref) == {'path', 'dev', 'ino'}, 'WORD_LOCK_IDENTITY_REQUIRED')
        path = ex.absolute(ref['path'])
        info = path.stat()
        ex.need(path.is_file() and info.st_uid == os.getuid() and not info.st_mode & 0o022
                and info.st_dev == ref['dev'] and info.st_ino == ref['ino'], 'ORIGINAL_WORD_LOCK_CHANGED')
    return expected


def validate_inputs(contract):
    plan = ex.read_object(ex.checked_ref(contract['word_plan'], body=True))
    ex.need(set(plan) == {'schema', 'input', 'output', 'output_directory', 'services',
                         'executables', 'locks', 'budget_seconds', 'required_free_bytes'}
            and plan['schema'] == PLAN_SCHEMA, 'EXACT_WORD_PDF_PLAN_REQUIRED')
    ex.checked_ref(plan['input'])
    ex.need(plan['input'] in contract['inputs'] and contract['word_plan'] in contract['inputs'],
            'WORD_PLAN_AND_INPUT_PINS_REQUIRED')
    parent = directory(plan['output_directory'])
    output = Path(plan['output'])
    ex.need(output.is_absolute() and output.parent == parent and output.suffix.lower() == '.pdf'
            and not output.is_symlink(), 'CANONICAL_WORD_OUTPUT_REQUIRED')
    ex.need(set(plan['services']) == set(SERVICE_PINS), 'FIXED_WORD_SERVICE_REQUIRED')
    for name, ref in plan['services'].items():
        ex.need(ref['path'] == str(SERVICE_ROOT/name) and ref['sha256'] == SERVICE_PINS[name],
                'FIXED_WORD_SERVICE_REQUIRED')
        ex.checked_ref(ref)
        ex.need(ref in contract['transport_artifacts'], 'WORD_SERVICE_TRANSPORT_PIN_REQUIRED')
    ex.need(set(plan['executables']) == {'osascript', 'pdfinfo', 'pdftotext'}, 'FIXED_WORD_EXECUTABLES_REQUIRED')
    for name, ref in plan['executables'].items():
        path = ex.absolute(ref['path'])
        ex.need(path.name == name and os.access(path, os.X_OK)
                and (name != 'osascript' or str(path) == OSASCRIPT), 'WORD_EXECUTABLE_REQUIRED')
        ex.checked_ref(ref)
        ex.need(ref in contract['transport_artifacts'], 'WORD_EXECUTABLE_TRANSPORT_PIN_REQUIRED')
    lock_refs(plan)
    ex.need(type(plan['budget_seconds']) is int and 1 <= plan['budget_seconds'] <= 1800
            and type(plan['required_free_bytes']) is int and plan['required_free_bytes'] >= FLOOR,
            'WORD_BUDGET_AND_DISK_FLOOR_REQUIRED')
    return plan


def external_module(name, ref):
    raw = ex.checked_ref(ref, body=True)
    code = compile(raw, ref['path'], 'exec', dont_inherit=True)
    module = sys.modules.get(name)
    if module is not None:
        ex.need(getattr(module, '__file__', None) == ref['path']
                and getattr(module, '__execution_pin__', None) == ref['sha256']
                and getattr(module, '__word_loaded_code__', None) == code,
                'FOREIGN_OR_CHANGED_WORD_SERVICE_MODULE')
        return module
    spec = importlib.util.spec_from_file_location(name, ref['path'])
    module = importlib.util.module_from_spec(spec)
    module.__execution_pin__ = ref['sha256']
    module.__word_loaded_code__ = code
    sys.modules[name] = module
    try:
        exec(code, module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def controller_module(contract, name):
    refs = ex.controller_refs(contract['controller'])
    finder = ex.PinnedModules(refs)
    ref = finder.refs[name]
    module = sys.modules.get(name)
    if module is not None:
        ex.need(getattr(module, '__file__', None) == ref['path']
                and getattr(module, '__execution_pin__', None) == ref['sha256'], 'FOREIGN_WORD_CONTROLLER_MODULE')
        return module
    sys.meta_path.insert(0, finder)
    try:
        return importlib.import_module(name)
    finally:
        sys.meta_path.remove(finder)


def app_identity(contract):
    return controller_module(contract, 'word_restricted_pdf').app_binding('Microsoft Word')


def process_identity(contract, pid):
    value = controller_module(contract, 'cmux_daemon_identity').process(pid)
    return {key: value[key] for key in ('pid', 'ppid', 'birth', 'executable')}


def parent_identity(contract):
    return dict(process_identity(contract, os.getpid()), thread_id=threading.get_ident())


def expected_script(session, stage):
    quote = lambda v: '"'+str(v).replace('\\', '\\\\').replace('"', '\\"')+'"'
    binding = ('set docRef to document '+quote(session.owned.name)+'\n'
               'if (posix full name of docRef) is not '+quote(session.owned)+' then error "Owned document path mismatch"\n')
    bodies = {
        'PROBE': ('return {version, name of every document}', 20),
        'PREFLIGHT': ('if exists document '+quote(session.owned.name)+' then error "TARGET_ALREADY_OPEN"\nreturn "READY"', 20),
        'OPEN': ('open (POSIX file '+quote(session.owned)+') read only true add to recent files false', 180),
        'RESOLVE_PATH': (binding+'return posix full name of docRef', 20),
        'EXPORT': (binding+'set show revisions of docRef to false\nset print revisions of docRef to false\nset outputFile to '+quote(session.temporary)+'\nsave as docRef file name outputFile file format format PDF add to recent files false', 180),
        'CLOSE': (binding+'close docRef saving no', 30),
    }
    body, seconds = bodies[stage]
    return ('with timeout of '+str(seconds)+' seconds\ntell application "Microsoft Word"\n'+body+'\nend tell\nend timeout\n').encode()


class WordExecution(ex.Execution):
    def word_schema(self):
        self.db.execute('CREATE TABLE IF NOT EXISTS resource_execution_word_sessions(ticket_id TEXT PRIMARY KEY, session TEXT NOT NULL, session_sha256 TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS resource_execution_word_stages(ticket_id TEXT NOT NULL, ordinal INTEGER NOT NULL, stage TEXT NOT NULL, intent TEXT NOT NULL, intent_sha256 TEXT NOT NULL, state TEXT NOT NULL, pgid INTEGER, process TEXT, result TEXT, result_sha256 TEXT, PRIMARY KEY(ticket_id,ordinal))')

    def typed(self, ticket):
        binding, contract = self.binding(ticket)
        ex.need(contract['schema'] == ex.WORD_SCHEMA, 'WORD_TYPED_CONTRACT_REQUIRED')
        return binding, contract

    def inspect(self, ticket):
        binding, contract = self.typed(ticket)
        row = self.b.get(ticket)
        attempt = self.db.execute('SELECT * FROM resource_execution_attempts WHERE ticket_id=?', (ticket,)).fetchone()
        stages = []
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='resource_execution_word_stages'").fetchone():
            stages = [dict(r) for r in self.db.execute('SELECT * FROM resource_execution_word_stages WHERE ticket_id=? ORDER BY ordinal', (ticket,))]
        return dict(status='READBACK_ONLY_NO_RECOVERY', ticket=row, binding=binding,
                    attempt=dict(attempt) if attempt else None, stages=stages,
                    native_caller_authenticated=False, live_groups_observed=False,
                    replay_allowed=False, unknown_release_supported=False)

    def pending(self):
        return Path.home()/'.cache/wordrev/word-automation.pending.json'

    def pending_exists(self):
        return os.path.lexists(self.pending())

    def fresh(self, *, stage=False):
        self.require(self.ticket, self.token, self.generation)
        contract = self.authenticate(self.ticket)
        ex.need(contract == self.contract, 'WORD_ORIGINAL_CONTRACT_CHANGED')
        ex.need(parent_identity(contract) == self.parent, 'WORD_PARENT_OR_THREAD_CHANGED')
        ex.need(app_identity(contract) == self.app, 'WORD_APPLICATION_CHANGED')
        if self.pending_exists():
            # This check also runs before runner's stage try block. A normal
            # Exception here lets the installed service overwrite foreign
            # pending after OPEN; use its non-overwriting escape throughout.
            raise PendingPreserved('WORD_PENDING_PRESERVED')
        ex.need(time.monotonic() < self.deadline, 'WORD_OPERATION_DEADLINE')
        ex.need(shutil.disk_usage(self.plan['output_directory']['path']).free >= self.plan['required_free_bytes'], 'WORD_DISK_ADMISSION_CHANGED')
        lock_refs(self.plan)
        resource = dict(self.db.execute('SELECT * FROM resources WHERE name=?', ('word-zotero',)).fetchone())
        ex.need(resource == self.resource, 'WORD_RESOURCE_MAPPING_CHANGED')
        ex.need(self.service.word_lock is self.runtime.word_lock
                and (os.getpid(), str(Path.home()/'.cache/wordrev/word-automation.lock'))
                in getattr(self.runtime._HELD, 'paths', set()), 'ORIGINAL_WORD_LOCK_CONTEXT_REQUIRED')
        external_module('word_runtime', self.plan['services']['word_runtime.py'])
        external_module('word_pdf_service', self.plan['services']['word_pdf_service.py'])
        if stage:
            ex.checked_ref(dict(self.plan['input'], path=str(self.session.owned)))
            ex.need(self.session.source == Path(self.plan['input']['path'])
                    and self.session.output == Path(self.plan['output'])
                    and self.session.runner == self.runner, 'WORD_SESSION_MUTATED')
            for path in (self.session.evidence,self.session.staging):
                ex.absolute(str(path))
                ex.need(path.is_dir() and path.stat().st_uid == os.getuid()
                        and not path.stat().st_mode & 0o022, 'WORD_OWNED_DIRECTORY_CHANGED')

    def record_failure_pending(self, reason):
        # Preserve any already-existing pending file byte for byte. O_EXCL makes
        # this adapter's fallback atomic without rewriting another original run.
        value = dict(pid=os.getpid(), at=time.time(), run_id=getattr(self.session, 'run_id', None),
                     ticket=self.ticket, attempt_id=self.contract['attempt_id'], generation=self.generation,
                     status='TIMEOUT_UNRESOLVED', reason=str(reason))
        try:
            fd = os.open(self.pending(), os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            return
        try:
            with os.fdopen(fd, 'w') as out:
                out.write(ex.canonical(value)); out.flush(); os.fsync(out.fileno())
        except BaseException:
            raise

    def runner(self, argv, **kwargs):
        ordinal = self.next_stage
        stage = STAGES[ordinal] if ordinal < len(STAGES) else 'UNEXPECTED'
        if self.pending_exists():
            raise PendingPreserved('WORD_PENDING_PRESERVED')
        self.fresh(stage=True)
        ex.need(ordinal < len(STAGES) and kwargs == dict(capture_output=True, text=True, timeout=TIMEOUTS[ordinal]),
                'EXACT_WORD_STAGE_OPTIONS_REQUIRED')
        if stage in ('pdfinfo', 'pdftotext'):
            expected = ([stage, str(self.session.temporary)] if stage == 'pdfinfo'
                        else [stage, '-layout', str(self.session.temporary), '-'])
            ex.need(argv == expected, 'EXACT_WORD_VALIDATOR_ARGV_REQUIRED')
            actual = [self.plan['executables'][stage]['path'], *argv[1:]]
            artifact = file_ref(self.session.temporary)
        else:
            path = self.session.evidence/(stage.lower()+'.applescript')
            ex.need(argv == ['/usr/bin/osascript', str(path)] and not path.is_symlink()
                    and path.read_bytes() == expected_script(self.session, stage), 'EXACT_WORD_STAGE_ARGV_AND_SOURCE_REQUIRED')
            actual = [self.plan['executables']['osascript']['path'], str(path)]
            artifact = file_ref(path)
        ex.checked_ref(artifact)
        ex.checked_ref(self.plan['executables']['osascript' if stage not in ('pdfinfo','pdftotext') else stage])
        intent = dict(ticket_id=self.ticket, generation=self.generation, attempt_id=self.contract['attempt_id'],
                      contract_sha256=ex.sha(self.contract), ordinal=ordinal, stage=stage, argv=actual,
                      artifact=artifact, parent=self.parent, at=time.time())
        with self.b.transaction():
            self.require(self.ticket, self.token, self.generation)
            self.db.execute('INSERT INTO resource_execution_word_stages(ticket_id,ordinal,stage,intent,intent_sha256,state) VALUES(?,?,?,?,?,?)',
                            (self.ticket,ordinal,stage,ex.canonical(intent),ex.sha(intent),'UNKNOWN'))
        proc = None
        try:
            self.fresh(stage=True)
            ex.checked_ref(artifact)
            self.require(self.ticket,self.token,self.generation)
            lease,_=self.require(self.ticket,self.token,self.generation)
            budget = min(kwargs['timeout'], self.deadline-time.monotonic(),lease['expires']-time.time())
            ex.need(budget > 0, 'WORD_OPERATION_DEADLINE')
            proc = subprocess.Popen(actual, cwd=self.contract['cwd'], env=self.contract['environment'],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
            self.children.append(proc)
            with self.b.transaction():
                self.db.execute('UPDATE resource_execution_word_stages SET pgid=? WHERE ticket_id=? AND ordinal=?',
                                (proc.pid,self.ticket,ordinal))
            try:
                child = process_identity(self.contract,proc.pid)
                ex.need(child['ppid'] == os.getpid(), 'WORD_STAGE_PARENT_CHANGED')
            except (OSError, RuntimeError):
                ex.need(proc.poll() is not None and not self.group_alive(proc.pid), 'WORD_STAGE_PROCESS_IDENTITY_UNKNOWN')
                child = dict(pid=proc.pid, reaped_before_identity=True)
            with self.b.transaction():
                self.db.execute('UPDATE resource_execution_word_stages SET process=? WHERE ticket_id=? AND ordinal=?',
                                (ex.canonical(child),self.ticket,ordinal))
            budget=min(budget,self.deadline-time.monotonic())
            ex.need(budget>0,'WORD_OPERATION_DEADLINE')
            stdout,stderr = proc.communicate(timeout=budget)
            empty = not self.group_alive(proc.pid)
            result = dict(returncode=proc.returncode, stdout=stdout, stderr=stderr,
                          pgid=proc.pid, process_group_empty=empty, intent_sha256=ex.sha(intent))
            with self.b.transaction():
                self.db.execute('UPDATE resource_execution_word_stages SET state=?,result=?,result_sha256=? WHERE ticket_id=? AND ordinal=?',
                                ('TERMINAL' if empty else 'UNKNOWN',ex.canonical(result),ex.sha(result),self.ticket,ordinal))
            ex.need(empty, 'WORD_STAGE_GROUP_NOT_DRAINED')
            # The fixed service may publish/copy/clean up immediately after this
            # runner returns. Recheck the same authority and bytes at that edge.
            self.fresh(stage=True)
            ex.checked_ref(artifact)
            if proc.returncode == 0:
                self.next_stage += 1
            elif stage == 'OPEN':
                raise RuntimeError('WORD_OPEN_EFFECTS_UNCERTAIN')
            return subprocess.CompletedProcess(actual,proc.returncode,stdout,stderr)
        except BaseException as exc:
            if self.pending_exists():
                raise PendingPreserved(str(exc)) from exc
            # Even OPEN has not set the service's opened flag yet. Convert any
            # consumed-stage uncertainty to the service's real pending branch.
            raise self.service.StageError(stage,str(exc),uncertain=True) from exc

    def run_pdf(self,ticket,token,*,generation,operation,request_revision,attempt_id):
        _,self.contract = self.typed(ticket)
        self.ticket,self.token,self.generation = ticket,token,generation
        self.require(ticket,token,generation)
        ex.need(all(self.contract[k] == v for k,v in [('operation',operation),('request_revision',request_revision),('attempt_id',attempt_id)]),
                'WORD_OPERATION_BINDING_CHANGED')
        self.authenticate(ticket)
        self.plan = validate_inputs(self.contract)
        ex.need(not Path(self.plan['output']).exists(), 'WORD_OUTPUT_EXISTS_NO_REPLAY')
        ex.need(not self.pending_exists(), 'WORD_PENDING_PRESERVED')
        self.runtime=external_module('word_runtime',self.plan['services']['word_runtime.py'])
        self.service=external_module('word_pdf_service',self.plan['services']['word_pdf_service.py'])
        ex.need(self.service.word_lock is self.runtime.word_lock, 'SAME_WORD_RUNTIME_INSTANCE_REQUIRED')
        self.parent=parent_identity(self.contract)
        self.deadline=time.monotonic()+self.plan['budget_seconds']
        self.session=None;self.children=[];self.next_stage=0
        with ExitStack() as locks:
            locks.enter_context(self.lock(self.b.lock_path('word-zotero')))
            self.resource=dict(self.db.execute('SELECT * FROM resources WHERE name=?',('word-zotero',)).fetchone())
            check_release_requirements(self.resource)
            ex.need(set(json.loads(self.resource['external_locks'])) == lock_refs(self.plan), 'ORIGINAL_WORD_RESOURCE_MAPPING_REQUIRED')
            locks.enter_context(self.runtime.word_lock('enforced-word:'+ticket,timeout=5))
            locks.enter_context(self.lock(Path.home()/'.cmuxterm/locks/desktop-focus.lock'))
            self.app=app_identity(self.contract)
            self.fresh()
            intent=dict(ticket_id=ticket,generation=generation,operation=operation,request_revision=request_revision,
                        attempt_id=attempt_id,contract_sha256=ex.sha(self.contract),mode=ex.WORD_SCHEMA,
                        parent=self.parent,at=time.time(),stage_order=list(STAGES))
            with self.b.transaction():
                self.word_schema()
                row,_=self.require(ticket,token,generation)
                ex.need(row['invocations']==0 and row['pgid'] is None
                        and self.db.execute('SELECT 1 FROM resource_execution_attempts WHERE ticket_id=?',(ticket,)).fetchone() is None,
                        'ORIGINAL_ATTEMPT_EXISTS_NO_REPLAY')
                ex.need(self.db.execute('PRAGMA synchronous').fetchone()[0] in (2,3),'DURABLE_INTENT_REQUIRED')
                self.db.execute('INSERT INTO resource_execution_attempts(ticket_id,attempt_id,generation,state,intent,intent_sha256) VALUES(?,?,?,?,?,?)',
                                (ticket,attempt_id,generation,'UNKNOWN',ex.canonical(intent),ex.sha(intent)))
                self.db.execute('UPDATE tickets SET invocations=1 WHERE id=?',(ticket,))
            try:
                self.session=self.service.ExportSession(self.plan['input']['path'],self.plan['output'],runner=self.runner)
                session=dict(run_id=self.session.run_id,evidence=str(self.session.evidence),owned=str(self.session.owned),
                             staging=str(self.session.staging),temporary=str(self.session.temporary),parent=self.parent,
                             generation=generation,attempt_id=attempt_id,contract_sha256=ex.sha(self.contract))
                root=Path.home()/'Library/Containers/com.microsoft.Word/Data/Documents/wordrev-pdf'
                ex.need(self.session.staging == root/self.session.run_id
                        and self.session.owned == self.session.staging/('wordrev_'+self.session.run_id[:12]+'.docx')
                        and self.session.temporary == self.session.staging/'export.pdf'
                        and self.session.evidence.parent == Path(self.plan['output']).parent, 'WORD_REALIZED_PATHS_CHANGED')
                with self.b.transaction():
                    self.db.execute('INSERT INTO resource_execution_word_sessions VALUES(?,?,?)',
                                    (ticket,ex.canonical(session),ex.sha(session)))
                service_result=self.session.export()
                self.fresh()
                ex.need(self.next_stage==len(STAGES) and service_result.get('success') is True
                        and service_result.get('cleanup')=='PASS' and service_result.get('input_unchanged') is True
                        and service_result.get('staged_docx_unchanged') is True and not self.session.staging.exists(),
                        'WORD_TYPED_EXPORT_INCOMPLETE')
                stage_digest=self.all_stages(ticket)
                result=dict(mode=ex.WORD_SCHEMA,intent_sha256=ex.sha(intent),stage_digest=stage_digest,
                            pdf=file_ref(Path(self.plan['output'])),service_result=file_ref(self.session.evidence/'result.json'),
                            process_group_empty=True,completed_at=time.time())
                ex.need(result['pdf']['sha256']==service_result.get('pdf_sha256'),'WORD_FINAL_PDF_CHANGED')
                with self.b.transaction():
                    self.require(ticket,token,generation)
                    self.db.execute("UPDATE resource_execution_attempts SET state='TERMINAL',result=?,result_sha256=? WHERE ticket_id=?",
                                    (ex.canonical(result),ex.sha(result),ticket))
                return dict(status='OWNED_PDF_COMPLETE_RESOURCE_STILL_ACTIVE',execution=self.enrich(self.b.get(ticket))['execution'],
                            result=result,resource_released=False,scope='WORD_PDF_TYPED_ENTRY_ONLY')
            except BaseException as exc:
                if self.next_stage >= 2 or self.children:
                    self.record_failure_pending(exc)
                raise ex.ExecutionError('WORD_ORIGINAL_ATTEMPT_UNKNOWN_INSPECT_ONLY: '+str(exc)) from exc
            finally:
                deadline=time.monotonic()+5
                for proc in self.children:
                    if proc.poll() is None:
                        try: proc.wait(timeout=max(.001,deadline-time.monotonic()))
                        except BaseException: pass
                while time.monotonic()<deadline and any(self.group_alive(p.pid) for p in self.children):
                    time.sleep(min(.05,max(0,deadline-time.monotonic())))

    def all_stages(self,ticket):
        binding,contract=self.typed(ticket)
        rows=[dict(r) for r in self.db.execute('SELECT * FROM resource_execution_word_stages WHERE ticket_id=? ORDER BY ordinal',(ticket,))]
        ex.need([r['stage'] for r in rows]==list(STAGES) and [r['ordinal'] for r in rows]==list(range(8)), 'ALL_WORD_STAGES_REQUIRED')
        for row in rows:
            result=ex.read_object(row['result']) if row['result'] else {}
            intent=ex.read_object(row['intent'])
            ex.need(row['state']=='TERMINAL' and row['pgid'] is not None and row['process'] is not None
                    and ex.sha(intent)==row['intent_sha256']
                    and intent.get('ticket_id')==ticket and intent.get('generation')==binding['generation']
                    and intent.get('attempt_id')==contract['attempt_id'] and intent.get('contract_sha256')==binding['contract_sha256']
                    and intent.get('ordinal')==row['ordinal'] and intent.get('stage')==row['stage']
                    and result.get('intent_sha256')==row['intent_sha256'] and result.get('pgid')==row['pgid']
                    and ex.sha(result)==row['result_sha256'] and result.get('returncode')==0
                    and result.get('process_group_empty') is True and not self.group_alive(row['pgid']), 'WORD_STAGE_TERMINAL_REQUIRED')
        return ex.sha(rows)

    def check_release(self,row,proof,generation,reconcile):
        self.typed(row['id'])
        resource=dict(self.db.execute('SELECT * FROM resources WHERE name=?',('word-zotero',)).fetchone())
        check_release_requirements(resource)
        self.require(row['id'],row['token'],generation,expired=True)
        ex.need(not self.pending_exists(),'WORD_PENDING_PRESERVED')
        expected=self.enrich(row)['execution']
        supplied=proof.get('execution')
        ex.need(isinstance(supplied,dict) and all(supplied.get(k)==expected[k] for k in ex.KEYS), 'RELEASE_EXECUTION_BINDING_REQUIRED')
        attempt=self.db.execute('SELECT * FROM resource_execution_attempts WHERE ticket_id=?',(row['id'],)).fetchone()
        if attempt is None:
            ex.need(row['invocations']==0 and row['pgid'] is None and proof.get('no_request_submitted') is True,'NO_SUBMISSION_PROOF_REQUIRED')
            return
        ex.need(attempt['state']=='TERMINAL','WORD_UNKNOWN_RELEASE_UNSUPPORTED_INSPECT_ONLY')
        result=ex.read_object(attempt['result'])
        session=self.db.execute('SELECT * FROM resource_execution_word_sessions WHERE ticket_id=?',(row['id'],)).fetchone()
        ex.need(session is not None and ex.sha(ex.read_object(session['session']))==session['session_sha256'],
                'WORD_REALIZED_SESSION_CHANGED')
        ex.need(ex.sha(result)==attempt['result_sha256']==supplied.get('result_sha256')
                and result['stage_digest']==self.all_stages(row['id']),'WORD_AGGREGATE_RESULT_CHANGED')
        ex.checked_ref(result['pdf']);ex.checked_ref(result['service_result'])
        observation=ex.read_object(ex.checked_ref(proof.get('observed_receipt'),body=True))
        observed=observation.get('execution')
        ex.need(isinstance(observed,dict) and all(observed.get(k)==expected[k] for k in ex.KEYS)
                and observation.get('result_sha256')==attempt['result_sha256']
                and observation.get('stage_digest')==result['stage_digest']
                and type(observation.get('observed_at')) in (int,float)
                and result['completed_at'] <= observation['observed_at'] <= time.time()+1
                and time.time()-observation['observed_at'] <= 30,'FRESH_WORD_GLOBAL_EXECUTION_OBSERVATION_REQUIRED')
