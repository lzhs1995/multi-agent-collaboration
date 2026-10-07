"""Resolve a shared Codex daemon caller from live process evidence, never focus.

No environment mutation or terminal operations. The thread ID is only a selector:
a unique same-user `codex resume <id>` client, executable, birth, TTY and cmux UUID
must agree. Ordinary clients retain the original identify/environment checks.
"""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


class IdentityError(RuntimeError):
    pass


class BsdInfo(ctypes.Structure):
    _fields_ = [('flags', ctypes.c_uint32), ('status', ctypes.c_uint32),
                ('xstatus', ctypes.c_uint32), ('pid', ctypes.c_uint32),
                ('ppid', ctypes.c_uint32), ('uid', ctypes.c_uint32),
                ('gid', ctypes.c_uint32), ('ruid', ctypes.c_uint32),
                ('rgid', ctypes.c_uint32), ('svuid', ctypes.c_uint32),
                ('svgid', ctypes.c_uint32), ('reserved', ctypes.c_uint32),
                ('comm', ctypes.c_char * 16), ('name', ctypes.c_char * 32),
                ('misc', ctypes.c_uint32 * 6), ('start_sec', ctypes.c_uint64),
                ('start_usec', ctypes.c_uint64)]


def _args(data):
    argc = ctypes.c_int.from_buffer_copy(data[:4]).value
    if not 1 <= argc <= 65536:
        raise IdentityError('invalid process arguments')
    offset = data.index(b'\0', 4) + 1
    while offset < len(data) and data[offset] == 0:
        offset += 1
    argv = []
    for _ in range(argc):
        end = data.index(b'\0', offset)
        argv.append(data[offset:end].decode())
        offset = end + 1
    env = {}
    for item in data[offset:].split(b'\0'):
        if not item:
            break
        key, sep, value = item.partition(b'=')
        if not sep or key in env:
            raise IdentityError('invalid process environment')
        env[key] = value
    # Credentials never leave this reader.
    return argv, {k: env[k.encode()].decode() for k in
                  ('CMUX_SURFACE_ID', 'CMUX_WORKSPACE_ID', 'CODEX_THREAD_ID') if k.encode() in env}


def process(pid, *, arguments=True):
    if sys.platform != 'darwin' or type(pid) is not int or pid <= 1:
        raise IdentityError('live Darwin process required')
    lib = ctypes.CDLL('/usr/lib/libproc.dylib')
    lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                ctypes.c_void_p, ctypes.c_int]
    def info():
        b = BsdInfo()
        if (lib.proc_pidinfo(pid, 3, 0, ctypes.byref(b), ctypes.sizeof(b)) != ctypes.sizeof(b)
                or b.pid != pid or b.status == 5 or not b.start_sec or b.uid != os.getuid()):
            raise IdentityError('process exited, foreign, or inaccessible')
        return {'pid': pid, 'ppid': b.ppid, 'birth': [b.start_sec, b.start_usec]}
    result = info()
    if arguments:
        size = os.sysconf('SC_ARG_MAX')
        if not 0 < size <= 2 * 1024 * 1024:
            raise IdentityError('unsupported argument buffer')
        buf, length = ctypes.create_string_buffer(size), ctypes.c_size_t(size)
        sysctl = ctypes.CDLL(None).sysctl
        sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
                          ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
        if sysctl((ctypes.c_int * 3)(1, 49, pid), 3, buf, ctypes.byref(length), None, 0) != 0:
            raise IdentityError('process arguments inaccessible')
        argv, env = _args(buf.raw[:length.value])
        executable = ctypes.create_string_buffer(4096)
        lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        if lib.proc_pidpath(pid, executable, len(executable)) <= 0:
            raise IdentityError('executable identity inaccessible')
        path = Path(executable.value.decode()).resolve(strict=True)
        if not argv:
            raise IdentityError('process has no argv')
        if path.name == 'codex' and (not Path(argv[0]).is_absolute()
                                     or Path(argv[0]).resolve(strict=True) != path):
            raise IdentityError('argv executable differs from kernel executable')
        st = path.stat()
        result.update(argv=argv, env=env, executable=str(path),
                      file_identity=[st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns])
    if info() != {k: result[k] for k in ('pid', 'ppid', 'birth')}:
        raise IdentityError('process changed during read')
    return result


def client_candidates():
    """Enumerate kernel executable paths; pgrep -x can omit live Darwin clients.

    ps is discovery only: process() still verifies executable, owner, birth and
    argv before any candidate can become an authenticated caller.
    """
    listing = subprocess.run(['/bin/ps', '-U', str(os.getuid()), '-o', 'pid=,comm='],
                             capture_output=True, text=True, timeout=5)
    if listing.returncode != 0:
        raise IdentityError('client inventory unavailable')
    candidates = []
    for line in listing.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or not fields[0].isdigit():
            raise IdentityError('malformed client inventory')
        if Path(fields[1]).name == 'codex':
            candidates.append(fields[0])
    if len(set(candidates)) != len(candidates):
        raise IdentityError('duplicate client inventory')
    return candidates


def collect(env):
    """Return None outside a managed daemon. Errors never fall back to focus."""
    if sys.platform != 'darwin' or not env.get('CODEX_THREAD_ID'):
        return None
    chain, pid, daemon = [], os.getppid(), None
    for _ in range(64):
        if pid <= 1:
            break
        item = process(pid)
        chain.append(item)
        argv = item['argv']
        if (Path(item['executable']).name == 'codex' and len(argv) > 1
                and argv[1] == 'app-server' and '--managed-daemon' in argv[2:]):
            daemon = item
            break
        pid = item['ppid']
    if daemon is None:
        return None
    session = str(uuid.UUID(env['CODEX_THREAD_ID']))
    tool_threads = [p['env']['CODEX_THREAD_ID'] for p in chain[:-1]
                    if p['env'].get('CODEX_THREAD_ID')]
    if not tool_threads or any(t != session for t in tool_threads):
        raise IdentityError('thread selector differs from tool ancestry')
    if any(env.get(k) != daemon['env'].get(k) for k in
           ('CMUX_SURFACE_ID', 'CMUX_WORKSPACE_ID')):
        raise IdentityError('tool environment differs from managed daemon')
    candidates = client_candidates()
    clients = []
    for candidate in candidates:
        p = process(int(candidate))
        argv = p['argv']
        if len(argv) >= 3 and argv[1:3] == ['resume', session]:
            if Path(p['executable']).name != 'codex':
                raise IdentityError('client executable mismatch')
            tty = subprocess.run(['/bin/ps', '-p', candidate, '-o', 'tty='],
                                 capture_output=True, text=True, timeout=5, check=True).stdout.strip()
            if not tty or tty in ('?', '??', '-'):
                raise IdentityError('client has no terminal')
            clients.append(dict(p, tty=tty))
    if len(clients) != 1:
        raise IdentityError('native session has no unique live resumed client')
    # Verify every ancestor as well as the selected client after the inventory.
    for item in chain + clients:
        if process(item['pid']) != {k: v for k, v in item.items() if k != 'tty'}:
            raise IdentityError('process identity drift')
    return {'session': session, 'daemon': daemon, 'client': clients[0], 'chain': chain}


def resolve(identity, tree, env, proof):
    if proof is None:
        return identity, env
    client, daemon = proof['client'], proof['daemon']
    rows = []
    for window in tree.get('windows', []):
        for ws in window.get('workspaces', []):
            for pane in ws.get('panes', []):
                for surface in pane.get('surfaces', []):
                    rows.append(dict(surface_ref=surface.get('ref'), surface_id=surface.get('id'),
                                     workspace_ref=ws.get('ref'), workspace_id=ws.get('id'),
                                     pane_ref=pane.get('ref'), pane_id=pane.get('id'),
                                     window_ref=window.get('ref'), surface_type=surface.get('type'),
                                     tty=surface.get('tty')))
    def by_env(process_env):
        matches = [r for r in rows if str(r['surface_id']).upper() ==
                   str(process_env.get('CMUX_SURFACE_ID', '')).upper()]
        if len(matches) != 1 or str(matches[0]['workspace_id']).upper() != str(
                process_env.get('CMUX_WORKSPACE_ID', '')).upper():
            raise IdentityError('process UUIDs disagree with live tree')
        return matches[0]
    source, caller = by_env(daemon['env']), by_env(client['env'])
    raw = identity.get('caller') or {}
    if any(raw.get(k) != source[k] for k in ('surface_ref', 'workspace_ref', 'pane_ref')):
        raise IdentityError('identify differs from daemon origin')
    tty_matches = [r for r in rows if r['tty'] == client['tty']]
    if len(tty_matches) != 1 or tty_matches[0] != caller or caller['surface_type'] != 'terminal':
        raise IdentityError('native client TTY missing, reused, or ambiguous')
    resolved_env = dict(env, **{k: client['env'][k] for k in
                               ('CMUX_SURFACE_ID', 'CMUX_WORKSPACE_ID')})
    return dict(identity, caller=caller), resolved_env


def public_proof(proof):
    if proof is None:
        return None
    p = proof['client']
    return dict(session=proof['session'], pid=p['pid'], birth=p['birth'], tty=p['tty'],
                executable=p['executable'], surface_uuid=p['env']['CMUX_SURFACE_ID'],
                daemon_pid=proof['daemon']['pid'],
                argv_sha256=hashlib.sha256(json.dumps(p['argv']).encode()).hexdigest())
