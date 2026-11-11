# Resolves the configured remote Git source before FSQ execution without updating user checkouts.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$env:PYTHONIOENCODING = 'utf-8'
& $env:DRAGON_VAR_FSQ_PYTHON (Join-Path $PSScriptRoot 'git_source.py')
$result = $LASTEXITCODE
if ($result -ne 0) { & (Join-Path $PSScriptRoot 'publish.ps1') }
exit $result
