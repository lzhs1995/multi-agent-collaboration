# Finite, fair file sweeps

Version 0.4.65 keeps the ten-second outer file-work budget. After complete
CURRENT verification and bounded enumeration, each selected row has a slice
of ten seconds divided by the enrollment count, with a one-second minimum
and four-second maximum, capped by the remaining outer budget. Consecutive
timeouts increase that row's next slice up to four seconds. A row is tried at
most once per sweep; a timeout never retries its mutation or transport.

STATUS retains at most 256 service hints: last attempt, last completed
observation, next due, finished status and a bounded timeout count. These are
ordering data only. The original cursor breaks ties. Overdue or unseen work
precedes recent future-due and finished rows. At the same attempt age,
unfinished work precedes completed peers, so an interrupted last row in a
large sweep does not repeatedly fall behind them. Finished hints age out after
two sweep periods. Clock regression discards the prior hints; malformed values
are ignored. Every row still passes its original registry and episode locks,
binding, stop, authorization, reservation and complete controller checks.

A slice timeout emits EPISODE_BUDGET_EXHAUSTED, file_posts_complete=false and
may_have_committed=true. The aggregate counter includes only observed completed
posts; a zero is not proof that no file was committed. If the outer budget
remains, other rows continue. SWEPT_WITH_EPISODE_TIMEOUTS with pass_complete=true
means every selected row was visited, not that all work finished. Outer expiry
remains SWEEP_BUDGET_EXHAUSTED. Repeated timeout warnings are diagnostic; they
do not remove enrollment, defer authority or create a retry.

Complete release validation remains fresh on every sweep, with only the
existing within-sweep snapshot reuse and member/parent checks on every reuse.
No persistent validation cache replaces hashes. Slow release I/O consumes the
same outer budget and retains the prior cursor when it expires. Diagnostics,
final STATUS persistence, interpreter startup and host suspension retain their
separate measured boundaries; this change does not guarantee a 60-second
end-to-end response under suspension or arbitrary I/O load.

The complete 0.4.64 manifest, controller files, capabilities and original issuer
range are explicitly pinned for file-only compatibility. Old STOP, pending
input, reservations, callbacks and consumption remain with the original
executor and controller. Installing a release neither enrolls another session
nor proves its current process loaded hooks. Real request intervals, FULL body
reading, original consumption and useful deliverables need separate evidence.

Discriminating private controls cover slow-first/fast-peer service, eleven fast
plus one slow row, 64-row rotation, future and damaged hints, clock regression,
repeated timeouts, slow release validation and commit-before-timeout. Actual
timer controls exercise nested and outer deadlines. Boundary controls retain
stopped/revoked/expired state, exact reservation permissions, foreign bindings,
changed controller members and original UNKNOWN without live transport.
