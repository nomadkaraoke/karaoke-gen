"""Portrait (9:16) karaoke video rendering.

Re-renders karaoke lyrics for a 1080x1920 phone/social frame from the same
corrected-lyrics data used by the landscape pipeline. See ``renderer`` for the entry
point and ``docs/archive/2026-06-14-portrait-video-design.md`` for the rationale.
"""
from karaoke_gen.portrait.background import PortraitBrandConfig, build_background
from karaoke_gen.portrait.renderer import (
    PORTRAIT_HEIGHT,
    PORTRAIT_WIDTH,
    PortraitLayout,
    build_portrait_ass,
    prepare_portrait_segments,
    render_portrait_video,
)
from karaoke_gen.portrait.wrap import balance_segments

__all__ = [
    "PortraitBrandConfig",
    "build_background",
    "PortraitLayout",
    "PORTRAIT_WIDTH",
    "PORTRAIT_HEIGHT",
    "build_portrait_ass",
    "prepare_portrait_segments",
    "render_portrait_video",
    "balance_segments",
]
