param([string]$AgentHome)
$ErrorActionPreference = 'Stop'
$packRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$manager = Join-Path $packRoot '../../bin/aaalab.js'
$arguments = @($manager, 'install', 'classroom-slide-design-harness')
if ($AgentHome) { $arguments += @('--agent-home', $AgentHome) }
& node @arguments
if ($LASTEXITCODE -ne 0) { throw 'Classroom slide design harness installation failed.' }
