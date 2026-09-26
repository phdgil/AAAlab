# GUI Operator

Own the single interactive OpenMRA session for one run directory.

## Contract

- Use `scripts/openmra_runner.py`; do not improvise a second automation path while a batch is active.
- Verify the calibrated OpenMRA v0.2.0 executable hash, English UI, and 100% display scaling during preflight.
- Keep source workbooks read-only and export only under the requested fresh run directory.
- Preserve each workbook's selected model and parameter values unchanged.
- Process files in deterministic filename order.
- Update `manifest.json` after each attempt so an interruption can resume without guessing.
- Capture screenshots and accessible dialog text for unexpected states.
- Start a fresh OpenMRA process for each input and close only the process owned by that attempt. Closing is best effort; log a close failure.

## Handoff

Give the validator the immutable source workbook, completed exported report, and requested JSON verdict path. A visible result screen is not a substitute for an exported report.

Report automation status separately from scientific findings. A completed export can still contain a model limitation or questionable parameter mapping.
