"""Channel-neutral outgoing messages produced by the conversation engine, plus their
WhatsApp Cloud API payloads. WhatsApp limits are enforced here so the engine never has to care:
https://developers.facebook.com/docs/whatsapp/cloud-api/messages/interactive-list-messages
"""

from dataclasses import dataclass, field

LIST_MAX_ROWS = 10
LIST_ROW_TITLE_MAX = 24
LIST_ROW_DESC_MAX = 72
LIST_BUTTON_MAX = 20
HEADER_MAX = 60
FOOTER_MAX = 60
REPLY_BUTTONS_MAX = 3
REPLY_BUTTON_TITLE_MAX = 20
CAPTION_MAX = 1024


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


@dataclass
class Row:
    id: str
    title: str
    description: str = ""


@dataclass
class Button:
    id: str
    title: str


@dataclass
class TextMessage:
    body: str

    def payload(self) -> dict:
        return {"type": "text", "text": {"body": self.body, "preview_url": False}}


@dataclass
class ListMessage:
    body: str
    rows: list[Row]
    button: str = "Choose"
    header: str = ""
    footer: str = ""
    section_title: str = ""

    def __post_init__(self) -> None:
        if not 1 <= len(self.rows) <= LIST_MAX_ROWS:
            raise ValueError(f"A list message needs 1-{LIST_MAX_ROWS} rows, got {len(self.rows)}")

    def payload(self) -> dict:
        section: dict = {
            "rows": [
                {
                    "id": r.id,
                    "title": _clip(r.title, LIST_ROW_TITLE_MAX),
                    **({"description": _clip(r.description, LIST_ROW_DESC_MAX)} if r.description else {}),
                }
                for r in self.rows
            ]
        }
        if self.section_title:
            section["title"] = _clip(self.section_title, LIST_ROW_TITLE_MAX)
        interactive: dict = {
            "type": "list",
            "body": {"text": self.body},
            "action": {"button": _clip(self.button, LIST_BUTTON_MAX), "sections": [section]},
        }
        if self.header:
            interactive["header"] = {"type": "text", "text": _clip(self.header, HEADER_MAX)}
        if self.footer:
            interactive["footer"] = {"text": _clip(self.footer, FOOTER_MAX)}
        return {"type": "interactive", "interactive": interactive}


@dataclass
class ButtonsMessage:
    body: str
    buttons: list[Button]
    header: str = ""
    footer: str = ""

    def __post_init__(self) -> None:
        if not 1 <= len(self.buttons) <= REPLY_BUTTONS_MAX:
            raise ValueError(f"Reply buttons need 1-{REPLY_BUTTONS_MAX} buttons, got {len(self.buttons)}")

    def payload(self) -> dict:
        interactive: dict = {
            "type": "button",
            "body": {"text": self.body},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": b.id, "title": _clip(b.title, REPLY_BUTTON_TITLE_MAX)}}
                    for b in self.buttons
                ]
            },
        }
        if self.header:
            interactive["header"] = {"type": "text", "text": _clip(self.header, HEADER_MAX)}
        if self.footer:
            interactive["footer"] = {"text": _clip(self.footer, FOOTER_MAX)}
        return {"type": "interactive", "interactive": interactive}


@dataclass
class ImageMessage:
    content: bytes = field(repr=False)
    filename: str
    caption: str = ""
    mime_type: str = "image/png"

    def payload(self, media_id: str) -> dict:
        image: dict = {"id": media_id}
        if self.caption:
            image["caption"] = _clip(self.caption, CAPTION_MAX)
        return {"type": "image", "image": image}


OutgoingMessage = TextMessage | ListMessage | ButtonsMessage | ImageMessage
