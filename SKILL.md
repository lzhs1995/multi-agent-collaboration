---
name: multi-agent-collaboration
description: Coordinate existing CLI agents through a shared evidence contract, Herdr task profiles or native cmux delivery. Preserve original sessions and registered controllers; not a general-purpose parallel-agent launcher.
---

# Multi-Agent Collaboration

Start with the [shared contract](references/collaboration-contract-core.md),
[four modes & five scenarios](references/collaboration-modes.md), and
[task profile selection](references/collaboration-profiles.md).

- Existing task: retain its registered controller, original attempts and evidence.
- New Herdr task: read [Herdr collaboration](skills/herdr-collaboration/SKILL.md) and use the [Herdr fast-path](references/herdr-fast-path.md) (`herdr agent prompt` with zero extra handshake).
- New plain cmux or independent terminal task: read the
  [cmux collaboration skill](skills/cmux-collaboration/SKILL.md).
- Nested teams, such as chapter teams in separate cmux workspaces: read
  [nested research coordination](references/nested-research-coordination.md).
  Herdr organizes work inside each team; cmux organizes coordination between
  terminal teams. Both entries share one task and evidence contract.
  Check referenced actors, chapter ownership, routes and shared review budgets
  with [federation declaration validation](references/federated-declaration-validation.md).
  A consistent declaration does not establish live identity or delivery.

No environment variable enrolls a task or changes old hooks. Do not switch the
global CURRENT merely to enable Herdr. Source changes, installation, client
loading, native reception and business acceptance are separate evidence.

The Herdr profile adds no periodic reask, ACK loop or required three-round
consensus. New nested tasks use the requested 60-second progress visibility
and bounded review rules in the nested coordination reference; a progress
observation does not resend a prompt or wake a model. Existing explicitly
registered workflows retain their contracts.
Use only useful independent review, preserve drafts and original sessions, and
continue independent authorized work during finite observations.

Read-only profile helper: `python3 -B scripts/collaboration_profile.py route`.
Herdr identity helper: `python3 -B scripts/collaboration_profile.py check-herdr --target /absolute/target.json`.
Explicit Herdr topology & UI naming helper: `python3 -B scripts/herdr_roster.py map --all` (see [explicit naming](references/herdr-explicit-naming.md)).
Offline chapter delta validator: `python3 -B scripts/chapter_delta_validator.py /absolute/delta.json` (graduated from the archived `multi-agent-collaboration-upgrade` sandbox; this repository is the single canonical collaboration skill line).
These helpers send nothing, enroll nothing and do not make check-and-send atomic.

For task-driven expansion, session reuse and returning idle owned panels, read
[team capacity and panel lifecycle](references/team-capacity-and-panel-lifecycle.md).
