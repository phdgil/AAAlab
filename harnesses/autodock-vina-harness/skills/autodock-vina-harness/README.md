# AutoDock Vina Harness

Share this skill together with the sibling `autodock-vina` skill. The harness coordinates multi-agent execution; the base skill is the docking protocol source.

## Install

Copy both folders into the recipient's Codex skills directory:

```text
<CODEX_HOME>/skills/autodock-vina/
<CODEX_HOME>/skills/autodock-vina-harness/
```

Typical locations:

- Windows: `%USERPROFILE%\.codex\skills`
- macOS/Linux: `$HOME/.codex/skills`

After copying, start a new Codex session so the skills registry reloads.

## Runtime Requirements

- Codex with skill support.
- Multi-agent support enabled when using the full harness workflow.
- AutoDock Vina available on `PATH` for actual docking execution.
- At least one PDBQT preparation path: Meeko, MGLTools/AutoDockTools, or Open Babel.
- RDKit or another conformer-generation path when starting from SMILES or 2D ligands.
- Docker or native gnina only when gnina follow-up is requested.

The harness intentionally stops before docking if Vina or all PDBQT preparation paths are missing.

## Validate Installation

From PowerShell:

```powershell
& "$env:USERPROFILE\.codex\skills\autodock-vina-harness\scripts\validate_harness.ps1"
```

Expected output:

```text
AutoDock Vina harness structure OK.
```

## Use

Ask for actual docking execution through the harness:

```text
Use $autodock-vina-harness to dock these ligands against this receptor, validate anchors first, and generate pose_check.html.
```

For simple conceptual questions, use `$autodock-vina` or ask directly.
