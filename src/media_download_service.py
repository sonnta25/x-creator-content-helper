from __future__ import annotations

import ipaddress
import secrets
import shutil
import socket
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from src.config import Settings


class MediaDownloadError(RuntimeError):
    """A download failure that is safe to show to a Telegram user."""


@dataclass(frozen=True)
class DownloadedMedia:
    path: Path
    title: str
    source_url: str
    extractor: str

    @property
    def size_bytes(self) -> int:
        return self.path.stat().st_size

    def cleanup(self) -> None:
        download_dir = self.path.parent.resolve()
        temp_root = Path(tempfile.gettempdir()).resolve()
        if (
            download_dir.parent == temp_root
            and download_dir.name.startswith("x-content-download-")
        ):
            shutil.rmtree(download_dir, ignore_errors=True)
            return
        self.path.unlink(missing_ok=True)


class MediaDownloadService:
    def __init__(
        self,
        settings: Settings,
        *,
        ydl_factory: Callable[[dict[str, Any]], Any] | None = None,
        resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
    ) -> None:
        self.settings = settings
        self._ydl_factory = ydl_factory
        self._resolver = resolver

    def download(self, url: str) -> DownloadedMedia:
        normalized_url = validate_media_url(url)
        self._ensure_public_host(normalized_url)
        factory = self._ydl_factory or _load_yt_dlp_factory()
        workdir = Path(tempfile.mkdtemp(prefix="x-content-download-"))
        started_at = time.monotonic()
        max_bytes = self.settings.download_max_file_mb * 1024 * 1024

        def progress_hook(progress: dict[str, Any]) -> None:
            if time.monotonic() - started_at > self.settings.download_timeout_seconds:
                raise MediaDownloadError(
                    f"Download exceeded {self.settings.download_timeout_seconds} seconds."
                )
            downloaded = int(progress.get("downloaded_bytes") or 0)
            if downloaded > max_bytes:
                raise MediaDownloadError(
                    f"Video is larger than the {self.settings.download_max_file_mb} MB limit."
                )

        options: dict[str, Any] = {
            "paths": {"home": str(workdir)},
            "outtmpl": {"default": "download.%(ext)s"},
            "format": _format_selector(max_bytes),
            "noplaylist": True,
            "playlistend": 1,
            "quiet": True,
            "noprogress": True,
            "no_warnings": True,
            "cachedir": False,
            "restrictfilenames": True,
            "windowsfilenames": True,
            "overwrites": False,
            "continuedl": False,
            "writedescription": False,
            "writeinfojson": False,
            "writethumbnail": False,
            "writecomments": False,
            "writesubtitles": False,
            "writeautomaticsub": False,
            "socket_timeout": min(30, self.settings.download_timeout_seconds),
            "retries": 2,
            "fragment_retries": 2,
            "concurrent_fragment_downloads": 1,
            "progress_hooks": [progress_hook],
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36"
                )
            },
        }
        if self.settings.download_cookies_file:
            cookie_path = Path(self.settings.download_cookies_file).expanduser()
            if not cookie_path.is_file():
                shutil.rmtree(workdir, ignore_errors=True)
                raise MediaDownloadError(
                    f"DOWNLOAD_COOKIES_FILE does not exist: {cookie_path}"
                )
            options["cookiefile"] = str(cookie_path)

        try:
            with factory(options) as downloader:
                info = downloader.extract_info(normalized_url, download=True)
            path = _downloaded_file(workdir)
            path = _rename_for_delivery(path)
            if path.stat().st_size > max_bytes:
                raise MediaDownloadError(
                    f"Video is larger than the {self.settings.download_max_file_mb} MB limit."
                )
            metadata = _single_video_info(info)
            return DownloadedMedia(
                path=path,
                title=str(metadata.get("title") or path.stem).strip() or path.stem,
                source_url=normalized_url,
                extractor=str(
                    metadata.get("extractor_key") or metadata.get("extractor") or "website"
                ),
            )
        except MediaDownloadError:
            shutil.rmtree(workdir, ignore_errors=True)
            raise
        except Exception as exc:
            shutil.rmtree(workdir, ignore_errors=True)
            detail = " ".join(str(exc).split())
            lowered = detail.lower()
            if "video is larger than" in lowered:
                raise MediaDownloadError(
                    f"Video is larger than the {self.settings.download_max_file_mb} MB limit."
                ) from exc
            if "download exceeded" in lowered:
                raise MediaDownloadError(
                    f"Download exceeded {self.settings.download_timeout_seconds} seconds."
                ) from exc
            if "login" in lowered or "cookie" in lowered or "authentication" in lowered:
                raise MediaDownloadError(
                    "This video requires a logged-in session. Export a Netscape cookie file "
                    "and set DOWNLOAD_COOKIES_FILE, then try again."
                ) from exc
            if "unsupported url" in lowered:
                raise MediaDownloadError(
                    "This website or URL format is not supported by the downloader."
                ) from exc
            if "private" in lowered or "not available" in lowered:
                raise MediaDownloadError(
                    "This video is private, unavailable, or blocked in the bot's region."
                ) from exc
            raise MediaDownloadError(
                f"Could not download this video. Details: {detail or type(exc).__name__}"
            ) from exc

    def _ensure_public_host(self, url: str) -> None:
        host = urlsplit(url).hostname
        if host is None:
            raise MediaDownloadError("The URL does not contain a valid host.")
        try:
            addresses = self._resolver(host, None, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise MediaDownloadError(f"Could not resolve the URL host: {host}") from exc
        if not addresses:
            raise MediaDownloadError(f"Could not resolve the URL host: {host}")
        for address in addresses:
            ip_text = str(address[4][0]).split("%", 1)[0]
            if not ipaddress.ip_address(ip_text).is_global:
                raise MediaDownloadError("Private or local network URLs are not allowed.")


def validate_media_url(url: str) -> str:
    normalized = str(url or "").strip().strip("<>")
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError as exc:
        raise MediaDownloadError("The video URL is invalid.") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise MediaDownloadError("Please send a full public http(s) video URL.")
    if parsed.username or parsed.password:
        raise MediaDownloadError("URLs containing a username or password are not allowed.")
    if port not in {None, 80, 443}:
        raise MediaDownloadError("Only standard http(s) ports are allowed.")

    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(
        (".localhost", ".local", ".internal", ".lan", ".home", ".test")
    ):
        raise MediaDownloadError("Private or local network URLs are not allowed.")
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise MediaDownloadError("Private or local network URLs are not allowed.")
    return normalized


def _load_yt_dlp_factory() -> Callable[[dict[str, Any]], Any]:
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise MediaDownloadError(
            "yt-dlp is not installed. Run: pip install -e ."
        ) from exc
    return YoutubeDL


def _format_selector(max_bytes: int) -> str:
    return (
        f"best[filesize<={max_bytes}][ext=mp4]/"
        f"best[filesize_approx<={max_bytes}][ext=mp4]/"
        f"best[filesize<={max_bytes}]/"
        f"best[filesize_approx<={max_bytes}]/"
        "best[height<=720][ext=mp4]/best[height<=720]/worst[ext=mp4]/worst"
    )


def _downloaded_file(workdir: Path) -> Path:
    candidates = [
        path
        for path in workdir.iterdir()
        if path.is_file()
        and not path.name.endswith((".part", ".ytdl", ".temp"))
        and path.stat().st_size > 0
    ]
    if not candidates:
        raise MediaDownloadError("The website returned no downloadable video file.")
    return max(candidates, key=lambda path: path.stat().st_size)


def _rename_for_delivery(
    path: Path,
    *,
    now: datetime | None = None,
    token: str | None = None,
) -> Path:
    timestamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%d-%H%M%S")
    random_token = (token or secrets.token_hex(3)).lower()
    clean_token = "".join(character for character in random_token if character.isalnum())[:12]
    if not clean_token:
        clean_token = secrets.token_hex(3)
    suffix = path.suffix.lower()
    if not suffix.startswith(".") or not suffix[1:].isalnum():
        suffix = ".mp4"
    target = path.with_name(f"creator-video-{timestamp}-{clean_token}{suffix}")
    path.replace(target)
    return target


def _single_video_info(info: Any) -> dict[str, Any]:
    if not isinstance(info, dict):
        return {}
    entries = info.get("entries")
    if entries:
        first = next((entry for entry in entries if isinstance(entry, dict)), None)
        if first is not None:
            return first
    return info
