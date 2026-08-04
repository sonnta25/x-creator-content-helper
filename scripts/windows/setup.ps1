param(
    [string] $TelegramBotToken = "",
    [string] $TaskName = "XContentTelegramBot",
    [switch] $EnableVpsRestartSchedule,
    [switch] $SkipPythonInstall,
    [switch] $SkipOllamaInstall,
    [switch] $NoScheduledTask
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function Get-ExistingEnvValue {
    param([string] $Name)
    $envPath = Join-Path $ProjectRoot ".env"
    if (-not (Test-Path -LiteralPath $envPath)) { return "" }
    $line = Get-Content -LiteralPath $envPath |
        Where-Object { $_ -match "^$([regex]::Escape($Name))=" } |
        Select-Object -First 1
    if (-not $line) { return "" }
    return ($line -replace "^$([regex]::Escape($Name))=", "").Trim()
}

function Get-EnvValue {
    param([string] $Name, [string] $Default = "")
    $value = Get-ExistingEnvValue -Name $Name
    if ($value) { return $value }
    return $Default
}

function Get-CommandFile {
    param($Command)
    if (-not $Command) { return "" }
    foreach ($property in @("Path", "Source", "Definition")) {
        $value = $Command.$property
        if ($value) { return $value }
    }
    return ""
}

function Test-PythonCommand {
    param($PythonCommand)
    if (-not $PythonCommand -or -not $PythonCommand["File"]) { return $false }
    $args = @($PythonCommand["Args"]) + @(
        "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
    )
    try {
        & $PythonCommand["File"] @args | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Get-PythonCommand {
    $candidates = @(
        @{ Command = (Get-Command py -ErrorAction SilentlyContinue); Args = @("-3") },
        @{ Command = (Get-Command python -ErrorAction SilentlyContinue); Args = @() },
        @{ Command = (Get-Command python3 -ErrorAction SilentlyContinue); Args = @() }
    )
    foreach ($candidate in $candidates) {
        $command = @{ File = (Get-CommandFile $candidate.Command); Args = $candidate.Args }
        if (Test-PythonCommand $command) { return $command }
    }
    return $null
}

function Install-Python {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    $wingetFile = Get-CommandFile $winget
    if (-not $wingetFile) {
        throw "Python 3.11+ was not found. Install it from python.org with Add to PATH enabled."
    }
    & $wingetFile install --id Python.Python.3.11 --exact `
        --accept-package-agreements --accept-source-agreements --silent
    if ($LASTEXITCODE -ne 0) { throw "winget could not install Python 3.11." }
    $pathParts = @(
        [Environment]::GetEnvironmentVariable("Path", "Machine"),
        [Environment]::GetEnvironmentVariable("Path", "User"),
        $env:Path
    ) | Where-Object { $_ }
    $env:Path = ($pathParts -join ";")
}

Set-Location $ProjectRoot
if (-not $TelegramBotToken) {
    $TelegramBotToken = Get-ExistingEnvValue -Name "TELEGRAM_BOT_TOKEN"
}
if (-not $TelegramBotToken) { $TelegramBotToken = Read-Host "Enter TELEGRAM_BOT_TOKEN" }
if (-not $TelegramBotToken) { throw "TELEGRAM_BOT_TOKEN is required." }
$TelegramBotToken = $TelegramBotToken -replace "\s", ""

$python = Get-PythonCommand
if (-not $python -and -not $SkipPythonInstall) {
    Install-Python
    $python = Get-PythonCommand
}
if (-not $python) { throw "Python 3.11+ was not found." }

$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    $venvArgs = @($python["Args"]) + @("-m", "venv", ".venv")
    & $python["File"] @venvArgs
}

Write-Host "Installing Python dependencies..."
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Could not upgrade pip." }
& (Join-Path $PSScriptRoot "sync-dependencies.ps1") -ProjectRoot $ProjectRoot -Force

# Preserve every active operator setting so upgrades do not reset reply automation.
$envContent = @"
TELEGRAM_BOT_TOKEN=$TelegramBotToken
TELEGRAM_APPROVAL_CHAT_ID=$(Get-EnvValue "TELEGRAM_APPROVAL_CHAT_ID")
TELEGRAM_REPLY_TARGETS_MINUTES=$(Get-EnvValue "TELEGRAM_REPLY_TARGETS_MINUTES")
TELEGRAM_REPLY_TARGETS_UPDATED_AT=$(Get-EnvValue "TELEGRAM_REPLY_TARGETS_UPDATED_AT")
TELEGRAM_REPLY_VIDEO_MINUTES=$(Get-EnvValue "TELEGRAM_REPLY_VIDEO_MINUTES")
TELEGRAM_REPLY_VIDEO_UPDATED_AT=$(Get-EnvValue "TELEGRAM_REPLY_VIDEO_UPDATED_AT")
AUTOMATION_APPROVALS_PATH=$(Get-EnvValue "AUTOMATION_APPROVALS_PATH" "data/automation_approvals.json")
REPLY_TARGET_MAX_AGE_MINUTES=$(Get-EnvValue "REPLY_TARGET_MAX_AGE_MINUTES" "360")
REPLY_TARGET_LANGUAGES=$(Get-EnvValue "REPLY_TARGET_LANGUAGES" "en,ja")
REPLY_TARGET_METRICS_PATH=$(Get-EnvValue "REPLY_TARGET_METRICS_PATH" "data/reply_target_metrics.json")
REPLY_TARGET_MODE=$(Get-EnvValue "REPLY_TARGET_MODE" "balanced")
CREATOR_GOAL=$(Get-EnvValue "CREATOR_GOAL" "qualify")
REPLY_WATCH_PATH=$(Get-EnvValue "REPLY_WATCH_PATH" "data/reply_watchlist.json")
REPLY_TARGET_BATCH_SIZE=$(Get-EnvValue "REPLY_TARGET_BATCH_SIZE" "3")
REPLY_VIDEO_BATCH_SIZE=$(Get-EnvValue "REPLY_VIDEO_BATCH_SIZE" "3")
CREATOR_DAILY_REPLY_CAP=$(Get-EnvValue "CREATOR_DAILY_REPLY_CAP" "500")
REPLY_AUTHOR_DAILY_CAP=$(Get-EnvValue "REPLY_AUTHOR_DAILY_CAP" "5")
REPLY_SESSION_MINUTES=$(Get-EnvValue "REPLY_SESSION_MINUTES" "20")
REPLY_VIDEO_MIN_VIEWS=$(Get-EnvValue "REPLY_VIDEO_MIN_VIEWS" "15000")
REPLY_VIDEO_MAX_AGE_MINUTES=$(Get-EnvValue "REPLY_VIDEO_MAX_AGE_MINUTES" "45")
REPLY_VIDEO_FRAME_ANALYSIS=$(Get-EnvValue "REPLY_VIDEO_FRAME_ANALYSIS" "true")
REPLY_VIDEO_FRAME_COUNT=$(Get-EnvValue "REPLY_VIDEO_FRAME_COUNT" "2")
REPLY_LEARNING_ENABLED=$(Get-EnvValue "REPLY_LEARNING_ENABLED" "true")
REPLY_LEARNING_PATH=$(Get-EnvValue "REPLY_LEARNING_PATH" "data/reply_learning.json")
REVENUE_OPS_PATH=$(Get-EnvValue "REVENUE_OPS_PATH" "data/revenue_ops.json")
REPLY_TRACKING_POLL_MINUTES=$(Get-EnvValue "REPLY_TRACKING_POLL_MINUTES" "5")
REPLY_DAILY_DIGEST_HOUR=$(Get-EnvValue "REPLY_DAILY_DIGEST_HOUR" "22")
STALE_MOBILE_APPROVAL_HOURS=$(Get-EnvValue "STALE_MOBILE_APPROVAL_HOURS" "6")
CREATOR_TIMEZONE=$(Get-EnvValue "CREATOR_TIMEZONE" "Asia/Ho_Chi_Minh")

DOWNLOAD_MAX_FILE_MB=$(Get-EnvValue "DOWNLOAD_MAX_FILE_MB" "45")
DOWNLOAD_TIMEOUT_SECONDS=$(Get-EnvValue "DOWNLOAD_TIMEOUT_SECONDS" "180")
DOWNLOAD_COOKIES_FILE=$(Get-EnvValue "DOWNLOAD_COOKIES_FILE")
DOWNLOAD_COOKIES_FROM_BROWSER=$(Get-EnvValue "DOWNLOAD_COOKIES_FROM_BROWSER")
DOWNLOAD_BROWSER_PROFILE=$(Get-EnvValue "DOWNLOAD_BROWSER_PROFILE")

CONTENT_PROVIDER=extension_bridge
EXTENSION_BRIDGE_HOST=$(Get-EnvValue "EXTENSION_BRIDGE_HOST" "127.0.0.1")
EXTENSION_BRIDGE_PORT=$(Get-EnvValue "EXTENSION_BRIDGE_PORT" "8765")
EXTENSION_BRIDGE_TOKEN=$(Get-EnvValue "EXTENSION_BRIDGE_TOKEN" "local-bridge-change-me")
EXTENSION_BRIDGE_TIMEOUT_SECONDS=$(Get-EnvValue "EXTENSION_BRIDGE_TIMEOUT_SECONDS" "360")

X_COOKIE=$(Get-EnvValue "X_COOKIE")
X_ACCOUNT_NAME=$(Get-EnvValue "X_ACCOUNT_NAME" "telegram_bot")
X_OWNER_USERNAME=$(Get-EnvValue "X_OWNER_USERNAME")
X_ACCOUNTS_DB=$(Get-EnvValue "X_ACCOUNTS_DB" "data/twscrape_accounts.db")
X_SEARCH_LIMIT=$(Get-EnvValue "X_SEARCH_LIMIT" "8")
X_SEARCH_PRODUCT=$(Get-EnvValue "X_SEARCH_PRODUCT" "Top")
REPLY_TARGET_MIN_AUTHOR_FOLLOWERS=$(Get-EnvValue "REPLY_TARGET_MIN_AUTHOR_FOLLOWERS" "50000")
REPLY_TARGET_MIN_VIEWS=$(Get-EnvValue "REPLY_TARGET_MIN_VIEWS" "500")

CREATOR_NICHE=$(Get-EnvValue "CREATOR_NICHE" "gold markets, cryptocurrency, and practical AI tools")
CREATOR_VOICE=$(Get-EnvValue "CREATOR_VOICE" "witty, practical, dry, slightly contrarian, with a sharp creator POV")
TARGET_AUDIENCE=$(Get-EnvValue "TARGET_AUDIENCE" "Vietnamese retail investors, crypto users, creators, founders, and professionals")
"@
Set-Content -LiteralPath (Join-Path $ProjectRoot ".env") -Value $envContent -Encoding UTF8

if (-not $NoScheduledTask) {
    $runScript = Join-Path $ProjectRoot "scripts\windows\run-bot.ps1"
    $taskCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$runScript`""
    & schtasks.exe /Create /F /SC ONLOGON /TN $TaskName /TR $taskCommand | Out-Host
}

if ($EnableVpsRestartSchedule) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $ProjectRoot "scripts\windows\register-vps-restart.ps1")
}

& (Join-Path $ProjectRoot "scripts\windows\stop.ps1")
& (Join-Path $ProjectRoot "scripts\windows\start.ps1")

Write-Host "Setup complete. Try /replytargets, /replyvideo, or /download in Telegram."
