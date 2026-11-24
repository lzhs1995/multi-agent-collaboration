#!/usr/bin/env python3
"""Publish Herdr agent/pane status pills into cmux workspaces with 6+ pane folding.

Enhancements over v1:
1. Uses full Herdr `tab list` + `pane list` + `pane layout` + `process-info` so
   wrapped agents (`codex-aarch64-apple-darwin`) and explicitly named panes with
   `agent_status == "unknown"` are never silently dropped.
2. Adaptive folding when a Herdr session has >6 panes:
   - Active/attention panes (`blocked`, `working`, `done`) stay as top-sorted
     individual pills with spatial coordinates.
   - `idle` / `shell` panes in the same Tab fold into one compact per-Tab summary
     pill (`herdr:tab:<tab_id>`), preventing sidebar overflow and truncation.
3. Publishes compact workspace `description` across all sessions (including `default`)
   and supports multiple inner Herdr workspaces.
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
MAX_EXPANDED_PANES = 6
ACTIVE_STATUSES = frozenset({"blocked", "working", "done"})
STATE_DIR = Path.home() / ".local/state/cmux-herdr"
BASE_ENV = {**os.environ, "CMUX_QUIET": "1"}
STOP = threading.Event()

STATUS_STYLES = {
    "blocked": ("exclamationmark.triangle", 90),
    "working": ("hammer", 80),
    "done": ("checkmark.circle", 60),
    "idle": ("pause.circle", 40),
    "shell": ("terminal", 25),
    "unknown": ("questionmark.circle", 20),
}


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


def short_pane_token(pane: dict[str, Any]) -> str:
    """Compact identifier for folded idle summaries and workspace descriptions."""
    p_short = pane["pane_id"].split(":", 1)[-1]
    if pane.get("agent_name"):
        return str(pane["agent_name"])
    label = str(pane.get("pane_label") or "").strip()
    if label:
        # Extract role_tag from `[T1·LU|p1] SUP-CX1 · 职责` -> `p1:SUP-CX1`
        m = re.match(r"^\[[^\]]+\]\s*([^\u00b7·]+)", label)
        if m:
            role_part = m.group(1).strip()[:14]
            return f"{p_short}:{role_part}"
        return f"{p_short}:{label[:14]}"
    kind = pane.get("effective_kind") or "pane"
    return f"{p_short}:{kind}"


def full_pane_pill_value(pane: dict[str, Any], tab_label: str) -> str:
    kind = pane.get("effective_kind") or "pane"
    st = (pane.get("agent_status") or "unknown").lower()
    if st == "unknown" and kind == "shell":
        st = "idle"
    label = str(pane.get("pane_label") or "").strip()
    if label:
        return f"{kind}/{st} · {label}"
    title = str(pane.get("terminal_title") or "").strip()
    if len(title) > 28:
        title = title[:27] + "…"
    parts = [f"{kind}/{st}"]
    if tab_label:
        parts.append(tab_label)
    parts.append(title or f"{kind}@{pane['pane_id']}")
    return " · ".join(parts)


def plan_workspace_pills_and_description(
    tabs: list[dict[str, Any]],
    panes: list[dict[str, Any]],
    existing_herdr_keys: set[str],
    *,
    max_expanded: int = MAX_EXPANDED_PANES,
) -> dict[str, Any]:
    """Pure planner: compute desired set-status entries, clear-status keys, and description."""
    tab_by_id = {t["tab_id"]: t for t in tabs}
    desired_pills: dict[str, dict[str, Any]] = {}

    active_tokens: list[str] = []
    blocked_tokens: list[str] = []
    done_count = 0
    idle_count = 0

    for p in panes:
        st = (p.get("agent_status") or "unknown").lower()
        if st == "blocked":
            blocked_tokens.append(short_pane_token(p))
        elif st == "working":
            active_tokens.append(short_pane_token(p))
        elif st == "done":
            done_count += 1
        else:
            idle_count += 1

    if len(panes) <= max_expanded:
        for p in panes:
            tid = p["tab_id"]
            t_label = (tab_by_id.get(tid) or {}).get("label") or f"T{p.get('tab_index', 1)}"
            st = (p.get("agent_status") or "unknown").lower()
            style_key = "shell" if (st == "unknown" and p.get("effective_kind") == "shell") else st
            icon, priority = STATUS_STYLES.get(style_key, STATUS_STYLES["unknown"])
            desired_pills[f"herdr:{p['pane_id']}"] = {
                "value": full_pane_pill_value(p, t_label),
                "icon": icon,
                "priority": priority,
            }
    else:
        folded_by_tab: dict[str, list[dict[str, Any]]] = {}
        for p in panes:
            st = (p.get("agent_status") or "unknown").lower()
            tid = p["tab_id"]
            t_label = (tab_by_id.get(tid) or {}).get("label") or f"T{p.get('tab_index', 1)}"
            if st in ACTIVE_STATUSES:
                icon, priority = STATUS_STYLES.get(st, STATUS_STYLES["working"])
                desired_pills[f"herdr:{p['pane_id']}"] = {
                    "value": full_pane_pill_value(p, t_label),
                    "icon": icon,
                    "priority": priority,
                }
            else:
                folded_by_tab.setdefault(tid, []).append(p)

        for t in tabs:
            tid = t["tab_id"]
            idle_panes = folded_by_tab.get(tid) or []
            if not idle_panes:
                continue
            t_label = t.get("label") or f"T{t.get('tab_index', 1)}"
            tokens = [short_pane_token(x) for x in idle_panes]
            joined = ", ".join(tokens[:5])
            if len(tokens) > 5:
                joined += f" +{len(tokens) - 5}"
            desired_pills[f"herdr:tab:{tid}"] = {
                "value": f"{t_label} (⏸{len(idle_panes)}): {joined}",
                "icon": "pause.circle",
                "priority": 35,
            }

    stale_keys = sorted(existing_herdr_keys - set(desired_pills.keys()))

    # Build concise description that never overflows cmux sidebar width
    summary_parts = []
    if blocked_tokens:
        summary_parts.append(f"⚠️{len(blocked_tokens)}({','.join(blocked_tokens[:2])})")
    if active_tokens:
        shown = ",".join(active_tokens[:2])
        if len(active_tokens) > 2:
            shown += f"+{len(active_tokens) - 2}"
        summary_parts.append(f"🔨{len(active_tokens)}({shown})")
    if done_count:
        summary_parts.append(f"✅{done_count}")
    if idle_count:
        summary_parts.append(f"⏸{idle_count}")
    if not summary_parts:
        summary_parts.append("empty")
    description = f"herdr [{len(tabs)}T/{len(panes)}P]: " + " · ".join(summary_parts)

    return {
        "desired_pills": desired_pills,
        "stale_keys": stale_keys,
        "description": description,
        "folded": len(panes) > max_expanded,
    }


def list_existing_herdr_keys(workspace: str, env: dict[str, str] = BASE_ENV) -> set[str]:
    out = run_cmd(["cmux", "list-status", "--workspace", workspace], env=env)
    keys: set[str] = set()
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("herdr:") and "=" in line:
            keys.add(line.split("=", 1)[0])
    return keys


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
            # Lightweight process-info lookup for unknown panes
            try:
                pinfo = json.loads(
                    run_cmd([herdr_bin, "--session", session, "pane", "process-info", "--pane", pid], env=env)
                )["result"].get("process_info") or {}
                for proc in pinfo.get("foreground_processes") or []:
                    nm = os.path.basename(str(proc.get("argv0") or proc.get("name") or "")).lower()
                    if "codex" in nm:
                        eff_kind = "codex"
                        break
                    if "claude" in nm and "clauder" not in nm:
                        eff_kind = "claude"
                        break
                    if nm in ("agy", "agy-us1", "antigravity"):
                        eff_kind = "agy"
                        break
                    if nm == "grok":
                        eff_kind = "grok"
                        break
                if not eff_kind:
                    eff_kind = "shell"
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
                "terminal_title": p.get("terminal_title_stripped") or p.get("terminal_title"),
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

    existing_keys = list_existing_herdr_keys(workspace, env=BASE_ENV)
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
        or (isinstance(existing_desc, str) and existing_desc.startswith("herdr"))
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
        "folded": plan["folded"],
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
