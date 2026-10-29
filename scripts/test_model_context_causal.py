"""Change-specific full-consumer controls for causal result append order."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import model_context_causal as causal
import model_context_sequential as sequential
from test_model_context_consumption import raw, TARGET
import test_model_context_sequential as fixtures


class CausalOrderTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SequentialConsumptionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.events = copy.deepcopy(self.fixture.events)

    def check(self, events=None, legacy=False):
        f = self.fixture.fixture
        rows = sequential.original.parsed_records(
            b"".join(raw(obj) for obj in (self.events if events is None else events)),
            f.pin["offset"])
        fn = sequential.correlate if legacy else causal.correlate
        return fn(rows, f.binding, f.fence, f.wire, f.ref, f.body)

    def inverted(self):
        # API request records retain immediate order; results append later.
        return [self.events[n] for n in (0, 1, 2, 3, 5, 4, 6, 8, 7, 9)]

    def refused(self, events, reason="."):
        with self.assertRaisesRegex(ValueError, reason):
            self.check(events)

    def test_inverted_full_read_and_hash_batches_have_a_separate_evidence_class(self):
        value = self.check(self.inverted())
        self.assertEqual(value["observation"], "MODEL_CONTEXT_CAUSALLY_CONSUMED")
        self.assertEqual(value["schema"], causal.SCHEMA)
        self.assertEqual(value["parallel_tool_results"], 2)
        self.assertTrue(all(not b["result_file_order_between_requests"]
                            for b in value["sequential_batches"]))
        for name in ("native_user_received", "business_accepted", "official_receipt_published",
                     "task_authorized", "resource_released", "replay_authorized",
                     "historical_window_reclassified"):
            self.assertFalse(value[name], name)
        self.assertEqual(value["input_operations"], 0)

    def test_original_default_still_refuses_inverted_append_order(self):
        with self.assertRaisesRegex(ValueError, "SUCCESSFUL_ORDERED_NATIVE_RESULT"):
            self.check(self.inverted(), legacy=True)
        self.assertEqual(self.check(legacy=True)["schema"], sequential.SCHEMA)

    def test_append_order_does_not_depend_on_wall_clock_timestamp(self):
        events = self.inverted()
        for n, event in enumerate(events):
            event["timestamp"] = "2099-01-01T00:00:" + str(59-n).zfill(2) + "Z"
        self.assertTrue(self.check(events)["body_full_read"])

    def test_wrong_immediate_base_refuses_even_with_exact_batch_hash(self):
        events = self.inverted()
        events[4]["base"] = "foreign"
        self.refused(events, "CAUSAL_REQUEST_BASE")

    def test_wrong_call_requestref_cannot_be_hidden_by_valid_result_and_tail(self):
        events = self.inverted()
        events[3]["requestRef"] = "foreign"
        self.refused(events, "CAUSAL_RESPONSE")

    def test_parent_and_source_conflicts_refuse_inverted_results(self):
        for field in ("parentUuid", "sourceToolAssistantUUID"):
            events = self.inverted()
            events[8][field] = "foreign"
            with self.subTest(field=field):
                self.refused(events, "CAUSAL_NATIVE_RESULT")

    def test_foreign_session_meta_sidechain_and_alias_conflict_refuse(self):
        for field, value in (("sessionId", TARGET), ("session_id", TARGET),
                             ("isMeta", True), ("isSidechain", True)):
            events = self.inverted()
            events[8][field] = value
            with self.subTest(field=field):
                self.refused(events, "CAUSAL_NATIVE_RESULT")

    def test_failed_or_interrupted_results_remain_unverified(self):
        for field in ("is_error", "interrupted"):
            events = self.inverted()
            if field == "is_error":
                events[8]["message"]["content"][0][field] = True
            else:
                events[8]["toolUseResult"][field] = True
            with self.subTest(field=field):
                self.refused(events, "CAUSAL_NATIVE_RESULT")

    def test_duplicate_tool_result_and_call_uuid_refuse(self):
        for change in ("result", "tool", "uuid"):
            events = self.inverted()
            if change == "result":
                events.insert(9, copy.deepcopy(events[8]))
            elif change == "tool":
                events[6]["message"]["content"][0]["id"] = "tool-hash"
            else:
                events[-1]["uuid"] = events[6]["uuid"]
            with self.subTest(change=change):
                self.refused(events)

    def test_complete_parallel_result_after_following_request_is_included(self):
        events = self.fixture.add_parallel(copy.deepcopy(self.events))
        # Move the complete result out of the physical request interval.
        extra = events.pop(9)
        events.insert(11, extra)
        value = self.check(events)
        self.assertEqual(value["parallel_tool_results"], 3)
        self.assertEqual(len(value["sequential_batches"][1]["results"]), 2)

    def test_missing_parallel_result_refuses(self):
        events = self.fixture.add_parallel(copy.deepcopy(self.events))
        events.pop(9)
        self.refused(events, "UNIQUE_NATIVE_TOOL_RESULT")

    def test_omitted_call_cannot_hide_extra_result_under_resealed_subset_tail(self):
        events = self.fixture.add_parallel(copy.deepcopy(self.events))
        events.pop(7)
        request = next(e for e in events if e.get("id") == "request-two")
        read = next(e for e in events if e.get("uuid") == "result-read")
        request["tail"] = [self.fixture.batch_hash([read])]
        self.refused(events, "UNASSOCIATED_OR_DUPLICATE_NATIVE_CALL")

    def test_partial_or_duplicate_canonical_tail_refuses(self):
        for change in ("missing", "duplicate", "subset"):
            events = self.fixture.add_parallel(copy.deepcopy(self.events))
            following = next(e for e in events if e.get("id") == "request-two")
            if change == "missing":
                following["tail"] = []
            elif change == "duplicate":
                following["tail"] *= 2
            else:
                following["tail"] = [self.fixture.batch_hash([
                    next(e for e in events if e.get("uuid") == "result-read")])]
            with self.subTest(change=change):
                self.refused(events, "COMPLETE_RESULT_BATCH_HASH")

    def test_skipped_request_and_error_final_response_refuse(self):
        for change in ("skip", "error"):
            events = self.inverted()
            if change == "skip":
                events.insert(4, self.fixture.fixture.event(
                    "api-request", id="skipped", base="request-one",
                    querySource="repl_main_thread", tail=["0"*64]))
            else:
                events[-1]["isApiErrorMessage"] = True
            with self.subTest(change=change):
                self.refused(events)

    def test_truncated_read_still_refuses_after_causal_join(self):
        events = self.inverted()
        events[8]["toolUseResult"]["file"]["numLines"] = 1
        self.refused(events, "COMPLETE_EXACT_NATIVE_READ_RESULT")

    def test_compaction_between_final_response_and_late_result_is_not_ignored(self):
        events = self.inverted()
        result = events.pop(8)
        events.append(self.fixture.fixture.event("system", subtype="compact-boundary"))
        events.append(result)
        self.refused(events, "COMPACTION_TRANSITION")

    def test_public_cli_is_readonly_and_preserves_original_attempt_and_default(self):
        f = self.fixture.fixture
        f.events = self.inverted()
        attempt, notice = f.write_original_attempt()
        before = {str(p): p.read_bytes() for p in f.home.rglob("*") if p.is_file()}
        args = ["--attempt", str(attempt), "--attempt-sha256",
                sequential.digest(attempt.read_bytes()), "--text-file", str(notice),
                "--snapshot-end", str(f.transcript.stat().st_size)]
        for entry, code, klass in ((causal, 0, "MODEL_CONTEXT_CAUSALLY_CONSUMED"),
                                  (sequential, 2, "UNVERIFIED")):
            completed = subprocess.run(
                [sys.executable, "-BE", str(Path(entry.__file__).resolve()), *args],
                capture_output=True, text=True, timeout=12)
            with self.subTest(entry=entry.__name__):
                self.assertEqual(completed.returncode, code, completed.stderr)
                value = json.loads(completed.stdout)
                self.assertEqual(value["observation"], klass)
                self.assertEqual(value["input_operations"], 0)
                self.assertFalse(value["official_receipt_published"])
        self.assertEqual(before, {str(p): p.read_bytes() for p in f.home.rglob("*") if p.is_file()})

    def test_compute_budget_stays_checked_in_causal_membership_scan(self):
        with patch.object(sequential.time, "monotonic", side_effect=[0, 11]):
            self.refused(self.inverted(), "TIME_BUDGET")


if __name__ == "__main__":
    unittest.main(verbosity=2)

