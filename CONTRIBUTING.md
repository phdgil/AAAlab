# Contributing

Keep harnesses reliable, portable, and auditable.

## Harness Rules

- Put each harness under `harnesses/<name>/`.
- Keep each harness installable without machine-specific paths.
- Include a `README.md` for each harness.
- Include a license audit or third-party notice when a harness references external tools with meaningful licensing constraints.
- Do not vendor third-party code, binaries, models, or datasets without updating license documentation.
- Run validation before opening a pull request.

## Validation

PowerShell:

```powershell
.\scripts\validate-all.ps1
```

or directly:

```powershell
Get-ChildItem .\harnesses -Recurse -Filter validate_harness.ps1 | ForEach-Object { & $_.FullName }
```
