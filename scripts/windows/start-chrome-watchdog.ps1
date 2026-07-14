param(
    [string] $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [ValidateRange(10, 300)]
    [int] $IntervalSeconds = 30,
    [ValidateRange(1, 255)]
    [int] $CpuAffinityMask = 1
)

$ErrorActionPreference = "Stop"
$logDir = Join-Path $ProjectRoot "logs"
$pidPath = Join-Path $logDir "chrome-watchdog.pid"
$watchScript = Join-Path $PSScriptRoot "watch-chrome-lite.ps1"

if (Test-Path -LiteralPath $pidPath) {
    $existingPid = 0
    [void][int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$existingPid)
    if ($existingPid -and (Get-Process -Id $existingPid -ErrorAction SilentlyContinue)) {
        Write-Host "Chrome watchdog is already running. PID=$existingPid"
        exit 0
    }
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Force $logDir | Out-Null
$process = Start-Process `
    -FilePath "powershell.exe" `
    -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $watchScript,
        "-ProjectRoot", $ProjectRoot,
        "-IntervalSeconds", $IntervalSeconds,
        "-CpuAffinityMask", $CpuAffinityMask
    ) `
    -WindowStyle Hidden `
    -PassThru

Write-Host "Chrome watchdog started. PID=$($process.Id), CPU affinity mask=$CpuAffinityMask"
