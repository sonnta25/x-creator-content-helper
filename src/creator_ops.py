from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src.models import XSearchResult


WATCH_RETENTION_HOURS = 24
READY_OPPORTUNITY_SCORE = 68.0
READY_VIRAL_SCORE = 58.0
JAPANESE_READY_OPPORTUNITY_SCORE = 62.0
JAPANESE_READY_VIRAL_SCORE = 52.0


class ReplyWatchStore:
    """Small persistent queue separating discovery from expensive draft generation."""

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None
        self.data: dict[str, Any] = {"schema_version": 1, "items": {}}
        self._load()

    def classify(
        self,
        results: list[XSearchResult],
        *,
        source_type: str = "replytargets",
        now: datetime | None = None,
    ) -> tuple[list[XSearchResult], list[XSearchResult]]:
        current = now or datetime.now(UTC)
        ready: list[XSearchResult] = []
        watching: list[XSearchResult] = []
        items = self.data.setdefault("items", {})
        for result in results:
            key = result.url or str(result.id)
            row = items.get(key, {})
            seen_count = int(row.get("seen_count", 0)) + 1
            first_seen_at = str(row.get("first_seen_at") or current.isoformat())
            is_japanese = str(result.language or "").lower().startswith("ja")
            opportunity_floor = (
                JAPANESE_READY_OPPORTUNITY_SCORE
                if is_japanese
                else READY_OPPORTUNITY_SCORE
            )
            viral_floor = JAPANESE_READY_VIRAL_SCORE if is_japanese else READY_VIRAL_SCORE
            is_exceptional = (
                result.reply_opportunity_score >= opportunity_floor
                and result.viral_score >= viral_floor
            )
            is_confirmed = result.momentum_observation_count >= 2 or seen_count >= 2
            state = "ready" if is_exceptional or is_confirmed else "watching"
            items[key] = {
                "url": result.url,
                "tweet_id": result.id,
                "username": result.username,
                "text": result.text,
                "language": result.language,
                "first_seen_at": first_seen_at,
                "last_seen_at": current.isoformat(),
                "seen_count": seen_count,
                "state": state,
                "source_type": source_type,
                "has_video": bool(result.has_video),
                "viral_score": result.viral_score,
                "opportunity_score": result.reply_opportunity_score,
                "reply_count": result.reply_count,
                "view_count": result.view_count,
            }
            (ready if state == "ready" else watching).append(result)
        self._prune(current)
        self._save()
        return ready, watching

    def mark_drafted(self, url: str) -> None:
        row = self.data.get("items", {}).get(url)
        if isinstance(row, dict):
            row["state"] = "drafted"
            row["drafted_at"] = datetime.now(UTC).isoformat()
            self._save()

    def mark_expired(self, url: str, *, reason: str = "") -> None:
        row = self.data.get("items", {}).get(url)
        if isinstance(row, dict):
            row["state"] = "expired"
            row["expired_at"] = datetime.now(UTC).isoformat()
            row["expired_reason"] = str(reason or "")
            self._save()

    def inventory(self) -> dict[str, int]:
        counts = {"watching": 0, "ready": 0, "drafted": 0, "expired": 0}
        for row in self.data.get("items", {}).values():
            if not isinstance(row, dict):
                continue
            state = str(row.get("state") or "watching")
            counts[state] = counts.get(state, 0) + 1
        return counts

    def candidates_for_refresh(
        self,
        *,
        limit: int = 6,
        languages: list[str] | tuple[str, ...] | None = None,
        states: tuple[str, ...] = ("watching",),
        source_type: str = "replytargets",
        max_age_minutes: int | None = None,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return persisted watching rows that should be re-fetched by tweet ID."""
        current = now or datetime.now(UTC)
        allowed_languages = {
            str(language or "").strip().lower()
            for language in (languages or [])
            if str(language or "").strip()
        }
        changed = False
        rows: list[dict[str, Any]] = []
        for row in self.data.get("items", {}).values():
            if not isinstance(row, dict) or row.get("state") not in states:
                continue
            row_source = str(row.get("source_type") or "replytargets")
            if source_type and row_source != source_type:
                continue
            language = str(row.get("language") or "").strip().lower()
            if allowed_languages and language and language not in allowed_languages:
                continue
            if max_age_minutes is not None:
                try:
                    first_seen = datetime.fromisoformat(str(row.get("first_seen_at") or ""))
                except ValueError:
                    first_seen = current
                if first_seen.tzinfo is None:
                    first_seen = first_seen.replace(tzinfo=UTC)
                if current - first_seen > timedelta(minutes=max(1, max_age_minutes)):
                    row["state"] = "expired"
                    row["expired_at"] = current.isoformat()
                    changed = True
                    continue
            try:
                tweet_id = int(row.get("tweet_id"))
            except (TypeError, ValueError):
                continue
            if tweet_id <= 0:
                continue
            rows.append(dict(row))

        rows.sort(
            key=lambda row: (
                float(row.get("opportunity_score", 0.0)),
                float(row.get("viral_score", 0.0)),
                str(row.get("last_seen_at") or ""),
            ),
            reverse=True,
        )
        if changed:
            self._save()
        return rows[: max(0, limit)]

    def _prune(self, now: datetime) -> None:
        cutoff = now - timedelta(hours=WATCH_RETENTION_HOURS)
        items = self.data.get("items", {})
        stale = []
        for key, row in items.items():
            try:
                last_seen = datetime.fromisoformat(str(row.get("last_seen_at") or ""))
            except ValueError:
                stale.append(key)
                continue
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=UTC)
            if last_seen < cutoff:
                stale.append(key)
        for key in stale:
            items.pop(key, None)

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(payload, dict) and isinstance(payload.get("items", {}), dict):
            self.data = payload

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(self.path)
