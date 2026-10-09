# Global hooks, provider changes and executor follow-ups

The shared collaboration release owns transport and communication hooks. Other
skills and global CLAUDE.md link here; they do not implement another sender or
copy hook code. Pin one complete immutable release for each new task. An
in-flight task keeps its original controller, task pack, journal and receipts.

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
Only an explicitly authorized `executor_reask.py` episode keeps asking every
60 seconds until its supervisor replies, an authenticated new task is bound, or
the operator stops it. Install its optional Stop guard with `--executor-reask`.
An API outage, killed process or paused client can prevent execution; expose that
state instead of promising that software can never stop.

The Stop hook and CLI use one nonblocking caller lock. The hook enrolls only an
authenticated existing idle binding and performs no terminal input. It must not
reopen an explicit operator stop or steal another supervisor's executor. A new
task transition must match caller, workspace and supervisor and be newer than the
episode. Ordinary completion must not replay a previously accepted callback.

Each inquiry gets a new issued marker, preserving the same task/episode binding.
The original unconfirmed terminal attempt remains durable even after the bounded
history rotates. Read that original journal first. While it remains queued,
stranded or uncertain, later inquiries use files only: never paste another prompt
over it, change its nonce, or treat queue status as native receipt.

Each round updates configured file channels plus one durable supervisor inbox
entry. Track checks, attempts, successful file writes, actual pastes and exact
native receipts separately. Empty/failed channels are visible. A file write does
not prove the supervisor read it. The authenticated supervisor PostToolUse hook
discovers a bounded number of current entries and supplies their exact request
paths, hashes, reply mailboxes and templates in the official additionalContext
envelope; no terminal input or active task marker is required for discovery.

Reply in the request's mailbox with the complete issued template, or through the
original guarded route as a complete JSON object / `EXECUTOR_REPLY|<JSON>`. The
reply must match `caller_surface_uuid`, `supervisor_uuid`, `task_id`, `episode_id` and
an issued `marker`. A status line quoted in prose is not a reply. TASK is only an
answer to the inquiry; dispatch still requires the formal task-pack contract.
WAITING_DEPENDENCY, SOLO and BLOCKED require a concrete `trigger`; replace the
template placeholder. `queued:true` or `received:false` never ends the episode.
Late replies to issued markers remain valid after history rollover.

## Queues and per-session acceptance

Codex can accept a follow-up into a queue while its current turn continues. A
verified Tab-to-queue route is normal pending behavior, not proof of a stalled
composer or permission to force another key. Hand-typed input may take a different
steering path. Confirm reception only from the exact complete message in the
bound native user journal after the original EOF fence. Queue dwell time, native
reception, execution and callback settlement are different measurements.

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
