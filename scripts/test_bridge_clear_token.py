"""Drive owned-token recovery with a simulated editor and no live input.

The original failed probe and its gate stay bound. Exercise interruption at
actual key boundaries; a mock success alone cannot prove safe continuation.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_r3_hardening as r3
from test_bridge_clear_recovery import B, H, IDLE, ACTIVE, draft


class OwnedTokenTests(r3.OfflineWorkspaceFixture):
    TOKEN = "B1_01234567"

    def setUp(self):
        super().setUp()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        r3.gate(self.root)
        self.original = self.root / "bridge-test-evidence.json"
        ev = dict(task_id="r3-test", executor="surface:2", token=self.TOKEN,
                  clear_confirmed=False, pre_read_performed=True,
                  compose_was_empty_before_send=True, token_sent=True)
        ev["executors"] = [copy.deepcopy(ev)]
        self.original.write_text(json.dumps(ev))
        self.original_bytes = self.original.read_bytes()
        self.args = r3.args(self.root)
        self.journal = self.root / "bridge-token-cleanup.json"
        self.body = self.TOKEN
        self.screen_override = None
        self.keys = []
        self.after_key = None
        self.read_count = 0
        self.apply_key = True

        def read(*args, **kwargs):
            self.read_count += 1
            return self.screen_override if self.screen_override is not None else draft(self.body)

        def key(surface, name):
            self.assertEqual(surface, "surface:2")
            self.assertIn(name, ("end", "backspace"))
            events = self.events()
            self.assertEqual(events[-1]["event"], "KEY_INTENT")
            self.assertEqual(events[-1]["key"], name)
            self.assertEqual(events[-1]["expected_prefix"], self.body)
            self.keys.append(name)
            if self.apply_key and name == "backspace":
                self.body = self.body[:-1]
            if self.after_key:
                self.after_key(name)

        for obj, name, value in ((B, "read_screen", read), (B, "send_key", key)):
            patch = mock.patch.object(obj, name, side_effect=value)
            patch.start()
            self.addCleanup(patch.stop)
        for obj, name in ((B, "send_text"), (B, "focus_surface"),
                          (B, "clear_known_compose_by_delete"), (B, "submit_text"),
                          (H, "_touch_active_marker")):
            patch = mock.patch.object(obj, name)
            spy = patch.start()
            self.addCleanup(patch.stop)
            self.addCleanup(spy.assert_not_called)
        patch = mock.patch.object(H.time, "sleep")
        patch.start()
        self.addCleanup(patch.stop)

    def events(self):
        return [json.loads(row) for row in self.journal.read_text().splitlines()]

    def run_cleanup(self, success=True):
        if success:
            H.cmd_bridge_clear_token(self.args)
        else:
            with self.assertRaises(SystemExit) as error:
                H.cmd_bridge_clear_token(self.args)
            self.assertEqual(error.exception.code, 1)
        for name in ("handshake-receipt.json", "task-pack.json", "completion-callback-receipt.json"):
            self.assertFalse((self.root / name).exists())

    def test_exact_token_clears_with_durable_intent_before_each_key(self):
        self.run_cleanup()
        self.assertEqual(self.keys, ["end"] + ["backspace"] * len(self.TOKEN))
        self.assertEqual(self.body, "")
        self.assertEqual(self.events()[-1]["event"], "EMPTY_OBSERVED")
        self.assertIs(self.events()[-1]["handshake_performed"], False)
        self.assertEqual(self.original.read_bytes(), self.original_bytes)

    def test_partial_owned_token_uses_only_remaining_length(self):
        self.body = self.TOKEN[:4]
        self.run_cleanup()
        self.assertEqual(self.keys, ["end"] + ["backspace"] * 4)
        self.assertEqual(self.original.read_bytes(), self.original_bytes)

    def test_empty_editor_is_observed_without_any_key(self):
        self.body = ""
        self.run_cleanup()
        self.assertEqual(self.keys, [])
        self.assertEqual([r["event"] for r in self.events()], ["START", "EMPTY_OBSERVED"])

    def test_foreign_draft_unknown_active_and_queue_refuse_without_journal(self):
        for screen in (draft("my draft"), draft(self.TOKEN + "x"), "unknown receiver", ACTIVE,
                       "Messages to be submitted after next tool call\n" + IDLE,
                       "✢ Shimmying…\n" + IDLE, IDLE + "zsh$ "):
            with self.subTest(screen=screen):
                self.screen_override = screen
                self.run_cleanup(False)
                self.assertEqual(self.keys, [])
                self.assertFalse(self.journal.exists())

    def test_user_edit_after_end_stops_before_deleting(self):
        self.after_key = lambda key: setattr(self, "body", "user edit")
        self.run_cleanup(False)
        self.assertEqual(self.keys, ["end"])
        self.assertEqual(self.body, "user edit")
        self.assertEqual(self.events()[-1]["event"], "REFUSED")

    def test_activity_after_first_deletion_stops_at_next_boundary(self):
        def active(key):
            if key == "backspace":
                self.screen_override = "✢ Shimmying…\n" + draft(self.body)
        self.after_key = active
        self.run_cleanup(False)
        self.assertEqual(self.keys, ["end", "backspace"])

    def test_changed_original_binding_stops_before_deletion(self):
        self.after_key = lambda key: self.original.write_bytes(self.original_bytes + b"\n")
        self.run_cleanup(False)
        self.assertEqual(self.keys, ["end"])
        self.assertEqual(self.original.read_bytes(), self.original_bytes + b"\n")

    def test_identity_rechecked_between_end_and_backspace(self):
        original_recheck = H._recheck_workspace_gate
        def recheck(gate):
            if self.keys:
                raise RuntimeError("WORKSPACE_SCOPE_DENIED")
            return original_recheck(gate)
        with mock.patch.object(H, "_recheck_workspace_gate", side_effect=recheck):
            self.run_cleanup(False)
        self.assertEqual(self.keys, ["end"])

    def test_stale_screen_is_reread_without_duplicate_backspace(self):
        self.apply_key = False
        self.run_cleanup(False)
        self.assertEqual(self.keys, ["end", "backspace"])
        self.assertEqual(self.body, self.TOKEN)

    def test_uncertain_key_is_journaled_and_never_replayed(self):
        def failed(key):
            raise RuntimeError("key result unavailable")
        self.after_key = failed
        self.run_cleanup(False)
        frozen = self.journal.read_bytes()
        count = self.read_count
        self.run_cleanup(False)
        self.assertEqual(self.read_count, count)
        self.assertEqual(self.keys, ["end"])
        self.assertEqual(self.journal.read_bytes(), frozen)

    def test_success_journal_also_prevents_replay(self):
        self.body = ""
        self.run_cleanup()
        frozen = self.journal.read_bytes()
        count = self.read_count
        self.run_cleanup(False)
        self.assertEqual(self.read_count, count)
        self.assertEqual(self.journal.read_bytes(), frozen)
        self.assertEqual(self.keys, [])

    def test_invalid_token_and_substituted_journal_refuse_before_input(self):
        ev = json.loads(self.original_bytes)
        ev["token"] = ev["executors"][0]["token"] = "not a bridge probe"
        self.original.write_text(json.dumps(ev))
        self.run_cleanup(False)
        self.assertEqual(self.keys, [])
        self.assertFalse(self.journal.exists())
        self.original.write_bytes(self.original_bytes)
        self.journal.symlink_to(self.root / "elsewhere")
        self.run_cleanup(False)
        self.assertEqual(self.keys, [])
        self.assertFalse((self.root / "elsewhere").exists())


if __name__ == "__main__":
    unittest.main()
