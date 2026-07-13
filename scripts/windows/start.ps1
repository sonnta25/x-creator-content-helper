param(
    [string] $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"

$pythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    throw "Python venv was not found. Run scripts\windows\setup.ps1 first."
}

$existing = Get-CimInstance Win32_Process -Filter "name = 'python.exe'" |
    Where-Object {
        $_.CommandLine -like "*$pythonExe*" -and $_.CommandLine -like "*-m src.main*"
    }

if ($existing) {
    $ids = ($existing | Select-Object -ExpandProperty ProcessId) -join ", "
    Write-Host "Bot is already running. PID(s): $ids"
    exit 0
}

$logDir = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$outLog = Join-Path $logDir "bot.out.log"
$errLog = Join-Path $logDir "bot.err.log"

$process = Start-Process `
    -FilePath $pythonExe `
    -ArgumentList @("-m", "src.main") `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -PassThru

Write-Host "Bot started. PID=$($process.Id)"
Write-Host "Logs: $outLog, $errLog"
