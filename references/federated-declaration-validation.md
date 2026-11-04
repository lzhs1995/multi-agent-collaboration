# Read-only federation declaration validation

This checker supplements the existing shared contract and
[nested research coordination](nested-research-coordination.md). The two skill
entries share those workflow rules. It adds no sender, runtime,
participant authority, registration writer, observer or application lease guard.

From the complete source:

```bash
python3 -B scripts/federated_collaboration.py /absolute/federation.json
python3 -B scripts/federated_collaboration.py examples/federated-research/four-chapters.json
```

The [schema](../schemas/federated-collaboration.schema.json) defines the manifest
and referenced object shapes. The [four-chapter example](../examples/federated-research/four-chapters.json)
is synthetic, not a live registration. N chapters are supported. Chapter
workspaces and Herdr services must be exclusive; an existing dedicated senior
terminal need not acquire a fifth workspace. Pure cmux teams continue using their
original profile and role map; this checker covers the nested chapter topology.

Actors use aliases and references into existing binding evidence, not a parallel
identity registry. `binding_ref`, `mode_ref`, `budget_ref` and optional
`registration_ref` contain `path`, full-file `sha256`, and JSON `pointer`. Empty
pointer selects the entire object; relative paths resolve beside the manifest.
Inputs are bounded local JSON, never executable commands. Existing binding
objects retain their extra original evidence. Required fields are:

```text
agent / native_session_id / terminal_id
outer_cmux: workspace_uuid / pane_uuid / surface_uuid
endpoint: transport / host_identity
Herdr only: pane_id; endpoint.session_selector / endpoint.socket
```

All chapter actors must match the chapter workspace/service. Service uniqueness
uses host/socket and host/selector; native identity uses agent namespace/session;
terminal uniqueness uses host/terminal. Role labels may be identical. Outer
surface UUIDs may be shared by inner actors, but are never identity keys.
Chapter roots belong to their declared supervisor; the manuscript root and
cross-team work belong to the senior. Children remain inside their chapter tree.

`coordination_scope=chapter|federation` governs ownership. Independently,
`delivery_transport=herdr|cmux|original` chooses the last hop or preserves a legacy
controller. Senior-to-inner-supervisor work has federation scope and may use
Herdr delivery. Receiver location cannot erase cross-team coordination. Routes
must name task participants and bind the actual receiver's declared transport.
One task owns a nonempty `routes` array; each edge names sender and recipient.
The same task and budget can cover senior ↔ all chapter supervisors and chapter
supervisor ↔ executors, with Herdr and cmux last hops mixed per receiver. Duplicate
directed edges are rejected. Full all-to-all communication is never required.

Modes reference one of `peer`, `host_dispatch`, `hierarchical`, `closed_loop` plus
its revision. Budgets reference `acceptance_scope_id`, `issue_lineage_id`,
`max_rounds`, `rounds_used`, `time_limit_seconds` and `token_limit`. Maximum rounds
are one through three, including initial review. Every occurrence of the same
acceptance scope or correction lineage must share one pinned budget reference.
This preserves the stricter scope-wide budget across reviewers/routes as well as
the same unresolved correction chain across renamed artifacts/mode changes.
Required scientific/document checks still apply; a budget cannot waive a gate
or create a fourth round. Unresolved scope remains unaccepted for owner decision.
Existing registered tasks retain their original contracts; only their transport
and controller are checked here, with the supplied budget acting as a referenced
federation review declaration, not an overwrite of old task policy.

The checker detects forks within this manifest and range violations. It cannot
prove historical usage was honestly recorded or a removed registration was never
present. Cross-version history, original authorization, actual enrollment and
the task owner's decisions remain separate evidence. Do not omit an existing
registration to claim an old task is new. `registration_ref` pins the original
task ID and exact controller. A legacy registration requires `original` delivery;
profile-v1 registrations retain their profile and reject conflicting routes.

`declaration_consistent=true` means only internal declaration consistency and
matching referenced bytes. Results leave live identity, enrollment, sending,
acceptance and resource exclusion false. No files are modified. Use `python -B`
for imports too: a module cannot prevent its own initial import cache from inside
its body. Named local service live checks remain the separate
`collaboration_profile.py check-herdr --session` capability; remote `--machine`
inspection and atomic check-and-send are not implemented here.

Global Word/Zotero/NLM exclusion remains a separate implementation dependency.
Use the existing [resource broker](../scripts/resource_broker.py) and
[shared resource contract](availability-and-resources.md#shared-resources): its
global SQLite queue, process-group ownership and real OS flock checks must share
the same root/account key and pinned application/transport locks across chapters.
Do not create another per-team lock regime. This checker does not inspect that
broker or prove that every actual application write path uses it. Holder native
identity, resource, generation, allowed operations,
input revision, expiry/state and real MCP/API/application write-entry checks are
needed. Expiry does not stop an operation or release ownership. A valid manifest
cannot certify those guarantees or authorize parallel application writes.
