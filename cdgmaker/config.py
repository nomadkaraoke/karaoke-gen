from enum import StrEnum
from pathlib import Path
from typing import Any, TypeAlias

from attrs import define, field
from PIL import ImageColor


# NOTE RGBColor is specifically an RGB 3-tuple (red, green, blue).
RGBColor: TypeAlias = tuple[int, int, int]
def to_rgbcolor(val: Any) -> RGBColor:
    if isinstance(val, str):
        return ImageColor.getrgb(val)[:3]
    raise TypeError("color value not convertible to RGBColor")
def to_rgbcolor_or_none(val: Any) -> RGBColor | None:
    if val is None or val == "":
        return None
    if isinstance(val, str):
        return ImageColor.getrgb(val)[:3]
    raise TypeError("color value not convertible to RGBColor or None")


class LyricClearMode(StrEnum):
    PAGE = "page"
    LINE_EAGER = "eager"
    LINE_DELAYED = "delayed"


class TextAlign(StrEnum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class TextPlacement(StrEnum):
    TOP_LEFT = "top left"
    TOP_MIDDLE = "top middle"
    TOP_RIGHT = "top right"
    MIDDLE_LEFT = "middle left"
    MIDDLE = "middle"
    MIDDLE_RIGHT = "middle right"
    BOTTOM_LEFT = "bottom left"
    BOTTOM_MIDDLE = "bottom middle"
    BOTTOM_RIGHT = "bottom right"


class StrokeType(StrEnum):
    CIRCLE = "circle"
    SQUARE = "square"
    OCTAGON = "octagon"


@define
class SettingsInstrumental:
    sync: int
    line_tile_height: int

    wait: bool = True
    text: str = "INSTRUMENTAL"
    text_align: TextAlign = TextAlign.CENTER
    text_placement: TextPlacement = TextPlacement.MIDDLE
    fill: RGBColor = field(converter=to_rgbcolor, default="#bbb")
    stroke: RGBColor | None = field(
        converter=to_rgbcolor_or_none,
        default=None,
    )
    background: RGBColor | None = field(
        converter=to_rgbcolor_or_none,
        default=None,
    )
    image: Path | None = None
    transition: str | None = None
    x: int = 0
    y: int = 0


@define
class SettingsSinger:
    active_fill: RGBColor = field(converter=to_rgbcolor, default="#33f")
    active_stroke: RGBColor = field(converter=to_rgbcolor, default="#004")
    inactive_fill: RGBColor = field(converter=to_rgbcolor, default="#ddf")
    inactive_stroke: RGBColor = field(converter=to_rgbcolor, default="#009")


@define
class SettingsLyric:
    sync: list[int]
    text: str
    line_tile_height: int
    lines_per_page: int

    singer: int = 1
    row: int = 1


@define
class Settings:
    title: str
    artist: str
    file: Path
    font: Path
    title_screen_background: Path

    outname: str = "output"
    clear_mode: LyricClearMode = LyricClearMode.LINE_DELAYED
    sync_offset: int = 0
    highlight_bandwidth: int = 1
    draw_bandwidth: int = 1
    background: RGBColor = field(converter=to_rgbcolor, default="black")
    border: RGBColor | None = field(
        converter=to_rgbcolor_or_none,
        default="black",
    )
    font_size: int = 18
    stroke_width: int = 0
    stroke_type: StrokeType = StrokeType.OCTAGON
    instrumentals: list[SettingsInstrumental] = field(factory=list)
    singers: list[SettingsSinger] = field(factory=list)
    lyrics: list[SettingsLyric] = field(factory=list)
    title_color: RGBColor = field(converter=to_rgbcolor, default="#ffffff")
    artist_color: RGBColor = field(converter=to_rgbcolor, default="#ffdf6b")


__all__ = [
    "RGBColor",

    "LyricClearMode", "TextAlign", "TextPlacement", "StrokeType",

    "SettingsInstrumental", "SettingsSinger", "SettingsLyric",
    "Settings",
]
