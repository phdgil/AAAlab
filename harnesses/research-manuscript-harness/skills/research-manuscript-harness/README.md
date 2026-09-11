# Research Manuscript Harness

Install this harness together with the sibling `research-manuscript` protocol skill.

The harness coordinates the complete submission-bundle workflow: main and
Supporting Information manuscripts, the Supporting Information workbook,
cover letter, graphical TOC, publication TIFFs, rendered PDFs, evidence
mapping, scientific editing, structural audits, and independent final QA.

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

`scripts/audit_submission_bundle.py` is different: its DOCX, XLSX, ZIP,
XML, JSON, path, SHA-256, citation, reference, and manifest checks use only the
Python standard library. Pillow is the sole optional import. If Pillow is
missing while figure or graphical-TOC pixels need inspection, the relevant
gate is `UNVERIFIED`, submission readiness is withheld, and the process exits
nonzero. It never treats a missing image dependency as a pass.

## Submission-bundle audit

Run the auditor against an already assembled bundle. Every artifact argument
is a bundle-relative path; only `--bundle-root` identifies the containing
directory.

```text
python scripts/audit_submission_bundle.py \
  --bundle-root <bundle-directory> \
  --main-docx <relative-main.docx> \
  --si-docx <relative-supporting-information.docx> \
  --workbook <relative-supporting-information.xlsx> \
  --tiff-dir <relative-tiff-directory> \
  --toc <relative-graphical-toc.tiff-or-docx> \
  --checksum-manifest <relative-checksums.sha256> \
  --qa-receipt <relative-external-qa.json>
```

On PowerShell, use the same arguments on one line or replace each Bash `\`
continuation with a PowerShell backtick.

`--toc` means the journal's graphical table-of-contents artifact, not a file
inventory. It accepts a PNG, JPEG, TIFF, or BMP raster, or a DOCX container
with an embedded graphic. The auditor checks package containment, raster
decodability, native dimensions, and aspect preservation. It cannot decide
whether the graphic is legible, correctly composed, or scientifically
meaningful; render the authoritative container and record that review in the
hash-bound external QA receipt.

The two TOC input forms have explicit association semantics. A standalone
raster must match exactly one designated graphical-TOC image or, when no image
is designated, exactly one image not assigned to a numbered figure in the
main DOCX. Missing or ambiguous main-DOCX occurrences are blockers. The
auditor compares native dimensions and canonical pixels and checks that
occurrence's own drawing extent for stretching. A DOCX passed directly to
`--toc` is instead the designated authoritative TOC container: all of its
embedded graphical occurrences and their own drawing extents are checked, and
no second main-DOCX pairing is inferred. Missing or ambiguous drawing
ownership leaves the TOC gate `UNVERIFIED`.

For manuscript figures, the submitted TIFF must decode with native `RGB`
pixels. Embedded DOCX rasters are compared in a canonical `RGB` space without
resampling: native `RGB` is unchanged, and opaque `RGBA`, grayscale (`L` or
`LA`), bilevel (`1`), and palette (`P`) pixels are converted directly to
`RGB`. When an embedded raster has any nonopaque alpha, it is composited onto
an explicitly declared white page (`[255, 255, 255]`) before comparison. That
policy, the absence of resampling, and the native/canonical pixel hashes are
recorded in the receipt, and the audit emits a warning so meaningful
transparency is not changed silently. Undeclared DOCX color modes and
non-`RGB` TIFFs block the image gate.

All configured paths, manifest paths, Office package members, and internal
relationships are checked for traversal. Absolute paths, drive-qualified
paths, escaping relationships, symbolic links, and junctions are rejected.
Office packages are read in place and never extracted. The auditor writes no
files and emits one JSON receipt to stdout; a caller may redirect stdout to a
receipt outside the source bundle.

Office Open Packaging Convention relationships may use a single leading slash
for a package-root-relative part such as `/xl/worksheets/sheet1.xml`. The
auditor resolves that path inside the ZIP package; network-style `//host`
targets, drive-qualified targets, and paths that normalize outside the package
remain blockers.

### Checksum manifest

The checksum manifest is the sole complete package inventory. The portable
text form is one GNU-style SHA-256 record per line:

```text
<64-lowercase-hex-digest>  <bundle-relative/path>
```

A JSON form is also accepted:

```json
{
  "algorithm": "sha256",
  "files": [
    {"path": "<bundle-relative/path>", "sha256": "<64-hex-digest>"}
  ]
}
```

Every regular file below the bundle root must appear exactly once except the
manifest itself and Microsoft Office lock files whose basename starts with
`~$`. Missing entries, nonexistent entries, duplicates, malformed paths, and
stale digests block the manifest gate. The manifest itself and Office lock
files must not be listed.

### External QA receipt

`--qa-receipt` is optional as an input, but a missing, invalid, stale, or
unapproved receipt prevents submission readiness. The schema is
`aaalab.external-qa/v1`:

```json
{
  "schema_version": "aaalab.external-qa/v1",
  "reviewer": {
    "identity": "<independent-reviewer-id>",
    "role": "<reviewer-role>",
    "independence_basis": "<why this review is independent>"
  },
  "reviewed_at": "<ISO-8601 timestamp with timezone>",
  "outcome": "approved",
  "scope": {
    "visual_readability": {"outcome": "approved"},
    "citation_semantics": {"outcome": "approved"},
    "scientific_review": {"outcome": "approved"}
  },
  "artifact_hashes": {
    "<relative-main.docx>": "<sha256>",
    "<relative-supporting-information.docx>": "<sha256>",
    "<relative-supporting-information.xlsx>": "<sha256>",
    "<relative-graphical-toc>": "<sha256>",
    "<relative-figure.tiff>": "<sha256>"
  },
  "unresolved_blockers": []
}
```

The hash map must cover the current main DOCX, SI DOCX, workbook, graphical
TOC, and every TIFF discovered under `--tiff-dir`; there is no fixed artifact
count. Top-level and all three scoped outcomes must be `approved`, and
`unresolved_blockers` must be empty. Reviewer identity, role, and a descriptive
independence basis are required, but the machine cannot authenticate them. A
boolean such as `scientifically_verified` is ignored and never establishes
scientific correctness.

### Receipt and exit status

The stdout receipt uses schema
`aaalab.submission-bundle-audit/v1`. It contains relative input paths, a
SHA-256/size inventory, per-gate status, identifier summaries, verified image
pairs, manifest evidence, external-QA evidence, findings, unresolved blockers,
`publication_eligibility`, and the matching `exit_code`.

- Exit `0`: every structural gate passed and current approved external QA
  makes `publication_eligibility` equal to `ELIGIBLE`.
- Exit `1`: a structural gate failed or remained `UNVERIFIED`.
- Exit `2`: structural gates passed, but external QA is missing, invalid,
  stale, not approved, or records blockers.

Malformed CLI input (for example, a missing required option or an unknown
option) is separate from those audit outcomes: it emits an
`aaalab.submission-bundle-audit/v1` JSON receipt with
`verdict: INVALID_INPUT`, `structural_machine_audit: NOT_RUN`, and exit `1`.
Explicit `--help` remains normal argparse help output with exit `0`, not an
audit receipt.

The machine audit checks structural properties only. Visual readability,
semantic citation correctness, and scientific validity remain independent
human-review responsibilities. Hash binding proves which bytes were reviewed;
it does not prove that a reviewer claim is true.

## Regression and structure checks

The bounded unittest suite builds tiny synthetic DOCX, XLSX, PNG, and TIFF
fixtures. Pillow-dependent cases are explicitly skipped when Pillow is not
installed.

```text
python -m unittest discover -s scripts -p "test_submission_bundle*.py"
bash scripts/validate_harness.sh
pwsh -File scripts/validate_harness.ps1
```

## Example

```text
Use $research-manuscript-harness to prepare and audit the complete journal submission bundle: main DOCX, Supporting Information DOCX and workbook, cover letter, graphical TOC, publication TIFFs, and rendered PDFs; apply all author comments, verify evidence, abbreviations, citations, figures, tables, formatting, checksums, and bundle completeness, then perform an independent hash-bound final QA pass.
```
