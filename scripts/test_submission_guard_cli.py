"""Run semantic hook checks without aborting unittest discovery."""
from pathlib import Path
import subprocess
import sys
import unittest

class SubmissionGuardCLITests(unittest.TestCase):
    def test_semantic_checks(self):
        result = subprocess.run([sys.executable, '-B', str(Path(__file__).with_name('check_cmux_submit_confirmation_guard.py'))], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('ALL SEMANTIC TESTS PASSED', result.stdout)
