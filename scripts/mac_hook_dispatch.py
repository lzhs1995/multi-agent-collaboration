#!/usr/bin/env python3
"""Stable MAC hook entry: resolve CURRENT on every call, then exec that release.

The installed copy lives outside the release tree at ``bin/mac_hook_dispatch.py``
so the registered command string never changes across releases. Claude runs a
handler defined identically in several settings files once; a release-pinned
path made the startup ``--settings`` copy and the hot-reloaded user copy differ,
so both ran. Keep the argv contract ``--hook <name>`` permanent: a process that
pinned its settings at startup keeps calling this path for its whole lifetime.

Standard library only; this file must not import from any release. Every
refusal exits 0 without reading stdin, and leaves a bounded diagnostic record.
"""
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
from pathlib import Path

RELEASES = Path('.local/share/multi-agent-collaboration/releases')
CURRENT = Path('.local/state/multi-agent-collaboration/CURRENT.json')
DIAGNOSTIC = Path('.local/state/multi-agent-collaboration/hook-dispatch-v1/LAST-SKIP.json')
NAME = re.compile(r'[a-z][a-z0-9_]{2,63}')
MAX_JSON_BYTES = 4 * 1024 * 1024
# Releases published before the in-release entry existed. Frozen: later
# routing lives in each release's scripts/mac_hook_entry.py instead.
LEGACY_DIRECT = frozenset({'cmux_workspace_guard', 'cmux_agent_panel_guard'})


class Skip(Exception):
    """A refusal reason code; never contains payload or file content."""


def _read(path, limit):
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
        raise Skip('NOT_REGULAR_OR_TOO_LARGE')
    return path.read_bytes()


def _pinned(source, manifest, relative):
    entry = manifest.get('files', {}).get(relative)
    path = source / relative
    if path.is_symlink():
        raise Skip('RELEASE_MEMBER_SYMLINK')
    if not isinstance(entry, dict) or not path.is_file():
        return None
    if hashlib.sha256(_read(path, MAX_JSON_BYTES)).hexdigest() != entry.get('sha256'):
        raise Skip('RELEASE_MEMBER_CHANGED')
    return path


def resolve(home, hook):
    """Return the argv of the current release entry for ``hook``, or raise Skip."""
    if not isinstance(hook, str) or not NAME.fullmatch(hook):
        raise Skip('BAD_HOOK_NAME')
    home = Path(home)
    try:
        current = json.loads(_read(home / CURRENT, MAX_JSON_BYTES))
        source, python = Path(current['source']), Path(current['python'])
        expected_manifest = current['manifest_sha256']
    except Skip:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        raise Skip('CURRENT_UNREADABLE') from None
    try:
        parts = source.relative_to(home / RELEASES).parts
    except ValueError:
        raise Skip('CURRENT_SOURCE_OUTSIDE_RELEASES') from None
    if (len(parts) != 2 or parts[1] != 'source' or parts[0] in ('.', '..')
            or '..' in source.parts or source.is_symlink() or not source.is_dir()):
        raise Skip('CURRENT_SOURCE_OUTSIDE_RELEASES')
    if not python.is_absolute() or not python.name.startswith('python') or not os.access(python, os.X_OK):
        raise Skip('CURRENT_PYTHON_INVALID')
    try:
        raw = _read(source.parent / 'MANIFEST.json', MAX_JSON_BYTES)
        manifest = json.loads(raw)
    except Skip:
        raise
    except (OSError, ValueError):
        raise Skip('RELEASE_MANIFEST_UNREADABLE') from None
    if hashlib.sha256(raw).hexdigest() != expected_manifest or not isinstance(manifest, dict):
        raise Skip('RELEASE_MANIFEST_MISMATCH')
    entry = _pinned(source, manifest, 'scripts/mac_hook_entry.py')
    if entry is not None:
        return [str(python), '-B', str(entry), '--hook', hook]
    # Rollback to a release without the in-release router.
    if hook in LEGACY_DIRECT:
        target = _pinned(source, manifest, 'scripts/' + hook + '.py')
        if target is not None:
            return [str(python), '-B', str(target)]
        raise Skip('RELEASE_HOOK_MISSING')
    wrapper = _pinned(source, manifest, 'scripts/cmux_workflow_advisory.py')
    if wrapper is None:
        raise Skip('RELEASE_HOOK_MISSING')
    return [str(python), '-B', str(wrapper), '--hook', hook + '.py']


def record_skip(home, hook, reason):
    """Atomically replace one private, bounded record; failures are ignored."""
    temporary = None
    try:
        path = Path(home) / DIAGNOSTIC
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = path.parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077):
            return
        body = dict(schema='mac-hook-dispatch-skip-v1', reason=reason,
                    hook=hook if isinstance(hook, str) and NAME.fullmatch(hook) else None,
                    at_epoch=time.time(), pid=os.getpid(), ppid=os.getppid())
        fd, temporary = tempfile.mkstemp(prefix='.skip-', dir=path.parent)
        with os.fdopen(fd, 'w') as stream:
            json.dump(body, stream)
        os.replace(temporary, path)
        temporary = None
    except Exception:
        pass
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    hook = argv[1] if len(argv) == 2 and argv[0] == '--hook' else None
    home = Path.home()
    try:
        target = resolve(home, hook)
    except Skip as exc:
        reason = str(exc)
    except Exception as exc:
        reason = 'DISPATCH_ERROR_' + type(exc).__name__
    else:
        try:
            # stdin is inherited untouched; the release reads the hook payload.
            os.execv(target[0], target)
        except OSError as exc:
            reason = 'EXEC_FAILED_' + type(exc).__name__
    record_skip(home, hook, reason)
    try:
        print('mac-hook-dispatch: skipped ' + reason, file=sys.stderr)
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
