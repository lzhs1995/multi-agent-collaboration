"""Read-only Claude notice/body context evidence; never a native-user receipt.

This separate observation does not publish a receipt, change an attempt, send
input, authorize a task or release a resource. Original controllers retain
their original receipt contracts. A new task may explicitly bind this evidence
class after its supervisor reviews the complete policy and original records.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import sys
import uuid

import cmux_evidence_io as evidence
import cmux_native_delivery as native
import cmux_prompt_reference as reference

SCHEMA = 'model-context-consumption-observation-v1'
MAX_SCAN = 8 * 1024 * 1024
VERSION = '2.1.303'
PREFIX = 'The user sent a new message while you were working:\n'
SUFFIX = ('\n\nThis is how Claude Code surfaces messages the user sends mid-turn — within '
          'the running turn, often alongside the next tool result, rather than as a '
          'separate conversation turn. Address the message above as you continue this turn.')


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def api_hash(message):
    return digest(json.dumps(message, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode('utf-8'))


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'DUPLICATE_NATIVE_JSON_KEY_REFUSED')
            result[key] = value
        return result
    def invalid(_value):
        raise ValueError('NONFINITE_NATIVE_JSON_VALUE_REFUSED')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def supported_hash_command(command, path):
    """Verify a measured hash/size command as data; never execute it."""
    if not isinstance(command, str):
        return False
    quoted = shlex.quote(path)
    supported = {'shasum -a 256 -- ' + quoted + ' && wc -c < ' + quoted}
    if re.fullmatch(r'[-A-Za-z0-9_./]+', path):
        supported.update({
            'B=' + path + '; stat -f \'%z bytes mode=%Sp owner=%Su\' "$B"; shasum -a 256 "$B"; date -u +%H:%M:%SZ',
            'date -u +%Y-%m-%dT%H:%M:%SZ; B=' + path + '; ls -la "$B"; wc -c < "$B"; shasum -a 256 "$B"; '
            'stat -f \'dev=%d ino=%i mode=%Sp uid=%u size=%z mtime=%m\' "$B"',
        })
    return command in supported


def unique(items, reason):
    need(len(items) == 1, reason)
    return items[0]


def object_field(obj, key):
    value = obj.get(key)
    return value if isinstance(value, dict) else {}


def same_session(obj, expected):
    values = [obj[k] for k in ('sessionId', 'session_id') if k in obj]
    return (bool(values) and all(v == expected for v in values)
            and obj.get('isSidechain', False) is False and obj.get('isMeta', False) is False)


def blocks(obj):
    message = obj.get('message')
    content = message.get('content') if isinstance(message, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def parsed_records(raw, offset):
    need(isinstance(raw, bytes) and len(raw) <= MAX_SCAN and raw.endswith(b'\n'),
         'BOUNDED_COMPLETE_RECORD_SNAPSHOT_REQUIRED')
    records = []
    for line in raw.splitlines(keepends=True):
        need(len(line) <= native.MAX_RECORD and line.endswith(b'\n'), 'COMPLETE_NATIVE_RECORD_REQUIRED')
        obj = strict_json(line)
        need(isinstance(obj, dict), 'NATIVE_RECORD_OBJECT_REQUIRED')
        records.append((offset, line, obj))
        offset += len(line)
    return records


def slice_pin(item, path):
    offset, raw, obj = item
    return dict(path=path, offset=offset, bytes=len(raw), sha256=digest(raw),
                type=obj.get('type'), uuid=obj.get('uuid'), timestamp=obj.get('timestamp'))


def validate_binding(binding, fence, wire):
    need(isinstance(binding, dict) and isinstance(fence, dict), 'ORIGINAL_BINDING_OBJECTS_REQUIRED')
    need(binding.get('schema') == 'native-delivery-v1' and binding.get('provider') == 'claude',
         'ORIGINAL_CLAUDE_BINDING_REQUIRED')
    expected = binding['session_id']
    need(str(uuid.UUID(expected)) == expected, 'ORIGINAL_SESSION_REQUIRED')
    identity = binding['identity']
    need(isinstance(identity, dict) and
         set(identity) == {'workspace_uuid', 'caller_surface_uuid', 'target_surface_uuid', 'target_pane_uuid'}
         and all(str(uuid.UUID(v)).lower() == v.lower() for v in identity.values())
         and identity['caller_surface_uuid'] != identity['target_surface_uuid'], 'ORIGINAL_UUID_SCOPE_REQUIRED')
    process = binding['process']
    need(isinstance(process, dict) and type(process['pid']) is int and process['pid'] > 0 and
         isinstance(process['birth'], list) and len(process['birth']) == 2 and
         isinstance(process['file_identity'], list) and len(process['file_identity']) == 4 and
         all(type(v) is int and v >= 0 for v in process['birth'] + process['file_identity']) and
         Path(process['executable']).is_absolute() and isinstance(binding['tty'], str) and binding['tty'],
         'COMPLETE_ORIGINAL_PROCESS_BINDING_REQUIRED')
    need(binding['payload_sha256'] == digest(wire.encode('utf-8')) and
         isinstance(binding.get('receipt_marker'), str) and wire.count(binding['receipt_marker']) == 1,
         'EXACT_ORIGINAL_WIRE_REQUIRED')
    need(fence.get('schema') == 'native-paste-fence-v1' and
         fence.get('binding_sha256') == digest(json.dumps(binding, sort_keys=True).encode()),
         'ORIGINAL_PREINPUT_FENCE_REQUIRED')
    pin = fence['transcript']
    prior = binding['transcript']
    need(isinstance(pin, dict) and isinstance(prior, dict) and
         all(pin[k] == prior[k] for k in ('path', 'device', 'inode')) and
         type(pin['offset']) is int and pin['offset'] >= prior['offset'] and
         pin['at_record_boundary'] is True and pin['prefix_size'] == min(pin['offset'], 4096) and
         pin['tail_at'] == max(0, pin['offset'] - 4096), 'EXACT_ORIGINAL_TRANSCRIPT_FENCE_REQUIRED')
    root = Path(binding['native_config_root'])
    path = Path(pin['path'])
    need(root.is_absolute() and root in path.parents and path.name == expected + '.jsonl',
         'ORIGINAL_NATIVE_TRANSCRIPT_ROOT_REQUIRED')
    captured = fence['captured_at_epoch']
    need(type(captured) in (int, float) and math.isfinite(captured) and
         captured >= binding['bound_at_epoch'], 'ORIGINAL_INPUT_TIME_REQUIRED')
    return expected


def correlate(records, binding, fence, wire, body_ref, body_raw):
    """A strict evidence join over complete, independently pinned native records."""
    expected = validate_binding(binding, fence, wire)
    need(reference.wire_reference(wire) == body_ref and wire.startswith('STATUS: MESSAGE_REFERENCE_V2 '),
         'EXACT_V2_NOTICE_REFERENCE_REQUIRED')
    need(len(body_raw) == body_ref['bytes'] and digest(body_raw) == body_ref['sha256'], 'EXACT_BODY_REQUIRED')
    body = body_raw.decode('utf-8')
    need(all(item[0] >= fence['transcript']['offset'] for item in records), 'PRE_FENCE_RECORD_REFUSED')
    requests = [item for item in records if item[2].get('type') == 'api-request' and
                same_session(item[2], expected)]
    ids = [item[2].get('id') for item in requests]
    need(all(isinstance(v, str) and v for v in ids) and len(ids) == len(set(ids)), 'UNIQUE_API_REQUEST_IDS_REQUIRED')
    wrapper = PREFIX + wire + SUFFIX
    def exact_wrapper(obj):
        texts = [b.get('text', '') for b in blocks(obj) if b.get('type') == 'text']
        return texts.count(wrapper) == 1 and sum(t.count(wire) for t in texts if isinstance(t, str)) == 1
    blob = unique([item for item in records if item[2].get('type') == 'api-request-blob' and
                   same_session(item[2], expected) and
                   ('version' not in item[2] or item[2]['version'] == VERSION) and
                   object_field(item[2], 'message').get('role') == 'user' and
                   exact_wrapper(item[2])], 'UNIQUE_EXACT_API_USER_WRAPPER_REQUIRED')
    need(api_hash(blob[2]['message']) == blob[2]['hash'], 'API_BLOB_CONTENT_HASH_CHANGED')
    request = unique([item for item in requests if item[0] > blob[0] and
                      item[2].get('version') == VERSION and item[2].get('querySource') == 'repl_main_thread' and
                      isinstance(item[2].get('tail'), list) and item[2]['tail'].count(blob[2]['hash']) == 1],
                     'CORRESPONDING_WIRE_REQUEST_REQUIRED')
    def responses_to(request):
        return [item for item in records if item[0] > request[0] and
                item[2].get('type') == 'assistant' and same_session(item[2], expected) and
                item[2].get('version') == VERSION and item[2].get('requestRef') == request[2]['id'] and
                item[2].get('isApiErrorMessage', False) is False and
                object_field(item[2], 'message').get('role') == 'assistant' and
                object_field(item[2], 'message').get('type') == 'message']
    responses = responses_to(request)
    need(bool(responses), 'MATCHING_REAL_RESPONSE_REQUIRED')
    calls = [(item, b) for item in responses for b in blocks(item[2]) if b.get('type') == 'tool_use']
    need(len(calls) <= 32 and all(isinstance(b.get('id'), str) for _item, b in calls) and
         len({b['id'] for _item, b in calls}) == len(calls), 'UNIQUE_BOUNDED_NATIVE_TOOL_IDS_REQUIRED')
    read_call = unique([pair for pair in calls if pair[1].get('name') == 'Read' and
                        pair[1].get('input') == {'file_path': body_ref['path']}], 'EXACT_FULL_BODY_READ_CALL_REQUIRED')
    hash_call = unique([pair for pair in calls if pair[1].get('name') == 'Bash' and
                        body_ref['path'] in str(object_field(pair[1], 'input').get('command', ''))],
                       'CORRESPONDING_BODY_HASH_CALL_REQUIRED')
    need(supported_hash_command(object_field(hash_call[1], 'input').get('command'), body_ref['path']),
         'SUPPORTED_REAL_BODY_HASH_COMMAND_REQUIRED')
    def native_result(pair):
        call, block = pair
        return unique([(item, b) for item in records if item[0] > call[0] and
                       item[2].get('type') == 'user' and same_session(item[2], expected) and
                       item[2].get('version') == VERSION and object_field(item[2], 'message').get('role') == 'user' and
                       item[2].get('parentUuid') == call[2].get('uuid') and call[2].get('uuid')
                       for b in blocks(item[2]) if b.get('type') == 'tool_result' and
                       b.get('tool_use_id') == block['id']], 'MATCHING_NATIVE_TOOL_RESULT_REQUIRED')
    read_result, hash_result = native_result(read_call), native_result(hash_call)
    need(read_result[1].get('is_error', False) is False and hash_result[1].get('is_error', False) is False,
         'SUCCESSFUL_NATIVE_BODY_READ_AND_HASH_REQUIRED')
    returned_read = object_field(read_result[0][2], 'toolUseResult')
    returned = object_field(returned_read, 'file')
    need(returned_read.get('type') == 'text' and
         returned.get('filePath') == body_ref['path'] and returned.get('content') == body and
         returned.get('startLine') == 1 and type(returned.get('numLines')) is int and
         returned['numLines'] == returned.get('totalLines'), 'COMPLETE_EXACT_UTF8_READ_RESULT_REQUIRED')
    returned_hash = object_field(hash_result[0][2], 'toolUseResult')
    output = hash_result[1].get('content')
    hash_line = body_ref['sha256'] + '  ' + body_ref['path']
    size_line = re.compile(r'\s*' + str(body_ref['bytes']) + r'(?:\s+bytes mode=\S+ owner=\S+)?\s*')
    need(isinstance(output, str) and output.splitlines().count(hash_line) == 1 and
         any(size_line.fullmatch(line) for line in output.splitlines()) and returned_hash.get('stdout') == output and
         returned_hash.get('stderr') == '' and returned_hash.get('interrupted') is False,
         'SUCCESSFUL_EXACT_BODY_HASH_RESULT_REQUIRED')
    # Reconstruct all parallel results, including unrelated tools in the same
    # actual response. Subsetting the batch cannot match the real API tail hash.
    results = sorted([native_result(pair) for pair in calls], key=lambda pair: pair[0][0])
    merged_sha = api_hash(dict(role='user', content=[pair[1] for pair in results]))
    joined = [(item, responses_to(item)) for item in requests if item[0] > results[-1][0][0] and
              item[2].get('version') == VERSION and item[2].get('querySource') == 'repl_main_thread' and
              isinstance(item[2].get('tail'), list) and item[2]['tail'].count(merged_sha) == 1]
    joined = [(item, responses) for item, responses in joined if responses]
    need(bool(joined), 'FULL_BODY_REQUEST_AND_MATCHING_RESPONSE_REQUIRED')
    body_request, body_responses = joined[0]
    proof = [blob, request, *[pair[0] for pair in calls], *[pair[0] for pair in results],
             body_request, body_responses[0]]
    return dict(schema=SCHEMA, observation='MODEL_CONTEXT_CONSUMED', session_id=expected,
                marker=binding['receipt_marker'], wire_sha256=binding['payload_sha256'], body=body_ref,
                notice_request_id=request[2]['id'], body_request_id=body_request[2]['id'],
                body_api_message_sha256=merged_sha, parallel_tool_results=len(results),
                proof_slices=[slice_pin(item, fence['transcript']['path']) for item in proof],
                native_user_received=False, body_full_read=True, business_accepted=False,
                official_receipt_published=False, attempt_changed=False, input_operations=0,
                task_authorized=False, resource_released=False, replay_authorized=False,
                binding_scope='RECORDED_ORIGINAL_BINDING_NOT_CURRENT_PROCESS_HEALTH')


def observe_attempt(attempt_path, attempt_sha256, text_file, *, snapshot_end=None):
    raw, attempt_identity = evidence.snapshot(Path(attempt_path))
    need(digest(raw) == attempt_sha256, 'EXACT_ORIGINAL_ATTEMPT_PIN_REQUIRED')
    attempt = strict_json(raw)
    binding = attempt['native_binding']
    wire_raw, wire_identity = evidence.snapshot(Path(text_file), limit=reference.MAX_INLINE_BYTES)
    wire = wire_raw.decode('utf-8')
    body_ref = reference.wire_reference(wire)
    need(body_ref == attempt.get('body_reference'), 'ORIGINAL_BODY_REFERENCE_REQUIRED')
    body_raw, body_identity = evidence.snapshot(Path(body_ref['path']), limit=reference.MAX_BODY_BYTES)
    fence = unique([event['native_paste_fence'] for event in attempt['events']
                    if event.get('phase') == 'PASTE_INTENT'], 'ONE_ORIGINAL_INPUT_FENCE_REQUIRED')
    validate_binding(binding, fence, wire)
    bound = dict(binding, transcript=fence['transcript'])
    with native._open(fence['transcript']['path']) as f:
        native._metadata(f, 'claude', binding['session_id'])
        end = native._check_file(f, bound) if snapshot_end is None else snapshot_end
        size = native._check_file(f, bound)
        start = fence['transcript']['offset']
        need(type(end) is int and start < end <= size and end - start <= MAX_SCAN,
             'BOUNDED_ORIGINAL_SNAPSHOT_REQUIRED')
        f.seek(start); snapshot = f.read(end - start)
        result = correlate(parsed_records(snapshot, start), binding, fence, wire, body_ref, body_raw)
        native._check_file(f, bound)
        f.seek(start); need(f.read(end - start) == snapshot, 'ORIGINAL_RECORDS_CHANGED_DURING_OBSERVATION')
    for path, before, expected_raw, limit in (
        (attempt_path, attempt_identity, raw, evidence.MAX_EVIDENCE_BYTES),
        (text_file, wire_identity, wire_raw, reference.MAX_INLINE_BYTES),
        (body_ref['path'], body_identity, body_raw, reference.MAX_BODY_BYTES),
    ):
        current, identity = evidence.snapshot(Path(path), limit=limit)
        need(current == expected_raw and identity == before, 'ORIGINAL_INPUT_PIN_CHANGED')
    result.update(attempt=dict(path=str(attempt_path), sha256=attempt_sha256), snapshot_end=end,
                  snapshot_sha256=digest(snapshot), historical_receipt_contract=binding.get('receipt_contract'),
                  old_receipt_contract_unchanged=True, current_provider_health='NOT_MEASURED')
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
        result = observe_attempt(args.attempt, args.attempt_sha256, args.text_file,
                                 snapshot_end=args.snapshot_end)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError, UnicodeError, native.NativeDeliveryError) as exc:
        print(json.dumps(dict(schema=SCHEMA, observation='UNVERIFIED', reason=str(exc),
                              native_user_received=False, official_receipt_published=False,
                              attempt_changed=False, input_operations=0, replay_authorized=False)))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
