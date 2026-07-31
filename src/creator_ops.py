from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src.models import XSearchResult


WATCH_RETENTION_HOURS = 24
READY_OPPORTUNITY_SCORE = 68.0
READY_VIRAL_SCORE = 58.0


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
            is_exceptional = (
                result.reply_opportunity_score >= READY_OPPORTUNITY_SCORE
                and result.viral_score >= READY_VIRAL_SCORE
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

    def watching(self, limit: int = 5) -> list[dict[str, Any]]:
        rows = [
            dict(row)
            for row in self.data.get("items", {}).values()
            if isinstance(row, dict) and row.get("state") == "watching"
        ]
        rows.sort(
            key=lambda row: (
                float(row.get("opportunity_score", 0.0)),
                float(row.get("viral_score", 0.0)),
            ),
            reverse=True,
        )
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
