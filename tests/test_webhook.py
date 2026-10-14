import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.whatsapp.inbound import parse_webhook

SECRET = "app-secret"


def _text_payload(text: str, msg_id: str = "wamid.1") -> dict:
    return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp",
        "contacts": [{"wa_id": "8801700000000", "profile": {"name": "Rahim"}}],
        "messages": [{"from": "8801700000000", "id": msg_id, "timestamp": "1", "type": "text", "text": {"body": text}}],
    }}]}]}


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def client(settings):
    settings.whatsapp_app_secret = SECRET
    with TestClient(create_app(settings)) as c:
        yield c


def test_verification_handshake(client):
    ok = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    bad = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "42"})
    assert bad.status_code == 403


def test_rejects_bad_signature(client):
    body = json.dumps(_text_payload("Hi")).encode()
    resp = client.post("/webhook", content=body, headers={"X-Hub-Signature-256": "sha256=deadbeef",
                                                          "Content-Type": "application/json"})
    assert resp.status_code == 403


def test_signed_message_is_processed_once(client):
    sent = []

    class Recorder:
        async def send(self, to, message):
            sent.append((to, message))

        async def mark_read(self, message_id):
            pass

    client.app.state.sender = Recorder()
    body = json.dumps(_text_payload("Hi")).encode()
    headers = {"X-Hub-Signature-256": _sign(body), "Content-Type": "application/json"}
    assert client.post("/webhook", content=body, headers=headers).status_code == 200
    assert client.post("/webhook", content=body, headers=headers).status_code == 200  # Meta re-delivery
    assert len(sent) == 2  # welcome text + division list, only once
    assert sent[0][0] == "8801700000000"


def test_parse_list_reply_and_unsupported():
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "880", "id": "a", "type": "interactive",
         "interactive": {"type": "list_reply", "list_reply": {"id": "div:dhaka", "title": "Dhaka"}}},
        {"from": "880", "id": "b", "type": "interactive",
         "interactive": {"type": "button_reply", "button_reply": {"id": "cf:yes", "title": "Confirm"}}},
        {"from": "880", "id": "c", "type": "sticker", "sticker": {}},
    ]}}]}]}
    a, b, c = parse_webhook(payload)
    assert (a.reply_id, b.reply_id, c.unsupported_type) == ("div:dhaka", "cf:yes", "sticker")


def test_status_callbacks_are_ignored():
    payload = {"entry": [{"changes": [{"value": {"statuses": [{"id": "x", "status": "read"}]}}]}]}
    assert parse_webhook(payload) == []


def test_simulator_end_to_end(client):
    def sim(**kw):
        return client.post("/dev/simulate", json={"wa_id": "8801711111111", **kw}).json()["messages"]

    sim(text="Hi")
    sim(text="gulshan")
    sim(reply_id="svc:withdrawal")
    text, image = sim(reply_id="cf:yes")
    assert "*W001*" in text["text"]["body"]
    png = client.get(image["image"]["url"])
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
