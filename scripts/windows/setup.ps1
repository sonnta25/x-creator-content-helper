param(
    [string] $TelegramBotToken = "",
    [string] $Model = "qwen2.5:1.5b",
    [string] $TaskName = "XContentTelegramBot",
    [switch] $EnableVpsRestartSchedule,
    [switch] $SkipPythonInstall,
    [switch] $SkipOllamaInstall,
    [switch] $NoScheduledTask
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function Enable-Tls12 {
    try {
        [Net.ServicePointManager]::SecurityProtocol =
            [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    } catch {
        Write-Warning "Could not force TLS 1.2. Continuing with system defaults."
    }
}

function Get-ExistingEnvValue {
    param([string] $Name)

    $envPath = Join-Path $ProjectRoot ".env"
    if (-not (Test-Path $envPath)) {
        return ""
    }

    $line = Get-Content -Path $envPath |
        Where-Object { $_ -match "^$([regex]::Escape($Name))=" } |
        Select-Object -First 1

    if (-not $line) {
        return ""
    }

    return ($line -replace "^$([regex]::Escape($Name))=", "").Trim()
}

function Get-CommandFile {
    param($Command)

    if (-not $Command) {
        return ""
    }

    if ($Command.Path) {
        return $Command.Path
    }

    if ($Command.Source) {
        return $Command.Source
    }

    if ($Command.Definition) {
        return $Command.Definition
    }

    return ""
}

function Test-PythonCommand {
    param($PythonCommand)

    if (-not $PythonCommand) {
        return $false
    }

    $pythonFile = $PythonCommand["File"]
    if (-not $pythonFile) {
        return $false
    }

    $pythonArgs = @($PythonCommand["Args"]) + @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)")
    try {
        & $pythonFile @pythonArgs | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Get-PythonCommand {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $candidate = @{ File = (Get-CommandFile $py); Args = @("-3") }
        if (Test-PythonCommand $candidate) {
            return $candidate
        }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        $candidate = @{ File = (Get-CommandFile $python); Args = @() }
        if (Test-PythonCommand $candidate) {
            return $candidate
        }
    }

    $python3 = Get-Command python3 -ErrorAction SilentlyContinue
    if ($python3) {
        $candidate = @{ File = (Get-CommandFile $python3); Args = @() }
        if (Test-PythonCommand $candidate) {
            return $candidate
        }
    }

    return $null
}

function Install-Python {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "Python 3.11+ was not found and winget is unavailable. Install Python 3.11+ manually from https://www.python.org/downloads/windows/ with 'Add python.exe to PATH' enabled, then rerun setup.ps1."
    }

    $wingetFile = Get-CommandFile $winget
    if (-not $wingetFile) {
        throw "winget was found but its executable path could not be resolved. Install Python 3.11+ manually, then rerun setup.ps1."
    }

    Write-Host "Python 3.11+ was not found. Installing Python 3.11 with winget..."
    & $wingetFile install --id Python.Python.3.11 --exact --accept-package-agreements --accept-source-agreements --silent
    if ($LASTEXITCODE -ne 0) {
        throw "winget could not install Python 3.11. Install Python 3.11+ manually from https://www.python.org/downloads/windows/ with 'Add python.exe to PATH' enabled, then rerun setup.ps1."
    }

    $pathParts = @(
        [Environment]::GetEnvironmentVariable("Path", "Machine"),
        [Environment]::GetEnvironmentVariable("Path", "User"),
        $env:Path
    ) | Where-Object { $_ }
    $env:Path = ($pathParts -join ";")
}

function Get-OllamaExe {
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd) {
        $cmdFile = Get-CommandFile $cmd
        if ($cmdFile) {
            return $cmdFile
        }
    }

    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"),
        (Join-Path $env:ProgramFiles "Ollama\ollama.exe")
    )

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    return $null
}

function Wait-Ollama {
    param([int] $Seconds = 60)

    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 3 | Out-Null
            return
        } catch {
            Start-Sleep -Seconds 2
        }
    }

    throw "Ollama did not become ready at http://localhost:11434."
}

function Install-Ollama {
    $installUrl = "https://ollama.com/install.ps1"
    Enable-Tls12

    try {
        $installer = Invoke-RestMethod -Uri $installUrl
        Invoke-Expression $installer
        return
    } catch {
        Write-Warning "Invoke-RestMethod failed: $($_.Exception.Message)"
    }

    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if (-not $curl) {
        throw "Could not download Ollama installer. Install Ollama manually from https://ollama.com/download/windows, then rerun setup.ps1."
    }

    $curlFile = Get-CommandFile $curl
    if (-not $curlFile) {
        throw "curl.exe was found but its executable path could not be resolved. Install Ollama manually from https://ollama.com/download/windows, then rerun setup.ps1."
    }

    $installerPath = Join-Path $env:TEMP "ollama-install.ps1"
    & $curlFile -L $installUrl -o $installerPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $installerPath)) {
        throw "curl.exe could not download Ollama installer. Install Ollama manually from https://ollama.com/download/windows, then rerun setup.ps1."
    }

    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installerPath
}

Enable-Tls12
Set-Location $ProjectRoot

if (-not $TelegramBotToken) {
    $TelegramBotToken = Get-ExistingEnvValue -Name "TELEGRAM_BOT_TOKEN"
}

if (-not $TelegramBotToken) {
    $TelegramBotToken = Read-Host "Enter TELEGRAM_BOT_TOKEN"
}

if (-not $TelegramBotToken) {
    throw "TELEGRAM_BOT_TOKEN is required."
}
$TelegramBotToken = $TelegramBotToken -replace "\s", ""

Write-Host "Project root: $ProjectRoot"
Write-Host "Creating Python virtual environment..."
$python = Get-PythonCommand
if (-not $python -and -not $SkipPythonInstall) {
    Install-Python
    $python = Get-PythonCommand
}
if (-not $python) {
    throw "Python 3.11+ was not found. Install Python 3.11+ first, then run this script again."
}
$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    $pythonFile = $python["File"]
    if (-not $pythonFile) {
        throw "Python command was found but its executable path could not be resolved. Install Python 3.11+ from python.org and rerun setup.ps1."
    }
    $pythonArgs = @($python["Args"]) + @("-m", "venv", ".venv")
    & $pythonFile @pythonArgs
}

Write-Host "Installing Python dependencies..."
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -e .

Write-Host "Writing .env..."
$ContentProvider = Get-ExistingEnvValue -Name "CONTENT_PROVIDER"
if (-not $ContentProvider) { $ContentProvider = "extension_bridge" }

if ($ContentProvider -eq "ollama") {
    $ollamaExe = Get-OllamaExe
    if (-not $ollamaExe -and -not $SkipOllamaInstall) {
        Write-Host "Installing Ollama for Windows..."
        Install-Ollama
        Start-Sleep -Seconds 5
        $ollamaExe = Get-OllamaExe
    }

    if (-not $ollamaExe) {
        throw "Ollama was not found. Install Ollama or rerun without -SkipOllamaInstall."
    }

    Write-Host "Starting Ollama if needed..."
    try {
        Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 3 | Out-Null
    } catch {
        Start-Process -FilePath $ollamaExe -ArgumentList @("serve") -WindowStyle Hidden
        Wait-Ollama
    }

    Write-Host "Pulling Ollama model: $Model"
    & $ollamaExe pull $Model
}

$XCookie = Get-ExistingEnvValue -Name "X_COOKIE"
$XAccountName = Get-ExistingEnvValue -Name "X_ACCOUNT_NAME"
$XAccountsDb = Get-ExistingEnvValue -Name "X_ACCOUNTS_DB"
$XSearchLimit = Get-ExistingEnvValue -Name "X_SEARCH_LIMIT"
$XSearchProduct = Get-ExistingEnvValue -Name "X_SEARCH_PRODUCT"
$HashtagMode = Get-ExistingEnvValue -Name "HASHTAG_MODE"
$CreatorNiche = Get-ExistingEnvValue -Name "CREATOR_NICHE"
$CreatorVoice = Get-ExistingEnvValue -Name "CREATOR_VOICE"
$TargetAudience = Get-ExistingEnvValue -Name "TARGET_AUDIENCE"
$OllamaTimeoutSeconds = Get-ExistingEnvValue -Name "OLLAMA_TIMEOUT_SECONDS"
$ImageTimeoutSeconds = Get-ExistingEnvValue -Name "IMAGE_TIMEOUT_SECONDS"
$OllamaNumCtx = Get-ExistingEnvValue -Name "OLLAMA_NUM_CTX"
$OllamaNumPredict = Get-ExistingEnvValue -Name "OLLAMA_NUM_PREDICT"
$OllamaKeepAlive = Get-ExistingEnvValue -Name "OLLAMA_KEEP_ALIVE"
$ExtensionBridgeHost = Get-ExistingEnvValue -Name "EXTENSION_BRIDGE_HOST"
$ExtensionBridgePort = Get-ExistingEnvValue -Name "EXTENSION_BRIDGE_PORT"
$ExtensionBridgeToken = Get-ExistingEnvValue -Name "EXTENSION_BRIDGE_TOKEN"
$ExtensionBridgeTimeoutSeconds = Get-ExistingEnvValue -Name "EXTENSION_BRIDGE_TIMEOUT_SECONDS"
$GeminiImagePromptPrefix = Get-ExistingEnvValue -Name "GEMINI_IMAGE_PROMPT_PREFIX"
if (-not $GeminiImagePromptPrefix) { $GeminiImagePromptPrefix = Get-ExistingEnvValue -Name "GROK_IMAGE_PROMPT_PREFIX" }

if (-not $XAccountName) { $XAccountName = "telegram_bot" }
if (-not $XAccountsDb) { $XAccountsDb = "data/twscrape_accounts.db" }
if (-not $XSearchLimit) { $XSearchLimit = "8" }
if (-not $XSearchProduct) { $XSearchProduct = "Top" }
if (-not $HashtagMode) { $HashtagMode = "auto" }
if (-not $CreatorNiche) { $CreatorNiche = "AI tools, creator growth, and online business" }
if (-not $CreatorVoice) { $CreatorVoice = "witty, practical, slightly contrarian" }
if (-not $TargetAudience) { $TargetAudience = "US creators, founders, and indie hackers" }
if (-not $ContentProvider) { $ContentProvider = "extension_bridge" }
if (-not $OllamaTimeoutSeconds) { $OllamaTimeoutSeconds = "240" }
if (-not $ImageTimeoutSeconds) { $ImageTimeoutSeconds = "300" }
if (-not $OllamaNumCtx) { $OllamaNumCtx = "1536" }
if (-not $OllamaNumPredict) { $OllamaNumPredict = "512" }
if (-not $OllamaKeepAlive) { $OllamaKeepAlive = "10m" }
if (-not $ExtensionBridgeHost) { $ExtensionBridgeHost = "127.0.0.1" }
if (-not $ExtensionBridgePort) { $ExtensionBridgePort = "8765" }
if (-not $ExtensionBridgeToken) { $ExtensionBridgeToken = "local-bridge-change-me" }
if (-not $ExtensionBridgeTimeoutSeconds) { $ExtensionBridgeTimeoutSeconds = "300" }
if (-not $GeminiImagePromptPrefix) { $GeminiImagePromptPrefix = "Create one square realistic image for this social post. Return the image only, with no extra text." }

$envContent = @"
TELEGRAM_BOT_TOKEN=$TelegramBotToken

CONTENT_PROVIDER=$ContentProvider

EXTENSION_BRIDGE_HOST=$ExtensionBridgeHost
EXTENSION_BRIDGE_PORT=$ExtensionBridgePort
EXTENSION_BRIDGE_TOKEN=$ExtensionBridgeToken
EXTENSION_BRIDGE_TIMEOUT_SECONDS=$ExtensionBridgeTimeoutSeconds
GEMINI_IMAGE_PROMPT_PREFIX=$GeminiImagePromptPrefix

GENERATE_IMAGES=true
IMAGE_PROVIDER=extension_bridge
IMAGE_TIMEOUT_SECONDS=$ImageTimeoutSeconds

X_COOKIE=$XCookie
X_ACCOUNT_NAME=$XAccountName
X_ACCOUNTS_DB=$XAccountsDb
X_SEARCH_LIMIT=$XSearchLimit
X_SEARCH_PRODUCT=$XSearchProduct
HASHTAG_MODE=$HashtagMode
CREATOR_NICHE=$CreatorNiche
CREATOR_VOICE=$CreatorVoice
TARGET_AUDIENCE=$TargetAudience
"@
Set-Content -Path (Join-Path $ProjectRoot ".env") -Value $envContent -Encoding UTF8

if (-not $NoScheduledTask) {
    Write-Host "Registering Scheduled Task: $TaskName"
    $runScript = Join-Path $ProjectRoot "scripts\windows\run-bot.ps1"
    $taskCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$runScript`""
    & schtasks.exe /Create /F /SC ONLOGON /TN $TaskName /TR $taskCommand | Out-Host
}

if ($EnableVpsRestartSchedule) {
    Write-Host "Registering VPS restart schedule..."
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ProjectRoot "scripts\windows\register-vps-restart.ps1")
}

Write-Host "Restarting bot..."
& (Join-Path $ProjectRoot "scripts\windows\stop.ps1")
& (Join-Path $ProjectRoot "scripts\windows\start.ps1")

Write-Host ""
Write-Host "Done. Try the bot in Telegram with:"
Write-Host "/tweet AI creators"
Write-Host "/xsearch AI creators lang:en"
Write-Host "/reply Everyone is building AI agents but most are just fancy macros."
