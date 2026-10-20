#!/usr/bin/env python3
"""In-release hook router used by the stable dispatcher.

``manage_install.route`` is the one table for which guards run directly and
which run through ``cmux_workflow_advisory``. Running the target in-process
keeps its ``__file__``, ``sys.argv`` and exit status, so guard ownership checks,
runtime adoption receipts and the explicit Stop continuation behave exactly as
with the release-path commands. An unknown name is a no-op.
"""
import runpy
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def target(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2 or argv[0] != '--hook':
        return None
    sys.path.insert(0, str(SCRIPTS))
    try:
        import manage_install
        return manage_install.route(argv[1])
    except Exception:
        return None


def main():
    routed = target()
    if routed is None:
        print('mac-hook-entry: skipped unknown hook', file=sys.stderr)
        return 0
    script, arguments = routed
    sys.argv = [str(script), *arguments]
    runpy.run_path(str(script), run_name='__main__')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
