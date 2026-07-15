from datetime import datetime, timedelta, timezone

from src.models import XSearchResult, XTrend
from src.config import Settings
import pytest

from src.x_search_service import (
    TREND_CATEGORIES,
    TREND_FALLBACK_QUERIES,
    _to_search_result,
    default_english_query,
    extract_tweet_id,
    normalize_account_name,
    rank_fast_growing_posts,
    recent_search_query,
    summarize_trends_context,
)
from src.x_search_service import XSearchService


def test_trend_categories_match_supported_twscrape_ids() -> None:
    assert TREND_CATEGORIES == {"trending", "news", "sport", "entertainment"}
    assert set(TREND_FALLBACK_QUERIES) == TREND_CATEGORIES


def test_summarize_trends_context() -> None:
    context = summarize_trends_context(
        [XTrend(name="OpenAI", rank="1", description="Trending in Technology")]
    )

    assert context == "1. OpenAI (rank 1) - Trending in Technology"


def test_normalize_account_name() -> None:
    assert normalize_account_name("account_2") == "account_2"
    with pytest.raises(RuntimeError):
        normalize_account_name("bad account")


def test_default_english_query() -> None:
    assert default_english_query("AI agents") == "AI agents lang:en"
    assert default_english_query("AI agents   filter:links") == "AI agents filter:links lang:en"
    assert default_english_query("AI agents lang:vi") == "AI agents lang:vi"
    with pytest.raises(RuntimeError):
        default_english_query(" ")


def test_recent_search_query_adds_since_time() -> None:
    query = recent_search_query("AI agents", since_minutes=30)

    assert query.startswith("AI agents lang:en since_time:")
    assert recent_search_query("AI agents lang:vi since_time:123", 30) == (
        "AI agents lang:vi since_time:123"
    )


def test_extract_tweet_id() -> None:
    assert extract_tweet_id("https://x.com/user/status/1234567890") == 1234567890
    assert extract_tweet_id("https://twitter.com/user/status/987654321") == 987654321
    assert extract_tweet_id("https://x.com/i/web/status/111222333") == 111222333
    assert extract_tweet_id("not a tweet link") is None


def test_rank_fast_growing_posts_prefers_recent_velocity() -> None:
    now = datetime.now(timezone.utc)
    fast = XSearchResult(
        id=1,
        username="fast",
        display_name="Fast",
        text="Fresh post",
        created_at=str(now - timedelta(minutes=5)),
        created_at_timestamp=int((now - timedelta(minutes=5)).timestamp()),
        url="https://x.com/fast/status/1",
        like_count=10,
        retweet_count=2,
        reply_count=2,
    )
    slow = XSearchResult(
        id=2,
        username="slow",
        display_name="Slow",
        text="Older post",
        created_at=str(now - timedelta(minutes=25)),
        created_at_timestamp=int((now - timedelta(minutes=25)).timestamp()),
        url="https://x.com/slow/status/2",
        like_count=20,
        retweet_count=2,
        reply_count=2,
    )
    old = XSearchResult(
        id=3,
        username="old",
        display_name="Old",
        text="Too old",
        created_at=str(now - timedelta(minutes=45)),
        created_at_timestamp=int((now - timedelta(minutes=45)).timestamp()),
        url="https://x.com/old/status/3",
        like_count=100,
    )

    ranked = rank_fast_growing_posts([slow, old, fast], max_items=5, max_age_minutes=30)

    assert [result.username for result in ranked] == ["fast", "slow"]
    assert ranked[0].velocity_score > ranked[1].velocity_score


def test_rank_fast_growing_posts_filters_weak_low_view_posts() -> None:
    now = datetime.now(timezone.utc)
    weak = XSearchResult(
        id=1,
        username="weak",
        display_name="Weak",
        text="Fresh but not moving",
        created_at=str(now - timedelta(minutes=6)),
        created_at_timestamp=int((now - timedelta(minutes=6)).timestamp()),
        url="https://x.com/weak/status/1",
        like_count=3,
        reply_count=0,
        retweet_count=0,
        view_count=45,
    )
    good = XSearchResult(
        id=2,
        username="good",
        display_name="Good",
        text="Fresh and actually moving",
        created_at=str(now - timedelta(minutes=8)),
        created_at_timestamp=int((now - timedelta(minutes=8)).timestamp()),
        url="https://x.com/good/status/2",
        like_count=18,
        reply_count=2,
        retweet_count=1,
        quote_count=1,
        view_count=1200,
    )

    ranked = rank_fast_growing_posts([weak, good], max_items=5, max_age_minutes=30)

    assert [result.username for result in ranked] == ["good"]


def test_rank_fast_growing_posts_requires_large_accounts_when_configured() -> None:
    now = datetime.now(timezone.utc)
    small = XSearchResult(
        id=20,
        username="small",
        display_name="Small",
        text="High engagement from a small account",
        created_at=str(now - timedelta(minutes=5)),
        created_at_timestamp=int((now - timedelta(minutes=5)).timestamp()),
        url="https://x.com/small/status/20",
        like_count=100,
        view_count=10_000,
        author_followers_count=10_000,
    )
    large = XSearchResult(
        id=21,
        username="large",
        display_name="Large",
        text="Fresh post from a large account",
        created_at=str(now - timedelta(minutes=5)),
        created_at_timestamp=int((now - timedelta(minutes=5)).timestamp()),
        url="https://x.com/large/status/21",
        like_count=10,
        view_count=1_000,
        author_followers_count=100_000,
    )

    ranked = rank_fast_growing_posts(
        [small, large],
        max_age_minutes=30,
        min_author_followers=50_000,
    )

    assert [result.id for result in ranked] == [21]


def test_to_search_result_captures_author_reach() -> None:
    class User:
        username = "large"
        displayname = "Large Account"
        followersCount = 250_000
        verified = True
        blue = False

    class Tweet:
        id = 22
        user = User()
        rawContent = "A current high-reach post"
        date = datetime.now(timezone.utc)
        replyCount = 2
        retweetCount = 3
        quoteCount = 1
        likeCount = 20
        viewCount = 4_000
        media = None

    result = _to_search_result(Tweet())

    assert result.author_followers_count == 250_000
    assert result.author_verified is True


def test_trends_consumes_async_generator() -> None:
    class RawTrend:
        name = "OpenAI"
        rank = 1
        trend_metadata = None

    class FakeApi:
        async def trends(self, category: str, limit: int = -1):
            assert category == "trending"
            assert limit == 5
            yield RawTrend()

    class TestService(XSearchService):
        async def _get_api(self):
            return FakeApi()

    import asyncio

    service = TestService(Settings(telegram_bot_token="123:ABC", x_cookie="auth_token=a; ct0=b"))
    trends = asyncio.run(service.trends(limit=5))

    assert trends == [XTrend(name="OpenAI", rank="1")]
