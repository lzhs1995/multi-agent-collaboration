"""Fixed owned-copy revision child; no Refresh, arbitrary adapter or foreign save."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile

SCOPE = 'OWNED_REVISION_ROUNDTRIP'
LABELS = ('tracked', 'accepted', 'rejected')
STAGES = ('OPEN', 'SAVE', 'CLOSE', 'REOPEN', 'RECLOSE')
WORD_LAUNCH = ['/usr/bin/open', '-g', '-a', '/Applications/Microsoft Word.app']
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
REVISION_TAGS = {
    'ins', 'del', 'moveFrom', 'moveTo', 'moveFromRangeStart', 'moveFromRangeEnd',
    'moveToRangeStart', 'moveToRangeEnd', 'customXmlInsRangeStart', 'customXmlInsRangeEnd',
    'customXmlDelRangeStart', 'customXmlDelRangeEnd', 'customXmlMoveFromRangeStart',
    'customXmlMoveFromRangeEnd', 'customXmlMoveToRangeStart', 'customXmlMoveToRangeEnd',
    'numberingChange', 'cellIns', 'cellDel', 'cellMerge',
}


def checked_bytes(ref):
    path = Path(ref['path'])
    if not path.is_absolute() or path.resolve(strict=True) != path or path.is_symlink():
        raise RuntimeError('BOOTSTRAP_CANONICAL_PIN_REQUIRED')
    before = path.stat(); raw = path.read_bytes(); after = path.stat()
    facts = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if facts(before) != facts(after):
        raise RuntimeError('BOOTSTRAP_PIN_IDENTITY_CHANGED')
    if len(raw) != ref['bytes'] or hashlib.sha256(raw).hexdigest() != ref['sha256']:
        raise RuntimeError('BOOTSTRAP_PIN_CHANGED')
    return raw


class PinnedSourceFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, refs):
        self.refs = {Path(ref['path']).stem: ref for ref in refs.values()}

    def find_spec(self, fullname, path=None, target=None):
        if fullname in self.refs:
            return importlib.util.spec_from_loader(fullname, self, origin=self.refs[fullname]['path'])
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        ref = self.refs[module.__name__]
        module.__file__ = ref['path']
        exec(compile(checked_bytes(ref), ref['path'], 'exec'), module.__dict__)


def bootstrap(args):
    if not sys.flags.ignore_environment or not sys.dont_write_bytecode:
        raise RuntimeError('RESTRICTED_CHILD_REQUIRES_BE')
    root = Path(args.root).resolve(strict=True)
    db = sqlite3.connect((root / 'queue.sqlite3').as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        deadline = time.monotonic() + 3
        while True:
            value = db.execute('SELECT * FROM word_restricted_tickets WHERE id=?', (args.ticket,)).fetchone()
            if value is None:
                raise RuntimeError('ORIGINAL_RESTRICTED_TICKET_REQUIRED')
            row = dict(value)
            if row['pgid'] == os.getpid():
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('ORIGINAL_CHILD_REGISTRATION_UNAVAILABLE')
            time.sleep(.025)
        if not (row['status'] == 'ACTIVE' and row['token'] == args.token and row['nonce'] == args.nonce
                and row['invocations'] == 1 and os.getpid() == os.getpgrp() and row['parent_pid'] == os.getppid()):
            raise RuntimeError('ORIGINAL_BROKER_CHILD_REQUIRED')
        plan_ref = json.loads(row['plan'])
        plan = json.loads(checked_bytes(plan_ref).decode('utf-8'))
    finally:
        db.close()
    if plan.get('operation') != SCOPE:
        raise RuntimeError('EXACT_INITIAL_REVISION_OPERATION_REQUIRED')
    refs = plan['controller_pins']
    if Path(refs['word_restricted_revision.py']['path']) != Path(__file__).resolve():
        raise RuntimeError('ORIGINAL_RESTRICTED_ENTRYPOINT_REQUIRED')
    for ref in refs.values():
        checked_bytes(ref)
    sys.meta_path.insert(0, PinnedSourceFinder(refs))
    import word_restricted_broker as protocol
    import word_restricted_pdf as native
    broker = protocol.RestrictedWordBroker(root)
    try:
        row, verified = broker.require(args.ticket, args.token, child=True)
        if row['nonce'] != args.nonce or verified != plan:
            raise RuntimeError('ORIGINAL_CHILD_PLAN_CHANGED')
        return protocol, native, broker, plan_ref, plan
    except BaseException:
        broker.close()
        raise


def quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def owned_path(plan, label):
    if label not in LABELS:
        raise RuntimeError('UNKNOWN_OWNED_REVISION_LABEL')
    if plan.get('operation') != SCOPE or uuid.UUID(plan['stage_id']).hex != plan['stage_id']:
        raise RuntimeError('EXACT_INITIAL_STAGING_SCOPE_REQUIRED')
    return Path.home() / 'Library/Containers/com.microsoft.Word/Data/Documents/wordrev-pdf' / (
        'revision_' + plan['stage_id']) / ('wordrev_' + plan['stage_id'][:12] + '_' + label + '.docx')


def expected_revision_script(plan, label, stage):
    owned = owned_path(plan, label)
    binding = ('set docRef to document ' + quote(owned.name) + '\n'
               'if (posix full name of docRef) is not ' + quote(owned) +
               ' then error "Owned document path mismatch"\n')
    actions = {'tracked': '', 'accepted': 'accept all revisions docRef\n',
               'rejected': 'reject all revisions docRef\n'}
    bodies = {
        'OPEN': ('if exists document ' + quote(owned.name) + ' then error "TARGET_ALREADY_OPEN"\n'
                 'open (POSIX file ' + quote(owned) + ') add to recent files false\n' + binding, 180),
        'SAVE': (binding + actions[label] + 'set track revisions of docRef to ' +
                 ('true' if label == 'tracked' else 'false') + '\n'
                 'set print revisions of docRef to false\nset show revisions of docRef to false\nsave docRef\n'
                 'if (saved of docRef) is false then error "UNSAVED_OWNED_DOCUMENT"\n', 180),
        'CLOSE': (binding + 'close docRef saving yes\n', 30),
        'REOPEN': ('open (POSIX file ' + quote(owned) + ') read only true add to recent files false\n' +
                   binding + 'if (saved of docRef) is false then error "UNSAVED_REOPEN"\n', 180),
        'RECLOSE': (binding + 'close docRef saving no\n', 30),
    }
    if stage not in bodies:
        raise RuntimeError('UNKNOWN_OWNED_REVISION_STAGE')
    body, seconds = bodies[stage]
    return ('with timeout of ' + str(seconds) + ' seconds\ntell application "Microsoft Word"\n' +
            body + 'return ' + quote(stage) + '\nend tell\nend timeout\n').encode('utf-8')


def validate_revision_stage(plan, evidence_root, label, stage, path):
    path = Path(path)
    expected = Path(evidence_root) / label / (stage.lower() + '.applescript')
    if path != expected or path.is_symlink() or path.read_bytes() != expected_revision_script(plan, label, stage):
        raise RuntimeError('EXACT_OWNED_REVISION_SCRIPT_REQUIRED')


def revision_headers(path):
    result = {}
    with zipfile.ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            if not name.startswith('word/') or not name.endswith('.xml'):
                continue
            rows = []
            for node in ET.fromstring(archive.read(name)).iter():
                if not node.tag.startswith(W):
                    continue
                local = node.tag[len(W):]
                if local in REVISION_TAGS or local.endswith('PrChange') or local in ('tblGridChange', 'sectPrChange'):
                    rows.append((local, tuple(sorted(node.attrib.items()))))
            if rows:
                result[name] = rows
    return result


class Driver:
    def __init__(self, protocol, native, broker, args, plan_ref, plan):
        self.protocol, self.native, self.args, self.plan = protocol, native, args, plan
        self.base = native.Driver(protocol, broker, args, plan_ref, plan)
        self.root = self.base.root
        self.plan_ref = plan_ref
        self.may_be_open = False
        self.current_owned = None
        self.runtime = None
        self.root_identity = None
        self.stage_identity = None
        self.initial_job = self.protocol.object_bytes(self.protocol.read_pin(plan['initial_job']))
        self.semantic_policy = self.initial_job.get('tracked_validation')
        self.semantic_module = None
        if self.semantic_policy is not None:
            import word_semantic_contract
            word_semantic_contract.validate_policy(self.semantic_policy)
            self.semantic_module = word_semantic_contract

    def save(self, name, value):
        return self.base.save(name, value)

    def verify_semantic_views(self, source, name):
        inputs = {label: self.protocol.read_pin(ref)
                  for label, ref in self.initial_job['validation_inputs'].items()}
        source_bytes = self.protocol.read_pin(source)
        result = self.semantic_module.verify_views(source_bytes, inputs, self.semantic_policy)
        self.protocol.read_pin(source)
        for ref in self.initial_job['validation_inputs'].values():
            self.protocol.read_pin(ref)
        receipt = self.save(name, dict(result, source=source,
                            validation_inputs=self.initial_job['validation_inputs'],
                            policy=self.semantic_policy))
        self.protocol.need(result['all_pass'], 'TRACKED_SOURCE_VIEW_SEMANTICS_CHANGED')
        return receipt

    def verify_clean_view(self, saved, label):
        ref = self.initial_job['validation_inputs'][label]
        source_bytes, output_bytes = self.protocol.read_pin(ref), self.protocol.read_pin(saved)
        result = self.semantic_module.compare(source_bytes, output_bytes,
                  expected_accept=(label == 'accepted'), actual_accept=(label == 'accepted'),
                  page_fields=self.semantic_policy['page_cache_fields'][label],
                  require_page_cache=(label == 'accepted'))
        self.protocol.read_pin(ref); self.protocol.read_pin(saved)
        receipt = self.save(label + '/source-view-semantics.json', dict(result, source=ref, output=saved))
        self.protocol.need(result['all_pass'], 'CLEAN_SOURCE_VIEW_SEMANTICS_CHANGED')
        return receipt

    def guard(self):
        row = self.base.guard()
        if self.root_identity is not None:
            s = self.root.lstat()
            self.protocol.need(not self.root.is_symlink() and [s.st_dev, s.st_ino] == self.root_identity,
                               'INITIAL_EVIDENCE_DIRECTORY_CHANGED')
        if self.stage_identity is not None:
            p = owned_path(self.plan, 'tracked').parent; s = p.lstat()
            self.protocol.need(not p.is_symlink() and [s.st_dev, s.st_ino] == self.stage_identity,
                               'INITIAL_STAGING_DIRECTORY_CHANGED')
        return row

    def pdf_runner(self, argv, **kwargs):
        self.guard()
        return self.base.runner(argv, **kwargs)

    def _word_pid(self):
        binding = self.native.app_binding('Microsoft Word', allow_absent=True, timeout=5)
        return None if binding is None else binding['pid']

    def bind_apps(self):
        self.guard()
        if self._word_pid() is not None:
            self.base.apps = self.native.app_bindings()
            self.save('application-bindings.json', {'bindings': self.base.apps, 'word_launch_requested': False})
            return
        self.protocol.need(self.plan['start_word_if_absent'], 'WORD_NOT_RUNNING_START_NOT_AUTHORIZED')
        # A single fixed background launch is part of the original approved Word
        # task. It cannot close or save restored/foreign documents. Host success
        # is followed by process binding and a complete native baseline.
        before = self.native.app_binding('Zotero', timeout=5)
        self.save('word-launch-intent.json', {'argv': WORD_LAUNCH, 'plan': self.plan_ref,
                  'prior_word': 'NOT_RUNNING', 'zotero_binding': before, 'replay_allowed': False})
        row = self.guard()
        remaining = row['expires'] - time.time() - 5
        self.protocol.need(remaining > 0, 'ORIGINAL_NATIVE_BUDGET_EXHAUSTED')
        result = subprocess.run(WORD_LAUNCH, capture_output=True, text=True, timeout=min(15, remaining))
        self.save('word-launch-host-result.json', {'returncode': result.returncode,
                  'stdout': result.stdout, 'stderr': result.stderr, 'native_receipt_proven': False})
        self.protocol.need(result.returncode == 0, 'WORD_LAUNCH_HOST_REFUSED')
        deadline = min(time.monotonic() + 10, time.monotonic() + max(0, row['expires'] - time.time() - 5))
        while self._word_pid() is None:
            self.protocol.need(time.monotonic() < deadline, 'WORD_LAUNCH_PROCESS_UNOBSERVED')
            time.sleep(.1)
        self.base.apps = self.native.app_bindings()
        self.protocol.need(self.base.apps['Zotero'] == before, 'ORIGINAL_ZOTERO_BINDING_CHANGED')
        self.save('application-bindings.json', {'bindings': self.base.apps, 'word_launch_requested': True,
                  'foreign_state': 'BASELINE_REQUIRED_AFTER_AUTHORIZED_START', 'no_foreign_save_or_close': True})

    def copy(self, ref, target):
        raw = self.protocol.read_pin(ref)
        with Path(target).open('xb') as f:
            f.write(raw); f.flush(); os.fsync(f.fileno())
        self.protocol.read_pin(ref)
        copied = self.protocol.pin(target)
        self.protocol.need(copied['bytes'] == ref['bytes'] and copied['sha256'] == ref['sha256'],
                           'EXACT_OWNED_COPY_REQUIRED')
        return copied

    def step(self, label, stage):
        guard_started = time.monotonic()
        row = self.guard()
        guard_seconds = time.monotonic() - guard_started
        path = self.root / label / (stage.lower() + '.applescript')
        raw = expected_revision_script(self.plan, label, stage)
        with path.open('xb') as f:
            f.write(raw); f.flush(); os.fsync(f.fileno())
        validate_revision_stage(self.plan, self.root, label, stage, path)
        ref = self.protocol.pin(path)
        event = self.root / label / (stage.lower() + '-intent.json')
        with event.open('x', encoding='utf-8') as f:
            json.dump({'stage': stage, 'owned': str(owned_path(self.plan, label)),
                       'script': ref, 'plan': self.plan_ref, 'replay_allowed': False}, f)
            f.write('\n'); f.flush(); os.fsync(f.fileno())
        remaining = row['expires'] - time.time() - 5
        self.protocol.need(remaining > 0, 'ORIGINAL_NATIVE_BUDGET_EXHAUSTED')
        native_started_epoch, native_started = time.time(), time.monotonic()
        result = subprocess.run(['/usr/bin/osascript', str(path)], capture_output=True, text=True,
                                timeout=min(195 if stage in ('OPEN', 'SAVE', 'REOPEN') else 45, remaining))
        native_seconds = time.monotonic() - native_started
        receipt = self.save(label + '/' + stage.lower() + '-result.json', {
            'stage': stage, 'script': ref, 'returncode': result.returncode,
            'stdout': result.stdout, 'stderr': result.stderr,
            'pre_call_guard_seconds': guard_seconds,
            'native_started_epoch': native_started_epoch, 'native_process_seconds': native_seconds,
            'timing_scope': 'PRE_CALL_GUARD_AND_OSASCRIPT_ONLY_NOT_WHOLE_TASK',
            'owned_document': str(owned_path(self.plan, label))})
        self.protocol.need(result.returncode == 0 and result.stdout.strip() == stage,
                           'OWNED_REVISION_NATIVE_STAGE_UNCONFIRMED')
        self.protocol.read_pin(ref); self.guard()
        return receipt

    def roundtrip(self, label, source):
        target = owned_path(self.plan, label)
        self.current_owned = target
        evidence = self.root / label; evidence.mkdir()
        original = self.copy(source, target)
        before = revision_headers(target)
        self.protocol.need(bool(before), 'INITIAL_TRACKED_REVISIONS_REQUIRED')
        self.save(label + '/owned-input.json', {'source': source, 'copy': original,
                  'action': label, 'revision_headers': before})
        receipts = []
        self.may_be_open = True
        receipts.append(self.step(label, 'OPEN'))
        receipts.append(self.step(label, 'SAVE'))
        receipts.append(self.step(label, 'CLOSE'))
        self.may_be_open = False
        saved = self.protocol.pin(target)
        self.may_be_open = True
        receipts.append(self.step(label, 'REOPEN'))
        receipts.append(self.step(label, 'RECLOSE'))
        self.may_be_open = False
        self.protocol.need(self.protocol.pin(target) == saved, 'REOPEN_CHANGED_SAVED_FILE')
        after = revision_headers(target)
        semantics = None
        if self.semantic_module is not None:
            display = self.semantic_module.revision_display_state(self.protocol.read_pin(saved))
            self.save(label + '/revision-display-state.json', dict(display, output=saved))
            self.protocol.need(display['track_revisions'] == (label == 'tracked')
                               and display['markup_hidden'], 'NATIVE_REVISION_DISPLAY_STATE_CHANGED')
        if label == 'tracked':
            if self.semantic_module is None:
                self.protocol.need(after == before, 'ORIGINAL_REVISION_HEADERS_CHANGED')
            else:
                self.save(label + '/raw-revision-header-differences.json', {
                    'before': before, 'after': after, 'equal': after == before,
                    'policy': self.semantic_policy,
                    'source_semantics_verification': 'REQUIRED_BEFORE_ACCEPT_REJECT'})
                semantics = self.verify_semantic_views(saved, label + '/source-view-semantics.json')
        else:
            self.protocol.need(not after, 'NATIVE_REVISION_RESIDUE')
            if self.semantic_module is not None:
                semantics = self.verify_clean_view(saved, label)
        output = self.copy(saved, self.plan['revision_outputs'][label])
        self.save(label + '/roundtrip-result.json', {'source': source, 'output': output, 'saved': saved,
                  'stages': receipts, 'owned_document_closed': True,
                  'original_revision_headers_equal': label == 'tracked' and after == before,
                  'source_semantics_verification': ('SOURCE_BOUND_DUAL_VIEW_PASS' if semantics is not None
                                                    else 'PENDING_PARENT_SOURCE_CONTRACT'),
                  'semantic_receipt': semantics, 'zotero_refreshed': False})
        return output, saved

    def export_pdf(self, service, accepted):
        self.base.session = service.ExportSession(accepted['path'], self.plan['output'], runner=self.pdf_runner)
        self.current_owned = self.base.session.owned
        self.may_be_open = True
        result = self.base.session.export()
        self.may_be_open = result.get('cleanup') != 'PASS'
        exported = self.save('service-result.json', result)
        self.protocol.need(result.get('success') is True and result.get('cleanup') == 'PASS'
                           and result.get('input_unchanged') is True and result.get('staged_docx_unchanged') is True,
                           'NATIVE_OWNED_EXPORT_INCOMPLETE_PRESERVED')
        self.protocol.need(not self.base.session.staging.exists(), 'OWNED_PDF_STAGING_CLEANUP_REQUIRED')
        return result, exported

    def execute(self):
        self.root.mkdir(exist_ok=False)
        s = self.root.stat(); self.root_identity = [s.st_dev, s.st_ino]
        self.guard()
        if self.semantic_module is not None:
            # Reject a known bad new source before launching or querying Word.
            self.verify_semantic_views(self.plan['input'], 'source-pre-native-semantics.json')
        self.runtime = self.native.load_external('word_runtime', self.plan['service_pins']['word_runtime.py'])
        service = self.native.load_external('word_pdf_service', self.plan['service_pins']['word_pdf_service.py'])
        with ExitStack() as stack:
            stack.enter_context(self.runtime.word_lock('restricted-revision:' + self.args.ticket, timeout=30))
            stack.enter_context(self.protocol.os_lock(Path.home() / '.cmuxterm/locks/desktop-focus.lock'))
            self.bind_apps()
            before, pre = self.base.observe('PRE')
            self.save('native-intent.json', {'ticket': self.args.ticket, 'nonce': self.args.nonce,
                      'plan': self.plan_ref, 'input': self.plan['input'], 'before': before, 'pre': pre,
                      'operation': SCOPE, 'source_semantics_verification': 'PENDING_PARENT_SOURCE_CONTRACT'})
            staging = owned_path(self.plan, 'tracked').parent
            staging.parent.mkdir(parents=True, exist_ok=True)
            self.protocol.need(staging.parent.resolve(strict=True) == staging.parent, 'CANONICAL_STAGING_PARENT_REQUIRED')
            staging.mkdir(exist_ok=False)
            s = staging.stat(); self.stage_identity = [s.st_dev, s.st_ino]
            outputs, staged = {}, []
            outputs['tracked'], saved = self.roundtrip('tracked', self.plan['input']); staged.append(saved)
            for label in ('accepted', 'rejected'):
                outputs[label], saved = self.roundtrip(label, outputs['tracked']); staged.append(saved)
            # Each generated staging copy has a durable byte-identical artifact.
            # Remove only those exact files after their native close/reopen proof.
            for ref in staged:
                self.protocol.read_pin(ref)
                label = next(label for label in LABELS if str(owned_path(self.plan, label)) == ref['path'])
                self.protocol.need(self.protocol.read_pin(outputs[label]) == self.protocol.read_pin(ref), 'STAGING_BACKUP_NOT_IDENTICAL')
                Path(ref['path']).unlink()
            staging.rmdir()
            self.stage_identity = None
            result, exported = self.export_pdf(service, outputs['accepted'])
            after, post = self.base.observe('POST')
            self.protocol.need(after == before, 'FOREIGN_OBSERVATION_CHANGED_PRESERVED')
            self.protocol.read_pin(self.plan['input'])
            report = self.save('restricted-result.json', {
                'ticket': self.args.ticket, 'nonce': self.args.nonce, 'plan': self.plan_ref,
                'operation': SCOPE, 'initial_job': self.plan['initial_job'], 'input': self.plan['input'],
                'revision_outputs': outputs, 'pdf': self.protocol.pin(self.plan['output']),
                'pages': result['pages'], 'owned_cleanup': True, 'foreign_observation_preserved': True,
                'foreign_saved': 'UNKNOWN', 'pending': False, 'legacy_released': False,
                'zotero_refreshed': False, 'zotero_internal_idle_verified': False,
                'source_semantics_verification': ('SOURCE_BOUND_DUAL_VIEW_PASS' if self.semantic_module is not None
                                                   else 'PENDING_PARENT_SOURCE_CONTRACT'),
                'final_paper_acceptance': False, 'service': exported, 'pre': pre, 'post': post})
            return {'status': 'OWNED_REVISION_COMPLETE_WITH_FOREIGN_UNKNOWN', 'ticket': self.args.ticket,
                    'result': report, 'source_semantics_verification': ('SOURCE_BOUND_DUAL_VIEW_PASS'
                          if self.semantic_module is not None else 'PENDING_PARENT_SOURCE_CONTRACT'),
                    'final_paper_acceptance': False}

    def preserve_failure(self, exc):
        if self.may_be_open and self.runtime is not None:
            self.runtime.mark_word_pending({'owner': 'restricted-revision:' + self.args.ticket,
                'owned_document': str(self.current_owned), 'evidence_dir': str(self.root),
                'status': 'UNRESOLVED_NATIVE_OPERATION', 'retry_permitted': False})
        if self.root.is_dir():
            self.save('failure.json', {'reason': str(exc), 'error_type': type(exc).__name__,
                      'may_be_open': self.may_be_open, 'owned_document': str(self.current_owned),
                      'retry_permitted': False, 'no_foreign_save_or_close': True})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'ticket', 'token', 'nonce'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args(argv)
    broker = driver = None
    try:
        protocol, native, broker, ref, plan = bootstrap(args)
        driver = Driver(protocol, native, broker, args, ref, plan)
        result = driver.execute()
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        if driver:
            try:
                driver.preserve_failure(exc)
            except Exception as recording:
                print(json.dumps({'failure_record_error': str(recording), 'no_retry': True}), file=sys.stderr)
        print(json.dumps({'status': 'RESTRICTED_NATIVE_PENDING', 'reason': str(exc),
                          'error_type': type(exc).__name__, 'no_retry': True,
                          'no_foreign_save_or_close': True, 'legacy_released': False}, ensure_ascii=False), file=sys.stderr)
        return 2
    finally:
        if broker:
            broker.close()


if __name__ == '__main__':
    raise SystemExit(main())
