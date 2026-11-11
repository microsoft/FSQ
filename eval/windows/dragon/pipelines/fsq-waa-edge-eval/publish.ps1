# Publishes only existing per-Task reports and reports any missing deliverables.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $env:DRAGON_TASK_WORK_DIR) { throw 'The Dragon Task work directory is missing.' }
$root = Join-Path $env:DRAGON_TASK_WORK_DIR 'waa-eval'
$files = @{'waa-report' = 'report.html'; 'waa-results' = 'results.json'; 'waa-evidence' = 'evidence.zip'}
$missing = @()
foreach ($name in $files.Keys) {
    $path = Join-Path $root $files[$name]
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { $missing += $name; continue }
    if ($path.Contains('"') -or $path.Contains("`n") -or $path.Contains("`r")) {
        throw 'The artifact path cannot be published safely.'
    }
    Write-Output "[dragon:output name=$name type=file value=`"$path`"]"
}
if ($missing.Count) { throw "Some evaluation artifacts are missing: $($missing -join ', '). Inspect the failed Stage." }
