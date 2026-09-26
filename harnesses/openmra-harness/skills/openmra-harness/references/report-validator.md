# Report Validator

Inspect one completed OpenMRA report and its source workbook without controlling the GUI.

## Contract

- Use `scripts/report_validation.py`.
- Open OOXML packages read-only and do not save either workbook.
- Check ZIP/package integrity, workbook relationships, expected report structure, input/report identity evidence, and numeric result cells.
- Emit a deterministic JSON verdict to the requested path or standard output.
- Treat missing, stopped, or unattainable ECx levels as explicit report facts; do not synthesize values.

The verdict covers report integrity and traceability. It must not state that CA/IA predictions are scientifically valid, that SigmaPlot and OpenMRA equations are equivalent, or that an EC50 definition is correct without a separate domain review.
