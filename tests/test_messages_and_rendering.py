import io
from datetime import datetime

import pytest
from PIL import Image

from app.rendering.token_image import HEIGHT, WIDTH, TokenTicket, render_token_image
from app.whatsapp.messages import Button, ButtonsMessage, ListMessage, Row


def test_list_titles_are_clipped_to_whatsapp_limits():
    msg = ListMessage(body="b", rows=[Row("x", "A very long branch name that exceeds", "d" * 100)])
    row = msg.payload()["interactive"]["action"]["sections"][0]["rows"][0]
    assert len(row["title"]) <= 24 and len(row["description"]) <= 72


def test_list_rejects_more_than_ten_rows():
    with pytest.raises(ValueError):
        ListMessage(body="b", rows=[Row(str(i), str(i)) for i in range(11)])


def test_buttons_reject_more_than_three():
    with pytest.raises(ValueError):
        ButtonsMessage(body="b", buttons=[Button(str(i), str(i)) for i in range(4)])


def test_token_image_renders_portrait_png():
    png = render_token_image(TokenTicket("G002", "Gulshan Branch, Dhaka", "Cash Deposit", datetime(2026, 10, 14, 10, 42), 5))
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG" and img.size == (WIDTH, HEIGHT)
    assert len(png) < 5 * 1024 * 1024  # WhatsApp image limit


def test_token_image_handles_long_values():
    render_token_image(TokenTicket("LOAN1234", "A Very Long Branch Name That Needs Wrapping, Chapai Nawabganj",
                                   "Loan Application", datetime.now(), None))
