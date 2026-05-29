param(
    [Alias("CodexHome")]
    [string]$AgentHome = $(if ($env:AAALAB_AGENT_HOME) { $env:AAALAB_AGENT_HOME } elseif ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" }),
    [string]$Harness = "*"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$harnessRoot = Join-Path $repoRoot "harnesses"

if (-not (Test-Path -LiteralPath $harnessRoot)) {
    Write-Error "Missing harnesses directory: $harnessRoot"
}

$selected = Get-ChildItem -LiteralPath $harnessRoot -Directory | Where-Object { $_.Name -like $Harness }
if ($selected.Count -eq 0) {
    Write-Error "No harness matched: $Harness"
}

foreach ($harnessDir in $selected) {
    $installer = Join-Path $harnessDir.FullName "install.ps1"
    if (Test-Path -LiteralPath $installer) {
        Write-Output "Installing harness: $($harnessDir.Name)"
        & $installer -AgentHome $AgentHome
    } else {
        Write-Warning "Skipping $($harnessDir.Name): no install.ps1 found."
    }
}
