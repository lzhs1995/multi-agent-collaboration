"""Small file fixtures only: never import a release member or touch live state."""
import copy
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import executor_reask_protocol as protocol


REVIEWED = tuple(protocol.LEGACY)
REVIEWED_V2 = tuple(name for name in REVIEWED if name not in protocol.LEGACY_FILE_PROTOCOL)
REVIEWED_V3 = tuple(protocol.LEGACY_FILE_PROTOCOL)
PYTHON = str(Path(sys.executable).resolve())
OWNER = '11111111-1111-4111-8111-111111111111'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def identity(source, pins):
    members = protocol.LEGACY_FILES if source.parent.name in REVIEWED else protocol.FILES
    return dict(source=str(source), python=PYTHON,
                files={key: pins[key]['sha256'] for key in members})


def issuer(source, pins):
    return dict(path=str(source / protocol.SCHEDULER), python=PYTHON,
                sha256=pins[protocol.SCHEDULER]['sha256'], label=protocol.LABEL)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.home = Path(self.stack.enter_context(tempfile.TemporaryDirectory(
            prefix='reask-protocol-'))).resolve()
        self.stack.enter_context(patch.object(Path, 'home', return_value=self.home))
        self.base = self.home / '.local/share/multi-agent-collaboration/releases'
        self.releases = {name: self.make_release(name) for name in REVIEWED}
        legacy = {name: {key: pins[key]['sha256'] for key in (*protocol.LEGACY_FILES, protocol.SCHEDULER)}
                  for name, (_source, pins, _raw) in self.releases.items()}
        manifests = {name: digest(raw) for name, (_source, _pins, raw) in self.releases.items()}
        self.stack.enter_context(patch.dict(protocol.LEGACY, legacy, clear=True))
        self.stack.enter_context(patch.dict(protocol.LEGACY_MANIFESTS, manifests, clear=True))
        self.current = self.make_release('0.4.99-current-fixture', current=True,
                                         source=self.home / 'writable-checkout')
        self.stack.enter_context(patch.object(protocol, '__file__', str(
            self.current[0] / 'scripts/executor_reask_protocol.py')))
        for member in self.current[1]:
            (self.current[0] / member).chmod(0o644)
        self.current_scheduler = issuer(*self.current[:2])

    def write_pin(self, path, raw, executable=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.chmod(0o644)
        path.write_bytes(raw)
        path.chmod(0o555 if executable else 0o444)

    def make_release(self, name, *, current=False, source=None):
        source = source or self.base / name / 'source'
        members = protocol.FILES if current else protocol.LEGACY_FILES
        names = (*members, protocol.SCHEDULER, 'scripts/configuration_guardian.py',
                 'scripts/manage_install.py', 'README.md', 'bin/helper')
        pins = {}
        for member in names:
            # Valid manifest members, deliberately unsafe to execute/import.
            raw = ("raise AssertionError('release code executed')\n# " + name + '/' + member).encode()
            executable = member == 'bin/helper'
            self.write_pin(source / member, raw, executable)
            pins[member] = dict(bytes=len(raw), sha256=digest(raw), executable=executable)
        raw = json.dumps(dict(files=pins), sort_keys=True).encode()
        self.write_pin(source.parent / 'MANIFEST.json', raw)
        return source, pins, raw

    def episode(self, name=None, *, current=False):
        source, pins, _raw = self.current if current else self.releases[name or REVIEWED[0]]
        modern = current or name in protocol.LEGACY_FILE_PROTOCOL
        controller = identity(source, pins)
        continuation = dict(enabled=True, id='a' * 32, status='ACTIVE', overall_task='fixture task',
                            owner=OWNER, authorized_epoch=1.0, until_epoch=None,
                            authorization=dict(path=str(self.home / 'authorization.txt'),
                                               sha256='b' * 64, bytes=10),
                            controller=copy.deepcopy(controller))
        state = dict(version=3 if modern else 2, supervisor=OWNER,
                     controller=controller, continuation=continuation)
        record = dict(schema='executor-reask-enrollment-v2' if modern else 'executor-reask-enrollment-v1',
                      authorization='EXPLICIT_REASK_START', controller=copy.deepcopy(controller),
                      scheduler=issuer(source, pins))
        if modern:
            state['file_protocol'] = protocol.descriptor()
            record['file_protocol'] = protocol.descriptor()
        return state, record

    def assert_supported(self, state, record):
        self.assertEqual(protocol.state_controller(state, record), record['controller'])
        self.assertIs(protocol.supports_state(state, record), True)
        self.assertIs(protocol.supports_record(record, state, self.current_scheduler), True)

    def assert_rejected(self, state, record):
        self.assertIs(protocol.supports_state(state, record), False)
        self.assertIs(protocol.supports_record(record, state, self.current_scheduler), False)

    def test_reservation_inquiry_capability_is_current_controller_only(self):
        current, record = self.episode(current=True)
        current['continuation']['reservation_inquiries'] = True
        self.assert_supported(current, record)
        for name in REVIEWED:
            for value in (False, True):
                with self.subTest(release=name, value=value):
                    state, record = self.episode(name)
                    self.assert_supported(state, record)
                    state['continuation']['reservation_inquiries'] = value
                    self.assert_rejected(state, record)

    def test_reservation_inquiry_capability_requires_a_boolean(self):
        for value in (None, 1, 'true', [], {}):
            with self.subTest(value=value):
                state, record = self.episode(current=True)
                state['continuation']['reservation_inquiries'] = value
                self.assert_rejected(state, record)

    def test_reviewed_v2_originals_and_missing_top_level_controller(self):
        for name in REVIEWED_V2:
            with self.subTest(release=name):
                state, record = self.episode(name)
                self.assert_supported(state, record)
                del state['controller']
                self.assert_supported(state, record)
                self.assertTrue(protocol.supports_state(state))
                state['continuation'] = None
                self.assert_supported(state, record)
                self.assertFalse(protocol.supports_state(state))

    def test_reviewed_v3_original_and_current_reenrollment_keep_controller(self):
        for name in REVIEWED_V3:
            with self.subTest(release=name):
                state, record = self.episode(name)
                original = copy.deepcopy(state)
                self.assert_supported(state, record)
                record.update(scheduler=self.current_scheduler,
                              authorization='EXPLICIT_REASK_ENROLL_EXISTING')
                self.assert_supported(state, record)
                self.assertEqual(state, original)
                del state['controller']
                self.assert_rejected(state, record)

    def test_reviewed_v2_issuer_keeps_its_historical_controller_range(self):
        for name in REVIEWED_V3:
            for origin in (*REVIEWED_V2, name):
                with self.subTest(issuer=name, controller=origin):
                    state, record = self.episode(origin)
                    record.update(schema='executor-reask-enrollment-v2',
                                  file_protocol=protocol.descriptor(),
                                  scheduler=issuer(*self.releases[name][:2]),
                                  authorization='EXPLICIT_REASK_ENROLL_EXISTING')
                    self.assert_supported(state, record)
                    record['authorization'] = 'EXPLICIT_REASK_START'
                    self.assertIs(protocol.supports_record(record, state, self.current_scheduler),
                                  origin == name)
            state, record = self.episode(current=True)
            record.update(scheduler=issuer(*self.releases[name][:2]),
                          authorization='EXPLICIT_REASK_ENROLL_EXISTING')
            # The old issuer could not enroll the new CURRENT's controller.
            self.assertFalse(protocol.supports_record(record, state, self.current_scheduler))

    def test_reviewed_v3_requires_its_actual_schemas_and_both_descriptors(self):
        for name in REVIEWED_V3:
            for target, key, value in (
                    ('state', 'version', 2), ('state', 'file_protocol', {}),
                    ('record', 'file_protocol', {}),
                    ('record', 'schema', 'executor-reask-enrollment-v1'),
                    ('record', 'authorization', 'INFERRED_FROM_INSTALLATION')):
                with self.subTest(release=name, target=target, field=key):
                    state, record = self.episode(name)
                    (state if target == 'state' else record)[key] = value
                    self.assertFalse(protocol.supports_record(record, state, self.current_scheduler))
                    if target == 'state':
                        self.assertFalse(protocol.supports_state(state))
            state, record = self.episode(name)
            record['scheduler']['sha256'] = '0' * 64
            self.assertFalse(protocol.supports_record(record, state, self.current_scheduler))

    def test_reviewed_issuers_keep_older_controllers_without_reverse_authority(self):
        release32 = '0.4.32-c7338a400bf4'
        release33 = '0.4.33-09de16ceb823'
        release34 = '0.4.34-d333557fb498'
        for origin, scheduler, supported in (
                (release32, release33, True), (release33, release32, False),
                (release32, release34, True), (release34, release32, False),
                (release33, release34, True), (release34, release33, False)):
            with self.subTest(controller=origin, issuer=scheduler):
                state, record = self.episode(origin)
                original = copy.deepcopy((state, record['controller']))
                record.update(scheduler=issuer(*self.releases[scheduler][:2]),
                              authorization='EXPLICIT_REASK_ENROLL_EXISTING')
                self.assertIs(protocol.supports_state(state, record), True)
                self.assertIs(protocol.supports_record(record, state, self.current_scheduler),
                              supported)
                self.assertEqual((state, record['controller']), original)
                record['authorization'] = 'EXPLICIT_REASK_START'
                self.assertIs(protocol.supports_record(record, state, self.current_scheduler), False)

    def test_reviewed_v3_rechecks_complete_manifest_without_cached_trust(self):
        for name in REVIEWED_V3:
            source, _pins, manifest = self.releases[name]
            state, record = self.episode(name)
            self.assert_supported(state, record)
            member = source / 'README.md'
            raw = member.read_bytes()
            self.write_pin(member, b'X' + raw[1:])
            self.assert_rejected(state, record)
            self.write_pin(member, raw)
            self.assert_supported(state, record)
            self.write_pin(source.parent / 'MANIFEST.json', manifest + b'\n')
            self.assert_rejected(state, record)
            self.write_pin(source.parent / 'MANIFEST.json', manifest)
            self.assert_supported(state, record)

    def test_verified_current_v3_and_explicit_original_reenrollment(self):
        self.assert_supported(*self.episode(current=True))
        state, record = self.episode()
        record.update(schema='executor-reask-enrollment-v2', file_protocol=protocol.descriptor(),
                      scheduler=self.current_scheduler, authorization='EXPLICIT_REASK_ENROLL_EXISTING')
        self.assert_supported(state, record)
        # Only explicit re-enrollment may retain a different original interpreter.
        old_python = str(self.home / 'old-python')
        for value in (state['controller'], state['continuation']['controller'], record['controller']):
            value['python'] = old_python
        self.assert_supported(state, record)
        record['authorization'] = 'EXPLICIT_REASK_START'
        self.assertFalse(protocol.supports_record(record, state, self.current_scheduler))

    def test_no_old_import_execution_or_input_and_no_data_mutation(self):
        state, record = self.episode()
        before = copy.deepcopy((state, record))
        with patch.dict(sys.modules, {'executor_reask': None, 'configuration_guardian': None,
                                     'manage_install': None}), \
                patch('importlib.util.spec_from_file_location', side_effect=AssertionError('import')), \
                patch('subprocess.run', side_effect=AssertionError('subprocess')):
            self.assert_supported(state, record)
            self.assert_supported(*self.episode(current=True))
            for name in REVIEWED_V3:
                self.assert_supported(*self.episode(name))
        self.assertEqual((state, record), before)

    def test_raw_manifest_digest_cannot_be_redeclared(self):
        source, _pins, raw = self.releases[REVIEWED[0]]
        path = source.parent / 'MANIFEST.json'
        self.write_pin(path, raw + b'\n')
        self.assert_rejected(*self.episode())
        self.write_pin(path, raw)
        self.assert_supported(*self.episode())
        # Coherent attacker-supplied pins cannot replace the compiled trust root.
        manifest = json.loads(raw)
        payload = b'changed member'
        self.write_pin(source / 'README.md', payload)
        manifest['files']['README.md'].update(bytes=len(payload), sha256=digest(payload))
        self.write_pin(path, json.dumps(manifest).encode())
        self.assert_rejected(*self.episode())

    def test_every_manifest_member_is_checked_and_approvals_are_not_cached(self):
        source, pins, _raw = self.releases[REVIEWED[0]]
        state, record = self.episode()
        self.assert_supported(state, record)
        for member, pin in pins.items():
            with self.subTest(member=member):
                path = source / member
                raw = path.read_bytes()
                self.write_pin(path, b'X' + raw[1:], pin['executable'])
                self.assert_rejected(state, record)
                self.write_pin(path, raw, pin['executable'])
        self.assert_supported(state, record)

    def test_missing_writable_symlinked_or_special_members_are_refused(self):
        source, pins, raw = self.releases[REVIEWED[0]]
        manifest = source.parent / 'MANIFEST.json'
        manifest.chmod(0o644)
        self.assert_rejected(*self.episode())
        manifest.chmod(0o444)
        member = source / 'README.md'
        payload = member.read_bytes()
        member.chmod(0o644)
        self.assert_rejected(*self.episode())
        member.unlink()
        self.assert_rejected(*self.episode())
        target = self.home / 'same-content'
        self.write_pin(target, payload)
        member.symlink_to(target)
        self.assert_rejected(*self.episode())
        member.unlink()
        member.mkdir()
        self.assert_rejected(*self.episode())
        member.rmdir()
        self.write_pin(member, payload)
        script_dir = source / 'scripts'
        real_dir = source / 'real-scripts'
        script_dir.rename(real_dir)
        script_dir.symlink_to(real_dir, target_is_directory=True)
        self.assert_rejected(*self.episode())
        script_dir.unlink()
        real_dir.rename(script_dir)
        manifest.unlink()
        target = self.home / 'same-manifest'
        self.write_pin(target, raw)
        manifest.symlink_to(target)
        self.assert_rejected(*self.episode())

    def test_mode_and_byte_count_are_pins(self):
        source, pins, _raw = self.releases[REVIEWED[0]]
        script = source / 'bin/helper'
        script.chmod(0o444)
        self.assert_rejected(*self.episode())
        script.chmod(0o555)
        member = source / 'README.md'
        self.write_pin(member, member.read_bytes() + b'X')
        self.assert_rejected(*self.episode())

    def test_manifest_budget_path_and_pin_schema_fail_closed(self):
        source, _pins, raw = self.releases[REVIEWED[0]]
        for bad_name in ('../escape', '/absolute', './README.md', 'scripts//extra'):
            with self.subTest(member=bad_name):
                value = json.loads(raw)
                value['files'][bad_name] = dict(bytes=0, sha256=digest(b''), executable=False)
                changed = json.dumps(value).encode()
                self.write_pin(source.parent / 'MANIFEST.json', changed)
                # Supply an independent test trust root to exercise structural checks.
                with patch.dict(protocol.LEGACY_MANIFESTS, {REVIEWED[0]: digest(changed)}):
                    self.assert_rejected(*self.episode())
        self.write_pin(source.parent / 'MANIFEST.json', raw)
        with patch.object(protocol, 'MAX_RELEASE_FILES', 1):
            self.assert_rejected(*self.episode())
        with patch.object(protocol, 'MAX_RELEASE_BYTES', 1):
            self.assert_rejected(*self.episode())
        value = json.loads(raw)
        value['files']['README.md']['bytes'] = True
        changed = json.dumps(value).encode()
        self.write_pin(source.parent / 'MANIFEST.json', changed)
        with patch.dict(protocol.LEGACY_MANIFESTS, {REVIEWED[0]: digest(changed)}):
            self.assert_rejected(*self.episode())

    def test_descriptor_and_canonical_path_do_not_grant_unknown_v3_support(self):
        state, record = self.episode()
        state.update(version=3, file_protocol=protocol.descriptor())
        self.assert_rejected(state, record)
        unknown = self.make_release('0.4.100-unreviewed-fixture', current=True)
        controller = identity(*unknown[:2])
        state['controller'] = copy.deepcopy(controller)
        state['continuation']['controller'] = copy.deepcopy(controller)
        record.update(controller=controller, scheduler=issuer(*unknown[:2]),
                      schema='executor-reask-enrollment-v2', file_protocol=protocol.descriptor())
        self.assert_rejected(state, record)
        state['version'] = 2
        self.assert_rejected(state, record)
        state, record = self.episode(current=True)
        state['file_protocol']['sha256'] = '0' * 64
        self.assert_rejected(state, record)

    def test_continuation_must_be_complete_even_with_a_good_top_controller(self):
        for bad in ({}, [], False, '', {'controller': self.episode()[0]['controller']}):
            with self.subTest(continuation=bad):
                state, record = self.episode()
                state['continuation'] = bad
                self.assertIsNone(protocol.state_controller(state, record))
                self.assert_rejected(state, record)
        for field in self.episode()[0]['continuation']:
            with self.subTest(missing=field):
                state, record = self.episode()
                del state['continuation'][field]
                self.assert_rejected(state, record)

    def test_each_controller_candidate_must_match_in_full(self):
        for candidate in ('state', 'continuation', 'record'):
            with self.subTest(candidate=candidate):
                state, record = self.episode()
                other = self.episode(REVIEWED[1])[0]['controller']
                target = state if candidate == 'state' else record if candidate == 'record' else state['continuation']
                target['controller'] = other
                self.assertIsNone(protocol.state_controller(state, record))
                self.assert_rejected(state, record)
        state, record = self.episode()
        del record['controller']
        self.assert_rejected(state, record)
        state, record = self.episode(current=True)
        del state['controller']
        self.assert_rejected(state, record)

    def test_declared_six_member_combination_must_match_actual_pins(self):
        for member in protocol.LEGACY_FILES:
            with self.subTest(member=member):
                state, record = self.episode()
                for controller in (state['controller'], state['continuation']['controller'], record['controller']):
                    controller['files'][member] = '0' * 64
                self.assert_rejected(state, record)

    def test_scheduler_hash_source_python_label_and_producer_schema_are_bound(self):
        for field, value in (('sha256', '0' * 64), ('python', str(self.home / 'different-python')),
                             ('label', 'wrong'), ('path', str(self.releases[REVIEWED[1]][0] / protocol.SCHEDULER))):
            with self.subTest(field=field):
                state, record = self.episode()
                record['scheduler'][field] = value
                self.assertFalse(protocol.supports_record(record, state, self.current_scheduler))
        state, record = self.episode()
        record.update(schema='executor-reask-enrollment-v2', file_protocol=protocol.descriptor(),
                      authorization='EXPLICIT_REASK_ENROLL_EXISTING')
        self.assertFalse(protocol.supports_record(record, state, self.current_scheduler))
        state, record = self.episode(current=True)
        wrong = dict(self.current_scheduler, sha256='0' * 64)
        self.assertFalse(protocol.supports_record(record, state, wrong))
        record['file_protocol']['sha256'] = '0' * 64
        self.assertFalse(protocol.supports_record(record, state, self.current_scheduler))

    def test_current_fast_path_checks_actual_controller_and_execution_identity(self):
        state, record = self.episode(current=True)
        source, _pins, _raw = self.current
        self.assert_supported(state, record)  # Writable checkout, outside releases.
        member = source / 'scripts/executor_ready.py'
        payload = member.read_bytes()
        self.write_pin(member, b'X' + payload[1:])
        self.assert_rejected(state, record)
        self.write_pin(member, payload)
        with patch.object(protocol.sys, 'executable', str(self.home / 'different-python')):
            self.assert_rejected(state, record)
        scheduler = source / protocol.SCHEDULER
        payload = scheduler.read_bytes()
        self.write_pin(scheduler, b'X' + payload[1:])
        self.assertFalse(protocol.supports_record(record, state, self.current_scheduler))
        self.write_pin(scheduler, payload)
        self.assert_supported(state, record)

    def test_malformed_json_shapes_return_boolean_false(self):
        for value in (None, [], '', 0, {}, {'version': 2}, {'continuation': []}):
            with self.subTest(state=value):
                self.assertIs(protocol.supports_state(value), False)
                self.assertIs(protocol.supports_record(value, value, None), False)
        state, record = self.episode()
        for malformed in (None, [], {}, dict(record['controller'], files=list(protocol.FILES))):
            with self.subTest(controller=malformed):
                bad = copy.deepcopy(state)
                bad['controller'] = malformed
                self.assert_rejected(bad, record)


class InstalledReleasePinsTests(unittest.TestCase):
    def test_real_current_checkout_controller_and_provenance(self):
        # Exercise the real checkout without importing the concurrently edited
        # scheduler/reask modules or requiring an installed manifest for tests.
        source = Path(protocol.__file__).resolve().parents[1]
        pins = {key: dict(sha256=digest((source / key).read_bytes()))
                for key in (*protocol.FILES, protocol.SCHEDULER)}
        controller, scheduler = identity(source, pins), issuer(source, pins)
        state = dict(version=3, controller=controller, continuation=None,
                     file_protocol=protocol.descriptor())
        record = dict(schema='executor-reask-enrollment-v2', authorization='EXPLICIT_REASK_START',
                      file_protocol=protocol.descriptor(), controller=copy.deepcopy(controller),
                      scheduler=scheduler)
        self.assertIs(protocol.supports_state(state, record), True)
        self.assertIs(protocol.supports_record(record, state, scheduler), True)

    def test_reviewed_installed_files_without_copying_or_loading_code(self):
        base = Path.home() / '.local/share/multi-agent-collaboration/releases'
        if not all((base / name / 'MANIFEST.json').is_file() for name in REVIEWED):
            self.skipTest('reviewed installed releases unavailable')
        for name in REVIEWED:
            with self.subTest(release=name):
                source = base / name / 'source'
                pins = {key: dict(sha256=value) for key, value in protocol.LEGACY[name].items()}
                controller = identity(source, pins)
                modern = name in protocol.LEGACY_FILE_PROTOCOL
                state = dict(version=3 if modern else 2, controller=controller, continuation=None)
                record = dict(schema='executor-reask-enrollment-v2' if modern else 'executor-reask-enrollment-v1', authorization='EXPLICIT_REASK_START',
                              controller=copy.deepcopy(controller), scheduler=issuer(source, pins))
                if modern:
                    state['file_protocol'] = protocol.descriptor()
                    record['file_protocol'] = protocol.descriptor()
                self.assertIs(protocol.supports_state(state, record), True)
                self.assertIs(protocol.supports_record(record, state, record['scheduler']), True)


if __name__ == '__main__':
    unittest.main()
