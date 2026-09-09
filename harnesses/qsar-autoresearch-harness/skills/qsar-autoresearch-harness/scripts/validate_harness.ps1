param(
    [string]$HarnessRoot = "",
    [string]$ProtocolSkill = ""
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($HarnessRoot)) {
    $HarnessRoot = Resolve-Path -LiteralPath (Join-Path $scriptDir "..")
}
if ([string]::IsNullOrWhiteSpace($ProtocolSkill)) {
    $skillsRoot = Split-Path -Parent $HarnessRoot
    $ProtocolSkill = Join-Path $skillsRoot "qsar-autoresearch\SKILL.md"
}

$required = @(
    "SKILL.md",
    "README.md",
    "agents/openai.yaml",
    "references/trigger-tests.md",
    "references/agents/dataset-contract-auditor.md",
    "references/agents/feature-prep-lead.md",
    "references/agents/training-eval-lead.md",
    "references/agents/next-experiment-controller.md",
    "references/agents/qa-reviewer.md",
    "scripts/verify_dependency_contract.ps1",
    "scripts/check_runtime_dependencies.ps1"
)

$missing = @()
foreach ($relative in $required) {
    $path = Join-Path $HarnessRoot $relative
    if (-not (Test-Path -LiteralPath $path)) {
        $missing += $relative
    }
}

if (-not (Test-Path -LiteralPath $ProtocolSkill)) {
    $missing += "protocol skill: $ProtocolSkill"
}

if ($missing.Count -gt 0) {
    Write-Error ("Missing required harness files: " + ($missing -join ", "))
}

$skill = Get-Content -Raw -LiteralPath (Join-Path $HarnessRoot "SKILL.md")
foreach ($needle in @("name: qsar-autoresearch-harness", "description:", "qsar-autoresearch", "Phase 0: Context check", "Phase 6: QA review", "Test Scenarios")) {
    if ($skill -notlike "*$needle*") {
        Write-Error "SKILL.md is missing required text: $needle"
    }
}

& (Join-Path $HarnessRoot "scripts/verify_dependency_contract.ps1") -HarnessRoot $HarnessRoot -ProtocolSkill $ProtocolSkill

Write-Output "QSAR autoresearch harness structure OK."
