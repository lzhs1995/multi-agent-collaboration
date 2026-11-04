# Nested research teams: Herdr inward, cmux outward

Use this workflow for a new, authorized project that combines internal teamwork
and coordination between independent terminal teams. Existing tasks keep their
registered controller, original attempts and explicit cadence. This workflow
does not migrate them or start replacement agents.

## Topology and ownership

For a dissertation with four core chapters, assign one cmux workspace to each
chapter and one existing Herdr service/team to that workspace. Each chapter
supervisor delegates bounded work to its executors and integrates their results.
A senior supervisor coordinates the four chapter supervisors and owns whole-thesis
integration. It can occupy an existing dedicated terminal; a fifth workspace is
optional. Four chapters are the example, not a hardcoded system limit.

```text
Senior supervisor — whole-project decisions, shared interfaces, integration
  ├─ cmux workspace A → Herdr A → chapter supervisor → executors
  ├─ cmux workspace B → Herdr B → chapter supervisor → executors
  ├─ cmux workspace C → Herdr C → chapter supervisor → executors
  └─ cmux workspace D → Herdr D → chapter supervisor → executors
```

Record the project/chapter/task relationship, original participants, mode,
allowed output paths, shared resource leases, progress path, review budget and
acceptance owner. For a new Herdr task, bind the actual inner service endpoint,
terminal and native session; record the outer cmux location as a timestamped
location observation, not a substitute for that identity. Re-observe a legitimate
move and retain its history; never pick a replacement from focus or a stale ref.
Existing cmux contracts retain their own UUID/identity requirements. The same
pane name on two services is not one identity. Resolve each communication edge; a single
project-wide transport flag cannot represent every edge in a mixed topology.

The outward skill owns coordination between teams even when delivery to an
inner supervisor uses Herdr's supported explicit selector. This preserves the
user's inward/outward organization without typing blindly into a container.
Maintain pure cmux and Herdr-only use as supported smaller topologies.

The senior supervisor changes shared specifications and integrates accepted
chapter deltas. Chapter supervisors own chapter files and their executor scopes.
An executor's report is not a final chapter verdict. In peer mode, designate one
integration writer even though discussion remains equal. Shared Word/Zotero or
account resources retain one lease regime across all teams.

## Four cooperation modes

Choose a mode for the current task or phase; do not infer it from the number of
panes. Modes are workflow metadata, not new `collaboration-profile-v1` values
or an implemented automatic scheduler. The corresponding topology labels are
`mesh` (free peer), `star` (moderator), `hierarchical` (delegation) and `ring`
(ordered iteration). Message hops and scheduler steps do not count review rounds. A mode switch records its boundary, accepted work, unfinished operations
and next owner; it does not replay the task or discard failures.

| Mode | Use and control | Exit condition |
| --- | --- | --- |
| Free peer discussion | Independent ideas or cross-review; participants may respond equally. Bound the question and participants, retain attributed views, nominate an integration writer. | Evidence-backed decision or explicit unresolved alternatives; no vote converts a scientific unknown into truth. |
| Moderator dispatch | A moderator assigns questions, limits overlap and synthesizes answers. Participants return one scoped result. | Moderator consumes the results and records a decision or missing dependency. |
| Hierarchical delegation | Senior supervisor sets interfaces; chapter supervisors split specialist tasks; executors return through their task parent. | Each parent reads and accepts its children's actual artifacts; whole-project integration accepts chapter deltas. |
| Closed-loop iteration | A directed pipeline passes a frozen artifact through analysis, revision, review and integration. Corrections return only to the responsible stage. | Named quality conditions pass, or the review limit yields a partial/blocked outcome with unresolved issues. |

These modes can be combined: hierarchical chapter structure, moderator-led
chapter work, a scoped peer review of a disputed claim and a bounded revision
loop. Executors need not be recreated when the mode changes. Do not give every
agent every transcript or make every participant communicate with all others.

## Five collaboration rules

### 1. Traceable handoff

For a substantive assignment, bind task ID, author and intended receiver, scope,
input version and SHA256/byte count, output path, acceptance conditions and the
next action. Append logs are bound by the inspected prefix or exact record range;
immutable artifacts use full-file hashes. Preserve the original message/attempt
ID and unresolved operations. Hashes establish content integrity, not authority,
reading, review or acceptance.

The receiver reads the full applicable body, reuses already accepted work and
continues from the checkpoint. Ask only about an actual gap or contradiction.
Ordinary short questions need a traceable message, not a formal archive package.

### 2. Progress visible every minute; notify on stopping

For new tasks using this workflow, register a 60-second progress cadence and an
observer responsible for it. Each snapshot states its observation time, last
artifact/checkpoint time, actual handle/state, completed increment, current work,
blocker and next action. Separate a fresh observation from fresh productive work.
An unchanged snapshot can honestly say "still running; no new result observed".

Keep expected activity (working, planned standby or waiting for input) separate
from observed runtime state and outcome. API failure, normal completion and a
planned idle agent cannot be distinguished by `finished:true` or `idle` alone.
Use the original native error/result evidence to diagnose causes. Transcript
growth from compaction or hook attachments is not productive progress; if a task
needs transcript observation, use its supported inode/offset reader and expose
coverage gaps rather than scanning a whole large log every minute.

Agents checkpoint at safe boundaries. While an agent is in a long tool call, the
task's supported observer checks its actual process/job/session handle and updates
the progress view; it does not inject a continuation prompt every minute. Before
claiming automated coverage, verify the observer is active for these exact tasks.
A prompt, skill file, installed hook or scheduler record cannot alone provide a
minute-by-minute guarantee. Without an active observer, report coverage as absent
and continue useful work while preparing the supported task-scoped mechanism.

On an observed terminal state, explicit pause, hard failure or loss of the known
handle, notify the designated supervisor once with the checkpoint and outstanding
operations. "Blocked on input" and "no recent activity" remain distinct from
process termination. When the observer itself stops, make coverage unknown rather
than fabricating progress. Do not reset a live job after an observation timeout.

The senior view aggregates chapter snapshots without occupying every executor's
conversation. Reuse the task's registered observer/lease system; never install a
global polling or Stop loop merely because an environment is nested.

For new local tasks without an existing observer, use
[`scripts/collaboration_progress.py`](../scripts/collaboration_progress.py).
It reuses the existing Darwin process reader and optional read-only Herdr checks;
it does not replace a task controller or implement a second sender. Unsupported
process observation remains UNKNOWN. Prepare an explicit manifest outside the
output directory; use actual live PID/birth/executable evidence, never a PID
guessed from a pane title:

```json
{
  "schema": "collaboration-progress-v1",
  "new_tasks_only": true,
  "project_id": "dissertation",
  "tasks": [{
    "task_id": "chapter-a",
    "role": "chapter-supervisor",
    "expected_state": "working",
    "process": {"pid": 1234, "birth": [1700000000, 123], "executable": "/absolute/actual/program"},
    "checkpoint": {"path": "/absolute/chapter-a/checkpoint.json", "stale_after_seconds": 180}
  }]
}
```

The numbers above illustrate the schema and are not runnable identity pins.
Optional `parent_task_id` names another task in the same manifest. Optional
`herdr` binds a local session selector and the full profile target; the command's
help gives that schema. `expected_state` is a planning declaration. Checkpoint
`updated_at`, `status`, `completed_increment`, `current_work`, `blocker`,
`next_action` and `outstanding_operations` remain unverified self-reports.

From the task's pinned source, after preparing real absolute paths:

```sh
python3 -B scripts/collaboration_progress.py validate --manifest /absolute/tasks.json
python3 -B scripts/collaboration_progress.py snapshot --manifest /absolute/tasks.json --output /absolute/new-snapshot
python3 -B scripts/collaboration_progress.py watch --manifest /absolute/tasks.json --output /absolute/new-watch --duration-seconds 600
```

The explicit watch uses a fixed 60-second cadence, at most 32 tasks, and a bounded
60–86400-second duration. Run it under the task's supported background tool and
preserve that tool handle. Each scan shares at most 50 seconds and the remaining
watch deadline; skipped reads become UNKNOWN. Synchronous OS stalls may exceed
that budget, so inspect actual scan times and recorded coverage gaps.

`snapshot.json` contains current observations; `observer.json` pins the manifest,
observer handle and deadline; `events.jsonl` records deduplicated STOP and observer
lifecycle events. The stdout events are convenient for the supervisor's running
tool, but the fsynced event journal is the consumption source. STOP means only
that the original pinned process is absent or replaced. API failures, successful
task completion, resource release and native notification are not inferred.

To end this observer, signal only its verified process with Ctrl-C/SIGTERM via its
original tool handle. To resume after interruption, use the identical manifest
and output with `watch --resume`; the original deadline and STOP IDs remain.
Do not delete the writer lock or start a second writer. An expired period stays
expired; a separately authorized new period uses new output and retains old data.

The supervisor consumes a STOP event by its ID, checks original task effects,
then submits one notice through the task's supported channel and records its
actual receipt/read evidence. The observer never sends that notice. Its final
coverage becomes UNKNOWN, and a hard kill may leave ACTIVE on disk: verify the
real observer handle and `coverage_valid_until` before claiming ongoing coverage.

### 3. Bidirectional wake-up, with visible conversation evidence

Both supervisor→executor and executor→supervisor need a supported route. Submit
a ready message while a peer works when its runtime permits queueing or mid-turn
input. Preserve real drafts/dialogs and serialize input at the actual terminal.
Use the runtime's supported input/notification route for an idle peer; do not
substitute a periodic file scan and claim the model woke.

For each direction, record submission, native representation, body consumption,
response and artifact separately. Herdr/Claude may represent a message as enqueue
then a mid-turn attachment. Record that accurately; it is not the independent
native-user receipt required by some old cmux contracts. A lifecycle transition,
Enter result or ACK alone does not prove this assignment ran. If a client cannot
surface a notification into the original conversation, declare that capability
unverified; keep the file fallback and independent work moving.

Maintain a runtime capability matrix for the actual versions and routes: submission
method, observed native representation, when the model consumed it, and measured
latency in each direction. Do not infer a Codex steer or an idle-agent wake-up
capability from Claude queueing. A stop event may be recorded and submitted
promptly while receiver consumption is delayed; report both times, not a guaranteed
60-second response.

### 4. Review once per frozen scope, at most three rounds

A review key is artifact hash + scope + reviewer role + acceptance standard.
Consume a finished verdict once. Reuse it for unchanged evidence; do not repeatedly
ask the same reviewer or an equivalent role to reread it for reassurance. Distinct
specialist scopes can have different reviewers without becoming duplicate rounds.

Register the acceptance scope before review: for example, a named chapter package
or the whole-thesis integration package. Each scope uses one initial review and,
only when actual corrections require them, at most two further rounds: three total
across its reviewers and delivery routes. Four independent chapter packages may
advance in parallel; do not add their message counts as rounds or multiply one
package's budget by its reviewers. Whole-thesis review checks integration and
changed interfaces, reusing accepted unchanged chapter evidence. Renaming a
package or passing it upward does not reset its unresolved review history.
Later rounds inspect changed and still unresolved scope. On the third failure,
freeze the result with its remaining
issues, notify the acceptance owner and continue independent work. Do not force
PASS, create a new task ID to reset the count or silently start round four.

The cap does not waive scientific or document checks. Schedule the required checks
within the remaining rounds: distinct specialist checks may run in one round,
while a second required full NLM review of the same PDF is a second review round.
If the gates cannot pass within three rounds, retain a partial outcome and seek
the acceptance owner's decision; do not add an exempt fourth round. Existing
registered tasks keep their original contract. A substantive change to standards
or scope needs an explicit new decision and budget, retaining the prior outcome.

Keep completed review rounds separate from upload/API attempts and incomplete
responses. A failed upload or empty response does not constitute a completed
review, but its original cost, retry budget and unknown effects remain consumed
or pending. This distinction never authorizes infinite retries. Freeze the review
scope at the first round; adding a specialist scope afterward requires the
acceptance owner's decision, not a silent split to obtain more rounds.

For the thesis example, chapter packages and whole-thesis integration have their
own predeclared scopes, each capped at three. Two clean complete NLM reviews of
one final PDF occupy two rounds in its integration scope. If round one causes
a revision, rounds two and three must both pass on the same revised PDF. A new
issue in round two makes that gate unattainable in the remaining budget: return
partial, retain the chapter's remaining budget and ask the acceptance owner to
decide any genuinely new scope. Refreezing alone never resets either ledger.

### 5. Recover intent and evidence; read back uncertain outcomes

Before a consequential operation, persist its intent, input pins, target and
original attempt ID. Append the actual returned result and receiver/artifact
observations when available; "intent only" never means discarding a known result.
After interruption, reconcile the original attempt read-only before deciding on
another operation. Unknown is neither failure nor success and does not authorize
replay, blind Enter, replacement agents or resource release.

Resume the original context-bearing session and accepted artifacts at a safe
write boundary. When an authorized takeover is needed, distinguish agent failure,
transport failure and resource limits; record who owns unfinished writes and who
accepts results. Preserve original controllers, deadlines, budgets and callbacks.

## Acceptance of the collaboration system

Validate both pure and nested environments with concrete tasks. Test matching pane
names on different services, shared outer surfaces, busy/idle input in both
directions, existing drafts, stale progress, a stopped observer, unknown sends,
bounded review and recovery of the original attempt. Do not inject failures into
active dissertation sessions; use isolated fixtures or explicitly scoped tests.

For the four-chapter example, demonstrate executor→chapter-supervisor consumption,
chapter-supervisor→senior consumption, a returned integration decision, a real
60-second progress observation and one verified stop notification. Mark simulated
and live evidence separately. Transport success does not prove chapter acceptance.

One happy-path observation is insufficient. Include a long tool call, compaction,
an API 429/502 outcome, planned standby and observer termination. None of these
may be reclassified as task success or automatic retry from lifecycle fields.

Compare quality, accepted artifacts, rework, human intervention, useful elapsed
time and available token/cost evidence on matched inputs and models. More panes,
ACKs or passing unit tests do not prove greater research productivity.

References for the selected transport remain authoritative for actual commands;
the paper's domain skill remains authoritative for scientific/document acceptance.

## Reference designs and limits

The four modes draw on Qunica's separation of communication topology from
scheduling. Its mesh/star/hierarchical/ring rules describe its own runtime, not
Herdr execution capabilities; its hierarchical implementation is not an arbitrary
parent-child tree. Use explicit local roles and edges above. See the fixed
[group guide](https://github.com/sligter/qunica/blob/0e5024242f76690975275f39eebc3ebbd7b9b982/backend-rs/crates/backend/src/docs/guide/groups.md)
and [topology implementation](https://github.com/sligter/qunica/blob/0e5024242f76690975275f39eebc3ebbd7b9b982/backend-rs/crates/backend/src/runtime/group_scheduler/topology.rs).

Independent, version-bound review and bounded correction are also informed by
the [Claude–Codex runbook](https://github.com/zhsongqing/claude-codex-workflow/blob/8e3d412186561a85b8d1329f650cad1845f95ef2/runbook.md).
Its Claude Code Monitor, model role choices, wait budgets and merge authorization
do not automatically apply here. Neither reference establishes a speedup for
this dissertation; local matched work is still required.
