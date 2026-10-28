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

Character-unit indentation follows Word's
[MS-OI29500 2.1.44 implementation note](https://learn.microsoft.com/en-us/openspecs/office_standards/ms-oi29500/138732dc-507a-4164-af66-808c2fd9f2f9):
a zero `startChars`, `endChars`, `firstLineChars` or `hangingChars` clears
earlier values of that attribute and leaves the applicable length indent in
effect. An inherited nonzero character indent still overrides a later length
value; changing only that overridden length can be a valid serialization.
The note does not establish the zero behavior of legacy `leftChars`/`rightChars`.
Their coexistence with nonzero legacy lengths remains conservatively compared;
it is not treated as equivalent to deleting those zero aliases. Pure zero
defaults and nonzero-character serialization equivalences remain supported.

[Custom tab stops are additive through the style hierarchy](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.wordprocessing.tabs?view=openxml-3.0.1).
The comparison accumulates positions, applies later definitions at the same
position, and lets `clear` remove only its position. Empty tabs retain inherited
stops; a materialized equivalent set and numeric/order serialization compare
equally. Conflicting duplicate positions in one layer, unknown tab content and
Word positions outside its documented -31680 to 31680 twip range refuse rather
than losing properties. Hanging indentation remains independently protected,
including its implicit tab-stop effect.

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
