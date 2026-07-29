from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.config import Settings
from src.media_download_service import (
    MediaDownloadError,
    MediaDownloadService,
    _rename_for_delivery,
    validate_media_url,
)


def _public_resolver(*_args, **_kwargs):
    return [(2, 1, 6, "", ("93.184.216.34", 0))]


def test_validate_media_url_accepts_public_https_url() -> None:
    assert (
        validate_media_url("https://www.tiktok.com/@creator/video/123?lang=en")
        == "https://www.tiktok.com/@creator/video/123?lang=en"
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost/video",
        "http://127.0.0.1/video",
        "http://10.0.0.2/video",
        "https://example.com:8443/video",
        "https://user:pass@example.com/video",
    ],
)
def test_validate_media_url_rejects_unsafe_url(url: str) -> None:
    with pytest.raises(MediaDownloadError):
        validate_media_url(url)


def test_downloader_rejects_domain_resolving_to_private_address() -> None:
    service = MediaDownloadService(
        Settings(telegram_bot_token="123:ABC"),
        ydl_factory=lambda _options: None,
        resolver=lambda *_args, **_kwargs: [(2, 1, 6, "", ("192.168.1.4", 0))],
    )

    with pytest.raises(MediaDownloadError, match="Private or local"):
        service.download("https://example.com/video")


def test_download_returns_file_and_applies_bounded_options() -> None:
    captured: dict = {}

    class FakeDownloader:
        def __init__(self, options):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, url, download):
            assert url == "https://www.douyin.com/video/123"
            assert download is True
            path = Path(captured["paths"]["home"]) / "sample-123.mp4"
            path.write_bytes(b"video bytes")
            captured["progress_hooks"][0]({"downloaded_bytes": path.stat().st_size})
            return {
                "title": "Sample video",
                "extractor_key": "Douyin",
            }

    settings = Settings(
        telegram_bot_token="123:ABC",
        download_max_file_mb=12,
        download_timeout_seconds=90,
    )
    service = MediaDownloadService(
        settings,
        ydl_factory=FakeDownloader,
        resolver=_public_resolver,
    )

    result = service.download("https://www.douyin.com/video/123")

    assert result.path.read_bytes() == b"video bytes"
    assert result.path.name.startswith("creator-video-")
    assert result.path.suffix == ".mp4"
    assert result.title == "Sample video"
    assert result.extractor == "Douyin"
    assert captured["outtmpl"] == {"default": "download.%(ext)s"}
    assert captured["noplaylist"] is True
    assert captured["concurrent_fragment_downloads"] == 1
    assert str(12 * 1024 * 1024) in captured["format"]
    assert captured["writeinfojson"] is False
    assert captured["writethumbnail"] is False
    assert captured["writesubtitles"] is False
    assert "http_headers" not in captured

    parent = result.path.parent
    result.cleanup()
    assert not parent.exists()


def test_download_refreshes_browser_cookies_and_retries_auth_failure(tmp_path) -> None:
    cookie_path = tmp_path / "download-cookies.txt"
    cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    attempts: list[dict] = []

    class RefreshingDownloader:
        def __init__(self, options):
            self.options = options
            attempts.append(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, _url, download):
            assert download is True
            if "cookiesfrombrowser" not in self.options:
                raise RuntimeError("Login required: cookies are expired")
            path = Path(self.options["paths"]["home"]) / "download.mp4"
            path.write_bytes(b"refreshed video")
            return {"title": "Video", "extractor_key": "Facebook"}

    service = MediaDownloadService(
        Settings(
            telegram_bot_token="123:ABC",
            download_cookies_file=str(cookie_path),
            download_cookies_from_browser="chrome",
            download_browser_profile="Profile 1",
        ),
        ydl_factory=RefreshingDownloader,
        resolver=_public_resolver,
    )

    result = service.download("https://www.facebook.com/watch/?v=123")

    assert len(attempts) == 2
    assert attempts[0]["cookiefile"] == str(cookie_path)
    assert "cookiesfrombrowser" not in attempts[0]
    assert attempts[1]["cookiesfrombrowser"] == (
        "chrome",
        "Profile 1",
        None,
        None,
    )
    assert result.path.read_bytes() == b"refreshed video"
    result.cleanup()


def test_browser_cookie_refresh_can_create_cookie_directory(tmp_path) -> None:
    cookie_path = tmp_path / "nested" / "download-cookies.txt"
    captured: dict = {}

    class BrowserDownloader:
        def __init__(self, options):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, _url, download):
            assert download is True
            path = Path(captured["paths"]["home"]) / "download.mp4"
            path.write_bytes(b"video")
            return {}

    service = MediaDownloadService(
        Settings(
            telegram_bot_token="123:ABC",
            download_cookies_file=str(cookie_path),
            download_cookies_from_browser="chrome",
        ),
        ydl_factory=BrowserDownloader,
        resolver=_public_resolver,
    )

    result = service.download("https://www.douyin.com/video/123")

    assert cookie_path.parent.is_dir()
    assert captured["cookiefile"] == str(cookie_path)
    assert captured["cookiesfrombrowser"] == ("chrome", None, None, None)
    result.cleanup()


def test_download_rejects_unsupported_browser_cookie_source() -> None:
    service = MediaDownloadService(
        Settings(
            telegram_bot_token="123:ABC",
            download_cookies_from_browser="internet-explorer",
        ),
        ydl_factory=lambda _options: None,
        resolver=_public_resolver,
    )

    with pytest.raises(MediaDownloadError, match="Unsupported.*internet-explorer"):
        service.download("https://example.com/video")


def test_download_requests_manual_login_after_browser_refresh_fails() -> None:
    class LoggedOutDownloader:
        def __init__(self, options):
            assert options["cookiesfrombrowser"] == ("chrome", None, None, None)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, _url, _download):
            raise RuntimeError("Login required after reading cookies")

    service = MediaDownloadService(
        Settings(
            telegram_bot_token="123:ABC",
            download_cookies_from_browser="chrome",
        ),
        ydl_factory=LoggedOutDownloader,
        resolver=_public_resolver,
    )

    with pytest.raises(MediaDownloadError, match="sign in or complete CAPTCHA"):
        service.download("https://www.facebook.com/watch/?v=123")


def test_rename_for_delivery_removes_original_title_and_source_id(tmp_path) -> None:
    original = tmp_path / "Original viral title-998877.mp4"
    original.write_bytes(b"video")

    renamed = _rename_for_delivery(
        original,
        now=datetime(2026, 7, 27, 10, 11, 12, tzinfo=UTC),
        token="a1b2c3",
    )

    assert renamed.name == "creator-video-20260727-101112-a1b2c3.mp4"
    assert renamed.read_bytes() == b"video"
    assert not original.exists()


def test_download_aborts_when_progress_exceeds_size_limit() -> None:
    workdir: Path | None = None

    class OversizedDownloader:
        def __init__(self, options):
            nonlocal workdir
            self.options = options
            workdir = Path(options["paths"]["home"])

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, _url, download):
            assert download is True
            max_bytes = 2 * 1024 * 1024
            self.options["progress_hooks"][0]({"downloaded_bytes": max_bytes + 1})
            return {}

    service = MediaDownloadService(
        Settings(telegram_bot_token="123:ABC", download_max_file_mb=2),
        ydl_factory=OversizedDownloader,
        resolver=_public_resolver,
    )

    with pytest.raises(MediaDownloadError, match="2 MB limit"):
        service.download("https://www.facebook.com/watch/?v=123")

    assert workdir is not None
    assert not workdir.exists()
