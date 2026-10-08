# Queue classification and bounded recovery

The measured Codex busy UI can leave a payload in compose after the first Enter.
Tab moves it under `• Queued follow-up inputs`, which waits until the whole turn
ends. A further Enter can move it to `Messages to be submitted after next tool
call`, which waits for a tool boundary. Long-running goals made the Tab route
appear stuck. These are distinct queues; neither proves consumption. Old Tab
attempts remain valid history, but the bridge no longer chooses that route.

If the entire original payload still occupies the measured Codex composer,
the bridge records at most one additional Enter intent, rechecks identity before
the key, and reads the result. This includes measured compaction, but not
reconnecting, unknown receivers, changed drafts or already-queued payloads.
The Tab hint is not required for a simple exact composer; unmeasured footer
shapes remain closed. Do not paste again or loop keys.

Recognize the observed queue header and stop the queue region at the next
composer. A marker in that composer or above the queue is not queue evidence.
Wrapped markers may be matched within the queue. Empty markers prove nothing.
`DELIVERY_QUEUED_AT_RECEIVER` remains unconfirmed. A next-tool queue must not be
confused with a whole-turn queue when reporting when the receiver may consume it.

Keep the original journal. Do not paste again, loop Enter, renew the nonce or
repeat a successful handshake to repair a classification error. Reconcile the
original attempt against actual receiver evidence. Continue independent work.
Do not call local discovery or UI classification failures Claude API errors.

Regression: `scripts/test_queued_followup.py` checks the measured display,
wrapping, unrelated markers, composer boundaries and one-send-only behavior.
`scripts/test_bidirectional_submission.py` verifies one paste, no Tab and at most
two Enter keys, including compaction and both queues. Callback recovery tests
check unchanged bindings, lock replacement, persisted key intent and failure
timestamps. Existing receiver and callback tests remain required. Offline
tests and a patched source are distinct from installation and live end-to-end
acceptance. Preserve in-flight task runtime bindings; do not overwrite releases.
