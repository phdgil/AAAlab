# QSAR Autoresearch Harness

Share this skill together with the sibling `qsar-autoresearch` skill. The harness coordinates multi-agent execution; the base skill is the protocol source.

## Install

Copy both folders into the recipient's Codex skills directory:

```text
<CODEX_HOME>/skills/qsar-autoresearch/
<CODEX_HOME>/skills/qsar-autoresearch-harness/
```

Typical locations:

- Windows: `%USERPROFILE%\.codex\skills`
- macOS/Linux: `$HOME/.codex/skills`

After copying, start a new Codex session so the skills registry reloads.

## Runtime Requirements

- Codex with skill support.
- Multi-agent support enabled when using the full harness workflow.
- Python with `pandas`, `scikit-learn`, `openpyxl`, and RDKit available to the workspace.
- A writable project workspace for manifests, prepared tables, model outputs, and summary HTML files.

## Validate Installation

From PowerShell:

```powershell
& "$env:USERPROFILE\.codex\skills\qsar-autoresearch-harness\scriptsalidate_harness.ps1"
```

Expected output:

```text
QSAR autoresearch harness structure OK.
```

## Use

Ask for actual pipeline execution through the harness:

```text
Use $qsar-autoresearch-harness to audit this in-vivo QSAR workbook, train the allowed tables, generate a Korean summary page, and design one next experiment if performance is weak.
```

For simple conceptual questions, use `$qsar-autoresearch` or ask directly.
