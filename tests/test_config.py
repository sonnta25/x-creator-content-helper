from src import config

from src.config import Settings


def test_settings_defaults() -> None:
    settings = Settings(telegram_bot_token="123:ABC")

    assert settings.content_provider == "extension_bridge"
    assert settings.generate_images is False
    assert settings.image_provider == "extension_bridge"
    assert settings.x_cookie == ""
    assert settings.x_account_name == "telegram_bot"
    assert settings.x_accounts_db == "data/twscrape_accounts.db"
    assert settings.x_search_limit == 8
    assert settings.x_search_product == "Top"
    assert settings.x_post_char_limit == 2000
    assert settings.trend_sources == "x,google_trends,rss"
    assert settings.google_trends_geo == "US"
    assert settings.trend_rss_urls == ""
    assert settings.hashtag_mode == "auto"
    assert settings.creator_niche == "AI tools, creator growth, and online business"
    assert settings.creator_voice == (
        "witty, practical, dry, slightly contrarian, with a sharp creator POV"
    )
    assert settings.target_audience == "Vietnamese X users, creators, founders, and indie hackers"
    assert settings.extension_bridge_host == "127.0.0.1"
    assert settings.extension_bridge_port == 8765
    assert settings.extension_bridge_token == "local-bridge-change-me"
    assert settings.extension_bridge_timeout_seconds == 360
    assert settings.telegram_approval_chat_id is None
    assert settings.telegram_reply_targets_minutes is None
    assert settings.automation_approvals_path == ""
    assert "square realistic image" in settings.gemini_image_prompt_prefix
    assert settings.grok_image_prompt_prefix == settings.gemini_image_prompt_prefix


def test_settings_from_env_loads_project_env_when_cwd_changes(monkeypatch, tmp_path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TELEGRAM_BOT_TOKEN=123:ABC\nGENERATE_IMAGES=true\n"
        "X_POST_CHAR_LIMIT=1800\nTELEGRAM_REPLY_TARGETS_MINUTES=45\n",
        encoding="utf-8-sig",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.delenv("GENERATE_IMAGES", raising=False)
    monkeypatch.setattr(config, "_project_env_path", lambda: env_path)

    settings = Settings.from_env()

    assert settings.telegram_bot_token == "123:ABC"
    assert settings.generate_images is True
    assert settings.x_post_char_limit == 1800
    assert settings.telegram_reply_targets_minutes == 45
