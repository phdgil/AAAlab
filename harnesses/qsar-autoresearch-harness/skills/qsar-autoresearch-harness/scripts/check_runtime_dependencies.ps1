$ErrorActionPreference = "Stop"

$checks = @()

function Add-Check([string]$Name, [bool]$Available, [string]$Source) {
    $script:checks += [pscustomobject]@{ name = $Name; available = $Available; source = $Source }
}

$python = Get-Command python -ErrorAction SilentlyContinue
Add-Check "python" ($null -ne $python) ($(if ($python) { $python.Source } else { "not found" }))

if ($python) {
    foreach ($module in @("pandas", "sklearn", "openpyxl", "rdkit")) {
        $result = & python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('$module') else 1)"
        Add-Check "python module: $module" ($LASTEXITCODE -eq 0) "python"
    }
}

$checks | ConvertTo-Json -Depth 4

if (($checks | Where-Object { -not $_.available }).Count -gt 0) {
    exit 1
}
