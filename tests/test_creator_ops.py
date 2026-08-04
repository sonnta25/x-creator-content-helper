from datetime import UTC, datetime, timedelta

from src.creator_ops import ReplyWatchStore
from src.models import XSearchResult


def _candidate(
    tweet_id: int,
    *,
    viral: float = 45.0,
    opportunity: float = 62.0,
    observations: int = 1,
    language: str = "en",
) -> XSearchResult:
    return XSearchResult(
        id=tweet_id,
        username="creator",
        display_name="Creator",
        text="A fresh specific post",
        created_at=datetime.now(UTC).isoformat(),
        url=f"https://x.com/creator/status/{tweet_id}",
        language=language,
        viral_score=viral,
        reply_opportunity_score=opportunity,
        momentum_observation_count=observations,
        view_count=2_000,
        reply_count=5,
    )


def test_watchlist_waits_for_second_observation_before_drafting(tmp_path) -> None:
    path = tmp_path / "watch.json"
    store = ReplyWatchStore(path)
    candidate = _candidate(1)
    now = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)

    ready, watching = store.classify([candidate], now=now)
    assert ready == []
    assert watching == [candidate]

    restored = ReplyWatchStore(path)
    ready, watching = restored.classify(
        [candidate],
        now=now + timedelta(minutes=15),
    )
    assert ready == [candidate]
    assert watching == []


def test_watchlist_allows_exceptional_first_observation_and_marks_drafted(tmp_path) -> None:
    store = ReplyWatchStore(tmp_path / "watch.json")
    candidate = _candidate(2, viral=80.0, opportunity=82.0)

    ready, watching = store.classify([candidate])
    store.mark_drafted(candidate.url)

    assert ready == [candidate]
    assert watching == []
    assert store.candidates_for_refresh() == []


def test_watchlist_allows_qualified_japanese_candidate_earlier(tmp_path) -> None:
    store = ReplyWatchStore(tmp_path / "watch.json")
    japanese = _candidate(3, viral=54.0, opportunity=64.0, language="ja")
    english = _candidate(4, viral=54.0, opportunity=64.0, language="en")

    ready, watching = store.classify([japanese, english])

    assert ready == [japanese]
    assert watching == [english]


def test_watchlist_exposes_candidates_for_refresh_and_expires_old_rows(tmp_path) -> None:
    store = ReplyWatchStore(tmp_path / "watch.json")
    candidate = _candidate(5, language="ja")
    now = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
    store.classify([candidate], now=now)

    rows = store.candidates_for_refresh(
        languages=["ja"],
        max_age_minutes=360,
        now=now + timedelta(minutes=15),
    )
    expired = store.candidates_for_refresh(
        languages=["ja"],
        max_age_minutes=360,
        now=now + timedelta(minutes=361),
    )

    assert [row["tweet_id"] for row in rows] == [5]
    assert expired == []


def test_watchlist_keeps_video_reservoir_separate_and_reports_inventory(tmp_path) -> None:
    store = ReplyWatchStore(tmp_path / "watch.json")
    target = _candidate(6)
    video = XSearchResult(**{**_candidate(7).__dict__, "has_video": True})

    store.classify([target], source_type="replytargets")
    store.classify([video], source_type="replyvideo")

    assert [row["tweet_id"] for row in store.candidates_for_refresh()] == [6]
    assert [
        row["tweet_id"]
        for row in store.candidates_for_refresh(source_type="replyvideo")
    ] == [7]
    assert store.inventory()["watching"] == 2

    store.mark_expired(video.url, reason="thread saturated")
    assert store.inventory()["watching"] == 1
    assert store.inventory()["expired"] == 1
