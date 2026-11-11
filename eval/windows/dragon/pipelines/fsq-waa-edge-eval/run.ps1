# Runs the isolated WAA-to-FSQ adapter and preserves evidence when execution fails.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$env:PYTHONIOENCODING = 'utf-8'
& $env:DRAGON_VAR_FSQ_PYTHON (Join-Path $PSScriptRoot 'runner.py') run
$result = $LASTEXITCODE
if ($result -ne 0) { & (Join-Path $PSScriptRoot 'publish.ps1') }
exit $result
