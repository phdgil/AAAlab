param(
    [string]$HarnessRoot = ""
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($HarnessRoot)) {
    $HarnessRoot = Resolve-Path -LiteralPath (Join-Path $scriptDir "..")
}

$required = @(
    "SKILL.md",
    "agents\openai.yaml",
    "references\gui-operator.md",
    "references\model-validation.md",
    "references\report-validator.md",
    "references\trigger-tests.md",
    "scripts\openmra_runner.py",
    "scripts\report_validation.py",
    "scripts\check_runtime_dependencies.ps1"
)

$missing = @()
foreach ($relative in $required) {
    $path = Join-Path $HarnessRoot $relative
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        $missing += $relative
    }
}
if ($missing.Count -gt 0) {
    Write-Error ("Missing required harness files: " + ($missing -join ", "))
}

$skill = Get-Content -Raw -LiteralPath (Join-Path $HarnessRoot "SKILL.md")
foreach ($needle in @(
    "name: openmra-harness",
    "OpenMRA v0.2.0",
    "RM mode only",
    "One GUI operator",
    "report_validation.py",
    "--resume",
    "Scientific anomaly"
)) {
    if (-not $skill.Contains($needle)) {
        Write-Error "SKILL.md is missing required text: $needle"
    }
}

$yaml = Get-Content -Raw -LiteralPath (Join-Path $HarnessRoot "agents\openai.yaml")
foreach ($needle in @("display_name:", "short_description:", "default_prompt:", "allow_implicit_invocation: true")) {
    if (-not $yaml.Contains($needle)) {
        Write-Error "agents/openai.yaml is missing required text: $needle"
    }
}

$forbiddenExtensions = @(".exe", ".dll", ".pyd", ".zip", ".xlsx", ".xls", ".png", ".jpg", ".jpeg", ".pptx")
$forbidden = Get-ChildItem -LiteralPath $HarnessRoot -Recurse -File | Where-Object {
    $forbiddenExtensions -contains $_.Extension.ToLowerInvariant()
}
if ($forbidden.Count -gt 0) {
    Write-Error ("Private data or vendored binaries are not allowed: " + (($forbidden.FullName) -join ", "))
}

Write-Output "OpenMRA harness structure OK."
