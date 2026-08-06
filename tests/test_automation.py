from datetime import UTC, datetime, timedelta

import pytest

from src.automation import AutomationApprovalStore


def test_approval_requires_matching_chat_and_is_claimed_once() -> None:
    store = AutomationApprovalStore()
    approval = store.create(
        kind="reply",
        text="A useful reply.",
        chat_id=123,
        target_url="https://x.com/user/status/1",
    )

    with pytest.raises(RuntimeError, match="different Telegram chat"):
        store.decide(approval.id, approve=True, chat_id=999, user_id=123)

    with pytest.raises(RuntimeError, match="Only the user"):
        store.decide(approval.id, approve=True, chat_id=123, user_id=999)

    store.decide(approval.id, approve=True, chat_id=123, user_id=123)
    claimed = store.claim_next()

    assert claimed is approval
    assert claimed.status == "dispatching"
    assert store.claim_next() is None


def test_approval_can_be_rejected_without_becoming_dispatchable() -> None:
    store = AutomationApprovalStore()
    approval = store.create(kind="post", text="Post draft", chat_id=123)

    store.decide(approval.id, approve=False, chat_id=123, user_id=123)

    assert approval.status == "rejected"
    assert store.claim_next() is None


def test_expired_approval_cannot_be_dispatched() -> None:
    store = AutomationApprovalStore()
    approval = store.create(kind="post", text="Post draft", chat_id=123)
    approval.created_at = datetime.now(UTC) - timedelta(hours=1)

    assert store.claim_next() is None
    assert approval.status == "expired"


def test_completed_approval_records_extension_result() -> None:
    store = AutomationApprovalStore()
    approval = store.create(kind="post", text="Post draft", chat_id=123)
    store.decide(approval.id, approve=True, chat_id=123, user_id=123)
    store.claim_next()

    completed = store.finish(approval.id, success=True)

    assert completed.status == "completed"


def test_mobile_approval_is_recorded_without_entering_extension_queue() -> None:
    store = AutomationApprovalStore()
    approval = store.create(
        kind="reply",
        text="Reply draft",
        chat_id=123,
        target_url="https://x.com/user/status/1",
    )

    store.decide(
        approval.id,
        approve=True,
        chat_id=123,
        user_id=123,
        destination="mobile",
    )

    assert approval.status == "mobile_approved"
    assert store.claim_next() is None
    assert store.has_active_target("https://x.com/user/status/1") is True


def test_mobile_approval_and_target_dedupe_survive_restart(tmp_path) -> None:
    path = tmp_path / "automation-approvals.json"
    store = AutomationApprovalStore(path)
    approval = store.create(
        kind="reply",
        text="Reply draft",
        chat_id=123,
        target_url="https://x.com/user/status/42",
    )
    store.decide(
        approval.id,
        approve=True,
        chat_id=123,
        user_id=123,
        destination="mobile",
    )

    restored = AutomationApprovalStore(path)

    assert restored.get(approval.id).status == "mobile_approved"
    assert restored.has_active_target("https://x.com/user/status/42") is True


def test_reply_tracking_metadata_and_decision_time_survive_restart(tmp_path) -> None:
    path = tmp_path / "automation-approvals.json"
    store = AutomationApprovalStore(path)
    approval = store.create(
        kind="reply",
        text="Reply draft",
        chat_id=123,
        target_url="https://x.com/user/status/42",
        metadata={"reply_strategy": "natural_humor", "root_views": 1200},
    )
    store.decide(
        approval.id,
        approve=True,
        chat_id=123,
        user_id=123,
        destination="mobile",
    )

    restored = AutomationApprovalStore(path).get(approval.id)

    assert restored is not None
    assert restored.decided_at is not None
    assert restored.metadata["reply_strategy"] == "natural_humor"
    assert restored.metadata["root_views"] == 1200


def test_mobile_approval_can_be_retried_after_a_failed_telegram_edit(tmp_path) -> None:
    store = AutomationApprovalStore(tmp_path / "approvals.json")
    approval = store.create(kind="post", text="Draft", chat_id=10, approver_user_id=20)

    first = store.decide(
        approval.id,
        approve=True,
        chat_id=10,
        user_id=20,
        destination="mobile",
    )
    retried = store.decide(
        approval.id,
        approve=True,
        chat_id=10,
        user_id=20,
        destination="mobile",
    )

    assert first.status == "mobile_approved"
    assert retried.status == "mobile_approved"


def test_pending_draft_can_be_revised_then_marked_published(tmp_path) -> None:
    path = tmp_path / "approvals.json"
    store = AutomationApprovalStore(path)
    approval = store.create(
        kind="reply",
        text="First draft",
        chat_id=10,
        target_url="https://x.com/source/status/42",
    )

    store.update_text(approval.id, "Shorter, stronger draft")
    store.update_metadata(approval.id, revision_count=1)
    store.decide(
        approval.id,
        approve=True,
        chat_id=10,
        user_id=10,
        destination="mobile",
    )
    store.finish_mobile(approval.id, published=True)

    restored = AutomationApprovalStore(path).get(approval.id)
    assert restored is not None
    assert restored.text == "Shorter, stronger draft"
    assert restored.metadata["revision_count"] == 1
    assert restored.status == "published"
    assert store.has_active_target("https://x.com/source/status/42") is True


def test_reply_only_migration_archives_posts_and_releases_stale_mobile_lock() -> None:
    store = AutomationApprovalStore()
    legacy_post = store.create(kind="post", text="Old post", chat_id=1)
    reply = store.create(
        kind="reply",
        text="Old mobile reply",
        chat_id=1,
        target_url="https://x.com/source/status/99",
    )
    store.decide(
        reply.id,
        approve=True,
        chat_id=1,
        user_id=1,
        destination="mobile",
    )
    current = datetime.now(UTC)
    legacy_post.created_at = current - timedelta(days=1)
    reply.decided_at = current - timedelta(hours=8)

    result = store.migrate_reply_only(stale_mobile_hours=6, now=current)

    assert result == {"archived_posts": 1, "released_mobile": 1}
    assert legacy_post.status == "archived"
    assert reply.status == "not_found"
    assert store.has_active_target(reply.target_url) is False
