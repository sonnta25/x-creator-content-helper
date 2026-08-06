import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from telegram import ForceReply, ReplyKeyboardMarkup

from src.automation import AutomationApproval
from src.bot import (
    BOT_COMMANDS,
    ContentBot,
    GLOBAL_REPLY_DELIVERY_QUEUE_ID,
    MENU_ACTIONS,
    MENU_LAYOUTS,
    MENU_INBOX,
    MENU_EXPERIMENTS,
    MENU_EXPERIMENTS_OFF,
    MENU_EXPERIMENTS_ON,
    MENU_EXPERIMENTS_SHOW,
    MENU_GOAL_EARN,
    MENU_GOAL_NETWORK,
    MENU_GOAL_QUALIFY,
    MENU_GOAL_SHOW,
    MENU_FOLLOW_SCHEDULE,
    MENU_FOLLOW_TARGETS,
    MENU_PACE,
    MENU_PACE_ADAPTIVE,
    MENU_PACE_CONSERVATIVE,
    MENU_PACE_HIGH,
    MENU_PACE_PAUSE,
    MENU_PACE_RESUME,
    MENU_PACE_SHOW,
    MENU_REPLY_GOAL,
    MENU_RISK,
    MENU_RISK_BALANCED,
    MENU_RISK_OPEN,
    MENU_RISK_SHOW,
    MENU_RISK_STRICT,
    MENU_REPLY_BATCH,
    MENU_REPLY_TARGETS,
    MENU_REPLY_VIDEO,
    MENU_SESSION,
    MENU_SETTINGS,
    MENU_VIDEO_SCHEDULE,
    _command_payload,
    _counts_toward_health_circuit_breaker,
    _dedupe_queries,
    _exception_detail,
    _extract_media_url,
    _format_file_size,
    _friendly_error,
    _format_reply_target_link,
    _format_reply_target_reply,
    _format_x_account_error_notification,
    _follow_target_digest,
    _follow_target_keyboard,
    _no_reply_targets_message,
    _approval_message_text,
    _approval_keyboard,
    _mobile_approval_note,
    _mobile_x_intent_url,
    _mobile_x_open_url,
    _parse_importcookie_args,
    _parse_persona_args,
    _reply_target_max_age_minutes,
    _reply_approvals_created_since,
    _reply_approvals_created_today,
    _author_approvals_created_today,
    _reply_video_search_queries,
    _video_context_quality,
    _is_reliable_video_context_text,
    _is_semantic_duplicate,
    _candidate_age_bucket,
    _creator_daypart,
    _daypart_language_fit,
    _distribution_stage,
    _reply_author_tier,
    _select_growth_portfolio,
    _select_reply_draft_batch,
    _select_reply_video_mix,
    _select_session_mix,
    _updated_reply_target_languages,
    _x_account_error_notifications,
)
from src.config import Settings
from src.media_download_service import DownloadedMedia
from src.models import (
    FollowCandidate,
    ImageAttachment,
    ReplyRevision,
    ReplyTargetDraft,
    XSearchResult,
    XTrend,
)


def test_follow_target_card_is_only_a_compact_profile_list() -> None:
    candidate = FollowCandidate(
        user_id=1,
        username="vietcreator",
        display_name="Viet Creator",
        description="",
        location="Vietnam",
        followers=1_000,
        following=900,
        statuses=100,
        profile_url="https://x.com/vietcreator",
        source_post_url="https://x.com/vietcreator/status/1",
        source_post_text="Recent post",
        source_post_created_at="2026-08-06T00:00:00+00:00",
        ratio=0.9,
        score=90,
        reasons=("Premium", "active in Vietnamese"),
    )

    text = _follow_target_digest([candidate])
    keyboard = _follow_target_keyboard([candidate])

    assert text == "Follow candidates\n1. Viet Creator (@vietcreator)"
    assert "Why:" not in text
    assert "followers" not in text
    assert "recent" not in text.lower()
    assert len(keyboard.inline_keyboard) == 1
    assert len(keyboard.inline_keyboard[0]) == 1
    assert keyboard.inline_keyboard[0][0].url == "https://x.com/vietcreator"


def test_parse_importcookie_args_default_account() -> None:
    account, cookie = _parse_importcookie_args(
        "auth_token=abc; ct0=def",
        "telegram_bot",
    )

    assert account == "telegram_bot"
    assert cookie == "auth_token=abc; ct0=def"


def test_content_rejection_does_not_pause_infrastructure_health_circuit() -> None:
    assert not _counts_toward_health_circuit_breaker(
        RuntimeError("AI returned no usable reply targets after one repair")
    )
    assert _counts_toward_health_circuit_breaker(
        RuntimeError("Extension bridge timed out waiting for Chrome")
    )


def test_growth_stage_classification_and_daypart_windows() -> None:
    assert _reply_author_tier(7_999) == "emerging_under_8k"
    assert _reply_author_tier(8_000) == "mid_8k_50k"
    assert _reply_author_tier(50_000) == "large_50k_300k"
    assert _reply_author_tier(300_000) == "mega_300k_plus"
    assert _distribution_stage(5_000) == "sweet_5k_50k"
    assert _distribution_stage(1_000_000) == "mega_1m_plus"

    daypart, _label = _creator_daypart(
        "Asia/Ho_Chi_Minh",
        now=datetime(2026, 8, 6, 1, 0, tzinfo=UTC),
    )
    assert daypart == "asia_morning"
    assert _daypart_language_fit("ja", daypart) == 100
    assert _daypart_language_fit("en", daypart) == 20


def test_growth_portfolio_reserves_mid_large_and_breakout_lanes() -> None:
    follower_counts = [600_000, 700_000, 20_000, 100_000, 30_000, 200_000]
    results = [
        XSearchResult(
            id=index + 1,
            username=f"author{index}",
            display_name="Author",
            text="Specific post",
            created_at="",
            url=f"https://x.com/author{index}/status/{index + 1}",
            author_followers_count=followers,
            reply_opportunity_score=100 - index,
        )
        for index, followers in enumerate(follower_counts)
    ]

    selected = _select_growth_portfolio(results, max_items=5)
    tiers = [_reply_author_tier(item.author_followers_count) for item in selected]

    assert tiers.count("mid_8k_50k") == 2
    assert tiers.count("large_50k_300k") == 2
    assert tiers.count("mega_300k_plus") == 1


def test_growth_stage_scoring_prefers_mid_tier_when_post_quality_is_equal() -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            creator_goal="qualify",
        )
    )
    common = {
        "display_name": "Author",
        "text": "A specific AI workflow improvement",
        "created_at": "",
        "language": "en",
        "reply_opportunity_score": 70.0,
        "viral_score": 70.0,
        "thread_availability_score": 70.0,
    }
    mega = XSearchResult(
        id=1,
        username="mega",
        url="https://x.com/mega/status/1",
        author_followers_count=500_000,
        **common,
    )
    mid = XSearchResult(
        id=2,
        username="mid",
        url="https://x.com/mid/status/2",
        author_followers_count=20_000,
        **common,
    )

    ranked = bot._apply_reply_target_mode([mega, mid], "balanced")

    assert ranked[0].username == "mid"
    assert ranked[0].author_tier == "mid_8k_50k"
    assert ranked[0].reply_opportunity_score > ranked[1].reply_opportunity_score


def test_candidate_age_bucket_keeps_early_and_late_breakouts_distinct() -> None:
    now = datetime.now(UTC)
    early = XSearchResult(
        id=1,
        username="early",
        display_name="Early",
        text="Early breakout",
        created_at=now.isoformat(),
        url="https://x.com/early/status/1",
        created_at_timestamp=int((now - timedelta(minutes=20)).timestamp()),
    )
    late = XSearchResult(
        id=2,
        username="late",
        display_name="Late",
        text="Late breakout",
        created_at=now.isoformat(),
        url="https://x.com/late/status/2",
        created_at_timestamp=int((now - timedelta(hours=3)).timestamp()),
    )

    assert _candidate_age_bucket(early) == "10_30m"
    assert _candidate_age_bucket(late) == "2h_plus"


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


def test_removed_commands_are_not_registered_in_telegram_menu() -> None:
    commands = {command.command for command in BOT_COMMANDS}

    assert {
        "vntweet", "angles", "xsearch", "tweet", "tweetx", "xtweet",
        "tweettrend3", "dailybrief", "retweet", "today", "automationhere",
    }.isdisjoint(commands)
    assert {
        "start", "menu", "help", "download", "replytargets", "replyvideo",
        "followtargets", "reply", "replyevery", "videoevery", "followevery", "replybatch", "replylangs",
        "replylearn", "replyreport", "setupcheck", "cancel", "session",
        "inbox", "replygoal", "replycap",
        "watchauthor", "money", "risk", "pace", "experiments",
        "profileaudit", "wins",
    }.issubset(commands)


def test_grouped_menu_keeps_replyvideo_and_automation_controls() -> None:
    assert MENU_SESSION in MENU_LAYOUTS["main"][0]
    assert MENU_INBOX in MENU_LAYOUTS["main"][1]
    assert MENU_SETTINGS in MENU_LAYOUTS["main"][2]
    assert MENU_REPLY_TARGETS in MENU_LAYOUTS["reply"][0]
    assert MENU_REPLY_VIDEO in MENU_LAYOUTS["reply"][0]
    assert MENU_VIDEO_SCHEDULE in MENU_LAYOUTS["automation"][0]
    assert any(MENU_FOLLOW_SCHEDULE in row for row in MENU_LAYOUTS["automation"])
    assert any(MENU_REPLY_BATCH in row for row in MENU_LAYOUTS["automation"])
    assert any(MENU_FOLLOW_TARGETS in row for row in MENU_LAYOUTS["creator"])
    assert MENU_ACTIONS[MENU_REPLY_VIDEO] == ("command", "replyvideo")
    assert MENU_ACTIONS[MENU_VIDEO_SCHEDULE] == ("command", "videoevery")
    assert MENU_ACTIONS[MENU_FOLLOW_TARGETS] == ("command", "followtargets")
    assert MENU_ACTIONS[MENU_FOLLOW_SCHEDULE] == ("command", "followevery")


def test_reply_safety_menu_exposes_every_mode_as_a_button() -> None:
    buttons = {
        button
        for row in MENU_LAYOUTS["risk"]
        for button in row
    }

    assert {MENU_RISK_SHOW, MENU_RISK_STRICT, MENU_RISK_BALANCED, MENU_RISK_OPEN} <= buttons
    assert MENU_ACTIONS[MENU_RISK] == ("menu", "risk")
    assert MENU_ACTIONS[MENU_RISK_SHOW] == ("command_args", "risk show")
    assert MENU_ACTIONS[MENU_RISK_STRICT] == ("command_args", "risk strict")
    assert MENU_ACTIONS[MENU_RISK_BALANCED] == ("command_args", "risk balanced")
    assert MENU_ACTIONS[MENU_RISK_OPEN] == ("command_args", "risk open")


def test_all_finite_setting_modes_are_button_selectable() -> None:
    assert MENU_ACTIONS[MENU_REPLY_GOAL] == ("menu", "goal")
    assert MENU_ACTIONS[MENU_GOAL_SHOW] == ("command_args", "replygoal show")
    assert MENU_ACTIONS[MENU_GOAL_QUALIFY] == ("command_args", "replygoal qualify")
    assert MENU_ACTIONS[MENU_GOAL_EARN] == ("command_args", "replygoal earn")
    assert MENU_ACTIONS[MENU_GOAL_NETWORK] == ("command_args", "replygoal network")

    assert MENU_ACTIONS[MENU_PACE] == ("menu", "pace")
    assert MENU_ACTIONS[MENU_PACE_SHOW] == ("command_args", "pace show")
    assert MENU_ACTIONS[MENU_PACE_CONSERVATIVE] == ("command_args", "pace conservative")
    assert MENU_ACTIONS[MENU_PACE_ADAPTIVE] == ("command_args", "pace adaptive")
    assert MENU_ACTIONS[MENU_PACE_HIGH] == ("command_args", "pace high")
    assert MENU_ACTIONS[MENU_PACE_PAUSE] == ("command_args", "pace pause")
    assert MENU_ACTIONS[MENU_PACE_RESUME] == ("command_args", "pace resume")

    assert MENU_ACTIONS[MENU_EXPERIMENTS] == ("menu", "experiments")
    assert MENU_ACTIONS[MENU_EXPERIMENTS_SHOW] == (
        "command_args",
        "experiments status",
    )
    assert MENU_ACTIONS[MENU_EXPERIMENTS_ON] == ("command_args", "experiments on")
    assert MENU_ACTIONS[MENU_EXPERIMENTS_OFF] == ("command_args", "experiments off")


def test_mode_button_passes_its_argument_without_force_reply() -> None:
    captured = {}

    class Message:
        text = MENU_RISK_BALANCED

        async def reply_text(self, _text, **_kwargs):
            return None

    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))

    async def fake_risk(_update, context):
        captured["args"] = context.args

    bot.risk = fake_risk
    update = SimpleNamespace(
        effective_message=Message(),
        effective_chat=SimpleNamespace(id=1),
        effective_user=SimpleNamespace(id=2),
    )
    context = SimpleNamespace(args=[])

    asyncio.run(bot.menu_action(update, context))

    assert captured["args"] == ["balanced"]


def test_risk_without_arguments_opens_choice_keyboard(tmp_path) -> None:
    captured = {}

    class Message:
        async def reply_text(self, text, **kwargs):
            captured["text"] = text
            captured["markup"] = kwargs.get("reply_markup")

    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            revenue_ops_path=str(tmp_path / "revenue.json"),
        )
    )
    update = SimpleNamespace(effective_message=Message())

    asyncio.run(bot.risk(update, SimpleNamespace(args=[])))

    assert "Choose a reply-safety mode" in captured["text"]
    assert isinstance(captured["markup"], ReplyKeyboardMarkup)


def test_reply_caps_count_only_cards_that_were_approved() -> None:
    now = datetime.now(UTC)
    cards = [
        AutomationApproval(
            id="pending",
            kind="reply",
            text="Pending",
            chat_id=1,
            approver_user_id=1,
            status="pending",
            created_at=now - timedelta(minutes=10),
            metadata={"root_author": "author"},
        ),
        AutomationApproval(
            id="approved",
            kind="reply",
            text="Approved",
            chat_id=1,
            approver_user_id=1,
            status="mobile_approved",
            created_at=now - timedelta(minutes=20),
            decided_at=now - timedelta(minutes=5),
            metadata={"root_author": "author"},
        ),
        AutomationApproval(
            id="approved-not-found",
            kind="reply",
            text="Approved but not confirmed",
            chat_id=1,
            approver_user_id=1,
            status="not_found",
            created_at=now - timedelta(minutes=25),
            decided_at=now - timedelta(minutes=4),
            metadata={"root_author": "author"},
        ),
        AutomationApproval(
            id="rejected",
            kind="reply",
            text="Rejected",
            chat_id=1,
            approver_user_id=1,
            status="rejected",
            created_at=now - timedelta(minutes=15),
            decided_at=now - timedelta(minutes=3),
            metadata={"root_author": "author"},
        ),
    ]

    assert _reply_approvals_created_today(cards) == 2
    assert _reply_approvals_created_since(
        cards,
        since=now - timedelta(hours=1),
    ) == 2
    assert _author_approvals_created_today(
        cards,
        username="@author",
        timezone_name="Asia/Ho_Chi_Minh",
    ) == 2


def test_reply_cap_uses_approval_time_instead_of_draft_creation_time() -> None:
    now = datetime.now(UTC)
    old_draft_approved_now = AutomationApproval(
        id="old-draft",
        kind="reply",
        text="Approved today",
        chat_id=1,
        approver_user_id=1,
        status="mobile_approved",
        created_at=now - timedelta(days=2),
        decided_at=now - timedelta(minutes=2),
    )

    assert _reply_approvals_created_today([old_draft_approved_now]) == 1
    assert _reply_approvals_created_since(
        [old_draft_approved_now],
        since=now - timedelta(hours=1),
    ) == 1


def test_replycap_show_reports_approved_usage_and_pending_drafts(tmp_path) -> None:
    class Message:
        def __init__(self) -> None:
            self.texts: list[str] = []

        async def reply_text(self, text: str):
            self.texts.append(text)
            return self

    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            telegram_approval_chat_id=123,
            creator_daily_reply_cap=10,
            automation_approvals_path=str(tmp_path / "approvals.json"),
            reply_learning_path=str(tmp_path / "learning.json"),
            revenue_ops_path=str(tmp_path / "revenue.json"),
            reply_watch_path=str(tmp_path / "watch.json"),
        )
    )
    approved = bot.approvals.create(
        kind="reply",
        text="Approved",
        chat_id=123,
        approver_user_id=123,
    )
    bot.approvals.decide(
        approved.id,
        approve=True,
        chat_id=123,
        user_id=123,
        destination="mobile",
    )
    bot.approvals.create(
        kind="reply",
        text="Pending",
        chat_id=123,
        approver_user_id=123,
    )
    message = Message()
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=123, type="private"),
        effective_message=message,
    )

    asyncio.run(bot.replycap(update, SimpleNamespace(args=["show"])))

    assert "Approved today: 1/10" in message.texts[-1]
    assert "Remaining today: 9" in message.texts[-1]
    assert "Approved in the last 60 minutes: 1/8" in message.texts[-1]
    assert "Available now: 7" in message.texts[-1]
    assert "Pending drafts not counted: 1" in message.texts[-1]


def test_risk_mode_clamps_global_hourly_reply_capacity(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            creator_daily_reply_cap=500,
            automation_approvals_path=str(tmp_path / "approvals.json"),
            reply_learning_path=str(tmp_path / "learning.json"),
            revenue_ops_path=str(tmp_path / "revenue.json"),
            reply_watch_path=str(tmp_path / "watch.json"),
        )
    )

    assert bot._adaptive_hourly_ceiling() == 20
    bot.revenue_ops.set_risk_mode("strict")
    assert bot._adaptive_hourly_ceiling() == 12
    bot.revenue_ops.set_risk_mode("open")
    assert bot._adaptive_hourly_ceiling() == 42


def test_japanese_guardrail_counts_only_approved_cards(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            automation_approvals_path=str(tmp_path / "approvals.json"),
            reply_learning_path=str(tmp_path / "learning.json"),
            revenue_ops_path=str(tmp_path / "revenue.json"),
            reply_watch_path=str(tmp_path / "watch.json"),
        )
    )
    for index in range(6):
        approval = bot.approvals.create(
            kind="reply",
            text=f"Specific Japanese reply {index}",
            chat_id=1,
            approver_user_id=1,
            metadata={"language": "ja", "root_author": f"author{index}"},
        )
        bot.approvals.decide(
            approval.id,
            approve=True,
            chat_id=1,
            user_id=1,
            destination="mobile",
        )
    bot.approvals.create(
        kind="reply",
        text="Pending Japanese draft",
        chat_id=1,
        approver_user_id=1,
        metadata={"language": "ja", "root_author": "pending-author"},
    )

    assert bot._japanese_reply_slots_remaining() == 0


def test_strict_risk_spreads_japanese_approvals_to_two_per_hour(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            automation_approvals_path=str(tmp_path / "approvals.json"),
            reply_learning_path=str(tmp_path / "learning.json"),
            revenue_ops_path=str(tmp_path / "revenue.json"),
            reply_watch_path=str(tmp_path / "watch.json"),
        )
    )
    bot.revenue_ops.set_risk_mode("strict")

    for index in range(2):
        approval = bot.approvals.create(
            kind="reply",
            text=f"Strict Japanese reply {index}",
            chat_id=1,
            approver_user_id=1,
            metadata={"language": "ja", "root_author": f"strict-author{index}"},
        )
        bot.approvals.decide(
            approval.id,
            approve=True,
            chat_id=1,
            user_id=1,
            destination="mobile",
        )
        assert bot._japanese_reply_slots_remaining() == 1 - index


def test_pending_japanese_cards_reserve_generation_slots_without_counting_usage(
    tmp_path,
) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            automation_approvals_path=str(tmp_path / "approvals.json"),
            reply_learning_path=str(tmp_path / "learning.json"),
            revenue_ops_path=str(tmp_path / "revenue.json"),
            reply_watch_path=str(tmp_path / "watch.json"),
        )
    )
    bot.revenue_ops.set_risk_mode("strict")
    for index in range(2):
        bot.approvals.create(
            kind="reply",
            text=f"Reserved Japanese reply {index}",
            chat_id=1,
            approver_user_id=1,
            metadata={
                "language": "ja",
                "root_author": f"reserved-author-{index}",
                "reply_delivery_queue_id": "reserved-batch",
            },
        )

    assert bot._japanese_reply_slots_remaining() == 2
    assert bot._japanese_reply_slots_remaining(reserve_pending=True) == 0

    candidates = [
        XSearchResult(
            id=1,
            username="new-ja",
            display_name="Japanese",
            text="Japanese candidate",
            created_at="",
            url="https://x.com/new-ja/status/1",
            language="ja",
        ),
        XSearchResult(
            id=2,
            username="new-en",
            display_name="English",
            text="English candidate",
            created_at="",
            url="https://x.com/new-en/status/2",
            language="en",
        ),
    ]
    selected, _author_skips, language_skips = (
        bot._filter_reply_generation_candidates(candidates)
    )

    assert [item.language for item in selected] == ["en"]
    assert language_skips == 1


def test_delivery_expires_safety_blocked_card_before_telegram(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            automation_approvals_path=str(tmp_path / "approvals.json"),
            reply_learning_path=str(tmp_path / "learning.json"),
            revenue_ops_path=str(tmp_path / "revenue.json"),
            reply_watch_path=str(tmp_path / "watch.json"),
        )
    )
    bot.revenue_ops.set_risk_mode("strict")
    for index in range(2):
        approved = bot.approvals.create(
            kind="reply",
            text=f"Approved Japanese reply {index}",
            chat_id=1,
            approver_user_id=1,
            metadata={"language": "ja", "root_author": f"approved-{index}"},
        )
        bot.approvals.decide(
            approved.id,
            approve=True,
            chat_id=1,
            user_id=1,
            destination="mobile",
        )
    blocked = bot.approvals.create(
        kind="reply",
        text="Blocked Japanese reply",
        chat_id=1,
        approver_user_id=1,
        target_url="https://x.com/blocked/status/10",
        metadata={
            "language": "ja",
            "root_author": "blocked",
            "reply_delivery_queue_id": "mixed-batch",
            "reply_delivery_queue_index": 0,
            "reply_delivery_card_sent": False,
        },
    )
    allowed = bot.approvals.create(
        kind="reply",
        text="Allowed English reply",
        chat_id=1,
        approver_user_id=1,
        target_url="https://x.com/allowed/status/11",
        metadata={
            "language": "en",
            "root_author": "allowed",
            "reply_delivery_queue_id": "mixed-batch",
            "reply_delivery_queue_index": 1,
            "reply_delivery_card_sent": False,
        },
    )
    sent = []

    async def send(approval):
        sent.append(approval.id)
        return SimpleNamespace(message_id=99)

    bot._send_approval = send

    asyncio.run(bot._send_next_reply_delivery("mixed-batch", respect_pacing=False))

    assert bot.approvals.get(blocked.id).status == "expired"
    assert sent == [allowed.id]
    assert bot.approvals.get(allowed.id).metadata["reply_delivery_card_sent"] is True


def test_natural_spacing_delays_delivery_without_blocking_approval(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            automation_approvals_path=str(tmp_path / "approvals.json"),
            reply_learning_path=str(tmp_path / "learning.json"),
            revenue_ops_path=str(tmp_path / "revenue.json"),
            reply_watch_path=str(tmp_path / "watch.json"),
        )
    )
    first = bot.approvals.create(
        kind="reply",
        text="First approved reply",
        chat_id=1,
        approver_user_id=1,
        metadata={"language": "en", "root_author": "first"},
    )
    first = bot.approvals.decide(
        first.id,
        approve=True,
        chat_id=1,
        user_id=1,
        destination="mobile",
    )
    next_reply = bot.approvals.create(
        kind="reply",
        text="Next unrelated reply",
        chat_id=1,
        approver_user_id=1,
        metadata={"language": "en", "root_author": "second"},
    )
    followup = bot.approvals.create(
        kind="reply",
        text="Direct author follow-up",
        chat_id=1,
        approver_user_id=1,
        metadata={
            "language": "ja",
            "root_author": "third",
            "relationship_followup": True,
        },
    )
    now = (first.decided_at or datetime.now(UTC)) + timedelta(seconds=5)

    assert bot._reply_approval_safety_block(next_reply, now=now) == ""
    assert bot._reply_approval_delay_seconds(next_reply, now=now) > 0
    assert bot._reply_approval_safety_block(followup, now=now) == ""
    assert bot._reply_approval_delay_seconds(followup, now=now) == 0


def test_open_risk_mode_still_keeps_a_high_pace_delivery_gap(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            automation_approvals_path=str(tmp_path / "approvals.json"),
            revenue_ops_path=str(tmp_path / "revenue.json"),
        )
    )
    bot.revenue_ops.set_risk_mode("open")
    bot.revenue_ops.set_pace_mode("high")

    assert bot._reply_delivery_base_gap_seconds() == 30


def test_balanced_mode_skips_japanese_tragedy_targets(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            revenue_ops_path=str(tmp_path / "revenue.json"),
        )
    )
    earthquake = XSearchResult(
        id=99,
        username="alerts",
        display_name="Alerts",
        text="緊急地震速報 第4報",
        created_at=datetime.now(UTC).isoformat(),
        url="https://x.com/alerts/status/99",
        language="ja",
        viral_score=90,
        reply_opportunity_score=90,
    )

    assert bot._apply_reply_target_mode([earthquake], "balanced") == []
    bot.revenue_ops.set_risk_mode("open")
    assert bot._apply_reply_target_mode([earthquake], "balanced")


def test_menu_keyboard_hides_after_a_selection() -> None:
    from src.bot import _menu_keyboard

    keyboard = _menu_keyboard("main")
    assert isinstance(keyboard, ReplyKeyboardMarkup)
    assert keyboard.is_persistent is False
    assert keyboard.one_time_keyboard is True


def test_session_mix_prefers_video_but_keeps_text_lane() -> None:
    def candidate(tweet_id: int, *, video: bool, score: float) -> XSearchResult:
        return XSearchResult(
            id=tweet_id,
            username=f"user{tweet_id}",
            display_name="User",
            text="Candidate",
            created_at=datetime.now(UTC).isoformat(),
            url=f"https://x.com/user{tweet_id}/status/{tweet_id}",
            has_video=video,
            goal_score=score,
            rankability_score=score,
        )

    videos = [candidate(index, video=True, score=90 - index) for index in range(1, 4)]
    targets = [candidate(index, video=False, score=80 - index) for index in range(10, 13)]

    selected = _select_session_mix(targets, videos, max_items=5)

    assert len(selected) == 5
    assert sum(item.has_video for item in selected) == 3
    assert sum(not item.has_video for item in selected) == 2


def test_duplicate_guard_rejects_near_copypasta_only() -> None:
    existing = ["The real bottleneck here is distribution, not model quality."]

    assert _is_semantic_duplicate(
        "The real bottleneck here is distribution not model quality",
        existing,
    )
    assert not _is_semantic_duplicate(
        "Cheap inference changes which workflows can run continuously.",
        existing,
    )
    assert _is_semantic_duplicate(
        "The real bottleneck is distribution rather than model quality.",
        existing,
    )


def test_earn_goal_rewards_verified_audience_proxy() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC", creator_goal="earn"))
    common = dict(
        display_name="User",
        text="A useful AI launch",
        created_at=datetime.now(UTC).isoformat(),
        language="en",
        viral_score=70,
        reply_opportunity_score=70,
        thread_availability_score=80,
        reply_saturation_penalty=10,
    )
    verified = XSearchResult(
        id=1,
        username="verified",
        url="https://x.com/verified/status/1",
        author_verified=True,
        verified_replier_ratio=0.5,
        **common,
    )
    regular = XSearchResult(
        id=2,
        username="regular",
        url="https://x.com/regular/status/2",
        **common,
    )

    ranked = bot._apply_reply_target_mode([regular, verified], "balanced")

    assert ranked[0].username == "verified"
    assert ranked[0].premium_audience_score > ranked[1].premium_audience_score


def test_earn_goal_excludes_red_monetization_target(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            creator_goal="earn",
            revenue_ops_path=str(tmp_path / "revenue.json"),
        )
    )
    risky = XSearchResult(
        id=1,
        username="market",
        display_name="Market",
        text="New Polymarket betting odds",
        created_at=datetime.now(UTC).isoformat(),
        url="https://x.com/market/status/1",
        viral_score=90,
        reply_opportunity_score=90,
        thread_availability_score=90,
    )

    assert bot._apply_reply_target_mode([risky], "balanced") == []


def test_dynamic_session_mix_reserves_watched_relationship_slot() -> None:
    def candidate(tweet_id: int, *, video: bool, score: float, watched: bool = False):
        return XSearchResult(
            id=tweet_id,
            username=f"user{tweet_id}",
            display_name="User",
            text="Candidate",
            created_at=datetime.now(UTC).isoformat(),
            url=f"https://x.com/user{tweet_id}/status/{tweet_id}",
            has_video=video,
            goal_score=score,
            watched_author=watched,
        )

    videos = [candidate(i, video=True, score=90 - i) for i in range(1, 5)]
    targets = [candidate(10, video=False, score=80), candidate(11, video=False, score=20, watched=True)]

    selected = _select_session_mix(targets, videos, max_items=5, video_share=0.75)

    assert any(item.watched_author for item in selected)


def test_session_queue_sends_one_unsent_card_at_a_time(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            automation_approvals_path=str(tmp_path / "approvals.json"),
        )
    )
    for index in range(2):
        bot.approvals.create(
            kind="reply",
            text=f"Reply {index}",
            chat_id=1,
            target_url=f"https://x.com/source/status/{index + 1}",
            metadata={
                "reply_session_id": "session-1",
                "reply_session_index": index,
                "session_card_sent": False,
            },
        )
    sent = []

    async def send(approval):
        sent.append(approval.text)

    bot._send_approval = send

    assert asyncio.run(bot._send_next_session_approval("session-1")) is True
    assert sent == ["Reply 0"]
    assert asyncio.run(bot._send_next_session_approval("session-1")) is True
    assert sent == ["Reply 0", "Reply 1"]
    assert asyncio.run(bot._send_next_session_approval("session-1")) is False


def test_reply_delivery_queue_schedules_next_card_instead_of_showing_wait_alert(
    tmp_path,
) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            automation_approvals_path=str(tmp_path / "approvals.json"),
        )
    )
    approval = bot.approvals.create(
        kind="reply",
        text="Queued reply",
        chat_id=1,
        target_url="https://x.com/source/status/2",
        metadata={
            "reply_delivery_queue_id": "batch-1",
            "reply_delivery_queue_index": 1,
            "reply_delivery_card_sent": False,
        },
    )
    scheduled = {}
    sent = []

    bot._reply_approval_delay_seconds = lambda _approval: 131
    bot._schedule_delayed_approval_queue = lambda queue_id, **kwargs: scheduled.update(
        {"queue_id": queue_id, **kwargs}
    )

    async def send(item):
        sent.append(item.id)

    bot._send_approval = send

    assert asyncio.run(
        bot._send_next_reply_delivery("batch-1", respect_pacing=True)
    ) is True
    refreshed = bot.approvals.get(approval.id)

    assert sent == []
    assert scheduled["queue_id"] == GLOBAL_REPLY_DELIVERY_QUEUE_ID
    assert scheduled["delay_seconds"] == 131
    assert refreshed is not None
    assert refreshed.metadata["reply_delivery_card_sent"] is False
    assert refreshed.metadata["reply_delivery_not_before"]


def test_rejected_card_releases_next_delivery_immediately(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            automation_approvals_path=str(tmp_path / "approvals.json"),
        )
    )
    approval = bot.approvals.create(
        kind="reply",
        text="Queued reply",
        chat_id=1,
        target_url="https://x.com/source/status/3",
        metadata={
            "reply_delivery_queue_id": "batch-2",
            "reply_delivery_queue_index": 1,
            "reply_delivery_card_sent": False,
        },
    )
    sent = []

    async def send(item):
        sent.append(item.id)

    bot._send_approval = send

    assert asyncio.run(
        bot._send_next_reply_delivery("batch-2", respect_pacing=False)
    ) is True
    refreshed = bot.approvals.get(approval.id)

    assert sent == [approval.id]
    assert refreshed is not None
    assert refreshed.metadata["reply_delivery_card_sent"] is True


def test_reply_delivery_is_global_across_overlapping_batches(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            automation_approvals_path=str(tmp_path / "approvals.json"),
        )
    )
    visible = bot.approvals.create(
        kind="reply",
        text="Visible replyvideo card",
        chat_id=1,
        approver_user_id=1,
        target_url="https://x.com/video/status/10",
        metadata={
            "reply_delivery_queue_id": "video-batch",
            "reply_delivery_queue_index": 0,
            "reply_delivery_card_sent": True,
            "reply_delivery_message_id": 10,
        },
    )
    queued = bot.approvals.create(
        kind="reply",
        text="Queued replytargets card",
        chat_id=1,
        approver_user_id=1,
        target_url="https://x.com/post/status/11",
        metadata={
            "reply_delivery_queue_id": "target-batch",
            "reply_delivery_queue_index": 0,
            "reply_delivery_card_sent": False,
        },
    )
    sent = []

    async def send(item):
        sent.append(item.id)

    bot._send_approval = send

    assert asyncio.run(
        bot._send_next_reply_delivery("target-batch", respect_pacing=False)
    ) is True
    assert sent == []

    bot.approvals.decide(
        visible.id,
        approve=False,
        chat_id=1,
        user_id=1,
        destination="mobile",
    )
    assert asyncio.run(
        bot._send_next_reply_delivery("video-batch", respect_pacing=False)
    ) is True
    assert sent == [queued.id]


def test_concurrent_reply_delivery_calls_send_one_card_only(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            automation_approvals_path=str(tmp_path / "approvals.json"),
        )
    )
    approval = bot.approvals.create(
        kind="reply",
        text="Only one Telegram card",
        chat_id=1,
        approver_user_id=1,
        target_url="https://x.com/source/status/12",
        metadata={
            "reply_delivery_queue_id": "race-batch",
            "reply_delivery_queue_index": 0,
            "reply_delivery_card_sent": False,
        },
    )
    bot._reply_approval_delay_seconds = lambda _approval: 0
    sent = []

    async def send(item):
        sent.append(item.id)
        await asyncio.sleep(0.02)
        return SimpleNamespace(message_id=99)

    bot._send_approval = send

    async def deliver_twice():
        await asyncio.gather(
            bot._send_next_reply_delivery("race-batch", respect_pacing=True),
            bot._send_next_reply_delivery("race-batch", respect_pacing=True),
        )

    asyncio.run(deliver_twice())

    assert sent == [approval.id]


def test_reply_target_and_video_discovery_share_one_scan_slot(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            automation_approvals_path=str(tmp_path / "approvals.json"),
        )
    )
    active = 0
    maximum_active = 0

    async def run_scan(label):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return label, [], ""

    async def target_scan(*_args, **_kwargs):
        return await run_scan("targets")

    async def video_scan(*_args, **_kwargs):
        return await run_scan("videos")

    bot._get_reply_target_context_locked = target_scan
    bot._get_reply_video_context_locked = video_scan

    async def discover_both():
        return await asyncio.gather(
            bot._get_reply_target_context("", SimpleNamespace()),
            bot._get_reply_video_context("", SimpleNamespace()),
        )

    results = asyncio.run(discover_both())

    assert maximum_active == 1
    assert [item[0] for item in results] == ["targets", "videos"]


def test_stale_queued_card_is_expired_before_telegram_delivery(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            automation_approvals_path=str(tmp_path / "approvals.json"),
            reply_watch_path=str(tmp_path / "watch.json"),
        )
    )
    approval = bot.approvals.create(
        kind="reply",
        text="Stale queued card",
        chat_id=1,
        approver_user_id=1,
        target_url="https://x.com/source/status/13",
        metadata={
            "reply_delivery_queue_id": "stale-batch",
            "reply_delivery_queue_index": 0,
            "reply_delivery_card_sent": False,
        },
    )
    approval.created_at = datetime.now(UTC) - timedelta(minutes=3)
    sent = []

    async def closed(_approval):
        return False, "The discussion became too crowded."

    async def send(item):
        sent.append(item.id)

    bot._queued_reply_delivery_status = closed
    bot._send_approval = send

    asyncio.run(
        bot._send_next_reply_delivery("stale-batch", respect_pacing=False)
    )

    assert sent == []
    assert bot.approvals.get(approval.id).status == "expired"


def test_unverified_stale_card_is_retried_without_telegram_delivery(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            automation_approvals_path=str(tmp_path / "approvals.json"),
        )
    )
    approval = bot.approvals.create(
        kind="reply",
        text="Temporarily unverifiable card",
        chat_id=1,
        approver_user_id=1,
        target_url="https://x.com/source/status/14",
        metadata={
            "reply_delivery_queue_id": "retry-batch",
            "reply_delivery_queue_index": 0,
            "reply_delivery_card_sent": False,
        },
    )
    approval.created_at = datetime.now(UTC) - timedelta(minutes=3)
    sent = []
    retries = []

    async def unknown(_approval):
        return None, "X lookup timed out"

    async def send(item):
        sent.append(item.id)

    def schedule(*args, **kwargs):
        retries.append((args, kwargs))

    bot._queued_reply_delivery_status = unknown
    bot._send_approval = send
    bot._schedule_delayed_approval_queue = schedule

    asyncio.run(
        bot._send_next_reply_delivery("retry-batch", respect_pacing=False)
    )

    stored = bot.approvals.get(approval.id)
    assert sent == []
    assert stored.status == "pending"
    assert stored.metadata["reply_delivery_last_error"] == "X lookup timed out"
    assert len(retries) == 1


def test_full_pending_queue_skips_gemini_generation(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            automation_approvals_path=str(tmp_path / "approvals.json"),
            reply_learning_path=str(tmp_path / "learning.json"),
            revenue_ops_path=str(tmp_path / "revenue.json"),
            reply_watch_path=str(tmp_path / "watch.json"),
            reply_pending_queue_cap=2,
        )
    )
    for index in range(2):
        bot.approvals.create(
            kind="reply",
            text=f"Pending {index}",
            chat_id=1,
            approver_user_id=1,
            target_url=f"https://x.com/pending/status/{20 + index}",
            metadata={
                "reply_delivery_queue_id": "existing-batch",
                "reply_delivery_queue_index": index,
                "reply_delivery_card_sent": index == 0,
            },
        )

    class FailAI:
        async def generate_reply_targets(self, *_args, **_kwargs):
            raise AssertionError("Gemini must not run while the queue is full")

    bot.ai = FailAI()
    candidates = [
        XSearchResult(
            id=30 + index,
            username=f"candidate{index}",
            display_name="Candidate",
            text="Fresh candidate",
            created_at="",
            url=f"https://x.com/candidate/status/{30 + index}",
        )
        for index in range(2)
    ]

    result = asyncio.run(
        bot._create_reply_approvals(
            candidates,
            query="test",
            chat_id=1,
            approver_user_id=1,
        )
    )

    assert result.created == 0
    assert "global reply-card queue" in result.diagnostic()


def test_replyvideo_falls_back_to_grounded_text_when_gemini_upload_control_fails(
    tmp_path,
) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            automation_approvals_path=str(tmp_path / "approvals.json"),
            reply_learning_path=str(tmp_path / "learning.json"),
            revenue_ops_path=str(tmp_path / "revenue.json"),
            reply_watch_path=str(tmp_path / "watch.json"),
        )
    )
    calls = []

    class UploadFailThenGroundedAI:
        async def generate_reply_targets(self, _query, context, **kwargs):
            calls.append((context, kwargs.get("visual_attachments", [])))
            if len(calls) == 1:
                raise RuntimeError(
                    "Gemini image file input was not found. url=https://gemini.google.com/app"
                )
            assert "visual/status/1" not in context
            return [
                ReplyTargetDraft(
                    url="https://x.com/caption/status/2",
                    target="@caption - grounded video",
                    reply="The clean framing makes the result immediately readable.",
                ),
                ReplyTargetDraft(
                    url="https://x.com/media/status/3",
                    target="@media - described video",
                    reply="That contrast is the detail that gives the clip its payoff.",
                ),
            ]

    async def opportunity_open(_result, *, video_mode):
        assert video_mode is True
        return True, ""

    async def release(_queue_id, *, respect_pacing):
        assert respect_pacing is True
        return True

    bot.ai = UploadFailThenGroundedAI()
    bot._opportunity_status = opportunity_open
    bot._send_next_reply_delivery = release
    candidates = [
        XSearchResult(
            id=1,
            username="visual",
            display_name="Visual",
            text="",
            created_at="",
            url="https://x.com/visual/status/1",
            language="en",
            has_video=True,
            video_context_quality="visual_frames",
            visual_frame_names=["frame-01.jpg", "frame-02.jpg"],
        ),
        XSearchResult(
            id=2,
            username="caption",
            display_name="Caption",
            text="A runner catches the falling baton",
            created_at="",
            url="https://x.com/caption/status/2",
            language="en",
            has_video=True,
            video_context_quality="caption_only",
        ),
        XSearchResult(
            id=3,
            username="media",
            display_name="Media",
            text="A before-and-after demonstration",
            created_at="",
            url="https://x.com/media/status/3",
            language="en",
            has_video=True,
            video_context_quality="grounded_text",
        ),
    ]

    result = asyncio.run(
        bot._create_reply_approvals(
            candidates,
            query="viral video",
            chat_id=1,
            approver_user_id=1,
            video_mode=True,
            visual_attachments=[
                ImageAttachment("frame-01.jpg", "image/jpeg", b"frame")
            ],
        )
    )

    assert result.created == 2
    assert len(calls) == 2
    assert len(calls[0][1]) == 1
    assert calls[1][1] == []
    assert {item.target_url for item in bot.approvals.items()} == {
        "https://x.com/caption/status/2",
        "https://x.com/media/status/3",
    }


def test_tracking_cycle_budget_prioritizes_recent_responses_and_checkpoints() -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            reply_tracking_checks_per_cycle=2,
        )
    )
    now = datetime.now(UTC)
    records = [
        {
            "approval_id": "old-author",
            "posted_at": (now - timedelta(hours=8)).isoformat(),
            "author_due": True,
            "checkpoint_due": False,
        },
        {
            "approval_id": "checkpoint",
            "posted_at": (now - timedelta(hours=2)).isoformat(),
            "author_due": False,
            "checkpoint_due": True,
        },
        {
            "approval_id": "recent-author",
            "posted_at": (now - timedelta(minutes=20)).isoformat(),
            "author_due": True,
            "checkpoint_due": False,
        },
    ]
    bot.reply_learning.due_checkpoint = (
        lambda record, *, now: 60 if record["checkpoint_due"] else None
    )
    bot.reply_learning.author_response_check_due = (
        lambda record, *, now: bool(record["author_due"])
    )

    selected = bot._tracking_records_for_cycle(records, now=now)

    assert [record["approval_id"] for record in selected] == [
        "recent-author",
        "checkpoint",
    ]


def test_failed_telegram_delivery_stays_unsent_and_retries(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            automation_approvals_path=str(tmp_path / "approvals.json"),
        )
    )
    approval = bot.approvals.create(
        kind="reply",
        text="Retry this card",
        chat_id=1,
        target_url="https://x.com/source/status/4",
        metadata={
            "reply_delivery_queue_id": "batch-retry",
            "reply_delivery_queue_index": 1,
            "reply_delivery_card_sent": False,
        },
    )
    scheduled = {}
    bot._reply_approval_delay_seconds = lambda _approval: 0
    bot._schedule_delayed_approval_queue = lambda queue_id, **kwargs: scheduled.update(
        {"queue_id": queue_id, **kwargs}
    )

    async def fail_send(_approval):
        raise RuntimeError("temporary Telegram timeout")

    bot._send_approval = fail_send

    assert asyncio.run(
        bot._send_next_reply_delivery("batch-retry", respect_pacing=True)
    ) is True
    refreshed = bot.approvals.get(approval.id)

    assert refreshed is not None
    assert refreshed.metadata["reply_delivery_card_sent"] is False
    assert refreshed.metadata["reply_delivery_attempts"] == 1
    assert "Telegram timeout" in refreshed.metadata["reply_delivery_last_error"]
    assert scheduled["queue_id"] == GLOBAL_REPLY_DELIVERY_QUEUE_ID
    assert scheduled["delay_seconds"] == 15


def test_restore_recovers_uncertain_delayed_card_from_previous_build(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            automation_approvals_path=str(tmp_path / "approvals.json"),
        )
    )
    approval = bot.approvals.create(
        kind="reply",
        text="Previously stuck card",
        chat_id=1,
        target_url="https://x.com/source/status/5",
        metadata={
            "reply_delivery_queue_id": "batch-old",
            "reply_delivery_queue_index": 1,
            "reply_delivery_card_sent": True,
        },
    )
    sent = []

    async def send(item):
        sent.append(item.id)
        return SimpleNamespace(message_id=99)

    bot._send_approval = send

    asyncio.run(bot._restore_reply_delivery_queues())
    refreshed = bot.approvals.get(approval.id)

    assert sent == [approval.id]
    assert refreshed is not None
    assert refreshed.metadata["reply_delivery_card_sent"] is True
    assert refreshed.metadata["reply_delivery_message_id"] == 99
    assert refreshed.metadata["reply_delivery_receipt_recovered"] is True


def test_final_opportunity_check_rejects_a_suddenly_saturated_thread() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
    timestamp = int(datetime.now(UTC).timestamp())
    original = XSearchResult(
        id=44,
        username="source",
        display_name="Source",
        text="Fast post",
        created_at=datetime.now(UTC).isoformat(),
        created_at_timestamp=timestamp,
        url="https://x.com/source/status/44",
        reply_count=5,
        view_count=10_000,
    )
    saturated = XSearchResult(
        **{
            **original.__dict__,
            "reply_count": 120,
            "view_count": 12_000,
        }
    )

    class XSearch:
        async def tweet_by_id(self, _tweet_id):
            return saturated

    bot.x_search = XSearch()

    assert asyncio.run(bot._opportunity_still_open(original, video_mode=False)) is False


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
            assert text == "Downloading media from the post..."
            return self.status

        async def reply_document(self, document, **kwargs):
            self.document_bytes = document.read()
            assert kwargs["filename"] == media_path.name
            assert "Prepared video" in kwargs["caption"]
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


def test_download_command_sends_every_image_in_a_carousel(tmp_path) -> None:
    first = tmp_path / "creator-image-one.jpg"
    second = tmp_path / "creator-image-two.webp"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    downloaded = DownloadedMedia(
        path=first,
        additional_paths=(second,),
        title="Carousel",
        source_url="https://www.instagram.com/p/example/",
        extractor="gallery-dl",
    )

    class FakeDownloader:
        def download(self, _url):
            return downloaded

    class FakeStatus:
        async def edit_text(self, text):
            assert "2 images" in text

        async def delete(self):
            return None

    class FakeChat:
        async def send_action(self, _action):
            return None

    class FakeMessage:
        text = f"/download {downloaded.source_url}"
        caption = None
        chat = FakeChat()

        def __init__(self):
            self.files = []

        async def reply_text(self, text):
            assert text == "Downloading media from the post..."
            return FakeStatus()

        async def reply_document(self, document, **kwargs):
            self.files.append((kwargs["filename"], document.read()))
            assert "Prepared media file" in kwargs["caption"]

    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
    bot.media_downloader = FakeDownloader()
    message = FakeMessage()
    update = SimpleNamespace(effective_message=message)

    asyncio.run(bot.download(update, SimpleNamespace(args=[downloaded.source_url])))

    assert message.files == [(first.name, b"one"), (second.name, b"two")]
    assert not first.exists()
    assert not second.exists()


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
        "follow_targets_minutes": None,
        "follow_targets_updated_at": None,
        "automation_running": False,
        "creator_timezone": "Asia/Ho_Chi_Minh",
        "reply_target_languages": "en,ja",
        "extension_bridge_timeout_seconds": 360,
        "creator_goal": "qualify",
        "daily_reply_cap": 500,
        "author_daily_reply_cap": 5,
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


def test_replyvideo_search_lanes_are_serialized_for_one_cookie_pool() -> None:
    class FakeSearch:
        def __init__(self):
            self.active = 0
            self.max_active = 0
            self.calls = 0

        async def search_recent(self, query, **_kwargs):
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.002)
            self.active -= 1
            return query, []

    class Status:
        async def edit_text(self, _text):
            return None

    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
    search = FakeSearch()
    bot.x_search = search

    _label, results, _tier = asyncio.run(
        bot._get_reply_video_context("", Status())
    )

    assert results == []
    assert search.calls == 8
    assert search.max_active == 1


def test_replyvideo_removes_active_cards_before_each_ranking_pass(
    tmp_path,
    monkeypatch,
) -> None:
    now = int(datetime.now(UTC).timestamp())
    active = XSearchResult(
        id=1201,
        username="activevideo",
        display_name="Active Video",
        text="Already has a card",
        created_at="",
        url="https://x.com/activevideo/status/1201",
        language="ja",
        created_at_timestamp=now - 120,
        like_count=1_000,
        view_count=200_000,
        has_video=True,
    )
    unused = XSearchResult(
        id=1202,
        username="unusedvideo",
        display_name="Unused Video",
        text="Fresh unused video",
        created_at="",
        url="https://x.com/unusedvideo/status/1202",
        language="en",
        created_at_timestamp=now - 120,
        like_count=500,
        view_count=100_000,
        has_video=True,
    )

    class FakeSearch:
        async def search_recent(self, query, **_kwargs):
            return query, [active, unused]

    class Status:
        async def edit_text(self, _text):
            return None

    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            automation_approvals_path=str(tmp_path / "approvals.json"),
            reply_learning_path=str(tmp_path / "learning.json"),
            revenue_ops_path=str(tmp_path / "revenue.json"),
            reply_watch_path=str(tmp_path / "watch.json"),
            reply_target_metrics_path=str(tmp_path / "metrics.json"),
        )
    )
    bot.x_search = FakeSearch()
    bot.approvals.create(
        kind="reply",
        text="Existing active video draft",
        chat_id=123,
        approver_user_id=123,
        target_url=active.url,
    )
    ranked_inputs: list[list[int]] = []

    def fake_rank(results, **_kwargs):
        ranked_inputs.append([result.id for result in results])
        return list(results)

    async def enrich(results):
        return results

    monkeypatch.setattr("src.bot.rank_viral_video_posts", fake_rank)
    bot._enrich_reply_thread_context = enrich
    bot._apply_reply_target_mode = lambda results, _mode: results

    _label, results, note = asyncio.run(
        bot._get_reply_video_context("", Status())
    )

    assert ranked_inputs == [[1202], [1202], [1202]]
    assert [result.id for result in results] == [1202]
    assert "Skipped 1 already-used" in note


def test_replyvideo_mix_prefers_two_japanese_then_best_global_alternative() -> None:
    results = [
        XSearchResult(id=1, username="a", display_name="", text="a", created_at="", url="1", language="en"),
        XSearchResult(id=2, username="b", display_name="", text="b", created_at="", url="2", language="ja"),
        XSearchResult(id=3, username="c", display_name="", text="c", created_at="", url="3", language="ko"),
        XSearchResult(id=4, username="d", display_name="", text="d", created_at="", url="4", language="vi"),
        XSearchResult(id=5, username="e", display_name="", text="e", created_at="", url="5", language="ja"),
    ]

    selected = _select_reply_video_mix(results)

    assert [item.language for item in selected] == ["ja", "ja", "en"]


def test_replyvideo_mix_falls_back_cleanly_when_japanese_is_scarce() -> None:
    results = [
        XSearchResult(id=1, username="a", display_name="", text="a", created_at="", url="1", language="en"),
        XSearchResult(id=2, username="b", display_name="", text="b", created_at="", url="2", language="ja"),
        XSearchResult(id=3, username="c", display_name="", text="c", created_at="", url="3", language="vi"),
    ]

    selected = _select_reply_video_mix(results)

    assert [item.language for item in selected] == ["ja", "en", "vi"]


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
        reply="This is the reply.",
    )

    assert _format_reply_target_reply(draft) == "This is the reply."
    assert _format_reply_target_link(draft) == "https://x.com/user/status/123"


def test_mobile_reply_intent_prefills_text_and_target_tweet() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
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
    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
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


def test_reply_approval_cards_offer_lazy_quick_actions() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
    reply = bot.approvals.create(
        kind="reply",
        text="A specific reply",
        chat_id=123,
        target_url="https://x.com/source/status/1",
    )
    reply_callbacks = {
        button.callback_data
        for row in _approval_keyboard(reply).inline_keyboard
        for button in row
        if button.callback_data
    }
    assert f"automation:alternative:{reply.id}" in reply_callbacks
    assert f"automation:shorter:{reply.id}" in reply_callbacks
    assert f"automation:skip:{reply.id}" in reply_callbacks


def test_shorter_updates_reply_translation_on_the_same_card() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
    approval = bot.approvals.create(
        kind="reply",
        text="The original reply is longer than it needs to be.",
        chat_id=123,
        approver_user_id=456,
        target_url="https://x.com/source/status/1",
        metadata={
            "root_text": "A source post about rollout timing.",
            "source_summary_vi": "Bài viết nói về thời điểm triển khai.",
            "reply_translation_vi": "Bản dịch cũ.",
        },
    )

    class FakeAI:
        async def generate_reply_revision(self, *_args):
            return ReplyRevision(
                reply="Timing is the real tradeoff.",
                reply_translation_vi="Thời điểm mới là sự đánh đổi thực sự.",
            )

    edits = []

    class FakeQuery:
        data = f"automation:shorter:{approval.id}"
        from_user = SimpleNamespace(id=456)
        message = SimpleNamespace(chat=SimpleNamespace(id=123))

        async def answer(self, *_args, **_kwargs):
            return None

        async def edit_message_text(self, *args, **kwargs):
            edits.append((args, kwargs))

    bot.ai = FakeAI()
    asyncio.run(
        bot.automation_approval(
            SimpleNamespace(callback_query=FakeQuery()),
            SimpleNamespace(),
        )
    )

    updated = bot.approvals.get(approval.id)
    assert updated.text == "Timing is the real tradeoff."
    assert updated.metadata["reply_translation_vi"] == (
        "Thời điểm mới là sự đánh đổi thực sự."
    )
    assert "Bản dịch cũ" not in edits[0][0][0]
    assert "Thời điểm mới là sự đánh đổi thực sự" in edits[0][0][0]


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
            return "That makes the rollout decision much clearer."

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


def test_expired_card_is_closed_and_releases_queue_without_negative_feedback(
    tmp_path,
) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            automation_approvals_path=str(tmp_path / "approvals.json"),
            reply_learning_path=str(tmp_path / "learning.json"),
        )
    )
    approval = bot.approvals.create(
        kind="reply",
        text="A now-expired draft",
        chat_id=123,
        approver_user_id=456,
        target_url="https://x.com/source/status/42",
        metadata={
            "reply_strategy": "specific_observation",
            "root_author": "source",
            "reply_delivery_queue_id": "expired-batch",
            "reply_delivery_queue_index": 0,
            "reply_delivery_card_sent": True,
        },
    )
    approval.created_at = datetime.now(UTC) - timedelta(minutes=31)
    released = []
    feedback = []
    answers = []
    edits = []

    async def release(queue_id, *, respect_pacing):
        released.append((queue_id, respect_pacing))
        return True

    class FakeQuery:
        data = f"automation:mobile:{approval.id}"
        from_user = SimpleNamespace(id=456)
        message = SimpleNamespace(
            chat=SimpleNamespace(id=123),
            text="Expired approval card",
        )

        async def answer(self, *args, **kwargs):
            answers.append((args, kwargs))

        async def edit_message_text(self, *args, **kwargs):
            edits.append((args, kwargs))

    bot._send_next_reply_delivery = release
    bot.reply_learning.record_feedback = lambda *args, **kwargs: feedback.append(
        (args, kwargs)
    )

    asyncio.run(
        bot.automation_approval(
            SimpleNamespace(callback_query=FakeQuery()),
            SimpleNamespace(),
        )
    )

    assert bot.approvals.get(approval.id).status == "expired"
    assert "closed automatically" in answers[0][0][0]
    assert "No reply was posted" in edits[0][0][0]
    assert edits[0][1]["reply_markup"] is None
    assert released == [("expired-batch", False)]
    assert feedback == []


def test_safety_blocked_visible_card_auto_closes_and_releases_queue(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            automation_approvals_path=str(tmp_path / "approvals.json"),
            reply_learning_path=str(tmp_path / "learning.json"),
            revenue_ops_path=str(tmp_path / "revenue.json"),
            reply_watch_path=str(tmp_path / "watch.json"),
        )
    )
    bot.revenue_ops.set_risk_mode("strict")
    for index in range(2):
        approved = bot.approvals.create(
            kind="reply",
            text=f"Earlier Japanese approval {index}",
            chat_id=123,
            approver_user_id=456,
            metadata={"language": "ja", "root_author": f"earlier-{index}"},
        )
        bot.approvals.decide(
            approved.id,
            approve=True,
            chat_id=123,
            user_id=456,
            destination="mobile",
        )
    blocked = bot.approvals.create(
        kind="reply",
        text="Japanese card that became blocked",
        chat_id=123,
        approver_user_id=456,
        target_url="https://x.com/source/status/99",
        metadata={
            "language": "ja",
            "root_author": "source",
            "reply_delivery_queue_id": "safety-batch",
            "reply_delivery_card_sent": True,
        },
    )
    answers = []
    edits = []
    released = []

    async def release(queue_id, *, respect_pacing):
        released.append((queue_id, respect_pacing))
        return False

    class FakeQuery:
        data = f"automation:mobile:{blocked.id}"
        from_user = SimpleNamespace(id=456)
        message = SimpleNamespace(
            chat=SimpleNamespace(id=123),
            text="Japanese approval card",
        )

        async def answer(self, *args, **kwargs):
            answers.append((args, kwargs))

        async def edit_message_text(self, *args, **kwargs):
            edits.append((args, kwargs))

    bot._send_next_reply_delivery = release

    asyncio.run(
        bot.automation_approval(
            SimpleNamespace(callback_query=FakeQuery()),
            SimpleNamespace(),
        )
    )

    assert bot.approvals.get(blocked.id).status == "expired"
    assert "closed automatically" in answers[0][0][0]
    assert answers[0][1].get("show_alert") is not True
    assert "Auto-closed" in edits[0][0][0]
    assert edits[0][1]["reply_markup"] is None
    assert released == [("safety-batch", False)]


def test_saved_approval_releases_queue_even_when_telegram_edit_fails(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            automation_approvals_path=str(tmp_path / "approvals.json"),
            reply_learning_path=str(tmp_path / "learning.json"),
        )
    )
    approval = bot.approvals.create(
        kind="reply",
        text="Approved despite edit failure",
        chat_id=123,
        approver_user_id=456,
        target_url="https://x.com/source/status/42",
        metadata={
            "reply_strategy": "specific_observation",
            "root_author": "source",
            "reply_delivery_queue_id": "batch-edit-failure",
            "reply_delivery_queue_index": 0,
            "reply_delivery_card_sent": True,
        },
    )
    released = []
    error_messages = []

    async def release(queue_id, *, respect_pacing):
        released.append((queue_id, respect_pacing))
        return True

    class Message:
        chat = SimpleNamespace(id=123)
        text = "Approval card"

        async def reply_text(self, text, **_kwargs):
            error_messages.append(text)

    class FakeQuery:
        data = f"automation:mobile:{approval.id}"
        from_user = SimpleNamespace(id=456)
        message = Message()

        async def answer(self, *_args, **_kwargs):
            return None

        async def edit_message_text(self, *_args, **_kwargs):
            raise RuntimeError("Telegram edit failed")

    bot._send_next_reply_delivery = release

    asyncio.run(
        bot.automation_approval(
            SimpleNamespace(callback_query=FakeQuery()),
            SimpleNamespace(),
        )
    )

    assert bot.approvals.get(approval.id).status == "mobile_approved"
    assert released == [("batch-edit-failure", True)]
    assert "queue" in error_messages[0].lower()


def test_daily_digest_marker_is_written_only_after_telegram_delivery(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            reply_learning_path=str(tmp_path / "learning.json"),
            creator_timezone="UTC",
            reply_daily_digest_hour=0,
        )
    )
    bot.approval_chat_id = 123
    bot.reply_learning.report = lambda *_args, **_kwargs: {
        "posted": 1,
        "measured": 1,
        "median_views": 10_000,
        "over_5k": 1,
        "over_20k": 0,
        "over_50k": 0,
        "author_response_rate": 0.25,
        "by_language": {},
        "by_source": {},
        "by_strategy": {},
    }

    class FailingTelegramBot:
        async def send_message(self, **_kwargs):
            raise RuntimeError("Telegram temporarily unavailable")

    bot._application = SimpleNamespace(bot=FailingTelegramBot())
    now = datetime(2026, 8, 5, 22, 0, tzinfo=UTC)

    try:
        asyncio.run(bot._maybe_send_daily_digest(now))
    except RuntimeError as exc:
        assert "Telegram temporarily unavailable" in str(exc)
    else:
        raise AssertionError("The Telegram failure should be visible to the scheduler")

    assert bot.reply_learning.last_digest_date == ""


def test_mobile_post_falls_back_to_a_short_composer_url_when_draft_is_long() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
    approval = bot.approvals.create(
        kind="post",
        text="Nội dung dài " * 200,
        chat_id=123,
    )

    assert len(_mobile_x_intent_url(approval)) > 1800
    assert _mobile_x_open_url(approval) == "https://x.com/compose/post"
    assert "copy it above" in _mobile_approval_note(approval)


def test_reply_approval_message_shows_vietnamese_summary_and_translation() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
    approval = bot.approvals.create(
        kind="reply",
        text="This is the reply draft.",
        chat_id=123,
        target_url="https://x.com/user/status/123",
        target_label="@user - topic",
        metadata={
            "source_summary_vi": "Tác giả chia sẻ một thay đổi trong kế hoạch.",
            "reply_translation_vi": "Điểm đánh đổi khi triển khai quan trọng hơn ngày ra mắt.",
            "root_views": 15_517,
            "root_replies": 21,
            "reply_opportunity_score": 56,
            "reply_strategy": "practical_implication",
            "video_context_quality": "caption_only",
        },
    )

    text = _approval_message_text(approval, reason="High engagement")

    assert text == (
        "https://x.com/user/status/123\n\n"
        "Tóm tắt bài viết:\n"
        "Tác giả chia sẻ một thay đổi trong kế hoạch.\n\n"
        "Bản dịch reply:\n"
        "Điểm đánh đổi khi triển khai quan trọng hơn ngày ra mắt.\n\n"
        "Reply gốc:\n"
        "This is the reply draft."
    )
    assert "caption only" not in text
    assert "15,517 views" not in text
    assert "21 replies" not in text
    assert "opportunity" not in text
    assert "practical implication" not in text
    assert "Why now" not in text


def test_approval_keyboard_omits_copy_button_for_long_post() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
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


def test_dedupe_queries_preserves_order() -> None:
    assert _dedupe_queries(["AI", " ai ", "", "Crypto", "crypto"]) == ["AI", "Crypto"]


def test_reply_target_max_age_is_clamped_to_supported_range() -> None:
    assert _reply_target_max_age_minutes(360, default=360) == 360
    assert _reply_target_max_age_minutes(15, default=360) == 30
    assert _reply_target_max_age_minutes("bad", default=360) == 360


def test_replytargets_fetches_each_topic_once_then_relaxes_locally() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
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
    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
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
    existing = bot.approvals.create(
        kind="reply",
        text="Existing reply card",
        chat_id=123,
        approver_user_id=123,
        target_url="https://x.com/already/status/1",
    )
    bot.approvals.decide(
        existing.id,
        approve=True,
        chat_id=123,
        user_id=123,
        destination="mobile",
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

    assert "reached the daily/adaptive hourly card ceiling" in messages[-1]
    assert "Confirmed now: 1" in messages[-1]
    assert "none is confirmed enough" not in messages[-1]


def test_replytargets_explicit_topic_expands_languages_without_topic_drift() -> None:
    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
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
    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
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


def test_replytargets_remove_active_cards_before_top_five_ranking(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            automation_approvals_path=str(tmp_path / "approvals.json"),
            reply_learning_path=str(tmp_path / "learning.json"),
            revenue_ops_path=str(tmp_path / "revenue.json"),
            reply_watch_path=str(tmp_path / "watch.json"),
            reply_target_metrics_path=str(tmp_path / "metrics.json"),
        )
    )
    now = int(datetime.now(UTC).timestamp())
    candidates: list[XSearchResult] = []
    for index in range(6):
        candidate = XSearchResult(
            id=900 + index,
            username=f"candidate{index}",
            display_name=f"Candidate {index}",
            text=f"Fresh distinct discussion {index}",
            created_at="",
            url=f"https://x.com/candidate{index}/status/{900 + index}",
            created_at_timestamp=now - 120,
            like_count=500 - index * 50,
            reply_count=5,
            view_count=100_000 - index * 10_000,
            author_followers_count=100_000,
        )
        candidates.append(candidate)
        if index < 5:
            bot.approvals.create(
                kind="reply",
                text=f"Existing draft {index}",
                chat_id=123,
                approver_user_id=123,
                target_url=candidate.url,
            )

    ranked = bot._rank_reply_target_pool(
        candidates,
        relaxed=True,
        max_age_minutes=360,
    )

    assert [result.id for result in ranked] == [905]


def test_replytargets_fill_from_other_languages_when_japanese_hourly_limit_is_full(
    tmp_path,
) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
            automation_approvals_path=str(tmp_path / "approvals.json"),
            reply_learning_path=str(tmp_path / "learning.json"),
            revenue_ops_path=str(tmp_path / "revenue.json"),
            reply_watch_path=str(tmp_path / "watch.json"),
            reply_target_metrics_path=str(tmp_path / "metrics.json"),
        )
    )
    for index in range(6):
        approval = bot.approvals.create(
            kind="reply",
            text=f"Approved Japanese reply {index}",
            chat_id=123,
            approver_user_id=123,
            target_url=f"https://x.com/history{index}/status/{1300 + index}",
            metadata={"language": "ja", "root_author": f"history{index}"},
        )
        bot.approvals.decide(
            approval.id,
            approve=True,
            chat_id=123,
            user_id=123,
            destination="mobile",
        )

    now = int(datetime.now(UTC).timestamp())
    candidates = [
        XSearchResult(
            id=1400 + index,
            username=f"japanese{index}",
            display_name=f"Japanese {index}",
            text=f"Fast Japanese post {index}",
            created_at="",
            url=f"https://x.com/japanese{index}/status/{1400 + index}",
            language="ja",
            created_at_timestamp=now - 120,
            like_count=1_000 - index * 50,
            reply_count=5,
            view_count=200_000 - index * 10_000,
            author_followers_count=100_000,
        )
        for index in range(5)
    ]
    candidates.append(
        XSearchResult(
            id=1500,
            username="englishbackup",
            display_name="English Backup",
            text="A slightly lower-ranked but eligible English post",
            created_at="",
            url="https://x.com/englishbackup/status/1500",
            language="en",
            created_at_timestamp=now - 120,
            like_count=300,
            reply_count=5,
            view_count=50_000,
            author_followers_count=100_000,
        )
    )

    ranked = bot._rank_reply_target_pool(
        candidates,
        relaxed=True,
        max_age_minutes=360,
    )

    assert [result.id for result in ranked] == [1500]


def test_replytargets_volume_fallback_lowers_view_floor_to_fill_batch(tmp_path) -> None:
    bot = ContentBot(
        Settings(
            telegram_bot_token="123:ABC",
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
    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
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
    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
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
    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
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
    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))

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
    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
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
    bot = ContentBot(Settings(telegram_bot_token="123:ABC"))
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
    assert "version 0.9.0" in message
    assert "fresh-tab retry" in message
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
