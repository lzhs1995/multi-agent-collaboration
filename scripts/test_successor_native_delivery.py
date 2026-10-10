"""Real-file native/journal behavior with only terminal and process I/O replaced."""
import contextlib
import io
import json
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import successor_native_delivery as delivery
import successor_rebind as rebind
import successor_rebind_cli as cli
import test_successor_rebind as fixtures
from test_successor_rebind import maintenance_artifact, SUCCESSOR, TARGET, NEW_WS, OLD_WS


class FakeTerminal:
    def __init__(self, owner):
        self.owner = owner
        self.wire = None
        self.operations = []
        self.response = "user"
        self.before_read = None
        self.fail_paste = False
        self.occupied = False

    def pin_successor_pair(self, artifact, **kwargs):
        pair = rebind.resolve_pair(self.owner.identity, self.owner.tree, artifact)
        return dict(mode=pair["mode"],
                    caller_surface_uuid=pair["caller"]["surface_uuid"],
                    caller_workspace_uuid=pair["caller"]["workspace_uuid"],
                    caller_pane_uuid=pair["caller"]["pane_uuid"],
                    target_surface_uuid=pair["target"]["surface_uuid"],
                    target_workspace_uuid=pair["target"]["workspace_uuid"],
                    target_pane_uuid=pair["target"]["pane_uuid"])

    def _run(self, *args):
        return json.dumps(self.owner.tree)

    def read_screen_successor(self, artifact):
        if self.before_read is not None:
            action, self.before_read = self.before_read, None
            action()
        return "foreign draft" if self.occupied else "idle" if self.wire is None else "draft:" + self.wire

    def require_agent_input(self, *args):
        pass

    def compose_block_is_empty(self, screen):
        return screen == "idle"

    def receiver_cannot_submit_now(self, screen):
        return False

    def pending_queue_holds(self, screen, text):
        return False

    def _exact_pending_text(self, screen, text):
        return screen == "draft:" + text

    def _draft_structure(self, screen, text):
        return screen

    def _codex_tab_queue_allowed(self, screen, text):
        return False

    def _queued_or_active_input(self, screen):
        return False

    def _run_successor(self, *args, artifact):
        # Observe actual durable bytes at the terminal side-effect boundary.
        journal = self.owner.journal()
        attempt = json.loads((journal / "attempt.json").read_text())
        events = [json.loads(path.read_text()) for path in sorted(journal.glob("event-*.json"))]
        assert attempt["native_binding"]["transcript"]["inode"] == self.owner.transcript.stat().st_ino
        paste = [event for event in events if event["phase"] == "PASTE_INTENT"]
        assert len(paste) == 1 and "native_paste_fence" in paste[0]
        self.operations.append(args[0])
        if args[0] == "rpc":
            assert not any(event["phase"] == "KEY_INTENT" for event in events)
            if self.fail_paste:
                raise RuntimeError("ambiguous paste failure")
            self.wire = json.loads(args[2])["text"]
        else:
            assert len([event for event in events if event["phase"] == "KEY_INTENT"]) == 1
            if self.response == "user":
                self.owner.append_record(self.wire)
            elif self.response == "queued":
                self.owner.append_record(self.wire, queued=True)
            elif self.response == "partial":
                self.owner.append_record(self.wire, newline=False)
            elif self.response == "wrong":
                self.owner.append_record(self.wire + " ")
            elif self.response == "cross_fence":
                with self.owner.transcript.open("ab") as stream:
                    stream.write(self.owner.cross_fence_tail)
        return "ok"


class SuccessorNativeDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.artifact = maintenance_artifact(self.root)
        self.tree = fixtures.SuccessorRebindTests.tree(self)
        self.identity = {"caller": {"surface_ref": "surface:46", "workspace_ref": "workspace:15", "pane_ref": "pane:33"}}
        self.session = str(uuid.uuid4())
        self.transcript = self.root / (self.session + ".jsonl")
        self.transcript.write_text(json.dumps({"sessionId": self.session, "type": "system"}) + "\n")
        self.process = dict(pid=50101, birth=[1, 0], executable="/local/claude", file_identity=[1, 2],
                            argv=["claude"], env={"CMUX_SURFACE_ID": TARGET, "CMUX_WORKSPACE_ID": OLD_WS})
        self.bridge = FakeTerminal(self)
        self.marker = "SUCCESSOR_TEST_ONE"
        self.text = "STATUS: " + self.marker + " acknowledge this maintenance communication."
        for context in (patch.object(delivery.native, "_target_process", return_value=self.process),
                        patch.object(delivery.native.identity, "process", return_value=self.process),
                        patch.object(delivery.native, "_live_session", return_value=self.session),
                        patch.object(delivery.native, "_transcript_path", return_value=self.transcript),
                        patch.object(delivery.native, "_native_root", return_value=self.root),
                        patch.object(delivery.native.subprocess, "run", return_value=Mock(stdout="ttys052")),
                        patch.object(delivery.time, "sleep")):
            context.start()
            self.addCleanup(context.stop)

    def record_bytes(self, text, *, queued=False):
        record = dict(sessionId=self.session, timestamp=datetime.now(timezone.utc).isoformat())
        if queued:
            record.update(type="attachment", attachment={"type": "queued_command", "prompt": text})
        else:
            record.update(type="user", message={"role": "user", "content": text})
        return json.dumps(record).encode() + b"\n"

    def append_record(self, text, *, queued=False, newline=True):
        raw = self.record_bytes(text, queued=queued)
        with self.transcript.open("ab") as stream:
            stream.write(raw if newline else raw[:-1])

    def journal(self):
        return delivery._paths(self.artifact, self.marker, self.root)[1]

    def submit(self):
        return delivery.submit(self.bridge, self.artifact, self.text,
                               marker=self.marker, state_root=self.root, wait_seconds=0)

    def reconcile(self):
        return delivery.reconcile(self.bridge, self.artifact, self.text,
                                  marker=self.marker, state_root=self.root)

    def test_actual_journal_precedes_paste_and_key_and_repeated_submit_has_zero_input(self):
        first = self.submit()
        self.assertTrue(first["confirmed"])
        self.assertEqual(self.bridge.operations, ["rpc", "send-key"])
        original_bytes = (self.journal() / "attempt.json").read_bytes()
        again = self.submit()
        self.assertTrue(again["confirmed"])
        self.assertEqual(again["input_operations"], 0)
        self.assertEqual(self.bridge.operations, ["rpc", "send-key"])
        self.assertEqual((self.journal() / "attempt.json").read_bytes(), original_bytes)

    def test_record_between_bind_and_paste_is_not_a_receipt(self):
        self.bridge.before_read = lambda: self.append_record(self.text)
        self.bridge.response = "none"
        self.assertFalse(self.submit()["confirmed"])
        self.append_record(self.text)
        self.assertTrue(self.reconcile()["confirmed"])

    def test_fence_crossing_record_is_not_a_receipt(self):
        def partial_before_fence():
            raw = self.record_bytes(self.text)
            middle = len(raw) // 2
            with self.transcript.open("ab") as stream:
                stream.write(raw[:middle])
            self.cross_fence_tail = raw[middle:]
        self.bridge.before_read = partial_before_fence
        self.bridge.response = "cross_fence"
        self.assertFalse(self.submit()["confirmed"])
        self.append_record(self.text)
        self.assertTrue(self.reconcile()["confirmed"])

    def test_incomplete_native_record_waits_for_complete_line(self):
        self.bridge.response = "partial"
        result = self.submit()
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["state"], "NATIVE_RECORD_INCOMPLETE")
        with self.transcript.open("ab") as stream:
            stream.write(b"\n")
        self.assertTrue(self.reconcile()["confirmed"])

    def test_queued_record_does_not_confirm_native_reception(self):
        self.bridge.response = "queued"
        result = self.submit()
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["state"], "NATIVE_QUEUED")

    def test_whole_exact_payload_required(self):
        self.bridge.response = "wrong"
        self.assertFalse(self.submit()["confirmed"])

    def test_long_body_is_immutable_reference_and_receipt_covers_notice_only(self):
        self.text += "\n" + "确切正文\t" * 200
        result = self.submit()
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["confirmation_scope"], "reference_notice")
        self.assertFalse(result["body_read_confirmed"])
        self.assertNotIn("\n", self.bridge.wire)
        self.assertLessEqual(len(self.bridge.wire.encode()), 700)
        body = Path(result["body_reference"]["path"])
        self.assertEqual(body.read_bytes(), self.text.encode())
        self.assertEqual(body.stat().st_mode & 0o222, 0)

    def test_ambiguous_paste_never_replays_or_submits_a_key(self):
        self.bridge.fail_paste = True
        with self.assertRaisesRegex(RuntimeError, "ambiguous paste failure"):
            self.submit()
        result = self.submit()
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["input_operations"], 0)
        self.assertEqual(self.bridge.operations, ["rpc"])

    def test_foreign_draft_has_zero_input_and_no_attempt(self):
        self.bridge.occupied = True
        with self.assertRaisesRegex(delivery.native.NativeDeliveryError, "COMPOSE_OCCUPIED"):
            self.submit()
        self.assertEqual(self.bridge.operations, [])
        self.assertFalse((self.journal() / "attempt.json").exists())

    def test_changed_body_cannot_reuse_original_marker(self):
        self.submit()
        self.text += " changed"
        with self.assertRaisesRegex(delivery.native.NativeDeliveryError, "ORIGINAL_ATTEMPT_CHANGED"):
            self.submit()
        self.assertEqual(self.bridge.operations, ["rpc", "send-key"])

    def test_pane_drift_before_key_preserves_single_paste(self):
        original_read = self.bridge.read_screen_successor
        def drift(artifact):
            result = original_read(artifact)
            if self.bridge.wire is not None:
                self.tree["windows"][0]["workspaces"][0]["panes"][0]["id"] = SUCCESSOR
            return result
        self.bridge.read_screen_successor = drift
        with self.assertRaisesRegex(rebind.SuccessorRebindError, "pane drift"):
            self.submit()
        self.assertEqual(self.bridge.operations, ["rpc"])

    def test_missing_paste_fence_cannot_be_reconstructed(self):
        binding = delivery.bind_target(self.bridge, self.artifact, self.text)
        self.append_record(self.text)
        with self.assertRaisesRegex(delivery.native.NativeDeliveryError, "PASTE_FENCE_REQUIRED"):
            delivery.scan_received(self.bridge, self.artifact, binding, self.text)


class SuccessorCliTests(unittest.TestCase):
    def test_validate_and_error_both_emit_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / "artifact.json"
            artifact = maintenance_artifact(root)
            path.write_text(json.dumps(artifact))
            output = io.StringIO()
            with patch("sys.argv", ["cli", "validate", "--artifact", str(path)]), contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(), 0)
            self.assertEqual(json.loads(output.getvalue())["schema"], rebind.SCHEMA_V2)
            output = io.StringIO()
            with patch("sys.argv", ["cli", "submit", "--artifact", str(path)]), contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(), 2)
            self.assertEqual(json.loads(output.getvalue())["state"], "ERROR")


if __name__ == "__main__":
    unittest.main()
