param(
    [Alias("CodexHome")]
    [string]$AgentHome = $(if ($env:AAALAB_AGENT_HOME) { $env:AAALAB_AGENT_HOME } elseif ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" })
)

$ErrorActionPreference = "Stop"
$packRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$sourceSkills = Join-Path $packRoot "skills"
$targetSkills = Join-Path $AgentHome "skills"
$skills = @("research-manuscript", "research-manuscript-harness")

foreach ($skill in $skills) {
    $source = Join-Path $sourceSkills $skill
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Missing source skill: $source"
    }
}

New-Item -ItemType Directory -Force -Path $targetSkills | Out-Null
foreach ($skill in $skills) {
    $source = Join-Path $sourceSkills $skill
    $target = Join-Path $targetSkills $skill
    if (Test-Path -LiteralPath $target) {
        Remove-Item -Recurse -Force -LiteralPath $target
    }
    Copy-Item -Recurse -Force -LiteralPath $source -Destination $targetSkills
    Write-Output "Installed $skill -> $target"
}

$targetHarnessSkill = Join-Path $targetSkills "research-manuscript-harness"
foreach ($notice in @("NOTICE", "LICENSE_AUDIT.md", "THIRD_PARTY_NOTICES.md")) {
    $source = Join-Path $packRoot $notice
    if (Test-Path -LiteralPath $source) {
        Copy-Item -Force -LiteralPath $source -Destination $targetHarnessSkill
    }
}

& (Join-Path $targetHarnessSkill "scripts\validate_harness.ps1")
Write-Output "Restart your agent runtime so its skill registry reloads."
