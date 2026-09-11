# Submission Bundle Workflow

This is the authoritative end-to-end workflow for preparing a research-manuscript submission package. The bundle is one coordinated release unit: manuscript, Supporting Information (SI), supporting workbook, cover letter, graphical table-of-contents (TOC) asset, submission figure TIFFs, rendered PDFs, checksum manifest, and external QA receipt. Journal instructions and explicit user decisions may narrow or strengthen this workflow; record each override.

## Non-negotiable boundaries

- Maintain exactly one authoritative candidate bundle. Do not create competing `final`, `final2`, or agent-specific packages.
- Before mutation, make and verify byte-for-byte preimage copies in a recovery directory **outside** the candidate bundle. Recovery files never ship in the submission bundle.
- Keep authoritative numerical evidence immutable. Copy it to a read-only evidence area outside the bundle, hash it, and make all claims traceable to it.
- Incorporate the latest author edits before freezing the preimage. Do not choose a source by filename or modification time alone; compare content, tracked changes, comments, author-designated authority, and available provenance. Stop on unresolved divergence.
- Never infer author order, affiliations, corresponding-author details, funding, conflicts of interest (COI), permissions, or AI-use facts. Missing facts require human resolution and block `submission-ready`.
- Preserve the context-isolation boundary: manuscript-specific evidence and author discussion remain in the manuscript run. A harness-maintenance run receives only generalized rules and a bounded requirement receipt.

## Run layout and authority

Use one run directory outside the final publication location. Names are illustrative:

```text
submission_run_<project>_<run-id>/
  recovery/                 # verified preimages; never inside bundle
  evidence/                 # immutable source evidence and evidence manifest
  work/                     # conversions, renders, inspections
  reviews/                  # audit output and durable reviewer evidence
  run-plan.json             # the only executable plan
  decision-log.jsonl        # append-only steering and authorization records
  publication-receipt.json  # final run receipt, outside bundle
  bundle.stage/             # the only candidate bundle
```

The candidate bundle contains only journal-facing or explicitly required audit artifacts. A typical inventory is:

```text
bundle.stage/
  manuscript.docx
  supporting-information.docx
  supporting-data.xlsx
  cover-letter.docx
  graphical-toc.docx        # or a journal-required raster
  tiff/
  pdf/
  external-qa-receipt.json
  SHA256SUMS
```

Actual names and required formats come from the journal. Record an authority ledger with one row per component: logical role, authoritative input, current candidate, required output, owner, source hash, current hash, and journal rule. A document is not authoritative merely because it is newest on disk.

## Single executable plan and steering

Create `run-plan.json` after discovery and before mutation. It must contain:

- run and bundle identifiers;
- authoritative inputs and verified preimage receipt;
- immutable evidence-manifest hash;
- allowed mutation scope and explicit exclusions;
- old-to-new naming mappings;
- main/SI/workbook/figure/cover/graphical-TOC/PDF/reference actions;
- assigned producer and reviewer for each gate;
- dependencies, acceptance checks, and invalidation rules;
- unresolved human facts and release blockers.

A user steer updates this same plan and appends a decision to `decision-log.jsonl`; it does not create a second plan or restart unaffected work. Mark affected completed steps invalid, preserve their receipts, and rerun only their downstream gates. Never let two agents independently translate the same steer into competing plans.

## Bounded handoff contract

The orchestrator owns authority decisions and the single plan. Every specialist handoff is bounded by:

```text
run_id; bundle_id; input hashes; preimage receipt; evidence-manifest hash;
assigned plan step; allowed read/write paths; required output; blocking questions;
downstream gate invalidated by any change
```

Producers return changed paths, before/after hashes, evidence used, checks performed, and blockers. Read-only reviewers return findings and durable evidence; they do not repair their own findings. Specialists do not cascade work to another specialist or silently widen scope. The orchestrator resolves conflicts and is the only agent that promotes the bundle.

## Stage 0 — Discover journal and package contract

1. Read the official journal instructions and any user-supplied checklist.
2. Inventory every candidate main document, SI file, workbook, cover, graphical-TOC raster/container, source image, figure TIFF, PDF, reference library, comment, tracked change, and release note.
3. Identify actual author-designated authority and the latest author edits by content comparison. Report ambiguous branches instead of merging them heuristically.
4. Record required file types, naming, size, color mode, compression, page, word, reference, disclosure, and separate-upload requirements.
5. Classify requested work and the exact removal authority. An instruction to reduce redundancy is not blanket permission to delete tables or raw data.
6. Discover the requested figure-generation provider and its installed interface before planning generation.

Gate: authority ledger, journal contract, removal scope, provider requirement, and missing human facts are explicit.

## Stage 1 — Freeze evidence and recoverable preimages

1. Close Office applications or otherwise ensure inputs are stable; reject temporary `~$` lock files as sources. Never delete a user's Office lock file: if a live or ambiguous lock prevents a stable read, preimage, audit, or promotion, report it and block until exclusive access is established.
2. Copy every authoritative input that may be changed to `recovery/`, preserving bytes and relative names.
3. Create a recovery SHA-256 manifest and verify every copied hash against its source before any mutation.
4. Copy authoritative numerical inputs, scripts, model outputs, raw workbook sources, formulas, full-precision values, structure strings such as SMARTS, and provenance records to `evidence/`. Make that evidence area read-only where the platform permits and write its manifest.
5. Record tool and source versions needed to reproduce numbers. Do not place credentials, workstation paths, private discussion, or session artifacts in a public bundle or receipt.

Numerical reproducibility and document recoverability are different:

- the evidence archive establishes which fixed inputs and computations support numbers;
- the recovery archive restores pre-edit documents and reader-facing artifacts;
- later wording, styling, pagination, or metadata edits create new document hashes but do not silently create a new numerical evidence version;
- any changed score, fit, filter, fold, or derived value requires a new evidence version and invalidates every dependent claim, figure, table, PDF, manifest, and review.

Gate: recovery and evidence manifests exist, source-to-copy verification passed, and their hashes are in the plan.

## Stage 2 — Reconcile names, claims, and dependencies

Build an occurrence-level dependency map for claims, names, figures, tables, citations, and assets across:

- main text, fields, captions, cross-references, headers, and footers;
- SI prose, captions, tables, and cross-references;
- workbook sheet names, title cells, formulas, defined names, charts, validation rules, and internal links;
- cover letter and graphical-TOC text or graphics;
- standalone TIFF/PDF filenames and the checksum manifest.

For every rename, keep a simultaneous `old -> new` mapping. Apply the complete mapping as one coordinated change; do not rename prose first and leave formulas or captions stale. Verify that no unintended old occurrence remains, formulas still resolve without `#REF!`, quoted sheet references are valid, title cells match their sheets, and captions/cross-references match the final objects. If an old term must remain in historical literature or quoted evidence, record the exception.

Build a claim-evidence matrix that separates numerical identity from interpretation. In particular, use accurate generic distinctions:

- **positive-only evaluation** has no verified negatives and cannot by itself establish discrimination, specificity, or a false-positive rate;
- **verified-negative evaluation** supports those negative-class quantities only within its documented sampling regime;
- **source-overlap consistency** describes a comparison whose source, selection, or overlap rules do not establish independence from model construction; it does not assert that every retained record overlaps and is not independent external validation;
- **full-fit results** come from a model fit using all designated data and are not held-out estimates;
- **held-out results** require evaluation records excluded according to the declared split;
- **fixed-baseline augmentation** measures an added component against an unchanged baseline and is not an end-to-end retraining comparison;
- **hash-based folds** are deterministic assignments but are not scaffold-disjoint unless structural grouping and separation were explicitly enforced and verified.

Gate: dependency map, claim-evidence matrix, and complete rename mapping are approved before coordinated edits.

## Stage 3 — Revise main manuscript and SI as complements

Revise the main manuscript for the discovery, essential method, central evidence, limitations, and bounded interpretation. Preserve the author's XML-level formatting unless normalization is explicitly authorized:

- retain paragraph and run properties, character styles, section/page/column breaks, field codes, bookmarks, comments, and tracked-change state outside the approved edit;
- retain table widths, cell properties, header-row repetition, `keep with next`, `keep lines together`, row-splitting or `cantSplit` behavior, and intentional pagination;
- mutate the smallest text or relationship scope possible; do not rebuild a document from extracted plain text;
- do not accept/reject tracked changes, delete comments, flatten fields, or normalize styles as a side effect;
- when formatting normalization is authorized, name the exact styles/properties and verify everything outside that set is unchanged.

SI must add explanation and reproducibility detail rather than repeat the main text. Put expanded methods, sensitivity checks, secondary analyses, supporting figures/tables, and enough provenance to interpret workbook data in SI. Keep a short cross-reference in the main text. Do not copy the same full method, result paragraph, or table into both documents merely for convenience.

Retain negative or failed results when they bound the chosen method. Never strengthen a claim beyond the immutable evidence.

Gate: main and SI are complementary, current author edits remain, comments/changes were handled only as authorized, and every edited claim maps to evidence.

## Stage 4 — Audit workbook keep/drop decisions

Inventory every sheet and every reader-facing table, including hidden/very-hidden sheets, formulas, charts, named ranges, links, and macros where applicable. For each item record:

```text
identifier; purpose; first main citation; first SI citation; evidence source;
decision = keep | merge | move | remove-from-reader-view;
rationale; explicit authorization; retained raw/recovery location; dependent formulas
```

Rules:

- Order substantive sheets by first citation in the main manuscript, then by first citation in SI, then place uncited data dictionaries, provenance, or audit material in a clearly named trailing section. Do not lead with a timeline, version log, or technical changelog.
- Recommend removal or merging when a reader-facing table merely duplicates a figure or another table, but perform it only within explicit authorized scope.
- `remove-from-reader-view` never means destroy. Preserve raw full-precision values, formulas, SMARTS or equivalent structure patterns, row-level provenance, units, missing-value meaning, and source identifiers in a retained artifact and the recovery archive.
- Never replace formula cells with rounded displayed values. If a journal-facing sheet must be simplified, keep the exact computational sheet in the retained workbook or an explicitly included supporting data artifact.
- Reconcile main/SI citations, sheet order, sheet names, title cells, formulas, defined names, and links after every decision.

Gate: every sheet/table has a documented decision, every deletion has authority, workbook formulas recalculate without broken references, and exact reusable data remain recoverable.

## Stage 5 — References and literature

Run two independent gates:

1. **Numbering gate:** references are numbered by first textual citation, citation clusters and ranges resolve, numbering is continuous, and every bibliography item/citation is paired.
2. **Semantic gate:** an independent reviewer checks that each source actually supports the nearby proposition and matches its topic, population/system, method, and claimed strength. Metadata or title similarity alone does not establish support.

Do not repair numbering while assuming topic support, and do not treat a topic match as proof that numbering is correct. Preserve exact identifiers and consult the source when support is uncertain; never invent bibliographic facts.

Gate: numbering and semantic-topic receipts both pass, or unresolved citations block readiness.

## Stage 6 — Generate and reconcile figures

Freeze the hashes of all data supplied to figure generation. Generation may alter presentation, layout, labels, or illustration, but must never rescore, refit, resample, reassign folds, change thresholds, or recompute scientific results. If a numerical defect is found, stop, return to the evidence stage, create a new evidence version with authorization, and invalidate downstream work.

Honor the user's requested generator and provider. When Codex CLI image generation is requested, use its actual installed image-generation capability and record the invoked interface, provider/model information made available by the tool, inputs, outputs, and hashes. If the requested capability is unavailable or fails, report the blocker. Never claim generated output, fake a provider call, substitute a placeholder, or silently replace requested image generation with a code-drawn graphic.

For every generated or edited asset:

- cross-check every displayed number, category, label, denominator, threshold, unit, and evidence boundary against the frozen inputs;
- verify icon meaning and licenses/permissions; decorative icons must not imply unmeasured mechanisms, populations, or outcomes;
- reconcile panels, captions, legends, in-text references, workbook data, and TIFF filenames;
- record actual AI assistance and obtain human-approved disclosure wording required by the journal; do not assert either AI use or no AI use without facts.

Gate: generation provenance and hashes exist, scientific content exactly matches frozen data, and disclosure facts await or have human approval.

## Stage 7 — Replace graphical TOC and build exact figure-TIFF inventory

The `--toc` audit input is the journal's **graphical TOC**, never an inventory of bundle files. It may be a PNG, JPEG, TIFF, or BMP raster, or a DOCX container with an embedded raster. Replace the graphical-TOC deliverable and, when it is a DOCX container, update its authoritative embedded image and OOXML relationship. Remove an obsolete relationship only when authorized and safe, then render the container to confirm the intended graphic appears rather than an old preview. If the journal requires both a container and a standalone raster, keep both in the checksum manifest and reconcile them manually; `--toc` identifies the one authoritative graphical-TOC input audited structurally.

Create one mapping from every captioned figure in the main manuscript and SI to its required file under `--tiff-dir`. Figure identifiers are derived from captions and TIFF filenames; the directory must have no missing or orphan identifier. The graphical TOC is inspected through `--toc`, not implicitly added to the captioned-figure TIFF mapping. If the graphical TOC itself is a TIFF, it is supplied as `--toc` unless it independently corresponds to a captioned figure.

Each submission TIFF must:

- preserve the authoritative image's native pixel width and height; do not resize or resample merely to meet a nominal DPI;
- be RGB and use LZW compression when the journal contract requires it;
- preserve aspect ratio with no stretching in conversion or DOCX placement;
- match the intended embedded image by pixels/content, allowing only the required lossless container/color-mode conversion;
- carry DPI metadata only as metadata. Changing a DPI tag does not add pixels or resolution. If native pixels are insufficient at final physical size, regenerate from the authoritative source rather than retag or upscale.

Record logical figure/panel, document occurrence/relationship, embedded-media hash, pixel dimensions, color mode, TIFF compression, TIFF hash, display dimensions, and aspect-ratio comparison. Inspect the OOXML package, not just visible captions. The current structural auditor verifies decoded TIFF format, single-frame RGB mode, exact native dimensions and pixels, and display aspect where a usable DOCX extent exists; an unavailable extent is `UNVERIFIED`. It does not certify LZW compression, so retain a separate compression check in the run evidence.

Gate: the authoritative graphical TOC is current and undistorted; every captioned main/SI figure maps to the exact figure-TIFF inventory; no old, missing, distorted, or orphan figure image remains. The checksum manifest—not the graphical TOC—is the sole complete bundle inventory.

## Stage 8 — Rendered and independent QA

QA has three separate evidence scopes whose receipt keys are exact:

1. `visual_readability`: inspect every source asset at native size and every embedded DOCX/PDF rendering at final physical display size. Record display width/scale, clipping, smallest meaningful text, contrast, panel completeness, page breaks, table pagination, and image distortion.
2. `citation_semantics`: verify citation-topic support independently of numbering.
3. `scientific_review`: check claims, exact numbers, denominators, labels, methods, split descriptions, limitations, main/SI/workbook consistency, and evidence boundaries.

The reviewer must be independent of the final producer. Machine checks, hashes, package parsing, pixel dimensions, DPI tags, and successful scripts cannot certify readability, citation meaning, or scientific correctness. Any source or embedded view that is unreadable, clipped, misleading, or incomplete blocks release even when structural checks pass.

After content and layout are final, render fresh PDFs from the final DOCX inputs. Compare pagination, tables, equations, fonts, links, figures, captions, and graphical-TOC rendering against the DOCX views. A PDF is a derivative deliverable, not evidence that its DOCX source is current.

Practical rendering safeguards:

- Use explicit UTF-8 for scripts and receipts. If Office automation has trouble with a non-ASCII path, render an ASCII-named temporary copy, not a rewritten authoritative document.
- With installed Word, use a separate application instance, read-only input, disabled macros, PDF export, and close without saving. Verify source hashes before and after. Never terminate the author's own Office process or delete its lock file.
- Compare extracted PDF text with all source paragraphs and table cells, allowing only documented Unicode/whitespace normalization. Review the actual page images as well; text equality does not detect an orphan caption, unreadable graphic, or empty table header.
- A figure and its complete caption must fit together. When a newly expanded cross-reference causes a spill, first remove redundant caption wording without losing meaning; do not silently shrink type, distort the image, or discard author breaks.
- Keep a table caption, header, and at least its first data row together. A header alone at the foot of a page is not adequate linkage. Preserve scientific cells and record narrowly scoped pagination-property repairs.
- After a local edit, rerender affected documents and invalidate their reviews. Retain other PDF/review receipts only after rechecking their source and output hashes; never mark an old proof current merely because its filename matches.

Create the external QA receipt inside the bundle only after an independent reviewer inspects the final artifacts. Use this exact implemented schema:

```json
{
  "schema_version": "aaalab.external-qa/v1",
  "reviewer": {
    "identity": "independent-reviewer-id",
    "role": "independent manuscript and bundle reviewer",
    "independence_basis": "Did not produce the final bundle artifacts"
  },
  "reviewed_at": "2025-01-02T03:04:05Z",
  "outcome": "approved",
  "scope": {
    "visual_readability": {"outcome": "approved"},
    "citation_semantics": {"outcome": "approved"},
    "scientific_review": {"outcome": "approved"}
  },
  "artifact_hashes": {
    "manuscript.docx": "<64-hex-sha256>",
    "supporting-information.docx": "<64-hex-sha256>",
    "supporting-data.xlsx": "<64-hex-sha256>",
    "graphical-toc.docx": "<64-hex-sha256>",
    "tiff/Figure-1.tif": "<64-hex-sha256>"
  },
  "unresolved_blockers": []
}
```

`identity`, `role`, and a descriptive `independence_basis` must be non-empty. `reviewed_at` must be an ISO-8601 timestamp with a timezone and must not be in the future. Top-level `outcome` and all three scope outcomes must be exactly `approved`, and `unresolved_blockers` must be an empty array. `artifact_hashes` must cover the current main DOCX, SI DOCX, workbook, configured graphical TOC, and **every** TIFF discovered under `--tiff-dir`; there is no fixed artifact count. Additional stated hashes are checked if supplied.

Keep detailed visual, citation, and scientific review evidence in the external run records and cite it from the publication receipt. The implemented QA object records outcomes and hash binding; it does not authenticate reviewer identity, independence, or scientific claims. A boolean such as `scientifically_verified` is ignored and cannot substitute for review.

Gate: fresh PDFs pass render comparison and external QA status is `CURRENT_APPROVED`, bound to the final main DOCX, SI DOCX, workbook, graphical TOC, and every TIFF discovered under `--tiff-dir`.

## Stage 9 — Cover letter and human-fact gate

Write a concise, discovery-first cover letter. State the research question, principal discovery, strongest evidence, fit for the journal, and bounded significance. Add only required declarations. Do not turn it into a project timeline, version history, implementation narrative, technical changelog, or exhaustive methods summary.

Obtain explicit human confirmation for author names/order, affiliations, corresponding author, funding/grants, COI, permissions, prior/submitted status, and actual AI use/disclosure. Preserve the confirmation reference in the run records without publishing private correspondence. Missing or ambiguous facts remain blockers; never fill them from inference.

Gate: cover letter matches current claims and journal scope, and all factual declarations have human evidence.

## Stage 10 — Finalize manifest and run structural audit

Do not refresh PDFs, QA receipts, or manifests until Stage 1 preimage verification passed. Once any final artifact changes, regenerate every dependent PDF/receipt and then the manifest.

Finalize all bundle artifacts first. The checksum manifest is the sole complete package inventory; the graphical TOC is a scientific graphic, not an inventory. Write `SHA256SUMS` last using portable GNU-style records:

```text
<64-lowercase-hex-sha256>  path/relative/to/bundle
```

The auditor also accepts this JSON form:

```json
{
  "algorithm": "sha256",
  "files": [
    {"path": "path/relative/to/bundle", "sha256": "<64-hex-sha256>"}
  ]
}
```

Use forward-slash relative paths. Paths must be unique, non-symlinked, inside the bundle, and free of traversal. Blank lines and `#` comments are allowed in the text form. Include every regular bundle file, including covers, PDFs, DOCX/XLSX files, figure TIFFs, the graphical TOC, and the external QA receipt. Exclude the checksum manifest itself and Office files whose basename starts with `~$`; neither may be listed. Exclusion is not permission to delete a lock: any lock that prevents stable inspection or exclusive promotion is a reported blocker.

Run the portable automated structural audit from the installed harness:

```bash
python scripts/audit_submission_bundle.py \
  --bundle-root PATH \
  --main-docx REL \
  --si-docx REL \
  --workbook REL \
  --tiff-dir REL \
  --toc REL \
  --checksum-manifest REL \
  --qa-receipt REL
```

`--toc` is a bundle-relative graphical-TOC raster or DOCX path. `--checksum-manifest` is the bundle-relative inventory path. `--qa-receipt` is optional to the CLI but required for publication eligibility. All configured paths must resolve inside the bundle root. The auditor rejects absolute, drive-qualified, escaping, symlinked, or junction-traversing paths; reads Office packages in place; makes no source writes; and emits one JSON receipt to standard output. Store that JSON outside the audited bundle to avoid a circular manifest.

Exit/readiness semantics:

- exit `0`: every structural gate is `PASS`, external QA status is `CURRENT_APPROVED`, `verdict` is `SUBMISSION_READY`, and `publication_eligibility` is `ELIGIBLE`;
- exit `1`: `structural_machine_audit` is `FAIL` or `UNVERIFIED`, `publication_eligibility` is `NOT_ELIGIBLE`, and `verdict` is respectively `BLOCKED` or `STRUCTURAL_UNVERIFIED`;
- exit `2`: every structural gate is `PASS`, but external QA is `MISSING`, `INVALID`, `STALE`, or `NOT_APPROVED`; `verdict` is `BLOCKED` and `publication_eligibility` is `NOT_ELIGIBLE`.

Malformed or missing CLI arguments also return exit `1`, with a JSON `INVALID_INPUT` verdict and `structural_machine_audit: NOT_RUN`; this is not a completed audit. Explicit `--help` returns ordinary help text and exit `0`, not a JSON audit receipt.

The stdout audit schema is exactly `aaalab.submission-bundle-audit/v1`. This successful-output example preserves the implemented field names:

```json
{
  "schema_version": "aaalab.submission-bundle-audit/v1",
  "generated_at": "2025-01-02T03:04:05Z",
  "bundle_root": ".",
  "inputs": {
    "main_docx": "manuscript.docx",
    "si_docx": "supporting-information.docx",
    "workbook": "supporting-data.xlsx",
    "tiff_dir": "tiff",
    "toc": "graphical-toc.docx",
    "checksum_manifest": "SHA256SUMS",
    "qa_receipt": "external-qa-receipt.json"
  },
  "verdict": "SUBMISSION_READY",
  "structural_machine_audit": "PASS",
  "publication_eligibility": "ELIGIBLE",
  "exit_code": 0,
  "source_writes_performed": false,
  "gates": {
    "path_safety": {"status": "PASS"},
    "docx_packages": {"status": "PASS"},
    "bibliography": {"status": "PASS"},
    "figure_table_references": {"status": "PASS"},
    "workbook": {"status": "PASS"},
    "images": {"status": "PASS"},
    "toc": {"status": "PASS"},
    "manifest": {"status": "PASS"},
    "external_qa": {"status": "CURRENT_APPROVED"}
  },
  "inventory": [
    {"path": "SHA256SUMS", "bytes": 700, "sha256": "<64-hex-sha256>"},
    {"path": "cover-letter.docx", "bytes": 123, "sha256": "<64-hex-sha256>"},
    {"path": "external-qa-receipt.json", "bytes": 123, "sha256": "<64-hex-sha256>"},
    {"path": "graphical-toc.docx", "bytes": 123, "sha256": "<64-hex-sha256>"},
    {"path": "manuscript.docx", "bytes": 123, "sha256": "<64-hex-sha256>"},
    {"path": "pdf/manuscript.pdf", "bytes": 123, "sha256": "<64-hex-sha256>"},
    {"path": "supporting-data.xlsx", "bytes": 123, "sha256": "<64-hex-sha256>"},
    {"path": "supporting-information.docx", "bytes": 123, "sha256": "<64-hex-sha256>"}
  ],
  "excluded_office_locks": [],
  "documents": [
    {
      "path": "manuscript.docx",
      "kind": "main",
      "bibliography_numbers": [1],
      "numeric_citations": [1],
      "figure_captions": [],
      "table_captions": [],
      "figure_references": [],
      "table_references": [],
      "embedded_image_count": 0
    },
    {
      "path": "supporting-information.docx",
      "kind": "si",
      "bibliography_numbers": [],
      "numeric_citations": [],
      "figure_captions": [],
      "table_captions": [],
      "figure_references": [],
      "table_references": [],
      "embedded_image_count": 0
    }
  ],
  "workbook": {
    "path": "supporting-data.xlsx",
    "sheets": ["Data"],
    "excel_tables": [],
    "figure_identifiers": [],
    "table_identifiers": [],
    "figure_references": [],
    "table_references": [],
    "formula_count": 0
  },
  "images": {"tiff_paths": [], "verified_pairs": []},
  "toc": {
    "path": "graphical-toc.docx",
    "kind": "docx_container",
    "sha256": "<64-hex-sha256>",
    "embedded_images": [
      {
        "member": "word/media/image1.png",
        "format": "PNG",
        "mode": "RGB",
        "native_pixels": [1200, 600],
        "pixel_sha256": "<64-hex-sha256>"
      }
    ]
  },
  "manifest": {
    "path": "SHA256SUMS",
    "algorithm": "sha256",
    "listed_files": 7,
    "expected_files": 7,
    "excluded_manifest": "SHA256SUMS",
    "excluded_office_locks": []
  },
  "external_qa": {
    "path": "external-qa-receipt.json",
    "status": "CURRENT_APPROVED",
    "schema_version": "aaalab.external-qa/v1",
    "reviewer": {
      "identity": "independent-reviewer-id",
      "role": "independent manuscript and bundle reviewer",
      "independence_basis": "Did not produce the final bundle artifacts"
    },
    "reviewed_at": "2025-01-02T03:04:05Z",
    "outcome": "approved",
    "scope_outcomes": {
      "visual_readability": "approved",
      "citation_semantics": "approved",
      "scientific_review": "approved"
    },
    "artifact_hash_count": 4,
    "artifact_hashes": {
      "graphical-toc.docx": "<64-hex-sha256>",
      "manuscript.docx": "<64-hex-sha256>",
      "supporting-data.xlsx": "<64-hex-sha256>",
      "supporting-information.docx": "<64-hex-sha256>"
    },
    "required_artifacts": [
      "graphical-toc.docx",
      "manuscript.docx",
      "supporting-data.xlsx",
      "supporting-information.docx"
    ],
    "hash_binding_current": true,
    "identity_authenticated": false,
    "scientific_claim_verified_by_machine": false
  },
  "findings": [],
  "unresolved_blockers": [],
  "limitations": [
    "The machine audit checks package structure, identifiers, relationships, hashes, and decodable raster properties; it does not judge visual readability.",
    "Numeric citation syntax and target existence are checked, but citation meaning and source support require independent semantic review.",
    "Scientific correctness is outside the machine audit. External QA is a hash-bound attestation whose reviewer identity, independence, and scientific claims are not authenticated by this tool.",
    "DOCX figure association is inferred from the caption paragraph and up to four immediately preceding paragraphs; unusual layouts require visual review."
  ]
}
```

`documents`, `workbook`, `images`, `toc`, `manifest`, and `external_qa` carry the actual content-dependent summaries for the audited files; the example simply uses a valid no-figure case. Structural gate statuses are `PASS|FAIL|UNVERIFIED`. External-QA status is `MISSING|INVALID|STALE|NOT_APPROVED|CURRENT_APPROVED`. Verdict is `SUBMISSION_READY|STRUCTURAL_UNVERIFIED|BLOCKED`; publication eligibility is `ELIGIBLE|NOT_ELIGIBLE`. Each finding has `gate`, `severity`, `code`, and `message`, with optional `path` and `details`; severity is `blocker|unverified|warning`.

This is an **automated structural audit**, not automated scientific verification. Gate: the checksum manifest has exact coverage and the auditor returns exit `0`, `structural_machine_audit: PASS`, external QA `CURRENT_APPROVED`, `verdict: SUBMISSION_READY`, and `publication_eligibility: ELIGIBLE`. These are pre-promotion requirements, not yet a readiness label for the publication destination; the workflow applies that label only after Stage 11 completes and verifies promotion.

## Stage 11 — Guarded publication and status receipt

Build and audit in `bundle.stage/` on the same filesystem as the publication destination; never describe a cross-volume move as atomic. Before promotion, acquire exclusive access, verify the destination and rollback policy, and make a checked rollback copy or preserve the existing published directory under an agreed recovery name. Do not delete user Office lock files: report a lock failure and stop.

Use a genuinely atomic directory rename/exchange only when the filesystem and operating-system API support replacing that destination in the actual situation. In particular, do not assume that renaming over a nonempty existing Windows directory is atomic or even supported. When atomic replacement is unavailable, perform an explicitly guarded recoverable transaction: keep the prior bundle intact, stage on the same filesystem, move the prior destination to its verified rollback location, move the fully audited stage into place under exclusive access, then recheck destination inventory, manifest hashes, QA receipt currency, and structural audit. Sequential file copies are never atomic and must not be described as such. Do not expose or claim `submission-ready` for the promoted destination until the entire transaction and post-promotion checks complete.

If any step fails, restore or retain the prior verified bundle, preserve the failed stage for diagnosis where safe, release exclusive access, and record the failure outside both bundles. If rollback itself cannot be verified, stop with no readiness promotion; never delete the prior bundle merely to force completion.

Readiness and publication states are independent evidence-backed fields:

- `review-ready`: content is assembled, independent review found no unresolved content, scientific, citation, or visual blocker, and any remaining submission-only blockers are explicit. It is not submission-ready.
- `submission-ready`: the final package has human-approved declarations, all workflow gates pass, the current audit returns exit `0` with `verdict: SUBMISSION_READY` and `publication_eligibility: ELIGIBLE`, and guarded promotion/post-promotion verification completes.
- `journal-submitted`: use only after durable evidence of actual journal transmission/receipt; never infer it from submission readiness.
- `git_source`: records whether source was published to the intended Git remote and commit; it says nothing about journal submission.
- `pypi`: records a separately verified package-index release; a Git tag is not a PyPI release.
- `github_assets`: records separately verified GitHub release assets and their hashes; a pushed source commit is not an asset release.

Do not perform external submission or publication without the required user authorization and credentials.

## Final publication receipt

Write `publication-receipt.json` outside the bundle only after guarded promotion and post-promotion verification so it cannot create a manifest hash cycle. The receipt schema is `aaalab.submission-bundle-publication-receipt.v1` with these required fields:

Use bundle-relative paths for bundle artifacts and run-relative paths for external run records. Never serialize absolute workstation paths, credentials, private correspondence, or session artifacts.

| Field | Required content |
|---|---|
| `schema_version`, `run_id`, `bundle_id`, `issued_at` | receipt identity and timestamp with timezone |
| `authority` | final relative component paths and hashes |
| `preimage` | external recovery-manifest path/hash and verification evidence |
| `numerical_evidence` | immutable evidence-manifest path/hash and evidence version |
| `plan` | single plan path/hash plus steering decision-log path/hash |
| `checksum_manifest` | final complete-inventory path/hash |
| `automated_audit` | command interface, report path/hash, `exit_code`, `verdict`, `structural_machine_audit`, `publication_eligibility`, external-QA gate status, and limitations |
| `external_qa` | receipt path/hash, exact schema, reviewer/independence fields, timezone-bearing review time, `CURRENT_APPROVED` status, exact scope outcomes, required hash coverage, and detailed-review evidence references |
| `human_approvals` | evidence references for required author/funding/COI/AI facts; no unsupported booleans |
| `statuses` | separate readiness, journal submission, Git source, PyPI, and GitHub-assets states with evidence |
| `unresolved`, `limitations` | explicit remaining blockers or bounded caveats |

Example (placeholder hashes are intentionally non-evidence):

```json
{
  "schema_version": "aaalab.submission-bundle-publication-receipt.v1",
  "run_id": "run-example",
  "bundle_id": "bundle-example",
  "issued_at": "2025-01-02T03:04:05Z",
  "authority": {
    "main_docx": {"path": "manuscript.docx", "sha256": "<sha256>"},
    "si_docx": {"path": "supporting-information.docx", "sha256": "<sha256>"},
    "workbook": {"path": "supporting-data.xlsx", "sha256": "<sha256>"},
    "cover": {"path": "cover-letter.docx", "sha256": "<sha256>"},
    "graphical_toc": {"path": "graphical-toc.docx", "sha256": "<sha256>"},
    "tiffs": [{"path": "tiff/Figure-1.tif", "sha256": "<sha256>"}],
    "pdfs": [{"path": "pdf/manuscript.pdf", "sha256": "<sha256>"}],
    "qa_receipt": {"path": "external-qa-receipt.json", "sha256": "<sha256>"}
  },
  "preimage": {
    "manifest_path": "recovery/SHA256SUMS.preimage",
    "manifest_sha256": "<sha256>",
    "verification_evidence": "reviews/preimage-verification.txt"
  },
  "numerical_evidence": {
    "version": "evidence-example",
    "manifest_path": "evidence/SHA256SUMS.evidence",
    "manifest_sha256": "<sha256>"
  },
  "plan": {
    "path": "run-plan.json",
    "sha256": "<sha256>",
    "decision_log_path": "decision-log.jsonl",
    "decision_log_sha256": "<sha256>"
  },
  "checksum_manifest": {"path": "SHA256SUMS", "sha256": "<sha256>"},
  "automated_audit": {
    "interface": "scripts/audit_submission_bundle.py",
    "exit_code": 0,
    "report_path": "reviews/submission-bundle-audit.json",
    "report_sha256": "<sha256>",
    "verdict": "SUBMISSION_READY",
    "structural_machine_audit": "PASS",
    "publication_eligibility": "ELIGIBLE",
    "gates": {
      "external_qa": {"status": "CURRENT_APPROVED"}
    },
    "limitations": []
  },
  "external_qa": {
    "receipt_path": "external-qa-receipt.json",
    "receipt_sha256": "<sha256>",
    "schema_version": "aaalab.external-qa/v1",
    "status": "CURRENT_APPROVED",
    "reviewer": {
      "identity": "independent-reviewer-id",
      "role": "independent manuscript and bundle reviewer",
      "independence_basis": "Did not produce the final bundle artifacts"
    },
    "reviewed_at": "2025-01-02T03:04:05Z",
    "scope_outcomes": {
      "visual_readability": "approved",
      "citation_semantics": "approved",
      "scientific_review": "approved"
    },
    "required_artifacts": [
      "manuscript.docx",
      "supporting-information.docx",
      "supporting-data.xlsx",
      "graphical-toc.docx",
      "tiff/Figure-1.tif"
    ],
    "review_evidence": [
      "reviews/final-size-visual-review.txt",
      "reviews/citation-support-review.txt",
      "reviews/scientific-review.txt"
    ]
  },
  "human_approvals": [
    {"scope": "authorship", "evidence": "reviews/authorship-approval.txt"},
    {"scope": "funding", "evidence": "reviews/funding-approval.txt"},
    {"scope": "coi", "evidence": "reviews/coi-approval.txt"},
    {"scope": "ai_disclosure", "evidence": "reviews/ai-disclosure-approval.txt"}
  ],
  "statuses": {
    "readiness": {
      "state": "submission-ready",
      "evidence": [
        "reviews/submission-bundle-audit.json",
        "external-qa-receipt.json",
        "reviews/authorship-approval.txt",
        "reviews/funding-approval.txt",
        "reviews/coi-approval.txt",
        "reviews/ai-disclosure-approval.txt"
      ]
    },
    "journal_submission": {"state": "not-submitted", "evidence": null},
    "git_source": {"state": "not-published", "evidence": null},
    "pypi": {"state": "not-published", "evidence": null},
    "github_assets": {"state": "not-published", "evidence": null}
  },
  "unresolved": [],
  "limitations": ["Automated checks do not certify scientific correctness or visual readability."]
}
```

A receipt records evidence; it does not create it. Any artifact mutation after its final hash makes the manifest, affected QA evidence, audit output, readiness status, and publication receipt stale.