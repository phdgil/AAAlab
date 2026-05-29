param(
    [string]$HarnessRoot = "",
    [string]$ProtocolSkill = "",
    [string]$LicenseAudit = "",
    [string]$ThirdPartyNotices = ""
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
if ([string]::IsNullOrWhiteSpace($LicenseAudit)) {
    $LicenseAudit = Join-Path (Split-Path -Parent (Split-Path -Parent $HarnessRoot)) "LICENSE_AUDIT.md"
}
if ([string]::IsNullOrWhiteSpace($ThirdPartyNotices)) {
    $ThirdPartyNotices = Join-Path (Split-Path -Parent (Split-Path -Parent $HarnessRoot)) "THIRD_PARTY_NOTICES.md"
}

function Assert-Contains {
    param(
        [string]$Content,
        [string]$Pattern,
        [string]$Label
    )

    if ($Content -notmatch $Pattern) {
        Write-Error "Dependency contract missing: $Label"
    }
}

$harnessSkillText = Get-Content -Raw -LiteralPath (Join-Path $HarnessRoot "SKILL.md")
$protocolSkillText = Get-Content -Raw -LiteralPath $ProtocolSkill
$protocolReference = Get-Content -Raw -LiteralPath (Join-Path (Split-Path -Parent $ProtocolSkill) "references\vina-draft-protocol.md")
$licenseAuditText = Get-Content -Raw -LiteralPath $LicenseAudit
$thirdPartyText = Get-Content -Raw -LiteralPath $ThirdPartyNotices
$combined = "$harnessSkillText`n$protocolSkillText`n$protocolReference"

Assert-Contains $harnessSkillText "Verify at least one PDBQT prep path exists: Meeko, MGLTools/AutoDockTools, or Open Babel" "harness preflight must preserve all PDBQT prep alternatives"
Assert-Contains $harnessSkillText "Check RDKit or another conformer path" "harness must require a conformer path for SMILES or 2D ligand generation"
Assert-Contains $harnessSkillText "If gnina is requested, check Docker or native gnina" "gnina must remain optional and explicitly checked"
Assert-Contains $harnessSkillText "If Vina or all PDBQT preparation paths are missing, stop and report missing dependencies" "missing runtime dependencies must stop execution"
Assert-Contains $protocolSkillText "Verify ``vina -h`` works" "base protocol must verify Vina before docking"
Assert-Contains $protocolSkillText "If ``vina`` or PDBQT preparation tools are missing, stop" "base protocol must fail closed when required tools are missing"
Assert-Contains $protocolSkillText "never parse the CNN pose score as CNN affinity" "gnina parser safety rule must remain"

foreach ($tool in @("AutoDock Vina", "gnina", "Meeko", "MGLTools", "AutoDockTools", "Open Babel", "RDKit", "Datamol", "PDBFixer", "3Dmol.js", "py3Dmol", "Docker")) {
    Assert-Contains $combined ([regex]::Escape($tool)) "workflow/protocol must still mention $tool when it is part of the supported dependency model"
}

foreach ($tool in @("AutoDock Vina", "gnina", "Meeko", "Open Babel", "RDKit", "Datamol", "PDBFixer", "3Dmol.js", "py3Dmol")) {
    Assert-Contains $licenseAuditText ([regex]::Escape($tool)) "license audit must mention $tool"
    Assert-Contains $thirdPartyText ([regex]::Escape($tool)) "third-party notices must mention $tool"
}

$forbiddenExtensions = @("*.exe", "*.dll", "*.so", "*.dylib", "*.whl", "*.tar", "*.gz", "*.zip")
$vendored = foreach ($pattern in $forbiddenExtensions) {
    Get-ChildItem -LiteralPath (Split-Path -Parent (Split-Path -Parent $HarnessRoot)) -Recurse -File -Filter $pattern -ErrorAction SilentlyContinue
}

if ($vendored.Count -gt 0) {
    $names = ($vendored | Select-Object -ExpandProperty FullName) -join ", "
    Write-Error "Potential vendored binary/archive dependencies found: $names"
}

Write-Output "AutoDock Vina dependency contract OK."
