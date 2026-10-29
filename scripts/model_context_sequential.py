"""Prospective, read-only causal Claude notice/body evidence across requests.

This is a separate opt-in observation. It cannot change an original receipt,
attempt, queue, controller, authorization or resource. Transcript commands are
data and are never executed. The original v1 observer is unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

import model_context_consumption as original

SCHEMA = 'model-context-sequential-consumption-observation-v2'
MAX_BATCHES = 8
MAX_TOOLS = 32
MAX_SECONDS = 10
need = original.need
digest = original.digest
api_hash = original.api_hash
blocks = original.blocks
object_field = original.object_field


class Budget:
    def __init__(self):
        self.deadline = time.monotonic() + MAX_SECONDS

    def check(self):
        need(time.monotonic() <= self.deadline, 'SEQUENTIAL_OBSERVATION_TIME_BUDGET_EXCEEDED')


def measured_command(command, body_ref):
    """Recognize the exact R76 measurement form, in addition to the v1 forms."""
    path = body_ref['path']
    if original.supported_hash_command(command, path):
        return True
    if not re.fullmatch(r'[-A-Za-z0-9_./]+', path):
        return False
    observed = (
        'B=' + path + '\n'
        'ls -la "$B" 2>&1\n'
        'echo "--- measured ---"\n'
        'shasum -a 256 "$B" 2>&1\n'
        'wc -c < "$B" 2>&1\n'
        'echo "expected sha=' + body_ref['sha256'] + ' bytes=' + str(body_ref['bytes']) + '"\n'
        'date -u +%Y-%m-%dT%H:%M:%SZ'
    )
    return command == observed


def _read_result(result, body_ref, body):
    item, _index, block = result
    returned = object_field(item[2], 'toolUseResult')
    file = object_field(returned, 'file')
    lines = body.splitlines()
    rendered = '\n'.join(str(index) + '\t' + line for index, line in enumerate(lines, 1))
    need(returned.get('type') == 'text' and file.get('filePath') == body_ref['path']
         and file.get('content') == body and type(file.get('startLine')) is int
         and file['startLine'] == 1 and type(file.get('numLines')) is int
         and type(file.get('totalLines')) is int
         and file['numLines'] == file['totalLines'] == len(lines)
         and block.get('content') == rendered, 'COMPLETE_EXACT_NATIVE_READ_RESULT_REQUIRED')


def _hash_result(result, body_ref):
    item, _index, block = result
    returned = object_field(item[2], 'toolUseResult')
    output = block.get('content')
    hash_line = body_ref['sha256'] + '  ' + body_ref['path']
    size = re.compile(r'\s*' + str(body_ref['bytes']) + r'(?:\s+bytes mode=\S+ owner=\S+)?\s*')
    need(isinstance(output, str) and output.splitlines().count(hash_line) == 1
         and sum(bool(size.fullmatch(line)) for line in output.splitlines()) == 1
         and returned.get('stdout') == output and returned.get('stderr') == ''
         and returned.get('interrupted') is False
         and ('exitCode' not in returned or returned['exitCode'] == 0),
         'SUCCESSFUL_EXACT_MEASURED_HASH_AND_SIZE_REQUIRED')


def _responses(records, request, end, expected):
    rows = [item for item in records if request[0] < item[0] < end
            and item[2].get('type') == 'assistant']
    need(bool(rows), 'MATCHING_REAL_RESPONSE_REQUIRED')
    for item in rows:
        obj = item[2]
        need(original.same_session(obj, expected) and obj.get('version') == original.VERSION
             and obj.get('requestRef') == request[2]['id']
             and obj.get('isApiErrorMessage', False) is False
             and object_field(obj, 'message').get('role') == 'assistant'
             and object_field(obj, 'message').get('type') == 'message'
             and isinstance(object_field(obj, 'message').get('content'), list)
             and isinstance(obj.get('uuid'), str) and bool(obj['uuid']),
             'NONERROR_SAME_REQUEST_RESPONSE_REQUIRED')
    return rows


def correlate(records, binding, fence, wire, body_ref, body_raw, *, budget=None, batch_join=None):
    """Join every immediate request and its whole actual parallel result batch."""
    budget = budget or Budget()
    budget.check()
    expected = original.validate_binding(binding, fence, wire)
    need(original.reference.wire_reference(wire) == body_ref
         and wire.startswith('STATUS: MESSAGE_REFERENCE_V2 '), 'EXACT_V2_NOTICE_REFERENCE_REQUIRED')
    need(bool(body_raw) and len(body_raw) == body_ref['bytes']
         and digest(body_raw) == body_ref['sha256'], 'EXACT_BODY_REQUIRED')
    body = body_raw.decode('utf-8')
    offset = fence['transcript']['offset']
    for at, raw, obj in records:
        budget.check()
        need(at == offset and raw.endswith(b'\n')
             and original.strict_json(raw) == obj, 'CONTIGUOUS_EXACT_NATIVE_RECORDS_REQUIRED')
        offset += len(raw)
    need(bool(records) and offset - fence['transcript']['offset'] <= original.MAX_SCAN,
         'BOUNDED_COMPLETE_RECORD_SNAPSHOT_REQUIRED')
    requests = [item for item in records if item[2].get('type') == 'api-request']
    need(all(original.same_session(item[2], expected) for item in requests),
         'SINGLE_ORIGINAL_SESSION_REQUEST_STREAM_REQUIRED')
    ids = [item[2].get('id') for item in requests]
    need(all(isinstance(value, str) and value for value in ids) and len(ids) == len(set(ids)),
         'UNIQUE_API_REQUEST_IDS_REQUIRED')
    wrapper = original.PREFIX + wire + original.SUFFIX

    def exact_wrapper(obj):
        texts = [block.get('text') for block in blocks(obj) if block.get('type') == 'text']
        return texts.count(wrapper) == 1 and sum(text.count(wire) for text in texts
                                                if isinstance(text, str)) == 1

    blob = original.unique([item for item in records if item[2].get('type') == 'api-request-blob'
        and original.same_session(item[2], expected)
        and item[2].get('version', original.VERSION) == original.VERSION
        and object_field(item[2], 'message').get('role') == 'user' and exact_wrapper(item[2])],
        'UNIQUE_EXACT_API_USER_WRAPPER_REQUIRED')
    need(api_hash(blob[2]['message']) == blob[2].get('hash'), 'API_BLOB_CONTENT_HASH_CHANGED')
    notice = original.unique([item for item in requests if item[0] > blob[0]
        and isinstance(item[2].get('tail'), list) and item[2]['tail'].count(blob[2]['hash']) == 1],
        'UNIQUE_CORRESPONDING_NOTICE_REQUEST_REQUIRED')
    need(next((item for item in requests if item[0] > blob[0]), None) == notice,
         'IMMEDIATE_NOTICE_REQUEST_REQUIRED')
    first = requests.index(notice)
    # Index all native result blocks, including duplicates/invalid candidates.
    # Filtering them before uniqueness checks could conceal a conflicting join.
    result_index = {}
    for item in records:
        for index, block in enumerate(blocks(item[2])):
            if item[2].get('type') == 'user' and block.get('type') == 'tool_result':
                result_index.setdefault(block.get('tool_use_id'), []).append((item, index, block))
    seen_tools, seen_uuids = set(), set()
    read_seen = hash_seen = None
    batches, proof = [], [blob]

    def native_result(call, block, next_request):
        result = original.unique(result_index.get(block['id'], []), 'UNIQUE_NATIVE_TOOL_RESULT_REQUIRED')
        item, _index, returned = result
        obj = item[2]
        need(call[0] < item[0] < next_request[0]
             and original.same_session(obj, expected) and obj.get('version') == original.VERSION
             and object_field(obj, 'message').get('role') == 'user'
             and obj.get('parentUuid') == call[2]['uuid']
             and obj.get('sourceToolAssistantUUID', call[2]['uuid']) == call[2]['uuid']
             and returned.get('is_error', False) is False
             and object_field(obj, 'toolUseResult').get('interrupted', False) is False
             and isinstance(obj.get('uuid'), str) and bool(obj['uuid']),
             'SUCCESSFUL_ORDERED_NATIVE_RESULT_REQUIRED')
        return result

    for index in range(first, min(len(requests), first + MAX_BATCHES)):
        budget.check()
        request = requests[index]
        need(index + 1 < len(requests), 'IMMEDIATE_RESULT_REQUEST_REQUIRED')
        following = requests[index + 1]
        for selected in (request, following):
            need(selected[2].get('version') == original.VERSION
                 and selected[2].get('querySource') == 'repl_main_thread'
                 and isinstance(selected[2].get('tail'), list), 'MEASURED_MAIN_THREAD_REQUEST_REQUIRED')
        need(following[2].get('base') == request[2]['id'], 'IMMEDIATE_CAUSAL_REQUEST_BASE_REQUIRED')
        if batch_join is None:
            responses = _responses(records, request, following[0], expected)
            calls = [(item, block) for item in responses for block in blocks(item[2])
                     if block.get('type') == 'tool_use']
            joined_results = None
        else:
            responses, calls, joined_results = batch_join(
                records, request, following, expected, budget=budget)
        need(0 < len(calls) <= MAX_TOOLS, 'BOUNDED_COMPLETE_TOOL_BATCH_REQUIRED')
        for item in responses:
            need(item[2]['uuid'] not in seen_uuids, 'DUPLICATE_NATIVE_RECORD_UUID_REFUSED')
            seen_uuids.add(item[2]['uuid'])
        results = []
        for call_index, (call, block) in enumerate(calls):
            budget.check()
            identity = block.get('id')
            need(isinstance(identity, str) and identity and identity not in seen_tools
                 and isinstance(block.get('input'), dict), 'UNIQUE_NATIVE_TOOL_IDS_REQUIRED')
            seen_tools.add(identity)
            result = (native_result(call, block, following) if joined_results is None
                      else joined_results[call_index])
            results.append(result)
            input = block['input']
            if block.get('name') == 'Read' and input.get('file_path') == body_ref['path']:
                need(read_seen is None and input == {'file_path': body_ref['path']},
                     'ONE_EXACT_FULL_BODY_READ_CALL_REQUIRED')
                _read_result(result, body_ref, body)
                read_seen = result
            if block.get('name') == 'Bash' and body_ref['path'] in str(input.get('command', '')):
                need(hash_seen is None and measured_command(input.get('command'), body_ref),
                     'ONE_SUPPORTED_MEASURED_HASH_CALL_REQUIRED')
                _hash_result(result, body_ref)
                hash_seen = result
        results.sort(key=lambda result: (result[0][0], result[1]))
        if batch_join is None:
            actual_results = [(item[0], block_index) for item in records
                if request[0] < item[0] < following[0] and item[2].get('type') == 'user'
                for block_index, block in enumerate(blocks(item[2])) if block.get('type') == 'tool_result']
            need(actual_results == [(item[0], block_index) for item, block_index, _block in results],
                 'EVERY_ACTUAL_PARALLEL_RESULT_REQUIRED')
        result_rows = {item[0]: item for item, _index, _block in results}
        for item in result_rows.values():
            need(item[2]['uuid'] not in seen_uuids, 'DUPLICATE_NATIVE_RECORD_UUID_REFUSED')
            seen_uuids.add(item[2]['uuid'])
        merged = api_hash(dict(role='user', content=[block for _item, _index, block in results]))
        need(following[2]['tail'].count(merged) == 1, 'IMMEDIATE_COMPLETE_RESULT_BATCH_HASH_REQUIRED')
        proof.extend([request, *responses, *result_rows.values(), following])
        batches.append(dict(request_id=request[2]['id'], next_request_id=following[2]['id'],
            result_batch_sha256=merged, tool_results=len(results),
            request=original.slice_pin(request, fence['transcript']['path']),
            responses=[original.slice_pin(item, fence['transcript']['path']) for item in responses],
            results=[dict(record=original.slice_pin(item, fence['transcript']['path']),
                          block_index=block_index, tool_id=block['tool_use_id'],
                          block_sha256=api_hash(block)) for item, block_index, block in results],
            next_request=original.slice_pin(following, fence['transcript']['path'])))
        if read_seen is not None and hash_seen is not None:
            end = requests[index + 2][0] if index + 2 < len(requests) else offset
            final = _responses(records, following, end, expected)
            for item in final:
                need(item[2]['uuid'] not in seen_uuids, 'DUPLICATE_NATIVE_RECORD_UUID_REFUSED')
                seen_uuids.add(item[2]['uuid'])
            proof.extend(final)
            proof_end = final[-1][0] + len(final[-1][1])
            if batch_join is not None:
                proof_end = max(item[0] + len(item[1]) for item in proof)
            for item in records:
                budget.check()
                obj = item[2]
                if notice[0] <= item[0] < proof_end:
                    need(obj.get('version', original.VERSION) == original.VERSION
                         and obj.get('type') not in ('summary', 'compact-boundary')
                         and 'compact' not in str(obj.get('subtype', '')).lower(),
                         'UNPROVEN_VERSION_OR_COMPACTION_TRANSITION_REFUSED')
            unique_proof = {item[0]: item for item in proof}
            budget.check()
            return dict(schema=SCHEMA, observation='MODEL_CONTEXT_SEQUENTIALLY_CONSUMED',
                session_id=expected, marker=binding['receipt_marker'],
                wire_sha256=binding['payload_sha256'], body=body_ref,
                notice_request_id=notice[2]['id'], body_request_id=following[2]['id'],
                sequential_batches=batches, parallel_tool_results=sum(b['tool_results'] for b in batches),
                proof_slices=[original.slice_pin(item, fence['transcript']['path'])
                              for _at, item in sorted(unique_proof.items())],
                native_user_received=False, body_full_read=True, business_accepted=False,
                official_receipt_published=False, attempt_changed=False, input_operations=0,
                task_authorized=False, resource_released=False, replay_authorized=False,
                binding_scope='RECORDED_ORIGINAL_BINDING_NOT_CURRENT_PROCESS_HEALTH')
    raise ValueError('COMPLETE_READ_HASH_CHAIN_REQUIRED_WITHIN_BATCH_BUDGET')


def _identity(info):
    # APFS device observations are retained but not used as a stable identity.
    return (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_mode, info.st_uid, info.st_gid, info.st_nlink)


def _snapshot(path, limit):
    path = Path(path)
    need(path.is_absolute() and path.resolve(strict=True) == path, 'CANONICAL_INPUT_PATH_REQUIRED')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        before = os.fstat(stream.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
             and before.st_size <= limit, 'BOUNDED_OWNED_REGULAR_INPUT_REQUIRED')
        raw = stream.read(limit + 1)
        after, named = os.fstat(stream.fileno()), path.lstat()
        need(stat.S_ISREG(named.st_mode) and _identity(before) == _identity(after) == _identity(named)
             and len(raw) == before.st_size, 'ORIGINAL_INPUT_CHANGED_DURING_READ')
    return raw, _identity(before), [before.st_dev, after.st_dev, named.st_dev]


def _check_transcript(stream, binding):
    pin = binding['transcript']
    held, named = os.fstat(stream.fileno()), Path(pin['path']).lstat()
    need(stat.S_ISREG(named.st_mode) and held.st_ino == named.st_ino == pin['inode']
         and held.st_uid == named.st_uid == os.getuid()
         and held.st_mode == named.st_mode and held.st_gid == named.st_gid
         and held.st_nlink == named.st_nlink and min(held.st_size, named.st_size) >= pin['offset'],
         'ORIGINAL_TRANSCRIPT_REPLACED_OR_TRUNCATED')
    stream.seek(0)
    need(digest(stream.read(pin['prefix_size'])) == pin['prefix_sha256'], 'ORIGINAL_PREFIX_CHANGED')
    stream.seek(pin['tail_at'])
    need(digest(stream.read(pin['offset'] - pin['tail_at'])) == pin['tail_sha256'], 'ORIGINAL_FENCE_TAIL_CHANGED')
    return min(held.st_size, named.st_size), [held.st_dev, named.st_dev]


def observe_attempt(attempt_path, attempt_sha256, text_file, *, snapshot_end=None, correlator=None):
    budget = Budget()
    raw, attempt_identity, attempt_devices = _snapshot(attempt_path, original.evidence.MAX_EVIDENCE_BYTES)
    need(digest(raw) == attempt_sha256, 'EXACT_ORIGINAL_ATTEMPT_PIN_REQUIRED')
    attempt = original.strict_json(raw)
    binding = attempt['native_binding']
    wire_raw, wire_identity, wire_devices = _snapshot(text_file, original.reference.MAX_INLINE_BYTES)
    wire = wire_raw.decode('utf-8')
    body_ref = original.reference.wire_reference(wire)
    need(body_ref == attempt.get('body_reference'), 'ORIGINAL_BODY_REFERENCE_REQUIRED')
    body_raw, body_identity, body_devices = _snapshot(body_ref['path'], original.reference.MAX_BODY_BYTES)
    fence = original.unique([event['native_paste_fence'] for event in attempt['events']
        if event.get('phase') == 'PASTE_INTENT'], 'ONE_ORIGINAL_INPUT_FENCE_REQUIRED')
    original.validate_binding(binding, fence, wire)
    bound = dict(binding, transcript=fence['transcript'])
    with original.native._open(fence['transcript']['path']) as stream:
        original.native._metadata(stream, 'claude', binding['session_id'])
        size, before_devices = _check_transcript(stream, bound)
        start = fence['transcript']['offset']
        end = size if snapshot_end is None else snapshot_end
        need(type(end) is int and start < end <= size and end - start <= original.MAX_SCAN,
             'BOUNDED_ORIGINAL_SNAPSHOT_REQUIRED')
        stream.seek(start)
        snapshot = stream.read(end - start)
        budget.check()
        records = original.parsed_records(snapshot, start)
        result = (correlate if correlator is None else correlator)(
            records, binding, fence, wire, body_ref, body_raw, budget=budget)
        _size, after_devices = _check_transcript(stream, bound)
        stream.seek(start)
        need(stream.read(end - start) == snapshot, 'ORIGINAL_RECORDS_CHANGED_DURING_OBSERVATION')
    inputs = []
    for path, before, expected_raw, limit, devices in (
        (attempt_path, attempt_identity, raw, original.evidence.MAX_EVIDENCE_BYTES, attempt_devices),
        (text_file, wire_identity, wire_raw, original.reference.MAX_INLINE_BYTES, wire_devices),
        (body_ref['path'], body_identity, body_raw, original.reference.MAX_BODY_BYTES, body_devices),
    ):
        budget.check()
        current, identity, later_devices = _snapshot(path, limit)
        need(current == expected_raw and identity == before, 'ORIGINAL_INPUT_PIN_CHANGED')
        inputs.append(dict(path=str(path), sha256=digest(current), devices=devices + later_devices))
    budget.check()
    devices = before_devices + after_devices
    result.update(attempt=dict(path=str(attempt_path), sha256=attempt_sha256),
        snapshot_end=end, snapshot_bytes=end - start, snapshot_sha256=digest(snapshot),
        historical_receipt_contract=binding.get('receipt_contract'), old_receipt_contract_unchanged=True,
        current_provider_health='NOT_MEASURED', input_pins=inputs,
        transcript_device_observations=devices,
        recorded_transcript_device=fence['transcript']['device'],
        transcript_device_changed=any(device != fence['transcript']['device'] for device in devices))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--attempt', required=True)
    parser.add_argument('--attempt-sha256', required=True)
    parser.add_argument('--text-file', required=True)
    parser.add_argument('--snapshot-end', type=int)
    args = parser.parse_args(argv)
    try:
        need(sys.flags.ignore_environment and sys.dont_write_bytecode, 'CONTEXT_OBSERVER_REQUIRES_BE')
        value = observe_attempt(args.attempt, args.attempt_sha256, args.text_file, snapshot_end=args.snapshot_end)
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError, UnicodeError, original.native.NativeDeliveryError) as exc:
        print(json.dumps(dict(schema=SCHEMA, observation='UNVERIFIED', reason=str(exc),
            native_user_received=False, official_receipt_published=False, attempt_changed=False,
            input_operations=0, replay_authorized=False)))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
