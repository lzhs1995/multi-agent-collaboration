# Prospective sequential model-context consumption

Use `scripts/model_context_sequential.py` only as an explicitly bound prospective
evidence class. Its `model-context-sequential-consumption-observation-v2` output
is separate from the unchanged v1 observer and the original native-user contract.
It publishes no receipt, changes no queue or controller, and grants no input,
task acceptance, reply consumption, recovery, resource release or provider-health
claim.

A hash tool and a full-body Read may occur in separate consecutive Claude
2.1.303 requests. The observer verifies the exact measured notice wrapper and
its API content hash, then follows every immediate request and complete parallel
tool-result batch. Each next request must name the preceding request in
`base` and contain the complete canonical result-batch SHA exactly once in
`tail`. Missing or failed intermediate results, a skipped request, duplicate
IDs, a conflicting parent/source UUID, a version transition or compaction leave
the chain unverified. The full Read batch must reach an immediate request with
a real matching non-error response.

All actual parallel result blocks participate in their native record/block
order. The exact pinned UTF-8 body must match the structured full-file result,
its complete line counts and the numbered content delivered to the model.
Measured hash/path and size output must each appear exactly once in actual
Bash stdout, with empty stderr and no interruption. The three v1 measured
commands remain supported. The additional R76 form is exactly:

```sh
B=<exact safe body path>
ls -la "$B" 2>&1
echo "--- measured ---"
shasum -a 256 "$B" 2>&1
wc -c < "$B" 2>&1
echo "expected sha=<exact SHA> bytes=<exact byte count>"
date -u +%Y-%m-%dT%H:%M:%SZ
```

The expected echo supplies no measured evidence. Other shell forms remain
unsupported. The observer never executes any command found in a transcript.

The original attempt/wire/body pins, canonical transcript path, original session
metadata, inode and pre-input prefix/tail hashes are required. One contiguous
snapshot and all inputs are reread before output. APFS device observations are
retained; a device-number change alone does not reject this separate observation
when the independently bound inode, canonical path, bytes and ownership checks
still match. The original native reader and v1 observer retain their device
checks and their original receipts.

Limits are 8 MiB per complete snapshot, eight tool batches, 32 tools per batch,
the original complete-record bound, and a checked ten-second compute/read budget.
A synchronous filesystem call cannot be preempted by that internal clock;
the invoking owner also needs its own bounded process-observation budget.
Partial EOF, malformed data or exhausted bounds return `UNVERIFIED` with rc2.

Run with an absolute Python interpreter and `-BE`, supplying `--attempt`,
`--attempt-sha256`, `--text-file` and optionally `--snapshot-end`.
There is no waiting loop or automatic registration. A recorded original binding
does not assert that its process is alive now. New live consumer wiring,
campaign policy and adoption belong to the authorized consumer owner.

The closed R76 sample reproduces the two already frozen hashes
`9ab85b42…efbf2` and `cde0832a…9549f` over its original 91475-byte snapshot.
That is source verification of existing evidence. The failed original u21/A
adjudication, controller and cost denominator remain unchanged.
