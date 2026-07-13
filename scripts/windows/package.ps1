param(
    [string] $OutputPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

if (-not $OutputPath) {
    $distDir = Join-Path $ProjectRoot "dist"
    New-Item -ItemType Directory -Force $distDir | Out-Null
    $OutputPath = Join-Path $distDir "x-content-bot-vps.zip"
}

$stagingRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("x-content-bot-" + [guid]::NewGuid())
New-Item -ItemType Directory -Force $stagingRoot | Out-Null

$excludedDirs = @(
    ".agents",
    ".codex",
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "data",
    "dist",
    "logs"
)
$excludedFiles = @(".env")

function Get-PackageFiles {
    param([string] $Path)

    $children = @()
    try {
        $children = Get-ChildItem -LiteralPath $Path -Force -ErrorAction Stop
    } catch {
        Write-Warning "Skipping inaccessible path: $Path"
        return
    }

    foreach ($child in $children) {
        if ($child.PSIsContainer) {
            if (($excludedDirs -contains $child.Name) -or ($child.Name -like "*.egg-info")) {
                continue
            }
            Get-PackageFiles -Path $child.FullName
            continue
        }

        if ($excludedFiles -contains $child.Name) {
            continue
        }

        $child
    }
}

try {
    $files = Get-PackageFiles -Path $ProjectRoot

    foreach ($file in $files) {
        $relative = $file.FullName.Substring($ProjectRoot.Length).TrimStart("\")
        $target = Join-Path $stagingRoot $relative
        $targetDir = Split-Path -Parent $target
        New-Item -ItemType Directory -Force $targetDir | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $target -Force
    }

    Compress-Archive -Path (Join-Path $stagingRoot "*") -DestinationPath $OutputPath -Force
    Write-Host "Package created: $OutputPath"
} finally {
    if (Test-Path $stagingRoot) {
        Remove-Item -LiteralPath $stagingRoot -Recurse -Force
    }
}
