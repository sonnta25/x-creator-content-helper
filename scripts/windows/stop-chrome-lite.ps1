param(
    [string] $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"
$watchdogStop = Join-Path $PSScriptRoot "stop-chrome-watchdog.ps1"
if (Test-Path -LiteralPath $watchdogStop) {
    & $watchdogStop -ProjectRoot $ProjectRoot
}
$profileDir = Join-Path $ProjectRoot "data\chrome-profile"
$chromeProcesses = Get-CimInstance Win32_Process -Filter "name = 'chrome.exe'" |
    Where-Object { $_.CommandLine -like "*--user-data-dir=$profileDir*" }

foreach ($process in $chromeProcesses) {
    Stop-Process -Id $process.ProcessId -Force
}

Write-Host "Stopped $(@($chromeProcesses).Count) low-memory Chrome process(es)."
