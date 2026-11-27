import json
import subprocess
import unittest
from types import SimpleNamespace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from herdr_roster import inspect_pane_compose_draft as inspect, check_pane_compose_draft as check, compose_check_exit_code

class DraftGuardTests(unittest.TestCase):
    def test_bounded_empty(self):
        r=inspect("────\n❯ \n────\n  [Opus] | 12%")
        self.assertEqual(r["compose_state"],"empty")
        self.assertFalse(r["atomic_send_protection"])
    def test_question_draft(self):
        self.assertEqual(inspect("────\n❯ ？\n────")["compose_state"],"draft")
    def test_multiline_after(self):
        self.assertEqual(inspect("────\n❯ \n  尚未提交\n────")["compose_state"],"draft")
    def test_multiline_before(self):
        self.assertEqual(inspect("────\n  尚未提交\n❯ \n────")["compose_state"],"draft")
    def test_missing_prompt_unknown(self):
        r=inspect("connection error\nreconnecting")
        self.assertEqual(r["compose_state"],"unknown")
        self.assertIsNone(r["draft_present"])
    def test_blank_multiline_and_stale_box_are_unknown(self):
        for screen in ("────\n❯ \n\n────", "────\n❯ \n────\nnew tool output",
                       "────\n❯ \n────\n  connection lost"):
            with self.subTest(screen=screen):
                self.assertEqual(inspect(screen)["compose_state"], "unknown")
    def test_missing_border_unknown(self):
        for text in ("❯ ","────\n❯ ","❯ \n────"):
            with self.subTest(text=text): self.assertEqual(inspect(text)["compose_state"],"unknown")
    def test_multiple_prompts_unknown(self):
        self.assertEqual(inspect("────\n❯ old\n────\n❯ \n────")["compose_state"],"unknown")
    def test_ansi_colours(self):
        self.assertEqual(inspect("────\n\x1b[32m› \x1b[0m\n────")["compose_state"],"empty")
    def test_controls_unknown(self):
        self.assertEqual(inspect("────\n❯ \x1b]0;title\x07\n────")["compose_state"],"unknown")
    def test_cursor_controls_unknown(self):
        for control in ("\x1b[2J", "\x1b[1A", "\rX"):
            self.assertEqual(inspect("────\n❯ " + control + "\n────")["compose_state"],"unknown")
    def test_hints_not_assumed_empty(self):
        self.assertEqual(inspect("────\n› Explain this code\n────")["compose_state"],"draft")
    def test_output_omits_draft(self):
        r=inspect("────\n❯ secret-example-only\n────")
        self.assertNotIn("secret-example-only",json.dumps(r))
        self.assertNotIn("draft_preview",r)
        self.assertNotIn("prompt_line",r)
    def test_exit_codes(self):
        self.assertEqual([compose_check_exit_code({"compose_state":s}) for s in ("empty","draft","unknown","other")],[0,2,3,3])
    def test_typed_separator_does_not_end_composer(self):
        for screen in (
            "  ────\n  ❯ \n  ----\n  unsubmitted draft\n  ────\n  [Opus] | 12%",
            "────\n❯ \n  ----\n  unsubmitted draft",
            "────\n❯ \n────\n  unsubmitted draft\n────",
            "────\n❯ \n─────\n  [Opus] | 12%",
        ):
            with self.subTest(screen=screen):
                self.assertEqual(inspect(screen)["compose_state"], "unknown")
    def test_transport_timeout_unknown_private(self):
        def runner(*a,**kw): raise subprocess.TimeoutExpired("private",8,output="SECRET")
        r=check("herdr","named","w1:pC",runner)
        self.assertEqual(r["compose_state"],"unknown")
        self.assertNotIn("SECRET",json.dumps(r))
    def test_transport_failure_unknown_private(self):
        r=check("herdr","named","w1:pC",lambda *a,**kw:SimpleNamespace(returncode=1,stdout="SECRET",stderr="SECRET"))
        self.assertEqual(r["compose_state"],"unknown")
        self.assertNotIn("SECRET",json.dumps(r))
    def test_schema_errors(self):
        for raw in ('bad','[]','{}','{"result":{"content":{}}}'):
            with self.subTest(raw=raw):
                r=check("herdr","named","w1:pC",lambda *a,**kw:SimpleNamespace(returncode=0,stdout=raw))
                self.assertEqual(r["compose_state"],"unknown")
    def test_visible_pane_read_returns_plain_text(self):
        calls=[]
        def runner(argv,**kw):
            calls.append(argv)
            return SimpleNamespace(returncode=0,stdout="────\n❯ \n────\n  [Opus] | 12%")
        r=check("herdr","named","w1:pC",runner)
        self.assertEqual(r["compose_state"],"empty")
        self.assertEqual(calls,[["herdr","--session","named","pane","read","w1:pC","--source","visible","--format","text"]])
        self.assertFalse(r["input_sent"])

if __name__ == "__main__": unittest.main()
