from __future__ import annotations

from datetime import UTC, datetime

from src.follow_targets import FollowTargetHistoryStore, rank_follow_candidates
from src.models import XSearchResult


def _post(
    username: str,
    *,
    premium: bool = True,
    followers: int = 1_000,
    following: int = 900,
    language: str = "vi",
) -> XSearchResult:
    return XSearchResult(
        id=123,
        username=username,
        display_name=f"Name {username}",
        text="Một bài viết mới dành cho cộng đồng Việt Nam",
        created_at=datetime.now(UTC).isoformat(),
        created_at_timestamp=int(datetime.now(UTC).timestamp()),
        url=f"https://x.com/{username}/status/123",
        language=language,
        author_id=42,
        author_followers_count=followers,
        author_following_count=following,
        author_statuses_count=3_000,
        author_blue_verified=premium,
        author_description="Creator công nghệ tại Việt Nam",
        author_location="Ho Chi Minh City, Vietnam",
    )


def test_follow_ranking_requires_premium_and_excludes_existing_relationships() -> None:
    organization = _post("organization")
    organization = XSearchResult(
        **{
            **organization.__dict__,
            "author_blue_type": "Business",
        }
    )
    ranked = rank_follow_candidates(
        [
            _post("balanced"),
            _post("already_followed"),
            _post("not_premium", premium=False),
            _post("celebrity_ratio", followers=50_000, following=100),
            organization,
        ],
        owner_username="owner",
        followed_usernames={"already_followed"},
    )

    assert [candidate.username for candidate in ranked] == ["balanced"]
    assert ranked[0].ratio == 0.9
    assert "Premium" in ranked[0].reasons


def test_follow_ranking_prefers_ratio_near_one() -> None:
    ranked = rank_follow_candidates(
        [
            _post("near_one", followers=2_000, following=1_900),
            _post("looser", followers=2_000, following=1_200),
        ],
        owner_username="owner",
        followed_usernames=set(),
    )

    assert [candidate.username for candidate in ranked] == ["near_one", "looser"]
    assert ranked[0].score > ranked[1].score


def test_follow_history_applies_and_expires_cooldown(tmp_path) -> None:
    store = FollowTargetHistoryStore(tmp_path / "follow-history.json")
    candidate = rank_follow_candidates(
        [_post("fresh")],
        owner_username="owner",
        followed_usernames=set(),
    )[0]
    now = datetime(2026, 8, 6, 3, 0, tzinfo=UTC)
    store.mark_suggested([candidate], now=now)

    assert store.recently_suggested("fresh", cooldown_hours=24, now=now)
    assert not store.recently_suggested(
        "fresh",
        cooldown_hours=24,
        now=datetime(2026, 8, 7, 4, 0, tzinfo=UTC),
    )
