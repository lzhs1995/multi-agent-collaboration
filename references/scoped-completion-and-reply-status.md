# Scoped completion and reply status

Version 0.4.62 also exposes the ordinary-body preparation CLI:

```sh
python3.14 -BE /absolute/installed/source/scripts/cmux_prompt_reference.py prepare \
  --text-file /absolute/new-body.txt --sha256 SHA256 --marker UNIQUE_MARKER
```

It reads the exact pinned UTF-8 body, persists its immutable reference when
needed and returns the short `text` for a separate authorized bridge submission.
It sends no input. `--help` lists this command. Formal packs and callbacks retain
their dedicated routes; unknown prior input is reconciled, never prepared anew.

Every new bounded maintenance work order declares its acceptance, progress and
final paths separately. It also declares `completion_channel`: `ORDINARY_NOTICE`
for scoped review work, or `FORMAL_CALLBACK` for a finalized harness task pack.
Use an original unique result marker, absolute final path, exact bytes and SHA256.
Do not invent a harness task, nonce or reservation for an ordinary scoped review.

For an issued scoped order, `scripts/scoped_work_completion.py --work-order
/absolute/order.json --work-order-sha256 SHA --report /absolute/result.json
--report-sha256 SHA` reads both exact bodies and returns a zero-input ordinary
notice plan. Its `submit_argv` uses the issued complete controller's actual
`cmux_bridge.py submit-text --text` entry with bounded wire text as one argument,
`-BE`, the original marker and a zero-second observation budget. Execute that
argument list directly; `submit_command_template` is its shell-quoted display.
The public bridge refuses raw text above its wire budget. When
`persist_body_required` is true, first save the planner's exact `body` to a new
absolute UTF-8 file, substitute that path in `persist_body_argv_template`, and
execute that list through the same issued controller. Its existing
`cmux_prompt_reference.persist` validates SHA and bytes, writes an immutable body
once, and refuses a changed prior body. Only then execute `submit_argv`, which
contains the short MESSAGE_REFERENCE_V2 notice. Planning writes no files or input;
body persistence and sending are separate authorized operations. The
helper checks that this is the declared final output; it does not
establish authorization or send. It refuses formal harness packs, which retain
their original `submit_completion_callback` route. An in-flight unknown notice
is reconciled only through its original attempt and controller.

From 0.4.66, `--prepare-body` explicitly persists the exact body and returns
ready `submit_argv`; it never sends. Without this flag, planning has no writes.
Alternatively execute `persist_body_argv` with the exact UTF-8
`persist_body_stdin`, then `submit_argv`. The legacy file template has
`persist_body_file_argument_index=6`; replace that placeholder before execution.
An issued controller and `required_skill` pin override any later global skill
link; conflicting aliases or changed skill bytes refuse before a plan.
Observed output path aliases, including top-level scoped order paths, are
accepted only when all supplied names agree and the three stages remain
separate canonical absolute files. No original order is rewritten.

An ordinary result notice is not a formal DONE callback. A notice's native
receipt, referenced body reading, parent acceptance and resource RELEASE are
separate. Freeze consumed finals; later changes use a new suffixed result with
the predecessor pin and revised scope. No ACK-only task or new clock is needed.

Capture the complete stdout **and** stderr and preserve the return code when
executing `submit_argv`. The bridge's expected `rc=75 / NATIVE_PENDING` path
can place the whole receipt on stderr with empty stdout. Keep both streams in
separate original files; an empty stdout file is not evidence of zero input.
Use `submit_capture_contract.original_reconcile_argv` for a later zero-input
observation. It retains the original `--surface`, `--marker` and `--text`;
all three remain required with `--reconcile-only`. Never resubmit on rc75.

Before publishing a reply candidate, `scripts/executor_reply_preflight.py`
accepts exact absolute `--state-file/--state-sha256` and
`--body-file/--body-sha256` pairs. It checks the original WAITING episode,
issued marker (including signed late markers), binding, supported status and
continuing wait owner/deadline; it refuses a changed file or inode. This is a
read-only structural diagnosis, not authentication, publication, body reading
or consumption. Publish only through the authenticated original supervisor.

`STANDBY`, `HOLD` and `WAIT` are prose instructions, not mailbox statuses. Use
`status=WAITING_DEPENDENCY` with a concrete trigger. For a continuing wait,
`owner` must equal the original supervisor and finite UTC epochs must satisfy
`0 < next_due_epoch <= deadline_epoch`. After copying the issued binding and
marker, use a trigger such as `Wait for the pinned patch review order, otherwise
report availability once at the stated due time`; set both epochs to that actual
due time. Preserve a refused reply and create a new candidate; do not overwrite
historical replies or widen an old episode.

Reply reminders describe the actual phase:

| Recorded phase | Next action |
| --- | --- |
| UNANSWERED | Continue the original authorized work or original due inquiry. |
| ANSWERED_UNCONSUMED | Read the FULL exact body, then original-controller SHA consume. |
| CONSUMED | Follow the actual reply and continue assigned work. |
| CONSUMED_STANDBY | Follow the verified task-bound waiting reply until its own due time or resumption condition. |
| RESERVED_ASSIGNED_WORK | Continue the original reserved task; consume does not RELEASE it. |

The status command reports these as diagnostics, reusing Stop's original
receipt-bound STANDBY rule. It never consumes, changes the episode or widens
authority. Recorded provider failures and their observation times are historical
facts; `status` does not run a live provider request or prove API availability.
`NO_EPISODE` explicitly means reservations were not observed without an episode.
It does not mean no reservation exists. Unreadable or replaced state is distinct
from an absent file.

Episode archives use a complete synced staging file before exclusive publication.
Existing archives are validated against caller, supervisor, workspace, task,
episode and original controller. Their existence is not FULL reading or consume.
A failed first file sync publishes no final; directory-sync uncertainty may leave
the complete final present, which must be inspected under the same original lock.
No old archive, controller, pending nonce or receipt is overwritten or replayed.
