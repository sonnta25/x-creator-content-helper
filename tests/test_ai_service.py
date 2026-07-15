import asyncio
import json
import time

from src.ai_service import create_ai_service
from src.config import Settings
from src.extension_bridge import (
    JOB_LEASE_SECONDS,
    ExtensionBridgeJob,
    ExtensionBridgeServer,
    _clean_final_image_prompt,
)
from src.extension_bridge_service import ExtensionBridgeService


def test_extension_bridge_provider_is_selected() -> None:
    settings = Settings(
        telegram_bot_token="123:ABC",
        content_provider="extension_bridge",
    )

    assert isinstance(create_ai_service(settings), ExtensionBridgeService)


def test_extension_bridge_service_covers_text_generation_methods() -> None:
    service = create_ai_service(
        Settings(
            telegram_bot_token="123:ABC",
            content_provider="extension_bridge",
        )
    )

    for method_name in (
        "generate_topic_post",
        "generate_topic_post_from_x_context",
        "generate_trend_post",
        "generate_trend_post_variants",
        "generate_daily_brief",
        "generate_retweet_remix",
        "generate_reply_from_text",
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


def test_clean_final_image_prompt_removes_long_guardrails() -> None:
    prompt = (
        "A founder reviewing AI dashboards in a small office. "
        "Realistic candid documentary photography style, natural lighting, "
        "real-world camera framing, believable anatomy. "
        + "extra detail " * 200
    )

    clean = _clean_final_image_prompt(
        prompt,
        "Create one square realistic image for this social post. Return the image only.",
    )

    assert clean.startswith("Create one square realistic image")
    assert "Create one square realistic image:" in clean
    assert "Realistic candid documentary photography style" not in clean
    assert "no previous attachments" in clean
    assert len(clean) < 1200


def test_bridge_claims_each_text_job_once_for_a_single_gemini_pass() -> None:
    async def exercise() -> None:
        server = ExtensionBridgeServer(Settings(telegram_bot_token="123:ABC"))
        job = ExtensionBridgeJob(
            id="job-1",
            kind="text",
            original_prompt="You are a Twitter/X Reply Engine. Return only ONE final reply.",
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
            kind="text",
            original_prompt="Reply task",
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


def test_bridge_routes_scheduled_trigger_to_automation_handler() -> None:
    class FakeAutomation:
        async def get_automation_config(self):
            return {"reply_targets_minutes": 45}

        async def trigger_replytargets(self, payload):
            return {"ok": True, "query": payload.get("query", "")}

        async def trigger_tweettrend3(self, payload):
            return {"ok": True}

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
