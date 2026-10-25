"""Close one proven pre-input owned revision failure, without replay or release."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import time
import uuid

import word_restricted_broker as protocol
from resource_broker import ResourceBusy, group_alive, os_lock

# The reviewed 0.4.70 source rejects this exact binding before launch intent,
# native inventory or document staging. Other controllers/reasons are unknown.
ORIGINAL_RELEASE = '0.4.70-33cef519989c'
ORIGINAL_MANIFEST = '4ecc6030063cb7a2b887d537844b190222766f353b16e48ba12cca113798e664'
ORIGINAL_DRIVERS = {
    'word_restricted_broker.py': '3c5a47a430fb693cd42695988844bf6ec032d8109f8b33c944feaaeb8bfcee2d',
    'word_restricted_pdf.py': '102ccec464b2fd626ab5ce62cbdeb44c3d6d0ba852bac69c4f454616aea0135b',
    'word_restricted_revision.py': 'e4f6b1bd317cc1ec37f0a710ebc019aed78625acc91fe514a1352d40a547196c',
}
PREINPUT_REASON = 'RUNNING_ORIGINAL_ZOTERO_REQUIRED'
TERMINAL = 'FAILED_PREINPUT'


def verify_original_source(plan):
    """Verify the complete original install without importing old code."""
    import executor_reask_protocol as releases
    source = Path(plan['controller_source'])
    expected = Path.home() / '.local/share/multi-agent-collaboration/releases' / ORIGINAL_RELEASE / 'source'
    protocol.need(source == expected and source.resolve(strict=True) == source,
                  'SUPPORTED_ORIGINAL_PREINPUT_CONTROLLER_REQUIRED')
    files = releases._verify_release(source, ORIGINAL_MANIFEST)
    members = {p.name for p in (source / 'scripts').glob('*.py')}
    protocol.need(members == set(plan['controller_pins']), 'COMPLETE_ORIGINAL_CONTROLLER_REQUIRED')
    for name, ref in plan['controller_pins'].items():
        approved = files.get('scripts/' + name)
        protocol.need(approved is not None and ref['path'] == str(source / 'scripts' / name)
                      and all(ref[k] == approved[k] for k in ('bytes', 'sha256')),
                      'ORIGINAL_CONTROLLER_PIN_CHANGED')
    protocol.need(all(plan['controller_pins'][name]['sha256'] == sha for name, sha in ORIGINAL_DRIVERS.items()),
                  'SUPPORTED_ORIGINAL_PREINPUT_DRIVER_REQUIRED')
    return dict(manifest=protocol.pin(source.parent / 'MANIFEST.json'), members=len(files),
                source=str(source), critical_drivers=ORIGINAL_DRIVERS)


def absent(path):
    protocol.need(not os.path.lexists(path), 'APPLICATION_INPUT_OR_OUTPUT_PRESERVED:' + str(path))


def directory_fact(path):
    path = Path(path)
    s = path.lstat()
    protocol.need(stat.S_ISDIR(s.st_mode) and not path.is_symlink() and path.resolve(strict=True) == path,
                  'CANONICAL_ORIGINAL_EVIDENCE_REQUIRED')
    return [s.st_dev, s.st_ino, s.st_mtime_ns, s.st_ctime_ns]


class PreinputRecovery:
    def __init__(self, root=None, *, readonly=True):
        self.broker = protocol.RestrictedWordBroker(root, readonly=readonly)

    def close(self):
        self.broker.close()

    def facts(self, ticket):
        row = self.broker.get(ticket)
        protocol.need(row['status'] == 'PENDING' and row['invocations'] == 1,
                      'ORIGINAL_PENDING_SINGLE_INVOCATION_REQUIRED')
        protocol.need(type(row['pgid']) is int and row['pgid'] > 0 and type(row['parent_pid']) is int
                      and row['parent_pid'] > 0, 'EXACT_ORIGINAL_CHILD_REQUIRED')
        protocol.need(str(uuid.UUID(row['nonce'])) == row['nonce'] and bool(row['token']),
                      'ORIGINAL_NONCE_AND_TOKEN_REQUIRED')
        ref = protocol.object_bytes(row['plan'].encode('utf-8'))
        plan = protocol.object_bytes(protocol.read_pin(ref))
        protocol.need(plan.get('operation') == protocol.REVISION_SCOPE, 'OWNED_REVISION_PREINPUT_ONLY')
        original_source = verify_original_source(plan)
        contract = self.broker.contract(row['disposition'])
        self.broker.plan_scope(contract, plan)
        protocol.need(protocol.actor_matches(plan['actor'], contract['successor']), 'ORIGINAL_ACTOR_CHANGED')
        protocol.verify_revision_plan(plan, running=True)
        protocol.read_pin(plan['input'])
        protocol.need(type(plan['required_free_bytes']) is int and plan['required_free_bytes'] >= protocol.FLOOR,
                      'ORIGINAL_WORD_FLOOR_REQUIRED')
        expected_locks = {str(Path.home() / '.cache/wordrev/word-automation.lock'),
                          str(Path.home() / '.cmuxterm/locks/desktop-focus.lock')}
        protocol.need(set(plan['locks']) == expected_locks, 'COMPLETE_ORIGINAL_LOCK_SCOPE_REQUIRED')
        for path, identity in plan['locks'].items():
            p = Path(path); s = p.lstat()
            protocol.need(stat.S_ISREG(s.st_mode) and not p.is_symlink()
                          and [s.st_dev, s.st_ino] == identity, 'ORIGINAL_LOCK_IDENTITY_CHANGED')
        result = protocol.object_bytes(row['result'].encode('utf-8'))
        protocol.need(set(result) == {'exit_code', 'stdout', 'stderr', 'pgid', 'process_group_empty', 'original_argv'}
                      and type(result['exit_code']) is int and result['exit_code'] == 2
                      and result['stdout'] == '' and result['pgid'] == row['pgid']
                      and result['process_group_empty'] is True, 'EXACT_ORIGINAL_TERMINAL_RESULT_REQUIRED')
        argv = result['original_argv']
        expected_tail = ['-BE', str(Path(plan['controller_source']) / 'scripts/word_restricted_revision.py'),
                         '--root', str(self.broker.root), '--ticket', ticket,
                         '--token', row['token'], '--nonce', row['nonce']]
        protocol.need(isinstance(argv, list) and len(argv) == 11 and isinstance(argv[0], str)
                      and Path(argv[0]).is_absolute() and argv[1:] == expected_tail,
                      'ORIGINAL_FIXED_CHILD_ARGV_REQUIRED')
        error = protocol.object_bytes(result['stderr'].encode('utf-8'))
        protocol.need(error == dict(status='RESTRICTED_NATIVE_PENDING', reason=PREINPUT_REASON,
                                    error_type='ResourceBusy', no_retry=True,
                                    no_foreign_save_or_close=True, legacy_released=False),
                      'SUPPORTED_PREINPUT_FAILURE_REQUIRED')
        evidence = Path(plan['evidence_root'])
        before = directory_fact(evidence)
        protocol.need([p.name for p in evidence.iterdir()] == ['failure.json'],
                      'APPLICATION_EVIDENCE_PRESERVED')
        protocol.need(not (evidence / 'failure.json').is_symlink(), 'ORIGINAL_FAILURE_FILE_REQUIRED')
        failure_ref = protocol.pin(evidence / 'failure.json')
        failure = protocol.object_bytes(protocol.read_pin(failure_ref))
        protocol.need(failure == dict(reason=PREINPUT_REASON, error_type='ResourceBusy',
                    may_be_open=False, owned_document='None', retry_permitted=False,
                    no_foreign_save_or_close=True), 'EXACT_PREINPUT_FAILURE_RECORD_REQUIRED')
        paths = [*plan['revision_outputs'].values(), plan['output'],
                 str(Path.home() / 'Library/Containers/com.microsoft.Word/Data/Documents/wordrev-pdf' /
                     ('revision_' + plan['stage_id'])),
                 str(Path.home() / '.cache/wordrev/word-automation.pending.json')]
        for path in paths:
            absent(path)
        protocol.need(not group_alive(row['pgid']), 'ORIGINAL_CHILD_ALIVE_OR_UNKNOWN')
        protocol.need(directory_fact(evidence) == before
                      and [p.name for p in evidence.iterdir()] == ['failure.json'],
                      'ORIGINAL_EVIDENCE_DIRECTORY_CHANGED')
        legacy, resource = self.broker.legacy(contract)
        queue_lock = self.broker.root / (hashlib.sha256(protocol.RESOURCE.encode()).hexdigest() + '.lock')
        s = queue_lock.lstat()
        protocol.need(stat.S_ISREG(s.st_mode) and not queue_lock.is_symlink(), 'ORIGINAL_QUEUE_LOCK_REQUIRED')
        snapshot = dict(root=str(self.broker.root), ticket=row, plan=ref, actor=plan['actor'],
                        controller=original_source, failure=failure_ref,
                        evidence_directory=dict(path=str(evidence), identity=before), absent_paths=paths,
                        original_group_empty=True, legacy_row_sha256=protocol.digest(legacy),
                        legacy_resource_sha256=protocol.digest(resource),
                        locks=plan['locks'], queue_lock=dict(path=str(queue_lock), identity=[s.st_dev, s.st_ino]))
        return snapshot

    def preview(self, ticket):
        facts = self.facts(ticket)
        return dict(outcome='OWNED_PREINPUT_READ_ONLY_PLAN', plan_sha256=protocol.digest(facts),
                    facts=facts, input_operations=0, queue_changes=0, original_run_replay=False,
                    new_run_authorized=False, legacy_released=False, ordinary_word_release=False)

    def apply(self, ticket, plan_sha256):
        first = self.preview(ticket)
        protocol.need(first['plan_sha256'] == plan_sha256, 'EXACT_PREINPUT_PLAN_REQUIRED')
        actor = first['facts']['actor']
        protocol.need(protocol.actor_matches(actor, protocol.native_identity()), 'ORIGINAL_ACTOR_APPLY_REQUIRED')
        queue_lock = self.broker.root / (hashlib.sha256(protocol.RESOURCE.encode()).hexdigest() + '.lock')
        with ExitStack() as stack:
            def held_lock(path, expected):
                handle = stack.enter_context(os_lock(path))
                held, fresh = os.fstat(handle.fileno()), Path(path).lstat()
                protocol.need(not Path(path).is_symlink() and
                              [held.st_dev, held.st_ino] == [fresh.st_dev, fresh.st_ino] == expected,
                              'ORIGINAL_HELD_LOCK_CHANGED')
            held_lock(queue_lock, first['facts']['queue_lock']['identity'])
            for path in sorted(first['facts']['locks']):
                held_lock(path, first['facts']['locks'][path])
            fresh = self.preview(ticket)
            protocol.need(fresh['plan_sha256'] == plan_sha256, 'PREINPUT_PREIMAGE_CHANGED')
            protocol.need(protocol.actor_matches(actor, protocol.native_identity()), 'ORIGINAL_ACTOR_APPLY_REQUIRED')
            row = fresh['facts']['ticket']
            closeout = dict(schema='owned-preinput-terminal-v1', status=TERMINAL,
                            closed_epoch=time.time(), actor=actor, approved_plan_sha256=plan_sha256,
                            original_ticket=row, evidence=fresh['facts'],
                            original_invocations_preserved=True, original_run_replay=False,
                            new_run_authorized=False, legacy_released=False, ordinary_word_release=False,
                            application_inputs=0, final_paper_acceptance=False)
            saved = dict(original_result=row['result'], preinput_closeout=closeout)
            with self.broker.transaction():
                protocol.need(self.broker.get(ticket) == row, 'PREINPUT_TICKET_COMPARE_AND_SWAP_FAILED')
                contract = self.broker.contract(row['disposition'])
                legacy, resource = self.broker.legacy(contract)
                protocol.need(protocol.digest(legacy) == fresh['facts']['legacy_row_sha256']
                              and protocol.digest(resource) == fresh['facts']['legacy_resource_sha256'],
                              'LEGACY_PREIMAGE_CHANGED')
                protocol.read_pin(fresh['facts']['failure'])
                protocol.need(directory_fact(fresh['facts']['evidence_directory']['path']) ==
                              fresh['facts']['evidence_directory']['identity'], 'ORIGINAL_EVIDENCE_DIRECTORY_CHANGED')
                for path in fresh['facts']['absent_paths']:
                    absent(path)
                protocol.need(not group_alive(row['pgid']), 'ORIGINAL_CHILD_ALIVE_OR_UNKNOWN')
                changed = self.broker.db.execute(
                    "UPDATE word_restricted_tickets SET status=?,result=? WHERE id=? AND status='PENDING' AND result=?",
                    (TERMINAL, json.dumps(saved, ensure_ascii=False), ticket, row['result']))
                protocol.need(changed.rowcount == 1, 'PREINPUT_TICKET_COMPARE_AND_SWAP_FAILED')
            return dict(outcome='OWNED_PREINPUT_CLOSED', status=TERMINAL, ticket=ticket,
                        closeout=closeout, input_operations=0, queue_changes=1,
                        original_run_replay=False, new_run_authorized=False, legacy_released=False,
                        ordinary_word_release=False, final_paper_acceptance=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('preview', 'apply'))
    parser.add_argument('--root')
    parser.add_argument('--ticket', required=True)
    parser.add_argument('--plan-sha256')
    args = parser.parse_args(argv)
    recovery = None
    try:
        protocol.need(sys.flags.ignore_environment and sys.dont_write_bytecode, 'PREINPUT_CLI_REQUIRES_BE')
        recovery = PreinputRecovery(args.root, readonly=args.command == 'preview')
        result = recovery.preview(args.ticket) if args.command == 'preview' else recovery.apply(args.ticket, args.plan_sha256)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ResourceBusy, ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
        print(json.dumps(dict(outcome='OWNED_PREINPUT_REFUSED', reason=str(exc), input_operations=0,
                              original_run_replay=False, new_run_authorized=False, legacy_released=False), ensure_ascii=False))
        return 2
    finally:
        if recovery is not None:
            recovery.close()


if __name__ == '__main__':
    raise SystemExit(main())
