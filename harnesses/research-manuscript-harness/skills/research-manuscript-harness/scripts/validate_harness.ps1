$ErrorActionPreference = "Stop"
$skillRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$protocolRoot = Join-Path (Split-Path -Parent $skillRoot) "research-manuscript"
$required = @(
    (Join-Path $skillRoot "SKILL.md"),
    (Join-Path $skillRoot "README.md"),
    (Join-Path $skillRoot "agents\openai.yaml"),
    (Join-Path $skillRoot "references\trigger-tests.md"),
    (Join-Path $skillRoot "references\agents\evidence-structure-editor.md"),
    (Join-Path $skillRoot "references\agents\methods-reproducibility-auditor.md"),
    (Join-Path $skillRoot "references\agents\abbreviation-citation-auditor.md"),
    (Join-Path $skillRoot "references\agents\figure-table-editor.md"),
    (Join-Path $skillRoot "references\agents\docx-format-auditor.md"),
    (Join-Path $skillRoot "references\agents\qa-reviewer.md"),
    (Join-Path $skillRoot "scripts\audit_docx.py"),
    (Join-Path $protocolRoot "SKILL.md"),
    (Join-Path $protocolRoot "references\manuscript-author-review-guidelines.md")
)
$missing = $required | Where-Object { -not (Test-Path -LiteralPath $_) }
if ($missing) {
    throw "Missing required harness files:`n$($missing -join "`n")"
}
$skill = Get-Content -Raw -LiteralPath (Join-Path $skillRoot "SKILL.md")
foreach ($needle in @("name: research-manuscript-harness", "Phase 0: Context and authority", "Independent QA", "Test Scenarios")) {
    if (-not $skill.Contains($needle)) { throw "SKILL.md missing required text: $needle" }
}
Write-Output "Research manuscript harness structure OK."
