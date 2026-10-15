"""Minimal CD+G decoder: render the screen of a .cdg file at a point in time.

Used by the theme editor's CD+G preview (backend/services/theme_preview_service.py)
and by tests to check real CDG output pixel by pixel. Handles the instructions
cdgmaker emits: memory preset, tile blocks (normal + XOR) and the colour tables.
"""
from typing import List, Optional, Sequence

from PIL import Image

CDG_FPS = 300  # packets per second
CDG_WIDTH, CDG_HEIGHT = 300, 216
PACKET_SIZE = 24

_CMD_CDG = 9
_MEMORY_PRESET = 1
_TILE_BLOCK = 6
_TILE_BLOCK_XOR = 38
_LOAD_COLOR_TABLE_LO = 30
_LOAD_COLOR_TABLE_HI = 31


def decode_cdg_frames(data: bytes, times: Sequence[float]) -> List[Image.Image]:
    """Decode CD+G packets once, returning the screen (RGB) at each of ``times`` (seconds)."""
    order = sorted(range(len(times)), key=lambda i: times[i])
    frames: List[Optional[Image.Image]] = [None] * len(times)
    px = bytearray(CDG_WIDTH * CDG_HEIGHT)
    pal = [(0, 0, 0)] * 16
    total = len(data) // PACKET_SIZE

    def snapshot() -> Image.Image:
        img = Image.new("RGB", (CDG_WIDTH, CDG_HEIGHT))
        img.putdata([pal[c] for c in px])
        return img

    i = 0
    for k in order:
        end = min(total, max(0, int(times[k] * CDG_FPS)))
        while i < end:
            _apply_packet(data[i * PACKET_SIZE:(i + 1) * PACKET_SIZE], px, pal)
            i += 1
        frames[k] = snapshot()
    return frames  # type: ignore[return-value]


def _apply_packet(p: bytes, px: bytearray, pal: list) -> None:
    if p[0] & 0x3F != _CMD_CDG:
        return
    ins, d = p[1] & 0x3F, p[4:20]
    if ins == _MEMORY_PRESET:
        px[:] = bytes([d[0] & 0x0F]) * (CDG_WIDTH * CDG_HEIGHT)
    elif ins in (_TILE_BLOCK, _TILE_BLOCK_XOR):
        c0, c1, row, col = d[0] & 0x0F, d[1] & 0x0F, d[2] & 0x1F, d[3] & 0x3F
        for r in range(12):
            bits = d[4 + r]
            y = row * 12 + r
            if y >= CDG_HEIGHT:
                continue
            for b in range(6):
                x = col * 6 + b
                if x < CDG_WIDTH:
                    v = c1 if bits & (0x20 >> b) else c0
                    idx = y * CDG_WIDTH + x
                    px[idx] = v ^ px[idx] if ins == _TILE_BLOCK_XOR else v
    elif ins in (_LOAD_COLOR_TABLE_LO, _LOAD_COLOR_TABLE_HI):
        base = 0 if ins == _LOAD_COLOR_TABLE_LO else 8
        for k in range(8):
            hi, lo = d[2 * k] & 0x3F, d[2 * k + 1] & 0x3F
            pal[base + k] = (
                ((hi >> 2) & 0xF) * 17,
                (((hi & 3) << 2) | ((lo >> 4) & 3)) * 17,
                (lo & 0xF) * 17,
            )


def decode_cdg(data: bytes, seconds: float) -> Image.Image:
    """Decode CD+G packets up to ``seconds`` and return the 300x216 screen as RGB."""
    return decode_cdg_frames(data, [seconds])[0]


def cdg_color(hex_color: str) -> tuple:
    """The RGB a hex colour becomes on a CD+G screen (12-bit palette: channels are multiples of 0x11)."""
    h = hex_color.lstrip("#")
    return tuple(0x11 * round(int(h[i:i + 2], 16) / 0x11) for i in (0, 2, 4))


def decode_frame(cdg_path: str, seconds: float) -> Image.Image:
    """Decode a .cdg file up to ``seconds`` and return the screen as RGB."""
    with open(cdg_path, "rb") as fh:
        return decode_cdg(fh.read(), seconds)


def cdg_duration_seconds(cdg_path_or_bytes) -> float:
    if isinstance(cdg_path_or_bytes, (bytes, bytearray)):
        size = len(cdg_path_or_bytes)
    else:
        import os

        size = os.path.getsize(cdg_path_or_bytes)
    return size / PACKET_SIZE / CDG_FPS
