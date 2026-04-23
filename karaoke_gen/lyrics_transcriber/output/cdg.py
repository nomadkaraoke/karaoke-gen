import logging
from typing import List, Optional, Tuple
import logging
import re
import toml
from pathlib import Path
from PIL import ImageFont
import os
import zipfile
import shutil

from karaoke_gen.lyrics_transcriber.output.cdgmaker.cdg import CDG_VISIBLE_WIDTH
from karaoke_gen.lyrics_transcriber.output.cdgmaker.composer import KaraokeComposer
from karaoke_gen.lyrics_transcriber.output.cdgmaker.config import SettingsLyric
from karaoke_gen.lyrics_transcriber.output.cdgmaker.render import get_wrapped_text
from karaoke_gen.lyrics_transcriber.types import LyricsSegment


# Map our internal SingerId (0/1/2) to the CDG composer's 1-indexed singer field.
# SingerId 0 ("Both") → CDG singer 3.
_SINGER_ID_TO_CDG_INDEX = {0: 3, 1: 1, 2: 2}


def build_cdg_lyrics(
    segments,
    is_duet: bool,
    line_tile_height: int,
    lines_per_page: int,
) -> list:
    """Build a list of SettingsLyric entries from our LyricsSegment list.

    When is_duet is False, every SettingsLyric uses the CDG default singer=1
    (regression guard — produces identical output to the previous path).

    When is_duet is True, each SettingsLyric.singer is derived from the
    segment-level singer only (0→3, 1→1, 2→2, None→1). Word-level overrides
    are ignored — CDG's model is line-level singer and splitting lines at
    word boundaries would produce visually distinct display lines.
    """
    out = []
    for seg in segments:
        # Build sync list (timings in centiseconds) — one entry per word start time
        sync = [int(round(w.start_time * 100)) for w in seg.words]
        text = seg.text

        if is_duet and seg.singer is not None:
            cdg_singer = _SINGER_ID_TO_CDG_INDEX.get(seg.singer, 1)
        else:
            cdg_singer = 1

        out.append(SettingsLyric(
            sync=sync,
            text=text,
            line_tile_height=line_tile_height,
            lines_per_page=lines_per_page,
            singer=cdg_singer,
        ))
    return out


def _rgb_to_hex(rgb) -> str:
    """Convert an (R, G, B) tuple to a #RRGGBB hex string."""
    r, g, b = rgb[0], rgb[1], rgb[2]
    return "#{:02X}{:02X}{:02X}".format(int(r), int(g), int(b))


class CDGGenerator:
    """Generates CD+G (CD Graphics) format karaoke files."""

    def __init__(self, output_dir: str, logger: Optional[logging.Logger] = None, is_duet: bool = False):
        """Initialize CDGGenerator.

        Args:
            output_dir: Directory where output files will be written
            logger: Optional logger instance
            is_duet: When True, emit the 3-singer duet palette and tag each
                lyric line with its segment-level singer index (1/2/3).
        """
        self.output_dir = output_dir
        self.logger = logger or logging.getLogger(__name__)
        self.cdg_visible_width = 280
        self.is_duet = is_duet

    def _sanitize_filename(self, filename: str) -> str:
        """Replace or remove characters that are unsafe for filenames."""
        if not filename:
            return ""
        # Replace problematic characters with underscores
        for char in ["\\", "/", ":", "*", "?", '"', "<", ">", "|"]:
            filename = filename.replace(char, "_")
        # Remove any trailing spaces
        filename = filename.rstrip(" ")
        return filename

    def _get_safe_filename(self, artist: str, title: str, suffix: str = "", ext: str = "") -> str:
        """Create a safe filename from artist and title."""
        safe_artist = self._sanitize_filename(artist)
        safe_title = self._sanitize_filename(title)
        base = f"{safe_artist} - {safe_title}"
        if suffix:
            base += f" ({suffix})"
        if ext:
            base += f".{ext}"
        return base

    def generate_cdg(
        self,
        segments: List[LyricsSegment],
        audio_file: str,
        title: str,
        artist: str,
        cdg_styles: dict,
    ) -> Tuple[str, str, str]:
        """Generate a CDG file from lyrics segments and audio file.

        Args:
            segments: List of LyricsSegment objects containing timing and text
            audio_file: Path to the audio file
            title: Title of the song
            artist: Artist name
            cdg_styles: Dictionary containing CDG style parameters

        Returns:
            Tuple containing paths to (cdg_file, mp3_file, zip_file)
        """
        self._validate_and_setup_font(cdg_styles)

        # Convert segments to the format expected by the rest of the code
        lyrics_data = self._convert_segments_to_lyrics_data(segments, is_duet=self.is_duet)

        toml_file = self._create_toml_file(
            audio_file=audio_file,
            title=title,
            artist=artist,
            lyrics_data=lyrics_data,
            cdg_styles=cdg_styles,
        )

        try:
            self._compose_cdg(toml_file)
            output_zip = self._find_cdg_zip(artist, title)
            self._extract_cdg_files(output_zip)

            cdg_file = self._get_cdg_path(artist, title)
            mp3_file = self._get_mp3_path(artist, title)

            self._verify_output_files(cdg_file, mp3_file)

            self.logger.info("CDG file generated successfully")
            return cdg_file, mp3_file, output_zip

        except Exception as e:
            self.logger.error(f"Error composing CDG: {e}")
            raise

    def _convert_segments_to_lyrics_data(
        self,
        segments: List[LyricsSegment],
        is_duet: bool = False,
    ) -> List[dict]:
        """Convert LyricsSegment objects to the format needed for CDG generation.

        In solo mode, each entry is ``{"timestamp", "text"}`` (byte-identical
        to the legacy flow). In duet mode, entries also carry a ``"singer"``
        field (1/2/3 — CDG composer 1-indexed). The first word of each segment
        (except the very first) is prefixed with ``/`` so ``format_lyrics``
        breaks visual lines at segment boundaries; this keeps each visual line
        associated with exactly one singer.
        """
        lyrics_data = []

        for seg_idx, segment in enumerate(segments):
            seg_singer = 1
            if is_duet and segment.singer is not None:
                seg_singer = _SINGER_ID_TO_CDG_INDEX.get(segment.singer, 1)

            for word_idx, word in enumerate(segment.words):
                # Convert time from seconds to centiseconds
                timestamp = int(word.start_time * 100)
                text = word.text.upper()  # CDG format expects uppercase text
                # Force a visual line break at every segment boundary in duet
                # mode so wrapped lines never mix words from different singers.
                if is_duet and seg_idx > 0 and word_idx == 0 and not text.startswith("/"):
                    text = "/" + text
                entry = {"timestamp": timestamp, "text": text}
                if is_duet:
                    entry["singer"] = seg_singer
                lyrics_data.append(entry)
                self.logger.debug(f"Added lyric: timestamp {timestamp}, text '{word.text}'")

        # Sort by timestamp to ensure correct order
        lyrics_data.sort(key=lambda x: x["timestamp"])
        return lyrics_data

    def _create_toml_file(
        self,
        audio_file: str,
        title: str,
        artist: str,
        lyrics_data: List[dict],
        cdg_styles: dict,
    ) -> str:
        """Create TOML configuration file for CDG generation."""
        safe_filename = self._get_safe_filename(artist, title, "Karaoke", "toml")
        toml_file = os.path.join(self.output_dir, safe_filename)
        self.logger.debug(f"Generating TOML file: {toml_file}")

        self.generate_toml(
            audio_file=audio_file,
            title=title,
            artist=artist,
            lyrics_data=lyrics_data,
            output_file=toml_file,
            cdg_styles=cdg_styles,
        )
        return toml_file

    def generate_toml(
        self,
        audio_file: str,
        title: str,
        artist: str,
        lyrics_data: List[dict],
        output_file: str,
        cdg_styles: dict,
    ) -> None:
        """Generate a TOML configuration file for CDG creation."""
        audio_file = os.path.abspath(audio_file)
        self.logger.debug(f"Using absolute audio file path: {audio_file}")

        self._validate_cdg_styles(cdg_styles)
        # Duet mode only kicks in when lyrics_data carries singer fields (set by
        # _convert_segments_to_lyrics_data when self.is_duet is True). The LRC
        # path never sets singer, so it always lands in the solo branch below.
        is_duet = self.is_duet and any("singer" in entry for entry in lyrics_data)
        instrumentals = self._detect_instrumentals(lyrics_data, cdg_styles)
        sync_times, formatted_lyrics, line_singers = self._format_lyrics_data(
            lyrics_data, instrumentals, cdg_styles, is_duet=is_duet,
        )

        toml_data = self._create_toml_data(
            title=title,
            artist=artist,
            audio_file=audio_file,
            output_name=f"{artist} - {title} (Karaoke)",
            sync_times=sync_times,
            instrumentals=instrumentals,
            formatted_lyrics=formatted_lyrics,
            cdg_styles=cdg_styles,
            line_singers=line_singers,
            is_duet=is_duet,
        )

        self._write_toml_file(toml_data, output_file)

    def _validate_and_setup_font(self, cdg_styles: dict) -> None:
        """Validate and set up font path in CDG styles.

        Resolution order:
        1. Use font_path as-is if it's an absolute path that exists
        2. Use font_path as-is if it exists as a relative path
        3. Try full relative path inside bundled fonts directory
        4. Try just the filename inside bundled fonts directory
        5. Fall back to arial.ttf from bundled fonts
        """
        if not cdg_styles.get("font_path"):
            return

        fonts_dir = os.path.join(os.path.dirname(__file__), "fonts")
        font_path = cdg_styles["font_path"]

        # Already exists (absolute or relative to cwd) — absolutize to avoid CWD issues
        if os.path.isfile(font_path):
            cdg_styles["font_path"] = os.path.abspath(font_path)
            return

        # Try full relative path in bundled fonts dir
        package_font_path = os.path.join(fonts_dir, font_path)
        if os.path.isfile(package_font_path):
            cdg_styles["font_path"] = os.path.abspath(package_font_path)
            self.logger.debug(f"Found font in package fonts directory: {package_font_path}")
            return

        # Try just the filename in bundled fonts dir
        font_filename = os.path.basename(font_path)
        package_font_by_name = os.path.join(fonts_dir, font_filename)
        if os.path.isfile(package_font_by_name):
            cdg_styles["font_path"] = os.path.abspath(package_font_by_name)
            self.logger.warning(
                f"Font file {font_path} not found, using bundled font: {font_filename}"
            )
            return

        # Last resort: fall back to arial.ttf
        fallback_font = os.path.join(fonts_dir, "arial.ttf")
        if os.path.isfile(fallback_font):
            cdg_styles["font_path"] = os.path.abspath(fallback_font)
            self.logger.warning(
                f"Font file {font_path} not found, falling back to bundled arial.ttf"
            )
            return

        self.logger.error(
            f"Font file {font_path} not found and no bundled fonts available in {fonts_dir}"
        )
        cdg_styles["font_path"] = None

    def _compose_cdg(self, toml_file: str) -> None:
        """Compose CDG using KaraokeComposer."""
        kc = KaraokeComposer.from_file(toml_file, logger=self.logger)
        kc.compose()
        # kc.create_mp4(height=1080, fps=30)

    def _find_cdg_zip(self, artist: str, title: str) -> str:
        """Find the generated CDG ZIP file."""
        safe_filename = self._get_safe_filename(artist, title, "Karaoke", "zip")
        output_zip = os.path.join(self.output_dir, safe_filename)

        self.logger.info(f"Looking for CDG ZIP file in output directory: {output_zip}")

        if os.path.isfile(output_zip):
            self.logger.info(f"Found CDG ZIP file: {output_zip}")
            return output_zip

        self.logger.error("Failed to find CDG ZIP file. Output directory contents:")
        for file in os.listdir(self.output_dir):
            self.logger.error(f" - {file}")
        raise FileNotFoundError(f"CDG ZIP file not found: {output_zip}")

    def _extract_cdg_files(self, zip_path: str) -> None:
        """Extract files from the CDG ZIP."""
        self.logger.info(f"Extracting CDG ZIP file: {zip_path}")
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(self.output_dir)

    def _get_cdg_path(self, artist: str, title: str) -> str:
        """Get the path to the CDG file."""
        safe_filename = self._get_safe_filename(artist, title, "Karaoke", "cdg")
        return os.path.join(self.output_dir, safe_filename)

    def _get_mp3_path(self, artist: str, title: str) -> str:
        """Get the path to the MP3 file."""
        safe_filename = self._get_safe_filename(artist, title, "Karaoke", "mp3")
        return os.path.join(self.output_dir, safe_filename)

    def _verify_output_files(self, cdg_file: str, mp3_file: str) -> None:
        """Verify that the required output files exist."""
        if not os.path.isfile(cdg_file):
            raise FileNotFoundError(f"CDG file not found after extraction: {cdg_file}")
        if not os.path.isfile(mp3_file):
            raise FileNotFoundError(f"MP3 file not found after extraction: {mp3_file}")

    def detect_instrumentals(
        self,
        lyrics_data,
        line_tile_height,
        instrumental_font_color,
        instrumental_background,
        instrumental_transition,
        instrumental_gap_threshold,
        instrumental_text,
    ):
        instrumentals = []
        for i in range(len(lyrics_data) - 1):
            current_end = lyrics_data[i]["timestamp"]
            next_start = lyrics_data[i + 1]["timestamp"]
            gap = next_start - current_end
            if gap >= instrumental_gap_threshold:
                instrumental_start = current_end + 200  # Add 2 seconds (200 centiseconds) delay
                instrumental_duration = (gap - 200) // 100  # Convert to seconds
                instrumentals.append(
                    {
                        "sync": instrumental_start,
                        "wait": True,
                        "text": f"{instrumental_text}\n{instrumental_duration} seconds\n",
                        "text_align": "center",
                        "text_placement": "bottom middle",
                        "line_tile_height": line_tile_height,
                        "fill": instrumental_font_color,
                        "stroke": "",
                        "image": instrumental_background,
                        "transition": instrumental_transition,
                    }
                )
                self.logger.info(
                    f"Detected instrumental: Gap of {gap} cs, starting at {instrumental_start} cs, duration {instrumental_duration} seconds"
                )

        self.logger.info(f"Total instrumentals detected: {len(instrumentals)}")
        return instrumentals

    def _validate_cdg_styles(self, cdg_styles: dict) -> None:
        """Validate required style parameters are present."""
        required_styles = {
            "title_color",
            "artist_color",
            "background_color",
            "border_color",
            "font_path",
            "font_size",
            "stroke_width",
            "stroke_style",
            "active_fill",
            "active_stroke",
            "inactive_fill",
            "inactive_stroke",
            "title_screen_background",
            "instrumental_background",
            "instrumental_transition",
            "instrumental_font_color",
            "title_screen_transition",
            "row",
            "line_tile_height",
            "lines_per_page",
            "clear_mode",
            "sync_offset",
            "instrumental_gap_threshold",
            "instrumental_text",
            "lead_in_threshold",
            "lead_in_symbols",
            "lead_in_duration",
            "lead_in_total",
            "title_artist_gap",
            "title_top_padding",
            "intro_duration_seconds",
            "first_syllable_buffer_seconds",
            "outro_background",
            "outro_transition",
            "outro_text_line1",
            "outro_text_line2",
            "outro_line1_color",
            "outro_line2_color",
            "outro_line1_line2_gap",
        }

        optional_styles_with_defaults = {
            "title_top_padding": 0,
            # Any other optional parameters with their default values
        }
        
        # Add any missing optional parameters with their default values
        for key, default_value in optional_styles_with_defaults.items():
            if key not in cdg_styles:
                cdg_styles[key] = default_value
        
        missing_styles = required_styles - set(cdg_styles.keys())
        if missing_styles:
            raise ValueError(f"Missing required style parameters: {', '.join(missing_styles)}")

    def _detect_instrumentals(self, lyrics_data: List[dict], cdg_styles: dict) -> List[dict]:
        """Detect instrumental sections in lyrics."""
        return self.detect_instrumentals(
            lyrics_data=lyrics_data,
            line_tile_height=cdg_styles["line_tile_height"],
            instrumental_font_color=cdg_styles["instrumental_font_color"],
            instrumental_background=cdg_styles["instrumental_background"],
            instrumental_transition=cdg_styles["instrumental_transition"],
            instrumental_gap_threshold=cdg_styles["instrumental_gap_threshold"],
            instrumental_text=cdg_styles["instrumental_text"],
        )

    def _format_lyrics_data(
        self,
        lyrics_data: List[dict],
        instrumentals: List[dict],
        cdg_styles: dict,
        is_duet: bool = False,
    ) -> tuple[List[int], str, Optional[List[int]]]:
        """Format lyrics data with lead-in symbols and handle line wrapping.

        Returns:
            tuple: (sync_times, formatted_text, line_singers)
            - sync_times: includes lead-in timings
            - formatted_text: newline-joined visual lines (no per-line singer
              prefixes — callers handle that)
            - line_singers: parallel list of singer indices per visual line
              when is_duet=True; None otherwise
        """
        sync_times = []
        formatted_lyrics = []
        word_singers: Optional[List[int]] = [] if is_duet else None

        for i, lyric in enumerate(lyrics_data):
            self.logger.debug(f"Processing lyric {i}: timestamp {lyric['timestamp']}, text '{lyric['text']}'")
            singer_for_word = lyric.get("singer", 1) if is_duet else None

            if i == 0 or lyric["timestamp"] - lyrics_data[i - 1]["timestamp"] >= cdg_styles["lead_in_threshold"]:
                lead_in_start = lyric["timestamp"] - cdg_styles["lead_in_total"]
                self.logger.debug(f"Adding lead-in before lyric {i} at timestamp {lead_in_start}")
                for j, symbol in enumerate(cdg_styles["lead_in_symbols"]):
                    sync_time = lead_in_start + j * cdg_styles["lead_in_duration"]
                    sync_times.append(sync_time)
                    formatted_lyrics.append(symbol)
                    if word_singers is not None:
                        # Lead-in symbols inherit the following lyric's singer
                        word_singers.append(singer_for_word)
                    self.logger.debug(f"  Added lead-in symbol {j+1}: '{symbol}' at {sync_time}")

            sync_times.append(lyric["timestamp"])
            formatted_lyrics.append(lyric["text"])
            if word_singers is not None:
                word_singers.append(singer_for_word)
            self.logger.debug(f"Added lyric: '{lyric['text']}' at {lyric['timestamp']}")

        formatted_text, line_singers = self.format_lyrics(
            formatted_lyrics,
            instrumentals,
            sync_times,
            font_path=cdg_styles["font_path"],
            font_size=cdg_styles["font_size"],
            word_singers=word_singers,
        )

        return sync_times, formatted_text, line_singers

    def _create_toml_data(
        self,
        title: str,
        artist: str,
        audio_file: str,
        output_name: str,
        sync_times: List[int],
        instrumentals: List[dict],
        formatted_lyrics,
        cdg_styles: dict,
        line_singers: Optional[List[int]] = None,
        is_duet: bool = False,
    ) -> dict:
        """Create TOML data structure."""
        if not cdg_styles.get("font_path"):
            raise RuntimeError(
                "CDG font_path is None — cannot generate CDG without a font. "
                "Check that the font file exists and was downloaded correctly."
            )
        safe_output_name = self._get_safe_filename(artist, title, "Karaoke")

        # Build the singers palette and per-line singer-tagged text
        if is_duet:
            # Lazy import to avoid pulling style_loader at module load time
            from karaoke_gen.style_loader import CDG_DUET_SINGERS
            singers = [
                {
                    "active_fill": _rgb_to_hex(s.active_fill),
                    "active_stroke": _rgb_to_hex(s.active_stroke),
                    "inactive_fill": _rgb_to_hex(s.inactive_fill),
                    "inactive_stroke": _rgb_to_hex(s.inactive_stroke),
                }
                for s in CDG_DUET_SINGERS
            ]
            lyric_lines = formatted_lyrics.split("\n") if isinstance(formatted_lyrics, str) else list(formatted_lyrics)
            if line_singers is None or len(line_singers) != len(lyric_lines):
                # Defensive fallback: if we somehow lost the mapping, default
                # every line to singer 1. This keeps the file valid instead of
                # crashing the render.
                self.logger.warning(
                    "CDG duet: line_singers missing/mismatched (have %s, lines %s) — defaulting to singer 1",
                    0 if line_singers is None else len(line_singers),
                    len(lyric_lines),
                )
                line_singers = [1] * len(lyric_lines)
            lyric_text = "\n".join(f"{s}|{line}" for s, line in zip(line_singers, lyric_lines))
        else:
            singers = [
                {
                    "active_fill": cdg_styles["active_fill"],
                    "active_stroke": cdg_styles["active_stroke"],
                    "inactive_fill": cdg_styles["inactive_fill"],
                    "inactive_stroke": cdg_styles["inactive_stroke"],
                }
            ]
            lyric_text = formatted_lyrics

        return {
            "title": title,
            "artist": artist,
            "file": audio_file,
            "outname": safe_output_name,
            "clear_mode": cdg_styles["clear_mode"],
            "sync_offset": cdg_styles["sync_offset"],
            "background": cdg_styles["background_color"],
            "border": cdg_styles["border_color"],
            "font": cdg_styles["font_path"],
            "font_size": cdg_styles["font_size"],
            "stroke_width": cdg_styles["stroke_width"],
            "stroke_style": cdg_styles["stroke_style"],
            "singers": singers,
            "lyrics": [
                {
                    "singer": 1,
                    "sync": sync_times,
                    "row": cdg_styles["row"],
                    "line_tile_height": cdg_styles["line_tile_height"],
                    "lines_per_page": cdg_styles["lines_per_page"],
                    "text": lyric_text,
                }
            ],
            "title_color": cdg_styles["title_color"],
            "artist_color": cdg_styles["artist_color"],
            "title_screen_background": cdg_styles["title_screen_background"],
            "title_screen_transition": cdg_styles["title_screen_transition"],
            "instrumentals": instrumentals,
            "intro_duration_seconds": cdg_styles["intro_duration_seconds"],
            "title_top_padding": cdg_styles["title_top_padding"],
            "title_artist_gap": cdg_styles["title_artist_gap"],
            "first_syllable_buffer_seconds": cdg_styles["first_syllable_buffer_seconds"],
            "outro_background": cdg_styles["outro_background"],
            "outro_transition": cdg_styles["outro_transition"],
            "outro_text_line1": cdg_styles["outro_text_line1"],
            "outro_text_line2": cdg_styles["outro_text_line2"],
            "outro_line1_color": cdg_styles["outro_line1_color"],
            "outro_line2_color": cdg_styles["outro_line2_color"],
            "outro_line1_line2_gap": cdg_styles["outro_line1_line2_gap"],
        }

    def _write_toml_file(self, toml_data: dict, output_file: str) -> None:
        """Write TOML data to file."""
        with open(output_file, "w", encoding="utf-8") as f:
            toml.dump(toml_data, f)
        self.logger.info(f"TOML file generated: {output_file}")

    def get_font(self, font_path=None, font_size=18):
        try:
            return ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
        except IOError:
            self.logger.warning(f"Font file {font_path} not found. Using default font.")
            return ImageFont.load_default()

    def get_text_width(self, text, font):
        return font.getmask(text).getbbox()[2]

    def wrap_text(self, text, max_width, font):
        words = text.split()
        lines = []
        current_line = []
        current_width = 0

        for word in words:
            word_width = self.get_text_width(word, font)
            if current_width + word_width <= max_width:
                current_line.append(word)
                current_width += word_width + self.get_text_width(" ", font)
            else:
                if current_line:
                    lines.append(" ".join(current_line))
                    self.logger.debug(f"Wrapped line: {' '.join(current_line)}")
                current_line = [word]
                current_width = word_width

        if current_line:
            lines.append(" ".join(current_line))
            self.logger.debug(f"Wrapped line: {' '.join(current_line)}")

        return lines

    def format_lyrics(
        self,
        lyrics_data,
        instrumentals,
        sync_times,
        font_path=None,
        font_size=18,
        word_singers: Optional[List[int]] = None,
    ):
        """Word-wrap lyrics into visual lines and pad pages.

        When ``word_singers`` is provided (duet mode), we track the singer
        associated with each visual line (accumulated from the current_line's
        source words) and return both the joined string and a parallel
        line_singers list. When it is None (solo), returns (joined_string, None)
        to preserve the legacy signature's text output exactly.
        """
        formatted_lyrics: List[str] = []
        line_singers: Optional[List[int]] = [] if word_singers is not None else None
        font = self.get_font(font_path, font_size)
        self.logger.debug(f"Using font: {font}")

        current_line = ""
        # Track the singer that owns current_line. All words accumulated into
        # current_line share a singer (duet mode inserts '/' at segment
        # boundaries, which forces a flush before the next singer begins).
        current_line_singer: Optional[int] = None
        last_seen_singer: int = 1  # For padding '~' lines after final flush
        lines_on_page = 0
        page_number = 1

        def _flush_wrapped(wrapped_lines: List[str], owner_singer: Optional[int]) -> None:
            """Append wrapped lines (with post-punctuation padding) and track singers."""
            nonlocal lines_on_page, page_number
            for wrapped_line in wrapped_lines:
                formatted_lyrics.append(wrapped_line)
                if line_singers is not None:
                    line_singers.append(owner_singer if owner_singer is not None else 1)
                lines_on_page += 1
                self.logger.debug(f"format_lyrics: Added wrapped line: '{wrapped_line}'. Lines on page: {lines_on_page}")
                # Add empty line after punctuation immediately
                if wrapped_line.endswith(("!", "?", ".")) and not wrapped_line == "~":
                    formatted_lyrics.append("~")
                    if line_singers is not None:
                        line_singers.append(owner_singer if owner_singer is not None else 1)
                    lines_on_page += 1
                    self.logger.debug(f"format_lyrics: Added empty line after punctuation. Lines on page now: {lines_on_page}")
                if lines_on_page == 4:
                    lines_on_page = 0
                    page_number += 1
                    self.logger.debug(f"format_lyrics: Page full. New page number: {page_number}")

        for i, text in enumerate(lyrics_data):
            self.logger.debug(f"format_lyrics: Processing text {i}: '{text}' (sync time: {sync_times[i]})")
            entry_singer = word_singers[i] if word_singers is not None else None
            if entry_singer is not None:
                last_seen_singer = entry_singer

            if text.startswith("/"):
                if current_line:
                    wrapped_lines = get_wrapped_text(current_line.strip(), font, CDG_VISIBLE_WIDTH).split("\n")
                    _flush_wrapped(wrapped_lines, current_line_singer)
                    current_line = ""
                # Start fresh: current_line's singer is this new entry's singer
                current_line_singer = entry_singer
                text = text[1:]
            else:
                # Keep current_line_singer set on first accumulation
                if current_line_singer is None:
                    current_line_singer = entry_singer

            current_line += text + " "
            # self.logger.debug(f"format_lyrics: Current line: '{current_line}'")

            is_last_before_instrumental = any(
                inst["sync"] > sync_times[i] and (i == len(sync_times) - 1 or sync_times[i + 1] > inst["sync"]) for inst in instrumentals
            )

            if is_last_before_instrumental or i == len(lyrics_data) - 1:
                if current_line:
                    wrapped_lines = get_wrapped_text(current_line.strip(), font, CDG_VISIBLE_WIDTH).split("\n")
                    _flush_wrapped(wrapped_lines, current_line_singer)
                    current_line = ""
                    current_line_singer = None

                if is_last_before_instrumental:
                    self.logger.debug(f"format_lyrics: is_last_before_instrumental: True lines_on_page: {lines_on_page}")
                    # Calculate remaining lines needed to reach next full page
                    remaining_lines = 4 - (lines_on_page % 4) if lines_on_page % 4 != 0 else 0
                    if remaining_lines > 0:
                        formatted_lyrics.extend(["~"] * remaining_lines)
                        if line_singers is not None:
                            line_singers.extend([last_seen_singer] * remaining_lines)
                        self.logger.debug(f"format_lyrics: Added {remaining_lines} empty lines to complete current page")

                    # Reset the counter and increment page
                    lines_on_page = 0
                    page_number += 1
                    self.logger.debug(f"format_lyrics: Reset lines_on_page to 0. New page number: {page_number}")

        joined = "\n".join(formatted_lyrics)
        if word_singers is None:
            # Legacy callers (LRC path) expect a single string return value.
            # Preserve that by returning a tuple only when duet mode was
            # requested; otherwise return the string directly.
            return joined, None
        return joined, line_singers

    def generate_cdg_from_lrc(
        self,
        lrc_file: str,
        audio_file: str,
        title: str,
        artist: str,
        cdg_styles: dict,
        lrc_has_countdown_padding: bool = False,
        countdown_padding_seconds: float = 3.0,
    ) -> Tuple[str, str, str]:
        """Generate a CDG file from an LRC file and audio file.

        Args:
            lrc_file: Path to the LRC file
            audio_file: Path to the audio file
            title: Title of the song
            artist: Artist name
            cdg_styles: Dictionary containing CDG style parameters
            lrc_has_countdown_padding: If True, LRC has countdown-shifted timestamps
                that need to be adjusted since CDG uses instrumental audio without
                the countdown padding applied
            countdown_padding_seconds: Duration of countdown to remove (default 3.0)

        Returns:
            Tuple containing paths to (cdg_file, mp3_file, zip_file)
        """
        self._validate_and_setup_font(cdg_styles)

        # Parse LRC file and convert to lyrics_data format
        lyrics_data = self._parse_lrc(lrc_file)

        # If LRC has countdown padding, remove the countdown segment and adjust timestamps
        # This is needed because CDG uses instrumental audio without countdown padding,
        # but the LRC was generated from video rendering which added countdown
        if lrc_has_countdown_padding:
            lyrics_data = self._remove_countdown_from_lyrics(lyrics_data, countdown_padding_seconds)

        toml_file = self._create_toml_file(
            audio_file=audio_file,
            title=title,
            artist=artist,
            lyrics_data=lyrics_data,
            cdg_styles=cdg_styles,
        )

        try:
            self._compose_cdg(toml_file)
            output_zip = self._find_cdg_zip(artist, title)
            self._extract_cdg_files(output_zip)

            cdg_file = self._get_cdg_path(artist, title)
            mp3_file = self._get_mp3_path(artist, title)

            self._verify_output_files(cdg_file, mp3_file)

            self.logger.info("CDG file generated successfully")
            return cdg_file, mp3_file, output_zip

        except Exception as e:
            self.logger.error(f"Error composing CDG: {e}")
            raise

    def _parse_lrc(self, lrc_file: str) -> List[dict]:
        """Parse LRC file and extract timestamps and lyrics."""
        with open(lrc_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Extract timestamps and lyrics
        pattern = r"\[(\d{2}):(\d{2})\.(\d{3})\](\d+:)?(/?.*)"
        matches = re.findall(pattern, content)

        if not matches:
            raise ValueError(f"No valid lyrics found in the LRC file: {lrc_file}")

        lyrics = []
        for match in matches:
            minutes, seconds, milliseconds = map(int, match[:3])
            timestamp = (minutes * 60 + seconds) * 100 + int(milliseconds / 10)  # Convert to centiseconds
            text = match[4].strip().upper()
            if text:  # Only add non-empty lyrics
                lyrics.append({"timestamp": timestamp, "text": text})

        self.logger.info(f"Found {len(lyrics)} lyric lines")
        return lyrics

    def _remove_countdown_from_lyrics(
        self,
        lyrics_data: List[dict],
        countdown_padding_seconds: float
    ) -> List[dict]:
        """Remove countdown segment and shift timestamps back by countdown duration.

        When video rendering adds a countdown (e.g., "3... 2... 1..."), it:
        1. Adds a countdown segment near the start (timestamp < countdown_padding)
        2. Shifts all subsequent lyrics timestamps forward by countdown_padding

        CDG uses instrumental audio without this padding, so we need to:
        1. Remove the countdown segment entirely
        2. Shift all remaining timestamps back to their original positions

        Args:
            lyrics_data: List of lyric entries with timestamp (centiseconds) and text
            countdown_padding_seconds: Duration of countdown padding in seconds

        Returns:
            Adjusted lyrics data with countdown removed and timestamps shifted back
        """
        countdown_cs = int(countdown_padding_seconds * 100)  # Convert to centiseconds

        adjusted = []
        skipped_countdown = False

        for entry in lyrics_data:
            # Skip entries that are part of the countdown (timestamp < countdown duration)
            # These are typically "3... 2... 1..." or similar countdown text
            if entry["timestamp"] < countdown_cs:
                self.logger.debug(f"Skipping countdown segment: timestamp={entry['timestamp']}cs, text='{entry['text']}'")
                skipped_countdown = True
                continue

            # Shift remaining timestamps back by the countdown duration
            adjusted.append({
                "timestamp": entry["timestamp"] - countdown_cs,
                "text": entry["text"]
            })

        if skipped_countdown:
            self.logger.info(
                f"Removed countdown padding: {len(lyrics_data)} -> {len(adjusted)} entries, "
                f"shifted timestamps by -{countdown_cs}cs ({countdown_padding_seconds}s)"
            )
        else:
            self.logger.warning(
                f"lrc_has_countdown_padding was True but no countdown segment found "
                f"(no entries with timestamp < {countdown_cs}cs). Proceeding with timestamp shift only."
            )
            # Still shift all timestamps even if no countdown segment was found
            adjusted = [
                {"timestamp": entry["timestamp"] - countdown_cs, "text": entry["text"]}
                for entry in lyrics_data
            ]

        return adjusted
