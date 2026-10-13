# Global hooks, provider changes and executor follow-ups

The shared collaboration release owns transport and communication hooks. Other
skills and global CLAUDE.md link here; they do not implement another sender or
copy hook code. Pin one complete immutable release for each new task. An
in-flight task keeps its original controller, task pack, journal and receipts.

This reference describes **0.4.32** behavior. See [CHANGELOG](../CHANGELOG.md)
for the final offline validation. Installation, actual scheduler
execution, automatic hooks and original-client adoption require separate live
evidence; offline results do not establish them.

## Task scope is explicit session enrollment

Installing this skill or its hooks globally does not enroll every conversation.
Ordinary new Codex/Claude sessions need no supervisor and must retain normal tool
use and turn completion. Reading, editing or auditing this skill is not enrollment.
Only an explicitly requested collaboration uses the identity gate to pin the
designated participants' native session IDs, workspace UUIDs, surface UUIDs and roles
in the task marker. Scope is checked before caller authentication or task gates.
A shared workspace, inherited daemon environment, reused surface or unrelated
active marker cannot impose another task's rules on a new conversation.

All installed workflow PreToolUse, PostToolUse and Stop registrations use the
same-release `cmux_workflow_advisory.py --hook <original-hook.py>`. They record
invocation provenance and exit zero, including for enrolled callers. They do not
invoke the old checker, enforce closeout gates, discover old reports, send notices,
force turns or publish receipts. The supervisor PostToolUse observer may expose
authenticated file requests through supported additionalContext. Task markers describe responsibility and
evidence; they cannot seal an entire conversation.

Explicit native-delivery, task and resource operations retain their own identity,
draft and evidence checks. A failed operation does not authorize a callback,
acceptance or resource release, and does not block unrelated ordinary work.

Legacy markers without native session pins are retained as evidence, but cannot
bind a newly started native conversation. Never fill their identity fields using
a new session or the currently focused pane. An existing supervisor may migrate
only from the original authenticated task/session evidence at a safe boundary;
an unresolved original binding is reported separately from ordinary-session access.
No marker, report, callback or research acceptance is deleted or manufactured.

## Configuration survives provider changes

Claude hooks are executable registrations in `.claude/settings.json`, not in
Markdown. A cc-switch profile can replace that JSON. Keeping rules in CLAUDE.md
is necessary guidance but cannot guarantee hook execution.

`scripts/configuration_guardian.py` provides one launchd job,
`org.multi-agent-collaboration.configuration-guardian`. It runs on login,
configuration-directory changes and every 60 seconds. It authenticates the full
release manifest and Python before importing the same-release installer, then
uses the shared `install.lock`. It makes no terminal input or API request.

The guardian restores only this project's hooks in the live Claude/Codex files
and all cc-switch Claude profiles, including newly imported profiles. It removes
duplicate owned registrations from the common Claude snippet. It preserves
provider selection, credentials, model settings, foreign hooks and other fields.
Profile changes commit in one SQLite transaction; live JSON replacements compare
the original bytes before atomic replacement. One previous-hooks snapshot per
client bounds backup growth. Never copy a provider profile over the live config.

The release manager supplies
`~/.local/state/multi-agent-collaboration/CURRENT.json` with `source`, `python`
and `manifest_sha256`. Source must be the complete read-only
`~/.local/share/multi-agent-collaboration/releases/<id>/source` tree; its sibling
MANIFEST.json pins every member's `sha256` and `bytes`. Run the pinned guardian
with `reconcile` and `install-job` to review, then repeat with `--apply`.
`install-job` replaces the same job; do not create a watcher per session.

Read `configuration-guardian-v1/STATUS.json` under the shared state directory.
`HOOKS_DISABLED_EXTERNALLY`, a manifest mismatch or a concurrent installer is a
real boundary, not a successful restoration. An explicit disableAllHooks is
reported, never silently overridden. Changes are eventually repaired; the
guardian cannot guarantee uninterrupted registration during every external write.
Alternate config roots require explicit registration; do not claim coverage of
uninspected roots or a client started with settings sources disabled.

## Explicitly authorized 60-second inquiries

The default `executor_ready.py persist` observes one request for a finite budget.
Only an explicitly authorized `executor_reask.py` episode issues new inquiries.
Its explicit runner checks the same episode and issues at most one new marker
every 60 seconds while executing. A call polls every five
seconds with a polling budget of at most 540 seconds; synchronous I/O can extend
wall time. The durable episode may still be waiting after the command returns.
Resume that same episode explicitly when needed, never reset its
pending send or claim an exited runner is active. A bound supervisor reply or
operator stop ends the wait. `--executor-reask` registers its advisory observer only;
Stop never enrolls an episode or launches the loop. An API outage, killed process
or paused client can prevent execution; expose that state instead of promising
that software can never stop.

The explicit CLI uses the nonblocking caller lock and an authenticated existing
idle binding where needed. Do not reopen an operator stop or take another supervisor's
executor. The legacy AUTHENTICATED_NEW_DISPATCH exit requires a newer idle binding
matching caller, workspace and supervisor; current advisory hooks do not write
that binding. Do not promise this exit merely because a new task pack exists.
Ordinary completion never replays an accepted callback.

Each inquiry gets a new issued marker, preserving the same task/episode binding.
The original unconfirmed terminal attempt remains durable even after the bounded
history rotates. Read that original journal first. While it remains queued,
stranded or uncertain, later inquiries use files only: never paste another prompt
over it, change its nonce, or treat queue status as native receipt.

Each round updates configured file channels plus one durable supervisor inbox
entry. Track checks, attempts, successful file writes, actual pastes and exact
native receipts separately. Empty/failed channels are visible. A file write does
not prove the supervisor read it. The supervisor explicitly consumes the bounded
inbox at task/tool boundaries, verifying request paths, body hashes, reply
mailboxes and issued templates. From 0.4.27, the registered supervisor PostToolUse
observer authenticates the actual current hook thread and offers at most eight
file requests per event, with fair rotation and reminders after at least 60
seconds while unanswered. It commits discovery cursors only after a successful
output flush. Preserve authenticated foreground.thread_id; record actual,
inherited and parent ids separately. A child cannot use an inherited root id to
consume its parent's inbox. It uses the supported hookSpecificOutput/additionalContext
envelope, does not invoke the old checker and exits zero on all failures. It
does not reply, send terminal input, reconcile old attempts or accept work.
Telemetry failure cannot suppress this separate discovery attempt. A supervisor
with no tool event receives no hook context: this is not an idle-client wakeup.

Reply in the request's mailbox with the complete issued template, or through the
original guarded route as a complete JSON object / `EXECUTOR_REPLY|<JSON>`. The
reply must match `caller_surface_uuid`, `supervisor_uuid`, `task_id`, `episode_id` and
an issued `marker`. A status line quoted in prose is not a reply. TASK is only an
answer to the inquiry; dispatch still requires the formal task-pack contract.
WAITING_DEPENDENCY, SOLO and BLOCKED require a concrete `trigger`; replace the
template placeholder. `queued:true` or `received:false` never ends the episode.
Late replies to issued markers remain valid after history rollover.

The offered `REQUEST` path is a rotating current file, not an immutable archive.
Read the complete bounded regular file once (the `supervisor_inbox.request_bytes`
helper hashes and parses one stable file-descriptor read), verify the offered SHA,
and save those exact bytes in an exclusive task-local snapshot before a long
review. Build the reply from that snapshot's template and original mailbox; do
not reopen the rotating current file to reconstruct the old reply. If the file
changed before the first verified read, make no reply from the mismatched bytes
and read a newly observed request. A later marker does not invalidate an earlier
issued marker in the same unconsumed episode. The observer does not automatically
archive every request or supply a separate reply-writing CLI.

Before fresh dispatch to a reasking executor, the bound supervisor first writes
the exact issued mailbox reply. The explicit runner then observes it and returns;
perform a fresh live preflight before the formal pack. A TASK reply does not
dispatch anything. This sequence avoids waiting for an idle window that the
runner itself prevents. An exited original receiver process does not authorize
forging a new original binding or deleting terminal_pending. Independently
validated historical native reception remains separate from official live
reconciliation; answer the original episode by its mailbox without replaying it.

## Dispatch cadence and metrics

Drive the next task through callback → independent acceptance/exact disarm →
prepare the next authorized pack → fresh live preflight → guarded dispatch.
The supervisor owns this chain. The explicitly authorized 60-second reask is a
fallback for missed coordination; its cadence is neither a dispatch target nor a
latency guarantee. A file timer cannot wake an idle model. Hook exposure still
requires a real tool event and does not prove reading, consumption or acceptance.

ANSWERED records detection, not consumption. Preserve the current business
episode and original pack while the original authenticated executor consumes the
exact reply; pending consumption does not authorize repeated dispatch. A TASK
reply remains separate from a formal task pack. A same-supervisor reservation
keeps inquiries paused until its exact RELEASE. Neither more asks nor a missing
marker justifies releasing that reservation or reopening a terminal episode.

Report useful outcomes and observed delays separately:

- Effective coverage: distinct independently accepted items against an explicit
  eligible scope. State the denominator and remaining gaps; executed test counts,
  repeated checks and duplicate dispatches do not by themselves prove coverage.
- Issue closure: distinct problems with verified resolution evidence. Preserve
  unresolved and reopened issues; ask_count, file_posts and attempts are diagnostics,
  never activity targets or substitutes for accepted work.
- Latency: use actual matched timestamps and publish stage-specific p50/p95 with
  the sample count, observation window and pending/missing samples. Keep
  reply_detected → consumed, callback receipt → acceptance/disarm, and next-pack
  readiness → exact native reception separate. Do not infer missing timestamps
  from scheduler cadence or treat unfinished observations as zero.

Two historical 0.4.31 observations measured reply_detected → consumed at
746.844425 s and 2486.045887 s. At observation time, one continuing inquiry was
paused by a formal reservation; the other was CONSUMED with continuation=null.
These intervals do not measure pure business idle time or establish a cause;
activity and ownership evidence must be assessed separately. Two cases do not
establish representative p50/p95, and these observations are not 0.4.32 live
validation. Keep existing business episodes unchanged when collecting metrics.

## Queues and per-session acceptance

From 0.4.26, new Codex-bound requests use its original loaded thread through the
authenticated native control socket. Active turns use one `turn/steer` with the
fresh expected turn id; idle threads use one `turn/start` without setting overrides.
Persist `NATIVE_INPUT_INTENT` and the fresh EOF fence first. No composer inspection,
paste, Enter or Tab is involved. A failed or uncertain request permits only
original-record observation, never a queue fallback or another submission.
Historical queued messages remain pending under their original controller and
must not be native-replayed. Exact native reception, consumption, execution and
callback settlement are separate measurements. See the [delivery contract](verified-compose-delivery.md).

Record adoption per designated session: resolved release/helper/hook paths;
automatic hook invocation in the current native client; exact native inbound
receipt; real callback and its independent acceptance. Settings counts, manual
doctor runs and offline tests cannot substitute for those observations. Retain
UNVERIFIED or a concrete blocker until evidence exists; never mark every session
passed because shared files changed.

Validate independent workspaces through their existing supervisors, serialize
same-pane input, and preserve active task ownership. Release each passing
workspace back to its original task immediately. Do not reopen accepted research
or repeat a completed callback merely to increase a test count.

## Discovery failure must not lock a conversation

Workflow observers permit ordinary tools and Stop regardless of enrollment,
caller resolution or outstanding work, preserving all existing evidence. Do not
reinstate an old task gate, repeatedly block the same session or demand a
specific resume command. Observer success is not task acceptance, disarm,
callback confirmation or permission to send to an unverified endpoint.
New/successor supervisors establish their own current native binding. Continue
already-authorized solo work while restoring the original executor at a safe
boundary; never make executor availability a prerequisite for local recovery.

## Active-session adoption receipts

The installed advisory hook entrypoint writes a bounded private latest receipt under
`~/.local/state/multi-agent-collaboration/runtime-adoption-v1/` for each native
session, event and hook. Receipts include source/version, event, scope result,
time and kernel parent process identifiers; they exclude tool text and secrets.
Audit failure must not block any tool. Offline unit-test entrypoints do not
create these runtime receipts. A manually invoked hook is still a manual probe:
check immediate native parent provenance and the real client event before
claiming automatic adoption. Inventory every live client, including idle ones;
missing post-install execution remains unverified, not implicitly successful.

## ConfigChange and embedded-client boundary

Claude's native settings watcher can invoke a temporary `ConfigChange` command
without submitting a prompt. A receipt from that event must include the original
session, settings path, release, process birth and parent chain. It is a
diagnostic adoption fact only. It cannot be relabeled as automatic `PreToolUse` or
`Stop`, complete hook coverage, native delivery, callback settlement, or business
acceptance. Register a probe with a CAS merge, preserve unrelated settings, use a
second genuine settings change when the watcher refreshes its cached snapshot,
and remove the owned probe with another CAS merge. Missing receipt is unresolved;
never feed synthetic hook JSON to the command.

The managed Codex daemon's native config reload refreshes its loaded threads only.
An older embedded Codex process outside that daemon has no inherited reload proof;
unless a supported native endpoint is independently demonstrated, keep its exact
session/rollout and report `UNVERIFIED`. Do not restart, replace, or alter its
conversation merely to manufacture an adoption event.

`cmux identify` returning a caller or focused surface is insufficient when the
live tree cannot be joined. Process/MCP liveness and a screen read prove neither
same-workspace handshake nor delivery. All terminal input remains blocked until
the current caller, target surface, pane, native process and session are freshly
authenticated. A successor supervisor may continue local authorized work and
preserve evidence while this transport boundary is unresolved.
## Successor supervisor rebind

The fixed historical `codex resume` identity is not a transport requirement.
A successor authorized by the user can establish current contact through
`successor-rebind-v2`, including an explicitly pinned cross-workspace
caller/target pair. Pin the authorization body and both endpoints' current
workspace, surface and pane UUIDs. Do not demand a failed supervisor's approval,
proof of its failure or prior settlement of an old task when existing user
authorization already covers this contact.

This maintenance route preserves the old controller, task pack, nonce, callback
and native evidence. Communication authority does not transfer old callback
identity, shared-file ownership or business scope. Configuration reloads,
process/MCP liveness and ConfigChange diagnostics do not establish a handshake
or adoption; current process/session/transcript checks and native reception
remain separate required evidence for the communication claim.

## Independent file scheduler (0.4.27)

After installing the complete approved release, review
`scripts/executor_reask_scheduler.py install-job`, then use `--apply` to register
`org.multi-agent-collaboration.executor-reask-scheduler`. This is separate from
the configuration guardian and workflow hooks. Installing it does not enroll
old episodes or authorize communication. A new explicitly authorized
`executor_reask.py start` authenticates the live pair and enrolls its exact task,
purpose, workspace, supervisor, episode, file channels and immutable controller.
Preserve the original controller of every earlier attempt; do not migrate it.

The launchd job runs at 60-second intervals with a 256-enrollment cap and a
10-second sweep budget, uses nonblocking registry/episode locks and rotates its
scan fairly. It only updates file requests and reads their bound file replies.
It never reads a transcript or legacy idle binding, sends native/terminal input,
reconciles a pending send, reopens operator stop or supplies a foreground identity.
UNKNOWN and terminal_pending are preserved, including when new file markers are
issued. A bound reply ends an ordinary episode; continuing replies await explicit
consumption. A TASK reply still requires a formal pack.

The scheduler reads its previous rotation cursor before full-release verification.
If reading that cursor times out, it preserves the original STATUS bytes and
writes LAST-ERROR.json separately. Later budget failures preserve the last
visited enrollment, so the next sweep can continue after it. `phase`,
`phase_seconds`, per-episode `duration_seconds` and `pending_enrollment` identify
where a sweep stopped. `file_posts_complete=false` means returned counts are
partial: a tick may have committed files before its return was interrupted.
Do not interpret zero partial posts as proof of no execution. Source checks,
the 10-second budget and original controller ownership remain unchanged.

Check `executor-reask-scheduler-v1/STATUS.json` and the exact enrollment's
last_execution separately from job registration. A file-only inquiry does not
wake an idle supervisor. Supported PostToolUse context exposes it at a real tool
boundary, and the supervisor must read the body and reply. Distinguish job
installation, enrollment, execution, file exposure, body reading, native input
and business acceptance. Ordinary tools and Stop remain available on every error.

<a id="candidate-cross-release-file-contract"></a>

### Cross-release file contract

In 0.4.32, the approved CURRENT scheduler uses its own
`executor_reask_protocol.py` interpreter. Cross-release support covers only the
reviewed v2 producers `0.4.28-ae66245771d0`, `0.4.30-ff75100795a9` and
`0.4.31-79ae83e9d5da`. Verify the trusted read-only original MANIFEST bytes and
every actual member's hash, size, executable mode and read-only status, plus all
six original controller members and the issuer scheduler pin as a complete
combination. A version string, descriptor or canonical path
alone is insufficient. Read old pins only; never import or execute old modules.
CURRENT's own state format remains supported behind its full approval gate;
unknown historical v3 producers are not supported by a matching descriptor.

Match the complete enrollment binding and registration token to the live state;
legacy tokenless states match only tokenless records. An existing continuation
must be structurally valid and contain the exactly equal complete original
controller. A v2 state may lack a top-level controller only when consistent
original enrollment/continuation evidence supplies it. Compatibility never
substitutes for authentication, authorization, stop or formal ownership checks.

Before the first cross-release write, retain one complete snapshot of the raw
original state and enrollment, including absence. Keep original controllers,
UNKNOWN, terminal_pending, ask evidence and old journals; ordinary bounded file
history can continue without erasing that snapshot. File ticks cannot transport,
read Tail/transcripts or legacy idle bindings, consume replies, reconcile pending
input, or create/update terminal_pending. No background terminal/native input or
idle-model wakeup is authorized.

### Registration commit and recovery

`start` checks CURRENT approval, the authenticated live pair, capacity, exact
binding, authorization and formal occupancy before changing active state or
channels. Authorization/boundary previews use `publish=False` on deep copies.
Registry operations take the registry lock before the caller lock and reread
their inputs under those locks.

A same-supervisor formal reservation created before a new episode also rejects
`start`, for both ordinary and continuing inquiries, including `--file-only`.
Rejection leaves the original marker and any existing state or enrollment intact
and performs no initial tick. Existing enrolled episodes instead follow the
pause and bound-reply rules below; creating a replacement is not pause recovery.

A PREPARING envelope retains the original active record and proposed enrollment,
with a pinned intent and complete preimages. The single commit point is live
state replacement with the matching `registration.token`; PREPARING alone is not
an enrolled WAITING episode. Recovery verifies the envelope's original INDEX
byte hash and all five snapshot members: `intent.json`, `previous-state.json`,
`previous-enrollment.json`, `previous-stop.json` and `prepared-state.json`.
Declared absent members must still be absent. Changed intent, INDEX, preimages or
member sets are rejected; recomputing an INDEX's own bytes/SHA fields is no proof.

- Before the live token commit, failure reports `REGISTRATION_UNCOMMITTED` and
  does not tick or replace the old state/channel. A sweep verifies the original
  intent and restores the exact old enrollment. For a failed first registration,
  it restores ABSENT enrollment without creating a state or sending anything.
- If state replacement succeeded before fsync or postcommit work failed, report
  `COMMITTED_RECOVERY_REQUIRED`. Complete that same transaction idempotently;
  never roll back visible committed state or create another episode as recovery.
  Retiring an old episode's channel must not overwrite a newer episode's channel.

Only a record selected by the state's token can authorize scheduling. A FINISHED
record can support terminal-state evidence; it cannot restore WAITING. A missing
or failed registration is visible failure, not permission to send or claim that a
background runner is active.

Explicit `start --file-only` and transcript channels are mutually exclusive.
File registration, a scheduler tick and an actual automatic hook invocation
are separate observations. The original model must still read the complete
bound reply and consume its exact SHA; no file-only operation wakes it.

### Explicit registration of an existing episode

The original executor may use `executor_reask.py enroll-existing --episode ID
--enrollment-sha256 SHA` after foreground authentication. ID is the exact existing
episode; SHA is the hash of the original raw enrollment bytes, or literal
`ABSENT` only when no enrollment exists. Preserve old records before replacing
the enrollment. This operation keeps the original controller, UNKNOWN and
pending/history, and does not create an episode, tick, consume or send.

A new 0.4.30 episode beside a stale 0.4.28 enrollment for a different supervisor
is not a matching pair and must not auto-migrate. Explicit replacement requires
the original executor's authenticated intended binding and the exact old SHA.
Changed SHA, invalid authorization, formal occupancy, STOPPED or finished work
rejects enrollment; compatibility cannot grant successor ownership.

## Formal reservation boundary

Version 0.4.32 applies this boundary before file compatibility checks, both to
continuing inquiries and ordinary WAITING, including old 0.4.27 episodes that
CURRENT cannot file-tick. A valid reservation must bind the original caller and
workspace. Same-supervisor reservations pause inquiries. A legitimate different
supervisor's reservation must be newer than the episode to end the old inquiry
with persistent `STOPPED / FORMAL_OWNER_CHANGED`. It does not migrate the old
controller, settle a callback or grant the successor old task scope.

A same-supervisor pause blocks new requests, not discovery of the already-bound
reply. The reply may become ANSWERED while the reservation remains. Only the
authenticated original executor through its original controller may consume it;
consumption does not clear the pause or issue a request before matching RELEASE.
The same rule applies to reviewed cross-release file scheduling. Validate it
separately from registration, actual scheduler execution, automatic hooks and
two real continuing rounds in the original sessions.

Only the matching supervisor/caller/workspace and exact collaboration/task
RELEASE removes its reservation; remaining reservations or ended authorization
still prevent resumption. Marker disappearance, TTL expiry and heartbeat do not
release or confer takeover authority. Unrelated, malformed or older foreign
reservations have no effect. Noncontinuing ANSWERED/CONSUMED and STOPPED episodes
are never revived. Preserve original controller, UNKNOWN and pending evidence.

CURRENT harness arm and disarm serialize marker/reservation changes under the
shared caller locks; disarm verifies the exact marker before RELEASE and unlink.
Heartbeat takes nonblocking locks, rereads the marker and never recreates it after
disarm. Marker writers take caller locks only; registry users keep registry →
caller order. Old writers that do not take these locks remain outside this race
guarantee. Ordinary tools and workflow hooks remain nonblocking.

Reservation is not native dispatch evidence or business acceptance.
TASK/DISPATCHED/ACK mailbox text alone does not dispatch or reserve work.

## Continuing authorization (0.4.28)

Ordinary `start` still ends when the bound reply arrives. Only an explicitly
user-authorized `start --continuing --overall-task TASK --authorization-file
/absolute/user-authorization.txt` enables ongoing file inquiries. Optional
`--authorization-until EPOCH` bounds that authority. The complete authorization
file and original controller are pinned; changing/removing the file revokes it.
Installation never enrolls or migrates existing episodes.

In this mode a WAITING_DEPENDENCY/SOLO/BLOCKED reply must carry `owner` (the bound
supervisor UUID), a concrete `trigger`, `next_due_epoch`, and `deadline_epoch`.
The complete reply body and canonical JSON SHA are retained. ANSWERED means only
that the reply was observed; scheduler enrollment remains active but sends no
new inquiry while consumption is pending. The original executor reads the full
body and calls `consume --reply-sha256 SHA`. This authenticated foreground act
records `detected_epoch` and `consumed_epoch` in a durable receipt. A timer, file
mtime, manual test or supervisor write is never labeled body consumption.

The current `consume` CLI also returns an invocation-specific `consume_result`.
`REPLY_CONSUMED` has `consumed=true`, the receipt path, original marker, body SHA
and consumed time; a continuing episode may already be `WAITING_REPLY` again.
`NOT_ANSWERED`, `NO_EPISODE`, `STOPPED` and `ORIGINAL_CONTROLLER_REQUIRED` have
`consumed=false` and exit code 2. A reservation is reported separately because
an already detected reply can be consumed while requests remain paused. This
result is not persisted in the episode or reused by a later status/tick. A
repeated call after consumption cannot claim a new acknowledgement. Old pinned
controllers retain their original output; verify their actual receipt and
consumption fields instead of treating exit code 0 as proof.

File compatibility does not let CURRENT consume an old controller's reply.
Except for operator stop, a controller mismatch returns
`ORIGINAL_CONTROLLER_REQUIRED` before authorization, receipt or state writes.
Consumption requires the authenticated original executor and original controller.

After consumption the same authorized episode resumes, no earlier than the
existing 60-second cadence and the reply's next due time. Each new inquiry has a
new marker. Previously consumed rounds cannot close it again. An elapsed deadline
allows a new inquiry; it never proves a dependency completed. No catch-up burst
occurs after a delayed sweep. This does not promise model response within 60 seconds.

The [formal reservation boundary](#formal-reservation-boundary) pauses on the
same supervisor's reservation and ends old inquiries on a legitimate newer
supervisor's reservation. Only exact release can resume a still-authorized paused
lifecycle; the former blanket rule that all foreign reservations are ineffective
does not apply to a valid new formal owner.
For overall completion, explicit stop or authorization withdrawal, use
`stop --reason OVERALL_TASK_COMPLETE` (or the applicable operator reason). Stop
works even while a reply awaits consumption. An expired/revoked authorization
never rearms. Existing UNKNOWN/controller/attempt budgets stay unchanged.

The independent scheduler remains files-only: no transcript reads, native input,
terminal paste, forced turns or blocking Stop hook. Verify the original executor's
explicit enrollment, automatic sweeps, exact replies, actual consumption, two
successive rounds and task/stop boundaries separately from installation.
