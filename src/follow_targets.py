from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from src.models import FollowCandidate, XSearchResult


VIETNAMESE_PROFILE_TERMS = (
    "việt nam",
    "vietnam",
    "hà nội",
    "ha noi",
    "sài gòn",
    "saigon",
    "ho chi minh",
    "đà nẵng",
    "da nang",
)


class FollowTargetHistoryStore:
    """Avoid repeating the same suggestion while preserving a fresh daily pool."""

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None
        self.data: dict[str, Any] = {"schema_version": 1, "items": {}}
        self._load()

    def recently_suggested(
        self,
        username: str,
        *,
        cooldown_hours: int,
        now: datetime | None = None,
    ) -> bool:
        current = now or datetime.now(UTC)
        row = self.data.get("items", {}).get(username.lower())
        if not isinstance(row, dict):
            return False
        try:
            suggested_at = datetime.fromisoformat(str(row.get("suggested_at") or ""))
        except ValueError:
            return False
        if suggested_at.tzinfo is None:
            suggested_at = suggested_at.replace(tzinfo=UTC)
        return current - suggested_at < timedelta(hours=max(1, cooldown_hours))

    def mark_suggested(
        self,
        candidates: Iterable[FollowCandidate],
        *,
        now: datetime | None = None,
    ) -> None:
        current = now or datetime.now(UTC)
        items = self.data.setdefault("items", {})
        for candidate in candidates:
            items[candidate.username.lower()] = {
                "username": candidate.username,
                "suggested_at": current.isoformat(),
                "score": round(candidate.score, 1),
                "followers": candidate.followers,
                "following": candidate.following,
            }
        self._prune(current)
        self._save()

    def excluded_usernames(
        self,
        *,
        cooldown_hours: int,
        now: datetime | None = None,
    ) -> set[str]:
        return {
            str(row.get("username") or key).lower()
            for key, row in self.data.get("items", {}).items()
            if isinstance(row, dict)
            and self.recently_suggested(
                str(row.get("username") or key),
                cooldown_hours=cooldown_hours,
                now=now,
            )
        }

    def _prune(self, now: datetime) -> None:
        cutoff = now - timedelta(days=30)
        items = self.data.get("items", {})
        for key, row in list(items.items()):
            try:
                timestamp = datetime.fromisoformat(str(row.get("suggested_at") or ""))
            except (AttributeError, ValueError):
                items.pop(key, None)
                continue
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            if timestamp < cutoff:
                items.pop(key, None)

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(payload, dict) and isinstance(payload.get("items"), dict):
            self.data = payload

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)


def rank_follow_candidates(
    posts: Iterable[XSearchResult],
    *,
    owner_username: str,
    followed_usernames: set[str],
    excluded_usernames: set[str] | None = None,
    min_followers: int = 100,
    max_followers: int = 50_000,
) -> list[FollowCandidate]:
    """Rank active Vietnamese Premium authors by an explainable follow-back proxy."""

    owner = owner_username.strip().lstrip("@").lower()
    followed = {value.strip().lstrip("@").lower() for value in followed_usernames}
    excluded = {value.strip().lstrip("@").lower() for value in (excluded_usernames or set())}
    best_by_username: dict[str, FollowCandidate] = {}
    now = datetime.now(UTC)

    for post in posts:
        username = post.username.strip().lstrip("@")
        key = username.lower()
        followers = int(post.author_followers_count or 0)
        following = int(post.author_following_count or 0)
        if (
            not username
            or key == owner
            or key in followed
            or key in excluded
            or not post.author_blue_verified
            or str(post.author_blue_type or "").lower() in {"business", "government"}
            or post.author_protected
            or followers < min_followers
            or followers > max_followers
            or following <= 0
        ):
            continue

        ratio = following / max(1, followers)
        # Start strict, but permit a modest fill range so a scheduled batch can
        # still reach 12 without pretending a celebrity account is likely to reciprocate.
        if ratio < 0.5 or ratio > 2:
            continue

        vietnamese_signal = _vietnamese_signal(post)
        if not vietnamese_signal:
            continue

        ratio_score = max(0.0, 1.0 - abs(_safe_log2(ratio))) * 45.0
        follower_score = _moderate_network_score(followers) * 18.0
        recency_score = _recency_score(post, now) * 17.0
        profile_score = (1.0 if _profile_has_vietnamese_signal(post) else 0.55) * 12.0
        activity_score = min(1.0, int(post.author_statuses_count or 0) / 2_000) * 8.0
        score = min(100.0, ratio_score + follower_score + recency_score + profile_score + activity_score)

        reasons = ["Premium", "active in Vietnamese"]
        if 0.67 <= ratio <= 1.5:
            reasons.append("following/follower ratio near 1")
        else:
            reasons.append("balanced following/follower range")
        if _profile_has_vietnamese_signal(post):
            reasons.append("Vietnamese profile signal")

        candidate = FollowCandidate(
            user_id=int(post.author_id or 0),
            username=username,
            display_name=post.display_name.strip(),
            description=post.author_description.strip(),
            location=post.author_location.strip(),
            followers=followers,
            following=following,
            statuses=int(post.author_statuses_count or 0),
            profile_url=f"https://x.com/{username}",
            source_post_url=post.url,
            source_post_text=post.text.strip(),
            source_post_created_at=post.created_at,
            ratio=ratio,
            score=score,
            reasons=tuple(reasons),
        )
        previous = best_by_username.get(key)
        if previous is None or candidate.score > previous.score:
            best_by_username[key] = candidate

    return sorted(
        best_by_username.values(),
        key=lambda item: (item.score, -abs(1.0 - item.ratio), item.followers),
        reverse=True,
    )


def _vietnamese_signal(post: XSearchResult) -> bool:
    return str(post.language or "").lower() == "vi" or _profile_has_vietnamese_signal(post)


def _profile_has_vietnamese_signal(post: XSearchResult) -> bool:
    haystack = f"{post.author_description} {post.author_location}".casefold()
    return any(term in haystack for term in VIETNAMESE_PROFILE_TERMS)


def _safe_log2(value: float) -> float:
    import math

    return math.log2(max(value, 0.0001))


def _moderate_network_score(followers: int) -> float:
    if 500 <= followers <= 10_000:
        return 1.0
    if 200 <= followers <= 25_000:
        return 0.8
    return 0.55


def _recency_score(post: XSearchResult, now: datetime) -> float:
    if post.created_at_timestamp is None:
        return 0.5
    age_hours = max(0.0, (now.timestamp() - post.created_at_timestamp) / 3600)
    if age_hours <= 6:
        return 1.0
    if age_hours <= 24:
        return 0.8
    if age_hours <= 72:
        return 0.55
    return 0.25
