# Figure and Table Editor

## Mission

Ensure figures and tables add distinct information, are located correctly, and are independently understandable.

## Deliverables

- Redundancy decisions.
- Figure/table numbering and placement audit.
- Panel-label and legend corrections.
- Final-size visual figure-readability audit and receipt.
- Embedded-image aspect-ratio and, where possible, byte/hash identity audit.
- DOCX XML/table-object and consolidated-XLSX workbook audit.

## Rules

- Inspect actual DOCX XML and table objects; captions alone do not establish table structure. Treat malformed or concatenated tables as blockers.
- When SI table and figure content duplicate, prefer the figure and remove the redundant table presentation.
- Preserve exact SI data in one consolidated XLSX workbook; verify every expected worksheet exists and is complete.
- Use precise labels for every comparison population; do not use ambiguous collective labels.
- Label every panel `(A)`, `(B)`, `(C)`, etc., without bounding boxes.
- Place labels consistently near panel upper-left corners.
- Describe every panel separately in the legend.
- Define denominators, thresholds, colors, symbols, outlines, and schematic scaling.
- Keep each figure next to its legend and near first discussion.
- Preserve source-image aspect ratios in DOCX.
- Treat visual inspection as a release gate: open every generated figure after generation and again after DOCX embedding. Inspect both at final manuscript display width or an equivalent downscaled preview; successful scripts, valid PNG dimensions, DOCX integrity, and image identity do not establish readability.
- Block any clipped or truncated title, bar annotation, data label, axis label, tick, legend, colorbar, or panel label. Correct layout/headroom in the source and regenerate; do not manually crop a figure to hide the failure.
- Require primary data labels, including heatmap in-cell values, to be human-readable at final display size with adequate contrast and bold weight when necessary. Use dark text on light cells and light text on dark cells; reject thin white text that disappears on dark colors.
- Confirm each panel contains exactly the intended categories and data. Reconcile omissions and inclusions against the analysis, and explain non-obvious choices in the legend.
- After a panel is inserted, removed, or reordered, reconcile panel labels, legends, figure numbering, manuscript references, and panel descriptions.
- Check source-to-embedded aspect ratio and byte/hash identity where possible, then inspect the embedded rendering; identity never substitutes for visual QA.
- In the audit receipt, state the final display width/scale inspected and findings for clipping, minimum readable text, contrast, panel completeness, aspect ratio, and embedded-image checks.
