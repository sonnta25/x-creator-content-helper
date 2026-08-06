import asyncio
import json
import time

import pytest

import src.extension_bridge as extension_bridge_module
from src.ai_service import create_ai_service
from src.config import Settings
from src.extension_bridge import (
    JOB_LEASE_SECONDS,
    ExtensionBridgeJob,
    ExtensionBridgeServer,
    _attachment_payloads,
)
from src.extension_bridge_service import ExtensionBridgeService
from src.models import ImageAttachment


def test_extension_bridge_provider_is_selected() -> None:
    settings = Settings(
        telegram_bot_token="123:ABC",
        content_provider="extension_bridge",
    )

    assert isinstance(create_ai_service(settings), ExtensionBridgeService)


def test_extension_bridge_serializes_provider_jobs_before_timeout_starts() -> None:
    server = ExtensionBridgeServer(Settings(telegram_bot_token="123:ABC"))
    active = 0
    max_active = 0

    async def no_start():
        return None

    async def wait_for_job(job):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return job.final_prompt

    server.start = no_start
    server._wait_for_job = wait_for_job

    async def submit_both():
        return await asyncio.gather(
            server.submit_text_job("first"),
            server.submit_text_job("second"),
        )

    results = asyncio.run(submit_both())

    assert len(results) == 2
    assert max_active == 1


def test_extension_bridge_service_covers_text_generation_methods() -> None:
    service = create_ai_service(
        Settings(
            telegram_bot_token="123:ABC",
            content_provider="extension_bridge",
        )
    )

    for method_name in (
        "generate_reply_from_text",
        "generate_reply_revision",
        "generate_reply_targets",
    ):
        assert hasattr(service, method_name)


def test_extension_bridge_service_rejects_prompt_leak_output() -> None:
    class FakeBridge:
        async def submit_text_job(self, prompt: str) -> str:
            return (
                "You are a Twitter/X Tweet QA + Humanizer. "
                "Your input is ONE generated tweet. "
                "Your job is NOT to create a new topic."
            )

    service = ExtensionBridgeService(Settings(telegram_bot_token="123:ABC"))
    service.bridge = FakeBridge()

    try:
        asyncio.run(service._generate_text("Write a tweet."))
    except RuntimeError as exc:
        assert "prompt instructions instead of final content" in str(exc)
    else:
        raise AssertionError("Expected prompt leak output to be rejected")


def test_bridge_claims_each_text_job_once_for_a_single_gemini_pass() -> None:
    async def exercise() -> None:
        server = ExtensionBridgeServer(Settings(telegram_bot_token="123:ABC"))
        job = ExtensionBridgeJob(
            id="job-1",
            phase="final_pending",
            final_prompt="Write one final reply.",
        )
        server._jobs[job.id] = job

        first = json.loads((await server._next_job()).split(b"\r\n\r\n", 1)[1])
        assert first["job"]["stage"] == "final"
        assert first["job"]["final_prompt"] == "Write one final reply."
        assert job.phase == "final_running"
        assert job.claim_attempts == 1
        assert job.last_heartbeat_at > 0

        previous_heartbeat = job.last_heartbeat_at
        await server._accept_heartbeat(job.id)
        assert job.last_heartbeat_at >= previous_heartbeat

        duplicate = json.loads((await server._next_job()).split(b"\r\n\r\n", 1)[1])
        assert duplicate["job"] is None

    asyncio.run(exercise())


def test_bridge_requeues_a_job_after_its_extension_lease_expires() -> None:
    async def exercise() -> None:
        server = ExtensionBridgeServer(Settings(telegram_bot_token="123:ABC"))
        job = ExtensionBridgeJob(
            id="job-stale",
            phase="final_running",
            final_prompt="Write one reply.",
            last_heartbeat_at=time.monotonic() - JOB_LEASE_SECONDS - 1,
            claim_attempts=1,
        )
        server._jobs[job.id] = job

        reclaimed = json.loads((await server._next_job()).split(b"\r\n\r\n", 1)[1])

        assert reclaimed["job"]["id"] == job.id
        assert job.phase == "final_running"
        assert job.claim_attempts == 2

    asyncio.run(exercise())


def test_bridge_waiter_requeues_stalled_job_for_extension_recovery(monkeypatch) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(extension_bridge_module, "JOB_LEASE_SECONDS", 0.01)
        monkeypatch.setattr(extension_bridge_module, "JOB_HEALTH_POLL_SECONDS", 0.005)
        server = ExtensionBridgeServer(
            Settings(
                telegram_bot_token="123:ABC",
                extension_bridge_timeout_seconds=1,
            )
        )
        job = ExtensionBridgeJob(
            id="job-interrupted",
            phase="final_running",
            final_prompt="Write one reply.",
            last_heartbeat_at=time.monotonic() - 0.02,
            claim_attempts=1,
        )
        server._jobs[job.id] = job

        waiter = asyncio.create_task(server._wait_for_job(job))
        for _ in range(20):
            if job.phase == "final_pending":
                break
            await asyncio.sleep(0.005)

        assert job.phase == "final_pending"
        reclaimed = json.loads((await server._next_job()).split(b"\r\n\r\n", 1)[1])
        assert reclaimed["job"]["id"] == job.id
        assert job.claim_attempts == 2
        job.future.set_result("Recovered result")
        assert await waiter == "Recovered result"

    asyncio.run(exercise())


def test_bridge_allows_original_worker_to_resume_a_requeued_lease() -> None:
    async def exercise() -> None:
        server = ExtensionBridgeServer(Settings(telegram_bot_token="123:ABC"))
        job = ExtensionBridgeJob(
            id="job-resumed-heartbeat",
            phase="final_pending",
            final_prompt="Write one reply.",
            last_heartbeat_at=0.0,
            claim_attempts=1,
        )
        server._jobs[job.id] = job

        response = await server._accept_heartbeat(job.id)
        payload = json.loads(response.split(b"\r\n\r\n", 1)[1])

        assert payload["ok"] is True
        assert payload["recovered_lease"] is True
        assert job.phase == "final_running"
        assert job.last_heartbeat_at > 0

    asyncio.run(exercise())


def test_bridge_gives_a_recovered_worker_a_fresh_stall_window(monkeypatch) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(extension_bridge_module, "JOB_LEASE_SECONDS", 0.03)
        monkeypatch.setattr(extension_bridge_module, "JOB_HEALTH_POLL_SECONDS", 0.005)
        monkeypatch.setattr(extension_bridge_module, "JOB_RECOVERY_WAIT_SECONDS", 0.2)
        server = ExtensionBridgeServer(
            Settings(
                telegram_bot_token="123:ABC",
                extension_bridge_timeout_seconds=1,
            )
        )
        job = ExtensionBridgeJob(
            id="job-recovered-twice",
            phase="final_running",
            final_prompt="Write one reply.",
            last_heartbeat_at=time.monotonic() - 0.04,
            claim_attempts=1,
        )
        server._jobs[job.id] = job
        waiter = asyncio.create_task(server._wait_for_job(job))

        for _ in range(40):
            if job.phase == "final_pending":
                break
            await asyncio.sleep(0.005)
        assert job.phase == "final_pending"

        # Keep the recovered lease healthy beyond most of the first recovery
        # window. A later interruption must start a new window, not reuse the
        # almost-expired timestamp from the first stall.
        for _ in range(8):
            await server._accept_heartbeat(job.id)
            await asyncio.sleep(0.02)

        for _ in range(40):
            if job.phase == "final_pending":
                break
            await asyncio.sleep(0.005)
        assert job.phase == "final_pending"
        await asyncio.sleep(0.05)

        reclaimed = json.loads((await server._next_job()).split(b"\r\n\r\n", 1)[1])
        assert reclaimed["job"]["id"] == job.id
        job.future.set_result("Recovered after a second interruption")
        assert await waiter == "Recovered after a second interruption"

    asyncio.run(exercise())


def test_bridge_stopped_heartbeat_fails_at_recovery_deadline(monkeypatch) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(extension_bridge_module, "JOB_LEASE_SECONDS", 0.005)
        monkeypatch.setattr(extension_bridge_module, "JOB_HEALTH_POLL_SECONDS", 0.005)
        monkeypatch.setattr(extension_bridge_module, "JOB_RECOVERY_WAIT_SECONDS", 0.03)
        server = ExtensionBridgeServer(
            Settings(
                telegram_bot_token="123:ABC",
                extension_bridge_timeout_seconds=1,
            )
        )
        job = ExtensionBridgeJob(
            id="job-not-reclaimed",
            phase="final_running",
            final_prompt="Write one reply.",
            last_heartbeat_at=time.monotonic() - 0.02,
            claim_attempts=1,
        )
        server._jobs[job.id] = job
        started = time.monotonic()

        with pytest.raises(RuntimeError, match="heartbeat stopped.*did not reclaim"):
            await server._wait_for_job(job)

        assert time.monotonic() - started < 0.2

    asyncio.run(exercise())


def test_bridge_active_heartbeat_extends_soft_timeout(monkeypatch) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(
            extension_bridge_module,
            "JOB_TIMEOUT_MIN_GRACE_SECONDS",
            0.5,
        )
        server = ExtensionBridgeServer(
            Settings(
                telegram_bot_token="123:ABC",
                extension_bridge_timeout_seconds=0.05,
            )
        )
        job = ExtensionBridgeJob(
            id="job-active",
            phase="final_running",
            final_prompt="Write one reply.",
            last_heartbeat_at=time.monotonic(),
            claim_attempts=1,
        )
        server._jobs[job.id] = job

        waiter = asyncio.create_task(server._wait_for_job(job))
        await asyncio.sleep(0.08)
        job.future.set_result("Finished after the original timeout")

        assert await waiter == "Finished after the original timeout"

    asyncio.run(exercise())


def test_bridge_health_poll_does_not_timeout_an_unclaimed_job_early(
    monkeypatch,
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(extension_bridge_module, "JOB_HEALTH_POLL_SECONDS", 0.01)
        server = ExtensionBridgeServer(
            Settings(
                telegram_bot_token="123:ABC",
                extension_bridge_timeout_seconds=0.2,
            )
        )
        job = ExtensionBridgeJob(
            id="job-claimed-later",
            phase="final_pending",
            final_prompt="Write one reply.",
        )
        server._jobs[job.id] = job

        waiter = asyncio.create_task(server._wait_for_job(job))
        await asyncio.sleep(0.04)
        assert waiter.done() is False

        claimed = json.loads((await server._next_job()).split(b"\r\n\r\n", 1)[1])
        assert claimed["job"]["id"] == job.id
        job.future.set_result("Chrome claimed after several health polls")
        assert await waiter == "Chrome claimed after several health polls"

    asyncio.run(exercise())


def test_bridge_timeout_explains_when_chrome_never_claimed_job() -> None:
    async def exercise() -> None:
        server = ExtensionBridgeServer(
            Settings(
                telegram_bot_token="123:ABC",
                extension_bridge_timeout_seconds=0.01,
            )
        )
        job = ExtensionBridgeJob(
            id="job-unclaimed",
            phase="final_pending",
            final_prompt="Write one reply.",
        )
        server._jobs[job.id] = job

        with pytest.raises(RuntimeError, match="Chrome never claimed"):
            await server._wait_for_job(job)

        assert job.future.cancelled() is False

    asyncio.run(exercise())


def test_bridge_routes_scheduled_trigger_to_automation_handler() -> None:
    class FakeAutomation:
        async def get_automation_config(self):
            return {"reply_targets_minutes": 45}

        async def trigger_replytargets(self, payload):
            return {"ok": True, "query": payload.get("query", "")}

        async def trigger_replyvideo(self, payload):
            return {"ok": True, "video_query": payload.get("query", "")}

        async def trigger_followtargets(self, payload):
            return {"ok": True, "minutes": payload.get("follow_targets_minutes")}

        async def next_approved_action(self):
            return None

        async def finish_approved_action(self, approval_id, *, success, error=""):
            return None

    async def exercise() -> None:
        settings = Settings(
            telegram_bot_token="123:ABC",
            extension_bridge_token="test-token",
        )
        server = ExtensionBridgeServer(settings)
        server.set_automation_handler(FakeAutomation())
        body = b'{"query":"AI agents"}'
        response = await server._route(
            {
                "method": "POST",
                "target": "/automation/triggers/replytargets",
                "headers": {"x-extension-bridge-token": "test-token"},
                "body": body,
            }
        )

        assert response.startswith(b"HTTP/1.1 202 Accepted")
        assert b'"query": "AI agents"' in response

        video_response = await server._route(
            {
                "method": "POST",
                "target": "/automation/triggers/replyvideo",
                "headers": {"x-extension-bridge-token": "test-token"},
                "body": b'{"query":"football"}',
            }
        )
        assert video_response.startswith(b"HTTP/1.1 202 Accepted")
        assert b'"video_query": "football"' in video_response

        follow_response = await server._route(
            {
                "method": "POST",
                "target": "/automation/triggers/followtargets",
                "headers": {"x-extension-bridge-token": "test-token"},
                "body": b'{"follow_targets_minutes":20}',
            }
        )
        assert follow_response.startswith(b"HTTP/1.1 202 Accepted")
        assert b'"minutes": 20' in follow_response

    asyncio.run(exercise())


def test_bridge_serializes_bounded_image_attachments() -> None:
    payloads = _attachment_payloads(
        [ImageAttachment("candidate 1.jpg", "image/jpeg", b"x" * 200)]
    )

    assert payloads[0]["name"] == "candidate1.jpg"
    assert payloads[0]["mime_type"] == "image/jpeg"
    assert payloads[0]["data_url"].startswith("data:image/jpeg;base64,")


def test_next_text_job_exposes_frame_attachments_to_extension() -> None:
    async def exercise() -> None:
        server = ExtensionBridgeServer(Settings(telegram_bot_token="123:ABC"))
        job = ExtensionBridgeJob(
            id="visual-job",
            phase="final_pending",
            final_prompt="Write grounded replies",
            attachments=[
                {
                    "name": "candidate-1-frame-01.jpg",
                    "mime_type": "image/jpeg",
                    "data_url": "data:image/jpeg;base64," + ("eA==" * 30),
                }
            ],
        )
        server._jobs[job.id] = job

        response = await server._next_job()
        payload = json.loads(response.split(b"\r\n\r\n", 1)[1])

        assert payload["job"]["attachments"][0]["name"] == (
            "candidate-1-frame-01.jpg"
        )

    asyncio.run(exercise())


def test_bridge_exposes_telegram_automation_config() -> None:
    class FakeAutomation:
        async def get_automation_config(self):
            return {"reply_targets_minutes": 45}

    async def exercise() -> None:
        settings = Settings(
            telegram_bot_token="123:ABC",
            extension_bridge_token="test-token",
        )
        server = ExtensionBridgeServer(settings)
        server.set_automation_handler(FakeAutomation())
        response = await server._route(
            {
                "method": "GET",
                "target": "/automation/config",
                "headers": {"x-extension-bridge-token": "test-token"},
                "body": b"",
            }
        )

        assert response.startswith(b"HTTP/1.1 200 OK")
        assert b'"reply_targets_minutes": 45' in response

    asyncio.run(exercise())
