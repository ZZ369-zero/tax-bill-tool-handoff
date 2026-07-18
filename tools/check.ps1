param(
    [switch]$InstallDependencies
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

Push-Location $ProjectRoot
try {
    if ($InstallDependencies) {
        python -m pip install -r requirements.txt
    }

    python -m unittest discover -s tests -v
    python -c "from web_app.app import health; assert health()['status'] == 'ok'; print('Health import OK')"

    Write-Host "Local checks passed."
}
finally {
    Pop-Location
}
