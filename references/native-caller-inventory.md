# Native caller inventory failures

A zero-input identity refusal occurs before executor delivery. Do not call it a
Claude API failure, unavailable executor, or missed ACK. Preserve the first
failure and continue authorized independent work while repairing discovery.

## Darwin process inventory

A live Codex process can be absent from `pgrep -x codex` while kernel executable
and argv inspection still identify the expected original resumed session. A
negative name query is not proof of process absence. Enumerate same-user `ps`
PID/executable rows, then independently validate each candidate with the existing
kernel executable, UID, birth, argv, terminal and cmux UUID checks. Discovery is
not authentication. Duplicate matching clients, changed process identity and
cross-workspace peers still deny input.

An unrelated discovery row may disappear between `ps` and the kernel read.
Only an initial ESRCH or a verified owned zombie is safely excluded; an unknown
read, denied permission, or exit during a partial read still refuses. Read a
nonmatching candidate twice with unchanged kernel identity and argv before
excluding it. Relative argv paths on these stable nonmatches do not authenticate
a caller. Matching session candidates retain strict executable-path validation
and the final identity recheck; a second match remains ambiguous.

Global dock surfaces can appear nested under a workspace in the cmux tree.
Their `dock_scope=global` flag excludes them from local peer discovery and from
both caller and target bindings. Duplicate UUID rows still refuse; filtering
must not erase an ambiguous local/global identity.

Do not infer the caller from focus, titles, remembered surface numbers or stale
observation files. Options before resume and thread switching require separate
supported identity proof; this narrow fix does not claim those cases are solved.

## Bounded diagnosis and acceptance

1. Resolve the caller before spending an ACK timeout. A deterministic local
   refusal triggers one evidence capture and repair, not repeated handshakes.
2. Keep existing executor context. Allocate independent work only to an actually
   available bound peer; do not queue reviews behind existing tasks.
3. External helper parity failure concerns that helper, not the executor. Use
   the shipped guarded bridge for both directions and preserve all identity gates.
4. Verify identity, then a nonce-bound ACK. Check after Enter: compose, queued,
   consumed and accepted are distinct states. Unknown delivery is not resent.
5. Report source tests, installed runtime, live ACK and callback separately.
   Passing a read-only guard does not establish bidirectional delivery.

Regression mapping: `test_cmux_daemon_identity.py` covers executable-path
inventory including spaces, failed/malformed/duplicate inventories, exact
basename selection, missing/duplicate sessions and identity drift;
`test_cmux_workspace_guard.py` preserves workspace and UUID boundaries.
`test_caller_inventory_boundaries.py` covers typed exit evidence, stable excluded
candidates, changed/unknown/duplicate caller candidates, and nested global docks.
