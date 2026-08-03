from src import config

from src.config import Settings


def test_settings_defaults() -> None:
    settings = Settings(telegram_bot_token="123:ABC")

    assert settings.content_provider == "extension_bridge"
    assert settings.x_cookie == ""
    assert settings.x_account_name == "telegram_bot"
    assert settings.x_owner_username == ""
    assert settings.x_accounts_db == "data/twscrape_accounts.db"
    assert settings.x_search_limit == 8
    assert settings.x_search_product == "Top"
    assert settings.reply_target_min_author_followers == 50_000
    assert settings.reply_target_min_views == 500
    assert settings.reply_target_max_age_minutes == 360
    assert settings.reply_target_languages == "en,ja"
    assert settings.reply_target_metrics_path == ""
    assert settings.reply_learning_enabled is True
    assert settings.reply_learning_path == "data/reply_learning.json"
    assert settings.reply_tracking_poll_minutes == 5
    assert settings.reply_watch_path == "data/reply_watchlist.json"
    assert settings.reply_target_mode == "balanced"
    assert settings.creator_daily_reply_cap == 40
    assert settings.reply_target_batch_size == 3
    assert settings.reply_video_batch_size == 3
    assert settings.creator_timezone == "Asia/Ho_Chi_Minh"
    assert "gold markets" in settings.creator_niche
    assert "ChatGPT" in settings.creator_niche
    assert settings.creator_voice == (
        "witty, practical, dry, slightly contrarian, with a sharp creator POV"
    )
    assert "Vietnamese retail investors" in settings.target_audience
    assert settings.extension_bridge_host == "127.0.0.1"
    assert settings.extension_bridge_port == 8765
    assert settings.extension_bridge_token == "local-bridge-change-me"
    assert settings.extension_bridge_timeout_seconds == 360
    assert settings.telegram_approval_chat_id is None
    assert settings.telegram_reply_targets_minutes is None
    assert settings.telegram_reply_targets_updated_at is None
    assert settings.reply_video_min_views == 15_000
    assert settings.reply_video_max_age_minutes == 45
    assert settings.reply_video_frame_analysis is True
    assert settings.reply_video_frame_count == 2
    assert settings.telegram_reply_video_minutes is None
    assert settings.telegram_reply_video_updated_at is None
    assert settings.automation_approvals_path == ""
    assert settings.download_max_file_mb == 45
    assert settings.download_timeout_seconds == 180
    assert settings.download_cookies_file == ""
    assert settings.download_cookies_from_browser == ""
    assert settings.download_browser_profile == ""


def test_settings_from_env_loads_project_env_when_cwd_changes(monkeypatch, tmp_path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TELEGRAM_BOT_TOKEN=123:ABC\n"
        "TELEGRAM_REPLY_TARGETS_MINUTES=45\n"
        "TELEGRAM_REPLY_TARGETS_UPDATED_AT=123456789\n"
        "TELEGRAM_REPLY_VIDEO_MINUTES=5\n"
        "TELEGRAM_REPLY_VIDEO_UPDATED_AT=987654321\n"
        "REPLY_VIDEO_MIN_VIEWS=12000\n"
        "REPLY_VIDEO_MAX_AGE_MINUTES=40\n"
        "REPLY_VIDEO_FRAME_ANALYSIS=false\n"
        "REPLY_VIDEO_FRAME_COUNT=3\n"
        "REPLY_TARGET_MAX_AGE_MINUTES=480\n"
        "REPLY_TARGET_LANGUAGES=en,ja,ko\n"
        "REPLY_TARGET_METRICS_PATH=data/test-reply-metrics.json\n"
        "X_OWNER_USERNAME=@real_owner\n"
        "REPLY_LEARNING_ENABLED=false\n"
        "REPLY_LEARNING_PATH=data/test-reply-learning.json\n"
        "REPLY_TRACKING_POLL_MINUTES=3\n"
        "REPLY_WATCH_PATH=data/test-reply-watch.json\n"
        "REPLY_TARGET_MODE=relationship\n"
        "CREATOR_DAILY_REPLY_CAP=6\n"
        "REPLY_TARGET_BATCH_SIZE=5\n"
        "REPLY_VIDEO_BATCH_SIZE=2\n"
        "CREATOR_TIMEZONE=Asia/Tokyo\n"
        "DOWNLOAD_MAX_FILE_MB=40\nDOWNLOAD_TIMEOUT_SECONDS=120\n"
        "DOWNLOAD_COOKIES_FILE=data/cookies.txt\n"
        "DOWNLOAD_COOKIES_FROM_BROWSER=chrome\n"
        "DOWNLOAD_BROWSER_PROFILE=Profile 1\n",
        encoding="utf-8-sig",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config, "_project_env_path", lambda: env_path)

    settings = Settings.from_env()

    assert settings.telegram_bot_token == "123:ABC"
    assert settings.telegram_reply_targets_minutes == 45
    assert settings.telegram_reply_targets_updated_at == 123456789
    assert settings.telegram_reply_video_minutes == 5
    assert settings.telegram_reply_video_updated_at == 987654321
    assert settings.reply_video_min_views == 12_000
    assert settings.reply_video_max_age_minutes == 40
    assert settings.reply_video_frame_analysis is False
    assert settings.reply_video_frame_count == 3
    assert settings.reply_target_max_age_minutes == 480
    assert settings.reply_target_languages == "en,ja,ko"
    assert settings.reply_target_metrics_path == "data/test-reply-metrics.json"
    assert settings.x_owner_username == "real_owner"
    assert settings.reply_learning_enabled is False
    assert settings.reply_learning_path == "data/test-reply-learning.json"
    assert settings.reply_tracking_poll_minutes == 3
    assert settings.reply_watch_path == "data/test-reply-watch.json"
    assert settings.reply_target_mode == "relationship"
    assert settings.creator_daily_reply_cap == 6
    assert settings.reply_target_batch_size == 5
    assert settings.reply_video_batch_size == 2
    assert settings.creator_timezone == "Asia/Tokyo"
    assert settings.download_max_file_mb == 40
    assert settings.download_timeout_seconds == 120
    assert settings.download_cookies_file == "data/cookies.txt"
    assert settings.download_cookies_from_browser == "chrome"
    assert settings.download_browser_profile == "Profile 1"
