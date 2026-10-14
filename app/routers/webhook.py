"""WhatsApp Cloud API webhook.

GET  /webhook  - Meta's one-time verification handshake.
POST /webhook  - incoming messages. We verify the signature, answer 200 straight away and do the
                 work in the background (Meta retries if it doesn't get a fast 200).
"""

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response

from app.conversation.engine import ConversationEngine
from app.conversation.store import KeyValueStore
from app.whatsapp.client import Sender
from app.whatsapp.inbound import IncomingMessage, parse_webhook, verify_signature
from app.whatsapp.messages import TextMessage

log = logging.getLogger(__name__)
router = APIRouter(tags=["whatsapp"])

DEDUP_TTL_SECONDS = 60 * 60 * 24
GENERIC_ERROR = "Sorry, something went wrong on our side. Please send *Hi* to try again."


@router.get("/webhook")
async def verify_webhook(
    request: Request,
    mode: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
) -> Response:
    if mode == "subscribe" and token == request.app.state.settings.whatsapp_verify_token:
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook")
async def receive_webhook(request: Request, background: BackgroundTasks) -> dict:
    settings = request.app.state.settings
    raw = await request.body()
    if settings.whatsapp_app_secret:
        if not verify_signature(raw, request.headers.get("X-Hub-Signature-256"), settings.whatsapp_app_secret):
            raise HTTPException(status_code=403, detail="Invalid signature")
    elif settings.whatsapp_mode == "cloud":
        # Unsigned requests could make us message arbitrary numbers with the real token.
        log.error("Rejecting webhook: WHATSAPP_APP_SECRET must be set in cloud mode")
        raise HTTPException(status_code=403, detail="Webhook signature verification not configured")
    else:
        log.warning("WHATSAPP_APP_SECRET not set - webhook signature NOT verified (dry-run only)")

    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    messages = parse_webhook(payload)
    if messages:
        background.add_task(process_messages, messages, request.app.state.engine,
                            request.app.state.sender, request.app.state.store)
    return {"status": "received"}


async def process_messages(messages: list[IncomingMessage], engine: ConversationEngine,
                           sender: Sender, store: KeyValueStore) -> None:
    for msg in messages:
        # Meta can deliver the same message more than once.
        if not await store.add_if_absent(f"seen:{msg.message_id}", DEDUP_TTL_SECONDS):
            log.info("Skipping duplicate message %s", msg.message_id)
            continue
        await sender.mark_read(msg.message_id)
        try:
            for out in await engine.handle(msg):
                await sender.send(msg.wa_id, out)
        except Exception:
            log.exception("Failed to handle message %s", msg.message_id)
            try:
                await sender.send(msg.wa_id, TextMessage(GENERIC_ERROR))
            except Exception:
                log.exception("Failed to send error reply to %s", msg.wa_id)
