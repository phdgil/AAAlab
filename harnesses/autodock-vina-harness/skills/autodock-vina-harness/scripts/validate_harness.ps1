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
    $ProtocolSkill = Join-Path $skillsRoot "autodock-vina\SKILL.md"
}

$required = @(
    "SKILL.md",
    "README.md",
    "agents\openai.yaml",
    "references\trigger-tests.md",
    "references\agents\structure-box-lead.md",
    "references\agents\validation-lead.md",
    "references\agents\prep-lead.md",
    "references\agents\docking-runner.md",
    "references\agents\qa-reviewer.md"
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
foreach ($needle in @("name: autodock-vina-harness", "description:", "autodock-vina", "Phase 0: Context Check", "QA Review", "Test Scenarios")) {
    if ($skill -notlike "*$needle*") {
        Write-Error "SKILL.md is missing required text: $needle"
    }
}

$protocol = Get-Content -Raw -LiteralPath $ProtocolSkill
if ($protocol -notlike "*autodock-vina-harness*") {
    Write-Warning "Base autodock-vina skill does not mention autodock-vina-harness routing."
}

Write-Output "AutoDock Vina harness structure OK."
