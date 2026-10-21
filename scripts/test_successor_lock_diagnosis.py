"""Lock contention stays zero-input and names the exact shared lock."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import successor_native_delivery as delivery


class SuccessorLockDiagnosisTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='successor-lock-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.journal = self.root / 'successor-dispatch-v2/J'
        self.journal.mkdir(parents=True)
        self.artifact = dict(target_workspace_uuid='1345022A-8558-4047-82F2-8C21B27F682C',
                             target_pane_uuid='ABE733B1-B9AF-4B39-BBA6-212850F63DE4',
                             target_executor_uuid='000AA6F1-E5C5-450B-ABCB-639692C2E925')

    def lock_paths(self):
        workspace, pane, target = (self.artifact[key] for key in
                                  ('target_workspace_uuid', 'target_pane_uuid', 'target_executor_uuid'))
        return {'pane': self.root / 'deliveries-v1' / ('pane-' + delivery._hash((workspace + ':' + pane).encode()) + '.lock'),
                'legacy-target': self.root / 'deliveries-v1' / ('target-' + delivery._hash(target.encode()) + '.lock'),
                'task-target': self.root / 'task-dispatch-v1' / ('target-' + delivery._hash(target.encode()) + '.lock'),
                'journal': self.journal / 'delivery.lock'}

    def test_actual_child_lock_contention_names_each_lock_without_input_intents(self):
        code = ('import fcntl,sys;f=open(sys.argv[1],"a");fcntl.flock(f,fcntl.LOCK_EX);'
                'print("held",flush=True);sys.stdin.read()')
        for kind, path in self.lock_paths().items():
            with self.subTest(kind=kind):
                path.parent.mkdir(parents=True, exist_ok=True)
                child = subprocess.Popen([sys.executable, '-B', '-c', code, str(path)],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
                try:
                    self.assertEqual(child.stdout.readline().strip(), 'held')
                    with self.assertRaises(delivery.native.NativeDeliveryError) as caught:
                        with delivery._locked(self.root, self.journal, self.artifact):
                            self.fail('contended lock entered')
                    message = str(caught.exception)
                    self.assertTrue(message.startswith('SUCCESSOR_DELIVERY_IN_PROGRESS: zero input'))
                    self.assertIn('held_lock=' + kind + ' ', message)
                    self.assertEqual(caught.exception.details['input_operations'], 0)
                    self.assertEqual(caught.exception.details['path'], str(path))
                    self.assertFalse(list(self.journal.glob('event-*.json')))
                    self.assertFalse((self.journal / 'attempt.json').exists())
                    if sys.platform == 'darwin':
                        self.assertIn(str(child.pid), message.split('holder_pids=')[1])
                finally:
                    child.stdin.close()
                    child.wait(timeout=5)
                    child.stdout.close()
        with delivery._locked(self.root, self.journal, self.artifact):
            self.assertFalse(list(self.journal.glob('event-*.json')))

    def test_diagnostic_timeout_never_changes_lock_outcome(self):
        with patch.object(subprocess, 'run', side_effect=subprocess.TimeoutExpired('lsof', 2)):
            self.assertEqual(delivery._lock_holders(self.root / 'lock'), 'unmeasured')
        result = subprocess.CompletedProcess(['lsof'], 0, stdout=str(os.getpid()) + '\n123\n123\n', stderr='')
        with patch.object(subprocess, 'run', return_value=result) as run:
            self.assertEqual(delivery._lock_holders(self.root / 'lock'), '123')
            self.assertEqual(run.call_args.kwargs['timeout'], 2)


if __name__ == '__main__':
    unittest.main()
