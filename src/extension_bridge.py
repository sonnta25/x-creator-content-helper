from __future__ import annotations

import asyncio
import base64
import json
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

from src.config import Settings
from src.extension_prompts import gemini_single_pass_prompt
from src.models import ImageAttachment


MAX_BODY_BYTES = 16 * 1024 * 1024
JOB_LEASE_SECONDS = 75
JOB_TIMEOUT_MIN_GRACE_SECONDS = 120
JOB_TIMEOUT_GRACE_RATIO = 0.25


class AutomationBridgeHandler(Protocol):
    async def get_automation_config(self) -> dict[str, Any]: ...

    async def trigger_replytargets(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def trigger_replyvideo(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def next_approved_action(self) -> dict[str, Any] | None: ...

    async def finish_approved_action(
        self,
        approval_id: str,
        *,
        success: bool,
        error: str = "",
    ) -> None: ...


@dataclass
class ExtensionBridgeJob:
    id: str
    kind: str
    original_prompt: str
    phase: str = "draft_pending"
    draft_prompt: str = ""
    final_prompt: str = ""
    last_heartbeat_at: float = 0.0
    claim_attempts: int = 0
    attachments: list[dict[str, str]] = field(default_factory=list)
    future: asyncio.Future[str | bytes] = field(default_factory=asyncio.Future)


class ExtensionBridgeServer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._jobs: OrderedDict[str, ExtensionBridgeJob] = OrderedDict()
        self._server: asyncio.AbstractServer | None = None
        self._lock = asyncio.Lock()
        self._automation_handler: AutomationBridgeHandler | None = None

    def set_automation_handler(self, handler: AutomationBridgeHandler) -> None:
        self._automation_handler = handler

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._handle_connection,
            self.settings.extension_bridge_host,
            self.settings.extension_bridge_port,
        )

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def submit_text_job(
        self,
        prompt: str,
        *,
        attachments: list[ImageAttachment] | None = None,
    ) -> str:
        await self.start()
        job = ExtensionBridgeJob(
            id=secrets.token_urlsafe(12),
            kind="text",
            original_prompt=prompt,
            phase="final_pending",
            final_prompt=gemini_single_pass_prompt(prompt),
            attachments=_attachment_payloads(attachments or []),
        )
        async with self._lock:
            self._jobs[job.id] = job
        return await self._wait_for_job(job)

    async def submit_image_job(self, prompt: str) -> bytes:
        await self.start()
        image_prompt = _clean_final_image_prompt(
            prompt,
            self.settings.gemini_image_prompt_prefix,
        )
        job = ExtensionBridgeJob(
            id=secrets.token_urlsafe(12),
            kind="image",
            original_prompt=prompt,
            phase="final_pending",
            final_prompt=image_prompt,
        )
        async with self._lock:
            self._jobs[job.id] = job
        result = await self._wait_for_job(job)
        if not isinstance(result, bytes):
            raise RuntimeError("Extension returned text instead of image bytes.")
        return result

    async def _wait_for_job(self, job: ExtensionBridgeJob) -> str | bytes:
        configured_timeout = float(self.settings.extension_bridge_timeout_seconds)
        started_at = time.monotonic()
        soft_deadline = started_at + configured_timeout
        hard_deadline = soft_deadline + max(
            JOB_TIMEOUT_MIN_GRACE_SECONDS,
            configured_timeout * JOB_TIMEOUT_GRACE_RATIO,
        )
        try:
            while True:
                now = time.monotonic()
                remaining = soft_deadline - now
                if remaining > 0:
                    try:
                        return await asyncio.wait_for(
                            asyncio.shield(job.future),
                            timeout=remaining,
                        )
                    except asyncio.TimeoutError:
                        pass

                now = time.monotonic()
                heartbeat_is_recent = (
                    job.last_heartbeat_at > 0
                    and now - job.last_heartbeat_at <= JOB_LEASE_SECONDS
                )
                if heartbeat_is_recent and now < hard_deadline:
                    # Chrome has claimed the job and is still alive. Give the
                    # provider enough room to finish its own configured timeout
                    # plus tab/composer startup, but never wait indefinitely.
                    soft_deadline = min(
                        hard_deadline,
                        now + JOB_LEASE_SECONDS,
                    )
                    continue

                elapsed = max(0, int(now - started_at))
                if job.claim_attempts == 0:
                    detail = (
                        "Chrome never claimed the queued job. Check that the updated "
                        "extension is loaded and Automation or Auto Run is ON."
                    )
                elif heartbeat_is_recent:
                    detail = (
                        "Chrome kept sending heartbeats, but Gemini did not finish before "
                        "the bounded extended deadline. Check the Gemini tab for a stuck "
                        "response or usage limit."
                    )
                else:
                    detail = (
                        "Chrome claimed the job, but its heartbeat stopped. The extension "
                        "worker, Chrome tab, or browser likely stopped responding."
                    )
                raise RuntimeError(
                    "Extension bridge timed out waiting for Chrome after "
                    f"{elapsed}s. {detail}"
                ) from asyncio.TimeoutError()
        finally:
            async with self._lock:
                self._jobs.pop(job.id, None)

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request = await self._read_request(reader)
            response = await self._route(request)
        except Exception as exc:
            response = _json_response(500, {"error": str(exc)})
        writer.write(response)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def _read_request(self, reader: asyncio.StreamReader) -> dict[str, Any]:
        header_bytes = await reader.readuntil(b"\r\n\r\n")
        header_text = header_bytes.decode("iso-8859-1")
        lines = header_text.split("\r\n")
        method, target, _version = lines[0].split(" ", 2)
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if not line or ":" not in line:
                continue
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
        length = int(headers.get("content-length", "0") or "0")
        if length > MAX_BODY_BYTES:
            raise RuntimeError("Request body is too large.")
        body = await reader.readexactly(length) if length else b""
        return {"method": method, "target": target, "headers": headers, "body": body}

    async def _route(self, request: dict[str, Any]) -> bytes:
        method = request["method"].upper()
        parsed = urlparse(request["target"])
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        if method == "OPTIONS":
            return _empty_response(204)
        if path == "/health" and method == "GET":
            return _json_response(
                200,
                {
                    "ok": True,
                    "pending_jobs": len(self._jobs),
                    "job_phases": _job_phase_counts(self._jobs.values()),
                    "provider": "extension_bridge",
                },
            )

        if not self._authorized(query, request["headers"]):
            return _json_response(401, {"error": "Invalid extension bridge token."})

        if path == "/jobs/next" and method == "GET":
            return await self._next_job()

        if path == "/automation/config" and method == "GET":
            handler = self._require_automation_handler()
            return _json_response(200, await handler.get_automation_config())

        if path == "/automation/triggers/replytargets" and method == "POST":
            handler = self._require_automation_handler()
            result = await handler.trigger_replytargets(_json_body(request["body"]))
            return _json_response(202, result)

        if path == "/automation/triggers/replyvideo" and method == "POST":
            handler = self._require_automation_handler()
            result = await handler.trigger_replyvideo(_json_body(request["body"]))
            return _json_response(202, result)

        if path == "/automation/approvals/next" and method == "GET":
            handler = self._require_automation_handler()
            action = await handler.next_approved_action()
            return _json_response(200, {"action": action})

        parts = [part for part in path.split("/") if part]
        if len(parts) == 3 and parts[0] == "jobs":
            job_id = parts[1]
            action = parts[2]
            payload = _json_body(request["body"])
            if action == "result" and method == "POST":
                return await self._accept_result(job_id, payload)
            if action == "error" and method == "POST":
                return await self._accept_error(job_id, payload)
            if action == "heartbeat" and method == "POST":
                return await self._accept_heartbeat(job_id)

        if len(parts) == 4 and parts[:2] == ["automation", "approvals"]:
            handler = self._require_automation_handler()
            approval_id = parts[2]
            action = parts[3]
            payload = _json_body(request["body"])
            if action == "complete" and method == "POST":
                await handler.finish_approved_action(approval_id, success=True)
                return _json_response(200, {"ok": True})
            if action == "error" and method == "POST":
                await handler.finish_approved_action(
                    approval_id,
                    success=False,
                    error=str(payload.get("error", "")),
                )
                return _json_response(200, {"ok": True})

        return _json_response(404, {"error": "Not found."})

    def _require_automation_handler(self) -> AutomationBridgeHandler:
        if self._automation_handler is None:
            raise RuntimeError("Automation is not ready. Start the Telegram bot first.")
        return self._automation_handler

    def _authorized(self, query: dict[str, list[str]], headers: dict[str, str]) -> bool:
        expected = self.settings.extension_bridge_token
        token = headers.get("x-extension-bridge-token") or (query.get("token") or [""])[0]
        return bool(expected) and secrets.compare_digest(token, expected)

    async def _next_job(self) -> bytes:
        async with self._lock:
            now = time.monotonic()
            for candidate in self._jobs.values():
                if (
                    candidate.phase in {"draft_running", "final_running"}
                    and candidate.last_heartbeat_at > 0
                    and now - candidate.last_heartbeat_at > JOB_LEASE_SECONDS
                    and not candidate.future.done()
                ):
                    candidate.phase = (
                        "draft_pending"
                        if candidate.phase == "draft_running"
                        else "final_pending"
                    )
                    candidate.last_heartbeat_at = 0.0
            job = next(
                (
                    candidate
                    for candidate in self._jobs.values()
                    if candidate.phase in {"draft_pending", "final_pending"}
                ),
                None,
            )
            if job is not None:
                job.phase = (
                    "draft_running"
                    if job.phase == "draft_pending"
                    else "final_running"
                )
                job.last_heartbeat_at = now
                job.claim_attempts += 1
        if job is None:
            return _json_response(200, {"job": None})
        if job.kind == "image" or job.phase == "final_running":
            return _json_response(
                200,
                {
                    "job": {
                        "id": job.id,
                        "kind": job.kind,
                        "stage": "final",
                        "provider": "gemini",
                        "final_prompt": job.final_prompt,
                        "grok_prompt": job.final_prompt,
                        "attachments": job.attachments,
                    }
                },
            )
        return _json_response(
            200,
            {
                "job": {
                    "id": job.id,
                    "kind": job.kind,
                    "stage": "draft",
                    "draft_prompt": job.draft_prompt,
                }
            },
        )

    async def _accept_result(self, job_id: str, payload: dict[str, Any]) -> bytes:
        job = await self._job(job_id)
        if job.future.done():
            return _json_response(200, {"ok": True})
        if job.kind == "image":
            image = _decode_image_payload(payload)
            if not image:
                keys = ", ".join(sorted(payload.keys())) or "none"
                return _json_response(
                    400,
                    {"error": f"Missing image data. Payload keys: {keys}."},
                )
            job.future.set_result(image)
        else:
            output = str(payload.get("output", "")).strip()
            if not output:
                return _json_response(400, {"error": "Missing final output."})
            job.future.set_result(output)
        job.phase = "completed"
        return _json_response(200, {"ok": True})

    async def _accept_error(self, job_id: str, payload: dict[str, Any]) -> bytes:
        job = await self._job(job_id)
        message = str(payload.get("error", "")).strip() or "Extension job failed."
        if not job.future.done():
            job.future.set_exception(RuntimeError(message))
        job.phase = "failed"
        return _json_response(200, {"ok": True})

    async def _accept_heartbeat(self, job_id: str) -> bytes:
        job = await self._job(job_id)
        if job.future.done():
            return _json_response(200, {"ok": True, "phase": job.phase})
        if job.phase not in {"draft_running", "final_running"}:
            return _json_response(
                409,
                {"error": f"Job is not running. Current phase: {job.phase}."},
            )
        job.last_heartbeat_at = time.monotonic()
        return _json_response(
            200,
            {"ok": True, "phase": job.phase, "claim_attempts": job.claim_attempts},
        )

    async def _job(self, job_id: str) -> ExtensionBridgeJob:
        async with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise RuntimeError("Unknown or expired job.")
        return job


_SERVERS: dict[tuple[str, int], ExtensionBridgeServer] = {}


def get_extension_bridge(settings: Settings) -> ExtensionBridgeServer:
    key = (settings.extension_bridge_host, settings.extension_bridge_port)
    if key not in _SERVERS:
        _SERVERS[key] = ExtensionBridgeServer(settings)
    return _SERVERS[key]


def _clean_final_image_prompt(prompt: str, prefix: str, limit: int = 900) -> str:
    clean_prompt = " ".join(str(prompt or "").split())
    clean_prefix = " ".join(str(prefix or "").split())
    if not clean_prefix:
        clean_prefix = "Create one square realistic image. Return the image only."

    cut_markers = (
        "Realistic candid documentary photography style",
        "Avoid logos",
        "Avoid readable text",
        "Avoid cartoon",
        "Avoid anime",
        "Avoid 3D render",
    )
    lowered = clean_prompt.lower()
    cut_at = len(clean_prompt)
    for marker in cut_markers:
        index = lowered.find(marker.lower())
        if index != -1:
            cut_at = min(cut_at, index)
    clean_prompt = clean_prompt[:cut_at].strip(" .;:-")
    if len(clean_prompt) > limit:
        clean_prompt = clean_prompt[:limit].rsplit(" ", 1)[0].strip(" .;:-")

    return (
        f"{clean_prefix}\n\n"
        f"Create one square realistic image: {clean_prompt}.\n"
        "Use no reference images, no previous attachments, no carousel, no extra text."
    ).strip()


def _clean_grok_image_prompt(prompt: str, prefix: str, limit: int = 900) -> str:
    return _clean_final_image_prompt(prompt, prefix, limit)


def _attachment_payloads(
    attachments: list[ImageAttachment],
) -> list[dict[str, str]]:
    if len(attachments) > 5:
        raise RuntimeError("A Gemini text job supports at most five image attachments.")
    payloads: list[dict[str, str]] = []
    total_bytes = 0
    for index, attachment in enumerate(attachments, start=1):
        mime_type = str(attachment.mime_type or "").strip().lower()
        if mime_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise RuntimeError(f"Unsupported attachment type: {mime_type or 'empty'}.")
        data = bytes(attachment.data)
        if len(data) < 100 or len(data) > 1_000_000:
            raise RuntimeError(
                f"Attachment {index} must contain 100-1,000,000 bytes."
            )
        total_bytes += len(data)
        if total_bytes > 4_000_000:
            raise RuntimeError("Image attachments exceed the 4 MB job limit.")
        safe_name = "".join(
            char for char in str(attachment.name or "")
            if char.isalnum() or char in {"-", "_", "."}
        ).strip(".")
        if not safe_name:
            safe_name = f"frame-{index:02d}.jpg"
        payloads.append(
            {
                "name": safe_name[:96],
                "mime_type": mime_type,
                "data_url": (
                    f"data:{mime_type};base64,"
                    f"{base64.b64encode(data).decode('ascii')}"
                ),
            }
        )
    return payloads


def _json_body(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    parsed = json.loads(body.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise RuntimeError("Request JSON must be an object.")
    return parsed


def _job_phase_counts(jobs: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for job in jobs:
        counts[job.phase] = counts.get(job.phase, 0) + 1
    return counts


def _decode_image_payload(payload: dict[str, Any]) -> bytes:
    raw = str(
        payload.get("image_base64")
        or payload.get("image_data_url")
        or payload.get("data_url")
        or ""
    ).strip()
    if not raw:
        return b""
    if raw.startswith("data:"):
        _prefix, raw = raw.split(",", 1)
    if len(raw) < 100:
        return b""
    try:
        decoded = base64.b64decode(raw, validate=True)
    except Exception:
        return b""
    if len(decoded) < 100:
        return b""
    return decoded


def _json_response(status: int, payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = _response_headers(status, "application/json; charset=utf-8", len(body))
    return headers + body


def _empty_response(status: int) -> bytes:
    return _response_headers(status, "text/plain; charset=utf-8", 0)


def _response_headers(status: int, content_type: str, length: int) -> bytes:
    reason = {
        200: "OK",
        202: "Accepted",
        204: "No Content",
        400: "Bad Request",
        401: "Unauthorized",
        404: "Not Found",
        409: "Conflict",
        500: "Internal Server Error",
    }.get(status, "OK")
    lines = [
        f"HTTP/1.1 {status} {reason}",
        f"Content-Type: {content_type}",
        f"Content-Length: {length}",
        "Access-Control-Allow-Origin: *",
        "Access-Control-Allow-Methods: GET, POST, OPTIONS",
        "Access-Control-Allow-Headers: Content-Type, X-Extension-Bridge-Token",
        "Connection: close",
        "",
        "",
    ]
    return "\r\n".join(lines).encode("utf-8")
