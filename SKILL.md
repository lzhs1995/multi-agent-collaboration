---
name: multi-agent-collaboration
description: Coordinate context-bearing CLI agents in macOS cmux using scoped task packs, authenticated acknowledgements, evidence-bound callbacks, review gates, and bounded failure recovery. Requires cmux; not a general-purpose parallel-agent launcher.
---

# Multi-Agent Collaboration

用户授权的正常继任握手、新进度通知与独立新任务联络默认允许，无需旧主管批准或
无关旧任务结算。复用原会话与健康握手，旧次证据单独保留；不再让无关 pending
永久阻塞新联络。完整边框外的 ASCII `...` 截断路径及任务摘要按 footer 识别。

**Version: 0.4.32.** Adds file scheduling across reviewed releases, recoverable
registration, formal reservation boundaries, a quoted-launch classification fix
and explicit recovery after proven zero-input preflight exhaustion. The full
offline suite passed 1,846 tests with zero failures or errors. Installation and
active-client adoption require separate live evidence. The 0.4.31 maintenance
notice recovery contract is retained.

The released baseline retains the removal of session-wide workflow blocks and recognizes
the measured wrapped Claude footer, split cwd/elapsed footer rows and tool-count overflow. Ordinary document
heredocs and quoted search text do not constitute terminal writes. Global workflow hooks are
non-blocking observers: missing identity, old task enrollment, unresolved callbacks,
review rounds and executor reasks must never prevent tools, replies or Stop. The
installed advisory entrypoint records automatic invocation and exits zero; it does
not run the historical blocking checker. Explicit senders still protect existing
drafts, avoid duplicate input and verify complete native reception.

New Codex-bound messages, task packs and callbacks use the authenticated native
control socket of the already-loaded receiver thread. Active turns receive one
`turn/steer` with the fresh expected turn id; idle threads receive one `turn/start`
without settings overrides. Persist `NATIVE_INPUT_INTENT` and a fresh EOF fence
first. No composer read, paste, Enter or Tab is involved. RPC acceptance alone
does not prove reception. An uncertain result permits only original-record
observation, never a retry or queue fallback. Historical attempts stay with their
original controller and must not be replayed through this new transport.

The supervisor PostToolUse observer exposes at most eight authenticated file
requests per tool event, rotates fairly and reminds again after at least 60
seconds while unanswered. It commits its cursor only after successful output
flush. The authenticated foreground thread is preserved; actual child identity
is not replaced by an inherited root selector. A child cannot consume its
parent's inbox. Discovery never sends, accepts work or forces a turn, and cannot
wake a truly idle supervisor.
A Claude executor that repeatedly waits for an unavailable old supervisor can
starve an already queued successor notice. For an explicitly authorized recovery,
enroll the original maintenance attempt with `scripts/claude_maintenance_context.py`.
The nonblocking PostToolUse observer verifies the original queue record, full body,
session, transcript and process, then exposes that body at a tool boundary. Read it
before another wait; give the successor your actual write/reservation status.
This is file-context recovery, not a repaste, native user receipt, business task or
old callback settlement. No enrollment means no new behavior. See
[queued maintenance recovery](references/nonblocking-recovery.md#queued-claude-maintenance).
A measured ellipsis-clipped git branch is accepted only at the physical end of
a footer row below matching complete composer borders; real drafts stay protected.

Explicit insufficient-balance errors are billing failures even when the provider
also prints a login hint or HTTP 403. Transport uncertainty and a supervisor's
short observation budget retain priority; neither is an API failure. Classifying
an outage does not restore the provider or prove a safe SOLO takeover boundary.

`cmux-agent self` resolves the same current native caller as the bridge. Raw
`cmux identify` can report the shared daemon's inherited surface and is not a
current-session identity verdict. Never require an old `codex resume` command.
The native resolver supports tools executed directly by the managed daemon after
shell exec optimization; no incidental shell or proxy ancestor is required. It
checks the tool's actual kernel identity and current native foreground selection.

An unrelated legacy client can reuse a PID that still has an older optional
foreground file. Version 0.4.30 treats that file as absent only after validating
its ownership, schema and stable reread, when it predates this process's birth
and this process has never had a verified selection. It never adopts the old
thread. Unreadable, malformed, future-dated or required-but-missing selections
exclude only that candidate; they cannot veto a different authenticated caller
or fall back to resume arguments. Recheck the selected caller strictly and reject
multiple authenticated matches, including a new match during the scan. Unrelated
selection failures and switches never enter the caller proof. Preserve the file
and process identity; do not rewrite them to make discovery pass. See
[caller identity](references/shared-daemon-caller.md#reused-pids-and-optional-foreground-files).

Any successor supervisor authorized by the user may contact the specified existing
executor, including across workspaces. For such communication use
`successor_rebind_cli.py` with a `successor-rebind-v2` maintenance authorization:
user text plus current caller/receiver workspace, surface and pane UUIDs. It does
not require a dead supervisor's approval, old task settlement or fabricated freeze
receipts. This authorizes communication only; old tasks are never replayed and no
shared write scope is transferred. New business work gets its own reviewed scope.

Use the same current release for new calls. Reconcile old attempts under their
original controller. One fresh native user record proves reception; an executor
response proves acknowledgement. Neither alone accepts the work. See
[non-blocking recovery](references/nonblocking-recovery.md).

The user chooses zero, one or two executors. When the original Claude cannot
continue after bounded retries, continue authorized work as Codex SOLO and preserve
its context. Resume collaboration at a safe boundary when it responds.

0.4.29 also holds the existing pane/target delivery mutex throughout a non-submitting
handshake probe. Automatic terminal writers must use the same lock and recheck the
whole composer before every submit. See [input collisions](references/input-collisions.md).

## Core Contract

- Reuse the designated context-bearing session. Never `/clear`, restart, replace,
  or switch models to recover from an API error or compaction failure. If its
  surface disappears, exact-session recovery requires the history-pinned,
  one-time authorization supported by the panel guard.
- Discover the caller workspace with the installed `cmux-agent self` before selecting
  peers. Bind UUIDs and current role-map evidence, not remembered surface numbers,
  titles, or another workspace's focused panel. Claude terminal peers must be visible
  side splits. The explicitly authorized same-pane Codex native route is separate.
  A new peer requires explicit authorization plus both
  `--spawn --spawn-authorized`; never create an agent tab as a shortcut.
- The supervisor owns scope, authorization, final acceptance and evidence review.
  The executor owns the scoped implementation and its truthful result. An ACK
  proves channel possession, never agreement with the supervisor's conclusion.
- This skill grants no production, deletion, deployment, or session-reset
  authority. Preserve concurrent user changes and scope mutations to the task.
- No Claude/multi-agent participation claim without current-task strict identity,
  acknowledgement and validation artifacts. SOLO work must be labelled SOLO.

## Current caller and authorized peers

Resolve the live caller through `cmux-agent self` or `caller_snapshot`, then pin
both endpoints in the current tree. Ordinary messages default to peers in distinct
panes of the same workspace. An explicitly authorized successor or maintenance
coordinator may use `successor-rebind-v2` for the exact designated cross-workspace
peer. Do not infer a new task, shared writer or replacement session from permission
to communicate. Raw terminal writes remain outside the supported sender.

Claude terminal input preserves occupied or unknown drafts. New Codex native
input does not inspect or modify the composer and can steer its active turn.
The conversation and unrelated authorized work continue. Identity, send, native
receipt, executor ACK, callback and acceptance are separate observations.

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

The current contract for both directions, original-attempt recovery, Stop and idle
waiting is [verified native delivery](references/verified-compose-delivery.md).
Historical screen confirmation and separate Enter/Tab retry budgets are superseded.
New ordinary messages exceeding 700 UTF-8 bytes or containing any CR, LF or tab use the shared
[short notice and exact body](references/long-prompt-delivery.md) route. A receipt
for the notice proves neither body reading nor task acceptance. Formal packs and
callbacks keep their task-bound entrypoints. The explicitly user-authorized
60-second new-marker reask remains separate from default bounded idle observation.

For new long ordinary messages and handshake/review challenges, use
[short notices with pinned bodies](references/long-prompt-delivery.md).
The shared helper and harness persist exact UTF-8 bytes before input; the journal
and native hook retain the original body pin. Reception confirms the short notice
only. New formal packs use a dedicated single-line TASK_PACK_V2 notice with the
full pack SHA; callbacks keep their exact task-bound line. Existing attempts keep
their original controller and bytes, including historical multiline notices.

Use [efficiency and task closeout](references/efficiency-and-closeout.md) to
choose zero, one or two executors, apply phase-specific handshake budgets,
attribute delivery failures, and close accepted work without repeated reviews.

After a report is written, preserve its original pack, callback and journal.
Pending closeout is a task status, never a seal on all tools or future communication.
Read diagnostics, receive successor handshakes and perform separately authorized
work normally. Reconcile an old send without repeating its input. Global Stop and
reask hooks do not force additional turns or periodic polling.

After verified acceptance/disarm, the supervisor owns
[closeout feedback and the next dependency](references/efficiency-and-closeout.md#核收后把结论和下一步交回执行者).
A normal task boundary is not an API failure or a permanent session stop. Use
current evidence for authorized follow-up; never ask the user to relay to a bound peer.

The primary next-task chain is callback → independent acceptance/exact disarm →
prepare the next authorized pack → fresh live preflight → guarded dispatch.
The explicitly authorized 60-second reask is a fallback; file timers cannot wake
an idle model. ANSWERED means a reply was detected, not consumed: await the
original executor's exact consumption without repeating dispatch or replacing
the business episode. Formal reservation pauses remain in force until exact RELEASE.
Measure accepted unique coverage, verified issue closures and actual stage
latency p50/p95 with sample counts; ask_count is diagnostic, not a performance
target. See [dispatch cadence and metrics](references/configuration-and-reasks.md#dispatch-cadence-and-metrics)
for timestamp boundaries and the limits of detected-to-consumed observations.

### Fast handshake and verified delivery in both directions

- Bridge cleanup must observe an empty compose, not merely the absence of the
  full probe. Short per-executor tokens reduce guarded key count; unknown input
  is reread, never blindly deleted. ACK waits remain `AWAITING_EXECUTOR_ACK`,
  not terminal failures. See the executable postconditions in
  [efficiency and closeout](references/efficiency-and-closeout.md).

- Give the executor the absolute pending handshake receipt path in the initial
  challenge. It reads that file and the bound skill, then returns the exact ACK;
  no registry search, task audit or formal consensus is part of a handshake.
  A healthy current-task handshake is reused, not repeated for each status turn.
- After original native reception has been proven, reconcile the existing receipt before waiting again. Preserve the failed receipt, actual reception time and original task/provider/nonce. An earlier observation timeout does not prohibit the executor from returning its genuine ACK; do not generate a replacement nonce for this case.
- Match the observation budget to the harness phase minimum. A shorter local
  observation is a supervisor budget limitation, not executor silence. Recover a
  late genuine ACK against the original task/provider/nonce without resending.
- **Keys and queue entries do not prove reception.** Bind the exact receiver
  process/session/transcript and a fresh EOF fence at the original input intent.
  Only a new whole native user record after that fence, exactly equal to the
  payload including whitespace, yields NATIVE_RECEIVED. Claude queued_command
  remains pending. New Codex input uses `NATIVE_INPUT_INTENT` and native
  steer/start, never the follow-up queue. Claude-bound terminal input retains
  `PASTE_INTENT`, one paste and one key after the complete draft is stable.
  Historical terminal recovery shares at most one additional key under its
  original controller; a native input has no key or resend recovery budget.
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

Supervisors explicitly inspect authenticated active markers and frozen reports.
The installed supervisor PostToolUse observer discovers bound file requests,
not old reports; it neither replies nor settles them. Read the exact request and
write its issued mailbox reply before attempting fresh dispatch to a reasking
executor. This lets its explicit wait return and a fresh live preflight run.
Discovery is not delivery, acceptance or disarm. Idle Stop only observes its
invocation and allows termination; it neither creates notices nor starts reasks.
Explicit `executor_ready.py persist` is read-only and bounded to 300 seconds.
An explicitly authorized `executor_reask.py run` checks every five seconds,
issues a new marker no more often than every 60 seconds, with a polling budget
of at most 540 seconds per invocation. Synchronous I/O can extend wall time.
After the command returns, its durable episode may remain WAITING_REPLY;
that state does not prove a runner is active or registration succeeded. The
separate launchd file scheduler was introduced in 0.4.27. Explicit authenticated
`executor_reask.py start` enrolls the exact task/purpose/workspace/controller;
merely installing the job does not enroll anyone. Each 60-second sweep updates
files only, with a 256-enrollment cap and a 10-second budget.

Version 0.4.32 lets approved CURRENT interpret only reviewed v2 producers for
cross-release file scheduling. It verifies the complete original manifest,
controller and issuer scheduler pins without importing or executing old code.
Original controller, UNKNOWN, terminal_pending and journals remain intact; a
one-time snapshot preserves complete original state/enrollment bytes before the
first cross-release write. File ticks never use transport, transcripts, legacy
idle bindings or reply consumption, and cannot wake a model.

Registration preflight precedes active state/channel changes. A PREPARING intent
is not success: `REGISTRATION_UNCOMMITTED` and `COMMITTED_RECOVERY_REQUIRED` expose
failures on either side of the live-state token commit. Recover the original
transaction; do not start another episode to hide it. An authenticated original
executor can explicitly use `enroll-existing --episode ID --enrollment-sha256 SHA`
for an existing WAITING episode, preserving its controller and pending evidence
without a new episode, tick or consume. SHA means the exact old enrollment bytes
(`ABSENT` only when absent); a stale supervisor binding is never auto-migrated.
See the [file and registration contract](references/configuration-and-reasks.md#cross-release-file-contract).

Verify enrollment, actual sweep execution and supervisor exposure separately.
Also verify automatic hooks, complete body reading and two real continuing
request/reply/consume/rearm rounds in the original sessions. Explicit
`start --file-only` is incompatible with transcript channels; file timers do
not establish model wakeup or grant consumption authority.
Current advisory hooks do not refresh the
legacy idle binding needed by AUTHENTICATED_NEW_DISPATCH. Use a bound supervisor
reply to end the wait; never restart the episode to evade an old pending send.
CCC waits have a deadline; native Goal behavior requires separate evidence.

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
- On uncertain delivery, inspect the original native evidence without resending.
  Missing original binding/fence cannot be backfilled. Screen activity, ACK,
  empty compose and marker absence never substitute for exact native reception.
- Claude footer fields are recognized only below matching full composer borders.
  The observed optional numeric `规则` field is accepted only between `CLAUDE.md`
  and `MCPs`; footer-like draft text and unknown layouts remain protected.
  An empty composer does not authorize clearing an active turn or prove delivery.
- Confirmed product virtual suggestions are not actual compose input. The bridge
  tests exact known shapes in both directions. Unknown text stays occupied.
  Public default is **no force-compose**. Only explicit operator permission may
  enable `--force-compose`; active/queued work still cannot be overwritten.
- Preserve the current model. An optional `MULTI_AGENT_EXECUTOR_MODEL` records
  the task's requested model; it does not silently issue a model-switch command.
- `scripts/render_cmux_agent.py` renders the helper from a pinned baseline.
  Installation must verify the whole helper and same-release adapter with
  `helper-parity`; a matching path or a successful exit is insufficient. Ordinary
  messages use its sole guarded bridge route. Formal task packs and completion
  callbacks retain their dedicated task-bound bridge entrypoints.

## Enforcement And Evidence

Installed workflow observers emit no tool denial or turn-control output. They
record entrypoint, original hook, advisory mode and actual process provenance.
The supervisor file-request observer and any explicit diagnostic use the
client-supported `hookSpecificOutput` envelope; internal `action/results` objects
are not valid top-level hook output. Actual automatic invocation in an active
client is separate from a manual script test and from native delivery evidence.

- Install guards on both clients through `scripts/manage_install.py`; `doctor`
  checks configuration and benign execution. Installation is not proof that an
  already-running client reloaded its configuration. Run harness `guard-check`
  after client settings changes and at task close.
- Require semantic positive and negative tests through real hook entrypoints,
  not only helper definitions or source-string counts. Workflow hook failures
  must leave tools and Stop available. Explicit senders and receipt validators
  reject missing evidence, changed pins and malformed receipts without claiming
  delivery or imposing a conversation-wide lock.
- Evidence roots are explicit or registry-bound, never guessed from cwd. A
  missing read is unmeasured, not zero. Parse structured data and check producer
  semantics before declaring a defect. Record mutation hit counts in test probes.
- Cross-workspace resource coordination uses file/queue receipts. User-authorized
  successor communication with a specified existing executor uses `successor-rebind-v2`;
  it does not transfer another task's shared write scope or permit raw terminal input.
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

执行[原生投递与有界等待](references/verified-compose-delivery.md)：新 Codex 输入经原会话的 native steer/start，先落 NATIVE_INPUT_INTENT 及新鲜 EOF fence，不操作 composer、不入 Tab 队列。原 fence 后完整 native user 才确认收到；RPC 接受与业务接受分列。未知结果只读核原次，历史排队 callback 不转发重放。Claude 终端与旧次保留原控制器。idle Stop 仅观察；主动求派依原授权显式运行。

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

### Single-line new input

All fresh pastes pass `cmux_prompt_reference.require_inline`: at most 700 UTF-8
bytes with no CR, LF or tab. Ordinary and handshake bodies exceeding that
constraint use the single-line MESSAGE_REFERENCE_V2 notice. Formal dispatch
uses `task_pack_notice` through `submit_task_pack`, validating the entire frozen
pack before input; it cannot be hidden in an ordinary reference. The callback
retains its dedicated exact line and refuses an oversized or multiline value.
Legacy V1 four-line notices are readable only for original-attempt reconciliation.
No new representation can be used to replay a previous attempt.

Claude may fold a multiline paste or expand a tab even when Enter returns success.
Keep full stable-draft matching and exact new native user proof; never infer
delivery from keys, partial text, queue banners or composer clearing. Live
acceptance records the callback and automatic hook invocation in the actual
client; running a hook manually proves only the invoked test.

## Consume pending supervisor requests and repair zero-input notices

At a major tool boundary, before a new long batch, and before reporting progress,
inspect the current supervisor surface for queued follow-up inputs. Verify each
pinned UTF-8 body and SHA, read it, and reply to its exact mailbox. Record consumed
and pending items; duplicate notice markers do not need duplicate replies. A UI
queue, body reading, native receipt, ACK and business acceptance are separate.
Do not leave maintenance requests unread while repeating status probes.

For new formal tasks, generate `TASK_PACK_V2` with `cmux_bridge.task_pack_notice`
(or `task-notice --task-pack /absolute/task-pack.json`) from the finalized pack.
Never hand-assemble a multiline task prompt. Since 0.4.23, wire validation happens
before a task journal is reserved. The explicit `scripts/repair_task_notice.py`
coordinator handles only a first `NO_INPUT` wire-format rejection with `events=[]`.
It verifies the pinned original attempt, original payload and unchanged pack;
requires the same caller, receiver process/session and original lock; and uses
the complete original immutable controller for terminal input and native proof.
The read-only default must return `READY_ZERO_INPUT` before `--apply`. It appends
only the original journal's one second attempt, retaining the original bytes,
completion nonce and callback controller. A crash consumes this budget. Pasted,
queued, uncertain, changed or already-received attempts cannot use this route.
The corrected TASK_PACK_V2 marker is the task id; this is not a new business task.

Version 0.4.32 adds `scripts/resume_task_no_input.py` for an original Claude
task dispatch whose bounded preflight exhausted attempts without input. It
defaults to a read-only plan: `--controller` names the original immutable source,
`--surface` the original executor, `--task-pack` the original pack and
`--text-file` the exact original notice. The plan returns `history_sha256` for
the complete contiguous attempt list. Apply requires
`--apply --history-sha256 SHA` matching that reviewed history.

Every historical attempt must be NO_INPUT with `events=[]` and only
COMPOSE_OCCUPIED or RECEIVER_COMPACTING. Original identity, full native binding,
lock, pack, payload and nonce must remain unchanged, and the current composer
must be empty and ready. Preserve every original byte and append one attempt
through the complete original controller. Any PASTE_INTENT, native input intent,
UNKNOWN, queue, receipt or changed history forbids recovery. Preserve a draft
encountered again; never clear it. Ordinary automatic preflight remains bounded
to two attempts, but proven zero input does not exhaust an actual-send budget.
This explicit route removes only that proven preflight deadlock; it never
authorizes an uncertain resend. Verify installation and live use separately.

A busy Claude may expose a `queued_command` attachment while working on a task.
Preserve that observation without declaring an exact native-user receipt. Never
resend merely because an ACK or report preceded that record. Executors must wait
for a finalized, hash-pinned task notice rather than treating a draft pack found
on disk as authority. Continue independent authorized work in SOLO when a peer
is unavailable; maintenance does not reopen accepted research reviews.

0.4.24 also recognizes local SQLite and disk-space inventory imports in quoted Python document heredocs. Agent names after semicolons inside literal document data are not CLI launches; adjacent real shell launches remain checked. Verify the exact rejected command through the real hook before claiming recovery.

Version 0.4.32 fixes the residual launch-classifier case where a single-quoted
message containing `; Codex alone` was parsed as an adjacent command. Preserve
the original message: classify literal data separately from shell code. Real
adjacent agent CLIs, executable nested substitutions and wrapper command
arguments still receive launch checks. Use the exact command with zero-input
`--check-command` and the real hook entry in isolated tests; this is additional
coverage beyond the earlier 0.4.24 literal and 0.4.27 heredoc fixes.

## Opt-in continuing inquiries

0.4.28 adds explicit `--continuing` authorization across bound replies and child
task boundaries. Read [the continuing contract](references/configuration-and-reasks.md#continuing-authorization-0428).
ANSWERED remains unconsumed; only the authenticated original executor's
original controller may perform `consume --reply-sha256` and rearm the same
authorization. CURRENT file compatibility grants no consumption authority.
The [formal reservation boundary](references/configuration-and-reasks.md#formal-reservation-boundary)
also protects ordinary WAITING episodes, including unsupported 0.4.27 producers.
A valid same-supervisor reservation pauses requests; only its exact RELEASE can
remove that pause while authority remains valid. An already-bound reply can
still be discovered as ANSWERED during the pause. The authenticated original
executor may consume it through the original controller, but consumption never
clears the reservation or issues another request before RELEASE.
A legitimate new supervisor's
reservation for the original caller/workspace, newer than the episode, ends the
old inquiry with `STOPPED / FORMAL_OWNER_CHANGED`. Unrelated or invalid markers
grant no authority. Marker disappearance, heartbeat and TTL do not release a
reservation or authorize a successor. Stop, expiry and revocation end authority;
noncontinuing ANSWERED/CONSUMED and STOPPED episodes are never revived by this
boundary. Original controllers and pending evidence remain unchanged.

### Observe an original late handshake ACK

`scripts/observe_handshake_ack.py` is a bounded, read-only observer. Supply the
original immutable `--controller`, `--receipt` and `--receipt-sha256`, original
dispatch `--attempt` and `--attempt-sha256`, and exact notice `--text-file`.
It returns a separate observation and preserves the original timeout, task,
nonce and send journal. It sends no input and does not settle or rewrite the
original receipt. Exact notice reception, complete referenced body reading,
the executor's ACK and business acceptance are separate claims.

同pane原生Codex的显式线程授权、继任迟到ACK核收与20GB主动空间管理，见
[协作效率与收尾](references/efficiency-and-closeout.md)。普通终端侧窗格门禁保持。
