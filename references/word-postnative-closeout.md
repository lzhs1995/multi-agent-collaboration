# Original owned Word failure closeout

The explicit `scripts/word_postnative_closeout.py` CLI closes one restricted
original 0.4.75 invocation that completed OPEN, SAVE, CLOSE, REOPEN and RECLOSE,
then stopped on `ORIGINAL_REVISION_HEADERS_CHANGED`. It performs no Word,
Zotero, focus or terminal input and never repeats that invocation.

## Evidence and authority

The read-only `preview` binds the complete published original release and
controller pins, original restricted plan, disposition and actor, exact failed
result/argv/nonce/PGID, source input, all five exact scripts/intents/results,
original PRE inventory and bindings, and the changed native revision headers.
The original process group must be empty. Accepted/rejected/tracked publication
paths, the requested PDF and the Word pending file must be absent.

All original evidence and stage files are pinned with bounded scans and stable
rereads. Evidence is limited to 64 entries and 32MiB; DOCX inspection allows at
most 512 unique ZIP members, 8MiB per Word XML and 32MiB total Word XML.
The stage contains only the original tracked DOCX and optionally its exact
same-basename directory with one `備份 <tracked filename>` file. This companion
must be byte-identical to the original input. It is retained and labelled
`INPUT_IDENTICAL_SAVE_COMPANION`; its producer is not independently attributed,
and it is not a manuscript artifact. Changed, extra, linked or special files
refuse closeout without deleting anything.

Only the original authenticated native actor can invoke `apply` with the
reviewed `plan_sha256`. Current process ancestry must use the supported direct
shell/rtk route with Python `-BE`. Existing queue, Word and desktop-focus flock
paths are acquired without creating them or following links. Evidence, actor
identity, lock identity and all row fields are rechecked under those locks and
again inside the short SQLite transaction.

The single transaction changes only the restricted ticket's status to
`FAILED_POSTNATIVE` and inserts a separate
`word_restricted_postnative_closeouts` receipt. Its original result, plan, token,
nonce, parent PID, PGID and consumed invocation remain byte-identical. Any
preimage/CAS or receipt-insert failure rolls back the status and schema changes.
A closed invocation cannot be rerun, refunded or closed again.

## Meaning and next work

`OWNED_POSTNATIVE_CLOSED` establishes a terminal failure of this owned job.
It neither accepts tracked preservation or table geometry, publishes official
manuscript/PDF outputs, grants a new job, performs citation Refresh, nor releases
the quarantined ordinary Word ticket. New owned work requires its own reviewed
authorization and FIFO admission. The original ordinary owner and its complete
native drain/release evidence remain a separate dependency.

Keep the preview, exact approval, complete command stdout/stderr, closeout
receipt and original failed evidence separately. Installation, actual original
actor apply, scientific review and eventual manuscript acceptance are distinct.

