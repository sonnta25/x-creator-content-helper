import asyncio

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
