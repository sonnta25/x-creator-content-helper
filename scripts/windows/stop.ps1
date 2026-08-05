param(
    [string] $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"
$pythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

$bots = Get-CimInstance Win32_Process -Filter "name = 'python.exe'" |
    Where-Object {
        $_.CommandLine -like "*-m src.main*" -and
        (
            $_.CommandLine -like "*$pythonExe*" -or
            $_.CommandLine -like "*x-content-bot*" -or
            $_.CommandLine -like "*x-creator-content-helper*"
        )
    }

if (-not $bots) {
    Write-Host "Bot is not running."
    exit 0
}

foreach ($bot in $bots) {
    try {
        Stop-Process -Id $bot.ProcessId -Force -ErrorAction Stop
        Write-Host "Stopped bot process PID=$($bot.ProcessId)"
    } catch [Microsoft.PowerShell.Commands.ProcessCommandException] {
        Write-Host "Bot process PID=$($bot.ProcessId) already exited."
    }
}
