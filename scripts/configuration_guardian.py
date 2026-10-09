#!/usr/bin/env python3
"""Reconcile owned hooks after provider switches; never run a transport command.

One launchd job follows a pinned CURRENT.json, watches configuration directories,
and checks every 60 seconds. API/provider values are neither copied nor logged.
The existing installation lock serializes this job with the shared installer.
"""
import argparse
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shlex
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time

LABEL = 'org.multi-agent-collaboration.configuration-guardian'
MAX_CONFIG_BYTES = 8 * 1024 * 1024
MAX_RELEASE_BYTES = 64 * 1024 * 1024


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def state_root(home):
    return Path(home) / '.local/state/multi-agent-collaboration'


def read_json(path):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        st = os.fstat(handle.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_CONFIG_BYTES:
            raise ValueError('configuration is not a bounded regular file')
        raw = handle.read(MAX_CONFIG_BYTES + 1)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('configuration must be an object')
    return raw, value


def atomic_json(path, value, expected=None, compare=False):
    """CAS against exact bytes; one bounded, private replacement, no backup growth."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError('symlinked configuration refused')
    def current():
        return read_json(path)[0] if path.exists() else None
    if compare and current() != expected:
        raise RuntimeError('configuration changed concurrently')
    fd, pending = tempfile.mkstemp(prefix='.guardian-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write((json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode())
            handle.flush()
            os.fsync(handle.fileno())
        if path.is_symlink() or compare and current() != expected:
            raise RuntimeError('configuration changed during replacement')
        os.replace(pending, path)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)


def load_release(home, manifest_path):
    """Authenticate the entire immutable release before importing its installer."""
    _raw, current = read_json(manifest_path)
    source = Path(current['source'])
    base = Path(home) / '.local/share/multi-agent-collaboration/releases'
    relative = source.relative_to(base)
    if (len(relative.parts) != 2 or relative.parts[-1] != 'source'
            or source.resolve() != source or '..' in source.parts):
        raise ValueError('CURRENT source is outside the immutable release layout')
    raw, manifest = read_json(source.parent / 'MANIFEST.json')
    if digest(raw) != current.get('manifest_sha256'):
        raise ValueError('release manifest hash mismatch')
    files = manifest.get('files')
    if not isinstance(files, dict) or not 1 <= len(files) <= 2048:
        raise ValueError('invalid release file manifest')
    total = 0
    for name, pin in files.items():
        path = source / name
        if (Path(name).is_absolute() or '..' in Path(name).parts
                or path.is_symlink() or not path.is_file()):
            raise ValueError('invalid release member')
        st = path.stat()
        total += st.st_size
        if total > MAX_RELEASE_BYTES or st.st_mode & 0o222:
            raise ValueError('release is writable or exceeds verification budget')
        if st.st_size != pin['bytes'] or digest(path.read_bytes()) != pin['sha256']:
            raise ValueError('release content mismatch: ' + name)
    required = {'scripts/configuration_guardian.py', 'scripts/manage_install.py'}
    if not required.issubset(files):
        raise ValueError('release lacks guardian/installer pins')
    if source / 'scripts/configuration_guardian.py' != Path(__file__).resolve():
        raise ValueError('guardian job is not from CURRENT release; reinstall the single job')
    if Path(current['python']).resolve() != Path(sys.executable).resolve():
        raise ValueError('guardian Python does not match CURRENT')
    spec = importlib.util.spec_from_file_location('guardian_approved_install', source / 'scripts/manage_install.py')
    manager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manager)
    return current, manager


def foreign_hooks(doc, manager, home):
    hooks = copy.deepcopy(doc.get('hooks', {}))
    if not isinstance(hooks, dict):
        raise ValueError('hooks must be an object')
    out = {}
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            raise ValueError('hook event must be a list')
        kept_entries = []
        for entry in entries:
            original = entry.get('hooks', [])
            kept = [h for h in original if not manager.owned_command(h.get('command'), home)]
            if kept or len(kept) == len(original):
                entry['hooks'] = kept
                kept_entries.append(entry)
        if kept_entries:
            out[event] = kept_entries
    return out


def normalized(doc, manager, home, reask):
    result = manager.transform(doc, home=home, executor_reask=reask)
    if {k: v for k, v in doc.items() if k != 'hooks'} != {k: v for k, v in result.items() if k != 'hooks'}:
        raise AssertionError('non-hook field changed')
    if foreign_hooks(doc, manager, home) != foreign_hooks(result, manager, home):
        raise AssertionError('foreign hook changed')
    return result


def reconcile_files(home, manager, apply):
    results = []
    for client, name, reask in [('claude', 'settings.json', True), ('codex', 'hooks.json', False)]:
        path = Path(home) / ('.' + client) / name
        # A missing live config can be repaired with hooks only, never from a
        # selected provider snapshot that might replace credentials or model.
        raw, doc = read_json(path) if path.exists() or path.is_symlink() else (None, {})
        new = normalized(doc, manager, home, reask)
        changed = doc != new
        result = dict(client=client, change_needed=changed, changed=False,
                      hooks_disabled=doc.get('disableAllHooks') is True)
        if changed and apply:
            atomic_json(state_root(home) / 'configuration-guardian-v1' / (client + '-previous-hooks.json'),
                        {'config_sha256': digest(raw) if raw is not None else None,
                         'hooks': doc.get('hooks'), 'at_epoch': time.time()})
            atomic_json(path, new, raw, compare=True)
            result['changed'] = True
        results.append(result)
    return results


def reconcile_profiles(home, manager, apply):
    database = Path(home) / '.cc-switch/cc-switch.db'
    if not database.exists():
        return dict(status='DATABASE_ABSENT', profiles=0, change_needed=0, changed=0)
    if database.is_symlink() or not database.is_file():
        raise ValueError('cc-switch database must be a regular nonsymlink file')
    # mode=rw never silently creates a replacement database. All profile writes
    # and the optional common-snippet cleanup commit in one transaction.
    db = sqlite3.connect(database.as_uri() + ('?mode=rw' if apply else '?mode=ro'), uri=True, timeout=2)
    try:
        db.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
        rows = db.execute("SELECT id, settings_config FROM providers WHERE app_type='claude' ORDER BY id").fetchall()
        plans, disabled = [], 0
        for key, raw in rows:
            if not isinstance(raw, str) or len(raw.encode()) > MAX_CONFIG_BYTES:
                raise ValueError('profile is not bounded JSON')
            doc = json.loads(raw)
            if not isinstance(doc, dict):
                raise ValueError('profile must be an object')
            new = normalized(doc, manager, home, True)
            disabled += doc.get('disableAllHooks') is True
            if doc != new:
                plans.append((key, raw, new))
        common = db.execute("SELECT value FROM settings WHERE key='common_config_claude'").fetchone()
        common_next = None
        if common and common[0]:
            doc = json.loads(common[0])
            if not isinstance(doc, dict):
                raise ValueError('common config must be an object')
            # Profiles own our eleven hooks. Avoid duplicate registrations from
            # a common snippet without toggling commonConfigEnabled or its env.
            if 'hooks' in doc:
                new = copy.deepcopy(doc)
                new['hooks'] = foreign_hooks(doc, manager, home)
                if new != doc:
                    common_next = new
        if apply:
            for key, raw, new in plans:
                count = db.execute("UPDATE providers SET settings_config=? WHERE id=? AND app_type='claude' AND settings_config=?",
                                   (json.dumps(new, ensure_ascii=False), key, raw)).rowcount
                if count != 1:
                    raise RuntimeError('profile compare-and-swap failed')
            if common_next is not None:
                count = db.execute("UPDATE settings SET value=? WHERE key='common_config_claude' AND value=?",
                                   (json.dumps(common_next, ensure_ascii=False), common[0])).rowcount
                if count != 1:
                    raise RuntimeError('common-hook compare-and-swap failed')
            db.commit()
        else:
            db.rollback()
        return dict(status='CHECKED', profiles=len(rows), change_needed=len(plans),
                    changed=len(plans) if apply else 0, hooks_disabled=disabled,
                    common_hook_cleanup=common_next is not None,
                    provider_selection_unchanged=True, non_hook_fields_preserved=True)
    finally:
        db.close()


def reconcile(home, manifest_path, apply=False):
    home = Path(home).expanduser().resolve()
    root = state_root(home)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(root / 'install.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return dict(status='INSTALLER_BUSY', changed=False, input_operations=0)
        current, manager = load_release(home, manifest_path)
        result = dict(status='RECONCILED', source=current['source'], at_epoch=time.time(),
                      apply=apply, input_operations=0, runtime_reload_proven=False)
        result['profiles'] = reconcile_profiles(home, manager, apply)
        result['files'] = reconcile_files(home, manager, apply)
        if any(x['hooks_disabled'] for x in result['files']) or result['profiles'].get('hooks_disabled'):
            result['status'] = 'HOOKS_DISABLED_EXTERNALLY'
        if apply:
            atomic_json(root / 'configuration-guardian-v1/STATUS.json', result)
        return result


def launchd_spec(home, source, python, manifest):
    home = Path(home)
    return dict(Label=LABEL, RunAtLoad=True, StartInterval=60, ThrottleInterval=10,
                ProgramArguments=[str(python), '-B', str(Path(source) / 'scripts/configuration_guardian.py'),
                                  'reconcile', '--home', str(home), '--manifest', str(manifest), '--apply'],
                WatchPaths=[str(home / '.claude'), str(home / '.codex'), str(home / '.cc-switch'), str(manifest)],
                StandardOutPath='/dev/null', StandardErrorPath='/dev/null', ProcessType='Background')


def install_job(home, manifest, apply=False):
    current, _manager = load_release(home, manifest)
    path = Path(home) / 'Library/LaunchAgents' / (LABEL + '.plist')
    spec = launchd_spec(home, current['source'], current['python'], manifest)
    raw = plistlib.dumps(spec, sort_keys=True)
    result = dict(status='JOB_PLANNED', plist=str(path), label=LABEL, interval_seconds=60)
    if not apply:
        return result
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError('symlinked launchd job refused')
    old = path.read_bytes() if path.exists() else None
    domain = 'gui/' + str(os.getuid())
    loaded = subprocess.run(['launchctl', 'print', domain + '/' + LABEL], capture_output=True, timeout=10).returncode == 0
    if old != raw:
        fd, pending = tempfile.mkstemp(prefix='.guardian-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            if (path.read_bytes() if path.exists() else None) != old:
                raise RuntimeError('launchd job changed concurrently')
            os.replace(pending, path)
        finally:
            if os.path.exists(pending):
                os.unlink(pending)
        if loaded:
            subprocess.run(['launchctl', 'bootout', domain + '/' + LABEL], check=True, capture_output=True, timeout=10)
            loaded = False
    if not loaded:
        subprocess.run(['launchctl', 'bootstrap', domain, str(path)], check=True, capture_output=True, timeout=10)
    result['status'] = 'JOB_REGISTERED'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['reconcile', 'install-job'])
    parser.add_argument('--home', type=Path, default=Path.home())
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    manifest = args.manifest or state_root(args.home) / 'CURRENT.json'
    try:
        result = (reconcile(args.home, manifest, args.apply) if args.mode == 'reconcile'
                  else install_job(args.home, manifest, args.apply))
    except Exception as exc:
        # Do not emit exception text: JSON/provider parsing errors can contain
        # credentials. Record only the class and keep the next reconcile alive.
        result = dict(status='RECONCILE_FAILED', error_type=type(exc).__name__,
                      at_epoch=time.time(), input_operations=0)
        if args.apply:
            atomic_json(state_root(args.home) / 'configuration-guardian-v1/STATUS.json', result)
    print(json.dumps(result, ensure_ascii=False))
    return 2 if result['status'] in ('RECONCILE_FAILED', 'HOOKS_DISABLED_EXTERNALLY') else 0


if __name__ == '__main__':
    raise SystemExit(main())
