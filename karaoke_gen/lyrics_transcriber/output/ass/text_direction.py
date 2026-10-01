"""Right-to-left (Hebrew, Arabic, ...) support for ASS karaoke output.

Two libass behaviours make RTL karaoke render wrong by default (both verified by
rendering with the production static ffmpeg 7.0.2 and libass 0.17.x):

1. With the style ``Encoding`` at 0 libass forces a left-to-right paragraph
   direction ("VSFilter compat"). The ``{\\kf}`` tag before each word splits the
   line into separate runs, so each Hebrew word is shaped correctly but the words
   are laid out — and highlighted — left to right. ``Encoding=-1`` makes libass
   detect the base direction per line, which fixes word order. See
   ``build_karaoke_styles``.

2. The ``\\kf`` fill always sweeps left→right within a word
   (https://github.com/libass/libass/issues/406). Since libass 0.15.0,
   ``{\\frz180\\frx180\\fry180}`` — three rotations that cancel out visually —
   flips the fill so it sweeps right→left, without splitting words (so Arabic
   letters still join).
"""
import unicodedata

# Visually a no-op; makes libass (>= 0.15) sweep \kf fills right-to-left.
RTL_KARAOKE_FILL_TAGS = r"{\frz180\frx180\fry180}"


def rtl_karaoke_fill_tags(style_angle: float = 0) -> str:
    """RTL fill tags for a style rotated by ``style_angle`` degrees.

    ``\\frz`` replaces the style's Angle, so fold it in: ``\\frx180\\fry180`` is itself
    a 180° z-rotation, so ``\\frz(180+angle)`` nets out to the theme's own angle.
    """
    if not style_angle:
        return RTL_KARAOKE_FILL_TAGS
    return r"{\frz" + f"{180 + style_angle:g}" + r"\frx180\fry180}"


# libass: -1 = auto-detect base direction per paragraph (LTR text is unaffected).
ASS_ENCODING_AUTO_DIRECTION = -1


def is_rtl_text(text: str) -> bool:
    """True if the first strongly-directional character of ``text`` is right-to-left.

    Mirrors the Unicode bidi algorithm's paragraph-direction rule (P2/P3), which is
    what libass uses with ``Encoding=-1`` — so our layout decisions (fill direction,
    lead-in side) agree with how the line is actually rendered.
    """
    for ch in text or "":
        bidi = unicodedata.bidirectional(ch)
        if bidi in ("R", "AL"):
            return True
        if bidi == "L":
            return False
    return False
