"""Separate prospective observer for causally linked, out-of-order native results.

This observer publishes only a read-only observation. Historical windows,
native-user receipts, controllers, task acceptance and resource ownership stay
unchanged. Append offsets are retained as provenance, never used to infer result
membership in the immediate request's complete canonical batch.
"""
from __future__ import annotations

import argparse
import json
import sys

import model_context_sequential as sequential

original = sequential.original
need = original.need
blocks = original.blocks
object_field = original.object_field
SCHEMA = "model-context-causal-consumption-observation-v1"


def join_batch(records, request, following, expected, *, budget):
    """Bind every result by requestRef, tool id and native parent/source UUID."""
    need(following[2].get("base") == request[2]["id"],
         "IMMEDIATE_CAUSAL_REQUEST_BASE_REQUIRED")
    responses = [item for item in records if item[2].get("type") == "assistant"
                 and item[2].get("requestRef") == request[2]["id"]]
    need(bool(responses), "MATCHING_REAL_CAUSAL_RESPONSE_REQUIRED")
    for item in responses:
        budget.check()
        obj = item[2]
        need(original.same_session(obj, expected) and obj.get("version") == original.VERSION
             and obj.get("isApiErrorMessage", False) is False
             and object_field(obj, "message").get("role") == "assistant"
             and object_field(obj, "message").get("type") == "message"
             and isinstance(object_field(obj, "message").get("content"), list)
             and isinstance(obj.get("uuid"), str) and bool(obj["uuid"]),
             "NONERROR_SAME_REQUEST_CAUSAL_RESPONSE_REQUIRED")
    calls = [(item, block) for item in responses for block in blocks(item[2])
             if block.get("type") == "tool_use"]
    need(0 < len(calls) <= sequential.MAX_TOOLS, "BOUNDED_COMPLETE_TOOL_BATCH_REQUIRED")
    call_ids = [block.get("id") for _item, block in calls]
    need(all(isinstance(value, str) and value for value in call_ids)
         and len(call_ids) == len(set(call_ids)), "UNIQUE_NATIVE_TOOL_IDS_REQUIRED")
    response_ids = {item[2]["uuid"] for item in responses}

    # Keep invalid and duplicate records in the index. Filtering them first
    # could turn a conflicting batch into a selected-subset success.
    result_index, all_results, global_calls = {}, [], {}
    for item in records:
        budget.check()
        for block_index, block in enumerate(blocks(item[2])):
            if item[2].get("type") == "assistant" and block.get("type") == "tool_use":
                global_calls.setdefault(block.get("id"), []).append((item, block))
            if item[2].get("type") == "user" and block.get("type") == "tool_result":
                result = (item, block_index, block)
                result_index.setdefault(block.get("tool_use_id"), []).append(result)
                all_results.append(result)
    # A result whose call is missing cannot be silently discarded by choosing
    # a resealed subset tail. An incomplete snapshot remains unverified.
    for item, _index, returned in all_results:
        budget.check()
        need(len(global_calls.get(returned.get("tool_use_id"), [])) == 1,
             "UNASSOCIATED_OR_DUPLICATE_NATIVE_CALL_REQUIRED")

    results = []
    for call, block in calls:
        budget.check()
        result = original.unique(result_index.get(block["id"], []),
                                 "UNIQUE_NATIVE_TOOL_RESULT_REQUIRED")
        item, _index, returned = result
        obj = item[2]
        need(original.same_session(obj, expected) and obj.get("version") == original.VERSION
             and object_field(obj, "message").get("role") == "user"
             and obj.get("parentUuid") == call[2]["uuid"]
             and obj.get("sourceToolAssistantUUID", call[2]["uuid"]) == call[2]["uuid"]
             and returned.get("is_error", False) is False
             and object_field(obj, "toolUseResult").get("interrupted", False) is False
             and isinstance(obj.get("uuid"), str) and bool(obj["uuid"]),
             "SUCCESSFUL_CAUSAL_NATIVE_RESULT_REQUIRED")
        results.append(result)
    # Whole membership replaces the old interval test as well as its ordering
    # predicate. Native record/block order is still used for canonical hashing.
    actual = [(item[0], block_index) for item, block_index, returned in all_results
              if (item[2].get("parentUuid") in response_ids
                  or item[2].get("sourceToolAssistantUUID") in response_ids
                  or returned.get("tool_use_id") in call_ids)]
    selected = sorted((item[0], block_index) for item, block_index, _block in results)
    need(actual == selected, "EVERY_ACTUAL_CAUSAL_PARALLEL_RESULT_REQUIRED")
    ordered = sorted(results, key=lambda result: (result[0][0], result[1]))
    canonical = sequential.api_hash(dict(role="user", content=[block for _, _, block in ordered]))
    need(isinstance(following[2].get("tail"), list)
         and following[2]["tail"].count(canonical) == 1,
         "IMMEDIATE_COMPLETE_RESULT_BATCH_HASH_REQUIRED")
    return responses, calls, results


def correlate(records, binding, fence, wire, body_ref, body_raw, *, budget=None):
    """Reuse unchanged full-body/hash/pin contracts with the causal batch join."""
    value = sequential.correlate(records, binding, fence, wire, body_ref, body_raw,
                                 budget=budget, batch_join=join_batch)
    value["schema"] = SCHEMA
    value["observation"] = "MODEL_CONTEXT_CAUSALLY_CONSUMED"
    value["historical_window_reclassified"] = False
    value["ordering_basis"] = "IMMEDIATE_REQUEST_BASE_REQUESTREF_WHOLE_RESULT_BATCH_SHA"
    for batch in value["sequential_batches"]:
        batch["next_request_base"] = batch["request_id"]
        batch["response_request_refs"] = [batch["request_id"]] * len(batch["responses"])
        batch["result_file_order_between_requests"] = all(
            batch["request"]["offset"] < item["record"]["offset"] < batch["next_request"]["offset"]
            for item in batch["results"])
    return value


def observe_attempt(attempt_path, attempt_sha256, text_file, *, snapshot_end=None):
    return sequential.observe_attempt(attempt_path, attempt_sha256, text_file,
                                      snapshot_end=snapshot_end, correlator=correlate)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--attempt-sha256", required=True)
    parser.add_argument("--text-file", required=True)
    parser.add_argument("--snapshot-end", type=int)
    args = parser.parse_args(argv)
    try:
        need(sys.flags.ignore_environment and sys.dont_write_bytecode,
             "CONTEXT_OBSERVER_REQUIRES_BE")
        value = observe_attempt(args.attempt, args.attempt_sha256, args.text_file,
                                snapshot_end=args.snapshot_end)
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError, UnicodeError,
            original.native.NativeDeliveryError) as exc:
        print(json.dumps(dict(schema=SCHEMA, observation="UNVERIFIED", reason=str(exc),
            native_user_received=False, official_receipt_published=False,
            attempt_changed=False, input_operations=0, replay_authorized=False,
            historical_window_reclassified=False)))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
