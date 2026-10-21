#!/usr/bin/env python3
"""Manage only this checkout's skill links and hook commands. Dry-run by default."""
import argparse
import copy
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

from cmux_workflow_advisory import WORKFLOW_HOOKS

ROOT = Path(__file__).resolve().parents[1]
GUARDS = {
    "cmux_executor_closeout_guard": "PreToolUse",
    "cmux_workspace_guard": "PreToolUse",
    "cmux_agent_panel_guard": "PreToolUse",
    "cmux_handshake_receipt_guard": "PreToolUse",
    "cmux_consensus_round_guard": "PreToolUse",
    "cmux_lease_guard": "PreToolUse",
    "cmux_consensus_stop_guard": "Stop",
    "cmux_executor_idle_guard": "Stop",
    "cmux_native_delivery_guard": "PostToolUse",
    "cmux_supervisor_report_guard": "PostToolUse",
    "executor_reply_advisory": "PostToolUse",
    "callback_settlement_advisory": "PostToolUse",
    "handshake_advisory": "PostToolUse",
}
RETIRED_GUARDS = {"cmux_submit_confirmation_guard", "cmux_send_proof_stop_guard"}
OPTIONAL_GUARDS = {"cmux_executor_reask_stop_guard": "Stop"}

# --home selects the configuration being inspected, never the runtime of a
# diagnostic hook. Run each real entrypoint in its own disposable namespace.
# In particular, do not inherit a live client's CMUX/Goal/agent selectors or
# rewrite the operator's HOME to make an empty-payload probe pass.
DOCTOR_BOOTSTRAP = """
import os
from pathlib import Path
import runpy
import sys
diagnostic_root = Path(sys.argv[1]).resolve(strict=True)
Path.home = staticmethod(lambda: diagnostic_root)
os.chdir(diagnostic_root)
script, *arguments = sys.argv[2:]
sys.path.insert(0, str(Path(script).parent))
sys.argv = [script, *arguments]
# A real exec starts a new interpreter and loses the patched diagnostic HOME.
# Exercise the dispatcher and its actual router inside this already isolated
# process; preserve target arguments and SystemExit, without touching live HOME.
if Path(script).name == 'mac_hook_dispatch.py':
    def diagnostic_exec(executable, target):
        routed, *routed_arguments = target[2:]
        sys.path.insert(0, str(Path(routed).parent))
        sys.argv = [routed, *routed_arguments]
        runpy.run_path(routed, run_name='__main__')
        raise SystemExit(0)
    os.execv = diagnostic_exec
runpy.run_path(script, run_name='__main__')
"""


def _probe_argv(argv, diagnostic_root, *, home=None):
    """Exercise a stable dispatcher against an isolated, exact release copy."""
    argv = list(argv)
    diagnostic_root = Path(diagnostic_root).resolve()
    if len(argv) > 2 and Path(argv[2]).name == 'mac_hook_dispatch.py':
        import mac_hook_dispatch as dispatch
        owner = Path(home) if home is not None else release_home()
        if owner is None:
            raise RuntimeError('stable probe requires its explicit owner')
        raw = dispatch._read(owner / dispatch.CURRENT, dispatch.MAX_JSON_BYTES)
        current = json.loads(raw)
        source = Path(current['source'])
        dispatch.resolve(owner, argv[-1])
        copied = Path(diagnostic_root) / RELEASES / source.parent.name / 'source'
        shutil.copytree(source, copied, symlinks=True)
        shutil.copy2(source.parent / 'MANIFEST.json', copied.parent / 'MANIFEST.json')
        current['source'] = str(copied)
        current_path = Path(diagnostic_root) / dispatch.CURRENT
        current_path.parent.mkdir(parents=True, exist_ok=True)
        current_path.write_text(json.dumps(current))
        target = dispatcher_path(diagnostic_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(argv[2], target)
        argv[2] = str(target)
    return argv


def doctor_probe(name, *, home=None, argv=None):
    argv = shlex.split(command(name)) if argv is None else list(argv)
    environment = {key: os.environ[key] for key in
                   ('PATH', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ', 'SYSTEMROOT')
                   if key in os.environ}
    result = dict(benignExitZero=False, probeScope='isolated-empty-payload',
                  liveInvocation=False)
    if len(argv) > 2 and Path(argv[2]).name == 'mac_hook_dispatch.py':
        result['execScope'] = 'isolated-in-process-real-entrypoints'
    try:
        with tempfile.TemporaryDirectory(prefix='mac-doctor-') as diagnostic_root:
            argv = _probe_argv(argv, diagnostic_root, home=home)
            run = subprocess.run(
                [sys.executable, '-I', '-B', '-c', DOCTOR_BOOTSTRAP,
                 diagnostic_root, *argv[2:]],
                input='{}', text=True, capture_output=True, timeout=15,
                cwd=diagnostic_root, env=environment)
            result.update(benignExitZero=run.returncode == 0, returncode=run.returncode)
            record = Path(diagnostic_root) / '.local/state/multi-agent-collaboration/hook-dispatch-v1/LAST-SKIP.json'
            if record.is_file():
                result.update(benignExitZero=False, skip_reason=json.loads(record.read_bytes())['reason'])
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        result['error_type'] = type(exc).__name__
    return result


DISPATCHER = Path('.local/share/multi-agent-collaboration/bin/mac_hook_dispatch.py')
RELEASES = Path('.local/share/multi-agent-collaboration/releases')
# Exact reviewed predecessor bytes, never a mutable owner/header claim.
# 6115fba6b5f709fc179b774120b19fdfe750d392; 1bf770adae69161c3b3a7c4b8410e9a6942bdf78.
APPROVED_DISPATCHERS = frozenset({
    '1e1104958d0715aa0c346b4d186a2c1d85cf6b2f908f9489f19f00723856aa19',
    'a2e0b9b94d41dc4659df269b0b33bd84c39c77d2959c90ec4bd1807158973f48',
})


def route(name, root=None):
    """The one guard routing table, shared by both command forms."""
    if name not in set(GUARDS) | set(OPTIONAL_GUARDS):
        return None
    scripts = Path(root or ROOT) / 'scripts'
    filename = name + '.py'
    if filename in WORKFLOW_HOOKS:
        return scripts / 'cmux_workflow_advisory.py', ['--hook', filename]
    return scripts / filename, []


def release_home(root=None):
    """HOME owning an immutable release checkout, else None (development checkout)."""
    root = Path(root or ROOT)
    parts = root.parts  # <home>/.local/share/multi-agent-collaboration/releases/<name>/source
    if len(parts) < 7 or parts[-1] != 'source' or Path(*parts[-6:-2]) != RELEASES:
        return None
    return Path(*parts[:-6])


def dispatcher_path(home):
    return Path(home) / DISPATCHER


def command(name, home=None):
    """Immutable releases register one release-independent handler per guard.

    A development checkout keeps its direct path so its own tests exercise
    that tree; it never relies on CURRENT.
    """
    owner = release_home()
    if owner is not None and (home is None or Path(home).resolve() == owner.resolve()):
        return shlex.join([sys.executable, '-B', str(dispatcher_path(owner)), '--hook', name])
    script, arguments = route(name)
    return shlex.join([sys.executable, '-B', str(script), *arguments])


def dispatcher_check(home):
    """Doctor view: installed bytes and what the real CURRENT resolves to now."""
    import mac_hook_dispatch as dispatch
    target = dispatcher_path(home)
    own = Path(__file__).resolve().with_name('mac_hook_dispatch.py').read_bytes()
    result = dict(path=str(target), present=target.is_file() and not target.is_symlink(),
                  benignExitZero=False)
    result['matches_release'] = result['present'] and target.read_bytes() == own
    try:
        argv = dispatch.resolve(home, 'cmux_lease_guard')
        result.update(resolves_to=argv[2], resolved=True)
    except dispatch.Skip as exc:
        result.update(resolved=False, skip_reason=str(exc))
    if result['present'] and result['matches_release'] and result['resolved']:
        result['probe'] = doctor_probe('cmux_lease_guard', home=home,
                                      argv=[sys.executable, '-B', str(target), '--hook', 'cmux_lease_guard'])
        result['benignExitZero'] = result['probe']['benignExitZero']
    return result


def _dispatcher_snapshot(directory, name):
    """Read a bounded regular file through the held, nonredirected parent."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        return dict(kind='absent')
    except OSError:
        raise RuntimeError('foreign hook dispatcher preserved') from None
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_size > 4 * 1024 * 1024
                or before.st_uid != os.getuid() or before.st_mode & 0o022):
            raise RuntimeError('foreign hook dispatcher preserved')
        raw = stream.read(4 * 1024 * 1024 + 1)
        after = os.fstat(stream.fileno())
        named = os.stat(name, dir_fd=directory, follow_symlinks=False)
    if (len(raw) != before.st_size or len(raw) > 4 * 1024 * 1024
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or (after.st_dev, after.st_ino) != (named.st_dev, named.st_ino)):
        raise RuntimeError('concurrent hook dispatcher preserved')
    return dict(kind='file', sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw),
                device=before.st_dev, inode=before.st_ino,
                mode=stat.S_IMODE(before.st_mode), uid=before.st_uid)


def _exchange_dispatcher(directory, old, new):
    """Exchange names atomically. Never fall back to destructive replace."""
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == 'darwin':
        operation = library.renameatx_np
    elif sys.platform.startswith('linux') and hasattr(library, 'renameat2'):
        operation = library.renameat2
    else:
        raise RuntimeError('preserving dispatcher upgrade unavailable on this platform')
    operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    operation.restype = ctypes.c_int
    # Both platforms use 2 for name exchange.
    if operation(directory, os.fsencode(old), directory, os.fsencode(new), 2):
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))


def _dispatcher_evidence(directory, name, body):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                 dir_fd=directory)
    with os.fdopen(fd, 'w') as stream:
        json.dump(body, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.fsync(directory)


def publish_dispatcher(home):
    """Publish before hook registration; upgrade only exact approved bytes.

    First publication is exclusive. Upgrades exchange names and retain the
    displaced inode plus intent/result evidence. The comparison is separate
    from exchange, NOT atomic CAS: a late foreign replacement may be displaced,
    but is preserved and detected. Never swap back or automatically roll back
    over another writer. Identical bytes reuse their existing inode.
    """
    source = Path(__file__).resolve().with_name('mac_hook_dispatch.py')
    raw = source.read_bytes()
    target = dispatcher_path(home)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    if target.parent.resolve() != target.parent or target.is_symlink():
        raise RuntimeError('symlinked hook dispatcher refused: ' + str(target))
    parent = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    parent_pin = os.fstat(parent)
    token = uuid.uuid4().hex
    pending = '.mac-hook-dispatch-' + token
    intent_name = pending + '.intent.json'
    result_name = pending + '.result.json'
    intent_written = False
    staged_pin = None
    try:
        before = _dispatcher_snapshot(parent, target.name)
        digest = hashlib.sha256(raw).hexdigest()
        if before.get('sha256') == digest:
            return dict(path=str(target), sha256=digest, changed=False)
        if before['kind'] != 'absent' and before.get('sha256') not in APPROVED_DISPATCHERS:
            raise RuntimeError('foreign hook dispatcher preserved: ' + str(target))
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fchmod(handle.fileno(), 0o644)
            os.fsync(handle.fileno())
        staged_pin = _dispatcher_snapshot(parent, pending)
        if _dispatcher_snapshot(parent, target.name) != before:
            raise RuntimeError('concurrent hook dispatcher preserved: ' + str(target))
        if before['kind'] == 'absent':
            # A concurrent creator must survive the exclusive link.
            try:
                os.link(pending, target.name, src_dir_fd=parent, dst_dir_fd=parent,
                        follow_symlinks=False)
            except FileExistsError:
                if _dispatcher_snapshot(parent, target.name).get('sha256') == digest:
                    return dict(path=str(target), sha256=digest, changed=False)
                raise RuntimeError('concurrent hook dispatcher preserved: ' + str(target)) from None
            os.fsync(parent)
        else:
            intent = dict(schema='mac-hook-dispatch-upgrade-v1', target=str(target),
                          displaced=str(target.parent / pending), before=before, staged=staged_pin,
                          atomic_cas=False, automatic_reverse_allowed=False,
                          parent_device=parent_pin.st_dev, parent_inode=parent_pin.st_ino)
            # From this point, every outcome retains the staging/displaced file.
            intent_written = True
            _dispatcher_evidence(parent, intent_name, intent)
            _exchange_dispatcher(parent, pending, target.name)
            observed = _dispatcher_snapshot(parent, target.name)
            displaced = _dispatcher_snapshot(parent, pending)
            current_parent = target.parent.lstat()
            conflict = (observed != staged_pin or displaced != before
                        or (current_parent.st_dev, current_parent.st_ino)
                        != (parent_pin.st_dev, parent_pin.st_ino))
            result = dict(intent, observed=observed, displaced_snapshot=displaced,
                          status='FOREIGN_CONFLICT_PRESERVED' if conflict else 'PUBLISHED')
            _dispatcher_evidence(parent, result_name, result)
            if conflict:
                raise RuntimeError('concurrent hook dispatcher preserved at ' + str(target.parent / pending))
        if _dispatcher_snapshot(parent, target.name).get('sha256') != digest:
            raise RuntimeError('concurrent hook dispatcher preserved: ' + str(target))
        return dict(path=str(target), sha256=digest, changed=True,
                    upgraded=before['kind'] != 'absent',
                    displaced=str(target.parent / pending) if intent_written else None,
                    intent=str(target.parent / intent_name) if intent_written else None,
                    result=str(target.parent / result_name) if intent_written else None,
                    atomic_cas=False)
    finally:
        if not intent_written and staged_pin is not None:
            try:
                pin = os.stat(pending, dir_fd=parent, follow_symlinks=False)
                if (pin.st_dev, pin.st_ino) == (staged_pin['device'], staged_pin['inode']):
                    os.unlink(pending, dir_fd=parent)
            except FileNotFoundError:
                pass
        os.close(parent)


def release_source(path, home):
    """Recognize only this package's exact, immutable release layout."""
    path = Path(path)
    base = Path(home) / '.local/share/multi-agent-collaboration/releases'
    try:
        parts = path.relative_to(base).parts
    except ValueError:
        return False
    return len(parts) == 2 and parts[0] not in ('.', '..') and parts[1] == 'source'


def owned_command(value, home):
    try:
        words = shlex.split(value)
    except (ValueError, TypeError):
        return False
    while words and words[0] in ('rtk', 'proxy'):
        words.pop(0)
    if not words or not Path(words.pop(0)).name.startswith('python'):
        return False
    while words and words[0] in ('-B', '-u'):
        words.pop(0)
    if not words:
        return False
    path = Path(words.pop(0))
    if path == dispatcher_path(home):
        # Exactly the stable form; tails and unknown names stay foreign.
        names = set(GUARDS) | RETIRED_GUARDS | set(OPTIONAL_GUARDS)
        return len(words) == 2 and words[0] == '--hook' and words[1] in names
    if path.name == 'cmux_workflow_advisory.py':
        # Do not consume similarly named foreign wrappers, malformed options,
        # shell tails or a wrapper invoked for a hook owned by another tool.
        if len(words) != 2 or words[0] != '--hook' or words[1] not in WORKFLOW_HOOKS:
            return False
    elif words or path.stem not in set(GUARDS) | RETIRED_GUARDS | set(OPTIONAL_GUARDS):
        return False
    if (not path.is_absolute() or '..' in path.parts or path.suffix != '.py'
            or path.parent.name != 'scripts'):
        return False
    source = path.parent.parent
    aliases = [Path(home) / client / 'skills/multi-agent-collaboration'
               for client in ('.agents', '.claude', '.codex')]
    return (source == ROOT or release_source(source, home)
            or source in aliases and release_source(source.resolve(), home))


def transform(doc, uninstall=False, *, home=None, executor_reask=False):
    result = copy.deepcopy(doc)
    hooks = result.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks must be an object")
    home = Path(home or Path.home())
    # Remove this package's historical registrations before adding exactly one
    # current entry. A same-named hook outside our release tree is foreign.
    for event, entries in list(hooks.items()):
        if not isinstance(entries, list):
            raise ValueError("hook event must be a list")
        cleaned = []
        for entry in entries:
            old = entry.get('hooks', [])
            kept = [h for h in old if not owned_command(h.get('command'), home)]
            if len(kept) == len(old) or kept:
                entry['hooks'] = kept
                cleaned.append(entry)
        hooks[event] = cleaned
    expected = dict(GUARDS)
    if executor_reask:
        expected.update(OPTIONAL_GUARDS)
    for name, event in expected.items():
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            raise ValueError("hook event must be a list")
        cmd = command(name, home)
        if not uninstall:
            item = {"type": "command", "command": cmd}
            if name == 'cmux_executor_idle_guard':
                item['timeout'] = 75  # task-scoped empty Stop reentry waits at most60s
            entries.append({"matcher": "*", "hooks": [item]})
    return result


def read(path):
    if path.is_symlink():
        raise ValueError("refusing symlinked configuration: " + str(path))
    raw = path.read_bytes() if path.exists() else None
    doc = json.loads(raw) if raw is not None else {}
    if not isinstance(doc, dict):
        raise ValueError("configuration must be an object")
    return raw, doc


def replace(path, old, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    if (path.read_bytes() if path.exists() else None) != old:
        raise RuntimeError("configuration changed concurrently; rerun at a quiet boundary")
    if old is not None:
        backup = path.with_name(path.name + ".multi-agent-backup-" + uuid.uuid4().hex)
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(old)
            handle.flush()
            os.fsync(handle.fileno())
    raw = (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode()
    fd, pending = tempfile.mkstemp(prefix=".multi-agent-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if (path.read_bytes() if path.exists() else None) != old:
            raise RuntimeError("configuration changed during write; refused replacement")
        os.replace(pending, path)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)


def skill_snapshot(path):
    """Fingerprint the whole old directory; rename preserves every resource."""
    if path.is_symlink():
        return ('link', os.readlink(path))
    if not path.exists():
        return ('absent',)
    if not path.is_dir():
        raise RuntimeError('foreign skill installation preserved: ' + str(path))
    entries = []
    for parent, dirs, files in os.walk(path, followlinks=False):
        for name in sorted(dirs + files):
            item = Path(parent) / name
            st = item.lstat()
            value = (os.readlink(item) if item.is_symlink() else
                     hashlib.sha256(item.read_bytes()).hexdigest() if item.is_file() else None)
            entries.append((str(item.relative_to(path)), st.st_ino, st.st_mode,
                            st.st_size, st.st_mtime_ns, value))
    return ('directory', sorted(entries))


def skill_plan(path, home):
    snapshot = skill_snapshot(path)
    if snapshot[0] == 'absent':
        return snapshot, False
    if snapshot[0] == 'link':
        source = path.resolve()
        if source == ROOT:
            return snapshot, True
        if release_source(source, home) and (source / 'SKILL.md').is_file():
            return snapshot, False
    elif snapshot[0] == 'directory':
        entry = path / 'SKILL.md'
        if entry.is_file() and not entry.is_symlink():
            body = entry.read_text()
            refs = [Path(p).parent for p in re.findall(r'\]\((/[^\n)]+/SKILL\.md)\)', body)
                    if release_source(Path(p).parent, home)]
            if (re.search(r'^name: multi-agent-collaboration\s*$', body, re.M)
                    and refs and len(set(refs)) == 1 and (refs[0] / 'SKILL.md').is_file()):
                return snapshot, False
    raise RuntimeError('foreign skill installation preserved: ' + str(path))


def replace_skill(path, snapshot, *, uninstall=False):
    if skill_snapshot(path) != snapshot:
        raise RuntimeError('skill changed concurrently; preserved: ' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if snapshot[0] != 'absent':
        backup = path.with_name(path.name + '.multi-agent-backup-' + uuid.uuid4().hex)
        path.rename(backup)
        # Catch an edit between the first comparison and rename before publishing.
        if skill_snapshot(backup) != snapshot:
            if not path.exists() and not path.is_symlink():
                backup.rename(path)
            raise RuntimeError('skill changed during migration; preserved backup')
    try:
        if not uninstall:
            path.symlink_to(ROOT, target_is_directory=True)
    except BaseException:
        if backup and not path.exists() and not path.is_symlink():
            backup.rename(path)
        raise
    return str(backup) if backup else None


def manage(home, mode, apply=False, executor_reask=False):
    if sys.version_info < (3, 10):
        raise RuntimeError("Python 3.10+ required; do not wire macOS Python 3.9")
    home = Path(home).expanduser().resolve()
    plans = []
    for client, filename in (("codex", "hooks.json"), ("claude", "settings.json")):
        config = home / ("." + client) / filename
        link = home / ("." + client) / "skills/multi-agent-collaboration"
        snapshot, owned = skill_plan(link, home)
        old, doc = read(config)
        new = transform(doc, mode == "uninstall", home=home,
                        executor_reask=executor_reask and client == 'claude')
        plans.append((config, link, snapshot, owned, old, doc, new))
    checks = {}
    # Check both clients before changing either one.
    for config, link, snapshot, owned, old, doc, new in plans:
        if read(config)[0] != old or skill_snapshot(link) != snapshot:
            raise RuntimeError('installation changed concurrently; rerun')
    stable = release_home() is not None and release_home().resolve() == home
    published = None
    if stable and mode == "install" and apply:
        # Publish before any configuration can name the stable path.
        published = publish_dispatcher(home)
    for config, link, snapshot, owned, old, doc, new in plans:
        expected = transform(doc, home=home, executor_reask=executor_reask and config.parent.name == '.claude')
        checks[str(config)] = {"linked": owned, "configured": doc == expected,
                               "changeNeeded": doc != new}
        if mode != "doctor" and apply:
            if doc != new:
                replace(config, old, new)
            if mode == "install" and not owned:
                checks[str(config)]['skillBackup'] = replace_skill(link, snapshot)
            elif mode == "uninstall" and snapshot[0] != 'absent':
                checks[str(config)]['skillBackup'] = replace_skill(link, snapshot, uninstall=True)
    if mode == "doctor":
        for name in dict(GUARDS, **(OPTIONAL_GUARDS if executor_reask else {})):
            checks[name] = doctor_probe(name)
        if stable:
            checks['stable_dispatcher'] = dispatcher_check(home)
        ok = all(v.get("benignExitZero", v.get("linked") and v.get("configured")) for v in checks.values())
        # CURRENT-based releases have two independent launchd jobs. Checking
        # hooks alone missed a scheduler still pinned to the previous release.
        from release_jobs import check as check_jobs
        jobs = check_jobs(home)
        checks['release_jobs'] = jobs
        ok = ok and jobs['ok']
    else:
        ok = True
    return {"ok": bool(ok), "mode": mode, "applied": apply and mode != "doctor",
            "checkout": str(ROOT), "checks": checks, "dispatcher_published": published,
            "scope": "configuration and isolated benign execution, not live client reload proof"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("install", "doctor", "uninstall"))
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--executor-reask", action="store_true", help="explicitly enable user-authorized 60-second reasks for Claude")
    args = parser.parse_args()
    try:
        result = manage(args.home, args.mode, args.apply, args.executor_reask)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["ok"] else 2)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(2)
