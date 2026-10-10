"""Provider switch/import repair without credentials, foreign hooks or selection drift."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

import configuration_guardian as g
import manage_install as m


class GuardianTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve()
        self.config = self.home / '.claude/settings.json'
        self.doc = {'env': {'API_KEY': 'fixture-secret', 'BASE_URL': 'fixture-url'},
                    'model': 'fixture-model', 'permissions': {'allow': ['Read']},
                    'hooks': {'Stop': [{'matcher': 'keep', 'hooks': [
                        {'type': 'command', 'command': 'foreign-fixture-hook'}]}]}}

    def write(self, path, doc):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc))

    def db(self):
        path = self.home / '.cc-switch/cc-switch.db'
        path.parent.mkdir(parents=True)
        db = sqlite3.connect(path)
        db.execute('CREATE TABLE providers (id TEXT, app_type TEXT, settings_config TEXT, is_current INTEGER)')
        db.execute('CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)')
        db.execute('INSERT INTO providers VALUES (?,?,?,?)', ('a', 'claude', json.dumps(self.doc), 1))
        db.execute('INSERT INTO providers VALUES (?,?,?,?)', ('c', 'codex', '{"keep":"codex"}', 1))
        db.commit()
        return db

    def test_switch_erasing_live_hooks_is_repaired_idempotently(self):
        self.write(self.config, self.doc)
        self.assertTrue(g.reconcile_files(self.home, m, False)[0]['change_needed'])
        self.assertEqual(json.loads(self.config.read_text()), self.doc)
        for _ in range(3):
            self.write(self.config, self.doc)  # cc-switch replaced settings
            g.reconcile_files(self.home, m, True)
            result = json.loads(self.config.read_text())
            self.assertEqual(result['env'], self.doc['env'])
            self.assertEqual(g.foreign_hooks(result, m, self.home), self.doc['hooks'])
            commands = [h['command'] for group in result['hooks'].values() for entry in group for h in entry['hooks']]
            self.assertEqual(commands.count(m.command('cmux_executor_reask_stop_guard')), 1)
            self.assertFalse(any(x['change_needed'] for x in g.reconcile_files(self.home, m, True)))
        snapshots = list(g.state_root(self.home).rglob('*previous-hooks.json'))
        self.assertEqual(len(snapshots), 2)
        self.assertNotIn('fixture-secret', ''.join(p.read_text() for p in snapshots))

    def test_mixed_gate_and_advisory_profiles_upgrade_without_foreign_hook_loss(self):
        release = self.home / '.local/share/multi-agent-collaboration/releases/old/source/scripts'
        hook = 'cmux_consensus_stop_guard.py'
        owned = [f'python3 -B {release / hook}',
                 f'python3 -B {release}/cmux_workflow_advisory.py --hook {hook}']
        foreign = f'python3 /opt/foreign/scripts/cmux_workflow_advisory.py --hook {hook}'
        self.doc['hooks']['Stop'][0]['hooks'].extend(
            {'type': 'command', 'command': command} for command in owned + [foreign])
        expected_foreign = g.foreign_hooks(self.doc, m, self.home)
        db = self.db()
        self.write(self.config, self.doc)
        g.reconcile_files(self.home, m, True)
        self.assertEqual(g.reconcile_profiles(self.home, m, True)['changed'], 1)
        documents = [json.loads(self.config.read_text()), json.loads(
            db.execute("SELECT settings_config FROM providers WHERE id='a'").fetchone()[0])]
        for document in documents:
            self.assertEqual(g.foreign_hooks(document, m, self.home), expected_foreign)
            commands = [row['command'] for entries in document['hooks'].values()
                        for entry in entries for row in entry['hooks']]
            self.assertIn(foreign, commands)
            self.assertFalse(set(owned) & set(commands))
            for name in dict(m.GUARDS, **m.OPTIONAL_GUARDS):
                self.assertEqual(commands.count(m.command(name)), 1)
            self.assertEqual(document['env'], self.doc['env'])
        self.assertEqual(g.reconcile_profiles(self.home, m, True)['changed'], 0)
        self.assertFalse(any(row['change_needed'] for row in g.reconcile_files(self.home, m, True)))
        db.close()

    def test_imported_profiles_and_common_snippet_preserve_credentials_selection_and_foreign_hooks(self):
        db = self.db()
        common = m.transform({'env': {'COMMON': 'keep'}, 'hooks': self.doc['hooks']}, executor_reask=True)
        db.execute('INSERT INTO settings VALUES (?,?)', ('common_config_claude', json.dumps(common)))
        db.commit()
        g.reconcile_profiles(self.home, m, True)
        db.execute('INSERT INTO providers VALUES (?,?,?,?)', ('b', 'claude', json.dumps(self.doc), 0))
        db.commit()
        r = g.reconcile_profiles(self.home, m, True)
        self.assertEqual((r['profiles'], r['changed']), (2, 1))
        rows = db.execute('SELECT id, settings_config, is_current FROM providers ORDER BY id').fetchall()
        self.assertEqual([r[2] for r in rows], [1, 0, 1])
        self.assertEqual(rows[-1][1], '{"keep":"codex"}')
        for _, raw, _ in rows[:2]:
            parsed = json.loads(raw)
            self.assertEqual(parsed['env'], self.doc['env'])
            self.assertEqual(g.foreign_hooks(parsed, m, self.home), self.doc['hooks'])
        snippet = json.loads(db.execute('SELECT value FROM settings').fetchone()[0])
        self.assertEqual(snippet, {'env': {'COMMON': 'keep'}, 'hooks': self.doc['hooks']})
        self.assertEqual(g.reconcile_profiles(self.home, m, True)['changed'], 0)
        db.close()

    def test_bad_profile_rolls_back_without_logging_credentials(self):
        db = self.db()
        db.execute('INSERT INTO providers VALUES (?,?,?,?)', ('z', 'claude', 'broken secret', 0))
        db.commit()
        with self.assertRaises(ValueError):
            g.reconcile_profiles(self.home, m, True)
        self.assertEqual(json.loads(db.execute("SELECT settings_config FROM providers WHERE id='a'").fetchone()[0]), self.doc)
        db.close()

    def test_cas_and_symlink_refusal_preserve_concurrent_config(self):
        self.write(self.config, self.doc)
        with self.assertRaisesRegex(RuntimeError, 'concurrently'):
            g.atomic_json(self.config, {}, b'{}', compare=True)
        before = self.config.read_bytes()
        with patch.object(g.tempfile, 'mkstemp', wraps=g.tempfile.mkstemp) as make:
            original = g.os.fsync
            def concurrent(fd):
                original(fd)
                self.config.write_text('{"new_provider":"keep"}')
            with patch.object(g.os, 'fsync', side_effect=concurrent):
                with self.assertRaises(RuntimeError):
                    g.atomic_json(self.config, {}, before, compare=True)
            self.assertEqual(make.call_count, 1)
        self.assertEqual(json.loads(self.config.read_text()), {'new_provider': 'keep'})
        link = self.home / 'link.json'
        link.symlink_to(self.config)
        with self.assertRaises(ValueError):
            g.atomic_json(link, {})
        self.assertFalse(list(self.config.parent.glob('.guardian-*')))

    def test_one_launchd_job_watches_switches_and_has_no_terminal_arguments(self):
        spec = g.launchd_spec(self.home, '/fixture/source', sys.executable, '/fixture/CURRENT.json')
        self.assertEqual(spec['StartInterval'], 60)
        self.assertEqual(spec['Label'], g.LABEL)
        self.assertIn(str(self.home / '.cc-switch'), spec['WatchPaths'])
        self.assertIn('reconcile', spec['ProgramArguments'])
        self.assertNotIn('send', spec['ProgramArguments'])

    def test_disable_all_hooks_is_visible_and_never_silently_changed(self):
        doc = dict(self.doc, disableAllHooks=True)
        self.write(self.config, doc)
        self.assertTrue(g.reconcile_files(self.home, m, True)[0]['hooks_disabled'])
        self.assertTrue(json.loads(self.config.read_text())['disableAllHooks'])

    def test_manifest_verifies_immutable_members_and_running_version(self):
        source = self.home / '.local/share/multi-agent-collaboration/releases/fixture/source'
        (source / 'scripts').mkdir(parents=True)
        files = {}
        for name in ('configuration_guardian.py', 'manage_install.py'):
            path = source / 'scripts' / name
            raw = (Path(g.__file__).parent / name).read_bytes()
            path.write_bytes(raw)
            files['scripts/' + name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
            path.chmod(0o444)
        manifest = source.parent / 'MANIFEST.json'
        self.write(manifest, {'files': files})
        current = self.home / 'CURRENT.json'
        self.write(current, {'source': str(source), 'python': sys.executable,
                             'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()})
        with patch.object(g, '__file__', str(source / 'scripts/configuration_guardian.py')):
            g.load_release(self.home, current)
            member = source / 'scripts/manage_install.py'
            member.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'writable'):
                g.load_release(self.home, current)
            member.write_text('changed')
            member.chmod(0o444)
            with self.assertRaisesRegex(ValueError, 'mismatch'):
                g.load_release(self.home, current)


if __name__ == '__main__':
    unittest.main()
