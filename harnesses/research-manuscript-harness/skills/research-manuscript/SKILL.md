---
name: research-manuscript
description: "Apply a reusable publication-level protocol for converting research outcomes into a coherent manuscript, revising DOCX or Markdown drafts, auditing abbreviations and citations, organizing methods and results, improving figures and legends, and enforcing journal formatting. Use for manuscript writing, substantive revision, or final editorial QA."
---

# Research Manuscript Protocol

Use this protocol when research outputs must become a submission-ready manuscript or when an existing manuscript requires scientific and editorial revision.

## Required Reference

Load `references/manuscript-author-review-guidelines.md` before editing. Those rules are authoritative unless the user or target journal supplies a stricter requirement.

## Core Principles

1. Write for a reader encountering the project for the first time.
2. Preserve the scientific claim boundary; improve prose without strengthening unsupported claims.
3. Treat the abstract and main body as separate abbreviation scopes.
4. Explain cited work rather than listing citations.
5. Make methods reproducible without turning them into click-by-click tutorials.
6. Order Results by evidential dependency: data status and selection logic before downstream benchmarks.
7. Use figures only when they add information beyond tables.
8. Require unboxed parenthesized panel labels and panel-specific legends.
9. Keep Discussion synthetic and Conclusions bounded.
10. Verify the final file, not only the source text used to generate it.

## Workflow

### 1. Intake and evidence map

- Identify the authoritative manuscript file and Supporting Information.
- Identify current result tables, figures, model definitions, scripts, and release metadata.
- Record target-journal formatting requirements.
- Preserve user-authored comments and tracked decisions until each is applied.

### 2. Scientific structure audit

Audit the manuscript in reading order:

- title and author block,
- abstract,
- Introduction,
- Materials and Methods,
- Results,
- Discussion,
- Conclusions,
- data/code availability,
- Supporting Information statement,
- references.

Map each claim to current evidence. Flag stale values, duplicated reasoning, missing methodological definitions, unsupported generalization, and results presented out of causal order.

### 3. Revision

- Expand literature context enough to explain the cited work.
- Define exact data construction, overlap removal, similarity rules, thresholds, folds, and leakage controls.
- Split unrelated analyses into subsections.
- Put model-selection evidence before performance claims.
- Report negative and failed experiments when they determine the final panel.
- Rewrite Discussion to interpret rather than repeat Results.

### 4. Figure and table pass

- Remove redundant figure/table pairs.
- Place figures with legends near first discussion.
- Label every panel `(A)`, `(B)`, `(C)`, etc., without bounding boxes.
- Describe each panel separately in the legend.
- Verify denominators, thresholds, colors, outlines, and schematic caveats.
- Preserve source-image aspect ratios in DOCX.

### 5. Language and reference pass

- Define abbreviations independently in the abstract and main body.
- In each scope, write the full term before the abbreviation at first use.
- Verify first-citation order and reference-number continuity.
- Standardize terminology, capitalization, hyphenation, symbols, and precision.

### 6. Formatting pass

Apply the journal contract. The default author contract in the reference requires:

- double-line spacing throughout,
- plain text without bold, italics, underlining, or colored type unless required,
- all text black,
- headings distinguished by size and placement,
- centered author and affiliation block,
- figures and captions colocated.

### 7. Verification

Before completion, verify:

- all user comments are removed only after application,
- all tables and images remain present,
- image aspect ratios match embedded source images,
- figure/table numbering is sequential,
- multi-panel captions cover every panel,
- abstract and main-body abbreviation audits pass independently,
- references appear in first-citation order,
- document formatting matches the journal contract,
- the final DOCX opens and parses successfully.

## Output Contract

Report:

- authoritative output path,
- major structural changes,
- figure/table changes,
- abbreviation/reference corrections,
- formatting checks,
- verification actually performed,
- unresolved submission metadata or evidence limitations.

Never describe partial or unverified work as submission-ready.
