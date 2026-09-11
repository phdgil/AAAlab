# Manuscript Author Review Guidelines

Apply these editorial and scientific rules together with `submission-bundle-workflow.md`. The workflow governs package authority, provenance, staging, audits, and release; this document governs manuscript and supporting-content quality. Explicit user instructions and stricter official journal rules override defaults when the override is recorded.

## Scientific writing and claim boundaries

- Write for readers encountering the work for the first time and reading from beginning to end.
- Use publication-level scientific prose, not classroom explanation, lab notes, a development diary, or a technical changelog.
- Explain what relevant prior studies did, what their evidence established, and how the present work differs.
- Remove duplicated reasoning, vague claims, and sentences that do not advance the argument.
- Use the narrowest claim supported by current immutable evidence. Never turn consistency, inspectability, association, or a constructed comparison into independent, mechanistic, causal, or clinical validation.
- Give scientific rationale for consequential data, representation, cutoff, fold, candidate-selection, comparison, and external-source choices.
- Prefer precise phrases such as `pre-established`, `released`, `unchanged`, or `fixed before evaluation` when describing evaluation choices. Reserve “frozen data” for the workflow state that prohibits recomputation during figure rendering. Do not use `membership` when annotations can overlap.

State these distinctions accurately whenever they apply:

- Positive-only data lack verified negatives and cannot alone estimate discrimination, specificity, or false-positive behavior. Verified negatives support those estimates only for the documented sampling regime.
- Source-overlap consistency describes a comparison whose source, selection, or overlap rules do not establish independence from model construction. It does not imply that every retained record overlaps, and it is not independent external validation.
- A full-fit result and a held-out result answer different questions; never label training-inclusive performance as held-out.
- Adding a component to an unchanged baseline is fixed-baseline augmentation, not end-to-end refitting.
- Deterministic hash-based folds are not scaffold-disjoint unless scaffold grouping and separation were explicitly performed and checked.

## Human-supplied declarations

Obtain, rather than infer:

- author names and order;
- affiliations and corresponding-author details;
- funding sources, grant identifiers, and funder roles;
- COI or competing-interest declarations;
- permissions, prior-submission facts, and repository/accession facts;
- actual generative-AI use and the journal-required disclosure wording.

Use placeholders or an unresolved-input register during drafting. Missing or unconfirmed facts block `submission-ready`. Do not publish private correspondence as proof.

## Abbreviations and terminology

- Treat the abstract and the main body as independent abbreviation scopes.
- At first use in each scope, write the full term followed by the abbreviation in parentheses.
- Expand a table abbreviation if preceding text in the relevant document does not define it.
- Do not invent expansions for product, database, software, algorithm, repository, or other proper names.
- Reconcile terminology, capitalization, hyphenation, symbols, units, category names, and precision across main text, SI, captions, workbook, cover, graphical TOC, TIFF names, and PDFs.
- Apply approved renames through one complete old-to-new map. Check captions, cross-references, sheet names, title cells, formulas, defined names, links, and file names at the same gate; do not leave mixed nomenclature.

## Introduction and literature

- Make literature context understandable without requiring retrieval of every citation.
- Describe relevant approaches, inputs, outputs, evidence, and limitations.
- Avoid a novelty paragraph that merely repeats the objective.
- End with explicit research questions and a bounded contribution.
- Audit citation numbering and citation meaning separately:
  - numbering follows first textual citation and remains continuous;
  - source content and topic must support the nearby proposition at the strength claimed.
- A correct number does not establish semantic support, and a relevant paper does not establish correct numbering. Never invent bibliographic details.

## Materials and Methods

- Provide enough detail to reproduce the analysis without website-navigation instructions.
- Name authoritative data sources, versions or access dates when required, category hierarchies, and inclusion/exclusion rules.
- State structure acquisition, parsing, standardization, canonicalization, exact-overlap handling, and deduplication.
- Define representations, descriptors or fingerprints, parameters, metrics, cutoffs, folds, and leakage controls.
- Explain positive and comparison sets, whether negatives are verified or constructed, unresolved inputs, and annotation overlap.
- Explain the rationale for descriptor selection, near-positive filtering, numerical cutoffs, fold construction, candidate caps, external-search breadth, and databases selected for direct assessment.
- Describe graph or pattern discovery with its nodes, edges, ranking/restart parameters, fold design, and leakage controls when used.
- Distinguish hash assignment from scaffold-disjoint splitting; report only the separation actually implemented.
- Distinguish full-fit, held-out, fixed-baseline augmentation, and end-to-end training in both method and result labels.
- State when a method was evaluated for every category; reserve category-specific adoption decisions for Results.
- Split genuinely distinct analyses into subsections.
- Main Methods cover analyses reported in main Results. SI methods add the detail needed to reproduce SI-only or expanded analyses rather than repeating main Methods verbatim.

## Results

- Present evidence in the order that determines later decisions.
- Put data status, overlap, specificity, filtering, and model-selection evidence before dependent benchmark or application claims.
- Explain why each candidate was retained, merged, rejected, or failed when that decision shapes the final method.
- Present comparison-set construction before interpreting performance.
- Separate primary benchmarking, held-out evaluation, positive-only checks, source-overlap consistency, external assessment, specificity, uncertainty, rebuilding, and augmentation.
- Label denominators, sampling regimes, split types, baselines, and uncertainty explicitly.
- Report failed candidates or promotion experiments when they bound the chosen panel or claim.
- Move an analysis between main and SI with its method, result, and necessary limitations together.
- Integrate secondary category validation into the relevant category-validation subsection instead of creating a disconnected narrative.
- Balance the abstract around the central final outcomes; do not let an auxiliary method dominate.
- Keep a comparator that measures a different construct in SI when it would confuse the main claim, and explain the mismatch.

## Discussion and Conclusions

- Synthesize rather than replay Results.
- Distinguish domain overlap from analytical specificity and source-overlap consistency from independent validation.
- Interpret performance only within the documented positive/negative sampling and split regime.
- Separate inspectability from mechanistic or causal explanation.
- State what positive-only evidence can and cannot establish.
- Discuss annotation dependence, constructed negatives, unresolved structures, operational thresholds, source overlap, data leakage controls, and important untested alternatives.
- End Conclusions with the narrowest defensible claim.

## Main manuscript and Supporting Information

- Keep the main manuscript focused on the discovery, essential method, central evidence, and bounded significance.
- Use SI for explanatory depth, reproducibility details, expanded methods, sensitivity analyses, secondary checks, and supporting figures/tables.
- Do not repeat full main-text methods, result paragraphs, or tables in SI. Use concise cross-references and add information.
- Keep main/SI claims, names, numbers, captions, citations, figure/table identifiers, workbook sheet references, and availability statements synchronized.
- Preserve exact underlying values in supporting artifacts even when prose or displayed tables use journal-appropriate rounding.

## Workbook and reader-facing tables

- Inventory every sheet and table before changing the workbook or removing a document table.
- Record `keep`, `merge`, `move`, or `remove-from-reader-view`, with rationale, dependencies, and the user's authorized deletion scope.
- Do not retain every reader-facing table by default, and do not delete a duplicated table merely because an automated comparison flags it. Removal requires scientific judgment and authority.
- Order substantive workbook sheets by first main-manuscript citation, then first SI citation. Put data dictionaries, provenance, and audit material after substantive sheets; do not lead with a version history.
- Preserve raw full precision, formulas, structure expressions such as SMARTS, units, missing-value meaning, source identifiers, and row-level provenance in retained artifacts and in the external recovery archive.
- Never replace formulas with rounded display values as an editing shortcut.
- After renaming or reordering, verify sheet names, title cells, formulas, defined names, links, charts, and every main/SI citation together. Broken or stale references block release.

## Figures, graphical TOC, and TIFFs

- Use a figure only when it adds information beyond a table. Audit redundant figure/table pairs; merge or remove reader-facing duplication only within authorized scope while preserving exact data.
- Freeze and hash numerical inputs before figure work. Rendering may change visual presentation, not scores, fits, filters, folds, thresholds, categories, or derived values.
- Honor the requested generation provider. If Codex CLI image generation is requested, use the real installed image-generation capability and record its actual provenance. Do not fake generation, use a placeholder, or silently substitute a code-drawn graphic.
- Verify every displayed number, denominator, unit, category, label, color, outline, symbol, icon meaning, and evidence boundary against authoritative data.
- Decorative or generated icons must not imply an unmeasured mechanism or outcome. Verify permission/license obligations.
- Record actual AI assistance and obtain human approval for the required disclosure; never assume either use or non-use.
- Label panels `(A)`, `(B)`, `(C)`, and so on without boxes; place labels consistently and describe every panel separately in the legend.
- Reconcile panel labels, captions, legends, numbering, text references, workbook values, and filenames after insertion, removal, or reordering.
- Treat the journal's graphical TOC as a raster or DOCX container, never as the package file inventory. Replace it and, for a DOCX container, its authoritative embedded image; confirm the OOXML relationship renders the new graphic and no obsolete preview remains.
- Map every captioned main/SI figure to exactly one required submission TIFF, with no missing or orphan figure identifier. Audit the configured graphical TOC separately; it is not implicitly a figure-TIFF entry. The checksum manifest, not the graphical TOC, is the complete bundle inventory.
- Preserve native pixel dimensions and aspect ratio. Produce RGB/LZW TIFF when required without stretching, resampling, or treating a changed DPI tag as added resolution. Regenerate from the authoritative source if native pixels are insufficient at final size.

## Visual QA

- Open every source image at native size and inspect every embedded DOCX and PDF rendering at final physical display size or an equivalent downscaled preview.
- Check clipping and headroom for titles, annotations, data labels, axes, ticks, legends, colorbars, and panel labels.
- Check the smallest meaningful text, contrast, icon legibility, line weights, panel completeness, category inclusion, and aspect distortion.
- Check final table pagination, repeating headers, row splits, captions, page/section breaks, and proximity of figures to legends.
- Record display width/scale and concrete findings. Source validity, package parsing, hashes, pixel dimensions, and DPI metadata alone do not establish readability.
- Require an independent reviewer for final visual readability and scientific correctness. The final producer cannot self-certify.
- Bind final review to `aaalab.external-qa/v1`: reviewer `identity`, `role`, descriptive `independence_basis`, timezone-bearing `reviewed_at`, top-level `outcome: approved`, approved `visual_readability`, `citation_semantics`, and `scientific_review` outcomes, an empty `unresolved_blockers` array, and current hashes for main DOCX, SI DOCX, workbook, graphical TOC, and every TIFF discovered under `--tiff-dir`. The machine cannot authenticate the reviewer or scientific claims.

## DOCX fidelity and authorized formatting

- Preserve author paragraph/run styles and direct formatting, character styles, field codes, bookmarks, comments, tracked-change state, section/page/column breaks, and intentional blank paragraphs outside the approved edit.
- Preserve table grid/cell properties, widths, header repetition, row splitting or `cantSplit`, `keep with next`, `keep lines together`, and intentional pagination.
- Edit the smallest XML/text/relationship scope possible. Do not reconstruct the document from extracted plain text.
- Accept/reject changes, remove comments, flatten fields, or normalize styles only when explicitly authorized.
- When normalization is authorized, name the exact target styles/properties and compare all non-target XML properties before and after.
- Apply the journal's actual format. In the absence of a supplied requirement, report formatting as unresolved rather than imposing a project-specific template.
- Verify actual DOCX table objects against the approved final inventory. Captions do not prove that tables exist; removed tables are acceptable only when their recorded authorized disposition and retained data are verified.

## Cover letter

- Lead with the research question and principal discovery, then the strongest evidence, bounded significance, and journal fit.
- Keep it concise and reader-facing.
- Include declarations only when required and human-confirmed.
- Do not use the cover letter as a project timeline, version history, implementation log, technical changelog, or exhaustive methods appendix.

## Final checklist

- [ ] One authoritative bundle, one executable plan, and an append-only steering log were used.
- [ ] Latest author edits were reconciled before verified preimages were archived outside the bundle.
- [ ] Numerical evidence was hashed and kept immutable, separately from document recovery preimages.
- [ ] Main and SI are complementary rather than repetitive.
- [ ] Every claim and displayed number maps to authoritative evidence.
- [ ] Positive-only/verified-negative, overlap/external, full-fit/held-out, fixed-baseline/end-to-end, and hash/scaffold distinctions are accurate where applicable.
- [ ] Citation numbering and semantic topic support passed separate reviews.
- [ ] Workbook sheets/tables have authorized keep/drop decisions and main-citation-first substantive order.
- [ ] Full precision, formulas, SMARTS or equivalent structures, and provenance remain in retained artifacts and recovery.
- [ ] Old-to-new mappings were reconciled across main, SI, captions, workbook title cells/formulas, filenames, graphical TOC, and PDFs.
- [ ] Figure generation used frozen data and the requested real provider without recomputation or substitution.
- [ ] Numbers, evidence scope, icons, permissions, and actual AI disclosure were verified.
- [ ] The configured graphical TOC raster or DOCX container is current; any authoritative embedded graphic is current and renders without distortion.
- [ ] RGB/LZW figure-TIFF inventory exactly covers captioned main/SI figures at native pixels and undistorted aspect ratio; the graphical TOC was audited separately.
- [ ] Author XML styling, breaks, and table pagination were preserved except for explicit authorized normalization.
- [ ] Source and final-size embedded/rendered visual QA passed with an independent reviewer.
- [ ] Human-confirmed author, funding, COI, permissions, and AI facts are complete.
- [ ] Fresh PDFs, external QA receipt, and checksum manifest were generated only after preimage verification and final artifact changes.
- [ ] Automated structural audit and external `visual_readability`, `citation_semantics`, and `scientific_review` outcomes are reported as distinct evidence.
- [ ] The checksum manifest—not the graphical TOC—is the complete bundle inventory, and the external QA receipt is manifest-covered rather than inserted into the graphic.
- [ ] The `aaalab.submission-bundle-audit/v1` result is interpreted exactly: exit `0` is structural `PASS` plus `CURRENT_APPROVED` QA and `ELIGIBLE`; exit `1` is structural `FAIL`/`UNVERIFIED`; exit `2` is structural `PASS` without current approved QA.
- [ ] `review-ready`, `submission-ready`, `journal-submitted`, Git source, PyPI, and GitHub-assets statuses are not conflated.
