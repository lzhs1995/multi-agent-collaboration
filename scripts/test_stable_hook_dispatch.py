#!/usr/bin/env python3
"""Stable hook entry: one handler string per guard, resolved through CURRENT."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import configuration_guardian as g
import mac_hook_dispatch as dispatch
import mac_hook_entry as entry
import manage_install as m

SCRIPTS = Path(m.__file__).resolve().parent


def mac_handlers(doc, home):
    return sorted(json.dumps(h, sort_keys=True) for entries in doc.get('hooks', {}).values()
                  for e in entries for h in e['hooks'] if m.owned_command(h['command'], home))


class StableHookTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='stable-hook-')
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()

    def release(self, name, members):
        """Immutable-layout fixture; pins CURRENT to it and returns its source."""
        source = self.home / m.RELEASES / name / 'source'
        if 'scripts/mac_hook_entry.py' in members:
            members = dict({'scripts/manage_install.py': b'# fixture router\n',
                            'scripts/cmux_workflow_advisory.py': b'# fixture advisory\n'}, **members)
        files = {}
        for relative, raw in members.items():
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            path.chmod(0o444)
            files[relative] = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        manifest = source.parent / 'MANIFEST.json'
        manifest.write_text(json.dumps(dict(files=files)))
        current = self.home / dispatch.CURRENT
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_text(json.dumps(dict(source=str(source), python=sys.executable,
                                           manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest())))
        return source

    def test_release_home_is_exact_for_the_immutable_layout(self):
        source = self.home / m.RELEASES / '0.4.59-0123456789ab' / 'source'
        self.assertEqual(m.release_home(source), self.home)
        self.assertEqual(m.release_home(Path('/Users/lzhs') / m.RELEASES / '0.4.58-cf3f6175840b/source'),
                         Path('/Users/lzhs'))
        for other in (self.home / 'checkout', self.home / m.RELEASES / 'x' / 'source' / 'scripts',
                      self.home / '.local/share/other/releases/x/source', Path('/source')):
            self.assertIsNone(m.release_home(other), other)

    def test_route_is_the_one_table_and_matches_release_path_commands(self):
        for name in dict(m.GUARDS, **m.OPTIONAL_GUARDS):
            script, arguments = m.route(name)
            self.assertEqual(shlex.split(m.command(name))[2:], [str(script), *arguments])
        self.assertIsNone(m.route('not_a_guard'))
        self.assertEqual(m.route('cmux_executor_idle_guard'),
                         (SCRIPTS / 'cmux_workflow_advisory.py', ['--hook', 'cmux_executor_idle_guard.py']))
        self.assertEqual(m.route('cmux_workspace_guard'), (SCRIPTS / 'cmux_workspace_guard.py', []))

    def test_release_handlers_are_identical_across_releases_and_settings_sources(self):
        foreign = {'Stop': [{'matcher': '*', 'hooks': [{'type': 'command', 'command': 'node goal.mjs', 'timeout': 5}]}]}
        user = {'env': {'K': 'user'}, 'hooks': foreign}
        profile = {'env': {'K': 'profile'}, 'disableAllHooks': False, 'hooks': foreign}
        outputs = []
        for name in ('0.4.59-aaaaaaaaaaaa', '0.4.60-bbbbbbbbbbbb'):
            with patch.object(m, 'ROOT', self.home / m.RELEASES / name / 'source'):
                outputs.append((m.transform(user, home=self.home, executor_reask=True),
                                m.transform(profile, home=self.home, executor_reask=True)))
        handlers = {tuple(mac_handlers(doc, self.home)) for pair in outputs for doc in pair}
        self.assertEqual(len(handlers), 1)  # same bytes: Claude runs one handler once
        commands = [json.loads(h)['command'] for h in next(iter(handlers))]
        self.assertTrue(all('releases/' not in c and c.endswith(' --hook ' + c.split()[-1]) for c in commands))
        self.assertEqual(len(commands), len(m.GUARDS) + len(m.OPTIONAL_GUARDS))
        idle = [json.loads(h) for h in next(iter(handlers)) if 'cmux_executor_idle_guard' in h]
        self.assertEqual([h.get('timeout') for h in idle], [75])
        for user_out, profile_out in outputs:
            self.assertEqual(user_out['env'], user['env'])
            self.assertIs(profile_out['disableAllHooks'], False)
            self.assertEqual(g.foreign_hooks(profile_out, m, self.home), foreign)

    def test_legacy_release_paths_migrate_once_and_lookalikes_stay_foreign(self):
        old = self.home / m.RELEASES / '0.4.48-3b4304d24f90/source/scripts'
        legacy = {'hooks': {'PreToolUse': [{'matcher': '*', 'hooks': [
            {'type': 'command', 'command': f'python3 -B {old}/cmux_workspace_guard.py'}]}]}}
        stable_path = m.dispatcher_path(self.home)
        foreign = [f'python3 -B {stable_path} --hook cmux_lease_guard --extra',
                   f'python3 -B {stable_path} --hook not_ours',
                   'python3 -B /opt/x/bin/mac_hook_dispatch.py --hook cmux_lease_guard']
        for value in foreign:
            self.assertFalse(m.owned_command(value, self.home), value)
        legacy['hooks']['PreToolUse'][0]['hooks'] += [{'type': 'command', 'command': c} for c in foreign]
        with patch.object(m, 'ROOT', self.home / m.RELEASES / '0.4.59-aaaaaaaaaaaa' / 'source'):
            migrated = m.transform(legacy, home=self.home)
            self.assertEqual(m.transform(migrated, home=self.home), migrated)
            self.assertTrue(m.owned_command(m.command('cmux_lease_guard', self.home), self.home))
        commands = [h['command'] for es in migrated['hooks'].values() for e in es for h in e['hooks']]
        self.assertFalse(any(str(old) in c for c in commands))
        self.assertEqual(sum(c.endswith(' --hook cmux_workspace_guard') for c in commands), 1)
        self.assertEqual([c for c in commands if c in foreign], foreign)

    def skip(self, hook='cmux_lease_guard'):
        with self.assertRaises(dispatch.Skip) as caught:
            dispatch.resolve(self.home, hook)
        return str(caught.exception)

    def test_dispatcher_resolves_current_release_entry_each_call(self):
        first = self.release('0.4.59-aaaaaaaaaaaa', {'scripts/mac_hook_entry.py': b'# a\n'})
        self.assertEqual(dispatch.resolve(self.home, 'cmux_lease_guard'),
                         [sys.executable, '-B', str(first / 'scripts/mac_hook_entry.py'), '--hook', 'cmux_lease_guard'])
        second = self.release('0.4.60-bbbbbbbbbbbb', {'scripts/mac_hook_entry.py': b'# b\n'})
        self.assertEqual(dispatch.resolve(self.home, 'cmux_lease_guard')[2], str(second / 'scripts/mac_hook_entry.py'))

    def test_rollback_release_without_router_uses_frozen_legacy_route(self):
        old = self.release('0.4.58-cf3f6175840b', {'scripts/cmux_workspace_guard.py': b'# w\n',
                                                     'scripts/cmux_workflow_advisory.py': b'# a\n'})
        self.assertEqual(dispatch.resolve(self.home, 'cmux_workspace_guard'),
                         [sys.executable, '-B', str(old / 'scripts/cmux_workspace_guard.py')])
        self.assertEqual(dispatch.resolve(self.home, 'cmux_lease_guard')[2:],
                         [str(old / 'scripts/cmux_workflow_advisory.py'), '--hook', 'cmux_lease_guard.py'])

    def test_invalid_current_or_release_is_diagnosable_skip(self):
        self.assertEqual(self.skip(), 'CURRENT_UNREADABLE')
        source = self.release('0.4.59-aaaaaaaaaaaa', {'scripts/mac_hook_entry.py': b'# a\n'})
        self.assertEqual(self.skip('../x'), 'BAD_HOOK_NAME')
        current = self.home / dispatch.CURRENT
        body = json.loads(current.read_text())
        current.write_text(json.dumps(dict(body, source='/tmp/elsewhere/source')))
        self.assertEqual(self.skip(), 'CURRENT_SOURCE_OUTSIDE_RELEASES')
        current.write_text(json.dumps(dict(body, manifest_sha256='0' * 64)))
        self.assertEqual(self.skip(), 'RELEASE_MANIFEST_MISMATCH')
        current.write_text(json.dumps(body))
        member = source / 'scripts/mac_hook_entry.py'
        member.chmod(0o644)
        member.write_bytes(b'# changed\n')
        self.assertEqual(self.skip(), 'RELEASE_MEMBER_CHANGED')
        current.write_text('{broken')
        self.assertEqual(self.skip(), 'CURRENT_UNREADABLE')

    def run_dispatcher(self, payload, hook='cmux_lease_guard'):
        environment = dict(os.environ, HOME=str(self.home))
        return subprocess.run([sys.executable, '-B', str(SCRIPTS / 'mac_hook_dispatch.py'), '--hook', hook],
                              input=payload, text=True, capture_output=True, timeout=15, env=environment)

    def runtime_release(self, router=None):
        members = {str(path.relative_to(SCRIPTS.parent)): path.read_bytes()
                   for path in SCRIPTS.glob('*.py') if not path.name.startswith('test_')}
        members['scripts/cmux_workspace_guard.py'] = b'import sys\nprint("PINNED_GUARD", sys.stdin.read())\n'
        if router is not None:
            members['scripts/manage_install.py'] = router
        return self.release('0.4.59-aaaaaaaaaaaa', members)

    def test_real_entry_rejects_tampered_router_guard_wrapper_and_imported_dependency(self):
        source = self.runtime_release()
        control = self.run_dispatcher('{"control":1}', 'cmux_workspace_guard')
        self.assertEqual((control.returncode, control.stdout.strip()), (0, 'PINNED_GUARD {"control":1}'), control.stderr)
        for name in ('manage_install.py', 'cmux_workspace_guard.py',
                     'cmux_workflow_advisory.py', 'cmux_daemon_identity.py'):
            with self.subTest(name=name):
                path = source / 'scripts' / name
                raw = path.read_bytes()
                path.chmod(0o644)
                path.write_bytes(raw + b'\nprint("TAMPERED_MUST_NOT_RUN")\n')
                run = self.run_dispatcher('{"secret":"not-for-diagnostic"}', 'cmux_workspace_guard')
                self.assertEqual((run.returncode, run.stdout), (0, ''), run.stderr)
                record = (self.home / dispatch.DIAGNOSTIC).read_text()
                self.assertEqual(json.loads(record)['reason'], 'RELEASE_MEMBER_CHANGED')
                self.assertNotIn('not-for-diagnostic', record)
                path.write_bytes(raw)
        (source / 'scripts' / 'shadow_import.py').write_text('print("UNPINNED")\n')
        run = self.run_dispatcher('{}', 'cmux_workspace_guard')
        self.assertEqual((run.returncode, run.stdout), (0, ''))
        self.assertIn('RELEASE_HOOK_DEPENDENCY_MISSING', run.stderr)

    def test_real_entry_reports_pinned_router_import_failure_and_doctor_detects_it(self):
        source = self.runtime_release(router=b'not valid python !!!\n')
        m.publish_dispatcher(self.home)
        run = self.run_dispatcher('{}', 'cmux_workspace_guard')
        self.assertEqual((run.returncode, run.stdout), (0, ''))
        self.assertIn('ROUTER_IMPORT_FAILED', run.stderr)
        self.assertNotIn('unknown hook', run.stderr)
        self.assertEqual(json.loads((self.home / dispatch.DIAGNOSTIC).read_bytes())['reason'], 'ROUTER_IMPORT_FAILED')
        # Doctor must use the real entry rather than pass on resolve() alone.
        (self.home / dispatch.DIAGNOSTIC).unlink()
        check = m.dispatcher_check(self.home)
        self.assertTrue(check['resolved'])
        self.assertFalse(check['benignExitZero'], check)
        self.assertEqual(check['probe']['skip_reason'], 'ROUTER_IMPORT_FAILED')
        self.assertFalse((self.home / dispatch.DIAGNOSTIC).exists())

    def test_doctor_dispatcher_positive_control_and_unknown_hook_are_distinct(self):
        self.runtime_release()
        m.publish_dispatcher(self.home)
        check = m.dispatcher_check(self.home)
        self.assertTrue(check['benignExitZero'], check)
        run = self.run_dispatcher('{}', 'unknown_guard')
        self.assertEqual((run.returncode, run.stdout), (0, ''))
        self.assertIn('unknown hook', run.stderr)
        self.assertFalse((self.home / dispatch.DIAGNOSTIC).exists())

    def test_real_process_execs_release_with_stdin_and_status_or_skips_open(self):
        self.release('0.4.59-aaaaaaaaaaaa', {'scripts/mac_hook_entry.py':
            b'import sys\nprint(sys.argv[1:], sys.stdin.read())\nraise SystemExit(2)\n'})
        run = self.run_dispatcher('{"p":1}')
        self.assertEqual((run.returncode, run.stdout.strip()), (2, "['--hook', 'cmux_lease_guard'] {\"p\":1}"))
        (self.home / dispatch.CURRENT).write_text('{broken')
        secret = '{"session_id":"payload-must-not-be-recorded"}'
        run = self.run_dispatcher(secret)
        self.assertEqual((run.returncode, run.stdout), (0, ''))
        self.assertIn('skipped CURRENT_UNREADABLE', run.stderr)
        record = (self.home / dispatch.DIAGNOSTIC).read_text()
        self.assertEqual(json.loads(record)['reason'], 'CURRENT_UNREADABLE')
        self.assertNotIn('payload-must-not-be-recorded', record)
        self.assertFalse((self.home / dispatch.DIAGNOSTIC).parent.stat().st_mode & 0o077)

    def test_release_entry_routes_like_release_paths_and_unknown_is_noop(self):
        self.assertEqual(entry.target(['--hook', 'cmux_executor_idle_guard']), m.route('cmux_executor_idle_guard'))
        self.assertIsNone(entry.target(['--hook', 'unknown_guard']))
        self.assertIsNone(entry.target(['cmux_lease_guard']))
        environment = dict(os.environ, HOME=str(self.home))
        for hook in ('cmux_lease_guard', 'unknown_guard'):
            run = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'mac_hook_entry.py'), '--hook', hook],
                                 input='{}', text=True, capture_output=True, timeout=15, env=environment)
            self.assertEqual((run.returncode, run.stdout), (0, ''), (hook, run.stderr))

    def test_release_install_publishes_entry_first_and_uninstall_keeps_it(self):
        root = self.home / m.RELEASES / '0.4.59-aaaaaaaaaaaa' / 'source'
        root.mkdir(parents=True)
        (root / 'SKILL.md').write_text('release skill')
        stable = m.dispatcher_path(self.home)
        order, real_publish, real_replace = [], m.publish_dispatcher, m.replace
        with patch.object(m, 'ROOT', root), \
                patch.object(m, 'publish_dispatcher', side_effect=lambda h: order.append('entry') or real_publish(h)), \
                patch.object(m, 'replace', side_effect=lambda *a: order.append(a[0].name) or real_replace(*a)):
            result = m.manage(self.home, 'install', True)
            self.assertEqual(order[0], 'entry')
            self.assertEqual(result['dispatcher_published']['changed'], True)
            self.assertEqual(stable.read_bytes(), (SCRIPTS / 'mac_hook_dispatch.py').read_bytes())
            for client, filename in (('codex', 'hooks.json'), ('claude', 'settings.json')):
                doc = json.loads((self.home / ('.' + client) / filename).read_text())
                commands = {h['command'] for es in doc['hooks'].values() for e in es for h in e['hooks']}
                self.assertEqual(commands, {m.command(guard) for guard in m.GUARDS})
                self.assertTrue(all(str(stable) in c for c in commands))
            check = m.dispatcher_check(self.home)
            self.assertEqual((check['present'], check['matches_release'], check['resolved'], check['skip_reason']),
                             (True, True, False, 'CURRENT_UNREADABLE'))
            m.manage(self.home, 'uninstall', True)
        self.assertTrue(stable.is_file())  # pinned processes may still call it

    def test_guardian_reconciles_startup_profiles_and_only_owned_snapshots(self):
        root = self.home / m.RELEASES / '0.4.59-aaaaaaaaaaaa' / 'source'
        old = self.home / m.RELEASES / '0.4.48-3b4304d24f90/source/scripts'
        base = {'env': {'TOKEN': 'fixture-only'}, 'hooks': {'Stop': [{'matcher': '*', 'hooks': [
            {'type': 'command', 'command': 'node goal.mjs', 'timeout': 5}]}]}}
        pinned = json.loads(json.dumps(base))
        pinned['hooks']['PreToolUse'] = [{'matcher': '*', 'hooks': [
            {'type': 'command', 'command': f'python3 -B {old}/cmux_workspace_guard.py'}]}]
        profiles, cmux = self.home / '.config/magpie-stations/profiles', self.home / '.cmuxterm/claude-settings'
        files = {profiles / 'new.json': base, profiles / 'off.json': dict(base, disableAllHooks=True),
                 cmux / 'ccc-compact-abc.json': base, cmux / '0123abcd.json': pinned, cmux / 'fedc4321.json': base}
        for path, doc in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(doc))
        (profiles / 'new.json.bak').write_text('{}')
        (profiles / '.new.json.lock').write_text('')
        with patch.object(m, 'ROOT', root):
            first = g.reconcile_startup_files(self.home, m, True)
            self.assertEqual({Path(r['path']).name for r in first if r['changed']},
                             {'new.json', 'off.json', 'ccc-compact-abc.json', '0123abcd.json'})
            self.assertFalse(any(r['change_needed'] for r in g.reconcile_startup_files(self.home, m, True)))
            expected = mac_handlers(m.transform(base, home=self.home, executor_reask=True), self.home)
        self.assertEqual(json.loads((cmux / 'fedc4321.json').read_text()), base)  # never had our hooks
        for path in (profiles / 'new.json', profiles / 'off.json', cmux / 'ccc-compact-abc.json', cmux / '0123abcd.json'):
            doc = json.loads(path.read_text())
            self.assertEqual(mac_handlers(doc, self.home), expected, path.name)
            self.assertEqual(doc['env'], base['env'])
            self.assertEqual(g.foreign_hooks(doc, m, self.home), base['hooks'])
        self.assertIs(json.loads((profiles / 'off.json').read_text())['disableAllHooks'], True)
        self.assertEqual([r['hooks_disabled'] for r in first if Path(r['path']).name == 'off.json'], [True])
        self.assertEqual(len(list(g.state_root(self.home).glob('configuration-guardian-v1/profile-backups/*.json'))), 4)

    def test_guardian_names_stable_path_only_after_publishing_it(self):
        self.assertEqual(g.publish_stable_entry(self.home, m, True)['status'], 'NOT_STABLE_RELEASE')
        with patch.object(m, 'ROOT', self.home / m.RELEASES / '0.4.59-aaaaaaaaaaaa' / 'source'):
            self.assertEqual(g.publish_stable_entry(self.home, m, False)['status'], 'PLANNED')
            self.assertFalse(m.dispatcher_path(self.home).exists())
            self.assertEqual(g.publish_stable_entry(self.home, m, True)['status'], 'PUBLISHED')
        self.assertTrue(m.dispatcher_path(self.home).is_file())

    def test_dispatcher_publication_preserves_different_existing_file(self):
        target = m.dispatcher_path(self.home)
        target.parent.mkdir(parents=True)
        target.write_bytes(b'# another owner\n')
        before = target.stat()
        with self.assertRaisesRegex(RuntimeError, 'foreign hook dispatcher preserved'):
            m.publish_dispatcher(self.home)
        self.assertEqual(target.read_bytes(), b'# another owner\n')
        self.assertEqual(target.stat().st_ino, before.st_ino)

    def test_dispatcher_first_publication_does_not_overwrite_concurrent_creator(self):
        target = m.dispatcher_path(self.home)
        def concurrent_creator(source, destination):
            Path(destination).write_bytes(b'# concurrent owner\n')
            raise FileExistsError(str(destination))
        with patch.object(m.os, 'link', side_effect=concurrent_creator), \
                self.assertRaisesRegex(RuntimeError, 'concurrent hook dispatcher preserved'):
            m.publish_dispatcher(self.home)
        self.assertEqual(target.read_bytes(), b'# concurrent owner\n')
        self.assertFalse(list(target.parent.glob('.mac-hook-dispatch-*')))

    def test_dispatcher_identical_publication_reuses_existing_inode(self):
        first = m.publish_dispatcher(self.home)
        target = m.dispatcher_path(self.home)
        before = target.stat()
        second = m.publish_dispatcher(self.home)
        self.assertTrue(first['changed'])
        self.assertFalse(second['changed'])
        self.assertEqual(target.stat().st_ino, before.st_ino)

    def test_explicit_profile_repair_republishes_missing_dispatcher_without_job_or_profile_write(self):
        source = self.runtime_release()
        path = self.home / '.cmuxterm/claude-settings/pinned.json'
        path.parent.mkdir(parents=True)
        base = {'env': {'TOKEN': 'fixture'}, 'hooks': {'Stop': [{'hooks': [
            {'type': 'command', 'command': 'node goal.mjs'}]}]}}
        with patch.object(m, 'ROOT', source):
            path.write_text(json.dumps(g.normalized(base, m, self.home, True)))
            before = path.read_bytes()
            manifest = self.home / dispatch.CURRENT
            with patch.object(g, 'load_release', return_value=(dict(source=str(source)), m)):
                preview = g.reconcile_profile(self.home, manifest, path)
                self.assertFalse(m.dispatcher_path(self.home).exists())
                result = g.reconcile_profile(self.home, manifest, path, True, preview['before_sha256'])
                self.assertEqual(result['status'], 'PROFILE_UNCHANGED')
                self.assertFalse(result['changed'])
                self.assertTrue(result['stable_entry']['changed'])
                self.assertFalse(result['guardian_job_changed'])
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse((self.home / 'Library/LaunchAgents').exists())
                self.assertEqual(self.run_dispatcher('{}', 'cmux_workspace_guard').stdout.strip(), 'PINNED_GUARD {}')
                inode = m.dispatcher_path(self.home).stat().st_ino
                reused = g.reconcile_profile(self.home, manifest, path, True, preview['before_sha256'])
                self.assertFalse(reused['stable_entry']['changed'])
                self.assertEqual(m.dispatcher_path(self.home).stat().st_ino, inode)
                m.dispatcher_path(self.home).write_bytes(b'# foreign dispatcher\n')
                with self.assertRaisesRegex(RuntimeError, 'foreign hook dispatcher preserved'):
                    g.reconcile_profile(self.home, manifest, path, True, preview['before_sha256'])
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(m.dispatcher_path(self.home).read_bytes(), b'# foreign dispatcher\n')


if __name__ == '__main__':
    unittest.main()
