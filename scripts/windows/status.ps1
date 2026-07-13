param(
    [string] $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"

$pythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$bots = Get-CimInstance Win32_Process -Filter "name = 'python.exe'" |
    Where-Object {
        $_.CommandLine -like "*$pythonExe*" -and $_.CommandLine -like "*-m src.main*"
    } |
    Select-Object ProcessId, CreationDate, CommandLine

if ($bots) {
    $bots | Format-Table -AutoSize
} else {
    Write-Host "Bot is not running."
}

$logDir = Join-Path $ProjectRoot "logs"
$errLog = Join-Path $logDir "bot.err.log"
if (Test-Path $errLog) {
    Write-Host ""
    Write-Host "Last stderr lines:"
    Get-Content -Path $errLog -Tail 20
}
