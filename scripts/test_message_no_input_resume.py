"""Two real preflight refusals, explicit one-shot recovery, and no unsafe replay."""
import copy
import itertools
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch

import availability_contract
import cmux_bridge as bridge
import cmux_callback_journal
import cmux_codex_input as direct
import cmux_confirmation as confirmation
import cmux_evidence_io
import cmux_message_journal as journal
import cmux_native_delivery as native
import cmux_prompt_reference as reference
import resume_message_no_input as recovery
from native_test_support import LegacyTerminalFixture, NativeFixture
from test_codex_input import FakeRPC


class OrdinaryNoInputRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        p = patch.object(Path,'home',return_value=self.home)
        p.start(); self.addCleanup(p.stop)
        self.identity = dict(workspace_uuid='workspace',caller_surface_uuid='caller',
                             target_surface_uuid='target',target_pane_uuid='pane')
        self.fixture = LegacyTerminalFixture.attach(self,home=self.home,identity=self.identity)
        self.marker = 'message-original-two-no-input'
        self.text = 'STATUS: '+self.marker
        self.idle = '› \nGPT-6 high'
        self.modules = (bridge,native,cmux_evidence_io,cmux_callback_journal,availability_contract)
        self.text_file = self.home/'exact-wire.txt'
        self.args = SimpleNamespace(controller=str(Path(bridge.__file__).resolve().parent.parent),
            surface='peer',marker=self.marker,text_file=str(self.text_file),
            history_sha256=None,apply=False,wait_seconds=0)

    def two_refusals(self,body=False):
        if body:
            raw='STATUS: '+self.marker+'\n'+('exact UTF-8 正文\n'*100)
            planned=reference.plan(raw,self.marker)
            reference.persist(planned['reference'],raw)
            self.body=Path(planned['reference']['path'])
            self.text=planned['text']
        self.text_file.write_text(self.text)
        with patch.object(bridge,'read_screen',return_value='› existing user draft\nGPT-6 high'):
            for _ in range(2):
                with self.assertRaises((bridge.TaskPackContractError,bridge.DispatchUnconfirmed)):
                    bridge.submit_text('peer',self.text,marker=self.marker,wait_seconds=0)
        root=self.home/'.local/state/multi-agent-collaboration/message-dispatch-v1'
        dirs=list(root.iterdir())
        self.assertEqual(len(dirs),1)
        self.directory=dirs[0]
        self.paths=cmux_evidence_io.attempt_paths(self.directory)
        self.assertEqual(len(self.paths),2)
        self.old=[p.read_bytes() for p in self.paths]
        for p in self.paths:
            row=json.loads(p.read_bytes())
            self.assertEqual(row['phase'],'NO_INPUT')
            self.assertEqual(row['events'],[])
        self.fixture.send.reset_mock(); self.fixture.key.reset_mock()

    def preview(self):
        result=recovery.resume(self.args,self.modules)
        self.assertEqual(result['status'],'READY_ZERO_INPUT')
        self.assertEqual(result['input_operations'],0)
        self.args.history_sha256=result['plan']['history_sha256']
        return result

    def apply(self):
        self.args.apply=True
        return recovery.resume(self.args,self.modules)

    def assert_no_input(self):
        self.fixture.send.assert_not_called()
        self.fixture.key.assert_not_called()

    def successful_screens(self):
        return self.fixture.ready_screens(self.idle,self.fixture.draft(self.text),self.idle)

    def test_default_preview_is_read_only_and_explicit_third_attempt_delivers(self):
        self.two_refusals()
        names=sorted(p.name for p in self.directory.iterdir())
        with self.assertRaisesRegex(bridge.TaskPackContractError,'RETRY_BUDGET_EXHAUSTED'):
            bridge.submit_text('peer',self.text,marker=self.marker,wait_seconds=0)
        self.preview()
        self.assertEqual(sorted(p.name for p in self.directory.iterdir()),names)
        self.assert_no_input()
        with patch.object(bridge,'read_screen',side_effect=self.successful_screens()), \
                patch.object(bridge,'send_text') as paste, \
                patch.object(bridge,'send_key',side_effect=self.fixture.receipt_on_key(self.text)) as key:
            result=self.apply()
            self.assertTrue(result['confirmed'])
            self.assertEqual(Path(result['attempt']).name,'attempt-0003.json')
            paste.assert_called_once(); key.assert_called_once()
        self.assertEqual([p.read_bytes() for p in self.paths],self.old)
        proof=journal.verified_receipt(bridge,'peer',self.text,self.marker)
        self.assertIsNotNone(proof)
        with self.assertRaisesRegex(ValueError,'RECOVERY_ALREADY_USED'):
            self.apply()

    def test_apply_requires_reviewed_full_history_hash(self):
        self.two_refusals()
        self.args.apply=True
        with self.assertRaisesRegex(ValueError,'REVIEWED_HISTORY_SHA256_REQUIRED'):
            recovery.resume(self.args,self.modules)
        self.args.history_sha256='0'*64
        with self.assertRaisesRegex(ValueError,'REVIEWED_HISTORY_CHANGED'):
            recovery.resume(self.args,self.modules)
        self.assert_no_input()
        self.assertFalse((self.directory/'zero-input-recovery.json').exists())

    def test_payload_and_endpoint_changes_do_not_get_a_new_budget(self):
        self.two_refusals();self.preview()
        self.text_file.write_text(self.text+' changed')
        with self.assertRaisesRegex(ValueError,'BINDING_CHANGED'):
            self.apply()
        self.text_file.write_text(self.text)
        self.identity['target_pane_uuid']='changed-pane'
        with self.assertRaises((ValueError,FileNotFoundError)):
            self.apply()
        self.assert_no_input()

    def test_changed_complete_history_refuses(self):
        self.two_refusals();self.preview()
        row=json.loads(self.paths[0].read_bytes());row['error']+=' changed'
        self.paths[0].write_text(json.dumps(row))
        with self.assertRaisesRegex(ValueError,'REVIEWED_HISTORY_CHANGED'):
            self.apply()
        self.assert_no_input()

    def test_input_queue_unknown_or_result_is_never_zero_input_history(self):
        self.two_refusals()
        cases=[dict(phase=x) for x in ('PREPARED','PASTE_INTENT','NATIVE_PENDING','UNKNOWN','CONFIRMED')]
        cases += [dict(events=[dict(phase=x)]) for x in ('PASTE_INTENT','NATIVE_INPUT_INTENT','ENTER_SENT','QUEUE_TAB_INTENT','EXTRA_ENTER_INTENT')]
        cases += [dict(result=dict(confirmed=False,state='NATIVE_QUEUED'))]
        for fields in cases:
            with self.subTest(fields=fields):
                row=json.loads(self.old[0]);row.update(fields)
                self.paths[0].write_text(json.dumps(row))
                with self.assertRaisesRegex(ValueError,'HISTORY_HAS_INPUT_OR_UNKNOWN'):
                    recovery.resume(self.args,self.modules)
                self.assert_no_input()
        self.paths[0].write_bytes(self.old[0])

    def test_receipt_legacy_or_existing_recovery_forbids_replay(self):
        self.two_refusals()
        for name in ('receipt.json','zero-input-recovery.json'):
            with self.subTest(name=name):
                p=self.directory/name;p.write_text('{}')
                with self.assertRaisesRegex(ValueError,'RECEIPT_OR_LEGACY|RECOVERY_ALREADY_USED'):
                    recovery.resume(self.args,self.modules)
                p.unlink()
        root=self.home/'.local/state/multi-agent-collaboration/deliveries-v1'
        p=root/(self.directory.name+'.json');p.write_text('{}')
        with self.assertRaisesRegex(ValueError,'RECEIPT_OR_LEGACY'):
            recovery.resume(self.args,self.modules)
        self.assert_no_input()

    def test_missing_or_unbound_attempt_does_not_prove_zero_input(self):
        self.two_refusals()
        self.paths[0].unlink()
        with self.assertRaisesRegex(ValueError,'EXACTLY_TWO|CONTIGUOUS'):
            recovery.resume(self.args,self.modules)
        self.paths[0].write_bytes(self.old[0])
        for path in self.paths:
            row=json.loads(path.read_bytes());row.pop('native_binding',None)
            path.write_text(json.dumps(row))
        with self.assertRaisesRegex(ValueError,'ORIGINAL_NATIVE_BINDING_REQUIRED'):
            recovery.resume(self.args,self.modules)
        self.assert_no_input()

    def test_one_earlier_authenticated_native_binding_preserves_missing_later_observation(self):
        self.two_refusals()
        row=json.loads(self.paths[1].read_bytes());row.pop('native_binding')
        self.paths[1].write_text(json.dumps(row))
        result=self.preview()
        self.assertEqual(result['plan']['original_missing_native_bindings'],1)
        self.assert_no_input()

    def test_controller_and_original_runtime_changes_refuse_before_input(self):
        self.two_refusals()
        row=json.loads(self.old[0]);row['controller_provenance']['source']='/other'
        self.paths[0].write_text(json.dumps(row))
        with self.assertRaisesRegex(ValueError,'ORIGINAL_CONTROLLER_REQUIRED'):
            recovery.resume(self.args,self.modules)
        row=json.loads(self.old[0]);row['native_binding']['session_id']='other-session'
        self.paths[0].write_text(json.dumps(row))
        with self.assertRaisesRegex(ValueError,'ORIGINAL_RUNTIME_CHANGED'):
            recovery.resume(self.args,self.modules)
        self.assert_no_input()

    def test_long_body_replacement_with_same_bytes_refuses(self):
        self.two_refusals(body=True);self.preview()
        replacement=self.body.with_name('replacement.txt')
        replacement.write_bytes(self.body.read_bytes());replacement.chmod(0o400)
        replacement.replace(self.body)
        with self.assertRaisesRegex(ValueError,'BODY_CHANGED'):
            self.apply()
        self.assert_no_input()

    def test_a_third_pre_input_refusal_consumes_the_single_recovery(self):
        self.two_refusals();self.preview()
        with patch.object(bridge,'read_screen',return_value='› user draft\nGPT-6 high'):
            with self.assertRaises((bridge.TaskPackContractError,bridge.DispatchUnconfirmed)):
                self.apply()
        p=self.directory/'attempt-0003.json'
        self.assertEqual(json.loads(p.read_bytes())['phase'],'NO_INPUT')
        self.assertEqual([p.read_bytes() for p in self.paths],self.old)
        with self.assertRaisesRegex(ValueError,'RECOVERY_ALREADY_USED'):
            self.apply()
        self.assert_no_input()

    def test_crash_after_durable_slot_never_restores_budget(self):
        self.two_refusals();self.preview()
        with patch.object(recovery,'_sync',side_effect=OSError('directory sync interrupted')):
            with self.assertRaises(OSError):self.apply()
        self.assertTrue((self.directory/'zero-input-recovery.json').exists())
        self.assertFalse((self.directory/'attempt-0003.json').exists())
        with self.assertRaisesRegex(ValueError,'RECOVERY_ALREADY_USED'):
            self.apply()
        self.assert_no_input()

    def test_uncertain_third_input_only_reconciles_through_original_controller(self):
        self.two_refusals();self.preview()
        with patch.object(bridge,'read_screen',return_value=self.idle), \
                patch.object(bridge,'send_text',side_effect=OSError('lost paste ACK')) as paste, \
                patch.object(bridge,'send_key') as key:
            with self.assertRaises(OSError):self.apply()
            paste.assert_called_once();key.assert_not_called()
        with self.assertRaisesRegex(ValueError,'RECOVERY_ALREADY_USED'):
            self.apply()
        self.fixture.append_user(self.text)
        with patch.object(bridge,'send_text') as paste,patch.object(bridge,'send_key') as key:
            result=bridge.submit_text('peer',self.text,marker=self.marker,reconcile_only=True,wait_seconds=0)
            self.assertTrue(result['confirmed'])
            self.assertEqual(Path(result['attempt']).name,'attempt-0003.json')
            paste.assert_not_called();key.assert_not_called()
        self.assertEqual([p.read_bytes() for p in self.paths],self.old)

    def test_original_lock_replacement_invalidates_reviewed_plan(self):
        self.two_refusals();result=self.preview()
        p=Path(result['plan']['locks'][0]['path'])
        keep=p.with_suffix('.held')
        p.rename(keep);p.touch()
        with self.assertRaisesRegex(ValueError,'REVIEWED_HISTORY_CHANGED'):
            self.apply()
        self.assert_no_input()

    def test_duplicate_keys_and_nonstandard_numbers_never_prove_no_input(self):
        self.two_refusals()
        row = json.loads(self.old[0])
        text = json.dumps(row)
        malformed = [
            text[:-1] + ',"phase":"NO_INPUT"}',
            text.replace('"phase": "NO_INPUT"', '"phase":"PASTE_INTENT","phase":"NO_INPUT"'),
            text[:-1] + ',"metadata":{"number":NaN}}',
            text[:-1] + ',"metadata":{"number":Infinity}}',
            text[:-1] + ',"metadata":{"number":1e999}}',
            text[:-1] + ',"metadata":{"x":1,"x":2}}',
            '[]',
        ]
        for value in malformed:
            with self.subTest(value=value[-80:]):
                self.paths[0].write_text(value)
                with self.assertRaisesRegex(ValueError, 'DUPLICATE_HISTORY|INVALID_HISTORY|HISTORY_OBJECT'):
                    recovery.resume(self.args, self.modules)
                self.assert_no_input()
        self.paths[0].write_bytes(self.old[0])

    def test_contradictory_input_operation_evidence_is_not_no_input(self):
        self.two_refusals()
        for fields in [dict(input_operations=1), dict(error_details=dict(input_operations=1)),
                       dict(error_details=dict(input_operations=False)), dict(error_details=None)]:
            with self.subTest(fields=fields):
                row = json.loads(self.old[0]); row.update(fields)
                self.paths[0].write_text(json.dumps(row))
                with self.assertRaisesRegex(ValueError, 'HISTORY_HAS_INPUT_OR_UNKNOWN'):
                    recovery.resume(self.args, self.modules)
                self.assert_no_input()
        self.paths[0].write_bytes(self.old[0])

    def test_unreviewed_original_driver_or_version_refuses_before_input(self):
        self.two_refusals()
        original_read = cmux_evidence_io.read_bytes
        source = Path(self.args.controller)
        for which in ['driver', 'version', 'next_version']:
            with self.subTest(which=which):
                def changed(path):
                    if which == 'driver' and path == source / 'scripts/cmux_message_journal.py':
                        return original_read(path) + b'\n# changed driver'
                    if which == 'version' and path == source / 'VERSION':
                        return b'0.4.999\n'
                    if which == 'next_version' and path == source / 'VERSION':
                        return b'0.4.75\n'
                    return original_read(path)
                with patch.object(cmux_evidence_io, 'read_bytes', side_effect=changed):
                    with self.assertRaisesRegex(ValueError, 'UNSUPPORTED_ORIGINAL_DRIVER'):
                        recovery.resume(self.args, self.modules)
                self.assert_no_input()
        self.assertFalse((self.directory / 'zero-input-recovery.json').exists())


class NativeRPCRecoveryTests(unittest.TestCase):
    """Exercise original journals and native receipt readers, mocking only the host."""

    def setUp(self):
        self.fixture = NativeFixture.attach(self)
        self.rpc = FakeRPC(self.fixture)
        self.fixture.rpc.side_effect = lambda **kwargs: self.rpc
        self.marker = 'original-native-two-no-input'
        self.text = 'STATUS: ' + self.marker + ' exact 中文 payload  spaces'
        self.text_file = self.fixture.home / 'exact-wire.txt'
        self.text_file.write_text(self.text, encoding='utf-8')
        self.modules = (bridge, native, cmux_evidence_io, cmux_callback_journal, availability_contract)
        self.args = SimpleNamespace(controller=str(Path(bridge.__file__).resolve().parent.parent),
            surface='target', marker=self.marker, text_file=str(self.text_file),
            history_sha256=None, apply=False, wait_seconds=0)
        rpc_clock = SimpleNamespace(monotonic=itertools.count().__next__, time=time.time, sleep=time.sleep)
        self.fixture.stack.enter_context(patch.object(direct, 'time', rpc_clock))
        self.fixture.stack.enter_context(patch.object(confirmation, 'time', rpc_clock))
        # Metadata explicitly refuses native input. Protected terminal fallback
        # gets an actual host draft refusal and makes two NO_INPUT journals.
        self.rpc.thread_changes['canAcceptDirectInput'] = False
        def occupied_host(*args, **kwargs):
            value = self.fixture._run(*args, **kwargs)
            if args[:2] == ('rpc', 'surface.input_state'):
                state = json.loads(value)
                state.update(state='occupied', blocks_typing=True)
                return json.dumps(state)
            return value
        with patch.object(bridge, '_run', side_effect=occupied_host):
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, 'PROMPT_NOT_EMPTY'):
                    bridge.submit_text('target', self.text, marker=self.marker, wait_seconds=0)
        roots = list((self.fixture.home / '.local/state/multi-agent-collaboration/message-dispatch-v1').iterdir())
        self.assertEqual(len(roots), 1)
        self.directory = roots[0]
        self.paths = cmux_evidence_io.attempt_paths(self.directory)
        self.assertEqual(len(self.paths), 2)
        self.old = [path.read_bytes() for path in self.paths]
        for raw in self.old:
            row = json.loads(raw)
            self.assertEqual((row['phase'], row['events']), ('NO_INPUT', []))
            self.assertEqual(row['native_binding']['provider'], 'codex')
            self.assertEqual(row['native_binding']['session_id'], self.fixture.session_id)
        self.assertEqual(self.rpc.mutations, [])
        self.fixture.send.assert_not_called()
        self.fixture.key.assert_not_called()
        self.rpc.thread_changes.clear()
        self.read_screen = self.fixture.stack.enter_context(patch.object(bridge, 'read_screen',
            side_effect=AssertionError('native recovery must not inspect or mutate a composer')))

    def plan(self):
        before = sorted(path.name for path in self.directory.iterdir())
        result = recovery.resume(self.args, self.modules)
        self.assertEqual((result['status'], result['input_operations']), ('READY_ZERO_INPUT', 0))
        self.assertEqual(sorted(path.name for path in self.directory.iterdir()), before)
        self.assertEqual(self.rpc.mutations, [])
        self.args.history_sha256 = result['plan']['history_sha256']

    def apply(self):
        self.args.apply = True
        return recovery.resume(self.args, self.modules)

    def assert_original_and_no_terminal(self):
        self.assertEqual([path.read_bytes() for path in self.paths], self.old)
        self.fixture.send.assert_not_called()
        self.fixture.key.assert_not_called()
        self.read_screen.assert_not_called()

    def test_active_recovery_steers_once_with_durable_budget_and_fresh_original_fence(self):
        self.plan()
        def before_input(method, params):
            slot = json.loads((self.directory / 'zero-input-recovery.json').read_bytes())
            self.assertEqual(slot['state'], 'BUDGET_CONSUMED')
            attempt = json.loads((self.directory / 'attempt-0003.json').read_bytes())
            event = attempt['events'][-1]
            self.assertEqual(event['phase'], 'NATIVE_INPUT_INTENT')
            self.assertEqual(event['native_input_fence']['transcript']['offset'], self.fixture.transcript.stat().st_size)
            self.assertEqual(event['native_request']['params'], params)
        self.rpc.before_mutation = before_input
        result = self.apply()
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['transport'], direct.TRANSPORT)
        self.assertEqual(self.rpc.mutations, [('turn/steer', dict(threadId=self.fixture.session_id,
            expectedTurnId=self.rpc.turn, input=[dict(type='text', text=self.text)]))])
        self.assertIsNotNone(journal.verified_receipt(bridge, 'target', self.text, self.marker))
        with self.assertRaisesRegex(ValueError, 'RECOVERY_ALREADY_USED'):
            self.apply()
        self.assert_original_and_no_terminal()

    def test_idle_recovery_starts_same_thread_once_without_settings_overrides(self):
        self.rpc.active = False
        self.plan()
        self.assertTrue(self.apply()['confirmed'])
        self.assertEqual(self.rpc.mutations, [('turn/start', dict(threadId=self.fixture.session_id,
            input=[dict(type='text', text=self.text)]))])
        self.assert_original_and_no_terminal()

    def test_native_acceptance_without_record_only_reconciles_original_third_attempt(self):
        self.rpc.receive = False
        self.plan()
        with self.assertRaises(bridge.DispatchUnconfirmed):
            self.apply()
        self.assertEqual(len(self.rpc.mutations), 1)
        self.assertFalse((self.directory / 'receipt.json').exists())
        third = (self.directory / 'attempt-0003.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'RECOVERY_ALREADY_USED'):
            self.apply()
        self.fixture.append_user(self.text)
        result = bridge.submit_text('target', self.text, marker=self.marker, reconcile_only=True, wait_seconds=0)
        self.assertTrue(result['confirmed'])
        self.assertTrue(result['reconciled_read_only'])
        self.assertEqual(len(self.rpc.mutations), 1)
        self.assertEqual((self.directory / 'attempt-0003.json').read_bytes(), third)
        self.assert_original_and_no_terminal()

    def test_timeout_after_native_receipt_confirms_without_second_rpc(self):
        self.rpc.mutation_error = TimeoutError('original host timed out after receipt')
        self.plan()
        result = self.apply()
        self.assertTrue(result['confirmed'])
        self.assertEqual(len(self.rpc.mutations), 1)
        self.assert_original_and_no_terminal()


if __name__=='__main__':
    unittest.main()
