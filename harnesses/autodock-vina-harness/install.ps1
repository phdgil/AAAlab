param(
    [string]$CodexHome = (Join-Path $env:USERPROFILE ".codex")
)

$ErrorActionPreference = "Stop"

$packRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$sourceSkills = Join-Path $packRoot "skills"
$targetSkills = Join-Path $CodexHome "skills"

foreach ($skill in @("autodock-vina", "autodock-vina-harness")) {
    $source = Join-Path $sourceSkills $skill
    if (-not (Test-Path -LiteralPath $source)) {
        Write-Error "Missing source skill: $source"
    }
}

New-Item -ItemType Directory -Force -Path $targetSkills | Out-Null

foreach ($skill in @("autodock-vina", "autodock-vina-harness")) {
    $source = Join-Path $sourceSkills $skill
    $target = Join-Path $targetSkills $skill
    if (Test-Path -LiteralPath $target) {
        Remove-Item -Recurse -Force -LiteralPath $target
    }
    Copy-Item -Recurse -Force -LiteralPath $source -Destination $targetSkills
    Write-Output "Installed $skill -> $target"
}

$validator = Join-Path $targetSkills "autodock-vina-harness\scripts\validate_harness.ps1"
& $validator

Write-Output "Restart Codex so the skill registry reloads."
