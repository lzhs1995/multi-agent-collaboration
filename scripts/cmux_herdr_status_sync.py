#!/usr/bin/env python3
"""Publish noise-free, Tab-grouped Herdr tool status pills into cmux workspaces.

Design principles (Phase 2):
1. Zero task-reply text in cmux sidebar: never prints terminal titles, assistant
   replies, or verbose task descriptions. Only tool types (CC/CX/GK/AGY/SH),
   live states (⚠️/🔨/✅/⏸), and 2D spatial positions (L/R/LU/RD/CU...) appear.
2. Single mental model (1 pill per Tab):
   - Workspace `description`: `Herdr · <T>T/<P>P · 🔨w ⏸i`
   - Each Tab gets one stable row (`herdr:tab:<tab_id>`), ordered `T1, T2, T3...`,
     whose icon reflects the highest-priority state inside that Tab.
3. Full process-info fallback so Magpie (`mg cc` / `mg cx`), `agy` wrappers,
   `codex-aarch64-apple-darwin`, and idle shells (`SH`) are always classified.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
from typing import Any


PREFERRED = (
    ("default", "E18EA3A3-DBF0-4146-9524-E22926FD6C64"),
    ("c4-multiagent", "1FF621E6-4F83-44C8-B0AE-6C93DA3146BC"),
    ("c2-cmaverse", "7B8351A7-9815-4817-93FC-3DA4E9386E25"),
    ("c2-spatial", "A77E1AF8-95DF-4555-B6A4-03E86C623B50"),
    ("stata", "EA1D69B4-2F78-4D24-B94D-5FC7C536C419"),
    ("c3-mplusguide", "E84867DF-4D7B-45CC-949A-488DD57DAB4B"),
)
MAX_INLINE_PANES_PER_TAB = 4
ACTIVE_STATUSES = frozenset({"blocked", "working", "done"})
STATE_DIR = Path.home() / ".local/state/cmux-herdr"
BASE_ENV = {**os.environ, "CMUX_QUIET": "1"}
STOP = threading.Event()

TOOL_BADGES = {
    "claude": "CC",
    "codex": "CX",
    "grok": "GK",
    "agy": "AGY",
    "shell": "SH",
}
TOOL_ORDER = ("CC", "CX", "GK", "AGY", "SH", "UNK")

STATE_EMOJI = {
    "blocked": "⚠️",
    "working": "🔨",
    "done": "✅",
    "idle": "⏸",
    "shell": "⏸",
    "unknown": "⏸",
}
STATE_RANK = {
    "blocked": 4,
    "working": 3,
    "done": 2,
    "idle": 1,
    "shell": 1,
    "unknown": 1,
}
TAB_ICON_BY_RANK = {
    4: "exclamationmark.triangle",
    3: "hammer",
    2: "checkmark.circle",
    1: "pause.circle",
}
# Redundant built-in hook keys that clutter Herdr-hosted cmux workspace cards
REDUNDANT_HOST_KEYS = frozenset({"codex", "claude", "claude_code"})
SPATIAL_FROM_LABEL_RE = re.compile(r"^\[T\d+[·.]([^\]|]+)\|[^\]]+\]")


def run_cmd(argv: list[str], env: dict[str, str] = BASE_ENV, timeout: int = 12) -> str:
    result = subprocess.run(
        argv, env=env, capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode:
        raise RuntimeError(f"{argv[0]} {argv[1] if len(argv) > 1 else ''} failed: {result.stderr.strip()[:500]}")
    return result.stdout


def collect_workspaces(node: Any, found: dict[str, dict[str, Any]]) -> None:
    if isinstance(node, dict):
        workspace = node.get("id")
        if workspace and isinstance(node.get("panes"), list):
            found[workspace] = node
        for value in node.values():
            collect_workspaces(value, found)
    elif isinstance(node, list):
        for value in node:
            collect_workspaces(value, found)


def terminal_surfaces(host: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        surface
        for pane in host.get("panes", [])
        for surface in pane.get("surfaces", [])
        if surface.get("type") == "terminal" and surface.get("id")
    ]


def herdr_attach_by_tty(runner=subprocess.run) -> dict[str, str]:
    result = runner(
        ["ps", "-axo", "pid=,tty=,command="],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"ps failed: {result.stderr.strip()[:400]}")
    mapping: dict[str, str] = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        _pid, tty, command = parts
        if tty in ("??", "-", ""):
            continue
        if "herdr server" in command or "cmux-herdr" in command or "ccc_herdr" in command:
            continue
        tokens = command.split()
        if not tokens or Path(tokens[0]).name != "herdr":
            continue
        session = "default"
        if "--session" in tokens:
            index = tokens.index("--session")
            if index + 1 >= len(tokens):
                continue
            session = tokens[index + 1]
        mapping[tty] = session
    return mapping


def find_host_surface(
    session: str, surfaces: list[dict[str, Any]], attaches: dict[str, str]
) -> str:
    tty_hits = [s for s in surfaces if attaches.get(s.get("tty")) == session]
    title_hits = [s for s in surfaces if s.get("title") == f"Herdr · {session}"]
    if len(tty_hits) == 1:
        return tty_hits[0]["id"]
    if len(title_hits) == 1:
        return title_hits[0]["id"]
    titled_tty = [s for s in tty_hits if s.get("title") == f"Herdr · {session}"]
    if len(titled_tty) == 1:
        return titled_tty[0]["id"]
    raise RuntimeError(
        f"session {session} matched {len(tty_hits)} tty surfaces and {len(title_hits)} titled surfaces"
    )


def discover_targets(
    workspaces: dict[str, dict[str, Any]], attaches: dict[str, str]
) -> list[tuple[str, str]]:
    claimed_sessions: set[str] = set()
    claimed_workspaces: set[str] = set()
    targets: list[tuple[str, str]] = []
    for session, workspace in PREFERRED:
        host = workspaces.get(workspace)
        if host is None:
            continue
        surfaces = terminal_surfaces(host)
        try:
            find_host_surface(session, surfaces, attaches)
        except RuntimeError:
            continue
        targets.append((session, workspace))
        claimed_sessions.add(session)
        claimed_workspaces.add(workspace)
    extras: list[tuple[str, str]] = []
    for workspace, host in workspaces.items():
        if workspace in claimed_workspaces:
            continue
        sessions: list[str] = []
        for surface in terminal_surfaces(host):
            session = attaches.get(surface.get("tty"))
            if session and session not in claimed_sessions and session not in sessions:
                sessions.append(session)
        if len(sessions) != 1:
            continue
        extras.append((sessions[0], workspace))
        claimed_sessions.add(sessions[0])
        claimed_workspaces.add(workspace)
    extras.sort()
    return targets + extras


def session_env(session: str, workspace: str, surface: str) -> dict[str, str]:
    env = BASE_ENV.copy()
    for key in (
        "HERDR_CLIENT_SOCKET_PATH",
        "HERDR_SERVER_PID",
        "HERDR_WORKSPACE_ID",
        "HERDR_TAB_ID",
        "HERDR_PANE_ID",
        "CMUX_TAB_ID",
        "CMUX_PANE_ID",
    ):
        env.pop(key, None)
    directory = Path.home() / ".config/herdr"
    if session != "default":
        directory = directory / "sessions" / session
    socket = directory / "herdr.sock"
    if not socket.is_socket():
        raise RuntimeError(f"session {session} has no live socket")
    env.update(
        HERDR_ENV="1",
        HERDR_SESSION=session,
        HERDR_SOCKET_PATH=str(socket),
        CMUX_WORKSPACE_ID=workspace,
        CMUX_SURFACE_ID=surface,
    )
    return env


def tool_badge(pane: dict[str, Any]) -> str:
    kind = str(pane.get("effective_kind") or "").lower()
    return TOOL_BADGES.get(kind, "UNK")


def extract_spatial_token(pane: dict[str, Any]) -> str:
    spatial = str(pane.get("spatial") or "").strip()
    if spatial and spatial != "?":
        return "" if spatial == "FULL" else spatial
    label = str(pane.get("pane_label") or "").strip()
    m = SPATIAL_FROM_LABEL_RE.match(label)
    if m:
        code = m.group(1).strip()
        return "" if code == "FULL" else code
    return pane["pane_id"].split(":", 1)[-1]


def compact_tab_heading(tab: dict[str, Any], fallback_idx: int) -> str:
    idx = tab.get("tab_index") or fallback_idx
    raw = str(tab.get("label") or "").strip()
    # Strip leading `T1-` / `T1 ` prefix to avoid `T1 T1-xxx`
    raw = re.sub(r"^T\d+\s*[-·:]?\s*", "", raw)
    if not raw or raw.isdigit():
        return f"T{idx}"
    if len(raw) > 6:
        raw = raw[:6]
    return f"T{idx} {raw}"


def format_pane_chip(pane: dict[str, Any]) -> str:
    st = str(pane.get("agent_status") or "unknown").lower()
    emoji = STATE_EMOJI.get(st, "⏸")
    badge = tool_badge(pane)
    pos = extract_spatial_token(pane)
    if pos and badge != "SH":
        return f"{emoji}{badge}({pos})"
    return f"{emoji}{badge}"


def format_idle_group(idle_panes: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for p in idle_panes:
        b = tool_badge(p)
        counts[b] = counts.get(b, 0) + 1
    parts = []
    for b in TOOL_ORDER:
        c = counts.get(b, 0)
        if c == 1:
            parts.append(b)
        elif c > 1:
            parts.append(f"{b}×{c}")
    return f"⏸{len(idle_panes)}({','.join(parts)})"


def plan_workspace_pills_and_description(
    tabs: list[dict[str, Any]],
    panes: list[dict[str, Any]],
    existing_status_keys: set[str],
    *,
    max_inline_per_tab: int = MAX_INLINE_PANES_PER_TAB,
) -> dict[str, Any]:
    """Pure planner: 1 status pill per Tab (tool status only, zero reply text)."""
    panes_by_tab: dict[str, list[dict[str, Any]]] = {}
    blocked_count = 0
    working_count = 0
    done_count = 0
    idle_count = 0

    for p in panes:
        panes_by_tab.setdefault(p["tab_id"], []).append(p)
        st = str(p.get("agent_status") or "unknown").lower()
        if st == "blocked":
            blocked_count += 1
        elif st == "working":
            working_count += 1
        elif st == "done":
            done_count += 1
        else:
            idle_count += 1

    desired_pills: dict[str, dict[str, Any]] = {}
    ordered_tabs = sorted(tabs, key=lambda t: (t.get("tab_index") or 999, t["tab_id"]))
    for idx, t in enumerate(ordered_tabs, start=1):
        tid = t["tab_id"]
        t_panes = panes_by_tab.get(tid) or []
        if not t_panes:
            continue
        heading = compact_tab_heading(t, idx)
        top_rank = max(STATE_RANK.get(str(p.get("agent_status") or "unknown").lower(), 1) for p in t_panes)
        icon = TAB_ICON_BY_RANK.get(top_rank, "pause.circle")
        # Stable T1, T2, T3 top-to-bottom ordering in cmux sidebar
        priority = max(10, 80 - idx)

        if len(t_panes) <= max_inline_per_tab:
            chips = " ".join(format_pane_chip(p) for p in t_panes)
            value = f"{heading}: {chips}"
        else:
            active_panes = [
                p for p in t_panes if str(p.get("agent_status") or "unknown").lower() in ACTIVE_STATUSES
            ]
            idle_panes = [
                p for p in t_panes if str(p.get("agent_status") or "unknown").lower() not in ACTIVE_STATUSES
            ]
            segments = []
            if active_panes:
                shown = [format_pane_chip(p) for p in active_panes[:3]]
                if len(active_panes) > 3:
                    shown.append(f"+{len(active_panes) - 3}")
                segments.append(" ".join(shown))
            if idle_panes:
                segments.append(format_idle_group(idle_panes))
            value = f"{heading}: {' · '.join(segments)}"

        desired_pills[f"herdr:tab:{tid}"] = {
            "value": value,
            "icon": icon,
            "priority": priority,
        }

    removable_existing = {
        k for k in existing_status_keys if k.startswith("herdr:") or k in REDUNDANT_HOST_KEYS
    }
    stale_keys = sorted(removable_existing - set(desired_pills.keys()))

    counts_summary = []
    if blocked_count:
        counts_summary.append(f"⚠️{blocked_count}")
    if working_count:
        counts_summary.append(f"🔨{working_count}")
    if done_count:
        counts_summary.append(f"✅{done_count}")
    if idle_count:
        counts_summary.append(f"⏸{idle_count}")
    if not counts_summary:
        counts_summary.append("empty")
    description = f"Herdr · {len(ordered_tabs)}T/{len(panes)}P · {' '.join(counts_summary)}"

    return {
        "desired_pills": desired_pills,
        "stale_keys": stale_keys,
        "description": description,
        "tab_grouped": True,
    }


def list_existing_status_keys(workspace: str, env: dict[str, str] = BASE_ENV) -> set[str]:
    out = run_cmd(["cmux", "list-status", "--workspace", workspace], env=env)
    keys: set[str] = set()
    for line in out.splitlines():
        line = line.strip()
        if "=" in line:
            keys.add(line.split("=", 1)[0].strip())
    return keys


def infer_kind_from_process_info(pinfo: dict[str, Any]) -> str:
    for proc in pinfo.get("foreground_processes") or []:
        if not isinstance(proc, dict):
            continue
        argv = proc.get("argv") if isinstance(proc.get("argv"), list) else []
        tokens = [
            os.path.basename(str(proc.get("argv0") or "")).lower(),
            os.path.basename(str(proc.get("name") or "")).lower(),
            *[os.path.basename(str(tok)).lower() for tok in argv[:6] if isinstance(tok, str)],
        ]
        joined = " ".join(str(tok).lower() for tok in argv[:8] if isinstance(tok, str))
        for nm in tokens:
            if not nm:
                continue
            if "codex" in nm or nm == "ccc-codex-host":
                return "codex"
            if "claude" in nm and "clauder" not in nm:
                return "claude"
            if nm in ("agy", "agy.real", "agy-us1", "agy-pro", "antigravity"):
                return "agy"
            if nm == "grok":
                return "grok"
        if "mg cc " in joined:
            return "claude"
        if "mg cx " in joined:
            return "codex"
    return "shell"


def collect_enriched_session(session: str, env: dict[str, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    herdr_bin = "/opt/homebrew/bin/herdr" if os.path.isfile("/opt/homebrew/bin/herdr") else "herdr"
    tabs_raw = json.loads(run_cmd([herdr_bin, "--session", session, "tab", "list"], env=env))["result"]["tabs"]
    panes_raw = json.loads(run_cmd([herdr_bin, "--session", session, "pane", "list"], env=env))["result"]["panes"]
    agents_raw = json.loads(run_cmd([herdr_bin, "--session", session, "agent", "list"], env=env))["result"]["agents"]
    agent_by_pane = {a["pane_id"]: a for a in agents_raw if isinstance(a, dict) and a.get("pane_id")}

    sorted_tabs = sorted(tabs_raw, key=lambda t: (t.get("number") or 999, t["tab_id"]))
    tab_ord = {t["tab_id"]: idx for idx, t in enumerate(sorted_tabs, start=1)}
    tabs = [{**t, "tab_index": tab_ord[t["tab_id"]]} for t in sorted_tabs]

    panes = []
    for p in panes_raw:
        pid = p["pane_id"]
        arow = agent_by_pane.get(pid) or {}
        detected = p.get("agent") or arow.get("agent")
        status_str = p.get("agent_status") or arow.get("agent_status") or "unknown"
        eff_kind = detected
        if not eff_kind:
            try:
                pinfo = json.loads(
                    run_cmd([herdr_bin, "--session", session, "pane", "process-info", "--pane", pid], env=env)
                )["result"].get("process_info") or {}
                eff_kind = infer_kind_from_process_info(pinfo)
            except (RuntimeError, ValueError, KeyError):
                eff_kind = "shell"

        panes.append(
            {
                "pane_id": pid,
                "tab_id": p["tab_id"],
                "tab_index": tab_ord.get(p["tab_id"], 1),
                "workspace_id": p["workspace_id"],
                "effective_kind": eff_kind,
                "agent_name": arow.get("name"),
                "pane_label": p.get("label"),
                "agent_status": status_str,
            }
        )
    panes.sort(key=lambda row: (row["tab_index"], row["pane_id"]))
    return tabs, panes


def publish_target(
    session: str,
    workspace: str,
    host: dict[str, Any],
    descriptions: dict[str, str | None],
    attaches: dict[str, str],
) -> dict[str, Any]:
    surface = find_host_surface(session, terminal_surfaces(host), attaches)
    env = session_env(session, workspace, surface)
    tabs, panes = collect_enriched_session(session, env)

    existing_keys = list_existing_status_keys(workspace, env=BASE_ENV)
    plan = plan_workspace_pills_and_description(tabs, panes, existing_keys)

    for key in plan["stale_keys"]:
        run_cmd(["cmux", "clear-status", key, "--workspace", workspace], env=BASE_ENV)

    for key, spec in plan["desired_pills"].items():
        run_cmd(
            [
                "cmux",
                "set-status",
                key,
                spec["value"],
                "--workspace",
                workspace,
                "--icon",
                spec["icon"],
                "--priority",
                str(spec["priority"]),
            ],
            env=BASE_ENV,
        )

    existing_desc = descriptions.get(workspace)
    if existing_desc != plan["description"] and (
        existing_desc in (None, "")
        or (isinstance(existing_desc, str) and existing_desc.lower().startswith("herdr"))
    ):
        run_cmd(
            [
                "cmux",
                "workspace-action",
                "--workspace",
                workspace,
                "--action",
                "set-description",
                "--description",
                plan["description"],
            ],
            env=BASE_ENV,
        )

    return {
        "session": session,
        "workspace": workspace,
        "surface": surface,
        "pane_count": len(panes),
        "pill_count": len(plan["desired_pills"]),
        "tab_grouped": plan["tab_grouped"],
        "description": plan["description"],
    }


def publish_once() -> dict[str, Any]:
    tree = json.loads(run_cmd(["cmux", "tree", "--id-format", "both", "--json"]))
    workspaces: dict[str, dict[str, Any]] = {}
    collect_workspaces(tree, workspaces)
    listed = json.loads(run_cmd(["cmux", "workspace", "list", "--json"]))["workspaces"]
    descriptions = {row["id"]: row.get("description") for row in listed}
    attaches = herdr_attach_by_tty()
    results = []
    errors = []
    for session, workspace in discover_targets(workspaces, attaches):
        try:
            results.append(
                publish_target(session, workspace, workspaces[workspace], descriptions, attaches)
            )
        except (RuntimeError, ValueError, KeyError, OSError, subprocess.TimeoutExpired) as error:
            errors.append(f"{session}: {error}")
    return {"results": results, "errors": errors}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    options = parser.parse_args(argv)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[RotatingFileHandler(STATE_DIR / "agent-status.log", maxBytes=1_000_000, backupCount=2)],
    )
    with (STATE_DIR / "agent-status.lock").open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("An agent status sync is already running")
        lock.seek(0)
        lock.truncate()
        lock.write(str(os.getpid()) + "\n")
        lock.flush()
        signal.signal(signal.SIGTERM, lambda *_: STOP.set())
        signal.signal(signal.SIGINT, lambda *_: STOP.set())
        previous = None
        while not STOP.is_set():
            try:
                report = publish_once()
            except (RuntimeError, ValueError, KeyError, OSError, subprocess.TimeoutExpired) as error:
                report = {"results": [], "errors": [str(error)]}
            signature = json.dumps(report, ensure_ascii=False, sort_keys=True)
            if signature != previous:
                logging.log(logging.ERROR if report["errors"] else logging.INFO, "%s", signature)
                previous = signature
            if options.once:
                print(signature)
                return int(bool(report["errors"]))
            STOP.wait(3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
