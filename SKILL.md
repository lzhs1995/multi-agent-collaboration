---
name: multi-agent-collaboration
description: Coordinate existing CLI agents through a shared evidence contract, Herdr task profiles or native cmux delivery. Preserve original sessions and registered controllers; not a general-purpose parallel-agent launcher.
---

# Multi-Agent Collaboration

Start with the [shared contract](references/collaboration-contract-core.md) and
[task profile selection](references/collaboration-profiles.md).

- Existing task: retain its registered controller, original attempts and evidence.
- New Herdr task: read [Herdr collaboration](skills/herdr-collaboration/SKILL.md).
- New plain cmux or independent terminal task: read the
  [cmux profile](references/cmux-collaboration-profile.md), then the
  [complete existing cmux guidance](references/cmux-legacy-entry.md) for its
  supported harness and delivery contract.

No environment variable enrolls a task or changes old hooks. Do not switch the
global CURRENT merely to enable Herdr. Source changes, installation, client
loading, native reception and business acceptance are separate evidence.

The Herdr profile adds no periodic reask, ACK loop or required three-round
consensus. Existing explicitly registered workflows retain their contracts.
Use only useful independent review, preserve drafts and original sessions, and
continue independent authorized work during finite observations.

Read-only profile helper: `python3 scripts/collaboration_profile.py route`.
Herdr identity helper: `python3 scripts/collaboration_profile.py check-herdr --target /absolute/target.json`.
These helpers send nothing, enroll nothing and do not make check-and-send atomic.
