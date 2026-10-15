"""Public, synthetic equivalents of the observed Claude wrapping geometries.

No captured screen, identity, session, message body or private path is included.
External process/UI I/O is replaced; task contracts, journals and receipt checks
run against temporary files.
"""
import hashlib
import json
from pathlib import Path
import textwrap
import unittest
from unittest.mock import patch

import cmux_bridge as bridge
import cmux_prompt_reference as reference
from native_test_support import NativeFixture


DIGEST = hashlib.sha256(b"public synthetic compose fixture").hexdigest()
TASK_SUFFIX = (
    " READ_AND_OBEY_REQUIRED_SKILL_FIRST; verify pack SHA; follow only its scope; "
    "write report then call cmux_bridge.submit_completion_callback(pack)."
)
FOOTER = [
    "[Opus 5] │ fixture git:(main) │ ⏱️  1h 2m",
    "上下文 █░░░░░░░░░ 13%",
    "1 CLAUDE.md | 9 MCPs | 7 钩子",
    "✓ Bash ×2 | ✓ Read ×1",
    "⏵⏵ bypass permissions on (shift+tab to cycle)",
]


def frame(rows, width, before=()):
    return "\n".join([
        *before, "─" * width, "❯ " + rows[0],
        *("  " + row for row in rows[1:]), "─" * width, *FOOTER,
    ]) + "\n"


def reference_material(padding=16, before=()):
    # BODY token: 144 display cells; SHA word wraps at the 183-cell frame.
    path = "/tmp/" + "f" * 63 + "/" + DIGEST + ".txt"
    ref = dict(format=reference.SINGLE_LINE_FORMAT, path=path,
               sha256=DIGEST, bytes=1660)
    text = reference._wire("CASE0001", ref)
    token = "BODY=" + json.dumps(path)
    header, _, tail = text.partition(" " + token + " ")
    assert len(token) == 144
    rows = [header, token + " " * padding, *textwrap.wrap(
        tail, width=179, break_long_words=False, break_on_hyphens=False)]
    return frame(rows, 183, before), text


def task_path(root, token_cells):
    # One wide character plus ASCII keeps fixture display width independent
    # of the parser under test.
    base = str(root / "文" / "task-pack.json")
    token = "PACK=" + json.dumps(base, ensure_ascii=False)
    fill = token_cells - len(token) - 1
    assert fill > 0
    return root / ("文" + "p" * fill) / "task-pack.json"


def task_frame(text, padding):
    header, _, rest = text.partition(" PACK=")
    path, end = json.JSONDecoder().raw_decode(rest)
    token = "PACK=" + json.dumps(path, ensure_ascii=False)
    # The recorded first PACK row uses 114 cells.
    split = next(i for i in range(len(token))
                 if len(token[:i]) + token[:i].count("文") == 114)
    tail = rest[end + 1:]
    cut = tail.index(" pack SHA;")
    return frame([header, token[:split], token[split:] + " " * padding,
                  tail[:cut], tail[cut + 1:]], 118)


def task_material(token_cells, padding):
    path = task_path(Path("/tmp/public-fixture"), token_cells)
    text = ('TASK_PACK_V2 TASK="public-review" PACK='
            + json.dumps(str(path), ensure_ascii=False)
            + " SHA256=" + DIGEST + TASK_SUFFIX)
    return task_frame(text, padding), text


class ClaudeRenderedPaddingTests(unittest.TestCase):
    def test_reference_padding_and_cursor_cell_are_recognized(self):
        for padding in (16, 17):
            with self.subTest(padding=padding):
                screen, text = reference_material(padding)
                self.assertTrue(bridge._exact_pending_text(screen, text))
                cursor = screen.replace("\n" + "─" * 183,
                                        " \n" + "─" * 183, 1)
                # The existing one-cell cursor rule is limited to Claude's
                # observed NBSP prompt separator, not every trailing space.
                self.assertFalse(bridge._exact_pending_text(cursor, text))
                cursor = cursor.replace("\n❯ ", "\n❯\u00a0", 1)
                self.assertTrue(bridge._exact_pending_text(cursor, text))
                self.assertFalse(bridge._exact_pending_text(
                    cursor.replace(" \n" + "─" * 183,
                                   "  \n" + "─" * 183, 1), text))

    def test_reference_other_padding_and_geometry_are_refused(self):
        for padding in (1, 2, 15, 18, 32):
            screen, text = reference_material(padding)
            with self.subTest(padding=padding):
                self.assertFalse(bridge._exact_pending_text(screen, text))
        screen, text = reference_material()
        for width in (182, 184, 250):
            with self.subTest(width=width):
                self.assertFalse(bridge._exact_pending_text(
                    screen.replace("─" * 183, "─" * width), text))

    def test_payload_spaces_unicode_hash_and_footer_are_not_normalized(self):
        screen, text = reference_material()
        changes = [
            screen.replace("BODY=", "BODY= ", 1),
            screen.replace(" " * 16 + "\n", " " * 15 + "\u00a0\n", 1),
            screen.replace('.txt"', '.txtX"', 1),
            screen.replace("SHA256=", "SHA256=x", 1),
            screen.replace("  SHA256=", "   SHA256=", 1),
            screen.replace("BODY=", "EXTRA BODY=", 1),
            screen.replace("  SHA256=", "  \n  SHA256=", 1),
            screen.replace("bypass permissions on", "unknown permissions", 1),
        ]
        for i, changed in enumerate(changes):
            with self.subTest(mutation=i):
                self.assertFalse(bridge._exact_pending_text(changed, text))
        for expected in (text.replace(" BODY=", "  BODY=", 1), text + " "):
            self.assertFalse(bridge._exact_pending_text(screen, expected))

    def test_partial_or_added_draft_is_not_complete(self):
        screen, text = reference_material()
        border = "\n" + "─" * 183
        for changed in (
            screen.replace(border, "\n  extra" + border, 1),
            screen.replace("body reading and acceptance are separate.", "body ...", 1),
            screen.replace(border, "\n" + "─" * 182, 1),
        ):
            self.assertFalse(bridge._exact_pending_text(changed, text))

    def test_two_reads_retain_original_padding_without_io(self):
        screen, text = reference_material(16)
        changed, _ = reference_material(17)
        original = bridge._draft_structure(screen, text)
        self.assertNotEqual(original, bridge._draft_structure(changed, text))
        with patch.object(bridge.time, "sleep"), \
                patch.object(reference, "read_body") as read, \
                patch.object(bridge, "send_text") as paste, \
                patch.object(bridge, "send_key") as key:
            with patch.object(bridge, "read_screen", return_value=screen) as observe:
                self.assertEqual(bridge._stable_owned_draft(
                    "offline", text, 200, None), screen)
                self.assertEqual(observe.call_count, 2)
            with patch.object(bridge, "read_screen", return_value=changed):
                with self.assertRaisesRegex(
                        bridge.DispatchUnconfirmed, "COMPOSE_STRUCTURE_CHANGED"):
                    bridge._stable_owned_draft(
                        "offline", text, 200, None, expected_structure=original)
            read.assert_not_called()
            paste.assert_not_called()
            key.assert_not_called()

    def test_three_task_pack_geometries_and_negative_controls(self):
        for cells, padding in ((158, 52), (166, 44), (159, 27)):
            screen, text = task_material(cells, padding)
            with self.subTest(cells=cells):
                self.assertTrue(bridge._exact_pending_text(screen, text))
                for bad in (padding - 1, padding + 1):
                    self.assertFalse(bridge._exact_pending_text(
                        task_frame(text, bad), text))
                for changed in (
                    screen.replace("SHA256=", "SHA256=x", 1),
                    screen.replace("PACK=", "PACK= ", 1),
                    screen.replace(" " * padding + "\n",
                                   " " * (padding - 1) + "\u00a0\n", 1),
                    screen.replace("─" * 118, "─" * 119),
                    screen.replace("scope;", "scopeX;", 1),
                ):
                    self.assertFalse(bridge._exact_pending_text(changed, text))
                self.assertFalse(bridge._exact_pending_text(screen, text + " "))

    def test_unverified_frame_does_not_authorize_a_submission_key(self):
        for screen, text in (reference_material(), task_material(158, 52)):
            bad = screen.replace("SHA256=", "SHA256=x", 1)
            with NativeFixture(provider="claude") as native, \
                    patch.object(bridge, "read_screen", return_value=bad):
                with self.assertRaisesRegex(bridge.DispatchUnconfirmed, "DRAFT_NOT_READY"):
                    bridge._stable_owned_draft("offline", text, 200, None)
                native.send.assert_not_called()
                native.key.assert_not_called()

    def test_goal_duration_does_not_hide_current_active_spinner(self):
        for duration in ("1d", "2d", "1h 2m", "3m 4s"):
            screen, text = reference_material(
                before=["· Working… (1m 2s)", "◎ /goal active (" + duration + ")"])
            self.assertTrue(bridge._exact_pending_text(screen, text))
            self.assertTrue(bridge._queued_or_active_input(screen))

    def test_monitor_and_shell_footer_variants_are_bounded(self):
        screen, text = reference_material()
        for monitor in ("1 monitor", "2 monitors", "12 monitors", "20 monitors", "1 shell"):
            changed = screen.replace(
                "(shift+tab to cycle)", "· " + monitor + " · ← for agents")
            self.assertTrue(bridge._exact_pending_text(changed, text))
        for monitor in ("0 monitors", "2 shells", "unknown"):
            changed = screen.replace(
                "(shift+tab to cycle)", "· " + monitor + " · ← for agents")
            self.assertFalse(bridge._exact_pending_text(changed, text))

    def test_unknown_goal_or_footer_stays_unparsed(self):
        screen, text = reference_material(
            before=["· Working… (1m 2s)", "◎ /goal active (unknown)"])
        self.assertFalse(bridge._exact_pending_text(screen, text))
        with self.assertRaises(bridge.DispatchUnconfirmed):
            bridge.require_agent_input(screen, "offline")

    def test_historical_monitor_summary_does_not_become_current_activity(self):
        screen = frame([""], 183, before=["1 monitor still running", "✻ Finished for 1m 2s"])
        self.assertFalse(bridge._queued_or_active_input(screen))


class ClaudePaddingNativeContractTests(unittest.TestCase):
    def material(self, native, cells, padding):
        path = task_path(native.home, cells)
        path.parent.mkdir()
        report = native.home / "report.md"
        report.write_text("Frozen synthetic output.\n", encoding="utf-8")
        task = "public-review"
        pack = dict(
            task_id=task, completion_nonce="test-nonce-12345", draft=False,
            executor_uuid="target", report=str(report),
            required_skill=str(bridge.COLLABORATION_SKILL_PATH),
            callback_target="surface:1",
            completion_receipt=str(path.parent / "completion.json"),
            completion_delivery=dict(
                transport="cmux_bridge.submit_completion_callback", require_confirmed=True),
            completion_callback="DONE|" + task + "|test-nonce-12345|REPORT=" + str(report))
        path.write_text(json.dumps(pack, ensure_ascii=False) + "\n", encoding="utf-8")
        text = bridge.task_pack_notice(path)
        return path, text, frame([""], 118), task_frame(text, padding)

    def test_exact_native_receipt_confirms_once_after_one_enter(self):
        for cells, padding in ((158, 52), (166, 44), (159, 27)):
            with self.subTest(cells=cells), NativeFixture(provider="claude") as native:
                path, text, idle, draft = self.material(native, cells, padding)
                native.key.side_effect = native.receipt_on_key(text)
                with patch("availability_contract.require_action"), \
                        patch.object(bridge, "read_screen",
                                     side_effect=native.ready_screens(idle, draft)):
                    result = bridge.submit_task_pack("offline", text, str(path))
                    self.assertTrue(result["confirmed"])
                    self.assertEqual(result["confirmation_source"], "native_user_message_v1")
                    with self.assertRaises(bridge.TaskPackContractError):
                        bridge.submit_task_pack("offline", text, str(path))
                native.send.assert_called_once_with("offline", text)
                native.key.assert_called_once_with("offline", "enter")

    def test_enter_empty_editor_and_queue_do_not_prove_reception(self):
        with NativeFixture(provider="claude") as native:
            path, text, idle, draft = self.material(native, 158, 52)
            with patch("availability_contract.require_action"), \
                    patch.object(bridge, "read_screen",
                                 side_effect=native.ready_screens(idle, draft, idle)):
                with self.assertRaises(bridge.DispatchUnconfirmed):
                    bridge.submit_task_pack("offline", text, str(path))
                with self.assertRaises(bridge.TaskPackContractError):
                    bridge.submit_task_pack("offline", text, str(path))
                native.append_queued(text)
                with self.assertRaises(bridge.DispatchUnconfirmed):
                    bridge.submit_task_pack("offline", text, str(path), reconcile_only=True)
                native.append_user(text)
                self.assertTrue(bridge.submit_task_pack(
                    "offline", text, str(path), reconcile_only=True)["reconciled_read_only"])
            native.send.assert_called_once_with("offline", text)
            native.key.assert_called_once_with("offline", "enter")


if __name__ == "__main__":
    unittest.main()
