# Non-blocking coordination and successor recovery

0.4.19 supersedes historical instructions that freeze every tool, require an old
supervisor for handshakes, or block Stop while awaiting a reply. Installed workflow
hooks use cmux_workflow_advisory.py; historical checker modules are diagnostic only.
The sender still refuses duplicate input, an occupied draft or an unverified target.
That refusal applies to one delivery and must not end authorized work.

The raw cmux identify caller may belong to the shared daemon. Use cmux-agent self,
which invokes the native foreground-thread resolver also used by the transport.
Do not infer failure or a cross-workspace caller from inherited environment.

For an authorized successor or maintenance coordinator, bind exact endpoint UUIDs
and user-message evidence with successor-rebind-v2. Maintenance communication does
not require prior task settlement, prove old supervisor failure, replay old packs,
or authorize shared writes. Keep old task evidence and create distinct new scope.

Before input, journal the complete payload, live process/session/transcript and
fresh EOF fence. One paste and one submit maximum; retries reconcile read-only.
A new exact native user record after the fence proves reception. ACK, body reading,
callback and acceptance remain separate. Never present installation, manual hook
execution, a queue item or a saved report as proof of all live clients adopting it.

One owner integrates shared fixes. Other agents send evidence to that owner while
continuing their original work. Use zero, one or two executors for independent
outputs. API failure triggers bounded recovery then authorized SOLO takeover.

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

A busy Claude may expose a `queued_command` attachment while working on a task.
Preserve that observation without declaring an exact native-user receipt. Never
resend merely because an ACK or report preceded that record. Executors must wait
for a finalized, hash-pinned task notice rather than treating a draft pack found
on disk as authority. Continue independent authorized work in SOLO when a peer
is unavailable; maintenance does not reopen accepted research reviews.

0.4.24 also recognizes local SQLite and disk-space inventory imports in quoted Python document heredocs. Agent names after semicolons inside literal document data are not CLI launches; adjacent real shell launches remain checked. Verify the exact rejected command through the real hook before claiming recovery.
