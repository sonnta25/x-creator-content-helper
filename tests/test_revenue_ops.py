import json
from datetime import UTC, datetime, timedelta

from src.models import XSearchResult
from src.revenue_ops import (
    RevenueOpsStore,
    assess_monetization_safety,
    reply_farming_guardrails,
)


def _result(text: str) -> XSearchResult:
    return XSearchResult(
        id=1,
        username="source",
        display_name="Source",
        text=text,
        created_at=datetime.now(UTC).isoformat(),
        url="https://x.com/source/status/1",
    )


def test_monetization_safety_blocks_betting_and_flags_disasters() -> None:
    assert assess_monetization_safety(_result("New Polymarket betting odds")).level == "red"
    assert assess_monetization_safety(_result("緊急地震速報 第4報")).level == "yellow"
    assert assess_monetization_safety(_result("A practical AI workflow")).level == "green"
    assert assess_monetization_safety(_result("著名な俳優の訃報と追悼コメント")).level == "yellow"


def test_reply_farming_guardrails_follow_risk_mode() -> None:
    strict = reply_farming_guardrails("strict")
    balanced = reply_farming_guardrails("balanced")
    open_mode = reply_farming_guardrails("open")

    assert strict.global_hourly_cap == 12
    assert strict.japanese_daily_cap == 20
    assert strict.japanese_hourly_cap == 2
    assert balanced.global_hourly_cap == 20
    assert balanced.japanese_daily_cap == 30
    assert balanced.japanese_hourly_cap == 6
    assert open_mode.global_hourly_cap is None
    assert open_mode.minimum_approval_gap_seconds == 0


def test_revenue_store_persists_watchlist_payout_and_eligibility(tmp_path) -> None:
    path = tmp_path / "revenue.json"
    store = RevenueOpsStore(path)

    assert store.add_watch_author("@Creator_1") == "creator_1"
    store.add_payout("2026-08-01", 125.4, "usd")
    store.set_eligibility("premium", "on")
    store.set_eligibility("verified_followers", "512")

    restored = RevenueOpsStore(path)
    assert restored.watch_authors() == ["creator_1"]
    assert restored.payouts(90, now=datetime(2026, 8, 4, tzinfo=UTC))[0]["amount"] == 125.4
    assert restored.eligibility()["premium"] is True
    assert restored.eligibility()["verified_followers"] == 512


def test_pacing_pauses_after_three_health_errors_and_can_resume(tmp_path) -> None:
    store = RevenueOpsStore(tmp_path / "revenue.json")
    now = datetime(2026, 8, 4, 9, tzinfo=UTC)

    assert store.record_health_error("one", now=now) is False
    assert store.record_health_error("two", now=now + timedelta(minutes=2)) is False
    assert store.record_health_error("three", now=now + timedelta(minutes=4)) is True
    assert store.pace_paused is True

    store.set_pace_paused(False)
    store.clear_health_errors()
    assert store.pace_paused is False


def _portfolio(
    username: str,
    *,
    replies: int = 3,
    measured: int = 3,
    median_views: int = 25_000,
    response_rate: float = 0.0,
    verified_proxy: float = 40.0,
    relationship: float = 20.0,
    green_rate: float = 1.0,
    last_interaction_at: str = "2026-08-03T12:00:00+00:00",
) -> dict:
    return {
        "username": username,
        "replies": replies,
        "measured": measured,
        "median_views": median_views,
        "author_response_rate": response_rate,
        "verified_audience_proxy": verified_proxy,
        "relationship_strength": relationship,
        "green_rate": green_rate,
        "last_interaction_at": last_interaction_at,
    }


def test_schema_one_watch_authors_migrate_to_pinned(tmp_path) -> None:
    path = tmp_path / "revenue.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "watch_authors": {
                    "manual_creator": {
                        "username": "manual_creator",
                        "added_at": "2026-08-01T00:00:00+00:00",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    store = RevenueOpsStore(path)

    assert store.watch_author_rows()[0]["kind"] == "pinned"


def test_auto_watch_promotes_good_authors_but_respects_pins_and_blocks(tmp_path) -> None:
    store = RevenueOpsStore(tmp_path / "revenue.json")
    store.pin_watch_author("manual_creator")
    store.block_watch_author("blocked_creator")
    now = datetime(2026, 8, 4, 12, tzinfo=UTC)

    changes = store.refresh_auto_authors(
        [
            _portfolio("good_creator"),
            _portfolio("manual_creator", median_views=1_000),
            _portfolio("blocked_creator", median_views=100_000),
            _portfolio("too_early", replies=1, measured=1, median_views=100_000),
        ],
        now=now,
    )

    rows = {row["username"]: row for row in store.watch_author_rows()}
    assert changes == {"promoted": ["good_creator"], "demoted": []}
    assert rows["manual_creator"]["kind"] == "pinned"
    assert rows["good_creator"]["kind"] == "auto"
    assert "blocked_creator" not in rows
    assert "too_early" not in rows


def test_auto_watch_demotes_weak_entries_without_touching_pins(tmp_path) -> None:
    store = RevenueOpsStore(tmp_path / "revenue.json")
    store.add_watch_author("weak_auto", pinned=False)
    store.pin_watch_author("weak_pin")
    now = datetime(2026, 8, 4, 12, tzinfo=UTC)
    weak = {
        "replies": 6,
        "measured": 6,
        "median_views": 1_000,
        "response_rate": 0.0,
        "verified_proxy": 10.0,
        "green_rate": 1.0,
        "last_interaction_at": now.isoformat(),
    }

    changes = store.refresh_auto_authors(
        [
            _portfolio(
                "weak_auto",
                replies=weak["replies"],
                measured=weak["measured"],
                median_views=weak["median_views"],
                response_rate=weak["response_rate"],
                verified_proxy=weak["verified_proxy"],
                green_rate=weak["green_rate"],
                last_interaction_at=weak["last_interaction_at"],
            ),
            _portfolio(
                "weak_pin",
                replies=weak["replies"],
                measured=weak["measured"],
                median_views=weak["median_views"],
                response_rate=weak["response_rate"],
                verified_proxy=weak["verified_proxy"],
                green_rate=weak["green_rate"],
                last_interaction_at=weak["last_interaction_at"],
            ),
        ],
        now=now,
    )

    assert changes["demoted"] == ["weak_auto"]
    assert store.watch_authors() == ["weak_pin"]


def test_watch_author_queries_rotate_and_mix_pinned_with_auto(tmp_path) -> None:
    store = RevenueOpsStore(tmp_path / "revenue.json")
    for username in ("pin_a", "pin_b", "pin_c"):
        store.pin_watch_author(username)
    for username in ("auto_a", "auto_b", "auto_c"):
        store.add_watch_author(username, pinned=False)

    first = store.query_watch_authors(
        limit=4,
        now=datetime(2026, 8, 4, 12, tzinfo=UTC),
    )
    second = store.query_watch_authors(
        limit=4,
        now=datetime(2026, 8, 4, 12, 5, tzinfo=UTC),
    )

    rows = {row["username"]: row["kind"] for row in store.watch_author_rows()}
    assert first != second
    assert sum(rows[name] == "pinned" for name in first) == 2
    assert sum(rows[name] == "auto" for name in first) == 2
    assert set(first + second) == set(rows)


def test_auto_watch_can_be_disabled(tmp_path) -> None:
    store = RevenueOpsStore(tmp_path / "revenue.json")
    store.set_auto_watch_enabled(False)

    changes = store.refresh_auto_authors([_portfolio("good_creator")])

    assert changes == {"promoted": [], "demoted": []}
    assert store.watch_authors() == []
