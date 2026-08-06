param(
    [string] $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"
$pythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

$knownBots = Get-CimInstance Win32_Process -Filter "name = 'python.exe'" |
    Where-Object {
        $_.CommandLine -like "*-m src.main*" -and
        (
            $_.CommandLine -like "*$pythonExe*" -or
            $_.CommandLine -like "*x-content-bot*" -or
            $_.CommandLine -like "*x-creator-content-helper*"
        )
    }

foreach ($bot in $knownBots) {
    Stop-Process -Id $bot.ProcessId -Force -ErrorAction Stop
    Write-Host "Stopped X Content Bot PID=$($bot.ProcessId)"
}

$remaining = Get-CimInstance Win32_Process -Filter "name = 'python.exe'" |
    Where-Object {
        $_.CommandLine -like "*-m src.main*" -and
        (
            $_.CommandLine -like "*$pythonExe*" -or
            $_.CommandLine -like "*x-content-bot*" -or
            $_.CommandLine -like "*x-creator-content-helper*"
        )
    }
if ($remaining) {
    $remainingIds = ($remaining | Select-Object -ExpandProperty ProcessId) -join ", "
    throw "Could not stop the previous X Content Bot process(es): $remainingIds"
}

& (Join-Path $PSScriptRoot "start.ps1") -ProjectRoot $ProjectRoot
