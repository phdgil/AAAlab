# Trigger Tests

## Should trigger

- "Run all of these revised RM workbooks in OpenMRA and save the exports in a new folder."
- "Resume the interrupted OpenMRA batch without repeating finished files."
- "Validate that this OpenMRA report belongs to this input workbook and is structurally complete."
- "Regenerate the CA and IA results from the OpenMRA v0.2.0 desktop app."
- "Use OpenMRA-RMFix to export this Logistic4 input with both models."
- "The app was interrupted after exporting a report; resume without overwriting it."
- "Check whether this exported CA/IA workbook contains the selected mixture."
- "Rerun this supported RM input using the same coefficients and save fresh results."
- "Check the OpenMRA desktop prerequisites before starting the batch."
- "Audit the existing OpenMRA run manifest against the input and export hashes."

## Should not trigger

- "Explain concentration addition versus independent action."
- "Fit a dose-response curve directly in Python."
- "Run an OpenMRA DRC_INPUT workbook."
- "Reverse engineer or redistribute OpenMRA."
- "Interpret whether this mixture is synergistic" when no application run or report-integrity task is requested.
- "Convert SigmaPlot Sigmoid4 coefficients into another model."
- "Operate OpenMRA in Simple CA mode."
- "Create a new toxicity data workbook from these handwritten notes."
- "Recalculate these CA predictions in Python without using OpenMRA."
- "Restyle the chart in an existing Excel workbook."

## Normal flow

1. Preflight confirms the v0.2.0 executable, English UI, unlocked desktop, Python dependencies, immutable RM inputs, and a new output path.
2. One operator runs each workbook in deterministic order and exports reports.
3. The read-only validator checks each report against its input and emits JSON.
4. The final summary reports completed, failed, and scientifically unresolved items separately.

## Failure flow

1. The desktop locks or an unfamiliar dialog appears during a workbook.
2. The operator saves state and evidence, then exits without marking that workbook complete.
3. A later `--resume` confirms run identity and continues from the recorded state.
