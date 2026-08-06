from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
EXPERIMENT_VARIANTS = (
    "concise_statement",
    "insight_then_question",
    "confident_implication",
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
            "schema_version": 3,
            "enabled": bool(enabled),
            "strategy_weights": dict(DEFAULT_STRATEGY_WEIGHTS),
            "strategy_cursor": 0,
            "experiment_enabled": True,
            "experiment_cursor": 0,
            "weight_version": 1,
            "weight_history": [],
            "last_tuned_at": "",
            "last_tuned_sample_count": 0,
            "records": {},
            "feedback_events": [],
            "last_digest_date": "",
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
    def experiment_enabled(self) -> bool:
        return bool(self.data.get("experiment_enabled", True))

    def set_experiment_enabled(self, enabled: bool) -> None:
        self.data["experiment_enabled"] = bool(enabled)
        self._save()

    def choose_experiment_variant(self) -> str:
        if not self.experiment_enabled:
            return "adaptive"
        cursor = int(self.data.get("experiment_cursor", 0))
        selected = EXPERIMENT_VARIANTS[cursor % len(EXPERIMENT_VARIANTS)]
        self.data["experiment_cursor"] = cursor + 1
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
            "root_author_verified": bool(metadata.get("root_author_verified")),
            "root_author_followers": metadata.get("root_author_followers"),
            "author_tier": str(metadata.get("author_tier") or "unknown"),
            "root_views_at_approval": metadata.get("root_views"),
            "root_replies_at_approval": metadata.get("root_replies"),
            "source_type": str(metadata.get("source_type") or "replytargets"),
            "has_video": bool(metadata.get("has_video")),
            "rankability_score": float(metadata.get("rankability_score") or 0.0),
            "premium_audience_score": float(
                metadata.get("premium_audience_score") or 0.0
            ),
            "verified_audience_proxy": float(
                metadata.get("verified_audience_proxy")
                or metadata.get("premium_audience_score")
                or 0.0
            ),
            "monetization_risk_level": str(
                metadata.get("monetization_risk_level") or "green"
            ),
            "monetization_safety_score": float(
                metadata.get("monetization_safety_score") or 100.0
            ),
            "watched_author": bool(metadata.get("watched_author")),
            "experiment_variant": str(
                metadata.get("experiment_variant") or "adaptive"
            ),
            "candidate_age_minutes_at_card": float(
                metadata.get("candidate_age_minutes_at_card") or 0.0
            ),
            "candidate_age_bucket": str(
                metadata.get("candidate_age_bucket") or "unknown"
            ),
            "distribution_stage": str(
                metadata.get("distribution_stage") or "unknown"
            ),
            "discovery_daypart": str(
                metadata.get("discovery_daypart") or "global_offpeak"
            ),
            "daypart_fit_score": float(metadata.get("daypart_fit_score") or 0.0),
            "approval_latency_seconds": float(
                metadata.get("approval_latency_seconds") or 0.0
            ),
            "generation_latency_seconds": float(
                metadata.get("generation_latency_seconds") or 0.0
            ),
            "creator_goal": str(metadata.get("creator_goal") or "qualify"),
            "creator_timezone": str(
                metadata.get("creator_timezone") or "Asia/Ho_Chi_Minh"
            ),
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
            "audience_response_ids": [],
            "audience_followup_created": False,
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
                "posted_hour_utc": _result_datetime(reply).hour,
                "posted_hour_local": _local_hour(
                    _result_datetime(reply),
                    str(record.get("creator_timezone") or "Asia/Ho_Chi_Minh"),
                ),
                "language": reply.language or record.get("language", ""),
                "owner_followers_at_posting": reply.author_followers_count,
                "edit_similarity": _text_similarity(
                    str(record.get("draft_text") or ""),
                    reply.text,
                ),
                "posting_latency_seconds": round(
                    max(
                        0.0,
                        (
                            _result_datetime(reply)
                            - _parse_datetime(record.get("approved_at"))
                        ).total_seconds(),
                    ),
                    1,
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
                "source_type": str(
                    (approval.metadata or {}).get("source_type") or "replytargets"
                ),
                "revision": str(
                    (approval.metadata or {}).get("last_revision") or "first"
                ),
                "revision_count": int(
                    (approval.metadata or {}).get("revision_count") or 0
                ),
                "approved": bool(approved),
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
        self.data["feedback_events"] = events[-1000:]
        self._save()

    @property
    def last_digest_date(self) -> str:
        return str(self.data.get("last_digest_date") or "")

    def mark_digest_sent(self, date_value: str) -> None:
        self.data["last_digest_date"] = str(date_value or "")
        self._save()

    def style_examples(
        self,
        *,
        language: str = "",
        source_type: str = "",
        limit: int = 3,
    ) -> list[str]:
        """Return top real posted replies as bounded prompt examples."""
        clean_language = language.strip().lower()
        clean_source = source_type.strip().lower()
        rows = []
        for row in self.records("tracking", "measured"):
            text = str(row.get("actual_text") or "").strip()
            if not text:
                continue
            if clean_language and str(row.get("language") or "").lower() != clean_language:
                continue
            if clean_source and str(row.get("source_type") or "").lower() != clean_source:
                continue
            rows.append(row)
        rows.sort(
            key=lambda row: (
                float(row.get("final_score") or 0.0),
                float(row.get("edit_similarity") or 0.0),
                str(row.get("posted_at") or ""),
            ),
            reverse=True,
        )
        examples: list[str] = []
        seen: set[str] = set()
        for row in rows:
            text = str(row.get("actual_text") or "").strip()
            normalized = re.sub(r"\W+", " ", text.casefold()).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            examples.append(text[:280])
            if len(examples) >= max(0, limit):
                break
        return examples

    def performance_adjustment(
        self,
        *,
        language: str,
        source_type: str,
        hour_utc: int | None = None,
    ) -> float:
        """Bayesian-smoothed multiplier for language/source/time allocation."""
        measured = [
            row
            for row in self.records("measured")
            if row.get("final_score") is not None
        ]
        if len(measured) < 5:
            return 1.0
        global_mean = sum(float(row["final_score"]) for row in measured) / len(measured)
        selected = [
            row
            for row in measured
            if str(row.get("language") or "").lower() == language.strip().lower()
            and str(row.get("source_type") or "").lower()
            == source_type.strip().lower()
            and (
                hour_utc is None
                or abs(int(row.get("posted_hour_utc") or 0) - int(hour_utc)) <= 2
            )
        ]
        if not selected:
            return 1.0
        shrunk = (
            sum(float(row["final_score"]) for row in selected) + 6 * global_mean
        ) / (len(selected) + 6)
        return round(max(0.85, min(1.15, shrunk / max(1.0, global_mean))), 3)

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
            or (
                record.get("followup_created")
                and record.get("audience_followup_created")
            )
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

    def mark_audience_response(
        self,
        approval_id: str,
        response: XSearchResult,
    ) -> None:
        record = self._record(approval_id)
        ids = {
            int(value)
            for value in record.get("audience_response_ids", [])
            if str(value).isdigit()
        }
        ids.add(int(response.id))
        record["audience_response_ids"] = sorted(ids)
        record["audience_followup_created"] = True
        self._save()

    def audience_response_seen(self, record: dict[str, Any], response_id: int) -> bool:
        return int(response_id) in {
            int(value)
            for value in record.get("audience_response_ids", [])
            if str(value).isdigit()
        }

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
        views = [
            int((row.get("snapshots") or [{}])[-1].get("views") or 0)
            for row in measured
            if row.get("snapshots")
        ]
        approval_latencies = [
            float(row.get("approval_latency_seconds") or 0.0)
            for row in rows
            if float(row.get("approval_latency_seconds") or 0.0) > 0
        ]
        posting_latencies = [
            float(row.get("posting_latency_seconds") or 0.0)
            for row in rows
            if float(row.get("posting_latency_seconds") or 0.0) > 0
        ]
        generation_latencies = [
            float(row.get("generation_latency_seconds") or 0.0)
            for row in rows
            if float(row.get("generation_latency_seconds") or 0.0) > 0
        ]
        feedback = [
            row
            for row in self.data.get("feedback_events", [])
            if isinstance(row, dict)
            and _parse_datetime(row.get("created_at")) >= cutoff
        ]
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
            "median_views": int(statistics.median(views)) if views else 0,
            "over_5k": sum(value >= 5_000 for value in views),
            "over_20k": sum(value >= 20_000 for value in views),
            "over_50k": sum(value >= 50_000 for value in views),
            "author_response_rate": (
                sum(bool(row.get("author_replied")) for row in rows) / len(rows)
                if rows
                else 0.0
            ),
            "approval_rate": (
                sum(bool(row.get("approved")) for row in feedback) / len(feedback)
                if feedback
                else 0.0
            ),
            "by_strategy": by_strategy,
            "by_language": _dimension_report(measured, "language"),
            "by_source": _dimension_report(measured, "source_type"),
            "by_experiment": _dimension_report(measured, "experiment_variant"),
            "by_risk": _dimension_report(measured, "monetization_risk_level"),
            "by_author_tier": _dimension_report(measured, "author_tier"),
            "by_age_bucket": _dimension_report(measured, "candidate_age_bucket"),
            "by_distribution_stage": _dimension_report(
                measured,
                "distribution_stage",
            ),
            "by_daypart": _dimension_report(measured, "discovery_daypart"),
            "by_hour_utc": _dimension_report(measured, "posted_hour_utc"),
            "by_hour_local": _dimension_report(measured, "posted_hour_local"),
            "posts": sum(row.get("kind") == "post" for row in rows),
            "replies": sum(row.get("kind") == "reply" for row in rows),
            "follower_window_lift": _follower_window_lift(rows),
            "reply_view_sum_proxy": sum(views),
            "median_approval_latency_seconds": int(statistics.median(approval_latencies))
            if approval_latencies
            else 0,
            "median_posting_latency_seconds": int(statistics.median(posting_latencies))
            if posting_latencies
            else 0,
            "median_generation_latency_seconds": int(statistics.median(generation_latencies))
            if generation_latencies
            else 0,
        }

    def author_portfolio(self, username: str) -> dict[str, Any]:
        clean = username.strip().lstrip("@").casefold()
        rows = [
            row
            for row in self.records("tracking", "measured")
            if str(row.get("root_author") or "").casefold() == clean
        ]
        views = [
            int((row.get("snapshots") or [{}])[-1].get("views") or 0)
            for row in rows
            if row.get("snapshots")
        ]
        proxies = [float(row.get("verified_audience_proxy") or 0.0) for row in rows]
        follower_samples = [
            int(row.get("root_author_followers") or 0)
            for row in rows
            if int(row.get("root_author_followers") or 0) > 0
        ]
        interaction_dates = [
            max(
                _parse_datetime(row.get("posted_at")),
                _parse_datetime(row.get("author_response_detected_at")),
            )
            for row in rows
        ]
        green_count = sum(
            str(row.get("monetization_risk_level") or "green") == "green"
            for row in rows
        )
        return {
            "username": clean,
            "replies": len(rows),
            "measured": len(views),
            "median_views": int(statistics.median(views)) if views else 0,
            "over_20k": sum(value >= 20_000 for value in views),
            "author_response_rate": (
                sum(bool(row.get("author_replied")) for row in rows) / len(rows)
                if rows
                else 0.0
            ),
            "verified_audience_proxy": (
                sum(proxies) / len(proxies) if proxies else 0.0
            ),
            "author_followers": (
                int(statistics.median(follower_samples)) if follower_samples else 0
            ),
            "author_tier": _author_tier_from_followers(
                int(statistics.median(follower_samples)) if follower_samples else 0
            ),
            "relationship_strength": self.relationship_strength(clean),
            "green_rate": green_count / len(rows) if rows else 0.0,
            "last_interaction_at": (
                max(interaction_dates).isoformat() if interaction_dates else ""
            ),
        }

    def author_portfolios(self) -> list[dict[str, Any]]:
        usernames = sorted(
            {
                str(row.get("root_author") or "").strip().lstrip("@").casefold()
                for row in self.records("tracking", "measured")
                if str(row.get("root_author") or "").strip()
            }
        )
        return [self.author_portfolio(username) for username in usernames]

    def recommended_video_share(self, goal: str = "qualify") -> float:
        measured = [row for row in self.records("measured") if row.get("final_score") is not None]
        videos = [row for row in measured if row.get("source_type") == "replyvideo"]
        texts = [row for row in measured if row.get("source_type") == "replytargets"]
        baseline = 0.45 if goal == "network" else 0.60
        if len(videos) < 3 or len(texts) < 3:
            return baseline
        video_mean = sum(float(row["final_score"]) for row in videos) / len(videos)
        text_mean = sum(float(row["final_score"]) for row in texts) / len(texts)
        share = video_mean / max(1.0, video_mean + text_mean)
        lower, upper = ((0.30, 0.55) if goal == "network" else (0.35, 0.75))
        return round(max(lower, min(upper, share)), 2)

    def winning_insights(
        self,
        days: int = 30,
        *,
        limit: int = 5,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        cutoff = (now or datetime.now(UTC)) - timedelta(days=max(1, days))
        rows = [
            row
            for row in self.records("tracking", "measured")
            if _parse_datetime(row.get("posted_at")) >= cutoff
            and str(row.get("actual_text") or "").strip()
        ]
        rows.sort(
            key=lambda row: (
                int((row.get("snapshots") or [{}])[-1].get("views") or 0),
                float(row.get("final_score") or 0.0),
            ),
            reverse=True,
        )
        return rows[: max(0, limit)]

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


def _dimension_report(
    rows: list[dict[str, Any]],
    key: str,
) -> dict[str, dict[str, float | int]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        value = str(row.get(key) if row.get(key) not in {None, ""} else "unknown")
        groups.setdefault(value, []).append(row)
    report: dict[str, dict[str, float | int]] = {}
    for value, selected in groups.items():
        scores = [float(row.get("final_score") or 0.0) for row in selected]
        views = [
            int((row.get("snapshots") or [{}])[-1].get("views") or 0)
            for row in selected
            if row.get("snapshots")
        ]
        report[value] = {
            "count": len(selected),
            "average_score": round(sum(scores) / max(1, len(scores)), 1),
            "median_views": int(statistics.median(views)) if views else 0,
            "author_response_rate": round(
                sum(bool(row.get("author_replied")) for row in selected)
                / max(1, len(selected)),
                3,
            ),
        }
    return dict(
        sorted(
            report.items(),
            key=lambda item: (
                float(item[1]["average_score"]),
                int(item[1]["count"]),
            ),
            reverse=True,
        )
    )


def _author_tier_from_followers(followers: int) -> str:
    if followers <= 0:
        return "unknown"
    if followers < 8_000:
        return "emerging_under_8k"
    if followers < 50_000:
        return "mid_8k_50k"
    if followers < 300_000:
        return "large_50k_300k"
    return "mega_300k_plus"


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
    """Account delta across tracked reply/post windows without double counting."""
    baselines = []
    observed = []
    for row in rows:
        if not row.get("snapshots"):
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


def _local_hour(value: datetime, timezone_name: str) -> int:
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone = UTC
    return value.astimezone(timezone).hour
