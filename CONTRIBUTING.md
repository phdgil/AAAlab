# Contributing

Keep harnesses reliable, portable, and auditable.

## Harness Rules

- Put each harness under `harnesses/<name>/`.
- Build, validate, and publish a harness outside the active scientific-workflow context. Use an isolated worktree, separate agent session, or bounded subagent so packaging work does not displace the research evidence and decisions held in the main context.
- Pass only the finalized protocol, explicit requirements, and necessary files into the harness-building context. Return a compact validation and publication receipt to the scientific-workflow context.
- Do not clone repositories, inspect unrelated harnesses, run package validation, or perform Git publishing inside an active manuscript, docking, QSAR, or other research execution context.
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
