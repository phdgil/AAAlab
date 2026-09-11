---
name: research-manuscript-harness
description: "Coordinate a multi-agent research-manuscript workflow from evidence and author edits through one submission bundle: main/SI, workbook, cover, graphical TOC, figure TIFF/PDF assets, references, provenance, structural audit, independent QA, and guarded publication. Load research-manuscript as the protocol source."
metadata:
  protocol_skill: "research-manuscript"
  protocol_relative_path: "../research-manuscript/SKILL.md"
---

# Research Manuscript Harness

This harness coordinates specialist producers and reviewers around the sibling `research-manuscript` protocol. It can create or revise a manuscript, but its complete unit of work is one coherent submission package whose editable documents, data, images, derivatives, references, declarations, provenance, and receipts agree.

## Execution Mode

Use producer-reviewer gates with the main agent as orchestrator.

- The orchestrator owns the authority ledger, the only executable plan, scope decisions, cross-artifact integration, invalidation, and final promotion.
- Use read-only agents for evidence, methods, citation, abbreviation, package, and final QA audits.
- Give editing agents explicit file boundaries only after authority, external preimages, and immutable evidence are established.
- Keep numerical analysis separate from figure rendering. Rendering uses fixed hashed inputs and never rescoring/refitting.
- Require an independent reviewer after the last content or asset edit. A producer never approves its own submission-ready result.
- Use the protocol directly for a bounded single-artifact issue; use this roster when dependencies span artifacts or independent lanes materially reduce risk.

Create one evidence-grounded `run-plan.json` before mutation. A user steer updates that plan and appends one decision-log entry; it does not produce a second plan or duplicate already completed work. Invalidate and rerun only affected downstream gates.

## Context Preservation Boundary

Manuscript work and harness maintenance are separate workflows.

- Never build, package, validate, or publish this harness inside an active manuscript run.
- If the user asks to convert manuscript lessons into reusable harness rules, hand a generalized rule set and compact requirement receipt to an isolated worktree, separate session, or bounded subagent.
- Keep manuscript evidence, private numerical decisions, author identities, unresolved comments, correspondence, credentials, workstation paths, and session artifacts in the manuscript context.
- Public harness files contain no private case data. Return only changed harness paths, validation evidence, publication identifiers/links, and material limitations to the manuscript context.

## Protocol Source

Before execution, load:

1. sibling skill `$research-manuscript`, normally `../research-manuscript/SKILL.md`;
2. `../research-manuscript/references/submission-bundle-workflow.md` as the authoritative package workflow;
3. `../research-manuscript/references/manuscript-author-review-guidelines.md` as the editorial/scientific rule set;
4. target-journal instructions supplied by the user or obtained from the official journal source;
5. project-local evidence, author-edited documents, figures, tables, workbooks, and release metadata.

User instructions and official journal requirements override defaults. Record the source, exact scope, and affected gates for every override.

## Agent Roster

Preserve these specialist roles; use only the subset needed for the assigned scope.

| Role file | Purpose | Required output |
|---|---|---|
| `references/agents/evidence-structure-editor.md` | Establish bundle inventory and claim/evidence structure; audit cover/SI organization and redundancy | bundle inventory/evidence map, cover/SI decisions, ordered bundle revision plan |
| `references/agents/methods-reproducibility-auditor.md` | Audit data construction, thresholds, fits, folds, comparisons, leakage controls, and reproducibility boundaries | methods/evidence audit with blockers and exact supporting artifacts |
| `references/agents/abbreviation-citation-auditor.md` | Audit independent abstract/main terminology, citation order, and source-topic support | language audit plus separate numbering and semantic-citation findings |
| `references/agents/figure-table-editor.md` | Reconcile figures, tables, workbook dependencies, graphical TOC, figure TIFFs, and final-size rendering | synchronized dependency audit, matched RGB/LZW figure-TIFF inventory, source/rendered visual-QA receipt |
| `references/agents/docx-format-auditor.md` | Verify final DOCX structure and preservation of styles, breaks, tables, pagination, fields, and image geometry | structural/format audit against approved inventory and journal rules |
| `references/agents/qa-reviewer.md` | Independently review the complete final bundle | fail-closed verdict, readiness label, blocking-fix list, submission-bundle QA receipt |

Do not let specialists cascade tasks or silently widen scope. Every handoff includes `run_id`, `bundle_id`, input hashes, recovery/evidence manifest hashes, one assigned plan step, allowed paths, required output, blockers, and the downstream gate invalidated by a change. Producers return before/after hashes and evidence; reviewers do not repair their own findings.

## Workspace Layout

Create one run directory unless the user supplies an equivalent layout:

```text
submission_run_<project>_<run-id>/
  recovery/                 # verified preimages, outside the bundle
  evidence/                 # immutable numerical/source evidence
  work/                     # renders and conversions
  reviews/                  # durable review evidence and audit JSON
  run-plan.json             # the only executable plan
  decision-log.jsonl        # steering/authorization log
  publication-receipt.json  # final receipt outside bundle
  bundle.stage/             # the only candidate submission bundle
```

Never place recovery preimages in the submission package. Never create competing `final` directories. Promote `bundle.stage/` only after all applicable gates pass.

## Workflow

### Phase 0: Context and authority

The orchestrator:

- reads official journal instructions and inventories main/SI/workbook/cover/graphical-TOC/figure-TIFF/PDF/reference candidates;
- establishes actual authority and reconciles latest author edits by content and provenance, not filename or timestamp alone;
- inventories comments and tracked changes without accepting, rejecting, or deleting them implicitly;
- records exact authorized mutation and table-removal scope;
- obtains or lists as blockers the human facts for authorship, funding, COI, permissions, submission history, and actual AI use;
- identifies the requested figure provider, including Codex CLI image generation when specified;
- makes and hash-verifies recoverable preimages outside the bundle;
- freezes and hashes numerical evidence, full-precision data, formulas, SMARTS or equivalent structures, scripts, and provenance separately;
- writes the single executable plan with owners, dependencies, checks, and invalidation rules.

Gate: authority, recovery, evidence, journal contract, human-fact blockers, and allowed scope are unambiguous before any editor mutates a file.

### Phase 1: Evidence, methods, and dependency map

Run the evidence-structure editor and methods-reproducibility auditor in parallel for non-trivial work. Merge their findings into one orchestrator-owned map; do not let either agent create another plan.

Required coverage:

- every numerical claim maps to current immutable evidence;
- main and SI form a complementary narrative rather than repeat one another;
- retained, merged, rejected, and failed candidates are explained where decision-relevant;
- Methods disclose data construction, exclusions, representations, thresholds, fits, folds, and leakage controls with scientific rationale;
- positive-only versus verified-negative, source-overlap versus external validation, full-fit versus held-out, fixed-baseline augmentation versus end-to-end, and hash-fold versus scaffold-disjoint claims are accurate;
- source-overlap consistency denotes a comparison whose source, selection, or overlap rules do not establish independence from model construction; it does not imply that every retained record overlaps;
- a simultaneous `old -> new` ledger covers main/SI text, captions, fields, workbook sheets/title cells/formulas/defined names, cover, graphical TOC, filenames, TIFF/PDF derivatives, and checksum-manifest entries;
- downstream artifacts and gates invalidated by each planned change are explicit.

Gate: stale or contradictory claims and incomplete rename dependencies block mutation.

### Phase 2: Coordinated manuscript and SI revision

The main editor applies approved changes in reading order while SI is revised as complementary explanatory/reproducibility material. Preserve author XML run/paragraph properties, styles, fields, breaks, tracked-change state, comments, and table pagination outside the authorized scope. Do not reconstruct DOCX files from plain text or normalize formatting unless exact properties are approved.

Literal brace comments and other instructions remain until their local and global applications are complete. A moved analysis carries its method, result, limitation, citation, figure/table, and workbook references with it.

Gate: the latest author content survives; main/SI claims and identifiers agree; no unsupported claim strengthening or duplicated explanatory block remains.

### Phase 3: Workbook keep/drop and synchronized mappings

The evidence-structure editor proposes and the orchestrator approves an occurrence-level audit for each sheet and reader-facing table: `keep`, `merge`, `move`, or `remove-from-reader-view`, with rationale, dependencies, and explicit removal authority.

- Order substantive sheets by first citation in the main manuscript, then first SI citation; place data dictionary/provenance/audit sheets after substantive content.
- Remove unnecessary duplicate reader-facing tables only inside authorized scope.
- Preserve raw full precision, formulas, SMARTS or equivalent patterns, units, identifiers, and row provenance in retained artifacts and the external recovery archive.
- Apply every approved old-to-new name simultaneously across sheet names, title cells, formulas, defined names, main/SI citations, captions, and links; broken or stale references block the gate.

Gate: every sheet/table has a disposition, no unauthorized data loss occurred, and retained computational content remains reproducible.

### Phase 4: Reference and language gates

The abbreviation-citation auditor performs independent checks for:

1. abstract and main-body abbreviation/terminology scopes;
2. first-citation numbering, continuity, ranges, and bibliography pairing;
3. source-level semantic and topic support for each cited proposition.

Renumbering is not semantic verification. Topic similarity is not proof that a source supports the claim strength. Unresolved sources block readiness.

### Phase 5: Figure, graphical-TOC, and TIFF gate

The figure-table editor works only from frozen, hashed data. It may correct presentation but never rescore, refit, resample, change thresholds, reassign folds, or recompute results. A numerical defect returns to Phase 1 with a new authorized evidence version and invalidates downstream work.

Honor the actual requested generator/provider. For requested Codex CLI image generation, invoke its real installed capability and record actual provider/tool provenance. Unavailable generation is a blocker; a fake call, placeholder, or silent code-drawn substitute is prohibited.

Required reconciliation:

- exact numbers, categories, denominators, units, evidence scope, labels, icons, permissions, panels, captions, and actual AI-use facts;
- the journal's graphical TOC is treated as a raster or DOCX-container deliverable, never as a package file inventory; when it is a DOCX container, its authoritative embedded image is replaced and rendered for inspection;
- every captioned main/SI figure maps to exactly one figure TIFF, with no missing/orphan TIFF identifier; the graphical TOC is audited separately and is not implicitly part of this mapping;
- required TIFFs preserve native pixels and aspect ratio and use RGB/LZW without treating a DPI tag as additional resolution;
- source images, the graphical TOC, and final-size DOCX/PDF renderings receive recorded visual inspection.

Gate: figure/table/workbook dependencies agree, graphical-TOC replacement is real, the captioned-figure TIFF inventory is exact, and independent visual findings have no blockers.

### Phase 6: Cover, formatting, and rendered derivatives

Write a concise discovery-first cover letter centered on the research question, finding, strongest evidence, bounded significance, and journal fit. Exclude timelines, version histories, implementation narration, and technical changelogs.

The DOCX format auditor checks the journal contract and approved object inventory, using `scripts/audit_docx.py` where its focused checks apply. Coverage includes preserved run/paragraph properties, fields, breaks, tables, pagination, and image geometry. It does not require every preimage table to remain; it requires every final keep/drop decision to be authorized, represented correctly, and recoverable.

Render PDFs only after editable artifacts are final. Compare DOCX and PDF at final physical display size for pagination, tables, captions, equations, fonts, links, contrast, clipping, panel completeness, and distortion. Any later source edit invalidates its PDF.

### Phase 7: Human fact gate

Obtain human evidence for author order/details, funding, COI, permissions, prior/submitted status, and actual AI disclosure. Do not infer a declaration from manuscript content or tool logs. Store only a durable evidence reference in public-safe receipts, not private correspondence.

Gate: unresolved required facts block `submission-ready` even when files parse and hashes match.

### Phase 8: Independent QA

The independent QA reviewer reads and renders the final bundle from beginning to end. The verdict is fail-closed and includes:

- exact claim/number/method/reference mismatches and supporting evidence;
- separate semantic-citation and numbering findings;
- main/SI/workbook/cover/graphical-TOC/TIFF/PDF/checksum-manifest consistency;
- source-native and final-size embedded/rendered visual evidence;
- DOCX style/break/table-pagination preservation and approved removal dispositions;
- human metadata/disclosure blockers;
- `BLOCK`, `review-ready`, or `submission-ready` recommendation, never `journal-submitted` without submission evidence.

The reviewer produces the `aaalab.external-qa/v1` receipt described in the authoritative workflow. It requires reviewer `identity`, `role`, and a descriptive `independence_basis`; timezone-bearing `reviewed_at`; top-level `outcome: approved`; approved `visual_readability`, `citation_semantics`, and `scientific_review` scope outcomes; an empty `unresolved_blockers` array; and exact current hashes for main DOCX, SI DOCX, workbook, graphical TOC, and every TIFF discovered under `--tiff-dir`. Preserve detailed review evidence separately in the run records. The auditor cannot authenticate reviewer identity or scientific claims; booleans and machine-check success are not proof.

Gate: fix every blocker, then repeat every invalidated review against new hashes.

### Phase 9: Manifest, automated structural audit, and guarded publication

After verified preimages, final content, fresh PDFs, and current external QA:

1. include the external QA receipt as a regular bundle file; do not place an inventory or receipt listing in the graphical TOC;
2. generate the exact-coverage checksum manifest last as the sole complete package inventory, excluding only itself and Office `~$*` lock files;
3. run `scripts/audit_submission_bundle.py` with the exact interface in `submission-bundle-workflow.md`;
4. store audit JSON outside the bundle to avoid a hash cycle;
5. require exit `0`, `structural_machine_audit: PASS`, external-QA gate `CURRENT_APPROVED`, `verdict: SUBMISSION_READY`, and `publication_eligibility: ELIGIBLE` as pre-promotion evidence, not yet as the published destination's readiness label; exit `1` means structural `FAIL` or `UNVERIFIED`, while exit `2` means structural `PASS` without current approved QA;
6. promote under exclusive access from same-filesystem staging, using a genuinely atomic directory operation only where supported; otherwise use the verified rollback transaction defined in `submission-bundle-workflow.md`, report rather than delete user Office locks, preserve the prior verified bundle on any promotion or rollback failure, and apply `submission-ready` only after rechecking the completed destination;
7. write the external publication receipt from the authoritative schema/example.

This is an automated structural audit, never automated scientific verification.

Keep statuses separate and evidence-backed:

- `review-ready` has no unresolved content, scientific, citation, or visual blocker after independent review but may have explicit submission-only blockers;
- `submission-ready` satisfies all package, human, external-QA, and current-hash gates;
- `journal-submitted` requires durable evidence of actual transmission/receipt;
- Git source, PyPI, and GitHub release assets each require their own publication evidence and do not imply one another or journal status.

Do not perform an external submission or release without user authorization and the required credentials.

## Deliverables

- one authoritative staged or fully verified promoted submission bundle;
- coordinated main manuscript, SI, workbook, cover, graphical TOC, figure TIFFs, PDFs, references, checksum manifest, and external QA receipt as journal-required;
- recovery-preimage and immutable-evidence manifests outside the bundle;
- the single executable plan and append-only decision log;
- producer/reviewer receipts with final hashes and durable evidence;
- external structural-audit JSON and final publication receipt;
- explicit unresolved facts, limitations, and separately evidenced readiness/publication statuses.

## Test Scenarios

See `references/trigger-tests.md`. The harness must distinguish narrow editorial questions from artifact mutation and full-package preparation, fail closed on ambiguous authority or missing evidence, preserve the context-isolation boundary, and never equate a machine pass with scientific or submission approval.
