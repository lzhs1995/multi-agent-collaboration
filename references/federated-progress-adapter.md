# Federation input for the existing progress observer

This adapter validates a [federation declaration](federated-declaration-validation.md)
and projects referenced actor bindings into the existing
[`collaboration_progress.py`](../scripts/collaboration_progress.py) observer. It
adds no scheduler, transport, participant authority or event state machine. The
[nested research contract](nested-research-coordination.md) remains the workflow
authority. Original business controllers, budgets and callbacks are unchanged.

```bash
/opt/homebrew/bin/python3.14 -B scripts/federated_progress_adapter.py prepare --manifest /absolute/federation.json --local-host explicit-local-host-id
/opt/homebrew/bin/python3.14 -B scripts/federated_progress_adapter.py snapshot --manifest /absolute/federation.json --local-host explicit-local-host-id --output /absolute/new-observation
/opt/homebrew/bin/python3.14 -B scripts/federated_progress_adapter.py watch --manifest /absolute/federation.json --local-host explicit-local-host-id --output /absolute/another-new-observation --duration-seconds 120
```

Use original binding evidence; the adapter never discovers or invents missing
process pins. Each eligible binding has the existing progress format:
`process: {pid, birth: [seconds, microseconds], executable: /absolute/path}` and
optionally the existing `checkpoint: {path, max_bytes, stale_after_seconds}`.
These are extra fields in the original referenced binding, not a new identity
database. Their original file SHA remains pinned. The explicit local host ID is
an operator selection, not host authentication. Other-host actors stay UNKNOWN;
remote machine forwarding is unsupported. Synthetic examples remain synthetic.

Every actor is retained in `actor-map.json`, including missing/invalid process
pins or unsupported host endpoints. Eligible actors receive observation aliases;
their real actor IDs, role labels and original business-task/controller links stay
in the map. The generated `new_tasks_only=true` manifest describes a new read-only
**observation assignment**, not a claim that business tasks are new, enrolled,
dispatched or migrated. Missing actors are not silently omitted from the coverage
report. If no actor is eligible, no runtime reader or watch starts. CLI status 2
means partial/unknown coverage; 1 means a declaration/configuration error.

All live reads, snapshot cadence, time budgets, interruption handling, output
locks and event deduplication remain in the existing observer. It uses the native
kernel process reader, then `check_herdr` with the same explicit session selector
and original endpoint/pane/terminal/native-session pins. The thin evidence wrapper
only records exact read outcomes/timestamps, including error codes otherwise
reduced to an exception class. It never sends or retries a read itself. Plain cmux
results prove only the pinned process handle; this adapter does not claim a fresh
native-session identity check for them.

The output contains:

- `progress-input.json`: standard original-observer input, pinned for that run.
- `actor-map.json`: all actors, original references, controllers and eligibility.
- `progress/`: original observer state, latest snapshot and `events.jsonl`.
- `native-read-evidence.json`: most recent actual read result/error per PID or
  native Herdr session; observation time is separate from progress/checkpoint time.
- `federation-snapshot.json`: final all-actor projection, with stale samples kept
  as original evidence and effective UNKNOWN. During a watch, consume the current
  original snapshot together with the static actor map; the adapter does not run
  a second projection timer. At normal exit monitoring coverage is UNKNOWN.

For a one-time view while watch is running, run this from the complete source
root. It reads the current original snapshot and retains all missing actors;
`federation-snapshot.json` is written only at exit, not every minute.

```bash
/opt/homebrew/bin/python3.14 -B - /absolute/another-new-observation <<'PY'
import json, sys, time
from pathlib import Path
sys.path.insert(0, 'scripts')
from federated_progress_adapter import project
root = Path(sys.argv[1])
read = lambda name: json.loads((root / name).read_text())
view = project(read('actor-map.json'), read('progress/snapshot.json'),
               read('progress/observer.json'), {}, now=time.time())
print(json.dumps(view, ensure_ascii=False, indent=2))
PY
```

Before the first snapshot exists, coverage is UNKNOWN; wait for the original
observer's first sample rather than treating the missing file as completion.
A recorded ACTIVE status alone does not prove the observer is still alive; its
original process pin and coverage deadline remain the monitoring checks.

Inputs and checkpoints must remain outside the output directory. A new output
directory is required. The adapter itself does not add a resume controller; the
underlying observer's original pinned input, deadlines and resume rules remain
available for explicit original-run recovery. A declaration revision does not
update the pinned observation assignment or move monitoring to a replacement.

`working`, `idle`, `blocked`, `done`, a missing process, or a checkpoint claiming
completion never accepts a business task. The adapter keeps acceptance/completion,
input and registration claims false. A read timeout or identity change is UNKNOWN;
it does not transfer ownership, stop a peer or release application leases.
An identity-matched Herdr response may still report runtime UNKNOWN: retain
`native_identity_verified=true` as the separate identity fact, keep actor coverage
UNKNOWN, and return partial-coverage exit code 2 even if all other actors are known.

The integrated original observer emits lifecycle events, deduplicated STOP for
pinned processes observed ABSENT/REPLACED, and identity-bound Herdr baseline/state
change events. Unchanged Herdr state does not repeat the event. The adapter adds
no second event state machine. These events describe observation, not acceptance.
Event files are not native delivery, supervisor body reading or wake-up evidence.
An unchanged state does not generate another STOP. No automated four-chapter
coverage or performance gain follows from a mock test or one live sample.
