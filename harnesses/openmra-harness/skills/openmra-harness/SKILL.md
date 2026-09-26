---
name: openmra-harness
description: Run OpenMRA v0.2.0 RM-mode Logistic3/4 workbook batches through its Windows English GUI, preserve auditable run artifacts, resume interrupted batches, and validate exported reports without changing source data. Use for actual OpenMRA execution or report integrity checks; the validator fails closed for Sigmoid and other models. Do not use for DRC-mode runs or biological interpretation.
metadata:
  version: "0.2.0"
  platform: "windows"
---

# OpenMRA Harness

Use this harness for reproducible OpenMRA RM-mode execution. It controls the existing desktop application through `scripts/openmra_runner.py` and checks completed report workbooks through the read-only `scripts/report_validation.py`.

## Supported boundary

- OpenMRA v0.2.0, English UI, 100% Windows display scaling, RM mode only.
- Windows interactive desktop only. The user session must remain signed in and unlocked.
- `.xlsx` input workbooks supplied by the user.
- Automated acceptance is limited to RM_INPUT `Logistic3` and `Logistic4`; Sigmoid and every other model fail as unsupported by the validator.
- One GUI operator process at a time.
- A separate validator may inspect completed files read-only after they are no longer being written.
- Preserve the model selection and parameter values already present in each input; do not tune or rewrite them during automation.

The calibrated executable SHA-256 is `2b30c03ce2ee87b11fa3b6effb2d3d0617b185aa2462252a5a164257d73843ec`. The runner refuses unknown builds because this application profile includes fixed geometry. Stop before execution if the requested mode, build, language, display profile, or platform falls outside this boundary. A new profile requires manual calibration and a successful smoke test; never skip that gate or guess control positions.

## Roles

For a non-trivial batch, keep GUI ownership and validation separate:

- The [GUI operator](references/gui-operator.md) owns OpenMRA, output creation, retries, screenshots, and run state.
- The [report validator](references/report-validator.md) reads exported workbooks and inputs without controlling OpenMRA or changing any file.

When multiple agents are unavailable, perform these roles sequentially and preserve the same boundary. Never run two GUI operators concurrently.

## Preflight

Before launching the application:

1. Resolve the exact `OpenMRA.exe`, input directory, and proposed output directory.
2. Confirm each input workbook is a regular `.xlsx` file. Ignore Office lock files beginning with `~$`.
3. Record SHA-256 for each source workbook. Treat the source directory as immutable.
4. Require a new output directory. Existing directories are accepted only with `--resume` and only when their recorded app and input identities match.
5. Confirm an unlocked interactive desktop, Python, `pywinauto`, and Pillow.
6. Run:

```powershell
python scripts/openmra_runner.py preflight --app "C:\path\OpenMRA.exe"
```

Python 3.11 or later is required. If preflight fails, report the exact dependency or desktop condition and stop. A `READY` verdict confirms prerequisites and known controls; it does not prove that every later GUI step will succeed. Do not work around a locked screen with coordinate-only clicks or synthetic desktop sessions.

## Execute a batch

Start a fresh run:

```powershell
python scripts/openmra_runner.py run `
  --app "C:\path\OpenMRA.exe" `
  --input-dir "C:\path\inputs" `
  --output-dir "C:\path\runs\openmra_run_YYYYMMDD_HHMMSS"
```

Continue the same interrupted run:

```powershell
python scripts/openmra_runner.py run `
  --app "C:\path\OpenMRA.exe" `
  --input-dir "C:\path\inputs" `
  --output-dir "C:\path\runs\openmra_run_YYYYMMDD_HHMMSS" `
  --resume
```

The runner processes inputs in deterministic filename order and starts a fresh OpenMRA process for each input. For each workbook it loads the file, runs both CA and IA predictions exposed by the RM workflow, exports the report, validates it, and retains evidence sufficient to distinguish success, application rejection, timeout, and interrupted execution.

The batch stops on any input failure. Each input receives at most two attempts in one run; after that, inspect the captured evidence and start a new run after fixing a concrete cause. Do not overwrite a completed report automatically. `--resume` revalidates accepted reports and source hashes, skips only accepted inputs that still pass, and continues from the first unresolved input.

## Validate exported reports

Run validation only after the report is closed or stable:

```powershell
python scripts/report_validation.py `
  --report "C:\path\run\exports\A.xlsx" `
  --input "C:\path\inputs\A.xlsx" `
  --output "C:\path\run\A.validation.json"
```

Omit `--output` to emit JSON to standard output. The validator uses the Python standard library and reads OOXML members directly. It checks package integrity, workbook relationships, required report structure, identity evidence carried from the input, and finite numeric result cells where results exist. It must report unavailable effect levels as unavailable rather than inventing or extrapolating values.

Validation proves file and handoff integrity. It is not independent scientific validation and does not prove that the regression equation, coefficient mapping, response normalization, ECx definition, CA/IA assumptions, or biological conclusion is correct.

Read [model validation scope](references/model-validation.md) before interpreting a validator failure. The current validator accepts only RM_INPUT `Logistic3` and `Logistic4` parameter contracts and fails explicitly for Sigmoid and other models; that limit does not establish which models OpenMRA itself can execute.

## Required run artifacts

Keep the following inside the run directory:

```text
openmra_run_YYYYMMDD_HHMMSS/
  manifest.json
  inputs/
  exports/
  _workspace/
    preflight.json
    <stem>_attemptN/
      actions.jsonl
      01_loaded.png
      02_result.png
      validation.json
      report.xlsx
```

The manifest records application identity, input hashes and order, attempt history, acceptance status, and export paths. Files under `inputs/` are immutable snapshots for the run. Accepted reports are copied to `exports/`; per-attempt evidence remains under `_workspace/`.

Never copy source workbooks into a report path under the guise of an export. If retaining a source snapshot is necessary, use a clearly named `inputs_snapshot/` directory and preserve hashes.

## Failure handling

- Locked or disconnected desktop: preserve state and stop as interrupted.
- Unexpected application dialog: capture a screenshot and accessible control text, record the current input, then stop unless the dialog has a deterministic, documented recovery.
- Input rejected by OpenMRA: record the application message and stop the batch after the allowed attempts.
- Export missing or zero bytes: mark the item failed; do not mark it complete based on the visible graph alone.
- Validator failure: preserve the report and JSON verdict for diagnosis. Re-run only the affected input after identifying a concrete export or automation failure.
- Scientific anomaly such as a vertical curve, missing ECx, or a cross-tool EC50 difference: preserve the output and flag it for domain review. Do not silently change parameters or input data.

## Completion

A batch is complete only when:

- every discovered input has an explicit manifest status,
- all successful items have non-empty exported reports,
- report validation has an accepted JSON verdict for every exported report,
- source hashes still match the pre-run inventory,
- the final summary separates automation success from scientific interpretation.

Application close is best effort. If the owned process remains after a close failure, the runner logs that condition; report it rather than claiming clean shutdown.

See [trigger tests](references/trigger-tests.md) for routing examples.
