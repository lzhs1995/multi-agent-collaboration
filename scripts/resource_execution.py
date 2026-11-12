"""Opt-in, one-operation prelaunch enforcement in the original broker database.

This is not a sandbox or a remote write gate. Only newly adopted tickets use
these checks. Old controllers/tickets remain unchanged. UNKNOWN attempts never
retry; a crash after intent can require external original-attempt readback.
"""
from contextlib import ExitStack, contextmanager
import hashlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import uuid

_EXECUTION_LOADED_CODE = sys._getframe().f_code
__execution_pin__ = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

SOURCE = Path(__file__).resolve().parent.parent
SCHEMA = 'resource-execution-v1'
WORD_SCHEMA = 'resource-execution-word-pdf-v1'
KEYS = ('generation', 'operation', 'request_revision', 'attempt_id', 'contract_sha256')


class ExecutionError(ValueError):
    pass


def need(value, message):
    if not value:
        raise ExecutionError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def read_object(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs,
                       parse_constant=lambda _: need(False, 'NONFINITE_JSON'))
    need(isinstance(value, dict), 'JSON_OBJECT_REQUIRED')
    return value


def absolute(value):
    need(isinstance(value, str) and '\0' not in value, 'ABSOLUTE_CANONICAL_PATH_REQUIRED')
    path = Path(value)
    need(path.is_absolute() and str(path.resolve(strict=True)) == value
         and not path.is_symlink(), 'ABSOLUTE_CANONICAL_PATH_REQUIRED')
    return path


def checked_ref(ref, *, body=False):
    need(isinstance(ref, dict) and set(ref) == {'path', 'bytes', 'sha256'}, 'EXACT_FILE_REF_REQUIRED')
    need(type(ref['bytes']) is int and ref['bytes'] >= 0
         and isinstance(ref['sha256'], str) and re.fullmatch('[0-9a-f]{64}', ref['sha256']),
         'INVALID_FILE_REF')
    path = absolute(ref['path'])
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    digest = hashlib.sha256()
    chunks = []
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_size == ref['bytes'], 'PIN_SIZE_OR_TYPE_CHANGED')
        need(not body or before.st_size <= 8 * 1024**2, 'PIN_BODY_TOO_LARGE')
        total = 0
        while True:
            data = os.read(fd, 1024**2)
            if not data:
                break
            total += len(data)
            need(total <= ref['bytes'], 'PIN_GREW_DURING_READ')
            digest.update(data)
            if body:
                chunks.append(data)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        need(identity(before) == identity(os.fstat(fd)) == identity(path.stat()), 'PIN_IDENTITY_CHANGED')
        need(total == ref['bytes'] and digest.hexdigest() == ref['sha256'], 'PIN_CONTENT_CHANGED')
    finally:
        os.close(fd)
    return b''.join(chunks) if body else None


def controller_refs(controller):
    need(isinstance(controller, dict) and set(controller) == {'source', 'files'}, 'CONTROLLER_REQUIRED')
    source = absolute(controller['source'])
    need(source == SOURCE and source.is_dir(), 'ORIGINAL_EXECUTION_CONTROLLER_REQUIRED')
    files = controller['files']
    need(isinstance(files, list) and files, 'COMPLETE_SCRIPTS_CLOSURE_REQUIRED')
    refs = {}
    for item in files:
        need(isinstance(item, dict) and set(item) == {'relative', 'bytes', 'sha256'}, 'CONTROLLER_FILE_REQUIRED')
        relative = item['relative']
        need(isinstance(relative, str) and re.fullmatch(r'scripts/[A-Za-z0-9_]+\.py', relative)
             and relative not in refs, 'INVALID_CONTROLLER_MEMBER')
        refs[relative] = {'path': str(source/relative), 'bytes': item['bytes'], 'sha256': item['sha256']}
    expected = {str(p.relative_to(source)) for p in (source/'scripts').glob('*.py')}
    need(set(refs) == expected and {'scripts/resource_broker.py', 'scripts/resource_execution.py',
         'scripts/cmux_workspace_guard.py'} <= expected, 'COMPLETE_SCRIPTS_CLOSURE_REQUIRED')
    for ref in refs.values():
        checked_ref(ref)
    return refs


class PinnedModules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, refs):
        self.refs = {Path(ref['path']).stem: ref for ref in refs.values()}

    def find_spec(self, fullname, path=None, target=None):
        if fullname in self.refs:
            return importlib.util.spec_from_loader(fullname, self, origin=self.refs[fullname]['path'])

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        ref = self.refs[module.__name__]
        module.__file__ = ref['path']
        module.__execution_pin__ = ref['sha256']
        exec(compile(checked_ref(ref, body=True), ref['path'], 'exec', dont_inherit=True), module.__dict__)


def native_identity(contract):
    """Use only the pinned controller's actual foreground caller, never selectors."""
    refs = controller_refs(contract['controller'])
    finder = PinnedModules(refs)
    for name, ref in finder.refs.items():
        module = sys.modules.get(name)
        if module is None:
            continue
        need(Path(getattr(module, '__file__', '')).resolve() == Path(ref['path']), 'FOREIGN_CONTROLLER_MODULE')
        need(getattr(module, '__execution_pin__', None) == ref['sha256'], 'UNPINNED_CONTROLLER_MODULE')
    sys.meta_path.insert(0, finder)
    try:
        import cmux_workspace_guard as guard
        import cmux_identity_budget as budget
        with budget.limit(20):
            snapshot, _, _, proof = guard.caller_snapshot()
        thread = (proof or {}).get('foreground', {}).get('thread_id')
        need(bool(thread), 'AUTHENTICATED_FOREGROUND_THREAD_REQUIRED')
        caller = snapshot['caller']
        return {'workspace_uuid': caller['workspace_id'], 'surface_uuid': caller['surface_id'],
                'pane_uuid': caller['pane_id'], 'session_id': thread}
    finally:
        sys.meta_path.remove(finder)


def actor_matches(expected, actual):
    try:
        return all(uuid.UUID(expected[k]) == uuid.UUID(actual[k]) for k in (
            'workspace_uuid', 'surface_uuid', 'pane_uuid', 'session_id'))
    except (TypeError, ValueError, KeyError, AttributeError):
        return False


def validate(contract, *, execution_inputs=True):
    loaded_source = Path(__file__).read_bytes()
    need(hashlib.sha256(loaded_source).hexdigest() == __execution_pin__
         and _EXECUTION_LOADED_CODE == compile(loaded_source, __file__, 'exec', dont_inherit=True),
         'LOADED_EXECUTION_SOURCE_CHANGED')
    word = isinstance(contract, dict) and contract.get('schema') == WORD_SCHEMA
    need(isinstance(contract, dict) and set(contract) == {
        'schema', 'task_id', 'resource', 'actor', 'controller', 'operation', 'request_revision',
        'attempt_id', 'cwd', 'environment', 'transport_artifacts', 'inputs',
        'word_plan' if word else 'argv'}, 'EXACT_EXECUTION_CONTRACT_REQUIRED')
    need(contract['schema'] in (SCHEMA, WORD_SCHEMA), 'EXECUTION_SCHEMA_REQUIRED')
    if word:
        need(contract['resource'] == 'word-zotero' and contract['operation'] == 'owned_word_pdf_export',
             'WORD_PDF_OPERATION_REQUIRED')
    for key in ('task_id', 'resource', 'operation', 'attempt_id'):
        need(isinstance(contract[key], str) and 0 < len(contract[key]) <= 256
             and not any(c in contract[key] for c in '\0\r\n'), 'INVALID_EXECUTION_IDENTIFIER')
    need(isinstance(contract['request_revision'], str)
         and re.fullmatch('[0-9a-f]{64}', contract['request_revision']), 'REQUEST_REVISION_SHA_REQUIRED')
    actor = contract['actor']
    need(isinstance(actor, dict) and set(actor) == {
        'workspace_uuid', 'surface_uuid', 'pane_uuid', 'session_id'} and actor_matches(actor, actor), 'EXACT_NATIVE_ACTOR_REQUIRED')
    controller_refs(contract['controller'])
    if not execution_inputs:
        # Cancelling a never-started operation authorizes no execution. Its
        # original research input, transport or cwd may legitimately be gone.
        return read_object(canonical(contract))
    need(absolute(contract['cwd']).is_dir(), 'CWD_DIRECTORY_REQUIRED')
    env = contract['environment']
    if not word:
        argv = contract['argv']
        need(isinstance(argv, list) and argv and all(isinstance(x, str) and '\0' not in x for x in argv), 'EXACT_ARGV_REQUIRED')
        executable = absolute(argv[0])
        need(executable.is_file() and os.access(executable, os.X_OK), 'PINNED_EXECUTABLE_REQUIRED')
    need(isinstance(env, dict) and all(isinstance(k, str) and k and '=' not in k
         and '\0' not in k and isinstance(v, str) and '\0' not in v for k, v in env.items()), 'COMPLETE_ENVIRONMENT_REQUIRED')
    for field in ('inputs', 'transport_artifacts'):
        need(isinstance(contract[field], list) and contract[field], 'INPUT_AND_TRANSPORT_PINS_REQUIRED')
        seen = set()
        for ref in contract[field]:
            checked_ref(ref)
            need(ref['path'] not in seen, 'DUPLICATE_PIN')
            seen.add(ref['path'])
    if word:
        word_module().validate_inputs(contract)
    else:
        need(str(executable) in {ref['path'] for ref in contract['transport_artifacts']}, 'EXECUTABLE_TRANSPORT_PIN_REQUIRED')
    return read_object(canonical(contract))


def word_module():
    """One exact adjacent typed implementation; no arbitrary adapter injection."""
    path = Path(__file__).resolve().with_name('word_execution.py')
    raw = path.read_bytes()
    module = sys.modules.get('word_execution')
    if module is not None:
        need(Path(getattr(module, '__file__', '')).resolve() == path
             and getattr(module, '__execution_pin__', None) == hashlib.sha256(raw).hexdigest()
             and getattr(module, '_EXECUTION_LOADED_CODE', None) == compile(raw, str(path), 'exec', dont_inherit=True),
             'FOREIGN_OR_CHANGED_WORD_EXECUTION_MODULE')
        return module
    spec = importlib.util.spec_from_file_location('word_execution', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules['word_execution'] = module
    try:
        exec(compile(raw, str(path), 'exec', dont_inherit=True), module.__dict__)
    except BaseException:
        sys.modules.pop('word_execution', None)
        raise
    return module


def validate_contract(supplied, *, execution_inputs=True):
    """Public read-only preflight, including fresh native actor authentication."""
    contract = validate(supplied, execution_inputs=execution_inputs)
    need(actor_matches(contract['actor'], native_identity(contract)), 'NATIVE_EXECUTION_ACTOR_MISMATCH')
    # Native collection can be slow; pins must still hold after it returns.
    return validate(contract, execution_inputs=execution_inputs)


class Execution:
    def __init__(self, broker, lock, group_alive):
        self.b, self.db, self.lock, self.group_alive = broker, broker.db, lock, group_alive

    def schema(self):
        # Add metadata only: old SELECT * row hashes must remain unchanged.
        self.db.execute('CREATE TABLE IF NOT EXISTS resource_execution_generations(resource TEXT PRIMARY KEY, generation INTEGER NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS resource_execution_bindings(ticket_id TEXT PRIMARY KEY, contract TEXT NOT NULL, contract_sha256 TEXT NOT NULL, generation INTEGER, attempt_id TEXT UNIQUE NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS resource_execution_attempts(ticket_id TEXT PRIMARY KEY, attempt_id TEXT UNIQUE NOT NULL, generation INTEGER NOT NULL, state TEXT NOT NULL, intent TEXT NOT NULL, intent_sha256 TEXT NOT NULL, pgid INTEGER, result TEXT, result_sha256 TEXT, reconciliation TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS resource_execution_unsubmitted_history(ticket_id TEXT NOT NULL, generation INTEGER NOT NULL, lease TEXT NOT NULL, lease_sha256 TEXT NOT NULL, binding TEXT NOT NULL, binding_sha256 TEXT NOT NULL, release_proof TEXT NOT NULL, release_proof_sha256 TEXT NOT NULL, PRIMARY KEY(ticket_id,generation))')
        self.db.execute('CREATE TABLE IF NOT EXISTS resource_execution_cancellations(ticket_id TEXT PRIMARY KEY, cancellation TEXT NOT NULL, cancellation_sha256 TEXT NOT NULL)')

    def binding(self, ticket):
        need(self.b._bound(ticket), 'ENFORCED_TICKET_REQUIRED')
        row = dict(self.db.execute('SELECT * FROM resource_execution_bindings WHERE ticket_id=?', (ticket,)).fetchone())
        contract = read_object(row['contract'])
        need(sha(contract) == row['contract_sha256'], 'EXECUTION_CONTRACT_CHANGED')
        return row, contract

    def authenticate(self, ticket, *, execution_inputs=True):
        _, contract = self.binding(ticket)
        return validate_contract(contract, execution_inputs=execution_inputs)

    @contextmanager
    def locked_native(self, ticket, *, cancellation=False):
        """Collect slow native proof under original locks, outside SQLite writes.

        Local row/pin comparisons in the following transaction detect changes;
        native identity observation and commit remain separate operations.
        """
        row = self.b.get(ticket)
        with ExitStack() as locks:
            locks.enter_context(self.lock(self.b.lock_path(row['resource'])))
            resource = dict(self.db.execute('SELECT * FROM resources WHERE name=?', (row['resource'],)).fetchone())
            paths = json.loads(resource['external_locks'])
            need(len(paths) == len(set(paths)), 'DUPLICATE_EXTERNAL_LOCK')
            if not cancellation:
                for path in paths:
                    locks.enter_context(self.lock(path))
            contract = self.authenticate(ticket, execution_inputs=not cancellation)
            self.prechecked = (row, resource, sha(contract), cancellation)
            try:
                self.local_check(ticket)
                yield
            finally:
                self.prechecked = None

    def local_check(self, ticket):
        saved = getattr(self, 'prechecked', None)
        need(saved is not None, 'LOCKED_NATIVE_PREFLIGHT_REQUIRED')
        row, resource, digest, cancellation = saved
        need(row['id'] == ticket and self.b.get(ticket) == row, 'PREFLIGHT_TICKET_CHANGED')
        actual = self.db.execute('SELECT * FROM resources WHERE name=?', (row['resource'],)).fetchone()
        need(actual is not None and dict(actual) == resource, 'PREFLIGHT_RESOURCE_CHANGED')
        _, contract = self.binding(ticket)
        need(sha(contract) == digest, 'PREFLIGHT_CONTRACT_CHANGED')
        validate(contract, execution_inputs=not cancellation)  # Local pins; never native I/O.

    def withdraw(self, ticket, *, attempt_id, contract_sha256):
        with self.locked_native(ticket, cancellation=True):
            with self.b.transaction():
                self.local_check(ticket)
                row = self.b.get(ticket)
                binding, contract = self.binding(ticket)
                need(attempt_id == contract['attempt_id'] and contract_sha256 == binding['contract_sha256'],
                     'ORIGINAL_WITHDRAWAL_BINDING_REQUIRED')
                need(row['task_id'] == contract['task_id'] and row['resource'] == contract['resource']
                     and row['workspace_uuid'] == contract['actor']['workspace_uuid']
                     and row['surface_uuid'] == contract['actor']['surface_uuid'], 'EXECUTION_TICKET_BINDING_CHANGED')
                need(row['status'] == 'WAITING' and binding['generation'] is None
                     and row['invocations'] == 0 and row['pgid'] is None
                     and row['token'] is None and row['expires'] is None
                     and row['release_proof'] is None
                     and self.db.execute('SELECT 1 FROM resource_execution_attempts WHERE ticket_id=?', (ticket,)).fetchone() is None,
                     'WAITING_UNSTARTED_WITHDRAWAL_REQUIRED')
                self.schema()
                cancellation = dict(schema='resource-execution-cancellation-v1', ticket=row,
                                    binding=binding, actor=contract['actor'], at=time.time(),
                                    no_invocation=True, input_pins_not_required_for_cancellation=True)
                self.db.execute('INSERT INTO resource_execution_cancellations VALUES(?,?,?)',
                                (ticket, canonical(cancellation), sha(cancellation)))
                self.db.execute("UPDATE tickets SET status='WITHDRAWN' WHERE id=?", (ticket,))
            return self.enrich(self.b.get(ticket))

    def enrich(self, row):
        binding, contract = self.binding(row['id'])
        execution = {key: contract[key] for key in ('operation', 'request_revision', 'attempt_id')}
        execution.update(generation=binding['generation'], contract_sha256=binding['contract_sha256'])
        attempt = self.db.execute('SELECT * FROM resource_execution_attempts WHERE ticket_id=?', (row['id'],)).fetchone()
        if attempt:
            execution.update(state=attempt['state'], intent_sha256=attempt['intent_sha256'],
                             result_sha256=attempt['result_sha256'])
        return dict(row, execution=execution)

    def request(self, supplied):
        contract = validate_contract(supplied)
        with self.b.transaction():
            self.schema()
            need(self.db.execute('SELECT 1 FROM resources WHERE name=?', (contract['resource'],)).fetchone(), 'RESOURCE_NOT_CONFIGURED')
            old = self.db.execute('SELECT * FROM resource_execution_bindings WHERE attempt_id=?', (contract['attempt_id'],)).fetchone()
            if old:
                need(old['contract_sha256'] == sha(contract) and old['contract'] == canonical(contract), 'ORIGINAL_ATTEMPT_BINDING_CHANGED')
                return self.enrich(self.b.get(old['ticket_id']))
            rows = self.db.execute('SELECT id,status FROM tickets WHERE resource=? AND task_id=?',
                                   (contract['resource'], contract['task_id'])).fetchall()
            for row in rows:
                need(self.b._bound(row['id']), 'LEGACY_TICKET_CANNOT_BE_UPGRADED')
                need(row['status'] not in ('WAITING', 'ACTIVE'), 'ENFORCED_TASK_ALREADY_OWNED')
                _, previous = self.binding(row['id'])
                need((previous['operation'], previous['request_revision']) !=
                     (contract['operation'], contract['request_revision']), 'ORIGINAL_OPERATION_EXISTS_NO_REPLAY')
            ticket = str(uuid.uuid4())
            actor = contract['actor']
            self.db.execute("INSERT INTO tickets(id,task_id,workspace_uuid,surface_uuid,resource,status,requested) VALUES(?,?,?,?,?,'WAITING',?)",
                            (ticket, contract['task_id'], actor['workspace_uuid'], actor['surface_uuid'], contract['resource'], time.time()))
            self.db.execute('INSERT INTO resource_execution_bindings VALUES(?,?,?,?,?)',
                            (ticket, canonical(contract), sha(contract), None, contract['attempt_id']))
            return self.enrich(self.b.get(ticket))

    def requeue_unsubmitted(self, row, binding):
        """Requeue only an operation proved never submitted, preserving its lease."""
        ticket = row['id']
        need(row['status'] == 'RELEASED' and row['invocations'] == 0 and row['pgid'] is None
             and self.db.execute('SELECT 1 FROM resource_execution_attempts WHERE ticket_id=?', (ticket,)).fetchone() is None,
             'UNSUBMITTED_REQUEUE_REQUIRED')
        generation = binding['generation']
        need(type(generation) is int and generation > 0, 'ORIGINAL_RELEASE_GENERATION_REQUIRED')
        proof = read_object(row['release_proof'])
        expected = self.enrich(row)['execution']
        supplied = proof.get('execution')
        need(proof.get('no_request_submitted') is True and proof.get('pending') is False
             and proof.get('own_processes_empty') is True
             and proof.get('external_locks_probed_and_released') is True
             and isinstance(supplied, dict) and all(supplied.get(k) == expected[k] for k in KEYS),
             'ORIGINAL_NO_SUBMISSION_RELEASE_REQUIRED')
        self.db.execute('INSERT INTO resource_execution_unsubmitted_history VALUES(?,?,?,?,?,?,?,?)',
                        (ticket, generation, canonical(row), sha(row), canonical(binding), sha(binding),
                         canonical(proof), sha(proof)))
        self.db.execute("UPDATE tickets SET status='WAITING',requested=?,token=NULL,expires=NULL,release_proof=NULL WHERE id=?", (time.time(), ticket))
        self.db.execute('UPDATE resource_execution_bindings SET generation=NULL WHERE ticket_id=?', (ticket,))

    def granted(self, row):
        binding, _ = self.binding(row['id'])
        need(binding['generation'] is None, 'ORIGINAL_GRANT_EXISTS')
        previous = self.db.execute('SELECT generation FROM resource_execution_generations WHERE resource=?', (row['resource'],)).fetchone()
        generation = 0 if previous is None else previous[0]
        need(type(generation) is int and 0 <= generation < 9223372036854775807, 'GENERATION_EXHAUSTED')
        self.db.execute('INSERT INTO resource_execution_generations VALUES(?,?) ON CONFLICT(resource) DO UPDATE SET generation=excluded.generation', (row['resource'], generation+1))
        self.db.execute('UPDATE resource_execution_bindings SET generation=? WHERE ticket_id=?', (generation+1, row['id']))

    def require(self, ticket, token, generation, *, expired=False):
        row = self.b._require(ticket, token, allow_expired=expired)
        binding, contract = self.binding(ticket)
        current = self.db.execute('SELECT generation FROM resource_execution_generations WHERE resource=?', (row['resource'],)).fetchone()
        need(type(generation) is int and generation > 0 and current is not None
             and generation == binding['generation'] == current[0], 'EXECUTION_GENERATION_MISMATCH')
        need(row['task_id'] == contract['task_id'] and row['resource'] == contract['resource']
             and row['workspace_uuid'] == contract['actor']['workspace_uuid']
             and row['surface_uuid'] == contract['actor']['surface_uuid'], 'EXECUTION_TICKET_BINDING_CHANGED')
        return row, contract

    def run(self, ticket, token, *, generation, operation, request_revision, attempt_id, **kwargs):
        need(set(kwargs) <= {'text', 'stdout', 'stderr'}, 'UNBOUND_POPEN_OPTIONS')
        need('text' not in kwargs or type(kwargs['text']) is bool, 'INVALID_TEXT_OPTION')
        row, contract = self.require(ticket, token, generation)
        need(contract['schema'] == SCHEMA, 'WORD_TYPED_RUN_REQUIRED')
        for key, value in [('operation', operation), ('request_revision', request_revision), ('attempt_id', attempt_id)]:
            need(contract[key] == value, 'EXECUTION_OPERATION_BINDING_CHANGED')
        self.authenticate(ticket)
        with ExitStack() as locks:
            locks.enter_context(self.lock(self.b.lock_path(row['resource'])))
            resource = self.db.execute('SELECT external_locks FROM resources WHERE name=?', (row['resource'],)).fetchone()
            paths = json.loads(resource[0])
            need(len(paths) == len(set(paths)), 'DUPLICATE_EXTERNAL_LOCK')
            for path in paths:
                locks.enter_context(self.lock(path))
            self.authenticate(ticket)
            with self.b.transaction():
                row, contract = self.require(ticket, token, generation)
                need(row['invocations'] == 0 and row['pgid'] is None
                     and self.db.execute('SELECT 1 FROM resource_execution_attempts WHERE ticket_id=?', (ticket,)).fetchone() is None,
                     'ORIGINAL_ATTEMPT_EXISTS_NO_REPLAY')
                need(self.db.execute('PRAGMA synchronous').fetchone()[0] in (2, 3), 'DURABLE_INTENT_REQUIRED')
                intent = dict(ticket_id=ticket, generation=generation, operation=operation,
                              request_revision=request_revision, attempt_id=attempt_id,
                              contract_sha256=sha(contract), parent_pid=os.getpid(), at=time.time())
                self.db.execute('INSERT INTO resource_execution_attempts(ticket_id,attempt_id,generation,state,intent,intent_sha256) VALUES(?,?,?,?,?,?)',
                                (ticket, attempt_id, generation, 'UNKNOWN', canonical(intent), sha(intent)))
                self.db.execute('UPDATE tickets SET invocations=invocations+1 WHERE id=?', (ticket,))
            # Durable intent comes first. Any subsequent refusal leaves UNKNOWN.
            contract = self.authenticate(ticket)
            self.require(ticket, token, generation)
            proc = subprocess.Popen(contract['argv'], cwd=contract['cwd'], env=contract['environment'],
                                    start_new_session=True, **kwargs)
            try:
                with self.b.transaction():
                    self.db.execute('UPDATE tickets SET pgid=? WHERE id=?', (proc.pid, ticket))
                    self.db.execute('UPDATE resource_execution_attempts SET pgid=? WHERE ticket_id=?', (proc.pid, ticket))
                stdout, stderr = proc.communicate()
                empty = not self.group_alive(proc.pid)
                terminal = dict(exit_code=proc.returncode, process_group_empty=empty, pgid=proc.pid,
                                generation=generation, operation=operation, request_revision=request_revision,
                                attempt_id=attempt_id, contract_sha256=sha(contract), intent_sha256=sha(intent))
                with self.b.transaction():
                    self.db.execute('UPDATE resource_execution_attempts SET state=?,result=?,result_sha256=? WHERE ticket_id=?',
                                    ('TERMINAL' if empty else 'UNKNOWN', canonical(terminal), sha(terminal), ticket))
                    if empty:
                        self.db.execute('UPDATE tickets SET pgid=NULL WHERE id=?', (ticket,))
            except BaseException:
                # Keep these same locks during a bounded owned-child drain. No
                # signal, resubmission or inference of remote completion. If the
                # budget expires the durable ACTIVE/UNKNOWN lease still blocks
                # broker users; process-local flock cannot protect bypass users
                # after this function exits (nor survive a process/host crash).
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    try:
                        proc.wait(timeout=max(0.001, deadline-time.monotonic()))
                        if not self.group_alive(proc.pid):
                            break
                        time.sleep(min(.05, max(0, deadline-time.monotonic())))
                    except BaseException:
                        break
                raise
            execution = self.enrich(self.b.get(ticket))['execution']
            return dict(terminal, stdout=stdout, stderr=stderr, execution=execution, enforcement_scope='PRELAUNCH_ONLY')

    def check_release(self, row, proof, generation, reconcile):
        _, contract = self.require(row['id'], row['token'], generation, expired=True)
        need(contract['schema'] == SCHEMA, 'WORD_TYPED_RELEASE_REQUIRED')
        expected = self.enrich(row)['execution']
        supplied = proof.get('execution')
        need(isinstance(supplied, dict) and all(supplied.get(k) == expected[k] for k in KEYS), 'RELEASE_EXECUTION_BINDING_REQUIRED')
        attempt = self.db.execute('SELECT * FROM resource_execution_attempts WHERE ticket_id=?', (row['id'],)).fetchone()
        if attempt is None:
            need(row['invocations'] == 0 and row['pgid'] is None and proof.get('no_request_submitted') is True,
                 'NO_SUBMISSION_PROOF_REQUIRED')
        elif attempt['state'] == 'TERMINAL':
            need(supplied.get('result_sha256') == attempt['result_sha256']
                 and attempt['result'] is not None and sha(read_object(attempt['result'])) == attempt['result_sha256'],
                 'ORIGINAL_ATTEMPT_TERMINAL_REQUIRED')
            need(read_object(attempt['result'])['process_group_empty'] is True
                 and not self.group_alive(attempt['pgid']), 'ATTEMPT_DRAIN_REQUIRED')
        else:
            need(reconcile and attempt['pgid'] is not None and not self.group_alive(attempt['pgid']), 'UNKNOWN_ORIGINAL_ATTEMPT_READBACK_REQUIRED')
            ref = proof.get('execution_readback')
            readback = read_object(checked_ref(ref, body=True))
            need(readback.get('schema') == 'resource-execution-zero-effects-readback-v1'
                 and readback.get('ticket_id') == row['id']
                 and all(readback.get(k) == expected[k] for k in KEYS)
                 and readback.get('intent_sha256') == attempt['intent_sha256']
                 and readback.get('pgid') == attempt['pgid']
                 and all(readback.get(k) is True for k in ('zero_effects', 'process_group_empty', 'remote_terminal')),
                 'EXACT_ZERO_EFFECTS_READBACK_REQUIRED')
            self.db.execute('UPDATE resource_execution_attempts SET reconciliation=? WHERE ticket_id=?', (canonical(ref), row['id']))
        if row['resource'].startswith('nlm-account:') and attempt is not None:
            receipt = read_object(checked_ref(proof.get('terminal_receipt'), body=True))
            recorded = (receipt.get('resource_lease') or {}).get('execution') or {}
            need(all(recorded.get(k) == expected[k] for k in KEYS)
                 and receipt.get('operation') == contract['operation'], 'NLM_TERMINAL_EXECUTION_MISMATCH')
