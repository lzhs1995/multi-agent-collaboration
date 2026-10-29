"""Regression of pinned native batches; never runs an official window or input.

The evidence report and original transcript slices remain local. This command
only reads their exact bytes, calls the prospective batch join, and prints a
separate regression result. No transcript command is executed.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import model_context_causal as causal
import model_context_sequential as sequential

MAX_PREFIX = 8 * 1024 * 1024


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def run(report_path, report_sha256):
    path = Path(report_path).resolve(strict=True)
    raw = path.read_bytes()
    causal.need(digest(raw) == report_sha256, "EXACT_REAL_REPORT_PIN_REQUIRED")
    report = sequential.original.strict_json(raw)
    causal.need(report["of"] == report["holding"] == len(report["cases"]) == 4,
                "FOUR_FROZEN_ORIGINAL_CASES_REQUIRED")
    prefixes = {}
    for case in report["cases"]:
        for ref in case["original_records"]:
            key = ref["path"]
            end = ref["offset"] + ref["bytes"]
            causal.need(end <= MAX_PREFIX, "BOUNDED_REAL_PREFIX_REQUIRED")
            prefixes[key] = max(prefixes.get(key, 0), end)
    snapshots, source_pins = {}, []
    for name, end in prefixes.items():
        with Path(name).open("rb") as stream:
            snapshot = stream.read(end)
        causal.need(len(snapshot) == end, "COMPLETE_REAL_PREFIX_REQUIRED")
        snapshots[name] = sequential.original.parsed_records(snapshot, 0)
        source_pins.append(dict(path=name, bytes=end, prefix_sha256=digest(snapshot)))

    observations = []
    for case in report["cases"]:
        refs = case["original_records"]
        records = snapshots[refs[0]["path"]]
        by_offset = {item[0]: item for item in records}
        for ref in refs:
            item = by_offset[ref["offset"]]
            causal.need(len(item[1]) == ref["bytes"] and digest(item[1]) == ref["sha256"],
                        "EXACT_ORIGINAL_NATIVE_SLICE_REQUIRED")
        requests = [item for item in records if item[2].get("type") == "api-request"]
        request = sequential.original.unique(
            [item for item in requests if item[2].get("id") == case["preceding_request_id"]],
            "EXACT_PRECEDING_REAL_REQUEST_REQUIRED")
        following = sequential.original.unique(
            [item for item in requests if item[2].get("id") == case["next_request_id"]],
            "EXACT_FOLLOWING_REAL_REQUEST_REQUIRED")
        causal.need(requests.index(request) + 1 == requests.index(following),
                    "IMMEDIATE_REAL_REQUEST_REQUIRED")
        response_rows = [item for item in records if item[2].get("type") == "assistant"
                         and item[2].get("requestRef") == request[2]["id"]]
        expected = response_rows[0][2]["sessionId"]
        responses, calls, results = causal.join_batch(
            records, request, following, expected, budget=sequential.Budget())
        ordered = sorted(results, key=lambda result: (result[0][0], result[1]))
        batch_sha = sequential.api_hash(dict(role="user", content=[block for _, _, block in ordered]))
        causal.need(batch_sha == case["canonical_batch_sha256"]
                    and following[2]["tail"].count(batch_sha) == 1,
                    "EXACT_WHOLE_REAL_BATCH_TAIL_REQUIRED")
        causal.need(any(block["tool_use_id"] == case["tool_id"] for _, _, block in results),
                    "ORIGINAL_REAL_TOOL_REQUIRED")
        physical_order = all(call[0] < result[0][0] < following[0]
                             for (call, _), result in zip(calls, results))
        causal.need(not physical_order, "ORIGINAL_FILE_ORDER_FAILURE_REQUIRED")

        # These are memory-only counterfactuals, not modified native evidence.
        negative_controls = []
        wrong_base = copy.deepcopy(following)
        wrong_base[2]["base"] = "foreign"
        try:
            causal.join_batch(records, request, wrong_base, expected, budget=sequential.Budget())
        except ValueError as error:
            negative_controls.append(dict(change="wrong_base", refused=str(error)))
        else:
            raise AssertionError("wrong causal base accepted")
        changed = copy.deepcopy(records)
        returned = next(item for item in changed if item[0] == results[0][0][0])
        returned[2]["parentUuid"] = "foreign"
        try:
            causal.join_batch(changed, request, following, expected, budget=sequential.Budget())
        except ValueError as error:
            negative_controls.append(dict(change="wrong_parent", refused=str(error)))
        else:
            raise AssertionError("wrong result parent accepted")
        wrong_tail = copy.deepcopy(following)
        wrong_tail[2]["tail"].remove(batch_sha)
        try:
            causal.join_batch(records, request, wrong_tail, expected, budget=sequential.Budget())
        except ValueError as error:
            negative_controls.append(dict(change="missing_whole_batch_sha", refused=str(error)))
        else:
            raise AssertionError("missing whole batch SHA accepted")
        observations.append(dict(lane=case["lane"], tool_id=case["tool_id"],
            request_id=request[2]["id"], next_request_id=following[2]["id"],
            result_batch_sha256=batch_sha, whole_batch_tail_count=1,
            tool_results=len(results), file_order_holds=physical_order,
            causal_batch_join_passed=True, negative_controls=negative_controls,
            original_records=refs))
    return dict(schema="causal-result-order-frozen-regression-v1",
        report=dict(path=str(path), bytes=len(raw), sha256=report_sha256),
        source_prefixes=source_pins, cases=observations, passed=4, total=4,
        scope="COMPONENT_BATCH_JOIN_REGRESSION_ONLY_NOT_FULL_BODY_CONSUMPTION",
        official_window_runs=0, historical_window_reclassified=False,
        original_inputs_mutated=0, input_operations=0, application_calls=0,
        official_receipt_published=False, acceptance=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--report-sha256", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(run(args.report, args.report_sha256), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
