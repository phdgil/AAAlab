# Contributing

Keep changes focused on improving docking reliability.

## Guidelines

- Update `skills/autodock-vina/SKILL.md` when the protocol itself changes.
- Update `skills/autodock-vina-harness/SKILL.md` when orchestration, routing, rerun behavior, or QA gates change.
- Keep machine-specific paths out of committed files.
- Keep anchor validation logic conservative. Failed known anchors should block biological interpretation, not be hidden.
- Do not vendor third-party docking or chemistry tools without updating `LICENSE_AUDIT.md` and `THIRD_PARTY_NOTICES.md`.
- Run the harness validator before opening a pull request.

## Validation

PowerShell:

```powershell
& .\skills\autodock-vina-harness\scripts\validate_harness.ps1
```

Expected output:

```text
AutoDock Vina harness structure OK.
```
