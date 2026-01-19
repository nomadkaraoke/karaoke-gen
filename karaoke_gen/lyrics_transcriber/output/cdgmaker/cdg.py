from collections.abc import Sequence
from enum import Enum
from typing import BinaryIO, NamedTuple, TypeAlias

from .utils import *


_RGBColor: TypeAlias = tuple[int, int, int]


CDG_COMMAND = 0x09
# TODO Find out how to store proper parity bytes
CDG_PARITY = 0xa5
CDG_MASK = 0x3f

CDG_SCREEN_WIDTH = 300
CDG_SCREEN_HEIGHT = 216
CDG_VISIBLE_WIDTH = 280
CDG_VISIBLE_HEIGHT = 192
CDG_VISIBLE_X = 6
CDG_VISIBLE_Y = 12
CDG_TILE_WIDTH = 6
CDG_TILE_HEIGHT = 12

CDG_FPS = 300


class CDGInstruction(Enum):
    NO_INSTRUCTION = 0x00
    MEMORY_PRESET = 0x01
    BORDER_PRESET = 0x02
    TILE_BLOCK = 0x06
    SCROLL_PRESET = 0x14
    SCROLL_COPY = 0x18
    DEFINE_TRANSPARENT = 0x1c
    LOAD_COLOR_TABLE_LO = 0x1e
    LOAD_COLOR_TABLE_HI = 0x1f
    TILE_BLOCK_XOR = 0x26


class CDGScrollCommand(Enum):
    NO_SCROLL = 0
    SCROLL_RIGHT = 1
    SCROLL_DOWN = 1
    SCROLL_AHEAD = 1
    SCROLL_LEFT = 2
    SCROLL_UP = 2
    SCROLL_BACK = 2


class CDGPacket(NamedTuple):
    command: bool
    instruction: CDGInstruction
    data: bytes


class CDGWriter:
    def __init__(self):
        self.packets: list[CDGPacket] = []

    def queue_packet(self, packet: CDGPacket):
        self.packets.append(packet)

    def queue_packets(self, packets: Sequence[CDGPacket]):
        for packet in packets:
            self.queue_packet(packet)

    @property
    def packets_queued(self) -> int:
        return len(self.packets)

    def write_packets(self, stream: BinaryIO):
        for packet in self.packets:
            self.write_packet(stream, packet)

    def write_packet(self, stream: BinaryIO, packet: CDGPacket):
        stream.write((CDG_COMMAND if packet.command else 0x00).to_bytes())
        stream.write(packet.instruction.value.to_bytes())
        stream.write(CDG_PARITY.to_bytes() * 2)
        stream.write(packet.data)
        stream.write(CDG_PARITY.to_bytes() * 4)


def no_instruction() -> CDGPacket:
    return CDGPacket(
        command=False,
        instruction=CDGInstruction.NO_INSTRUCTION,
        data=b"\x00" * 16,
    )

def memory_preset(color: int, repeat: int = 0) -> CDGPacket:
    return CDGPacket(
        command=True,
        instruction=CDGInstruction.MEMORY_PRESET,
        data=bytes(pad(
            [
                color & 0xf,
                repeat & 0xf,
            ],
            16,
            padvalue=0,
        )),
    )

def border_preset(color: int) -> CDGPacket:
    return CDGPacket(
        command=True,
        instruction=CDGInstruction.BORDER_PRESET,
        data=bytes(pad(
            [
                color & 0xf,
            ],
            16,
            padvalue=0,
        )),
    )

def tile_block(
        color0: int,
        color1: int,
        row: int,
        column: int,
        tile: Sequence[int],
) -> CDGPacket:
    assert len(tile) == 12, "tile must have 12 rows"
    return CDGPacket(
        command=True,
        instruction=CDGInstruction.TILE_BLOCK,
        data=bytes([
            color0 & 0xf,
            color1 & 0xf,
            row & 0x1f,
            column & 0x3f,
            *[t & 0x3f for t in tile],
        ]),
    )

def scroll_preset(
        color: int = 0,
        hcmd: CDGScrollCommand = CDGScrollCommand.NO_SCROLL,
        hoffset: int = 0,
        vcmd: CDGScrollCommand = CDGScrollCommand.NO_SCROLL,
        voffset: int = 0,
) -> CDGPacket:
    return CDGPacket(
        command=True,
        instruction=CDGInstruction.SCROLL_PRESET,
        data=bytes(pad(
            [
                color & 0xf,
                (hcmd.value << 4) | (hoffset & 0x7),
                (vcmd.value << 4) | (voffset & 0xf),
            ],
            16,
            padvalue=0,
        )),
    )

def scroll_copy(
        color: int = 0,
        hcmd: CDGScrollCommand = CDGScrollCommand.NO_SCROLL,
        hoffset: int = 0,
        vcmd: CDGScrollCommand = CDGScrollCommand.NO_SCROLL,
        voffset: int = 0,
) -> CDGPacket:
    return CDGPacket(
        command=True,
        instruction=CDGInstruction.SCROLL_COPY,
        data=bytes(pad(
            [
                color & 0xf,
                (hcmd.value << 4) | (hoffset & 0x7),
                (vcmd.value << 4) | (voffset & 0xf),
            ],
            16,
            padvalue=0,
        )),
    )

def define_transparent(levels: Sequence[int]) -> CDGPacket:
    assert len(levels) == 16, "must load 16 transparency levels"
    return CDGPacket(
        command=True,
        instruction=CDGInstruction.DEFINE_TRANSPARENT,
        data=bytes([level & CDG_MASK for level in levels]),
    )

def load_color_table_lo(colors: Sequence[_RGBColor]) -> CDGPacket:
    assert len(colors) == 8, "must load 8 colors into low table"
    return CDGPacket(
        command=True,
        instruction=CDGInstruction.LOAD_COLOR_TABLE_LO,
        data=b"".join(map(_rgb_to_bytes, colors)),
    )

def load_color_table_hi(colors: Sequence[_RGBColor]) -> CDGPacket:
    assert len(colors) == 8, "must load 8 colors into high table"
    return CDGPacket(
        command=True,
        instruction=CDGInstruction.LOAD_COLOR_TABLE_HI,
        data=b"".join(map(_rgb_to_bytes, colors)),
    )

def tile_block_xor(
        color0: int,
        color1: int,
        row: int,
        column: int,
        tile: Sequence[int],
) -> CDGPacket:
    assert len(tile) == 12, "tile must have 12 rows"
    return CDGPacket(
        command=True,
        instruction=CDGInstruction.TILE_BLOCK_XOR,
        data=bytes([
            color0 & 0xf,
            color1 & 0xf,
            row & 0x1f,
            column & 0x3f,
            *[t & 0x3f for t in tile],
        ]),
    )


def memory_preset_repeat(color: int) -> list[CDGPacket]:
    return [
        memory_preset(color, repeat)
        for repeat in range(16)
    ]

def load_color_table(colors: Sequence[_RGBColor]) -> list[CDGPacket]:
    assert len(colors) == 16, "must load 16 colors into table"
    return [
        load_color_table_lo(colors[:8]),
        load_color_table_hi(colors[8:]),
    ]


def _rgb_to_bytes(rgb: _RGBColor) -> bytes:
    r, g, b = rgb
    hi = ((r & 0xf0) >> 2) | ((g & 0xc0) >> 6)
    lo = (g & 0x30) | ((b & 0xf0) >> 4)
    return bytes([hi, lo])


__all__ = [
    "CDG_COMMAND", "CDG_PARITY", "CDG_MASK",
    "CDG_SCREEN_WIDTH", "CDG_SCREEN_HEIGHT",
    "CDG_VISIBLE_WIDTH", "CDG_VISIBLE_HEIGHT",
    "CDG_VISIBLE_X", "CDG_VISIBLE_Y",
    "CDG_TILE_WIDTH", "CDG_TILE_HEIGHT", 
    "CDG_FPS",

    "CDGInstruction", "CDGScrollCommand", "CDGPacket",
    "CDGWriter",

    "no_instruction", "memory_preset", "border_preset", "tile_block",
    "scroll_preset", "scroll_copy", "define_transparent",
    "load_color_table_lo", "load_color_table_hi", "tile_block_xor",

    "memory_preset_repeat", "load_color_table",
]
