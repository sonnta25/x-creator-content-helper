param(
    [string] $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"
$pidPath = Join-Path $ProjectRoot "logs\chrome-watchdog.pid"
if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Host "Chrome watchdog is not running."
    exit 0
}

$watchdogPid = 0
[void][int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$watchdogPid)
if ($watchdogPid) {
    Stop-Process -Id $watchdogPid -Force -ErrorAction SilentlyContinue
}
Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
Write-Host "Chrome watchdog stopped."
