# Unresolved Word references and exact read-only PRE failure closeout

Word can return an unsaved recovery document's display name, such as `文件1`,
as its POSIX full name. The native inventory now accepts that bare reference
only when `saved=false`, the reference equals its document name, and exactly
one original DOC and one WIN row contain it. Path-like values, `.`/`..`, saved
or unknown save states, control characters and ambiguous references remain
refused. Absolute and `MISSING` references retain their original representation.

An accepted bare reference remains display metadata. Its extra classification
is `UNRESOLVED_UNSAVED_BARE_NAME`, `filesystem_path=null`, `ownership=UNKNOWN`
and `writable=false`. Original DOC, WIN and ACTIVE rows are unchanged. The
existing PDF and revision consumers compare the complete inventory before and
after their owned operation, including this classification. Parsing an unsaved
reference establishes no file ownership, idle application or save/close right.

The original 0.4.72 owned revision can fail during its read-only PRE inventory
with `NATIVE_INVENTORY_UNAVAILABLE` and AppleEvent `-1712`, consuming one broker
invocation while leaving its restricted ticket PENDING. The new closeout profile
accepts only the complete published 0.4.72 source: 385 members, 5,508,124 bytes
and executable flags with tree digest
`bf4cf132c5b8a7d30677ae4a1e9e260f0ddff3d076d94c594704b2075a7f1aad`.
An immutable private copy may retain its original path. Every original Python
controller pin must match; the old controller is read, never imported or edited.

All four original evidence files are required: `failure.json`,
`PRE-inventory.json`, `application-bindings.json` and `pre.applescript`.
The script must match the fixed read-only inventory script. PRE must have
returncode 1, empty stdout and only the original script's Word `-1712` diagnostic.
Word and Zotero process bindings must agree with the original application record,
and `word_launch_requested` must be false. Existing child/result, absent output,
staging and application-intent checks remain in force. Another failure, partial
metadata, extra stages or changed evidence keeps the ticket PENDING.

Use the new complete controller's `scripts/word_preinput_recovery.py preview`
with Python `-BE`, the original queue `--root` and `--ticket`. Preview sends no
application input and changes no queue. Only the authenticated original actor
may run `apply --plan-sha256 <exact-preview-sha>`. The existing same-session
surface-resume option retains its original thread/workspace/pane checks.
Apply repeats the full checks under the original queue, Word and desktop-focus
locks and CAS-updates only this restricted ticket to FAILED_PREINPUT. It retains
the consumed invocation, original result, token, nonce and every evidence file.

This closes one proven pre-input failure. It grants no replay,
third request, ordinary legacy return, citation Refresh, foreign save/close or
manuscript acceptance. Subsequent work requires a separately approved job and
current FIFO, capacity and application-readiness evidence. The original 0.4.70
binding-failure profile remains available without changing its original source.

Private parser/consumer and queue controls, together with a real original-ticket
read-only preview, verify source behavior. Release, installation, actual original
actor closeout, native manuscript output, client adoption and overall efficiency
are separate observations.
