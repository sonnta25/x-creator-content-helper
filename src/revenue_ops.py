from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from src.models import XSearchResult


MONETIZATION_GREEN = "green"
MONETIZATION_YELLOW = "yellow"
MONETIZATION_RED = "red"

_RED_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "gambling or betting",
        (
            "polymarket",
            "sportsbook",
            "sports betting",
            "online casino",
            "casino bonus",
            "betting odds",
            "place your bet",
            "gambling",
            "lottery ticket",
            "賭博",
            "オンラインカジノ",
            "スポーツベッティング",
            "博彩",
            "cá cược",
        ),
    ),
    (
        "adult or sexual content",
        (
            "pornhub",
            "onlyfans",
            "pornography",
            "explicit sex",
            "アダルト動画",
            "ポルノ",
        ),
    ),
    (
        "illegal or scam promotion",
        (
            "guaranteed returns",
            "double your money",
            "send crypto to",
            "free money giveaway",
            "phishing",
            "rug pull",
        ),
    ),
)

_YELLOW_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "disaster or tragedy",
        (
            "earthquake",
            "tsunami",
            "wildfire",
            "mass casualty",
            "fatal crash",
            "funeral",
            "passed away",
            "in memoriam",
            "緊急地震速報",
            "地震",
            "津波",
            "災害",
            "訃報",
            "死去",
            "葬儀",
            "追悼",
            "động đất",
            "sóng thần",
            "thảm họa",
        ),
    ),
    (
        "war, conflict, or graphic violence",
        (
            "armed conflict",
            "air strike",
            "airstrike",
            "missile attack",
            "graphic footage",
            "killed in",
            "戦争",
            "空爆",
            "死傷者",
            "chiến tranh",
            "không kích",
        ),
    ),
    (
        "controversial political or social issue",
        (
            "election fraud",
            "political unrest",
            "civil disorder",
            "protest crackdown",
            "選挙不正",
            "政治資金",
            "政権批判",
            "bầu cử gian lận",
            "bạo loạn",
        ),
    ),
    (
        "strong language",
        (
            "fuck ",
            " fucking",
            "shit ",
            "bitch",
            "くそ",
            "クソ",
        ),
    ),
)


@dataclass(frozen=True)
class MonetizationAssessment:
    level: str
    score: float
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReplyFarmingGuardrails:
    """Operator safety heuristics; these are not published X rate limits."""

    global_hourly_cap: int | None
    japanese_daily_cap: int | None
    japanese_hourly_cap: int | None
    minimum_approval_gap_seconds: int


def reply_farming_guardrails(risk_mode: str) -> ReplyFarmingGuardrails:
    mode = str(risk_mode or "balanced").strip().lower()
    if mode == "strict":
        return ReplyFarmingGuardrails(
            global_hourly_cap=12,
            japanese_daily_cap=20,
            # Spread the 20/day Japanese allowance across the creator day
            # instead of exhausting it during the first few scan windows.
            japanese_hourly_cap=2,
            minimum_approval_gap_seconds=120,
        )
    if mode == "open":
        return ReplyFarmingGuardrails(
            global_hourly_cap=None,
            japanese_daily_cap=None,
            japanese_hourly_cap=None,
            minimum_approval_gap_seconds=0,
        )
    return ReplyFarmingGuardrails(
        global_hourly_cap=20,
        japanese_daily_cap=30,
        japanese_hourly_cap=6,
        minimum_approval_gap_seconds=60,
    )


def assess_monetization_safety(result: XSearchResult | str) -> MonetizationAssessment:
    if isinstance(result, XSearchResult):
        parts = [result.text, *(result.media_descriptions or [])]
    else:
        parts = [str(result or "")]
    text = " ".join(parts).casefold()
    red = _matched_reasons(text, _RED_RULES)
    if red:
        return MonetizationAssessment(MONETIZATION_RED, 0.0, tuple(red))
    yellow = _matched_reasons(text, _YELLOW_RULES)
    if yellow:
        return MonetizationAssessment(MONETIZATION_YELLOW, 45.0, tuple(yellow))
    return MonetizationAssessment(MONETIZATION_GREEN, 100.0, ())


def _matched_reasons(
    text: str,
    rules: Iterable[tuple[str, tuple[str, ...]]],
) -> list[str]:
    reasons: list[str] = []
    for reason, terms in rules:
        if any(term.casefold() in text for term in terms):
            reasons.append(reason)
    return reasons


class RevenueOpsStore:
    """Small persistent creator-revenue control plane.

    This store deliberately keeps manual payout and eligibility inputs separate
    from reply-learning metrics. Public X data cannot reveal actual eligible
    Home impressions or subscriber tier, so the bot labels all such estimates
    as proxies.
    """

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None
        self.data: dict[str, Any] = self._defaults()
        self._load()

    @staticmethod
    def _defaults() -> dict[str, Any]:
        return {
            "schema_version": 2,
            "watch_authors": {},
            "blocked_authors": {},
            "auto_watch_enabled": True,
            "payouts": [],
            "eligibility": {
                "premium": False,
                "stripe": False,
                "identity": False,
                "two_factor": False,
                "verified_followers": 0,
            },
            "risk_mode": "balanced",
            "pace_mode": "adaptive",
            "pace_paused": False,
            "health_errors": [],
        }

    @property
    def risk_mode(self) -> str:
        value = str(self.data.get("risk_mode") or "balanced")
        return value if value in {"strict", "balanced", "open"} else "balanced"

    def set_risk_mode(self, mode: str) -> None:
        clean = str(mode or "").strip().lower()
        if clean not in {"strict", "balanced", "open"}:
            raise RuntimeError("Risk mode must be strict, balanced, or open.")
        self.data["risk_mode"] = clean
        self._save()

    @property
    def pace_mode(self) -> str:
        value = str(self.data.get("pace_mode") or "adaptive")
        return value if value in {"conservative", "adaptive", "high"} else "adaptive"

    @property
    def pace_paused(self) -> bool:
        return bool(self.data.get("pace_paused"))

    def set_pace_mode(self, mode: str) -> None:
        clean = str(mode or "").strip().lower()
        if clean not in {"conservative", "adaptive", "high"}:
            raise RuntimeError("Pace mode must be conservative, adaptive, or high.")
        self.data["pace_mode"] = clean
        self.data["pace_paused"] = False
        self._save()

    def set_pace_paused(self, paused: bool) -> None:
        self.data["pace_paused"] = bool(paused)
        self._save()

    def hourly_ceiling(self, daily_cap: int) -> int:
        if self.pace_mode == "conservative":
            return max(4, min(16, round(max(1, daily_cap) / 24)))
        if self.pace_mode == "high":
            return max(12, min(75, round(max(1, daily_cap) / 8)))
        return max(8, min(50, round(max(1, daily_cap) / 12)))

    @property
    def auto_watch_enabled(self) -> bool:
        return bool(self.data.get("auto_watch_enabled", True))

    def set_auto_watch_enabled(self, enabled: bool) -> None:
        self.data["auto_watch_enabled"] = bool(enabled)
        self._save()

    def add_watch_author(self, username: str, *, pinned: bool = True) -> str:
        clean = _clean_username(username)
        authors = self.data.setdefault("watch_authors", {})
        row = authors.get(clean, {})
        authors[clean] = {
            "username": clean,
            "added_at": str(row.get("added_at") or datetime.now(UTC).isoformat()),
            "kind": "pinned" if pinned else str(row.get("kind") or "auto"),
            "last_queried_at": str(row.get("last_queried_at") or ""),
            "auto_score": float(row.get("auto_score") or 0.0),
        }
        if pinned:
            self.data.setdefault("blocked_authors", {}).pop(clean, None)
        self._save()
        return clean

    def pin_watch_author(self, username: str) -> str:
        return self.add_watch_author(username, pinned=True)

    def remove_watch_author(self, username: str) -> bool:
        clean = _clean_username(username)
        removed = self.data.setdefault("watch_authors", {}).pop(clean, None) is not None
        if removed:
            self._save()
        return removed

    def watch_authors(self) -> list[str]:
        rows = self.data.get("watch_authors", {})
        if not isinstance(rows, dict):
            return []
        return sorted(str(key) for key in rows if str(key).strip())

    def watch_author_rows(self) -> list[dict[str, Any]]:
        rows = self.data.get("watch_authors", {})
        if not isinstance(rows, dict):
            return []
        output = []
        for username, raw in rows.items():
            if not isinstance(raw, dict):
                raw = {}
            output.append(
                {
                    "username": str(username),
                    "kind": str(raw.get("kind") or "pinned"),
                    "added_at": str(raw.get("added_at") or ""),
                    "last_queried_at": str(raw.get("last_queried_at") or ""),
                    "auto_score": float(raw.get("auto_score") or 0.0),
                }
            )
        return sorted(
            output,
            key=lambda row: (
                row["kind"] != "pinned",
                -float(row["auto_score"]),
                row["username"],
            ),
        )

    def query_watch_authors(
        self,
        *,
        limit: int = 4,
        now: datetime | None = None,
    ) -> list[str]:
        """Rotate pinned and learned authors instead of querying a fixed alphabetic prefix."""
        if limit <= 0:
            return []
        current = now or datetime.now(UTC)
        rows = self.watch_author_rows()
        pinned = [row for row in rows if row["kind"] == "pinned"]
        learned = [row for row in rows if row["kind"] != "pinned"]

        def oldest(pool: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
            return sorted(
                pool,
                key=lambda row: (
                    _parse_datetime(row.get("last_queried_at")),
                    -float(row.get("auto_score") or 0.0),
                ),
            )[: max(0, count)]

        if pinned and learned and limit >= 2:
            pinned_slots = min(len(pinned), max(1, (limit + 1) // 2))
            selected = oldest(pinned, pinned_slots)
            selected += oldest(learned, limit - len(selected))
        else:
            selected = oldest(pinned or learned, limit)
        if len(selected) < limit:
            selected_names = {str(row["username"]) for row in selected}
            selected += oldest(
                [row for row in rows if str(row["username"]) not in selected_names],
                limit - len(selected),
            )
        authors = self.data.setdefault("watch_authors", {})
        for row in selected:
            stored = authors.get(str(row["username"]))
            if isinstance(stored, dict):
                stored["last_queried_at"] = current.isoformat()
        if selected:
            self._save()
        return [str(row["username"]) for row in selected]

    def block_watch_author(self, username: str) -> str:
        clean = _clean_username(username)
        self.data.setdefault("watch_authors", {}).pop(clean, None)
        self.data.setdefault("blocked_authors", {})[clean] = {
            "username": clean,
            "blocked_at": datetime.now(UTC).isoformat(),
        }
        self._save()
        return clean

    def unblock_watch_author(self, username: str) -> bool:
        clean = _clean_username(username)
        removed = self.data.setdefault("blocked_authors", {}).pop(clean, None) is not None
        if removed:
            self._save()
        return removed

    def blocked_authors(self) -> list[str]:
        rows = self.data.get("blocked_authors", {})
        return sorted(str(key) for key in rows) if isinstance(rows, dict) else []

    def refresh_auto_authors(
        self,
        portfolios: Iterable[dict[str, Any]],
        *,
        now: datetime | None = None,
        max_auto: int = 20,
    ) -> dict[str, list[str]]:
        """Promote proven authors and retire weak/stale automatically learned entries."""
        if not self.auto_watch_enabled:
            return {"promoted": [], "demoted": []}
        current = now or datetime.now(UTC)
        authors = self.data.setdefault("watch_authors", {})
        blocked = set(self.blocked_authors())
        candidates: list[tuple[float, str, dict[str, Any]]] = []
        portfolio_by_name: dict[str, dict[str, Any]] = {}
        for portfolio in portfolios:
            username = str(portfolio.get("username") or "").strip().lstrip("@").casefold()
            if not username or username in blocked:
                continue
            portfolio_by_name[username] = portfolio
            replies = int(portfolio.get("replies") or 0)
            measured = int(portfolio.get("measured") or 0)
            median_views = int(portfolio.get("median_views") or 0)
            response_rate = float(portfolio.get("author_response_rate") or 0.0)
            verified_proxy = float(portfolio.get("verified_audience_proxy") or 0.0)
            relationship = float(portfolio.get("relationship_strength") or 0.0)
            green_rate = float(portfolio.get("green_rate") or 0.0)
            qualifies = (
                replies >= 2
                and green_rate >= 0.70
                and (measured >= 2 or response_rate > 0)
                and (
                    median_views >= 20_000
                    or response_rate >= 0.34
                    or relationship >= 45.0
                )
                and (verified_proxy >= 25.0 or response_rate > 0)
            )
            if not qualifies:
                continue
            score = min(40.0, median_views / 2_500) + response_rate * 25.0
            score += verified_proxy * 0.20 + relationship * 0.15 + green_rate * 10.0
            author_tier = str(portfolio.get("author_tier") or "unknown")
            score += {
                "mid_8k_50k": 8.0,
                "large_50k_300k": 4.0,
                "mega_300k_plus": -4.0,
                "emerging_under_8k": 1.0,
                "unknown": 0.0,
            }.get(author_tier, 0.0)
            candidates.append((round(score, 2), username, portfolio))

        candidates.sort(reverse=True)
        keep_auto = {username for _score, username, _row in candidates[:max_auto]}
        promoted: list[str] = []
        demoted: list[str] = []
        for score, username, _portfolio in candidates[:max_auto]:
            existing = authors.get(username)
            if isinstance(existing, dict) and str(existing.get("kind") or "pinned") == "pinned":
                continue
            if not isinstance(existing, dict):
                promoted.append(username)
                existing = {
                    "username": username,
                    "added_at": current.isoformat(),
                    "kind": "auto",
                    "last_queried_at": "",
                }
            existing["kind"] = "auto"
            existing["auto_score"] = score
            existing["last_qualified_at"] = current.isoformat()
            authors[username] = existing

        for username, raw in list(authors.items()):
            if not isinstance(raw, dict) or str(raw.get("kind") or "pinned") != "auto":
                continue
            portfolio = portfolio_by_name.get(str(username))
            last_interaction = _parse_datetime(
                portfolio.get("last_interaction_at") if portfolio else ""
            )
            stale = last_interaction < current - timedelta(days=30)
            clearly_weak = bool(
                portfolio
                and int(portfolio.get("measured") or 0) >= 5
                and int(portfolio.get("median_views") or 0) < 5_000
                and float(portfolio.get("author_response_rate") or 0.0) == 0.0
            )
            if username not in keep_auto and (stale or clearly_weak or len(keep_auto) >= max_auto):
                authors.pop(username, None)
                demoted.append(str(username))
        if promoted or demoted or candidates:
            self._save()
        return {"promoted": sorted(promoted), "demoted": sorted(demoted)}

    def add_payout(
        self,
        payout_date: str,
        amount: float,
        currency: str = "USD",
    ) -> dict[str, Any]:
        try:
            parsed = date.fromisoformat(str(payout_date).strip())
        except ValueError as exc:
            raise RuntimeError("Payout date must use YYYY-MM-DD.") from exc
        if amount <= 0:
            raise RuntimeError("Payout amount must be greater than zero.")
        clean_currency = re.sub(r"[^A-Z]", "", currency.upper())[:3] or "USD"
        row = {
            "date": parsed.isoformat(),
            "amount": round(float(amount), 2),
            "currency": clean_currency,
            "created_at": datetime.now(UTC).isoformat(),
        }
        payouts = self.data.setdefault("payouts", [])
        payouts.append(row)
        payouts.sort(key=lambda item: str(item.get("date") or ""))
        self.data["payouts"] = payouts[-200:]
        self._save()
        return row

    def payouts(self, days: int = 90, *, now: datetime | None = None) -> list[dict[str, Any]]:
        current = (now or datetime.now(UTC)).date()
        cutoff = current - timedelta(days=max(1, days))
        rows: list[dict[str, Any]] = []
        for row in self.data.get("payouts", []):
            if not isinstance(row, dict):
                continue
            try:
                payout_day = date.fromisoformat(str(row.get("date") or ""))
            except ValueError:
                continue
            if payout_day >= cutoff:
                rows.append(dict(row))
        return rows

    def set_eligibility(self, key: str, value: str) -> None:
        clean_key = str(key or "").strip().lower()
        aliases = {
            "2fa": "two_factor",
            "twofactor": "two_factor",
            "verifiedfollowers": "verified_followers",
            "followers": "verified_followers",
        }
        clean_key = aliases.get(clean_key, clean_key)
        if clean_key not in {
            "premium",
            "stripe",
            "identity",
            "two_factor",
            "verified_followers",
        }:
            raise RuntimeError(
                "Eligibility key must be premium, stripe, identity, 2fa, or verified_followers."
            )
        eligibility = self.data.setdefault("eligibility", {})
        if clean_key == "verified_followers":
            try:
                parsed: Any = max(0, int(value))
            except ValueError as exc:
                raise RuntimeError("verified_followers must be a whole number.") from exc
        else:
            normalized = str(value or "").strip().lower()
            if normalized not in {"on", "off", "yes", "no", "true", "false", "1", "0"}:
                raise RuntimeError(f"{clean_key} must be on or off.")
            parsed = normalized in {"on", "yes", "true", "1"}
        eligibility[clean_key] = parsed
        self._save()

    def eligibility(self) -> dict[str, Any]:
        defaults = self._defaults()["eligibility"]
        raw = self.data.get("eligibility", {})
        return defaults | (raw if isinstance(raw, dict) else {})

    def record_health_error(self, error: str, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        rows = [
            row
            for row in self.data.get("health_errors", [])
            if isinstance(row, dict)
            and _parse_datetime(row.get("at")) >= current - timedelta(hours=1)
        ]
        rows.append({"at": current.isoformat(), "error": str(error or "")[:300]})
        self.data["health_errors"] = rows[-20:]
        should_pause = len(rows) >= 3
        if should_pause:
            self.data["pace_paused"] = True
        self._save()
        return should_pause

    def clear_health_errors(self) -> None:
        self.data["health_errors"] = []
        self._save()

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        defaults = self._defaults()
        defaults.update(payload)
        if isinstance(payload.get("eligibility"), dict):
            defaults["eligibility"] = self._defaults()["eligibility"] | payload["eligibility"]
        authors = defaults.get("watch_authors", {})
        if isinstance(authors, dict):
            for username, raw in list(authors.items()):
                row = raw if isinstance(raw, dict) else {}
                authors[username] = {
                    "username": str(row.get("username") or username),
                    "added_at": str(row.get("added_at") or ""),
                    # Schema v1 rows came only from the manual command, so they
                    # migrate to pinned and can never be auto-demoted.
                    "kind": str(row.get("kind") or "pinned"),
                    "last_queried_at": str(row.get("last_queried_at") or ""),
                    "auto_score": float(row.get("auto_score") or 0.0),
                } | {
                    key: value
                    for key, value in row.items()
                    if key not in {"username", "added_at", "kind", "last_queried_at", "auto_score"}
                }
        self.data = defaults

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)


def _clean_username(username: str) -> str:
    clean = str(username or "").strip().lstrip("@").lower()
    if not re.fullmatch(r"[a-z0-9_]{1,15}", clean):
        raise RuntimeError("Invalid X username.")
    return clean


def _parse_datetime(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
