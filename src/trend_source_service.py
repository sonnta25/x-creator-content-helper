from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
import re
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

import httpx

from src.config import Settings
from src.models import TrendSignal
from src.x_search_service import TREND_CATEGORIES, XSearchService


DEFAULT_GOOGLE_NEWS_RSS_URLS = {
    "trending": "https://news.google.com/rss/topstories?hl=en-US&gl=US&ceid=US:en",
    "news": "https://news.google.com/rss/headlines/section/topic/NATION?hl=en-US&gl=US&ceid=US:en",
    "sport": "https://news.google.com/rss/headlines/section/topic/SPORTS?hl=en-US&gl=US&ceid=US:en",
    "entertainment": (
        "https://news.google.com/rss/headlines/section/topic/ENTERTAINMENT?"
        "hl=en-US&gl=US&ceid=US:en"
    ),
}
SOURCE_WEIGHTS = {
    "Niche Google News RSS": 140.0,
    "Google Trends": 130.0,
    "Google News RSS": 100.0,
    "Custom RSS": 95.0,
    "X Trends": 90.0,
}


class TrendSourceService:
    def __init__(self, settings: Settings, x_search: XSearchService) -> None:
        self.settings = settings
        self.x_search = x_search
        self._http_client: httpx.AsyncClient | None = None

    async def aclose(self) -> None:
        if self._http_client is None:
            return
        await self._http_client.aclose()
        self._http_client = None

    async def collect(
        self,
        category: str,
        limit_per_source: int = 10,
    ) -> tuple[list[TrendSignal], list[str]]:
        clean_category = normalize_trend_category(category)
        sources = _configured_sources(self.settings.trend_sources)
        jobs: list[tuple[str, object]] = []
        if "x" in sources:
            jobs.append(
                ("X trends", self._x_trends(clean_category, limit=limit_per_source))
            )

        if "google_trends" in sources:
            jobs.append(
                (
                    "Google Trends RSS",
                    self._rss_url_signals(
                        google_trends_rss_url(self.settings.google_trends_geo),
                        source="Google Trends",
                        category=clean_category,
                        limit=limit_per_source,
                    ),
                )
            )

        if "rss" in sources:
            rss_feeds = _rss_feeds_for_category(
                self.settings.trend_rss_urls,
                clean_category,
                geo=self.settings.google_trends_geo,
                language=self.settings.trend_language,
            )
            for label, url in rss_feeds:
                jobs.append(
                    (
                        label,
                        self._rss_url_signals(
                            url,
                            source=label,
                            category=clean_category,
                            limit=limit_per_source,
                        ),
                    )
                )

        signals: list[TrendSignal] = []
        errors: list[str] = []
        responses = await asyncio.gather(
            *(job for _label, job in jobs),
            return_exceptions=True,
        )
        for (label, _job), response in zip(jobs, responses):
            if isinstance(response, Exception):
                errors.append(f"{label}: {response}")
            else:
                signals.extend(response)
        return rank_trend_signals(signals), errors

    async def collect_niche(
        self,
        niche: str,
        limit: int = 12,
    ) -> tuple[list[TrendSignal], list[str]]:
        """Find current news topics specifically related to the creator niche."""
        clean_niche = " ".join(niche.split())
        if not clean_niche:
            return [], ["Creator niche is empty."]

        sources = _configured_sources(self.settings.trend_sources)
        if not ({"google_trends", "rss"} & sources):
            return [], ["Niche trend search needs google_trends or rss in TREND_SOURCES."]

        try:
            signals = await self._rss_url_signals(
                google_news_search_rss_url(
                    niche_search_query(clean_niche),
                    self.settings.google_trends_geo,
                ),
                source="Niche Google News RSS",
                category="niche",
                limit=limit,
            )
        except Exception as exc:
            return [], [f"Niche Google News RSS: {exc}"]
        return rank_trend_signals(signals), []

    async def _x_trends(self, category: str, limit: int) -> list[TrendSignal]:
        trends = await self.x_search.trends(category, limit=limit)
        return [
            TrendSignal(
                title=trend.name,
                source="X Trends",
                category=category,
                description=trend.description,
                score=SOURCE_WEIGHTS["X Trends"] + max(0, limit - index),
            )
            for index, trend in enumerate(trends)
            if trend.name
        ]

    async def _rss_url_signals(
        self,
        url: str,
        *,
        source: str,
        category: str,
        limit: int,
    ) -> list[TrendSignal]:
        xml_text = await self._fetch_text(url)
        items = parse_rss_trend_signals(xml_text, source=source, category=category, limit=limit)
        return [
            TrendSignal(
                title=item.title,
                source=item.source,
                category=item.category,
                url=item.url,
                description=item.description,
                published_at=item.published_at,
                score=SOURCE_WEIGHTS.get(source, SOURCE_WEIGHTS["Custom RSS"])
                + max(0, limit - index),
            )
            for index, item in enumerate(items)
        ]

    async def _fetch_text(self, url: str) -> str:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=15,
                follow_redirects=True,
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
                headers={"User-Agent": "x-telegram-content-bot/0.1"},
            )
        response = await self._http_client.get(url)
        response.raise_for_status()
        return response.text


def normalize_trend_category(category: str) -> str:
    clean_category = category.strip().lower() or "trending"
    if clean_category not in TREND_CATEGORIES:
        raise RuntimeError("Unknown trend category. Use trending, news, sport, or entertainment.")
    return clean_category


def google_trends_rss_url(geo: str) -> str:
    clean_geo = re.sub(r"[^A-Za-z0-9_-]", "", geo.strip() or "US")
    return f"https://trends.google.com/trending/rss?geo={clean_geo}"


def google_news_search_rss_url(query: str, geo: str) -> str:
    clean_query = " ".join(query.split())
    clean_geo = re.sub(r"[^A-Za-z0-9_-]", "", geo.strip() or "US")
    language = "vi" if clean_geo.upper() == "VN" else "en"
    locale = "vi" if language == "vi" else "en-US"
    return (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(clean_query)}&hl={locale}&gl={clean_geo}&ceid={clean_geo}:{language}"
    )


def google_news_category_rss_url(
    category: str,
    geo: str,
    language: str,
) -> str:
    clean_geo = re.sub(r"[^A-Za-z0-9_-]", "", geo.strip() or "US").upper()
    clean_language = re.sub(r"[^A-Za-z-]", "", language.strip() or "en").lower()
    locale = "vi" if clean_language == "vi" else "en-US"
    topic = {
        "trending": "topstories",
        "news": "headlines/section/topic/NATION",
        "sport": "headlines/section/topic/SPORTS",
        "entertainment": "headlines/section/topic/ENTERTAINMENT",
    }[category]
    return (
        f"https://news.google.com/rss/{topic}?"
        f"hl={locale}&gl={clean_geo}&ceid={clean_geo}:{clean_language}"
    )


def niche_search_query(niche: str) -> str:
    lanes = [
        " ".join(part.split()).strip(" ,;&")
        for part in re.split(r"\s*(?:,|;|&|\band\b)\s*", niche, flags=re.IGNORECASE)
    ]
    lanes = [lane for lane in lanes if len(lane) >= 2][:4]
    if not lanes:
        return " ".join(niche.split())
    if len(lanes) == 1:
        return f'"{lanes[0]}" when:2d'
    quoted_lanes = " OR ".join(f'"{lane}"' for lane in lanes)
    return f"({quoted_lanes}) when:2d"


def parse_rss_trend_signals(
    xml_text: str,
    *,
    source: str,
    category: str,
    limit: int = 10,
) -> list[TrendSignal]:
    root = ET.fromstring(xml_text)
    signals: list[TrendSignal] = []
    for item in root.findall(".//item"):
        title = _clean_text(_child_text(item, "title"))
        if not title:
            continue
        signals.append(
            TrendSignal(
                title=title,
                source=source,
                category=category,
                url=_clean_text(_child_text(item, "link")),
                description=_clean_description(_child_text(item, "description")),
                published_at=_clean_text(_child_text(item, "pubDate")),
            )
        )
        if len(signals) >= limit:
            break
    return signals


def summarize_trend_signals(signals: list[TrendSignal], max_items: int = 10) -> str:
    lines: list[str] = []
    for index, signal in enumerate(signals[:max_items], start=1):
        detail_parts = [signal.source]
        if signal.published_at:
            detail_parts.append(signal.published_at)
        if signal.url:
            detail_parts.append(signal.url)
        description = f" - {_compact_text(signal.description, 220)}" if signal.description else ""
        lines.append(f"{index}. {signal.title} ({'; '.join(detail_parts)}){description}")
    return "\n".join(lines)


def rank_trend_signals(signals: list[TrendSignal]) -> list[TrendSignal]:
    grouped: dict[str, list[TrendSignal]] = {}
    for signal in signals:
        key = _trend_key(signal.title)
        if not key:
            continue
        grouped.setdefault(key, []).append(signal)
    ranked: list[TrendSignal] = []
    now = datetime.now(timezone.utc)
    for group in grouped.values():
        best = max(group, key=lambda item: item.score)
        confirmations = len({item.source for item in group})
        recency_bonus = _published_recency_bonus(best.published_at, now)
        ranked.append(
            replace(
                best,
                score=best.score + min(30.0, (confirmations - 1) * 12.0) + recency_bonus,
            )
        )
    return sorted(ranked, key=lambda item: item.score, reverse=True)


def _configured_sources(raw_sources: str) -> set[str]:
    aliases = {
        "google": "google_trends",
        "googletrend": "google_trends",
        "googletrends": "google_trends",
        "google_trends": "google_trends",
        "rss": "rss",
        "x": "x",
        "twitter": "x",
    }
    sources = {
        aliases[source.strip().lower()]
        for source in re.split(r"[,;\s]+", raw_sources)
        if source.strip().lower() in aliases
    }
    return sources or {"x", "google_trends", "rss"}


def _rss_feeds_for_category(
    raw_urls: str,
    category: str,
    *,
    geo: str = "US",
    language: str = "en",
) -> list[tuple[str, str]]:
    feeds = [
        (
            "Google News RSS",
            google_news_category_rss_url(category, geo, language),
        )
    ]
    feeds.extend(_custom_rss_feeds(raw_urls))
    return feeds


def _custom_rss_feeds(raw_urls: str) -> list[tuple[str, str]]:
    feeds: list[tuple[str, str]] = []
    for chunk in re.split(r"[\n;]+", raw_urls):
        clean = chunk.strip()
        if not clean:
            continue
        label = "Custom RSS"
        url = clean
        if "|" in clean:
            label_part, url_part = clean.split("|", 1)
            label = label_part.strip() or label
            url = url_part.strip()
        if url.lower().startswith(("http://", "https://")):
            feeds.append((label, url))
    return feeds


def _child_text(item: ET.Element, child_name: str) -> str:
    child = item.find(child_name)
    return child.text if child is not None and child.text else ""


def _clean_description(text: str) -> str:
    clean = re.sub(r"<[^>]+>", " ", unescape(text or ""))
    return _clean_text(clean)


def _clean_text(text: str) -> str:
    return " ".join(unescape(text or "").split())


def _compact_text(text: str, limit: int) -> str:
    clean = _clean_text(text)
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _trend_key(title: str) -> str:
    clean = "".join(
        char.casefold() if char.isalnum() else " "
        for char in title
    )
    words = [
        word
        for word in clean.split()
        if len(word) > 2 or any(ord(char) > 127 for char in word)
    ]
    return " ".join(words[:8])


def _published_recency_bonus(value: str, now: datetime) -> float:
    if not value:
        return 0.0
    try:
        published = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return 0.0
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (now - published.astimezone(timezone.utc)).total_seconds() / 3600)
    return max(0.0, 18.0 * (1.0 - min(age_hours, 48.0) / 48.0))
