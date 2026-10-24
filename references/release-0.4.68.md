# 0.4.68: bounded idle-request recovery

An idle ask persists its exact pending marker before creating the request file.
The request directory is synced before the durable asks commit and any input
opportunity. An explicit ask may finish a pre-creation interruption using that
same marker. If the original request exists, recovery remains read-only and
preserves its original payload, episode, target and no-resend boundary.

Legacy discovery no longer fails solely because 128 unrelated entries precede
the caller. It streams with constant memory inside a one-second local budget
and the existing finite invocation budget. A second fresh directory stream
checks metadata fingerprints, counts and directory identity before absence can
authorize a new request. Malformed, unreadable, changed, mismatched, multiple
and uncertain originals retain their refusal paths.

Incomplete progress is recorded as diagnostics under the original loop's
request_recovery field. An interrupted pass restarts. It does not store a
portable directory cursor or authorize a new request from a partial scan.

## Disclosed limits

A large or continuously changing legacy directory can repeatedly produce
INCOMPLETE or CHANGED. Preserve the diagnostics and use the exact original
record/controller for reconciliation. Repeated failure never establishes
absence and never grants a resend or a new episode.

The first pass reads files by path and relies on final path/directory/member
stability verification. The checks detect observed replacement and changes;
they are not an atomic guarantee against concurrent filesystem mutation.

The complete reviewed 0.4.67 manifest, controller members, file-protocol
descriptor, issuer range and capabilities remain explicitly pinned. An old
issuer cannot acquire 0.4.68 authority by naming CURRENT. Existing tasks,
callbacks, reservations and pending input keep their original controllers.

Source tests and private crash fixtures establish the checked behavior.
Installation, automatic client loading, body consumption, dissertation
acceptance and measured overall efficiency require separate evidence.
