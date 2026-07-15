param(
    [string] $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [switch] $NoWatchdog,
    [ValidateRange(1, 255)]
    [int] $CpuAffinityMask = 1
)

$ErrorActionPreference = "Stop"

function Get-ChromeExe {
    $candidates = @(
        (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
        (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }
    throw "Google Chrome was not found. Install Chrome, then run this script again."
}

function Set-ChromeCpuLimits {
    param([int[]] $ProcessIds)

    foreach ($processId in $ProcessIds) {
        try {
            $chromeProcess = Get-Process -Id $processId -ErrorAction Stop
            $chromeProcess.PriorityClass = "BelowNormal"
            $chromeProcess.ProcessorAffinity = [IntPtr]$CpuAffinityMask
        } catch {
            # A Chrome child can exit between discovery and this adjustment.
        }
    }
}

$chromeExe = Get-ChromeExe
$profileDir = Join-Path $ProjectRoot "data\chrome-profile"
$extensionDir = Join-Path $ProjectRoot "browser_extension"
New-Item -ItemType Directory -Force $profileDir | Out-Null

try {
    $existing = Get-CimInstance Win32_Process -Filter "name = 'chrome.exe'" |
        Where-Object { $_.CommandLine -like "*--user-data-dir=$profileDir*" }
} catch {
    # Some locked-down VPS policies deny WMI command-line reads. Avoid opening
    # another browser in that case if Chrome is already present.
    $existing = Get-Process -Name chrome -ErrorAction SilentlyContinue
}
if ($existing) {
    $processIds = @(
        $existing | ForEach-Object {
            if ($_.PSObject.Properties.Name -contains "ProcessId") { $_.ProcessId } else { $_.Id }
        }
    )
    Set-ChromeCpuLimits -ProcessIds $processIds
    $ids = $processIds -join ", "
    Write-Host "Low-memory Chrome is already running. PID(s): $ids"
    if (-not $NoWatchdog) {
        & (Join-Path $PSScriptRoot "start-chrome-watchdog.ps1") -ProjectRoot $ProjectRoot -CpuAffinityMask $CpuAffinityMask
    }
    exit 0
}

$arguments = @(
    "--user-data-dir=$profileDir",
    "--disable-extensions-except=$extensionDir",
    "--load-extension=$extensionDir",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--disable-breakpad",
    "--mute-audio",
    # One renderer proved too aggressive for Gemini's multi-process web app.
    # Two keeps the VPS bounded without forcing every task into one renderer.
    "--renderer-process-limit=2",
    "--process-per-site",
    # Do not let a short-lived Gemini session build a large on-disk cache.
    "--disk-cache-size=16777216",
    "--media-cache-size=1048576",
    "--disable-logging",
    "--log-level=3",
    "https://gemini.google.com/app"
)

$process = Start-Process -FilePath $chromeExe -ArgumentList $arguments -PassThru
Set-ChromeCpuLimits -ProcessIds @($process.Id)
Write-Host "Low-memory Chrome started. PID=$($process.Id)"
Write-Host "CPU limit: one logical CPU, BelowNormal priority."
Write-Host "Profile: $profileDir"
Write-Host "Sign in to Gemini once in this Chrome window."
if (-not $NoWatchdog) {
    & (Join-Path $PSScriptRoot "start-chrome-watchdog.ps1") -ProjectRoot $ProjectRoot -CpuAffinityMask $CpuAffinityMask
}
