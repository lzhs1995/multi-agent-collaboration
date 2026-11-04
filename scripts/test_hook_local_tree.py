"""Only authenticated embedded hooks may limit the host tree to their workspace."""
import copy
import os
import unittest
from unittest.mock import patch

import cmux_workspace_guard as guard
import test_cmux_daemon_identity as fixture


class HookLocalTreeTests(unittest.TestCase):
    def setUp(self):
        fixture.DaemonTests.setUp(self)
        self.managed_proof = copy.deepcopy(self.proof)
        self.managed_identity = copy.deepcopy(self.identity)
        self.proof.update(source='embedded-native-client-v1', daemon=None)
        self.identity['caller'] = dict(surface_ref='surface:1',workspace_ref='workspace:1',pane_ref='pane:1')
        self.calls = []

    def read(self, *args):
        self.calls.append(args)
        if args[0] == 'identify':
            return copy.deepcopy(self.identity)
        result = copy.deepcopy(self.tree)
        if '--workspace' in args:
            self.assertEqual(args[args.index('--workspace')+1],fixture.W)
            result['windows'][0]['workspaces'] = result['windows'][0]['workspaces'][:1]
        return result

    def snapshot(self, local=True):
        with patch.object(guard, '_read_json_command', side_effect=self.read):
            return guard.caller_snapshot(collector=lambda:copy.deepcopy(self.proof),local_hook=local)

    def test_selected_workspace_comes_from_live_proof_not_inherited_selector(self):
        with patch.dict(os.environ, {'CMUX_WORKSPACE_ID':fixture.X}):
            identity, _, env, proof = self.snapshot()
        self.assertEqual(identity['caller']['surface_id'],fixture.C)
        self.assertEqual(env['CMUX_WORKSPACE_ID'],fixture.W)
        self.assertEqual(proof['session'],fixture.S)
        trees = [c for c in self.calls if c[0]=='tree']
        self.assertEqual(trees,[('tree','--workspace',fixture.W,'--json','--id-format','both')]*2)

    def test_generic_call_keeps_complete_tree_for_cross_workspace_checks(self):
        self.snapshot(local=False)
        self.assertTrue(all('--all' in c for c in self.calls if c[0]=='tree'))

    def test_managed_daemon_keeps_both_origin_and_client_workspaces(self):
        self.proof, self.identity = self.managed_proof, self.managed_identity
        self.snapshot()
        self.assertTrue(all('--all' in c for c in self.calls if c[0]=='tree'))

    def test_no_proof_cannot_request_restricted_tree(self):
        self.proof = None
        self.snapshot()
        self.assertTrue(all('--all' in c for c in self.calls if c[0]=='tree'))

    def test_wrong_workspace_and_second_observation_drift_still_refuse(self):
        changed = copy.deepcopy(self.tree)
        changed['windows'][0]['workspaces'][0]['id'] = fixture.X
        for replies in ([self.identity,changed], [self.identity,self.tree,self.identity,changed]):
            with patch.object(guard,'_read_json_command',side_effect=replies):
                with self.assertRaises(guard.WorkspaceScopeError):
                    guard.caller_snapshot(collector=lambda:copy.deepcopy(self.proof),local_hook=True)

    def test_native_process_drift_after_final_tree_still_refuses(self):
        changed = copy.deepcopy(self.proof)
        changed['client']['birth'][0] += 1
        with patch.object(guard,'_read_json_command',side_effect=self.read):
            with self.assertRaisesRegex(guard.WorkspaceScopeError,'final check'):
                items = iter([self.proof,self.proof,changed])
                guard.caller_snapshot(collector=lambda:next(items),local_hook=True)


if __name__ == '__main__':
    unittest.main()
