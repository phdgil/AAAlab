---
name: research-manuscript-harness
description: "Coordinate a multi-agent research-manuscript harness for evidence mapping, scientific editing, methods reproducibility, abbreviation and citation auditing, figure and table QA, DOCX formatting, and independent final review. Use for real manuscript creation or substantive revision; load research-manuscript as the protocol source."
metadata:
  protocol_skill: "research-manuscript"
  protocol_relative_path: "../research-manuscript/SKILL.md"
---

# Research Manuscript Harness

This harness coordinates specialist reviewers around the `research-manuscript` protocol. It converts research outcomes into a coherent manuscript or performs a publication-level revision of DOCX and Markdown drafts while preserving scientific claim boundaries.

## Execution Mode

Use producer-reviewer gates with the main agent as orchestrator.

- Use read-only agents for evidence, citation, abbreviation, and QA audits.
- Use editing agents only after the authoritative manuscript and evidence sources are identified.
- Keep figure generation separate from manuscript prose editing when practical.
- Require an independent QA pass after all edits.
- Require an explicit executable plan before any document mutation, then execute it through final verification in the same run.
- For small single-issue requests, use the protocol directly rather than spawning the full roster.

## Context Preservation Boundary

Manuscript work and harness maintenance are separate workflows.

- Never build, package, validate, or publish this harness inside an active manuscript run.
- If the user asks to convert new manuscript rules into a reusable harness, hand the finalized rule document and a compact requirement receipt to an isolated worktree, separate agent session, or bounded subagent.
- Keep manuscript evidence, numerical decisions, and unresolved author comments in the manuscript context; do not copy the full conversational history into the harness-building context.
- Return only the harness path, validation results, commit identifier, publication link, and material limitations to the manuscript context.

## Protocol Source

Before execution, load:

1. sibling skill `$research-manuscript`, normally at `../research-manuscript/SKILL.md`;
2. `../research-manuscript/references/manuscript-author-review-guidelines.md`;
3. target-journal author instructions supplied by the user or retrieved from the official journal source;
4. project-local evidence, figures, tables, Supporting Information, and release metadata.

User instructions and official journal requirements override defaults. Record every override.

## Agent Roster

| Role file | Purpose | Primary output |
|---|---|---|
| `references/agents/evidence-structure-editor.md` | Map claims to evidence and revise narrative order | evidence map, section revision plan |
| `references/agents/methods-reproducibility-auditor.md` | Audit data construction, thresholds, folds, and leakage controls | methods audit |
| `references/agents/abbreviation-citation-auditor.md` | Audit independent abstract/main abbreviation scopes and citation order | language/reference audit |
| `references/agents/figure-table-editor.md` | Audit redundancy, panel labels, legends, placement, aspect ratios, and final-size visual readability | figure/table audit |
| `references/agents/docx-format-auditor.md` | Audit line spacing, plain black text, headings, author block, tables, and images | format audit |
| `references/agents/qa-reviewer.md` | Independently verify final scientific and document integrity | QA verdict |

Use fewer agents when scope is narrow, but never let the final producer self-approve a submission-ready result.

## Workspace Layout

Create one run directory unless the user supplies one:

```text
manuscript_run_<project>_<timestamp>/
  00_intake/
  01_evidence/
  02_section_audits/
  03_revision/
  04_figures_tables/
  05_formatting/
  06_qa/
  final/
  run_index.json
```

Do not overwrite the only manuscript copy without a recoverable source or version-control state.

## Workflow

### Phase 0: Context and authority

- Identify the authoritative manuscript and Supporting Information.
- Identify whether the current request is creation, substantive revision, formatting-only, or QA-only.
- Read embedded comments, tracked decisions, and project guidance.
- Treat every literal `{...}` comment as an instruction inventory. Map each to its local passage and any global rule before any mutation.
- Record target journal and current author metadata.
- Stop if the manuscript target or evidence source is ambiguous.

Produce an executable plan that names section changes, main/SI moves, evidence artifacts, comment applications, figure/table/workbook actions, and final audits. Do not mutate documents before this plan is complete.

### Phase 1: Evidence and structure audit

Run the evidence-structure editor and methods auditor in parallel when the manuscript is non-trivial.

Required checks:

- all numerical claims map to current artifacts;
- retained, merged, rejected, and failed candidates are explained;
- Results order follows evidential dependency;
- Methods disclose data construction, exclusions, thresholds, folds, and leakage controls;
- methods give scientific rationale for descriptor selection, near-positive filtering intent, numerical cutoffs, fold construction, candidate caps, external-source search breadth, and databases selected for direct assessment;
- main Methods describe analyses reported in main Results, while SI-only analyses have SI methods;
- Discussion does not restate Results or exceed evidence.

Gate: no prose mutation until stale or contradictory claims are mapped.

### Phase 2: Language and reference audit

Run the abbreviation-citation auditor.

Hard requirements:

- abstract and main body are separate abbreviation scopes;
- first use in each scope is `full name (abbreviation)`;
- references follow first-citation order;
- table abbreviations are already defined or expanded;
- proper names do not receive invented expansions.

### Phase 3: Manuscript revision

The main editor applies approved changes section by section:

1. title and author block;
2. abstract;
3. Introduction;
4. Materials and Methods;
5. Results;
6. Discussion;
7. Conclusions;
8. availability and Supporting Information statements;
9. references.

Remove brace comments only after their instruction has been applied locally and, when relevant, globally. Balance the abstract around final scoring-function outcomes for all retained categories; keep auxiliary methods proportionate. Move baselines measuring a different construct to SI with rationale, and integrate secondary validation into the relevant category-validation subsection.

### Phase 4: Figure and table gate

Run the figure-table editor.

Hard requirements:

- no redundant table/figure pair without justification;
- when a table and figure duplicate information, the SI retains the figure and one consolidated supporting XLSX workbook retains exact data on clearly named sheets;
- figures are located with their legends;
- each multi-panel image visibly uses `(A)`, `(B)`, `(C)`, etc.;
- panel labels have no bounding boxes;
- every panel is described separately in the legend;
- source and embedded image aspect ratios match;
- numbering follows first appearance.
- every generated figure and embedded rendering are opened and visually inspected at final manuscript display width or an equivalent downscaled preview; file, dimension, package, or hash checks alone do not pass this gate;
- clipped, truncated, undersized, low-contrast, or panel-incomplete figures block release and must be regenerated with corrected layout/headroom;
- panel labels, legends, numbering, manuscript references, and panel descriptions agree after panel insertion, removal, or reordering;
- the figure QA receipt states display width/scale and findings for clipping, minimum readable text, contrast, panel completeness, and embedded-image checks.

### Phase 5: DOCX formatting gate

Run the DOCX format auditor or equivalent local verification.

Default contract:

- double-line spacing everywhere;
- plain black text;
- no bold, italics, underlining, or colored text unless journal-required;
- author block centered under title;
- all tables and images retained;
- DOCX package opens and parses;
- actual final DOCX table structures are present and distinct; missing, concatenated, or malformed tables block completion, and captions are not table evidence.

### Phase 6: Independent QA

The QA reviewer reads the final manuscript from beginning to end and returns:

- `PASS`, `PASS WITH DISCLOSED LIMITATIONS`, or `BLOCK`;
- exact blocking passages;
- numerical or citation mismatches;
- numerical cross-checks against authoritative CSV, JSON, or XLSX artifacts;
- formatting failures;
- independent audits of abstract/main-body abbreviations, citation order, tables, figures, workbook sheets, formatting, and unresolved comments;
- final-size visual figure checks, including clipping, readable minimum text, contrast, panel completeness, and embedded-image rendering;
- unresolved submission metadata.

A manuscript may be called review-ready only after all blocking items are fixed and the final file is re-audited.

## Deliverables

- revised manuscript in the requested format;
- revised Supporting Information when affected;
- updated figures and legends when affected;
- audit receipt listing checks actually performed;
- unresolved-input list for authors, funding, competing interests, or repository metadata.

## Test Scenarios

See `references/trigger-tests.md`. The harness must distinguish substantive manuscript work from simple grammar questions and must fail closed when authoritative evidence or the target file is unknown.
