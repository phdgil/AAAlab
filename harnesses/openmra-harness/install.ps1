param(
    [Alias("CodexHome")]
    [string]$AgentHome = $(if ($env:AAALAB_AGENT_HOME) { $env:AAALAB_AGENT_HOME } elseif ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" })
)

$ErrorActionPreference = "Stop"

$packRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$source = Join-Path $packRoot "skills\openmra-harness"
$targetSkills = Join-Path $AgentHome "skills"
$target = Join-Path $targetSkills "openmra-harness"

if (-not (Test-Path -LiteralPath $source)) {
    Write-Error "Missing source skill: $source"
}

New-Item -ItemType Directory -Force -Path $targetSkills | Out-Null
if (Test-Path -LiteralPath $target) {
    Write-Error "Install target already exists: $target. Use 'aaalab install openmra-harness --agent-home <path>' for a managed update, or move the existing installation aside first."
}
Copy-Item -Recurse -Force -LiteralPath $source -Destination $targetSkills

foreach ($notice in @("LICENSE", "NOTICE", "LICENSE_AUDIT.md", "THIRD_PARTY_NOTICES.md")) {
    $noticeSource = Join-Path $packRoot $notice
    if (Test-Path -LiteralPath $noticeSource) {
        Copy-Item -Force -LiteralPath $noticeSource -Destination $target
    }
}

& (Join-Path $target "scripts\validate_harness.ps1")
Write-Output "Installed openmra-harness -> $target"
Write-Output "Restart your agent runtime so its skill registry reloads."
