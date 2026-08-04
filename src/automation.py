from __future__ import annotations

import secrets
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path


APPROVAL_TTL_MINUTES = 30


@dataclass
class AutomationApproval:
    id: str
    kind: str
    text: str
    chat_id: int
    approver_user_id: int
    target_url: str = ""
    target_label: str = ""
    status: str = "pending"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    decided_at: datetime | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    error: str = ""

    @property
    def expires_at(self) -> datetime:
        return self.created_at + timedelta(minutes=APPROVAL_TTL_MINUTES)

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(UTC)) >= self.expires_at

    def as_extension_payload(self) -> dict[str, str]:
        return {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "target_url": self.target_url,
            "target_label": self.target_label,
        }


class AutomationApprovalStore:
    def __init__(self, persist_path: str | Path | None = None) -> None:
        self._items: dict[str, AutomationApproval] = {}
        self._persist_path = Path(persist_path) if persist_path else None
        self._load()

    def create(
        self,
        *,
        kind: str,
        text: str,
        chat_id: int,
        approver_user_id: int | None = None,
        target_url: str = "",
        target_label: str = "",
        metadata: dict[str, object] | None = None,
    ) -> AutomationApproval:
        if kind not in {"reply", "post"}:
            raise RuntimeError(f"Unsupported approval kind: {kind}")
        clean_text = str(text or "").strip()
        if not clean_text:
            raise RuntimeError("Approval text cannot be empty.")
        approval = AutomationApproval(
            id=secrets.token_urlsafe(9),
            kind=kind,
            text=clean_text,
            chat_id=int(chat_id),
            approver_user_id=int(
                approver_user_id if approver_user_id is not None else chat_id
            ),
            target_url=str(target_url or "").strip(),
            target_label=str(target_label or "").strip(),
            metadata=dict(metadata or {}),
        )
        self._items[approval.id] = approval
        self.prune()
        self._save()
        return approval

    def get(self, approval_id: str) -> AutomationApproval | None:
        approval = self._items.get(str(approval_id or ""))
        if approval is not None and approval.is_expired() and approval.status not in {
            "completed",
            "rejected",
            "mobile_approved",
            "published",
            "not_found",
        }:
            approval.status = "expired"
            self._save()
        return approval

    def decide(
        self,
        approval_id: str,
        *,
        approve: bool,
        chat_id: int,
        user_id: int,
        destination: str = "computer",
    ) -> AutomationApproval:
        approval = self.get(approval_id)
        if approval is None:
            raise RuntimeError("Unknown approval request.")
        if approval.chat_id != int(chat_id):
            raise RuntimeError("This approval belongs to a different Telegram chat.")
        if approval.approver_user_id != int(user_id):
            raise RuntimeError("Only the user who requested this draft can approve it.")
        # A Telegram message edit can fail after the mobile decision was saved
        # (for example, because a pre-filled X URL is too long). Let the same
        # authorized user press the original mobile button again to recover it.
        if (
            approval.status == "mobile_approved"
            and approve
            and destination == "mobile"
        ):
            return approval
        if approval.status != "pending":
            raise RuntimeError(f"Approval is already {approval.status}.")
        if approve:
            approval.status = "mobile_approved" if destination == "mobile" else "approved"
        else:
            approval.status = "rejected"
        approval.decided_at = datetime.now(UTC)
        self._save()
        return approval

    def has_active_target(self, target_url: str) -> bool:
        clean_url = str(target_url or "").strip()
        if not clean_url:
            return False
        self.prune()
        return any(
            approval.target_url == clean_url
            and approval.status
            in {
                "pending",
                "approved",
                "dispatching",
                "completed",
                "mobile_approved",
                "published",
            }
            for approval in self._items.values()
        )

    def items(self) -> list[AutomationApproval]:
        self.prune()
        return list(self._items.values())

    def migrate_reply_only(
        self,
        *,
        stale_mobile_hours: int = 6,
        now: datetime | None = None,
    ) -> dict[str, int]:
        """Archive removed post workflows and release stale mobile reply locks."""
        current = now or datetime.now(UTC)
        archived_posts = 0
        released_mobile = 0
        for approval in self._items.values():
            age = current - (approval.decided_at or approval.created_at)
            if approval.kind == "post" and approval.status not in {
                "archived",
                "rejected",
                "expired",
                "failed",
            }:
                approval.status = "archived"
                approval.error = "Archived after the bot moved to a reply-only workflow."
                archived_posts += 1
                continue
            if (
                approval.kind == "reply"
                and approval.status == "mobile_approved"
                and age >= timedelta(hours=max(2, stale_mobile_hours))
            ):
                approval.status = "not_found"
                approval.error = (
                    "Posting could not be confirmed before the stale approval window ended."
                )
                released_mobile += 1
        if archived_posts or released_mobile:
            self._save()
        return {
            "archived_posts": archived_posts,
            "released_mobile": released_mobile,
        }

    def pending_by_metadata(self, key: str, value: object) -> list[AutomationApproval]:
        return [
            approval
            for approval in self.items()
            if approval.status == "pending"
            and (approval.metadata or {}).get(key) == value
        ]

    def cancel_pending_by_metadata(self, key: str, value: object) -> int:
        cancelled = 0
        for approval in self._items.values():
            if (
                approval.status == "pending"
                and (approval.metadata or {}).get(key) == value
            ):
                approval.status = "rejected"
                approval.error = "Cancelled with the reply session."
                approval.decided_at = datetime.now(UTC)
                cancelled += 1
        if cancelled:
            self._save()
        return cancelled

    def update_text(self, approval_id: str, text: str) -> AutomationApproval:
        approval = self.get(approval_id)
        if approval is None:
            raise RuntimeError("Unknown approval request.")
        if approval.status != "pending":
            raise RuntimeError("Only a pending approval can be revised.")
        clean = str(text or "").strip()
        if not clean:
            raise RuntimeError("Approval text cannot be empty.")
        approval.text = clean
        self._save()
        return approval

    def update_metadata(
        self,
        approval_id: str,
        **values: object,
    ) -> AutomationApproval:
        approval = self.get(approval_id)
        if approval is None:
            raise RuntimeError("Unknown approval request.")
        approval.metadata.update(values)
        self._save()
        return approval

    def finish_mobile(
        self,
        approval_id: str,
        *,
        published: bool,
    ) -> AutomationApproval:
        approval = self.get(approval_id)
        if approval is None:
            raise RuntimeError("Unknown approval request.")
        if approval.status not in {"mobile_approved", "completed"}:
            return approval
        approval.status = "published" if published else "not_found"
        self._save()
        return approval

    def claim_next(self) -> AutomationApproval | None:
        self.prune()
        for approval in self._items.values():
            if approval.status == "approved":
                approval.status = "dispatching"
                self._save()
                return approval
        return None

    def finish(self, approval_id: str, *, success: bool, error: str = "") -> AutomationApproval:
        approval = self.get(approval_id)
        if approval is None:
            raise RuntimeError("Unknown approval request.")
        if approval.status not in {"approved", "dispatching"}:
            raise RuntimeError(f"Approval cannot be completed from {approval.status}.")
        approval.status = "completed" if success else "failed"
        approval.error = str(error or "").strip()
        self._save()
        return approval

    def prune(self, *, keep: int = 200) -> None:
        for approval in self._items.values():
            if approval.is_expired() and approval.status in {"pending", "approved"}:
                approval.status = "expired"
        if len(self._items) <= keep:
            return
        removable = [
            key
            for key, approval in self._items.items()
            if approval.status
            in {
                "completed",
                "rejected",
                "expired",
                "failed",
                "mobile_approved",
                "published",
                "not_found",
                "archived",
            }
        ]
        for key in removable[: max(0, len(self._items) - keep)]:
            self._items.pop(key, None)
        self._save()

    def _load(self) -> None:
        if self._persist_path is None or not self._persist_path.exists():
            return
        try:
            payload = json.loads(self._persist_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        rows = payload.get("approvals", []) if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                approval = AutomationApproval(
                    id=str(row["id"]),
                    kind=str(row["kind"]),
                    text=str(row["text"]),
                    chat_id=int(row["chat_id"]),
                    approver_user_id=int(row["approver_user_id"]),
                    target_url=str(row.get("target_url", "")),
                    target_label=str(row.get("target_label", "")),
                    status=str(row.get("status", "pending")),
                    created_at=datetime.fromisoformat(str(row["created_at"])),
                    decided_at=(
                        datetime.fromisoformat(str(row["decided_at"]))
                        if row.get("decided_at")
                        else None
                    ),
                    metadata=(
                        dict(row.get("metadata", {}))
                        if isinstance(row.get("metadata", {}), dict)
                        else {}
                    ),
                    error=str(row.get("error", "")),
                )
            except (KeyError, TypeError, ValueError):
                continue
            self._items[approval.id] = approval

    def _save(self) -> None:
        if self._persist_path is None:
            return
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "approvals": [
                {
                    "id": approval.id,
                    "kind": approval.kind,
                    "text": approval.text,
                    "chat_id": approval.chat_id,
                    "approver_user_id": approval.approver_user_id,
                    "target_url": approval.target_url,
                    "target_label": approval.target_label,
                    "status": approval.status,
                    "created_at": approval.created_at.isoformat(),
                    "decided_at": (
                        approval.decided_at.isoformat() if approval.decided_at else None
                    ),
                    "metadata": approval.metadata,
                    "error": approval.error,
                }
                for approval in self._items.values()
            ]
        }
        temp_path = self._persist_path.with_suffix(self._persist_path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self._persist_path)
