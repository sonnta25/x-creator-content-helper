import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WINDOWS_SCRIPTS = PROJECT_ROOT / "scripts" / "windows"


def test_start_script_checks_for_live_or_legacy_bot_before_dependency_sync() -> None:
    script = (WINDOWS_SCRIPTS / "start.ps1").read_text(encoding="utf-8-sig")

    sync_position = script.index("sync-dependencies.ps1")
    process_position = script.index("Get-CimInstance Win32_Process")

    assert process_position < sync_position
    assert "restart.ps1" in script
    assert "x-content-bot" in script
    assert "x-creator-content-helper" in script


def test_dependency_sync_tracks_pyproject_and_checks_download_package() -> None:
    script = (WINDOWS_SCRIPTS / "sync-dependencies.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert "pyproject.toml" in script
    assert "requirements.lock" in script
    assert ".project-dependencies.sha256" in script
    assert "yt_dlp" in script
    assert "gallery_dl" in script
    assert "httpx" not in script
    assert "pip install" in script
    assert "-e $ProjectRoot" in script
    assert "-c $lockFile" in script
    assert '$ErrorActionPreference = "SilentlyContinue"' in script
    assert "$pipExitCode = $LASTEXITCODE" in script
    assert "return ($exitCode -eq 0)" in script


def test_restart_script_stops_old_copies_before_starting_updated_bot() -> None:
    script = (WINDOWS_SCRIPTS / "restart.ps1").read_text(encoding="utf-8-sig")

    assert "Get-CimInstance Win32_Process" in script
    assert "Stop-Process" in script
    assert "x-content-bot" in script
    assert "x-creator-content-helper" in script
    assert 'Join-Path $PSScriptRoot "start.ps1"' in script


def test_setup_preserves_download_settings_and_forces_dependency_sync() -> None:
    script = (WINDOWS_SCRIPTS / "setup.ps1").read_text(encoding="utf-8-sig")

    assert "sync-dependencies.ps1" in script
    assert "-Force" in script
    assert "DOWNLOAD_MAX_FILE_MB=" in script
    assert "DOWNLOAD_TIMEOUT_SECONDS=" in script
    assert "DOWNLOAD_COOKIES_FILE=" in script
    assert "DOWNLOAD_COOKIES_FROM_BROWSER=" in script
    assert "DOWNLOAD_BROWSER_PROFILE=" in script


def test_setup_preserves_active_reply_settings_and_drops_removed_post_settings() -> None:
    script = (WINDOWS_SCRIPTS / "setup.ps1").read_text(encoding="utf-8-sig")

    for setting in (
        "TELEGRAM_REPLY_VIDEO_MINUTES",
        "REPLY_TARGET_MODE",
        "REPLY_TARGET_BATCH_SIZE",
        "REPLY_VIDEO_BATCH_SIZE",
        "REPLY_VIDEO_FRAME_ANALYSIS",
        "REPLY_LEARNING_ENABLED",
        "REPLY_TRACKING_POLL_MINUTES",
        "CREATOR_TIMEZONE",
        "X_OWNER_USERNAME",
    ):
        assert f"{setting}=" in script

    for removed in (
        "GENERATE_IMAGES=",
        "IMAGE_PROVIDER=",
        "GEMINI_IMAGE_PROMPT_PREFIX=",
        "TREND_SOURCES=",
        "TREND_RSS_URLS=",
        "HASHTAG_MODE=",
        "X_POST_CHAR_LIMIT=",
    ):
        assert removed not in script


def test_setup_preserves_every_documented_env_setting_and_uses_runtime_cap() -> None:
    script = (WINDOWS_SCRIPTS / "setup.ps1").read_text(encoding="utf-8-sig")
    example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8-sig")
    assignment = re.compile(r"^([A-Z][A-Z0-9_]*)=", re.MULTILINE)

    assert set(assignment.findall(example)) <= set(assignment.findall(script))
    assert 'CREATOR_DAILY_REPLY_CAP=$(Get-EnvValue "CREATOR_DAILY_REPLY_CAP" "500")' in script


def test_vps_package_excludes_development_and_private_runtime_files() -> None:
    script = (WINDOWS_SCRIPTS / "package.ps1").read_text(encoding="utf-8-sig")

    for excluded in ('".env"', '".gitignore"', '".github"', '"tests"'):
        assert excluded in script
    for sensitive in ('".env.*"', '"*cookie*"', '"auth_token*"', '"ct0*"', '"*.pem"', '"*.key"'):
        assert sensitive in script
    assert 'if ($File.Name -eq ".env.example") { return $false }' in script
