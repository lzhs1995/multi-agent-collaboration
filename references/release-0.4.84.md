# 0.4.84 — declared PAGE proofing metadata

Word can add `w:noProof` to the generated numeric result of a running-story PAGE
field. The semantic comparison records that difference on each declared result
and compares the remaining effective run properties exactly. The same rule is
applied when constructing the expected and actual projections.

Only a declared PAGE result receives this treatment. Other boolean properties,
fonts, size, color, language, separator formatting, surrounding text and field
boundaries retain their existing checks. A digit outside an inserted field can
survive rejection; it remains a content difference and is not discarded by this
exception. Historical table grids do not establish the live grid dimensions or
prove that a live width change is harmless.

The ordinary zero-input recovery table includes the byte-identical protected
driver in complete releases 0.4.83 and 0.4.84. It continues to reject unreviewed
future versions, changed drivers and any old input or unknown history. Legacy
AX and inventory fixtures now model their original distinct revision drivers,
so they do not select the newer disk-failure profile accidentally.

The four changed test modules passed 140 checks in the original invocation.
The earlier fixture failure and 0.4.83 CI failure remain retained. This release
does not replay Word operations, settle a restricted job or release an ordinary
lease. Current document acceptance and resource disposition need their original
actor and actual evidence.
