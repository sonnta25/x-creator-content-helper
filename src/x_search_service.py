from __future__ import annotations

from dataclasses import replace
from contextlib import aclosing
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import re
from typing import Any

from src.config import Settings
from src.models import XSearchResult, XTrend


TREND_CATEGORIES = {"trending", "news", "sport", "entertainment"}
SUPPORTED_REPLY_TARGET_LANGUAGES = {
    "am", "ar", "bg", "bn", "bo", "ca", "ckb", "cs", "cy", "da", "de",
    "dv", "el", "en", "es", "et", "eu", "fa", "fi", "fr", "gu", "he",
    "hi", "ht", "hu", "hy", "id", "in", "is", "it", "iw", "ja", "ka",
    "km", "kn", "ko", "lo", "lt", "lv", "ml", "mr", "my", "ne", "nl",
    "no", "or", "pa", "pl", "ps", "pt", "ro", "ru", "sd", "si", "sk",
    "sl", "sr", "sv", "ta", "te", "th", "tl", "tr", "ug", "uk", "ur",
    "vi", "zh-cn", "zh-tw",
}
MAX_REPLY_TARGET_LANGUAGES = 6

TREND_FALLBACK_QUERIES = {
    "trending": ["openai", "AI", "news", "entertainment"],
    "news": ["news", "politics", "business", "technology"],
    "sport": ["sports", "NBA", "NFL", "soccer"],
    "entertainment": ["entertainment", "movies", "music", "Netflix"],
}

REPLY_TARGET_SEARCH_LIMIT = 40
MIN_REPLY_TARGET_ENGAGEMENT_SCORE = 10.0
MIN_REPLY_TARGET_VELOCITY_SCORE = 1.0
MIN_REPLY_TARGET_VIEW_VELOCITY_SCORE = 50.0
MIN_REPLY_TARGET_VIEW_COUNT = 500
MIN_REPLY_TARGET_AUTHOR_FOLLOWERS = 50_000


class XSearchService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._api: Any | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.settings.x_cookie)

    async def import_cookie_account(self, account_name: str, cookie: str) -> str:
        clean_name = normalize_account_name(account_name)
        api = await self._get_api()
        await api.pool.delete_accounts(clean_name)
        await api.pool.add_account_cookies(clean_name, cookie)
        await api.pool.reset_locks()
        return clean_name

    async def remove_cookie_account(self, account_name: str) -> str:
        clean_name = normalize_account_name(account_name)
        api = await self._get_api()
        await api.pool.delete_accounts(clean_name)
        await api.pool.reset_locks()
        return clean_name

    async def accounts_info(self) -> list[dict[str, Any]]:
        api = await self._get_api()
        return await api.pool.accounts_info()

    async def search(
        self,
        query: str,
        limit: int | None = None,
        product: str | None = None,
    ) -> list[XSearchResult]:
        clean_query = default_english_query(query)
        api = await self._get_api()
        search_limit = limit or self.settings.x_search_limit
        results: list[XSearchResult] = []
        try:
            async with aclosing(
                api.search(
                    clean_query,
                    limit=search_limit,
                    kv={"product": product or self.settings.x_search_product},
                )
            ) as stream:
                async for tweet in stream:
                    result = _to_search_result(tweet)
                    if result.text:
                        results.append(result)
                    if len(results) >= search_limit:
                        break
        except Exception as exc:
            raise RuntimeError(f"X search failed: {exc}") from exc
        return results

    async def search_recent(
        self,
        query: str,
        since_minutes: int,
        limit: int | None = None,
        product: str = "Latest",
    ) -> tuple[str, list[XSearchResult]]:
        recent_query = recent_search_query(query, since_minutes)
        return recent_query, await self.search(recent_query, limit=limit, product=product)

    async def tweet_by_id(self, tweet_id: int) -> XSearchResult | None:
        api = await self._get_api()
        try:
            tweet = await api.tweet_details(tweet_id)
        except Exception as exc:
            raise RuntimeError(f"X tweet lookup failed: {exc}") from exc
        if tweet is None:
            return None
        return _to_search_result(tweet)

    async def user_tweets_and_replies(
        self,
        username: str,
        *,
        limit: int = 40,
    ) -> list[XSearchResult]:
        api = await self._get_api()
        clean_username = username.strip().lstrip("@")
        if not clean_username:
            raise RuntimeError("X owner username is empty.")
        try:
            user = await api.user_by_login(clean_username)
            if user is None:
                raise RuntimeError(f"X user @{clean_username} was not found.")
            results: list[XSearchResult] = []
            async with aclosing(
                api.user_tweets_and_replies(int(user.id), limit=limit)
            ) as stream:
                async for tweet in stream:
                    result = _to_search_result(tweet)
                    if result.text:
                        results.append(result)
                    if len(results) >= limit:
                        break
            return results
        except Exception as exc:
            raise RuntimeError(f"X owner timeline lookup failed: {exc}") from exc

    async def tweet_replies(
        self,
        tweet_id: int,
        *,
        limit: int = 20,
    ) -> list[XSearchResult]:
        api = await self._get_api()
        try:
            results: list[XSearchResult] = []
            async with aclosing(api.tweet_replies(tweet_id, limit=limit)) as stream:
                async for tweet in stream:
                    result = _to_search_result(tweet)
                    if result.text:
                        results.append(result)
                    if len(results) >= limit:
                        break
            return results
        except Exception as exc:
            raise RuntimeError(f"X reply lookup failed: {exc}") from exc

    async def trends(self, category: str = "trending", limit: int = 10) -> list[XTrend]:
        clean_category = category.strip().lower() or "trending"
        if clean_category not in TREND_CATEGORIES:
            raise RuntimeError(
                "Unknown trend category. Use trending, news, sport, or entertainment."
            )

        api = await self._get_api()
        trends: list[XTrend] = []
        try:
            async with aclosing(api.trends(clean_category, limit=limit)) as stream:
                async for trend in stream:
                    parsed = _to_trend(trend)
                    if parsed.name:
                        trends.append(parsed)
                    if len(trends) >= limit:
                        break
        except Exception as exc:
            raise RuntimeError(f"X trends failed: {exc}") from exc
        return trends

    async def trend_fallback_search(
        self,
        category: str = "trending",
        limit: int | None = None,
    ) -> tuple[str, list[XSearchResult]]:
        clean_category = category.strip().lower() or "trending"
        if clean_category not in TREND_CATEGORIES:
            raise RuntimeError(
                "Unknown trend category. Use trending, news, sport, or entertainment."
            )

        for query in TREND_FALLBACK_QUERIES[clean_category]:
            search_query = default_english_query(query)
            search_query = recent_search_query(search_query, 24 * 60)
            results = await self.search(search_query, limit=limit, product="Latest")
            if results:
                return search_query, results
        return recent_search_query(
            default_english_query(TREND_FALLBACK_QUERIES[clean_category][-1]),
            24 * 60,
        ), []

    async def _get_api(self) -> Any:
        if self._api is None:
            try:
                from twscrape import API
            except ImportError as exc:
                raise RuntimeError(
                    "twscrape is not installed. Run `pip install -e .` in the venv."
                ) from exc

            db_path = Path(self.settings.x_accounts_db)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._api = API(str(db_path), raise_when_no_account=True)
            if self.settings.x_cookie:
                clean_name = normalize_account_name(self.settings.x_account_name)
                existing = await self._api.pool.get_account(clean_name)
                if existing is None:
                    await self._api.pool.add_account_cookies(
                        clean_name,
                        self.settings.x_cookie,
                    )
        return self._api


def format_x_results(results: list[XSearchResult]) -> str:
    if not results:
        return "No X posts found."

    lines: list[str] = []
    for index, result in enumerate(results, start=1):
        author = f"@{result.username}"
        name = result.display_name.strip()
        if name and name.lower() != result.username.lower():
            author = f"{name} ({author})"
        metrics = (
            f"{result.like_count} likes, {result.retweet_count} reposts, "
            f"{result.quote_count} quotes, {result.reply_count} replies"
        )
        if result.view_count is not None:
            metrics = f"{metrics}, {result.view_count} views"
        if result.author_followers_count is not None:
            metrics = f"{metrics}, {result.author_followers_count} author followers"
        lines.append(
            f"{index}. {author} - {result.created_at}\n"
            f"{_compact_text(result.text, 420)}\n"
            f"{metrics}\n"
            f"{result.url}"
        )
    return "\n\n".join(lines)


def summarize_x_context(results: list[XSearchResult], max_items: int = 6) -> str:
    lines: list[str] = []
    for index, result in enumerate(results[:max_items], start=1):
        lines.append(
            f"{index}. @{result.username} ({result.created_at}, "
            f"{result.like_count} likes): {_compact_text(result.text, 320)}"
        )
    return "\n".join(lines)


def summarize_reply_target_context(results: list[XSearchResult], max_items: int = 5) -> str:
    lines: list[str] = []
    for index, result in enumerate(results[:max_items], start=1):
        lines.append(
            f"{index}. URL: {result.url}\n"
            f"Author: @{result.username}\n"
            f"Language: {result.language or 'unknown'}; "
            f"country metadata: {result.place_country_code or 'not provided'}\n"
            f"Metrics: {result.like_count} likes, {result.retweet_count} reposts, "
            f"{result.quote_count} quotes, {result.reply_count} replies, "
            f"{_format_views(result)}, {_format_author_reach(result)}, "
            f"{result.view_velocity_score:.1f} views/min, "
            f"weighted engagement velocity {result.velocity_score:.2f}/min, "
            f"engagement rate {result.engagement_rate * 100:.2f}%, "
            f"conversation velocity {result.conversation_velocity_score:.2f}/min, "
            f"audience breakout {result.breakout_ratio:.2f}x, "
            f"viral score {result.viral_score:.1f}/100, "
            f"thread availability {result.thread_availability_score:.1f}/100, "
            f"{result.views_per_reply:.1f} lifetime views/reply, "
            f"{result.recent_views_per_reply:.1f} recent views/min/reply, "
            f"saturation penalty {result.reply_saturation_penalty:.1f}, "
            f"reply opportunity {result.reply_opportunity_score:.1f}/100\n"
            f"Momentum basis: {_format_momentum_basis(result)}\n"
            f"Age: {_format_age_minutes(result)}\n"
            f"Post: {_compact_text(result.text, 360)}"
        )
    return "\n\n".join(lines)


def summarize_trends_context(trends: list[XTrend], max_items: int = 10) -> str:
    lines: list[str] = []
    for index, trend in enumerate(trends[:max_items], start=1):
        detail = f" - {trend.description}" if trend.description else ""
        rank = f"rank {trend.rank}" if trend.rank else f"item {index}"
        lines.append(f"{index}. {trend.name} ({rank}){detail}")
    return "\n".join(lines)


def _to_search_result(tweet: Any) -> XSearchResult:
    user = getattr(tweet, "user", None)
    username = str(getattr(user, "username", "") or "")
    tweet_id = int(getattr(tweet, "id", 0) or 0)
    tweet_date = getattr(tweet, "date", None)
    created_at_timestamp = _to_timestamp(tweet_date)
    return XSearchResult(
        id=tweet_id,
        username=username,
        display_name=str(getattr(user, "displayname", "") or ""),
        text=str(getattr(tweet, "rawContent", "") or getattr(tweet, "text", "") or ""),
        created_at=str(tweet_date or ""),
        created_at_timestamp=created_at_timestamp,
        url=_tweet_url(username, tweet_id),
        language=str(getattr(tweet, "lang", "") or ""),
        place_country_code=str(
            getattr(getattr(tweet, "place", None), "countryCode", "") or ""
        ).upper(),
        is_reply=getattr(tweet, "inReplyToTweetId", None) is not None,
        is_retweet=getattr(tweet, "retweetedTweet", None) is not None,
        reply_count=int(getattr(tweet, "replyCount", 0) or 0),
        retweet_count=int(getattr(tweet, "retweetCount", 0) or 0),
        quote_count=int(getattr(tweet, "quoteCount", 0) or 0),
        like_count=int(getattr(tweet, "likeCount", 0) or 0),
        view_count=_optional_int(getattr(tweet, "viewCount", None)),
        author_followers_count=_optional_int(getattr(user, "followersCount", None)),
        author_verified=bool(
            getattr(user, "verified", False)
            or getattr(user, "blue", False)
        ),
        media_urls=_media_urls(tweet),
        author_id=_optional_int(getattr(user, "id", None)),
        conversation_id=_optional_int(getattr(tweet, "conversationId", None)),
        in_reply_to_tweet_id=_optional_int(getattr(tweet, "inReplyToTweetId", None)),
    )


def _to_trend(trend: Any) -> XTrend:
    metadata = getattr(trend, "trend_metadata", None)
    description = str(getattr(metadata, "meta_description", "") or "")
    return XTrend(
        name=str(getattr(trend, "name", "") or ""),
        rank=str(getattr(trend, "rank", "") or ""),
        description=description,
    )


def _tweet_url(username: str, tweet_id: int) -> str:
    if username and tweet_id:
        return f"https://x.com/{username}/status/{tweet_id}"
    if tweet_id:
        return f"https://x.com/i/web/status/{tweet_id}"
    return "https://x.com"


def _compact_text(text: str, limit: int) -> str:
    one_line = " ".join(text.split())
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 3].rstrip() + "..."


def extract_tweet_id(text: str) -> int | None:
    match = re.search(r"(?:x|twitter)\.com/[^/\s]+/status/(\d+)", text)
    if match:
        return int(match.group(1))
    match = re.search(r"(?:x|twitter)\.com/i/web/status/(\d+)", text)
    if match:
        return int(match.group(1))
    return None


def default_english_query(query: str) -> str:
    clean_query = " ".join(query.strip().split())
    if not clean_query:
        raise RuntimeError("Search query was empty.")
    if "lang:" in clean_query.lower():
        return clean_query
    return f"{clean_query} lang:en"


def recent_search_query(query: str, since_minutes: int) -> str:
    clean_query = default_english_query(query)
    if re.search(r"\bsince_time:\d+\b", clean_query, flags=re.IGNORECASE):
        return clean_query
    since_at = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
    return f"{clean_query} since_time:{int(since_at.timestamp())}"


def parse_reply_target_languages(
    value: str | list[str] | tuple[str, ...] | None,
    *,
    default: str = "en,ja",
    max_languages: int = MAX_REPLY_TARGET_LANGUAGES,
) -> list[str]:
    if isinstance(value, (list, tuple)):
        parts = [str(item) for item in value]
    else:
        parts = re.split(r"[\s,;]+", str(value or ""))
    normalized: list[str] = []
    for part in parts:
        language = part.strip().lower()
        if language == "zh":
            language = "zh-cn"
        if language and language in SUPPORTED_REPLY_TARGET_LANGUAGES:
            if language not in normalized:
                normalized.append(language)
        if len(normalized) >= max_languages:
            break
    if normalized:
        return normalized
    if str(value or "").strip() == str(default or "").strip():
        return ["en", "ja"]
    return parse_reply_target_languages(
        default,
        default="en,ja",
        max_languages=max_languages,
    )


def query_for_language(query: str, language: str) -> str:
    clean_query = " ".join(str(query or "").strip().split())
    if not clean_query:
        raise RuntimeError("Search query was empty.")
    if re.search(r"\blang:[\w-]+\b", clean_query, flags=re.IGNORECASE):
        return clean_query
    normalized = parse_reply_target_languages(language, default="en", max_languages=1)[0]
    return f"{clean_query} lang:{normalized}"


def rank_fast_growing_posts(
    results: list[XSearchResult],
    max_items: int = 5,
    max_age_minutes: int = 30,
    min_engagement_score: float = MIN_REPLY_TARGET_ENGAGEMENT_SCORE,
    min_velocity_score: float = MIN_REPLY_TARGET_VELOCITY_SCORE,
    min_view_velocity_score: float = MIN_REPLY_TARGET_VIEW_VELOCITY_SCORE,
    min_view_count: int = MIN_REPLY_TARGET_VIEW_COUNT,
    min_author_followers: int = 0,
) -> list[XSearchResult]:
    now = datetime.now(timezone.utc).timestamp()
    ranked: list[XSearchResult] = []
    for result in results:
        if result.is_reply or result.is_retweet:
            continue
        age_minutes = _age_minutes(result, now)
        if age_minutes is None or age_minutes > max_age_minutes:
            continue
        engagement = _engagement_score(result)
        age_denominator = max(age_minutes, 1.0)
        lifetime_velocity = engagement / age_denominator
        lifetime_view_velocity = (
            result.view_count / age_denominator
            if result.view_count is not None
            else 0.0
        )
        has_recent_window = result.momentum_observation_count >= 2
        velocity = (
            result.recent_engagement_velocity_score
            if has_recent_window
            else lifetime_velocity
        )
        view_velocity = (
            result.recent_view_velocity_score
            if has_recent_window and result.view_count is not None
            else lifetime_view_velocity
        )
        engagement_rate = (
            engagement / result.view_count
            if result.view_count is not None and result.view_count > 0
            else 0.0
        )
        lifetime_conversation_velocity = (
            result.reply_count + (result.quote_count * 2)
        ) / age_denominator
        conversation_velocity = (
            result.recent_conversation_velocity_score
            if has_recent_window
            else lifetime_conversation_velocity
        )
        reply_velocity = (
            result.recent_reply_velocity_score
            if has_recent_window
            else result.reply_count / age_denominator
        )
        breakout_ratio = (
            result.view_count / result.author_followers_count
            if result.view_count is not None
            and result.author_followers_count is not None
            and result.author_followers_count > 0
            else 0.0
        )
        # A post can be breaking out through views before likes/replies catch up.
        # Require either engagement momentum or view momentum instead of treating
        # follower count as proof that the post itself is viral.
        if (
            engagement < min_engagement_score
            and view_velocity < min_view_velocity_score
        ):
            continue
        if velocity < min_velocity_score and view_velocity < min_view_velocity_score:
            continue
        if result.view_count is not None and result.view_count < min_view_count:
            continue
        if not _has_reply_target_signal(result):
            continue
        viral_score = _viral_score(
            view_velocity=view_velocity,
            engagement_velocity=velocity,
            engagement_rate=engagement_rate,
            conversation_velocity=conversation_velocity,
            breakout_ratio=breakout_ratio,
            author_followers=result.author_followers_count,
            preferred_author_followers=min_author_followers,
            acceleration=result.momentum_acceleration if has_recent_window else 0.0,
        )
        competing_replies = max(result.reply_count, 0) + 1
        views_per_reply = (
            result.view_count / competing_replies
            if result.view_count is not None
            else 0.0
        )
        recent_views_per_reply = view_velocity / competing_replies
        thread_availability_score = _thread_availability_score(
            view_velocity=view_velocity,
            view_count=result.view_count,
            reply_count=result.reply_count,
            reply_velocity=reply_velocity,
        )
        saturation_penalty = _reply_saturation_penalty(
            view_velocity=view_velocity,
            reply_count=result.reply_count,
            reply_velocity=reply_velocity,
        )
        reply_opportunity_score = _reply_opportunity_score(
            viral_score=viral_score,
            thread_availability_score=thread_availability_score,
            saturation_penalty=saturation_penalty,
            age_minutes=age_minutes,
            max_age_minutes=max_age_minutes,
        )
        ranked.append(
            replace(
                result,
                velocity_score=velocity,
                view_velocity_score=view_velocity,
                engagement_rate=engagement_rate,
                conversation_velocity_score=conversation_velocity,
                breakout_ratio=breakout_ratio,
                viral_score=viral_score,
                reply_opportunity_score=reply_opportunity_score,
                thread_availability_score=thread_availability_score,
                reply_saturation_penalty=saturation_penalty,
                views_per_reply=views_per_reply,
                recent_views_per_reply=recent_views_per_reply,
            )
        )

    ranked = _apply_language_opportunity_percentiles(ranked)
    ordered = sorted(
        ranked,
        key=lambda result: (
            result.reply_opportunity_score,
            result.thread_availability_score,
            result.viral_score,
            result.view_velocity_score,
            result.velocity_score,
            result.created_at_timestamp or 0,
        ),
        reverse=True,
    )
    return _select_language_balanced(ordered, max_items=max_items)


def _engagement_score(result: XSearchResult) -> float:
    return (
        result.like_count
        + (result.retweet_count * 3)
        + (result.quote_count * 5)
        + result.reply_count
    )


def _has_reply_target_signal(result: XSearchResult) -> bool:
    return (
        result.like_count >= 5
        or result.reply_count >= 1
        or result.retweet_count >= 2
        or result.quote_count >= 1
    )


def _age_minutes(result: XSearchResult, now_timestamp: float | None = None) -> float | None:
    if result.created_at_timestamp is None:
        return None
    now = now_timestamp or datetime.now(timezone.utc).timestamp()
    return max((now - result.created_at_timestamp) / 60, 0.0)


def _format_age_minutes(result: XSearchResult) -> str:
    age = _age_minutes(result)
    if age is None:
        return "unknown"
    if age < 1:
        return "<1m"
    return f"{int(age)}m"


def _format_views(result: XSearchResult) -> str:
    if result.view_count is None:
        return "views unknown"
    return f"{result.view_count} views"


def _format_author_reach(result: XSearchResult) -> str:
    if result.author_followers_count is None:
        return "author followers unknown"
    verified = ", verified" if result.author_verified else ""
    return f"{result.author_followers_count} author followers{verified}"


def _viral_score(
    *,
    view_velocity: float,
    engagement_velocity: float,
    engagement_rate: float,
    conversation_velocity: float,
    breakout_ratio: float,
    author_followers: int | None,
    preferred_author_followers: int,
    acceleration: float,
) -> float:
    """Score current momentum, not accumulated popularity or account size alone."""
    view_component = 25 * _log_ratio(view_velocity, 1_000)
    engagement_component = 22 * _log_ratio(engagement_velocity, 50)
    engagement_rate_component = 15 * min(max(engagement_rate, 0.0) / 0.05, 1.0)
    # Active discussion confirms interest, but replies are also competitors for
    # visibility. Keep this signal capped and let thread availability decide
    # whether the conversation is still worth entering.
    conversation_component = 8 * _log_ratio(conversation_velocity, 10)
    breakout_component = 10 * min(max(breakout_ratio, 0.0) / 0.25, 1.0)
    reach_component = 0.0
    if preferred_author_followers > 0 and author_followers is not None:
        reach_component = 4 * min(
            max(author_followers, 0) / preferred_author_followers,
            1.0,
        )
    acceleration_component = 16 * min(max(acceleration, 0.0), 1.0)
    return min(
        view_component
        + engagement_component
        + engagement_rate_component
        + conversation_component
        + breakout_component
        + reach_component
        + acceleration_component,
        100.0,
    )


def _reply_opportunity_score(
    *,
    viral_score: float,
    thread_availability_score: float,
    saturation_penalty: float,
    age_minutes: float,
    max_age_minutes: int,
) -> float:
    recency = 1.0 - min(max(age_minutes, 0.0) / max(max_age_minutes, 1), 1.0)
    return min(
        max(
            (viral_score * 0.65)
            + (thread_availability_score * 0.25)
            + (recency * 10.0)
            - saturation_penalty,
            0.0,
        ),
        100.0,
    )


def _thread_availability_score(
    *,
    view_velocity: float,
    view_count: int | None,
    reply_count: int,
    reply_velocity: float,
) -> float:
    """Estimate remaining audience per reply competing in the root thread."""
    competitors = max(reply_count, 0) + 1
    recent_audience_per_reply = max(view_velocity, 0.0) / competitors
    lifetime_audience_per_reply = (
        max(view_count, 0) / competitors
        if view_count is not None
        else 0.0
    )
    recent_audience_component = 45 * _log_ratio(recent_audience_per_reply, 250)
    lifetime_audience_component = 25 * _log_ratio(
        lifetime_audience_per_reply,
        5_000,
    )
    open_thread_component = 20 * (1.0 - _log_ratio(max(reply_count, 0), 300))
    low_pressure_component = 10 * (1.0 - _log_ratio(max(reply_velocity, 0.0), 5))
    return min(
        max(
            recent_audience_component
            + lifetime_audience_component
            + open_thread_component
            + low_pressure_component,
            0.0,
        ),
        100.0,
    )


def _reply_saturation_penalty(
    *,
    view_velocity: float,
    reply_count: int,
    reply_velocity: float,
) -> float:
    """Soft penalty: crowded threads survive only while distribution is exceptional."""
    reply_load = _log_ratio(max(reply_count - 30, 0), 970)
    reply_pressure = _log_ratio(max(reply_velocity, 0.0), 10)
    raw_penalty = (24 * reply_load) + (6 * reply_pressure)
    ongoing_distribution = _log_ratio(max(view_velocity, 0.0), 1_000)
    return raw_penalty * (1.0 - (0.5 * ongoing_distribution))


def _apply_language_opportunity_percentiles(
    results: list[XSearchResult],
) -> list[XSearchResult]:
    groups: dict[str, list[XSearchResult]] = {}
    for result in results:
        language = result.language.strip().lower() or "unknown"
        groups.setdefault(language, []).append(result)

    adjusted: list[XSearchResult] = []
    for group in groups.values():
        ordered = sorted(
            group,
            key=lambda item: item.reply_opportunity_score,
            reverse=True,
        )
        count = len(ordered)
        for index, result in enumerate(ordered):
            if count == 1:
                percentile = min(max(result.reply_opportunity_score / 100.0, 0.0), 1.0)
            else:
                percentile = 1.0 - (index / (count - 1))
            # Keep absolute quality dominant while allowing the strongest post in
            # each language to compete against larger-language pools.
            blended = (result.reply_opportunity_score * 0.85) + (percentile * 15.0)
            adjusted.append(
                replace(
                    result,
                    reply_opportunity_score=min(max(blended, 0.0), 100.0),
                    language_opportunity_percentile=percentile,
                )
            )
    return adjusted


def _select_language_balanced(
    ordered: list[XSearchResult],
    *,
    max_items: int,
) -> list[XSearchResult]:
    if max_items <= 0:
        return []
    known_languages: list[str] = []
    for result in ordered:
        language = result.language.strip().lower()
        if (
            language in SUPPORTED_REPLY_TARGET_LANGUAGES
            and language not in known_languages
        ):
            known_languages.append(language)

    selected: list[XSearchResult] = []
    selected_ids: set[tuple[int, str]] = set()
    for language in known_languages:
        candidate = next(
            (
                result
                for result in ordered
                if result.language.strip().lower() == language
            ),
            None,
        )
        if candidate is None:
            continue
        selected.append(candidate)
        selected_ids.add((candidate.id, candidate.url))
        if len(selected) >= max_items:
            return sorted(
                selected,
                key=lambda item: item.reply_opportunity_score,
                reverse=True,
            )

    for result in ordered:
        identity = (result.id, result.url)
        if identity in selected_ids:
            continue
        selected.append(result)
        selected_ids.add(identity)
        if len(selected) >= max_items:
            break
    return sorted(
        selected,
        key=lambda item: item.reply_opportunity_score,
        reverse=True,
    )


def _log_ratio(value: float, reference: float) -> float:
    if value <= 0 or reference <= 0:
        return 0.0
    return min(math.log1p(value) / math.log1p(reference), 1.0)


def _format_momentum_basis(result: XSearchResult) -> str:
    if result.momentum_observation_count < 2:
        return "first observation; lifetime average since posting"
    return (
        f"snapshot delta ({result.momentum_observation_count} observations), "
        f"recent {result.recent_view_velocity_score:.1f} views/min, "
        f"recent engagement {result.recent_engagement_velocity_score:.2f}/min, "
        f"recent replies {result.recent_reply_velocity_score:.2f}/min, "
        f"acceleration {result.momentum_acceleration:.2f}"
    )


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_timestamp(value: Any) -> int | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.astimezone(timezone.utc).timestamp())


def _media_urls(tweet: Any) -> list[str]:
    media = getattr(tweet, "media", None)
    if media is None:
        return []

    urls: list[str] = []
    for photo in getattr(media, "photos", []) or []:
        url = str(getattr(photo, "url", "") or "").strip()
        if url:
            urls.append(url)

    for video in getattr(media, "videos", []) or []:
        url = str(getattr(video, "thumbnailUrl", "") or "").strip()
        if url:
            urls.append(url)

    for animated in getattr(media, "animated", []) or []:
        url = str(getattr(animated, "thumbnailUrl", "") or "").strip()
        if url:
            urls.append(url)

    return urls


def normalize_account_name(account_name: str) -> str:
    clean_name = account_name.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,48}", clean_name):
        raise RuntimeError(
            "X account name must be 1-48 characters: letters, numbers, underscore, dot, or dash."
        )
    return clean_name
