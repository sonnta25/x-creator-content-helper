import asyncio
import json

from src.ai_service import create_ai_service
from src.config import Settings
from src.extension_bridge import ExtensionBridgeJob, ExtensionBridgeServer, _clean_final_image_prompt
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

        duplicate = json.loads((await server._next_job()).split(b"\r\n\r\n", 1)[1])
        assert duplicate["job"] is None

    asyncio.run(exercise())
