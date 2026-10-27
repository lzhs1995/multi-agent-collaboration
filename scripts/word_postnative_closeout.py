"""Settle one closed .75 tracked-save failure; preserve its original result/stage.

No Word operation, retry, invocation refund, manuscript acceptance or ordinary
resource release. Preview is read-only. Only the original native actor may apply
the exact reviewed plan under the original queue and application locks.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile

import cmux_evidence_io as evidence_io
import cmux_workspace_guard as workspace_guard
import executor_reask_protocol as releases
import word_restricted_broker as protocol
import word_restricted_inventory as inventory
import word_restricted_revision as revision
from resource_broker import ResourceBusy, group_alive

ORIGINAL_RELEASE = '0.4.75-fb808b9ece97'
ORIGINAL_MANIFEST = 'a9cfd9a4c7b80c4c12ed772ec286dbcad84954b7432bcdbfbb9590af977528d9'
DRIVERS = {
    'word_restricted_broker.py': '3c5a47a430fb693cd42695988844bf6ec032d8109f8b33c944feaaeb8bfcee2d',
    'word_restricted_pdf.py': '80e26376616ec47b139afc7ab7e09fa1d8c4541bb6d41cf38a04b813e5b777d4',
    'word_restricted_revision.py': '0988e835e661961c2f17e7c32a4a444a2bb25ca7feefd8f13abb3fd5158159b1',
    'word_restricted_inventory.py': '78059570de0b17edb1bbc416d308e8af8a95dbaa0d55c737231bedc85d8561b5',
}
REASON = 'ORIGINAL_REVISION_HEADERS_CHANGED'
TERMINAL = 'FAILED_POSTNATIVE'
TABLE = 'word_restricted_postnative_closeouts'
MAX_ENTRIES = 64
MAX_FILE_BYTES = 32 * 1024**2
MAX_TREE_BYTES = 32 * 1024**2
MAX_XML_BYTES = 8 * 1024**2
MAX_ZIP_MEMBERS = 512


def verify_source(plan):
    source = Path(plan['controller_source'])
    expected = Path.home() / '.local/share/multi-agent-collaboration/releases' / ORIGINAL_RELEASE / 'source'
    protocol.need(source == expected and source.resolve(strict=True) == source,
                  'EXACT_PUBLISHED_0475_CONTROLLER_REQUIRED')
    files = releases._verify_release(source, ORIGINAL_MANIFEST)
    members = {name.removeprefix('scripts/') for name in files
               if name.startswith('scripts/') and name.endswith('.py') and len(Path(name).parts) == 2}
    protocol.need(members == set(plan['controller_pins']), 'COMPLETE_ORIGINAL_CONTROLLER_REQUIRED')
    for name, ref in plan['controller_pins'].items():
        original = files['scripts/' + name]
        protocol.need(ref['path'] == str(source / 'scripts' / name) and
                      all(ref[k] == original[k] for k in ('bytes', 'sha256')), 'ORIGINAL_CONTROLLER_PIN_CHANGED')
    protocol.need(all(plan['controller_pins'][name]['sha256'] == sha for name, sha in DRIVERS.items()),
                  'EXACT_ORIGINAL_POSTNATIVE_DRIVER_REQUIRED')
    return dict(source=str(source), manifest=protocol.pin(source.parent / 'MANIFEST.json'),
                members=len(files), critical_drivers=DRIVERS)


def directory_identity(path):
    path = Path(path)
    s = path.lstat()
    protocol.need(stat.S_ISDIR(s.st_mode) and not path.is_symlink() and path.resolve(strict=True) == path,
                  'CANONICAL_ORIGINAL_DIRECTORY_REQUIRED')
    return [s.st_dev, s.st_ino, s.st_mtime_ns, s.st_ctime_ns]


def absent(path):
    protocol.need(not os.path.lexists(path), 'POSTNATIVE_OUTPUT_OR_PENDING_PRESERVED:' + str(path))


def read_object(path):
    ref = bounded_pin(path)
    return protocol.object_bytes(protocol.read_pin(ref)), ref


def bounded_pin(path):
    path = Path(path)
    protocol.need(path.is_absolute() and path.resolve(strict=True) == path,
                  'CANONICAL_ORIGINAL_FILE_REQUIRED')
    raw, _identity = evidence_io.snapshot(path, limit=MAX_FILE_BYTES)
    return dict(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


@contextmanager
def held_original_lock(path, expected):
    """Acquire an existing original flock; never create or follow a lock path."""
    path = Path(path)
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'r+b') as handle:
        held, fresh = os.fstat(handle.fileno()), path.lstat()
        protocol.need(stat.S_ISREG(held.st_mode) and stat.S_ISREG(fresh.st_mode) and
                      held.st_nlink == fresh.st_nlink == 1 and
                      path.resolve(strict=True) == path and
                      [held.st_dev, held.st_ino] == [fresh.st_dev, fresh.st_ino] == expected,
                      'ORIGINAL_POSTNATIVE_HELD_LOCK_CHANGED')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ResourceBusy('OS_LOCK_BUSY: ' + str(path)) from exc
        try:
            yield handle
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def evidence_snapshot(root):
    """Bound and pin the complete original evidence tree without modifying it."""
    root = Path(root)
    before = directory_identity(root)
    files, directories = {}, {'.': before}
    pending, count, total_bytes = [root], 0, 0
    while pending:
        folder = pending.pop()
        identity = directory_identity(folder)
        with os.scandir(folder) as entries:
            for entry in entries:
                count += 1
                protocol.need(count <= MAX_ENTRIES, 'BOUNDED_ORIGINAL_POSTNATIVE_EVIDENCE_REQUIRED')
                path = Path(entry.path)
                info = entry.stat(follow_symlinks=False)
                name = path.relative_to(root).as_posix()
                protocol.need(not stat.S_ISLNK(info.st_mode), 'ORIGINAL_EVIDENCE_SYMLINK_REFUSED')
                if stat.S_ISDIR(info.st_mode):
                    directories[name] = directory_identity(path)
                    pending.append(path)
                else:
                    protocol.need(stat.S_ISREG(info.st_mode), 'REGULAR_ORIGINAL_EVIDENCE_REQUIRED')
                    total_bytes += info.st_size
                    protocol.need(total_bytes <= MAX_TREE_BYTES, 'BOUNDED_ORIGINAL_POSTNATIVE_BYTES_REQUIRED')
                    files[name] = bounded_pin(path)
        protocol.need(directory_identity(folder) == identity, 'ORIGINAL_EVIDENCE_CHANGED_DURING_SNAPSHOT')
    protocol.need(all(directory_identity(root / name) == value for name, value in directories.items()),
                  'ORIGINAL_EVIDENCE_CHANGED_DURING_SNAPSHOT')
    return dict(path=str(root), directories=dict(sorted(directories.items())), files=dict(sorted(files.items())))


def stage_snapshot(tracked, original_input):
    tracked = Path(tracked)
    folder = tracked.parent
    before = directory_identity(folder)
    names = set()
    companion = tracked.with_suffix('')
    with os.scandir(folder) as entries:
        for entry in entries:
            names.add(entry.name)
            protocol.need(len(names) <= 2 and entry.name in (tracked.name, companion.name),
                          'ONLY_ORIGINAL_TRACKED_STAGE_PRESERVED')
    protocol.need(tracked.name in names, 'ONLY_ORIGINAL_TRACKED_STAGE_PRESERVED')
    ref = bounded_pin(tracked)
    backup = None
    if companion.name in names:
        backup_directory = directory_identity(companion)
        expected = '備份 ' + tracked.name
        count = 0
        with os.scandir(companion) as entries:
            for entry in entries:
                count += 1
                protocol.need(count == 1 and entry.name == expected, 'EXACT_INPUT_IDENTICAL_STAGE_BACKUP_REQUIRED')
        protocol.need(count == 1, 'EXACT_INPUT_IDENTICAL_STAGE_BACKUP_REQUIRED')
        pinned = bounded_pin(companion / expected)
        protocol.need(all(pinned[key] == original_input[key] for key in ('bytes', 'sha256')),
                      'EXACT_INPUT_IDENTICAL_STAGE_BACKUP_REQUIRED')
        protocol.need(directory_identity(companion) == backup_directory, 'ORIGINAL_TRACKED_BACKUP_DIRECTORY_CHANGED')
        backup = dict(path=str(companion), identity=backup_directory, file=pinned,
                      role='INPUT_IDENTICAL_SAVE_COMPANION', producer='NOT_INDEPENDENTLY_ATTRIBUTED',
                      preserved=True, paper_artifact=False)
    protocol.need(directory_identity(folder) == before, 'ORIGINAL_TRACKED_STAGE_DIRECTORY_CHANGED')
    return ref, dict(path=str(folder), identity=before, input_identical_backup=backup)


def bounded_revision_headers(ref):
    """Limit decompression before using the original header comparison."""
    raw = protocol.read_pin(ref, limit=MAX_FILE_BYTES)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        protocol.need(len(members) <= MAX_ZIP_MEMBERS and
                      len({info.filename for info in members}) == len(members),
                      'BOUNDED_UNIQUE_ORIGINAL_DOCX_MEMBERS_REQUIRED')
        xml = [info for info in members if info.filename.startswith('word/') and info.filename.endswith('.xml')]
        protocol.need(all(info.file_size <= MAX_XML_BYTES for info in xml) and
                      sum(info.file_size for info in xml) <= MAX_TREE_BYTES,
                      'BOUNDED_ORIGINAL_DOCX_XML_REQUIRED')
    return revision.revision_headers(io.BytesIO(raw))


class PostnativeCloseout:
    def __init__(self, root=None, *, readonly=True):
        self.broker = protocol.RestrictedWordBroker(root, readonly=readonly)

    def close(self):
        self.broker.close()

    def facts(self, ticket):
        row = self.broker.get(ticket)
        protocol.need(row['status'] == 'PENDING' and row['invocations'] == 1,
                      'ORIGINAL_PENDING_SINGLE_INVOCATION_REQUIRED')
        protocol.need(type(row['pgid']) is int and row['pgid'] > 0 and
                      type(row['parent_pid']) is int and row['parent_pid'] > 0 and
                      str(uuid.UUID(row['nonce'])) == row['nonce'] and bool(row['token']),
                      'COMPLETE_ORIGINAL_INVOCATION_REQUIRED')
        ref = protocol.object_bytes(row['plan'].encode())
        plan = protocol.object_bytes(protocol.read_pin(ref))
        protocol.need(plan.get('operation') == protocol.REVISION_SCOPE, 'OWNED_REVISION_POSTNATIVE_ONLY')
        source = verify_source(plan)
        contract = self.broker.contract(row['disposition'])
        self.broker.plan_scope(contract, plan)
        protocol.need(protocol.actor_matches(plan['actor'], contract['successor']), 'ORIGINAL_ACTOR_CHANGED')
        protocol.verify_revision_plan(plan, running=True)
        protocol.read_pin(plan['input'])
        protocol.need(type(plan['required_free_bytes']) is int and plan['required_free_bytes'] >= protocol.FLOOR,
                      'ORIGINAL_WORD_FLOOR_REQUIRED')
        result = protocol.object_bytes(row['result'].encode())
        protocol.need(set(result) == {'exit_code', 'stdout', 'stderr', 'pgid', 'process_group_empty', 'original_argv'}
                      and type(result['exit_code']) is int and result['exit_code'] == 2 and
                      result['stdout'] == '' and result['pgid'] == row['pgid'] and
                      result['process_group_empty'] is True, 'EXACT_POSTNATIVE_TERMINAL_RESULT_REQUIRED')
        error = protocol.object_bytes(result['stderr'].encode())
        protocol.need(error == dict(status='RESTRICTED_NATIVE_PENDING', reason=REASON, error_type='ResourceBusy',
                                    no_retry=True, no_foreign_save_or_close=True, legacy_released=False),
                      'EXACT_ORIGINAL_TRACKED_SAVE_FAILURE_REQUIRED')
        argv = result['original_argv']
        expected = ['-BE', str(Path(plan['controller_source']) / 'scripts/word_restricted_revision.py'),
                    '--root', str(self.broker.root), '--ticket', ticket, '--token', row['token'], '--nonce', row['nonce']]
        protocol.need(isinstance(argv, list) and len(argv) == 11 and Path(argv[0]).is_absolute() and argv[1:] == expected,
                      'ORIGINAL_FIXED_REVISION_CHILD_REQUIRED')
        root = Path(plan['evidence_root'])
        snapshot = evidence_snapshot(root)
        expected_files = {'application-bindings.json', 'PRE-inventory.json', 'pre.applescript',
                          'native-intent.json', 'failure.json', 'tracked/owned-input.json'}
        for stage in revision.STAGES:
            expected_files.update('tracked/' + stage.lower() + suffix for suffix in
                                  ('.applescript', '-intent.json', '-result.json'))
        launch_files = {'word-launch-intent.json', 'word-launch-host-result.json'}
        present = set(snapshot['files'])
        protocol.need(present in (expected_files, expected_files | launch_files) and
                      set(snapshot['directories']) == {'.', 'tracked'}, 'EXACT_COMPLETED_TRACKED_STAGE_EVIDENCE_REQUIRED')
        tracked = revision.owned_path(plan, 'tracked')
        native_ref, stage_directory = stage_snapshot(tracked, plan['input'])
        failure = protocol.object_bytes(protocol.read_pin(snapshot['files']['failure.json']))
        protocol.need(failure == dict(reason=REASON, error_type='ResourceBusy', may_be_open=False,
                                     owned_document=str(tracked), retry_permitted=False, no_foreign_save_or_close=True),
                      'ORIGINAL_CLOSED_POSTNATIVE_FAILURE_REQUIRED')
        owned = protocol.object_bytes(protocol.read_pin(snapshot['files']['tracked/owned-input.json']))
        protocol.need(owned['source'] == plan['input'] and owned['action'] == 'tracked' and
                      owned['copy']['path'] == str(tracked) and
                      all(owned['copy'][k] == plan['input'][k] for k in ('bytes', 'sha256')),
                      'EXACT_ORIGINAL_TRACKED_INPUT_REQUIRED')
        before_headers = bounded_revision_headers(plan['input'])
        after_headers = bounded_revision_headers(native_ref)
        protocol.need(bool(before_headers) and bool(after_headers) and
                      protocol.digest(owned['revision_headers']) == protocol.digest(before_headers) and
                      protocol.digest(after_headers) != protocol.digest(before_headers),
                      'ACTUAL_ORIGINAL_HEADER_DIFFERENCE_REQUIRED')
        for stage in revision.STAGES:
            lower = stage.lower()
            script = snapshot['files']['tracked/' + lower + '.applescript']
            protocol.need(protocol.read_pin(script) == revision.expected_revision_script(plan, 'tracked', stage),
                          'EXACT_ORIGINAL_TRACKED_STAGE_SCRIPT_REQUIRED')
            intent = protocol.object_bytes(protocol.read_pin(snapshot['files']['tracked/' + lower + '-intent.json']))
            completed = protocol.object_bytes(protocol.read_pin(snapshot['files']['tracked/' + lower + '-result.json']))
            protocol.need(intent == dict(stage=stage, owned=str(tracked), script=script, plan=ref, replay_allowed=False),
                          'EXACT_ORIGINAL_TRACKED_STAGE_INTENT_REQUIRED')
            protocol.need(completed.get('stage') == stage and completed.get('script') == script and
                          type(completed.get('returncode')) is int and completed['returncode'] == 0 and
                          isinstance(completed.get('stdout'), str) and completed['stdout'].strip() == stage and
                          completed.get('stderr') == '' and completed.get('owned_document') == str(tracked),
                          'ALL_FIVE_ORIGINAL_NATIVE_STAGES_REQUIRED')
        native_intent = protocol.object_bytes(protocol.read_pin(snapshot['files']['native-intent.json']))
        protocol.need(native_intent['ticket'] == ticket and native_intent['nonce'] == row['nonce'] and
                      native_intent['plan'] == ref and native_intent['input'] == plan['input'] and
                      native_intent['operation'] == protocol.REVISION_SCOPE and
                      native_intent['source_semantics_verification'] == 'PENDING_PARENT_SOURCE_CONTRACT',
                      'ORIGINAL_NATIVE_INTENT_REQUIRED')
        pre = protocol.object_bytes(protocol.read_pin(snapshot['files']['PRE-inventory.json']))
        apps = protocol.object_bytes(protocol.read_pin(snapshot['files']['application-bindings.json']))
        protocol.need(type(pre['returncode']) is int and pre['returncode'] == 0 and pre['stderr'] == '' and
                      pre['apps'] == apps['bindings'] and native_intent['pre'] == snapshot['files']['PRE-inventory.json'],
                      'SUCCESSFUL_ORIGINAL_PRE_INVENTORY_REQUIRED')
        protocol.need(protocol.read_pin(snapshot['files']['pre.applescript']) == inventory.SCRIPT.encode('utf-8') and
                      native_intent['before'] == inventory.parse(pre['stdout']),
                      'EXACT_ORIGINAL_COMPLETE_PRE_INVENTORY_REQUIRED')
        if present & launch_files:
            launch = protocol.object_bytes(protocol.read_pin(snapshot['files']['word-launch-intent.json']))
            host = protocol.object_bytes(protocol.read_pin(snapshot['files']['word-launch-host-result.json']))
            protocol.need(plan['start_word_if_absent'] is True and launch['argv'] == revision.WORD_LAUNCH and
                          launch['plan'] == ref and launch['prior_word'] == 'NOT_RUNNING' and
                          launch['replay_allowed'] is False and type(host['returncode']) is int and
                          host['returncode'] == 0 and host['stderr'] == '' and
                          launch['zotero_binding'] == apps['bindings']['Zotero'] and
                          apps['word_launch_requested'] is True, 'ORIGINAL_AUTHORIZED_SINGLE_WORD_LAUNCH_REQUIRED')
        else:
            protocol.need(apps['word_launch_requested'] is False, 'ORIGINAL_NO_WORD_LAUNCH_REQUIRED')
        paths = [*plan['revision_outputs'].values(), plan['output'],
                 str(Path.home() / '.cache/wordrev/word-automation.pending.json')]
        for path in paths:
            absent(path)
        expected_locks = {str(Path.home() / '.cache/wordrev/word-automation.lock'),
                          str(Path.home() / '.cmuxterm/locks/desktop-focus.lock')}
        protocol.need(set(plan['locks']) == expected_locks, 'EXACT_ORIGINAL_APPLICATION_LOCKS_REQUIRED')
        queue_lock = self.broker.root / (hashlib.sha256(protocol.RESOURCE.encode()).hexdigest() + '.lock')
        locks = dict(plan['locks'])
        info = queue_lock.lstat()
        locks[str(queue_lock)] = [info.st_dev, info.st_ino]
        for path, identity in locks.items():
            p = Path(path); s = p.lstat()
            protocol.need(stat.S_ISREG(s.st_mode) and not p.is_symlink() and p.resolve(strict=True) == p and
                          [s.st_dev, s.st_ino] == identity, 'ORIGINAL_LOCK_IDENTITY_CHANGED')
        protocol.need(not group_alive(row['pgid']), 'ORIGINAL_CHILD_ALIVE_OR_UNKNOWN')
        legacy, resource = self.broker.legacy(contract)
        protocol.need(evidence_snapshot(root) == snapshot and
                      stage_snapshot(tracked, plan['input']) == (native_ref, stage_directory),
                      'ORIGINAL_POSTNATIVE_EVIDENCE_CHANGED')
        return dict(root=str(self.broker.root), original_ticket=row, plan=ref, actor=plan['actor'],
                    original_source=source, evidence=snapshot, input=plan['input'], native_stage=native_ref,
                    stage_directory=stage_directory,
                    original_group_empty=True, original_result_sha256=hashlib.sha256(row['result'].encode()).hexdigest(),
                    headers_before=sum(len(rows) for rows in before_headers.values()),
                    headers_after=sum(len(rows) for rows in after_headers.values()),
                    native_tracked_roundtrip_closed=True, tracked_preservation_accepted=False,
                    table_geometry_accepted=False, absent_paths=paths, locks=locks,
                    legacy_row_sha256=protocol.digest(legacy), legacy_resource_sha256=protocol.digest(resource))

    def preview(self, ticket):
        facts = self.facts(ticket)
        return dict(outcome='OWNED_POSTNATIVE_READ_ONLY_PLAN', plan_sha256=protocol.digest(facts), facts=facts,
                    input_operations=0, queue_changes=0, original_result_changed=False, original_stage_preserved=True,
                    original_run_replay=False, new_run_authorized=False, legacy_released=False,
                    ordinary_word_release=False, final_paper_acceptance=False)

    def apply(self, ticket, plan_sha256):
        first = self.preview(ticket)
        protocol.need(first['plan_sha256'] == plan_sha256, 'EXACT_POSTNATIVE_PLAN_REQUIRED')
        actor = first['facts']['actor']
        protocol.need(protocol.actor_matches(actor, protocol.native_identity()), 'ORIGINAL_POSTNATIVE_ACTOR_REQUIRED')
        with ExitStack() as stack:
            for path, expected in sorted(first['facts']['locks'].items()):
                stack.enter_context(held_original_lock(path, expected))
            current = self.preview(ticket)
            protocol.need(current['plan_sha256'] == plan_sha256, 'POSTNATIVE_PREIMAGE_CHANGED')
            protocol.need(protocol.actor_matches(actor, protocol.native_identity()), 'ORIGINAL_POSTNATIVE_ACTOR_REQUIRED')
            row = current['facts']['original_ticket']
            closeout = dict(schema='owned-postnative-terminal-v1', status=TERMINAL, closed_epoch=time.time(),
                            approved_plan_sha256=plan_sha256, original_actor=actor, original_evidence=current['facts'],
                            original_result_preserved=True, original_stage_preserved=True,
                            original_invocations_preserved=True, original_run_replay=False,
                            new_run_authorized=False, application_inputs=0, legacy_released=False,
                            ordinary_word_release=False, table_geometry_accepted=False, final_paper_acceptance=False)
            with self.broker.transaction():
                protocol.need(self.broker.get(ticket) == row, 'POSTNATIVE_TICKET_COMPARE_AND_SWAP_FAILED')
                final = self.preview(ticket)
                protocol.need(final['plan_sha256'] == plan_sha256, 'POSTNATIVE_TRANSACTION_PREIMAGE_CHANGED')
                self.broker.db.execute('CREATE TABLE IF NOT EXISTS ' + TABLE +
                    ' (ticket TEXT PRIMARY KEY, original_result_sha256 TEXT NOT NULL, receipt TEXT NOT NULL, closed REAL NOT NULL)')
                protocol.need(self.broker.db.execute('SELECT ticket FROM ' + TABLE + ' WHERE ticket=?',
                                                    (ticket,)).fetchone() is None, 'ORIGINAL_POSTNATIVE_CLOSEOUT_ALREADY_EXISTS')
                changed = self.broker.db.execute(
                    "UPDATE word_restricted_tickets SET status=? WHERE id=? AND status='PENDING' AND invocations=1 AND result=?",
                    (TERMINAL, ticket, row['result']))
                protocol.need(changed.rowcount == 1, 'POSTNATIVE_TICKET_COMPARE_AND_SWAP_FAILED')
                self.broker.db.execute('INSERT INTO ' + TABLE + ' VALUES (?,?,?,?)',
                    (ticket, current['facts']['original_result_sha256'], json.dumps(closeout, ensure_ascii=False), closeout['closed_epoch']))
            return dict(outcome='OWNED_POSTNATIVE_CLOSED', status=TERMINAL, ticket=ticket, closeout=closeout,
                        input_operations=0, queue_changes=1, original_result_changed=False,
                        original_stage_preserved=True, original_invocations_preserved=True, original_run_replay=False,
                        new_run_authorized=False, legacy_released=False, ordinary_word_release=False,
                        final_paper_acceptance=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('preview', 'apply'))
    parser.add_argument('--root')
    parser.add_argument('--ticket', required=True)
    parser.add_argument('--plan-sha256')
    args = parser.parse_args(argv)
    closeout = None
    try:
        protocol.need(sys.flags.ignore_environment and sys.dont_write_bytecode, 'POSTNATIVE_CLI_REQUIRES_BE')
        closeout = PostnativeCloseout(args.root, readonly=args.command == 'preview')
        result = closeout.preview(args.ticket) if args.command == 'preview' else closeout.apply(args.ticket, args.plan_sha256)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ResourceBusy, ValueError, TypeError, KeyError, OSError, sqlite3.Error,
            workspace_guard.WorkspaceScopeError, zipfile.BadZipFile, ET.ParseError, RuntimeError) as exc:
        print(json.dumps(dict(outcome='OWNED_POSTNATIVE_REFUSED', reason=str(exc), input_operations=0,
                              original_run_replay=False, new_run_authorized=False, legacy_released=False,
                              ordinary_word_release=False, original_stage_preserved=True), ensure_ascii=False))
        return 2
    finally:
        if closeout is not None:
            closeout.close()


if __name__ == '__main__':
    raise SystemExit(main())
