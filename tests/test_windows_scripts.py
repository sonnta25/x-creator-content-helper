from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WINDOWS_SCRIPTS = PROJECT_ROOT / "scripts" / "windows"


def test_start_script_syncs_dependencies_before_process_check() -> None:
    script = (WINDOWS_SCRIPTS / "start.ps1").read_text(encoding="utf-8-sig")

    sync_position = script.index("sync-dependencies.ps1")
    process_position = script.index("Get-CimInstance Win32_Process")

    assert sync_position < process_position


def test_dependency_sync_tracks_pyproject_and_checks_download_package() -> None:
    script = (WINDOWS_SCRIPTS / "sync-dependencies.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert "pyproject.toml" in script
    assert ".project-dependencies.sha256" in script
    assert "yt_dlp" in script
    assert "gallery_dl" in script
    assert "pip install" in script
    assert "-e $ProjectRoot" in script
    assert '$ErrorActionPreference = "SilentlyContinue"' in script
    assert "$pipExitCode = $LASTEXITCODE" in script
    assert "return ($exitCode -eq 0)" in script


def test_setup_preserves_download_settings_and_forces_dependency_sync() -> None:
    script = (WINDOWS_SCRIPTS / "setup.ps1").read_text(encoding="utf-8-sig")

    assert "sync-dependencies.ps1" in script
    assert "-Force" in script
    assert "DOWNLOAD_MAX_FILE_MB=" in script
    assert "DOWNLOAD_TIMEOUT_SECONDS=" in script
    assert "DOWNLOAD_COOKIES_FILE=" in script
    assert "DOWNLOAD_COOKIES_FROM_BROWSER=" in script
    assert "DOWNLOAD_BROWSER_PROFILE=" in script
