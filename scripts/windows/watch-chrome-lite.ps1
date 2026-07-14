param(
    [string] $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [ValidateRange(10, 300)]
    [int] $IntervalSeconds = 30,
    [ValidateRange(1, 255)]
    [int] $CpuAffinityMask = 1
)

$ErrorActionPreference = "Stop"
$profileDir = Join-Path $ProjectRoot "data\chrome-profile"
$logDir = Join-Path $ProjectRoot "logs"
$pidPath = Join-Path $logDir "chrome-watchdog.pid"
$logPath = Join-Path $logDir "chrome-watchdog.log"
$startScript = Join-Path $PSScriptRoot "start-chrome-lite.ps1"
New-Item -ItemType Directory -Force $logDir | Out-Null
Set-Content -LiteralPath $pidPath -Value $PID -Encoding ascii

try {
    while ($true) {
        try {
            $chrome = Get-CimInstance Win32_Process -Filter "name = 'chrome.exe'" |
                Where-Object { $_.CommandLine -like "*--user-data-dir=$profileDir*" }
        } catch {
            $chrome = Get-Process -Name chrome -ErrorAction SilentlyContinue
        }
        if (-not $chrome) {
            Add-Content -LiteralPath $logPath -Value "$(Get-Date -Format s) Chrome missing; starting low-memory Chrome."
            & $startScript -ProjectRoot $ProjectRoot -NoWatchdog -CpuAffinityMask $CpuAffinityMask | Out-Null
        } else {
            foreach ($entry in $chrome) {
                $processId = if ($entry.PSObject.Properties.Name -contains "ProcessId") { $entry.ProcessId } else { $entry.Id }
                try {
                    $chromeProcess = Get-Process -Id $processId -ErrorAction Stop
                    $chromeProcess.PriorityClass = "BelowNormal"
                    $chromeProcess.ProcessorAffinity = [IntPtr]$CpuAffinityMask
                } catch {
                    # Ignore short-lived Chrome child processes.
                }
            }
        }
        Start-Sleep -Seconds $IntervalSeconds
    }
} finally {
    if (Test-Path -LiteralPath $pidPath) {
        $storedPid = (Get-Content -LiteralPath $pidPath -Raw).Trim()
        if ($storedPid -eq "$PID") {
            Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
        }
    }
}
