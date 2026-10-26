"""Close one proven pre-input owned revision failure, without replay or release."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time
import uuid

import cmux_workspace_guard as workspace_guard
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
INVENTORY_REASON = 'NATIVE_INVENTORY_UNAVAILABLE'
# Published 0.4.72 source, including executable flags. Complete private copies
# can retain their original plan paths without editing the old controller.
INVENTORY_SOURCE_COUNT = 385
INVENTORY_SOURCE_BYTES = 5508124
INVENTORY_SOURCE_DIGEST = 'bf4cf132c5b8a7d30677ae4a1e9e260f0ddff3d076d94c594704b2075a7f1aad'
INVENTORY_DRIVERS = {
    'word_restricted_broker.py': '3c5a47a430fb693cd42695988844bf6ec032d8109f8b33c944feaaeb8bfcee2d',
    'word_restricted_pdf.py': '80e26376616ec47b139afc7ab7e09fa1d8c4541bb6d41cf38a04b813e5b777d4',
    'word_restricted_revision.py': '0988e835e661961c2f17e7c32a4a444a2bb25ca7feefd8f13abb3fd5158159b1',
    'word_restricted_inventory.py': '0a50fb85b89f78d1b3bb092d62f569f279cba62b2c8c9f984cd04f5b2e737182',
}
INVENTORY_SCRIPT_SHA256 = 'c07e5b41ea7a060c4d483121bfbfddaad3a418cb77110bd3221225474c549957'
AX_SOURCE_COUNT = 388
AX_SOURCE_BYTES = 5537868
AX_SOURCE_DIGEST = '9865984adecb074b7e7aabe4efa971f29e8fadcd55ccba36890bbd8e2e18a716'
AX_DRIVERS = {
    'word_restricted_broker.py': '3c5a47a430fb693cd42695988844bf6ec032d8109f8b33c944feaaeb8bfcee2d',
    'word_restricted_pdf.py': '80e26376616ec47b139afc7ab7e09fa1d8c4541bb6d41cf38a04b813e5b777d4',
    'word_restricted_revision.py': '0988e835e661961c2f17e7c32a4a444a2bb25ca7feefd8f13abb3fd5158159b1',
    'word_restricted_inventory.py': 'b46b4668a477431e0ab8fec6c598d0ccbee757f58cc40abedf99d18722100d6b',
}
WORD_TIMEOUT_PROFILE = 'WORD_TIMEOUT_0472'
AX_REFERENCE_PROFILE = 'SYSTEM_EVENTS_WINDOW_REFERENCE_0473'
# Only this observed failure expression in the fixed original read-only script.
# It is not a diagnosis of a permanent AX permission problem or a proven race.
AX_REFERENCE_DIAGNOSTIC = ('System Events發生錯誤：無法取得「window "文件1  -  已復原" '
                           'of application process "Microsoft Word"」。 (-1728)')
TERMINAL = 'FAILED_PREINPUT'


def verify_inventory_source(plan):
    return verify_published_inventory_source(plan, count=INVENTORY_SOURCE_COUNT,
        total=INVENTORY_SOURCE_BYTES, tree=INVENTORY_SOURCE_DIGEST,
        drivers=INVENTORY_DRIVERS, release='0472', profile=WORD_TIMEOUT_PROFILE)


def verify_ax_source(plan):
    return verify_published_inventory_source(plan, count=AX_SOURCE_COUNT,
        total=AX_SOURCE_BYTES, tree=AX_SOURCE_DIGEST, drivers=AX_DRIVERS,
        release='0473', profile=AX_REFERENCE_PROFILE)


def verify_published_inventory_source(plan, *, count, total, tree, drivers, release, profile):
    """Read a complete published original tree; never import or patch it."""
    import executor_reask_protocol as releases
    source = Path(plan['controller_source'])
    protocol.need(source.is_absolute() and source.resolve(strict=True) == source
                  and not source.is_symlink() and source.is_dir(),
                  'SUPPORTED_ORIGINAL_PREINPUT_CONTROLLER_REQUIRED')
    before = directory_fact(source)
    paths = []
    entries = 0
    for path in source.rglob('*'):
        entries += 1
        protocol.need(entries <= 2048, 'BOUNDED_ORIGINAL_CONTROLLER_REQUIRED')
        info = path.lstat()
        protocol.need(not path.is_symlink() and (stat.S_ISDIR(info.st_mode)
                      or stat.S_ISREG(info.st_mode)), 'REGULAR_ORIGINAL_CONTROLLER_REQUIRED')
        if stat.S_ISREG(info.st_mode):
            paths.append((path, info.st_size, os.access(path, os.X_OK)))
    protocol.need(len(paths) == count
                  and sum(size for _path, size, _executable in paths) == total,
                  'COMPLETE_PUBLISHED_' + release + '_CONTROLLER_REQUIRED')
    files = {}
    for path, size, executable in paths:
        raw = releases._read_regular(path, size, executable=executable)
        protocol.need(len(raw) == size, 'ORIGINAL_CONTROLLER_PIN_CHANGED')
        files[path.relative_to(source).as_posix()] = dict(
            bytes=size, sha256=hashlib.sha256(raw).hexdigest(), executable=executable)
    protocol.need(protocol.digest(files) == tree
                  and directory_fact(source) == before, 'PUBLISHED_' + release + '_CONTROLLER_CHANGED')
    members = {name[len('scripts/'):] for name in files
               if name.startswith('scripts/') and name.endswith('.py')
               and len(Path(name).parts) == 2}
    protocol.need(members == set(plan['controller_pins']), 'COMPLETE_ORIGINAL_CONTROLLER_REQUIRED')
    for name, ref in plan['controller_pins'].items():
        approved = files.get('scripts/' + name)
        protocol.need(approved is not None and ref['path'] == str(source / 'scripts' / name)
                      and all(ref[k] == approved[k] for k in ('bytes', 'sha256')),
                      'ORIGINAL_CONTROLLER_PIN_CHANGED')
    protocol.need(all(plan['controller_pins'][name]['sha256'] == sha
                      for name, sha in drivers.items()),
                  'SUPPORTED_ORIGINAL_PREINPUT_DRIVER_REQUIRED')
    return dict(members=len(files), source=str(source), published_tree_sha256=tree,
                critical_drivers=drivers, preinput_reason=INVENTORY_REASON, failure_profile=profile)


def verify_original_source(plan):
    """Verify the complete original install without importing old code."""
    import executor_reask_protocol as releases
    source = Path(plan['controller_source'])
    expected = Path.home() / '.local/share/multi-agent-collaboration/releases' / ORIGINAL_RELEASE / 'source'
    if source != expected:
        pins = plan['controller_pins']
        protocol.need(isinstance(pins, dict), 'COMPLETE_ORIGINAL_CONTROLLER_REQUIRED')
        inventory_pin = pins.get('word_restricted_inventory.py')
        if (isinstance(inventory_pin, dict) and
                inventory_pin.get('sha256') == AX_DRIVERS['word_restricted_inventory.py']):
            return verify_ax_source(plan)
        return verify_inventory_source(plan)
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


def inventory_timeout_evidence(evidence, profile=WORD_TIMEOUT_PROFILE):
    """Accept only a failed original read-only PRE script with no Word launch."""
    refs = {}
    for name in ('PRE-inventory.json', 'application-bindings.json', 'pre.applescript'):
        path = evidence / name
        protocol.need(stat.S_ISREG(path.lstat().st_mode) and not path.is_symlink(),
                      'REGULAR_ORIGINAL_INVENTORY_EVIDENCE_REQUIRED')
        refs[name] = protocol.pin(path)
    protocol.need(refs['pre.applescript']['sha256'] == INVENTORY_SCRIPT_SHA256,
                  'ORIGINAL_READ_ONLY_INVENTORY_SCRIPT_REQUIRED')
    pre = protocol.object_bytes(protocol.read_pin(refs['PRE-inventory.json']))
    binding = protocol.object_bytes(protocol.read_pin(refs['application-bindings.json']))
    protocol.need(set(pre) == {'returncode', 'stdout', 'stderr', 'apps'}
                  and type(pre['returncode']) is int and pre['returncode'] == 1
                  and pre['stdout'] == '' and isinstance(pre['stderr'], str),
                  'EXACT_ORIGINAL_PRE_INVENTORY_FAILURE_REQUIRED')
    protocol.need(profile in (WORD_TIMEOUT_PROFILE, AX_REFERENCE_PROFILE),
                  'SUPPORTED_ORIGINAL_INVENTORY_FAILURE_PROFILE_REQUIRED')
    if profile == AX_REFERENCE_PROFILE:
        diagnostic = (re.escape(str(evidence / 'pre.applescript')) +
                      ':2817:2837: execution error: ' + re.escape(AX_REFERENCE_DIAGNOSTIC) + r'\n?')
        rejection = 'EXACT_ORIGINAL_SYSTEM_EVENTS_WINDOW_REFERENCE_REQUIRED'
    else:
        diagnostic = re.escape(str(evidence / 'pre.applescript')) + (
            r':\d+:\d+: execution error: Microsoft Word[^\r\n]*\(-1712\)\n?')
        rejection = 'EXACT_ORIGINAL_WORD_APPLEEVENT_TIMEOUT_REQUIRED'
    protocol.need(re.fullmatch(diagnostic, pre['stderr']) is not None,
                  rejection)
    protocol.need(set(binding) == {'bindings', 'word_launch_requested'}
                  and binding['word_launch_requested'] is False
                  and pre['apps'] == binding['bindings'], 'ORIGINAL_NO_LAUNCH_BINDING_REQUIRED')
    apps = binding['bindings']
    expected = {'Microsoft Word': '/Applications/Microsoft Word.app/Contents/MacOS/Microsoft Word',
                'Zotero': '/Applications/Zotero.app/Contents/MacOS/zotero'}
    protocol.need(isinstance(apps, dict) and set(apps) == set(expected),
                  'COMPLETE_ORIGINAL_APPLICATION_BINDINGS_REQUIRED')
    for app, executable in expected.items():
        row = apps[app]
        protocol.need(isinstance(row, dict) and set(row) == {'pid', 'birth', 'executable', 'file_identity'}
                      and type(row['pid']) is int and row['pid'] > 0
                      and isinstance(row['birth'], list) and len(row['birth']) == 2
                      and all(type(v) is int and v >= 0 for v in row['birth'])
                      and row['executable'] == executable
                      and isinstance(row['file_identity'], list) and len(row['file_identity']) == 4
                      and all(type(v) is int and v > 0 for v in row['file_identity']),
                      'EXACT_ORIGINAL_APPLICATION_BINDING_REQUIRED')
    return refs


def resumed_identity(original, expected=None):
    """Authenticate a resumed original thread without rewriting its old actor."""
    identity, tree, _env, proof = workspace_guard.caller_snapshot()
    caller = identity['caller']
    thread = (proof or {}).get('foreground', {}).get('thread_id')
    protocol.need(bool(thread), 'AUTHENTICATED_FOREGROUND_THREAD_REQUIRED')
    current = dict(workspace_uuid=caller['workspace_id'], surface_uuid=caller['surface_id'],
                   pane_uuid=caller['pane_id'], session_id=thread)
    for key in ('workspace_uuid', 'pane_uuid', 'session_id'):
        protocol.need(str(uuid.UUID(original[key])) == str(uuid.UUID(current[key])),
                      'SAME_ORIGINAL_THREAD_WORKSPACE_PANE_REQUIRED')
    original_surface = str(uuid.UUID(original['surface_uuid']))
    current_surface = str(uuid.UUID(current['surface_uuid']))
    protocol.need(current_surface != original_surface, 'DISTINCT_RESUMED_SURFACE_REQUIRED')
    rows = []
    protocol.need(type(tree.get('windows')) is list and bool(tree['windows']),
                  'COMPLETE_RESUMED_TREE_REQUIRED')
    for window in tree['windows']:
        for workspace in window['workspaces']:
            for pane in workspace['panes']:
                for surface in pane['surfaces']:
                    rows.append((str(uuid.UUID(surface['id'])), str(uuid.UUID(workspace['id'])),
                                 str(uuid.UUID(pane['id']))))
    protocol.need(not any(row[0] == original_surface for row in rows),
                  'ORIGINAL_SURFACE_STILL_PRESENT')
    protocol.need([row for row in rows if row[0] == current_surface] ==
                  [(current_surface, str(uuid.UUID(current['workspace_uuid'])),
                    str(uuid.UUID(current['pane_uuid'])))], 'EXACT_RESUMED_SURFACE_REQUIRED')
    if expected is not None:
        protocol.need(protocol.actor_matches(expected, current), 'RESUMED_ACTOR_CHANGED')
    return current


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
        reason = original_source.get('preinput_reason', PREINPUT_REASON)
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
        protocol.need(error == dict(status='RESTRICTED_NATIVE_PENDING', reason=reason,
                                    error_type='ResourceBusy', no_retry=True,
                                    no_foreign_save_or_close=True, legacy_released=False),
                      'SUPPORTED_PREINPUT_FAILURE_REQUIRED')
        evidence = Path(plan['evidence_root'])
        before = directory_fact(evidence)
        expected_names = (['PRE-inventory.json', 'application-bindings.json', 'failure.json', 'pre.applescript']
                          if reason == INVENTORY_REASON else ['failure.json'])
        protocol.need(sorted(p.name for p in evidence.iterdir()) == expected_names,
                      'APPLICATION_EVIDENCE_PRESERVED')
        protocol.need(not (evidence / 'failure.json').is_symlink(), 'ORIGINAL_FAILURE_FILE_REQUIRED')
        failure_ref = protocol.pin(evidence / 'failure.json')
        failure = protocol.object_bytes(protocol.read_pin(failure_ref))
        protocol.need(failure == dict(reason=reason, error_type='ResourceBusy',
                    may_be_open=False, owned_document='None', retry_permitted=False,
                    no_foreign_save_or_close=True), 'EXACT_PREINPUT_FAILURE_RECORD_REQUIRED')
        inventory_refs = (inventory_timeout_evidence(evidence, original_source.get('failure_profile', WORD_TIMEOUT_PROFILE))
                          if reason == INVENTORY_REASON else None)
        paths = [*plan['revision_outputs'].values(), plan['output'],
                 str(Path.home() / 'Library/Containers/com.microsoft.Word/Data/Documents/wordrev-pdf' /
                     ('revision_' + plan['stage_id'])),
                 str(Path.home() / '.cache/wordrev/word-automation.pending.json')]
        for path in paths:
            absent(path)
        protocol.need(not group_alive(row['pgid']), 'ORIGINAL_CHILD_ALIVE_OR_UNKNOWN')
        protocol.need(directory_fact(evidence) == before
                      and sorted(p.name for p in evidence.iterdir()) == expected_names,
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
        if inventory_refs is not None:
            snapshot['native_inventory_evidence'] = inventory_refs
        return snapshot

    def preview(self, ticket, *, resume_same_session=False):
        facts = self.facts(ticket)
        if resume_same_session:
            facts['resumed_actor'] = resumed_identity(facts['actor'])
        return dict(outcome='OWNED_PREINPUT_READ_ONLY_PLAN', plan_sha256=protocol.digest(facts),
                    facts=facts, input_operations=0, queue_changes=0, original_run_replay=False,
                    new_run_authorized=False, legacy_released=False, ordinary_word_release=False)

    def apply(self, ticket, plan_sha256, *, resume_same_session=False):
        first = self.preview(ticket, resume_same_session=resume_same_session)
        protocol.need(first['plan_sha256'] == plan_sha256, 'EXACT_PREINPUT_PLAN_REQUIRED')
        actor = first['facts']['actor']
        resumed_actor = first['facts'].get('resumed_actor')
        if resumed_actor is not None:
            resumed_identity(actor, resumed_actor)
        else:
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
            fresh = self.preview(ticket, resume_same_session=resume_same_session)
            protocol.need(fresh['plan_sha256'] == plan_sha256, 'PREINPUT_PREIMAGE_CHANGED')
            if resumed_actor is not None:
                resumed_identity(actor, resumed_actor)
            else:
                protocol.need(protocol.actor_matches(actor, protocol.native_identity()), 'ORIGINAL_ACTOR_APPLY_REQUIRED')
            row = fresh['facts']['ticket']
            closeout = dict(schema='owned-preinput-terminal-v1', status=TERMINAL,
                            closed_epoch=time.time(), actor=actor, approved_plan_sha256=plan_sha256,
                            original_ticket=row, evidence=fresh['facts'],
                            original_invocations_preserved=True, original_run_replay=False,
                            new_run_authorized=False, legacy_released=False, ordinary_word_release=False,
                            application_inputs=0, final_paper_acceptance=False)
            if resumed_actor is not None:
                closeout['resumed_actor'] = resumed_actor
                closeout['original_actor_preserved'] = True
            saved = dict(original_result=row['result'], preinput_closeout=closeout)
            with self.broker.transaction():
                protocol.need(self.broker.get(ticket) == row, 'PREINPUT_TICKET_COMPARE_AND_SWAP_FAILED')
                contract = self.broker.contract(row['disposition'])
                legacy, resource = self.broker.legacy(contract)
                protocol.need(protocol.digest(legacy) == fresh['facts']['legacy_row_sha256']
                              and protocol.digest(resource) == fresh['facts']['legacy_resource_sha256'],
                              'LEGACY_PREIMAGE_CHANGED')
                protocol.read_pin(fresh['facts']['failure'])
                for ref in fresh['facts'].get('native_inventory_evidence', {}).values():
                    protocol.read_pin(ref)
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
    parser.add_argument('--resume-same-session', action='store_true',
                        help='Bind a fresh surface of the same original native thread, workspace and pane')
    args = parser.parse_args(argv)
    recovery = None
    try:
        protocol.need(sys.flags.ignore_environment and sys.dont_write_bytecode, 'PREINPUT_CLI_REQUIRES_BE')
        recovery = PreinputRecovery(args.root, readonly=args.command == 'preview')
        result = (recovery.preview(args.ticket, resume_same_session=args.resume_same_session)
                  if args.command == 'preview' else recovery.apply(
                      args.ticket, args.plan_sha256, resume_same_session=args.resume_same_session))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ResourceBusy, ValueError, TypeError, KeyError, OSError, sqlite3.Error,
            workspace_guard.WorkspaceScopeError) as exc:
        print(json.dumps(dict(outcome='OWNED_PREINPUT_REFUSED', reason=str(exc), input_operations=0,
                              original_run_replay=False, new_run_authorized=False, legacy_released=False), ensure_ascii=False))
        return 2
    finally:
        if recovery is not None:
            recovery.close()


if __name__ == '__main__':
    raise SystemExit(main())
