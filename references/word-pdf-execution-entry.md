# Explicit Word PDF entry for new tasks

This is an opt-in entry for the Word-specific execution contract described in
[resource execution](resource-execution.md). It is separate from the installed
legacy `wordrev` commands. Running a new script does not migrate an old task,
unlock a pending operation or select an executor for the user.

Use `scripts/word_pdf_execution_cli.py` from the exact complete source bound in
the contract. It performs one step only. Every step takes the absolute contract
path and its full SHA256. There is no default resource root, automatic resource
configuration, combined run/release action, retry or pending acknowledgement.

| Step | Additional required inputs | Effect |
| --- | --- | --- |
| `preflight` | None | Check the exact contract and actual native caller without constructing a broker or opening its database. |
| `request` | `--root` | Request the one original operation in the existing configured queue. |
| `grant` | `--root`, `--ticket` | Apply the original FIFO and grant conditions; `--ttl` defaults to 600 seconds. |
| `run` | `--root`, `--lease`, `--lease-sha256` | Run this exact original lease once through the Word-specific driver. |
| `release` | `--root`, pinned lease, `--proof`, `--proof-sha256` | Check the typed result and original global Word/Zotero release conditions. |

For example, after binding concrete existing files and the chosen immutable
source, the preflight command has this form:

```sh
python3 -B /absolute/pinned/source/scripts/word_pdf_execution_cli.py preflight \
  --contract /absolute/task/word-contract.json \
  --contract-sha256 FULL_64_CHARACTER_SHA256
```

The paths and digest above are placeholders, not a prepared job or permission to
use an existing ticket. The caller must preserve each original JSON result as
an immutable task artifact before using it as the next step's pinned input.
This S40 execution type requires at least 40 GiB of actual free space; a plan
may raise that floor, and cannot lower it.
A failed preflight does not construct `Broker`; later steps open only the
explicit existing `queue.sqlite3` and can initialize its supported metadata.
They are not read-only inspection commands.

The lease file is the original grant result, including `id`, `token` and
`execution`. The CLI compares its contract digest, generation, operation,
request revision and attempt with the stored binding before calling the driver.
The driver rechecks its own authoritative state. A caller cannot turn an
arbitrary lease JSON into a valid grant. A successful run never automatically
calls release.

Retain `UNKNOWN`, failed stage records and pending evidence. The first Word
implementation supports inspecting the original stage ledger through an
already-open broker; it does not provide automatic reconciliation or replay of
an uncertain Word operation. `--reconcile` only passes the explicit flag to the
typed release validator and does not override that restriction. Do not call a
generic run/release API to get around a typed refusal.

The fixed service still performs Python staging, publication and cleanup within
the same guarded operation. Its `success=true` alone is insufficient: a PDF can
already exist when closing the owned document fails. The typed result requires
the complete ordered stage evidence, unchanged input, successful owned cleanup,
absence of pending state and drained process groups. Global Word/Zotero release
conditions remain separate. This entry covers neither other Word commands nor
direct Zotero, MCP or AppleScript paths.
