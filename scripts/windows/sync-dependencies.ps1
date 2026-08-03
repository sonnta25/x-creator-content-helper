param(
    [string] $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [switch] $Force
)

$ErrorActionPreference = "Stop"

$pythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$projectFile = Join-Path $ProjectRoot "pyproject.toml"
$stampFile = Join-Path $ProjectRoot ".venv\.project-dependencies.sha256"

if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Python venv was not found. Run scripts\windows\setup.ps1 first."
}
if (-not (Test-Path -LiteralPath $projectFile)) {
    throw "pyproject.toml was not found: $projectFile"
}

function Get-FileSha256 {
    param([string] $Path)

    $stream = [System.IO.File]::OpenRead($Path)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $sha256.ComputeHash($stream)
        return ([System.BitConverter]::ToString($bytes)).Replace("-", "")
    } finally {
        $sha256.Dispose()
        $stream.Dispose()
    }
}

function Test-RequiredImports {
    $previousPreference = $ErrorActionPreference
    $exitCode = 1
    try {
        # Missing imports are expected here. Windows PowerShell turns native
        # stderr into ErrorRecords, so suppress it and use the process exit code.
        $ErrorActionPreference = "SilentlyContinue"
        & $pythonExe -c "import dotenv, gallery_dl, telegram, twscrape, yt_dlp" *> $null
        $exitCode = $LASTEXITCODE
    } catch {
        $exitCode = 1
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    return ($exitCode -eq 0)
}

$projectHash = Get-FileSha256 -Path $projectFile
$installedHash = ""
if (Test-Path -LiteralPath $stampFile) {
    $installedHash = (Get-Content -LiteralPath $stampFile -Raw).Trim()
}

$importsReady = Test-RequiredImports
if (-not $Force -and $importsReady -and $installedHash -eq $projectHash) {
    Write-Host "Python dependencies are up to date."
    exit 0
}

Write-Host "Synchronizing Python dependencies..."
$previousPreference = $ErrorActionPreference
$pipExitCode = 1
try {
    # pip can emit non-fatal progress and warnings on stderr. Preserve them for
    # diagnosis, but decide success from its native exit code.
    $ErrorActionPreference = "Continue"
    & $pythonExe -m pip install --disable-pip-version-check -e $ProjectRoot
    $pipExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousPreference
}
if ($pipExitCode -ne 0) {
    throw "Could not install Python dependencies from pyproject.toml."
}
if (-not (Test-RequiredImports)) {
    throw "Dependency installation finished, but required Python imports still fail."
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($stampFile, $projectHash, $utf8NoBom)
Write-Host "Python dependencies synchronized."
