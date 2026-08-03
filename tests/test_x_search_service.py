import asyncio
from datetime import datetime, timedelta, timezone

from src.models import XSearchResult, XTrend
from src.config import Settings
import pytest

from src.x_search_service import (
    TREND_CATEGORIES,
    _to_search_result,
    default_english_query,
    extract_tweet_id,
    normalize_account_name,
    parse_reply_target_languages,
    query_for_language,
    rank_fast_growing_posts,
    rank_viral_video_posts,
    recent_search_query,
)
from src.x_search_service import XSearchService


def test_trend_categories_match_supported_twscrape_ids() -> None:
    assert TREND_CATEGORIES == {"trending", "news", "sport", "entertainment"}


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


def test_viral_video_ranking_prioritizes_low_reply_competition() -> None:
    created = int((datetime.now(timezone.utc) - timedelta(minutes=20)).timestamp())
    crowded = XSearchResult(
        id=1,
        username="crowded",
        display_name="",
        text="video",
        created_at="",
        created_at_timestamp=created,
        url="https://x.com/crowded/status/1",
        has_video=True,
        view_count=100_000,
        like_count=500,
        reply_count=500,
    )
    open_thread = XSearchResult(
        id=2,
        username="open",
        display_name="",
        text="video",
        created_at="",
        created_at_timestamp=created,
        url="https://x.com/open/status/2",
        has_video=True,
        view_count=80_000,
        like_count=400,
        reply_count=5,
    )

    ranked = rank_viral_video_posts(
        [crowded, open_thread],
        min_view_count=15_000,
        min_view_velocity=300,
    )

    assert [item.id for item in ranked] == [2, 1]
    assert ranked[0].views_per_reply > ranked[1].views_per_reply


def test_reply_target_languages_support_japanese_and_bound_the_scan() -> None:
    assert parse_reply_target_languages("en, ja,ko,ja") == ["en", "ja", "ko"]
    assert parse_reply_target_languages("bad-code", default="en,ja") == ["en", "ja"]
    assert parse_reply_target_languages("en,ja,ko,es,pt,id,vi") == [
        "en",
        "ja",
        "ko",
        "es",
        "pt",
        "id",
    ]
    assert query_for_language("OpenAI", "ja") == "OpenAI lang:ja"
    assert query_for_language("OpenAI lang:vi", "ja") == "OpenAI lang:vi"


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


def test_rank_fast_growing_posts_allows_view_only_volume_fallback() -> None:
    now = datetime.now(timezone.utc)
    view_only = XSearchResult(
        id=9,
        username="viewonly",
        display_name="View Only",
        text="Views arrived before reactions",
        created_at=str(now - timedelta(minutes=3)),
        created_at_timestamp=int((now - timedelta(minutes=3)).timestamp()),
        url="https://x.com/viewonly/status/9",
        view_count=125,
    )

    standard = rank_fast_growing_posts(
        [view_only],
        max_age_minutes=30,
        min_engagement_score=0,
        min_velocity_score=0,
        min_view_velocity_score=0,
        min_view_count=125,
    )
    fallback = rank_fast_growing_posts(
        [view_only],
        max_age_minutes=30,
        min_engagement_score=0,
        min_velocity_score=0,
        min_view_velocity_score=0,
        min_view_count=125,
        allow_view_only_signal=True,
    )

    assert standard == []
    assert [result.id for result in fallback] == [9]


def test_rank_fast_growing_posts_excludes_replies_and_retweet_wrappers() -> None:
    now = datetime.now(timezone.utc)
    base = dict(
        username="user",
        display_name="User",
        text="Strong metrics",
        created_at=str(now - timedelta(minutes=5)),
        created_at_timestamp=int((now - timedelta(minutes=5)).timestamp()),
        like_count=100,
        reply_count=20,
        view_count=10_000,
        author_followers_count=100_000,
    )
    original = XSearchResult(
        id=10,
        url="https://x.com/user/status/10",
        **base,
    )
    reply = XSearchResult(
        id=11,
        url="https://x.com/user/status/11",
        is_reply=True,
        **base,
    )
    retweet = XSearchResult(
        id=12,
        url="https://x.com/user/status/12",
        is_retweet=True,
        **base,
    )

    ranked = rank_fast_growing_posts([reply, retweet, original], max_age_minutes=360)

    assert [result.id for result in ranked] == [10]


def test_rank_fast_growing_posts_prefers_breakout_over_account_size() -> None:
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

    assert [result.id for result in ranked] == [20, 21]
    assert ranked[0].viral_score > ranked[1].viral_score
    assert ranked[0].breakout_ratio == 1.0
    assert ranked[0].view_velocity_score > ranked[1].view_velocity_score


def test_rank_fast_growing_posts_keeps_conversation_signal_without_rewarding_crowding() -> None:
    now = datetime.now(timezone.utc)
    broadcast = XSearchResult(
        id=30,
        username="broadcast",
        display_name="Broadcast",
        text="Lots of views but almost no conversation",
        created_at=str(now - timedelta(minutes=10)),
        created_at_timestamp=int((now - timedelta(minutes=10)).timestamp()),
        url="https://x.com/broadcast/status/30",
        like_count=40,
        view_count=20_000,
        author_followers_count=100_000,
    )
    conversation = XSearchResult(
        id=31,
        username="conversation",
        display_name="Conversation",
        text="A post people are actively discussing",
        created_at=str(now - timedelta(minutes=10)),
        created_at_timestamp=int((now - timedelta(minutes=10)).timestamp()),
        url="https://x.com/conversation/status/31",
        like_count=40,
        reply_count=20,
        quote_count=10,
        view_count=20_000,
        author_followers_count=100_000,
    )

    ranked = rank_fast_growing_posts(
        [broadcast, conversation],
        max_age_minutes=30,
        min_author_followers=50_000,
    )

    by_id = {result.id: result for result in ranked}
    assert [result.id for result in ranked] == [30, 31]
    assert by_id[31].conversation_velocity_score > by_id[30].conversation_velocity_score
    assert by_id[31].viral_score > by_id[30].viral_score
    assert by_id[30].thread_availability_score > by_id[31].thread_availability_score
    assert by_id[30].reply_opportunity_score > by_id[31].reply_opportunity_score


def test_rank_fast_growing_posts_penalizes_a_saturated_reply_thread() -> None:
    now = datetime.now(timezone.utc)
    base = dict(
        display_name="Post",
        text="The same distribution, with very different reply competition",
        created_at="",
        created_at_timestamp=int((now - timedelta(minutes=20)).timestamp()),
        language="en",
        like_count=100,
        view_count=40_000,
        author_followers_count=100_000,
        momentum_observation_count=2,
        recent_view_velocity_score=1_000,
        recent_engagement_velocity_score=20,
    )
    open_thread = XSearchResult(
        id=32,
        username="open",
        url="https://x.com/open/status/32",
        reply_count=20,
        recent_reply_velocity_score=0.5,
        **base,
    )
    saturated = XSearchResult(
        id=33,
        username="saturated",
        url="https://x.com/saturated/status/33",
        reply_count=700,
        recent_reply_velocity_score=10,
        **base,
    )

    ranked = rank_fast_growing_posts(
        [saturated, open_thread],
        max_age_minutes=360,
    )

    assert [result.id for result in ranked] == [32, 33]
    assert ranked[0].views_per_reply > ranked[1].views_per_reply
    assert ranked[0].recent_views_per_reply > ranked[1].recent_views_per_reply
    assert ranked[0].reply_saturation_penalty < ranked[1].reply_saturation_penalty


def test_rank_fast_growing_posts_keeps_one_qualified_candidate_per_language() -> None:
    now = datetime.now(timezone.utc)

    def candidate(post_id: int, language: str, views: int, likes: int) -> XSearchResult:
        return XSearchResult(
            id=post_id,
            username=f"user{post_id}",
            display_name="User",
            text="Qualified candidate",
            created_at="",
            created_at_timestamp=int((now - timedelta(minutes=15)).timestamp()),
            url=f"https://x.com/user{post_id}/status/{post_id}",
            language=language,
            like_count=likes,
            reply_count=10,
            view_count=views,
            author_followers_count=100_000,
        )

    ranked = rank_fast_growing_posts(
        [
            candidate(34, "en", 20_000, 200),
            candidate(35, "en", 15_000, 150),
            candidate(36, "ja", 5_000, 50),
        ],
        max_items=2,
        max_age_minutes=360,
    )

    assert {result.language for result in ranked} == {"en", "ja"}


def test_to_search_result_captures_author_reach() -> None:
    class User:
        id = 7
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
        conversationId = 22
        inReplyToTweetId = 11

    result = _to_search_result(Tweet())

    assert result.author_followers_count == 250_000
    assert result.author_verified is True
    assert result.author_id == 7
    assert result.conversation_id == 22
    assert result.in_reply_to_tweet_id == 11


def test_to_search_result_marks_video_and_keeps_exposed_alt_text() -> None:
    class User:
        id = 8
        username = "video"
        displayname = "Video"

    class Video:
        thumbnailUrl = "https://pbs.twimg.com/video_thumb/1.jpg"
        altText = "A goalkeeper makes a last-second save"

    class Media:
        photos = []
        videos = [Video()]
        animated = []

    class Tweet:
        id = 23
        user = User()
        rawContent = "Perfect timing"
        date = datetime.now(timezone.utc)
        replyCount = 1
        retweetCount = 2
        quoteCount = 0
        likeCount = 100
        viewCount = 20_000
        media = Media()

    result = _to_search_result(Tweet())

    assert result.has_video is True
    assert result.media_urls == ["https://pbs.twimg.com/video_thumb/1.jpg"]
    assert result.media_descriptions == ["A goalkeeper makes a last-second save"]


def test_search_keeps_captionless_video_candidates() -> None:
    class User:
        id = 8
        username = "video"
        displayname = "Video"

    class Video:
        thumbnailUrl = "https://pbs.twimg.com/video_thumb/2.jpg"

    class Media:
        photos = []
        videos = [Video()]
        animated = []

    class Tweet:
        id = 24
        user = User()
        rawContent = ""
        text = ""
        date = datetime.now(timezone.utc)
        media = Media()

    class FakeApi:
        async def search(self, query, limit, kv):
            yield Tweet()

    service = XSearchService(Settings(telegram_bot_token="123:ABC"))
    service._api = FakeApi()

    results = asyncio.run(
        service.search("filter:videos lang:en", limit=1, product="Latest")
    )

    assert len(results) == 1
    assert results[0].text == ""
    assert results[0].has_video is True


def test_owner_timeline_and_direct_replies_use_twscrape_api() -> None:
    class User:
        id = 7
        username = "owner"
        displayname = "Owner"

    class Tweet:
        def __init__(self, tweet_id: int, parent_id: int) -> None:
            self.id = tweet_id
            self.user = User()
            self.rawContent = "Posted reply"
            self.date = datetime.now(timezone.utc)
            self.inReplyToTweetId = parent_id
            self.replyCount = 0
            self.retweetCount = 0
            self.quoteCount = 0
            self.likeCount = 0
            self.viewCount = 10
            self.media = None

    class FakeApi:
        async def user_by_login(self, username: str):
            assert username == "owner"
            return User()

        async def user_tweets_and_replies(self, user_id: int, limit: int = -1):
            assert user_id == 7
            assert limit == 5
            yield Tweet(50, 42)

        async def tweet_replies(self, tweet_id: int, limit: int = -1):
            assert tweet_id == 50
            assert limit == 3
            yield Tweet(51, 50)

    class TestService(XSearchService):
        async def _get_api(self):
            return FakeApi()

    import asyncio

    service = TestService(Settings(telegram_bot_token="123:ABC"))
    timeline = asyncio.run(service.user_tweets_and_replies("@owner", limit=5))
    replies = asyncio.run(service.tweet_replies(50, limit=3))

    assert timeline[0].in_reply_to_tweet_id == 42
    assert replies[0].in_reply_to_tweet_id == 50


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
