"""Test-only CLI driver: real hook source/main with private marker directories.

Product entrypoints never import this module. This driver replaces only the
kernel process reader with an ordinary-client fixture, not identity resolution,
hook verdicts, stdin parsing, or exit codes. It works even when the test runner
itself is a child of a managed daemon.
"""
from contextlib import contextmanager, ExitStack
import importlib
from pathlib import Path
import runpy
import sys
from unittest.mock import patch


def ordinary_process(pid, **_kwargs):
    return dict(pid=pid, ppid=1, birth=[1, 0], argv=['/offline/test-client'],
                executable='/offline/test-client', env={})


@contextmanager
def hook_environment(active_dir):
    """Keep real guard semantics while isolating only process/filesystem inputs."""
    active_dir = Path(active_dir)
    registry_dir = active_dir.parent / 'registry'
    with ExitStack() as stack:
        daemon = importlib.import_module('cmux_daemon_identity')
        stack.enter_context(patch.object(daemon, 'process', side_effect=ordinary_process))
        for name in ('cmux_consensus_stop_guard', 'cmux_lease_guard',
                     'cmux_consensus_round_guard', 'cmux_handshake_receipt_guard',
                     'cmux_idle_pull'):
            module = importlib.import_module(name)
            for attribute, value in (('ACTIVE_DIR', active_dir), ('REGISTRY_DIR', registry_dir)):
                if hasattr(module, attribute):
                    stack.enter_context(patch.object(module, attribute, value))
        yield


def command(script, active_dir, *args):
    return [sys.executable, '-B', str(Path(__file__).resolve()),
            str(script), str(active_dir), *map(str, args)]


def main():
    script, active_dir, *args = sys.argv[1:]
    script, active_dir = Path(script).resolve(), Path(active_dir).resolve()
    # The script is loaded exactly from the caller-selected path; a mutation
    # override such as STOP_GUARD_UNDER_TEST remains under test.
    with hook_environment(active_dir), patch.object(sys, 'argv', [str(script), *args]):
        namespace = runpy.run_path(str(script), run_name='_offline_hook_under_test')
        entry = namespace['main']
        # run_path creates separate function globals from canonical imports.
        # Patch those constants too, before entering the real main/stdin parser.
        for attribute, value in (('ACTIVE_DIR', active_dir),
                                 ('REGISTRY_DIR', active_dir.parent / 'registry')):
            if attribute in entry.__globals__:
                entry.__globals__[attribute] = value
        return entry()


if __name__ == '__main__':
    raise SystemExit(main())
