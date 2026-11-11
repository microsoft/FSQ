# Runs FSQ's official device authorization on the remote account without exporting credentials.
$ErrorActionPreference = 'Stop'
$python = $env:DRAGON_VAR_RUNTIME_PYTHON
if (-not $python -or -not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'The deployed remote FSQ interpreter is missing.'
}
$env:PYTHONIOENCODING = 'utf-8'
& $python -u -c 'from fsq_agent.adapters.cli import main; main(args=["providers","configure","github_copilot","--model","gpt-5.6-sol-fast"],prog_name="fsq")'
exit $LASTEXITCODE
