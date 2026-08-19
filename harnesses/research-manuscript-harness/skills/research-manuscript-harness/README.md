# Research Manuscript Harness

Install this harness together with the sibling `research-manuscript` protocol skill.

The harness coordinates evidence mapping, scientific editing, methods review, abbreviation and citation audits, figure and table review, DOCX formatting, and independent QA.

## Install

Copy both folders into the agent skill directory:

```text
<AGENT_HOME>/skills/research-manuscript/
<AGENT_HOME>/skills/research-manuscript-harness/
```

Restart the agent runtime after installation.

## Runtime requirements

- An agent runtime with skill support.
- Multi-agent support for full orchestration.
- Read/write access to the manuscript workspace.
- Python with `python-docx` for DOCX mutation or structural audits.
- Pillow for embedded-image dimension and aspect-ratio audits.
- `openpyxl` for consolidated Supporting Information workbook audits.

The protocol can still review Markdown without `python-docx` or Pillow, but DOCX verification must stop and disclose missing dependencies.

## Example

```text
Use $research-manuscript-harness to audit this manuscript against current results, apply all author comments, improve section structure, verify abbreviations and citations, repair multi-panel figures and legends, enforce the journal DOCX format, and perform an independent final QA pass.
```
