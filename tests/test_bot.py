import asyncio
from urllib.parse import parse_qs, urlparse

import pytest

from src.bot import (
    BOT_COMMANDS,
    ContentBot,
    _dedupe_queries,
    _exception_detail,
    _friendly_error,
    _format_reply_target_link,
    _format_reply_target_reply,
    _format_trend_variant_copy,
    _format_x_account_error_notification,
    _no_reply_targets_message,
    _approval_message_text,
    _approval_keyboard,
    _mobile_x_intent_url,
    _parse_importcookie_args,
    _parse_persona_args,
    _parse_retweet_args,
    _parse_tweettrend3_args,
    _reply_target_batch_score,
    _x_account_error_notifications,
)
from src.config import Settings
from src.models import GeneratedContent
from src.models import ReplyTargetDraft, TrendPostVariant, XSearchResult


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


def test_automation_config_exposes_telegram_reply_interval() -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            telegram_reply_targets_minutes=45,
        )
    )

    assert asyncio.run(bot.get_automation_config()) == {
        "reply_targets_minutes": 45,
    }


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


def test_reply_target_batch_score_prefers_velocity_and_count() -> None:
    result = XSearchResult(
        id=1,
        username="user",
        display_name="User",
        text="Post",
        created_at="",
        url="https://x.com/user/status/1",
        velocity_score=3.5,
    )

    assert _reply_target_batch_score([result]) == 4.5


def test_replytargets_tries_other_topics_and_relaxed_mode_until_one_result() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC", generate_images=False))
    attempts = []
    expected = XSearchResult(
        id=2,
        username="user",
        display_name="User",
        text="Post",
        created_at="",
        url="https://x.com/user/status/2",
    )

    class Status:
        async def edit_text(self, _text):
            return None

    async def auto_queries():
        return ["empty topic", "working topic"]

    async def search(query, *, relaxed=False):
        attempts.append((query, relaxed))
        if query == "working topic" and relaxed:
            return "working topic lang:en", [expected]
        return f"{query} lang:en", []

    bot._auto_reply_target_queries = auto_queries
    bot._search_rank_reply_targets = search

    search_query, results, note = asyncio.run(
        bot._get_reply_target_context("", Status())
    )

    assert results == [expected]
    assert search_query == "working topic lang:en"
    assert "wider fallback" in note
    assert attempts == [
        ("empty topic", False),
        ("working topic", False),
        ("empty topic", True),
        ("working topic", True),
    ]


def test_no_reply_targets_message_allows_auto_mode() -> None:
    message = _no_reply_targets_message("auto hot topics", auto=True)

    assert "auto-scanning hot topics" in message
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
