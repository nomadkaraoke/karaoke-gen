"""
Real renders of the tenant theme preview (title card + karaoke frame).

Uses the production renderers end-to-end with a colour-background theme and a
bundled font; storage is faked in-memory. Skipped without ffmpeg (CI installs it).
"""
import io
import shutil

import pytest
from PIL import Image

from backend.services import theme_preview_service as tps

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _theme(sung="255, 0, 0, 255", bg="#102030"):
    return {
        "intro": {
            "background_color": bg,
            "background_image": None,
            "font": "AvenirNext-Bold.ttf",
            "title_color": "#ffffff",
            "artist_color": "#ffdf6b",
            "title_region": "370,980,3100,350",
            "artist_region": "370,1400,3100,450",
        },
        "karaoke": {
            "background_color": bg,
            "background_image": None,
            "font_path": "AvenirNext-Bold.ttf",
            "font": "Avenir Next Bold",
            "ass_name": "Nomad",
            "primary_color": sung,
            "secondary_color": "255, 255, 255, 255",
            "outline_color": "0, 0, 0, 255",
            "back_color": "0, 0, 0, 0",
            "bold": False, "italic": False, "scale_x": 100, "scale_y": 100, "spacing": 0,
            "border_style": 1, "outline": 1, "shadow": 0, "margin_l": 0, "margin_r": 0, "margin_v": 0,
        },
        "end": {},
        "cdg": {},
    }


class _NoGcsAssets:
    """Storage whose theme has no uploaded assets (font resolves to bundled)."""

    class _Bucket:
        def get_blob(self, path):
            return None

    bucket = _Bucket()


def _img(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")


def test_renders_title_card_and_karaoke_frame():
    tps._CACHE.clear()
    result = tps.render_theme_preview("t1", _theme(), artist="Randy Vild", title="What Goes Up", storage=_NoGcsAssets())
    title, karaoke = _img(result.title_card), _img(result.karaoke_frame)
    assert title.size == (1280, 720)
    assert karaoke.size == (1280, 720)
    # The theme's solid #102030 background is what's in the corners of both frames
    for frame in (title, karaoke):
        r, g, b = frame.getpixel((8, 8))
        assert abs(r - 0x10) <= 6 and abs(g - 0x20) <= 6 and abs(b - 0x30) <= 6
    urls = result.as_data_urls()
    assert urls["title_card"].startswith("data:image/jpeg;base64,")


def test_sung_colour_is_what_changes_in_the_karaoke_frame():
    """The frame shows a part-sung line, so changing only the sung colour must
    change the karaoke frame (and not the title card)."""
    tps._CACHE.clear()
    red = tps.render_theme_preview("t1", _theme(sung="255, 0, 0, 255"), storage=_NoGcsAssets())
    green = tps.render_theme_preview("t1", _theme(sung="0, 255, 0, 255"), storage=_NoGcsAssets())
    assert red.title_card == green.title_card
    assert red.karaoke_frame != green.karaoke_frame
    reds = sum(1 for r, g, b in _img(red.karaoke_frame).getdata() if r > 200 and g < 60 and b < 60)
    greens = sum(1 for r, g, b in _img(green.karaoke_frame).getdata() if g > 200 and r < 60 and b < 60)
    assert reds > 200 and greens > 200


def test_identical_drafts_are_cached():
    tps._CACHE.clear()
    a = tps.render_theme_preview("t1", _theme(), storage=_NoGcsAssets())
    b = tps.render_theme_preview("t1", _theme(), storage=_NoGcsAssets())
    assert a is b


def test_missing_background_asset_is_a_preview_error():
    theme = _theme()
    theme["karaoke"]["background_image"] = "missing.jpg"
    with pytest.raises(tps.ThemePreviewError, match="missing.jpg"):
        tps.resolve_assets(theme, "t1", storage=_NoGcsAssets())


def test_font_family_name_from_bundled_font():
    import os

    name = tps.font_family_name(os.path.join(tps.BUNDLED_FONTS_DIR, "AvenirNext-Bold.ttf"))
    assert name.startswith("Avenir Next")


def test_evict_keeps_cache_under_limit(tmp_path):
    import os
    import time

    for i in range(5):
        p = tmp_path / f"f{i}"
        p.write_bytes(b"x" * 100)
        os.utime(p, (time.time() - 100 + i, time.time()))
    tps._evict(str(tmp_path), 250, keep=str(tmp_path / "f0"))
    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert sum((tmp_path / n).stat().st_size for n in remaining) <= 250
    assert "f0" in remaining  # the file just written is never evicted


def test_failed_download_leaves_no_partial_cache_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tps, "ASSET_CACHE_ROOT", str(tmp_path))

    class Blob:
        generation = 7

        def download_to_filename(self, path):
            open(path, "wb").write(b"partial")
            raise IOError("network died")

    class Storage:
        class bucket:
            @staticmethod
            def get_blob(path):
                return Blob()

    with pytest.raises(IOError):
        tps._local_theme_asset(Storage, "t1", "bg.jpg")
    assert not any(f.is_file() for f in tmp_path.rglob("*"))
