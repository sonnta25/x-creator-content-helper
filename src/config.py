from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    content_provider: str = "extension_bridge"
    telegram_caption_limit: int = 1024
    x_cookie: str = ""
    x_account_name: str = "telegram_bot"
    x_owner_username: str = ""
    x_accounts_db: str = "data/twscrape_accounts.db"
    x_search_limit: int = 8
    x_search_product: str = "Top"
    reply_target_min_author_followers: int = 50_000
    reply_target_min_views: int = 500
    reply_target_max_age_minutes: int = 360
    reply_target_languages: str = "en,ja"
    reply_target_metrics_path: str = ""
    reply_learning_enabled: bool = True
    reply_learning_path: str = "data/reply_learning.json"
    reply_tracking_poll_minutes: int = 5
    reply_watch_path: str = "data/reply_watchlist.json"
    reply_target_mode: str = "balanced"
    creator_goal: str = "qualify"
    reply_target_batch_size: int = 3
    reply_video_batch_size: int = 3
    reply_session_minutes: int = 20
    reply_author_daily_cap: int = 5
    stale_mobile_approval_hours: int = 6
    reply_daily_digest_hour: int = 22
    creator_daily_reply_cap: int = 500
    creator_timezone: str = "Asia/Ho_Chi_Minh"
    creator_niche: str = "gold markets, cryptocurrency, and practical AI tools such as ChatGPT, Claude, Grok, and emerging AI products"
    creator_voice: str = "witty, practical, dry, slightly contrarian, with a sharp creator POV"
    target_audience: str = "Vietnamese retail investors, crypto users, creators, founders, and professionals seeking timely practical insights on gold, crypto, and AI tools"
    extension_bridge_host: str = "127.0.0.1"
    extension_bridge_port: int = 8765
    extension_bridge_token: str = "local-bridge-change-me"
    extension_bridge_timeout_seconds: int = 360
    telegram_approval_chat_id: int | None = None
    telegram_reply_targets_minutes: int | None = None
    telegram_reply_targets_updated_at: int | None = None
    reply_video_min_views: int = 15_000
    reply_video_max_age_minutes: int = 45
    reply_video_frame_analysis: bool = True
    reply_video_frame_count: int = 2
    telegram_reply_video_minutes: int | None = None
    telegram_reply_video_updated_at: int | None = None
    automation_approvals_path: str = ""
    download_max_file_mb: int = 45
    download_timeout_seconds: int = 180
    download_cookies_file: str = ""
    download_cookies_from_browser: str = ""
    download_browser_profile: str = ""
    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(_project_env_path(), override=True, encoding="utf-8-sig")
        return cls(
            telegram_bot_token=_required("TELEGRAM_BOT_TOKEN"),
            content_provider=_choice_env(
                "CONTENT_PROVIDER",
                "extension_bridge",
                {"extension_bridge"},
            ),
            x_cookie=os.getenv("X_COOKIE", "").strip(),
            x_account_name=os.getenv("X_ACCOUNT_NAME", "telegram_bot").strip()
            or "telegram_bot",
            x_owner_username=os.getenv("X_OWNER_USERNAME", "").strip().lstrip("@"),
            x_accounts_db=os.getenv(
                "X_ACCOUNTS_DB", "data/twscrape_accounts.db"
            ).strip()
            or "data/twscrape_accounts.db",
            x_search_limit=_int_env("X_SEARCH_LIMIT", 8),
            x_search_product=os.getenv("X_SEARCH_PRODUCT", "Top").strip() or "Top",
            reply_target_min_author_followers=max(
                0, _int_env("REPLY_TARGET_MIN_AUTHOR_FOLLOWERS", 50_000)
            ),
            reply_target_min_views=max(0, _int_env("REPLY_TARGET_MIN_VIEWS", 500)),
            reply_target_max_age_minutes=min(
                1440,
                max(30, _int_env("REPLY_TARGET_MAX_AGE_MINUTES", 360)),
            ),
            reply_target_languages=os.getenv(
                "REPLY_TARGET_LANGUAGES",
                "en,ja",
            ).strip()
            or "en,ja",
            reply_target_metrics_path=os.getenv(
                "REPLY_TARGET_METRICS_PATH",
                "data/reply_target_metrics.json",
            ).strip()
            or "data/reply_target_metrics.json",
            reply_learning_enabled=_bool_env("REPLY_LEARNING_ENABLED", True),
            reply_learning_path=os.getenv(
                "REPLY_LEARNING_PATH", "data/reply_learning.json"
            ).strip()
            or "data/reply_learning.json",
            reply_tracking_poll_minutes=max(
                1, _int_env("REPLY_TRACKING_POLL_MINUTES", 5)
            ),
            reply_watch_path=os.getenv(
                "REPLY_WATCH_PATH", "data/reply_watchlist.json"
            ).strip()
            or "data/reply_watchlist.json",
            reply_target_mode=_choice_env(
                "REPLY_TARGET_MODE",
                "balanced",
                {"balanced", "reach", "qualified", "relationship"},
            ),
            creator_goal=_choice_env(
                "CREATOR_GOAL",
                "qualify",
                {"qualify", "earn", "network"},
            ),
            reply_target_batch_size=min(
                5,
                max(2, _int_env("REPLY_TARGET_BATCH_SIZE", 3)),
            ),
            reply_video_batch_size=min(
                5,
                max(2, _int_env("REPLY_VIDEO_BATCH_SIZE", 3)),
            ),
            creator_daily_reply_cap=max(
                1, _int_env("CREATOR_DAILY_REPLY_CAP", 500)
            ),
            reply_session_minutes=min(
                120,
                max(10, _int_env("REPLY_SESSION_MINUTES", 20)),
            ),
            reply_author_daily_cap=min(
                25,
                max(1, _int_env("REPLY_AUTHOR_DAILY_CAP", 5)),
            ),
            stale_mobile_approval_hours=min(
                72,
                max(2, _int_env("STALE_MOBILE_APPROVAL_HOURS", 6)),
            ),
            reply_daily_digest_hour=min(
                23,
                max(0, _int_env("REPLY_DAILY_DIGEST_HOUR", 22)),
            ),
            creator_timezone=os.getenv(
                "CREATOR_TIMEZONE", "Asia/Ho_Chi_Minh"
            ).strip()
            or "Asia/Ho_Chi_Minh",
            creator_niche=os.getenv(
                "CREATOR_NICHE",
                "gold markets, cryptocurrency, and practical AI tools such as ChatGPT, Claude, Grok, and emerging AI products",
            ).strip()
            or "gold markets, cryptocurrency, and practical AI tools such as ChatGPT, Claude, Grok, and emerging AI products",
            creator_voice=os.getenv(
                "CREATOR_VOICE",
                "witty, practical, dry, slightly contrarian, with a sharp creator POV",
            ).strip()
            or "witty, practical, dry, slightly contrarian, with a sharp creator POV",
            target_audience=os.getenv(
                "TARGET_AUDIENCE",
                "Vietnamese retail investors, crypto users, creators, founders, and professionals seeking timely practical insights on gold, crypto, and AI tools",
            ).strip()
            or "Vietnamese retail investors, crypto users, creators, founders, and professionals seeking timely practical insights on gold, crypto, and AI tools",
            extension_bridge_host=os.getenv(
                "EXTENSION_BRIDGE_HOST",
                "127.0.0.1",
            ).strip()
            or "127.0.0.1",
            extension_bridge_port=_int_env("EXTENSION_BRIDGE_PORT", 8765),
            extension_bridge_token=os.getenv(
                "EXTENSION_BRIDGE_TOKEN",
                "local-bridge-change-me",
            ).strip()
            or "local-bridge-change-me",
            extension_bridge_timeout_seconds=_int_env("EXTENSION_BRIDGE_TIMEOUT_SECONDS", 360),
            telegram_approval_chat_id=_optional_int_env("TELEGRAM_APPROVAL_CHAT_ID"),
            telegram_reply_targets_minutes=_optional_int_env(
                "TELEGRAM_REPLY_TARGETS_MINUTES"
            ),
            telegram_reply_targets_updated_at=_optional_int_env(
                "TELEGRAM_REPLY_TARGETS_UPDATED_AT"
            ),
            reply_video_min_views=max(0, _int_env("REPLY_VIDEO_MIN_VIEWS", 15_000)),
            reply_video_max_age_minutes=min(
                180,
                max(15, _int_env("REPLY_VIDEO_MAX_AGE_MINUTES", 45)),
            ),
            reply_video_frame_analysis=_bool_env(
                "REPLY_VIDEO_FRAME_ANALYSIS", True
            ),
            reply_video_frame_count=min(
                4,
                max(2, _int_env("REPLY_VIDEO_FRAME_COUNT", 2)),
            ),
            telegram_reply_video_minutes=_optional_int_env(
                "TELEGRAM_REPLY_VIDEO_MINUTES"
            ),
            telegram_reply_video_updated_at=_optional_int_env(
                "TELEGRAM_REPLY_VIDEO_UPDATED_AT"
            ),
            automation_approvals_path=os.getenv(
                "AUTOMATION_APPROVALS_PATH",
                "data/automation_approvals.json",
            ).strip()
            or "data/automation_approvals.json",
            download_max_file_mb=_int_env("DOWNLOAD_MAX_FILE_MB", 45),
            download_timeout_seconds=_int_env("DOWNLOAD_TIMEOUT_SECONDS", 180),
            download_cookies_file=os.getenv("DOWNLOAD_COOKIES_FILE", "").strip(),
            download_cookies_from_browser=os.getenv(
                "DOWNLOAD_COOKIES_FROM_BROWSER", ""
            ).strip(),
            download_browser_profile=os.getenv(
                "DOWNLOAD_BROWSER_PROFILE", ""
            ).strip(),
        )


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean, got {raw!r}")


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive, got {raw!r}")
    return value


def _optional_int_env(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc


def _choice_env(name: str, default: str, choices: set[str]) -> str:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    value = raw.strip().lower()
    if value not in choices:
        allowed = ", ".join(sorted(choices))
        raise RuntimeError(f"{name} must be one of: {allowed}. Got {raw!r}")
    return value


def _project_env_path() -> Path:
    return Path(__file__).resolve().parents[1] / ".env"
