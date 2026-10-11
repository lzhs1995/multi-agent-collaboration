# Global hooks, provider changes and executor follow-ups

The shared collaboration release owns transport and communication hooks. Other
skills and global CLAUDE.md link here; they do not implement another sender or
copy hook code. Pin one complete immutable release for each new task. An
in-flight task keeps its original controller, task pack, journal and receipts.

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
Only an explicitly authorized `executor_reask.py` episode issues a new inquiry
every 60 seconds while its explicit runner is executing. A call polls every five
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
mailboxes and issued templates. From 0.4.26, the registered supervisor PostToolUse
observer authenticates the current supervisor and offers bounded, deduplicated
file-request context. It uses the supported hookSpecificOutput/additionalContext
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

Before fresh dispatch to a reasking executor, the bound supervisor first writes
the exact issued mailbox reply. The explicit runner then observes it and returns;
perform a fresh live preflight before the formal pack. A TASK reply does not
dispatch anything. This sequence avoids waiting for an idle window that the
runner itself prevents. An exited original receiver process does not authorize
forging a new original binding or deleting terminal_pending. Independently
validated historical native reception remains separate from official live
reconciliation; answer the original episode by its mailbox without replaying it.

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
