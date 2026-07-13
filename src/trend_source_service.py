from __future__ import annotations

from html import unescape
import re
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
    "Google Trends": 130.0,
    "Google News RSS": 100.0,
    "Custom RSS": 95.0,
    "X Trends": 90.0,
}


class TrendSourceService:
    def __init__(self, settings: Settings, x_search: XSearchService) -> None:
        self.settings = settings
        self.x_search = x_search

    async def collect(
        self,
        category: str,
        limit_per_source: int = 10,
    ) -> tuple[list[TrendSignal], list[str]]:
        clean_category = normalize_trend_category(category)
        sources = _configured_sources(self.settings.trend_sources)
        signals: list[TrendSignal] = []
        errors: list[str] = []

        if "x" in sources:
            try:
                signals.extend(await self._x_trends(clean_category, limit=limit_per_source))
            except Exception as exc:
                errors.append(f"X trends: {exc}")

        if "google_trends" in sources:
            try:
                signals.extend(
                    await self._rss_url_signals(
                        google_trends_rss_url(self.settings.google_trends_geo),
                        source="Google Trends",
                        category=clean_category,
                        limit=limit_per_source,
                    )
                )
            except Exception as exc:
                errors.append(f"Google Trends RSS: {exc}")

        if "rss" in sources:
            rss_feeds = _rss_feeds_for_category(self.settings.trend_rss_urls, clean_category)
            for label, url in rss_feeds:
                try:
                    signals.extend(
                        await self._rss_url_signals(
                            url,
                            source=label,
                            category=clean_category,
                            limit=limit_per_source,
                        )
                    )
                except Exception as exc:
                    errors.append(f"{label}: {exc}")

        return rank_trend_signals(signals), errors

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
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            response = await client.get(
                url,
                headers={"User-Agent": "x-telegram-content-bot/0.1"},
            )
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
    deduped: list[TrendSignal] = []
    seen: set[str] = set()
    for signal in sorted(signals, key=lambda item: item.score, reverse=True):
        key = _trend_key(signal.title)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(signal)
    return deduped


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


def _rss_feeds_for_category(raw_urls: str, category: str) -> list[tuple[str, str]]:
    feeds = [("Google News RSS", DEFAULT_GOOGLE_NEWS_RSS_URLS[category])]
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
    clean = re.sub(r"[^a-z0-9]+", " ", title.lower())
    words = [word for word in clean.split() if len(word) > 2]
    return " ".join(words[:8])
