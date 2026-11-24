import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from herdr_roster import (
    RosterError,
    apply_roster_manifest,
    collect_session_snapshot,
    derive_spatial_codes,
    format_pane_label,
    infer_process_kind,
    sanitize_agent_code,
)


class FakeRunner:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        key = tuple(argv[3:])  # strip [bin, '--session', session]
        if key in self.responses:
            payload = self.responses[key]
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
        if argv[3:5] == ["tab", "rename"] or argv[3:5] == ["pane", "rename"] or argv[3:5] == ["agent", "rename"]:
            return subprocess.CompletedProcess(argv, 0, '{"ok":true}', "")
        raise AssertionError(f"Unexpected command: {argv}")


def sample_responses():
    return {
        ("status", "--json"): {
            "client": {"version": "0.9.5", "session": "c4-multiagent"},
            "server": {
                "running": True,
                "status": "running",
                "compatible": True,
                "endpoint_compatible": True,
                "session": "c4-multiagent",
                "socket": "/tmp/herdr-c4.sock",
                "version": "0.9.5",
            },
        },
        ("tab", "list"): {
            "result": {
                "tabs": [
                    {"tab_id": "w1:t1", "number": 1, "label": "1", "pane_count": 2, "agent_status": "working"},
                    {"tab_id": "w1:t6", "number": 6, "label": "2", "pane_count": 4, "agent_status": "working"},
                ]
            }
        },
        ("pane", "list"): {
            "result": {
                "panes": [
                    {
                        "pane_id": "w1:p1",
                        "tab_id": "w1:t1",
                        "workspace_id": "w1",
                        "terminal_id": "term_1",
                        "agent": "codex",
                        "agent_status": "idle",
                        "label": None,
                        "terminal_title_stripped": "Implement plan | cnm",
                    },
                    {
                        "pane_id": "w1:p3",
                        "tab_id": "w1:t1",
                        "workspace_id": "w1",
                        "terminal_id": "term_3",
                        "agent": None,
                        "agent_status": "unknown",
                        "label": None,
                    },
                    {
                        "pane_id": "w1:pB",
                        "tab_id": "w1:t6",
                        "workspace_id": "w1",
                        "terminal_id": "term_B",
                        "agent": "codex",
                        "agent_status": "idle",
                        "agent_session": {"kind": "id", "value": "01a1d601-2f37-7b12-90b2-1565d3db5e59"},
                    },
                    {
                        "pane_id": "w1:pD",
                        "tab_id": "w1:t6",
                        "workspace_id": "w1",
                        "terminal_id": "term_D",
                        "agent": "codex",
                        "agent_status": "idle",
                        "agent_session": {"kind": "id", "value": "01a1d601-df24-70b2-ab0d-971ccd9f5e9d"},
                    },
                    {
                        "pane_id": "w1:pC",
                        "tab_id": "w1:t6",
                        "workspace_id": "w1",
                        "terminal_id": "term_C",
                        "agent": "claude",
                        "agent_status": "idle",
                        "agent_session": {"kind": "id", "value": "42977dde-eb08-4900-aad1-1d20db13ff17"},
                    },
                    {
                        "pane_id": "w1:pE",
                        "tab_id": "w1:t6",
                        "workspace_id": "w1",
                        "terminal_id": "term_E",
                        "agent": "agy",
                        "agent_status": "working",
                        "agent_session": {"kind": "id", "value": "a719d11f-93a4-4729-aef2-b247af11748e"},
                    },
                ]
            }
        },
        ("agent", "list"): {
            "result": {
                "agents": [
                    {"pane_id": "w1:p1", "agent": "codex", "agent_status": "idle"},
                    {"pane_id": "w1:pB", "agent": "codex", "agent_status": "idle"},
                    {"pane_id": "w1:pD", "agent": "codex", "agent_status": "idle"},
                    {"pane_id": "w1:pC", "agent": "claude", "agent_status": "idle"},
                    {"pane_id": "w1:pE", "agent": "agy", "agent_status": "working"},
                ]
            }
        },
        ("pane", "layout", "--pane", "w1:p1"): {
            "result": {
                "layout": {
                    "area": {"x": 0, "y": 0, "width": 200, "height": 100},
                    "panes": [
                        {"pane_id": "w1:p1", "rect": {"x": 0, "y": 0, "width": 100, "height": 100}},
                        {"pane_id": "w1:p3", "rect": {"x": 100, "y": 0, "width": 100, "height": 100}},
                    ],
                }
            }
        },
        ("pane", "layout", "--pane", "w1:pB"): {
            "result": {
                "layout": {
                    "area": {"x": 0, "y": 0, "width": 297, "height": 128},
                    "panes": [
                        {"pane_id": "w1:pB", "rect": {"x": 0, "y": 0, "width": 149, "height": 64}},
                        {"pane_id": "w1:pD", "rect": {"x": 0, "y": 64, "width": 149, "height": 64}},
                        {"pane_id": "w1:pC", "rect": {"x": 149, "y": 0, "width": 148, "height": 64}},
                        {"pane_id": "w1:pE", "rect": {"x": 149, "y": 64, "width": 148, "height": 64}},
                    ],
                }
            }
        },
        ("pane", "process-info", "--pane", "w1:p3"): {
            "result": {
                "process_info": {
                    "pane_id": "w1:p3",
                    "foreground_processes": [
                        {
                            "pid": 93938,
                            "name": "codex-aarch64-apple-darwin",
                            "argv0": "/Users/test/codex-aarch64-apple-darwin",
                            "argv": ["/Users/test/codex-aarch64-apple-darwin", "resume"],
                        }
                    ],
                }
            }
        },
    }


class HerdrRosterTests(unittest.TestCase):
    def test_spatial_codes_cover_quad_and_seven_pane_stata_grid(self):
        quad = derive_spatial_codes(
            {"x": 0, "y": 0, "width": 297, "height": 128},
            [
                {"pane_id": "pB", "rect": {"x": 0, "y": 0, "width": 149, "height": 64}},
                {"pane_id": "pD", "rect": {"x": 0, "y": 64, "width": 149, "height": 64}},
                {"pane_id": "pC", "rect": {"x": 149, "y": 0, "width": 148, "height": 64}},
                {"pane_id": "pE", "rect": {"x": 149, "y": 64, "width": 148, "height": 64}},
            ],
        )
        self.assertEqual(quad, {"pB": "LU", "pD": "LD", "pC": "RU", "pE": "RD"})

        stata_grid = derive_spatial_codes(
            {"x": 0, "y": 0, "width": 297, "height": 119},
            [
                {"pane_id": "p1", "rect": {"x": 0, "y": 0, "width": 112, "height": 60}},
                {"pane_id": "p4", "rect": {"x": 0, "y": 60, "width": 112, "height": 59}},
                {"pane_id": "p2", "rect": {"x": 112, "y": 0, "width": 93, "height": 60}},
                {"pane_id": "p5", "rect": {"x": 112, "y": 60, "width": 93, "height": 26}},
                {"pane_id": "p8", "rect": {"x": 112, "y": 86, "width": 93, "height": 33}},
                {"pane_id": "p3", "rect": {"x": 205, "y": 0, "width": 92, "height": 60}},
                {"pane_id": "p6", "rect": {"x": 205, "y": 60, "width": 92, "height": 59}},
            ],
        )
        self.assertEqual(
            stata_grid,
            {"p1": "LU", "p4": "LD", "p2": "CU", "p5": "CM", "p8": "CD", "p3": "RU", "p6": "RD"},
        )

    def test_wrapper_codex_and_idle_shell_inference(self):
        wrapped = infer_process_kind(
            {
                "foreground_processes": [
                    {"name": "python3.14", "argv0": "python3.14", "argv": ["python3.14", "launch.py"]},
                    {"name": "codex-aarch64-apple-darwin", "argv0": "/bin/codex-aarch64-apple-darwin"},
                ]
            }
        )
        self.assertEqual(wrapped["inferred_kind"], "codex")
        self.assertFalse(wrapped["is_shell_only"])

        shell = infer_process_kind({"foreground_processes": [{"name": "zsh", "argv0": "-zsh", "argv": ["-zsh"]}]})
        self.assertIsNone(shell["inferred_kind"])
        self.assertTrue(shell["is_shell_only"])

    def test_snapshot_and_apply_never_send_input_and_enforce_pins(self):
        runner = FakeRunner(sample_responses())
        snap = collect_session_snapshot("c4-multiagent", herdr_bin="/mock/herdr", runner=runner)
        self.assertFalse(snap["input_sent"])
        by_id = {p["pane_id"]: p for p in snap["panes"]}
        self.assertEqual(by_id["w1:p3"]["effective_kind"], "codex")
        self.assertEqual(by_id["w1:pB"]["spatial"], "LU")
        self.assertEqual(by_id["w1:pE"]["tab_index"], 2)

        manifest = {
            "schema": "herdr-roster-manifest-v1",
            "sessions": [
                {
                    "session": "c4-multiagent",
                    "expect_socket": "/tmp/herdr-c4.sock",
                    "tabs": [
                        {"tab_id": "w1:t1", "label": "T1-主控双审"},
                        {"tab_id": "w1:t6", "label": "T2-升级规划"},
                    ],
                    "panes": [
                        {
                            "pane_id": "w1:pB",
                            "expect_terminal_id": "term_B",
                            "expect_native_session_id": "01a1d601-2f37-7b12-90b2-1565d3db5e59",
                            "role_tag": "SUP-CX(S40)",
                            "duty": "MAC维护",
                            "agent_name": "t2-sup-s40",
                        },
                        {
                            "pane_id": "w1:p3",
                            "expect_terminal_id": "term_3",
                            "role_tag": "EXE-CX",
                            "duty": "包装器终端",
                            "agent_name": "t1-exe-cx",
                        },
                    ],
                }
            ],
        }
        dry = apply_roster_manifest(manifest, dry_run=True, herdr_bin="/mock/herdr", runner=runner)
        self.assertTrue(dry["dry_run"])
        self.assertEqual(dry["executed_command_count"], 0)
        # w1:p3 is omitted from Herdr agent list, so agent rename is skipped while pane rename runs
        self.assertEqual(dry["planned_command_count"], 5)

        live = apply_roster_manifest(manifest, dry_run=False, herdr_bin="/mock/herdr", runner=runner)
        self.assertEqual(live["executed_command_count"], 5)
        self.assertFalse(live["input_sent"])
        for call in runner.calls:
            self.assertNotIn("send-text", call)
            self.assertNotIn("send-keys", call)
            self.assertNotIn("prompt", call)

        bad_manifest = json.loads(json.dumps(manifest))
        bad_manifest["sessions"][0]["panes"][0]["expect_terminal_id"] = "term_WRONG"
        with self.assertRaises(RosterError) as ctx:
            apply_roster_manifest(bad_manifest, dry_run=False, herdr_bin="/mock/herdr", runner=runner)
        self.assertEqual(ctx.exception.code, "TERMINAL_MISMATCH")

    def test_format_pane_label_truncates_cleanly(self):
        lbl = format_pane_label(
            tab_index=2,
            spatial_code="LU",
            pane_id="w1:pB",
            role_tag="SUP-CX(S40)",
            duty="超长职责说明用于验证自动截断不破坏前缀可读性",
            max_chars=30,
        )
        self.assertTrue(lbl.startswith("[T2·LU|pB] SUP-CX(S40)"))
        self.assertLessEqual(len(lbl), 30)
        self.assertEqual(sanitize_agent_code("t2-lane-a-cc"), "t2-lane-a-cc")
        for bad in ("t2-laneA-cc", "1start-digit", "has.dot"):
            with self.assertRaises(RosterError):
                sanitize_agent_code(bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)
