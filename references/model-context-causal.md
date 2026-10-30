# Prospective causal model-context consumption

Use `scripts/model_context_causal.py` only for a newly and explicitly bound
observation. Its `model-context-causal-consumption-observation-v1` output and
`MODEL_CONTEXT_CAUSALLY_CONSUMED` class are separate from the unchanged v1
and sequential observers. None publishes a receipt, changes an original
attempt, or grants input, business acceptance, reply consumption or release.

Claude 2.1.303 can append an actual tool result after the API request that
already includes it. Physical file offsets alone therefore cannot identify
the preceding request's complete result batch. The causal observer requires:

- The immediately following main-thread request names the preceding request
  in `base`; each selected non-error response names it in `requestRef`.
- Every native tool ID and record UUID is unique. Each successful result
  matches its tool ID, native parent/source assistant UUID, session and
  version. Failed, interrupted, foreign or duplicate results remain refused.
- Every actual parallel result associated with that response batch is
  included. An orphan result or missing call cannot be hidden by selecting
  and resealing a subset.
- The SHA of the complete canonical user result batch, in actual native
  record/block order, occurs exactly once in the next request's `tail`.

The consumer reuses the complete pinned UTF-8 Read result, measured Bash
hash/byte forms, numbered content, original attempt/wire/body pins, transcript
metadata and stable snapshot checks. Its full Read must reach a real matching
non-error response to the immediate next request. Skipped requests, compaction
or version transitions leave the chain unverified. Offsets and whether results
were physically between requests remain explicit provenance.

Run with an absolute Python interpreter and `-BE`, providing `--attempt`,
`--attempt-sha256`, `--text-file` and optionally `--snapshot-end`. Limits
remain one complete 8 MiB snapshot, eight batches, 32 tools per batch and the
checked ten-second compute/read budget. There is no waiting loop or automatic
registration. Installing source does not prove live consumer adoption.

From .81, a tool result whose call preceded the original paste fence can be
disambiguated through that exact transcript prefix. The observer streams and
checks its captured prefix SHA under the same ten-second budget, retaining at
most the last 8 MiB of complete pre-fence records. This context is loaded only
when an in-window result has no in-window call. A unique non-error native call
must match the result's tool ID, session, version and parent/source UUIDs.
Prior-call matches are published separately in `pre_fence_context`, outside
the notice's `proof_slices` and complete canonical result batches. They grant
no receipt, task acceptance or resource action. A call absent or clipped from
this bounded context remains unverified; unrelatedness is never inferred merely
from an unknown ID or parent. Unknown/duplicate calls and resealed subsets keep
their original refusal checks. The v1 and sequential implementations are unchanged.

R204 independently found this boundary shape in u24 Claude A: the real call
ends exactly at its original fence and its same-parent/source result follows
the fence. That observation does not run .80 against u24, identify the cause of
its old .78 verdict or change its result/controller/window.

The four frozen u24 records verify the component batch join and its rejection
of wrong base, wrong parent and missing whole-batch SHA. The local
`verification/check_causal_real_cases.py` accepts their exact report path and
SHA with `--report` and `--report-sha256`; it reads pinned evidence and never
executes transcript commands. Those regressions do not prove full-body
consumption or reopen the original failed window. A future campaign must bind
its own complete controller and evidence; old u24 results and inputs remain.
