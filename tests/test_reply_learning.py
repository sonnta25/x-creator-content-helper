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
