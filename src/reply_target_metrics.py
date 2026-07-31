from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import time
from typing import Any

from src.models import XSearchResult


MIN_SNAPSHOT_INTERVAL_SECONDS = 2 * 60
MAX_SNAPSHOT_INTERVAL_SECONDS = 12 * 60 * 60
SNAPSHOT_RETENTION_SECONDS = 48 * 60 * 60
MAX_SNAPSHOT_POSTS = 1_000


class ReplyTargetMetricStore:
    """Persist lightweight metric deltas so delayed breakouts are observable."""

    def __init__(self, persist_path: str | Path | None = None) -> None:
        self._persist_path = Path(persist_path) if persist_path else None
        self._posts: dict[str, dict[str, Any]] = {}
        self._load()

    def observe(
        self,
        results: list[XSearchResult],
        *,
        now_timestamp: float | None = None,
    ) -> list[XSearchResult]:
        now = float(now_timestamp if now_timestamp is not None else time.time())
        annotated: list[XSearchResult] = []
        changed = False

        for result in results:
            key = result.url or str(result.id)
            previous = self._posts.get(key)
            observation_count = int((previous or {}).get("observation_count", 0))
            recent_view_velocity = float(
                (previous or {}).get("recent_view_velocity", 0.0)
            )
            recent_engagement_velocity = float(
                (previous or {}).get("recent_engagement_velocity", 0.0)
            )
            recent_conversation_velocity = float(
                (previous or {}).get("recent_conversation_velocity", 0.0)
            )
            recent_reply_velocity = float(
                (previous or {}).get("recent_reply_velocity", 0.0)
            )
            acceleration = float((previous or {}).get("acceleration", 0.0))

            should_update = previous is None
            interval_seconds = (
                now - float(previous.get("observed_at", 0.0))
                if previous is not None
                else 0.0
            )
            if previous is not None and interval_seconds >= MIN_SNAPSHOT_INTERVAL_SECONDS:
                should_update = True
                if interval_seconds <= MAX_SNAPSHOT_INTERVAL_SECONDS:
                    can_compare_acceleration = observation_count >= 2
                    interval_minutes = interval_seconds / 60.0
                    previous_view_velocity = float(
                        previous.get("recent_view_velocity", 0.0)
                    )
                    previous_engagement_velocity = float(
                        previous.get("recent_engagement_velocity", 0.0)
                    )
                    recent_view_velocity = _counter_velocity(
                        result.view_count,
                        previous.get("view_count"),
                        interval_minutes,
                    )
                    recent_engagement_velocity = _counter_velocity(
                        _weighted_engagement(result),
                        previous.get("weighted_engagement"),
                        interval_minutes,
                    )
                    recent_conversation_velocity = _counter_velocity(
                        _conversation_score(result),
                        previous.get("conversation_score"),
                        interval_minutes,
                    )
                    recent_reply_velocity = _counter_velocity(
                        result.reply_count,
                        previous.get("reply_count"),
                        interval_minutes,
                    )
                    acceleration = (
                        max(
                            _acceleration(recent_view_velocity, previous_view_velocity),
                            _acceleration(
                                recent_engagement_velocity,
                                previous_engagement_velocity,
                            ),
                        )
                        if can_compare_acceleration
                        else 0.0
                    )
                    observation_count += 1
                else:
                    recent_view_velocity = 0.0
                    recent_engagement_velocity = 0.0
                    recent_conversation_velocity = 0.0
                    recent_reply_velocity = 0.0
                    acceleration = 0.0
                    observation_count = 1

            if previous is None:
                observation_count = 1

            if should_update:
                self._posts[key] = {
                    "observed_at": now,
                    "view_count": result.view_count,
                    "weighted_engagement": _weighted_engagement(result),
                    "conversation_score": _conversation_score(result),
                    "reply_count": result.reply_count,
                    "recent_view_velocity": recent_view_velocity,
                    "recent_engagement_velocity": recent_engagement_velocity,
                    "recent_conversation_velocity": recent_conversation_velocity,
                    "recent_reply_velocity": recent_reply_velocity,
                    "acceleration": acceleration,
                    "observation_count": observation_count,
                }
                changed = True

            annotated.append(
                replace(
                    result,
                    recent_view_velocity_score=recent_view_velocity,
                    recent_engagement_velocity_score=recent_engagement_velocity,
                    recent_conversation_velocity_score=recent_conversation_velocity,
                    recent_reply_velocity_score=recent_reply_velocity,
                    momentum_acceleration=acceleration,
                    momentum_observation_count=observation_count,
                )
            )

        if self._prune(now):
            changed = True
        if changed:
            self._save()
        return annotated

    def _prune(self, now: float) -> bool:
        changed = False
        stale = [
            key
            for key, row in self._posts.items()
            if now - float(row.get("observed_at", 0.0)) > SNAPSHOT_RETENTION_SECONDS
        ]
        for key in stale:
            self._posts.pop(key, None)
            changed = True

        if len(self._posts) > MAX_SNAPSHOT_POSTS:
            oldest = sorted(
                self._posts,
                key=lambda key: float(self._posts[key].get("observed_at", 0.0)),
            )
            for key in oldest[: len(self._posts) - MAX_SNAPSHOT_POSTS]:
                self._posts.pop(key, None)
                changed = True
        return changed

    def _load(self) -> None:
        if self._persist_path is None or not self._persist_path.exists():
            return
        try:
            payload = json.loads(self._persist_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        posts = payload.get("posts", {}) if isinstance(payload, dict) else {}
        if isinstance(posts, dict):
            self._posts = {
                str(key): value
                for key, value in posts.items()
                if isinstance(value, dict)
            }

    def _save(self) -> None:
        if self._persist_path is None:
            return
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._persist_path.with_suffix(self._persist_path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps({"posts": self._posts}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self._persist_path)


def _counter_velocity(
    current: int | float | None,
    previous: int | float | None,
    interval_minutes: float,
) -> float:
    if current is None or previous is None or interval_minutes <= 0:
        return 0.0
    return max(float(current) - float(previous), 0.0) / interval_minutes


def _acceleration(current_velocity: float, previous_velocity: float) -> float:
    if current_velocity <= 0:
        return 0.0
    if previous_velocity <= 0:
        return 1.0
    return min(max((current_velocity / previous_velocity) - 1.0, 0.0), 3.0)


def _weighted_engagement(result: XSearchResult) -> float:
    return (
        result.like_count
        + (result.retweet_count * 3)
        + (result.quote_count * 5)
        + result.reply_count
    )


def _conversation_score(result: XSearchResult) -> float:
    return result.reply_count + (result.quote_count * 2)
