Fixtures preserve measured footer shapes. Task payloads, paths and unrelated transcript content are replaced with synthetic examples; these are regression fixtures, not live delivery proof.

`codex-busy-*-20261008.txt` are one busy Codex composer captured twice during a
single paste (surface:42, 2026-10-08): `-paste-partial-` is the mid-render state,
`-wordwrap-full-` the completed render, `-payload-` the pasted text. They pin two
measured renderer properties for `scripts/test_codex_busy_settle_steer.py`: a long
paste arrives in row batches, and a soft word-wrap boundary consumes exactly one
payload space (row 5), never more. Idle and consumed screens in that test are
composed from these rows and are not separately measured.
