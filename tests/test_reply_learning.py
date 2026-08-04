from datetime import UTC, datetime, timedelta

from src.automation import AutomationApproval
from src.models import XSearchResult
from src.reply_learning import (
    MIN_FEEDBACK_SAMPLES_TO_TUNE,
    MIN_FINAL_SAMPLES_TO_TUNE,
    ReplyLearningStore,
    match_posted_content,
)


def _result(
    tweet_id: int,
    *,
    target_id: int | None = None,
    text: str = "Useful posted reply",
    created_at: datetime | None = None,
    views: int = 100,
    likes: int = 2,
    username: str = "owner",
    followers: int | None = None,
) -> XSearchResult:
    date = created_at or datetime.now(UTC)
    return XSearchResult(
        id=tweet_id,
        username=username,
        display_name=username,
        text=text,
        created_at=date.isoformat(),
        created_at_timestamp=int(date.timestamp()),
        url=f"https://x.com/{username}/status/{tweet_id}",
        language="en",
        is_reply=target_id is not None,
        in_reply_to_tweet_id=target_id,
        view_count=views,
        like_count=likes,
        author_followers_count=followers,
    )


def _approval(
    approval_id: str,
    *,
    target_id: int,
    approved_at: datetime,
    strategy: str = "specific_observation",
) -> AutomationApproval:
    return AutomationApproval(
        id=approval_id,
        kind="reply",
        text="Useful posted reply",
        chat_id=1,
        approver_user_id=1,
        target_url=f"https://x.com/source/status/{target_id}",
        status="mobile_approved",
        created_at=approved_at,
        decided_at=approved_at,
        metadata={
            "reply_strategy": strategy,
            "root_author": "source",
            "root_views": 1000,
            "root_replies": 5,
        },
    )


def test_matches_posted_content_by_parent_time_and_text() -> None:
    approved_at = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
    record = {
        "target_id": 42,
        "approved_at": approved_at.isoformat(),
        "draft_text": "Useful posted reply",
    }
    wrong_parent = _result(
        101,
        target_id=99,
        created_at=approved_at + timedelta(minutes=2),
    )
    correct = _result(
        102,
        target_id=42,
        text="Useful posted reply!",
        created_at=approved_at + timedelta(minutes=3),
    )

    assert match_posted_content(record, [wrong_parent, correct]) == correct


def test_tracking_persists_and_finishes_at_24h(tmp_path) -> None:
    path = tmp_path / "reply-learning.json"
    store = ReplyLearningStore(path)
    approved_at = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)
    approval = _approval("a1", target_id=42, approved_at=approved_at)
    store.register_approval(approval)
    posted = _result(100, target_id=42, created_at=approved_at + timedelta(minutes=2))
    store.mark_discovered("a1", posted)

    assert store.due_checkpoint(
        store.records("tracking")[0],
        now=approved_at + timedelta(minutes=20),
    ) == 15

    root = _result(42, views=2000, username="source")
    store.add_snapshot(
        "a1",
        checkpoint_minutes=1440,
        reply=posted,
        root=root,
        author_replied=True,
        captured_at=approved_at + timedelta(days=1, minutes=2),
    )

    restored = ReplyLearningStore(path)
    record = restored.records("measured")[0]
    assert record["author_replied"] is True
    assert record["final_score"] > 0


def test_learning_waits_for_60_samples_then_tunes_with_bounded_rollback(tmp_path) -> None:
    store = ReplyLearningStore(tmp_path / "learning.json")
    now = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
    for index in range(MIN_FINAL_SAMPLES_TO_TUNE):
        strategy = (
            "specific_observation"
            if index < MIN_FINAL_SAMPLES_TO_TUNE // 2
            else "natural_humor"
        )
        approval = _approval(
            f"a{index}",
            target_id=1000 + index,
            approved_at=now - timedelta(days=2),
            strategy=strategy,
        )
        store.register_approval(approval)
        reply_views = 200 if strategy == "specific_observation" else 1
        reply = _result(
            2000 + index,
            target_id=1000 + index,
            created_at=now - timedelta(days=1),
            views=reply_views,
            likes=10 if strategy == "specific_observation" else 0,
        )
        store.mark_discovered(approval.id, reply)
        store.add_snapshot(
            approval.id,
            checkpoint_minutes=1440,
            reply=reply,
            root=_result(1000 + index, views=2000, username="source"),
            captured_at=now,
        )

    old = store.weights
    assert store.maybe_tune(now=now) is True
    new = store.weights
    assert new["specific_observation"] > old["specific_observation"]
    for strategy, weight in old.items():
        assert abs(new[strategy] - weight) <= weight * 0.100001
    assert store.rollback() is True
    assert store.weights == old


def test_matches_original_post_and_reports_account_follower_window_proxy(tmp_path) -> None:
    approved_at = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)
    approval = AutomationApproval(
        id="post-1",
        kind="post",
        text="A concrete original post",
        chat_id=1,
        approver_user_id=1,
        status="mobile_approved",
        created_at=approved_at,
        decided_at=approved_at,
    )
    store = ReplyLearningStore(tmp_path / "learning.json")
    record = store.register_approval(approval)
    assert record is not None

    posted = _result(
        501,
        text="A concrete original post",
        created_at=approved_at + timedelta(minutes=4),
        followers=1_000,
    )
    reply = _result(
        502,
        target_id=99,
        text="A concrete original post",
        created_at=approved_at + timedelta(minutes=3),
    )
    assert match_posted_content(record, [reply, posted]) == posted

    store.mark_discovered(approval.id, posted)
    measured = _result(
        501,
        text=posted.text,
        created_at=posted.created_at_timestamp
        and datetime.fromtimestamp(posted.created_at_timestamp, tz=UTC),
        views=2_000,
        likes=50,
        followers=1_012,
    )
    store.add_snapshot(
        approval.id,
        checkpoint_minutes=1440,
        reply=measured,
        root=None,
        captured_at=approved_at + timedelta(days=1),
    )

    report = store.report(now=approved_at + timedelta(days=1, minutes=1))
    assert report["posts"] == 1
    assert report["replies"] == 0
    assert report["follower_window_lift"] == 12


def test_approval_feedback_can_tune_strategy_mix_without_analytics_import(tmp_path) -> None:
    store = ReplyLearningStore(tmp_path / "learning.json")
    now = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
    for index in range(MIN_FEEDBACK_SAMPLES_TO_TUNE):
        strategy = (
            "specific_observation"
            if index < MIN_FEEDBACK_SAMPLES_TO_TUNE // 2
            else "natural_humor"
        )
        approval = _approval(
            f"feedback-{index}",
            target_id=2000 + index,
            approved_at=now,
            strategy=strategy,
        )
        store.record_feedback(
            approval,
            approved=strategy == "specific_observation",
        )

    old = store.weights
    assert store.maybe_tune(now=now) is True
    assert store.weights["specific_observation"] > old["specific_observation"]
    assert store.weights["natural_humor"] < old["natural_humor"]


def test_author_response_builds_relationship_strength_and_stop_signal(tmp_path) -> None:
    store = ReplyLearningStore(tmp_path / "learning.json")
    posted_at = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
    approval = _approval(
        "relationship-1",
        target_id=42,
        approved_at=posted_at - timedelta(minutes=2),
    )
    store.register_approval(approval)
    posted = _result(100, target_id=42, created_at=posted_at)
    store.mark_discovered(approval.id, posted)
    tracking = store.records("tracking")[0]
    assert store.author_response_check_due(
        tracking,
        now=posted_at + timedelta(minutes=20),
    ) is True
    store.mark_author_response_checked(
        approval.id,
        checked_at=posted_at + timedelta(minutes=20),
    )
    assert store.author_response_check_due(
        tracking,
        now=posted_at + timedelta(minutes=24),
    ) is False
    assert store.author_response_check_due(
        tracking,
        now=posted_at + timedelta(minutes=25),
    ) is True
    response = _result(
        101,
        target_id=100,
        text="Good question. Retention was the deciding factor.",
        created_at=posted_at + timedelta(minutes=18),
        username="source",
    )

    record = store.mark_author_response(
        approval.id,
        response,
        detected_at=posted_at + timedelta(minutes=20),
    )
    strength_before_stop = store.relationship_strength(
        "source",
        now=posted_at + timedelta(minutes=20),
    )

    assert record["author_replied"] is True
    assert record["author_response_id"] == 101
    assert record["author_response_text"].startswith("Good question")
    assert record["author_response_latency_minutes"] == 18
    assert strength_before_stop > 0

    store.mark_conversation_stopped(approval.id)
    strength_after_stop = store.relationship_strength(
        "source",
        now=posted_at + timedelta(minutes=20),
    )
    assert store.records("tracking")[0]["conversation_stopped"] is True
    assert strength_after_stop < strength_before_stop


def test_style_memory_and_multidimensional_report_use_real_posted_replies(tmp_path) -> None:
    store = ReplyLearningStore(tmp_path / "learning.json")
    now = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    for index in range(5):
        approval = _approval(
            f"style-{index}",
            target_id=500 + index,
            approved_at=now - timedelta(days=1),
        )
        approval.text = f"Specific draft {index}"
        approval.metadata.update(
            {
                "language": "ja" if index < 3 else "en",
                "source_type": "replyvideo" if index < 3 else "replytargets",
                "creator_timezone": "Asia/Ho_Chi_Minh",
            }
        )
        store.register_approval(approval)
        posted = _result(
            800 + index,
            target_id=500 + index,
            text=f"Real posted reply {index}",
            created_at=now - timedelta(hours=20 - index),
            views=25_000 if index < 3 else 1_000,
            likes=20,
        )
        posted = XSearchResult(**{**posted.__dict__, "language": approval.metadata["language"]})
        store.mark_discovered(approval.id, posted)
        store.add_snapshot(
            approval.id,
            checkpoint_minutes=1440,
            reply=posted,
            root=_result(500 + index, views=100_000, username="source"),
            captured_at=now,
        )

    examples = store.style_examples(
        language="ja",
        source_type="replyvideo",
        limit=2,
    )
    report = store.report(now=now + timedelta(minutes=1))

    assert len(examples) == 2
    assert all(text.startswith("Real posted reply") for text in examples)
    assert report["median_views"] > 0
    assert report["over_20k"] == 3
    assert report["by_language"]["ja"]["count"] == 3
    assert report["by_source"]["replyvideo"]["count"] == 3
    assert report["by_hour_local"]
    assert store.performance_adjustment(
        language="ja",
        source_type="replyvideo",
    ) > 1.0


def test_daily_digest_marker_persists(tmp_path) -> None:
    path = tmp_path / "learning.json"
    store = ReplyLearningStore(path)
    store.mark_digest_sent("2026-08-03")

    assert ReplyLearningStore(path).last_digest_date == "2026-08-03"


def test_reply_windows_contribute_to_follower_lift_and_experiment_report(tmp_path) -> None:
    store = ReplyLearningStore(tmp_path / "learning.json")
    now = datetime(2026, 8, 4, 12, tzinfo=UTC)
    approval = _approval("reply-lift", target_id=900, approved_at=now - timedelta(days=1))
    approval.metadata.update(
        {
            "experiment_variant": "concise_statement",
            "approval_latency_seconds": 45,
            "verified_audience_proxy": 70,
        }
    )
    store.register_approval(approval)
    posted = _result(
        901,
        target_id=900,
        created_at=now - timedelta(hours=23),
        views=25_000,
        followers=1_000,
    )
    store.mark_discovered(approval.id, posted)
    later = _result(
        901,
        target_id=900,
        created_at=now - timedelta(hours=23),
        views=25_000,
        followers=1_025,
    )
    store.add_snapshot(
        approval.id,
        checkpoint_minutes=1440,
        reply=later,
        root=_result(900, views=100_000, username="source"),
        owner_followers=1_025,
        captured_at=now,
    )

    report = store.report(now=now + timedelta(minutes=1))

    assert report["follower_window_lift"] == 25
    assert report["by_experiment"]["concise_statement"]["count"] == 1
    assert report["median_approval_latency_seconds"] == 45


def test_experiment_variants_rotate_and_can_be_disabled(tmp_path) -> None:
    store = ReplyLearningStore(tmp_path / "learning.json")

    first = store.choose_experiment_variant()
    second = store.choose_experiment_variant()
    assert first != second

    store.set_experiment_enabled(False)
    assert store.choose_experiment_variant() == "adaptive"


def test_author_portfolios_expose_auto_watch_signals(tmp_path) -> None:
    store = ReplyLearningStore(tmp_path / "learning.json")
    now = datetime(2026, 8, 4, 12, tzinfo=UTC)
    approval = _approval(
        "portfolio-signals",
        target_id=700,
        approved_at=now - timedelta(hours=2),
    )
    approval.metadata.update(
        {
            "verified_audience_proxy": 65,
            "monetization_risk_level": "green",
        }
    )
    store.register_approval(approval)
    posted = _result(
        701,
        target_id=700,
        created_at=now - timedelta(hours=1),
        views=25_000,
    )
    store.mark_discovered(approval.id, posted)
    store.add_snapshot(
        approval.id,
        checkpoint_minutes=60,
        reply=posted,
        root=_result(700, views=100_000, username="source"),
        author_replied=True,
        captured_at=now,
    )

    rows = store.author_portfolios()

    assert len(rows) == 1
    assert rows[0]["username"] == "source"
    assert rows[0]["median_views"] == 25_000
    assert rows[0]["verified_audience_proxy"] == 65
    assert rows[0]["green_rate"] == 1.0
    assert rows[0]["last_interaction_at"] == posted.created_at
