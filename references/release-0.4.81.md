# Release 0.4.81

The prospective causal observer now distinguishes a genuine pre-fence call's
late result from an unknown orphan using the exact original transcript prefix.
It hashes that prefix under the existing ten-second budget and retains at most
8 MiB of complete prior records, only when an in-window call is missing.

A unique same-session/version native call must match the late result's tool ID
and parent/source UUIDs. These prior-call witnesses remain separate from the
notice's full-body proof and selected canonical result batch. Missing, duplicate
or foreign selected results and a resealed batch subset still refuse. Unproved
or clipped prior calls remain unverified.

Eight added controls cover full fence-to-body consumption, ordered and inverted
append order, the unchanged sequential verdict, ambiguous/foreign prior calls,
whole-batch conflicts and bounded prior evidence. All 18 existing causal and
33 sequential controls retain their assertions. The public CLI also exercises
the pinned pre-fence path without changing input or controller files.

The old .80 publication and u24 evidence remain fixed. R204 establishes a real
record shape, not a new historical observer verdict. Installation, future u25
binding/launch and live efficiency require their original owner gates.

Only the existing exact ordinary-message driver SHA is registered for .81;
changed driver bytes and the next unreviewed version remain refused.
