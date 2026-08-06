from __future__ import annotations

import ipaddress
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from src.config import Settings


SUPPORTED_COOKIE_BROWSERS = {
    "brave",
    "chrome",
    "chromium",
    "edge",
    "firefox",
    "opera",
    "safari",
    "vivaldi",
    "whale",
}
COOKIE_REFRESH_ERROR_MARKERS = {
    "401",
    "403",
    "authentication",
    "captcha",
    "cookie",
    "forbidden",
    "login",
    "not authorized",
    "sign in",
}
IMAGE_EXTENSIONS = {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}


class MediaDownloadError(RuntimeError):
    """A download failure that is safe to show to a Telegram user."""


@dataclass(frozen=True)
class DownloadedMedia:
    path: Path
    title: str
    source_url: str
    extractor: str
    additional_paths: tuple[Path, ...] = ()

    @property
    def paths(self) -> tuple[Path, ...]:
        return (self.path, *self.additional_paths)

    @property
    def size_bytes(self) -> int:
        return sum(path.stat().st_size for path in self.paths)

    @property
    def media_kind(self) -> str:
        suffixes = {path.suffix.lower() for path in self.paths}
        if suffixes and suffixes.issubset(IMAGE_EXTENSIONS):
            return "image" if len(self.paths) == 1 else "images"
        return "video" if len(self.paths) == 1 else "media files"

    def cleanup(self) -> None:
        temp_root = Path(tempfile.gettempdir()).resolve()
        resolved_path = self.path.resolve()
        for parent in resolved_path.parents:
            if parent.parent == temp_root and parent.name.startswith("x-content-download-"):
                shutil.rmtree(parent, ignore_errors=True)
                return
        for path in self.paths:
            path.unlink(missing_ok=True)


class MediaDownloadService:
    def __init__(
        self,
        settings: Settings,
        *,
        ydl_factory: Callable[[dict[str, Any]], Any] | None = None,
        gallery_runner: Callable[..., Any] | None = None,
        resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
    ) -> None:
        self.settings = settings
        self._ydl_factory = ydl_factory
        self._gallery_runner = gallery_runner or subprocess.run
        self._resolver = resolver

    def download(self, url: str) -> DownloadedMedia:
        normalized_url = validate_media_url(url)
        self._ensure_public_host(normalized_url)
        factory = self._ydl_factory or _load_yt_dlp_factory()
        started_at = time.monotonic()
        max_bytes = self.settings.download_max_file_mb * 1024 * 1024
        cookie_path = self._cookie_file_path()
        browser_spec = _browser_cookie_spec(
            self.settings.download_cookies_from_browser,
            self.settings.download_browser_profile,
        )
        attempts = _cookie_attempts(cookie_path, browser_spec)
        browser_attempted = False
        video_error: Exception | None = None

        for attempt_index, use_browser in enumerate(attempts):
            browser_attempted = browser_attempted or use_browser
            try:
                return self._download_once(
                    normalized_url,
                    factory=factory,
                    started_at=started_at,
                    max_bytes=max_bytes,
                    cookie_path=cookie_path,
                    browser_spec=browser_spec if use_browser else None,
                )
            except MediaDownloadError as exc:
                if _can_try_gallery_fallback(exc):
                    video_error = exc
                    break
                raise
            except Exception as exc:
                has_next_attempt = attempt_index + 1 < len(attempts)
                if has_next_attempt and _should_refresh_browser_cookies(exc):
                    continue
                video_error = _friendly_download_error(
                    exc,
                    self.settings,
                    browser_attempted=browser_attempted,
                )
                break

        try:
            return self._download_gallery(
                normalized_url,
                started_at=started_at,
                max_bytes=max_bytes,
                cookie_path=cookie_path,
                browser_spec=browser_spec,
            )
        except MediaDownloadError as gallery_error:
            if video_error is not None and "gallery-dl is not installed" in str(gallery_error):
                if any(
                    marker in str(video_error).lower()
                    for marker in ("authentication", "logged-in", "sign in", "captcha")
                ):
                    raise video_error from gallery_error
                raise gallery_error from video_error
            if video_error is not None and "unsupported" not in str(video_error).lower():
                raise MediaDownloadError(
                    f"{video_error} Image/gallery fallback also failed: {gallery_error}"
                ) from gallery_error
            raise gallery_error from video_error

    def _download_gallery(
        self,
        normalized_url: str,
        *,
        started_at: float,
        max_bytes: int,
        cookie_path: Path | None,
        browser_spec: tuple[str, str | None, None, None] | None,
    ) -> DownloadedMedia:
        remaining = self.settings.download_timeout_seconds - (time.monotonic() - started_at)
        if remaining <= 0:
            raise MediaDownloadError(
                f"Download exceeded {self.settings.download_timeout_seconds} seconds."
            )
        workdir = Path(tempfile.mkdtemp(prefix="x-content-download-"))
        command = [
            sys.executable,
            "-m",
            "gallery_dl",
            "--config-ignore",
            "--no-input",
            "--destination",
            str(workdir),
            "--filename",
            "download-{num}.{extension}",
            "--range",
            "1-10",
            "--filesize-max",
            f"{self.settings.download_max_file_mb}M",
            "--no-mtime",
        ]
        if cookie_path is not None and cookie_path.is_file():
            command.extend(("--cookies", str(cookie_path)))
        if browser_spec is not None:
            browser, profile, _keyring, _container = browser_spec
            browser_value = f"{browser}:{profile}" if profile else browser
            command.extend(("--cookies-from-browser", browser_value))
        command.append(normalized_url)

        try:
            completed = self._gallery_runner(
                command,
                capture_output=True,
                text=True,
                timeout=max(1, int(remaining)),
                check=False,
            )
            if int(getattr(completed, "returncode", 1)) != 0:
                detail = " ".join(
                    str(getattr(completed, "stderr", "") or getattr(completed, "stdout", ""))
                    .split()
                )
                if "No module named gallery_dl" in detail:
                    raise MediaDownloadError(
                        "gallery-dl is not installed. Run: pip install -e ."
                    )
                raise MediaDownloadError(
                    f"Could not download images from this post. Details: "
                    f"{detail or 'gallery extractor failed'}"
                )
            paths = _downloaded_files(workdir)
            total_bytes = sum(path.stat().st_size for path in paths)
            if total_bytes > max_bytes:
                raise MediaDownloadError(
                    "Downloaded media is larger than the "
                    f"{self.settings.download_max_file_mb} MB total limit."
                )
            renamed = tuple(
                _rename_for_delivery(path, index=index, total=len(paths))
                for index, path in enumerate(paths, start=1)
            )
            return DownloadedMedia(
                path=renamed[0],
                additional_paths=renamed[1:],
                title="Downloaded social post media",
                source_url=normalized_url,
                extractor="gallery-dl",
            )
        except subprocess.TimeoutExpired as exc:
            shutil.rmtree(workdir, ignore_errors=True)
            raise MediaDownloadError(
                f"Download exceeded {self.settings.download_timeout_seconds} seconds."
            ) from exc
        except MediaDownloadError:
            shutil.rmtree(workdir, ignore_errors=True)
            raise
        except Exception as exc:
            shutil.rmtree(workdir, ignore_errors=True)
            raise MediaDownloadError(
                f"Could not download images from this post. Details: {exc}"
            ) from exc

    def _cookie_file_path(self) -> Path | None:
        raw_path = self.settings.download_cookies_file
        if not raw_path:
            return None
        cookie_path = Path(raw_path).expanduser()
        if cookie_path.is_file():
            return cookie_path
        if self.settings.download_cookies_from_browser:
            cookie_path.parent.mkdir(parents=True, exist_ok=True)
            return cookie_path
        raise MediaDownloadError(
            f"DOWNLOAD_COOKIES_FILE does not exist: {cookie_path}"
        )

    def _download_once(
        self,
        normalized_url: str,
        *,
        factory: Callable[[dict[str, Any]], Any],
        started_at: float,
        max_bytes: int,
        cookie_path: Path | None,
        browser_spec: tuple[str, str | None, None, None] | None,
    ) -> DownloadedMedia:
        workdir = Path(tempfile.mkdtemp(prefix="x-content-download-"))

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
        }
        if cookie_path is not None:
            options["cookiefile"] = str(cookie_path)
        if browser_spec is not None:
            options["cookiesfrombrowser"] = browser_spec

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
        except Exception:
            shutil.rmtree(workdir, ignore_errors=True)
            raise

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


def _browser_cookie_spec(
    browser_name: str,
    profile: str,
) -> tuple[str, str | None, None, None] | None:
    normalized_browser = str(browser_name or "").strip().lower()
    if not normalized_browser:
        return None
    if normalized_browser not in SUPPORTED_COOKIE_BROWSERS:
        supported = ", ".join(sorted(SUPPORTED_COOKIE_BROWSERS))
        raise MediaDownloadError(
            f"Unsupported DOWNLOAD_COOKIES_FROM_BROWSER value: {browser_name}. "
            f"Supported browsers: {supported}."
        )
    normalized_profile = str(profile or "").strip() or None
    return normalized_browser, normalized_profile, None, None


def _cookie_attempts(
    cookie_path: Path | None,
    browser_spec: tuple[str, str | None, None, None] | None,
) -> tuple[bool, ...]:
    if browser_spec is None:
        return (False,)
    if cookie_path is not None and cookie_path.is_file():
        return False, True
    return (True,)


def _should_refresh_browser_cookies(exc: Exception) -> bool:
    lowered = " ".join(str(exc).split()).lower()
    return any(marker in lowered for marker in COOKIE_REFRESH_ERROR_MARKERS)


def _friendly_download_error(
    exc: Exception,
    settings: Settings,
    *,
    browser_attempted: bool,
) -> MediaDownloadError:
    detail = " ".join(str(exc).split())
    lowered = detail.lower()
    if "video is larger than" in lowered:
        return MediaDownloadError(
            f"Video is larger than the {settings.download_max_file_mb} MB limit."
        )
    if "download exceeded" in lowered:
        return MediaDownloadError(
            f"Download exceeded {settings.download_timeout_seconds} seconds."
        )
    if browser_attempted and _is_browser_cookie_load_error(lowered):
        browser = settings.download_cookies_from_browser or "browser"
        profile = settings.download_browser_profile or "the most recent profile"
        return MediaDownloadError(
            f"Could not refresh cookies from {browser} ({profile}). Run the bot and "
            "browser under the same Windows user, verify the profile, then try again."
        )
    if any(marker in lowered for marker in COOKIE_REFRESH_ERROR_MARKERS):
        if browser_attempted:
            return MediaDownloadError(
                "The bot refreshed cookies from the browser, but the website still "
                "requires authentication. Open the website in that browser profile, "
                "sign in or complete CAPTCHA, then try again."
            )
        return MediaDownloadError(
            "This media requires a logged-in session. Set "
            "DOWNLOAD_COOKIES_FROM_BROWSER=chrome for automatic refresh, or provide "
            "a Netscape cookie file with DOWNLOAD_COOKIES_FILE."
        )
    if "unsupported url" in lowered:
        return MediaDownloadError(
            "This website or URL format is not supported by the downloader."
        )
    if "private" in lowered or "not available" in lowered:
        return MediaDownloadError(
            "This media is private, unavailable, or blocked in the bot's region."
        )
    return MediaDownloadError(
        f"Could not download this media. Details: {detail or type(exc).__name__}"
    )


def _can_try_gallery_fallback(exc: MediaDownloadError) -> bool:
    lowered = str(exc).lower()
    return any(
        marker in lowered
        for marker in (
            "no downloadable video file",
            "no downloadable media file",
            "not supported by the downloader",
            "could not download this video",
            "could not download this media",
            "private, unavailable",
            "logged-in session",
        )
    )


def _is_browser_cookie_load_error(lowered_detail: str) -> bool:
    return any(
        marker in lowered_detail
        for marker in (
            "could not copy",
            "could not find",
            "cookie load error",
            "failed to decrypt",
            "keyring",
        )
    )


def validate_media_url(url: str) -> str:
    normalized = str(url or "").strip().strip("<>")
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError as exc:
        raise MediaDownloadError("The media URL is invalid.") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise MediaDownloadError("Please send a full public http(s) post or media URL.")
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
    return max(_downloaded_files(workdir), key=lambda path: path.stat().st_size)


def _downloaded_files(workdir: Path) -> list[Path]:
    candidates = sorted(
        (
            path
            for path in workdir.rglob("*")
            if path.is_file()
            and not path.name.endswith((".part", ".ytdl", ".temp", ".json"))
            and path.stat().st_size > 0
        ),
        key=lambda path: str(path.relative_to(workdir)).lower(),
    )
    if not candidates:
        raise MediaDownloadError("The website returned no downloadable media file.")
    return candidates

def _rename_for_delivery(
    path: Path,
    *,
    now: datetime | None = None,
    token: str | None = None,
    index: int = 1,
    total: int = 1,
) -> Path:
    timestamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%d-%H%M%S")
    random_token = (token or secrets.token_hex(3)).lower()
    clean_token = "".join(character for character in random_token if character.isalnum())[:12]
    if not clean_token:
        clean_token = secrets.token_hex(3)
    suffix = path.suffix.lower()
    if not suffix.startswith(".") or not suffix[1:].isalnum():
        suffix = ".mp4"
    media_label = "image" if suffix in IMAGE_EXTENSIONS else "video"
    sequence = f"-{index:02d}" if total > 1 else ""
    target = path.with_name(
        f"creator-{media_label}-{timestamp}-{clean_token}{sequence}{suffix}"
    )
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
