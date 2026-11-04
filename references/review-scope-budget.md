# New-task review scope accounting

`scripts/collaboration_review_budget.py` is an explicit local SQLite ledger for
new tasks adopting the nested research contract. It never sends a review, starts
an agent, grades evidence, grants application access or changes an old task.
Existing `mac_harness.py` rounds, original minimum-round rules, thesis
`workflow.py`/`delivery_audit.py` and same-PDF NLM acceptance remain authoritative.
Reference their original result files; do not issue replacement reviews merely
to populate this ledger.

The thesis requirement for two complete, independent NLM reviews of the same PDF
must still be proven by the original workflow and its domain audit. A `REUSE`
result from this helper is never evidence of a second independent review and
cannot satisfy either missing NLM gate. This helper does not take over domain
round scheduling. New contracts that require independent stages must declare
those stages and their independence before review; the role label and cached
verdict alone do not prove independence.

## Contract and boundaries

An authorized owner designates one canonical absolute database path per project.
Creation requires `new_tasks_only: true`, a project ID, original owner label and
a complete path/bytes/SHA256 reference to the existing project decision. It
refuses an existing path rather than reinitializing it. A partially initialized
file after a crash must be inspected; it is not automatically replaced.

Each registration fixes the scope ID, issue lineage, original owner, standard
reference, required reviewer roles with their review subscopes, and an attempt
ceiling of 1–3 per role per round. Registration is immutable, including before
the first round. The project owner remains the decision reference authority;
execution may pass to another owner without changing the contract. This is a
declared responsibility, not native identity verification or a new account DB.

One lineage maps to one canonical scope. An explicit alias points to that same
scope and budget. Changing an artifact SHA, executor owner, model, transport or
alias does not create more rounds. Re-registering the same scope with another
lineage, new standard or added reviewer is refused. A second scope with the same
lineage must use an alias. Truly independent predeclared scopes may work in
parallel. The ledger does not infer parent-child scope trees or semantic overlap.

**A new scope and a new lineage together cannot be recognized as a disguised
old research issue automatically. Nor can this local database prevent copying
it or creating a second project database.** The original owner must reconcile
those decisions with prior scope/history and keep the canonical project ledger.
Every new registration requires a decision explicitly naming its full contract;
an old generic project authorization cannot be passed as that decision. This
does not create a new user-approval requirement when existing authorization
already covers the exact decision. Bytes and claimed owner labels do not prove
that the decision was legitimately authorized.

## Intent, return and reuse

`begin_round` atomically records one frozen artifact and one original attempt
for every required reviewer role. All declared subscopes belong to the same
round; they may return independently. At most one unresolved round per canonical
scope is allowed. All required roles must have a complete original result before
that round counts as complete. A complete negative verdict still consumes the
round. The maximum is three completed rounds; there is no fourth-round override.

`settle` records either `COMPLETE` with `PASS`, `NEEDS_CHANGES` or `FAIL`, or
`INCOMPLETE` with a null verdict. Its evidence list points at original round/NLM
outputs or receipts. Complete-result insertion is unique for frozen input
SHA/bytes + canonical scope + reviewer role + standard SHA/bytes. A fresh request
for an already completed key returns `REUSE` with the original verdict after
rehashing its evidence, without a new round or attempt. The returned
`newly_recorded` flag concerns this ledger only: it never proves body reading,
native acceptance, final manuscript adoption or scientific PASS.

`mark_unknown` retains the original pending attempt, its diagnostic evidence and
monotonic cumulative usage. Unknown/PENDING never allows another round or a
retry. Read the original attempt first; settling UNKNOWN requires an additional
full-pin `reconciliation` JSON reference with schema `review-attempt-readback-v1`,
`project_id`, original `round_id`, original `attempt_id`, `outcome` and `verdict`.
All six fields must match the original attempt and proposed settlement; unrelated
files or declarations about another attempt/outcome are refused. This checks
the declared binding and bytes, not whether the original runtime was actually
read or its observation proves the claimed outcome. The caller retains the
original runtime and acceptance responsibility.

An INCOMPLETE result is final for that attempt and does not count as a completed
review. A retry is allowed only with a separate existing owner decision binding
the original attempt and proposed new attempt. It stays within the same round,
artifact and role, retains previous failure/usage, and respects the registered
finite attempt ceiling. Already complete roles are never repeated. An unresolved
attempt or exhausted attempt budget leaves the scope unresolved; it is not a
license to switch routes, create new IDs, waive NLM checks or fabricate closure.

Usage is a caller-supplied cumulative nonnegative integer map per attempt
(for example `tokens`, `cost_microunits`). Known values cannot decrease; previous
observations remain in the event journal. This is accounting of supplied evidence,
not an independent billing meter.

## Persistence and recovery

All mutations, their request IDs and result events share one SQLite
`BEGIN IMMEDIATE` transaction with `synchronous=FULL`. Competing writers serialize;
lock acquisition is bounded and returns `LEDGER_BUSY_OR_UNAVAILABLE`. An event
write failure rolls back the intent and attempts as well. An interrupted SQLite
writer rolls back through SQLite's journal; the next process opens the existing
project instead of creating another ledger. There is no daemon, polling loop,
TTL-based takeover, lease deletion or automatic retry.

Every `apply` JSON has a unique `operation_id`. Replaying exactly that operation
returns its original recorded response; using the same ID for different input
fails. Such replay is historical recovery, not fresh reference verification.
A separate `status` is a consistent current-state read. Keep intent, unknown
observations, incomplete results and completed rounds distinct.

Input artifact/standard/evidence files are full-byte hashed and checked for stable
metadata; per-file maximum is 512 MiB, with at most 32 evidence references. Owner
decision/readback/request JSON is at most 1 MiB. These checks prevent accidental stale
references; they do not create signatures, native role authority or semantic
acceptance. Complete settlement rechecks frozen artifact and standard. Files
must remain immutable; revisions use new paths and preserve prior evidence.

## Small explicit API and CLI

Python API: `Ledger.create(path, project_id=..., owner=..., authorization=...,
new_tasks_only=True)`, `Ledger(path, project_id=...)`, `apply(request)`,
`status(scope_id)`, `close()`.

All commands require an explicit database and project. JSON is supplied in an
absolute request file. Example with a newly authorized test project:

```text
python3 -B scripts/collaboration_review_budget.py --db /absolute/new-project.sqlite3 --project-id new-project create --request /absolute/create.json
python3 -B scripts/collaboration_review_budget.py --db /absolute/new-project.sqlite3 --project-id new-project apply --request /absolute/action.json
python3 -B scripts/collaboration_review_budget.py --db /absolute/new-project.sqlite3 --project-id new-project status --scope-id chapter
```

Create request: `owner`, `authorization` reference and `new_tasks_only: true`.
Every action includes `operation_id` plus one of these `action` values:

| Action | Additional fields |
| --- | --- |
| `register_scope` | `scope_id`, `issue_lineage`, `owner`, `authorization`, `standard`, `required_reviews`, `max_attempts_per_review` |
| `alias_scope` | canonical `scope_id`, `issue_lineage`, `alias`, `authorized_by`, `authorization` |
| `begin_round` | `scope_id`, unique `round_id`, `artifact`, `attempts` |
| `mark_unknown` | `attempt_id`, `reason`, `evidence`, `usage` |
| `settle` | `attempt_id`, `outcome`, `verdict`, `evidence`, `usage`; `reconciliation` required after UNKNOWN |
| `retry` | original `attempt_id`, `new_attempt_id`, `actor`, `authorized_by`, `authorization` |

A reference has exactly `path`, `bytes`, `sha256`. A required review has
`reviewer_role` and `review_scope`. A begin attempt has `attempt_id`,
`reviewer_role`, and an `actor` containing owner/model/route labels. These actor
labels are audit context, never budget keys or native identity evidence.

A scope/alias/retry decision is JSON with schema
`review-owner-decision-v1`, `project_id`, original `owner`, the matching `action`,
and `binding`. Registration binding contains the full registration request except
`authorization`. Alias binding contains canonical `scope_id`, `issue_lineage`,
`alias`. Retry binding contains canonical `scope_id`, `issue_lineage`,
`attempt_id`, `new_attempt_id`. The tests contain complete synthetic examples.

No success output from this tool asserts authority, dispatch permission or domain
acceptance. Those three fields remain false. It is a candidate accounting helper,
not an installed controller or proof that any live chapter team adopted it.
