import unittest
import cmux_bridge as b

TEXT = "DONE|task-example|nonce-example|REPORT=/archive/review/report.md"
SCREEN = "\n".join(["• Working (1m • esc to interrupt)", " ", "› " + TEXT,
                      " ", " ", "  GPT-6-Astra xhigh · ~/project · Context 31% used",
                      "  tab to queue message"])
class CodexSpaceGap(unittest.TestCase):
    def test_actual_full_payload(self):
        self.assertTrue(b._codex_extra_enter_allowed(SCREEN, TEXT))
    def test_changed_payload_rejected(self):
        self.assertFalse(b._codex_extra_enter_allowed(SCREEN, TEXT + "x"))
    def test_extra_draft_rejected(self):
        self.assertFalse(b._codex_extra_enter_allowed(SCREEN.replace("\n \n \n  GPT", "\n injected\n \n  GPT"), TEXT))
    def test_unknown_footer_rejected(self):
        self.assertFalse(b._codex_extra_enter_allowed(SCREEN.replace("GPT-6-Astra", "UNKNOWN"), TEXT))
    def test_unmeasured_space_gap_without_hint_rejected(self):
        self.assertFalse(b._codex_extra_enter_allowed(SCREEN.replace("tab to queue message", ""), TEXT))
    def test_simple_exact_composer_does_not_require_tab_hint(self):
        screen = '• Working (3s • esc to interrupt)\n› ' + TEXT + '\nGPT-6 high'
        self.assertTrue(b._codex_extra_enter_allowed(screen, TEXT))
    def test_three_gap_rows_rejected(self):
        self.assertFalse(b._codex_extra_enter_allowed(SCREEN.replace("\n \n \n  GPT", "\n \n \n \n  GPT"), TEXT))
