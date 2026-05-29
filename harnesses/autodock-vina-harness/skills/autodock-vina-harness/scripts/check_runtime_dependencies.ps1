param(
    [switch]$RequireDocking,
    [switch]$RequireGnina
)

$ErrorActionPreference = "Stop"

function Test-CommandAvailable {
    param([string]$Name)
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) {
        [pscustomobject]@{
            Name = $Name
            Available = $true
            Source = $command.Source
        }
    } else {
        [pscustomobject]@{
            Name = $Name
            Available = $false
            Source = ""
        }
    }
}

function Test-PythonModule {
    param([string]$Name)
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        return [pscustomobject]@{
            Name = "python module: $Name"
            Available = $false
            Source = "python not found"
        }
    }

    $code = "import importlib.util; import sys; sys.exit(0 if importlib.util.find_spec('$Name') else 1)"
    & python -c $code *> $null
    [pscustomobject]@{
        Name = "python module: $Name"
        Available = ($LASTEXITCODE -eq 0)
        Source = "python"
    }
}

$checks = @()
$checks += Test-CommandAvailable "vina"
$checks += Test-CommandAvailable "obabel"
$checks += Test-CommandAvailable "mk_prepare_receptor.py"
$checks += Test-CommandAvailable "mk_prepare_ligand.py"
$checks += Test-CommandAvailable "prepare_receptor4.py"
$checks += Test-CommandAvailable "prepare_ligand4.py"
$checks += Test-CommandAvailable "gnina"
$checks += Test-CommandAvailable "docker"
$checks += Test-CommandAvailable "python"
$checks += Test-PythonModule "rdkit"
$checks += Test-PythonModule "meeko"
$checks += Test-PythonModule "py3Dmol"

$checks | Format-Table -AutoSize

$vinaAvailable = ($checks | Where-Object { $_.Name -eq "vina" }).Available
$prepAvailable = @(
    ($checks | Where-Object { $_.Name -eq "obabel" }).Available,
    ($checks | Where-Object { $_.Name -eq "mk_prepare_receptor.py" }).Available,
    ($checks | Where-Object { $_.Name -eq "mk_prepare_ligand.py" }).Available,
    ($checks | Where-Object { $_.Name -eq "prepare_receptor4.py" }).Available,
    ($checks | Where-Object { $_.Name -eq "prepare_ligand4.py" }).Available,
    ($checks | Where-Object { $_.Name -eq "python module: meeko" }).Available
) -contains $true

$gninaAvailable = @(
    ($checks | Where-Object { $_.Name -eq "gnina" }).Available,
    ($checks | Where-Object { $_.Name -eq "docker" }).Available
) -contains $true

if ($RequireDocking -and (-not $vinaAvailable -or -not $prepAvailable)) {
    Write-Error "Docking runtime is not ready: Vina and at least one PDBQT preparation path are required."
}

if ($RequireGnina -and (-not $gninaAvailable)) {
    Write-Error "gnina runtime is not ready: native gnina or Docker is required."
}

if (-not $vinaAvailable -or -not $prepAvailable) {
    Write-Output "Preflight verdict: real docking must stop and report missing dependencies."
} else {
    Write-Output "Preflight verdict: Vina docking runtime appears available."
}

if (-not $gninaAvailable) {
    Write-Output "gnina verdict: gnina follow-up must be skipped unless installed."
} else {
    Write-Output "gnina verdict: gnina follow-up may proceed after a real CNN smoke test when GPU scoring is intended."
}
