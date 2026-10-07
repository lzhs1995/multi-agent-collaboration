"""Read-only closeout evidence, not a delivery receipt or product acceptance.

The existing callback journal freezes the report. Once that attempt returns,
the executor may hand uncertain transport to its supervisor and stop. No network,
polling, receipt creation, disarming, or new completion protocol is needed here.
"""
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def terminal_report(marker, workspace, surface):
    """A frozen terminal attempt for this marker's exact executor, else None.

    Missing, legacy, malformed or in-flight evidence never seals tools. In
    particular, ended_at cannot settle a resumed callback holding delivery.lock.
    """
    pins = {}

    def read(path):
        if not path.is_absolute() or path.is_symlink():
            raise ValueError('noncanonical evidence path')
        raw = path.read_bytes()
        pins[path] = raw
        return raw

    def doc(path):
        value = json.loads(read(path))
        if not isinstance(value, dict):
            raise ValueError('object required')
        return value

    try:
        root = Path(marker['artifact_root'])
        if not root.is_absolute() or marker.get('workspace_uuid', workspace) != workspace:
            return None
        peers = marker['participants']
        executors = [p for p in peers if p.get('surface_uuid') == surface
                     and str(p.get('role', '')).startswith('executor')]
        supervisors = [p for p in peers if p.get('role') == 'supervisor']
        if len(executors) != 1 or len(supervisors) != 1:
            return None
        supervisor = supervisors[0]
        pack_path = root / 'task-pack.json'
        pack = doc(pack_path)
        required = ('task_id', 'executor_uuid', 'completion_nonce', 'completion_callback',
                    'callback_target', 'report', 'completion_receipt')
        if (pack.get('draft') is not False
                or any(not isinstance(pack.get(k), str) or not pack[k] for k in required)
                or pack['task_id'] != marker['task_id'] or pack['executor_uuid'] != surface
                or pack['callback_target'] not in (supervisor.get('surface_ref'),
                                                   supervisor.get('surface_uuid'))):
            return None
        report = Path(pack['report'])
        receipt = Path(pack['completion_receipt'])
        if report.parent != root or receipt.parent != root:
            return None
        raw_report = read(report)
        if not raw_report.strip():
            return None
        journal = receipt.with_name(receipt.stem + '-attempts')
        lock_path = journal / 'delivery.lock'
        # Never create/unlink the persistent lock. Nonblocking shared lock
        # excludes the existing sender's exclusive delivery operation.
        fd = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            opened = os.fstat(lock.fileno())

            def same_inode():
                current = os.lstat(lock_path)
                return (opened.st_dev, opened.st_ino, 1) == (
                    current.st_dev, current.st_ino, current.st_nlink)

            if not same_inode():
                return None
            attempts = sorted(journal.glob('attempt-*.json'))
            if not attempts:
                return None
            attempt = doc(attempts[-1])
            binding = attempt['binding']
            expected = {k: pack[k] for k in ('task_id', 'completion_nonce',
                        'completion_callback', 'callback_target', 'report')}
            expected.update(task_pack_sha256=hashlib.sha256(pins[pack_path]).hexdigest(),
                            report_sha256=hashlib.sha256(raw_report).hexdigest(),
                            report_bytes=len(raw_report))
            identity = binding['identity']
            if (any(binding.get(k) != v for k, v in expected.items())
                    or identity.get('workspace_uuid') != workspace
                    or identity.get('caller_surface_uuid') != surface
                    or identity.get('target_surface_uuid') != supervisor.get('surface_uuid')
                    or not identity.get('target_pane_uuid')):
                return None
            start, end = attempt['started_at_epoch'], attempt['ended_at_epoch']
            if not _number(start) or not _number(end) or not 0 < start <= end:
                return None
            phase = attempt['phase']
            events = attempt['events']
            phases = {'PASTE_INTENT', 'PASTED', 'ENTER_INTENT', 'ENTER_SENT',
                      'POST_ENTER_OBSERVATION', 'EXTRA_ENTER_INTENT',
                      'QUEUE_TAB_INTENT', 'POST_QUEUE_TAB_OBSERVATION'}
            if not isinstance(events, list) or phase not in phases | {'NO_INPUT', 'CONFIRMED'}:
                return None
            previous = start
            for event in events:
                at = event['at_epoch']
                if not _number(at) or not previous <= at <= end or event['phase'] not in phases:
                    return None
                previous = at
                if 'screen' in event or 'screen_sha256' in event:
                    if hashlib.sha256(event['screen'].encode()).hexdigest()[:16] != event['screen_sha256']:
                        return None
            if phase == 'CONFIRMED':
                if attempt.get('result', {}).get('confirmed') is not True or not events:
                    return None
            elif not isinstance(attempt.get('error'), str) or not attempt['error']:
                return None
            if phase == 'NO_INPUT':
                if events:
                    return None
            elif phase != 'CONFIRMED' and (not events or events[-1]['phase'] != phase):
                return None
            if (not same_inode() or sorted(journal.glob('attempt-*.json')) != attempts
                    or any(p.read_bytes() != raw for p, raw in pins.items())):
                return None
            return dict(task_id=pack['task_id'], report=str(report),
                        attempt=str(attempts[-1]), report_sha256=expected['report_sha256'])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def handoff_line(evidence):
    # Exact honest template accepted by Stop; no delivery/quality claim.
    return ('STATUS: REPORT_READY TASK_ID=' + evidence['task_id']
            + ' CALLBACK_UNCONFIRMED REPORT=' + evidence['report']
            + ' supervisor_reconciliation_required')

