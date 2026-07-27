import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from src.bot import (
    BOT_COMMANDS,
    ContentBot,
    _dedupe_queries,
    _exception_detail,
    _extract_media_url,
    _format_file_size,
    _friendly_error,
    _format_reply_target_link,
    _format_reply_target_reply,
    _format_trend_variant_copy,
    _format_x_account_error_notification,
    _no_reply_targets_message,
    _approval_message_text,
    _approval_keyboard,
    _mobile_approval_note,
    _mobile_x_intent_url,
    _mobile_x_open_url,
    _parse_importcookie_args,
    _parse_persona_args,
    _parse_retweet_args,
    _parse_tweettrend3_args,
    _reply_target_interval_minutes,
    _x_account_error_notifications,
)
from src.config import Settings
from src.media_download_service import DownloadedMedia
from src.models import (
    GeneratedContent,
    ReplyTargetDraft,
    TrendPostVariant,
    TrendSignal,
    XSearchResult,
    XTrend,
)


def test_parse_importcookie_args_default_account() -> None:
    account, cookie = _parse_importcookie_args(
        "auth_token=abc; ct0=def",
        "telegram_bot",
    )

    assert account == "telegram_bot"
    assert cookie == "auth_token=abc; ct0=def"


def test_parse_importcookie_args_named_account() -> None:
    account, cookie = _parse_importcookie_args(
        "account2 auth_token=abc; ct0=def",
        "telegram_bot",
    )

    assert account == "account2"
    assert cookie == "auth_token=abc; ct0=def"


def test_parse_persona_args() -> None:
    updates = _parse_persona_args(
        "niche=AI automation; voice=witty and practical; audience=US founders"
    )

    assert updates == {
        "niche": "AI automation",
        "voice": "witty and practical",
        "audience": "US founders",
    }


def test_parse_persona_args_rejects_unknown_key() -> None:
    with pytest.raises(RuntimeError):
        _parse_persona_args("tone=witty")


def test_parse_retweet_args() -> None:
    link, visual_note = _parse_retweet_args(
        "https://x.com/user/status/123 | Brazil supporter in yellow jersey sitting in stadium stands"
    )

    assert link == "https://x.com/user/status/123"
    assert visual_note == "Brazil supporter in yellow jersey sitting in stadium stands"


def test_parse_retweet_args_without_pipe() -> None:
    link, visual_note = _parse_retweet_args(
        "https://x.com/user/status/123 Brazil supporter in stadium stands"
    )

    assert link == "https://x.com/user/status/123"
    assert visual_note == "Brazil supporter in stadium stands"


def test_parse_tweettrend3_args_defaults_to_auto_vietnamese() -> None:
    assert _parse_tweettrend3_args([]) == ("auto", "Vietnamese")


def test_removed_commands_are_not_registered_in_telegram_menu() -> None:
    commands = {command.command for command in BOT_COMMANDS}

    assert {"vntweet", "angles", "xsearch"}.isdisjoint(commands)
    assert {"tweet", "tweettrend3", "tweetx", "dailybrief", "retweet", "reply"}.issubset(commands)
    assert "replyevery" in commands
    assert "download" in commands


def test_extract_media_url_accepts_share_text_and_trims_punctuation() -> None:
    assert _extract_media_url(
        "Check this video https://v.douyin.com/example/?share=1)."
    ) == "https://v.douyin.com/example/?share=1"


def test_format_file_size_is_human_readable() -> None:
    assert _format_file_size(512) == "1 KB"
    assert _format_file_size(3 * 1024 * 1024) == "3.0 MB"


def test_download_command_sends_document_and_cleans_temp_file(tmp_path) -> None:
    media_path = tmp_path / "sample.mp4"
    media_path.write_bytes(b"downloaded video")
    downloaded = DownloadedMedia(
        path=media_path,
        title="Sample",
        source_url="https://www.tiktok.com/@creator/video/123",
        extractor="TikTok",
    )

    class FakeDownloader:
        def download(self, url):
            assert url == downloaded.source_url
            return downloaded

    class FakeChat:
        async def send_action(self, action):
            assert action

    class FakeStatus:
        def __init__(self):
            self.edits = []
            self.deleted = False

        async def edit_text(self, text):
            self.edits.append(text)

        async def delete(self):
            self.deleted = True

    class FakeDownloadMessage:
        def __init__(self):
            self.text = f"/download {downloaded.source_url}"
            self.caption = None
            self.chat = FakeChat()
            self.status = FakeStatus()
            self.document_bytes = b""

        async def reply_text(self, text):
            assert text == "Downloading the video..."
            return self.status

        async def reply_document(self, document, **kwargs):
            self.document_bytes = document.read()
            assert kwargs["filename"] == "sample.mp4"
            assert "Source:" in kwargs["caption"]

    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
    bot.media_downloader = FakeDownloader()
    message = FakeDownloadMessage()
    update = SimpleNamespace(effective_message=message)
    context = SimpleNamespace(args=[downloaded.source_url])

    asyncio.run(bot.download(update, context))

    assert message.document_bytes == b"downloaded video"
    assert message.status.deleted is True
    assert not media_path.exists()


def test_automation_config_exposes_telegram_reply_interval() -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            telegram_reply_targets_minutes=45,
        )
    )

    assert asyncio.run(bot.get_automation_config()) == {
        "reply_targets_minutes": 45,
        "reply_targets_updated_at": None,
        "automation_running": False,
    }

    bot._automation_running.add("replytargets")
    assert asyncio.run(bot.get_automation_config())["automation_running"] is True


def test_parse_tweettrend3_args_accepts_vietnamese_shortcut() -> None:
    assert _parse_tweettrend3_args(["vi"]) == ("auto", "Vietnamese")
    assert _parse_tweettrend3_args(["news", "vi"]) == ("news", "Vietnamese")
    assert _parse_tweettrend3_args(["vi", "entertainment"]) == (
        "entertainment",
        "Vietnamese",
    )
    assert _parse_tweettrend3_args(["news", "en"]) == ("news", "Vietnamese")


def test_format_reply_target_messages_are_copy_focused() -> None:
    draft = ReplyTargetDraft(
        url="https://x.com/user/status/123",
        target="@user - AI",
        reason="Good target",
        reply="This is the reply.",
    )

    assert _format_reply_target_reply(draft) == "This is the reply."
    assert _format_reply_target_link(draft) == "https://x.com/user/status/123"


def test_mobile_reply_intent_prefills_text_and_target_tweet() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC", generate_images=False))
    approval = bot.approvals.create(
        kind="reply",
        text="This is ready to paste & reply.",
        chat_id=123,
        target_url="https://x.com/user/status/987654321",
    )

    parsed = urlparse(_mobile_x_intent_url(approval))
    params = parse_qs(parsed.query)

    assert parsed.netloc == "x.com"
    assert parsed.path == "/intent/tweet"
    assert params["text"] == ["This is ready to paste & reply."]
    assert params["in_reply_to"] == ["987654321"]


def test_approval_keyboard_adds_mobile_intent_and_short_copy_button() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC", generate_images=False))
    approval = bot.approvals.create(
        kind="post",
        text="Short post draft",
        chat_id=123,
    )

    markup = _approval_keyboard(approval)
    buttons = [button for row in markup.inline_keyboard for button in row]
    approved_buttons = [
        button
        for row in _approval_keyboard(approval, include_decisions=False).inline_keyboard
        for button in row
    ]

    assert any(button.callback_data == f"automation:mobile:{approval.id}" for button in buttons)
    assert all(button.url is None for button in buttons)
    assert any(
        button.url and button.url.startswith("https://x.com/intent/tweet?")
        for button in approved_buttons
    )
    assert any(button.copy_text and button.copy_text.text == approval.text for button in buttons)


def test_mobile_post_falls_back_to_a_short_composer_url_when_draft_is_long() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC", generate_images=False))
    approval = bot.approvals.create(
        kind="post",
        text="Nội dung dài " * 200,
        chat_id=123,
    )

    assert len(_mobile_x_intent_url(approval)) > 1800
    assert _mobile_x_open_url(approval) == "https://x.com/compose/post"
    assert "copy it above" in _mobile_approval_note(approval)


def test_reply_approval_message_contains_only_link_and_draft() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC", generate_images=False))
    approval = bot.approvals.create(
        kind="reply",
        text="This is the reply draft.",
        chat_id=123,
        target_url="https://x.com/user/status/123",
        target_label="@user - topic",
    )

    text = _approval_message_text(approval, reason="High engagement")

    assert text == "https://x.com/user/status/123\n\nThis is the reply draft."
    assert "Reply approval" not in text
    assert "Why:" not in text
    assert "Choose" not in text
    assert "Approved" not in text


def test_approval_keyboard_omits_copy_button_for_long_post() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC", generate_images=False))
    approval = bot.approvals.create(
        kind="post",
        text="x" * 257,
        chat_id=123,
    )

    buttons = [
        button
        for row in _approval_keyboard(approval).inline_keyboard
        for button in row
    ]

    assert all(button.copy_text is None for button in buttons)


def test_format_trend_variant_copy_removes_option_score_metadata() -> None:
    variant = TrendPostVariant(
        angle="Useful observation",
        text="Entertainment drama is really a fight over who gets to own the story.",
        hashtags=["#EntertainmentBiz", "#CreatorLife"],
        image_prompt="realistic photo",
        score="Originality 4/5, Clarity 5/5",
    )

    copy_text = _format_trend_variant_copy(variant)

    assert copy_text == (
        "Entertainment drama is really a fight over who gets to own the story.\n\n"
        "#EntertainmentBiz #CreatorLife"
    )
    assert "Option" not in copy_text
    assert "Score" not in copy_text
    assert "Hashtags:" not in copy_text


def test_format_trend_variant_copy_does_not_duplicate_existing_hashtags() -> None:
    variant = TrendPostVariant(
        angle="Useful observation",
        text="AI wrappers are becoming cable bundles with better branding. #AI",
        hashtags=["#AI", "#CreatorTools"],
        image_prompt="realistic photo",
        score="",
    )

    assert _format_trend_variant_copy(variant).endswith("#CreatorTools")
    assert _format_trend_variant_copy(variant).count("#AI") == 1


def test_send_optional_image_is_silent_when_images_are_disabled() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC", generate_images=False))
    message = _FakeMessage()
    generated = GeneratedContent(
        text="Post text",
        image_prompt="image prompt should not be sent",
        topic="topic",
    )

    asyncio.run(bot._send_optional_image(message, generated, "topic"))

    assert message.texts == []
    assert message.photos == []


def test_send_trend_variant_does_not_send_image_prompt_when_images_are_disabled() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC", generate_images=False))
    message = _FakeMessage()
    variant = TrendPostVariant(
        angle="Angle",
        text="Post text",
        hashtags=[],
        image_prompt="image prompt should not be sent",
        score="",
    )

    asyncio.run(bot._send_trend_variant(message, variant, 1))

    assert message.texts == ["Post text"]
    assert message.photos == []


def test_send_trend_variant_uses_approval_card_when_provided() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC", generate_images=False))
    message = _FakeMessage()
    variant = TrendPostVariant(
        angle="Angle",
        text="Post text",
        hashtags=[],
        image_prompt="image prompt",
        score="",
    )
    approval = bot.approvals.create(
        kind="post",
        text="Post text",
        chat_id=123,
        approver_user_id=456,
    )
    sent = []

    async def fake_send_approval(item, *, reason=""):
        sent.append((item, reason))

    bot._send_approval = fake_send_approval

    asyncio.run(
        bot._send_trend_variant(
            message,
            variant,
            1,
            approval=approval,
            approval_reason="scheduled trend",
        )
    )

    assert message.texts == []
    assert sent == [(approval, "scheduled trend")]


def test_dedupe_queries_preserves_order() -> None:
    assert _dedupe_queries(["AI", " ai ", "", "Crypto", "crypto"]) == ["AI", "Crypto"]


def test_reply_target_interval_is_clamped_to_supported_schedule_range() -> None:
    assert _reply_target_interval_minutes(45, default=30) == 45
    assert _reply_target_interval_minutes("bad", default=30) == 30
    assert _reply_target_interval_minutes(1, default=30) == 5
    assert _reply_target_interval_minutes(9999, default=30) == 1440


def test_replytargets_fetches_each_topic_once_then_relaxes_locally() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC", generate_images=False))
    attempts = []
    expected = XSearchResult(
        id=2,
        username="user",
        display_name="User",
        text="Post",
        created_at="",
        url="https://x.com/user/status/2",
        created_at_timestamp=int(datetime.now(UTC).timestamp()),
        like_count=5,
        author_followers_count=50_000,
    )

    class Status:
        async def edit_text(self, _text):
            return None

    async def auto_queries():
        return ["empty topic", "working topic"]

    async def search(query, *, interval_minutes=30):
        attempts.append((query, interval_minutes))
        if query == "working topic":
            return "working topic lang:en", [expected]
        return f"{query} lang:en", []

    bot._auto_reply_target_queries = auto_queries
    bot._search_reply_target_pool = search

    search_query, results, note = asyncio.run(
        bot._get_reply_target_context("", Status())
    )

    assert [result.id for result in results] == [expected.id]
    assert results[0].velocity_score > 0
    assert search_query == "working topic lang:en"
    assert "fresh fallback" in note
    assert attempts == [
        ("empty topic", 30),
        ("working topic", 30),
    ]


def test_replytargets_relaxed_ranking_keeps_view_count_as_a_hard_floor() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC", generate_images=False))
    now = int(datetime.now(UTC).timestamp())
    low_view = XSearchResult(
        id=9,
        username="lowview",
        display_name="Low View",
        text="One minute old but almost unseen",
        created_at="",
        url="https://x.com/lowview/status/9",
        created_at_timestamp=now - 60,
        like_count=9,
        view_count=9,
        author_followers_count=500_000,
    )
    qualified = XSearchResult(
        id=10,
        username="qualified",
        display_name="Qualified",
        text="Fresh post with real distribution",
        created_at="",
        url="https://x.com/qualified/status/10",
        created_at_timestamp=now - 120,
        like_count=5,
        view_count=500,
        author_followers_count=500_000,
    )

    ranked = bot._rank_reply_target_pool(
        [low_view, qualified],
        relaxed=True,
        interval_minutes=15,
    )

    assert [result.id for result in ranked] == [10]


def test_replytargets_relaxed_ranking_rejects_fresh_posts_without_signal() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC", generate_images=False))
    no_signal = XSearchResult(
        id=11,
        username="nosignal",
        display_name="No Signal",
        text="Fresh but no visible engagement",
        created_at="",
        url="https://x.com/nosignal/status/11",
        created_at_timestamp=int(datetime.now(UTC).timestamp()) - 60,
        view_count=None,
        author_followers_count=500_000,
    )

    ranked = bot._rank_reply_target_pool(
        [no_signal],
        relaxed=True,
        interval_minutes=15,
    )

    assert ranked == []


def test_replytargets_never_accepts_posts_older_than_interval() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC", generate_images=False))
    expected = XSearchResult(
        id=3,
        username="user",
        display_name="User",
        text="Post",
        created_at="",
        url="https://x.com/user/status/3",
        created_at_timestamp=int((datetime.now(UTC) - timedelta(hours=2)).timestamp()),
    )

    class Status:
        async def edit_text(self, _text):
            return None

    async def auto_queries():
        return ["first topic", "second topic"]

    async def search(query, *, interval_minutes=30):
        if query == "second topic":
            return "second topic since_time:1", [expected]
        return "first topic since_time:1", []

    bot._auto_reply_target_queries = auto_queries
    bot._search_reply_target_pool = search

    search_query, results, note = asyncio.run(
        bot._get_reply_target_context("", Status())
    )

    assert search_query == "second topic since_time:1"
    assert results == []
    assert note == ""


def test_replytargets_auto_topic_discovery_uses_one_bounded_trend_call() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC", generate_images=False))
    calls = []

    class XSearch:
        async def trends(self, category, limit):
            calls.append((category, limit))
            return [XTrend(name="AI launch", rank="1", description="")]

    bot.x_search = XSearch()
    queries = asyncio.run(bot._auto_reply_target_queries())

    assert calls == [("trending", 4)]
    assert queries[0] == "AI launch"
    assert bot.settings.creator_niche not in queries


def test_replytargets_searches_top_posts_within_the_freshness_window() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC", generate_images=False))
    calls = []

    class XSearch:
        async def search_recent(self, query, **kwargs):
            calls.append((query, kwargs))
            return "news lang:en since_time:1", []

    bot.x_search = XSearch()
    asyncio.run(bot._search_reply_target_pool("news", interval_minutes=15))

    assert calls[0][0] == "news"
    assert calls[0][1]["since_minutes"] == 15
    assert calls[0][1]["product"] == "Top"


def test_tweettrend3_collects_three_distinct_topics() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC", generate_images=False))
    searched = []

    class Status:
        async def edit_text(self, _text):
            return None

    class Trends:
        async def collect(self, category):
            return [
                TrendSignal(title="Topic one", source="RSS", category=category, score=30),
                TrendSignal(title="Topic two", source="RSS", category=category, score=20),
                TrendSignal(title="Topic three", source="RSS", category=category, score=10),
            ], []

    class XSearch:
        async def search_recent(self, query, **_kwargs):
            searched.append(query)
            return query, []

    bot.trend_sources = Trends()
    bot.x_search = XSearch()

    contexts = asyncio.run(
        bot._get_trend_contexts_for_tweettrend3("news", Status())
    )

    assert [context[0] for context in contexts] == [
        "Topic one",
        "Topic two",
        "Topic three",
    ]
    assert searched == ["Topic one", "Topic two", "Topic three"]


def test_tweettrend3_auto_prefers_creator_niche_topics() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC", generate_images=False))

    class Status:
        async def edit_text(self, _text):
            return None

    class Trends:
        async def collect_niche(self, niche):
            assert niche == bot.settings.creator_niche
            return [
                TrendSignal(title="Creator tool launch", source="Niche", category="niche", score=5),
                TrendSignal(title="AI workflow update", source="Niche", category="niche", score=4),
                TrendSignal(title="Indie business funding", source="Niche", category="niche", score=3),
            ], []

        async def collect(self, _category):
            raise AssertionError("General trends should not be needed when niche has three topics")

    class XSearch:
        async def search_recent(self, query, **_kwargs):
            return query, []

    bot.trend_sources = Trends()
    bot.x_search = XSearch()

    contexts = asyncio.run(bot._get_trend_contexts_for_tweettrend3("auto", Status()))

    assert [context[0] for context in contexts] == [
        "Creator tool launch",
        "AI workflow update",
        "Indie business funding",
    ]


def test_no_reply_targets_message_allows_auto_mode() -> None:
    message = _no_reply_targets_message("auto hot topics", auto=True)

    assert "last 30 minutes" in message
    assert "without accepting older posts" in message
    assert "/replytargets crypto" in message


def test_x_account_error_notifications_only_report_new_errors() -> None:
    seen: dict[str, str] = {}
    accounts = [
        {"username": "account2", "error_msg": "Internal server error"},
        {"username": "account3", "error_msg": "None"},
    ]

    first = _x_account_error_notifications(accounts, seen)
    second = _x_account_error_notifications(accounts, seen)

    assert len(first) == 1
    assert "account2" in first[0]
    assert "/xremove account2" in first[0]
    assert second == []


def test_x_account_error_notifications_reset_when_error_clears() -> None:
    seen = {"account2": "Internal server error"}

    notifications = _x_account_error_notifications(
        [{"username": "account2", "error_msg": "None"}],
        seen,
    )

    assert notifications == []
    assert seen == {}


def test_format_x_account_error_notification_has_recovery_commands() -> None:
    message = _format_x_account_error_notification("account2", "Internal server error")

    assert "/xremove account2" in message
    assert "/importcookie account2 auth_token=...; ct0=..." in message


def test_exception_detail_falls_back_to_exception_type() -> None:
    assert _exception_detail(TimeoutError()) == "TimeoutError"


def test_friendly_error_does_not_return_empty_detail() -> None:
    assert "The request timed out." in _friendly_error(TimeoutError())


def test_friendly_error_guides_extension_bridge_connection() -> None:
    message = _friendly_error(
        RuntimeError("Connection refused")
    )

    assert "local Chrome extension bridge" in message
    assert "Bridge URL/token" in message


class _FakeMessage:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.photos: list[object] = []

    async def reply_text(self, text: str):
        self.texts.append(text)
        return self

    async def reply_photo(self, photo, caption=None):
        self.photos.append((photo, caption))
        return self

    async def delete(self) -> None:
        return None

    async def edit_text(self, text: str) -> None:
        self.texts.append(text)
