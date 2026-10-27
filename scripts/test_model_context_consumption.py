"""Native record joins, complete-body controls and zero-mutation observations."""
import copy
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import model_context_consumption as context
import cmux_prompt_reference as reference

SESSION = '11111111-1111-4111-8111-111111111111'
CALLER = '22222222-2222-4222-8222-222222222222'
TARGET = '33333333-3333-4333-8333-333333333333'
WORKSPACE = '44444444-4444-4444-8444-444444444444'
PANE = '55555555-5555-4555-8555-555555555555'


def raw(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode() + b'\n'


class ContextConsumptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='model-context-observation-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.body = ('固定的完整正文。\n' + 'body ' * 180).encode()
        self.body_path = self.home / 'body.txt'
        self.body_path.write_bytes(self.body)
        self.ref = dict(format=reference.SINGLE_LINE_FORMAT, path=str(self.body_path),
                        sha256=context.digest(self.body), bytes=len(self.body))
        self.marker = 'CONTEXT_TEST_NOTICE_1'
        self.wire = reference._wire(self.marker, self.ref)
        self.native_root = self.home / 'claude'
        self.transcript = self.native_root / 'projects/-task' / (SESSION + '.jsonl')
        self.transcript.parent.mkdir(parents=True)
        self.prefix = raw(dict(type='system', sessionId=SESSION, subtype='native_metadata'))
        self.pin = dict(path=str(self.transcript), device=0, inode=0, offset=len(self.prefix),
                        prefix_size=len(self.prefix), prefix_sha256=context.digest(self.prefix),
                        tail_at=0, tail_sha256=context.digest(self.prefix), at_record_boundary=True)
        self.binding = dict(schema='native-delivery-v1', provider='claude', session_id=SESSION,
                            identity=dict(caller_surface_uuid=CALLER, target_surface_uuid=TARGET,
                                          workspace_uuid=WORKSPACE, target_pane_uuid=PANE),
                            process=dict(pid=12345, birth=[1000, 1], executable='/original/claude',
                                         file_identity=[1, 2, 3, 4]), tty='ttys123',
                            native_config_root=str(self.native_root), transcript=copy.deepcopy(self.pin),
                            payload_sha256=context.digest(self.wire.encode()),
                            receipt_contract='native-user-marker-v1', receipt_marker=self.marker,
                            bound_at_epoch=1000.)
        self.fence = dict(schema='native-paste-fence-v1', transcript=copy.deepcopy(self.pin),
                          captured_at_epoch=1001.)
        self.refresh_fence()
        self.events = self.make_events()

    def refresh_fence(self):
        self.fence['binding_sha256'] = context.digest(json.dumps(self.binding, sort_keys=True).encode())

    def event(self, kind, **kwargs):
        return dict(type=kind, sessionId=SESSION, version=context.VERSION, **kwargs)

    def assistant(self, rid, identity, content):
        return self.event('assistant', requestRef=rid, uuid=identity, isSidechain=False,
                          message=dict(type='message', role='assistant', id='msg_' + identity,
                                       content=content))

    def make_events(self):
        message = dict(role='user', content=[dict(type='text', text=context.PREFIX + self.wire + context.SUFFIX)])
        blob = self.event('api-request-blob', hash=context.api_hash(message), message=message)
        request = self.event('api-request', id='request-one', querySource='repl_main_thread', tail=[blob['hash']])
        path = shlex.quote(str(self.body_path))
        bash = dict(type='tool_use', id='tool-hash', name='Bash',
                    input=dict(command='shasum -a 256 -- ' + path + ' && wc -c < ' + path))
        read = dict(type='tool_use', id='tool-read', name='Read', input=dict(file_path=str(self.body_path)))
        hash_block = dict(type='tool_result', tool_use_id='tool-hash', is_error=False,
                          content=f"{self.ref['sha256']}  {self.body_path}\n{len(self.body)}")
        read_block = dict(type='tool_result', tool_use_id='tool-read', content='1\t' + self.body.decode())
        hash_result = self.event('user', uuid='result-hash', parentUuid='call-hash',
                                 message=dict(role='user', content=[hash_block]),
                                 toolUseResult=dict(stdout=hash_block['content'], stderr='', interrupted=False))
        read_result = self.event('user', uuid='result-read', parentUuid='call-read',
                                 message=dict(role='user', content=[read_block]),
                                 toolUseResult=dict(type='text', file=dict(filePath=str(self.body_path),
                                   content=self.body.decode(), startLine=1, numLines=2, totalLines=2)))
        body_request = self.event('api-request', id='request-two', querySource='repl_main_thread',
                                  tail=[context.api_hash(dict(role='user', content=[hash_block, read_block]))])
        return [self.event('attachment', attachment=dict(type='queued_command', prompt=self.wire)),
                blob, request, self.assistant('request-one', 'call-hash', [bash]),
                self.assistant('request-one', 'call-read', [read]), hash_result, read_result,
                body_request, self.assistant('request-two', 'response-body', [dict(type='text', text='Full body received.')])]

    def check(self, events=None, **kwargs):
        rows = context.parsed_records(b''.join(raw(obj) for obj in (self.events if events is None else events)),
                                      self.pin['offset'])
        return context.correlate(rows, kwargs.get('binding', self.binding), kwargs.get('fence', self.fence),
                                 kwargs.get('wire', self.wire), self.ref, kwargs.get('body', self.body))

    def test_exact_response_and_full_body_api_join_are_a_separate_observation(self):
        result = self.check()
        self.assertEqual(result['observation'], 'MODEL_CONTEXT_CONSUMED')
        self.assertTrue(result['body_full_read'])
        self.assertEqual(result['parallel_tool_results'], 2)
        for field in ('native_user_received', 'business_accepted', 'official_receipt_published',
                      'attempt_changed', 'task_authorized', 'resource_released', 'replay_authorized'):
            self.assertFalse(result[field], field)
        self.assertEqual(result['input_operations'], 0)

    def test_queue_remove_and_unrelated_assistant_do_not_prove_consumption(self):
        for events in ([self.events[0]], [self.events[0], self.events[-1]],
                       [self.events[0], self.event('attachment', attachment={'type': 'queued_command_removed'})]):
            with self.subTest(events=len(events)), self.assertRaises(ValueError):
                self.check(events)

    def test_plain_native_user_record_does_not_get_renamed_to_context_consumption(self):
        events = [self.event('user', message={'role': 'user', 'content': self.wire})]
        with self.assertRaises(ValueError):
            self.check(events)

    def test_wire_only_in_tool_output_or_assistant_echo_is_refused(self):
        for role, kind in [('user', 'tool_result'), ('assistant', 'text')]:
            events = copy.deepcopy(self.events)
            events[1]['message'] = dict(role=role, content=[dict(type=kind, content=self.wire, text=self.wire)])
            with self.subTest(role=role), self.assertRaises(ValueError):
                self.check(events)

    def test_changed_or_arbitrary_wrapper_is_refused_even_with_valid_blob_hash(self):
        for prefix, suffix in [('prefix\n', context.SUFFIX), (context.PREFIX, '\nextra suffix'),
                               (context.PREFIX, context.SUFFIX + ' '), ('', '')]:
            events = copy.deepcopy(self.events)
            events[1]['message']['content'][0]['text'] = prefix + self.wire + suffix
            events[1]['hash'] = context.api_hash(events[1]['message'])
            events[2]['tail'] = [events[1]['hash']]
            with self.subTest(prefix=prefix, suffix=suffix), self.assertRaises(ValueError):
                self.check(events)

    def test_duplicate_wire_in_api_user_text_is_ambiguous(self):
        events = copy.deepcopy(self.events)
        events[1]['message']['content'].append(dict(type='text', text=self.wire))
        with self.assertRaisesRegex(ValueError, 'UNIQUE_EXACT_API_USER_WRAPPER'):
            self.check(events)

    def test_same_marker_with_changed_wire_sha_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'EXACT_ORIGINAL_WIRE'):
            self.check(wire=self.wire.replace('BYTES=' + str(len(self.body)), 'BYTES=1'))

    def test_cross_session_sidechain_and_meta_records_cannot_supply_the_join(self):
        for field, value in [('sessionId', TARGET), ('session_id', TARGET), ('isSidechain', True), ('isMeta', True)]:
            events = copy.deepcopy(self.events); events[1][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.check(events)

    def test_api_content_hash_and_request_tail_are_both_bound(self):
        for index, field, value in [(1, 'hash', '0' * 64), (2, 'tail', ['0' * 64]),
                                     (2, 'querySource', 'unrelated'), (2, 'version', 'unmeasured-version')]:
            events = copy.deepcopy(self.events); events[index][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.check(events)

    def test_only_request_intent_cancelled_request_and_unrelated_response_are_refused(self):
        cases = [self.events[:3], [obj for obj in self.events if obj.get('type') != 'assistant']]
        wrong = copy.deepcopy(self.events)
        for event in wrong:
            if event.get('requestRef') == 'request-one':
                event['requestRef'] = 'unrelated-request'
        cases.append(wrong)
        for events in cases:
            with self.subTest(length=len(events)), self.assertRaises(ValueError):
                self.check(events)

    def test_api_error_assistant_does_not_supply_a_real_response(self):
        events = copy.deepcopy(self.events)
        for event in events:
            if event.get('requestRef') == 'request-one':
                event['isApiErrorMessage'] = True
        with self.assertRaisesRegex(ValueError, 'MATCHING_REAL_RESPONSE'):
            self.check(events)

    def test_full_read_requires_exact_path_range_bytes_and_native_result(self):
        cases = []
        for change in [{'startLine': 2}, {'numLines': 1}, {'content': 'partial'}, {'filePath': '/another/file'}]:
            events = copy.deepcopy(self.events); events[6]['toolUseResult']['file'].update(change); cases.append(events)
        events = copy.deepcopy(self.events); events[4]['message']['content'][0]['input']['limit'] = 1; cases.append(events)
        events = copy.deepcopy(self.events); del events[6]['toolUseResult']; cases.append(events)
        for events in cases:
            with self.subTest(change=str(events[6].get('toolUseResult'))[:60]), self.assertRaises(ValueError):
                self.check(events)

    def test_body_hash_error_interruption_or_changed_result_parent_is_refused(self):
        for change in ['error', 'interrupted', 'parent', 'hash']:
            events = copy.deepcopy(self.events)
            if change == 'error': events[5]['message']['content'][0]['is_error'] = True
            if change == 'interrupted': events[5]['toolUseResult']['interrupted'] = True
            if change == 'parent': events[5]['parentUuid'] = 'unrelated-assistant'
            if change == 'hash': events[5]['message']['content'][0]['content'] = 'not a hash result'
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.check(events)

    def test_echoed_hash_or_unsupported_command_cannot_prove_a_real_body_hash(self):
        for command in [
            'echo ' + self.ref['sha256'] + ' ' + str(self.body_path),
            'shasum -a 256 ' + str(self.body_path),
            'cat ' + str(self.body_path),
        ]:
            events = copy.deepcopy(self.events)
            events[3]['message']['content'][0]['input']['command'] = command
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, 'REAL_BODY_HASH_COMMAND'):
                self.check(events)

    def test_hash_output_requires_exact_hash_path_and_size_lines(self):
        for output in [self.ref['sha256'] + '  ' + str(self.body_path) + '\nbytes ' + str(len(self.body)),
                       'prefix ' + self.events[5]['toolUseResult']['stdout'],
                       self.events[5]['toolUseResult']['stdout'] + '\n' + self.events[5]['toolUseResult']['stdout']]:
            events = copy.deepcopy(self.events)
            events[5]['message']['content'][0]['content'] = output
            events[5]['toolUseResult']['stdout'] = output
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, 'BODY_HASH_RESULT'):
                self.check(events)

    def test_measured_mac_hash_stat_and_wc_command_forms_are_supported(self):
        path = str(self.body_path)
        commands = [
            'B=' + path + '; stat -f \'%z bytes mode=%Sp owner=%Su\' "$B"; shasum -a 256 "$B"; date -u +%H:%M:%SZ',
            'date -u +%Y-%m-%dT%H:%M:%SZ; B=' + path + '; ls -la "$B"; wc -c < "$B"; shasum -a 256 "$B"; '
            'stat -f \'dev=%d ino=%i mode=%Sp uid=%u size=%z mtime=%m\' "$B"',
        ]
        for command in commands:
            events = copy.deepcopy(self.events)
            events[3]['message']['content'][0]['input']['command'] = command
            with self.subTest(command=command):
                self.assertEqual(self.check(events)['observation'], 'MODEL_CONTEXT_CONSUMED')

    def test_blob_or_native_result_version_and_role_changes_are_refused(self):
        for index, field, value in [(1, 'version', '2.1.999'), (5, 'version', '2.1.999')]:
            events = copy.deepcopy(self.events); events[index][field] = value
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.check(events)
        events = copy.deepcopy(self.events); events[5]['message']['role'] = 'assistant'
        with self.assertRaisesRegex(ValueError, 'NATIVE_TOOL_RESULT'):
            self.check(events)

    def test_measured_blob_without_version_still_binds_versioned_real_request_response(self):
        events = copy.deepcopy(self.events); events[1].pop('version')
        self.assertEqual(self.check(events)['observation'], 'MODEL_CONTEXT_CONSUMED')

    def test_malformed_native_message_and_tool_result_objects_are_unverified(self):
        for index, field in [(1, 'message'), (3, 'message'), (5, 'message'),
                             (5, 'toolUseResult'), (6, 'toolUseResult')]:
            for value in (None, [], 'invalid'):
                events = copy.deepcopy(self.events); events[index][field] = value
                with self.subTest(index=index, field=field, value=value), self.assertRaises(ValueError):
                    self.check(events)
        for value in (None, [], 'invalid'):
            events = copy.deepcopy(self.events); events[6]['toolUseResult']['file'] = value
            with self.subTest(file=value), self.assertRaises(ValueError):
                self.check(events)

    def test_duplicate_keys_and_nonfinite_records_are_refused(self):
        for value in [b'{"type":"user","type":"api-request"}\n', b'{"value":NaN}\n', b'{"value":Infinity}\n']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                context.parsed_records(value, self.pin['offset'])

    def test_full_body_result_without_matching_later_request_response_is_unverified(self):
        for events in (self.events[:7], self.events[:8]):
            with self.subTest(length=len(events)), self.assertRaisesRegex(ValueError, 'FULL_BODY_REQUEST'):
                self.check(events)

    def test_parallel_extra_result_is_included_in_the_real_api_hash(self):
        events = copy.deepcopy(self.events)
        extra_block = dict(type='tool_result', tool_use_id='extra', content='challenge bytes')
        extra_call = self.assistant('request-one', 'call-extra', [dict(type='tool_use', id='extra',
                                    name='Read', input={'file_path': '/original/challenge.json'})])
        extra_result = self.event('user', uuid='result-extra', parentUuid='call-extra',
                                  message={'role': 'user', 'content': [extra_block]})
        events.insert(5, extra_call); events.insert(8, extra_result)
        with self.assertRaisesRegex(ValueError, 'FULL_BODY_REQUEST'):
            self.check(events)
        events[-2]['tail'] = [context.api_hash(dict(role='user', content=[
            events[6]['message']['content'][0], events[7]['message']['content'][0], extra_block]))]
        self.assertEqual(self.check(events)['parallel_tool_results'], 3)

    def test_old_fence_or_changed_preinput_binding_is_refused(self):
        fence = copy.deepcopy(self.fence); fence['binding_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'PREINPUT_FENCE'):
            self.check(fence=fence)
        rows = context.parsed_records(b''.join(raw(obj) for obj in self.events), 0)
        with self.assertRaisesRegex(ValueError, 'PRE_FENCE'):
            context.correlate(rows, self.binding, self.fence, self.wire, self.ref, self.body)

    def test_partial_records_and_oversized_snapshots_are_refused(self):
        for value in [raw(self.events[0])[:-1], b'{}\n' + b'x' * context.MAX_SCAN]:
            with self.assertRaises(ValueError):
                context.parsed_records(value, self.pin['offset'])

    def test_duplicate_request_ids_and_missing_original_process_are_refused(self):
        events = copy.deepcopy(self.events); events[-2]['id'] = 'request-one'
        with self.assertRaisesRegex(ValueError, 'UNIQUE_API_REQUEST'):
            self.check(events)
        binding = copy.deepcopy(self.binding); binding['process']['birth'] = []
        with self.assertRaisesRegex(ValueError, 'ORIGINAL_PROCESS_BINDING'):
            self.check(binding=binding)

    def write_original_attempt(self):
        self.transcript.write_bytes(self.prefix + b''.join(raw(obj) for obj in self.events))
        info = self.transcript.stat()
        for pin in (self.pin, self.binding['transcript'], self.fence['transcript']):
            pin.update(device=info.st_dev, inode=info.st_ino)
        self.refresh_fence()
        attempt = dict(native_binding=self.binding, body_reference=self.ref,
                       events=[dict(phase='PASTE_INTENT', native_paste_fence=self.fence),
                               dict(phase='ENTER_SENT')], controller_provenance={'source': '/original/release'})
        path = self.home / 'attempt.json'; path.write_bytes(raw(attempt))
        notice = self.home / 'notice.txt'; notice.write_bytes(self.wire.encode())
        return path, notice

    def test_file_observer_keeps_attempt_controller_receipts_and_queue_bytes_unchanged(self):
        attempt, notice = self.write_original_attempt()
        before = {str(p):p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        with patch('subprocess.run', side_effect=AssertionError('no process or input permitted')):
            result = context.observe_attempt(str(attempt), context.digest(before[str(attempt)]), str(notice))
        self.assertEqual(result['observation'], 'MODEL_CONTEXT_CONSUMED')
        self.assertEqual(result['historical_receipt_contract'], 'native-user-marker-v1')
        self.assertEqual({str(p):p.read_bytes() for p in self.home.rglob('*') if p.is_file()}, before)

    def test_public_cli_reads_original_config_and_refuses_incomplete_or_changed_chains(self):
        original = copy.deepcopy(self.events)
        for change in ('control', 'wrapper', 'body_hash', 'partial_read', 'missing_response', 'malformed_result'):
            self.events = copy.deepcopy(original)
            if change == 'wrapper':
                self.events[1]['message']['content'][0]['text'] = 'unmeasured wrapper ' + self.wire
                self.events[1]['hash'] = context.api_hash(self.events[1]['message'])
                self.events[2]['tail'] = [self.events[1]['hash']]
            if change == 'body_hash':
                self.events[5]['message']['content'][0]['content'] = 'echoed bytes'
                self.events[5]['toolUseResult']['stdout'] = 'echoed bytes'
            if change == 'partial_read':
                self.events[6]['toolUseResult']['file']['numLines'] = 1
            if change == 'missing_response':
                self.events.pop()
            if change == 'malformed_result':
                self.events[6]['toolUseResult'] = None
            attempt, notice = self.write_original_attempt()
            before = {str(p):p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
            completed = subprocess.run([sys.executable, '-BE', str(Path(context.__file__).resolve()),
                '--attempt', str(attempt), '--attempt-sha256', context.digest(attempt.read_bytes()),
                '--text-file', str(notice)], capture_output=True, text=True, timeout=10)
            with self.subTest(change=change):
                self.assertEqual(completed.returncode, 0 if change == 'control' else 2, completed.stderr)
                value = json.loads(completed.stdout)
                self.assertEqual(value['observation'], 'MODEL_CONTEXT_CONSUMED' if change == 'control' else 'UNVERIFIED')
                self.assertFalse(value['official_receipt_published'])
                self.assertEqual(value['input_operations'], 0)
                self.assertEqual({str(p):p.read_bytes() for p in self.home.rglob('*') if p.is_file()}, before)

    def test_file_pin_body_or_transcript_replacement_cannot_create_a_proof(self):
        for change in ['attempt', 'body', 'transcript', 'symlink']:
            with self.subTest(change=change):
                attempt, notice = self.write_original_attempt(); attempt_sha = context.digest(attempt.read_bytes())
                if change == 'attempt': attempt.write_bytes(attempt.read_bytes() + b' ')
                if change == 'body': self.body_path.write_bytes(b'changed')
                if change == 'transcript':
                    data = self.transcript.read_bytes()
                    original = self.transcript.stat()
                    # Keep the original inode allocated while making its
                    # replacement; unlink/recreate may reuse it on Linux.
                    replacement = self.transcript.with_name('replacement.jsonl')
                    replacement.write_bytes(data)
                    changed = replacement.stat()
                    self.assertNotEqual((changed.st_dev, changed.st_ino),
                                        (original.st_dev, original.st_ino))
                    replacement.replace(self.transcript)
                if change == 'symlink':
                    data = self.transcript.read_bytes(); other = self.home / 'other.jsonl'; other.write_bytes(data)
                    self.transcript.unlink(); self.transcript.symlink_to(other)
                with self.assertRaises((ValueError, OSError, context.native.NativeDeliveryError)):
                    context.observe_attempt(str(attempt), attempt_sha, str(notice))
                if self.transcript.is_symlink(): self.transcript.unlink()
                self.body_path.write_bytes(self.body)


if __name__ == '__main__':
    unittest.main()
