param(
    [string] $TaskPrefix = "XContentBotVpsRestart",
    [string[]] $Times = @("00:00", "11:45"),
    [int] $RestartDelaySeconds = 60,
    [switch] $Remove,
    [switch] $CurrentUser
)

$ErrorActionPreference = "Stop"

function Convert-TimeToTaskSuffix {
    param([string] $Time)

    return ($Time -replace ":", "")
}

function Assert-ValidTime {
    param([string] $Time)

    if ($Time -notmatch "^([01]\d|2[0-3]):[0-5]\d$") {
        throw "Invalid time '$Time'. Use 24-hour HH:mm format, for example 00:00 or 11:45."
    }
}

if ($RestartDelaySeconds -lt 0) {
    throw "RestartDelaySeconds must be 0 or greater."
}

foreach ($time in $Times) {
    Assert-ValidTime -Time $time

    $suffix = Convert-TimeToTaskSuffix -Time $time
    $taskName = "$TaskPrefix-$suffix"

    if ($Remove) {
        Write-Host "Removing Scheduled Task: $taskName"
        & schtasks.exe /Delete /TN $taskName /F | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Could not remove $taskName. It may not exist."
        }
        continue
    }

    $taskRun = "shutdown.exe /r /t $RestartDelaySeconds"
    $args = @(
        "/Create",
        "/F",
        "/SC", "DAILY",
        "/ST", $time,
        "/TN", $taskName,
        "/TR", $taskRun
    )

    if (-not $CurrentUser) {
        $args += @("/RU", "SYSTEM")
    }

    Write-Host "Registering Scheduled Task: $taskName at $time"
    & schtasks.exe @args | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Could not register $taskName. Run PowerShell as Administrator, or rerun with -CurrentUser."
    }
}

if ($Remove) {
    Write-Host "VPS restart schedule removed."
} else {
    Write-Host "VPS restart schedule registered."
    Write-Host "If a restart countdown is already running, cancel it with: shutdown /a"
}
