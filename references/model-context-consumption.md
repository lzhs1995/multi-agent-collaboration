# Separate Claude model-context consumption evidence

For a separately bound prospective policy spanning immediate requests, see
[the sequential observer](model-context-sequential.md). The v1 checker described
below remains unchanged and cannot reclassify an original failed observation.

The read-only `scripts/model_context_consumption.py` verifies one complete,
originally bound Claude 2.1.303 MESSAGE_REFERENCE_V2 notice and body chain. Its
`MODEL_CONTEXT_CONSUMED` observation is distinct from `NATIVE_USER_RECEIVED`.
It never writes an attempt or official receipt, changes a queue, sends input,
consumes a reply, releases resources, or accepts business work. Ordinary native
receipt validation remains unchanged. Historical controllers keep their exact
contracts; this checker cannot retroactively pass a handshake or repair its
frozen-reader-before-ACK ordering.

An explicitly reviewed **new** task policy may bind this evidence class before
its task/attempt is frozen. The original supervisor still checks actual native
identity, complete source and input pins, reading order, authority and acceptance.
Only the private upgrade owner integrates its asynchronous consumer and campaign
gate; this shared checker does not enroll a worker or synchronous hook. No old
u19/u20 run, receipt, child allowance or business denominator is upgraded.

## Required chain

1. Keep the original complete attempt SHA, native process/session/UUID/TTY binding,
   fresh pre-paste EOF fence, transcript device/inode and prefix/tail hashes, exact
   wire SHA and exact body SHA/bytes. Read only complete records in a bounded
   original snapshot and verify a stable reread. A recorded binding does not claim
   current process availability or provider health.
2. In the same original session, find one API `user` text block equal to the fixed
   measured wrapper around the entire exact wire. Verify its compact sorted UTF-8
   JSON SHA and a unique main-thread API request referencing that hash. A matching
   actual assistant `requestRef` must supply the subsequent body-reading tools.
3. Match native successful SHA/byte output and a complete exact-path `Read` result
   by tool ID and the original assistant parent UUID. The returned UTF-8 body must
   equal the pinned bytes, with a complete range and no error or interruption.
   The Bash command must exactly match a measured hash-and-size form. An echo,
   a path substring or a SHA without the exact path and size output is refused.
4. Reconstruct **all** parallel native tool-result blocks from that response in
   native order, including an additional challenge read if present. Verify the
   compact sorted UTF-8 JSON SHA appears in a later same-session main-thread API
   request, then require its matching actual response. API blob logs may omit a
   cached message; the exact reproducible hash still binds the complete content.

The measured API blob can omit its `version` field. If supplied it must be
2.1.303; both API requests, their real responses and native tool results must
carry that version. Duplicate JSON keys, nonfinite values and malformed
message/result objects cannot supply the join. Unsupported layouts remain
unverified rather than being interpreted as a different receipt contract.

Only queued/remove events, a marker echo, a recorded request without its response,
a tool-output-only wire, arbitrary wrapper text, a different wire with the same
marker, partial Read, a different session/fence, or an incomplete result batch
remain `UNVERIFIED`. Versions and Read representations not covered by this exact
contract also remain unverified. This observation establishes exposure and
consumption of the notice/body context; it does not establish semantic agreement,
downstream execution, scientific acceptance or native-user receipt.

The exact wrapper is:

```text
The user sent a new message while you were working:\n
<exact original wire>
\n\nThis is how Claude Code surfaces messages the user sends mid-turn — within the running turn, often alongside the next tool result, rather than as a separate conversation turn. Address the message above as you continue this turn.
```

## Read-only use

Run the fixed release's `model_context_consumption.py` with `Python -BE`,
`--attempt`, `--attempt-sha256`, `--text-file` and optional `--snapshot-end`.
Without an end it takes one bounded complete snapshot; it never waits or sends.
The JSON has no primary `confirmed` or published receipt. Preserve the complete
stdout/stderr and command result separately. A candidate source run is source
validation; installation, active loading and a newly bound live consumer remain
separate acceptance steps.
