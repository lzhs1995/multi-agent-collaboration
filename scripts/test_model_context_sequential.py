"""Prospective sequential joins: adversarial chains, public CLI and preserved inputs."""
import copy
import json
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import model_context_sequential as sequential
import model_context_consumption as original
import test_model_context_consumption as fixture_module
from test_model_context_consumption import raw, TARGET


class SequentialConsumptionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.ContextConsumptionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        events = copy.deepcopy(f.events)
        events[6]['message']['content'][0]['content'] = self.render(f.body)
        # Actual R76 shape: hash result -> immediate Read request/result ->
        # immediate body-context request and its real response.
        hash_request = events[2]
        read_request = f.event('api-request', id='request-read', base=hash_request['id'],
            querySource='repl_main_thread', tail=[self.batch_hash([events[5]])])
        body_request = events[7]
        body_request.update(base=read_request['id'], tail=[self.batch_hash([events[6]])])
        events[4]['requestRef'] = read_request['id']
        events[3]['message']['content'][0]['input']['command'] = self.r76_command()
        self.events = [events[0], events[1], hash_request, events[3], events[5],
                       read_request, events[4], events[6], body_request, events[8]]

    @staticmethod
    def render(body):
        return '\n'.join(str(n) + '\t' + text for n, text in enumerate(body.decode().splitlines(), 1))

    @staticmethod
    def batch_hash(results):
        # Independent canonical content construction, using the established SHA
        # format rather than any prospective chain-selection implementation.
        content = [block for event in results for block in event['message']['content']
                   if block.get('type') == 'tool_result']
        return original.api_hash(dict(role='user', content=content))

    def r76_command(self):
        f = self.fixture
        return ('B=' + str(f.body_path) + '\nls -la "$B" 2>&1\necho "--- measured ---"\n'
                'shasum -a 256 "$B" 2>&1\nwc -c < "$B" 2>&1\n'
                'echo "expected sha=' + f.ref['sha256'] + ' bytes=' + str(len(f.body)) + '"\n'
                'date -u +%Y-%m-%dT%H:%M:%SZ')

    def check(self, events=None, **kwargs):
        f = self.fixture
        rows = original.parsed_records(b''.join(raw(obj) for obj in
            (self.events if events is None else events)), f.pin['offset'])
        return sequential.correlate(rows, kwargs.get('binding', f.binding),
            kwargs.get('fence', f.fence), kwargs.get('wire', f.wire),
            kwargs.get('ref', f.ref), kwargs.get('body', f.body))

    def reseal(self, events):
        requests = [i for i, event in enumerate(events) if event['type'] == 'api-request']
        for left, right in zip(requests, requests[1:]):
            results = [event for event in events[left + 1:right]
                       if event['type'] == 'user' and any(
                           b.get('type') == 'tool_result' for b in original.blocks(event))]
            events[right]['tail'] = [self.batch_hash(results)]
        return events

    def refused(self, events, reason=None):
        with self.assertRaisesRegex(ValueError, reason or '.'):
            self.check(events)

    def test_two_batches_join_real_measured_hash_full_read_and_next_response(self):
        value = self.check()
        self.assertEqual(value['observation'], 'MODEL_CONTEXT_SEQUENTIALLY_CONSUMED')
        self.assertEqual(value['schema'], sequential.SCHEMA)
        self.assertEqual([b['request_id'] for b in value['sequential_batches']],
                         ['request-one', 'request-read'])
        self.assertEqual(value['body_request_id'], 'request-two')
        self.assertEqual(value['parallel_tool_results'], 2)
        self.assertEqual([b['result_batch_sha256'] for b in value['sequential_batches']],
                         [self.batch_hash([self.events[4]]), self.batch_hash([self.events[7]])])
        for field in ('native_user_received', 'official_receipt_published', 'business_accepted',
                      'attempt_changed', 'task_authorized', 'resource_released', 'replay_authorized'):
            self.assertFalse(value[field], field)
        self.assertEqual(value['input_operations'], 0)
        self.assertTrue(value['body_full_read'])
        self.assertEqual(len({p['offset'] for p in value['proof_slices']}), len(value['proof_slices']))

    def test_v1_single_request_policy_is_not_reclassified(self):
        f = self.fixture
        rows = original.parsed_records(b''.join(raw(obj) for obj in self.events), f.pin['offset'])
        with self.assertRaisesRegex(ValueError, 'EXACT_FULL_BODY_READ_CALL'):
            original.correlate(rows, f.binding, f.fence, f.wire, f.ref, f.body)

    def test_reviewed_release_drivers_require_original_exact_bytes_and_refuse_future(self):
        import resume_message_no_input as recovery
        root = Path(sequential.__file__).resolve().parent.parent
        source = self.fixture.home / 'release-driver'
        (source / 'scripts').mkdir(parents=True)
        driver = (root / 'scripts/cmux_message_journal.py').read_bytes()
        self.assertEqual(original.digest(driver), recovery.DRIVER_SHA256)
        (source / 'scripts/cmux_message_journal.py').write_bytes(driver)
        for version in ('0.4.78', '0.4.79', '0.4.80', '0.4.81', '0.4.82', '0.4.83', '0.4.84'):
            (source / 'VERSION').write_bytes((version + '\n').encode())
            value = recovery.original_driver(source, original.evidence)
            self.assertEqual(value['version'], version)
        latest = max(int(v.rsplit('.', 1)[1]) for v in recovery.SUPPORTED_DRIVERS)
        (source / 'VERSION').write_bytes(('0.4.%d\n' % (latest + 1)).encode())
        with self.assertRaisesRegex(ValueError, 'UNSUPPORTED_ORIGINAL_DRIVER'):
            recovery.original_driver(source, original.evidence)
        (source / 'VERSION').write_bytes(b'0.4.78\n')
        (source / 'scripts/cmux_message_journal.py').write_bytes(driver + b'\n')
        with self.assertRaisesRegex(ValueError, 'UNSUPPORTED_ORIGINAL_DRIVER'):
            recovery.original_driver(source, original.evidence)

    def test_hash_and_read_can_share_a_complete_parallel_batch_in_native_result_order(self):
        events = copy.deepcopy(self.fixture.events)
        events[6]['message']['content'][0]['content'] = self.render(self.fixture.body)
        events[7]['base'] = 'request-one'
        # Reverse call order without reversing actual result order.
        events[3], events[4] = events[4], events[3]
        value = self.check(self.reseal(events))
        self.assertEqual(len(value['sequential_batches']), 1)
        self.assertEqual(value['parallel_tool_results'], 2)

    def add_parallel(self, events, *, failed=False):
        f = self.fixture
        call = f.assistant('request-read', 'call-extra', [
            dict(type='tool_use', id='tool-extra', name='Read', input={'file_path': '/challenge.json'})])
        result = f.event('user', uuid='result-extra', parentUuid='call-extra',
            message=dict(role='user', content=[
                dict(type='tool_result', tool_use_id='tool-extra', content='challenge', is_error=failed)]))
        events.insert(7, call)
        events.insert(9, result)
        return self.reseal(events)

    def test_unrelated_parallel_result_is_required_and_its_failure_cannot_be_skipped(self):
        events = self.add_parallel(copy.deepcopy(self.events))
        self.assertEqual(self.check(events)['parallel_tool_results'], 3)
        events[9]['message']['content'][0]['is_error'] = True
        self.refused(self.reseal(events), 'SUCCESSFUL_ORDERED_NATIVE_RESULT')

    def test_dropped_parallel_result_cannot_match_complete_api_tail(self):
        events = self.add_parallel(copy.deepcopy(self.events))
        events.pop(9)
        self.refused(events, 'UNIQUE_NATIVE_TOOL_RESULT')

    def test_omitting_an_actual_result_cannot_be_hidden_by_omitting_its_call(self):
        events = self.add_parallel(copy.deepcopy(self.events))
        events.pop(7)
        # Even a selected-subset tail must not conceal the extra native result.
        requests = [i for i, obj in enumerate(events) if obj['type'] == 'api-request']
        events[requests[-1]]['tail'] = [self.batch_hash([events[7]])]
        self.refused(events, 'EVERY_ACTUAL_PARALLEL_RESULT')

    def test_full_batch_preserves_block_order_inside_one_native_result_record(self):
        events = copy.deepcopy(self.fixture.events)
        # Two calls in one assistant -> two result blocks in one native record.
        read_block = events[6]['message']['content'][0]
        read_block['content'] = self.render(self.fixture.body)
        hash_block = events[5]['message']['content'][0]
        calls = [events[4]['message']['content'][0], events[3]['message']['content'][0]]
        assistant = self.fixture.assistant('request-one', 'combined-call', calls)
        combined = copy.deepcopy(events[6])
        combined.update(parentUuid='combined-call', sourceToolAssistantUUID='combined-call')
        combined['message']['content'] = [hash_block, read_block]
        # The body/hash tools need distinct structured results, so this unmeasured
        # combined toolUseResult representation must remain unverified.
        merged = [events[0], events[1], events[2], assistant, combined, events[7], events[8]]
        merged[-2]['base'] = 'request-one'
        self.refused(self.reseal(merged), 'MEASURED_HASH_AND_SIZE')

    def test_echoed_expected_hash_without_measured_stdout_is_refused(self):
        events = copy.deepcopy(self.events)
        output = 'expected sha=' + self.fixture.ref['sha256'] + ' bytes=' + str(len(self.fixture.body))
        events[4]['message']['content'][0]['content'] = output
        events[4]['toolUseResult']['stdout'] = output
        self.refused(self.reseal(events), 'MEASURED_HASH_AND_SIZE')

    def test_repeated_hash_or_size_lines_are_ambiguous_even_with_resealed_tail(self):
        for extra in (self.events[4]['toolUseResult']['stdout'].splitlines()[0],
                      str(len(self.fixture.body))):
            events = copy.deepcopy(self.events)
            output = events[4]['toolUseResult']['stdout'] + '\n' + extra
            events[4]['message']['content'][0]['content'] = output
            events[4]['toolUseResult']['stdout'] = output
            with self.subTest(extra=extra):
                self.refused(self.reseal(events), 'MEASURED_HASH_AND_SIZE')

    def test_stdout_stderr_interrupt_and_native_error_are_each_checked(self):
        for change in ('stdout', 'stderr', 'interrupted', 'is_error', 'exitCode'):
            events = copy.deepcopy(self.events)
            if change == 'stdout': events[4]['toolUseResult']['stdout'] += ' changed'
            if change == 'stderr': events[4]['toolUseResult']['stderr'] = 'failure'
            if change == 'interrupted': events[4]['toolUseResult']['interrupted'] = True
            if change == 'is_error': events[4]['message']['content'][0]['is_error'] = True
            if change == 'exitCode': events[4]['toolUseResult']['exitCode'] = 1
            with self.subTest(change=change):
                self.refused(self.reseal(events))

    def test_only_exact_observed_bash_or_existing_measured_forms_are_allowed(self):
        for command in (self.r76_command() + '; echo extra',
                        self.r76_command().replace('2>&1', '2>/dev/null', 1),
                        self.r76_command().replace('expected sha=', 'expected hash='),
                        'echo ' + self.fixture.ref['sha256'] + ' ' + str(self.fixture.body_path)):
            events = copy.deepcopy(self.events)
            events[3]['message']['content'][0]['input']['command'] = command
            with self.subTest(command=command):
                self.refused(events, 'SUPPORTED_MEASURED_HASH_CALL')
        path = str(self.fixture.body_path)
        commands = [
            'shasum -a 256 -- ' + shlex.quote(path) + ' && wc -c < ' + shlex.quote(path),
            'B=' + path + '; stat -f \'%z bytes mode=%Sp owner=%Su\' "$B"; shasum -a 256 "$B"; date -u +%H:%M:%SZ',
            'date -u +%Y-%m-%dT%H:%M:%SZ; B=' + path + '; ls -la "$B"; wc -c < "$B"; shasum -a 256 "$B"; '
            'stat -f \'dev=%d ino=%i mode=%Sp uid=%u size=%z mtime=%m\' "$B"',
        ]
        for command in commands:
            events = copy.deepcopy(self.events)
            events[3]['message']['content'][0]['input']['command'] = command
            with self.subTest(command=command):
                self.assertTrue(self.check(events)['body_full_read'])

    def test_exact_read_path_range_content_count_and_rendered_body_are_required(self):
        for change in ('path', 'start', 'partial', 'count', 'count_bool', 'metadata_body',
                       'rendered_body', 'limit', 'source'):
            events = copy.deepcopy(self.events)
            file = events[7]['toolUseResult']['file']
            if change == 'path': file['filePath'] = '/different/body'
            if change == 'start': file['startLine'] = 2
            if change == 'partial': file['numLines'] = 1
            if change == 'count': file['numLines'] = file['totalLines'] = 100
            if change == 'count_bool': file['startLine'] = True
            if change == 'metadata_body': file['content'] = 'partial body'
            if change == 'rendered_body': events[7]['message']['content'][0]['content'] = 'body was read'
            if change == 'limit': events[6]['message']['content'][0]['input']['limit'] = 1
            if change == 'source': events[7]['sourceToolAssistantUUID'] = 'wrong-parent'
            with self.subTest(change=change):
                self.refused(self.reseal(events))

    def test_one_line_native_read_layout_matches_r76(self):
        f = self.fixture
        f.body = b'exact single line'
        f.ref.update(bytes=len(f.body), sha256=original.digest(f.body))
        f.body_path.write_bytes(f.body)
        f.wire = original.reference._wire(f.marker, f.ref)
        f.binding['payload_sha256'] = original.digest(f.wire.encode())
        f.refresh_fence()
        events = copy.deepcopy(self.events)
        events[1]['message']['content'][0]['text'] = original.PREFIX + f.wire + original.SUFFIX
        events[1]['hash'] = original.api_hash(events[1]['message'])
        events[2]['tail'] = [events[1]['hash']]
        events[3]['message']['content'][0]['input']['command'] = self.r76_command()
        output = f.ref['sha256'] + '  ' + str(f.body_path) + '\n ' + str(len(f.body))
        events[4]['message']['content'][0]['content'] = output
        events[4]['toolUseResult']['stdout'] = output
        events[7]['message']['content'][0]['content'] = '1\t' + f.body.decode()
        events[7]['toolUseResult']['file'].update(content=f.body.decode(), numLines=1, totalLines=1)
        self.assertTrue(self.check(self.reseal(events))['body_full_read'])

    def test_changed_body_or_wire_pin_is_refused(self):
        for kwargs in ({'body': b'changed'}, {'wire': self.fixture.wire + ' '},
                       {'ref': dict(self.fixture.ref, bytes=1)}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.check(**kwargs)

    def test_missing_or_skipped_immediate_request_and_wrong_base_are_refused(self):
        for change in ('missing', 'base', 'tail', 'insert'):
            events = copy.deepcopy(self.events)
            if change == 'missing': events.pop(5)
            if change == 'base': events[5]['base'] = 'unrelated-request'
            if change == 'tail': events[5]['tail'] = ['0' * 64]
            if change == 'insert':
                request = self.fixture.event('api-request', id='skipped-request', base='request-one',
                    querySource='repl_main_thread', tail=['0' * 64])
                events.insert(5, request)
            with self.subTest(change=change):
                self.refused(events)

    def test_final_matching_nonerror_response_is_mandatory(self):
        for change in ('missing', 'error', 'request', 'session', 'meta', 'version'):
            events = copy.deepcopy(self.events)
            if change == 'missing': events.pop()
            if change == 'error': events[-1]['isApiErrorMessage'] = True
            if change == 'request': events[-1]['requestRef'] = 'wrong-request'
            if change == 'session': events[-1]['sessionId'] = TARGET
            if change == 'meta': events[-1]['isMeta'] = True
            if change == 'version': events[-1]['version'] = 'unmeasured'
            with self.subTest(change=change):
                self.refused(events)

    def test_cross_session_sidechain_meta_and_conflicting_alias_results_cannot_supply_join(self):
        for field, value in (('sessionId', TARGET), ('session_id', TARGET),
                             ('isSidechain', True), ('isMeta', True)):
            events = copy.deepcopy(self.events)
            events[7][field] = value
            with self.subTest(field=field):
                self.refused(events, 'ORDERED_NATIVE_RESULT')
        events = copy.deepcopy(self.events)
        events[5]['session_id'] = TARGET
        self.refused(events, 'SINGLE_ORIGINAL_SESSION_REQUEST_STREAM')

    def test_duplicate_request_tool_result_and_response_uuid_are_refused(self):
        for change in ('request', 'tool', 'result', 'uuid', 'parent'):
            events = copy.deepcopy(self.events)
            if change == 'request': events[5]['id'] = events[2]['id']
            if change == 'tool': events[6]['message']['content'][0]['id'] = 'tool-hash'
            if change == 'result': events.insert(8, copy.deepcopy(events[7]))
            if change == 'uuid': events[-1]['uuid'] = events[6]['uuid']
            if change == 'parent': events[7]['parentUuid'] = 'wrong'
            with self.subTest(change=change):
                self.refused(events)

    def test_duplicate_body_read_cannot_be_selected_away(self):
        events = copy.deepcopy(self.events)
        duplicate = copy.deepcopy(events[6])
        duplicate['uuid'] = 'second-read'
        duplicate['message']['content'][0]['id'] = 'second-read-tool'
        result = copy.deepcopy(events[7])
        result.update(uuid='second-read-result', parentUuid='second-read')
        result['message']['content'][0]['tool_use_id'] = 'second-read-tool'
        events.insert(7, duplicate)
        events.insert(9, result)
        self.refused(self.reseal(events), 'ONE_EXACT_FULL_BODY_READ')

    def test_changed_api_wrapper_content_hash_and_duplicate_notice_are_refused(self):
        for change in ('wrapper', 'hash', 'duplicate'):
            events = copy.deepcopy(self.events)
            if change == 'wrapper':
                events[1]['message']['content'][0]['text'] = 'arbitrary wrapper ' + self.fixture.wire
                events[1]['hash'] = original.api_hash(events[1]['message'])
                events[2]['tail'] = [events[1]['hash']]
            if change == 'hash': events[1]['hash'] = '0' * 64
            if change == 'duplicate': events.insert(2, copy.deepcopy(events[1]))
            with self.subTest(change=change):
                self.refused(events)
        events = copy.deepcopy(self.events)
        events[1].pop('version')
        self.assertTrue(self.check(events)['body_full_read'])

    def test_version_change_compaction_or_unmeasured_request_source_is_not_skipped(self):
        for change in ('version', 'compact', 'source'):
            events = copy.deepcopy(self.events)
            if change == 'version': events[5]['version'] = 'unmeasured'
            if change == 'source': events[5]['querySource'] = 'unmeasured'
            if change == 'compact':
                events.insert(5, self.fixture.event('system', subtype='compact_boundary'))
            with self.subTest(change=change):
                self.refused(events)

    def test_contiguous_records_partial_eof_and_scan_budget_are_required(self):
        f = self.fixture
        rows = original.parsed_records(b''.join(raw(obj) for obj in self.events), f.pin['offset'])
        rows.pop(4)
        with self.assertRaisesRegex(ValueError, 'CONTIGUOUS'):
            sequential.correlate(rows, f.binding, f.fence, f.wire, f.ref, f.body)
        for data in (raw(self.events[0])[:-1], b'{}\n' + b'x' * original.MAX_SCAN):
            with self.subTest(bytes=len(data)), self.assertRaises(ValueError):
                original.parsed_records(data, f.pin['offset'])

    def intermediate_batches(self, count):
        f = self.fixture
        events = copy.deepcopy(self.events[:5])
        previous = 'request-one'
        tail = self.batch_hash([events[-1]])
        for n in range(count):
            rid, cid, tid = 'intermediate-' + str(n), 'extra-call-' + str(n), 'extra-tool-' + str(n)
            events.append(f.event('api-request', id=rid, base=previous,
                querySource='repl_main_thread', tail=[tail]))
            events.append(f.assistant(rid, cid, [
                dict(type='tool_use', id=tid, name='Read', input={'file_path': '/other/' + str(n)})]))
            result = f.event('user', uuid='extra-result-' + str(n), parentUuid=cid,
                message=dict(role='user', content=[dict(type='tool_result', tool_use_id=tid, content='other')]))
            events.append(result)
            previous, tail = rid, self.batch_hash([result])
        rest = copy.deepcopy(self.events[5:])
        rest[0].update(base=previous, tail=[tail])
        events.extend(rest)
        return events

    def test_eight_batches_pass_and_ninth_batch_remains_bounded_unverified(self):
        self.assertEqual(len(self.check(self.intermediate_batches(6))['sequential_batches']), 8)
        self.refused(self.intermediate_batches(7), 'WITHIN_BATCH_BUDGET')

    def test_more_than_32_tools_cannot_expand_one_batch(self):
        events = copy.deepcopy(self.events)
        calls = events[6]['message']['content']
        calls.extend(dict(type='tool_use', id='extra-' + str(n), name='Read', input={'file_path': '/other'})
                     for n in range(32))
        self.refused(events, 'BOUNDED_COMPLETE_TOOL_BATCH')

    def test_time_budget_is_a_visible_refusal(self):
        with patch.object(sequential.time, 'monotonic', side_effect=[0, 11]):
            with self.assertRaisesRegex(ValueError, 'TIME_BUDGET'):
                self.check()

    def write_attempt(self):
        self.fixture.events = self.events
        return self.fixture.write_original_attempt()

    def file_check(self, attempt, notice, **kwargs):
        return sequential.observe_attempt(str(attempt), original.digest(attempt.read_bytes()), str(notice), **kwargs)

    def test_file_observer_calls_no_process_and_changes_no_controller_queue_or_input(self):
        attempt, notice = self.write_attempt()
        f = self.fixture
        before = {str(p): p.read_bytes() for p in f.home.rglob('*') if p.is_file()}
        with patch('subprocess.run', side_effect=AssertionError('no process or input permitted')):
            value = self.file_check(attempt, notice)
        self.assertTrue(value['old_receipt_contract_unchanged'])
        self.assertEqual(value['historical_receipt_contract'], 'native-user-marker-v1')
        self.assertEqual({str(p): p.read_bytes() for p in f.home.rglob('*') if p.is_file()}, before)

    def test_apfs_device_drift_is_recorded_without_changing_old_binding(self):
        attempt, notice = self.write_attempt()
        data = original.strict_json(attempt.read_bytes())
        for pin in (data['native_binding']['transcript'], data['events'][0]['native_paste_fence']['transcript']):
            pin['device'] += 1
        data['events'][0]['native_paste_fence']['binding_sha256'] = original.digest(
            json.dumps(data['native_binding'], sort_keys=True).encode())
        attempt.write_bytes(raw(data))
        before = attempt.read_bytes()
        value = self.file_check(attempt, notice)
        self.assertTrue(value['transcript_device_changed'])
        self.assertEqual(attempt.read_bytes(), before)
        with self.assertRaises(original.native.NativeDeliveryError):
            original.observe_attempt(str(attempt), original.digest(before), str(notice))

    def test_device_changes_during_reads_keep_independent_inode_and_byte_checks(self):
        attempt, notice = self.write_attempt()
        actual_fstat = sequential.os.fstat
        calls = []
        def changed(fd):
            info = actual_fstat(fd)
            values = {key: getattr(info, key) for key in dir(info) if key.startswith('st_')}
            values['st_dev'] += len(calls) + 1
            calls.append(fd)
            return SimpleNamespace(**values)
        with patch.object(sequential.os, 'fstat', side_effect=changed):
            value = self.file_check(attempt, notice)
        self.assertTrue(value['transcript_device_changed'])
        self.assertGreater(len(calls), 4)

    def test_retained_inode_replacement_prefix_tail_and_symlink_each_refuse(self):
        f = self.fixture
        for change in ('inode', 'prefix', 'tail', 'symlink'):
            attempt, notice = self.write_attempt()
            data = f.transcript.read_bytes()
            if change == 'inode':
                old = f.transcript.stat()
                other = f.transcript.with_name('replacement.jsonl')
                other.write_bytes(data)
                self.assertNotEqual(other.stat().st_ino, old.st_ino)
                other.replace(f.transcript)
            if change == 'prefix': f.transcript.write_bytes(b'X' + data[1:])
            if change == 'tail':
                pos = f.pin['offset'] - 2
                f.transcript.write_bytes(data[:pos] + b'X' + data[pos + 1:])
            if change == 'symlink':
                other = f.home / 'other.jsonl'; other.write_bytes(data)
                f.transcript.unlink(); f.transcript.symlink_to(other)
            with self.subTest(change=change), self.assertRaises((ValueError, original.native.NativeDeliveryError)):
                self.file_check(attempt, notice)
            if f.transcript.is_symlink(): f.transcript.unlink()

    def test_input_recheck_catches_concurrent_body_change(self):
        attempt, notice = self.write_attempt()
        snapshot = sequential._snapshot
        visits = []
        def changing(path, limit):
            value = snapshot(path, limit)
            visits.append(str(path))
            if len(visits) == 3:
                self.fixture.body_path.write_bytes(b'changed after snapshot')
            return value
        with patch.object(sequential, '_snapshot', side_effect=changing):
            with self.assertRaisesRegex(ValueError, 'ORIGINAL_INPUT_PIN_CHANGED'):
                self.file_check(attempt, notice)

    def test_snapshot_end_must_be_complete_bounded_and_not_past_file(self):
        attempt, notice = self.write_attempt()
        f = self.fixture
        for end in (f.pin['offset'], f.transcript.stat().st_size + 1, f.transcript.stat().st_size - 1):
            with self.subTest(end=end), self.assertRaises(ValueError):
                self.file_check(attempt, notice, snapshot_end=end)

    def test_public_cli_pass_refusal_and_be_requirement_preserve_all_original_files(self):
        attempt, notice = self.write_attempt()
        f = self.fixture
        before = {str(p): p.read_bytes() for p in f.home.rglob('*') if p.is_file()}
        args = [str(Path(sequential.__file__).resolve()), '--attempt', str(attempt),
                '--attempt-sha256', original.digest(attempt.read_bytes()), '--text-file', str(notice),
                '--snapshot-end', str(f.transcript.stat().st_size)]
        for flags, expected in ((['-BE'], 0), ([], 2)):
            completed = subprocess.run([sys.executable, *flags, *args], capture_output=True, text=True, timeout=10)
            with self.subTest(flags=flags):
                self.assertEqual(completed.returncode, expected, completed.stderr)
                value = json.loads(completed.stdout)
                self.assertFalse(value['official_receipt_published'])
                self.assertEqual(value['input_operations'], 0)
        completed = subprocess.run([sys.executable, '-BE', *args[:-1], str(f.transcript.stat().st_size - 1)],
                                   capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(json.loads(completed.stdout)['observation'], 'UNVERIFIED')
        self.assertEqual({str(p): p.read_bytes() for p in f.home.rglob('*') if p.is_file()}, before)


if __name__ == '__main__':
    unittest.main()
