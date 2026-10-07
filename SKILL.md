---
name: multi-agent-collaboration
description: Coordinate context-bearing CLI agents in macOS cmux using scoped task packs, authenticated acknowledgements, evidence-bound callbacks, review gates, and bounded failure recovery. Requires cmux; not a general-purpose parallel-agent launcher.
---

# Multi-Agent Collaboration

Use this skill for an explicitly requested supervisor/executor workflow. Do not
start agents merely because this skill is being installed, edited, or reviewed.
Requires Python 3.10+ and macOS cmux for live coordination. Offline tests also run
on Linux. Other terminals and agent-native sessions are not certified transports.

## Core Contract

- Reuse the designated context-bearing session. Never `/clear`, restart, replace,
  or switch models to recover from an API error or compaction failure. If its
  surface disappears, exact-session recovery requires the history-pinned,
  one-time authorization supported by the panel guard.
- Discover the caller workspace with `cmux identify --json` before selecting
  peers. Bind UUIDs and current role-map evidence, not remembered surface numbers,
  titles, or another workspace's focused panel. Existing peers must be visible
  terminal side splits. A new peer requires explicit authorization plus both
  `--spawn --spawn-authorized`; never create an agent tab as a shortcut.
- The supervisor owns scope, authorization, final acceptance and evidence review.
  The executor owns the scoped implementation and its truthful result. An ACK
  proves channel possession, never agreement with the supervisor's conclusion.
- This skill grants no production, deletion, deployment, or session-reset
  authority. Preserve concurrent user changes and scope mutations to the task.
- No Claude/multi-agent participation claim without current-task strict identity,
  acknowledgement and validation artifacts. SOLO work must be labelled SOLO.

## Workflow

1. Read [supervisor protocol](references/supervisor-protocol.md) when coordinating,
   [executor protocol](references/executor-protocol.md) when receiving work, and
   [review standard](references/consensus-audit-standard.md) when conducting review.
   This entrypoint overrides historical personal defaults in those references.
2. Run `scripts/mac_harness.py preflight` with an explicit task id, absolute
   artifact root, and the existing executor surface. This covers setup, identity,
   visible naming, non-submitting bridge proof, handshake and validation. Do not
   refresh a healthy handshake mid-command or stack another task behind work.
3. For formal plan consensus, record at least three non-blocking review rounds
   and require `consensus-check` PASS. Each round needs its own real artifact and
   hash. Earlier objections must be explicitly resolved, not hidden by a later
   PASS. Never pre-fill the executor's verdict. Do not impose consensus on a
   user-authorized solo task or call solo review multi-agent consensus.
4. Finalize a durable task pack with objective, allowed changes, prohibitions,
   input identities, verification, authorization source and completion template.
   Require `required_skill` to resolve to this installation's SKILL.md.
   Send only through `cmux_bridge.submit_task_pack`, explicitly including
   `READ_AND_OBEY_REQUIRED_SKILL_FIRST`. The executor must read this skill.
5. Completion is report-first, callback-second. The executor must actively call
   `cmux_bridge.submit_completion_callback` to send the pack-bound task id,
   nonce and report hash. A written report, screen DONE, prompt echo, or helper
   exit code is not completion. The Stop hook rejects missing/mismatched receipts.
6. Independently read raw artifacts before accepting a result. Check current
   pins, actual counts, negative controls, incomplete work, and postconditions.
   Keep failures immutable; publish corrections separately. Update handoff and
   disarm only at the verified terminal boundary.

## Delivery And Monitoring

Follow [bounded handshakes, delivery and closeout](references/bounded-collaboration.md)
for each dispatch. Keep business acceptance, callback confirmation and resource
release separate. Once the original callback is verified and its task marker is
disarmed, stop that task's callback probes; transport maintenance is a separate
scope. Use a second executor only for an independent unresolved deliverable.

Read [receiver input and delivery](references/receiver-input-and-delivery.md)
when a surface may have returned to a shell or a send is unconfirmed. Current
input type, submission, consumption, agreement and accepted output are separate
facts. The bridge refuses SHELL/UNKNOWN before sending and preserves user drafts.

- Normal supervision is callback-first: one sentinel per task, 7200-second stable
  cadence, 1800 seconds for medium-risk work, 300-600 only for bounded high-risk
  windows. Do not spend tokens polling a healthy peer every few seconds.
- Active compaction is different: sample at 60 seconds, act after two unchanged
  samples; explicit thrashing is immediate failure. Freshly read the same surface,
  interrupt the failed compact once with Esc, preserve/disarm work, then permit
  one narrow same-session compact only within operator authority. Never clear.
- Retryable 5xx: 60 seconds, at most three outer retries after the prior attempt
  ends. Billing/authentication failures: zero retries. Stop the sentinel and
  record unavailability. Use `executor_availability.py` for authorized SOLO
  takeover and phase-boundary handback; never allow concurrent writers.
  When the user has authorized automatic takeover, continue the task locally
  after freezing the unavailable executor; do not stop the whole task. Read
  [availability and shared resources](references/availability-and-resources.md)
  for v2 initialization, v1 migration, exact-session recovery and resource leases.
- Pasting is not submission. Confirm lowercase `enter` and new receiver activity
  using the bridge's delivery classifier. Prompt echo, stale callbacks and marker
  absence are not proof. On ambiguous delivery inspect before any resend.
- Confirmed product virtual suggestions are not actual compose input. The bridge
  tests exact known shapes in both directions. Unknown text stays occupied.
  Public default is **no force-compose**. Only explicit operator permission may
  enable `--force-compose`; active/queued work still cannot be overwritten.
- Preserve the current model. An optional `MULTI_AGENT_EXECUTOR_MODEL` records
  the task's requested model; it does not silently issue a model-switch command.
- The external `cmux-agent` helper is not shipped. Check `helper-parity` before
  trusting it. The in-repo bridge is authoritative for task/callback delivery.

## Enforcement And Evidence

- Install guards on both clients through `scripts/manage_install.py`; `doctor`
  checks configuration and benign execution. Installation is not proof that an
  already-running client reloaded its configuration. Run harness `guard-check`
  after client settings changes and at task close.
- Require semantic positive and negative tests through real hook entrypoints,
  not only helper definitions or source-string counts. Missing/null/boolean-as-
  count, stale evidence, changed pins and malformed receipts must fail closed.
- Evidence roots are explicit or registry-bound, never guessed from cwd. A
  missing read is unmeasured, not zero. Parse structured data and check producer
  semantics before declaring a defect. Record mutation hit counts in test probes.
- Cross-workspace coordination assigns resources, not another task's executor.
  Word/Zotero share one serial resource; NotebookLM sharing is account-wide.
  A lease, an actual OS lock and a task-bound drain receipt are distinct facts.
  Unknown Word document counts and unknown remote query outcomes prevent release.
- Domain-specific Stata experiments do not belong in this generic contract.
  See [lessons and test mapping](references/lessons.md) for reusable findings;
  the [incident archive](references/sentinel-and-compaction-incidents.md) records
  historical observations, not current model availability or deployment proof.
- `schemas/` contains versioned contracts; active-marker version 1 is unchanged.
  Capability data is observation-only: no ACP interoperability claim until two
  endpoints have actually been measured. Unknown capabilities remain null.
