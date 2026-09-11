---
name: research-manuscript
description: "Prepare or revise a research manuscript and its coordinated submission bundle: main text, Supporting Information, workbook, cover, graphical TOC, figure TIFFs, PDFs, references, provenance, and QA. Use directly for bounded work or as the protocol source for research-manuscript-harness."
---

# Research Manuscript Protocol

Use this protocol for manuscript creation, substantive revision, editorial QA, or a complete submission-ready package. It preserves scientific claim boundaries while coordinating every artifact that can become stale when the manuscript changes.

## Required references

Load both before editing:

1. `references/submission-bundle-workflow.md` — authoritative package, provenance, staging, audit, readiness, and publication workflow.
2. `references/manuscript-author-review-guidelines.md` — authoritative scientific-writing, SI, workbook, figure, reference, DOCX, cover, and visual-review rules.

Explicit user instructions and official target-journal requirements override defaults. Record the source and scope of every override in the single run plan.

## Operating contract

1. Maintain one authoritative bundle and one executable `run-plan.json`.
2. Reconcile the latest author edits and verify recoverable preimage copies outside the bundle before mutation.
3. Hash and preserve immutable numerical evidence separately from document recovery preimages.
4. Keep edits within the user's authorized scope. Recommend reader-facing table removal when justified, but do not delete it without authority; preserve exact data and provenance.
5. Update dependent main/SI/workbook/cover/graphical-TOC/figure-TIFF/PDF/reference artifacts together.
6. Use independent producer-reviewer gates. Machine checks cannot certify visual readability, citation meaning, or scientific correctness.
7. Never infer author, funding, COI, permissions, submission, or AI-use facts.
8. Never call partial or stale work submission-ready.

For a narrow request, execute only affected stages, but still establish authority, preimages, evidence, dependencies, and downstream invalidation. For a full multi-agent package, invoke the sibling `$research-manuscript-harness` skill and retain this document as the protocol source.

## Workflow

### 1. Discover authority and write the single plan

- Read the official journal instructions and user constraints.
- Inventory candidate main documents, SI, workbooks, figures, figure TIFFs, graphical-TOC assets, PDFs, reference sources, comments, and tracked changes.
- Establish content authority; do not select by filename or timestamp alone.
- Reconcile the latest author edits or stop on an unresolved branch.
- Create verified byte-for-byte recovery preimages outside the candidate bundle.
- Freeze and hash the numerical evidence, formulas, scripts, SMARTS or equivalent structures, and provenance needed to support the submission.
- Inventory every literal `{...}` instruction and embedded author comment. Map local and global effects; remove only when both are complete and removal is authorized.
- Write the one evidence-grounded plan before mutation. When the user steers, revise that plan and append the decision; do not create a duplicate plan.

### 2. Map evidence and coordinated dependencies

- Map every numerical and scientific claim to immutable evidence.
- Separate measured results from interpretation and record limitations.
- Create simultaneous `old -> new` mappings across main/SI prose, fields, captions, cross-references, workbook sheet names, title cells, formulas, defined names, links, graphical TOC, TIFF/PDF filenames, and checksum-manifest entries.
- Distinguish positive-only from verified-negative evaluation, source-overlap consistency from independent external validation, full-fit from held-out results, fixed-baseline augmentation from end-to-end training, and hash folds from verified scaffold-disjoint folds.
- Treat source-overlap consistency as a comparison whose source, selection, or overlap rules do not establish independence from model construction; do not assert that every retained record overlaps.
- Invalidate every dependent artifact and gate when a source value, name, figure, table, or declaration changes.

### 3. Revise main manuscript and SI

Audit and revise in reading order:

- title and human-confirmed author block;
- abstract;
- Introduction;
- Materials and Methods;
- Results;
- Discussion;
- Conclusions;
- availability and SI statements;
- references.

Keep main text focused on the discovery, essential method, central evidence, and bounded significance. Make SI complementary: add reproducibility detail, expanded methods, secondary evidence, and supporting interpretation rather than repeating main-text prose or tables. Move a main/SI analysis with its method, result, limitation, and references together.

Preserve author-authored Word XML properties, run/paragraph styles, breaks, fields, comments, tracked-change state, and table pagination outside authorized changes. Apply style normalization only when its exact scope is approved and non-target properties are verified unchanged.

### 4. Audit workbook keep/drop decisions

- Inventory visible, hidden, and very-hidden sheets, reader-facing tables, formulas, named ranges, charts, links, title cells, and citations.
- Classify each as `keep`, `merge`, `move`, or `remove-from-reader-view`, with rationale, dependencies, and explicit authority.
- Order substantive sheets by first main-manuscript citation, then first SI citation; place dictionaries/provenance/audit material afterward.
- Preserve full-precision raw values, formulas, SMARTS or equivalent structure definitions, units, identifiers, and row provenance in retained artifacts and the recovery archive.
- Reconcile sheet names, title cells, formulas, defined names, charts, and main/SI citations as one operation. Broken formulas or stale labels block release.

### 5. Run independent reference gates

Run numbering and semantic support as separate reviews:

- first-citation numbering, continuity, cluster/range resolution, and bibliography pairing;
- source-level topic and proposition support at the strength claimed.

A renumbered bibliography is not a semantic citation review. Missing source support blocks readiness.

### 6. Generate figures only from fixed inputs

Hash the exact input data before rendering. Figure work may change presentation but never rescore, refit, resample, reassign folds, change thresholds, or recompute results. A numerical correction reopens evidence mapping and invalidates all downstream artifacts.

Honor the requested generator/provider. If the user requests Codex CLI image generation, use the installed real image-generation interface and record actual tool/provider provenance. If it is unavailable, block and report; never fake generation or silently substitute a code-drawn image.

Verify exact numbers, labels, units, categories, evidence scope, panel/caption consistency, icon meaning and permissions, and actual AI involvement. Human-approved disclosure wording is required when applicable.

### 7. Synchronize graphical-TOC and figure-TIFF deliverables

Treat the graphical TOC as a journal-facing raster or DOCX container, not as the bundle inventory. Replace the deliverable and, when it is a DOCX container, its authoritative embedded image. Inspect OOXML relationships and rendered output so an obsolete preview cannot survive.

Build an exact mapping from each captioned main/SI figure to the submitted figure-TIFF directory. Require no missing or orphan TIFF identifier. Audit the graphical TOC separately as its configured raster or DOCX container; do not treat it as the checksum inventory or automatically add it to the captioned-figure TIFF mapping. Preserve native pixel dimensions and aspect ratio; produce RGB/LZW TIFF when required without stretching, resampling, or representing a changed DPI tag as new resolution. Regenerate from source when native pixels are inadequate.

### 8. Verify source, embedded, and rendered artifacts

- Inspect each source visual at native size.
- Inspect each DOCX and PDF rendering at final physical display size or an equivalent downscaled preview.
- Check clipping, minimum readable text, contrast, icons, panel/category completeness, distortion, captions, page breaks, and table pagination.
- Cross-check every number against authoritative CSV/JSON/XLSX or equivalent evidence.
- Require an independent reviewer for visual readability, semantic citations, and scientific correctness.
- Record final approval as `aaalab.external-qa/v1` with non-empty reviewer `identity`, `role`, and `independence_basis`; timezone-bearing `reviewed_at`; top-level `outcome: approved`; approved `visual_readability`, `citation_semantics`, and `scientific_review` scope outcomes; an empty `unresolved_blockers` array; and current hashes for main DOCX, SI DOCX, workbook, configured graphical TOC, and every TIFF discovered under `--tiff-dir`.
- Render fresh PDFs only after editable artifacts are final; any later edit invalidates the derivative and its receipt.

### 9. Complete cover and human facts

Make the cover letter concise and discovery-first: research question, principal discovery, strongest evidence, bounded significance, and journal fit. Do not write a timeline, version history, implementation narrative, or technical changelog.

Obtain human approval for names/order, affiliations, corresponding author, funding, COI, permissions, prior/submission status, and actual AI use/disclosure. Do not infer declarations.

### 10. Finalize, audit, and publish with verified rollback

Follow the exact finalization sequence in `submission-bundle-workflow.md`:

1. verify external preimages and immutable evidence;
2. finalize all content, figure TIFFs, graphical TOC, and PDFs;
3. obtain hash-bound external visual, semantic-citation, and scientific QA;
4. include the external QA receipt as a regular bundle file, not as content in the graphical TOC;
5. write the exact-coverage checksum manifest last as the sole complete bundle inventory;
6. run `scripts/audit_submission_bundle.py` and store its JSON outside the bundle;
7. require exit `0`, `structural_machine_audit: PASS`, external-QA status `CURRENT_APPROVED`, `verdict: SUBMISSION_READY`, and `publication_eligibility: ELIGIBLE` as pre-promotion evidence; exit `1` means a structural gate failed or is unverified, and exit `2` means structural gates passed without current approved QA;
8. promote under exclusive access from same-filesystem staging: use a genuinely atomic directory operation only where supported, otherwise use a guarded recoverable transaction; report rather than delete user Office locks, preserve the prior verified bundle on any promotion or rollback failure, and withhold `submission-ready` until the completed destination is rechecked;
9. write the external publication receipt.

Treat `review-ready`, `submission-ready`, `journal-submitted`, Git source publication, PyPI release, and GitHub asset publication as separate evidence-backed statuses. The automated structural audit never proves readability or scientific correctness.

## Completion contract

Report:

- authoritative bundle and component paths using run-relative paths rather than workstation-specific paths;
- final artifact and checksum-manifest hashes;
- recovery-preimage and numerical-evidence manifest references;
- major main/SI/workbook/figure/graphical-TOC/reference changes;
- authorized removals and where their exact data remain recoverable;
- figure provider/provenance and actual AI-disclosure disposition;
- source/final-size visual, semantic-citation, scientific, DOCX, TIFF, PDF, and structural checks actually performed;
- current readiness/publication statuses with durable evidence;
- unresolved human facts, scientific limitations, and blockers.

Use the final receipt schema and example in `submission-bundle-workflow.md`. A structural pass without current independent QA is not submission-ready, and submission-ready is not journal-submitted.
