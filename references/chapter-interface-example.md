# Chapter interface example: preserve different research quantities

This is a definition-boundary subset from the accepted dissertation's chapters
5 and 6, consumed on 2026-11-04. It omits the research-question/hypothesis mapping
and complete table/figure locations, so it is not a complete chapter delivery
interface or chapter/scientific acceptance. ROOT assembled the boundaries and
consumed an internal collaborator's code evidence. The original Claude reviewer
then applied the e901 domain rules and returned six artifact corrections in
round 1. ROOT prepared a separate frozen correction; the same reviewer closed
all six items in round 2, and ROOT accepted it as `ACCEPT_CORRECTED_SUBSET`
in the v14 return. Reuse this accepted subset;
this documentation update starts no review, fit, manuscript edit or NLM request.
It does not establish
two live Herdr chapter teams, bidirectional native delivery or any of the original 33 business
cases / 15 pairs. Their reported counts remain 0/33 and 0/15.

## The interface that must survive integration

The accepted source is `博士论文_八章清洁稿_20261101.docx`, 18,005,318 bytes,
SHA256 `84f1929d02f2b44d467f2dfa158a17708d509e711788759937ac67da2ed2cbd8`.
Paragraph locators below mean `word/document.xml / w:body / direct w:p`, counted
from 1; they exclude table-cell paragraphs. They are not rendered page numbers.

| Handoff field | Chapter 5 | Chapter 6 | Senior supervisor's integration rule |
| --- | --- | --- | --- |
| Research quantity | Dual-lineage relative distribution/shares (p1128) | Single-lineage support levels and changes over time (p1432) | A larger share does not establish a higher level on that side. Keep both quantities and their denominators explicit. |
| Continuous resources | Own share of the couple's total; named ratio predictors are centered by subtracting 0.5 | Current/lagged continuous variables and squared terms enter FEIS | Separate centering, categorical boundaries and a hypothesized behavioral threshold. |
| Categorical definition | Strictly greater than the spouse; ties give both spouses 0, so the indicators are not complementary (p1164) | Below 25%; at least 75%; the middle interval is [25%,75%) (p1485) | Do not replace either definition with the other. Chapter 6's balance category is an interval, not equality at 50%. |
| Interpretation | 0.5 is a prespecified boundary, not an estimated behavioral threshold (p1164) | Threshold selection considered significance; categorical associations retain exploratory status (p1486) | Do not report an identified power-transition mechanism or a common empirically estimated cutoff. |
| Counting units | 9,121 couple-year records, 4,092 household IDs and 4,073 couple pairs are separately supported by the expanded p1163 quotation (`C5-UNIT`) | Parent-child relations, adult children and relation-wave records are distinct (`C6-UNIT`, p1474) | Do not merge these counts into one sample N or infer independent observations from pair identifiers. |
| Time comparison | Retain the chapter's own estimand | Numerical changes in event-time associations do not establish significant differences between time points or prove long-term accumulation, permanent harm or recovery (`C6-TIME`, p1488) | Keep the full p1488 boundary; do not replace it with an unsupported generic statement about cumulative-effect estimands. |

The corrected `C6-UNIT.provenance_limit` separately records p1474's statement
that the saved 3,788-relation sample's generation chain is not fully recovered
and cannot be assumed to be a uniform deletion from the preceding sample. This
is a manuscript-stated source limitation, not the unit-definition quote or a
verified code-generation fact. `C6-EXPLORATORY` now cites the full p1486 passage,
including the significance-informed threshold choice stated as the reason for
retaining exploratory status. The corrected report also includes
p1488 in its locator list.

## What the code evidence establishes

Use the named source keys and model/table keys in the handoff, rather than a
general chapter PASS. ROOT's completed integration records 10 exact manuscript
quote checks and 13 exact source ranges. The code result remains
`PARTIAL_STATIC_CODE_AND_EXISTING_PROVENANCE`:

- `ch5_r` is `20250713 Mod_mediation.R`, SHA256
  `ac6b4bf14279484e7851c7d66a60494babfde77b5050b8ec8fb5d17d30ec5d48`.
  Lines 77–79 and 135–141 support centering the named continuous predictors.
  Line 52 imports already constructed variables; the inspected script does not
  establish the upstream strict-comparison/tie generation. Lines 632–650 show
  distinct `ecof` Gaussian and `econf` logit channels, not all outcome units or
  historical model adoption. Preserve the `opf` / `op5` naming difference until
  the relevant saved model/table key resolves it; do not silently rename it.
- `ch6_r` is `20250602 FEIS_part1_renew.R`, SHA256
  `f090580de3246cae0d73edbbd5678093d78aedfdfd62963a80a572434cf46139`.
  Lines 65–86 consume continuous, `inccb3/hwb3` and `inccb4/hwb4` channels.
  Their names and factor use do not prove upstream 25/75 construction, endpoint
  equality or missing-value handling. The manuscript's explicit boundaries stay
  in the interface while implementation provenance stays partial. Lines 89–92
  and 476–491 do not establish all imported outcome units or ranges.
- `ch6_r:33–34` assigns two input files to the same `mwp.df`; sequential execution
  would overwrite the first. That is not evidence of which lines were executed
  for each historical model. Table 6-13 has three saved-frame matches to the
  3,788 candidate and nine unbound inputs. Even the three matches are not runtime
  input logs. Preserve these exact keys:
  `op1_/direction/lag1/rhs.incre.int/aage2`,
  `op1_/direction/lag1/rhs.above.int/ppage`, and
  `op1_/direction/lag1/rhs.below.int/apage`.
- Chapter 5's archive hashes, 24 parsed OUT samples and summary-CSV reconstruction
  establish archive/read evidence. Chapter 6's 480 passing historical HTML cells
  establish replay; the source labels it `EXISTING_HTML_REPLAY_NOT_MODEL_REFIT`.
  None fills the missing original estimation/input-adoption chain. Keep the
  original mediation/moderation branches and adopted outputs distinct.

## A bounded supervisor handoff

A chapter supervisor returns the source pin, locator, interface ID, variable or
object key, accepted definition, evidence status and remaining source dependency.
For example, `C5-BINARY` returns the strict/tie definition with upstream generation
unverified; `C6-BINARY`, `C6-BALANCE` and `C6-EXPLORATORY` return the asymmetric
25/75 boundaries, interval and exploratory interpretation with the same kind of
source gap. These are separate interface records, not aliases of one variable.

ROOT's earlier `INTERFACE_ARTIFACT_ACCEPTED_WITH_SOURCE_GAPS` is retained as
history. The first original-reviewer verdict was `ACCEPT_WITH_CORRECTIONS`,
consumed by ROOT as `ACCEPT_WITH_SIX_LOCAL_ARTIFACT_CORRECTIONS`; neither is a
final unqualified PASS. The same reviewer closed 6/6 corrections in round 2,
and ROOT consumed the result and recorded `ACCEPT_CORRECTED_SUBSET`. The
`two-chapter-interface-artifact-20261104` scope has used two of its maximum three
rounds, retaining the first round. No further review is required
for this unchanged subset. Quotations must support the full claim; the initial
six findings and their corrections remain part of the record. Preserve both
definitions and their interpretation limits. If a later authorized writing change depends on
the missing generation evidence, ask the original material owner for that exact
upstream code and saved model/table binding. Do not refit or reopen whole chapters
merely to consume this example. Align terminology and evidence fields without
unifying different research questions.

The existing acceptance retains 19 scientific conditions, 7 HOLD groups,
77 protected files, 3 translation holds and 40 previously used NLM queries.
The strict two-pass acceptance on the same final PDF remains unmet. This handoff
does not reset those conditions, review history or original controllers, including
the separate .84 / ROOT j6 boundaries.

## Evidence and the preserved correction

The local evidence package is
`/Users/lzhs/uz/state/B/recovery-20261024/r89-u22-held-verification-v1/SUCCESSOR-01a1a1e3-20261104-LIVE-v10/two-chapter-readonly/`.
Use `correction-round-2/CHAPTER-INTERFACES.json` and `correction-round-2/REPORT.md`
for the current six-point correction, with its `CORRECTION-RESPONSE.json` and
`MANIFEST.json`. Keep the original `REPORT.md`, `ROOT-INTEGRATION.json` and
`CHAPTER-INTERFACES.json` as frozen history. `BODY-EXCERPTS.json` and
`code-evidence/REPORT.md` remain the source evidence; full source paths and exact
ranges are in `code-evidence/SOURCES.json` and `EXACT-LINE-EXCERPTS.json`.

| Artifact | Bytes | SHA256 |
| --- | ---: | --- |
| Preserved v11 `MANIFEST.json` | 1557 | `2fd7b453fdf4fbca78b1a907bf6f73538b2cd2607f64bae16a44be2e959be408` |
| Preserved v11 `CHAPTER-INTERFACES.json` | 7380 | `679c0e248d7c068b5f486dbe14dcdd23c80a5d56be790121955120ddc2e03f73` |
| `REPORT.md` | 3021 | `748225dcd8f658bab1e0146eb58d400c83a660007aec154cf1aaf0c98daf1504` |
| `ROOT-INTEGRATION.json` | 3318 | `0e94e60cc9b15b2510eff428435f5185b55b6bc62296dde81a4707bd5db08d44` |
| `code-evidence/REPORT.md` | 8817 | `5085268eb6f229d767cb19c62c5135436227d972c799b80a49c0973afb4d7940` |
| `correction-round-2/MANIFEST.json` | 2114 | `b43faf72301f410af2a6fe6a958784a8b0b187e13b0e38c456f95ecca791c3b6` |
| `correction-round-2/CHAPTER-INTERFACES.json` | 9865 | `602ac96a59c72586f6d4587cf43e109cffc218423da232c9c45b500403550c5f` |
| `correction-round-2/REPORT.md` | 3615 | `668dca5933ceec7dabab6bbf6f7744972b8257178fe4b15c55f7423659dce106` |
| `correction-round-2/CORRECTION-RESPONSE.json` | 2672 | `24fec9b2a351a268cc5957698581b60fa7ec07f442ec3784f110ff3cdba6aa76` |
| `correction-round-2/ACCEPTANCE.json` | 1917 | `0c3cf68dc16f7c8b4976fc0ec707d49e41307d4c4b1f89d0c6e67f839dbed4ac` |

The original maintenance notice pinned a different manifest: 1485 bytes,
`a6e4d0aad78fab115f3da2960295679b79cd00f28e2e9898ad796d3071bfa676`.
It no longer matches the current path. Its exact preimage is retained at
`pre-domain-completion/MANIFEST.json`; the corresponding original interface is
6756 bytes, `02ae8b05f65bd3335552243a3d8586fc51fe4d2df829131b44c0750acf9fee63`,
retained at `pre-domain-completion/CHAPTER-INTERFACES.json`.

The earlier v11 correction replaced only the interface's stale code-evidence PENDING value
with the completed pinned report/integration and updates its manifest reference
and timestamp. REPORT, ROOT integration and code evidence are unchanged. Preserve
both notices under
`/Users/lzhs/Documents/cnm/tasks/06_Mintransition/verification/ccc-successor-claude-20261010/maintenance-inbox/`:
`root-01a1a1e3-observer-and-chapter-evidence-20261104-v11.json` and
`root-01a1a1e3-chapter-manifest-completion-correction-20261104-v11.json`.
Do not silently treat the old pin as matching the new bytes.

The later `root-01a1a1e3-domain-corrections-toolchain-20261104-v13.json` notice
introduces the separately versioned six-point correction above. Its domain
evidence is in the sibling `domain-adoption-live/` directory:
`CLAUDE-RESULT.json` is 6952 bytes, SHA256
`a139324e8fc022add505a69ceae66b2dd1dd162269fea772568ebb8151d2d31c`, and
`ROOT-CONSUMPTION.json` is 1431 bytes, SHA256
`6b427511353fedcbab9018c83905220407a9a57d47ef4eb3a7cd11c0fd2289a0`.
ROOT's `continuation-1/FIVE-READS-VERIFIED.json` and pinned
`continuation-2/VERIFICATION.json` record the original main session's five manual
Reads and successful producer/tool-result association. The reviewer applied the
e901 definition, partial-status and bounded-review rules to identify the six
corrections; that proves scoped manual application, not automatic hook loading
or reverse native delivery. In round 1 the reviewer did not independently read
the mother manuscript, BODY excerpts or original R programs. Retain that history.

The v14 `root-01a1a1e3-corrected-subset-accepted-word-input-20261104-v14.json`
notice supplies the later acceptance. Under the sibling
`domain-adoption-live/correction-round-2/`, `CLAUDE-RESULT.json` is 5780 bytes,
SHA256 `a947a860274b6f9a13a50487844643b994d92cee9bde18e476a2ee759c1e13c6`,
and `ROOT-CONSUMPTION.json` is 4192 bytes,
SHA256 `e15c7c62d0b2e397d073ddd9e21390333aba1774f46fd7448276f84366644387`.
Together with the pinned subset `ACCEPTANCE.json` above, these record 6/6 closed,
no unresolved correction in this scope, and zero original business/pair increment.
The frozen correction inputs still describe their pre-review state; acceptance
is a separate record, not an overwrite of those inputs or v1 findings.

Round 2 additionally checked 11/11 quotations against the pinned DOCX's direct
body paragraphs using `w:t` text. It did not check deleted revision text or field
code effects, or recheck the original R programs. ROOT consumed this scoped
result; the scientific/source gaps remain unchanged. The original send timed out
before native receipt and was not resent; the later file return is not reverse
native delivery or evidence of causal efficiency gains. Template defaults and
shared runtime state remain outside this definition subset; transport evidence
and domain acceptance retain their separate boundaries.
