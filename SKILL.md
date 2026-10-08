---
name: multi-agent-collaboration
description: Coordinate context-bearing CLI agents in macOS cmux using scoped task packs, authenticated acknowledgements, evidence-bound callbacks, review gates, and bounded failure recovery. Requires cmux; not a general-purpose parallel-agent launcher.
---

# Multi-Agent Collaboration

Specify the executor provider on every new preflight: `--executor claude` for
Claude Code, or a provider on every surface such as `--executor-surface surface:24=claude`.
The harness rejects omitted providers before discovery or input; it no longer
silently defaults to Codex. This declaration does not replace live UUID checks
or the executor's truthful ACK. A wrong-provider challenge is a supervisor
configuration failure, not executor unavailability: preserve the failed attempt,
close its marker at the verified terminal boundary, then use a fresh task/nonce
with the correct provider. Never edit an old receipt or ask the peer to claim a
false identity. Existing healthy, correctly bound handshakes remain reusable.

For the measured Codex `Queued follow-up inputs` display, see
[queue classification and bounded recovery](references/queued-followup-inputs.md).
Queued is pending consumption, never compose failure or confirmed completion.

For zero-input identity refusals, read [native caller inventory failures](references/native-caller-inventory.md).
Repair discovery before retrying a handshake; retain kernel/UUID authentication
and distinguish local bridge failure from executor availability.

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

## Same-workspace handshake: mandatory transport boundary

Before discovery, naming, bridge-test, handshake, task dispatch, callback, paste
or key submission, resolve the **live caller** from `cmux identify --json` and
join it to `cmux tree --all --json --id-format both`. Compare nonempty workspace
UUIDs, not labels, titles, focused panes, remembered refs or inherited summaries.
Both participants must be terminal surfaces in different panes of that workspace.

A user-designated executor is an additional exact UUID constraint. Supply
`--expected-workspace-uuid` and repeatable `--expected-executor-uuid` to the harness.
If the designation conflicts with the live caller, refuse input immediately;
do not select an old peer, alter environment identity, move a surface, launch a
replacement, or claim a successful handshake. Existing sessions remain intact.

`scripts/cmux_workspace_guard.py` provides the shared fail-closed check.
`cmux_agent_panel_guard.py` invokes it before help/recovery exceptions. Raw
`cmux send/send-key`, `cmux-agent ask/broadcast` and terminal-write RPC paths are
blocked by the hook; use `cmux_bridge`, which rechecks every paste/key and addresses
both workspace and surface by UUID. The harness rechecks saved gate UUIDs before
later phases. Missing identity, ambiguous refs, stale UUIDs, target movement,
unknown cmux state and a changed designated peer deny before input. No force,
message-prefix, timeout, recovery-authority or environment bypass exists.

An optional caller-scoped file at
`~/.local/state/multi-agent-collaboration/workspace-scope/<CALLER_UUID>.json`
preserves an explicit user designation across invocations. Schema:
`{"version":1,"caller_surface_uuid":"UUID","workspace_uuid":"UUID","target_surface_uuids":["UUID"],"authorization_source":"user instruction"}`.
Only a new explicit user designation may replace that intent; do not clear it
because a target is unavailable. The scope narrows access and cannot permit
cross-workspace input. Resource coordination across workspaces uses existing
file/queue receipts, not executor-handshake transport or a STATUS-prefix exemption.

Install via `scripts/manage_install.py`; it registers the guard for both clients.
Run the installed hook with a cross-workspace/raw-input negative case and verify
exit 2 and zero input before claiming enforcement. Client settings registration,
active hook execution and imported Python modules are separate states; a process
that already imported an older transport has not magically hot-reloaded.
There is no atomic cmux snapshot-plus-send API: UUID addressing closes ref reuse,
and a fresh check precedes each input, but this is a supported-transport guard,
not an OS sandbox against arbitrary self-written socket clients.

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
   exit code is not completion. Missing/mismatched receipts remain unconfirmed.
   After the original callback returns, use the bounded closeout path below;
   do not turn receipt reconciliation into unlimited executor work.
6. Independently read raw artifacts before accepting a result. Check current
   pins, actual counts, negative controls, incomplete work, and postconditions.
   Keep failures immutable; publish corrections separately. Update handoff and
   disarm only at the verified terminal boundary.

## Delivery And Monitoring

Use [efficiency and task closeout](references/efficiency-and-closeout.md) to
choose zero, one or two executors, apply phase-specific handshake budgets,
attribute delivery failures, and close accepted work without repeated reviews.

Use [executor closeout enforcement](references/executor-closeout-enforcement.md):
the PreToolUse guard blocks additional tools after a bound report's callback
attempt has returned. Stop permits its exact honest REPORT_READY handoff without
manufacturing delivery confirmation. Supervisor reconciliation and task acceptance
remain separate. Missing/in-flight/changed evidence cannot use this exception.

After verified acceptance/disarm, the supervisor owns
[closeout feedback and the next dependency](references/efficiency-and-closeout.md#核收后把结论和下一步交回执行者).
A normal task boundary is not an API failure or a permanent session stop. Use
current evidence for authorized follow-up; never ask the user to relay to a bound peer.

### Fast handshake and verified delivery in both directions

- Bridge cleanup must observe an empty compose, not merely the absence of the
  full probe. Short per-executor tokens reduce guarded key count; unknown input
  is reread, never blindly deleted. ACK waits remain `AWAITING_EXECUTOR_ACK`,
  not terminal failures. See the executable postconditions in
  [efficiency and closeout](references/efficiency-and-closeout.md).

- A clipped Claude footer is not a user draft. Recognize measured git/progress
  truncation only below a complete bordered editor and known provider footer;
  preserve identical text inside the editor. Recover a failed probe through
  [original probe recovery](references/original-probe-recovery.md), retaining the
  original evidence instead of repeating bridge-test or inventing an ACK.

- Give the executor the absolute pending handshake receipt path in the initial
  challenge. It reads that file and the bound skill, then returns the exact ACK;
  no registry search, task audit or formal consensus is part of a handshake.
  A healthy current-task handshake is reused, not repeated for each status turn.
- Match the observation budget to the harness phase minimum. A shorter local
  observation is a supervisor budget limitation, not executor silence. Recover a
  late genuine ACK against the original task/provider/nonce without resending.
- **Paste is not submission; Enter is not delivery.** For prompts and callbacks
  alike, use the guarded bridge, lowercase `enter`, and a post-key screen check.
  A marker still in compose is not delivered. A queued marker is pending, not
  failed and not confirmed. Only real receiver activity after the marker can
  confirm delivery; a prompt echo or unrelated activity cannot.
- `submit_completion_callback` writes an exclusive attempt journal before
  input, binding task pack SHA, report SHA, nonce and live workspace identities.
  A submitted attempt refuses repeat delivery; `--reconcile-only` is the explicit
  no-input recovery entrypoint. Changed report/pack/peer bindings fail closed. Keep an uncertain
  attempt; do not delete its journal to retry. Older attempts without a journal
  require their original delivery evidence and receiver history; do not create a
  retroactive send intent or manufacture a confirmed receipt.
- A pending intent is not a completion receipt. A returned, bound original attempt
  may satisfy only the honest handoff exception, never a delivery/consensus claim.
  If zero-input refusal is proven, preserve that evidence and fix the input
  classifier before authorizing a successor attempt. Do not treat a classifier
  error as executor unavailability or bypass workspace/compose guards.
- Two user-authorized existing executors are useful for independent bounded
  tasks. Give each a separate task id, nonce, artifact root and write scope;
  shared repositories have one writer. Two tabs in one executor pane are not
  two independent UI channels: serialize input and recheck exact surface UUIDs.
  Supervisors continue independent work while callbacks are pending. Persistent
  executor failures use the authorized solo takeover boundary below.

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
- Retryable Claude API failures: use bounded same-session retries, at least
  60 seconds after the prior attempt ends. Temporary unavailability requires
  at least 300 consecutive seconds of evidenced API failures, starting at the
  first actual failure, plus a fresh failed attempt at or beyond that threshold.
  Any actual API success resets the failure clock; a static screen, queued or
  unknown delivery, and retry counts do not establish that failure interval.
  Billing/authentication/quota failures permit no blind retries and are classified
  separately, as is a user's explicit stop or withdrawal of authorization.
  Continue independent work while waiting. Freeze the executor, stop its sentinel
  and verify no concurrent writers before an already-authorized SOLO takeover;
  recover only in the original session at a safe handoff and fresh handshake.
  Read [availability and shared resources](references/availability-and-resources.md)
  for the evidence rule, v2 initialization, v1 migration and resource leases.
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
- Cross-workspace coordination uses file/queue receipts for resources, never another task's executor or raw terminal input.
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

## 双向投递与高效协作维护

执行[高效握手、多执行者与双向投递](references/efficient-bidirectional-collaboration-20261004.md)：每次Enter后读回；输入框残留、排队与消费分别记录；可能已发送的回调仅只读核收，禁止重贴。

### Stop hook reentry

Stop/SubagentStop with boolean `stop_hook_active=true` exits successfully before task gates to prevent recursion. This does not confirm callbacks, disarm tasks, or bypass checks on the next normal turn. See [Stop hook lifecycle](references/stop-hook-lifecycle-20261005.md).

### Read-only confirmation

For an existing callback journal with an exact native receiver user record,
use the explicit original-lock settlement described below. Preserve the original
task/report/attempt and authenticate the live receiver; never resend to repair a
missing receipt. Evidence validation alone is not receipt publication, and a
published receipt does not prove a later Stop hook or whole-task acceptance.

Use [read-only confirmation and native receipts](references/read-only-delivery-confirmation.md) to distinguish compose, queue, consumption, and report acceptance. Preserve the original sender journal and runtime; the new checker does not authorize retries or silently migrate old attempts.


## 归档双执行者现场经验（2026-10-05）

见[归档回调与独立收尾](references/archive-callback-boundaries-20261005.md)。区分原生入站、正式回执与候选补丁实效；仅文档增量，不替换在途控制器。


## Shared managed-daemon callers

Read [caller identity and bounded callback closeout](references/shared-daemon-caller.md). The live guard resolves the original native client when a managed daemon inherits another terminal environment; it never changes process environment or relaxes UUID checks. Harness marker ownership uses the same resolution.

The unique live native client's UUID selects the caller; unrelated recycled TTY
rows cannot veto it. The selected UUID row must still match its actual TTY and
workspace. Report caller-resolution failures as such, never as Claude identity
failures. Keep the bridge test bounded to the test token and verify compose is
clear; keep prompt/callback Enter checks and original-attempt recovery intact.

For an ordinary-terminal Hook stopped by a root login permission denial, use the
[narrow login boundary](references/root-login-permission-boundary.md). Preserve
the original session and task; a source fix or process probe is not proof that
the original executor has resumed.
