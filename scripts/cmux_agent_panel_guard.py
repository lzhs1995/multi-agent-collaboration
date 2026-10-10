#!/usr/bin/env python3
"""
Guard cmux multi-agent panel creation.

Purpose:
- Reuse an existing context-bearing executor session whenever one exists.
- Block direct agent CLI launches that bypass the harness reuse/authorization
  gate, including launches pasted into a newly-created terminal surface.
- New executor agent panels must be side split panels, not tabs.
- Block `cmux new-surface` by default because agent creation is often split
  across multiple shell commands and a per-command hook cannot see future sends.
- Allow explicit non-agent terminal/browser surface creation.

This script can run as a Codex/Claude PreToolUse hook (JSON on stdin) or as a
dry-run checker with `--check-command`.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
from typing import Any
from cmux_workspace_guard import validate_command as validate_workspace_command


AGENT_NAMES = ("claude", "codex", "opencode", "omo", "omx", "omc")
AGENT_LAUNCH_NAMES = AGENT_NAMES + (
    "claude-anthrop",
    "claude-kiro-economy",
    "claude-kiro-strict",
    "claude-pinned",
    "claude-rescue",
    "claude-safe-resume",
)
ALLOWED_DIRECTIONS = ("left", "right", "up", "down")
RECOVERY_AUTH_ENV = "CMUX_AGENT_SAME_SESSION_RECOVERY_AUTH"


def _flatten_json_strings(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(_flatten_json_strings(v))
    elif isinstance(value, list):
        for v in value:
            out.extend(_flatten_json_strings(v))
    return out


def _extract_command(payload: dict[str, Any]) -> str:
    """Best-effort extraction for Codex/Claude hook payload variants."""
    tool_name = str(
        payload.get("tool_name")
        or payload.get("toolName")
        or payload.get("name")
        or ""
    ).lower()
    if tool_name and not any(part in tool_name for part in ("bash", "exec_command", "shell")):
        return ""

    # Tool identity precedes field names: edits can also carry a command field.
    for container in (payload, payload.get("tool_input"), payload.get("toolInput"), payload.get("input")):
        if isinstance(container, dict):
            for key in ("command", "cmd"):
                candidate = container.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate

    # Last resort: scan all strings for a Bash command containing cmux.
    for text in _flatten_json_strings(payload):
        if "cmux" in text and ("new-surface" in text or "new-split" in text or "split-off" in text):
            return text
    return ""


def _normalize_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


def _contains_agent_launch(command: str) -> bool:
    # Match shell-ish launch forms, including `cmux send ... "claude\n"`.
    lowered = command.lower()
    for agent in AGENT_NAMES:
        if re.search(rf"(^|[;&|`\s\"']){re.escape(agent)}(\s|\\n|$|[\"'])", lowered):
            return True
    return False


def _text_starts_agent_launch(text: str) -> bool:
    """Return True when text executes an agent CLI rather than mentions one."""
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        tokens = text.split()
    if not tokens:
        return False

    # Shell wrappers commonly used to start a long-lived CLI process.
    while tokens and tokens[0] in {"exec", "command", "nohup", "rtk"}:
        tokens = tokens[1:]
    if tokens and tokens[0] == "env":
        tokens = tokens[1:]
        while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
            tokens = tokens[1:]
        while tokens and tokens[0] in {"exec", "command", "nohup", "rtk"}:
            tokens = tokens[1:]
    if not tokens:
        return False
    return os.path.basename(tokens[0]).lower() in AGENT_LAUNCH_NAMES


def _python_document_data(command: str) -> str:
    """Exclude literal document text in a single quoted Python stdin program.

    Newlines inside Python strings are not shell command boundaries. Only an
    exact, unexpanded Python heredoc is recognized here; shell heredocs,
    pipelines, unknown headers and code that can evaluate/launch commands keep
    the conservative existing scan. This changes classification data only.
    """
    pattern = re.compile(
        r"(?m)^(?P<header>[^\r\n]*?)<<\s*(?P<quote>['\"])"
        r"(?P<delimiter>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)[ \t]*\r?\n"
        r"(?P<body>.*?)\r?\n(?P=delimiter)(?:\r?\n|$)", re.S,
    )
    executable_calls = {
        "eval", "exec", "compile", "__import__", "getattr", "setattr",
        "globals", "locals", "vars", "startfile",
        "system", "popen", "Popen", "run", "call", "check_call", "check_output",
        "execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp",
        "execlpe", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv",
        "spawnve", "spawnvp", "spawnvpe", "posix_spawn", "posix_spawnp",
        "create_subprocess_exec", "create_subprocess_shell",
    }
    # This is a document-update exception, not a general Python interpreter.
    # Unknown imports and executable references (including aliases) retain the
    # old scan. The original update uses only these standard-library modules.
    document_modules = {"pathlib", "datetime", "json", "hashlib", "os"}

    def replace(match: re.Match[str]) -> str:
        try:
            argv = shlex.split(match["header"], posix=True)
            if argv[:1] == ["rtk"]:
                argv = argv[1:]
                if argv[:1] == ["proxy"]:
                    argv = argv[1:]
            if (len(argv) < 2
                    or not re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", os.path.basename(argv[0]))
                    or any(c in argv[0] for c in "$`\\\r\n")
                    or argv[-1] != "-"
                    or any(v not in ("-B", "-u", "-I", "-E", "-s", "-S") for v in argv[1:-1])):
                return match[0]
            body = match["body"]
            tree = ast.parse(body)
        except (SyntaxError, ValueError, RecursionError):
            return match[0]
        nodes = list(ast.walk(tree))
        for node in nodes:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = ([node.module or ""] if isinstance(node, ast.ImportFrom)
                           else [item.name for item in node.names])
                if (any(m not in document_modules for m in modules)
                        or isinstance(node, ast.ImportFrom) and node.level
                        or any(item.name in executable_calls for item in node.names)):
                    return match[0]
            if isinstance(node, (ast.Name, ast.Attribute)):
                reference = node.id if isinstance(node, ast.Name) else node.attr
                if reference in executable_calls or reference.startswith("__"):
                    return match[0]
            if isinstance(node, ast.Call):
                name = (node.func.id if isinstance(node.func, ast.Name)
                        else node.func.attr if isinstance(node.func, ast.Attribute) else None)
                if name is None or name in executable_calls:
                    return match[0]
        # AST column offsets are UTF-8 bytes, including on non-ASCII documents.
        raw = body.encode("utf-8")
        starts, offset = [], 0
        for line in raw.split(b"\n"):
            starts.append(offset)
            offset += len(line) + 1
        masked = bytearray(raw)
        for node in nodes:
            if (isinstance(node, ast.JoinedStr)
                    or isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes))):
                begin = starts[node.lineno - 1] + node.col_offset
                end = starts[node.end_lineno - 1] + node.end_col_offset
                for index in range(begin, end):
                    if masked[index] not in (10, 13):
                        masked[index] = 32
        start = match.start("body") - match.start()
        stop = match.end("body") - match.start()
        return match[0][:start] + masked.decode("utf-8") + match[0][stop:]

    return pattern.sub(replace, command)


def _shell_starts_agent_launch(command: str) -> bool:
    """Detect a direct agent launch in any simple shell command segment."""
    for segment in re.split(r"(?:^|\s*(?:&&|\|\||;|\n)\s*)", _python_document_data(command)):
        if _text_starts_agent_launch(segment.strip()):
            return True
    return False


def _cmux_send_starts_agent_launch(command: str) -> bool:
    """Detect an agent CLI pasted into a terminal via cmux/cmux-agent send."""
    tokens = _normalize_tokens(command)
    for i, token in enumerate(tokens):
        if token != "send":
            continue

        # cmux-agent send <surface> <message...>
        if i > 0 and os.path.basename(tokens[i - 1]).lower() == "cmux-agent":
            if i + 2 < len(tokens):
                return _text_starts_agent_launch(" ".join(tokens[i + 2 :]))
            return False

        # cmux send [--surface <surface>] [--workspace <workspace>] [--] <text>
        later = tokens[i + 1 :]
        payload: list[str] = []
        skip_value = False
        for item in later:
            if skip_value:
                skip_value = False
                continue
            if item in {"--surface", "--workspace", "--window"}:
                skip_value = True
                continue
            if item == "--":
                continue
            if item.startswith("-") and not payload:
                continue
            payload.append(item)
        if payload:
            return _text_starts_agent_launch(" ".join(payload))
    return False


def _task_like_message(text: str) -> bool:
    """Recognize an executor task envelope, not a terminal callback."""
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("[CMUX-AGENT]"):
            continue
        return bool(re.match(r"^(?:TASK|TASK_PACK|TASK PACK)\s*[:=]", line, re.I))
    return False


def _raw_cmux_task_dispatch(command: str) -> bool:
    """Block task prompts that bypass cmux_bridge.submit_task_pack."""
    if "submit_task_pack" in command:
        return False
    tokens = _normalize_tokens(command)
    for i, token in enumerate(tokens):
        if token not in {"ask", "send"} or i == 0:
            continue
        owner = os.path.basename(tokens[i - 1]).lower()
        if owner == "cmux-agent":
            payload = " ".join(tokens[i + 2 :]) if i + 2 < len(tokens) else ""
            if _task_like_message(payload):
                return True
        if owner == "cmux":
            later = tokens[i + 1 :]
            payload = []
            skip_value = False
            for item in later:
                if skip_value:
                    skip_value = False
                    continue
                if item in {"--surface", "--workspace", "--window"}:
                    skip_value = True
                    continue
                if item == "--":
                    continue
                if item.startswith("-") and not payload:
                    continue
                payload.append(item)
            if _task_like_message(" ".join(payload)):
                return True
    if "submit_text" in command and _task_like_message(command):
        return True
    return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _authorized_same_session_recovery(command: str) -> tuple[bool, str]:
    """Allow one auditable resume of the exact persisted executor session.

    This is deliberately narrower than a normal agent launch. The command must
    be exactly ``env AUTH=... claude --resume SESSION_ID`` (optionally prefixed
    by ``rtk``), and the authorization must pin the existing Claude history
    bytes. Once the resumed session appends to its history, the authorization
    becomes unusable automatically.
    """
    tokens = _normalize_tokens(command)

    # The supervisor normally pastes the resume command into a newly-created,
    # empty side-split terminal. Bind the outer send to the one recovery
    # surface recorded in the same authorization.
    for i, token in enumerate(tokens):
        if (token != "send" or i == 0
                or os.path.basename(tokens[i - 1]).lower() != "cmux"):
            continue
        target = None
        payload: list[str] = []
        later = tokens[i + 1 :]
        cursor = 0
        while cursor < len(later):
            item = later[cursor]
            if item in {"--surface", "--workspace", "--window"}:
                if cursor + 1 >= len(later):
                    return False, "same-session recovery send has an incomplete target flag"
                if item == "--surface":
                    target = later[cursor + 1]
                cursor += 2
                continue
            if item == "--":
                cursor += 1
                continue
            if item.startswith("-") and not payload:
                cursor += 1
                continue
            payload = later[cursor:]
            break
        if target is None or not payload:
            return False, "same-session recovery send requires an explicit surface and payload"

        payload_command = " ".join(payload)
        inner_ok, inner_message = _authorized_same_session_recovery(payload_command)
        if not inner_ok:
            return False, inner_message
        payload_tokens = _normalize_tokens(payload_command)
        if payload_tokens and payload_tokens[0] == "rtk":
            payload_tokens = payload_tokens[1:]
        if not payload_tokens or payload_tokens[0] != "env":
            return False, "same-session recovery send payload is not explicit env form"
        auth_value = None
        for assignment in payload_tokens[1:]:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", assignment):
                break
            name, value = assignment.split("=", 1)
            if name == RECOVERY_AUTH_ENV:
                auth_value = value
        try:
            auth = json.loads(Path(auth_value or "").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False, "same-session recovery send authorization cannot be re-read"
        if auth.get("recoverySurfaceRef") != target:
            return False, "same-session recovery send target is not the authorized surface"
        return True, "exact persisted Claude session recovery send authorized"

    while tokens and tokens[0] in {"rtk", "command"}:
        tokens = tokens[1:]
    if not tokens or tokens[0] != "env":
        return False, "not an explicit same-session recovery"
    tokens = tokens[1:]

    environment: dict[str, str] = {}
    while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
        name, value = tokens.pop(0).split("=", 1)
        environment[name] = value

    if len(tokens) != 3:
        return False, "same-session recovery command must contain no extra arguments"
    launcher, option, session_id = tokens
    if os.path.basename(launcher).lower() != "claude" or option != "--resume":
        return False, "same-session recovery must use claude --resume"

    auth_value = environment.get(RECOVERY_AUTH_ENV)
    if not auth_value:
        return False, f"same-session recovery requires {RECOVERY_AUTH_ENV}"
    auth_path = Path(auth_value)
    if not auth_path.is_absolute() or not auth_path.is_file():
        return False, "same-session recovery authorization must be an existing absolute file"

    try:
        auth = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False, "same-session recovery authorization is unreadable or invalid"

    required = {
        "schemaVersion": 1,
        "action": "resume-existing-executor-session",
        "executor": "claude",
        "sessionId": session_id,
        "previousSurfaceMissing": True,
        "requireSameSession": True,
        "forbidReplacement": True,
        "oneTimeByHistoryPin": True,
    }
    if any(auth.get(key) != value for key, value in required.items()):
        return False, "same-session recovery authorization fields do not match the command"

    history_value = auth.get("historyPath")
    if not isinstance(history_value, str):
        return False, "same-session recovery authorization lacks historyPath"
    history_path = Path(history_value)
    if (not history_path.is_absolute()
            or history_path.name != f"{session_id}.jsonl"
            or not history_path.is_file()):
        return False, "same-session recovery history path is not the pinned session"
    try:
        history_bytes = history_path.stat().st_size
        history_sha256 = _sha256(history_path)
    except OSError:
        return False, "same-session recovery history cannot be measured"
    if (auth.get("historyBytes") != history_bytes
            or auth.get("historySha256") != history_sha256):
        return False, "same-session recovery history pin has changed"
    return True, "exact persisted Claude session recovery authorized"


def _has_cmux_new_surface(command: str) -> bool:
    tokens = _normalize_tokens(command)
    return any(tok == "new-surface" for tok in tokens) and any("cmux" in tok.lower() for tok in tokens)


def _has_cmux_new_split(command: str) -> bool:
    tokens = _normalize_tokens(command)
    return any(tok == "new-split" for tok in tokens) and any("cmux" in tok.lower() for tok in tokens)


def _has_cmux_split_off(command: str) -> bool:
    tokens = _normalize_tokens(command)
    return any(tok == "split-off" for tok in tokens) and any("cmux" in tok.lower() for tok in tokens)


def _new_split_direction(command: str) -> str | None:
    tokens = _normalize_tokens(command)
    for i, tok in enumerate(tokens):
        if tok == "new-split" and i + 1 < len(tokens):
            return tokens[i + 1]
    return None


def _split_off_direction(command: str) -> str | None:
    tokens = _normalize_tokens(command)
    for i, tok in enumerate(tokens):
        if tok == "split-off":
            # split-off flags may appear before the positional direction.
            for later in tokens[i + 1 :]:
                if later in ALLOWED_DIRECTIONS:
                    return later
    return None


def _has_help_flag(command: str) -> bool:
    tokens = _normalize_tokens(command)
    return "--help" in tokens or "-h" in tokens


def _new_surface_is_agent(command: str) -> bool:
    """True only when a new-surface command intends to create an AGENT panel.

    A bare `cmux new-surface --type browser|terminal` (used by cmux-artifact,
    cmux-workspace helper panes, markdown/HTML preview) is NOT an agent panel
    and must be allowed. We only block agent-session surfaces, --provider
    surfaces, or a new-surface chained with an agent CLI launch.

    Detection order matters:
    1. An explicit `--type agent-session` or `--provider` always means agent.
    2. An explicit non-agent `--type terminal|browser` is authoritative — do NOT
       fall back to scanning the command string for agent names (avoids false
       positives like `--title "codex output"` or `# run claude later`).
    3. Only when surface type is unspecified do we scan for a chained agent CLI
       launch (e.g. `new-surface && claude`).
    """
    tokens = _normalize_tokens(command)

    # The --provider flag only exists for agent surfaces
    if any(tok == "--provider" for tok in tokens):
        return True

    explicit_type = None
    if "--type" in tokens:
        idx = tokens.index("--type")
        if idx + 1 < len(tokens):
            explicit_type = tokens[idx + 1].lower()

    if explicit_type == "agent-session":
        return True
    # Explicit non-agent surface type is authoritative — trust it.
    if explicit_type in {"terminal", "browser"}:
        return False

    # Surface type unspecified: a chained agent CLI launch reveals intent.
    if _contains_agent_launch(command):
        return True
    return False


def validate_command(command: str) -> tuple[bool, str]:
    command = command.strip()
    if not command:
        return True, "no command"

    ok, message = validate_workspace_command(command)
    if not ok:
        return False, message

    # Help queries never create a panel; always allow.
    if _has_help_flag(command):
        return True, "help query allowed"

    # A vanished surface may be recovered only by resuming its exact persisted
    # session under a one-time, history-pinned authorization. This is session
    # reuse, not replacement-session creation.
    recovery_ok, recovery_message = _authorized_same_session_recovery(command)
    if recovery_ok:
        return True, recovery_message

    # Check launches before panel operations so a compound create-and-launch
    # command cannot bypass executor reuse.
    if _shell_starts_agent_launch(command) or _cmux_send_starts_agent_launch(command):
        return False, (
            "Blocked: direct agent CLI launch would bypass existing-session reuse. "
            "Reuse the already handshaken, context-bearing executor with `cmux-agent ask/read`. "
            "A genuinely new executor is allowed only after the user explicitly confirms that no "
            "reusable executor can satisfy the task, using the harness double-authorization path."
        )

    if _raw_cmux_task_dispatch(command):
        return False, (
            "Blocked: executor task dispatch bypasses the finalized task-pack contract. "
            "Use cmux_bridge.submit_task_pack with required_skill, a fresh completion "
            "nonce, callback_target, and require_confirmed completion delivery."
        )

    if _has_cmux_new_split(command):
        direction = _new_split_direction(command)
        if direction not in ALLOWED_DIRECTIONS:
            return False, (
                "cmux new-split must specify one of left/right/up/down. "
                "Default to: cmux new-split right --surface <supervisor_surface> --focus true"
            )
        return True, "side split allowed"

    if _has_cmux_split_off(command):
        direction = _split_off_direction(command)
        if direction not in ALLOWED_DIRECTIONS:
            return False, "cmux split-off must move the executor to left/right/up/down"
        return True, "split-off allowed"

    # Only block new-surface when it intends to open an AGENT panel as a tab.
    # Bare browser/terminal/markdown new-surface (official cmux skills) is allowed.
    if _has_cmux_new_surface(command) and _new_surface_is_agent(command):
        return False, (
            "Blocked: do not create a new agent executor as a tab with `cmux new-surface`. "
            "Use a side split panel instead. Default command: "
            "`cmux new-split right --surface <supervisor_surface> --focus true`, "
            "then launch the requested agent CLI in that new surface. If the user specified a direction, "
            "use left/right/up/down as requested. Agent tabs have no bypass."
        )

    return True, "allowed"


def _hook_response(ok: bool, message: str) -> int:
    if ok:
        print(json.dumps({}))
        return 0
    print(
        "cmux multi-agent panel guard blocked this command.\n"
        f"{message}\n"
        "Policy: reuse the existing context-bearing executor; session creation is never a recovery strategy.",
        file=sys.stderr,
    )
    # Claude/Codex hooks both treat non-zero as blocking in strict PreToolUse flows;
    # exit 2 is the conventional blocking code for Claude Code.
    return 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-command", help="Dry-run a shell command instead of reading hook JSON")
    parser.add_argument("--json", action="store_true", help="Emit JSON in dry-run mode")
    args = parser.parse_args()

    if args.check_command is not None:
        ok, message = validate_command(args.check_command)
        if args.json:
            print(json.dumps({"ok": ok, "message": message}, ensure_ascii=False))
        else:
            print(("ALLOW: " if ok else "BLOCK: ") + message)
        return 0 if ok else 2

    raw = sys.stdin.read()
    if not raw.strip():
        return _hook_response(True, "empty hook payload")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # Do not break unrelated hooks on malformed payloads.
        return _hook_response(True, "non-json hook payload")

    command = _extract_command(payload)
    ok, message = validate_command(command)
    return _hook_response(ok, message)


if __name__ == "__main__":
    raise SystemExit(main())
