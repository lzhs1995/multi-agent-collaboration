# Successor plans across polling observations

Version 0.4.63 gives a new successor preview a semantic plan hash. A normal
file sweep may advance check_count, reply_scan_ordinal and last_poll timing
without invalidating the reviewed plan. Counters must be integers rather than
booleans; the scan ordinal remains within the unchanged attempt count. Timing
must be finite and nonnegative, with at most five seconds of future clock
tolerance. This comparison does not establish monotonic history for discarded
observation counters.

The enrollment's zero-post last_execution observation is normalized only for
the exact current scheduler source and provenance, unchanged episode state and
zero input. The producer stays in the semantic pin even before the first
observation. Source, producer, state, post counts, unknown fields and
release_resume_observation remain significant. A first sweep after a producer
upgrade may therefore require a fresh preview. An enrollment's file_posts is
the sweep delta, rather than the episode's cumulative post count.

The preview returns original_observations with the complete original state,
enrollment and stop file SHA256 and byte counts separately from plan_sha256.
Apply still requires the reviewed plan SHA. Under the original registry and
episode locks it rebuilds the semantic plan, captures the current complete
raw files and archives those exact preimages. The archive includes both plan
and preimage digests, so distinct observations cannot collide at one archive.

Before the registration token commits, apply checks the raw state and stop
files, the original enrollment SHA in the immutable transaction intent and
its envelope, and the authenticated original executor, target and authority.
It rereads the raw files after endpoint checks too. Cooperating writers use
the existing locks; these reads do not promise atomic exclusion of an
uncooperative writer after the last check. A failed transaction stays
REGISTRATION_UNCOMMITTED or COMMITTED_RECOVERY_REQUIRED and uses its original
recovery path.

New asks or file posts, replies, reservations, pending input, task and episode
identity, controller, stop and authorization changes require a new review.
Unknown JSON fields stay hashed; duplicate keys and nonfinite values are
refused. No original callback, pending send or shared write ownership moves.

The private regression runs the real scheduler sweep between preview and
apply, including the executor_reask.py CLI entry. Negative controls retain
byte-identical live files on preflight refusal and keep commit failures
recoverable without input. Offline evidence does not prove original-session
adoption or two real reply/consume/rearm rounds.

