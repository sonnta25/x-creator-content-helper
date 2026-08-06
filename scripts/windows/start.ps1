param(
    [string] $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"

$pythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    throw "Python venv was not found. Run scripts\windows\setup.ps1 first."
}

# Check before changing the venv. Updating dependencies underneath a live bot
# can leave its imported modules and on-disk packages out of sync.
$knownBots = Get-CimInstance Win32_Process -Filter "name = 'python.exe'" |
    Where-Object {
        $_.CommandLine -like "*-m src.main*" -and
        (
            $_.CommandLine -like "*$pythonExe*" -or
            $_.CommandLine -like "*x-content-bot*" -or
            $_.CommandLine -like "*x-creator-content-helper*"
        )
    }
$currentBots = $knownBots | Where-Object { $_.CommandLine -like "*$pythonExe*" }
$legacyBots = $knownBots | Where-Object { $_.CommandLine -notlike "*$pythonExe*" }

if ($legacyBots) {
    $legacyIds = ($legacyBots | Select-Object -ExpandProperty ProcessId) -join ", "
    throw "Another X Content Bot copy is already running (PID(s): $legacyIds). Run scripts\windows\restart.ps1 from the updated folder."
}
if ($currentBots) {
    $ids = ($currentBots | Select-Object -ExpandProperty ProcessId) -join ", "
    Write-Host "Bot is already running. PID(s): $ids"
    Write-Host "Use scripts\windows\restart.ps1 after updating code."
    exit 0
}

$syncScript = Join-Path $PSScriptRoot "sync-dependencies.ps1"
if (-not (Test-Path -LiteralPath $syncScript)) {
    throw "Dependency sync script was not found: $syncScript"
}
& $syncScript -ProjectRoot $ProjectRoot

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
