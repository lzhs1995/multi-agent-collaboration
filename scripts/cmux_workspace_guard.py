#!/usr/bin/env python3
"""Fail-closed same-workspace transport guard. No force/skip/environment bypass.

The caller comes from cmux identify, never the focused surface. UUIDs come from
the live tree. A per-caller scope file can narrow the user's designated peers.
This guards the supported transport, not arbitrary malicious code execution.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import uuid
import cmux_daemon_identity as daemon_identity
import cmux_identity_budget as identity_budget

CMUX = "/Applications/cmux.app/Contents/Resources/bin/cmux"
SCOPE_DIR = Path.home() / ".local/state/multi-agent-collaboration/workspace-scope"


class WorkspaceScopeError(RuntimeError):
    pass


def deny(reason):
    raise WorkspaceScopeError("WORKSPACE_SCOPE_DENIED: " + reason)


def uuid_value(value):
    try:
        return str(uuid.UUID(value)).upper()
    except (ValueError, TypeError, AttributeError):
        deny("missing or invalid UUID")


def resolve_snapshot(identity, tree, target, *, env=None, expected=None):
    """Pure validation; no I/O or mutation. Ambiguous/missing identities reject."""
    env = {} if env is None else env
    caller = identity.get("caller")
    if not isinstance(caller, dict) or not caller.get("surface_ref"):
        deny("live caller missing; focused identity is never a fallback")
    rows = []
    for window in tree.get("windows", []):
        for workspace in window.get("workspaces", []):
            for pane in workspace.get("panes", []):
                for surface in pane.get("surfaces", []):
                    rows.append({
                        "surface_ref": surface.get("ref"),
                        "surface_uuid": surface.get("id"),
                        "workspace_ref": workspace.get("ref"),
                        "workspace_uuid": workspace.get("id"),
                        "pane_uuid": pane.get("id"),
                        "pane_ref": pane.get("ref"),
                        "surface_type": surface.get("type"),
                        "dock_scope": surface.get("dock_scope"),
                    })
    def one(selector):
        matches = [r for r in rows if selector in (r["surface_ref"], r["surface_uuid"])
                   or (isinstance(selector, str) and isinstance(r["surface_uuid"], str)
                       and selector.upper() == r["surface_uuid"].upper())]
        if len(matches) != 1:
            deny("surface missing or ambiguous: " + str(selector))
        r = dict(matches[0])
        if r["dock_scope"] == "global":
            deny("global dock is not a workspace member: " + str(selector))
        for k in ("workspace_uuid", "surface_uuid", "pane_uuid"):
            r[k] = uuid_value(r[k])
        return r
    me, peer = one(caller["surface_ref"]), one(target)
    if any(caller.get(k) != me[k] for k in ("workspace_ref", "pane_ref")):
        deny("caller/tree disagreement")
    for env_key, key in (("CMUX_WORKSPACE_ID", "workspace_uuid"),
                         ("CMUX_SURFACE_ID", "surface_uuid")):
        if env.get(env_key) and uuid_value(env[env_key]) != me[key]:
            deny("environment/live caller disagreement: " + env_key)
    if me["workspace_uuid"] != peer["workspace_uuid"]:
        deny(f"caller {me['workspace_ref']} and target {peer['workspace_ref']} differ")
    if me["surface_uuid"] == peer["surface_uuid"] or me["pane_uuid"] == peer["pane_uuid"]:
        deny("executor must be another terminal side pane")
    if me["surface_type"] != "terminal" or peer["surface_type"] != "terminal":
        deny("caller and executor must be terminal surfaces")
    binding = {"caller_surface_uuid": me["surface_uuid"],
               "caller_pane_uuid": me["pane_uuid"],
               "workspace_uuid": me["workspace_uuid"],
               "target_surface_uuid": peer["surface_uuid"],
               "target_pane_uuid": peer["pane_uuid"],
               "caller_surface_ref": me["surface_ref"],
               "target_surface_ref": peer["surface_ref"],
               "workspace_ref": me["workspace_ref"]}
    for key, value in (expected or {}).items():
        if key not in binding or uuid_value(value) != uuid_value(binding[key]):
            deny("designated or previously bound identity changed: " + key)
    return binding


def _read_json_command(*args):
    try:
        result = subprocess.run([CMUX, *args], capture_output=True, text=True,
                                timeout=identity_budget.timeout(), check=True)
        identity_budget.check()
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            deny("identity response is not an object")
        return value
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        deny("live identity unavailable: " + type(exc).__name__)


_UNCOLLECTED = object()


def caller_snapshot(*, collector=None, initial_proof=_UNCOLLECTED):
    """Resolve caller once and recheck the process proof around live cmux reads."""
    try:
        collect = collector or (lambda: daemon_identity.collect(os.environ))
        proof = collect() if initial_proof is _UNCOLLECTED else initial_proof
        identity = _read_json_command("identify", "--json")
        tree = _read_json_command("tree", "--all", "--json", "--id-format", "both")
        resolved, env = daemon_identity.resolve(identity, tree, os.environ, proof)
        if proof is not None:
            again = collect()
            final_identity = _read_json_command("identify", "--json")
            final_tree = _read_json_command("tree", "--all", "--json", "--id-format", "both")
            final, final_env = daemon_identity.resolve(final_identity, final_tree, os.environ, again)
            if again != proof or final['caller'] != resolved['caller'] or final_env != env:
                deny("native caller changed during resolution")
            # Last process read follows the last cmux observation.
            if collect() != proof:
                deny("native caller changed at final check")
            return final, final_tree, final_env, daemon_identity.public_proof(proof)
        return resolved, tree, env, None
    except (daemon_identity.IdentityError, OSError, ValueError, subprocess.SubprocessError) as exc:
        deny("native caller unavailable: " + str(exc))


def require_same_workspace(target, *, expected=None):
    identity, tree, env, _proof = caller_snapshot()
    binding = resolve_snapshot(identity, tree, target, env=env, expected=expected)
    scope_path = SCOPE_DIR / (binding["caller_surface_uuid"] + ".json")
    if scope_path.exists():
        try:
            scope = json.loads(scope_path.read_text())
            if scope.get("version") != 1:
                deny("invalid designated scope version")
            if uuid_value(scope["caller_surface_uuid"]) != binding["caller_surface_uuid"]:
                deny("designated caller mismatch")
            if uuid_value(scope["workspace_uuid"]) != binding["workspace_uuid"]:
                deny("user-designated workspace differs from live caller; no relocation or fallback")
            targets = scope["target_surface_uuids"]
            if not isinstance(targets, list) or not targets:
                deny("empty designated target set")
            if binding["target_surface_uuid"] not in [uuid_value(x) for x in targets]:
                deny("target is not the user-designated executor")
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            deny("unreadable designated scope: " + type(exc).__name__)
    return binding


def _verified_helper_command(command):
    """只放行单条、完整同版固定路由；不接受 shell 或环境覆盖。"""
    if any(char in command for char in ('`', '$', '\n', '\r')):
        return False
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|()<>')
        lexer.whitespace_split = True
        tokens = list(lexer)
        if any(token and all(char in ';&|()<>' for char in token) for token in tokens):
            return False
        if tokens[:1] == ['rtk']:
            tokens = tokens[1:]
            if tokens[:1] == ['proxy']:
                tokens = tokens[1:]
        if (len(tokens) < 2 or Path(tokens[0]).name != 'cmux-agent'
                or tokens[1] not in ('ask', 'send', 'broadcast', 'reconcile')):
            return False
        selected = shutil.which(tokens[0]) if tokens[0] == 'cmux-agent' else tokens[0]
        if not selected or not Path(selected).is_absolute() or not os.access(selected, os.X_OK):
            return False
        from cmux_evidence_io import read_bytes
        from render_cmux_agent import verify_route
        route = verify_route(read_bytes(selected), Path(__file__).resolve().parents[1])
        return Path(route['python']).resolve() == Path(sys.executable).resolve()
    except (OSError, ValueError, TypeError, KeyError):
        return False


def validate_command(command, *, _allow_helper=True):
    """Block raw outbound paths; reads remain available across workspaces.

    Input is sent only through cmux_bridge, whose runtime rechecks every send and
    key and addresses both workspace and surface by UUID. No message-prefix,
    --help, recovery authorization, or shell environment exemption.
    """
    command = str(command).replace("\\\n", "")
    if _allow_helper and _verified_helper_command(command):
        return True, "verified same-release helper; bridge rechecks every input"
    # Tokenize shell syntax so a quoted search pattern is not an invocation.
    # Inspect nested shell -c separately; --help never exempts a compound write.
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return False, "WORKSPACE_SCOPE_DENIED: unparseable shell input"
    blocked = False
    for i, token in enumerate(tokens):
        name = Path(token).name
        if name in ("cmux", "cmux-agent"):
            for arg in tokens[i + 1:]:
                if arg and all(c in ";&|()\n" for c in arg):
                    break
                if arg in ("send", "send-key", "send-text", "paste", "ask", "broadcast"):
                    blocked = True
                    break
        if name in ("sh", "bash", "zsh") and i + 2 < len(tokens):
            if tokens[i + 1] in ("-c", "-lc", "-ic"):
                ok, _ = validate_command(tokens[i + 2], _allow_helper=False)
                blocked = blocked or not ok
    if re.search(r"\b(?:surface|terminal)\.(?:send|send_text|send_key|write)\b", command):
        blocked = True
    if blocked:
        return False, ("WORKSPACE_SCOPE_DENIED: raw terminal input bypasses UUID binding; "
                       "use the installed cmux_bridge transport. No cross-workspace handshake, "
                       "legacy target fallback, broadcast, or force override.")
    return True, "no raw outbound terminal input"


def extract_command(payload):
    """Read shell payload variants without treating edited text as execution."""
    if not isinstance(payload, dict):
        deny("invalid hook payload")
    tool = str(payload.get("tool_name") or payload.get("toolName") or payload.get("name") or "").lower()
    if tool and not any(x in tool for x in ("bash", "shell", "exec_command")):
        return ""
    for entry in (payload.get("tool_input"), payload.get("toolInput"), payload.get("input"), payload):
        if isinstance(entry, dict):
            for key in ("command", "cmd"):
                value = entry.get(key)
                if isinstance(value, str) and value.strip():
                    return value
    return ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-target")
    parser.add_argument("--workspace-uuid")
    parser.add_argument("--target-uuid")
    args = parser.parse_args()
    try:
        if args.check_target:
            expected = {}
            if args.workspace_uuid:
                expected["workspace_uuid"] = args.workspace_uuid
            if args.target_uuid:
                expected["target_surface_uuid"] = args.target_uuid
            print(json.dumps(require_same_workspace(args.check_target, expected=expected)))
            return 0
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            deny("invalid hook payload")
        command = extract_command(payload)
        ok, message = validate_command(command)
        if not ok:
            deny(message.removeprefix("WORKSPACE_SCOPE_DENIED: "))
        print("{}")
        return 0
    except (WorkspaceScopeError, ValueError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
