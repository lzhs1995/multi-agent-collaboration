#!/usr/bin/env python3
"""Explicit spatial naming and read-only topology mapper for Herdr sessions.

Display labels (`workspace.label`, `tab.label`, `pane.label`, `agent.name`) help
humans and agents locate panes across multi-tab / 6+ pane Herdr layouts. They
NEVER replace `socket + pane_id + terminal_id + native_session_id` for security
or routing. This tool never sends text, keys, prompts, shell commands, or stops.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any


HERDR_DEFAULT_BIN = "/opt/homebrew/bin/herdr"
SHELL_NAMES = frozenset({"zsh", "-zsh", "bash", "-bash", "sh", "fish", "login"})
KNOWN_AGENT_BINS = {
    "codex": "codex",
    "codex-aarch64-apple-darwin": "codex",
    "ccc-codex-host": "codex",
    "codex-toolchain-launch": "codex",
    "claude": "claude",
    "agy": "agy",
    "agy.real": "agy",
    "agy-us1": "agy",
    "agy-pro": "agy",
    "antigravity": "agy",
    "grok": "grok",
}
PROFILE_JSON_RE = re.compile(r"/profiles/([A-Za-z0-9._-]+)\.json$")
PROMPT_LINE_RE = re.compile(r"^\s*(?:❯|›)\s*(.*)$")
ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;:]*m")
COMPOSE_BORDER_RE = re.compile(r"^─{4,}$")
MODEL_STATUS_RE = re.compile(r"  \[(?:Opus|Sonnet|Haiku)[^\]\n]*\]\s*\|\s*(?:上下文\s*)?\d{1,3}%\s*")


def inspect_pane_compose_draft(screen_text: str) -> dict[str, Any]:
    """Conservative screen observation, not an atomic permission to send.

    Only a completely visible, bordered empty composer is classified empty.
    Unknown renderings must not be used to authorize input. Never echo text.
    """
    def result(state: str, reason: str) -> dict[str, Any]:
        return {"compose_state": state,
                "draft_present": {"empty": False, "draft": True, "unknown": None}[state],
                "reason": reason, "input_sent": False,
                "atomic_send_protection": False}

    if not isinstance(screen_text, str) or not screen_text.strip():
        return result("unknown", "SCREEN_MISSING")
    clean = ANSI_CSI_RE.sub("", screen_text).replace("\r\n", "\n")
    # Unhandled terminal controls can move/hide text; do not guess.
    if any(ord(c) < 32 and c not in "\n\t" for c in clean):
        return result("unknown", "UNSUPPORTED_TERMINAL_CONTROL")
    lines = clean.splitlines()[-18:]
    prompts = [(i, PROMPT_LINE_RE.match(line)) for i, line in enumerate(lines)]
    prompts = [(i, match) for i, match in prompts if match is not None]
    if len(prompts) != 1:
        return result("unknown", "PROMPT_MISSING_OR_AMBIGUOUS")
    index, match = prompts[0]
    if match.group(1).strip():
        # Placeholder hints cannot be distinguished reliably from typed text.
        return result("draft", "NONEMPTY_PROMPT")
    borders = [i for i, line in enumerate(lines) if COMPOSE_BORDER_RE.fullmatch(line.rstrip())]
    if len(borders) != 2:
        return result("unknown", "COMPOSER_BOUNDS_MISSING_OR_AMBIGUOUS")
    first, last = borders
    if not first < index < last or lines[first].rstrip() != lines[last].rstrip():
        return result("unknown", "COMPOSER_BOUNDS_MISSING")
    contents = [line.strip() for i, line in enumerate(lines[first + 1:last], first + 1)
                if i != index]
    if any(contents):
        return result("draft", "MULTILINE_COMPOSER_CONTENT")
    if contents or any(line.strip() and not MODEL_STATUS_RE.fullmatch(line) for line in lines[last + 1:]):
        return result("unknown", "MULTILINE_OR_STALE_COMPOSER")
    return result("empty", "VISIBLE_EMPTY_BOUNDED_COMPOSER")


def check_pane_compose_draft(binary: str, session: str, pane: str, runner=subprocess.run) -> dict[str, Any]:
    """Read-only observation; transport/schema failures remain unknown."""
    report = {"session": session, "pane_id": pane,
              **inspect_pane_compose_draft("")}
    try:
        proc = runner([binary, "--session", session, "pane", "read", pane,
                       "--source", "visible", "--format", "text"],
                      capture_output=True, text=True, timeout=8, check=False)
        if proc.returncode != 0:
            report["reason"] = "PANE_READ_FAILED"
            return report
        report.update(inspect_pane_compose_draft(proc.stdout))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        report["reason"] = "PANE_READ_UNAVAILABLE"
    return report


def compose_check_exit_code(report: dict[str, Any]) -> int:
    return {"empty": 0, "draft": 2, "unknown": 3}.get(report.get("compose_state"), 3)


class RosterError(ValueError):
    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def resolve_herdr_bin() -> str:
    if os.path.isfile(HERDR_DEFAULT_BIN) and os.access(HERDR_DEFAULT_BIN, os.X_OK):
        return HERDR_DEFAULT_BIN
    return "herdr"


def detect_herdr_caller_env(environ: dict[str, str] | None = None) -> dict[str, Any]:
    """Detect whether the current process is inside a Herdr-managed pane.

    Supports both standard `HERDR_ENV=1` and panes started with `HERDR_ENV=0 mg ...`
    where `HERDR_PANE_ID` and live `HERDR_SOCKET_PATH` remain inherited.
    """
    env = os.environ if environ is None else environ
    pane_id = str(env.get("HERDR_PANE_ID") or "").strip()
    socket_path = str(env.get("HERDR_SOCKET_PATH") or "").strip()
    herdr_env = str(env.get("HERDR_ENV") or "").strip()
    socket_live = bool(socket_path) and Path(socket_path).is_socket()
    inside = (herdr_env == "1" and bool(pane_id)) or (bool(pane_id) and socket_live)
    return {
        "inside_herdr": inside,
        "herdr_env_flag": herdr_env,
        "herdr_env_suppressed": inside and herdr_env != "1",
        "pane_id": pane_id or None,
        "tab_id": env.get("HERDR_TAB_ID") or None,
        "workspace_id": env.get("HERDR_WORKSPACE_ID") or None,
        "socket_path": socket_path or None,
    }


def sanitize_label(text: str, max_chars: int = 34) -> str:
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        raise RosterError("EMPTY_LABEL", "Display label must not be empty.")
    chars = list(cleaned)
    if len(chars) > max_chars:
        return "".join(chars[: max_chars - 1]).rstrip() + "…"
    return cleaned


def sanitize_agent_code(name: str) -> str:
    cleaned = str(name or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", cleaned):
        raise RosterError(
            "INVALID_AGENT_CODE",
            "Agent short name must match Herdr's exact rule: [a-z][a-z0-9_-]{0,31}.",
            agent_name=name,
        )
    return cleaned


def short_pane_id(pane_id: str) -> str:
    return pane_id.split(":", 1)[1] if ":" in pane_id else pane_id


def derive_spatial_codes(
    area: dict[str, Any] | None, pane_rects: list[dict[str, Any]]
) -> dict[str, str]:
    """Derive human-readable spatial coordinates (LU, LD, CU, RM, RD, etc.) from 2D rects."""
    if not pane_rects:
        return {}
    if len(pane_rects) == 1:
        return {str(pane_rects[0]["pane_id"]): "FULL"}

    items = []
    for entry in pane_rects:
        pid = str(entry["pane_id"])
        rect = entry.get("rect") or {}
        x = float(rect.get("x", 0))
        y = float(rect.get("y", 0))
        w = max(1.0, float(rect.get("width", 1)))
        h = max(1.0, float(rect.get("height", 1)))
        items.append({"pane_id": pid, "x": x, "y": y, "w": w, "h": h, "cx": x + w / 2.0, "cy": y + h / 2.0})

    items.sort(key=lambda it: (it["x"], it["cx"], it["y"]))
    columns: list[list[dict[str, Any]]] = []
    for item in items:
        placed = False
        for col in columns:
            ref_x0 = min(c["x"] for c in col)
            ref_x1 = max(c["x"] + c["w"] for c in col)
            overlap = min(ref_x1, item["x"] + item["w"]) - max(ref_x0, item["x"])
            min_w = min(ref_x1 - ref_x0, item["w"])
            if overlap > 0.45 * min_w:
                col.append(item)
                placed = True
                break
        if not placed:
            columns.append([item])

    columns.sort(key=lambda col: sum(c["cx"] for c in col) / len(col))
    for col in columns:
        col.sort(key=lambda c: (c["y"], c["cy"], c["x"]))

    num_cols = len(columns)
    col_names_map = {1: [""], 2: ["L", "R"], 3: ["L", "C", "R"]}
    row_names_map = {1: [""], 2: ["U", "D"], 3: ["U", "M", "D"]}

    codes: dict[str, str] = {}
    use_grid_fallback = num_cols > 3 or any(len(col) > 3 for col in columns)
    for c_idx, col in enumerate(columns):
        num_rows = len(col)
        for r_idx, item in enumerate(col):
            if use_grid_fallback:
                code = f"C{c_idx + 1}R{r_idx + 1}"
            else:
                c_tag = col_names_map[num_cols][c_idx]
                r_tag = row_names_map[num_rows][r_idx]
                code = f"{c_tag}{r_tag}" or "FULL"
            codes[item["pane_id"]] = code

    seen: dict[str, int] = {}
    for pid, code in list(codes.items()):
        seen[code] = seen.get(code, 0) + 1
    if any(count > 1 for count in seen.values()):
        idx_seen: dict[str, int] = {}
        for pid, code in list(codes.items()):
            if seen[code] > 1:
                idx_seen[code] = idx_seen.get(code, 0) + 1
                codes[pid] = f"{code}{idx_seen[code]}"
    return codes


def format_pane_label(
    *,
    tab_index: int,
    spatial_code: str,
    pane_id: str,
    role_tag: str,
    duty: str,
    max_chars: int = 34,
) -> str:
    p_short = short_pane_id(pane_id)
    prefix = f"[T{tab_index}·{spatial_code}|{p_short}] {role_tag.strip()}"
    duty_clean = str(duty or "").strip()
    full = f"{prefix} · {duty_clean}" if duty_clean else prefix
    return sanitize_label(full, max_chars=max_chars)


def infer_process_kind(process_info: dict[str, Any] | None) -> dict[str, Any]:
    """Inspect `pane process-info` to detect wrapped/Magpie/AGY agents vs idle shells."""
    if not isinstance(process_info, dict):
        return {"inferred_kind": None, "is_shell_only": False, "summary": None, "launcher_hint": None}
    procs = process_info.get("foreground_processes")
    if not isinstance(procs, list) or not procs:
        return {"inferred_kind": None, "is_shell_only": False, "summary": None, "launcher_hint": None}

    names: list[str] = []
    inferred = None
    launcher_hint = None
    for proc in procs:
        if not isinstance(proc, dict):
            continue
        argv = [str(tok) for tok in (proc.get("argv") or []) if isinstance(tok, str)]
        candidates = [
            os.path.basename(str(proc.get("argv0") or "")),
            os.path.basename(str(proc.get("name") or "")),
            *[os.path.basename(tok) for tok in argv[:6]],
        ]
        for idx, tok in enumerate(argv):
            m = PROFILE_JSON_RE.search(tok)
            if m and launcher_hint is None:
                launcher_hint = m.group(1)
            if os.path.basename(tok) == "mg" and idx + 2 < len(argv):
                sub_cmd = argv[idx + 1]
                station = argv[idx + 2]
                if sub_cmd == "cc" and inferred is None:
                    inferred = "claude"
                    launcher_hint = launcher_hint or station
                elif sub_cmd == "cx" and inferred is None:
                    inferred = "codex"
                    launcher_hint = launcher_hint or station
        for cand in candidates:
            if not cand:
                continue
            if cand not in names:
                names.append(cand)
            lower = cand.lower()
            if lower in KNOWN_AGENT_BINS and inferred is None:
                inferred = KNOWN_AGENT_BINS[lower]
            elif "codex" in lower and inferred is None:
                inferred = "codex"
            elif "claude" in lower and "clauder" not in lower and inferred is None:
                inferred = "claude"

    non_shells = [n for n in names if n not in SHELL_NAMES]
    is_shell_only = bool(names) and not non_shells and inferred is None
    return {
        "inferred_kind": inferred,
        "is_shell_only": is_shell_only,
        "summary": "+".join(names[:3]) if names else None,
        "launcher_hint": launcher_hint,
    }


def collect_session_snapshot(
    session: str, *, herdr_bin: str | None = None, runner=subprocess.run
) -> dict[str, Any]:
    binary = herdr_bin or resolve_herdr_bin()
    prefix = [binary, "--session", session]

    def call_json(args: list[str]) -> dict[str, Any]:
        proc = runner(prefix + args, capture_output=True, text=True, timeout=10, check=False)
        if proc.returncode != 0:
            raise RosterError(
                "HERDR_CALL_FAILED",
                f"Herdr command failed for session {session}: {' '.join(args)}",
                returncode=proc.returncode,
                stderr=(proc.stderr or "")[:400],
            )
        return json.loads(proc.stdout)

    status = call_json(["status", "--json"])
    server = status.get("server") or {}
    if not server.get("running") or server.get("status") != "running":
        raise RosterError("SERVER_NOT_RUNNING", f"Session {session} is not running.")

    workspaces_raw = []
    try:
        workspaces_raw = call_json(["workspace", "list"])["result"].get("workspaces") or []
    except (RosterError, KeyError, AssertionError):
        workspaces_raw = []

    tabs = call_json(["tab", "list"])["result"]["tabs"]
    panes = call_json(["pane", "list"])["result"]["panes"]
    agents = call_json(["agent", "list"])["result"]["agents"]
    agent_by_pane = {row["pane_id"]: row for row in agents if isinstance(row, dict) and row.get("pane_id")}

    panes_by_tab: dict[str, list[dict[str, Any]]] = {}
    for p in panes:
        panes_by_tab.setdefault(p["tab_id"], []).append(p)

    spatial_by_pane: dict[str, str] = {}
    tab_ordinal_by_id: dict[str, int] = {}
    sorted_tabs = sorted(tabs, key=lambda t: (t.get("number") or 999, t["tab_id"]))
    for idx, tab in enumerate(sorted_tabs, start=1):
        tid = tab["tab_id"]
        tab_ordinal_by_id[tid] = idx
        t_panes = panes_by_tab.get(tid, [])
        if t_panes:
            lay = call_json(["pane", "layout", "--pane", t_panes[0]["pane_id"]])["result"]["layout"]
            spatial_by_pane.update(derive_spatial_codes(lay.get("area"), lay.get("panes") or []))

    enriched_panes = []
    for p in panes:
        pid = p["pane_id"]
        arow = agent_by_pane.get(pid) or {}
        native = p.get("agent_session") or arow.get("agent_session") or {}
        native_sid = native.get("value") if native.get("kind") == "id" else None
        detected_agent = p.get("agent") or arow.get("agent")
        status_str = p.get("agent_status") or arow.get("agent_status") or "unknown"
        proc_obs = None
        if not detected_agent or status_str == "unknown":
            try:
                pinfo = call_json(["pane", "process-info", "--pane", pid])["result"].get("process_info")
                proc_obs = infer_process_kind(pinfo)
            except (RosterError, KeyError, ValueError):
                proc_obs = {"inferred_kind": None, "is_shell_only": False, "summary": None, "launcher_hint": None}

        effective_kind = detected_agent or (proc_obs or {}).get("inferred_kind") or native.get("agent")
        if not effective_kind and (proc_obs or {}).get("is_shell_only"):
            effective_kind = "shell"

        enriched_panes.append(
            {
                "pane_id": pid,
                "tab_id": p["tab_id"],
                "tab_index": tab_ordinal_by_id.get(p["tab_id"], 1),
                "workspace_id": p["workspace_id"],
                "spatial": spatial_by_pane.get(pid, "?"),
                "terminal_id": p.get("terminal_id"),
                "detected_agent": detected_agent,
                "effective_kind": effective_kind or "unknown",
                "agent_name": arow.get("name"),
                "pane_label": p.get("label"),
                "terminal_title": p.get("terminal_title_stripped") or p.get("terminal_title"),
                "agent_status": status_str,
                "native_session_id": native_sid,
                "cwd": p.get("cwd"),
                "recognized_by_herdr_agent_list": pid in agent_by_pane,
                "process_observation": proc_obs,
            }
        )

    enriched_panes.sort(key=lambda row: (row["tab_index"], row["pane_id"]))
    return {
        "schema": "herdr-roster-snapshot-v1",
        "session": session,
        "socket": server.get("socket"),
        "server_version": server.get("version"),
        "workspaces": [
            {
                "workspace_id": w["workspace_id"],
                "label": w.get("label"),
                "tab_count": w.get("tab_count", 0),
                "pane_count": w.get("pane_count", 0),
            }
            for w in workspaces_raw
            if isinstance(w, dict) and w.get("workspace_id")
        ],
        "tabs": [
            {
                "tab_id": t["tab_id"],
                "tab_index": tab_ordinal_by_id[t["tab_id"]],
                "number": t.get("number"),
                "label": t.get("label"),
                "pane_count": t.get("pane_count", 0),
                "agent_status": t.get("agent_status", "unknown"),
            }
            for t in sorted_tabs
        ],
        "panes": enriched_panes,
        "input_sent": False,
    }


def render_markdown_map(snapshots: list[dict[str, Any]]) -> str:
    lines = ["# Herdr Multi-Agent Explicit Topology Map", ""]
    for snap in snapshots:
        ws_desc = ", ".join(f"{w['workspace_id']}={w.get('label') or '-'}" for w in snap.get("workspaces") or [])
        ws_part = f" · space `{ws_desc}`" if ws_desc else ""
        lines.append(
            f"## Session `{snap['session']}` ({len(snap['tabs'])} Tabs / {len(snap['panes'])} Panes{ws_part} · socket `{snap['socket']}`)"
        )
        tab_labels = {t["tab_id"]: f"T{t['tab_index']}:{t.get('label') or t['tab_id']}" for t in snap["tabs"]}
        lines.append("")
        lines.append("| Tab | Pos | Pane | Agent Code | Kind | Status | Explicit Label / Title | Native SID |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for p in snap["panes"]:
            sid_short = (p["native_session_id"] or "-")[:8]
            display = str(p["pane_label"] or p["terminal_title"] or "-").replace("|", "\\|")
            ag_code = p["agent_name"] or "-"
            lines.append(
                f"| `{tab_labels.get(p['tab_id'], p['tab_id'])}` | **{p['spatial']}** | `{p['pane_id']}` | `{ag_code}` | `{p['effective_kind']}` | `{p['agent_status']}` | {display} | `{sid_short}` |"
            )
        lines.append("")
    return "\n".join(lines)


def apply_roster_manifest(
    manifest: dict[str, Any],
    *,
    dry_run: bool = False,
    herdr_bin: str | None = None,
    runner=subprocess.run,
) -> dict[str, Any]:
    """Validate pinned terminal/session identities and apply UI-only labels."""
    if not isinstance(manifest, dict) or manifest.get("schema") != "herdr-roster-manifest-v1":
        raise RosterError("INVALID_MANIFEST_SCHEMA", "Expected schema 'herdr-roster-manifest-v1'.")
    sessions_spec = manifest.get("sessions")
    if not isinstance(sessions_spec, list) or not sessions_spec:
        raise RosterError("EMPTY_MANIFEST", "Manifest requires a non-empty 'sessions' list.")

    binary = herdr_bin or resolve_herdr_bin()
    planned_commands: list[list[str]] = []
    executed_commands: list[list[str]] = []
    session_reports = []

    verified_actions: list[tuple[str, list[list[str]]]] = []
    for entry in sessions_spec:
        session = entry.get("session")
        if not isinstance(session, str) or not session:
            raise RosterError("INVALID_SESSION_NAME", "Session entry lacks a valid session name.")
        snap = collect_session_snapshot(session, herdr_bin=binary, runner=runner)
        if entry.get("expect_socket") and os.path.normpath(entry["expect_socket"]) != os.path.normpath(
            snap["socket"] or ""
        ):
            raise RosterError(
                "SOCKET_MISMATCH",
                f"Socket mismatch for {session}",
                expected=entry["expect_socket"],
                actual=snap["socket"],
            )

        workspaces_by_id = {w["workspace_id"]: w for w in snap.get("workspaces") or []}
        tabs_by_id = {t["tab_id"]: t for t in snap["tabs"]}
        panes_by_id = {p["pane_id"]: p for p in snap["panes"]}
        cmds: list[list[str]] = []

        ws_label = entry.get("workspace_label")
        if ws_label:
            clean_ws = sanitize_label(ws_label, max_chars=20)
            target_ws_ids = sorted({p["workspace_id"] for p in snap["panes"] if p.get("workspace_id")})
            for wid in target_ws_ids:
                if (workspaces_by_id.get(wid) or {}).get("label") != clean_ws:
                    cmds.append([binary, "--session", session, "workspace", "rename", wid, clean_ws])

        for t_spec in entry.get("tabs") or []:
            tid = t_spec.get("tab_id")
            if tid not in tabs_by_id:
                raise RosterError("TAB_NOT_FOUND", f"Tab {tid} missing in session {session}.")
            new_tlabel = sanitize_label(t_spec["label"], max_chars=20)
            if tabs_by_id[tid].get("label") != new_tlabel:
                cmds.append([binary, "--session", session, "tab", "rename", tid, new_tlabel])

        for p_spec in entry.get("panes") or []:
            pid = p_spec.get("pane_id")
            if pid not in panes_by_id:
                raise RosterError("PANE_NOT_FOUND", f"Pane {pid} missing in session {session}.")
            live_p = panes_by_id[pid]
            if p_spec.get("expect_terminal_id") and live_p["terminal_id"] != p_spec["expect_terminal_id"]:
                raise RosterError(
                    "TERMINAL_MISMATCH",
                    f"Terminal changed on {session} {pid}; refusing to rename.",
                    expected=p_spec["expect_terminal_id"],
                    actual=live_p["terminal_id"],
                )
            if p_spec.get("expect_native_session_id") and live_p["native_session_id"] != p_spec["expect_native_session_id"]:
                raise RosterError(
                    "NATIVE_SESSION_MISMATCH",
                    f"Native session changed on {session} {pid}; refusing to rename.",
                    expected=p_spec["expect_native_session_id"],
                    actual=live_p["native_session_id"],
                )

            if "label" in p_spec and p_spec["label"]:
                new_plabel = sanitize_label(p_spec["label"], max_chars=36)
            else:
                new_plabel = format_pane_label(
                    tab_index=live_p["tab_index"],
                    spatial_code=live_p["spatial"],
                    pane_id=pid,
                    role_tag=p_spec["role_tag"],
                    duty=p_spec.get("duty", ""),
                    max_chars=36,
                )
            if live_p.get("pane_label") != new_plabel:
                cmds.append([binary, "--session", session, "pane", "rename", pid, new_plabel])

            agent_code = p_spec.get("agent_name")
            if agent_code and live_p["recognized_by_herdr_agent_list"]:
                clean_code = sanitize_agent_code(agent_code)
                if live_p.get("agent_name") != clean_code:
                    cmds.append([binary, "--session", session, "agent", "rename", pid, clean_code])

        planned_commands.extend(cmds)
        verified_actions.append((session, cmds))
        session_reports.append(
            {
                "session": session,
                "pane_count": len(snap["panes"]),
                "planned_updates": len(cmds),
            }
        )

    if not dry_run:
        for session, cmds in verified_actions:
            for argv in cmds:
                proc = runner(argv, capture_output=True, text=True, timeout=10, check=False)
                if proc.returncode != 0:
                    raise RosterError(
                        "RENAME_FAILED",
                        f"Metadata rename command failed in {session}",
                        argv=argv,
                        returncode=proc.returncode,
                        stderr=(proc.stderr or "")[:400],
                    )
                executed_commands.append(argv)

    return {
        "schema": "herdr-roster-apply-result-v1",
        "ok": True,
        "dry_run": dry_run,
        "input_sent": False,
        "sessions": session_reports,
        "planned_command_count": len(planned_commands),
        "executed_command_count": len(executed_commands),
        "planned_commands": planned_commands,
    }


def discover_live_sessions(herdr_bin: str | None = None, runner=subprocess.run) -> list[str]:
    found = []
    base = Path.home() / ".config/herdr"
    if (base / "herdr.sock").is_socket():
        found.append("default")
    sessions_dir = base / "sessions"
    if sessions_dir.is_dir():
        for child in sorted(sessions_dir.iterdir()):
            if child.is_dir() and (child / "herdr.sock").is_socket():
                found.append(child.name)
    live = []
    binary = herdr_bin or resolve_herdr_bin()
    for name in found:
        proc = runner([binary, "--session", name, "status"], capture_output=True, text=True, timeout=6, check=False)
        if proc.returncode == 0 and "status: running" in proc.stdout:
            live.append(name)
    return live


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for sub_name in ("map", "snapshot"):
        p_map = sub.add_parser(sub_name, help="Inspect 2D spatial layout and explicit naming across Herdr sessions")
        target = p_map.add_mutually_exclusive_group(required=False)
        target.add_argument("--session", help="Specific local Herdr session name")
        target.add_argument("--all", action="store_true", help="All live local Herdr sessions")
        p_map.add_argument("--format", choices=("markdown", "json"), default="markdown")

    p_env = sub.add_parser("caller-env", help="Detect Herdr caller context even when HERDR_ENV=0 was passed")
    p_env.add_argument("--json", action="store_true", default=True)

    p_draft = sub.add_parser("check-draft", help="Read-only check whether a target pane has unsubmitted user text in its prompt")
    p_draft.add_argument("--session", required=True, help="Exact Herdr session; no implicit default")
    p_draft.add_argument("--pane", required=True, help="Exact pane_id such as w1:pC")

    p_apply = sub.add_parser("apply", help="Apply validated UI-only workspace/tab/pane/agent labels from a manifest")
    p_apply.add_argument("--manifest", required=True, type=Path)
    p_apply.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "caller-env":
            info = detect_herdr_caller_env()
            print(json.dumps(info, ensure_ascii=False, indent=2))
            return 0 if info["inside_herdr"] else 1
        if args.command == "check-draft":
            report = check_pane_compose_draft(resolve_herdr_bin(), args.session, args.pane)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return compose_check_exit_code(report)
        if args.command in ("map", "snapshot"):
            if args.all:
                names = discover_live_sessions()
            elif args.session:
                names = [args.session]
            else:
                names = [os.environ.get("HERDR_SESSION") or "default"]
            snaps = [collect_session_snapshot(n) for n in names]
            if args.format == "json":
                print(json.dumps({"schema": "herdr-roster-map-v1", "sessions": snaps, "input_sent": False}, ensure_ascii=False, indent=2))
            else:
                print(render_markdown_map(snaps))
            return 0
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        result = apply_roster_manifest(manifest, dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (RosterError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, RosterError) else type(exc).__name__
        details = exc.details if isinstance(exc, RosterError) else {}
        print(
            json.dumps(
                {"ok": False, "error": {"code": code, "message": str(exc), **details}, "input_sent": False},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
