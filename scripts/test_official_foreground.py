"""Control the live-FD selection contract without any application or input."""
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import cmux_official_foreground as foreground
import cmux_daemon_identity as identity
import cmux_native_delivery as delivery

THREAD = "01234567-89ab-4cde-8f01-23456789abcd"


@unittest.skipUnless(hasattr(os, "getuid"), "Darwin/Unix native file identity")
class OfficialForegroundTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve() / ".codex"
        self.directory = self.home / "sessions/2026/10/30"
        self.directory.mkdir(parents=True)
        self.path = self.directory / ("rollout-2026-10-30T10-00-00-" + THREAD + ".jsonl")
        self.client = {"pid": 23456, "birth": [int(time.time()) - 100, 0],
                       "env": {"HOME": str(self.home.parent)}}
        self.meta = {"timestamp": "2026-01-01T00:00:00Z", "type": "session_meta",
                     "payload": {"source": "cli", "thread_source": "user",
                                 "id": THREAD, "session_id": THREAD, "cli_version": "0.160.0"}}
        self.event = {"timestamp": datetime.now(timezone.utc).isoformat(),
                      "type": "response_item", "payload": {"type": "custom_tool_call", "input": "one"}}
        self.write()
        foreground._seen.clear()
        self.patch = mock.patch.object(foreground.subprocess, "run", side_effect=self.lsof)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def write(self):
        self.path.write_text(json.dumps(self.meta) + "\n" + json.dumps(self.event) + "\n")
        self.path.chmod(0o644)

    def frame(self, path=None, access="u", fd=35):
        path = path or self.path
        return "f" + str(fd) + "\na" + access + "\ni" + str(path.stat().st_ino) + "\nn" + str(path) + "\n"

    def lsof(self, *args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, "p23456\n" + self.frame(), "")

    def test_unique_writable_native_rollout_selects_original_thread(self):
        selected = foreground.read(self.client)
        self.assertEqual(selected["thread_id"], THREAD)
        self.assertEqual(selected["source"], "official-native-rollout-fd-v1")
        self.assertEqual(selected["descriptor"]["inode"], self.path.stat().st_ino)

    def test_exact_native_token_usage_witness_binds_both_session_aliases(self):
        raw = (Path(__file__).parent / "fixtures/official-token-usage.jsonl").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         "37fb5593548895d427da34ebbf9b6dbf3259f311ba5e1df4ed2ebbe5a5796104")
        record = json.loads(raw)
        thread = record["payload"]["thread_id"]
        stamp = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")).timestamp()
        with mock.patch.object(foreground.time, "time", return_value=stamp + 1):
            evidence, _ = foreground._latest(io.BytesIO(raw), len(raw), (stamp - 100) * 1000, thread)
        self.assertEqual(evidence["sha256"], hashlib.sha256(raw).hexdigest())

    def test_token_usage_witness_refuses_missing_or_conflicting_bound_aliases(self):
        raw = (Path(__file__).parent / "fixtures/official-token-usage.jsonl").read_bytes()
        record = json.loads(raw)
        thread = record["payload"]["thread_id"]
        stamp = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")).timestamp()
        for field, value in (("thread_id", "foreign"), ("session_id", "foreign"),
                             ("thread_id", None), ("session_id", None)):
            changed = json.loads(raw)
            changed["payload"][field] = value
            candidate = (json.dumps(changed) + "\n").encode()
            with self.subTest(field=field, value=value), \
                 mock.patch.object(foreground.time, "time", return_value=stamp + 1):
                with self.assertRaisesRegex(ValueError, "session mismatch"):
                    foreground._latest(io.BytesIO(candidate), len(candidate), (stamp - 100) * 1000, thread)
        with self.assertRaisesRegex(ValueError, "session mismatch"):
            foreground._latest(io.BytesIO(raw), len(raw), (stamp - 100) * 1000)

    def test_token_usage_witness_remains_subject_to_post_birth_gate(self):
        raw = (Path(__file__).parent / "fixtures/official-token-usage.jsonl").read_bytes()
        record = json.loads(raw)
        stamp = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")).timestamp()
        with mock.patch.object(foreground.time, "time", return_value=stamp + 1):
            with self.assertRaisesRegex(ValueError, "post-birth record"):
                foreground._latest(io.BytesIO(raw), len(raw), (stamp + 1) * 1000,
                                   record["payload"]["thread_id"])

    def test_resumed_metadata_version_does_not_impersonate_current_executable_version(self):
        self.meta["payload"]["cli_version"] = "0.1.0"
        self.write()
        self.assertEqual(foreground.read(self.client)["thread_id"], THREAD)

    def test_actual_descriptor_parser_refuses_readonly(self):
        with mock.patch.object(foreground.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 0, "p23456\n" + self.frame(access="r"), "")):
            with self.assertRaises(ValueError):
                foreground.read(self.client)

    def test_multiple_writable_rollouts_refuse_ambiguity(self):
        with mock.patch.object(foreground.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 0, "p23456\n" + self.frame() + self.frame(fd=36), "")):
            with self.assertRaises(ValueError):
                foreground.read(self.client)

    def test_process_mismatch_refuses(self):
        with mock.patch.object(foreground.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 0, "p23457\n" + self.frame(), "")):
            with self.assertRaises(ValueError):
                foreground.read(self.client)

    def test_native_metadata_alias_conflict_refuses(self):
        self.meta["payload"]["session_id"] = "11234567-89ab-4cde-8f01-23456789abcd"
        self.write()
        with self.assertRaises(ValueError):
            foreground.read(self.client)

    def test_subagent_metadata_cannot_borrow_root_selection(self):
        self.meta["payload"]["thread_source"] = {"subagent": {"parent_thread_id": THREAD}}
        self.write()
        with self.assertRaises(ValueError):
            foreground.read(self.client)

    def test_non_cli_source_refuses(self):
        self.meta["payload"]["source"] = "app-server"
        self.write()
        with self.assertRaises(ValueError):
            foreground.read(self.client)

    def test_only_prebirth_history_refuses(self):
        self.event["timestamp"] = "2026-01-01T00:00:00Z"
        self.write()
        with self.assertRaises(ValueError):
            foreground.read(self.client)

    def test_future_native_timestamp_refuses(self):
        self.event["timestamp"] = "2999-01-01T00:00:00Z"
        self.write()
        with self.assertRaises(ValueError):
            foreground.read(self.client)

    def test_required_original_observer_never_falls_back(self):
        self.client["env"]["CODEX_CLIENT_THREAD_OBSERVER"] = "1"
        with self.assertRaises(ValueError):
            foreground.read(self.client)

    def test_untrusted_writable_file_refuses(self):
        self.path.chmod(0o666)
        with self.assertRaises(ValueError):
            foreground.read(self.client)

    def test_preserved_historical_hardlink_keeps_exact_native_fd_identity(self):
        os.link(self.path, self.directory / "second.jsonl")
        selected = foreground.read(self.client)
        self.assertEqual(selected["descriptor"]["inode"], self.path.stat().st_ino)
        self.assertEqual(selected["signature"][-1], 2)

    def test_link_count_change_during_native_read_refuses(self):
        original = foreground._latest
        def changing(*args):
            result = original(*args)
            os.link(self.path, self.directory / "new-link.jsonl")
            return result
        with mock.patch.object(foreground, "_latest", side_effect=changing):
            with self.assertRaises(ValueError):
                foreground.read(self.client)

    def test_symlinked_descriptor_refuses(self):
        actual = self.directory / "actual.jsonl"
        self.path.rename(actual)
        self.path.symlink_to(actual)
        with self.assertRaises(ValueError):
            foreground.read(self.client)

    def test_inode_disagreement_refuses(self):
        with mock.patch.object(foreground, "descriptors",
                               return_value={"f": "35", "a": "u", "i": "1", "n": str(self.path)}):
            with self.assertRaises(ValueError):
                foreground.read(self.client)

    def test_partial_tail_does_not_supply_native_record(self):
        self.path.write_text(json.dumps(self.meta) + "\n" + json.dumps(self.event))
        with self.assertRaises(ValueError):
            foreground.read(self.client)

    def test_native_append_preserves_same_selection_and_original_evidence(self):
        before = foreground.read(self.client)
        with self.path.open("a") as stream:
            stream.write(json.dumps(dict(self.event, payload={"type": "message", "text": "new"})) + "\n")
        self.assertEqual(foreground.read(self.client), before)

    def test_rewrite_of_observed_record_refuses(self):
        foreground.read(self.client)
        self.event["payload"]["input"] = "two"
        self.write()
        with self.assertRaises(ValueError):
            foreground.read(self.client)

    def test_descriptor_switch_during_read_refuses(self):
        first = {"f": "35", "a": "u", "i": str(self.path.stat().st_ino), "n": str(self.path)}
        second = dict(first, f="36")
        with mock.patch.object(foreground, "descriptors", side_effect=[first, second]):
            with self.assertRaises(ValueError):
                foreground.read(self.client)

    def test_duplicate_native_metadata_key_refuses(self):
        self.path.write_text('{"type":"session_meta","type":"session_meta"}\n' + json.dumps(self.event) + "\n")
        with self.assertRaises(ValueError):
            foreground.read(self.client)

    def test_failed_or_warning_descriptor_read_refuses(self):
        for result in (subprocess.CompletedProcess([], 1, "", ""),
                       subprocess.CompletedProcess([], 0, "p23456\n" + self.frame(), "warning")):
            with self.subTest(result=result.returncode), mock.patch.object(foreground.subprocess, "run", return_value=result):
                with self.assertRaises(ValueError):
                    foreground.read(self.client)

    def test_timeout_budget_is_respected(self):
        with mock.patch.object(foreground.budget, "timeout", side_effect=subprocess.TimeoutExpired("identity", 0)):
            with self.assertRaises(subprocess.TimeoutExpired):
                foreground.read(self.client)

    def test_canonical_codex_keeps_original_absent_foreground_behavior(self):
        client = {"executable": "/example/codex"}
        with mock.patch.object(identity.foreground, "read", return_value=None), \
             mock.patch.object(identity, "official_codex") as signed:
            self.assertIsNone(identity.foreground_selection(client))
            signed.assert_not_called()

    def test_existing_foreground_error_is_not_masked(self):
        client = {"executable": "/example/codex-aarch64-apple-darwin"}
        with mock.patch.object(identity.foreground, "read", side_effect=ValueError("malformed original")), \
             mock.patch.object(foreground, "read") as fallback:
            with self.assertRaisesRegex(ValueError, "malformed original"):
                identity.foreground_selection(client)
            fallback.assert_not_called()

    def test_delivery_binds_current_native_selection_before_resume_arguments(self):
        client = {"executable": "/example/codex-aarch64-apple-darwin",
                  "argv": ["/example/codex-aarch64-apple-darwin", "resume", "old-thread"]}
        with mock.patch.object(identity, "foreground_selection", return_value={"thread_id": THREAD}), \
             mock.patch.object(delivery, "_session_argument") as legacy:
            self.assertEqual(delivery._live_session(client, "codex", require_native=True), THREAD)
            legacy.assert_not_called()

    def test_delivery_does_not_hide_an_invalid_original_selection(self):
        with mock.patch.object(identity, "foreground_selection", side_effect=ValueError("invalid original")), \
             mock.patch.object(delivery, "_session_argument") as legacy:
            with self.assertRaisesRegex(delivery.NativeDeliveryError, "NATIVE_FOREGROUND_INVALID"):
                delivery._live_session({}, "codex", require_native=True)
            legacy.assert_not_called()


if __name__ == "__main__":
    unittest.main()

