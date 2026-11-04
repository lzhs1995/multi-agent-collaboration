#!/usr/bin/env python3
"""Read existing restricted Word bindings; never grant, run, release or repair.

Use Python -B, including callers importing this file. The CLI always reads the
canonical global resource root; no environment/root override or arbitrary argv
is accepted. Matching declarations are not native identity or authorization.
Original controllers retain every live check, approval, budget and recovery.
"""
import sys
sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import time
import uuid

SCHEMA = 'federated-resource-preflight-v1'
ROOT = Path.home() / '.local/state/multi-agent-collaboration/resources'
RESOURCE = 'word-zotero'
OPERATIONS = {'OWNED_PDF_ONLY', 'OWNED_REVISION_ROUNDTRIP'}
MAX_FILE = 32 * 1024 * 1024
MAX_CLOSURE = 64 * 1024 * 1024
MAX_WAL = 64 * 1024 * 1024


class PreflightError(ValueError):
    pass


def require(value, reason):
    if not value:
        raise PreflightError(reason)


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    def invalid(_):
        raise PreflightError('NONFINITE_JSON')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def canonical_path(value):
    require(isinstance(value, str) and '\0' not in value, 'INVALID_PATH')
    path = Path(value)
    require(path.is_absolute() and str(path.resolve(strict=True)) == value,
            'CANONICAL_EXISTING_PATH_REQUIRED')
    return path


def read_file(path, limit=MAX_FILE):
    path = canonical_path(str(path))
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_size <= limit, 'BOUNDED_REGULAR_FILE_REQUIRED')
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    require(identity(before) == identity(after) == identity(path.stat()), 'FILE_CHANGED_DURING_READ')
    require(len(raw) <= limit, 'FILE_LIMIT')
    return raw


def read_pin(ref):
    require(isinstance(ref, dict) and set(ref) == {'path', 'bytes', 'sha256'}, 'EXACT_FILE_PIN_REQUIRED')
    require(type(ref['bytes']) is int and 0 <= ref['bytes'] <= MAX_FILE and
            isinstance(ref['sha256'], str) and re.fullmatch('[0-9a-f]{64}', ref['sha256']), 'INVALID_FILE_PIN')
    raw = read_file(ref['path'])
    require(len(raw) == ref['bytes'] and hashlib.sha256(raw).hexdigest() == ref['sha256'], 'FILE_PIN_CHANGED')
    return raw


def object_pin(ref):
    value = decode(read_pin(ref))
    require(isinstance(value, dict), 'OBJECT_REQUIRED')
    return value


def actor(value):
    required = {'workspace_uuid', 'surface_uuid', 'session_id'}
    require(isinstance(value, dict) and required <= value.keys() and
            value.keys() <= required | {'pane_uuid'}, 'EXACT_NATIVE_ACTOR_FIELDS_REQUIRED')
    try:
        return {key: str(uuid.UUID(item)) for key, item in value.items()}
    except (ValueError, TypeError, AttributeError) as exc:
        raise PreflightError('INVALID_NATIVE_ACTOR_UUID') from exc


def result(status, reason, **facts):
    return dict(schema=SCHEMA, status=status, reason=reason, **facts,
        input_sent=False, application_invoked=False, queue_changed=False,
        lease_granted=False, execution_authorized=False, native_executor_verified=False,
        original_approval_verified=False, accepted=False,
        original_controller_recheck_required=True)


def file_image(path, limit):
    """Capture bytes plus metadata, rejecting a changing file or symlink."""
    if not path.exists() and not path.is_symlink():
        return None
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode), 'REGULAR_QUEUE_IMAGE_REQUIRED')
    raw = read_file(path, limit)
    after = path.lstat()
    metadata = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    require(metadata(before) == metadata(after), 'QUEUE_CHANGED_DURING_SNAPSHOT')
    return {'bytes': raw, 'metadata': metadata(after), 'sha256': hashlib.sha256(raw).hexdigest()}


def queue_images(database):
    return {suffix: file_image(Path(str(database) + suffix), limit)
            for suffix, limit in (('', MAX_FILE), ('-wal', MAX_WAL), ('-journal', MAX_WAL))}


def stable_queue_images(database):
    # Never open the source through sqlite: even mode=ro can touch its SHM mtime.
    # Include committed WAL pages; immutable=1 on the source would ignore them.
    first, second = queue_images(database), queue_images(database)
    require(first == second, 'QUEUE_CHANGED_DURING_SNAPSHOT')
    require(first[''] is not None, 'EXISTING_QUEUE_REQUIRED')
    require(first['-journal'] is None or not first['-journal']['bytes'],
            'ROLLBACK_JOURNAL_REQUIRES_ORIGINAL_CONTROLLER')
    return first


def existing(ticket_id, expected_root):
    """Read one transaction; never instantiate a broker (its constructor writes)."""
    require(isinstance(ticket_id, str), 'TICKET_ID_REQUIRED')
    try:
        uuid.UUID(ticket_id)
    except ValueError as exc:
        raise PreflightError('INVALID_TICKET_ID') from exc
    root = canonical_path(str(expected_root))
    require(root.is_dir(), 'RESOURCE_ROOT_DIRECTORY_REQUIRED')
    database = canonical_path(str(root / 'queue.sqlite3'))
    require(stat.S_ISREG(database.stat().st_mode), 'REGULAR_QUEUE_REQUIRED')
    identity = (database.stat().st_dev, database.stat().st_ino)
    images = stable_queue_images(database)
    with tempfile.TemporaryDirectory(prefix='resource-preflight-') as temporary:
        copied = Path(temporary) / 'queue.sqlite3'
        for suffix in ('', '-wal'):
            if images[suffix] is not None:
                Path(str(copied) + suffix).write_bytes(images[suffix]['bytes'])
        db = sqlite3.connect(copied.as_uri() + '?mode=ro', uri=True,
                             isolation_level=None, timeout=3)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            require('word_restricted_tickets' in names and 'resources' in names,
                    'EXISTING_RESTRICTED_QUEUE_REQUIRED')
            row = db.execute('SELECT * FROM word_restricted_tickets WHERE id=?', (ticket_id,)).fetchone()
            require(row is not None, 'EXISTING_RESTRICTED_TICKET_REQUIRED')
            row = dict(row)
            contracts = []
            for table in ('word_restricted_dispositions', 'word_restricted_revision_dispositions'):
                if table in names:
                    contracts += [dict(item) for item in db.execute('SELECT * FROM ' + table + ' WHERE id=?', (row['disposition'],))]
            require(len(contracts) == 1, 'ONE_EXISTING_DISPOSITION_REQUIRED')
            resource = db.execute('SELECT * FROM resources WHERE name=?', (RESOURCE,)).fetchone()
            require(resource is not None, 'EXISTING_GLOBAL_RESOURCE_REQUIRED')
            db.execute('ROLLBACK')
        finally:
            db.close()
    require(images == queue_images(database), 'QUEUE_CHANGED_DURING_SNAPSHOT')
    require(identity == (database.stat().st_dev, database.stat().st_ino), 'QUEUE_IDENTITY_CHANGED')
    return row, contracts[0], dict(resource), identity


def inspect(ticket_id, *, expected_root=ROOT):
    row, disposition, resource, identity = existing(ticket_id, expected_root)
    contract_ref = decode(disposition['contract'])
    plan_ref = decode(row['plan'])
    plan, contract = object_pin(plan_ref), object_pin(contract_ref)
    require(disposition.get('original_ticket') == contract.get('original_ticket') and
            isinstance(contract.get('original_ticket'), str), 'ORIGINAL_DISPOSITION_TICKET_MISMATCH')
    require(contract.get('original_resource_sha256') == digest(resource), 'ORIGINAL_RESOURCE_MAPPING_CHANGED')
    require(plan.get('operation') in OPERATIONS and plan['operation'] == contract.get('scope'),
            'ORIGINAL_OPERATION_MISMATCH')
    require((contract.get('schema'), plan['operation']) in {
        ('word-restricted-disposition-v1', 'OWNED_PDF_ONLY'),
        ('word-restricted-disposition-v2', 'OWNED_REVISION_ROUNDTRIP'),
        ('word-restricted-native-successor-disposition-v1', 'OWNED_REVISION_ROUNDTRIP')},
        'ORIGINAL_DISPOSITION_SCHEMA_MISMATCH')
    original_actor = actor(plan.get('actor'))
    require(original_actor == actor(contract.get('successor')), 'ORIGINAL_EXECUTOR_MISMATCH')
    source = canonical_path(plan.get('controller_source'))
    closure = plan.get('controller_pins')
    require(isinstance(closure, dict) and 0 < len(closure) <= 1024, 'CONTROLLER_CLOSURE_REQUIRED')
    members = {p.name for p in (source / 'scripts').glob('*.py')}
    needed = {'word_restricted_broker.py', 'word_restricted_pdf.py', 'resource_broker.py', 'cmux_workspace_guard.py'}
    if plan['operation'] == 'OWNED_REVISION_ROUNDTRIP':
        needed.add('word_restricted_revision.py')
    require(members == closure.keys() and needed <= members, 'COMPLETE_ORIGINAL_CONTROLLER_REQUIRED')
    total = 0
    for name, ref in closure.items():
        require(isinstance(ref, dict) and ref.get('path') == str(source / 'scripts' / name), 'CONTROLLER_MEMBER_PATH_MISMATCH')
        require(type(ref.get('bytes')) is int, 'INVALID_FILE_PIN')
        total += ref['bytes']
        require(0 <= total <= MAX_CLOSURE, 'CONTROLLER_CLOSURE_LIMIT')
        read_pin(ref)
    read_pin(plan.get('input'))
    original_task = None
    if plan['operation'] == 'OWNED_REVISION_ROUNDTRIP':
        require(plan.get('initial_job') == contract.get('initial_job'), 'ORIGINAL_JOB_REFERENCE_MISMATCH')
        require(disposition.get('job_sha256') == plan['initial_job'].get('sha256'), 'ORIGINAL_DISPOSITION_JOB_MISMATCH')
        job = object_pin(plan.get('initial_job'))
        require(job.get('schema') == 'owned-initial-word-job-v1' and
                actor(job.get('actor')) == original_actor and job.get('input') == plan.get('input'),
                'ORIGINAL_JOB_BINDING_MISMATCH')
        require(job.get('zotero_refresh') is False and job.get('final_paper_acceptance') is False,
                'UNSUPPORTED_ORIGINAL_JOB_CAPABILITY')
        require(isinstance(job.get('task_id'), str) and bool(job['task_id'].strip()), 'ORIGINAL_JOB_TASK_REQUIRED')
        original_task = job['task_id']
    require(type(row.get('invocations')) is int and row['invocations'] >= 0, 'INVALID_ORIGINAL_INVOCATIONS')
    needs_reconcile = (row['status'] not in ('WAITING', 'ACTIVE') or row['invocations'] > 0
                       or any(row.get(k) is not None for k in ('nonce', 'parent_pid', 'pgid', 'result')))
    if row['status'] == 'ACTIVE':
        expires = row.get('expires')
        needs_reconcile |= (type(expires) not in (int, float) or not math.isfinite(expires)
                            or expires <= time.time())
    facts = dict(resource_root=str(expected_root), resource=RESOURCE,
        database_identity=list(identity), resource_record_sha256=digest(resource),
        ticket_id=row['id'], ticket_sha256=digest(row), disposition_id=row['disposition'],
        ticket_status=row['status'], invocations=row['invocations'], original_task_id=original_task,
        original_executor=original_actor, operation=plan['operation'], plan_ref=plan_ref,
        disposition_ref=contract_ref, disposition_record_sha256=digest(disposition),
        controller_ref=closure['word_restricted_broker.py'],
        input_ref=plan['input'], original_records_match=True, controller_source_bytes_match=True,
        service_and_live_guards_checked=False, task_binding_matched=False,
        global_root_authenticated=False, queue_read_method='stable_source_db_and_wal_copy_then_sqlite_mode_ro',
        next_action='READ_BACK_ORIGINAL_ATTEMPT_WITH_ORIGINAL_CONTROLLER' if needs_reconcile else
                    'SUPPLY_EXACT_EXISTING_TASK_BINDING_THEN_ORIGINAL_CONTROLLER_RECHECK')
    return result('ORIGINAL_RECONCILIATION_REQUIRED' if needs_reconcile else 'ORIGINAL_RECORDS_MATCH',
                  'ORIGINAL_INVOCATION_OR_OUTCOME_MUST_NOT_BE_REPLAYED' if needs_reconcile else
                  'INSPECTION_ALONE_DOES_NOT_BIND_A_NEW_TASK', **facts)


def preflight(request, *, expected_root=ROOT):
    require(isinstance(request, dict) and request.get('schema') == SCHEMA, 'INVALID_REQUEST_SCHEMA')
    allowed = {'schema', 'resource_root', 'resource', 'task_id', 'operation', 'executor', 'existing'}
    require(request.keys() <= allowed, 'UNSUPPORTED_ENTRYPOINT_OR_REQUEST_FIELDS')
    if request.get('resource') != RESOURCE or request.get('operation') not in OPERATIONS:
        return result('UNSUPPORTED_ENTRYPOINT', 'ONLY_EXISTING_RESTRICTED_WORD_OPERATIONS_ARE_INSPECTED')
    require(request.get('resource_root') == str(expected_root) and
            canonical_path(request['resource_root']) == canonical_path(str(expected_root)),
            'GLOBAL_RESOURCE_ROOT_MISMATCH')
    required = ('task_id', 'executor', 'existing')
    missing = [key for key in required if key not in request]
    if missing:
        return result('BINDING_REQUIRED', 'DECLARATION_HAS_NO_COMPLETE_ORIGINAL_RESOURCE_BINDING', missing=missing)
    binding = request['existing']
    fields = {'ticket_id', 'ticket_sha256', 'plan_ref', 'disposition_ref', 'controller_ref'}
    require(isinstance(binding, dict) and binding.keys() <= fields, 'INVALID_EXISTING_BINDING')
    if binding.keys() != fields:
        return result('BINDING_REQUIRED', 'EXACT_EXISTING_RECORD_REFERENCES_REQUIRED', missing=sorted(fields-binding.keys()))
    observed = inspect(binding['ticket_id'], expected_root=expected_root)
    require(observed['operation'] == request['operation'], 'OPERATION_BINDING_MISMATCH')
    require(actor(request['executor']) == observed['original_executor'], 'EXECUTOR_BINDING_MISMATCH')
    for key in ('ticket_sha256', 'plan_ref', 'disposition_ref', 'controller_ref'):
        require(binding[key] == observed[key], 'EXISTING_' + key.upper() + '_MISMATCH')
    if observed['original_task_id'] is None:
        # The PDF-only contract carries no authoritative new business task id.
        if observed['status'] != 'ORIGINAL_RECONCILIATION_REQUIRED':
            observed.update(status='BINDING_REQUIRED', reason='ORIGINAL_PLAN_HAS_NO_BUSINESS_TASK_BINDING')
        return observed
    require(request['task_id'] == observed['original_task_id'], 'ORIGINAL_TASK_BINDING_MISMATCH')
    observed['task_binding_matched'] = True
    if observed['status'] != 'ORIGINAL_RECONCILIATION_REQUIRED':
        observed.update(status='ORIGINAL_CONTROLLER_RECHECK_REQUIRED',
            reason='RECORDS_MATCH_BUT_NATIVE_CALLER_APPROVAL_AND_LIVE_GUARDS_ARE_NOT_CHECKED',
            next_action='ORIGINAL_EXECUTOR_MUST_USE_ORIGINAL_CONTROLLER_WITHOUT_REPLAY')
    return observed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--request', type=Path)
    group.add_argument('--ticket', help='inspect one existing ticket; this does not bind a new task')
    args = parser.parse_args(argv)
    try:
        value = preflight(decode(read_file(args.request, 256*1024))) if args.request else inspect(args.ticket)
    except (PreflightError, OSError, sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        reason = str(exc) if isinstance(exc, PreflightError) else type(exc).__name__
        value = result('UNKNOWN' if reason in ('QUEUE_CHANGED_DURING_SNAPSHOT', 'FILE_CHANGED_DURING_READ')
                       else 'REJECTED', reason)
    print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
    # No successful command exit is an application execution authorization.
    return 0 if value['status'] == 'ORIGINAL_CONTROLLER_RECHECK_REQUIRED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
