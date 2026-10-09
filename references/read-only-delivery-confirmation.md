# Read-only submission confirmation and native receipts

The detailed current contract is [verified native delivery](verified-compose-delivery.md).
Screen inspection protects the draft. Only one new native user record after the
fresh EOF fence saved by the original PASTE_INTENT, from the same authenticated
workspace/surface/process/session/transcript, with the complete payload unchanged,
establishes `NATIVE_RECEIVED`. Claude `queued_command` remains pending. Preserve
spaces, tabs, blank lines and literal escapes; never concatenate records, search
other sessions or treat an ACK/activity/queue entry as receipt.

## The current hook reads the current original attempt

`scripts/cmux_native_delivery_guard.py` is the read-only PostToolUse entrypoint.
It resolves each actual invocation independently through `cmux_submission_inputs.py`;
quoted examples, tool output and unresolved dynamic arguments do not identify a
recipient. It checks the original controller's journal, live identity, full payload,
native binding/fence and applicable task/report pins. Missing or changed evidence
remains unconfirmed. Ordinary tools do not trigger scans of unrelated old attempts.
The hook sends no keys, writes no receipts and has no disable/advisory success mode.

`manage_install.py` registers the native guard for both clients; supported retirement
of this package's old screen-only/send-proof hooks preserves foreign configuration.
Install the sender, reader, wrappers and hooks as one complete fixed release.
Configuration edits, actual hook execution, loaded client modules and native receipt
are separate evidence. This is a client hook, not an operating-system sandbox.

## Preserve the original controller and budget

Use the original sender/controller for zero-input reconciliation. It can append a
pinned observation and atomically publish a receipt under the original persistent
lock only after exact native proof. It does not alter the report, task pack or prior
attempt records. A missing original binding/fence, changed file identity, truncated
transcript, incompatible legacy journal or active sender lock is not permission to
fabricate a baseline, migrate the attempt or resend. There is no standalone
`cmux_native_delivery.py` CLI; use only the original controller's supported entrypoint.

First input pastes once, waits for the full stable draft and submits once: busy Codex
with the verified `tab to queue message` hint uses Tab directly; other clear supported
states use Enter. Both automatic and explicit recovery share at most one additional
key, consumed when its intent is persisted. Unknown state/history, queue, compaction,
reconnection, changed structure or an altered draft bars that key. Display equivalence
can protect visible draft ownership; it cannot normalize native receipt text.

The public `--recover-stranded` option belongs only to `submit-text` and requires the
original `NATIVE_PENDING` state, `PASTE_INTENT`/`ENTER_SENT`, unused shared budget and
all live/native/draft gates. Task dispatch and callbacks use zero-input reconciliation.
A historical queue-only callback API does not create another Tab allowance.

## Closeout, diagnostics and discovery

A frozen bound report and returned original callback can end with an ordinary honest
waiting statement. Stop returns `WAITING_SUPERVISOR`, `continue:false` and
`suppressOutput:true` without inventing a receipt or disarming the task. Strict
task-bound `scripts/cmux_callback_diagnose.py --task-pack <original absolute path>`
and original-controller reconciliation remain available after closeout; arbitrary
tools and report changes do not.

The supervisor's PostToolUse `scripts/cmux_supervisor_report_guard.py` performs
bounded discovery through authenticated active markers and records
`REPORT_DISCOVERED`. Discovery is not delivery, acceptance or disarm.
Idle Stop writes one durable notice and permits exit. Explicit `executor_ready.py
persist` observes only the original request and bound reply for at most 300 seconds.
CCC waits have a deadline; native Goal behavior needs separate validation.

The historical mixed-version findings below are retained verbatim. They do not
certify a new installation, live hook, current native receipt or research acceptance.

## Keep the reader and bridge in one version root

A reader-only overlay is not a supported transition. A focused comparison found
that the retained bridge lacked `_delivery_confirmed`, which the new journal
reader requires: the mixed pair failed five of nine cases (two failures and
three errors), while the complete candidate passed all nine. Broad suites with
unchanged failure names did not detect this incompatibility reliably.

Point new-task wrappers and hook commands at the same complete, pinned version
root. Before applying a scoped transition, preserve original bytes, modes and
hashes, validate exact changes, and use the supported mutation lock and
compare-and-swap installer. Do not overwrite foreign skill directories or
rewrite historical task controllers and journals. Verify actual import roots,
wrapper startup and semantic hook cases after installation. Configuration edits
still do not prove existing clients reloaded or that callbacks were delivered.

For a late handshake ACK, retain the expired receipt, original detector error and
task/provider/nonce. Recheck live workspace and peer identity, parse the actual
assistant response with the canonical ACK parser, and publish the recovery
through the original receipt writer. Do not resend the challenge, invent an
earlier submission time, or blame executor silence for a supervisor timeout
below the phase minimum. Finalize a task pack before dispatch and include every
required prompt binding; a pre-input contract refusal is not a sent task.
