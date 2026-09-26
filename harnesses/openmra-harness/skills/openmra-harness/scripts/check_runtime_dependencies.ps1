param(
    [string]$App = $(if ($env:OPENMRA_APP) { $env:OPENMRA_APP } else { "" })
)

$ErrorActionPreference = "Stop"
$failed = $false

if ($env:OS -eq "Windows_NT") {
    Write-Output "Windows: available"
} else {
    Write-Output "Windows: missing (OpenMRA GUI execution requires Windows)"
    $failed = $true
}

$python = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $python) {
    Write-Output "python: missing"
    $failed = $true
} else {
    Write-Output "python: available ($($python.Source))"
    & $python.Source -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
    if ($LASTEXITCODE -eq 0) {
        Write-Output "python >= 3.11: available"
    } else {
        Write-Output "python >= 3.11: missing"
        $failed = $true
    }
    foreach ($module in @("pywinauto", "PIL")) {
        & $python.Source -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('$module') else 1)"
        if ($LASTEXITCODE -eq 0) {
            Write-Output "python module $module: available"
        } else {
            Write-Output "python module $module: missing"
            $failed = $true
        }
    }
}

if ([string]::IsNullOrWhiteSpace($App)) {
    Write-Output "OpenMRA executable: not checked (pass -App or set OPENMRA_APP)"
} elseif (Test-Path -LiteralPath $App -PathType Leaf) {
    if ([System.IO.Path]::GetExtension($App) -ieq ".exe") {
        Write-Output "OpenMRA executable: available ($App)"
    } else {
        Write-Output "OpenMRA executable: invalid extension ($App)"
        $failed = $true
    }
} else {
    Write-Output "OpenMRA executable: missing ($App)"
    $failed = $true
}

if ($failed) {
    Write-Output "Preflight verdict: OpenMRA GUI execution must stop and report missing dependencies."
    exit 1
}

Write-Output "Preflight verdict: required automation dependencies appear available. Run openmra_runner.py preflight with the exact executable before execution."
