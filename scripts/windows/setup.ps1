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
& $venvPython -m pip install -e .

# Preserve every operator setting that setup owns. This prevents an upgrade from
# erasing the approval chat, schedule interval, trend sources, persona, or image mode.
$envContent = @"
TELEGRAM_BOT_TOKEN=$TelegramBotToken
TELEGRAM_APPROVAL_CHAT_ID=$(Get-EnvValue "TELEGRAM_APPROVAL_CHAT_ID")
TELEGRAM_REPLY_TARGETS_MINUTES=$(Get-EnvValue "TELEGRAM_REPLY_TARGETS_MINUTES")
AUTOMATION_APPROVALS_PATH=$(Get-EnvValue "AUTOMATION_APPROVALS_PATH" "data/automation_approvals.json")

CONTENT_PROVIDER=extension_bridge
EXTENSION_BRIDGE_HOST=$(Get-EnvValue "EXTENSION_BRIDGE_HOST" "127.0.0.1")
EXTENSION_BRIDGE_PORT=$(Get-EnvValue "EXTENSION_BRIDGE_PORT" "8765")
EXTENSION_BRIDGE_TOKEN=$(Get-EnvValue "EXTENSION_BRIDGE_TOKEN" "local-bridge-change-me")
EXTENSION_BRIDGE_TIMEOUT_SECONDS=$(Get-EnvValue "EXTENSION_BRIDGE_TIMEOUT_SECONDS" "360")

GENERATE_IMAGES=$(Get-EnvValue "GENERATE_IMAGES" "false")
IMAGE_PROVIDER=extension_bridge
GEMINI_IMAGE_PROMPT_PREFIX=$(Get-EnvValue "GEMINI_IMAGE_PROMPT_PREFIX" "Create one square realistic image for this social post. Return the image only, with no extra text.")

X_COOKIE=$(Get-EnvValue "X_COOKIE")
X_ACCOUNT_NAME=$(Get-EnvValue "X_ACCOUNT_NAME" "telegram_bot")
X_ACCOUNTS_DB=$(Get-EnvValue "X_ACCOUNTS_DB" "data/twscrape_accounts.db")
X_SEARCH_LIMIT=$(Get-EnvValue "X_SEARCH_LIMIT" "8")
X_SEARCH_PRODUCT=$(Get-EnvValue "X_SEARCH_PRODUCT" "Top")
X_POST_CHAR_LIMIT=$(Get-EnvValue "X_POST_CHAR_LIMIT" "2000")

TREND_SOURCES=$(Get-EnvValue "TREND_SOURCES" "x,google_trends,rss")
GOOGLE_TRENDS_GEO=$(Get-EnvValue "GOOGLE_TRENDS_GEO" "US")
TREND_RSS_URLS=$(Get-EnvValue "TREND_RSS_URLS")
HASHTAG_MODE=$(Get-EnvValue "HASHTAG_MODE" "auto")

CREATOR_NICHE=$(Get-EnvValue "CREATOR_NICHE" "AI tools, creator growth, and online business")
CREATOR_VOICE=$(Get-EnvValue "CREATOR_VOICE" "witty, practical, dry, slightly contrarian, with a sharp creator POV")
TARGET_AUDIENCE=$(Get-EnvValue "TARGET_AUDIENCE" "Vietnamese X users, creators, founders, and indie hackers")
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

Write-Host "Setup complete. Try /tweettrend3 or /replytargets in Telegram."
