# Research Manuscript Harness

Turn research evidence and the latest author edits into one coherent, recoverable submission package—not just a polished article. The harness coordinates the main manuscript, Supporting Information (SI), workbook, cover letter, graphical table-of-contents (TOC) asset, native-pixel figure TIFFs, rendered PDFs, references, provenance, human declarations, and independent QA without exceeding the evidence.

Use it when a change in one artifact can make another stale: renamed analyses, revised figures, moved SI content, workbook table decisions, citation repairs, journal packaging, or final submission readiness.

## What it enforces

- One authoritative candidate bundle and one executable per-run plan; user steering updates that plan instead of creating a competing plan.
- Verified byte-for-byte preimages outside the bundle and separately hashed immutable numerical evidence.
- Latest author edits preserved, with targeted DOCX changes that retain run/paragraph styles, breaks, fields, and table pagination unless normalization is authorized.
- Complementary SI that adds explanation and reproducibility detail rather than repeating the main text.
- Authorized workbook keep/merge/move/drop decisions, substantive sheets ordered by first main citation, and retained full precision, formulas, SMARTS or equivalent structures, and provenance.
- Simultaneous old-to-new mappings across main/SI text, captions, sheet names, title cells, formulas, graphical TOC, filenames, and derivatives.
- Separate citation-numbering and semantic source-topic review.
- Figure rendering from fixed hashed data without rescoring or refitting. The requested real provider is used—including Codex CLI image generation when requested—or the task blocks; generation is never faked or silently replaced with a code-drawn substitute.
- Coordinated replacement and rendered inspection of the journal's graphical TOC raster or DOCX container and, for a DOCX container, its authoritative embedded image.
- An exact captioned main/SI figure-to-TIFF inventory using native pixels and undistorted RGB/LZW output when required; changing a DPI tag is not treated as added resolution. The graphical TOC is a separate audited artifact, not the package inventory or an implied figure-TIFF entry.
- Source-native and final-size embedded/rendered visual QA plus an independent scientific reviewer. Machine checks cannot certify readability, citation meaning, or scientific correctness.
- Concise, discovery-first cover letters rather than timelines, version histories, or technical changelogs.
- Human confirmation of author, funding, COI, permissions, and actual AI-use facts; declarations are never inferred.
- Same-filesystem staged publication with verified rollback and exclusive promotion: use a genuinely atomic directory operation only where the platform supports it; otherwise use a guarded, recoverable transaction and withhold `submission-ready` until its completion is checked. Cross-volume moves and sequential file copies are not atomic; lock failures are reported without deleting user Office locks.

The protocol explicitly distinguishes positive-only from verified-negative evaluation, source-overlap consistency from external validation, full-fit from held-out results, fixed-baseline augmentation from end-to-end training, and hash folds from verified scaffold-disjoint folds. “Source-overlap consistency” covers comparisons whose source, selection, or overlap rules do not establish independence from model construction; it does not assert that every retained record overlaps.

## Package contents

The package installs two existing skills together:

- `research-manuscript`: reusable scientific and submission-bundle protocol.
- `research-manuscript-harness`: multi-agent orchestration using the existing specialist role layout and bounded producer/reviewer handoffs.

Detailed rules live in:

- `skills/research-manuscript/references/submission-bundle-workflow.md`
- `skills/research-manuscript/references/manuscript-author-review-guidelines.md`

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

## Validate the installed harness

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

- A skill-enabled agent runtime.
- Multi-agent support for the full harness; the protocol skill also supports bounded direct work.
- Python for local document and bundle audits.
- `python-docx` for DOCX inspection/editing and structural verification.
- Pillow for raster decoding, native dimensions, RGB/pixel-identity, and aspect-ratio checks; retain a separate recorded LZW-compression check because the bundle auditor does not certify compression.
- `openpyxl` for workbook inventory, formulas, title cells, sheet-order, and citation-parity audits.
- The actual requested image-generation provider/interface when generation is in scope.

Missing mutation, rendering, or provider capabilities must block that operation and be reported; the harness does not claim success through a fallback.

## Example prompt

```text
Use $research-manuscript-harness to prepare one submission bundle from the latest author-edited files. Preserve verified preimages outside the bundle and freeze the numerical evidence; reconcile the main text, SI, supporting workbook, cover, graphical TOC, figure TIFFs, PDFs, and references; preserve author Word formatting; run independent final-size visual, citation-semantic, and scientific QA; then report the exact readiness status without submitting externally.
```

## Automated structural audit

`scripts/audit_docx.py` remains the focused per-document/workbook helper. The bundle auditor below adds cross-package path, inventory, manifest, and external-receipt checks; neither tool replaces independent review.

`audit_submission_bundle.py` uses the Python standard library for DOCX/XLSX package, XML, JSON, path, SHA-256, citation, reference, and manifest checks. Pillow is its sole optional import; when relevant figure or graphical-TOC pixels cannot be inspected because Pillow is missing, the structural gate is `UNVERIFIED` and the command exits nonzero.

After all artifacts, fresh PDFs, and the external QA receipt are final, generate the exact-coverage checksum manifest last and run:

```bash
python scripts/audit_submission_bundle.py \
  --bundle-root PATH \
  --main-docx REL \
  --si-docx REL \
  --workbook REL \
  --tiff-dir REL \
  --toc REL \
  --checksum-manifest REL \
  --qa-receipt REL
```

`--toc` is the journal's graphical TOC raster or a DOCX container with an embedded graphic; it is not a file list. `--checksum-manifest` is the sole complete bundle inventory. All configured paths must remain inside the bundle; symlinks, junctions, and escapes are rejected. The tool is read-only and emits one `aaalab.submission-bundle-audit/v1` JSON receipt to standard output.

- Exit `0`: `structural_machine_audit` is `PASS`, external QA is `CURRENT_APPROVED`, `verdict` is `SUBMISSION_READY`, and `publication_eligibility` is `ELIGIBLE`.
- Exit `1`: `structural_machine_audit` is `FAIL` or `UNVERIFIED`; `publication_eligibility` is `NOT_ELIGIBLE`.
- Exit `2`: structural gates pass, but external QA is `MISSING`, `INVALID`, `STALE`, or `NOT_APPROVED`; `verdict` is `BLOCKED` and `publication_eligibility` is `NOT_ELIGIBLE`.

The optional QA input uses schema `aaalab.external-qa/v1`. It requires reviewer `identity`, `role`, and `independence_basis`; a timezone-bearing `reviewed_at`; top-level `outcome: approved`; approved outcomes for `visual_readability`, `citation_semantics`, and `scientific_review`; no `unresolved_blockers`; and current hashes for the main DOCX, SI DOCX, workbook, graphical TOC, and every TIFF discovered under `--tiff-dir`. The machine does not authenticate the reviewer or prove scientific correctness.

The QA receipt is a regular bundle file and therefore belongs in the checksum manifest; it is not listed in or encoded into the graphical TOC. Store audit JSON and the final publication receipt outside the audited bundle to avoid circular hashes. See the authoritative workflow for exact schemas, output fields, manifest rules, guarded promotion, and status definitions.

## License

Original harness text and scripts are Apache-2.0. Optional document and provider libraries remain external and retain their own licenses. See `LICENSE_AUDIT.md` and `THIRD_PARTY_NOTICES.md`.
