from datetime import UTC, datetime, timedelta

from src.creator_ops import ReplyWatchStore
from src.models import XSearchResult


def _candidate(
    tweet_id: int,
    *,
    viral: float = 45.0,
    opportunity: float = 62.0,
    observations: int = 1,
) -> XSearchResult:
    return XSearchResult(
        id=tweet_id,
        username="creator",
        display_name="Creator",
        text="A fresh specific post",
        created_at=datetime.now(UTC).isoformat(),
        url=f"https://x.com/creator/status/{tweet_id}",
        language="en",
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
    assert store.watching() == []
