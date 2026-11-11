# Nested chapter and whole-document coordination

For new research tasks, cmux can coordinate independent outer workspaces while
Herdr coordinates the agents inside each workspace. Keep both skills: MAC covers
outer coordination and pure cmux tasks; the
[Herdr skill](../skills/herdr-collaboration/SKILL.md) covers inner teams. Both use
the [shared contract](collaboration-contract-core.md), maintained in one place.
Herdr runtime capabilities do not replace task scope, authorship or acceptance.

## Four-chapter layout

Give each of four core chapters its own cmux workspace and one Herdr team inside
that workspace. Each team has a chapter supervisor and only the executors or
reviewers justified by the work. A senior supervisor receives the four chapter
supervisors' outputs and integrates the whole dissertation: terminology,
references, cross-references, shared assumptions and final acceptance.

Chapter supervisors own chapter integration; they send bounded deltas, evidence
references and unresolved questions to the senior supervisor. Cross-chapter
agreements return through those supervisors. Shared Word, Zotero and other leased
resources retain one owner across all teams. Four teams do not grant four writers
access to the same manuscript or account.

This chapter tree is the task's explicit design. It is not a claim that an
external framework's leaders/workers grouping implements this hierarchy.

## Bind both address layers

Record outer cmux workspace and surface identities, then inner Herdr server
selector, terminal, pane and native agent session identity for each recipient.
Names such as `w1:p1` are not globally unique. Resolve the actual recipient and
preserve the explicit server selector on supported Herdr commands; detect stale
bindings before sending. A terminal address alone is not a session identity.

Never paste into an outer cmux surface hosting Herdr and assume the intended
inner agent will receive it. Use the supported inner route, or a separately
verified direct native-session route. Outer coordination carries the chapter
task/evidence references; the inner route delivers to the bound chapter supervisor.
If only a file handoff is available, record it as such: saving a file is not
waking the receiver, confirmed reception or acceptance.

## Workflow modes are metadata

Select a mode in the new task's written plan, alongside participants, ownership,
deliverables and stopping conditions. These names describe the intended workflow;
they are not additional `collaboration-profile-v1` values, enforced configuration
schema or an implemented automatic scheduler.

| Mode | Appropriate use | Boundary |
| --- | --- | --- |
| `star` | Default within a chapter: its supervisor assigns bounded work and integrates results. | Executors need not wait for unrelated peers. |
| `hierarchical` | Default across the book: chapter supervisors report to the senior supervisor. | Each level has explicit integration responsibility. |
| `mesh` | A named, limited disagreement requiring direct discussion among relevant peers. | Fix the question and participants; end when resolved or explicitly left unresolved. |
| `ring` | Sequential passes with a useful ordering, such as language then references then format. | Specify the last reviewer and return point; no perpetual circulation. |

Change modes only for a useful task reason, preserving evidence and ownership.
An ordered review cycle may be draft → review → necessary correction → acceptance,
within the common contract's total round limit; this does not start an automatic loop.
Message-hop, scheduler-step, time, token and failure budgets bound transport or
execution; they are separate from the common contract's review/correction limit.
A ring's next edge does not itself enforce content review rounds. No mode grants
automatic merging, process termination, reauthentication or new account authority.

## Apply the common contract without extra ceremonies

Use its fingerprinted handoffs, factual progress/checkpoints, authorized two-way
queued communication, bounded review and intent/result records at both layers.
Keep native reception, complete body reading and acceptance separate. Do not
duplicate these rules into a second protocol or add acknowledgement loops to
bridge the layers. The senior supervisor accepts the integrated result, not just
four successful transport receipts.

This layout supports new assignments. Pure cmux remains valid. Existing tasks
keep their registered controller, original attempts, review contract and evidence;
moving a UI into Herdr does not migrate them. Do not reset an old controller or
replay an old send while adopting this layout for new work.
