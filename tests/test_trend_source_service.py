import asyncio

from src.config import Settings
from src.models import XTrend
from src.trend_source_service import (
    TrendSourceService,
    google_trends_rss_url,
    parse_rss_trend_signals,
    rank_trend_signals,
    summarize_trend_signals,
)
from src.x_search_service import XSearchService


RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss>
  <channel>
    <item>
      <title>OpenAI launches a new creator tool</title>
      <link>https://example.com/openai</link>
      <description><![CDATA[<p>Creators are testing the workflow.</p>]]></description>
      <pubDate>Fri, 10 Jul 2026 12:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


def test_google_trends_rss_url_sanitizes_geo() -> None:
    assert google_trends_rss_url("US") == "https://trends.google.com/trending/rss?geo=US"
    assert google_trends_rss_url("US<script>") == (
        "https://trends.google.com/trending/rss?geo=USscript"
    )


def test_parse_rss_trend_signals() -> None:
    signals = parse_rss_trend_signals(
        RSS_SAMPLE,
        source="Google News RSS",
        category="news",
    )

    assert len(signals) == 1
    assert signals[0].title == "OpenAI launches a new creator tool"
    assert signals[0].url == "https://example.com/openai"
    assert signals[0].description == "Creators are testing the workflow."
    assert signals[0].published_at == "Fri, 10 Jul 2026 12:00:00 GMT"


def test_rank_trend_signals_dedupes_by_title() -> None:
    signals = parse_rss_trend_signals(
        RSS_SAMPLE.replace("OpenAI launches a new creator tool", "OpenAI creator tool"),
        source="Google Trends",
        category="trending",
    ) + parse_rss_trend_signals(
        RSS_SAMPLE.replace("OpenAI launches a new creator tool", "OpenAI creator tool"),
        source="Custom RSS",
        category="trending",
    )
    signals = [
        signal.__class__(**{**signal.__dict__, "score": score})
        for signal, score in zip(signals, [130.0, 90.0], strict=True)
    ]

    ranked = rank_trend_signals(signals)

    assert len(ranked) == 1
    assert ranked[0].source == "Google Trends"


def test_summarize_trend_signals_includes_source_and_url() -> None:
    signal = parse_rss_trend_signals(
        RSS_SAMPLE,
        source="Google News RSS",
        category="news",
    )[0]

    summary = summarize_trend_signals([signal])

    assert "Google News RSS" in summary
    assert "https://example.com/openai" in summary


def test_trend_source_service_collects_x_and_rss() -> None:
    class FakeXSearch(XSearchService):
        async def trends(self, category: str = "trending", limit: int = 10):
            assert category == "news"
            assert limit == 10
            return [XTrend(name="AI regulation", rank="1", description="Policy")]

    class TestService(TrendSourceService):
        async def _fetch_text(self, url: str) -> str:
            assert url.startswith("https://")
            if "example.com" in url:
                return RSS_SAMPLE.replace(
                    "OpenAI launches a new creator tool",
                    "A creator economy platform is going viral",
                )
            return RSS_SAMPLE

    settings = Settings(
        telegram_bot_token="123:ABC",
        x_cookie="auth_token=a; ct0=b",
        trend_sources="x,rss",
        trend_rss_urls="Creator Feed|https://example.com/rss",
    )
    service = TestService(settings, FakeXSearch(settings))

    signals, errors = asyncio.run(service.collect("news"))

    assert errors == []
    assert [signal.source for signal in signals] == [
        "Google News RSS",
        "Creator Feed",
        "X Trends",
    ]
