"""Local WhatsApp simulator (dev only, disable with ENABLE_SIMULATOR=false).

GET  /dev/chat      - browser chat UI that behaves like WhatsApp lists/buttons.
POST /dev/simulate  - run one message through the real engine; returns the exact WhatsApp payloads.
"""

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.whatsapp.inbound import IncomingMessage
from app.whatsapp.messages import ImageMessage

router = APIRouter(prefix="/dev", tags=["simulator"])
STATIC = Path(__file__).resolve().parent.parent / "static"


class SimulatedMessage(BaseModel):
    wa_id: str = "8801700000000"
    text: str = ""
    reply_id: str | None = None
    reply_title: str = ""


@router.get("/chat", include_in_schema=False)
async def chat_ui() -> FileResponse:
    return FileResponse(STATIC / "simulator.html")


@router.post("/simulate")
async def simulate(body: SimulatedMessage, request: Request) -> dict:
    media_dir: Path = request.app.state.settings.media_dir
    msg = IncomingMessage(message_id=uuid.uuid4().hex, wa_id=body.wa_id, text=body.text,
                          reply_id=body.reply_id, reply_title=body.reply_title)
    out = []
    for item in await request.app.state.engine.handle(msg):
        if isinstance(item, ImageMessage):
            media_dir.mkdir(parents=True, exist_ok=True)
            name = f"{uuid.uuid4().hex[:8]}_{item.filename}"
            (media_dir / name).write_bytes(item.content)
            out.append({"type": "image", "image": {"url": f"/dev/media/{name}", "caption": item.caption}})
        else:
            out.append(item.payload())
    return {"messages": out}


@router.get("/media/{name}", include_in_schema=False)
async def media(name: str, request: Request) -> FileResponse:
    path = (request.app.state.settings.media_dir / name).resolve()
    if path.parent != request.app.state.settings.media_dir.resolve() or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/png")
