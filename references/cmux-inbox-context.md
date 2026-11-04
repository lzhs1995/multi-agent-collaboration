# Optional observation of the native cmux inbox

`scripts/cmux_inbox_context.py` is an independent PostToolUse observer for
explicitly selected root sessions. It does not replace the original controller,
send or replay input, claim a message, mark it read, accept a task or block Stop.
Its context output is **exposure**, not a native user receipt or proof that a
referenced body has been read. Keep those later outcomes separate.

The cmux inbox list can include both incoming and outgoing messages. The observer
uses the actual native root identity and filters exact recipient workspace and
surface UUIDs before exposure. A child, an unverified identity, or a session not
selected by the pinned policy produces no output. It retains the existing
multi-rollout refusal; it does not solve same-process invocation authentication.

An explicit policy is a private, canonical JSON file with schema
`cmux-inbox-context-policy-v1` and an `actors` list. Each actor names
`session_id`, `workspace_uuid`, and `surface_uuid`. The command requires the
exact policy SHA256 and a private absolute `--state-dir`. Reading this reference
does not create policy, enroll an actor, install a hook, or prove a running client
has loaded it. Register only in an already authorized maintenance scope and pin
the complete immutable source; do not migrate in-flight attempts.

The observer reads at most 256 queued rows, exposes at most eight notices and
16 KiB per event, and bounds each body's exposure to 4096 UTF-8 bytes. Larger
bodies are explicitly omitted with their byte count and SHA. The snapshot can
omit older rows; it is not a complete inbox audit. It never executes body paths.
The private cursor records successful output flushes, not message consumption.
Unchanged notices are deduplicated; a failed flush leaves them available. No
timer, wake-up process, automatic resend or periodic reminder is installed.

Native cmux delivery, task file replies and this observer can coexist. If cmux
later delivers the original queued message, preserve that later native receipt
separately. Do not turn an earlier context exposure into an invented timestamp,
or use blanket `--mark-read` on a mixed sender/recipient query.
