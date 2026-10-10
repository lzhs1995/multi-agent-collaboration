"""The installed workflow entrypoints observe every failure without blocking."""
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import cmux_hook_runtime_audit as audit
import cmux_workflow_advisory as advisory
import manage_install as manager


class WorkflowAdvisoryTests(unittest.TestCase):
    def test_all_installed_workflow_commands_allow_fault_payloads(self):
        events = dict(manager.GUARDS, **manager.OPTIONAL_GUARDS)
        for name in sorted(advisory.WORKFLOW_HOOKS):
            event = events[Path(name).stem]
            faults = ['{broken', '[]', json.dumps({
                'session_id': 'unresolved-native-session',
                'hook_event_name': event,
                'tool_name': 'Bash',
                'tool_input': {'command': 'cmux-agent self'},
                'stop_hook_active': True,
                'last_assistant_message': 'Report frozen; callback could not be resolved.',
                'error': 'HOOK_CALLER_UNRESOLVED',
            })]
            for raw in faults:
                with self.subTest(hook=name, payload=raw[:30]):
                    command = shlex.split(manager.command(Path(name).stem))
                    self.assertEqual(Path(command[2]).name, 'cmux_workflow_advisory.py')
                    run = subprocess.run(command, input=raw, text=True,
                                         capture_output=True, timeout=10)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    # Empty output cannot deny a tool, force continuation,
                    # request another task or misrepresent delivery evidence.
                    self.assertEqual((run.stdout, run.stderr), ('', ''))

    def test_invalid_options_payload_or_observer_error_never_blocks(self):
        for argv, raw in [([], '{}'), (['--hook', 'foreign.py'], '{}'),
                          (['--hook', 'cmux_consensus_stop_guard.py'], 'null'),
                          (['--hook', 'cmux_consensus_stop_guard.py'],
                           'x' * (advisory.MAX_INPUT_CHARS + 1))]:
            with self.subTest(argv=argv, chars=len(raw)), \
                    patch.object(sys, 'argv', ['advisory', *argv]), \
                    patch.object(sys, 'stdin', io.StringIO(raw)), \
                    patch.object(audit, 'record') as record:
                self.assertEqual(advisory.main(), 0)
                record.assert_not_called()
        with patch.object(sys, 'argv', ['advisory', '--hook', 'cmux_consensus_stop_guard.py']), \
                patch.object(sys, 'stdin', io.StringIO('{}')), \
                patch.object(audit, 'record', side_effect=OSError('state unavailable')):
            self.assertEqual(advisory.main(), 0)

    def test_real_wrapper_records_original_hook_without_claiming_native_delivery(self):
        # Run the product wrapper/main and audit in a separate interpreter.
        # Only the storage home is isolated; no process provenance is forged.
        with tempfile.TemporaryDirectory(prefix='advisory-audit-') as directory:
            wrapper = Path(advisory.__file__).resolve()
            driver = (
                'from pathlib import Path; import runpy,sys; '
                'root=Path(sys.argv.pop(1)); '
                'Path.home=staticmethod(lambda: root); '
                'script=sys.argv.pop(1); sys.argv[0]=script; '
                'sys.path.insert(0,str(Path(script).parent)); '
                'runpy.run_path(script,run_name="__main__")'
            )
            session = '11111111-1111-4111-8111-111111111111'
            hook = 'cmux_native_delivery_guard.py'
            payload = dict(session_id=session, hook_event_name='PostToolUse',
                           tool_input={'command': 'private-command-data'})
            run = subprocess.run([sys.executable, '-B', '-c', driver, directory,
                                  str(wrapper), '--hook', hook], input=json.dumps(payload),
                                 text=True, capture_output=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual((run.stdout, run.stderr), ('', ''))
            receipts = list(Path(directory).rglob('*.json'))
            self.assertEqual(len(receipts), 1)
            receipt = json.loads(receipts[0].read_text())
            self.assertEqual(receipt['mode'], 'advisory')
            self.assertEqual(receipt['hook'], hook)
            self.assertEqual(receipt['original_hook'], hook)
            self.assertEqual(receipt['entrypoint'], wrapper.name)
            self.assertEqual(receipt['event'], 'PostToolUse')
            self.assertFalse(receipt['native_receipt'])
            self.assertNotIn('automatic_client_adoption', receipt)
            self.assertNotIn('private-command-data', receipts[0].read_text())


if __name__ == '__main__':
    unittest.main()
