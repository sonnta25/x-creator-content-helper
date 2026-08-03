from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from src.automation import AutomationApproval
from src.models import XSearchResult
from src.x_search_service import extract_tweet_id


STRATEGIES = (
    "specific_observation",
    "practical_implication",
    "respectful_counterpoint",
    "author_specific_question",
    "natural_humor",
)
DEFAULT_STRATEGY_WEIGHTS = {
    "specific_observation": 0.25,
    "practical_implication": 0.25,
    "respectful_counterpoint": 0.20,
    "author_specific_question": 0.20,
    "natural_humor": 0.10,
}
CHECKPOINT_MINUTES = (15, 60, 360, 1440)
MIN_FINAL_SAMPLES_TO_TUNE = 60
MIN_FEEDBACK_SAMPLES_TO_TUNE = 20
TUNE_INTERVAL_DAYS = 7
MAX_WEIGHT_CHANGE = 0.10


@dataclass(frozen=True)
class ReplyLearningStatus:
    enabled: bool
    waiting: int
    tracking: int
    measured: int
    unmatched: int
    weights: dict[str, float]
    version: int
    last_tuned_at: str


class ReplyLearningStore:
    """Persistent, bounded learning state. It never edits prompts or source files."""

    def __init__(self, path: str | Path | None, *, enabled: bool = True) -> None:
        self.path = Path(path) if path else None
        self.data: dict[str, Any] = self._default_data(enabled)
        self._load()

    @staticmethod
    def _default_data(enabled: bool) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "enabled": bool(enabled),
            "strategy_weights": dict(DEFAULT_STRATEGY_WEIGHTS),
            "strategy_cursor": 0,
            "weight_version": 1,
            "weight_history": [],
            "last_tuned_at": "",
            "last_tuned_sample_count": 0,
            "records": {},
            "feedback_events": [],
        }

    @property
    def enabled(self) -> bool:
        return bool(self.data.get("enabled", True))

    def set_enabled(self, enabled: bool) -> None:
        self.data["enabled"] = bool(enabled)
        self._save()

    def choose_strategy(self) -> str:
        weights = self.weights
        cursor = int(self.data.get("strategy_cursor", 0)) % 100
        point = (cursor + 0.5) / 100
        cumulative = 0.0
        selected = STRATEGIES[-1]
        for strategy in STRATEGIES:
            cumulative += weights[strategy]
            if point <= cumulative:
                selected = strategy
                break
        self.data["strategy_cursor"] = (cursor + 37) % 100
        self._save()
        return selected

    @property
    def weights(self) -> dict[str, float]:
        raw = self.data.get("strategy_weights", {})
        weights = {
            strategy: max(0.02, float(raw.get(strategy, DEFAULT_STRATEGY_WEIGHTS[strategy])))
            for strategy in STRATEGIES
        }
        total = sum(weights.values()) or 1.0
        return {strategy: value / total for strategy, value in weights.items()}

    def register_approval(self, approval: AutomationApproval) -> dict[str, Any] | None:
        if approval.kind not in {"reply", "post"}:
            return None
        target_id = extract_tweet_id(approval.target_url)
        if approval.kind == "reply" and target_id is None:
            return None
        records = self.data.setdefault("records", {})
        existing = records.get(approval.id)
        if isinstance(existing, dict):
            return existing
        metadata = dict(approval.metadata or {})
        record = {
            "approval_id": approval.id,
            "kind": approval.kind,
            "target_id": target_id,
            "target_url": approval.target_url,
            "draft_text": approval.text,
            "strategy": str(metadata.get("reply_strategy") or "specific_observation"),
            "language": str(metadata.get("language") or ""),
            "root_author": str(metadata.get("root_author") or ""),
            "root_author_id": metadata.get("root_author_id"),
            "root_views_at_approval": metadata.get("root_views"),
            "root_replies_at_approval": metadata.get("root_replies"),
            "approved_at": (
                approval.decided_at or approval.created_at
            ).astimezone(UTC).isoformat(),
            "status": "waiting",
            "reply_id": None,
            "reply_url": "",
            "actual_text": "",
            "posted_at": "",
            "snapshots": [],
            "author_replied": False,
            "author_response_id": None,
            "author_response_url": "",
            "author_response_text": "",
            "author_response_detected_at": "",
            "author_response_latency_minutes": None,
            "last_author_response_check_at": "",
            "final_score": None,
            "edit_similarity": None,
            "followup_created": False,
            "conversation_stopped": False,
        }
        records[approval.id] = record
        self._save()
        return record

    def records(self, *statuses: str) -> list[dict[str, Any]]:
        rows = [
            row
            for row in self.data.get("records", {}).values()
            if isinstance(row, dict)
        ]
        if statuses:
            allowed = set(statuses)
            rows = [row for row in rows if row.get("status") in allowed]
        return rows

    def mark_unmatched(self, approval_id: str) -> None:
        record = self._record(approval_id)
        record["status"] = "unmatched"
        self._save()

    def mark_discovered(
        self,
        approval_id: str,
        reply: XSearchResult,
    ) -> dict[str, Any]:
        record = self._record(approval_id)
        record.update(
            {
                "status": "tracking",
                "reply_id": reply.id,
                "reply_url": reply.url,
                "actual_text": reply.text,
                "posted_at": _result_datetime(reply).isoformat(),
                "language": reply.language or record.get("language", ""),
                "owner_followers_at_posting": reply.author_followers_count,
                "edit_similarity": _text_similarity(
                    str(record.get("draft_text") or ""),
                    reply.text,
                ),
            }
        )
        self._save()
        return record

    def due_checkpoint(
        self,
        record: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> int | None:
        if record.get("status") != "tracking" or not record.get("posted_at"):
            return None
        current = now or datetime.now(UTC)
        posted = datetime.fromisoformat(str(record["posted_at"]))
        age_minutes = (current - posted).total_seconds() / 60
        captured = {
            int(item.get("checkpoint_minutes", 0))
            for item in record.get("snapshots", [])
            if isinstance(item, dict)
        }
        return next(
            (
                checkpoint
                for checkpoint in CHECKPOINT_MINUTES
                if checkpoint <= age_minutes and checkpoint not in captured
            ),
            None,
        )

    def add_snapshot(
        self,
        approval_id: str,
        *,
        checkpoint_minutes: int,
        reply: XSearchResult,
        root: XSearchResult | None,
        author_replied: bool = False,
        owner_followers: int | None = None,
        captured_at: datetime | None = None,
    ) -> dict[str, Any]:
        record = self._record(approval_id)
        snapshot = {
            "checkpoint_minutes": int(checkpoint_minutes),
            "captured_at": (captured_at or datetime.now(UTC)).isoformat(),
            "views": int(reply.view_count or 0),
            "likes": int(reply.like_count),
            "replies": int(reply.reply_count),
            "reposts": int(reply.retweet_count),
            "quotes": int(reply.quote_count),
            "root_views": int(root.view_count or 0) if root else None,
            "root_replies": int(root.reply_count) if root else None,
            "owner_followers": (
                int(owner_followers)
                if owner_followers is not None
                else reply.author_followers_count
            ),
        }
        snapshots = [
            item
            for item in record.get("snapshots", [])
            if int(item.get("checkpoint_minutes", 0)) != int(checkpoint_minutes)
        ]
        snapshots.append(snapshot)
        snapshots.sort(key=lambda item: int(item["checkpoint_minutes"]))
        record["snapshots"] = snapshots
        record["author_replied"] = bool(record.get("author_replied") or author_replied)
        if checkpoint_minutes >= CHECKPOINT_MINUTES[-1]:
            record["status"] = "measured"
            record["final_score"] = (
                _post_outcome_score(record, snapshot)
                if record.get("kind") == "post"
                else _outcome_score(record, snapshot)
            )
        self._save()
        return record

    def maybe_tune(self, *, now: datetime | None = None) -> bool:
        measured = self.records("measured")
        feedback = [
            row
            for row in self.data.get("feedback_events", [])
            if isinstance(row, dict)
        ]
        if (
            len(measured) < MIN_FINAL_SAMPLES_TO_TUNE
            and len(feedback) < MIN_FEEDBACK_SAMPLES_TO_TUNE
        ):
            return False
        total_samples = len(measured) + len(feedback)
        if total_samples <= int(self.data.get("last_tuned_sample_count", 0)):
            return False
        current = now or datetime.now(UTC)
        last_raw = str(self.data.get("last_tuned_at") or "")
        if last_raw and current - datetime.fromisoformat(last_raw) < timedelta(days=TUNE_INTERVAL_DAYS):
            return False

        scores: dict[str, list[tuple[float, float]]] = {
            strategy: [] for strategy in STRATEGIES
        }
        for row in measured:
            strategy = str(row.get("strategy") or "")
            if strategy in scores and row.get("final_score") is not None:
                edit_weight = max(0.15, float(row.get("edit_similarity") or 1.0))
                scores[strategy].append((float(row["final_score"]), edit_weight))
        for row in feedback:
            strategy = str(row.get("strategy") or "")
            if strategy in scores:
                scores[strategy].append(
                    (70.0 if row.get("approved") else 25.0, 0.35)
                )
        if sum(bool(values) for values in scores.values()) < 2:
            return False

        old = self.weights
        weighted_total = sum(
            score * weight
            for values in scores.values()
            for score, weight in values
        )
        weight_total = sum(
            weight for values in scores.values() for _score, weight in values
        )
        global_mean = weighted_total / max(1.0, weight_total)
        quality: dict[str, float] = {}
        for strategy in STRATEGIES:
            values = scores[strategy]
            # Six virtual samples at the global mean prevent a small lucky batch
            # from taking over strategy selection.
            observed_weight = sum(weight for _score, weight in values)
            quality[strategy] = (
                sum(score * weight for score, weight in values)
                + 6 * global_mean
            ) / (observed_weight + 6)
        # Treat quality as a multiplier on the current distribution. This makes
        # an above-average strategy move up and a below-average strategy move
        # down without flattening untouched strategies toward equal weights.
        desired_raw = {
            strategy: old[strategy]
            * max(0.25, min(4.0, quality[strategy] / max(1.0, global_mean)))
            for strategy in STRATEGIES
        }
        desired_total = sum(desired_raw.values()) or 1.0
        desired = {
            strategy: desired_raw[strategy] / desired_total
            for strategy in STRATEGIES
        }
        changes = [
            MAX_WEIGHT_CHANGE * old[strategy]
            / abs(desired[strategy] - old[strategy])
            for strategy in STRATEGIES
            if desired[strategy] != old[strategy]
        ]
        blend = min([1.0, *changes])
        # Both distributions sum to one, so this convex update preserves the
        # total while guaranteeing every relative change is at most 10%.
        new = {
            strategy: old[strategy] + blend * (desired[strategy] - old[strategy])
            for strategy in STRATEGIES
        }
        history = self.data.setdefault("weight_history", [])
        history.append(
            {
                "version": int(self.data.get("weight_version", 1)),
                "weights": old,
                "saved_at": current.isoformat(),
            }
        )
        self.data["weight_history"] = history[-12:]
        self.data["strategy_weights"] = new
        self.data["weight_version"] = int(self.data.get("weight_version", 1)) + 1
        self.data["last_tuned_at"] = current.isoformat()
        self.data["last_tuned_sample_count"] = total_samples
        self._save()
        return True

    def rollback(self) -> bool:
        history = self.data.get("weight_history", [])
        if not isinstance(history, list) or not history:
            return False
        previous = history.pop()
        self.data["strategy_weights"] = previous["weights"]
        self.data["weight_version"] = int(previous.get("version", 1))
        self.data["weight_history"] = history
        self.data["last_tuned_at"] = ""
        self.data["last_tuned_sample_count"] = 0
        self._save()
        return True

    def status(self) -> ReplyLearningStatus:
        return ReplyLearningStatus(
            enabled=self.enabled,
            waiting=len(self.records("waiting")),
            tracking=len(self.records("tracking")),
            measured=len(self.records("measured")),
            unmatched=len(self.records("unmatched")),
            weights=self.weights,
            version=int(self.data.get("weight_version", 1)),
            last_tuned_at=str(self.data.get("last_tuned_at") or ""),
        )

    def record_feedback(self, approval: AutomationApproval, *, approved: bool) -> None:
        strategy = str((approval.metadata or {}).get("reply_strategy") or "")
        if strategy not in STRATEGIES:
            return
        events = self.data.setdefault("feedback_events", [])
        if any(
            isinstance(row, dict)
            and row.get("approval_id") == approval.id
            and row.get("approved") == bool(approved)
            for row in events
        ):
            return
        events.append(
            {
                "approval_id": approval.id,
                "strategy": strategy,
                "language": str((approval.metadata or {}).get("language") or ""),
                "approved": bool(approved),
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
        self.data["feedback_events"] = events[-1000:]
        self._save()

    def author_response_rate(self, username: str) -> float:
        clean = username.strip().lstrip("@").casefold()
        rows = [
            row
            for row in self.records("tracking", "measured")
            if str(row.get("root_author") or "").casefold() == clean
        ]
        if not rows:
            return 0.0
        # One virtual success and three virtual failures avoid over-trusting a
        # single lucky author interaction.
        return (
            1 + sum(bool(row.get("author_replied")) for row in rows)
        ) / (4 + len(rows))

    def relationship_strength(
        self,
        username: str,
        *,
        now: datetime | None = None,
    ) -> float:
        clean = username.strip().lstrip("@").casefold()
        rows = [
            row
            for row in self.records("tracking", "measured")
            if str(row.get("root_author") or "").casefold() == clean
        ]
        responses = [row for row in rows if bool(row.get("author_replied"))]
        if not responses:
            return 0.0

        response_rate = len(responses) / max(1, len(rows))
        conversation_depth = min(1.0, len(responses) / 3.0)
        latencies = [
            float(row["author_response_latency_minutes"])
            for row in responses
            if row.get("author_response_latency_minutes") is not None
        ]
        latency_score = (
            max(0.0, 1.0 - (sum(latencies) / len(latencies)) / 360.0)
            if latencies
            else 0.5
        )
        current = now or datetime.now(UTC)
        detected = [
            _parse_datetime(row.get("author_response_detected_at"))
            for row in responses
            if row.get("author_response_detected_at")
        ]
        recency_score = 0.5
        if detected:
            age_days = max(0.0, (current - max(detected)).total_seconds() / 86400)
            recency_score = max(0.0, 1.0 - min(age_days, 30.0) / 30.0)
        stop_ratio = sum(bool(row.get("conversation_stopped")) for row in rows) / max(
            1,
            len(rows),
        )
        score = 100.0 * (
            0.50 * response_rate
            + 0.20 * conversation_depth
            + 0.15 * latency_score
            + 0.15 * recency_score
        )
        return round(max(0.0, min(100.0, score * (1.0 - 0.25 * stop_ratio))), 2)

    def mark_author_response(
        self,
        approval_id: str,
        response: XSearchResult,
        *,
        detected_at: datetime | None = None,
    ) -> dict[str, Any]:
        record = self._record(approval_id)
        detected = detected_at or datetime.now(UTC)
        response_at = _result_datetime(response)
        posted_at = _parse_datetime(record.get("posted_at"))
        record.update(
            {
                "author_replied": True,
                "author_response_id": response.id,
                "author_response_url": response.url,
                "author_response_text": response.text,
                "author_response_detected_at": detected.isoformat(),
                "author_response_latency_minutes": round(
                    max(0.0, (response_at - posted_at).total_seconds() / 60),
                    2,
                ),
            }
        )
        self._save()
        return record

    def author_response_check_due(
        self,
        record: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> bool:
        if (
            record.get("kind") != "reply"
            or record.get("status") != "tracking"
            or record.get("followup_created")
            or record.get("conversation_stopped")
        ):
            return False
        current = now or datetime.now(UTC)
        posted_at = _parse_datetime(record.get("posted_at"))
        age_minutes = max(0.0, (current - posted_at).total_seconds() / 60)
        interval_minutes = 5 if age_minutes <= 60 else 15 if age_minutes <= 360 else 60
        last_check_raw = str(record.get("last_author_response_check_at") or "")
        if not last_check_raw:
            return True
        return (
            current - _parse_datetime(last_check_raw)
        ).total_seconds() >= interval_minutes * 60

    def mark_author_response_checked(
        self,
        approval_id: str,
        *,
        checked_at: datetime | None = None,
    ) -> None:
        record = self._record(approval_id)
        record["last_author_response_check_at"] = (
            checked_at or datetime.now(UTC)
        ).isoformat()
        self._save()

    def mark_followup_created(self, approval_id: str) -> None:
        record = self._record(approval_id)
        record["followup_created"] = True
        self._save()

    def mark_conversation_stopped(self, approval_id: str) -> None:
        record = self._record(approval_id)
        record["conversation_stopped"] = True
        self._save()

    def report(self, days: int = 30, *, now: datetime | None = None) -> dict[str, Any]:
        current = now or datetime.now(UTC)
        cutoff = current - timedelta(days=max(1, days))
        rows = [
            row
            for row in self.records("tracking", "measured")
            if _parse_datetime(row.get("posted_at")) >= cutoff
        ]
        measured = [row for row in rows if row.get("final_score") is not None]
        by_strategy: dict[str, dict[str, float | int]] = {}
        for strategy in STRATEGIES:
            selected = [row for row in measured if row.get("strategy") == strategy]
            by_strategy[strategy] = {
                "count": len(selected),
                "average_score": (
                    sum(float(row["final_score"]) for row in selected) / len(selected)
                    if selected
                    else 0.0
                ),
            }
        return {
            "days": days,
            "posted": len(rows),
            "measured": len(measured),
            "author_replies": sum(bool(row.get("author_replied")) for row in rows),
            "average_score": (
                sum(float(row["final_score"]) for row in measured) / len(measured)
                if measured
                else 0.0
            ),
            "by_strategy": by_strategy,
            "posts": sum(row.get("kind") == "post" for row in rows),
            "replies": sum(row.get("kind") == "reply" for row in rows),
            "follower_window_lift": _follower_window_lift(rows),
        }

    def _record(self, approval_id: str) -> dict[str, Any]:
        record = self.data.get("records", {}).get(approval_id)
        if not isinstance(record, dict):
            raise RuntimeError("Unknown tracked approval.")
        return record

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        default_enabled = bool(self.data["enabled"])
        self.data.update(payload)
        if "enabled" not in payload:
            self.data["enabled"] = default_enabled

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


def match_posted_reply(
    record: dict[str, Any],
    replies: Iterable[XSearchResult],
    *,
    discovery_window_minutes: int = 90,
) -> XSearchResult | None:
    return match_posted_content(
        record,
        replies,
        discovery_window_minutes=discovery_window_minutes,
    )


def match_posted_content(
    record: dict[str, Any],
    replies: Iterable[XSearchResult],
    *,
    discovery_window_minutes: int = 90,
) -> XSearchResult | None:
    approved_at = _parse_datetime(record.get("approved_at"))
    target_id = int(record.get("target_id") or 0)
    kind = str(record.get("kind") or "reply")
    candidates = []
    for reply in replies:
        if kind == "reply" and reply.in_reply_to_tweet_id != target_id:
            continue
        if kind == "post" and reply.in_reply_to_tweet_id is not None:
            continue
        posted_at = _result_datetime(reply)
        seconds = (posted_at - approved_at).total_seconds()
        if seconds < -60 or seconds > discovery_window_minutes * 60:
            continue
        similarity = _text_similarity(str(record.get("draft_text") or ""), reply.text)
        candidates.append((similarity, -abs(seconds), reply))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def _text_similarity(left: str, right: str) -> float:
    normalize = lambda value: re.sub(r"\W+", " ", value.casefold()).strip()
    return SequenceMatcher(None, normalize(left), normalize(right)).ratio()


def _outcome_score(record: dict[str, Any], snapshot: dict[str, Any]) -> float:
    views = max(0, int(snapshot.get("views") or 0))
    weighted_engagement = (
        int(snapshot.get("likes") or 0)
        + 2 * int(snapshot.get("replies") or 0)
        + 2 * int(snapshot.get("reposts") or 0)
        + 3 * int(snapshot.get("quotes") or 0)
    )
    root_start = int(record.get("root_views_at_approval") or 0)
    root_current = int(snapshot.get("root_views") or 0)
    incremental_root_views = max(1, root_current - root_start)
    exposure_share = min(1.0, views / incremental_root_views)
    engagement_rate = weighted_engagement / max(1, views)
    score = (
        50 * min(1.0, exposure_share / 0.10)
        + 30 * min(1.0, engagement_rate / 0.05)
        + (20 if record.get("author_replied") else 0)
    )
    return round(max(0.0, min(100.0, score)), 2)


def _post_outcome_score(record: dict[str, Any], snapshot: dict[str, Any]) -> float:
    views = max(0, int(snapshot.get("views") or 0))
    followers = max(1, int(record.get("owner_followers_at_posting") or 1))
    weighted_engagement = (
        int(snapshot.get("likes") or 0)
        + 2 * int(snapshot.get("replies") or 0)
        + 2 * int(snapshot.get("reposts") or 0)
        + 3 * int(snapshot.get("quotes") or 0)
    )
    reach_ratio = views / followers
    engagement_rate = weighted_engagement / max(1, views)
    return round(
        max(
            0.0,
            min(
                100.0,
                (60 * min(1.0, reach_ratio / 2.0))
                + (40 * min(1.0, engagement_rate / 0.05)),
            ),
        ),
        2,
    )


def _follower_window_lift(rows: list[dict[str, Any]]) -> int:
    """Account delta across tracked post windows without double-counting overlaps."""
    baselines = []
    observed = []
    for row in rows:
        if row.get("kind") != "post" or not row.get("snapshots"):
            continue
        baseline = int(row.get("owner_followers_at_posting") or 0)
        latest = int((row.get("snapshots") or [{}])[-1].get("owner_followers") or 0)
        if baseline > 0:
            baselines.append(baseline)
        if latest > 0:
            observed.append(latest)
    if not baselines or not observed:
        return 0
    return max(0, max(observed) - min(baselines))


def _result_datetime(result: XSearchResult) -> datetime:
    if result.created_at_timestamp is not None:
        return datetime.fromtimestamp(result.created_at_timestamp, tz=UTC)
    return _parse_datetime(result.created_at)


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or ""))
        except ValueError:
            return datetime.min.replace(tzinfo=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
