#!/usr/bin/env python3
"""
cmux_bridge.py — thin wrapper around the verified cmux CLI primitives.

All functions raise RuntimeError on cmux CLI failure.
Surface refs are strings like "surface:17" or UUIDs.
"""
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

CMUX = os.environ.get(
    "CMUX_BIN",
    "/Applications/cmux.app/Contents/Resources/bin/cmux",
)

COLLABORATION_SKILL_PATH = Path(__file__).resolve().parents[1] / "SKILL.md"


class TaskPackContractError(RuntimeError):
    """Raised before delivery when a task pack can bypass callback discipline."""


def _first_payload_line(text):
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("[CMUX-AGENT]"):
            continue
        return line
    return ""


def _looks_like_task_dispatch(text):
    first = _first_payload_line(text)
    return bool(re.match(r"^(?:TASK|TASK_PACK|TASK PACK)\s*[:=]", first, re.I))


def validate_task_pack_contract(task_pack_path, prompt_text=None):
    """Validate the callback contract before a task reaches an executor."""
    path = Path(str(task_pack_path or ""))
    problems = []
    if not path.is_absolute() or not path.is_file():
        raise TaskPackContractError(
            "TASK_PACK_NOT_DISPATCHABLE: task_pack_path must be an existing absolute file"
        )
    try:
        pack = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TaskPackContractError(
            f"TASK_PACK_NOT_DISPATCHABLE: unreadable task pack: {exc}"
        ) from exc
    if not isinstance(pack, dict):
        raise TaskPackContractError("TASK_PACK_NOT_DISPATCHABLE: root must be an object")

    task_id = pack.get("task_id")
    nonce = pack.get("completion_nonce")
    report = pack.get("report")
    completion = pack.get("completion_callback")
    target = pack.get("callback_target")
    skill_value = pack.get("required_skill")
    skill = Path(skill_value) if isinstance(skill_value, str) else None

    if pack.get("draft") is not False:
        problems.append("draft must be false")
    if not isinstance(task_id, str) or not task_id:
        problems.append("task_id must be nonempty")
    if not isinstance(nonce, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.:-]{7,}", nonce or ""
    ):
        problems.append("completion_nonce must be concrete")
    if not isinstance(report, str) or not Path(report).is_absolute():
        problems.append("report must be absolute")
    if skill is None or not skill.is_absolute() or not skill.is_file():
        problems.append("required_skill must be an existing absolute file")
    elif skill.resolve() != COLLABORATION_SKILL_PATH.resolve():
        problems.append("required_skill must be the canonical collaboration skill")
    if not isinstance(target, str) or not re.fullmatch(r"surface:[0-9]+", target):
        problems.append("callback_target must be surface:<N>")
    expected_callbacks = {
        f"DONE|{task_id}|{nonce}|REPORT={report}",
        f"BLOCKED|{task_id}|{nonce}|REPORT={report}",
    }
    if completion not in expected_callbacks:
        problems.append("completion_callback does not exactly bind task, nonce, and report")
    if pack.get("completion_delivery") != {
        "transport": "cmux_bridge.submit_completion_callback",
        "require_confirmed": True,
    }:
        problems.append("completion_delivery must require confirmed bridge delivery")
    receipt = pack.get("completion_receipt")
    if not isinstance(receipt, str) or not Path(receipt).is_absolute():
        problems.append("completion_receipt must be absolute")
    elif Path(receipt).parent.resolve() != path.parent.resolve():
        problems.append("completion_receipt must be a sibling of the task pack")

    if prompt_text is not None:
        prompt = str(prompt_text)
        required_fragments = (
            f"TASK_PACK={path}",
            f"REQUIRED_SKILL={COLLABORATION_SKILL_PATH}",
            "READ_AND_OBEY_REQUIRED_SKILL_FIRST",
            str(completion),
            f"CALLBACK_TARGET={target}",
        )
        for fragment in required_fragments:
            if fragment not in prompt:
                problems.append(f"prompt missing required binding: {fragment}")

    if problems:
        raise TaskPackContractError(
            "TASK_PACK_NOT_DISPATCHABLE: " + "; ".join(problems)
        )
    return pack


def _run(*args, check=True, capture=True):
    """Run cmux with given args, return stdout string."""
    cmd = [CMUX] + list(args)
    result = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"cmux {' '.join(args)} failed (rc={result.returncode}):\n"
            f"{result.stderr.strip()}"
        )
    return result.stdout.strip() if capture else ""


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def ping():
    """Return True if cmux socket is reachable."""
    try:
        out = _run("ping", check=False)
        return "PONG" in out
    except Exception:
        return False


def whoami():
    """
    Return caller identity dict from `cmux identify --json`.

    Keys: workspace_ref, surface_ref, surface_type, pane_ref, window_ref,
          workspace_id (from env).
    """
    raw = _run("identify", "--json")
    data = json.loads(raw)
    caller = data.get("caller", {})
    return {
        "workspace_ref":  caller.get("workspace_ref", ""),
        "surface_ref":    caller.get("surface_ref", ""),
        "surface_type":   caller.get("surface_type", ""),
        "pane_ref":       caller.get("pane_ref", ""),
        "window_ref":     caller.get("window_ref", ""),
        "workspace_id":   os.environ.get("CMUX_WORKSPACE_ID", ""),
        "surface_id":     os.environ.get("CMUX_SURFACE_ID", ""),
    }


def identify_surface(surface_ref, workspace=None, window=None):
    """
    Return identity details for an explicit surface.

    This is used by the harness to prove the executor is in a different pane
    from the supervisor when a new side split panel is required.
    """
    args = ["identify", "--surface", surface_ref, "--json"]
    if workspace:
        args += ["--workspace", workspace]
    if window:
        args += ["--window", window]
    raw = _run(*args)
    data = json.loads(raw)
    caller = data.get("caller", {})
    return {
        "workspace_ref": caller.get("workspace_ref", ""),
        "surface_ref": caller.get("surface_ref", ""),
        "surface_type": caller.get("surface_type", ""),
        "pane_ref": caller.get("pane_ref", ""),
        "window_ref": caller.get("window_ref", ""),
    }


def surface_uuid_map():
    """Map surface_ref -> identity dict from `cmux tree --all --json --id-format both`.

    Refs (surface:N) are renumbered whenever surfaces open or close, so any
    artifact that outlives one poll must carry the stable UUID next to the ref.
    This is the same lesson the CCC watcher learned on 2026-08-20: joining
    tree/top snapshots on refs bound one surface's data to another's identity.

    Each value: {"surface_id", "workspace_id", "workspace_ref", "pane_ref",
    "title", "surface_type"}.  Returns {} when the tree is unreadable — callers
    must treat a missing UUID as "unknown", never guess from the ref.
    """
    try:
        raw = _run("tree", "--all", "--json", "--id-format", "both")
        data = json.loads(raw)
    except Exception:
        return {}
    out = {}
    for window in data.get("windows") or []:
        for workspace in window.get("workspaces") or []:
            ws_id = workspace.get("id", "")
            ws_ref = workspace.get("ref", "")
            for pane in workspace.get("panes") or []:
                for surface in pane.get("surfaces") or []:
                    ref = surface.get("ref", "")
                    sid = surface.get("id", "")
                    if not ref or not sid:
                        continue
                    out[ref] = {
                        "surface_id": sid,
                        "workspace_id": ws_id,
                        "workspace_ref": ws_ref,
                        "pane_ref": surface.get("pane_ref", ""),
                        "title": surface.get("title") or "",
                        "surface_type": surface.get("type") or "",
                    }
    return out


# ---------------------------------------------------------------------------
# Surface discovery
# ---------------------------------------------------------------------------

def list_surfaces(workspace=None):
    """
    Return list of surface dicts using `cmux list-panels` (preferred, workspace-wide).

    list-panels output: "* surface:17  terminal  [focused]  \"title\""
                        "  surface:25  agentSession  \"title\""

    Fallback: cmux list-pane-surfaces (pane-scope only).
    Each dict: {ref, title, selected, surface_type}.
    """
    # Try list-panels first (workspace-wide, shows type)
    args = ["list-panels"]
    if workspace:
        args += ["--workspace", workspace]
    raw = _run(*args, check=False)
    if raw and "surface:" in raw:
        return _parse_list_panels(raw)

    # Fallback: list-pane-surfaces (pane-scoped, text format)
    args2 = ["list-pane-surfaces"]
    if workspace:
        args2 += ["--workspace", workspace]
    raw2 = _run(*args2)
    return _parse_list_pane_surfaces(raw2)


def _parse_list_panels(raw):
    """Parse cmux list-panels output."""
    import re
    surfaces = []
    for line in raw.splitlines():
        line_stripped = line.strip()
        if not line_stripped or "surface:" not in line_stripped:
            continue
        focused = "[focused]" in line_stripped
        selected = focused  # focused == selected for list-panels
        # Extract surface ref
        m = re.search(r"(surface:\S+)", line_stripped)
        ref = m.group(1) if m else ""
        # Extract type (terminal, agentSession, browser, ...)
        type_m = re.search(r"surface:\S+\s+(\S+)", line_stripped)
        surface_type = type_m.group(1) if type_m else "unknown"
        surface_type = surface_type.replace("[focused]", "").strip()
        # Extract quoted title
        title_m = re.search(r'"([^"]*)"', line_stripped)
        title = title_m.group(1) if title_m else line_stripped
        surfaces.append({
            "ref": ref,
            "title": title,
            "selected": selected,
            "surface_type": surface_type,
            "is_terminal": surface_type == "terminal",
        })
    return surfaces


def _parse_list_pane_surfaces(raw):
    """Parse cmux list-pane-surfaces text output."""
    surfaces = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        selected = "[selected]" in line
        line = line.replace("[selected]", "").replace("*", "").strip()
        parts = line.split(None, 1)
        if not parts:
            continue
        ref = parts[0]
        title = parts[1].lstrip("⠂ ").strip() if len(parts) > 1 else ""
        surfaces.append({
            "ref": ref,
            "title": title,
            "selected": selected,
            "surface_type": "terminal",  # assumed
            "is_terminal": True,
        })
    return surfaces


def detect_provider(surface_dict):
    """
    Heuristic: infer agent provider from surface title and type.
    Returns 'claude', 'codex', 'opencode', or 'unknown'.
    """
    title = surface_dict.get("title", "").lower()
    stype = surface_dict.get("surface_type", "").lower()
    # agentSession surfaces have known providers
    if "claude" in title:
        return "claude"
    if "codex" in title:
        return "codex"
    if "opencode" in title:
        return "opencode"
    # Terminal surfaces running agent CLIs need screen-read to detect,
    # but we trust explicit --executor arg over heuristics
    return "unknown"


# ---------------------------------------------------------------------------
# Surface lifecycle
# ---------------------------------------------------------------------------

def spawn_executor(provider="codex", pane=None, cwd=None, focus=False, direction=None, supervisor_surface=None):
    """
    Spawn a terminal side split panel and launch the agent CLI inside it.

    agent-session surfaces (Codex native UI) do NOT support cmux send/read-screen.
    We use a plain terminal side panel and launch 'codex' (or other agent CLI)
    inside it so that send_text + read_screen work for harness I/O.

    Returns {ref, pane_ref, workspace_ref, provider}.
    """
    # New executor agents must be visible side panels, not tabs.
    # Split from the supervisor/caller surface unless a pane-only fallback is
    # explicitly needed by older cmux versions.
    direction = (direction or os.environ.get("MULTI_AGENT_PANEL_DIRECTION", "right")).strip().lower()
    if direction not in {"left", "right", "up", "down"}:
        direction = "right"

    supervisor_surface = supervisor_surface or os.environ.get("CMUX_SURFACE_ID", "")
    args = ["new-split", direction]
    if supervisor_surface:
        args += ["--surface", supervisor_surface]
    args += ["--focus", "true" if focus else "false"]
    out = _run(*args)
    # expected: "OK surface:24 workspace:7" for new-split
    parts = out.split()
    if len(parts) < 3 or parts[0] != "OK":
        raise RuntimeError(f"Unexpected new-split output: {out!r}")
    result = {
        "ref":           parts[1],
        "pane_ref":      "",
        "workspace_ref": parts[2],
        "provider":      provider,
        "mode":          "terminal+cli",
        "panel_mode":    f"side-split:{direction}",
    }
    return result


def launch_agent_in_terminal(surface_ref, provider="codex", prompt=None, cwd=None):
    """
    Launch an agent CLI inside a terminal surface.
    After this, the terminal is ready for send_text / read_screen.
    """
    import shutil
    if shutil.which(provider) is None:
        raise RuntimeError(f"Agent CLI '{provider}' not found on PATH")
    # Give the shell a moment to initialise
    import time as _time
    _time.sleep(1)
    cmd = provider
    if prompt:
        # non-interactive exec with initial prompt
        cmd = f"{provider} {prompt}"
    if cwd:
        import shlex
        cmd = f"cd {shlex.quote(str(cwd))} && {cmd}"
    # Start interactive session; handshake will confirm readiness
    send_text(surface_ref, cmd + "\n")


# ---------------------------------------------------------------------------
# Text I/O
# ---------------------------------------------------------------------------

class DispatchUnconfirmed(RuntimeError):
    """The TUI did not prove that a submitted prompt was consumed.

    Carries a `state` drawn from SUBMISSION_STATES. The message text stays
    human-readable and backward compatible (callers that only str() the
    exception keep working), but `state` is what a caller must branch on:
    only DELIVERY_QUEUED_AT_RECEIVER means "wait", and no state means
    "resend blindly".
    """

    def __init__(self, message, state=None):
        super().__init__(message)
        self.state = state


# ---------------------------------------------------------------------------
# Submission state taxonomy (R3 consensus, task multi-agent-skill-hardening-20260830)
#
# One reason string cannot carry five delivery states with different corrective
# actions. `SUBMISSION_ABORTED_BUSY` and `COMPOSE_OCCUPIED` remain distinct
# constants for receipt readability even though they share the same busy-buffer
# remedy; collapsing the states is what made a delivered message indistinguishable
# from one that was never pasted.
# ---------------------------------------------------------------------------

SUPERVISOR_DID_NOT_SUBMIT = "SUPERVISOR_DID_NOT_SUBMIT"
SUBMISSION_ABORTED_BUSY = "SUBMISSION_ABORTED_BUSY"
COMPOSE_OCCUPIED = "COMPOSE_OCCUPIED"
DELIVERY_UNVERIFIED_BY_DETECTOR = "DELIVERY_UNVERIFIED_BY_DETECTOR"
DELIVERY_QUEUED_AT_RECEIVER = "DELIVERY_QUEUED_AT_RECEIVER"

SUBMISSION_STATES = (
    SUPERVISOR_DID_NOT_SUBMIT,
    SUBMISSION_ABORTED_BUSY,
    COMPOSE_OCCUPIED,
    DELIVERY_UNVERIFIED_BY_DETECTOR,
    DELIVERY_QUEUED_AT_RECEIVER,
)

# Only this state means "wait"; every other state means "stop and fix". No
# state means "resend blindly".
SUBMISSION_STATES_MEANING_WAIT = (DELIVERY_QUEUED_AT_RECEIVER,)

# A receiver that queues an inbound message while inside a tool call renders a
# labelled pending region. The marker sitting there is proof of delivery, not
# proof of failure.
_PENDING_QUEUE_RE = re.compile(
    r"Messages? to be submitted after|"
    r"Press up to edit queued messages|"
    r"queued message",
    re.IGNORECASE,
)


def compose_contains(screen, marker):
    """Public: is `marker` still sitting in the active compose block?

    Exposed because both the harness and the guards must ask this question, and
    a private helper forced each caller to re-implement compose parsing. The
    bridge-test clear postcondition is the primary consumer: `ctrl+u` is only
    proven to have worked when this returns False on a fresh read.
    """
    return _prompt_block_pending(screen, marker)


# Empty-compose placeholders, per receiver UI. An empty box is not a blank line:
# each TUI renders prompt text inside it, so a substring search for "any text"
# would classify every idle box as occupied.
COMPOSE_PLACEHOLDERS = (
    "Ask Codex to do anything",
    "Ask Claude to do anything",
    "esc to interrupt",
    "? for shortcuts",
    "for shortcuts",
    "Try \"",
)

# Claude Code can render product-owned virtual suggestions inside an otherwise
# empty compose box. They look exactly like submitted Chinese prompts in a
# screen read, but are not user input and disappear when real text is entered.
# Keep this list exact and normalized: broad substring matching could erase a
# genuine prompt. These strings were confirmed by the user after a false
# COMPOSE_OCCUPIED diagnosis on the context-bearing Claude session.
CLAUDE_VIRTUAL_COMPOSE_PROMPTS = (
    "/multi-agent-collaboration",
    "continue",
    "read the executor report",
    "read the report",
    "review the consensus documents",
    "read the review and apply the changes",
    "check the integration validation artifact",
    "git diff scripts/thesis_format_adapter.py scripts/test_thesis_adapter_hardening.py",
    "/context",
    "任务中断了么？如果是就请继续，如果任务完成了务必在最后一句向我报告 ‘ 完成，建议检查 usage: /context’。如果任务没有中断就请继续，不要影响你的进度",
    "看一下 codex任务进展到哪了？下一步该干啥。详细计划给我。",
    "看一下 codex 那边进展到哪了？下一步该干啥。详细计划给我。",
    "看一下 codex 那边进展",
    "看一下 codex 那边收到了吗",
    "看一下 codex 那边收到没有",
    "看一下 codex 那边接下来要做什么",
    "继续，等 supervisor 的 nonce ACK",
    "继续握手，发送 ACK",
    "继续，等 supervisor 的下一个 dispatch",
    "继续等 codex 下一个派发",
    "继续，等 codex 的下一个 dispatch",
    "请澄清 203 Python 测试的位置和接口",
)

# Status/footer lines some TUIs render inside or directly under the compose box.
# They are chrome, not user content, but they are not fixed strings either, so
# they need patterns rather than substring removal.
_COMPOSE_CHROME_RE = re.compile(
    r"^\s*(?:gpt-[\w.\-]+|claude-[\w.\-]+|opus-[\w.\-]+|sonnet-[\w.\-]+)\b.*$|"
    r"^\s*\[(?:Opus|Claude|Sonnet|GPT)[^\]]*\].*$|"
    r"^\s*[^\n]*\bgit:\([^)]*\).*$|"
    r"^\s*\d+%\s+context.*$|"
    r"^\s*上下文\b.*$|"
    r"^\s*⏱️?\s+\d+[hm]\b.*$|"
    r"^\s*\d+\s+.*(?:CLAUDE|MCPs|hooks|钩子).*$|"
    r"^\s*▸\s+.+\(\d+/\d+\)\s*$|"
    r"^\s*[✓⏵⏩].*$|"
    r"^\s*[─│╭╮╰╯>\s?]*$",
    re.IGNORECASE,
)


def compose_block_text(screen):
    """Body of the CURRENT compose block, or None when no block is open.

    Uses the same last-block parse as ``_prompt_block_pending``: the final
    glyph block is live compose and an earlier one is transcript echo. Reusing
    that function's rule rather than re-deriving it is deliberate -- treating an
    earlier block as live was a real bug fixed once already, and a second parse
    would be free to regress independently.
    """
    block = []
    in_prompt = False
    for line in screen.splitlines():
        if _PROMPT_GLYPH_RE.match(line):
            in_prompt = True
            block = [_PROMPT_GLYPH_RE.sub("", line, count=1)]
            continue
        if _ACTIVITY_RE.match(line):
            in_prompt = False
            block = []
            continue
        if in_prompt:
            block.append(line)
    if not in_prompt:
        return None
    return "\n".join(block)


def compose_block_is_empty(screen):
    """True only when the current compose block holds no user content.

    Returns False when no block is open, because 'cannot see the box' must never
    read as 'the box is safe to overwrite'. A block containing only a known
    placeholder counts as empty.
    """
    body = compose_block_text(screen)
    if body is None:
        return False
    residual_lines = []
    for raw in body.splitlines():
        line = raw.strip(" \t\r\n│─╭╮╰╯")
        if not line:
            continue
        if _COMPOSE_CHROME_RE.match(line):
            continue
        # Remove any placeholder this line consists of. Longest-first, because
        # replacing "for shortcuts" before "? for shortcuts" leaves a stray "?"
        # that then reads as user content -- measured while building this.
        for ph in sorted(COMPOSE_PLACEHOLDERS, key=len, reverse=True):
            line = line.replace(ph, "")
        line = line.strip(" \t\r\n│─╭╮╰╯>?")
        if line:
            residual_lines.append(line)
    if not residual_lines:
        return True
    normalized = re.sub(r"\s+", "", "".join(residual_lines))
    virtual_prompts = {
        re.sub(r"\s+", "", prompt)
        for prompt in CLAUDE_VIRTUAL_COMPOSE_PROMPTS
    }
    if normalized in virtual_prompts:
        return True
    # Near its automatic compaction threshold, Claude Code renders `/compact`
    # as a dim product suggestion even though the editable compose buffer is
    # empty. Plain `cmux read-screen` loses that styling. Bind this exception to
    # the product's adjacent percentage banner so a real `/compact` command in
    # any other screen state remains occupied.
    if normalized == "/compact" and re.search(
        r"\b\d+%\s+until\s+auto-compact\b", screen, re.I
    ):
        return True
    # Claude's automatic retry UI may put its queue-control hint directly
    # after a confirmed virtual suggestion. The hint by itself still means an
    # unknown queued message and must remain occupied; only this exact pair is
    # a product-owned empty compose rendering.
    queue_hint = re.sub(r"\s+", "", "Press up to edit queued messages")
    return bool(
        normalized.endswith(queue_hint)
        and normalized[:-len(queue_hint)] in virtual_prompts)


def pending_queue_holds(screen, marker):
    """True when the receiver has queued the marker behind an active tool call.

    Positively detectable, so this state never needs a timeout to diagnose. The
    marker must appear at or after the pending-queue banner; a marker elsewhere
    on screen is a different state entirely.
    """
    lines = screen.splitlines()
    for index, line in enumerate(lines):
        if _PENDING_QUEUE_RE.search(line):
            if marker in "\n".join(lines[index:]):
                return True
    return False


def classify_submission_failure(screen, marker, submitted):
    """Map observable evidence onto exactly one submission state.

    `submitted` is whether the paste+enter pair actually ran. It is passed in
    rather than inferred because "never sent" and "sent but unverified" are
    indistinguishable from the screen alone, and that ambiguity is precisely
    what caused a valid ACK to be recorded as executor silence.
    """
    if not submitted:
        return SUPERVISOR_DID_NOT_SUBMIT
    if pending_queue_holds(screen, marker):
        return DELIVERY_QUEUED_AT_RECEIVER
    if _queued_or_active_input(screen):
        return COMPOSE_OCCUPIED
    if compose_contains(screen, marker):
        return SUBMISSION_ABORTED_BUSY
    return DELIVERY_UNVERIFIED_BY_DETECTOR


# Compose-prompt glyphs, by receiver UI. Claude renders ``❯`` (U+276F); Codex
# renders ``›`` (U+203A). The busy/compose detector is provider-agnostic by
# design -- it runs against whatever surface we are sending to -- so knowing only
# one glyph made it blind in one direction. Measured: a callback left unsubmitted
# in a Codex supervisor's compose box classified as
# DELIVERY_UNVERIFIED_BY_DETECTOR ("cannot tell") when the true state was
# definitively SUBMISSION_ABORTED_BUSY ("still in compose, never sent"). The same
# blindness would have made the bridge-test clear postcondition report
# clear_confirmed=true against a Codex receiver without ever verifying anything --
# a false green in the mechanism built to prevent false greens.
_PROMPT_GLYPH_RE = re.compile(r"^\s*(?:❯|›)(?:\s|$)")

# A structural activity line closes a compose block. Named once and shared, so
# the block parse cannot drift between callers: `compose_block_text` and
# `_prompt_block_pending` must agree on where the current block starts and ends,
# and two copies of this pattern would be free to diverge.
_ACTIVITY_RE = re.compile(
    r"^\s*(?:⏺|✻|✢|✳|✶|✽|◐|◑|◒|◓)(?:\s|$)|"
    r"^\s*•\s+(?:Edited|Ran|Read|Updated|Created|Deleted|Applied|Searched|Checked|Viewed|Wrote)(?:\s|$)"
)


def _prompt_block_pending(screen, marker):
    """Return True only when marker remains in the CURRENT compose block.

    Handles both receiver UIs; see ``_PROMPT_GLYPH_RE``. Only a glyph in leading
    position opens a block, so the same character quoted inside prose is not a
    boundary.

    "Current" means the last block on screen, which is the live compose box.
    An earlier glyph block is transcript: both UIs echo a submitted message with
    the same glyph they use for the input box, so a marker found there is proof
    of delivery, not of a stuck paste. An earlier version returned True as soon
    as a second block opened, which inverted exactly that case -- measured on a
    real callback that Codex had already received and was actively working on.
    """
    block = []
    in_prompt = False
    for line in screen.splitlines():
        if _PROMPT_GLYPH_RE.match(line):
            in_prompt = True
            block = [line]
            continue
        if _ACTIVITY_RE.match(line):
            in_prompt = False
            block = []
            continue
        if in_prompt:
            block.append(line)
    return in_prompt and marker in "\n".join(block)


def _submission_confirmed(screen, marker):
    """Require marker followed by a fresh assistant/spinner output block."""
    found = False
    for line in screen.splitlines():
        if found and _ACTIVITY_LINE_RE.match(line):
            return True
        if marker in line:
            found = True
    return False


_ACTIVITY_LINE_RE = re.compile(
    r"^\s*(?:⏺|✻|✢|✳|✶|✽|◐|◑|◒|◓)(?:\s|$)|"
    r"^\s*•\s+(?:Edited|Ran|Read|Updated|Created|Deleted|Applied|Searched|Checked|Viewed|Wrote)(?:\s|$)"
)


def _new_activity_after_submit(before, after):
    """Prove progress when the delivery marker scrolled out of view."""
    if before == after:
        return False
    before_lines = Counter(
        line for line in before.splitlines() if _ACTIVITY_LINE_RE.match(line)
    )
    after_lines = Counter(
        line for line in after.splitlines() if _ACTIVITY_LINE_RE.match(line)
    )
    return any(after_lines[line] > before_lines[line] for line in after_lines)


def _queued_or_active_input(screen):
    """Do not issue a blind second Enter while a queued/active task owns the TUI."""
    if re.search(r"Press up to edit queued messages", screen):
        return True
    # Claude uses the same glyph for active and completed summaries. Restrict
    # this guard to words that identify live work; "Churned/Brewed/Cooked"
    # summaries must not strand the next prompt in compose.
    active_words = (
        r"thinking|running|waiting|retrying|recording|checking|working|"
        r"processing|executing|loading|tool|bash:|api error"
    )
    return bool(
        re.search(
            rf"^[ \t]*(?:✻|✢|✳|✶|✽|◐|◑|◒|◓)[ \t]+.*(?:{active_words})\b",
            screen,
            re.I | re.M,
        )
    )


def send_text(surface, text):
    """
    Paste raw text to a surface without consumption confirmation.

    cmux interprets a literal ``\\n`` in the payload as an Enter event, which is
    useful for explicit shell startup commands. It is not valid evidence that a
    task or callback was consumed; use :func:`submit_text` for those messages.
    """
    _run("send", "--surface", surface, "--", text)


def send_key(surface, key):
    """
    Send a key event (e.g. 'enter', 'ctrl+c', 'ctrl+u') to a surface.
    """
    _run("send-key", "--surface", surface, "--", key)


def focus_surface(surface):
    """Focus the pane owning ``surface`` before sending terminal editing keys."""
    identity = identify_surface(surface)
    pane = identity.get("pane_ref")
    if pane:
        _run("focus-pane", "--pane", pane)


def clear_known_compose_by_delete(surface, compose):
    """Backspace one fingerprinted idle compose buffer, then let caller verify.

    Claude's multiline editor has ignored Escape, ctrl+u, and ctrl+c in real
    sessions. Its terminal also ignores forward Delete even after Home, while
    End+Backspace edits the actual buffer. The force-compose path already owns
    the exact buffer, so erase a bounded number of trailing cells. The
    subsequent screen read remains the acceptance authority.
    """
    focus_surface(surface)
    send_key(surface, "end")
    count = min(max(len(compose) + 64, 256), 4096)
    for _ in range(count):
        send_key(surface, "backspace")
    return count


def submit_text(surface, text, marker=None, confirm_lines=200, task_pack_path=None,
                force_compose=False):
    """Submit text with lowercase Enter and prove the TUI consumed it.

    ``cmux send`` only pastes text.  Submission is deliberately separate so a
    caller cannot mistake a successful paste for an executed prompt.  When a
    marker is supplied it must disappear from the active compose block -- the
    LAST prompt-glyph block on screen, either UI's glyph (see
    ``_PROMPT_GLYPH_RE``); an earlier such block is transcript echo and proves
    delivery rather than a stuck paste.  Marker-linked output or a new
    structural activity line relative to the
    pre-submit snapshot proves consumption when the marker has scrolled away.
    One bounded retry handles a known paste-without-submit event, but queued or
    active input is never double-submitted.
    """
    if task_pack_path is not None:
        pack = validate_task_pack_contract(task_pack_path, prompt_text=text)
        from availability_contract import require_action
        require_action(pack["task_id"], "dispatch", pack)
    elif _looks_like_task_dispatch(text):
        raise TaskPackContractError(
            "TASK_PACK_REQUIRED: task dispatches must use submit_task_pack with a "
            "finalized pack carrying required_skill and confirmed completion callback"
        )

    before = read_screen(surface, lines=confirm_lines) if (marker or force_compose) else ""
    if force_compose:
        # The harness enables this supervisor-owned compose replacement by
        # default for this user; direct library callers must opt in explicitly.
        compose = compose_block_text(before)
        if compose and not compose_block_is_empty(before):
            send_key(surface, "escape")
            time.sleep(float(os.environ.get("CMUX_AGENT_POST_SUBMIT_DELAY", "0.75")))
            cleared = read_screen(surface, lines=confirm_lines)
            if not compose_block_is_empty(cleared):
                # Claude's terminal composer may accept Escape without
                # editing the buffer.  The override is already explicit and
                # fingerprints the known buffer, so focus its pane and issue
                # one line-clear request before failing closed.
                focus_surface(surface)
                send_key(surface, "ctrl+u")
                time.sleep(float(os.environ.get("CMUX_AGENT_POST_SUBMIT_DELAY", "0.75")))
                cleared = read_screen(surface, lines=confirm_lines)
                if not compose_block_is_empty(cleared):
                    # Claude's multiline editor can also ignore ctrl+u.  At
                    # this point the operator explicitly owns the fingerprinted
                    # idle buffer and both gentler clears failed, so cancel that
                    # compose once.  Never use ctrl+c on an active command.
                    send_key(surface, "ctrl+c")
                    time.sleep(float(os.environ.get("CMUX_AGENT_POST_SUBMIT_DELAY", "0.75")))
                    cleared = read_screen(surface, lines=confirm_lines)
                    if not compose_block_is_empty(cleared):
                        clear_known_compose_by_delete(surface, compose)
                        time.sleep(float(os.environ.get("CMUX_AGENT_POST_SUBMIT_DELAY", "0.75")))
                        cleared = read_screen(surface, lines=confirm_lines)
                        if (
                            not compose_block_is_empty(cleared)
                            and _queued_or_active_input(cleared)
                        ):
                            raise DispatchUnconfirmed(
                                f"DISPATCH_UNCONFIRMED marker={marker or '<none>'} "
                                f"surface={surface} (forced compose discard did not clear)",
                                state=COMPOSE_OCCUPIED,
                            )
                        # An idle Claude virtual suggestion is rendered after
                        # the prompt glyph but is not part of the editable
                        # buffer. It survives every editing key and is replaced
                        # atomically by the first pasted character. The user has
                        # explicitly made force-compose authoritative, so after
                        # the bounded real-buffer erase we may paste through an
                        # idle residual rendering. Active/queued input remains
                        # protected by the branch above.
            before = cleared
    send_text(surface, text)
    time.sleep(float(os.environ.get("CMUX_AGENT_SUBMIT_DELAY", "0.25")))
    send_key(surface, "enter")
    time.sleep(float(os.environ.get("CMUX_AGENT_POST_SUBMIT_DELAY", "0.75")))
    if not marker:
        return {"confirmed": True, "retries": 0}

    screen = read_screen(surface, lines=confirm_lines)
    # A slow first render is not a failed delivery. Observe the same submission
    # once more; never paste or press Enter while its outcome is unknown.
    if (not _submission_confirmed(screen, marker)
            and not _prompt_block_pending(screen, marker)
            and not _new_activity_after_submit(before, screen)
            and not pending_queue_holds(screen, marker)):
        try:
            late_delay = float(os.environ.get("CMUX_AGENT_LATE_CONFIRM_DELAY", "3.0"))
        except ValueError:
            late_delay = 3.0
        if not math.isfinite(late_delay):
            late_delay = 3.0
        time.sleep(max(0.0, min(5.0, late_delay)))
        screen = read_screen(surface, lines=confirm_lines)
        if (not _prompt_block_pending(screen, marker) and not pending_queue_holds(screen, marker)
                and (_submission_confirmed(screen, marker) or _new_activity_after_submit(before, screen))):
            return {"confirmed": True, "retries": 0, "late_confirmation": True}
    if (
        not _prompt_block_pending(screen, marker)
        and (
            _submission_confirmed(screen, marker)
            or _new_activity_after_submit(before, screen)
        )
    ):
        return {"confirmed": True, "retries": 0}
    # Queued-at-receiver is checked FIRST and before any retry path, because it
    # is the one state that is positively detectable and the one where a resend
    # would duplicate an already-delivered message.
    if pending_queue_holds(screen, marker):
        raise DispatchUnconfirmed(
            f"DISPATCH_UNCONFIRMED marker={marker} surface={surface} "
            "(delivery queued at receiver; awaiting its tool boundary — wait, do not resend)",
            state=DELIVERY_QUEUED_AT_RECEIVER,
        )
    if not _prompt_block_pending(screen, marker):
        raise DispatchUnconfirmed(
            f"DISPATCH_UNCONFIRMED marker={marker} surface={surface} "
            "(marker not visible after submit)",
            state=DELIVERY_UNVERIFIED_BY_DETECTOR,
        )
    if _queued_or_active_input(screen):
        raise DispatchUnconfirmed(
            f"DISPATCH_UNCONFIRMED marker={marker} surface={surface} "
            "(queued/active input; no blind retry)",
            state=COMPOSE_OCCUPIED,
        )

    retry_before = screen
    send_key(surface, "enter")
    time.sleep(float(os.environ.get("CMUX_AGENT_POST_SUBMIT_DELAY", "0.75")))
    screen = read_screen(surface, lines=confirm_lines)
    if pending_queue_holds(screen, marker):
        raise DispatchUnconfirmed(
            f"DISPATCH_UNCONFIRMED marker={marker} surface={surface} "
            "(delivery queued at receiver after one retry — wait, do not resend)",
            state=DELIVERY_QUEUED_AT_RECEIVER,
        )
    if _prompt_block_pending(screen, marker) or (
        not _submission_confirmed(screen, marker)
        and not _new_activity_after_submit(retry_before, screen)
    ):
        raise DispatchUnconfirmed(
            f"DISPATCH_UNCONFIRMED marker={marker} surface={surface} "
            "(compose still pending or marker missing after one retry)",
            state=classify_submission_failure(screen, marker, submitted=True),
        )
    return {"confirmed": True, "retries": 1}


def submit_task_pack(surface, text, task_pack_path, marker=None, confirm_lines=200,
                     force_compose=False):
    """Only dispatch entry point for executor tasks."""
    from availability_contract import require_action
    pack = validate_task_pack_contract(task_pack_path)
    require_action(pack["task_id"], "dispatch", pack)
    return submit_text(
        surface,
        text,
        marker=marker,
        confirm_lines=confirm_lines,
        task_pack_path=task_pack_path,
        force_compose=force_compose,
    )


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def submit_completion_callback(task_pack_path, confirm_lines=200):
    """Deliver the exact terminal callback and persist confirmation evidence."""
    pack = validate_task_pack_contract(task_pack_path)
    from availability_contract import require_action
    require_action(pack["task_id"], "callback", pack)
    report = Path(pack["report"])
    if not report.is_file():
        raise TaskPackContractError(
            "COMPLETION_CALLBACK_REFUSED: report must exist before callback"
        )
    receipt_path = Path(pack["completion_receipt"])
    if receipt_path.exists():
        raise TaskPackContractError(
            f"COMPLETION_RECEIPT_EXISTS: refusing duplicate callback for {receipt_path}"
        )
    result = submit_text(
        pack["callback_target"],
        pack["completion_callback"],
        marker=pack["completion_nonce"],
        confirm_lines=confirm_lines,
    )
    if result.get("confirmed") is not True:
        raise DispatchUnconfirmed("completion callback delivery was not confirmed")

    receipt = {
        "task_id": pack["task_id"],
        "completion_nonce": pack["completion_nonce"],
        "completion_callback": pack["completion_callback"],
        "callback_target": pack["callback_target"],
        "report": str(report),
        "report_sha256": _sha256_file(report),
        "report_bytes": report.stat().st_size,
        "confirmed": True,
        "bridge_retries": result.get("retries", 0),
        "recorded_at_epoch": time.time(),
    }
    encoded = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        fd = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise TaskPackContractError(
            f"COMPLETION_RECEIPT_EXISTS: refusing to replace {receipt_path}"
        ) from exc
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)
    return receipt


def read_screen(surface, lines=200):
    """
    Read last N lines of a surface via `cmux read-screen`.
    Returns plain text string.
    """
    return _run("read-screen", "--surface", surface, "--lines", str(lines))


def wait_for_ack(surface, ack_prefix="PREFLIGHT_ACK", timeout=120, poll=3, lines=200, task_id=None, provider=None, nonce=None):
    """
    Poll read_screen until a real executor ACK appears, or timeout.

    When nonce is provided, the ACK must be generated by the executor response.
    The handshake prompt must not contain the literal ack_prefix; this prevents
    matching the echoed prompt text instead of Claude/Codex's reply.

    Returns the matching line, or raises TimeoutError.
    """
    def compact(text):
        return re.sub(r"\s+", "", text)

    def in_claude_response_block(screen_lines, line_index):
        # Claude's alternate-screen UI may wrap a cautious acknowledgement
        # across enough prose that the leading assistant marker is more than
        # ten rows above the nonce. Scan to the nearest *structural* block
        # marker instead of using a fixed-distance heuristic. Symbols quoted
        # inside prose are not block boundaries.
        for j in range(line_index, -1, -1):
            line = screen_lines[j]
            if re.match(r"^\s*⏺(?:\s|$)", line):
                return True
            if re.match(r"^\s*❯(?:\s|$)", line):
                return False
        return False

    def find_nonce_ack(screen):
        if not (task_id and provider and nonce):
            return None

        expected = f"{ack_prefix}|{task_id}|{provider}:identity|READY|INLINE|{nonce}"
        screen_lines = screen.splitlines()
        for i in range(len(screen_lines)):
            window_lines = screen_lines[i : i + 4]
            window = " ".join(window_lines).strip()
            window_compact = compact(window)
            if expected not in window_compact:
                continue
            if provider == "claude":
                # A window that crosses a Claude user-prompt marker is prompt
                # evidence even if an older assistant marker is nearby.
                if any("❯" in line for line in window_lines):
                    continue
                if not in_claude_response_block(screen_lines, i):
                    continue
            return expected

        if provider != "claude" and expected in compact(screen):
            return expected
        return None

    deadline = time.time() + timeout
    while time.time() < deadline:
        screen = read_screen(surface, lines=lines)
        ack = find_nonce_ack(screen)
        if ack:
            return ack
        for line in reversed(screen.splitlines()):
            if nonce is None and ack_prefix in line:
                return line.strip()
        time.sleep(poll)
    raise TimeoutError(
        f"Timed out waiting for '{ack_prefix}' on {surface} after {timeout}s"
    )


# ---------------------------------------------------------------------------
# Consensus round evidence (anti-forgery)
# ---------------------------------------------------------------------------

def screen_hash(text):
    """Return a short stable hash of captured screen text (tamper-evidence)."""
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def capture_round_evidence(surface, nonce, provider=None, lines=200, expected_ack=None):
    """
    Capture proof that the executor actually produced a per-round nonce reply.

    Reuses the handshake anti-echo rule: for a Claude executor the nonce must
    appear inside a genuine response block (after ``⏺``), not echoed after the
    ``❯`` prompt. Returns a dict with the capture hash and whether the nonce was
    found in a real executor response. Never raises — callers decide on failure.
    """
    screen = read_screen(surface, lines=lines)
    screen_lines = screen.splitlines()

    def in_claude_response_block(line_index):
        for j in range(line_index, -1, -1):
            line = screen_lines[j]
            if re.match(r"^\s*⏺(?:\s|$)", line):
                return True
            if re.match(r"^\s*❯(?:\s|$)", line):
                return False
        return False

    found = False
    for i in range(len(screen_lines)):
        window_lines = screen_lines[i : i + 4]
        compact_window = re.sub(r"\s+", "", " ".join(window_lines))
        evidence_present = (
            expected_ack in compact_window if expected_ack else nonce in compact_window
        )
        if not evidence_present:
            continue
        if provider == "claude":
            if any("❯" in line for line in window_lines):
                continue
            if not in_claude_response_block(i):
                continue
        found = True
        break

    return {
        # `executor_nonce_found` is overloaded by design: when expected_ack is
        # supplied it means the COMPLETE line matched in a genuine response
        # block, not merely that the nonce appeared. These two extra keys make
        # that explicit so a caller cannot mistake nonce-possession for a full
        # verdict line -- a distinction that matters now that the executor picks
        # its own verdict rather than echoing the requested one.
        "expected_ack": expected_ack,
        "expected_ack_found": (found if expected_ack else None),
        "nonce": nonce,
        "executor_nonce_found": found,
        "screen_hash": screen_hash(screen),
        "screen_lines_read": len(screen_lines),
    }


# ---------------------------------------------------------------------------
# Labelling
# ---------------------------------------------------------------------------

def rename_tab(surface, title):
    """Set the visible tab title of a surface."""
    _run("rename-tab", "--surface", surface, "--", title)


# ---------------------------------------------------------------------------
# Notifications / feed
# ---------------------------------------------------------------------------

def notify(surface, title, body=""):
    """
    Send a notification to a surface via `cmux notify`.
    Falls back silently if cmux notify is not available in this version.
    """
    try:
        _run("notify", "--surface", surface, "--title", title, "--body", body)
    except RuntimeError:
        pass  # advisory; non-fatal


def _cli_main(argv=None):
    """Command-line facade for the same bridge functions used by the harness.

    The old module entry point printed a diagnostic ping regardless of the
    arguments supplied by an executor.  That made ``python cmux_bridge.py
    submit_text ...`` look successful while never sending anything.  Keep the
    import API authoritative, but make script invocation explicit and
    fail-closed: every mutating command returns a confirmation JSON object or
    a non-zero error with its delivery state.
    """
    import argparse

    parser = argparse.ArgumentParser(description="cmux collaboration bridge")
    subs = parser.add_subparsers(dest="command", required=True)

    subs.add_parser("ping")
    subs.add_parser("whoami")
    subs.add_parser("surfaces")

    read = subs.add_parser("read-screen", aliases=["read_screen"])
    read.add_argument("--surface", required=True)
    read.add_argument("--lines", type=int, default=200)

    submit = subs.add_parser("submit-text", aliases=["submit_text"])
    submit.add_argument("--surface", required=True)
    submit.add_argument("--text", required=True)
    submit.add_argument("--marker")
    submit.add_argument("--confirm-lines", type=int, default=200)
    submit.add_argument(
        "--force-compose",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="replace idle Claude compose before submit (default: enabled)",
    )

    pack = subs.add_parser("submit-task-pack", aliases=["submit_task_pack"])
    pack.add_argument("--surface", required=True)
    pack.add_argument("--text", required=True)
    pack.add_argument("--task-pack", required=True)
    pack.add_argument("--marker")
    pack.add_argument("--confirm-lines", type=int, default=200)
    pack.add_argument(
        "--force-compose",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="replace idle Claude compose before task dispatch (default: enabled)",
    )

    completion = subs.add_parser(
        "submit-completion-callback", aliases=["submit_completion_callback"]
    )
    completion.add_argument("--task-pack", required=True)
    completion.add_argument("--confirm-lines", type=int, default=200)

    args = parser.parse_args(argv)
    try:
        if args.command == "ping":
            result = {"command": "ping", "ok": bool(ping())}
        elif args.command == "whoami":
            result = {"command": "whoami", "value": whoami()}
        elif args.command == "surfaces":
            result = {"command": "surfaces", "value": list_surfaces()}
        elif args.command in {"read-screen", "read_screen"}:
            # Screen text is intentionally not JSON-escaped into a nested
            # field: callers use it as the raw input to the classifier.
            print(read_screen(args.surface, lines=args.lines), end="")
            return 0
        elif args.command in {"submit-text", "submit_text"}:
            result = submit_text(
                args.surface,
                args.text,
                marker=args.marker,
                confirm_lines=args.confirm_lines,
                force_compose=args.force_compose,
            )
            result = {"command": "submit_text", "surface": args.surface, **result}
        elif args.command in {"submit-task-pack", "submit_task_pack"}:
            result = submit_task_pack(
                args.surface,
                args.text,
                args.task_pack,
                marker=args.marker,
                confirm_lines=args.confirm_lines,
                force_compose=args.force_compose,
            )
            result = {
                "command": "submit_task_pack",
                "surface": args.surface,
                "task_pack": args.task_pack,
                **result,
            }
        elif args.command in {
            "submit-completion-callback",
            "submit_completion_callback",
        }:
            result = submit_completion_callback(
                args.task_pack, confirm_lines=args.confirm_lines
            )
            result = {"command": "submit_completion_callback", **result}
        else:  # pragma: no cover - argparse makes this unreachable
            parser.error(f"unknown command: {args.command}")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except DispatchUnconfirmed as exc:
        payload = {
            "confirmed": False,
            "error": str(exc),
            "delivery_state": getattr(exc, "state", None),
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 75
    except (TaskPackContractError, OSError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {"confirmed": False, "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(_cli_main())
