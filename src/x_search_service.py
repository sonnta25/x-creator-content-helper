from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Any

from src.config import Settings
from src.models import XSearchResult, XTrend


TREND_CATEGORIES = {"trending", "news", "sport", "entertainment"}

TREND_FALLBACK_QUERIES = {
    "trending": ["openai", "AI", "news", "entertainment"],
    "news": ["news", "politics", "business", "technology"],
    "sport": ["sports", "NBA", "NFL", "soccer"],
    "entertainment": ["entertainment", "movies", "music", "Netflix"],
}

REPLY_TARGET_SEARCH_LIMIT = 40
MIN_REPLY_TARGET_ENGAGEMENT_SCORE = 10.0
MIN_REPLY_TARGET_VELOCITY_SCORE = 1.0
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
            async for tweet in api.search(
                clean_query,
                limit=search_limit,
                kv={"product": product or self.settings.x_search_product},
            ):
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

    async def trends(self, category: str = "trending", limit: int = 10) -> list[XTrend]:
        clean_category = category.strip().lower() or "trending"
        if clean_category not in TREND_CATEGORIES:
            raise RuntimeError(
                "Unknown trend category. Use trending, news, sport, or entertainment."
            )

        api = await self._get_api()
        trends: list[XTrend] = []
        try:
            async for trend in api.trends(clean_category, limit=limit):
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
            f"Metrics: {result.like_count} likes, {result.retweet_count} reposts, "
            f"{result.quote_count} quotes, {result.reply_count} replies, "
            f"{_format_views(result)}, {_format_author_reach(result)}, "
            f"engagement velocity {result.velocity_score:.2f}/min\n"
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


def rank_fast_growing_posts(
    results: list[XSearchResult],
    max_items: int = 5,
    max_age_minutes: int = 30,
    min_engagement_score: float = MIN_REPLY_TARGET_ENGAGEMENT_SCORE,
    min_velocity_score: float = MIN_REPLY_TARGET_VELOCITY_SCORE,
    min_view_count: int = MIN_REPLY_TARGET_VIEW_COUNT,
    min_author_followers: int = 0,
) -> list[XSearchResult]:
    now = datetime.now(timezone.utc).timestamp()
    ranked: list[XSearchResult] = []
    for result in results:
        age_minutes = _age_minutes(result, now)
        if age_minutes is None or age_minutes > max_age_minutes:
            continue
        engagement = _engagement_score(result)
        velocity = engagement / max(age_minutes, 5.0)
        if engagement < min_engagement_score:
            continue
        if velocity < min_velocity_score:
            continue
        if result.view_count is not None and result.view_count < min_view_count:
            continue
        if min_author_followers > 0 and (
            result.author_followers_count is None
            or result.author_followers_count < min_author_followers
        ):
            continue
        if not _has_reply_target_signal(result):
            continue
        ranked.append(replace(result, velocity_score=velocity))

    return sorted(
        ranked,
        key=lambda result: (
            _view_velocity(result, now),
            result.author_followers_count or 0,
            result.velocity_score,
            _engagement_score(result),
            result.created_at_timestamp or 0,
        ),
        reverse=True,
    )[:max_items]


def _engagement_score(result: XSearchResult) -> float:
    return (
        result.like_count
        + (result.retweet_count * 3)
        + (result.quote_count * 3)
        + (result.reply_count * 2)
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


def _view_velocity(result: XSearchResult, now_timestamp: float) -> float:
    if result.view_count is None:
        return 0.0
    age_minutes = _age_minutes(result, now_timestamp)
    if age_minutes is None:
        return 0.0
    return result.view_count / max(age_minutes, 5.0)


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
