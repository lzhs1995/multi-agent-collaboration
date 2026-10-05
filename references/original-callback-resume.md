# Original callback recovery candidate

This source candidate adds `submit_completion_callback(..., resume_queue_only=True)` for an original executor whose recorded Enter left the complete callback in the Codex composer with an explicit Tab queue hint. This is not a generic retry or a new handshake.

The controller requires the original task/report/identity binding, a single recorded paste and Enter, original screen hashes, no previous Tab intent, the exact current composer, unchanged task and report, unchanged journal and both held lock inodes. It persists the Tab intent before at most one Tab. It never pastes or sends another Enter. A crash or uncertain outcome prohibits another Tab. A visible queue is not completion; only actual consumption can create a receipt, otherwise use read-only reconciliation.

Both directions must preserve drafts and distinguish pasted, queued and consumed messages. Reports already read by the supervisor do not manufacture delivery receipts. The supervisor must not impersonate the executor to recover a callback.

Validation: 107 callback, native-evidence, observation, receiver and bidirectional tests passed locally on 2026-10-05. This candidate has not been deployed or verified against the live original callback. Existing pinned runtimes remain unchanged; do not copy an individual module into them. Full compatible installation and original-executor verification are still required.
