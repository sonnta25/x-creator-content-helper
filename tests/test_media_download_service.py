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

    parent = result.path.parent
    result.cleanup()
    assert not parent.exists()


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
