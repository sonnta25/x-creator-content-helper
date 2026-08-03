import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from telegram import ForceReply

from src.bot import (
    BOT_COMMANDS,
    ContentBot,
    _command_payload,
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
    _reply_target_max_age_minutes,
    _reply_video_search_queries,
    _video_context_quality,
    _is_reliable_video_context_text,
    _select_reply_draft_batch,
    _select_reply_video_mix,
    _updated_reply_target_languages,
    _x_account_error_notifications,
)
from src.config import Settings
from src.media_download_service import DownloadedMedia
from src.models import (
    GeneratedContent,
    ImageAttachment,
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
    assert {"replylearn", "replyreport", "replylangs", "replybatch"}.issubset(commands)
    assert {"today", "setupcheck"}.issubset(commands)
    assert "download" in commands
    assert "cancel" in commands


def test_command_payload_preserves_plain_follow_up_text() -> None:
    message = SimpleNamespace(
        text="https://www.tiktok.com/@creator/video/123",
        caption=None,
    )

    assert _command_payload(message, SimpleNamespace(args=[])) == message.text


def test_download_without_url_waits_for_next_message() -> None:
    class FakeMessage:
        text = "/download"
        caption = None

        def __init__(self):
            self.reply_markup = None

        async def reply_text(self, text, **kwargs):
            assert "Send /cancel to stop." in text
            self.reply_markup = kwargs["reply_markup"]
            return SimpleNamespace(message_id=91)

    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
    message = FakeMessage()
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(id=10, type="private"),
        effective_user=SimpleNamespace(id=20),
    )

    asyncio.run(bot.download(update, SimpleNamespace(args=[])))

    assert isinstance(message.reply_markup, ForceReply)
    assert bot._pending_inputs[(10, 20)].command == "download"
    assert bot._pending_inputs[(10, 20)].prompt_message_id == 91


def test_pending_text_runs_the_selected_command_then_clears_state() -> None:
    class FakePromptMessage:
        text = "/download"
        caption = None

        async def reply_text(self, _text, **_kwargs):
            return SimpleNamespace(message_id=92)

    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
    identity = {
        "effective_chat": SimpleNamespace(id=11, type="private"),
        "effective_user": SimpleNamespace(id=21),
    }
    prompt_update = SimpleNamespace(
        effective_message=FakePromptMessage(),
        **identity,
    )
    asyncio.run(bot.download(prompt_update, SimpleNamespace(args=[])))

    captured = {}

    async def fake_download(update, context):
        captured["text"] = update.effective_message.text
        captured["args"] = context.args

    bot.download = fake_download
    url = "https://www.tiktok.com/@creator/video/456"
    follow_up = SimpleNamespace(
        effective_message=SimpleNamespace(text=url, caption=None),
        **identity,
    )
    context = SimpleNamespace(args=[])

    asyncio.run(bot.pending_command_input(follow_up, context))

    assert captured == {"text": url, "args": [url]}
    assert (11, 21) not in bot._pending_inputs


def test_cancel_clears_pending_command() -> None:
    replies = []

    class FakeMessage:
        text = "/download"
        caption = None

        async def reply_text(self, text, **_kwargs):
            replies.append(text)
            return SimpleNamespace(message_id=93)

    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
    update = SimpleNamespace(
        effective_message=FakeMessage(),
        effective_chat=SimpleNamespace(id=12, type="private"),
        effective_user=SimpleNamespace(id=22),
    )
    asyncio.run(bot.download(update, SimpleNamespace(args=[])))
    update.effective_message.text = "/cancel"

    asyncio.run(bot.cancel(update, SimpleNamespace(args=[])))

    assert (12, 22) not in bot._pending_inputs
    assert replies[-1] == "Cancelled the pending command."


def test_extract_media_url_accepts_share_text_and_trims_punctuation() -> None:
    assert _extract_media_url(
        "Check this video https://v.douyin.com/example/?share=1)."
    ) == "https://v.douyin.com/example/?share=1"


def test_format_file_size_is_human_readable() -> None:
    assert _format_file_size(512) == "1 KB"
    assert _format_file_size(3 * 1024 * 1024) == "3.0 MB"


def test_download_command_sends_document_and_cleans_temp_file(tmp_path) -> None:
    media_path = tmp_path / "creator-video-20260727-101112-a1b2c3.mp4"
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
            assert kwargs["filename"] == media_path.name
            assert "Prepared video file" in kwargs["caption"]
            assert "Source reference:" in kwargs["caption"]
            assert "Sample" not in kwargs["caption"]

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
        "reply_video_minutes": None,
        "reply_video_updated_at": None,
        "automation_running": False,
        "creator_timezone": "Asia/Ho_Chi_Minh",
        "reply_target_languages": "en,ja",
        "extension_bridge_timeout_seconds": 360,
    }
    bot._automation_running.add("replytargets")
    assert asyncio.run(bot.get_automation_config())["automation_running"] is True


def test_replyvideo_queries_cover_global_english_japanese_and_vietnamese() -> None:
    lanes = dict(_reply_video_search_queries())

    assert list(lanes) == ["global", "English", "Japanese", "Vietnamese"]
    assert "filter:videos min_faves:200" in lanes["global"]
    assert "min_faves:300 lang:en" in lanes["English"]
    assert "min_faves:150 lang:ja" in lanes["Japanese"]
    assert "min_faves:80 lang:vi" in lanes["Vietnamese"]


def test_replyvideo_mix_prefers_two_global_and_one_vietnamese() -> None:
    results = [
        XSearchResult(id=1, username="a", display_name="", text="a", created_at="", url="1", language="en"),
        XSearchResult(id=2, username="b", display_name="", text="b", created_at="", url="2", language="ja"),
        XSearchResult(id=3, username="c", display_name="", text="c", created_at="", url="3", language="ko"),
        XSearchResult(id=4, username="d", display_name="", text="d", created_at="", url="4", language="vi"),
    ]

    selected = _select_reply_video_mix(results)

    assert [item.language for item in selected] == ["en", "ja", "vi"]


def test_replyvideo_context_quality_rejects_empty_emoji_and_boilerplate() -> None:
    assert _is_reliable_video_context_text("ðŸ˜‚ðŸ˜‚ðŸ˜‚") is False
    assert _is_reliable_video_context_text("Watch this") is False
    assert _is_reliable_video_context_text(
        "Video attached to this post",
        media_description=True,
    ) is False
    assert _is_reliable_video_context_text(
        "A goalkeeper reaches the ball beside the left post",
        media_description=True,
    ) is True

    no_context = XSearchResult(
        id=8,
        username="clip",
        display_name="",
        text="ðŸ˜‚",
        created_at="",
        url="https://x.com/clip/status/8",
        has_video=True,
        media_descriptions=["Video attached"],
    )
    caption_only = XSearchResult(
        id=9,
        username="clip",
        display_name="",
        text="The goalkeeper recovered after slipping at the near post",
        created_at="",
        url="https://x.com/clip/status/9",
        has_video=True,
    )

    assert _video_context_quality(no_context) == "visual_required"
    assert _video_context_quality(caption_only) == "caption_only"


def test_replyvideo_extracts_frames_only_for_ungrounded_candidate(tmp_path) -> None:
    cleaned: list[str] = []

    class FakeDownloader:
        def download(self, url):
            return SimpleNamespace(
                path=tmp_path / "clip.mp4",
                cleanup=lambda: cleaned.append(url),
            )

    class FakeExtractor:
        def extract(self, path, *, prefix, max_frames):
            assert path == tmp_path / "clip.mp4"
            assert max_frames == 2
            return [
                ImageAttachment(f"{prefix}-frame-01.jpg", "image/jpeg", b"a" * 200),
                ImageAttachment(f"{prefix}-frame-02.jpg", "image/jpeg", b"b" * 200),
            ]

    class Status:
        async def edit_text(self, text):
            self.text = text

    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
    bot.media_downloader = FakeDownloader()
    bot.video_frame_extractor = FakeExtractor()
    ungrounded = XSearchResult(
        id=10,
        username="clip",
        display_name="",
        text="",
        created_at="",
        url="https://x.com/clip/status/10",
        has_video=True,
    )
    captioned = XSearchResult(
        id=11,
        username="clip",
        display_name="",
        text="A dog catches the ball before it hits the ground",
        created_at="",
        url="https://x.com/clip/status/11",
        has_video=True,
    )

    prepared, attachments, skipped = asyncio.run(
        bot._prepare_reply_video_evidence([ungrounded, captioned], Status(), max_items=2)
    )

    assert [item.video_context_quality for item in prepared] == [
        "visual_frames",
        "caption_only",
    ]
    assert len(attachments) == 2
    assert skipped == 0
    assert cleaned == [ungrounded.url]


def test_replyvideo_cleans_download_when_frame_extraction_fails(tmp_path) -> None:
    cleaned: list[bool] = []

    class FakeDownloader:
        def download(self, _url):
            return SimpleNamespace(
                path=tmp_path / "clip.mp4",
                cleanup=lambda: cleaned.append(True),
            )

    class FailingExtractor:
        def extract(self, _path, **_kwargs):
            raise RuntimeError("broken video")

    class Status:
        async def edit_text(self, _text):
            return None

    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
    bot.media_downloader = FakeDownloader()
    bot.video_frame_extractor = FailingExtractor()
    ungrounded = XSearchResult(
        id=12,
        username="clip",
        display_name="",
        text="",
        created_at="",
        url="https://x.com/clip/status/12",
        has_video=True,
    )

    prepared, attachments, skipped = asyncio.run(
        bot._prepare_reply_video_evidence([ungrounded], Status(), max_items=2)
    )

    assert prepared == []
    assert attachments == []
    assert skipped == 1
    assert cleaned == [True]

def test_parse_tweettrend3_args_accepts_vietnamese_shortcut() -> None:
    assert _parse_tweettrend3_args(["vi"]) == ("auto", "Vietnamese")
    assert _parse_tweettrend3_args(["news", "vi"]) == ("news", "Vietnamese")
    assert _parse_tweettrend3_args(["vi", "entertainment"]) == (
        "entertainment",
        "Vietnamese",
    )
    assert _parse_tweettrend3_args(["news", "en"]) == ("news", "Vietnamese")


def test_reply_language_updates_add_remove_set_and_validate_limits() -> None:
    assert _updated_reply_target_languages("en,ja", "add", "ko, es") == [
        "en",
        "ja",
        "ko",
        "es",
    ]
    assert _updated_reply_target_languages("en,ja,ko", "remove", "ja") == [
        "en",
        "ko",
    ]
    assert _updated_reply_target_languages("en,ja", "set", "vn jp kr") == [
        "vi",
        "ja",
        "ko",
    ]
    with pytest.raises(RuntimeError, match="Unsupported X language"):
        _updated_reply_target_languages("en,ja", "add", "xx")
    with pytest.raises(RuntimeError, match="At least one"):
        _updated_reply_target_languages("en", "remove", "en")
    with pytest.raises(RuntimeError, match="at most 6"):
        _updated_reply_target_languages("en,ja", "set", "en ja ko es pt id vi")


def test_replylangs_command_updates_runtime_settings_and_env(monkeypatch) -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
    saved = []
    replies = []

    async def reply_text(text):
        replies.append(text)

    monkeypatch.setattr("src.bot.update_env_value", lambda name, value: saved.append((name, value)))
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=123, type="private"),
        effective_message=SimpleNamespace(reply_text=reply_text),
    )
    context = SimpleNamespace(args=["add", "ko", "es"])

    asyncio.run(bot.replylangs(update, context))

    assert bot.settings.reply_target_languages == "en,ja,ko,es"
    assert saved == [("REPLY_TARGET_LANGUAGES", "en,ja,ko,es")]
    assert "Saved to .env" in replies[0]


def test_replybatch_command_updates_each_runtime_setting_and_env(monkeypatch) -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
    saved = []
    replies = []

    async def reply_text(text):
        replies.append(text)

    monkeypatch.setattr(
        "src.bot.update_env_value",
        lambda name, value: saved.append((name, value)),
    )
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=123, type="private"),
        effective_message=SimpleNamespace(reply_text=reply_text),
    )

    asyncio.run(bot.replybatch(update, SimpleNamespace(args=["targets", "5"])))
    asyncio.run(bot.replybatch(update, SimpleNamespace(args=["video", "2"])))

    assert bot.settings.reply_target_batch_size == 5
    assert bot.settings.reply_video_batch_size == 2
    assert saved == [
        ("REPLY_TARGET_BATCH_SIZE", "5"),
        ("REPLY_VIDEO_BATCH_SIZE", "2"),
    ]
    assert all("applied immediately" in reply for reply in replies)


def test_replybatch_command_rejects_values_outside_two_to_five() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
    replies = []

    async def reply_text(text):
        replies.append(text)

    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=123, type="private"),
        effective_message=SimpleNamespace(reply_text=reply_text),
    )

    asyncio.run(bot.replybatch(update, SimpleNamespace(args=["targets", "6"])))

    assert bot.settings.reply_target_batch_size == 3
    assert replies == ["Batch size must be between 2 and 5 replies."]


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


def test_reply_and_post_approval_cards_offer_lazy_quick_actions() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC", generate_images=False))
    reply = bot.approvals.create(
        kind="reply",
        text="A specific reply",
        chat_id=123,
        target_url="https://x.com/source/status/1",
    )
    post = bot.approvals.create(
        kind="post",
        text="An original post",
        chat_id=123,
        metadata={"image_prompt": "A square realistic photo"},
    )

    reply_callbacks = {
        button.callback_data
        for row in _approval_keyboard(reply).inline_keyboard
        for button in row
        if button.callback_data
    }
    post_callbacks = {
        button.callback_data
        for row in _approval_keyboard(post).inline_keyboard
        for button in row
        if button.callback_data
    }

    assert f"automation:alternative:{reply.id}" in reply_callbacks
    assert f"automation:shorter:{reply.id}" in reply_callbacks
    assert f"automation:visual:{post.id}" in post_callbacks
    assert f"automation:skip:{reply.id}" in reply_callbacks


def test_author_followup_card_shows_response_and_continue_stop_actions() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
    approval = bot.approvals.create(
        kind="reply",
        text="Retention being decisive explains the slower rollout.",
        chat_id=123,
        target_url="https://x.com/source/status/101",
        metadata={
            "relationship_followup": True,
            "relationship_parent_approval_id": "parent-1",
            "root_author": "source",
            "author_response_url": "https://x.com/source/status/101",
            "author_response_text": (
                "Good question. Retention was the deciding factor."
            ),
        },
    )

    text = _approval_message_text(approval)
    buttons = {
        button.callback_data: button.text
        for row in _approval_keyboard(approval).inline_keyboard
        for button in row
        if button.callback_data
    }

    assert "@source replied:" in text
    assert "Retention was the deciding factor" in text
    assert "Suggested follow-up:" in text
    assert buttons[f"automation:continue:{approval.id}"] == "Continue conversation"
    assert buttons[f"automation:stop:{approval.id}"] == "Stop here"
    assert f"automation:mobile:{approval.id}" not in buttons
    assert f"automation:skip:{approval.id}" not in buttons


def test_author_response_is_detected_between_metric_checkpoints(tmp_path) -> None:
    learning_path = tmp_path / "learning.json"
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            reply_learning_path=str(learning_path),
            reply_watch_path="",
        )
    )
    posted_at = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
    original = bot.approvals.create(
        kind="reply",
        text="Which tradeoff mattered most?",
        chat_id=123,
        approver_user_id=123,
        target_url="https://x.com/source/status/42",
        metadata={
            "reply_strategy": "author_specific_question",
            "root_author": "source",
            "root_views": 1_000,
            "root_replies": 5,
        },
    )
    bot.approvals.decide(
        original.id,
        approve=True,
        chat_id=123,
        user_id=123,
        destination="mobile",
    )
    bot.reply_learning.register_approval(original)
    posted = XSearchResult(
        id=100,
        username="owner",
        display_name="Owner",
        text=original.text,
        created_at=posted_at.isoformat(),
        created_at_timestamp=int(posted_at.timestamp()),
        url="https://x.com/owner/status/100",
        is_reply=True,
        in_reply_to_tweet_id=42,
    )
    bot.reply_learning.mark_discovered(original.id, posted)
    bot.reply_learning.add_snapshot(
        original.id,
        checkpoint_minutes=15,
        reply=posted,
        root=XSearchResult(
            id=42,
            username="source",
            display_name="Source",
            text="Launch details",
            created_at=posted_at.isoformat(),
            url="https://x.com/source/status/42",
        ),
        captured_at=posted_at + timedelta(minutes=15),
    )
    response = XSearchResult(
        id=101,
        username="source",
        display_name="Source",
        text="Retention was the deciding factor.",
        created_at=(posted_at + timedelta(minutes=18)).isoformat(),
        created_at_timestamp=int((posted_at + timedelta(minutes=18)).timestamp()),
        url="https://x.com/source/status/101",
        is_reply=True,
        in_reply_to_tweet_id=100,
    )

    class FakeXSearch:
        async def tweet_replies(self, tweet_id, *, limit):
            assert tweet_id == 100
            assert limit == 20
            return [response]

        async def tweet_by_id(self, _tweet_id):
            raise AssertionError("No metric checkpoint is due at minute 20")

    class FakeAI:
        async def generate_reply_from_text(self, text):
            assert text == response.text
            return GeneratedContent(
                text="That makes the rollout decision much clearer.",
                image_prompt="",
                topic="reply",
            )

    sent = []

    class FakeTelegramBot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    bot.x_search = FakeXSearch()
    bot.ai = FakeAI()
    bot._application = SimpleNamespace(bot=FakeTelegramBot())

    asyncio.run(
        bot._process_reply_tracking_once(now=posted_at + timedelta(minutes=20))
    )

    record = bot.reply_learning.records("tracking")[0]
    assert record["author_replied"] is True
    assert record["followup_created"] is True
    assert record["author_response_latency_minutes"] == 18
    assert len(sent) == 1
    assert "@source replied:" in sent[0]["text"]
    callbacks = {
        button.callback_data
        for row in sent[0]["reply_markup"].inline_keyboard
        for button in row
        if button.callback_data
    }
    assert any(value.startswith("automation:continue:") for value in callbacks)
    assert any(value.startswith("automation:stop:") for value in callbacks)


def test_stop_here_marks_the_parent_conversation_stopped(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            reply_learning_path=str(tmp_path / "learning.json"),
            reply_watch_path="",
        )
    )
    parent = bot.approvals.create(
        kind="reply",
        text="Original reply",
        chat_id=123,
        approver_user_id=456,
        target_url="https://x.com/source/status/42",
        metadata={"reply_strategy": "specific_observation", "root_author": "source"},
    )
    bot.reply_learning.register_approval(parent)
    followup = bot.approvals.create(
        kind="reply",
        text="Suggested follow-up",
        chat_id=123,
        approver_user_id=456,
        target_url="https://x.com/source/status/101",
        metadata={
            "reply_strategy": "author_specific_question",
            "relationship_followup": True,
            "relationship_parent_approval_id": parent.id,
            "root_author": "source",
            "author_response_text": "Thanks for asking.",
        },
    )
    edits = []

    class FakeQuery:
        data = f"automation:stop:{followup.id}"
        from_user = SimpleNamespace(id=456)
        message = SimpleNamespace(
            chat=SimpleNamespace(id=123),
            text="Author follow-up card",
        )

        async def answer(self, *args, **kwargs):
            del args, kwargs

        async def edit_message_text(self, *args, **kwargs):
            edits.append((args, kwargs))

    update = SimpleNamespace(callback_query=FakeQuery())
    asyncio.run(bot.automation_approval(update, SimpleNamespace()))

    assert bot.approvals.get(followup.id).status == "rejected"
    assert bot.reply_learning.data["records"][parent.id]["conversation_stopped"] is True
    assert "Conversation stopped" in edits[0][0][0]


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

    assert text == (
        "https://x.com/user/status/123\n"
        "Why now: High engagement\n\n"
        "This is the reply draft."
    )
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
    assert _reply_target_max_age_minutes(360, default=360) == 360
    assert _reply_target_max_age_minutes(15, default=360) == 30
    assert _reply_target_max_age_minutes("bad", default=360) == 360


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

    async def auto_queries(_languages=None, *, mode="balanced"):
        del mode
        return ["empty topic", "working topic"]

    async def search(query, *, max_age_minutes=360):
        attempts.append((query, max_age_minutes))
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
    assert "relaxed momentum" in note
    assert attempts == [
        ("empty topic", 360),
        ("working topic", 360),
    ]


def test_replytargets_auto_mode_compares_candidates_across_topics() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC", generate_images=False))
    now = int(datetime.now(UTC).timestamp())
    first_topic_slow = XSearchResult(
        id=40,
        username="large",
        display_name="Large",
        text="Qualified, but moving slowly",
        created_at="",
        url="https://x.com/large/status/40",
        created_at_timestamp=now - 10 * 60,
        like_count=20,
        reply_count=2,
        view_count=1_000,
        author_followers_count=500_000,
    )
    second_topic_breakout = XSearchResult(
        id=41,
        username="breakout",
        display_name="Breakout",
        text="A smaller account with much stronger current momentum",
        created_at="",
        url="https://x.com/breakout/status/41",
        created_at_timestamp=now - 5 * 60,
        like_count=100,
        reply_count=20,
        quote_count=5,
        view_count=10_000,
        author_followers_count=10_000,
    )
    attempts = []

    class Status:
        async def edit_text(self, _text):
            return None

    async def auto_queries(_languages=None, *, mode="balanced"):
        del mode
        return ["first topic", "second topic"]

    async def search(query, *, max_age_minutes=360):
        del max_age_minutes
        attempts.append(query)
        if query == "first topic":
            return "first topic lang:en", [first_topic_slow]
        return "second topic lang:en", [second_topic_breakout]

    bot._auto_reply_target_queries = auto_queries
    bot._search_reply_target_pool = search

    search_query, results, note = asyncio.run(
        bot._get_reply_target_context("", Status())
    )

    assert attempts == ["first topic", "second topic"]
    assert results[0].id == second_topic_breakout.id
    assert search_query == "second topic lang:en"
    assert "across current topics" in note


def test_replytargets_auto_mode_refetches_persisted_watched_tweet(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            generate_images=False,
            reply_watch_path=str(tmp_path / "watch.json"),
            reply_target_metrics_path=str(tmp_path / "metrics.json"),
        )
    )
    now = int(datetime.now(UTC).timestamp())
    first = XSearchResult(
        id=401,
        username="watched",
        display_name="Watched",
        text="A fresh update with an open discussion",
        created_at=datetime.now(UTC).isoformat(),
        url="https://x.com/watched/status/401",
        language="en",
        created_at_timestamp=now - 5 * 60,
        like_count=0,
        reply_count=0,
        view_count=600,
        author_followers_count=5_000,
    )
    refreshed = XSearchResult(
        id=401,
        username="watched",
        display_name="Watched",
        text=first.text,
        created_at=first.created_at,
        url=first.url,
        language="en",
        created_at_timestamp=first.created_at_timestamp,
        like_count=18,
        reply_count=3,
        view_count=1_800,
        author_followers_count=5_000,
    )
    search_calls = 0
    detail_calls = []

    class Status:
        async def edit_text(self, _text):
            return None

    class XSearch:
        async def tweet_by_id(self, tweet_id):
            detail_calls.append(tweet_id)
            return refreshed

        async def tweet_replies(self, _tweet_id, *, limit):
            assert limit == 12
            return []

    async def auto_queries(_languages=None, *, mode="balanced"):
        del mode
        return ["current topic"]

    async def search(_query, *, max_age_minutes=360):
        nonlocal search_calls
        del max_age_minutes
        search_calls += 1
        return "current topic lang:en", [first] if search_calls == 1 else []

    bot.x_search = XSearch()
    bot._auto_reply_target_queries = auto_queries
    bot._search_reply_target_pool = search

    _query, first_results, _note = asyncio.run(
        bot._get_reply_target_context("", Status(), languages=["en"])
    )
    ready, watching = bot.reply_watch.classify(first_results)
    assert ready == []
    assert watching

    _query, second_results, _note = asyncio.run(
        bot._get_reply_target_context("", Status(), languages=["en"])
    )
    ready, watching = bot.reply_watch.classify(second_results)

    assert detail_calls == [401]
    assert [result.id for result in ready] == [401]
    assert watching == []
    assert second_results[0].view_count == 1_800


def test_scheduled_replytargets_reports_daily_cap_instead_of_confirmation(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            generate_images=False,
            creator_daily_reply_cap=1,
            automation_approvals_path=str(tmp_path / "approvals.json"),
            reply_watch_path=str(tmp_path / "watch.json"),
        )
    )
    bot.approval_chat_id = 123
    candidate = XSearchResult(
        id=402,
        username="ready",
        display_name="Ready",
        text="A confirmed candidate",
        created_at=datetime.now(UTC).isoformat(),
        url="https://x.com/ready/status/402",
    )
    bot.approvals.create(
        kind="reply",
        text="Existing reply card",
        chat_id=123,
        approver_user_id=123,
        target_url="https://x.com/already/status/1",
    )
    messages = []

    class Status:
        async def edit_text(self, text):
            messages.append(text)

    class TelegramBot:
        async def send_message(self, **_kwargs):
            return Status()

    async def context(*_args, **_kwargs):
        return "auto hot topics", [candidate], ""

    bot._application = SimpleNamespace(bot=TelegramBot())
    bot._get_reply_target_context = context
    bot.reply_watch.classify = lambda _results: ([candidate], [])

    asyncio.run(bot._run_scheduled_replytargets("", 360, ["en"]))

    assert "reached today's reply-card cap" in messages[-1]
    assert "Confirmed now: 1" in messages[-1]
    assert "none is confirmed enough" not in messages[-1]


def test_replytargets_explicit_topic_expands_languages_without_topic_drift() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC", generate_images=False))
    attempts = []
    japanese = XSearchResult(
        id=42,
        username="jpuser",
        display_name="JP User",
        text="AIについての話題",
        created_at="",
        url="https://x.com/jpuser/status/42",
        language="ja",
        created_at_timestamp=int(datetime.now(UTC).timestamp()) - 5 * 60,
        like_count=80,
        reply_count=12,
        quote_count=4,
        view_count=8_000,
        author_followers_count=20_000,
    )

    class Status:
        async def edit_text(self, _text):
            return None

    async def search(query, *, max_age_minutes=360):
        attempts.append((query, max_age_minutes))
        if query.endswith("lang:ja"):
            return f"{query} since_time:1", [japanese]
        return f"{query} since_time:1", []

    bot._search_reply_target_pool = search

    search_query, results, note = asyncio.run(
        bot._get_reply_target_context(
            "AI",
            Status(),
            max_age_minutes=360,
            languages=["en", "ja"],
        )
    )

    assert attempts == [("AI lang:en", 360), ("AI lang:ja", 360)]
    assert [result.id for result in results] == [42]
    assert search_query.startswith("AI lang:ja")
    assert "requested topic and languages" in note


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
        max_age_minutes=360,
    )

    assert [result.id for result in ranked] == [10]


def test_replytargets_volume_fallback_lowers_view_floor_to_fill_batch(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            generate_images=False,
            reply_watch_path=str(tmp_path / "watch.json"),
            reply_target_metrics_path=str(tmp_path / "metrics.json"),
            reply_target_min_views=500,
        )
    )
    now = int(datetime.now(UTC).timestamp())
    standard = XSearchResult(
        id=801,
        username="standard",
        display_name="Standard",
        text="Strong current discussion",
        created_at=datetime.now(UTC).isoformat(),
        url="https://x.com/standard/status/801",
        language="en",
        created_at_timestamp=now - 5 * 60,
        like_count=20,
        reply_count=2,
        view_count=2_000,
    )
    lower_view = XSearchResult(
        id=802,
        username="fallback",
        display_name="Fallback",
        text="Smaller but real current discussion",
        created_at=datetime.now(UTC).isoformat(),
        url="https://x.com/fallback/status/802",
        language="en",
        created_at_timestamp=now - 3 * 60,
        like_count=5,
        reply_count=1,
        view_count=125,
    )

    class Status:
        async def edit_text(self, _text):
            return None

    class XSearch:
        async def tweet_replies(self, _tweet_id, *, limit):
            assert limit == 12
            return []

    async def auto_queries(_languages=None, *, mode="balanced"):
        del mode
        return ["current topic"]

    async def search(_query, *, max_age_minutes=360):
        del max_age_minutes
        return "current topic lang:en", [standard, lower_view]

    bot.x_search = XSearch()
    bot._auto_reply_target_queries = auto_queries
    bot._search_reply_target_pool = search

    _query, results, note = asyncio.run(
        bot._get_reply_target_context("", Status(), languages=["en"])
    )

    assert {result.id for result in results} == {801, 802}
    assert "volume fallback (125+ views" in note


def test_replytargets_minimum_batch_fallback_accepts_any_visible_views(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            generate_images=False,
            reply_watch_path=str(tmp_path / "watch.json"),
            reply_target_metrics_path=str(tmp_path / "metrics.json"),
            reply_target_min_views=500,
        )
    )
    now = int(datetime.now(UTC).timestamp())
    candidates = [
        XSearchResult(
            id=811 + index,
            username=f"small{index}",
            display_name=f"Small {index}",
            text=f"Fresh visible post {index}",
            created_at=datetime.now(UTC).isoformat(),
            url=f"https://x.com/small{index}/status/{811 + index}",
            language="en",
            created_at_timestamp=now - ((index + 1) * 60),
            view_count=20 + index,
        )
        for index in range(2)
    ]

    class Status:
        async def edit_text(self, _text):
            return None

    class XSearch:
        async def tweet_replies(self, _tweet_id, *, limit):
            assert limit == 12
            return []

    async def auto_queries(_languages=None, *, mode="balanced"):
        del mode
        return ["current topic"]

    async def search(_query, *, max_age_minutes=360):
        del max_age_minutes
        return "current topic lang:en", candidates

    bot.x_search = XSearch()
    bot._auto_reply_target_queries = auto_queries
    bot._search_reply_target_pool = search

    _query, results, note = asyncio.run(
        bot._get_reply_target_context("", Status(), languages=["en"])
    )

    assert {result.id for result in results} == {811, 812}
    assert "minimum-batch fallback" in note


def test_replytargets_reports_search_outage_instead_of_empty_market(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            generate_images=False,
            reply_watch_path=str(tmp_path / "watch.json"),
            reply_target_metrics_path=str(tmp_path / "metrics.json"),
        )
    )

    class Status:
        async def edit_text(self, _text):
            return None

    async def auto_queries(_languages=None, *, mode="balanced"):
        del mode
        return ["current topic", "breaking news"]

    async def search(query, *, max_age_minutes=360):
        del max_age_minutes
        raise RuntimeError(f"Logged-out X web app for {query}")

    bot._auto_reply_target_queries = auto_queries
    bot._search_reply_target_pool = search

    with pytest.raises(RuntimeError, match="not a lack of viral tweets"):
        asyncio.run(bot._get_reply_target_context("", Status(), languages=["en"]))


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
        max_age_minutes=360,
    )

    assert ranked == []


def test_replytargets_never_accepts_posts_older_than_lookback() -> None:
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

    async def auto_queries(_languages=None, *, mode="balanced"):
        del mode
        return ["first topic", "second topic"]

    async def search(query, *, max_age_minutes=30):
        del max_age_minutes
        if query == "second topic":
            return "second topic since_time:1", [expected]
        return "first topic since_time:1", []

    bot._auto_reply_target_queries = auto_queries
    bot._search_reply_target_pool = search

    search_query, results, note = asyncio.run(
        bot._get_reply_target_context("", Status(), max_age_minutes=30)
    )

    assert search_query == "second topic since_time:1"
    assert results == []
    assert "Fetched 1 unique root posts" in note
    assert "age" in note


def test_replytargets_auto_topic_discovery_uses_one_bounded_trend_call() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC", generate_images=False))
    calls = []

    class XSearch:
        async def trends(self, category, limit):
            calls.append((category, limit))
            return [
                XTrend(name="AI launch", rank="1", description=""),
                XTrend(name="#OpenSource", rank="2", description=""),
                XTrend(name="Market news", rank="3", description=""),
                XTrend(name="New game", rank="4", description=""),
            ]

    bot.x_search = XSearch()
    queries = asyncio.run(bot._auto_reply_target_queries(mode="reach"))

    assert calls == [("trending", 4)]
    assert queries[:4] == [
        "AI launch (lang:en OR lang:ja)",
        "#OpenSource (lang:en OR lang:ja)",
        "Market news (lang:en OR lang:ja)",
        "New game (lang:en OR lang:ja)",
    ]
    assert "経済 OR 日銀 OR 円相場" in queries[4]
    assert queries[4].endswith("lang:ja")
    assert queries[5].endswith("lang:en")
    assert bot.settings.creator_niche not in queries


def test_replytargets_auto_fallback_round_robins_configured_languages() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC", generate_images=False))

    class XSearch:
        async def trends(self, category, limit):
            del category, limit
            return []

    bot.x_search = XSearch()
    queries = asyncio.run(bot._auto_reply_target_queries(["en", "ja"], mode="reach"))

    assert len(queries) == 7
    assert [query.rsplit("lang:", 1)[-1] for query in queries] == [
        "ja",
        "en",
        "ja",
        "en",
        "ja",
        "en",
        "ja",
    ]
    assert queries[-2:] == ["AI lang:en", "AI lang:ja"]
    assert "アニメ OR ゲーム OR テクノロジー" in queries[0]


def test_reply_target_mode_penalizes_a_dominant_top_reply() -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            creator_niche="AI automation",
            target_audience="AI builders",
        )
    )
    open_thread = XSearchResult(
        id=1,
        username="open",
        display_name="Open",
        text="AI automation launch",
        created_at="",
        url="https://x.com/open/status/1",
        viral_score=70,
        reply_opportunity_score=75,
        thread_availability_score=80,
        top_reply_like_count=1,
    )
    crowded_thread = XSearchResult(
        id=2,
        username="crowded",
        display_name="Crowded",
        text="AI automation launch",
        created_at="",
        url="https://x.com/crowded/status/2",
        viral_score=70,
        reply_opportunity_score=75,
        thread_availability_score=80,
        top_reply_like_count=10_000,
    )

    ranked = bot._apply_reply_target_mode(
        [crowded_thread, open_thread],
        "balanced",
    )

    assert ranked[0].id == open_thread.id
    assert ranked[0].audience_affinity_score > 0


def test_reply_batch_promotes_top_watching_candidate_to_reach_two() -> None:
    ready = [
        XSearchResult(
            id=701,
            username="ready",
            display_name="Ready",
            text="Confirmed",
            created_at="",
            url="https://x.com/ready/status/701",
        )
    ]
    watching = [
        XSearchResult(
            id=702,
            username="watching",
            display_name="Watching",
            text="Qualified first observation",
            created_at="",
            url="https://x.com/watching/status/702",
        )
    ]

    selected, promoted = _select_reply_draft_batch(
        ready,
        watching,
        capacity=3,
    )

    assert [result.id for result in selected] == [701, 702]
    assert promoted == 1


def test_reply_batch_waits_when_fewer_than_two_candidates_exist() -> None:
    only_candidate = XSearchResult(
        id=703,
        username="only",
        display_name="Only",
        text="Only candidate",
        created_at="",
        url="https://x.com/only/status/703",
    )

    selected, promoted = _select_reply_draft_batch(
        [],
        [only_candidate],
        capacity=3,
    )

    assert selected == []
    assert promoted == 0


def test_replytargets_searches_top_and_latest_root_posts_within_freshness_window() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC", generate_images=False))
    calls = []

    class XSearch:
        async def search_recent(self, query, **kwargs):
            calls.append((query, kwargs))
            return "news lang:en since_time:1", []

    bot.x_search = XSearch()
    asyncio.run(bot._search_reply_target_pool("news lang:ja", max_age_minutes=360))

    assert len(calls) == 2
    assert all(
        call[0] == "news lang:ja -is:reply -is:retweet"
        for call in calls
    )
    assert all(call[1]["since_minutes"] == 360 for call in calls)
    assert {call[1]["product"] for call in calls} == {"Top", "Latest"}
    assert all(call[1]["limit"] == 8 for call in calls)


def test_replytargets_merges_top_and_latest_and_keeps_fresher_metrics() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC", generate_images=False))
    duplicate_top = XSearchResult(
        id=50,
        username="same",
        display_name="Same",
        text="Same post",
        created_at="",
        url="https://x.com/same/status/50",
        view_count=1_000,
        like_count=10,
    )
    duplicate_latest = XSearchResult(
        id=50,
        username="same",
        display_name="Same",
        text="Same post",
        created_at="",
        url="https://x.com/same/status/50",
        view_count=1_500,
        like_count=20,
    )
    latest_only = XSearchResult(
        id=51,
        username="latest",
        display_name="Latest",
        text="Early candidate",
        created_at="",
        url="https://x.com/latest/status/51",
        view_count=700,
        like_count=8,
    )

    class XSearch:
        async def search_recent(self, query, **kwargs):
            assert "-is:reply -is:retweet" in query
            if kwargs["product"] == "Top":
                return f"{query} since_time:1", [duplicate_top]
            return f"{query} since_time:1", [duplicate_latest, latest_only]

    bot.x_search = XSearch()
    _query, results = asyncio.run(
        bot._search_reply_target_pool("AI lang:en", max_age_minutes=360)
    )

    assert [result.id for result in results] == [50, 51]
    assert results[0].view_count == 1_500
    assert results[0].like_count == 20


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
    message = _no_reply_targets_message(
        "auto hot topics",
        auto=True,
        diagnostic="Fetched 3 unique root posts; 2 search lanes failed.",
    )

    assert "last 360 minutes" in message
    assert "Scan diagnostics: Fetched 3 unique root posts" in message
    assert "without accepting older posts" in message
    assert "/replytargets crypto" in message


def test_replytargets_connection_error_is_not_reported_as_extension_bridge() -> None:
    error = RuntimeError(
        "All 6 reply-target search lanes failed. First error: "
        "RuntimeError: X search failed: All connection attempts failed"
    )

    message = _friendly_error(error)

    assert "Could not search X" in message
    assert "No Gemini or Chrome bridge job was started" in message
    assert "Could not connect to the local Chrome extension bridge" not in message


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


def test_friendly_error_preserves_bridge_timeout_diagnosis() -> None:
    message = _friendly_error(
        RuntimeError(
            "Extension bridge timed out waiting for Chrome after 600s. "
            "Chrome never claimed the queued job."
        )
    )

    assert "Chrome never claimed the queued job" in message
    assert "This usually means" not in message


def test_friendly_error_guides_extension_bridge_connection() -> None:
    message = _friendly_error(
        RuntimeError("Connection refused")
    )

    assert "local Chrome extension bridge" in message
    assert "Bridge URL/token" in message


def test_friendly_error_distinguishes_gemini_upload_dom_from_bridge_endpoint() -> None:
    message = _friendly_error(
        RuntimeError(
            "Gemini image file input was not found. "
            "url=https://gemini.google.com/app; fileInputs=0"
        )
    )

    assert "attachment control was not detected" in message
    assert "version 0.8.1" in message
    assert "bridge endpoint itself is working" in message
    assert "endpoint was not found" not in message


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
