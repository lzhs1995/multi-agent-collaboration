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
