from __future__ import annotations

from src.config import Settings
from src.content_service import ContentService
from src.extension_bridge_service import ExtensionBridgeService


def create_ai_service(settings: Settings) -> ContentService:
    if settings.content_provider != "extension_bridge":
        raise RuntimeError("Only CONTENT_PROVIDER=extension_bridge is supported.")
    return ExtensionBridgeService(settings)
