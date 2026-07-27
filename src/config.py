from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    content_provider: str = "extension_bridge"
    generate_images: bool = False
    image_provider: str = "extension_bridge"
    telegram_caption_limit: int = 1024
    x_cookie: str = ""
    x_account_name: str = "telegram_bot"
    x_accounts_db: str = "data/twscrape_accounts.db"
    x_search_limit: int = 8
    x_search_product: str = "Top"
    x_post_char_limit: int = 2000
    reply_target_min_author_followers: int = 50_000
    reply_target_min_views: int = 500
    trend_sources: str = "x,google_trends,rss"
    google_trends_geo: str = "US"
    trend_rss_urls: str = ""
    hashtag_mode: str = "auto"
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
    automation_approvals_path: str = ""
    download_max_file_mb: int = 45
    download_timeout_seconds: int = 180
    download_cookies_file: str = ""
    gemini_image_prompt_prefix: str = (
        "Create one square realistic image for this social post. Return the image only, "
        "with no extra text."
    )

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
            generate_images=_bool_env("GENERATE_IMAGES", False),
            image_provider=_choice_env(
                "IMAGE_PROVIDER",
                "extension_bridge",
                {"extension_bridge"},
            ),
            x_cookie=os.getenv("X_COOKIE", "").strip(),
            x_account_name=os.getenv("X_ACCOUNT_NAME", "telegram_bot").strip()
            or "telegram_bot",
            x_accounts_db=os.getenv(
                "X_ACCOUNTS_DB", "data/twscrape_accounts.db"
            ).strip()
            or "data/twscrape_accounts.db",
            x_search_limit=_int_env("X_SEARCH_LIMIT", 8),
            x_search_product=os.getenv("X_SEARCH_PRODUCT", "Top").strip() or "Top",
            x_post_char_limit=_int_env("X_POST_CHAR_LIMIT", 2000),
            reply_target_min_author_followers=max(
                0, _int_env("REPLY_TARGET_MIN_AUTHOR_FOLLOWERS", 50_000)
            ),
            reply_target_min_views=max(0, _int_env("REPLY_TARGET_MIN_VIEWS", 500)),
            trend_sources=os.getenv("TREND_SOURCES", "x,google_trends,rss").strip()
            or "x,google_trends,rss",
            google_trends_geo=os.getenv("GOOGLE_TRENDS_GEO", "US").strip() or "US",
            trend_rss_urls=os.getenv("TREND_RSS_URLS", "").strip(),
            hashtag_mode=_choice_env("HASHTAG_MODE", "auto", {"none", "auto", "one"}),
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
            automation_approvals_path=os.getenv(
                "AUTOMATION_APPROVALS_PATH",
                "data/automation_approvals.json",
            ).strip()
            or "data/automation_approvals.json",
            download_max_file_mb=_int_env("DOWNLOAD_MAX_FILE_MB", 45),
            download_timeout_seconds=_int_env("DOWNLOAD_TIMEOUT_SECONDS", 180),
            download_cookies_file=os.getenv("DOWNLOAD_COOKIES_FILE", "").strip(),
            gemini_image_prompt_prefix=(
                os.getenv("GEMINI_IMAGE_PROMPT_PREFIX")
                or os.getenv(
                    "GROK_IMAGE_PROMPT_PREFIX",
                    (
                        "Create one square realistic image for this social post. "
                        "Return the image only, with no extra text."
                    ),
                )
            ).strip()
            or "Create one square realistic image for this social post. Return the image only.",
        )

    @property
    def grok_image_prompt_prefix(self) -> str:
        return self.gemini_image_prompt_prefix


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
