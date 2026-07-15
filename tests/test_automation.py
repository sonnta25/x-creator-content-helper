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
