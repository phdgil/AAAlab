# OpenMRA Harness

Windows harness for reproducible OpenMRA v0.2.0 RM-mode mixture-risk runs and read-only workbook validation.

The harness controls an existing, separately obtained `OpenMRA.exe`; the application and research data are not distributed here. It preserves source workbooks, writes each batch to a new run directory, records machine-readable run state, and validates exported reports in a separate process.

The automated validator accepts only RM_INPUT `Logistic3` and `Logistic4` parameter contracts. Sigmoid and other model reports fail closed as unsupported by the validator, even though OpenMRA may execute additional models.

## Install

```powershell
npm install -g github:phdgil/AAAlab
aaalab install openmra-harness
```

From a local clone:

```powershell
.\install.ps1 -Harness openmra-harness
```

Restart the agent runtime after installation so its skill registry reloads.

## Runtime prerequisites

- Windows with an unlocked interactive desktop.
- OpenMRA v0.2.0 using the English UI at 100% Windows display scaling.
- Python 3.11 or later on `PATH`.
- `pywinauto` and Pillow for GUI control and screenshots.
- RM-mode `.xlsx` input files prepared for OpenMRA.

Check the environment:

```powershell
aaalab runtime-check openmra-harness
python skills\openmra-harness\scripts\openmra_runner.py preflight --app "C:\path\OpenMRA.exe"
```

`aaalab runtime-check` returns a nonzero status when Windows, Python 3.11, `pywinauto`, or Pillow is missing. If `OPENMRA_APP` is supplied, a missing path or non-`.exe` file also fails; when it is unset, the executable check is explicitly advisory and remains unchecked. The runner's executable/profile preflight is the blocking gate for an actual GUI run.

## Run

The output path must be a new directory. Use `--resume` only to continue the same interrupted run.

```powershell
python skills\openmra-harness\scripts\openmra_runner.py run `
  --app "C:\path\OpenMRA.exe" `
  --input-dir "C:\path\inputs" `
  --output-dir "C:\path\runs\openmra_run_20260926"
```

Validate one exported report without launching or controlling OpenMRA:

```powershell
python skills\openmra-harness\scripts\report_validation.py `
  --report "C:\path\runs\openmra_run_20260926\exports\A.xlsx" `
  --input "C:\path\inputs\A.xlsx" `
  --output "C:\path\runs\openmra_run_20260926\A.validation.json"
```

## Safety boundary

Only one operator may control the OpenMRA GUI at a time. Validation is read-only and may run separately after an export is complete. The harness sends each workbook to OpenMRA without changing its selected model or parameter values. It does not edit source workbooks, bundle OpenMRA, interpret biological significance, or claim that a structurally valid report is independently scientifically validated.

GUI automation uses a calibrated application profile because part of this OpenMRA build exposes fixed geometry rather than stable automation identifiers. The tested executable SHA-256 is `2b30c03ce2ee87b11fa3b6effb2d3d0617b185aa2462252a5a164257d73843ec`. The runner refuses unknown executable builds. Supporting another build, UI language, or display profile requires manual calibration followed by a successful smoke test; this gate cannot be skipped.

## Validation evidence

- 29 deterministic Python tests passed for report integrity, partial-result warnings, executable gating, manifest behavior, crash recovery, and resume checks. One symlink-specific test was skipped on the local Windows host because symlink creation privileges were unavailable; direct-path and hardlink overwrite tests passed.
- A 16-workbook application batch completed and all 16 exported reports passed read-only validation.
- A fresh two-input end-to-end runner smoke completed with exit code 0 and both reports accepted.
- Resume revalidated those two accepted reports and skipped both without rerunning OpenMRA.
- Package validation, skill validation, and the full AAAlab npm test suite passed.

No OpenMRA executable, source workbook, raw report, screenshot, or private input value is included in this repository.

See [model validation scope](skills/openmra-harness/references/model-validation.md) for the exact Logistic3/4 provenance checks and current limitations.

## Validate the package

```powershell
aaalab validate openmra-harness
python -m unittest discover -s skills\openmra-harness\scripts -p "test_*.py"
```
