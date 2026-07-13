from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    content_provider: str = "extension_bridge"
    generate_images: bool = True
    image_provider: str = "extension_bridge"
    telegram_caption_limit: int = 1024
    x_cookie: str = ""
    x_account_name: str = "telegram_bot"
    x_accounts_db: str = "data/twscrape_accounts.db"
    x_search_limit: int = 8
    x_search_product: str = "Top"
    x_post_char_limit: int = 2000
    trend_sources: str = "x,google_trends,rss"
    google_trends_geo: str = "US"
    trend_rss_urls: str = ""
    hashtag_mode: str = "auto"
    creator_niche: str = "AI tools, creator growth, and online business"
    creator_voice: str = "witty, practical, dry, slightly contrarian, with a sharp creator POV"
    target_audience: str = "Vietnamese X users, creators, founders, and indie hackers"
    extension_bridge_host: str = "127.0.0.1"
    extension_bridge_port: int = 8765
    extension_bridge_token: str = "local-bridge-change-me"
    extension_bridge_timeout_seconds: int = 300
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
            generate_images=_bool_env("GENERATE_IMAGES", True),
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
            trend_sources=os.getenv("TREND_SOURCES", "x,google_trends,rss").strip()
            or "x,google_trends,rss",
            google_trends_geo=os.getenv("GOOGLE_TRENDS_GEO", "US").strip() or "US",
            trend_rss_urls=os.getenv("TREND_RSS_URLS", "").strip(),
            hashtag_mode=_choice_env("HASHTAG_MODE", "auto", {"none", "auto", "one"}),
            creator_niche=os.getenv(
                "CREATOR_NICHE",
                "AI tools, creator growth, and online business",
            ).strip()
            or "AI tools, creator growth, and online business",
            creator_voice=os.getenv(
                "CREATOR_VOICE",
                "witty, practical, dry, slightly contrarian, with a sharp creator POV",
            ).strip()
            or "witty, practical, dry, slightly contrarian, with a sharp creator POV",
            target_audience=os.getenv(
                "TARGET_AUDIENCE",
                "Vietnamese X users, creators, founders, and indie hackers",
            ).strip()
            or "Vietnamese X users, creators, founders, and indie hackers",
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
            extension_bridge_timeout_seconds=_int_env("EXTENSION_BRIDGE_TIMEOUT_SECONDS", 300),
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
