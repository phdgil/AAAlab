$ErrorActionPreference = 'Stop'
& python -X utf8 (Join-Path $PSScriptRoot 'validate_harness.py')
if ($LASTEXITCODE -ne 0) { throw 'Korean document style harness validation failed.' }
