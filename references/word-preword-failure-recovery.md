# Returning the S39 pre-Word failure lease

This maintenance entry settles one named, proven failure before Word/plugin
input. It does not alter `Broker.release`, the resource configuration, normal
success handling, or the treatment of other unknown results. No Word, Zotero,
AX, clipboard, network, or plugin command is available in this entry.

The first profile is `S39_INIT_ESCAPE_THEN_AX_DENIED_V1`, limited to ticket
`2adbf3c0-1928-497a-9a21-5ccc44421b80`. The fixture file pins the original S39
controller, task, source, normal-close receipts, initial pending archive, and
the later observer failure. This is an operational private profile containing
absolute evidence paths and the original lease token; it is not a general
portable release recipe. A different ticket or failure needs a separately
reviewed profile, not a caller-provided validator or a claim of zero inputs.

The public checkout contains the implementation and synthetic tests only. The
private profile is intentionally omitted and must never be committed or uploaded.
Without its exact locally provisioned bytes, every operational command refuses
before observing processes or opening the queue. The profile identifier and hash
in the source are integrity constraints, not credentials or permission to settle
another ticket. The tests construct temporary evidence and need no private file.

The original S39 settlement was completed by its owner on 2026-11-23; its failed
business outcome was retained. Publishing these files does not rerun that attempt,
change installed releases, or make the Word/plugin benchmark complete.

The original business return remains exit 1. The observer's original
`UNKNOWN_NATIVE_RESULT` bytes remain unchanged. Its exact source and error
offset prove that this particular failure occurred at the first AX window
read, before focus, menu, clipboard or RunJS input. Other unknown results
remain blocked. The returned proof says global Word and Zotero state is
`NOT_ASSESSED`; it never manufactures a `SIX_ZERO` receipt.

The entry supports four operations. Invoke the complete reviewed candidate
with Python `-BE`; select the fixed ticket and profile on every command:

```text
word_preword_failure_recovery.py preview --ticket <fixed-ticket> --profile <fixed-profile>
word_preword_failure_recovery.py apply --ticket <fixed-ticket> --profile <fixed-profile> --plan-sha256 <preview-hash>
word_preword_failure_recovery.py inspect-closeout --ticket <fixed-ticket> --profile <fixed-profile>
word_preword_failure_recovery.py recover-closeout --ticket <fixed-ticket> --profile <fixed-profile> --intent-sha256 <original-intent-hash>
```

Only the original execution SID, verified by the pinned OS ancestor verifier,
can preview or mutate the settlement. There is no alternate root, evidence
path, actor, process provider or validator CLI option. The runtime imports
only the original read-only actor verifier. It never loads a bootstrap,
plugin, native observer or old broker constructor. The queue is opened in
SQLite read-only mode for observations and existing-file read/write mode for
the exact transaction; no schema migration or broker configuration runs.

The profile checks immutable source and complete original evidence directory
sets, normal-close PID/birth, the failed invocation, both pending identities,
and absence of subsequent plugin/refresh attempts. Fresh system observations
check only the task's old processes/groups, own profile/data handles, own
runner arguments and port 23139. Permission errors, tool diagnostics, missing
evidence or reused old PIDs refuse settlement. Foreign Word documents are
not queried or modified.

Under the original queue, Word and focus locks, apply verifies the preview
again, writes and fsyncs a unique intent containing the exact preimages,
hardlinks and verifies the original pending inode into the settlement archive,
and keeps the live pending link. It then commits a full-row compare-and-swap
of the named settlement proof while retaining `ACTIVE`, invocation 1, token,
and every other ticket field. Only after this exact database proof is read
back does it record cleanup intent and unlink the matching live pending link.
After directory fsync, a second exact compare-and-swap marks it `RELEASED`.
Final readback and `COMPLETE.json` establish settlement completion.

SQLite commit and pending unlink are separate durable operations. A database
`ACTIVE` settlement proof alone is not a completed settlement. A crash before
the first commit keeps the original active lease and live pending; a crash
after that commit keeps the active lease and pending until exact recovery.
A crash after unlink still keeps the active lease, blocking the next grant,
until final status CAS. Recovery accepts only this existing settlement intent,
unchanged evidence/profile/source, same actual actor, and exact preimage or
committed row. Every existing published event and the fixed original intent
are checked before either database commit or pending unlink. Recovery fsyncs
the evidence and pending directories even when a previous invocation already
performed the relevant link or unlink. It never creates a new business attempt or interprets disappearance of
pending as proof of commit. A changed or foreign pending is preserved.

There are deliberate fail-closed limits. If the root Codex process itself
has been replaced since the settlement intent, the original actor comparison
refuses recovery even when the SID is the same; this candidate does not
silently authorize a new actor generation for an in-flight write. Receipts
are fsynced to unique temporary files and atomically hardlinked into their final
names. Unpublished partial bytes remain as evidence, never authority. An empty
private settlement directory or one containing only unpublished intent files
can continue with the unchanged plan and original active lease/pending. Other
unfinished states require the exact published intent SHA. If a subsequent task
creates a foreign pending after final release but before COMPLETE, this entry
conservatively refuses even receipt-only recovery and preserves that foreign
pending; it does not take ownership of the later task. Ordinary cleanup does not
grant new execution permission or establish Word plugin/benchmark success.

Run the synthetic regression suite from `scripts`:

```sh
python3 -BE -m unittest -v test_word_preword_failure_recovery
```

Tests build fresh temporary evidence, a temporary legacy queue, and real local
hardlinks. Native process readers and signals are replaced or forbidden.
The suite exercises failure evidence, TOCTOU changes, locks, pending ownership,
partial settlement, and the original ordinary `Broker.release` behavior.
Passing these tests is not installation, current-owner admission or live
settlement evidence.
