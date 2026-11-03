# Shared collaboration contract

Use this contract for new tasks in either transport. Existing tasks keep their
registered controller, input attempts and acceptance contract. Reading a skill
does not enroll a task, start an agent, grant shared writes or enable a loop.

## Assign useful work

The supervisor owns scope, integration and final acceptance. An executor owns a
bounded deliverable; a reviewer evaluates named evidence. For each bounded work
package, choose zero, one or two executors based on independent work, not the
number of available panes. This is not a cap on a book's separate chapter teams.
Use the designated context-bearing sessions. Preserve their history and actual
drafts; communication trouble is not authority to reset, replace or switch them.

A substantive assignment records the objective, task ID, original participants,
input versions, allowed output paths, single writer for shared files, verification
and stopping condition. A short question need not become a formal file package.
For a new nested chapter/team assignment, use the
[nested research coordination layout](nested-research-coordination.md).
Maintenance is a separate assignment; it must not silently consume a dissertation
reviewer's scope. Explicitly assigned maintenance is not a model capability fault.

For a takeover, read the complete applicable authorization and later directions;
record the clause used and the observed reason. Distinguish executor failure,
transport failure, capacity/resource limits and an authorized efficiency choice.
More than one may apply. A refused maintenance notice does not establish failure
of every task channel; a working file mailbox does not establish formal dispatch.
Keep the failed attempt's time and scope: repeated observations of one probe are
not independent sends or API retries, and an earlier failure does not prove a
continuing outage. Use existing authority without adding an approval loop.

When an executor recovers, state who finishes an already started package and who
owns subsequent work. Record start, recovery, freeze and acceptance separately;
reuse accepted work at the handoff boundary. Preserve original sessions, output
ownership and resource limits. Label solo results as self-review.

## Evidence and outcomes

Keep submission, native reception, full body reading, produced artifact,
supervisor acceptance and resource release distinct. An ACK, lifecycle state,
hash match or file existence establishes only the fact it actually checks.
Bind substantive handoffs to input/output fingerprints (version or hash), the
decision they implement, task ID and exact participants; preserve that binding
through acceptance. Child packages also identify their parent task and chapter.
Do not introduce an ACK of an ACK. Read and integrate a finished result before
dispatching dependent work. Preserve unanswered or unknown items in the ledger.

The reviewer chooses its own verdict from the supported outcomes. Keep any
requested outcome separate from the actual reviewer decision; do not prefill or
report the requested outcome as the reviewer's judgment.

A write audit states its inspected paths and time window. A clean package-root
diff says nothing about outside maintenance or memory writes. Record relevant
outside writes with their existing authority; add post-freeze activity separately
instead of changing a frozen report. A missing authorization check remains
unverified, rather than becoming a claim of unauthorized action.

For related packages, carry forward the agreed audit scope and cutoff instead of
silently reducing them when a checker changes. Cover the task's actual write
channels: direct tools, shell redirection, generators and their relevant outputs;
include memory, temporary or maintenance paths when those channels use them.
Record exclusions. Validate a new or changed checker against a known positive
case; do not create real out-of-scope writes as a test or mandate a whole-machine
scan on every package. A supervisor's caveat does not repair missing coverage.

Bind review claims to object ID, paragraph/page or other locator, short supporting
quotation and input version/hash. Check that the quote supports the claim, not
merely that it occurs somewhere. Verify objects, negation, qualifications, model
names and completion status as well as numbers. State the denominator and stage
for every count; proposed adoption is not an applied or accepted change.

Validate one real input/output example before a batch. Save each completed batch
before reading the next. Reuse accepted decisions against unchanged evidence;
review only changed scope and unresolved issues. New review tasks default to one
pass; add corrections only when needed, with at most three review/correction
rounds in total. Do not add reciprocal reviews to fill a round quota. Unresolved
scientific questions remain explicitly unresolved; exhausted time, token or round
budgets never turn them into PASS. Scheduler steps and message hops are not review
rounds. Existing registered review contracts remain unchanged.

## Bounded communication

New Herdr tasks use the official lifecycle primitives with finite timeouts;
new cmux tasks use their selected supported transport. Registered older tasks
retain their original cadence. Environment alone never overrides registration.
Do not add polling, repeated continuation prompts or stop-hook loops by default.
Preserve the original attempt on timeout, stalled or ambiguous delivery. Inspect
before further action; those outcomes never authorize blind resubmission.
Continue independent work while observing the pending result.

During active work, provide a brief factual progress update at least every
60 seconds and persist completed batches or changed state in the checkpoint.
Notify the relevant coordinator once when work stops or a blocker changes.
This is a progress obligation, not a new minute-by-minute ACK demand, wake-up
service or recurring prompt loop. A durable file alone cannot wake its reader.
Send substantive task-ready, result-ready, blocked or changed-decision events
once with a stable event ID and the original body/artifact reference. An observer
may report stale progress, but silence alone is not proof of executor failure.

Within already authorized collaboration, either side may send a relevant
clarification or correction while work continues. Use supported queued delivery
at the actual recipient; preserve drafts and do not force an interruption.
Reception, body reading and acceptance remain separate, including for queued
messages. A request for acknowledgement must not become an ACK-of-ACK chain.

Before a mutating operation, record its intent with target, scope and original
attempt ID in the task journal or checkpoint; then retain its actual result and
evidence. Keep this proportional to the operation, without a new approval cycle.
An UNKNOWN result requires reconciliation of that original attempt before
repeating the operation or making a dependent write; never replay it to
manufacture a definite outcome. Independent work continues.

Serialize input to the actual terminal being written, not every inner pane in
one outer surface. Shared Word/Zotero and account resources keep their original
single-writer/lease contracts across chapters and servers. Task timeout does not
release a resource or prove a process stopped.

## Measure the useful result

Measure matched send/receipt/read/acceptance intervals with task IDs, sample counts,
missing observations and overlapping work. Agent uptime, keyword buckets, tool
gaps, compaction counts and test totals cannot establish model speed, productive
hours or the cause of delays. Report output quality and verified closure first.

Evidence handling details: [handoffs](collaboration-evidence-and-handoffs.md).
Transport selection: [profiles](collaboration-profiles.md).
