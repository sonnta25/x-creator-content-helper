param(
    [string] $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"
$startScript = Join-Path $PSScriptRoot "start.ps1"

if (-not (Test-Path -LiteralPath $startScript)) {
    throw "Bot start script was not found: $startScript"
}

# The current bot uses only the Chrome extension bridge. Do not start Ollama or
# any other model server here; that consumed VPS resources without serving jobs.
& $startScript -ProjectRoot $ProjectRoot
