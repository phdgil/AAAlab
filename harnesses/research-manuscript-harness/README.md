# Research Manuscript Harness

Agent harness for converting research outcomes into publication-level manuscripts and performing evidence-grounded revision of DOCX or Markdown drafts.

The package contains two skills:

- `research-manuscript`: reusable scientific-writing and document-review protocol.
- `research-manuscript-harness`: multi-agent orchestration for evidence mapping, methods auditing, abbreviation and citation checks, figure/table review, DOCX formatting, and independent QA.

Install both skills together.

## Install

Recommended global install:

```bash
npm install -g github:phdgil/AAAlab
aaalab install research-manuscript-harness
```

One-shot install:

```bash
npx --yes github:phdgil/AAAlab install research-manuscript-harness
```

Custom agent home:

```bash
aaalab install research-manuscript-harness --agent-home "$HOME/.codex"
```

From this directory in a local clone:

PowerShell:

```powershell
.\install.ps1
```

macOS/Linux:

```bash
./install.sh
```

Manual installation:

```text
copy skills/research-manuscript         -> <AGENT_HOME>/skills/research-manuscript
copy skills/research-manuscript-harness -> <AGENT_HOME>/skills/research-manuscript-harness
```

Restart the agent runtime after installation.

## Validate

```bash
aaalab validate research-manuscript-harness
```

PowerShell:

```powershell
& "$env:USERPROFILE\.codex\skills\research-manuscript-harness\scripts\validate_harness.ps1"
```

macOS/Linux:

```bash
~/.codex/skills/research-manuscript-harness/scripts/validate_harness.sh
```

Expected output:

```text
Research manuscript harness structure OK.
```

## Runtime prerequisites

- Skill-enabled agent runtime.
- Multi-agent support for the full harness.
- Python for local document audits.
- `python-docx` for DOCX reading, editing, and structural verification.
- Pillow for embedded-image dimensions and aspect-ratio checks.
- `openpyxl` for consolidated Supporting Information workbook and worksheet-parity audits.

Markdown review can proceed without DOCX dependencies. DOCX mutation or verification must stop and report missing dependencies rather than claiming success.

## Example prompt

```text
Use $research-manuscript-harness to reconcile this manuscript with current results, apply all author comments, improve scientific structure, audit abbreviations and citation order, repair multi-panel figures and legends, enforce the journal Word format, and run independent QA on the final DOCX.
```

## Enforced review rules

- Evidence mapping before prose mutation.
- An executable revision plan before mutation, followed by execution and final verification in the same run.
- Local and global application of literal `{...}` comments before comment removal.
- Publication-level literature explanation.
- Reproducible methods with explicit thresholds, folds, and leakage controls.
- Scientific rationale for descriptor sets, filtering intent, cutoffs, fold design, top-N caps, and external-source selection.
- Separate abbreviation scopes for abstract and main body.
- First-citation-order references.
- Results ordered by evidential dependency.
- Main/SI method-result alignment and consolidated XLSX delivery for exact supporting tables.
- Unboxed parenthesized multi-panel labels and panel-specific legends.
- Mandatory visual inspection of source and embedded figures at final manuscript display size.
- Blocking checks for clipped annotations, unreadable fonts, weak contrast, and unintended panel data.
- Nonredundant figures and tables.
- Actual DOCX table-object checks that block missing, concatenated, or malformed tables.
- Final DOCX formatting and image-geometry checks.
- Independent QA before “review-ready” or “submission-ready” claims.

## License

Original harness text and scripts are Apache-2.0. Optional document libraries remain external and retain their own licenses. See `LICENSE_AUDIT.md` and `THIRD_PARTY_NOTICES.md`.
