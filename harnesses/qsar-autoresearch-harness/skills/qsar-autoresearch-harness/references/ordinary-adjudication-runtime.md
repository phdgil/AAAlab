# Ordinary-source adjudication engine

This is an isolated evidence engine for `npass`, `ice_oral`, `oecd`, and `toxric`.
It is not producer/guard/material/runtime activation or scientific acceptance.
HSDB retains its existing XML evidence and identity rules. SQL and HSDB do not
gain the bounded unknown-origin treatment through this engine or a shared enum.

`OrdinaryAdjudication(binding, entry, contract, registry, work_dir)` takes the
actual full-contract and registry **paths**, plus the selected registry entry.
It independently reads every physical native table row, including blank and
other-task rows, using the existing table adapter and native ObservationV2
builder. `work_dir` is reserved for coordinated staging; this isolated engine
keeps its scope index in memory and creates no temporary artifacts.

Call `resolve(untouched_observation)` exactly once per native row and then
`finish()`. Unknown, changed, repeated, omitted, or extra members reject. The
member key is the existing canonical hash of source ID, physical locator, and
`row`; no NPASS identifier or measurement is substituted for physical identity.
Existing HSDB and ordinary output identity functions are unchanged.

The returned ScientificResult has exactly the approved appendix fields. It
contains the validated route override, typed origin, separate origin/route
treatments, and remaining scientific issues. A result with remaining issues
is still blocking, even when another property has a validated hold. `finish()`
reports full-U accounting and separate counts, **not** scientific completion.
The integrating producer and guard must independently reconstruct results,
compare staged values exactly, apply the final scientific gate, emit zero
outputs for holds, and preserve eligible siblings exactly once.

## Evidence files and bindings

The binding has schema version 1 and kind `ordinary_source_adjudication_v1`.
`decisions`, `context_inventory`, and `bounded_reviews` are JSONL files (empty
files are permitted only when the corresponding scope is empty). Notices and
review receipts are JSON objects. Duplicate JSON keys, nonfinite values, extra
schema keys, boolean integer fields, duplicate identities, and unconsumed reviews
reject. Set arrays use the approved sorted identity convention; evidence/issue
arrays sort by their canonical content hashes. JSONL record order is immaterial.

All file descriptors use absolute paths and actual SHA-256 bytes. Context JSON
uses exact RFC6901 pointers, optionally followed by exact string offsets; UTF8
uses null pointer and exact offsets. Native pointers refer only to native
records in that observation. Absent mapped native fields are null; present
blank/null values retain their pointers. Extra native columns remain in the
untouched observation. Context never enters the native body. Primary attribution
requires a primary-role context; a native export cannot impersonate a primary
column. Recovered route requires explicit route evidence and a source crosswalk
identifying the native chemical. Hashes validate fidelity and bindings; actual
independent review must assess semantic candidate linkage and extraction fidelity.

The receipt follows the approved OrdinaryReceipt/OrdinaryReviewContext shape and
the existing local leader-attested review convention. Its actual-review object
must exactly equal the loaded receipt plus its file descriptor. Different
producer/reviewer IDs, nonempty bound native review evidence, and exact accepted
full-contract candidate bindings are mandatory. Identity strings are not
authentication, and synthetic test envelopes are not actual scientific reviews.

`source_release` is the selected contract source object's explicit `release`
string, or null when absent; no date inference occurs. The complete original
source configuration is hashed before external binding injection. The fixed
seven-file code closure comes from `ordinary_code_bindings()` and cannot be
caller-selected. Python, RDKit, and installed openpyxl versions are bound.
Context extraction code is an additional evidence dependency. All dependencies
are rehashed at finish; complete references are returned for downstream reuse.

## Bounded inventory conventions

Every ReviewPath uses a concrete `target`:

- A native record ID for each record in the observation.
- The absolute path of the selected native source file.
- Each context ID in the source-scoped context inventory.
- Absolute paths of applicable contract-configured identity support files.
- A concrete locator for any additionally identified retrieval/linkage path.

Applicable identity supports follow the existing join families: NPASS structure
support for native NPIDs, ChemID support for native CAS, and mapped CAS/DTXSID
support for matching native identifier families. All registry files remain bound
for freshness, but unrelated registry sources do not require per-candidate
review. Supports missing from the registry reject. Their paths need bound
support-context evidence, and duplicate physical support records remain in the
bound artifact rather than collapsing to a distinct-structure assertion.

Native paths require native evidence; context paths require evidence from that
context; support paths require a context whose artifact/content is that support.
These target bindings must occur in `reviewed_evidence` itself: `identified_by`
only locates a path and cannot substitute for review of its target. Current
context artifact and extracted-content paths, including canonical local path
aliases, are known accessible alongside context IDs, native IDs and registry
paths. They cannot be declared access failures, including in known-notice
reopening assessments. Unbound inaccessible retrieval locators remain eligible
for an evidenced bounded-access assessment; no primary-paper requirement is added.
Every accessible context must appear in reviewed evidence, including reviewed
scope exclusions. Out-of-scope paths need evidence and a scope rationale under
the independent receipt. Relevant accessible files cannot be relabeled failed
access. In-scope unattempted or retrieved-unread paths, feasible remaining
actions, and remaining findings prevent closure. Failed access needs dated typed
attempts with bound evidence and independently reviewed scope justification.
An origin review must address the specific origin question, with at least two
source-supported interpretations. There are no completion booleans.

Unknown origin and unavailable route use distinct clause/review/property
bindings. Conflict always stays unresolved. A source-native or accepted constant
route cannot be held as unavailable. An origin hold cannot hide a missing route,
and no hold clears separate extraction, support, join, identity, or dose issues.
Null origin adds no specific origin question: missing papers or identifiers do
not automatically create one.

## Known evidence and limits

Known notices are recorded before reuse. Pending applicability blocks receipt
acceptance. Nonapplicability needs reviewed evidence and no addressed IDs.
Applicable notices must address every named member with current decisions;
accessible artifacts must occur in current context and reviewed decision/path
evidence, and inaccessible evidence requires a current bounded-access assessment.
Any notice byte change invalidates the existing receipt. No web monitor or
detection of evidence withheld from inputs is claimed.

Run `python scripts/test_qsar_ordinary_adjudication.py` from the repository root.
Fixtures cover eight NPASS rows, duplicate support, equal-valued distinct primary
columns, ordinary secondary experimental eligibility, prediction exclusion,
separate unknown-origin and unavailable-route holds, probit, permitted transform,
inequality, blank-row accounting, and positive native adapters for all four
advertised sources. These are synthetic engine tests. Producer/guard equality,
reviewed preidentity/material projection, zero-output holds, runtime PASS/reuse,
preparation propagation, and independent implementation review are separate
integration acceptance requirements.
