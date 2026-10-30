"""An isolated owned Word queue. Legacy tickets and release predicates never change."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import time
import uuid

from resource_broker import ResourceBusy, group_alive, os_lock

RESOURCE = 'word-zotero'
FLOOR = 22 * 1024**3
PDF_SCOPE = 'OWNED_PDF_ONLY'
REVISION_SCOPE = 'OWNED_REVISION_ROUNDTRIP'
NATIVE_REVISION_SCHEMA = 'word-restricted-native-successor-disposition-v1'
SERVICE_PINS = {
    'word_pdf_service.py': 'cbdee80cc5ac33565001ca221f7086ce2360292c27fa01329b761406f1e9bc77',
    'word_runtime.py': 'c700ef743e37532cebf3d10a0a78bb410920ddc1ca6280f10aaedf851a7b8458',
}
VALIDATOR_PINS = {
    'pdfinfo': '8a48bb18e99c8f1add637ffae1d860c3cdabe11f4f1672f3c1b5ef4c3e6e8461',
    'pdftotext': '52cb759b328134d667e0e9ba9142e952811473fba6caa3a053ff1e2605512fd9',
}


def need(condition, reason):
    if not condition:
        raise ResourceBusy(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def read_pin(ref, limit=32 * 1024**2):
    p = Path(ref['path'])
    need(p.is_absolute() and str(p.resolve(strict=True)) == str(p), 'CANONICAL_ABSOLUTE_PIN_REQUIRED')
    with os.fdopen(os.open(p, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as f:
        before = os.fstat(f.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_size <= limit, 'REGULAR_BOUNDED_PIN_REQUIRED')
        raw = f.read(limit + 1)
        after = os.fstat(f.fileno())
    fresh = p.stat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    need(identity(before) == identity(after) == identity(fresh), 'PIN_IDENTITY_CHANGED')
    need(len(raw) == ref['bytes'] and hashlib.sha256(raw).hexdigest() == ref['sha256'], 'PIN_CHANGED')
    return raw


def pin(p):
    p = Path(p).resolve(strict=True)
    raw = p.read_bytes()
    return dict(path=str(p), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def pairs(items):
    result = {}
    for k, v in items:
        need(k not in result, 'DUPLICATE_JSON_KEY')
        result[k] = v
    return result


def object_bytes(raw):
    def invalid(_):
        raise ResourceBusy('INVALID_JSON_CONSTANT')
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=invalid)
    need(isinstance(value, dict), 'JSON_OBJECT_REQUIRED')
    return value


def native_identity():
    import cmux_workspace_guard
    snapshot = cmux_workspace_guard.caller_snapshot()
    caller, proof = snapshot[0]['caller'], snapshot[-1]
    thread = (proof or {}).get('foreground', {}).get('thread_id')
    need(bool(thread), 'AUTHENTICATED_FOREGROUND_THREAD_REQUIRED')
    return dict(workspace_uuid=caller['workspace_id'], surface_uuid=caller['surface_id'],
                pane_uuid=caller['pane_id'], session_id=thread)


def actor_matches(expected, actual):
    keys = ('workspace_uuid', 'surface_uuid', 'session_id')
    if 'pane_uuid' in expected:
        keys += ('pane_uuid',)
    try:
        return all(str(uuid.UUID(expected[k])) == str(uuid.UUID(actual[k])) for k in keys)
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def locator_record(ref):
    p = Path(ref['path'])
    need(p.is_absolute() and str(p.resolve(strict=True)) == str(p), 'NATIVE_APPROVAL_PATH_REQUIRED')
    size, offset = ref['bytes'], ref['offset']
    need(type(size) is int and 0 < size <= 1024**2 and type(offset) is int and offset >= 0, 'BOUNDED_APPROVAL_LOCATOR_REQUIRED')
    with os.fdopen(os.open(p, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as f:
        s = os.fstat(f.fileno())
        need(stat.S_ISREG(s.st_mode) and [s.st_dev, s.st_ino] == ref['identity'], 'NATIVE_APPROVAL_IDENTITY_CHANGED')
        if offset:
            f.seek(offset - 1)
            need(f.read(1) == b'\n', 'APPROVAL_RECORD_BOUNDARY_REQUIRED')
        f.seek(offset)
        raw = f.read(size)
    need(raw.endswith(b'\n') and hashlib.sha256(raw).hexdigest() == ref['sha256'], 'NATIVE_APPROVAL_PIN_CHANGED')
    return object_bytes(raw)


def approved_plan(contract, *, root=None):
    """Bind the original native user approval to its preceding exact plan."""
    if contract.get('schema') == NATIVE_REVISION_SCHEMA:
        import word_native_approval
        word_native_approval.verify_native_disposition(contract, root=root)
        return
    need(actor_matches(contract['administrator'],contract['successor']), 'EXPLICIT_SUCCESSOR_ADMINISTRATION_REQUIRED')
    a, p = contract['approval'], contract['approved_plan']
    need(a['path'] == p['path'] and p['offset'] + p['bytes'] <= a['offset'], 'ORIGINAL_PLAN_APPROVAL_ORDER_REQUIRED')
    need(a['offset'] - p['offset'] <= 1024**2, 'APPROVAL_PLAN_BOUNDARY_TOO_LARGE')
    user, plan = locator_record(a), locator_record(p)
    up, pp = user.get('payload', {}), plan.get('payload', {})
    need(user.get('type') == plan.get('type') == 'response_item' and up.get('type') == pp.get('type') == 'message', 'NATIVE_MESSAGE_APPROVAL_REQUIRED')
    text = ''.join(x.get('text', '') for x in up.get('content', []) if x.get('type') == 'input_text')
    plan_text = ''.join(x.get('text', '') for x in pp.get('content', []) if x.get('type') == 'output_text')
    need(up.get('role') == 'user' and text.strip() == 'Implement the plan.' and pp.get('role') == 'assistant', 'EXPLICIT_USER_PLAN_APPROVAL_REQUIRED')
    need(contract['original_owner_session_id'] in plan_text, 'APPROVED_ORIGINAL_OWNER_REQUIRED')
    need(contract['original_owner']['workspace_uuid'] in plan_text
         and contract['successor']['workspace_uuid'] == contract['original_owner']['workspace_uuid'], 'APPROVED_ORIGINAL_WORKSPACE_REQUIRED')
    need('核收旧 Word 资源票' in plan_text and '不保存或关闭归属不明的' in plan_text,
         'APPROVED_PRESERVING_WORD_HANDOFF_REQUIRED')
    need(contract['successor']['session_id'] in Path(a['path']).name, 'APPROVED_SUCCESSOR_SESSION_REQUIRED')
    if contract['scope'] == REVISION_SCOPE:
        need(all('pane_uuid' in contract[k] for k in ('administrator', 'successor')),
             'EXACT_REVISION_PANE_REQUIRED')
        # Reuse an existing complete native manuscript plan. PDF-only approval
        # cannot acquire write/accept/reject capabilities by changing a field.
        need(all(text in plan_text for text in ('修订记录稿', '干净 DOCX', 'Word 原生导出 PDF')),
             'APPROVED_OWNED_REVISION_DELIVERABLES_REQUIRED')
        job = object_bytes(read_pin(contract['initial_job']))
        need(job['schema'] == 'owned-initial-word-job-v1'
             and actor_matches(contract['successor'], job['actor'])
             and job.get('zotero_refresh') is False,
             'EXACT_APPROVED_INITIAL_JOB_REQUIRED')


def verify_closure(plan, *, running=False):
    """Complete source pins plus the two supported external service members."""
    need(plan['operation'] in (PDF_SCOPE, REVISION_SCOPE), 'RESTRICTED_PDF_SCOPE_REQUIRED')
    need(type(plan['required_free_bytes']) is int and plan['required_free_bytes'] >= FLOOR, 'WORD_DISK_FLOOR_REQUIRED')
    source = Path(plan['controller_source'])
    need(source.is_absolute() and source.resolve(strict=True) == source, 'CONTROLLER_SOURCE_REQUIRED')
    closure = plan['controller_pins']
    required = {'word_restricted_broker.py', 'word_restricted_pdf.py', 'resource_broker.py', 'cmux_workspace_guard.py'}
    if plan['operation'] == REVISION_SCOPE:
        required.add('word_restricted_revision.py')
    need(required <= set(closure), 'COMPLETE_CONTROLLER_CLOSURE_REQUIRED')
    # Pin all Python members, not just the driver. The child compiles these
    # bytes directly instead of trusting a potentially unchecked pyc file.
    members = {p.name for p in (source / 'scripts').glob('*.py')}
    need(members == set(closure), 'COMPLETE_CONTROLLER_CLOSURE_REQUIRED')
    for name, ref in closure.items():
        need(Path(ref['path']) == source / 'scripts' / name, 'CONTROLLER_MEMBER_PATH_MISMATCH')
        read_pin(ref)
    need(Path(closure['word_restricted_broker.py']['path']) == Path(__file__).resolve(), 'ORIGINAL_RESTRICTED_CONTROLLER_REQUIRED')
    service = plan['service_pins']
    need(set(service) == set(SERVICE_PINS), 'SUPPORTED_PDF_SERVICE_REQUIRED')
    for name, ref in service.items():
        need(Path(ref['path']).name == name and ref['sha256'] == SERVICE_PINS[name], 'SUPPORTED_PDF_SERVICE_REQUIRED')
        read_pin(ref)
    need(Path(service['word_runtime.py']['path']).parent == Path(service['word_pdf_service.py']['path']).parent,
         'PDF_SERVICE_CLOSURE_MISMATCH')
    validators = plan['validator_pins']
    need(set(validators) == set(VALIDATOR_PINS), 'SUPPORTED_PDF_VALIDATORS_REQUIRED')
    for name, ref in validators.items():
        need(Path(ref['path']).name == name and ref['sha256'] == VALIDATOR_PINS[name]
             and os.access(ref['path'],os.X_OK), 'SUPPORTED_PDF_VALIDATORS_REQUIRED')
        read_pin(ref)
    read_pin(plan['input'])
    output = Path(plan['output'])
    need(output.is_absolute() and output.parent.resolve(strict=True) == output.parent and output.suffix.lower() == '.pdf', 'CANONICAL_NEW_PDF_PATH_REQUIRED')
    need(not output.is_symlink() and (running or not output.exists()), 'OUTPUT_EXISTS_NO_REPLAY')
    evidence = Path(plan['evidence_root'])
    need(evidence.is_absolute() and evidence.parent.resolve(strict=True) == evidence.parent
         and not evidence.is_symlink() and (running or not evidence.exists()), 'NEW_CANONICAL_EVIDENCE_ROOT_REQUIRED')
    lock_paths = {str(Path.home()/'.cache/wordrev/word-automation.lock'),
                  str(Path.home()/'.cmuxterm/locks/desktop-focus.lock')}
    need(set(plan['locks']) == lock_paths, 'COMPLETE_ORIGINAL_LOCK_SCOPE_REQUIRED')
    for path, identity in plan['locks'].items():
        p = Path(path); s = p.lstat()
        need(not p.is_symlink() and stat.S_ISREG(s.st_mode) and [s.st_dev,s.st_ino] == identity, 'ORIGINAL_LOCK_IDENTITY_CHANGED')
    if plan['operation'] == REVISION_SCOPE:
        verify_revision_plan(plan, running=running)


def verify_revision_plan(plan, *, running=False):
    """The fixed child receives paths/data only, never a caller's adapter code."""
    need(set(plan) == {'operation', 'actor', 'controller_source', 'controller_pins', 'service_pins',
         'validator_pins', 'input', 'output', 'evidence_root', 'required_free_bytes', 'locks',
         'initial_job', 'revision_outputs', 'stage_id', 'start_word_if_absent', 'directory_identities'},
         'INITIAL_PLAN_FIELDS_REQUIRED')
    need('pane_uuid' in plan['actor'], 'EXACT_REVISION_PANE_REQUIRED')
    job = object_bytes(read_pin(plan['initial_job']))
    need(set(job) <= {'schema', 'task_id', 'actor', 'run_dir', 'input', 'outputs', 'pdf_output',
         'native_actions', 'zotero_refresh', 'final_paper_acceptance', 'validation_inputs', 'source_job',
         'tracked_validation'},
         'INITIAL_JOB_FIELDS_REQUIRED')
    if 'tracked_validation' in job:
        import word_semantic_contract
        word_semantic_contract.validate_policy(job['tracked_validation'])
    need(job.get('schema') == 'owned-initial-word-job-v1' and bool(job.get('task_id'))
         and actor_matches(plan['actor'], job['actor']), 'INITIAL_JOB_OWNER_REQUIRED')
    need(job.get('native_actions') == ['PRESERVE_TRACKED', 'ACCEPT_ALL', 'REJECT_ALL', 'PDF_EXPORT']
         and job.get('zotero_refresh') is False and job.get('final_paper_acceptance') is False,
         'INITIAL_REVISION_CAPABILITIES_REQUIRED')
    need(job['input'] == plan['input'], 'INITIAL_JOB_INPUT_REQUIRED')
    run = Path(job['run_dir'])
    need(run.is_absolute() and run.resolve(strict=True) == run and run.is_dir()
         and run.stat().st_uid == os.getuid(), 'CANONICAL_OWNED_RUN_DIRECTORY_REQUIRED')
    expected = {label: str(run / 'artifacts' / (label + '.docx'))
                for label in ('tracked', 'accepted', 'rejected')}
    need(job.get('outputs') == plan.get('revision_outputs') == expected,
         'EXACT_INITIAL_REVISION_OUTPUTS_REQUIRED')
    need(job.get('pdf_output') == plan['output'] == str(run / 'evidence' / 'accepted.pdf')
         and plan['evidence_root'] == str(run / 'evidence' / 'restricted-native'),
         'EXACT_INITIAL_REVISION_EVIDENCE_REQUIRED')
    for path in expected.values():
        p = Path(path)
        need(p.parent.resolve(strict=True) == p.parent and not p.is_symlink()
             and (running or not p.exists()), 'REVISION_OUTPUT_EXISTS_NO_REPLAY')
    stage_id = plan.get('stage_id')
    need(isinstance(stage_id, str) and uuid.UUID(stage_id).hex == stage_id,
         'EXACT_INITIAL_STAGING_ID_REQUIRED')
    stage = Path.home() / 'Library/Containers/com.microsoft.Word/Data/Documents/wordrev-pdf' / ('revision_' + stage_id)
    need(stage.parent.resolve(strict=True) == stage.parent and not stage.is_symlink() and (running or not stage.exists()),
         'REVISION_STAGING_EXISTS_NO_REPLAY')
    expected_dirs = {str(run), str(run / 'artifacts'), str(run / 'evidence'), str(stage.parent)}
    need(set(plan['directory_identities']) == expected_dirs, 'COMPLETE_INITIAL_DIRECTORY_IDENTITIES_REQUIRED')
    for path, identity in plan['directory_identities'].items():
        p = Path(path); s = p.lstat()
        need(stat.S_ISDIR(s.st_mode) and not p.is_symlink() and p.resolve(strict=True) == p
             and [s.st_dev, s.st_ino] == identity, 'INITIAL_DIRECTORY_IDENTITY_CHANGED')
    need(type(plan.get('start_word_if_absent')) is bool, 'EXPLICIT_WORD_START_POLICY_REQUIRED')
    need(Path(plan['input']['path']).suffix.lower() == '.docx', 'INITIAL_TRACKED_DOCX_REQUIRED')
    need(set(job.get('validation_inputs', {})) == {'accepted', 'rejected'}, 'INITIAL_VALIDATION_INPUTS_REQUIRED')
    for ref in job['validation_inputs'].values():
        read_pin(ref)
    if 'source_job' in job:
        read_pin(job['source_job'])


class RestrictedWordBroker:
    def __init__(self, root=None, *, readonly=False):
        self.root = Path(root or Path.home() / '.local/state/multi-agent-collaboration/resources').resolve(strict=True)
        self.db = sqlite3.connect((self.root / 'queue.sqlite3').as_uri() + ('?mode=ro' if readonly else '?mode=rw'), uri=True, isolation_level=None, timeout=3)
        self.db.row_factory = sqlite3.Row
        if readonly:
            self.db.execute('PRAGMA query_only=ON')

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def legacy(self, contract):
        row = self.db.execute('SELECT * FROM tickets WHERE id=?', (contract['original_ticket'],)).fetchone()
        need(row is not None, 'ORIGINAL_TICKET_REQUIRED')
        row = dict(row)
        need(digest(row) == contract['original_row_sha256'], 'LEGACY_PREIMAGE_CHANGED')
        owner = contract['original_owner']
        need(owner['workspace_uuid'] == row['workspace_uuid'] and owner['surface_uuid'] == row['surface_uuid']
             and owner['session_id'] == contract['original_owner_session_id'], 'ORIGINAL_TICKET_OWNER_SCOPE_REQUIRED')
        need(row['resource'] == RESOURCE and row['status'] == 'ACTIVE' and type(row['expires']) in (int, float)
             and math.isfinite(row['expires']) and row['expires'] <= time.time(), 'EXPIRED_ACTIVE_QUARANTINE_REQUIRED')
        need(row['pgid'] is None and type(row['invocations']) is int and row['invocations'] > 0, 'ORIGINAL_CHILD_DRAIN_REQUIRED')
        resource = dict(self.db.execute('SELECT * FROM resources WHERE name=?', (RESOURCE,)).fetchone())
        need(digest(resource) == contract['original_resource_sha256'], 'LEGACY_RESOURCE_MAPPING_CHANGED')
        need(len(contract['terminal_receipts']) == row['invocations'], 'COMPLETE_ORIGINAL_TERMINALS_REQUIRED')
        for ref in contract['terminal_receipts']:
            terminal = object_bytes(read_pin(ref))
            need(terminal.get('process_group_empty') is True and type(terminal.get('pgid')) is int,
                 'ORIGINAL_TERMINAL_DRAIN_REQUIRED')
            need(not group_alive(terminal['pgid']), 'ORIGINAL_GROUP_ALIVE_OR_UNKNOWN')
        service = object_bytes(read_pin(contract['accepted_export']))
        need(service.get('success') is True and service.get('cleanup') == 'PASS'
             and service.get('input_unchanged') is True and service.get('staged_docx_unchanged') is True, 'ORIGINAL_EXPORT_CLEANUP_REQUIRED')
        read_pin(contract['accepted_export_input']); read_pin(contract['accepted_export_pdf'])
        need(contract['accepted_export_input']['path'] == service.get('input')
             and contract['accepted_export_input']['sha256'] == service.get('input_sha256')
             and contract['accepted_export_pdf']['path'] == service.get('pdfPath')
             and contract['accepted_export_pdf']['sha256'] == service.get('pdf_sha256'), 'ACCEPTED_ORIGINAL_EXPORT_BINDING_REQUIRED')
        need(not Path(service['staging_dir']).exists(), 'ORIGINAL_STAGING_NOT_DRAINED')
        need(not (Path.home() / '.cache/wordrev/word-automation.pending.json').exists(), 'WORD_PENDING_PRESERVED')
        return row, resource

    def preview(self, ref):
        contract = object_bytes(read_pin(ref))
        need((contract['schema'], contract['scope']) in (
             ('word-restricted-disposition-v1', PDF_SCOPE),
             ('word-restricted-disposition-v2', REVISION_SCOPE),
             (NATIVE_REVISION_SCHEMA, REVISION_SCOPE)), 'RESTRICTED_DISPOSITION_REQUIRED')
        approved_plan(contract, root=self.root)
        if contract['schema'] == NATIVE_REVISION_SCHEMA:
            import word_native_approval
            word_native_approval.verify_applied_native_parent(self.db, contract)
        self.legacy(contract)
        return dict(outcome='RESTRICTED_WORD_READ_ONLY_PLAN', plan_sha256=digest(dict(contract=ref, legacy=contract['original_row_sha256'])),
                    contract=ref, input_operations=0, queue_changes=0, legacy_released=False, foreign_saved='UNKNOWN')

    def apply(self, ref, plan_sha256):
        contract = object_bytes(read_pin(ref))
        need(actor_matches(contract['administrator'], native_identity()), 'ORIGINAL_ADMINISTRATOR_REQUIRED')
        need(self.preview(ref)['plan_sha256'] == plan_sha256, 'EXACT_DISPOSITION_PLAN_REQUIRED')
        with os_lock(self.root / (hashlib.sha256(RESOURCE.encode()).hexdigest() + '.lock')):
            with self.transaction():
                _row, resource = self.legacy(contract)
                if contract['schema'] == NATIVE_REVISION_SCHEMA:
                    import word_native_approval
                    word_native_approval.verify_applied_native_parent(self.db, contract)
                for path in json.loads(resource['external_locks']):
                    with os_lock(path):
                        pass
                self.db.execute('CREATE TABLE IF NOT EXISTS word_restricted_dispositions (id TEXT PRIMARY KEY, original_ticket TEXT UNIQUE NOT NULL, contract TEXT NOT NULL, applied REAL NOT NULL)')
                self.db.execute('CREATE TABLE IF NOT EXISTS word_restricted_tickets (id TEXT PRIMARY KEY, disposition TEXT NOT NULL, plan TEXT NOT NULL, status TEXT NOT NULL, requested REAL NOT NULL, token TEXT, expires REAL, nonce TEXT, parent_pid INTEGER, pgid INTEGER, invocations INTEGER NOT NULL DEFAULT 0, result TEXT)')
                self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_restricted_word_writer ON word_restricted_tickets((1)) WHERE status IN ('ACTIVE','PENDING')")
                identity = str(uuid.uuid4())
                if contract['scope'] == REVISION_SCOPE:
                    # Each reviewed new job has a separate create-only authority.
                    # Old PDF dispositions and their UNIQUE(original_ticket)
                    # rule stay intact; future owned jobs do not require changing
                    # a frozen disposition or releasing the quarantined ticket.
                    self.db.execute('CREATE TABLE IF NOT EXISTS word_restricted_revision_dispositions (id TEXT PRIMARY KEY, original_ticket TEXT NOT NULL, job_sha256 TEXT UNIQUE NOT NULL, contract TEXT UNIQUE NOT NULL, applied REAL NOT NULL)')
                    need(self.db.execute('SELECT 1 FROM word_restricted_revision_dispositions WHERE job_sha256=?',
                         (contract['initial_job']['sha256'],)).fetchone() is None, 'INITIAL_JOB_AUTHORIZATION_EXISTS_NO_REPLAY')
                    self.db.execute('INSERT INTO word_restricted_revision_dispositions VALUES(?,?,?,?,?)',
                         (identity, contract['original_ticket'], contract['initial_job']['sha256'], json.dumps(ref), time.time()))
                else:
                    need(self.db.execute('SELECT 1 FROM word_restricted_dispositions WHERE original_ticket=?', (contract['original_ticket'],)).fetchone() is None, 'ORIGINAL_DISPOSITION_EXISTS_NO_REPLAY')
                    self.db.execute('INSERT INTO word_restricted_dispositions VALUES(?,?,?,?)', (identity, contract['original_ticket'], json.dumps(ref), time.time()))
        return dict(disposition_id=identity, legacy_released=False, generic_consumers_unblocked=False, foreign_saved='UNKNOWN')

    def contract(self, disposition):
        rows = list(self.db.execute('SELECT contract FROM word_restricted_dispositions WHERE id=?', (disposition,)))
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='word_restricted_revision_dispositions'").fetchone():
            rows.extend(self.db.execute('SELECT contract FROM word_restricted_revision_dispositions WHERE id=?', (disposition,)))
        need(len(rows) == 1, 'APPLIED_RESTRICTED_DISPOSITION_REQUIRED')
        value = object_bytes(read_pin(json.loads(rows[0][0])))
        approved_plan(value, root=self.root)
        if value['schema'] == NATIVE_REVISION_SCHEMA:
            import word_native_approval
            word_native_approval.verify_applied_native_parent(self.db, value)
        self.legacy(value)
        return value

    def request(self, disposition, plan_ref):
        contract = self.contract(disposition)
        plan = object_bytes(read_pin(plan_ref))
        need(actor_matches(contract['successor'], plan['actor']) and actor_matches(plan['actor'], native_identity()), 'ORIGINAL_AUTHORIZED_SUCCESSOR_REQUIRED')
        self.plan_scope(contract, plan)
        verify_closure(plan)
        with self.transaction():
            self.contract(disposition)
            if plan['operation'] == REVISION_SCOPE:
                need(self.db.execute('SELECT 1 FROM word_restricted_tickets WHERE disposition=?',
                     (disposition,)).fetchone() is None, 'INITIAL_JOB_ALREADY_REQUESTED_NO_REPLAY')
            need(self.db.execute("SELECT 1 FROM word_restricted_tickets WHERE plan=?", (json.dumps(plan_ref),)).fetchone() is None, 'ORIGINAL_PLAN_ALREADY_REQUESTED')
            identity = str(uuid.uuid4())
            self.db.execute("INSERT INTO word_restricted_tickets(id,disposition,plan,status,requested) VALUES(?,?,?,'WAITING',?)", (identity, disposition, json.dumps(plan_ref), time.time()))
        return self.get(identity)

    def get(self, ticket):
        row = self.db.execute('SELECT * FROM word_restricted_tickets WHERE id=?', (ticket,)).fetchone()
        need(row is not None, 'RESTRICTED_TICKET_REQUIRED')
        return dict(row)

    @staticmethod
    def plan_scope(contract, plan):
        need(plan['operation'] == contract['scope'], 'EXACT_DISPOSITION_OPERATION_REQUIRED')
        if plan['operation'] == REVISION_SCOPE:
            need(plan['initial_job'] == contract['initial_job'], 'EXACT_DISPOSITION_INITIAL_JOB_REQUIRED')
        if contract.get('schema') == NATIVE_REVISION_SCHEMA:
            import word_native_approval
            word_native_approval.verify_native_execution_plan(contract, plan)

    def require(self, ticket, token, *, child=False):
        row = self.get(ticket)
        need(row['status'] == 'ACTIVE' and token and row['token'] == token and row['expires'] > time.time(), 'ORIGINAL_RESTRICTED_LEASE_REQUIRED')
        plan = object_bytes(read_pin(json.loads(row['plan'])))
        contract = self.contract(row['disposition'])
        self.plan_scope(contract, plan)
        verify_closure(plan, running=child)
        need(actor_matches(plan['actor'], contract['successor']), 'AUTHORIZED_SUCCESSOR_CHANGED')
        if child:
            need(row['pgid'] == os.getpid() == os.getpgrp() and row['parent_pid'] == os.getppid(), 'ORIGINAL_BROKER_CHILD_REQUIRED')
        else:
            need(actor_matches(plan['actor'], native_identity()), 'ORIGINAL_SUCCESSOR_REQUIRED')
        return row, plan

    def grant(self, ticket, ttl=900):
        need(type(ttl) is int and 30 <= ttl <= 1800, 'BOUNDED_RESTRICTED_LEASE_REQUIRED')
        row = self.get(ticket)
        plan = object_bytes(read_pin(json.loads(row['plan'])))
        need(actor_matches(plan['actor'], native_identity()), 'ORIGINAL_SUCCESSOR_REQUIRED')
        contract = self.contract(row['disposition']); self.plan_scope(contract, plan); verify_closure(plan)
        with os_lock(self.root / (hashlib.sha256(RESOURCE.encode()).hexdigest() + '.lock')):
            for path in plan['locks']:
                with os_lock(path):
                    pass
            with self.transaction():
                self.plan_scope(self.contract(row['disposition']), plan)
                verify_closure(plan)
                need(self.db.execute("SELECT 1 FROM word_restricted_tickets WHERE status IN ('ACTIVE','PENDING')").fetchone() is None, 'RESTRICTED_WORD_WRITER_BUSY')
                first = self.db.execute("SELECT id FROM word_restricted_tickets WHERE status='WAITING' ORDER BY requested,id LIMIT 1").fetchone()
                need(first is not None and first[0] == ticket and row['status'] == 'WAITING', 'RESTRICTED_WORD_FIFO_REQUIRED')
                self.db.execute("UPDATE word_restricted_tickets SET status='ACTIVE',token=?,expires=? WHERE id=?", (str(uuid.uuid4()), time.time()+ttl, ticket))
        return self.get(ticket)

    def run(self, ticket, token):
        """No caller-controlled argv, environment or runner is accepted."""
        row, plan = self.require(ticket, token)
        with os_lock(self.root / (hashlib.sha256(RESOURCE.encode()).hexdigest() + '.lock')):
            with self.transaction():
                row, plan = self.require(ticket, token)
                need(row['invocations'] == 0 and row['nonce'] is None and row['pgid'] is None, 'ORIGINAL_RUN_EXISTS_NO_REPLAY')
                nonce = str(uuid.uuid4())
                self.db.execute('UPDATE word_restricted_tickets SET invocations=1,nonce=?,parent_pid=? WHERE id=?', (nonce, os.getpid(), ticket))
            entrypoint = 'word_restricted_revision.py' if plan['operation'] == REVISION_SCOPE else 'word_restricted_pdf.py'
            argv = [str(Path(sys.executable).resolve()), '-BE', str(Path(plan['controller_source'])/'scripts'/entrypoint),
                    '--root', str(self.root), '--ticket', ticket, '--token', token, '--nonce', nonce]
            try:
                proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                self.db.execute('UPDATE word_restricted_tickets SET pgid=? WHERE id=?', (proc.pid, ticket))
                out, err = proc.communicate(timeout=max(1, row['expires']-time.time()+30))
                result = dict(exit_code=proc.returncode, stdout=out.decode('utf-8', 'replace'), stderr=err.decode('utf-8', 'replace'), pgid=proc.pid, process_group_empty=not group_alive(proc.pid), original_argv=argv)
                success = proc.returncode == 0 and result['process_group_empty']
                if success:
                    report = object_bytes(read_pin(pin(Path(plan['evidence_root'])/'restricted-result.json')))
                    success = (report.get('ticket') == ticket and report.get('nonce') == nonce and report.get('owned_cleanup') is True
                               and report.get('foreign_observation_preserved') is True and report.get('pending') is False
                               and report.get('plan') == json.loads(row['plan']) and report.get('input') == plan['input']
                               and report.get('pdf',{}).get('path') == plan['output'])
                    if success:
                        read_pin(report['pdf'])
                        if plan['operation'] == REVISION_SCOPE:
                            success = (report.get('operation') == REVISION_SCOPE
                                       and report.get('initial_job') == plan['initial_job']
                                       and set(report.get('revision_outputs', {})) == {'tracked', 'accepted', 'rejected'}
                                       and report.get('zotero_refreshed') is False)
                            if success:
                                for label, ref in report['revision_outputs'].items():
                                    need(ref['path'] == plan['revision_outputs'][label], 'REVISION_REPORT_PATH_CHANGED')
                                    read_pin(ref)
                        verify_closure(plan,running=True)
                        self.contract(row['disposition'])
                status = 'COMPLETE_WITH_FOREIGN_UNKNOWN' if success else 'PENDING'
            except BaseException as exc:
                result = dict(error_type=type(exc).__name__, error=str(exc), no_retry=True, process_group_empty=False)
                status = 'PENDING'
            # A child error or uncertain communicate never kills Word, clears
            # a pending file, refunds the invocation or reopens the queue.
            with self.transaction():
                self.db.execute('UPDATE word_restricted_tickets SET status=?,result=? WHERE id=?', (status, json.dumps(result), ticket))
            return dict(status=status, legacy_released=False, documents_verified=False, result=result)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('preview','apply','request','grant','run','status'))
    parser.add_argument('--root'); parser.add_argument('--file'); parser.add_argument('--sha256')
    parser.add_argument('--plan-sha256'); parser.add_argument('--disposition'); parser.add_argument('--ticket'); parser.add_argument('--token')
    args = parser.parse_args(argv)
    broker = RestrictedWordBroker(args.root, readonly=args.command in ('preview','status'))
    try:
        ref = pin(args.file) if args.file else None
        if ref:
            need(ref['sha256'] == args.sha256, 'EXACT_INPUT_FILE_SHA_REQUIRED')
        if args.command == 'preview': result = broker.preview(ref)
        elif args.command == 'apply': result = broker.apply(ref,args.plan_sha256)
        elif args.command == 'request': result = broker.request(args.disposition,ref)
        elif args.command == 'grant': result = broker.grant(args.ticket)
        elif args.command == 'run': result = broker.run(args.ticket,args.token)
        else: result = broker.get(args.ticket)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if result.get('status') != 'PENDING' else 2
    except (ResourceBusy,ValueError,TypeError,KeyError,OSError,sqlite3.Error,RecursionError) as exc:
        print(json.dumps(dict(outcome='RESTRICTED_WORD_REFUSED',reason=str(exc),legacy_released=False),ensure_ascii=False))
        return 2
    finally:
        broker.close()


if __name__ == '__main__':
    raise SystemExit(main())
