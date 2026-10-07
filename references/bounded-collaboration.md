# Bounded collaboration: spend time on the user's deliverable

This protocol clarifies operator behavior. It does not install a hook, add an API,
change an active task's pinned runtime, or prove that a running client reloaded.
Use the task's actual supported bridge and receipt schema. A newer local helper
must not be assumed present in a public or older pinned installation.

## Dispatch only useful independent work

Before dispatch, record the unresolved question, expected artifact, input pins,
write boundary, stop condition and which supervisor work can proceed concurrently.
Prefer complementary work such as implementation versus independent verification.
Do not ask both executors to collect the same data, repeat an accepted audit or
write the same repository. Shared acquisition and shared-file edits have one owner.

Two authorized context-bearing executors can help when both tasks meet that test.
Their task IDs, nonces and output roots stay separate. Input to surfaces sharing
a pane is serialized and the exact UUID is checked each time. Shared Word/Zotero
access remains serial; a second agent does not create a second application lease.
An idle executor is appropriate when no valuable independent task remains or its
previous task has not safely closed. Do not fill the panel with busywork.

## A handshake proves identity, not scientific agreement

The initial challenge includes the absolute pending receipt and required skill.
The executor reads them, checks identity/nonce and returns the exact ACK before
research, registry searches or source audits. Reuse a healthy current-task
handshake. Set a finite observation budget compatible with the harness phase;
a shorter local wait is not evidence that the executor failed.

A late ACK belongs to the original challenge. Use a supported read-only recovery
path against its original evidence; do not issue another challenge just because
the first screen detector timed out. Formal consensus rounds apply only to an
explicit formal consensus phase, not to channel readiness or ordinary status.

## Both directions require post-Enter verification

Use `submit_task_pack` for task dispatch and `submit_completion_callback` for
completion, from the bound guarded bridge. A helper return code, echoed prompt,
report file or disappearing marker is insufficient evidence of delivery.

| Observation after the guarded send | Meaning and next action |
| --- | --- |
| Complete payload remains in compose | Not submitted; preserve the draft and original attempt. No blind Enter, Tab, paste or force-clear. |
| Payload is in the receiver queue | Submitted but pending; observe that attempt without resending. |
| Exact task payload has task-related receiver activity or verified native receipt | Record actual consumption against the original attempt; do not resend after a detector false-negative. |
| Missing, truncated or ambiguous evidence | UNKNOWN; preserve identity/journal and do bounded read-only recovery. |

Recovery may not change the task, report hash, nonce or original attempt to make
it pass. If the pinned version lacks a read-only recovery API, checkpoint the
evidence for the supervisor; do not emulate it by recalling a sender that may
paste again. A native record must be a real receiver event, not a quote, tool
output or supervisor-authored note. Only a supported verifier can turn it into
the required receipt; narrative acceptance cannot forge one.

Do not wait for `supervisor busy=false` as an extra callback prerequisite. Invoke
the guarded entrypoint once and let it assess the input state. Do not manually
add a Tab-to-queue workaround or interrupt an active command to clear compose.

## Close callback work instead of engineering indefinitely

Completion has separate facts: business report accepted, original callback
verified, task monitor/marker disarmed, executor at a safe boundary, and any
shared resource released. None implies the others.

The initial callback is followed, if needed, by one bounded read-only recovery
window under the existing attempt. At its end, preserve unresolved transport
evidence and hand it to the supervisor. Do not launch a second watcher, poll each
second, lengthen the retry budget, delete the journal, or rewrite a frozen report.
This handoff is not a success receipt or permission to bypass the Stop guard.
The supervisor continues independent authorized work while resolving the missing
fact through the existing lifecycle, without stacking a new task on the executor.

When the original callback is verified and the supervisor has disarmed the
task's marker/monitor, end that task's callback probes. Do not require another ACK
of the closeout notice. Do not reopen it to improve a classifier or collect more
conformance measurements. If the runtime still blocks or restarts work, retain
that failure and make it a separate maintenance scope; do not claim a prose rule
fixed runtime enforcement. Safely finish an active command; do not raw Ctrl-C it.
Before reusing the executor, verify its actual safe boundary.

Persistent provider failures follow SKILL.md: finite retries only for retryable
errors, zero blind billing/auth retries, original session preserved, authorized
SOLO takeover after concurrent writes are stopped. The research task continues.

## Measure benefit and state the limits

At each accepted deliverable record the useful finding or unresolved question,
what was independently checked, what was only reused, and coordination overhead
(handshake, callback, recovery, resource wait). Do not add unlike counts or use
agent uptime as productivity. A cached receipt check is not a new model run or a
new scientific review; mixed content/liveness checks need separate denominators.

An observed failure pattern is accepted work followed by prolonged callback
diagnostics despite a confirmed original receipt. The remedy is the explicit
terminal boundary above, not a larger retry limit. A document change, offline
test, installation, active-client loading and actual delivery are five distinct
claims. Public lessons contain generic rules; private transcripts, research data,
accounts and machine-specific paths stay in the task evidence.
