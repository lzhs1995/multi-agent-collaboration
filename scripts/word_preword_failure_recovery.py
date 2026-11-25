"""Settle one pinned legacy pre-Word failure; never run Word or Zotero.

Ordinary Broker.release and its six-zero contract are deliberately untouched.
Only the built-in S39 profile is supported. Tests replace private OS readers;
the public API/CLI accepts no evidence provider, validator or alternate root.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import time
import types
import uuid

PROFILE = 'S39_INIT_ESCAPE_THEN_AX_DENIED_V1'
PROFILE_SHA = '892c3f09139240f94d2e69d85d0fb98ae6f6b516b4620df3965f49ee17aff6ce'
TICKET = '2adbf3c0-1928-497a-9a21-5ccc44421b80'
TASK = 'dissertation-postdelivery-20261110-v1'
SCHEMA = 'word-owned-preword-failure-release-v1'
SIX_ZERO = dict(documents=0, windows=0, modal=False, zotero_current_doc=False,
                zotero_current_window=False, pending=False)


class Refused(RuntimeError):
    pass


def need(ok, reason):
    if not ok:
        raise Refused(reason)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def regular(path):
    path = Path(path)
    need(path.is_absolute(), 'ABSOLUTE_PATH_REQUIRED')
    for parent in path.parents:
        need(not parent.is_symlink(), 'SYMLINK_PARENT_REFUSED')
    info = path.lstat()
    need(stat.S_ISREG(info.st_mode) and not path.is_symlink(), 'REGULAR_FILE_REQUIRED')
    return info


def identity(path):
    info = regular(path)
    return [info.st_dev, info.st_ino]


def pin(path):
    path = Path(path)
    before = regular(path)
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        need((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino), 'FILE_REPLACED')
        raw = stream.read()
        after = os.fstat(stream.fileno())
    final = regular(path)
    need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
         (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) ==
         (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns), 'FILE_CHANGED')
    return dict(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def checked(ref):
    need(pin(ref['path']) == ref, 'PIN_CHANGED:' + ref['path'])
    return Path(ref['path'])


def read(ref):
    path = checked(ref)
    value = json.loads(path.read_bytes())
    checked(ref)
    return value


def absent(path):
    need(not os.path.lexists(path), 'EXPECTED_ABSENT:' + str(path))


def directory(path):
    path = Path(path)
    need(path.is_absolute() and path.is_dir() and not path.is_symlink(), 'DIRECTORY_REQUIRED')
    for parent in path.parents:
        need(not parent.is_symlink(), 'SYMLINK_PARENT_REFUSED')
    return path


def tree(path):
    path = directory(path)
    entries = {}
    for child in sorted(path.rglob('*')):
        need(len(entries) < 512 and not child.is_symlink(), 'BOUNDED_REGULAR_TREE_REQUIRED')
        entries[str(child.relative_to(path))] = (
            dict(kind='directory') if child.is_dir() else dict(kind='file', **pin(child)))
    return entries


def _profile():
    path = Path(__file__).resolve().parent / 'fixtures/s39-preword-failure-profile-v1.json'
    ref = pin(path)
    need(ref['sha256'] == PROFILE_SHA, 'BUILTIN_PROFILE_CHANGED')
    value = read(ref)
    need(value['profile'] == PROFILE and value['ticket'] == TICKET and value['task'] == TASK,
         'BUILTIN_SCOPE_CHANGED')
    return value


def validate_history(p):
    """Verify immutable bytes AND their causal relation; no original code runs."""
    for ref in p['extra_files']:
        checked(ref)
    values = {k: read(v) if not v['path'].endswith(('.py', '.applescript')) else checked(v)
              for k, v in p['files'].items()}
    for path, expected in p['trees'].items():
        need(tree(path) == expected, 'ORIGINAL_EVIDENCE_SET_CHANGED')
    for path in p['absent_paths']:
        absent(path)
    plan, cfg, result = values['plan'], values['config'], values['return']
    need(plan['ticket_id'] == p['ticket'] and plan['task_id'] == p['task']
         and plan['actor'] == plan['execution_actor'], 'ORIGINAL_PLAN_SCOPE_CHANGED')
    for key, actor in [('controller', plan['original_controller']['sid']),
                       ('control', plan['actor']['sid'])]:
        need(values[key]['status'] == 'ACTIVE' and values[key]['actor'] == actor
             and values[key]['task_id'] == p['task'], 'CONTROL_NOT_ACTIVE')
    need(result['ticket'] == p['ticket'] and result['task_id'] == p['task']
         and result['lease_after'] == p['expected_lease'], 'ORIGINAL_LEASE_CHANGED')
    r = result['result']
    need(type(r['exit_code']) is int and r['exit_code'] == 1 and r['pgid'] == 53851
         and r['process_group_empty'] is True and r['stdout'] == ''
         and 'RuntimeError: TEST_ESCAPED_ORIGINAL_GROUP' in r['stderr']
         and 'line 403, in launch_cycle' in r['stderr'], 'BUSINESS_TERMINAL_NOT_PROVEN')
    need(values['failure']['error'] == 'TEST_ESCAPED_ORIGINAL_GROUP'
         and values['failure']['entered'] is True and values['failure']['no_retry'] is True,
         'EXACT_INITIALIZATION_FAILURE_REQUIRED')
    need(values['spawn']['launcher_pid'] == 54918 and values['spawn']['pgid'] == 53851,
         'SPAWN_CHANGED')
    argv = ['/Applications/Zotero.app/Contents/MacOS/zotero', '-no-remote', '-profile',
            cfg['profile'], '-datadir', cfg['data'], '-ZoteroDebugText']
    need(values['start']['argv'] == argv and values['start']['no_retry'] is True,
         'TEST_START_CHANGED')
    closed, quit_intent = values['closed'], values['quit_intent']
    target = quit_intent['target']
    need(closed['status'] == 'EXACT_FAILED_TEST_NORMALLY_CLOSED_PENDING_RETAINED'
         and closed['target_pid'] == target['pid'] == 55836
         and closed['target_birth'] == target['birth'] == 'Fri Nov 20 19:42:32 2026'
         and target['command'] == ' '.join(argv)
         and closed['test_port_empty'] is True and closed['test_database_handles_empty'] is True
         and closed['original_business_success'] is False, 'EXACT_NORMAL_CLOSE_REQUIRED')
    need(values['quit']['returncode'] == 0 and values['quit']['stderr']['bytes'] == 0,
         'NORMAL_QUIT_NOT_CONFIRMED')
    checked(values['quit']['stdout']); checked(values['quit']['stderr'])
    h, archived = values['history'], values['archived']
    for key in ['settlement_context', 'reconciliation', 'reviewed_code', 'original_sources',
                'old_actor', 'old_seal', 'close_receipt']:
        checked(h[key])
    reviewed = read(h['reviewed_code'])
    for ref in reviewed['files']:
        checked(ref)
    checked(reviewed['original_sources'])
    for ref in h['original_files'].values():
        checked(ref)
    need(h['ticket'] == p['ticket'] and h['pending'] == p['pending']
         and archived['archive'] == p['files']['initial_pending']
         and archived['original']['sha256'] == closed['pending']['sha256']
         == archived['archive']['sha256']
         and archived['inode'] == h['initial_pending_inode'] == p['initial_pending_identity'][1]
         and identity(archived['archive']['path']) == p['initial_pending_identity']
         and archived['failure_preserved'] is True and archived['business_success'] is False,
         'INITIAL_PENDING_CHAIN_CHANGED')
    initial = values['initial_pending']
    need(initial['ticket'] == p['ticket'] and initial['task_id'] == p['task'],
         'INITIAL_PENDING_OWNER_CHANGED')
    observation = values['observer_result']; host = values['observer_host']
    need(observation['host'] == host and host['pid'] == host['pgid'] == 83331
         and observation['returncode'] == 1 and observation['stdout'] == ''
         and observation['group_empty'] is True, 'OBSERVER_NOT_TERMINAL')
    helper = p['files']['helper']['path']
    expected_error = helper + ':11354:11414: execution error: System Events發生錯誤：osascript不允許輔助取用。 (-25211)\n'
    need(observation['stderr'] == expected_error, 'EXACT_PREINPUT_AX_FAILURE_REQUIRED')
    source = Path(helper).read_text().encode('utf-16-le')
    need(source[11354*2:11414*2].decode('utf-16-le') ==
         'count of (every window of p whose name is "Citation Dialog")', 'AX_FAILURE_BOUNDARY_CHANGED')
    intent = values['observer_intent']
    need(intent['ticket'] == p['ticket'] and intent['task_id'] == p['task']
         and intent['argv'] == ['/usr/bin/osascript', helper, str(cfg['production']['pid']), h['original_attempt'] +
             '/observation/six-zero/ZOTERO-READONLY.js', 'async', '30']
         and intent['no_retry'] is True, 'OBSERVER_INPUT_CHANGED')
    for key in ['stop', 'observer_failed']:
        need(values[key]['error'] == 'OBSERVER_UNCONFIRMED:ZOTERO-RUNJS'
             and values[key]['no_retry'] is True, 'OBSERVER_FAILURE_CHANGED')
    need(values['stop']['original_pending_archived'] is True
         and values['stop']['business_success'] is False, 'OBSERVER_CHAIN_CHANGED')
    # Fixed source pins + complete original dispatch/observation trees establish
    # the two pre-input boundaries. No caller-provided "calls=0" is accepted.
    return values


def _actor(p, values):
    ref = p['files']['verifier']
    source = checked(ref)
    # -B prevents writing bytecode, not reading it. Execute the checked source
    # bytes directly so a stale/malicious __pycache__ is never consulted.
    raw = source.read_bytes()
    need(len(raw) == ref['bytes'] and hashlib.sha256(raw).hexdigest() == ref['sha256'],
         'ACTOR_SOURCE_CHANGED')
    verifier = types.ModuleType('original_s39_readonly_actor')
    verifier.__file__ = str(source)
    exec(compile(raw, str(source), 'exec'), verifier.__dict__)
    old = values['plan']['actor']
    observed = verifier.discover(p['files']['authorization'], old)
    actor = observed['actor']
    mutable = {'pid', 'birth', 'executable', 'argv'}
    need(set(old) == set(actor) and {k: v for k, v in old.items() if k not in mutable} ==
         {k: v for k, v in actor.items() if k not in mutable}, 'ORIGINAL_AUTHORITY_CHANGED')
    verifier.check(actor, p['files']['authorization'])
    checked(ref)
    return actor


def _run(argv, absent_ok=False):
    r = subprocess.run(argv, capture_output=True, text=True, timeout=15)
    need(not r.stderr and (r.returncode == 0 or
         (absent_ok and r.returncode == 1 and not r.stdout)), 'OS_OBSERVATION_UNCONFIRMED')
    return r.stdout


def _os_state(p, values):
    # Signal zero checks existence, never terminates or changes a process.
    for pid in [53851, 54918, 55836, 81821, 83331]:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise Refused('OWNED_PID_PRESENT_OR_REUSED:' + str(pid))
    for pgid in [53851, 83331]:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            pass
        else:
            raise Refused('OWNED_GROUP_PRESENT_OR_UNKNOWN')
    cfg = values['config']
    processes = _run(['/bin/ps', '-ww', '-axo', 'pid=,command='])
    process_rows = [line.strip().split(None, 1) for line in processes.splitlines()]
    need(process_rows and all(len(row) == 2 and row[0].isdigit() and int(row[0]) > 0
         and row[1].strip() for row in process_rows)
         and len({row[0] for row in process_rows}) == len(process_rows),
         'PROCESS_INVENTORY_UNCONFIRMED')
    markers = [cfg['profile'], cfg['data'], values['plan']['runner']['path'],
               p['files']['helper']['path'], str(Path(p['files']['stop']['path']).parent.parent /
               'reconcile_original_failure.py')]
    need(not any(marker in processes for marker in markers), 'OWNED_RUNNER_STILL_PRESENT')
    for path in [cfg['data'], cfg['profile']]:
        directory(path)
        need(_run(['/usr/sbin/lsof', '-nP', '-t', '+D', path], absent_ok=True) == '',
             'OWNED_FILE_HANDLE_REMAINS')
    need(_run(['/usr/sbin/lsof', '-nP', '-iTCP:23139', '-sTCP:LISTEN', '-t'], absent_ok=True) == '',
         'OWNED_LISTENER_REMAINS')
    return dict(owned_pids_absent=True, owned_groups_empty=True, owned_handles_empty=True,
                owned_port_empty=True, owned_runners_absent=True)


def pending(p, *, path=None, links=1):
    candidate = Path(path or p['pending']['path'])
    actual = pin(candidate)
    need(actual['bytes'] == p['pending']['bytes'] and actual['sha256'] == p['pending']['sha256']
         and identity(candidate) == p['pending_identity']
         and regular(candidate).st_nlink == links, 'PENDING_COMPARE_AND_SWAP_FAILED')
    value = read(actual)
    h = read(p['files']['history'])
    need(value['ticket'] == p['ticket'] and value['task_id'] == value['owner'] == p['task']
         and value['pid'] == 81821 and value['stage'] == 'six-zero-zotero-observation'
         and value['attempt'] == h['original_attempt'] + '/observation/six-zero'
         and value['status'] == 'UNKNOWN_NATIVE_RESULT'
         and value['error'] == 'OBSERVER_UNCONFIRMED:ZOTERO-RUNJS'
         and value['host'] == [] and value['no_retry'] is True, 'PENDING_OWNER_OR_BOUNDARY_CHANGED')
    return dict(pin=actual, identity=identity(candidate))


@contextmanager
def connection(p, writable=False):
    path = Path(p['root']) / 'queue.sqlite3'
    before = identity(path)
    db = sqlite3.connect(path.as_uri() + ('?mode=rw' if writable else '?mode=ro'),
                         uri=True, timeout=5, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        need(identity(path) == before, 'DATABASE_REPLACED')
        need(db.execute('PRAGMA synchronous').fetchone()[0] >= 2, 'DURABLE_SQLITE_COMMIT_REQUIRED')
        need(not db.execute("SELECT name FROM sqlite_master WHERE type='trigger' "
                            "AND tbl_name IN ('tickets','resources')").fetchall(), 'QUEUE_TRIGGER_UNSUPPORTED')
        yield db
        need(identity(path) == before, 'DATABASE_REPLACED')
    finally:
        db.close()


def rows(db, p):
    row = db.execute('SELECT * FROM tickets WHERE id=?', (p['ticket'],)).fetchone()
    resource = db.execute('SELECT * FROM resources WHERE name=?', ('word-zotero',)).fetchone()
    need(row is not None and resource is not None, 'ORIGINAL_QUEUE_ROW_REQUIRED')
    return dict(row), dict(resource)


def resource_check(p, resource):
    expected = [path for path in p['locks'] if path.endswith('/word-automation.lock')]
    need(json.loads(resource['external_locks']) == expected and
         digest(json.loads(resource['release_requirements'])) == digest(SIX_ZERO),
         'ORIGINAL_RESOURCE_CONFIGURATION_CHANGED')


def facts(p, *, check_pending=True):
    values = validate_history(p)
    actor = _actor(p, values)
    own = _os_state(p, values)
    need(all(v is True for v in own.values()) and len(own) == 5, 'OWNED_STATE_NOT_PROVEN')
    for path, expected in p['locks'].items():
        need(identity(path) == expected, 'ORIGINAL_LOCK_REPLACED')
    with connection(p) as db:
        row, resource = rows(db, p)
    resource_check(p, resource)
    if check_pending:
        need(row == p['expected_lease'], 'EXACT_ACTIVE_FAILED_TICKET_REQUIRED')
        pending(p)
    return dict(profile=p['profile'], profile_sha256=digest(p), source=pin(__file__),
                original_ticket=row, resource=resource, actor=actor, owned_state=own,
                database_identity=identity(Path(p['root']) / 'queue.sqlite3'),
                locks=p['locks'], evidence_sha256=digest([p['files'], p['extra_files'], p['trees']]),
                pending=p['pending'], pending_identity=p['pending_identity'])


@contextmanager
def locks(p):
    # Open existing inode only: never mkdir/create/replace shared lock files.
    with ExitStack() as stack:
        queue = str(Path(p['root']) / (hashlib.sha256(b'word-zotero').hexdigest() + '.lock'))
        need(queue in p['locks'], 'ORIGINAL_QUEUE_LOCK_REQUIRED')
        for path in [queue, *sorted(k for k in p['locks'] if k != queue)]:
            need(identity(path) == p['locks'][path], 'ORIGINAL_LOCK_REPLACED')
            fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
            stream = stack.enter_context(os.fdopen(fd, 'r+b'))
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held = os.fstat(fd)
            need([held.st_dev, held.st_ino] == identity(path) == p['locks'][path], 'HELD_LOCK_REPLACED')
        yield


def sync_dir(path):
    fd = os.open(str(directory(path)), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_new(path, value):
    path = Path(path)
    directory(path.parent)
    temporary = path.with_name('.' + path.name + '.unpublished-' + uuid.uuid4().hex)
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw[:len(raw)//2]); stream.flush()
        _checkpoint('writing:' + path.name)
        stream.write(raw[len(raw)//2:]); stream.flush(); os.fsync(stream.fileno())
    _checkpoint('publishing:' + path.name)
    # link is an atomic, non-overwriting publication of already durable bytes.
    os.link(temporary, path, follow_symlinks=False)
    _checkpoint('linked_unsynced:' + path.name)
    sync_dir(path.parent)
    _checkpoint('published:' + path.name)
    need(identity(temporary) == identity(path), 'PUBLICATION_INODE_CHANGED')
    temporary.unlink(); sync_dir(path.parent)
    return pin(path)


def _checkpoint(stage):
    """Private fault-injection seam; no production/CLI override."""


class Recovery:
    def __init__(self):
        self.p = _profile()

    def preview(self, ticket=TICKET, profile=PROFILE):
        need(ticket == self.p['ticket'] and profile == self.p['profile'], 'NAMED_PROFILE_ONLY')
        self._preintent()
        value = facts(self.p)
        return dict(outcome='OWNED_PREWORD_READ_ONLY_PLAN', facts=value,
                    plan_sha256=digest(value), input_operations=0, queue_changes=0,
                    global_word_state='NOT_ASSESSED', global_zotero_state='NOT_ASSESSED')

    def apply(self, plan_sha256, ticket=TICKET, profile=PROFILE):
        p = self.p
        need(ticket == p['ticket'] and profile == p['profile'], 'NAMED_PROFILE_ONLY')
        if (Path(p['settlement']) / 'COMPLETE.json').exists():
            with locks(p):
                intent = self._load_intent()
                need(intent['plan_sha256'] == plan_sha256, 'EXACT_PLAN_REQUIRED')
                self._validate_journal(intent)
                return read(pin(Path(p['settlement']) / 'COMPLETE.json'))
        first = self.preview(ticket, profile)
        need(first['plan_sha256'] == plan_sha256, 'EXACT_PLAN_REQUIRED')
        with locks(p):
            fresh = self.preview(ticket, profile)
            need(fresh == first, 'PREVIEW_PREIMAGE_CHANGED')
            out = Path(p['settlement'])
            directory(out.parent)
            if not out.exists():
                out.mkdir(mode=0o700); sync_dir(out.parent)
            _checkpoint('before_intent')
            intent = dict(schema=SCHEMA, profile=p['profile'], ticket=p['ticket'],
                          plan_sha256=plan_sha256, facts=fresh['facts'], created_at=time.time())
            self.intent_ref = write_new(out / 'INTENT.json', intent)
            _checkpoint('intent')
            return self._finish(intent)

    def _load_intent(self):
        out = directory(self.p['settlement'])
        self.intent_ref = pin(out / 'INTENT.json')
        intent = read(self.intent_ref)
        need(intent['schema'] == SCHEMA and intent['profile'] == self.p['profile']
             and intent['ticket'] == self.p['ticket']
             and intent['plan_sha256'] == digest(intent['facts'])
             and intent['facts']['source'] == pin(__file__)
             and intent['facts']['original_ticket'] == self.p['expected_lease']
             and intent['facts']['profile_sha256'] == digest(self.p), 'SETTLEMENT_INTENT_CHANGED')
        self._entries()
        return intent

    def _entries(self):
        out = directory(self.p['settlement'])
        need(out.stat().st_uid == os.getuid() and stat.S_IMODE(out.stat().st_mode) == 0o700,
             'PRIVATE_SETTLEMENT_DIRECTORY_REQUIRED')
        events = {'INTENT.json', 'PENDING-ARCHIVE.json', 'ARCHIVED.json', 'COMMITTED.json',
                  'CLEANUP-INTENT.json', 'COMPLETE.json'}
        names = set()
        for path in out.iterdir():
            info = regular(path)
            need(info.st_uid == os.getuid(), 'SETTLEMENT_FILE_OWNER_CHANGED')
            if path.name in events:
                names.add(path.name)
            else:
                match = re.fullmatch(r'\.(.+)\.unpublished-[0-9a-f]{32}', path.name)
                need(match and match[1] in events - {'PENDING-ARCHIVE.json'}
                     and info.st_nlink in (1, 2), 'SETTLEMENT_FILE_SET_CHANGED')
                # Unpublished bytes are retained as evidence, never parsed as
                # authority and never removed by recovery.
                pin(path)
        return names

    def _preintent(self):
        out = Path(self.p['settlement'])
        if not os.path.lexists(out):
            return
        need(not self._entries(), 'SETTLEMENT_ALREADY_STARTED')
        need(all(path.name.startswith('.INTENT.json.unpublished-') for path in out.iterdir()),
             'UNKNOWN_PREINTENT_FILES')

    def _proof(self, intent):
        p = self.p; out = Path(p['settlement'])
        need(read(self.intent_ref) == intent, 'SETTLEMENT_INTENT_CHANGED')
        return dict(schema=SCHEMA, profile=p['profile'], ticket=p['ticket'], task_id=p['task'],
                    settlement_intent=self.intent_ref, approved_plan_sha256=intent['plan_sha256'],
                    business_outcome='FAILED', word_plugin_calls=0, observer_input_operations=0,
                    owned_test_process_closed=True, original_failures_preserved=True,
                    original_invocations_preserved=True, new_execution_authorized=False,
                    global_word_state='NOT_ASSESSED', global_zotero_state='NOT_ASSESSED',
                    archive=pin(out / 'PENDING-ARCHIVE.json'),
                    settlement_commit=True,
                    pending_cleanup='REQUIRED_UNTIL_COMPLETE_RECEIPT',
                    completion_receipt=str(out / 'COMPLETE.json'))

    def _validate_current(self, intent):
        p = self.p
        need(read(self.intent_ref) == intent, 'SETTLEMENT_INTENT_CHANGED')
        now = facts(p, check_pending=False)
        old = intent['facts']
        need({k: v for k, v in now.items() if k != 'original_ticket'} ==
             {k: v for k, v in old.items() if k != 'original_ticket'}, 'SETTLEMENT_PREIMAGE_CHANGED')
        return now['original_ticket']

    def _finish(self, intent):
        """Only caller holding all original locks can enter this state machine."""
        p = self.p; out = Path(p['settlement']); archive = out / 'PENDING-ARCHIVE.json'
        live = Path(p['pending']['path'])
        row = self._validate_journal(intent)
        sync_dir(out)
        expected = intent['facts']['original_ticket']
        if not archive.exists():
            need(row == expected, 'ARCHIVE_MISSING_AFTER_COMMIT')
            pending(p)
            os.link(live, archive, follow_symlinks=False)
            sync_dir(out)
            _checkpoint('archive_link')
        live_present = os.path.lexists(live)
        pending(p, path=archive, links=2 if live_present else 1)
        if live_present:
            pending(p, links=2)
        archive_record = dict(archive=pin(archive), identity=p['pending_identity'],
                              original_pending=p['pending'], original_failure_preserved=True)
        self._event(out / 'ARCHIVED.json', archive_record)
        _checkpoint('archived')
        proof = self._proof(intent)
        proof_text = json.dumps(proof, ensure_ascii=False, sort_keys=True)
        committed = dict(expected, release_proof=proof_text)
        released = dict(committed, status='RELEASED')
        if row == expected:
            need(live_present, 'LIVE_PENDING_MISSING_BEFORE_COMMIT')
            with connection(p, writable=True) as db:
                db.execute('BEGIN IMMEDIATE')
                try:
                    current, resource = rows(db, p)
                    need(current == expected and resource == intent['facts']['resource'], 'QUEUE_CAS_FAILED')
                    self._validate_journal(intent)
                    pending(p, links=2); pending(p, path=archive, links=2)
                    columns = list(expected)
                    where = ' AND '.join('"' + col + '" IS ?' for col in columns)
                    changed = db.execute('UPDATE tickets SET release_proof=? WHERE ' + where,
                                         [proof_text, *[expected[k] for k in columns]])
                    need(changed.rowcount == 1, 'QUEUE_CAS_FAILED')
                    _checkpoint('before_commit')
                    db.execute('COMMIT')
                except BaseException:
                    if db.in_transaction:
                        db.execute('ROLLBACK')
                    raise
            _checkpoint('committed_db')
        else:
            need(row in (committed, released), 'UNRECOGNIZED_QUEUE_TERMINAL')
        self._event(out / 'COMMITTED.json', dict(ticket=committed, intent=pin(out / 'INTENT.json')))
        # Never infer commit from path disappearance. Verify the exact DB proof
        # and original intent before deleting only the matching live link.
        need(self._validate_current(intent) in (committed, released), 'COMMIT_READBACK_CHANGED')
        cleanup = dict(intent=pin(out / 'INTENT.json'), archive=pin(archive),
                       pending=p['pending'], identity=p['pending_identity'])
        self._event(out / 'CLEANUP-INTENT.json', cleanup)
        _checkpoint('cleanup_intent')
        self._validate_journal(intent)
        if os.path.lexists(live):
            pending(p, links=2); pending(p, path=archive, links=2)
            live.unlink()
            _checkpoint('unlinked_unsynced')
        # A previous invocation may have crashed after unlink but before fsync.
        sync_dir(live.parent)
        sync_dir(out)
        _checkpoint('unlinked')
        absent(live); pending(p, path=archive, links=1)
        if self._validate_current(intent) == committed:
            with connection(p, writable=True) as db:
                db.execute('BEGIN IMMEDIATE')
                try:
                    current, resource = rows(db, p)
                    need(current == committed and resource == intent['facts']['resource'], 'FINAL_QUEUE_CAS_FAILED')
                    self._validate_journal(intent)
                    absent(live); pending(p, path=archive, links=1)
                    changed = db.execute("UPDATE tickets SET status='RELEASED' WHERE id=? AND status='ACTIVE' AND release_proof=?",
                                         (p['ticket'], proof_text))
                    need(changed.rowcount == 1, 'FINAL_QUEUE_CAS_FAILED')
                    _checkpoint('before_release_commit')
                    db.execute('COMMIT')
                except BaseException:
                    if db.in_transaction:
                        db.execute('ROLLBACK')
                    raise
            _checkpoint('released_db')
        need(self._validate_current(intent) == released, 'FINAL_READBACK_CHANGED')
        result = self._result(intent, proof)
        self._event(out / 'COMPLETE.json', result)
        return result

    def _result(self, intent, proof):
        return dict(outcome='FAILED_PREWORD_RESOURCES_RELEASED', ticket=self.p['ticket'],
                      release_proof=proof, original_invocations=intent['facts']['original_ticket']['invocations'],
                      pending_archived=True, pending_absent=True, business_success=False,
                      input_operations=0, new_execution_authorized=False)

    def _validate_journal(self, intent):
        """Validate ALL published events before any commit or pending mutation."""
        p = self.p; out = Path(p['settlement']); archive = out / 'PENDING-ARCHIVE.json'
        names = self._entries()
        need('INTENT.json' in names, 'ORIGINAL_SETTLEMENT_INTENT_REQUIRED')
        row = self._validate_current(intent)
        original = intent['facts']['original_ticket']
        live = os.path.lexists(p['pending']['path'])
        if 'PENDING-ARCHIVE.json' not in names:
            need(row == original and names == {'INTENT.json'}, 'EVENTS_BEFORE_ARCHIVE')
            pending(p)
            return row
        pending(p, path=archive, links=2 if live else 1)
        if live:
            pending(p, links=2)
        expected_events = {'ARCHIVED.json': dict(archive=pin(archive), identity=p['pending_identity'],
                               original_pending=p['pending'], original_failure_preserved=True)}
        proof = self._proof(intent)
        committed = dict(original, release_proof=json.dumps(proof, ensure_ascii=False, sort_keys=True))
        released = dict(committed, status='RELEASED')
        need(row in (original, committed, released), 'UNRECOGNIZED_QUEUE_STATE')
        expected_events['COMMITTED.json'] = dict(ticket=committed, intent=pin(out / 'INTENT.json'))
        expected_events['CLEANUP-INTENT.json'] = dict(intent=pin(out / 'INTENT.json'), archive=pin(archive),
                            pending=p['pending'], identity=p['pending_identity'])
        expected_events['COMPLETE.json'] = self._result(intent, proof)
        order = list(expected_events)
        published = [name for name in order if name in names]
        need(published == order[:len(published)], 'NONPREFIX_SETTLEMENT_EVENTS')
        for name in published:
            need(read(pin(out / name)) == expected_events[name], 'SETTLEMENT_EVENT_CHANGED')
        if row == original:
            need(live and len(published) <= 1, 'EVENTS_AHEAD_OF_DATABASE_COMMIT')
        elif row == committed:
            need('ARCHIVED.json' in names and 'COMPLETE.json' not in names, 'SETTLEMENT_COMMIT_EVENT_MISMATCH')
            need(live or 'CLEANUP-INTENT.json' in names, 'MISSING_PENDING_WITHOUT_CLEANUP_INTENT')
        else:
            need(not live and 'CLEANUP-INTENT.json' in names, 'RELEASED_WITHOUT_CLEANUP')
        return row

    @staticmethod
    def _event(path, value):
        if os.path.lexists(path):
            need(read(pin(path)) == value, 'SETTLEMENT_EVENT_CHANGED')
        else:
            write_new(path, value)

    def inspect(self):
        """Read only. Even a partial intent never clears pending."""
        intent = self._load_intent()
        row = self._validate_journal(intent)
        out = Path(self.p['settlement'])
        return dict(outcome='ORIGINAL_SETTLEMENT_INSPECTED', ticket=row,
                    intent=pin(out / 'INTENT.json'),
                    events=sorted(x.name for x in out.iterdir()),
                    pending_present=os.path.lexists(self.p['pending']['path']),
                    queue_changes=0, input_operations=0)

    def recover(self, intent_sha256):
        """Continue this exact settlement only, never create another attempt."""
        intent = self._load_intent()
        original_ref = self.intent_ref
        need(original_ref['sha256'] == intent_sha256,
             'EXACT_ORIGINAL_SETTLEMENT_REQUIRED')
        with locks(self.p):
            need(self._load_intent() == intent and self.intent_ref == original_ref,
                 'SETTLEMENT_INTENT_CHANGED')
            return self._finish(intent)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['preview', 'apply', 'inspect-closeout', 'recover-closeout'])
    parser.add_argument('--ticket', required=True, choices=[TICKET])
    parser.add_argument('--profile', required=True, choices=[PROFILE])
    parser.add_argument('--plan-sha256')
    parser.add_argument('--intent-sha256')
    args = parser.parse_args(argv)
    need(sys.flags.dont_write_bytecode and sys.flags.ignore_environment, 'EXACT_BE_REQUIRED')
    tool = Recovery()
    if args.command == 'preview':
        result = tool.preview(args.ticket, args.profile)
    elif args.command == 'apply':
        result = tool.apply(args.plan_sha256, args.ticket, args.profile)
    elif args.command == 'inspect-closeout':
        result = tool.inspect()
    else:
        result = tool.recover(args.intent_sha256)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
