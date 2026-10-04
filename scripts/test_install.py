import json
from pathlib import Path
import tempfile
import unittest
import manage_install as m
import cmux_bridge
import mac_harness


class InstallationTests(unittest.TestCase):
    def test_skill_path_is_portable_and_shared(self):
        self.assertEqual(cmux_bridge.COLLABORATION_SKILL_PATH, m.ROOT / "SKILL.md")
        self.assertEqual(mac_harness.COLLABORATION_SKILL_PATH, cmux_bridge.COLLABORATION_SKILL_PATH)

    def test_merge_is_idempotent_preserves_other_settings(self):
        old = {"custom": {"secret": "dummy"}, "hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": "foreign-hook"}]}]}}
        new = m.transform(old)
        self.assertEqual(m.transform(new), new)
        self.assertEqual(old["custom"], new["custom"])
        removed = m.transform(new, True)
        self.assertEqual(removed["custom"], old["custom"])
        self.assertEqual(removed["hooks"]["Stop"], old["hooks"]["Stop"])

    def test_sandbox_install_doctor_uninstall(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            self.assertTrue(m.manage(home, "install")["ok"])
            self.assertEqual(list(home.iterdir()), [])
            m.manage(home, "install", True)
            self.assertTrue(m.manage(home, "doctor")["ok"])
            m.manage(home, "install", True)
            self.assertEqual(list(home.rglob("*.multi-agent-backup-*")), [])
            cfg = home / ".claude/settings.json"
            doc = json.loads(cfg.read_bytes())
            doc["keep"] = 123
            cfg.write_text(json.dumps(doc))
            m.manage(home, "uninstall", True)
            self.assertEqual(json.loads(cfg.read_bytes())["keep"], 123)
            self.assertFalse((home / ".claude/skills/multi-agent-collaboration").exists())
            self.assertEqual(len(list(home.rglob("*.multi-agent-backup-*"))), 2)
            self.assertFalse(m.manage(home, "doctor")["ok"])

    def test_foreign_install_and_concurrent_edit_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            foreign = root / ".claude/skills/multi-agent-collaboration"
            foreign.mkdir(parents=True)
            with self.assertRaisesRegex(RuntimeError, "foreign"):
                m.manage(root, "install", True)
            self.assertFalse((root / ".codex").exists())
            config = root / "test.json"
            config.write_text('{"new":1}')
            with self.assertRaisesRegex(RuntimeError, "concurrently"):
                m.replace(config, b"{}", {})
            self.assertEqual(json.loads(config.read_bytes()), {"new": 1})

    def test_unwired_doctor_rejects(self):
        with tempfile.TemporaryDirectory() as td:
            m.manage(td, "install", True)
            cfg = Path(td) / ".codex/hooks.json"
            cfg.write_text('{"hooks":{}}')
            self.assertFalse(m.manage(td, "doctor")["ok"])

    def test_post_submit_hook_both_clients_and_missing_registration(self):
        with tempfile.TemporaryDirectory() as td:
            m.manage(td, "install", True)
            for client, filename in [('codex', 'hooks.json'), ('claude', 'settings.json')]:
                cfg = Path(td) / ('.' + client) / filename
                original = cfg.read_bytes()
                doc = json.loads(original)
                commands = [h['command'] for entry in doc['hooks']['PostToolUse']
                            for h in entry['hooks']]
                self.assertEqual(commands.count(m.command('cmux_submit_confirmation_guard')), 1)
                self.assertEqual(mac_harness._wired_guards(cfg)['cmux_submit_confirmation_guard'], {'PostToolUse'})
                doc['hooks']['PostToolUse'] = []
                cfg.write_text(json.dumps(doc))
                self.assertFalse(m.manage(td, 'doctor')['ok'])
                cfg.write_bytes(original)
            m.manage(td, 'uninstall', True)
            for client, filename in [('codex', 'hooks.json'), ('claude', 'settings.json')]:
                cfg = Path(td) / ('.' + client) / filename
                self.assertNotIn('cmux_submit_confirmation_guard', mac_harness._wired_guards(cfg))
