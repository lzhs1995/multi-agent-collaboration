"""Original executor's durable work progress; never parent acceptance or input."""
import copy
import hashlib
import json
import os
from pathlib import Path
import time

from cmux_evidence_io import snapshot
import executor_reask as reask
import executor_ready as ready

SCHEMA = 'executor-work-checkpoint-v1'
STATUSES = ('ACCEPTED_FOR_EXECUTION', 'IN_PROGRESS', 'READY_FOR_PARENT_REVIEW', 'EXECUTOR_COMPLETE')
MAX_WORK, MAX_UNITS = 64, 1024


def _text(value, limit=256):
    return isinstance(value, str) and bool(value.strip()) and len(value.encode('utf-8')) <= limit


def pin_file(value):
    if (not isinstance(value, dict) or set(value) != {'path', 'sha256', 'bytes'}
            or type(value.get('bytes')) is not int or not 0 < value['bytes'] <= 8 * 1024 * 1024):
        raise ValueError('WORK_CHECKPOINT_EXACT_FILE_PIN_REQUIRED')
    path = Path(value['path'])
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError('WORK_CHECKPOINT_CANONICAL_FILE_REQUIRED')
    raw, _identity = snapshot(path)
    if len(raw) != value['bytes'] or hashlib.sha256(raw).hexdigest() != value['sha256']:
        raise ValueError('WORK_CHECKPOINT_PIN_CHANGED')
    return copy.deepcopy(value)


def validate(record, state):
    identity = state['executor_identity']
    overall = (state.get('continuation') or {}).get('overall_task', state['task_id'])
    if (not isinstance(record, dict) or record.get('schema') != SCHEMA
            or record.get('caller_surface_uuid') != state['caller_surface_uuid']
            or record.get('executor_session_id') != identity['session_id']
            or record.get('supervisor_uuid') != state['supervisor']
            or record.get('episode_id') != state['episode_id']
            or record.get('task_id') != state['task_id'] or record.get('overall_task') != overall
            or not _text(record.get('work_id')) or record.get('status') not in STATUSES
            or record.get('parent_acceptance') is not None):
        raise ValueError('WORK_CHECKPOINT_ORIGINAL_BINDING_REQUIRED')
    pin_file(record.get('work_order'))
    sources = record.get('evidence', [])
    if not isinstance(sources, list) or len(sources) > 16:
        raise ValueError('WORK_CHECKPOINT_EVIDENCE_BUDGET')
    for value in sources:
        pin_file(value)
    for name in ('completed_unit_ids', 'closed_issue_ids'):
        values = record.get(name, [])
        if (not isinstance(values, list) or len(values) > MAX_UNITS
                or any(not _text(value) for value in values) or len(set(values)) != len(values)):
            raise ValueError('WORK_CHECKPOINT_UNIQUE_PROGRESS_REQUIRED')
    if not _text(record.get('next_action'), 4096):
        raise ValueError('WORK_CHECKPOINT_NEXT_ACTION_REQUIRED')
    return record


def progress(state):
    items = list((state.get('work_checkpoints') or {}).values())
    completed, issues = set(), set()
    statuses = {name: 0 for name in STATUSES}
    for item in items:
        statuses[item['status']] += 1
        completed.update(item.get('completed_unit_ids', []))
        issues.update(item.get('closed_issue_ids', []))
    return dict(schema='executor-work-progress-v1', work_orders_checkpointed=len(items),
        work_statuses=statuses, unique_completed_units_reported=len(completed),
        unique_closed_issues_reported=len(issues), parent_acceptance='UNMEASURED',
        claim_scope='AUTHENTICATED_ORIGINAL_EXECUTOR_SELF_REPORT',
        last_checkpoint_epoch=state.get('work_checkpoint_epoch'), inquiry_count_is_not_progress=True)


def checkpoint(caller, record_file, record_sha256, *, now=None):
    source = Path(record_file)
    if not source.is_absolute() or source.resolve() != source or source.is_symlink():
        raise ValueError('WORK_CHECKPOINT_CANONICAL_RECORD_REQUIRED')
    raw, source_identity = snapshot(source, limit=65536)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != record_sha256:
        raise ValueError('WORK_CHECKPOINT_RECORD_SHA_CHANGED')
    record = json.loads(raw)
    now = time.time() if now is None else now
    from executor_reask_lifecycle import require_epoch
    require_epoch(now, 'checkpoint_epoch')
    with reask.episode_lock(caller):
        state = reask.load(caller)
        if not state:
            return dict(outcome='NO_EPISODE', input_operations=0)
        from executor_reask_protocol import state_controller
        if state_controller(state) != reask.controller():
            return dict(outcome='ORIGINAL_CONTROLLER_REQUIRED', input_operations=0)
        from executor_reask_identity import authenticate
        proof = authenticate(state)
        validate(record, state)
        work = copy.deepcopy(state.get('work_checkpoints') or {})
        old = work.get(record['work_id'])
        if old and old['record_sha256'] == digest:
            return dict(outcome='WORK_CHECKPOINT_ALREADY_RECORDED', progress=progress(state), input_operations=0)
        if state.get('work_checkpoint_epoch') is not None and now < state['work_checkpoint_epoch']:
            raise ValueError('WORK_CHECKPOINT_EPOCH_REGRESSED')
        if not old and len(work) >= MAX_WORK:
            raise ValueError('WORK_CHECKPOINT_WORK_BUDGET_REACHED')
        previous = old['record_sha256'] if old else 'ABSENT'
        if record.get('previous_checkpoint_sha256') != previous:
            raise ValueError('WORK_CHECKPOINT_PREVIOUS_SHA_CHANGED')
        if old and (old['work_order'] != record['work_order']
                    or not set(old.get('completed_unit_ids', [])).issubset(record.get('completed_unit_ids', []))
                    or not set(old.get('closed_issue_ids', [])).issubset(record.get('closed_issue_ids', []))):
            raise ValueError('WORK_CHECKPOINT_ORIGINAL_WORK_OR_PROGRESS_CHANGED')
        if old and STATUSES.index(record['status']) < STATUSES.index(old['status']):
            raise ValueError('WORK_CHECKPOINT_STATUS_REGRESSED')
        directory = reask.root() / 'work-checkpoints' / ready.digest(caller) / ready.digest(state['episode_id'])
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / (digest + '.json')
        archive = dict(schema='executor-work-checkpoint-receipt-v1', checkpoint_epoch=now,
            record_sha256=digest, record=record, source_identity=list(source_identity),
            executor_authentication=proof, parent_acceptance='UNMEASURED', input_operations=0)
        if path.exists():
            existing = ready.read_json(path)
            if (not existing or existing.get('schema') != 'executor-work-checkpoint-receipt-v1'
                    or existing.get('record_sha256') != digest or existing.get('record') != record
                    or existing.get('parent_acceptance') != 'UNMEASURED'
                    or existing.get('input_operations') != 0):
                raise ValueError('WORK_CHECKPOINT_ARCHIVE_CHANGED')
            now = existing['checkpoint_epoch']
            require_epoch(now, 'archived_checkpoint_epoch')
            if state.get('work_checkpoint_epoch') is not None and now < state['work_checkpoint_epoch']:
                raise ValueError('WORK_CHECKPOINT_EPOCH_REGRESSED')
        else:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
            with os.fdopen(fd, 'wb') as h:
                h.write((json.dumps(archive, ensure_ascii=False, sort_keys=True) + '\n').encode())
                h.flush()
                os.fsync(h.fileno())
        # A failed directory sync may leave this original receipt visible.
        # Retry that same directory durability step before committing state.
        ready.sync_directory(directory)
        fresh, identity = snapshot(Path(record_file), limit=65536)
        if fresh != raw or identity != source_identity:
            raise ValueError('WORK_CHECKPOINT_RECORD_CHANGED_DURING_WRITE')
        work[record['work_id']] = dict(record_sha256=digest, archive=str(path),
            status=record['status'], work_order=record['work_order'],
            completed_unit_ids=record.get('completed_unit_ids', []), closed_issue_ids=record.get('closed_issue_ids', []),
            next_action=record['next_action'], checkpoint_epoch=now, previous_checkpoint_sha256=previous)
        state.update(work_checkpoints=work, work_checkpoint_epoch=now)
        reask.save(state)
        return dict(outcome='WORK_CHECKPOINT_RECORDED', checkpoint=str(path),
            record_sha256=digest, progress=progress(state), input_operations=0,
            inquiry_state_changed=False, reservation_released=False)
