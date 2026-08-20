# Trigger Tests

## Should use the full harness

1. “Read this complete manuscript, reconcile it with current results, revise every section, repair figures, and deliver a journal-formatted DOCX.”
2. “Apply all author comments globally, audit citations and abbreviations, and perform independent final QA.”
3. “Convert these research outputs into a manuscript with figures, tables, Supporting Information, and a submission-ready Word file.”
4. “Plan the revisions, apply every `{...}` comment locally and globally, move SI-only analyses with their methods, repair missing tables, and consolidate all supporting tables into one workbook.”

## Should use only the protocol or answer directly

1. “What is the difference between Results and Discussion?”
2. “Rewrite this one sentence.”
3. “How should AUC be introduced as an abbreviation?”

## Must stop or clarify

1. Multiple candidate manuscripts exist and the authoritative file is unknown.
2. Numerical claims cannot be mapped to current artifacts.
3. The target journal formatting contract is required but unavailable.
4. DOCX verification is requested but `python-docx` is missing.
5. The manuscript is locked and the authoritative file cannot be replaced safely.

## Acceptance scenarios

- Abstract and main body independently define abbreviations.
- Figure panels use unboxed `(A)`, `(B)`, `(C)` labels and legends describe each panel.
- A redundant AUC figure is removed when the same values are already in a table.
- A baseline that measures a different construct is moved to Supporting Information with a stated rationale.
- Missing or concatenated DOCX table objects block completion even when table captions are present.
- Redundant SI tables are removed from the DOCX, their figures remain, and exact values are preserved in one XLSX workbook with complete, clearly named sheets.
- A large source PNG passes dimension and aspect-ratio checks but is blocked because bar annotations are clipped or heatmap values become unreadable at final manuscript width.
- A heatmap is regenerated with larger, high-contrast, bold in-cell values, and the embedded DOCX rendering is inspected again at the recorded display width.
- Figure panels include only the intended categories; any non-obvious omission is reconciled with the analysis and explained in the legend.
- Positive-only external sources are described as concordance or recovery evidence, not full external classification validation.
