from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReplyTargetDraft:
    url: str
    target: str
    reply: str
    source_summary_vi: str = ""
    reply_translation_vi: str = ""


@dataclass(frozen=True)
class ReplyRevision:
    reply: str
    reply_translation_vi: str = ""


@dataclass(frozen=True)
class ImageAttachment:
    name: str
    mime_type: str
    data: bytes


@dataclass(frozen=True)
class XSearchResult:
    id: int
    username: str
    display_name: str
    text: str
    created_at: str
    url: str
    language: str = ""
    place_country_code: str = ""
    is_reply: bool = False
    is_retweet: bool = False
    created_at_timestamp: int | None = None
    reply_count: int = 0
    retweet_count: int = 0
    quote_count: int = 0
    like_count: int = 0
    view_count: int | None = None
    author_followers_count: int | None = None
    author_following_count: int | None = None
    author_statuses_count: int | None = None
    author_verified: bool = False
    author_blue_verified: bool = False
    author_blue_type: str = ""
    author_description: str = ""
    author_location: str = ""
    author_protected: bool = False
    velocity_score: float = 0.0
    view_velocity_score: float = 0.0
    engagement_rate: float = 0.0
    conversation_velocity_score: float = 0.0
    breakout_ratio: float = 0.0
    viral_score: float = 0.0
    reply_opportunity_score: float = 0.0
    thread_availability_score: float = 0.0
    reply_saturation_penalty: float = 0.0
    views_per_reply: float = 0.0
    recent_views_per_reply: float = 0.0
    language_opportunity_percentile: float = 0.0
    recent_view_velocity_score: float = 0.0
    recent_engagement_velocity_score: float = 0.0
    recent_conversation_velocity_score: float = 0.0
    recent_reply_velocity_score: float = 0.0
    momentum_acceleration: float = 0.0
    momentum_observation_count: int = 0
    media_urls: list[str] | None = None
    has_video: bool = False
    media_descriptions: list[str] | None = None
    video_context_quality: str = ""
    visual_frame_names: list[str] | None = None
    author_id: int | None = None
    conversation_id: int | None = None
    in_reply_to_tweet_id: int | None = None
    top_reply_like_count: int = 0
    root_author_has_replied: bool = False
    audience_affinity_score: float = 0.0
    relationship_score: float = 0.0
    rankability_score: float = 0.0
    premium_audience_score: float = 0.0
    verified_audience_proxy: float = 0.0
    verified_replier_ratio: float = 0.0
    monetization_safety_score: float = 100.0
    monetization_risk_level: str = "green"
    monetization_risk_reasons: tuple[str, ...] = ()
    watched_author: bool = False
    goal_score: float = 0.0
    author_tier: str = "unknown"
    discovery_daypart: str = "global_offpeak"
    daypart_fit_score: float = 0.0
    candidate_age_bucket: str = "unknown"
    distribution_stage: str = "unknown"


@dataclass(frozen=True)
class FollowCandidate:
    user_id: int
    username: str
    display_name: str
    description: str
    location: str
    followers: int
    following: int
    statuses: int
    profile_url: str
    source_post_url: str
    source_post_text: str
    source_post_created_at: str
    ratio: float
    score: float
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class XTrend:
    name: str
    rank: str
    description: str = ""
