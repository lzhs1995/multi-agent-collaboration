# Original callback recovery candidate

`submit_completion_callback(..., resume_queue_only=True)` recovers an original
executor's recorded first Enter when the complete callback is still in the
measured Codex composer. The API name is retained for compatibility; the action
is now one additional Enter, never Tab. Busy Codex and measured compaction are
supported; reconnecting and uncertain/changed composer contents remain closed.
This is not a generic retry or a new handshake.

The controller requires the original task/report/identity binding, a single
recorded paste and Enter, original screen hashes, no previous Tab or extra Enter
intent, the exact current composer, unchanged task and report, unchanged journal
and both held lock inodes. It persists `EXTRA_ENTER_INTENT` before the one key.
It never repastes. A crash, key error or uncertain outcome prohibits another
attempt. The original return is preserved; resumed success, failure and pending
outcomes all record their actual return time after the new events. A visible
queue is not completion; only actual consumption can create a receipt,
otherwise use read-only reconciliation.

Both directions must preserve drafts and distinguish pasted, queued and consumed messages. Reports already read by the supervisor do not manufacture delivery receipts. The supervisor must not impersonate the executor to recover a callback.

Validate through `test_callback_journal`, `test_callback_queue_resume` and the
bidirectional/receiver suites. Offline tests are not live acceptance. Existing
pinned runtimes remain unchanged; do not copy an individual module into them.
The recovery loader still uses the original task pack's controller. A candidate
in another checkout does not silently upgrade an in-flight task or authorize
repeating a previously attempted key.
