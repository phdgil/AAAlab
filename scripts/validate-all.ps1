$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$harnessRoot = Join-Path $repoRoot "harnesses"

if (-not (Test-Path -LiteralPath $harnessRoot)) {
    Write-Error "Missing harnesses directory: $harnessRoot"
}

$validators = Get-ChildItem -LiteralPath $harnessRoot -Recurse -Filter validate_harness.ps1
if ($validators.Count -eq 0) {
    Write-Error "No harness validators found."
}

foreach ($validator in $validators) {
    Write-Output "Running $($validator.FullName)"
    & $validator.FullName
}

Write-Output "All harness validators passed."
