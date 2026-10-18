"""Real archived continuation, binding, reader, locks and original writer.

Only external I/O is replaced. --original-controller validates the same tests
against one complete immutable historical release in a fresh process.
"""
import copy
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import sys
import unittest

import recover_bridge_probe_clear as clear

if __name__ == '__main__' and '--original-controller' in sys.argv:
    index = sys.argv.index('--original-controller')
    clear.continuation.original_modules(sys.argv[index + 1])
    del sys.argv[index:index + 2]

import cmux_bridge as bridge
import cmux_evidence_io as evidence
import cmux_input_mutex as mutex
import cmux_native_delivery as native
import mac_harness as harness
import test_bridge_continuation as fixture_support


class ProbeClearTests(unittest.TestCase):
    patch = fixture_support.BridgeContinuationTests.patch
    save = fixture_support.BridgeContinuationTests.save
    send = fixture_support.BridgeContinuationTests.send
    key = fixture_support.BridgeContinuationTests.key
    files = fixture_support.BridgeContinuationTests.files

    def read_screen(self, *args, **kwargs):
        screen = fixture_support.BridgeContinuationTests.read_screen(self, *args, **kwargs)
        return screen.replace('[claude-opus-5]', 'unknown footer') if self.unparsed and self.compose else screen

    def setUp(self):
        self.unparsed = True
        fixture_support.BridgeContinuationTests.setUp(self)
        self.args.apply = True
        result = clear.continuation.continue_probe(self.args, self.modules)
        self.assertEqual(result['status'], 'BRIDGE_CONTINUATION_FAILED_NO_REPLAY', result)
        self.assertEqual(result['probe']['clear_key_count'], 0)
        self.assertTrue(result['probe']['token_sent'])
        self.fixture.key.assert_not_called()
        self.unparsed = False
        self.args.publish_bridge_evidence = False
        self.args.archive_sha256 = None
        self.args.apply = False
        # Production arm_task already creates these participant lock files.
        # The fixture writes its marker directly, so initialize the real lock
        # infrastructure before asserting that rejected applies change no files.
        with harness._marker_update_lock(self.marker_path, self.marker):
            pass
        self.original_files = self.files()
        self.original_bridge = (self.root / 'bridge-test-evidence.json').read_bytes()
        self.preview = clear.recover_clear(self.args, self.modules)
        self.assertEqual(self.preview['status'], 'STATIC_ELIGIBLE_NO_INPUT', self.preview)
        self.args.archive_sha256 = self.preview['archive_sha256']
        self.fixture.send.reset_mock()
        self.fixture.key.reset_mock()
        self.caller.reset_mock()
        self.screen.reset_mock()

    def run_clear(self, apply=True, publish=False):
        self.args.apply = apply
        self.args.publish_bridge_evidence = publish
        value = clear.recover_clear(self.args, self.modules)
        self.fixture.send.assert_not_called()
        self.fixture.rpc.assert_not_called()
        self.assertEqual(value['submission_operations'], 0)
        self.assertEqual(value['probe_invocations'], 0)
        return value

    def refuse(self, error, *, publish=False):
        before = self.files()
        value = self.run_clear(publish=publish)
        self.assertEqual(value['status'], 'PROBE_CLEAR_UNAVAILABLE', value)
        expected = (error,) if isinstance(error, str) else error
        self.assertTrue(any(item in value['error'] for item in expected), value)
        self.assertFalse(value['archive_created'])
        self.fixture.key.assert_not_called()
        self.assertEqual(before, self.files())
        return value

    def test_static_preview_is_read_only(self):
        before = self.files()
        value = self.run_clear(apply=False)
        self.assertEqual(value['status'], 'STATIC_ELIGIBLE_NO_INPUT', value)
        self.assertEqual(before, self.files())
        self.screen.assert_not_called()
        self.caller.assert_not_called()
        self.fixture.key.assert_not_called()

    def test_clear_only_retains_all_original_bytes(self):
        value = self.run_clear()
        self.assertEqual(value['status'], 'PROBE_CLEARED_ONLY', value)
        self.assertEqual(self.compose, '')
        self.assertEqual(value['input_operations'], len(self.token) + 1)
        self.assertEqual(self.fixture.key.call_args_list,
                         [unittest.mock.call('target', k) for k in ['end'] + ['backspace'] * len(self.token)])
        self.assertEqual(value['bridge_writes'], 0)
        for path, pin in self.original_files.items():
            self.assertEqual(evidence.snapshot(Path(path)), pin)
        slot = self.root / clear.ARCHIVE
        self.assertEqual(json.loads((slot / 'RESULT.json').read_bytes()), value)
        samples = list(slot.glob('*-before-*.json')) + list(slot.glob('*-after-*.json'))
        self.assertGreaterEqual(len(samples), 4 * (len(self.token) + 1))
        for sample in samples:
            saved = json.loads(sample.read_bytes())
            self.assertIn('screen', saved)
            self.assertEqual(saved['body'], saved['expected_prefix'])
            self.assertFalse(saved['busy'])

    def test_original_writer_publishes_linked_clear_proof_only(self):
        value = self.run_clear(publish=True)
        self.assertEqual(value['status'], 'PROBE_CLEARED_BRIDGE_PUBLISHED', value)
        self.assertEqual(value['bridge_writes'], 1)
        current = json.loads((self.root / 'bridge-test-evidence.json').read_bytes())
        self.assertTrue(current['clear_confirmed'])
        self.assertEqual(current['token'], self.token)
        self.assertEqual(current['clear_recovery']['archive_sha256'], self.preview['archive_sha256'])
        self.assertEqual(current, dict(current['executors'][0], executors=current['executors']))
        self.assertFalse((self.root / 'handshake-receipt.json').exists())
        for path, pin in self.original_files.items():
            if Path(path).parent == self.root / clear.continuation.ARCHIVE:
                self.assertEqual(evidence.snapshot(Path(path)), pin)

    def test_released_task_can_clear_but_never_rearm_or_publish(self):
        self.marker_path.unlink()
        value = self.run_clear()
        self.assertEqual(value['status'], 'PROBE_CLEARED_ONLY', value)
        self.assertFalse(value['reservation_present'])
        self.assertFalse(self.marker_path.exists())
        self.assertEqual((self.root / 'bridge-test-evidence.json').read_bytes(), self.original_bridge)

    def test_released_task_cannot_publish(self):
        self.marker_path.unlink()
        self.refuse('RELEASED_TASK_CANNOT_PUBLISH', publish=True)

    def test_expired_original_reservation_cannot_publish(self):
        self.patch(clear.continuation.time, 'time', return_value=(
            datetime.now(timezone.utc) + timedelta(hours=7)).timestamp())
        self.refuse('EXPIRED_OR_INVALID', publish=True)

    def test_original_owner_only(self):
        self.caller.return_value[3]['foreground']['thread_id'] = 'maintenance-thread'
        self.refuse('SUPERVISOR_THREAD_CHANGED')

    def test_original_native_identity_is_not_rebound(self):
        with self.patch(native, 'bind_target', side_effect=AssertionError('must never rebind')):
            self.fixture.process_state['birth'] = 'replacement process'
            self.refuse('PROCESS_BIRTH_OR_EXECUTABLE_CHANGED')

    def test_changed_pane_or_session_refused(self):
        self.fixture.identity['target_pane_uuid'] = 'different-pane'
        self.refuse('TARGET_IDENTITY_CHANGED')

    def test_archive_pin_required(self):
        self.args.archive_sha256 = '0' * 64
        self.refuse('PREVIEW_ARCHIVE_PIN_REQUIRED')

    def test_archive_change_after_preview(self):
        path = self.root / clear.continuation.ARCHIVE / 'RESULT.json'
        path.write_bytes(path.read_bytes() + b' ')
        self.refuse('PREVIEW_ARCHIVE_PIN_REQUIRED')

    def test_changed_original_evidence(self):
        (self.root / 'naming-proof.json').write_text('{}')
        self.refuse('ORIGINAL_FILES_CHANGED')

    def test_original_exit_attestation_required(self):
        self.args.original_harness_exited = False
        self.refuse('HARNESS_EXIT_ATTESTATION_REQUIRED')

    def test_foreign_draft_whitespace_and_token_prefix_refused(self):
        for text in ('user draft', ' ' + self.token, self.token + ' ', self.token + '\n',
                     self.token + '\u00a0', self.token[:-1], ''):
            with self.subTest(text=text):
                self.compose = text
                self.refuse('COMPOSER_CHANGED_PRESERVE')

    def test_unknown_footer_cannot_be_treated_as_idle(self):
        self.unparsed = True
        self.refuse('COMPOSER_BUSY_OR_UNKNOWN')

    def test_busy_and_queue_screens(self):
        original = self.read_screen
        for prefix in ('✻ Thinking…\n', 'Messages to be submitted after\n'):
            with self.subTest(prefix=prefix):
                self.screen.side_effect = lambda *a, **k: prefix + original()
                self.refuse('COMPOSER_BUSY_OR_UNKNOWN')

    def test_native_user_or_queue_forbids_clear(self):
        self.fixture.append_user(self.token)
        self.refuse('NATIVE_RECEIVED_QUEUED_OR_UNKNOWN')

    def test_native_queued_attachment_forbids_clear(self):
        self.fixture.append_queued(self.token)
        self.refuse('NATIVE_RECEIVED_QUEUED_OR_UNKNOWN')

    def test_native_enqueue_before_attachment_forbids_clear(self):
        self.fixture.append_record(dict(type='queue-operation', operation='enqueue',
            sessionId=self.fixture.session_id, content=self.token))
        self.refuse('NATIVE_QUEUE_OPERATION_PRESERVE')

    def test_partial_native_record_forbids_clear(self):
        with self.fixture.transcript.open('ab') as stream:
            stream.write(b'{"type":"user"')
        self.refuse('NATIVE_RECEIVED_QUEUED_OR_UNKNOWN')

    def test_other_executor_reservation_forbids_clear(self):
        other = copy.deepcopy(self.marker)
        other.update(collaboration_id='another-reservation', task_id='other-task')
        (self.marker_path.parent / 'another.json').write_text(json.dumps(other))
        self.refuse('OTHER_OR_UNKNOWN_RESERVATION')

    def test_real_input_mutex_contention(self):
        with mutex.hold(self.fixture.identity):
            self.refuse('INPUT_CHANNEL_BUSY')

    def test_real_root_lock_contention(self):
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.refuse('temporarily unavailable')
        finally:
            os.close(fd)

    def test_real_reservation_lock_contention(self):
        with harness._marker_update_lock(self.marker_path, self.marker):
            self.refuse(('executor inquiry episode is busy', 'temporarily unavailable'))

    def test_no_second_key_after_user_edit(self):
        self.fixture.key.side_effect = lambda *a: setattr(self, 'compose', 'user typed something')
        value = self.run_clear()
        self.assertEqual(value['status'], 'PROBE_CLEAR_INCOMPLETE_NO_REPLAY', value)
        self.assertIn('COMPOSER_CHANGED_PRESERVE', value['error'])
        self.assertEqual(self.fixture.key.call_count, 1)
        self.assertEqual(self.compose, 'user typed something')
        self.assertEqual((self.root / 'bridge-test-evidence.json').read_bytes(), self.original_bridge)

    def test_no_second_key_after_queue_arrives(self):
        self.fixture.key.side_effect = lambda *a: self.fixture.append_queued(self.token)
        value = self.run_clear()
        self.assertEqual(value['status'], 'PROBE_CLEAR_INCOMPLETE_NO_REPLAY', value)
        self.assertIn('NATIVE_RECEIVED_QUEUED_OR_UNKNOWN', value['error'])
        self.assertEqual(self.fixture.key.call_count, 1)

    def test_uncertain_key_permanently_spends_slot(self):
        self.fixture.key.side_effect = RuntimeError('uncertain key result')
        value = self.run_clear()
        self.assertEqual(value['status'], 'PROBE_CLEAR_INCOMPLETE_NO_REPLAY', value)
        self.assertIsNone(value['input_operations'])
        self.assertEqual(value['key_intents'], 1)
        self.assertTrue((self.root / clear.ARCHIVE / '00-KEY-INTENT.json').exists())
        self.fixture.key.reset_mock()
        self.refuse('SLOT_EXISTS_NO_REPLAY')

    def test_interrupt_after_intent_permanently_spends_slot(self):
        self.fixture.key.side_effect = KeyboardInterrupt('simulated crash')
        with self.assertRaises(KeyboardInterrupt):
            self.run_clear()
        self.assertTrue((self.root / clear.ARCHIVE / '00-KEY-INTENT.json').exists())
        self.fixture.key.reset_mock()
        self.refuse('SLOT_EXISTS_NO_REPLAY')

    def test_publication_exception_has_no_automatic_replay(self):
        self.patch(harness, '_write_bridge_evidence', side_effect=RuntimeError('publisher failed'))
        value = self.run_clear(publish=True)
        self.assertTrue(value['clear_confirmed'])
        self.assertEqual(value['status'], 'PROBE_CLEAR_INCOMPLETE_NO_REPLAY', value)
        self.assertTrue((self.root / clear.ARCHIVE / 'PUBLISH-INTENT.json').exists())
        self.fixture.key.reset_mock()
        self.refuse('SLOT_EXISTS_NO_REPLAY')

    def test_release_between_clear_and_publication_cannot_publish(self):
        old_key = self.key
        def release(*args):
            old_key(*args)
            if self.compose == '':
                self.marker_path.unlink()
        self.fixture.key.side_effect = release
        value = self.run_clear(publish=True)
        self.assertEqual(value['status'], 'PROBE_CLEAR_INCOMPLETE_NO_REPLAY', value)
        self.assertIn('RESERVATION_BOUNDARY_CHANGED', value['error'])
        self.assertEqual(value['bridge_writes'], 0)
        self.assertFalse(self.marker_path.exists())

    def test_delayed_key_observed_without_another_key(self):
        pending = []
        reads = [0]

        def key(surface, name):
            if name == 'backspace':
                pending.append((surface, name))
                reads[0] = 0

        def read():
            if pending:
                reads[0] += 1
                if reads[0] == 3:
                    self.key(*pending.pop())

        self.fixture.key.side_effect = key
        self.read_action = read
        value = self.run_clear()
        self.assertEqual(value['status'], 'PROBE_CLEARED_ONLY', value)
        self.assertEqual(self.fixture.key.call_count, len(self.token) + 1)
        self.assertEqual(self.compose, '')
        self.assertEqual(pending, [])

    def test_key_without_effect_stops_without_retry(self):
        self.fixture.key.side_effect = lambda *a: None
        value = self.run_clear()
        self.assertEqual(value['status'], 'PROBE_CLEAR_INCOMPLETE_NO_REPLAY', value)
        self.assertIn('COMPOSER_NOT_STABLE', value['error'])
        self.assertEqual(self.fixture.key.call_count, 2)  # end, one backspace
        self.assertEqual(self.compose, self.token)
        self.fixture.key.reset_mock()
        self.refuse('SLOT_EXISTS_NO_REPLAY')

    def test_archive_mutation_after_key_stops_input(self):
        path = self.root / clear.continuation.ARCHIVE / 'RESULT.json'
        self.fixture.key.side_effect = lambda *a: path.write_bytes(path.read_bytes() + b' ')
        value = self.run_clear()
        self.assertEqual(value['status'], 'PROBE_CLEAR_INCOMPLETE_NO_REPLAY', value)
        self.assertIn('ARCHIVE_FILE_CHANGED', value['error'])
        self.assertEqual(self.fixture.key.call_count, 1)
        self.assertFalse(value['clear_confirmed'])
        self.assertEqual(value['bridge_writes'], 0)

    def test_interrupt_after_original_writer_has_no_replay(self):
        writer = harness._write_bridge_evidence

        def write_then_interrupt(*args):
            writer(*args)
            raise KeyboardInterrupt('interrupted after publication')

        self.patch(harness, '_write_bridge_evidence', side_effect=write_then_interrupt)
        with self.assertRaises(KeyboardInterrupt):
            self.run_clear(publish=True)
        slot = self.root / clear.ARCHIVE
        self.assertTrue((slot / 'PUBLISH-INTENT.json').exists())
        self.assertFalse((slot / 'RESULT.json').exists())
        current = json.loads((self.root / 'bridge-test-evidence.json').read_bytes())
        self.assertTrue(current['clear_confirmed'])
        self.assertEqual(current['clear_recovery']['archive_sha256'], self.preview['archive_sha256'])
        self.fixture.key.reset_mock()
        self.refuse('ORIGINAL_FILES_CHANGED')

    def test_original_heartbeat_under_reservation_lock_is_nonblocking(self):
        # Keep the real writer and real marker locks. Heartbeat is best effort;
        # it cannot acquire our held locks or delay the evidence publication.
        touch = self.patch(harness, '_touch_active_marker', wraps=harness._touch_active_marker)
        value = self.run_clear(publish=True)
        self.assertEqual(value['status'], 'PROBE_CLEARED_BRIDGE_PUBLISHED', value)
        touch.assert_called_once_with(self.root / 'bridge-test-evidence.json')
        self.assertEqual(json.loads(self.marker_path.read_bytes()), self.marker)


if __name__ == '__main__':
    unittest.main()
