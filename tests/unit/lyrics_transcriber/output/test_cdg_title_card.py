"""End-to-end tests for the configurable CDG title card ("up card") and outro.

Renders real CDG files through CDGGenerator + KaraokeComposer, decodes the
packets back into frames with a tiny CD+G decoder and asserts on the pixels:
separator line ("MADE FAMOUS BY"), text kept below a logo area
(``title_block_top``), outro shrink-to-fit, and the title-font fallback.
"""
import os
import shutil
import subprocess

import pytest
from PIL import Image

from karaoke_gen.lyrics_transcriber.output.cdg import CDGGenerator
from karaoke_gen.lyrics_transcriber.output.cdg_decoder import CDG_FPS, decode_frame

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required to compose CDG audio")

W, H = 300, 216
YELLOW = (255, 255, 0)
WHITE = (255, 255, 255)


def _font(name: str) -> str:
    import karaoke_gen.lyrics_transcriber.output as output_mod

    return os.path.join(os.path.dirname(output_mod.__file__), "fonts", name)


def rows_with(img: Image.Image, color) -> list:
    return sorted({y for y in range(H) for x in range(W) if img.getpixel((x, y)) == color})


def _styles(tmp_path, **overrides) -> dict:
    black = tmp_path / "black.png"
    Image.new("RGB", (W, H), (0, 0, 0)).save(black)
    styles = {
        "title_color": "#ffff00", "artist_color": "#ffff00", "background_color": "#000000",
        "border_color": "#000000", "font_path": _font("Oswald-SemiBold.ttf"), "font_size": 18,
        "stroke_width": 0, "stroke_style": "octagon", "active_fill": "#006CF9", "active_stroke": "#000000",
        "inactive_fill": "#ffff00", "inactive_stroke": "#000000", "title_screen_background": str(black),
        "instrumental_background": str(black), "instrumental_transition": "wipein",
        "instrumental_font_color": "#ffff00", "title_screen_transition": "wipein", "row": 4,
        "line_tile_height": 3, "lines_per_page": 4, "clear_mode": "eager", "sync_offset": 0,
        "instrumental_gap_threshold": 99999, "instrumental_text": "INSTRUMENTAL",
        "lead_in_threshold": 99999, "lead_in_symbols": [">"], "lead_in_duration": 30, "lead_in_total": 200,
        "title_artist_gap": 8, "title_top_padding": 0, "intro_duration_seconds": 5.0,
        "first_syllable_buffer_seconds": 0.5, "outro_background": str(black), "outro_transition": "wipein",
        "outro_text_line1": "$title", "outro_text_line2": "$artist", "outro_line1_color": "#ffff00",
        "outro_line2_color": "#ffff00", "outro_line1_line2_gap": 10,
    }
    styles.update(overrides)
    return styles


def render(tmp_path, title, artist, **overrides) -> str:
    audio = tmp_path / "silence.mp3"
    if not audio.exists():
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "16", "-q:a", "9", str(audio)],
            check=True,
        )
    lrc = tmp_path / "test.lrc"
    lrc.write_text("[00:08.000]1:/HELLO \n[00:08.500]1:WORLD \n[00:10.000]1:/SECOND \n[00:10.500]1:LINE \n")
    out_dir = tmp_path / "out"
    out_dir.mkdir(exist_ok=True)
    gen = CDGGenerator(output_dir=str(out_dir))
    cdg_file, _, _ = gen.generate_cdg_from_lrc(str(lrc), str(audio), title, artist, _styles(tmp_path, **overrides))
    return cdg_file


class TestTitleCard:
    def test_default_layout_has_no_separator(self, tmp_path):
        frame = decode_frame(render(tmp_path, "Hello", "World"), 4.8)
        assert rows_with(frame, YELLOW), "title/artist should render"
        assert not rows_with(frame, WHITE)

    def test_separator_line_renders_between_title_and_artist(self, tmp_path):
        cdg = render(
            tmp_path, "Abracadabra", "Lady Gaga",
            title_separator_text="MADE FAMOUS BY", title_separator_color="#ffffff",
        )
        frame = decode_frame(cdg, 4.8)
        yellow, white = rows_with(frame, YELLOW), rows_with(frame, WHITE)
        assert white, "separator should render in its own colour"
        # Title above, artist below the separator.
        assert min(yellow) < min(white) and max(white) < max(yellow)

    def test_title_block_top_keeps_text_below_logo_area(self, tmp_path):
        cdg = render(
            tmp_path, "There's a Kind of Hush (All Over the World)",
            "Herman's Hermits & The Very Long Band Name Orchestra",
            title_separator_text="MADE FAMOUS BY", title_block_top=60, title_max_height=130,
            title_font_size=28, artist_font_size=28,
        )
        frame = decode_frame(cdg, 4.8)
        text_rows = rows_with(frame, YELLOW) + rows_with(frame, WHITE)
        assert min(text_rows) >= 60
        assert max(text_rows) < 204  # inside the CDG safe area

    def test_missing_title_font_falls_back_to_lyrics_font(self, tmp_path):
        cdg = render(tmp_path, "Hello", "World", title_font_path="/nope/missing.ttf")
        assert rows_with(decode_frame(cdg, 4.8), YELLOW)

    def test_title_font_is_used_for_title_card(self, tmp_path):
        oswald = decode_frame(render(tmp_path, "Hello", "World"), 4.8)
        avenir = decode_frame(
            render(tmp_path, "Hello", "World", title_font_path=_font("AvenirNext-Bold.ttf")), 1.5
        )
        assert list(oswald.getdata()) != list(avenir.getdata())


class TestOutro:
    def test_long_outro_text_shrinks_instead_of_crashing(self, tmp_path):
        # Previously an assert fired when outro text exceeded 200px.
        long = "A Really Quite Extraordinarily Long Song Title That Keeps Going On And On Forever"
        cdg = render(tmp_path, long, long + " Featuring Absolutely Everybody", title_block_top=60)
        data_len = os.path.getsize(cdg) // 24
        frame = decode_frame(cdg, data_len / CDG_FPS)
        rows = rows_with(frame, YELLOW)
        assert rows and min(rows) >= 60


class TestUnbreakableWords:
    def test_long_unbreakable_title_shrinks_to_fit_the_width(self, tmp_path):
        cdg = render(tmp_path, "E2E vocalstar 2026-10-15T05-18-51-717Z", "Nomad E2E", title_font_size=28)
        frame = decode_frame(cdg, 4.8)
        cols = sorted({x for y in range(H) for x in range(W) if frame.getpixel((x, y)) == YELLOW})
        # Text stays inside the visible area instead of being clipped at the edges.
        assert cols and cols[0] > 6 and cols[-1] < W - 6

