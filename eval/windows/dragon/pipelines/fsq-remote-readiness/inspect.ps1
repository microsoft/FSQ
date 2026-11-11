# Inspects remote deployment prerequisites without changing repositories, software, or credentials.
$ErrorActionPreference = 'Stop'
if ($env:DRAGON_VAR_TARGET_TASK) {
    if ($env:DRAGON_VAR_TARGET_TASK -notmatch '^[A-Za-z0-9_-]+$') { throw 'Invalid Task identity.' }
    $evidence = Join-Path $env:USERPROFILE ('.fsq-waa\jobs\' + $env:DRAGON_VAR_TARGET_TASK + '\waa-eval')
    $reportPath = Join-Path $evidence 'results.json'
    if (-not (Test-Path -LiteralPath $reportPath)) { throw 'The target deployment has not written results.' }
    $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
    $report | Select-Object status, execution_machine, summary, errors,
        @{n='tasks';e={$_.tasks|Select-Object id,fsq_status,evaluation_status,benchmark_score,observed,error}} |
        ConvertTo-Json -Depth 7 | Write-Output
    $progress = Join-Path $evidence 'run-stage.stdout.log'
    if (Test-Path -LiteralPath $progress) { Get-Content -LiteralPath $progress -Tail 8 }
    $ownedRoot = if ($report.retained_root) { $report.retained_root } elseif ($report.window_acceptance.profile) { $report.window_acceptance.profile } else { $null }
    if ($ownedRoot) {
        $owners = @(Get-CimInstance Win32_Process -Filter "Name = 'msedge.exe' OR Name = 'python.exe'" |
            Where-Object { $_.CommandLine -and $_.CommandLine.Contains($ownedRoot) })
        Write-Output "Remaining processes for the exact evaluation root: $($owners.Count)"
        if ($owners.Count) { throw 'Evaluation processes are still active.' }
    }
    exit 0
}
$result = [ordered]@{
    machine = $env:COMPUTERNAME
    userProfile = $env:USERPROFILE
    taskWorkDir = $env:DRAGON_TASK_WORK_DIR
    tools = @{}
}
foreach ($name in @('git', 'uv', 'python', 'py', 'fsq', 'pwsh')) {
    $tool = Get-Command $name -ErrorAction Ignore
    if ($tool) { $result.tools[$name] = $tool.Source }
}
$result.edge = @('C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', 'C:\Program Files\Microsoft\Edge\Application\msedge.exe') |
    Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if ($result.tools.ContainsKey('python')) {
    $result.python = (& $result.tools.python --version 2>&1 | Out-String).Trim()
}
if ($result.tools.ContainsKey('uv')) {
    $result.uvTools = (& $result.tools.uv tool list 2>&1 | Out-String).Trim()
}
if ($result.tools.ContainsKey('fsq')) {
    $result.provider = (& $result.tools.fsq --output json --non-interactive providers status 2>&1 | Out-String).Trim()
}
Write-Output ($result | ConvertTo-Json -Depth 6)
$jobs = Join-Path $env:USERPROFILE '.fsq-waa\jobs'
if (Test-Path -LiteralPath $jobs) {
    Get-ChildItem -LiteralPath $jobs -Directory | ForEach-Object {
        Write-Output "Deployment job: $($_.Name)"
        $evidence = Join-Path $_.FullName 'waa-eval'
        if (Test-Path -LiteralPath $evidence) {
            Get-ChildItem -LiteralPath $evidence -Recurse -File |
                Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize
            Get-ChildItem -LiteralPath $evidence -Recurse -Filter '*.stderr.log' |
                ForEach-Object { if ($_.Length) { Write-Output $_.Name; Get-Content -LiteralPath $_.FullName -Tail 8 } }
        }
    }
}
