# 0.4.60: original reply observation

New enrollment records a file-only preference, honored by foreground run/tick
after original-controller verification. Old episodes are not migrated. A
recorded preference cannot change by reusing a waiting episode.

`observe-reply` authenticates the original executor under its original episode
lock. It checks operator stop, revocation, expiry and formal-owner changes
first. It sends no input, issues no ask, changes no counter or pending input,
consumes no reply and releases no reservation. An observed WAITING reply still
needs a tick to become ANSWERED, complete body reading and exact-SHA consume.
If an ANSWERED file changes, an immutable snapshot preserves the stored body;
the episode's reply remains intact.

A task-bound WAIT/STANDBY reply permits waiting only after its exact original
consume receipt and bindings verify, and only until that reply's own due time.
Unconsumed replies retain the read-and-consume instruction. Formally reserved
work keeps its own continue-assigned-work instruction and callback. Silence,
provider errors and scheduled wakeups grant neither STANDBY nor release.

Only episode-lock acquisition maps to `EPISODE_BUSY`; body I/O errors remain
visible. Partial channel write failures have explicit diagnostics. A visible
state candidate whose directory fsync failed is `COMMITTED_SYNC_UNKNOWN` with
its exact hash. `confirm-sync` checks the current candidate before retrying
directory fsync; a mismatch never causes file recreation or overwrite.

The complete reviewed 0.4.59 release is an explicit historical issuer and
controller. Its original range and pending evidence remain unchanged. The
reviewed c2d536f dispatcher hash is an approved upgrade predecessor. All hook
launchers require `-BE`; in-process cache-prefix refusal cannot protect stdlib
imports that already ran under a plain `-B` launch.
The guardian and file scheduler also generate `-BE` launchd commands; the
release-job checker rejects a remaining plain `-B` registration. This corrects
the nonblocking launchd observation from the 0.4.59 installation review.

Source validation, release, installation, original-session adoption, notice
receipt, full body reading, consume and rearm remain separate evidence.
