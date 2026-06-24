# QSAR Autoresearch Harness

Agent harness for reusable QSAR dataset-contract auditing, in-vivo table preparation, leakage-aware feature staging, per-table model training, bounded next-experiment design, and reproducible result packaging.

This harness packages the workflow proven in the endocrine QSAR project into an AAAlab installable harness. It is intentionally a first-step QSAR autoresearch harness rather than a full autonomous science stack: it enforces deterministic artifact contracts, leakage checks, and a one-step bounded retry loop before any broader autoresearch expansion.

This harness package contains two skills:

- `qsar-autoresearch`: the base protocol source for reusable QSAR pipeline design and execution.
- `qsar-autoresearch-harness`: the multi-agent harness for dataset audits, pipeline assembly, training, evaluation, retry-loop design, and QA.

Install both skill folders together. The harness expects the protocol skill to be installed as a sibling folder.

## Install

Recommended global install:

```bash
npm install -g github:phdgil/AAAlab
aaalab install qsar-autoresearch-harness
```

One-shot install without keeping a global `aaalab` command:

```bash
npx --yes github:phdgil/AAAlab install qsar-autoresearch-harness
```

Custom agent home:

```bash
aaalab install qsar-autoresearch-harness --agent-home "$HOME/.codex"
```

From this harness directory in a local clone:

PowerShell:

```powershell
.\install.ps1
```

macOS/Linux:

```bash
./install.sh
```

Manual install:

```text
copy skills/qsar-autoresearch         -> <AGENT_HOME>/skills/qsar-autoresearch
copy skills/qsar-autoresearch-harness -> <AGENT_HOME>/skills/qsar-autoresearch-harness
copy LICENSE_AUDIT.md                 -> <AGENT_HOME>/skills/qsar-autoresearch-harness/LICENSE_AUDIT.md
copy THIRD_PARTY_NOTICES.md           -> <AGENT_HOME>/skills/qsar-autoresearch-harness/THIRD_PARTY_NOTICES.md
```

Restart your agent runtime after installation so its skill or harness registry reloads.

## Validate

Cross-platform npm validation:

```bash
aaalab validate qsar-autoresearch-harness
```

PowerShell validation:

```powershell
& "$env:USERPROFILE\.codex\skills\qsar-autoresearch-harness\scripts\validate_harness.ps1"
```

Expected:

```text
QSAR autoresearch harness structure OK.
```

## Runtime Prerequisites

- An agent runtime with skills or harnesses enabled, such as Claude Code, Antigravity, or Codex CLI.
- Multi-agent support enabled for full harness orchestration when available.
- Python available on `PATH`.
- `pandas`, `scikit-learn`, and `openpyxl` available for workbook-driven QSAR pipelines.
- RDKit available for structure normalization, molecular weight calculations, and chemistry features.
- Optional plotting/report packages only when richer HTML or visualization exports are requested.

The harness intentionally stops before training if Python, RDKit, or the required tabular ML stack is missing. It also stops when no explicit dataset contract can explain sheet selection, in-vivo filtering, target construction, or leakage guardrails.

Check local runtime availability:

```bash
aaalab runtime-check qsar-autoresearch-harness
```

## Example Prompt

```text
Use $qsar-autoresearch-harness to audit this QSAR workbook, build a reusable in-vivo pipeline, train the allowed tables, generate a final HTML summary, and if the first run is weak produce exactly one bounded next-experiment manifest instead of improvising more runs.
```

## What The Harness Enforces

- Explicit dataset adapter or workbook contract before data preparation.
- Deterministic in-vivo subset preparation with preserved exclusion reasons.
- Leakage-aware feature staging and selector fitting inside training folds.
- Separation of categorical classification, numeric regression, and comparison-only parity branches.
- Final result packaging with machine-readable manifests and human-readable HTML summaries.
- A deterministic one-step `next_experiment_manifest` when the first run is not good enough.
- Independent QA before the final report.

## Publishing Notes

The repo is licensed under Apache-2.0 for the original skill text and scripts. External Python and cheminformatics libraries are not bundled and keep their own licenses.
