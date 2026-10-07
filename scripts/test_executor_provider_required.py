import unittest
from types import SimpleNamespace
from unittest import mock

import mac_harness as harness


class ExecutorProviderTests(unittest.TestCase):
    def test_missing_provider_fails_before_setup_or_identity(self):
        for entry in (harness.cmd_preflight, harness.cmd_identity_gate):
            with self.subTest(entry=entry.__name__), \
                 mock.patch.object(harness, "cmd_setup_check") as setup, \
                 mock.patch.object(harness.cmux, "whoami") as identity, \
                 mock.patch.object(harness, "_ensure_root") as mkdir:
                with self.assertRaises(SystemExit) as error:
                    entry(SimpleNamespace(executor=None, executor_surface=["surface:48"]))
                self.assertEqual(error.exception.code, 2)
                setup.assert_not_called()
                identity.assert_not_called()
                mkdir.assert_not_called()

    def test_explicit_provider_and_complete_per_surface_providers(self):
        for provider, refs in [("claude", ["surface:48"]),
                               ("codex", []),
                               (None, ["surface:48=claude", "surface:49=codex"])]:
            harness._require_executor_provider(SimpleNamespace(executor=provider, executor_surface=refs))

    def test_partial_or_empty_provider_is_rejected(self):
        for refs in ([], ["surface:48="], ["surface:48=claude", "surface:49"]):
            with self.assertRaises(SystemExit):
                harness._require_executor_provider(SimpleNamespace(executor=None, executor_surface=refs))


if __name__ == "__main__":
    unittest.main()
