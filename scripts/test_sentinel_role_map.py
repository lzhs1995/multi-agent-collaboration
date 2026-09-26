"""Offline producer/consumer checks for real harness role-map shapes."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import mac_harness
from cmux_executor_sentinel import verify_role_map_target


class RoleMapIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'role-map.json'

    def check(self, value, task='synthetic', supervisor='surface:2', executor='surface:1'):
        self.path.write_text(json.dumps(value))
        return verify_role_map_target(str(self.path), task, supervisor, executor)

    def test_actual_harness_map_output_is_accepted(self):
        gate = {'task_id': 'synthetic', 'status': 'PASS', 'workspace_ref': 'workspace:synthetic',
                'supervisor': 'surface:2', 'supervisor_provider': 'codex',
                'executor': 'surface:1', 'executor_provider': 'claude'}
        (self.root / 'identity-gate.json').write_text(json.dumps(gate))
        mac_harness.cmd_map(SimpleNamespace(task_id='synthetic', artifact_root=str(self.root), json=False))
        value = json.loads(self.path.read_text())
        self.assertIsInstance(value['supervisor'], dict)
        self.assertTrue(verify_role_map_target(str(self.path), 'synthetic', 'surface:2', 'surface:1')[0])

    def test_detailed_maps_still_reject_wrong_task_and_either_surface(self):
        value = {'task_id': 'synthetic', 'supervisor': {'surface_ref': 'surface:2'},
                 'executor': {'surface_ref': 'surface:1'}}
        for changes in ({'task': 'other'}, {'supervisor': 'surface:3'}, {'executor': 'surface:3'}):
            with self.subTest(changes=changes):
                self.assertFalse(self.check(value, **changes)[0])

    def test_legacy_flat_and_roles_list_remain_supported(self):
        for value in ({'supervisor': 'surface:2', 'executor': 'surface:1'},
                      {'roles': [{'role': 'supervisor', 'surface_ref': 'surface:2'},
                                 {'role': 'executor', 'surface_ref': 'surface:1'}]}):
            with self.subTest(value=value):
                self.assertTrue(self.check(value)[0])

    def test_missing_and_conflicting_role_identity_fail_closed(self):
        for value in ({'supervisor': {'provider': 'codex'}},
                      {'roles': {'supervisor': 'surface:9'}, 'supervisor': {'surface_ref': 'surface:2'}}):
            with self.subTest(value=value):
                self.assertFalse(self.check(value)[0])

    def test_explicit_malformed_role_cannot_accept_an_arbitrary_target(self):
        malformed = [{}, {'provider': 'claude'}, {'surface_ref': True},
                     {'surface_ref': None}, {'surface_ref': ''}, {'surface_ref': ' '},
                     True, None, '', ' ']
        for role in ('supervisor', 'executor'):
            for declaration in malformed:
                value = {'supervisor': {'surface_ref': 'surface:2'},
                         'executor': {'surface_ref': 'surface:1'}, role: declaration}
                with self.subTest(role=role, declaration=declaration):
                    self.assertFalse(self.check(value, **{role: 'surface:99999'})[0])

    def test_surface_aliases_must_be_valid_and_consistent(self):
        for declaration in ({'surface': 'surface:1'},
                            {'surface_ref': 'surface:1', 'surface': 'surface:1'}):
            self.assertTrue(self.check({'supervisor': 'surface:2', 'executor': declaration})[0])
        for declaration in ({'surface_ref': 'surface:1', 'surface': 'surface:9'},
                            {'surface_ref': None, 'surface': 'surface:1'},
                            {'surface_ref': True, 'surface': 'surface:1'}):
            with self.subTest(declaration=declaration):
                self.assertFalse(self.check({'supervisor': 'surface:2', 'executor': declaration})[0])


if __name__ == '__main__':
    unittest.main()
