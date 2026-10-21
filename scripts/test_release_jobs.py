import json
from pathlib import Path
import plistlib
import tempfile
import unittest
import os
import subprocess
import sys

import configuration_guardian as guardian
import executor_reask_scheduler as scheduler
import release_jobs


class ReleaseJobsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.manifest = self.home / '.local/state/multi-agent-collaboration/CURRENT.json'
        self.manifest.parent.mkdir(parents=True)
        self.source = self.home / 'release/source'

    def configure(self):
        self.manifest.write_text(json.dumps(dict(source=str(self.source), python='/python')))
        paths = []
        for module in (guardian, scheduler):
            path = self.home / 'Library/LaunchAgents' / (module.LABEL + '.plist')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(plistlib.dumps(module.launchd_spec(self.home, self.source, '/python', self.manifest)))
            paths.append(path)
        return paths

    def test_both_current_jobs_pass_without_claiming_execution(self):
        self.configure()
        result = release_jobs.check(self.home)
        self.assertTrue(result['ok'])
        self.assertFalse(result['execution_proven'])

    def test_old_scheduler_is_detected_with_current_guardian(self):
        _, path = self.configure()
        spec = plistlib.loads(path.read_bytes())
        spec['ProgramArguments'][2] = '/previous/source/scripts/executor_reask_scheduler.py'
        path.write_bytes(plistlib.dumps(spec))
        self.assertFalse(release_jobs.check(self.home)['ok'])

    def test_missing_and_symlink_job_fail_closed(self):
        _, path = self.configure()
        original = path.with_suffix('.old')
        path.rename(original)
        self.assertFalse(release_jobs.check(self.home)['ok'])
        path.symlink_to(original)
        self.assertFalse(release_jobs.check(self.home)['ok'])

    def test_unisolated_job_is_detected(self):
        paths = self.configure()
        for path in paths:
            original = path.read_bytes()
            spec = plistlib.loads(original)
            spec['ProgramArguments'][1] = '-B'
            path.write_bytes(plistlib.dumps(spec))
            self.assertFalse(release_jobs.check(self.home)['ok'])
            path.write_bytes(original)
        self.assertTrue(release_jobs.check(self.home)['ok'])

    def test_actual_job_launcher_ignores_external_python_cache_and_import_path(self):
        # Execute each generated job command against a disposable entrypoint;
        # no real HOME, launchctl, profile database or scheduler is touched.
        scripts = self.source / 'scripts'
        scripts.mkdir(parents=True)
        probe = "import json,sys; print(json.dumps([sys.flags.ignore_environment,sys.flags.dont_write_bytecode,sys.pycache_prefix]))"
        environment = dict(os.environ, PYTHONPYCACHEPREFIX=str(self.home / 'foreign-cache'),
                           PYTHONPATH=str(self.home / 'foreign-imports'))
        for module, name in ((guardian, 'configuration_guardian.py'),
                             (scheduler, 'executor_reask_scheduler.py')):
            (scripts / name).write_text(probe)
            command = module.launchd_spec(self.home, self.source, sys.executable, self.manifest)['ProgramArguments']
            result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), [1, 1, None])
            control = [command[0], '-B', *command[2:]]
            result = subprocess.run(control, env=environment, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), [0, 1, str(self.home / 'foreign-cache')])

    def test_unmanaged_checkout_does_not_require_launchd(self):
        result = release_jobs.check(self.home)
        self.assertTrue(result['ok'])
        self.assertFalse(result['applicable'])


if __name__ == '__main__':
    unittest.main()
