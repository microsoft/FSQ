# Checks the selected Machine and immutable Dataset before this local Windows evaluation.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $IsWindows) { throw 'Choose an unlocked Windows Machine for this Pipeline.' }
if (-not $env:DRAGON_VAR_FSQ_PYTHON -or -not (Test-Path -LiteralPath $env:DRAGON_VAR_FSQ_PYTHON -PathType Leaf)) {
    throw 'Set fsq-python to the absolute python.exe path of the FSQ environment on this Machine.'
}
if (-not $env:DRAGON_TASK_WORK_DIR) { throw 'The Dragon Task work directory is missing.' }
$env:PYTHONIOENCODING = 'utf-8'
& $env:DRAGON_VAR_FSQ_PYTHON (Join-Path $PSScriptRoot 'runner.py') prepare
$result = $LASTEXITCODE
if ($result -ne 0) { & (Join-Path $PSScriptRoot 'publish.ps1') }
exit $result
