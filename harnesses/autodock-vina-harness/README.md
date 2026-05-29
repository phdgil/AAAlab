# AutoDock Vina Harness

Agent harness for running protein-ligand docking through AutoDock Vina and optional gnina follow-up.

This is the first harness in AAAlab. License: Apache-2.0. See the repository root [LICENSE](../../LICENSE), plus this harness's [LICENSE_AUDIT.md](LICENSE_AUDIT.md) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

This harness package contains two skills:

- `autodock-vina`: the docking protocol source.
- `autodock-vina-harness`: the multi-agent harness for execution, validation, reruns, gnina follow-up, troubleshooting, and QA.

Install both skill folders together. The harness expects the protocol skill to be installed as a sibling folder.

## Install

From npm:

```bash
npm install -g github:shkdidrlf/AAAlab
aaalab install autodock-vina-harness
```

PowerShell:

```powershell
.\install.ps1
```

Custom agent home:

```powershell
.\install.ps1 -CodexHome "D:\path\to\.codex"
```

macOS/Linux:

```bash
./install.sh
```

Custom Codex home:

```bash
./install.sh "$HOME/.codex"
```

Manual install:

```text
copy skills/autodock-vina         -> <CODEX_HOME>/skills/autodock-vina
copy skills/autodock-vina-harness -> <CODEX_HOME>/skills/autodock-vina-harness
```

Restart your agent runtime after installation so its skill or harness registry reloads.

From the AAAlab repository root, you can also install this harness with:

```powershell
.\install.ps1 -Harness autodock-vina-harness
```

## Validate

Cross-platform npm validation:

```bash
aaalab validate autodock-vina-harness
```

PowerShell validation:

```powershell
& "$env:USERPROFILE\.codex\skills\autodock-vina-harness\scripts\validate_harness.ps1"
```

Expected:

```text
AutoDock Vina harness structure OK.
```

## Runtime Prerequisites

- An agent runtime with skills or harnesses enabled, such as Claude Code, Antigravity, or Codex CLI.
- Multi-agent support enabled for full harness orchestration when available.
- AutoDock Vina available on `PATH` for actual docking.
- At least one PDBQT prep path: Meeko, MGLTools/AutoDockTools, or Open Babel.
- RDKit or another conformer path when starting from SMILES or 2D ligands.
- Docker or native gnina only when gnina is requested.

The harness intentionally stops before docking if Vina or all PDBQT preparation paths are missing. License cleanup must not remove runtime alternatives; it only keeps those tools external.

Check local runtime availability:

```bash
aaalab runtime-check autodock-vina-harness
```

## Example Prompt

```text
Use $autodock-vina-harness to dock this ligand set against this receptor. Validate known anchors first, run Vina, create pose_check.html and interaction_analysis.html, and only make biological claims if the anchor gates pass.
```

## What The Harness Enforces

- Vina and PDBQT-prep preflight before execution.
- Structure and binding-box rationale before docking.
- Literature-derived or user-supplied validation gates when available.
- Anchor docking before novel-ligand interpretation.
- Separate Vina and gnina score parsing, including CNN pose score versus CNN affinity.
- Pose and interaction HTML checks before final claims.
- Independent QA pass before the final report.

## Publishing Notes

The repo is licensed under Apache-2.0 for the original skill text and scripts. External docking and chemistry tools are not bundled and keep their own licenses.
