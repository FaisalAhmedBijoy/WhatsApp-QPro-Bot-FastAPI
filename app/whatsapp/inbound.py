"""Parse WhatsApp Cloud API webhook payloads into simple IncomingMessage objects.
https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/payload-examples
"""

import hashlib
import hmac
from dataclasses import dataclass


@dataclass
class IncomingMessage:
    message_id: str
    wa_id: str  # sender's WhatsApp number, e.g. "8801711000000"
    text: str = ""  # typed text (empty for taps)
    reply_id: str | None = None  # id of the tapped list row / button
    reply_title: str = ""
    profile_name: str = ""
    unsupported_type: str | None = None  # e.g. "image", "sticker" - we only handle text and taps


def verify_signature(raw_body: bytes, signature_header: str | None, app_secret: str) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))


def parse_webhook(payload: dict) -> list[IncomingMessage]:
    """Extract user messages; delivery/read status callbacks are ignored."""
    messages: list[IncomingMessage] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {
                c.get("wa_id"): c.get("profile", {}).get("name", "") for c in value.get("contacts", [])
            }
            for msg in value.get("messages", []):
                messages.append(_parse_message(msg, names))
    return messages


def _parse_message(msg: dict, names: dict[str, str]) -> IncomingMessage:
    incoming = IncomingMessage(
        message_id=msg.get("id", ""), wa_id=msg.get("from", ""), profile_name=names.get(msg.get("from"), "")
    )
    kind = msg.get("type")
    if kind == "text":
        incoming.text = msg.get("text", {}).get("body", "")
    elif kind == "interactive":
        interactive = msg.get("interactive", {})
        reply = interactive.get(interactive.get("type", ""), {})  # list_reply / button_reply
        incoming.reply_id = reply.get("id")
        incoming.reply_title = reply.get("title", "")
    elif kind == "button":  # quick-reply button on a template message
        incoming.reply_id = msg.get("button", {}).get("payload")
        incoming.reply_title = msg.get("button", {}).get("text", "")
    else:
        incoming.unsupported_type = kind or "unknown"
    return incoming
