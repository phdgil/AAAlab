param(
    [Alias("CodexHome")]
    [string]$AgentHome = $(if ($env:AAALAB_AGENT_HOME) { $env:AAALAB_AGENT_HOME } elseif ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" })
)

$ErrorActionPreference = "Stop"

$packRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$sourceSkills = Join-Path $packRoot "skills"
$targetSkills = Join-Path $AgentHome "skills"

$skillNames = @(Get-ChildItem -LiteralPath $sourceSkills -Directory | Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "SKILL.md") } | Select-Object -ExpandProperty Name)
if ($skillNames.Count -eq 0) { throw "No QSAR skills found" }

foreach ($skill in $skillNames) {
    $source = Join-Path $sourceSkills $skill
    if (-not (Test-Path -LiteralPath $source)) {
        Write-Error "Missing source skill: $source"
    }
}

if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw "Node is required for QSAR skill validation" }
& node (Join-Path $sourceSkills "qsar-autoresearch-harness/scripts/validate_skill_map.js")
if ($LASTEXITCODE -ne 0) { throw "Invalid QSAR source skill assignments" }

New-Item -ItemType Directory -Force -Path $targetSkills | Out-Null

foreach ($skill in $skillNames) {
    $source = Join-Path $sourceSkills $skill
    $target = Join-Path $targetSkills $skill
    $resolvedTarget = [IO.Path]::GetFullPath($target)
    $resolvedSkills = [IO.Path]::GetFullPath($targetSkills).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $resolvedTarget.StartsWith($resolvedSkills, [StringComparison]::OrdinalIgnoreCase)) { throw "Target outside skill directory: $target" }
    if (Test-Path -LiteralPath $target) {
        if ((Get-Item -LiteralPath $target).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing to replace linked skill directory: $target" }
        Remove-Item -Recurse -Force -LiteralPath $target
    }
    Copy-Item -Recurse -Force -LiteralPath $source -Destination $targetSkills
    Write-Output "Installed $skill -> $target"
}

$targetHarnessSkill = Join-Path $targetSkills "qsar-autoresearch-harness"
foreach ($notice in @("LICENSE", "NOTICE", "LICENSE_AUDIT.md", "THIRD_PARTY_NOTICES.md")) {
    $source = Join-Path $packRoot $notice
    if (Test-Path -LiteralPath $source) {
        Copy-Item -Force -LiteralPath $source -Destination $targetHarnessSkill
    }
}

$validator = Join-Path $targetSkills "qsar-autoresearch-harness\scripts/validate_harness.ps1"
& $validator

Write-Output "Restart your agent runtime so its skill registry reloads."
