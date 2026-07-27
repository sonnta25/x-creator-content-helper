param(
    [string] $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [ValidateRange(1, 168)]
    [int] $Hours = 24
)

$ErrorActionPreference = "Stop"
$profileDir = Join-Path $ProjectRoot "data\chrome-profile"
$logDir = Join-Path $ProjectRoot "logs"
$reportPath = Join-Path $logDir ("chrome-diagnosis-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".txt")
$startTime = (Get-Date).AddHours(-$Hours)
New-Item -ItemType Directory -Force $logDir | Out-Null

function Add-Section {
    param([string] $Title, [object[]] $Rows)

    Add-Content -LiteralPath $reportPath -Value "`r`n=== $Title ==="
    if (-not $Rows -or $Rows.Count -eq 0) {
        Add-Content -LiteralPath $reportPath -Value "No matching entries."
        return
    }
    $Rows | Out-String -Width 240 | Add-Content -LiteralPath $reportPath
}

function Get-EventsSafely {
    param([string] $LogName, [int[]] $Ids, [string[]] $Providers)

    try {
        $events = Get-WinEvent -FilterHashtable @{ LogName = $LogName; StartTime = $startTime } -ErrorAction Stop
        return @(
            $events |
                Where-Object {
                    ($Ids -contains $_.Id) -and
                    (-not $Providers -or $Providers -contains $_.ProviderName)
                } |
                Select-Object -First 30 TimeCreated, Id, ProviderName, LevelDisplayName, Message
        )
    } catch {
        return @([pscustomobject]@{ Message = "Could not read ${LogName}: $($_.Exception.Message)" })
    }
}

Set-Content -LiteralPath $reportPath -Value @"
Chrome Lite diagnosis
Created: $(Get-Date -Format s)
Lookback: $Hours hour(s)
Profile: $profileDir

Interpretation:
- Resource-Exhaustion-Detector 2004 indicates Windows detected memory or commit exhaustion.
- Application Error / Windows Error Reporting entries indicate a Chrome crash or hang.
- No local event plus a watchdog restart can indicate a VPS host/provider kill or an external policy.
"@ -Encoding utf8

try {
    $chrome = Get-CimInstance Win32_Process -Filter "name = 'chrome.exe'" |
        Where-Object { $_.CommandLine -like "*--user-data-dir=$profileDir*" } |
        Select-Object ProcessId, ParentProcessId, CreationDate, CommandLine
} catch {
    $chrome = Get-Process -Name chrome -ErrorAction SilentlyContinue |
        Select-Object Id, ProcessName, StartTime, CPU, WorkingSet64, PrivateMemorySize64
}
Add-Section -Title "Current Chrome Lite processes" -Rows @($chrome)

$watchdogLog = Join-Path $logDir "chrome-watchdog.log"
if (Test-Path -LiteralPath $watchdogLog) {
    Add-Section -Title "Watchdog restarts" -Rows @(Get-Content -LiteralPath $watchdogLog -Tail 100)
} else {
    Add-Section -Title "Watchdog restarts" -Rows @()
}

Add-Section -Title "Windows resource exhaustion" -Rows (
    Get-EventsSafely -LogName "System" -Ids @(2004) -Providers @("Microsoft-Windows-Resource-Exhaustion-Detector")
)
Add-Section -Title "Chrome crash or hang events" -Rows (
    Get-EventsSafely -LogName "Application" -Ids @(1000, 1001, 1002) -Providers @("Application Error", "Windows Error Reporting", "Application Hang") |
        Where-Object { $_.Message -match "chrome\.exe|Chrome" }
)
Add-Section -Title "Windows Defender events" -Rows (
    Get-EventsSafely -LogName "Microsoft-Windows-Windows Defender/Operational" -Ids @(1116, 1117, 1121) -Providers @("Microsoft-Windows-Windows Defender")
)

$crashDirs = @(
    (Join-Path $profileDir "Crashpad\reports"),
    (Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data\Crashpad\reports")
)
$crashes = foreach ($crashDir in $crashDirs) {
    if (Test-Path -LiteralPath $crashDir) {
        Get-ChildItem -LiteralPath $crashDir -File -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTime -ge $startTime } |
            Select-Object LastWriteTime, Length, FullName
    }
}
Add-Section -Title "Chrome crash reports" -Rows @($crashes)

Write-Host "Diagnosis saved: $reportPath"
