"""Branded portrait (9:16) background generation for karaoke lyric videos.

Produces a 1080x1920 background with a branded header (a neon wordmark image when
one is supplied, otherwise the brand name rendered in the theme font), the song
title + artist, a subtle neon border, and a footer handle. The same background is
used behind the scrolling lyrics and as the held title card; an ``end`` variant adds
a centred "thank you" message.

The module is brand-agnostic: all brand specifics (wordmark, colours, footer text)
come in via :class:`PortraitBrandConfig`, so tenants can theme it without code
changes. Callers supply the config; the karaoke-gen defaults live at the call site.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

RGB = Tuple[int, int, int]
RGBA = Tuple[int, int, int, int]


@dataclass
class PortraitBrandConfig:
    """Visual configuration for the portrait background."""

    width: int = 1080
    height: int = 1920
    bg_top: RGB = (16, 18, 42)
    bg_bottom: RGB = (4, 5, 12)
    border_color: RGBA = (255, 60, 200, 90)
    inner_border_color: RGBA = (150, 80, 247, 50)
    # Header: prefer a wordmark image (neon logo); fall back to brand_text.
    wordmark_image: Optional[str] = None
    brand_text: Optional[str] = None
    brand_color: RGB = (255, 122, 204)
    title_color: RGB = (255, 255, 255)
    artist_color: RGB = (255, 223, 107)
    footer_text: Optional[str] = None
    footer_color: RGB = (255, 122, 204)
    end_text: str = "THANK YOU FOR SINGING!"
    end_text_color: RGB = (255, 122, 204)
    font_path: Optional[str] = None
    # Match the landscape title/end screens, which upper-case the song/artist.
    uppercase_meta: bool = True


def _gradient(cfg: PortraitBrandConfig) -> Image.Image:
    """Vertical gradient from ``bg_top`` to ``bg_bottom`` (numpy-free, one row/px)."""
    img = Image.new("RGB", (cfg.width, cfg.height), cfg.bg_bottom)
    draw = ImageDraw.Draw(img)
    h = cfg.height
    for y in range(h):
        t = y / (h - 1) if h > 1 else 0
        r = int(cfg.bg_top[0] * (1 - t) + cfg.bg_bottom[0] * t)
        g = int(cfg.bg_top[1] * (1 - t) + cfg.bg_bottom[1] * t)
        b = int(cfg.bg_top[2] * (1 - t) + cfg.bg_bottom[2] * t)
        draw.line([(0, y), (cfg.width, y)], fill=(r, g, b))
    return img


def _load_font(cfg: PortraitBrandConfig, size: int) -> ImageFont.FreeTypeFont:
    if cfg.font_path and os.path.isfile(cfg.font_path):
        return ImageFont.truetype(cfg.font_path, size)
    return ImageFont.load_default()


def _paste_wordmark(img: Image.Image, cfg: PortraitBrandConfig, top: int) -> int:
    """Paste the neon wordmark, knocking out its dark backing. Returns bottom y."""
    logo = Image.open(cfg.wordmark_image).convert("RGBA")
    target_w = int(cfg.width * 0.50)
    target_h = max(1, int(logo.height * target_w / logo.width))
    logo = logo.resize((target_w, target_h), Image.LANCZOS)
    px = logo.load()
    for y in range(logo.height):
        for x in range(logo.width):
            r, g, b, a = px[x, y]
            lum = max(r, g, b)
            if lum < 70:
                px[x, y] = (r, g, b, 0)
            elif lum < 130:
                px[x, y] = (r, g, b, int(a * (lum - 70) / 60))
    img.paste(logo, ((cfg.width - target_w) // 2, top), logo)
    return top + target_h


def build_background(
    cfg: PortraitBrandConfig,
    artist: str,
    title: str,
    variant: str = "lyrics",
) -> Image.Image:
    """Build a portrait background image.

    variant:
      * ``"lyrics"`` / ``"title"`` — header (brand + song/artist) and footer, empty
        centre (lyrics are overlaid at render time; the title card just holds this).
      * ``"end"`` — header brand, a centred end message, and footer.
    """
    if cfg.uppercase_meta:
        title = (title or "").upper()
        artist = (artist or "").upper()

    img = _gradient(cfg)
    draw = ImageDraw.Draw(img, "RGBA")

    # Neon border
    margin = int(cfg.width * 0.024)
    draw.rounded_rectangle(
        [margin, margin, cfg.width - margin, cfg.height - margin],
        radius=46, outline=cfg.border_color, width=4,
    )
    draw.rounded_rectangle(
        [margin + 6, margin + 6, cfg.width - margin - 6, cfg.height - margin - 6],
        radius=40, outline=cfg.inner_border_color, width=2,
    )

    # Header: wordmark image or brand text
    header_top = int(cfg.height * 0.036)
    header_bottom = header_top
    if cfg.wordmark_image and os.path.isfile(cfg.wordmark_image):
        header_bottom = _paste_wordmark(img, cfg, header_top)
    elif cfg.brand_text:
        f_brand = _load_font(cfg, int(cfg.width * 0.075))
        bw = draw.textlength(cfg.brand_text, font=f_brand)
        draw.text(((cfg.width - bw) / 2, header_top), cfg.brand_text,
                  font=f_brand, fill=cfg.brand_color)
        header_bottom = header_top + int(cfg.width * 0.075 * 1.2)

    # Song title + artist under the header
    y0 = header_bottom + int(cfg.height * 0.014)
    if title:
        f_title = _load_font(cfg, int(cfg.width * 0.056))
        tw = draw.textlength(title, font=f_title)
        draw.text(((cfg.width - tw) / 2, y0), title, font=f_title, fill=cfg.title_color)
        y0 += int(cfg.width * 0.056 * 1.15)
    if artist:
        f_artist = _load_font(cfg, int(cfg.width * 0.041))
        aw = draw.textlength(artist, font=f_artist)
        draw.text(((cfg.width - aw) / 2, y0), artist, font=f_artist, fill=cfg.artist_color)

    # End message (centred)
    if variant == "end" and cfg.end_text:
        f_end = _load_font(cfg, int(cfg.width * 0.06))
        ew = draw.textlength(cfg.end_text, font=f_end)
        draw.text(((cfg.width - ew) / 2, cfg.height * 0.46), cfg.end_text,
                  font=f_end, fill=cfg.end_text_color)

    # Footer handle
    if cfg.footer_text:
        f_foot = _load_font(cfg, int(cfg.width * 0.035))
        fw = draw.textlength(cfg.footer_text, font=f_foot)
        draw.text(((cfg.width - fw) / 2, cfg.height - int(cfg.height * 0.057)),
                  cfg.footer_text, font=f_foot, fill=cfg.footer_color)

    return img
