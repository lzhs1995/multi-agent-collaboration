---
name: herdr-collaboration
description: Coordinate existing context-bearing agents in Herdr using bounded tasks, versioned evidence and explicit acceptance. Use when the user requests Herdr collaboration and HERDR_ENV=1; retain registered legacy task controllers.
---

# Herdr collaboration

For a new collaborative task, read the shared
[contract](../../references/collaboration-contract-core.md) and
[profile rules](../../references/collaboration-profiles.md). An existing registered
task retains its controller and original attempts; moving inside Herdr does not
migrate it.

1. Verify `HERDR_ENV=1`, read `herdr --skill` and check `herdr status`. The installed
   CLI is the command authority. Do not copy an online or newer command syntax.
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

`scripts/collaboration_profile.py check-herdr --target <absolute-json>` from the
complete MAC source performs a read-only check. Target fields are `endpoint`
(`transport=herdr`, absolute `socket`), `pane_id`, `terminal_id`, and
`native_session_id`. It sends nothing and does not make check-and-send atomic.
The helper currently checks the inherited local server only; use the official
explicit server selector for other servers and preserve endpoint identity.

Cross-workspace on one server can stay in Herdr. Across servers preserve the
explicit endpoint/selector, not just a pane name. For independent terminals use
the [cmux profile](../../references/cmux-collaboration-profile.md). Never type a
peer message into the outer cmux terminal hosting Herdr.

In scientific work, read the domain skill as well. Assign chapter ownership,
share milestone deltas through chapter supervisors, and serialize global
Word/Zotero writes. JEV is optional future ranking assistance; it does not verify
identity, source truth, locks or completion.
