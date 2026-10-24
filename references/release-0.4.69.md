# Provider classification and original zero-input recovery

Codex terminal errors may carry integer HTTP status codes under
`codex_error_info.http_connection_failed.http_status_code` or camelCase aliases.
The circuit reads those structured fields, including a JSON error nested in the
message. Prose numbers and boolean/string statuses do not establish HTTP status.
Conflicting integer aliases leave HTTP classification unknown. Explicit quota
or balance failures are BILLING even alongside403; bare403 is AUTHENTICATION.
One billing/authentication failure or three distinct retryable failures can
open the existing circuit. Only a real authenticated terminal success rearms it.
Classification never changes a provider, resumes a Goal or disables file inquiries.

## A separate explicit ordinary-message recovery

Ordinary automatic dispatch remains bounded to two attempts. Both may have
failed in preflight without issuing input. Use the new coordinator only for
exactly two contiguous original `NO_INPUT` records with empty events, no result
and no contradictory input evidence. It does not recover formal task packs.

```bash
python3 -BE scripts/resume_message_no_input.py \
  --controller /absolute/original/complete/source \
  --surface ORIGINAL_RECEIVER_UUID --marker ORIGINAL_MARKER \
  --text-file /absolute/exact-original-wire.txt
```

The default returns `READY_ZERO_INPUT`, the complete `history_sha256` and zero
input operations. It creates no dispatch attempt, receipt or recovery slot.
Review that exact plan, then use the same arguments with
`--apply --history-sha256 FULL_REVIEWED_SHA256`.

The coordinator verifies the complete original controller before using its
protected sender and native reader. The reviewed ordinary journal driver is
unchanged in0.4.58 through0.4.69; older or future formats are refused. At least
one original authenticated native binding is required. An absent later native
observation remains absent; process/session/transcript identity is never invented.

Apply holds the existing pane/target/journal locks and rechecks the original
payload, referenced body identity, complete two-record history, runtime and
controller. The exclusive recovery slot and its directory sync consume the one
budget before a fresh third attempt. Native Codex uses one same-thread steer or
start and a fresh durable input fence; terminal fallback retains its real draft
and host-result checks. Busy work is not an input refusal.

A fresh preflight refusal, failed write, crash or UNKNOWN after reserving that
slot never grants another attempt. Missing original records/locks, mixed or
unreviewed controllers, changed payload/endpoints, input intents, queued input,
receipts, nonstandard JSON and contradictory input counts are refused. Preview
cannot make missing evidence into zero-input proof.

After uncertain third input, reconcile the same attempt through the original
controller using the original `--surface`, `--marker` and `--text` plus
`--reconcile-only`. Never rerun recovery apply, repaste or add a blind Enter.
Only the fresh original receiver marker user record proves notice reception;
body reading, work acceptance and callbacks remain separate.

## Version and evidence boundaries

CURRENT retains the complete immutable0.4.68 producer and its original issuer
range/capabilities. It never imports old producer code, upgrades an old inquiry
or transfers callback/resource ownership. No current provider, session, journal
or enrollment changes from a source test. Installation, actual client hooks,
native reception, full body consumption and accepted paper output each need
their own evidence. Test counts are not overall efficiency measurements.
