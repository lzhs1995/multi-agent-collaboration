# Shared daemon caller identity and bounded callback closeout

A managed CLI daemon can inherit the terminal environment of its first client.
That environment identifies the daemon origin, not necessarily the current task.
Resolve a managed Codex caller using the current thread selector, verified tool
ancestry and a unique same-user native resumed client. Require executable,
PID/birth, TTY and live cmux surface/workspace UUIDs to agree; recheck the proof
around tree reads and before each input. Ambiguity or drift denies input. Never
override CMUX environment values, use focus, relax the workspace boundary or
create a replacement session to make a check pass. Ordinary clients retain
their original checks. This is not an atomic OS snapshot-and-send guarantee.

Use the same resolved caller for transport, harness identity and active-marker
ownership. A correct send check alone does not repair a registry using inherited
workspace values. Test both positive routing and foreign-workspace/identity-drift
rejection. Test the assembled runtime, preserving existing delivery guards.

Hook readers must use that same identity too: Stop callback/ACK checks, executor
closeout and lease lookup resolve workspace and surface together. Reuse one
snapshot only within a hook evaluation; never cache it across calls or rewrite
process environment. Discovery failure denies the check, and a managed caller
cannot downgrade to inherited identity mid-resolution. Boolean Stop reentry
still exits before discovery without confirming or releasing a task.

CLI subprocess fixtures must not inherit the invoking live thread selector.
The offline runner removes it only from its test environment; managed identity
tests install explicit ancestry fixtures. Production guards remain unchanged.
`test_hook_identity.py` covers native/foreign markers, leases, returned callback
journals, discovery failure and successive caller changes. Preserve explicit
relative-root rejection even when no shell write target was extracted.

## Recycled TTY names are not caller identity

Resolve the unique live resumed native client first, then join its kernel-read
surface/workspace UUIDs to the current tree. Require that exact surface row to
be terminal and to match the client's TTY. An unrelated tree row retaining the
same recycled TTY name must neither select nor veto the caller. Duplicate caller
UUIDs, multiple native clients, a mismatch on the selected row, process drift
and cross-workspace targets still deny input. Never set CMUX identity variables
to a user-supplied value to work around a rejected proof.

Attribute errors to their failing layer: unresolved supervisor native client,
wrong target, missing ACK, provider failure and uncertain delivery are distinct.
A caller-resolution error is not evidence that Claude's identity is invalid.
Once a current-task ACK is validated, reuse it and dispatch the scoped work;
do not reopen a healthy handshake for status checks.

The non-submitting bridge test starts only with proven empty compose. Clear at
most the actual test token length, bounded by the existing cap, rather than
always issuing 256 backspaces. Every key retains the UUID check and the final
compose-clear postcondition remains mandatory. This reduces work without
turning token disappearance or a key return into delivery confirmation.

Ship resolver and harness changes as a new fixed release. Update assembled
launchers and hook paths with compare-before-write backups, preserve inline
post-submit checks and existing task journals, then verify the installed caller
and negative guard cases. Source tests alone do not fix installed entrypoints;
configuration updates do not prove that long-lived clients hot-reloaded.

Completed reports need a bounded callback, not an open-ended polling job. Separate
compose, submitted, queued, native consumption and report acceptance. For a
queued callback preserve the attempt and continue independent work. A native
user record can reconcile a missing receipt only through the original journal,
with the task/report/nonce and original-controller checks intact and zero input.
Do not replay a callback or rewrite an old controller to make it confirm.

After a confirmed terminal boundary assign the next useful independent task.
Use separate task IDs, output scopes and nonces for two original executors;
serialize terminal input when they occupy tabs of one pane. Allow at least the
600-second handshake phase budget and continue as soon as a valid ACK arrives.
If a provider fails, preserve the original session and use authorized SOLO
takeover while the executor recovers. Judge efficiency by accepted work advanced,
not the number of messages, watchers or repeated reviews.

## Closeout without consuming executor time

Read the receipt at the absolute path bound by the task pack. A relative `ls`
from a changed working directory cannot establish that a receipt is missing.
When a report is already accepted, a transport delay is a separate closeout
item; do not repeat the audit or ask the user to relay the same callback.
Record the next independent assignment, and dispatch at a verified safe
boundary. A native API retry is not idle capacity: preserve the session and
continue independent supervisor work under existing SOLO authority.

A post-submit detector may return unconfirmed before the message becomes
visible. Preserve that result and inspect the original attempt; a later exact
receiver message is new evidence, not permission to send it again. Do not turn
this recovery into continuous polling or another model-consuming conversation.

For offline harness tests, fixture workspace IDs must not reach live process
ancestry. The suite runner supplies an ordinary-client process fixture; identity
tests override it with their explicit daemon and drift cases. Do not weaken the
production guard or alter CMUX environment values to make tests or routing pass.


## Carry caller identity through every phase

A valid identity gate is insufficient if discovery, provider detection, marker
ownership, naming or screen reads fall back to the daemon's inherited workspace.
Use the resolved native caller throughout, select actual live tree members,
and address naming/reads with workspace and surface UUIDs. A focused executor
or old title does not identify the supervisor; global dock panels are not
workspace members. Recheck identity before mutations.

Preflight failure before challenge dispatch is a supervisor tooling failure,
not proof of an unreachable executor. Preserve its artifacts, disarm only that
failed task and use a new task/nonce after repair. Cover the assembled path as
well as identity helpers. Once an executor accepts useful work, continue the
main product task instead of adding coordination-only reviews.


## Attribute a blocked Stop to its actual task

With two executors in one workspace, a Stop failure must name the marker that
produced that verdict. Return the failing marker from the same evaluation; do
not rescan the registry and print its first entry. A wrong task hint can send an
executor looking for another task's receipt even when the underlying participant
check is correct. Test callback failures, evidence contradictions and missing
summaries with a different executor's marker first, plus registry changes after
evaluation. Keep callback, report-hash and reentry requirements intact.

For an already-submitted task, later user prompts may separate its marker from
visible activity. That makes screen attribution inconclusive; it does not prove
that the executor never received the task. Preserve the attempt and do not add
reminder prompts, repeat the task or relax cross-prompt evidence checks. Read a
completed report independently and record report acceptance separately from the
transport receipt. While useful work is already assigned, continue supervisor
work instead of creating another communication review or repeatedly polling.



## Compaction and delayed observation are separate from idle capacity

An executor compacting after a submitted challenge is not idle or unreachable.
Record its actual progress and the original handshake handle; do not stack another
challenge or generic continuation message behind it. A pending failure-shaped
receipt is not final while its original process is still observing a late ACK.
Follow that handle to its terminal result, then reuse the exact nonce for supported
read-only recovery. If the handshake ends without proof, record the outcome and
continue independent work under existing authority; do not claim it succeeded.

A null submission timestamp proves only an absent recorded timestamp. The input
journal can show paste and Enter even when a short screen detector saw nothing.
Determine zero input from the attempt, not from that null alone. Keep report
acceptance independent from missing transport receipts and give the executor no
polling assignment merely to make the supervisor's detector catch up.
