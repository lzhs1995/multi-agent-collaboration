import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import manage_install as m
import cmux_bridge
import mac_harness
import offline_test_hook


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='install-hook-fixture-')
        self.addCleanup(self.tmp.cleanup)
        self.active = Path(self.tmp.name) / 'active'
        self.active.mkdir()
        self.env = dict(os.environ, CMUX_WORKSPACE_ID='offline-doctor',
                        CMUX_SURFACE_ID='offline-doctor-surface')

        def run_hook(argv, **kwargs):
            # Manage's generated command and every hook's actual main remain
            # under test. Only its subprocess receives private process/marker
            # inputs; returning the real CompletedProcess preserves failures.
            self.assertEqual(argv[:2], [sys.executable, '-B'])
            self.assertEqual(len(argv), 3)
            self.assertIn(Path(argv[2]).stem, m.GUARDS)
            kwargs.setdefault('env', self.env)
            return subprocess.run(offline_test_hook.command(argv[2], self.active), **kwargs)

        runner = patch.object(m, 'subprocess', SimpleNamespace(run=run_hook))
        runner.start()
        self.addCleanup(runner.stop)

    def test_skill_path_is_portable_and_shared(self):
        self.assertEqual(cmux_bridge.COLLABORATION_SKILL_PATH, m.ROOT / "SKILL.md")
        self.assertEqual(mac_harness.COLLABORATION_SKILL_PATH, cmux_bridge.COLLABORATION_SKILL_PATH)
        self.assertEqual(mac_harness.REQUIRED_GUARD_WIRING, m.GUARDS)

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
            # Two config backups plus both retired skill links are preserved.
            self.assertEqual(len(list(home.rglob("*.multi-agent-backup-*"))), 4)
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

    def test_doctor_preserves_real_stop_guard_failure(self):
        with tempfile.TemporaryDirectory() as td:
            m.manage(td, 'install', True)
            root = Path(self.tmp.name) / 'task'
            root.mkdir()
            (root / 'task-pack.json').write_text(json.dumps(dict(
                draft=False, completion_receipt=str(root / 'missing-receipt.json'))))
            (self.active / 'offline-doctor.json').write_text(json.dumps(dict(
                task_id='offline-doctor-task', artifact_root=str(root),
                participants=[dict(role='executor', surface_uuid='offline-doctor-surface')])))
            result = m.manage(td, 'doctor')
            self.assertFalse(result['ok'])
            self.assertFalse(result['checks']['cmux_consensus_stop_guard']['benignExitZero'])

    def test_post_submit_hook_both_clients_and_missing_registration(self):
        with tempfile.TemporaryDirectory() as td:
            m.manage(td, "install", True)
            for client, filename in [('codex', 'hooks.json'), ('claude', 'settings.json')]:
                cfg = Path(td) / ('.' + client) / filename
                original = cfg.read_bytes()
                doc = json.loads(original)
                commands = [h['command'] for entry in doc['hooks']['PostToolUse']
                            for h in entry['hooks']]
                self.assertEqual(commands.count(m.command('cmux_native_delivery_guard')), 1)
                self.assertEqual(mac_harness._wired_guards(cfg)['cmux_native_delivery_guard'], {'PostToolUse'})
                doc['hooks']['PostToolUse'] = []
                cfg.write_text(json.dumps(doc))
                self.assertFalse(m.manage(td, 'doctor')['ok'])
                cfg.write_bytes(original)
            m.manage(td, 'uninstall', True)
            for client, filename in [('codex', 'hooks.json'), ('claude', 'settings.json')]:
                cfg = Path(td) / ('.' + client) / filename
                self.assertNotIn('cmux_native_delivery_guard', mac_harness._wired_guards(cfg))

    def test_owned_historical_hooks_retired_foreign_names_preserved(self):
        home = Path(self.tmp.name)
        release = home / '.local/share/multi-agent-collaboration/releases/old/source/scripts'
        foreign = '/opt/foreign/scripts/cmux_send_proof_stop_guard.py'
        original = {'keep': {'value': 7}, 'hooks': {'Stop': [{'matcher': 'custom', 'hooks': [
            {'type': 'command', 'command': f'python3 {release}/cmux_send_proof_stop_guard.py'},
            {'type': 'command', 'command': f'python3 -B {release}/cmux_executor_idle_guard.py'},
            {'type': 'command', 'command': f'python3 {foreign}'},
        ]}], 'PostToolUse': [{'hooks': [
            {'type': 'command', 'command': f'python3 -B {release}/cmux_native_delivery_guard.py'},
            {'type': 'command', 'command': f'python3 {release}/cmux_submit_confirmation_guard.py'},
        ]}]}}
        migrated = m.transform(original, home=home)
        self.assertEqual(migrated['keep'], original['keep'])
        self.assertEqual(migrated['hooks']['Stop'][0], {'matcher': 'custom', 'hooks': [
            {'type': 'command', 'command': f'python3 {foreign}'}]})
        commands = [h['command'] for entries in migrated['hooks'].values()
                    for entry in entries for h in entry['hooks']]
        self.assertFalse(any(str(release) in command for command in commands))
        for guard in m.GUARDS:
            self.assertEqual(commands.count(m.command(guard)), 1)
        stop_commands = [h['command'] for entry in migrated['hooks']['Stop'] for h in entry['hooks']]
        self.assertEqual(stop_commands.count(m.command('cmux_executor_idle_guard')), 1)
        self.assertEqual(m.transform(migrated, home=home), migrated)
        self.assertEqual(m.transform(migrated, True, home=home)['hooks']['Stop'],
                         [migrated['hooks']['Stop'][0]])

    def test_wrapper_migration_preserves_all_resources(self):
        home = Path(self.tmp.name).resolve() / 'wrapper-home'
        release = home / '.local/share/multi-agent-collaboration/releases/old/source'
        release.mkdir(parents=True)
        (release / 'SKILL.md').write_text('old immutable skill\n')
        wrappers = []
        for client in ('codex', 'claude'):
            wrapper = home / ('.' + client) / 'skills/multi-agent-collaboration'
            (wrapper / 'references').mkdir(parents=True)
            (wrapper / 'SKILL.md').write_text(
                f'---\nname: multi-agent-collaboration\n---\n[skill]({release}/SKILL.md)\n')
            (wrapper / 'references/local.md').write_bytes(b'preserve every byte\x00\n')
            (wrapper / 'references/original').symlink_to('local.md')
            wrappers.append((wrapper, m.skill_snapshot(wrapper)))
        m.manage(home, 'install', True)
        for wrapper, snapshot in wrappers:
            self.assertTrue(wrapper.is_symlink())
            self.assertEqual(wrapper.resolve(), m.ROOT)
            backups = list(wrapper.parent.glob(wrapper.name + '.multi-agent-backup-*'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(m.skill_snapshot(backups[0]), snapshot)
        self.assertEqual((release / 'SKILL.md').read_text(), 'old immutable skill\n')

    def test_config_drift_aborts_before_any_client_is_changed(self):
        home = Path(self.tmp.name).resolve() / 'drift-home'
        codex = home / '.codex/hooks.json'
        claude = home / '.claude/settings.json'
        for path in (codex, claude):
            path.parent.mkdir(parents=True)
            path.write_text('{"original":true}')
        real_read = m.read
        changed = False

        def concurrent_read(path):
            nonlocal changed
            result = real_read(path)
            if path == claude and not changed:
                changed = True
                path.write_text('{"concurrent":true}')
            return result

        with patch.object(m, 'read', side_effect=concurrent_read):
            with self.assertRaisesRegex(RuntimeError, 'concurrently'):
                m.manage(home, 'install', True)
        self.assertEqual(json.loads(codex.read_text()), {'original': True})
        self.assertEqual(json.loads(claude.read_text()), {'concurrent': True})
        self.assertEqual(list(home.rglob('multi-agent-collaboration')), [])

    def test_skill_resource_drift_preserved(self):
        wrapper = Path(self.tmp.name) / 'wrapper'
        wrapper.mkdir()
        (wrapper / 'SKILL.md').write_text('old')
        snapshot = m.skill_snapshot(wrapper)
        (wrapper / 'new-reference.md').write_text('concurrent work')
        with self.assertRaisesRegex(RuntimeError, 'concurrently'):
            m.replace_skill(wrapper, snapshot)
        self.assertFalse(wrapper.is_symlink())
        self.assertEqual((wrapper / 'new-reference.md').read_text(), 'concurrent work')
        self.assertEqual(list(wrapper.parent.glob('wrapper.multi-agent-backup-*')), [])

    def test_install_from_immutable_source_pins_commands_and_links(self):
        home = Path(self.tmp.name).resolve() / 'immutable-home'
        release = home / '.local/share/multi-agent-collaboration/releases/new/source'
        release.mkdir(parents=True)
        (release / 'SKILL.md').write_text('current immutable skill')
        with patch.object(m, 'ROOT', release):
            m.manage(home, 'install', True)
            for client, filename in (('codex', 'hooks.json'), ('claude', 'settings.json')):
                link = home / ('.' + client) / 'skills/multi-agent-collaboration'
                self.assertEqual(link.resolve(), release)
                doc = json.loads((home / ('.' + client) / filename).read_text())
                commands = [hook['command'] for entries in doc['hooks'].values()
                            for entry in entries for hook in entry['hooks']]
                self.assertEqual(set(commands), {m.command(guard) for guard in m.GUARDS})
