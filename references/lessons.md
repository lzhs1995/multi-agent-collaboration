# Lessons Converted Into Controls

| Failure | Maintained rule | Regression surface |
| --- | --- | --- |
| Context was lost by restarting a peer | Reuse exact context-bearing session; never clear | test_cmux_agent_panel_guard.py |
| Screen DONE never reached supervisor | Report hash + nonce-bound active callback + Stop guard | test_r3_hardening.py |
| Prompt echoed its own ACK | Parse assistant blocks, not pasted challenge | test_mac_harness_receipts.py |
| Old surface ref addressed another panel | UUID/workspace binding, fresh identity | test_workspace_uuid_alignment.py |
| Idle placeholder blocked real work | Exact known virtual suggestions; unknown text remains occupied | test_r3_hardening.py |
| Callback delivered twice | Classify delivery; never blind-resend | test_r3_hardening.py |
| Compact stalled for 15 minutes | 60-second compact samples, two unchanged checks, bounded intervention | test_cmux_executor_sentinel.py |
| Billing errors caused endless retries | Unavailable/solo/handoff state machine | test_executor_availability.py |
| Guard existed but did not run | Both clients wired; benign execution and negative entrypoint tests | test_install.py, test_r3_hardening.py |
| File-edit prose was parsed as a shell launch | Check tool identity first; cover command and cmd payloads in real hook processes | test_cmux_agent_panel_guard.py |
| Later PASS hid earlier objections | Explicit resolution links; fresh artifact hashes | test_r3_hardening.py |
| Concurrent evidence heartbeat was lost | Atomic state and concurrency tests | test_heartbeat_concurrency.py |

## Probe Discipline

Use the producer's actual schema. Null, false, absent and zero are different
observations. A SHA of empty bytes is evidence of empty bytes, not proof of a
failed read: independently check the read outcome and byte count. Never reconstruct
hashes from prefixes. A generated report cannot contain timestamps produced only
after that immutable report was read; use a separate readback receipt.

Run positive and negative controls with measured branch/mutation hit counts.
If the harness itself fails, correct the probe and preserve the failed observation;
do not turn a zero-hit mutation or an exception into a product defect or a PASS.

## Avoid Process Inflation

Apply [bounded collaboration](bounded-collaboration.md): freeze the business
report, reconcile the original send without replay, and stop task-specific
callback diagnostics after verified receipt and marker disarm. That protocol is
an operator rule, not a claim that a new runtime hook has been installed. Track
accepted findings and coordination overhead separately; two occupied panels do
not establish a speedup.

Scope each phase to a concrete outcome. Keep operational cleanup, product fixes,
historical evidence and final admission separate. Do not reopen settled findings
without new evidence. A repeated unproductive review is not progress. Use the
formal consensus floor only when formal multi-agent consensus is the requested
workflow; do not invent peer review during a SOLO task.

The detailed incident archive predates this public packaging and contains local
observations. It is not normative for model choice, input ownership or present
provider capability. SKILL.md and the tested public defaults take precedence.
