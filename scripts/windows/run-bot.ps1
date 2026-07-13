param(
    [string] $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"

function Get-OllamaExe {
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
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
    param([int] $Seconds = 30)

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

$ollamaExe = Get-OllamaExe
if ($ollamaExe) {
    try {
        Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 3 | Out-Null
    } catch {
        Start-Process -FilePath $ollamaExe -ArgumentList @("serve") -WindowStyle Hidden
        Wait-Ollama
    }
}

$pythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    throw "Python venv was not found. Run scripts\windows\setup.ps1 first."
}

$logDir = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$outLog = Join-Path $logDir "bot.out.log"
$errLog = Join-Path $logDir "bot.err.log"

Set-Location $ProjectRoot
& $pythonExe -m src.main >> $outLog 2>> $errLog
