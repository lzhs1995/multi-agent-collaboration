# Queue classification and bounded recovery

The measured Codex busy UI can leave a payload in compose after Enter, display
`tab to queue message`, then move it under `• Queued follow-up inputs` after the
bridge's single authorized Tab action. The edit-queue hint appears below the
payload. A detector searching only below that hint mislabels the queued message.

Recognize the observed queue header and stop the queue region at the next
composer. A marker in that composer or above the queue is not queue evidence.
Wrapped markers may be matched within the queue. Empty markers prove nothing.
`DELIVERY_QUEUED_AT_RECEIVER` remains unconfirmed; neither Enter nor queue entry
proves consumption, report acceptance or task completion.

Keep the original journal. Do not paste again, loop Enter, renew the nonce or
repeat a successful handshake to repair a classification error. Reconcile the
original attempt against actual receiver evidence. Continue independent work.
Do not call local discovery or UI classification failures Claude API errors.

Regression: `scripts/test_queued_followup.py` checks the measured display,
wrapping, unrelated markers, composer boundaries and one-send-only behavior.
Existing bidirectional, receiver and callback tests remain required. Offline
tests and a patched source are distinct from installation and live end-to-end
acceptance. Preserve in-flight task runtime bindings; do not overwrite releases.
