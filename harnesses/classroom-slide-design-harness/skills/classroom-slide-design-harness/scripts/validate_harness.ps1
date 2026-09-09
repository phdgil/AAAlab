$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
& python (Join-Path $scriptRoot 'validate_harness.py')
if ($LASTEXITCODE -ne 0) { throw 'Classroom slide design harness validation failed.' }
