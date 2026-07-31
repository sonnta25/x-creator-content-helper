from dataclasses import replace
import time

from src.models import XSearchResult
from src.reply_target_metrics import ReplyTargetMetricStore
from src.x_search_service import rank_fast_growing_posts


def _post(*, views: int, likes: int, replies: int = 0, quotes: int = 0) -> XSearchResult:
    return XSearchResult(
        id=1,
        username="latebreakout",
        display_name="Late Breakout",
        text="This post starts moving a few hours after publication.",
        created_at="",
        created_at_timestamp=1_000_000 - (3 * 60 * 60),
        url="https://x.com/latebreakout/status/1",
        language="ja",
        view_count=views,
        like_count=likes,
        reply_count=replies,
        quote_count=quotes,
        author_followers_count=20_000,
    )


def test_metric_store_detects_late_breakout_from_snapshot_deltas(tmp_path) -> None:
    store = ReplyTargetMetricStore(tmp_path / "reply-target-metrics.json")
    first = store.observe([_post(views=2_000, likes=30)], now_timestamp=1_000_000)[0]
    second = store.observe(
        [_post(views=5_000, likes=90, replies=10, quotes=2)],
        now_timestamp=1_000_000 + (15 * 60),
    )[0]
    third = store.observe(
        [_post(views=11_000, likes=220, replies=35, quotes=8)],
        now_timestamp=1_000_000 + (30 * 60),
    )[0]

    assert first.momentum_observation_count == 1
    assert second.momentum_observation_count == 2
    assert second.recent_view_velocity_score == 200
    assert second.recent_reply_velocity_score == 10 / 15
    assert second.momentum_acceleration == 0
    assert third.momentum_observation_count == 3
    assert third.recent_view_velocity_score == 400
    assert third.recent_conversation_velocity_score > second.recent_conversation_velocity_score
    assert third.recent_reply_velocity_score == 25 / 15
    assert third.momentum_acceleration > 0


def test_recent_snapshot_velocity_can_revive_an_older_post() -> None:
    current_timestamp = int(time.time())
    stalled = XSearchResult(
        id=2,
        username="stalled",
        display_name="Stalled",
        text="Large lifetime total but no movement in the latest window",
        created_at="",
        created_at_timestamp=current_timestamp - (3 * 60 * 60),
        url="https://x.com/stalled/status/2",
        view_count=100_000,
        like_count=2_000,
        author_followers_count=500_000,
        momentum_observation_count=2,
    )
    breakout = replace(
        _post(views=12_000, likes=250, replies=30, quotes=10),
        created_at_timestamp=current_timestamp - (3 * 60 * 60),
        momentum_observation_count=2,
        recent_view_velocity_score=500,
        recent_engagement_velocity_score=20,
        recent_conversation_velocity_score=3,
        momentum_acceleration=1.0,
    )

    ranked = rank_fast_growing_posts(
        [stalled, breakout],
        max_age_minutes=360,
        min_author_followers=50_000,
    )

    assert [result.id for result in ranked] == [breakout.id]
    assert ranked[0].viral_score > 0
    assert ranked[0].reply_opportunity_score > 0
