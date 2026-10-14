"""Senders that deliver engine output. ``CloudApiSender`` talks to Meta's Graph API;
``DryRunSender`` only logs, so the bot runs locally without any WhatsApp credentials."""

import json
import logging
from typing import Protocol

import httpx

from app.whatsapp.messages import ImageMessage, OutgoingMessage

log = logging.getLogger(__name__)


class WhatsAppError(RuntimeError):
    pass


class Sender(Protocol):
    async def send(self, to: str, message: OutgoingMessage) -> None: ...
    async def mark_read(self, message_id: str) -> None: ...


class CloudApiSender:
    def __init__(self, http: httpx.AsyncClient, *, access_token: str, phone_number_id: str,
                 api_version: str, base_url: str) -> None:
        if not access_token or not phone_number_id:
            raise ValueError("WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID are required in cloud mode")
        self._http = http
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._base = f"{base_url.rstrip('/')}/{api_version}/{phone_number_id}"

    async def send(self, to: str, message: OutgoingMessage) -> None:
        if isinstance(message, ImageMessage):
            payload = message.payload(await self._upload_media(message))
        else:
            payload = message.payload()
        await self._post_message({"messaging_product": "whatsapp", "recipient_type": "individual",
                                  "to": to, **payload})

    async def mark_read(self, message_id: str) -> None:
        try:
            await self._post_message({"messaging_product": "whatsapp", "status": "read", "message_id": message_id})
        except WhatsAppError:
            log.warning("Could not mark message %s as read", message_id)

    async def _post_message(self, body: dict) -> dict:
        resp = await self._http.post(f"{self._base}/messages", json=body, headers=self._headers)
        if resp.status_code >= 400:
            raise WhatsAppError(f"Send failed ({resp.status_code}): {resp.text}")
        return resp.json()

    async def _upload_media(self, image: ImageMessage) -> str:
        resp = await self._http.post(
            f"{self._base}/media",
            headers=self._headers,
            data={"messaging_product": "whatsapp", "type": image.mime_type},
            files={"file": (image.filename, image.content, image.mime_type)},
        )
        if resp.status_code >= 400:
            raise WhatsAppError(f"Media upload failed ({resp.status_code}): {resp.text}")
        return resp.json()["id"]


class DryRunSender:
    async def send(self, to: str, message: OutgoingMessage) -> None:
        if isinstance(message, ImageMessage):
            log.info("[dry-run] -> %s image %s (%d bytes) caption=%r", to, message.filename,
                     len(message.content), message.caption)
        else:
            log.info("[dry-run] -> %s %s", to, json.dumps(message.payload(), ensure_ascii=False))

    async def mark_read(self, message_id: str) -> None:
        pass
