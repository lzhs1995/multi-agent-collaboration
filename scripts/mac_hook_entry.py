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
import hashlib
import json
import os
import re
import stat
import tempfile
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
DIAGNOSTIC = Path('.local/state/multi-agent-collaboration/hook-dispatch-v1/LAST-SKIP.json')
NAME = re.compile(r'[a-z][a-z0-9_]{2,63}')


class RouterFailure(Exception):
    """A bounded reason code, separate from a genuine unknown hook."""


def record_skip(hook, reason):
    """Keep the same private diagnostic even when the router cannot import."""
    pending = None
    try:
        path = Path.home() / DIAGNOSTIC
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = path.parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077):
            return
        body = dict(schema='mac-hook-dispatch-skip-v1', reason=reason,
                    hook=hook if isinstance(hook, str) and NAME.fullmatch(hook) else None,
                    at_epoch=time.time(), pid=os.getpid(), ppid=os.getppid())
        fd, pending = tempfile.mkstemp(prefix='.skip-', dir=path.parent)
        with os.fdopen(fd, 'w') as stream:
            json.dump(body, stream)
        os.replace(pending, path)
        pending = None
    except Exception:
        pass
    finally:
        if pending:
            try:
                os.unlink(pending)
            except OSError:
                pass


def verify_source():
    """Direct immutable entry calls get the same dependency checks as dispatch.

    Bootstrap the verifier from its own pinned bytes; a development checkout
    has no release MANIFEST and retains its direct test/install behavior.
    """
    source = SCRIPTS.parent
    if source.name != 'source' or source.parent.parent.name != 'releases':
        return
    if sys.pycache_prefix is not None:
        raise RouterFailure('RELEASE_EXTERNAL_BYTECODE_CACHE_REFUSED')
    try:
        current = json.loads((Path.home() / '.local/state/multi-agent-collaboration/CURRENT.json').read_bytes())
        raw = (source.parent / 'MANIFEST.json').read_bytes()
        manifest = json.loads(raw)
        if (Path(current['source']) != source
                or hashlib.sha256(raw).hexdigest() != current['manifest_sha256']):
            raise RouterFailure('RELEASE_MANIFEST_MISMATCH')
        verifier = SCRIPTS / 'mac_hook_dispatch.py'
        pin = manifest['files']['scripts/mac_hook_dispatch.py']
        verified_bytes = verifier.read_bytes()
        if verifier.is_symlink() or hashlib.sha256(verified_bytes).hexdigest() != pin['sha256']:
            raise RouterFailure('RELEASE_MEMBER_CHANGED')
        # Importing this bootstrap would allow an unchecked cache to execute
        # before it can reject __pycache__. Execute the exact verified source.
        namespace = {'__file__': str(verifier), '__name__': 'mac_verified_dispatch'}
        exec(compile(verified_bytes, str(verifier), 'exec'), namespace)
        namespace['verify_runtime'](source, manifest)
    except RouterFailure:
        raise
    except Exception as exc:
        # The verifier's Skip carries only a reason code. Other failures have
        # no file content or exception text in diagnostics.
        reason = str(exc) if type(exc).__name__ == 'Skip' else 'RELEASE_VERIFICATION_FAILED'
        raise RouterFailure(reason) from None


def target(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2 or argv[0] != '--hook':
        return None
    sys.path.insert(0, str(SCRIPTS))
    verify_source()
    try:
        import manage_install
    except Exception:
        raise RouterFailure('ROUTER_IMPORT_FAILED') from None
    try:
        return manage_install.route(argv[1])
    except Exception:
        raise RouterFailure('ROUTER_ROUTE_FAILED') from None


def main():
    try:
        routed = target()
    except RouterFailure as exc:
        hook = sys.argv[2] if len(sys.argv) == 3 and sys.argv[1] == '--hook' else None
        record_skip(hook, str(exc))
        print('mac-hook-entry: skipped ' + str(exc), file=sys.stderr)
        return 0
    if routed is None:
        print('mac-hook-entry: skipped unknown hook', file=sys.stderr)
        return 0
    script, arguments = routed
    sys.argv = [str(script), *arguments]
    runpy.run_path(str(script), run_name='__main__')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
