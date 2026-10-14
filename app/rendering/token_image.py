"""Render the token image in the style of the Queue Pro kiosk "Your Token" screen:
blue gradient background with soft light streaks, logo top-left, white rounded card with
"Your Token / # G002 / Thank You". Portrait 1080x1350 so it reads well on a phone, with a
details section (branch, service, time) added under the card's original content.
"""

import io
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ASSETS = Path(__file__).resolve().parent.parent / "assets"
FONT_REGULAR = ASSETS / "fonts" / "NotoSans-Regular.ttf"
FONT_SEMIBOLD = ASSETS / "fonts" / "NotoSans-SemiBold.ttf"
FONT_BOLD = ASSETS / "fonts" / "NotoSans-Bold.ttf"
LOGO = ASSETS / "images" / "queuepro_logo.png"

WIDTH, HEIGHT = 1080, 1350

# Colours sampled from the kiosk screenshots.
GRADIENT_STOPS = [(0.0, (3, 222, 255)), (0.45, (3, 150, 240)), (0.75, (3, 90, 210)), (1.0, (3, 38, 170))]
CARD_BG = (255, 255, 255)
TEXT_LABEL = (75, 85, 99)  # "Your Token"
TEXT_TOKEN = (17, 24, 39)  # "# G002"
TEXT_THANKS = (55, 65, 81)  # "Thank You"
TEXT_MUTED = (107, 114, 128)
DIVIDER = (229, 231, 235)
ACCENT_GOLD = (226, 166, 36)  # kiosk button colour


@dataclass
class TokenTicket:
    token_no: str
    branch_name: str
    service_name: str
    issued_at: datetime
    waiting_count: int | None = None


@lru_cache(maxsize=32)
def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def _interpolate(stops: list[tuple[float, tuple[int, int, int]]], t: float) -> tuple[int, int, int]:
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        if t <= p1:
            f = (t - p0) / (p1 - p0)
            return tuple(round(a + (b - a) * f) for a, b in zip(c0, c1))
    return stops[-1][1]


@lru_cache(maxsize=1)
def _background() -> Image.Image:
    """Diagonal gradient (light top-right -> deep blue bottom-left) plus blurred light streaks."""
    # 0..255 ramp along the top-right -> bottom-left diagonal: average of a top->bottom ramp
    # and a right->left ramp.
    vertical = Image.linear_gradient("L").resize((WIDTH, HEIGHT))
    right_to_left = Image.linear_gradient("L").rotate(-90).resize((WIDTH, HEIGHT))
    ramp = ImageChops.add(vertical, right_to_left, scale=2)
    luts = [[_interpolate(GRADIENT_STOPS, i / 255)[ch] for i in range(256)] for ch in range(3)]
    bg = Image.merge("RGB", [ramp.point(lut) for lut in luts])

    # Soft curved light streaks near the bottom, like the kiosk wallpaper.
    streaks = Image.new("L", (WIDTH, HEIGHT), 0)
    draw = ImageDraw.Draw(streaks)
    for i, (alpha, width) in enumerate([(70, 5), (55, 4), (45, 3), (35, 3)]):
        off = i * 46
        draw.arc((-900, 760 + off, 2300, 2400 + off), start=200, end=340, fill=alpha, width=width)
    glow = streaks.filter(ImageFilter.GaussianBlur(10))
    streaks = ImageChops.lighter(streaks.filter(ImageFilter.GaussianBlur(1.2)), glow)
    white = Image.new("RGB", (WIDTH, HEIGHT), (255, 255, 255))
    return Image.composite(white, bg, streaks)


def _fit_font(draw: ImageDraw.ImageDraw, text: str, path: Path, size: int, max_width: int) -> ImageFont.FreeTypeFont:
    while size > 12:
        font = _font(path, size)
        if draw.textlength(text, font=font) <= max_width:
            return font
        size -= 4
    return _font(path, size)


def _centered(draw: ImageDraw.ImageDraw, y: int, text: str, font: ImageFont.FreeTypeFont, fill) -> None:
    draw.text((WIDTH // 2, y), text, font=font, fill=fill, anchor="mt")


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int, max_lines: int = 2) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width or not line:
            line = candidate
        else:
            lines.append(line)
            line = word
    lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while draw.textlength(lines[-1] + "…", font=font) > max_width and lines[-1]:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    return lines


def render_token_image(ticket: TokenTicket) -> bytes:
    img = _background().copy()

    # Logo on white tile, top-left (as on the kiosk).
    if LOGO.exists():
        logo = Image.open(LOGO).convert("RGB")
        logo = logo.resize((round(logo.width * 2.0), round(logo.height * 2.0)), Image.Resampling.LANCZOS)
        img.paste(logo, (48, 48))

    card_w, card_x = 880, (WIDTH - 880) // 2
    card_top, card_bottom = 300, 1180
    radius = 44

    # Soft drop shadow.
    shadow = Image.new("L", (WIDTH, HEIGHT), 0)
    ImageDraw.Draw(shadow).rounded_rectangle(
        (card_x, card_top + 18, card_x + card_w, card_bottom + 18), radius=radius, fill=90
    )
    img = Image.composite(Image.new("RGB", img.size, (2, 30, 110)), img, shadow.filter(ImageFilter.GaussianBlur(28)))

    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((card_x, card_top, card_x + card_w, card_bottom), radius=radius, fill=CARD_BG)

    # Original kiosk card content.
    _centered(draw, card_top + 70, "Your Token", _font(FONT_REGULAR, 50), TEXT_LABEL)
    token_text = f"# {ticket.token_no}"
    _centered(draw, card_top + 150, token_text, _fit_font(draw, token_text, FONT_BOLD, 200, card_w - 100), TEXT_TOKEN)
    _centered(draw, card_top + 400, "Thank You", _font(FONT_SEMIBOLD, 56), TEXT_THANKS)

    # Details section.
    pad = 70
    y = card_top + 510
    draw.line((card_x + pad, y, card_x + card_w - pad, y), fill=DIVIDER, width=3)
    y += 40
    label_font, value_font = _font(FONT_REGULAR, 30), _font(FONT_SEMIBOLD, 36)
    rows = [("Branch", ticket.branch_name), ("Service", ticket.service_name),
            ("Issued", ticket.issued_at.strftime("%d %b %Y, %I:%M %p"))]
    if ticket.waiting_count is not None:
        rows.append(("Ahead of you", str(ticket.waiting_count)))
    label_x, value_x, value_w = card_x + pad, card_x + 330, card_w - 330 - pad
    for label, value in rows:
        lines = _wrap(draw, value, value_font, value_w)
        draw.text((label_x, y + 4), label, font=label_font, fill=TEXT_MUTED)
        for i, line in enumerate(lines):
            draw.text((value_x, y + i * 46), line, font=value_font, fill=TEXT_TOKEN)
        y += 46 * len(lines) + 22

    # Gold accent strip at the card's bottom edge (kiosk button colour).
    strip = Image.new("L", img.size, 0)
    ImageDraw.Draw(strip).rounded_rectangle((card_x, card_top, card_x + card_w, card_bottom), radius=radius, fill=255)
    band = Image.new("L", img.size, 0)
    ImageDraw.Draw(band).rectangle((card_x, card_bottom - 14, card_x + card_w, card_bottom), fill=255)
    img = Image.composite(Image.new("RGB", img.size, ACCENT_GOLD), img, ImageChops.multiply(strip, band))

    draw = ImageDraw.Draw(img)
    _centered(draw, card_bottom + 50, "BRAC Bank Queue Pro  •  Issued via WhatsApp", _font(FONT_SEMIBOLD, 30), (255, 255, 255))
    _centered(draw, card_bottom + 95, "Please wait for your number to be called", _font(FONT_REGULAR, 28), (220, 235, 255))

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()
