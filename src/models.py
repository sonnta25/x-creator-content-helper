from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GeneratedContent:
    text: str
    image_prompt: str
    topic: str


@dataclass(frozen=True)
class TrendPostVariant:
    angle: str
    text: str
    image_prompt: str
    hashtags: list[str]
    score: str


@dataclass(frozen=True)
class ReplyTargetDraft:
    url: str
    target: str
    reason: str
    reply: str


@dataclass(frozen=True)
class XSearchResult:
    id: int
    username: str
    display_name: str
    text: str
    created_at: str
    url: str
    created_at_timestamp: int | None = None
    reply_count: int = 0
    retweet_count: int = 0
    quote_count: int = 0
    like_count: int = 0
    view_count: int | None = None
    velocity_score: float = 0.0
    media_urls: list[str] | None = None


@dataclass(frozen=True)
class XTrend:
    name: str
    rank: str
    description: str = ""


@dataclass(frozen=True)
class TrendSignal:
    title: str
    source: str
    category: str
    url: str = ""
    description: str = ""
    published_at: str = ""
    score: float = 0.0
