# Source-bound Word endpoint semantics

An explicitly approved **new** owned initial Word job can add this exact
optional object:

```json
{
  "tracked_validation": {
    "mode": "DUAL_VIEW_SEMANTIC_V1",
    "page_cache_fields": {
      "accepted": [
        {
          "story": "word/footer2.xml",
          "ordinal": 0,
          "instruction_sha256": "8a7ec6820565d7b1ae43fc384b3f497291bbc6090871629370bd43609f26f2cd"
        }
      ],
      "rejected": []
    }
  }
}
```

The ordinal counts complex field beginnings in the selected projected story.
A declaration applies only to a referenced header/footer and the exact original
instruction bytes. Each view allows at most 32 unique declarations. Empty
declaration lists allow no cache change. The same object must be reviewed and
pinned inside the job; a new source mode does not migrate an old ticket.

Before creating a job, the original maintainer must perform a read-only
source preflight on its complete frozen tracked, accepted and rejected inputs.
The child repeats the comparison before loading or querying native applications.
After Word saves, closes, reopens and recloses the tracked copy, both projected
views must still match. Only then can native accept/reject begin. Each clean
output must contain zero revisions and match its corresponding frozen source.

The implementation protects complete stories, table grids, widths, row/cell
properties and merges, effective paragraph/run formatting, used numbering and
table-style dependencies, field instructions/results, note references and
payloads, relationships, settings, custom properties, custom XML and media. Other
opaque package payloads stay protected; content-type/root-relationship indexes
and ordinary core/application metadata are outside endpoint semantics.
Supported text, property and row
revisions are projected without serialization writes. Unsupported paragraph-mark,
paired move-range, cell and nested property revisions fail closed.

Boolean aliases and proved inherited defaults are normalized. Snap-to-grid is
enabled by default in both paragraph and character formatting; the paragraph
defaults also enable widow control, right-indent adjustment, word wrapping and
Latin/number spacing with East Asian text. An unproved overflowPunct default
remains unspecified, distinct from an explicit false value. Ordinary characters
can ignore inactive bCs only when no complex-script, rtl, bidi or cs-font-hint
setting forces complex rendering. Unrecognized scripts keep that comparison.

Layout/field settings and webSettings are retained. Revision-session ids, proof
state, zoom, document ids and the save template's tracking/markup controls are
separate from endpoint semantics. The driver independently requires tracking
enabled only in the tracked output and markup hidden after each native roundtrip.
Raw before/after revision headers are saved even when their serialization differs.
The original exact-header gate remains mandatory when this mode is absent.

Only a declared complex PAGE cache can be empty in a source or intermediate view
or consist of 1–12 canonical ASCII decimal digits starting with 1–9. A final
accepted native output must have a nonempty cache. The instruction, field lock,
run formatting and surrounding text remain protected; nested fields, extra
content and code changes refuse the exemption. Other PAGE fields and all Zotero
instructions/results remain exact. This comparison does not establish that the
number is the correct page; actual Word/PDF evidence remains required.

A passing source contract is not a native operation, citation Refresh, ordinary
Word/Zotero release or scientific acceptance. Preserve original attempts,
controllers, UNKNOWN evidence, foreign documents and queues. The 22GiB Word
floor and the one-invocation native child remain unchanged.

Local validation covers endpoint mutations and actual driver entrypoints in
private mocks. A changed source refuses before native loading; changed tracked
geometry refuses before accept/reject; changed clean semantics, revision residue
or tracking/markup state refuse before publication. Real fixed CMA source views
and the original failed native file are separate read-only controls. Fixtures
and API comparisons do not replace original peer review, CI, installation or
real native rendering.

Normative default descriptions are reproduced by the Open XML SDK:
[SnapToGrid](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.wordprocessing.snaptogrid),
[AdjustRightIndent](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.wordprocessing.adjustrightindent),
[WordWrap](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.wordprocessing.wordwrap),
[AutoSpaceDE](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.wordprocessing.autospacede),
[AutoSpaceDN](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.wordprocessing.autospacedn).
