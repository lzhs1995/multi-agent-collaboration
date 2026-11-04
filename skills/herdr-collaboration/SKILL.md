---
name: herdr-collaboration
description: Coordinate existing context-bearing agents in Herdr using bounded tasks, versioned evidence and explicit acceptance. Use for requested Herdr collaboration in the inherited service or an explicitly designated existing local service; retain registered legacy task controllers.
---

# Herdr collaboration

For a new collaborative task, read the shared
[contract](../../references/collaboration-contract-core.md) and
[profile rules](../../references/collaboration-profiles.md). An existing registered
task retains its controller and original attempts; moving inside Herdr does not
migrate it.

This is the inward skill for an existing team. For a chapter team inside a cmux
workspace, read [nested research coordination](../../references/nested-research-coordination.md):
the chapter supervisor delegates bounded work, consumes executor results and
reports chapter deltas to the senior supervisor through the
[outward skill](../cmux-collaboration/SKILL.md). It defines the four modes and the
five shared rules, including task-scoped 60-second progress visibility and a
three-round ceiling for ordinary revision. Do not duplicate that contract here.

1. For the inherited service, verify `HERDR_ENV=1`. An outer caller may instead
   inspect an explicitly user-designated existing local service with a fixed
   `--session <name>` selector; never fabricate inherited environment or use a
   focused service by default. Read `herdr --skill` and check `herdr status` with
   that same selector when selected. The installed CLI is the command authority.
   Do not copy an online or newer command syntax.
2. Discover original participants through `herdr agent list`. Pin the server
   endpoint, pane ID, terminal ID and `agent_session.value` with `kind=id` in the
   task evidence. Names, focus and outer cmux surfaces do not establish identity.
3. Before a message, recheck that identity and actual input state. Use the
   official `agent prompt` route; no second sender framework. Preserve real
   drafts and dialogs. Existing user authorization applies to known approval
   questions; unknown questions remain pending. Never reset a peer to fix delivery.
4. Send a concrete question directly. For an explicitly required formal artifact,
   send its task ID and pinned input/assignment path; the receiver reads the body.
   Ordinary chat does not require a report file. Use the official history fallback
   only if a completed response cannot otherwise be recovered.
5. Observe with finite native waits, at most 30 seconds per foreground observation
   and within the task's original deadline. `--until blocked` is only for waiting
   for a question. A settled old turn or `idle/done` does not prove this assignment
   or background work finished. A timeout/stall never authorizes a blind resend.
6. Confirm the original receiver's new native message when claiming reception;
   read its response or output before integrating it. The supervisor records
   the actual scoped decision, outstanding items and next dependency. No ACK loop.

Keep the actual native representation: a queue enqueue, rendered mid-turn
attachment and independent user turn are different records. If a client renders
a prompt as a queued-command system reminder, record that evidence separately;
do not label it a legacy native-user receipt or replay the prompt to manufacture
one. A discovered file plus a substantive reply can establish body reading and
result availability without proving the original notification was received.

For inner Claude callers, read the [observed caller boundary](../../references/herdr-caller-boundary.md).
Receiver pinning and a completed main-session tool result do not authenticate a
future sender. Preserve unknown branch identity; PID, session ID and inherited
environment alone are insufficient.

`python3 -B scripts/collaboration_profile.py check-herdr --target <absolute-json>` from the
complete MAC source performs a read-only check. Target fields are `endpoint`
(`transport=herdr`, absolute `socket`), `pane_id`, `terminal_id`, and
`native_session_id`. It sends nothing and does not make check-and-send atomic.
Add `--session <name>` for a designated existing local service, including an
authorized caller outside Herdr. The helper checks actual status socket/session
and compatibility before the agent list; every command keeps the selector.
Without a selector, inherited `HERDR_ENV` and socket remain required. This helper
does not implement remote `--machine` forwarding. For that separate capability,
use the official supported selector and preserve machine plus endpoint identity.

Cross-workspace on one server can stay in Herdr. Across servers preserve the
explicit endpoint/selector, not just a pane name. For independent terminals use
the [cmux profile](../../references/cmux-collaboration-profile.md). Never type a
peer message into the outer cmux terminal hosting Herdr.

In scientific work, read the domain skill as well. Assign chapter ownership,
share milestone deltas through chapter supervisors, and serialize global
Word/Zotero writes. JEV is optional future ranking assistance; it does not verify
identity, source truth, locks or completion.
