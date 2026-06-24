param(
    [string]$HarnessRoot,
    [string]$ProtocolSkill
)

$ErrorActionPreference = "Stop"

$skill = Get-Content -Raw -LiteralPath (Join-Path $HarnessRoot "SKILL.md")
$protocol = Get-Content -Raw -LiteralPath $ProtocolSkill
$combined = "$skill`n$protocol"

foreach ($needle in @(
    "dataset contract",
    "in-vivo",
    "leakage",
    "next_experiment_manifest",
    "training_summary.html",
    "train/test counts",
    "label balance"
)) {
    if ($combined -notlike "*$needle*") {
        Write-Error "Dependency contract missing required concept: $needle"
    }
}

foreach ($tool in @("Python", "pandas", "scikit-learn", "openpyxl", "RDKit")) {
    if ($combined -notmatch [regex]::Escape($tool)) {
        Write-Error "Dependency contract missing runtime mention: $tool"
    }
}
