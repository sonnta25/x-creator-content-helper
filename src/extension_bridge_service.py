from __future__ import annotations

from src.config import Settings
from src.content_service import ContentService
from src.extension_bridge import get_extension_bridge
from src.prompt_safety import looks_like_prompt_leak
from src.models import ImageAttachment


class ExtensionBridgeService(ContentService):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.bridge = get_extension_bridge(settings)

    async def _generate_text(self, prompt: str) -> str:
        output = await self.bridge.submit_text_job(prompt)
        if looks_like_prompt_leak(output):
            raise RuntimeError("AI returned prompt instructions instead of final content.")
        return output

    async def _generate_text_with_images(
        self,
        prompt: str,
        attachments: list[ImageAttachment],
    ) -> str:
        output = await self.bridge.submit_text_job(
            prompt,
            attachments=attachments,
        )
        if looks_like_prompt_leak(output):
            raise RuntimeError("AI returned prompt instructions instead of final content.")
        return output

    async def generate_image(self, prompt: str) -> bytes:
        if self.settings.image_provider != "extension_bridge":
            return await super().generate_image(prompt)
        return await self.bridge.submit_image_job(prompt)
