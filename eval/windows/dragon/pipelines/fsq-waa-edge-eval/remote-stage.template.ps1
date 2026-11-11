# Unpacks the complete pinned evaluation payload on the executing Windows Machine.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $IsWindows) { throw 'The packaged native evaluation requires Windows.' }
if ($env:DRAGON_VAR_EXPECTED_HOST -and $env:DRAGON_VAR_EXPECTED_HOST -ine $env:COMPUTERNAME) {
    throw 'The selected Windows hostname differs from expected-host. No deployment was started.'
}
if (-not $env:DRAGON_TASK_ID) { throw 'The Dragon Task identity is missing.' }
if ($env:DRAGON_TASK_ID -notmatch '^[A-Za-z0-9_-]+$') { throw 'The Dragon Task identity is invalid.' }
$root = Join-Path $env:USERPROFILE ('.fsq-waa\jobs\' + $env:DRAGON_TASK_ID)
if ($root.Length -gt 100) { throw 'The remote job root is too long. Use a Windows account with a shorter profile root.' }
if (Test-Path -LiteralPath $root) { throw 'This Task already has deployment state. Start a fresh Task instead of overwriting it.' }
[void][IO.Directory]::CreateDirectory($root)
$env:DRAGON_TASK_WORK_DIR = $root
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$env:DRAGON_VAR_OPERATION_MODE = '__OPERATION_MODE__'
$bytes = [Convert]::FromBase64String('__PAYLOAD_BASE64__')
$actual = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
if ($actual -ne '__PAYLOAD_SHA256__') { throw 'The transported evaluation package checksum does not match.' }
$zip = Join-Path $root 'package.zip'
[IO.File]::WriteAllBytes($zip, $bytes)
$package = Join-Path $root 'package'
Expand-Archive -LiteralPath $zip -DestinationPath $package
$bootstrapLogs = Join-Path $root 'waa-eval\bootstrap'
[void][IO.Directory]::CreateDirectory($bootstrapLogs)
$status = 1
try {
$python = $env:DRAGON_VAR_BOOTSTRAP_PYTHON
if (-not $python) {
    $tools = Join-Path $env:USERPROFILE '.fsq-waa\tools'
    $uvRoot = Join-Path $tools 'uv-0.9.9'
    $uv = Join-Path $uvRoot 'uv.exe'
    if (-not (Test-Path -LiteralPath $uv -PathType Leaf)) {
        [void][IO.Directory]::CreateDirectory($tools)
        $download = Join-Path $root 'uv-0.9.9.zip'
        Invoke-WebRequest -Uri 'https://github.com/astral-sh/uv/releases/download/0.9.9/uv-x86_64-pc-windows-msvc.zip' -OutFile $download -TimeoutSec 180
        $digest = (Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($digest -ne 'b1c5d78c5cb6804e556e07eeafbcdea7378107c2bd5c342dea0cdc05a607d8e5') {
            throw 'The downloaded official uv asset failed its pinned release checksum.'
        }
        Expand-Archive -LiteralPath $download -DestinationPath $uvRoot
    }
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $tools 'python'
    $env:UV_PYTHON_BIN_DIR = Join-Path $tools 'bin'
    $env:UV_CACHE_DIR = Join-Path $tools 'cache'
    & $uv python install 3.12.12 1> (Join-Path $bootstrapLogs 'python-install.stdout.log') 2> (Join-Path $bootstrapLogs 'python-install.stderr.log')
    if ($LASTEXITCODE -ne 0) { throw 'The remote private Python installation failed.' }
    $python = (& $uv python find --managed-python 3.12.12 2> (Join-Path $bootstrapLogs 'python-find.stderr.log') | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'The installed private Python could not be located.' }
}
if (-not $python) { throw 'Python 3.11+ is required on the remote Machine. Set bootstrap-python to its executable path.' }
    & $python -u (Join-Path $package 'portable_bootstrap.py')
    $status = $LASTEXITCODE
} catch {
    $failure = $_
    $evidence = Join-Path $root 'waa-eval'
    [void][IO.Directory]::CreateDirectory($evidence)
    if (-not (Test-Path -LiteralPath (Join-Path $evidence 'results.json'))) {
        @{status='blocked'; execution_machine=$env:COMPUTERNAME; tasks=@(); errors=@(@{message=$failure.Exception.Message})} |
            ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $evidence 'results.json') -Encoding utf8
        $safe = [Net.WebUtility]::HtmlEncode($failure.Exception.Message)
        "<!doctype html><meta charset=`"utf-8`"><h1>Remote deployment blocked</h1><p>$safe</p>" |
            Set-Content -LiteralPath (Join-Path $evidence 'report.html') -Encoding utf8
    }
    if (-not (Test-Path -LiteralPath (Join-Path $evidence 'evidence.zip'))) {
        $entries = @(Get-ChildItem -LiteralPath $evidence | Select-Object -ExpandProperty FullName)
        Compress-Archive -LiteralPath $entries -DestinationPath (Join-Path $evidence 'evidence.zip')
    }
    Write-Output "Remote deployment blocked: $($failure.Exception.Message)"
} finally {
    $outputs = @{'waa-report' = 'report.html'; 'waa-results' = 'results.json'; 'waa-evidence' = 'evidence.zip'}
    foreach ($name in $outputs.Keys) {
        $path = Join-Path $root ('waa-eval\' + $outputs[$name])
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            Write-Output "[dragon:output name=$name type=file value=`"$path`"]"
        }
    }
}
exit $status
