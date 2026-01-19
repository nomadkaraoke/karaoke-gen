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
from karaoke_gen.lyrics_transcriber.output.cdgmaker.render import get_wrapped_text
from karaoke_gen.lyrics_transcriber.types import LyricsSegment


class CDGGenerator:
    """Generates CD+G (CD Graphics) format karaoke files."""

    def __init__(self, output_dir: str, logger: Optional[logging.Logger] = None):
        """Initialize CDGGenerator.

        Args:
            output_dir: Directory where output files will be written
            logger: Optional logger instance
        """
        self.output_dir = output_dir
        self.logger = logger or logging.getLogger(__name__)
        self.cdg_visible_width = 280

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
        lyrics_data = self._convert_segments_to_lyrics_data(segments)

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

    def _convert_segments_to_lyrics_data(self, segments: List[LyricsSegment]) -> List[dict]:
        """Convert LyricsSegment objects to the format needed for CDG generation."""
        lyrics_data = []

        for segment in segments:
            # Convert each word to a lyric entry
            for word in segment.words:
                # Convert time from seconds to centiseconds
                timestamp = int(word.start_time * 100)
                lyrics_data.append({"timestamp": timestamp, "text": word.text.upper()})  # CDG format expects uppercase text
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
        instrumentals = self._detect_instrumentals(lyrics_data, cdg_styles)
        sync_times, formatted_lyrics = self._format_lyrics_data(lyrics_data, instrumentals, cdg_styles)

        toml_data = self._create_toml_data(
            title=title,
            artist=artist,
            audio_file=audio_file,
            output_name=f"{artist} - {title} (Karaoke)",
            sync_times=sync_times,
            instrumentals=instrumentals,
            formatted_lyrics=formatted_lyrics,
            cdg_styles=cdg_styles,
        )

        self._write_toml_file(toml_data, output_file)

    def _validate_and_setup_font(self, cdg_styles: dict) -> None:
        """Validate and set up font path in CDG styles."""
        if not cdg_styles.get("font_path"):
            return

        if not os.path.isabs(cdg_styles["font_path"]) and not os.path.exists(cdg_styles["font_path"]):
            package_font_path = os.path.join(os.path.dirname(__file__), "fonts", cdg_styles["font_path"])
            if os.path.exists(package_font_path):
                cdg_styles["font_path"] = package_font_path
                self.logger.debug(f"Found font in package fonts directory: {cdg_styles['font_path']}")
            else:
                self.logger.warning(
                    f"Font file {cdg_styles['font_path']} not found in package fonts directory {package_font_path}, will use default font"
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

    def _format_lyrics_data(self, lyrics_data: List[dict], instrumentals: List[dict], cdg_styles: dict) -> tuple[List[int], List[str]]:
        """Format lyrics data with lead-in symbols and handle line wrapping.

        Returns:
            tuple: (sync_times, formatted_lyrics) where sync_times includes lead-in timings
        """
        sync_times = []
        formatted_lyrics = []

        for i, lyric in enumerate(lyrics_data):
            self.logger.debug(f"Processing lyric {i}: timestamp {lyric['timestamp']}, text '{lyric['text']}'")

            if i == 0 or lyric["timestamp"] - lyrics_data[i - 1]["timestamp"] >= cdg_styles["lead_in_threshold"]:
                lead_in_start = lyric["timestamp"] - cdg_styles["lead_in_total"]
                self.logger.debug(f"Adding lead-in before lyric {i} at timestamp {lead_in_start}")
                for j, symbol in enumerate(cdg_styles["lead_in_symbols"]):
                    sync_time = lead_in_start + j * cdg_styles["lead_in_duration"]
                    sync_times.append(sync_time)
                    formatted_lyrics.append(symbol)
                    self.logger.debug(f"  Added lead-in symbol {j+1}: '{symbol}' at {sync_time}")

            sync_times.append(lyric["timestamp"])
            formatted_lyrics.append(lyric["text"])
            self.logger.debug(f"Added lyric: '{lyric['text']}' at {lyric['timestamp']}")

        formatted_text = self.format_lyrics(
            formatted_lyrics,
            instrumentals,
            sync_times,
            font_path=cdg_styles["font_path"],
            font_size=cdg_styles["font_size"],
        )

        return sync_times, formatted_text

    def _create_toml_data(
        self,
        title: str,
        artist: str,
        audio_file: str,
        output_name: str,
        sync_times: List[int],
        instrumentals: List[dict],
        formatted_lyrics: List[str],
        cdg_styles: dict,
    ) -> dict:
        """Create TOML data structure."""
        safe_output_name = self._get_safe_filename(artist, title, "Karaoke")
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
            "singers": [
                {
                    "active_fill": cdg_styles["active_fill"],
                    "active_stroke": cdg_styles["active_stroke"],
                    "inactive_fill": cdg_styles["inactive_fill"],
                    "inactive_stroke": cdg_styles["inactive_stroke"],
                }
            ],
            "lyrics": [
                {
                    "singer": 1,
                    "sync": sync_times,
                    "row": cdg_styles["row"],
                    "line_tile_height": cdg_styles["line_tile_height"],
                    "lines_per_page": cdg_styles["lines_per_page"],
                    "text": formatted_lyrics,
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

    def format_lyrics(self, lyrics_data, instrumentals, sync_times, font_path=None, font_size=18):
        formatted_lyrics = []
        font = self.get_font(font_path, font_size)
        self.logger.debug(f"Using font: {font}")

        current_line = ""
        lines_on_page = 0
        page_number = 1

        for i, text in enumerate(lyrics_data):
            self.logger.debug(f"format_lyrics: Processing text {i}: '{text}' (sync time: {sync_times[i]})")

            if text.startswith("/"):
                if current_line:
                    wrapped_lines = get_wrapped_text(current_line.strip(), font, CDG_VISIBLE_WIDTH).split("\n")
                    for wrapped_line in wrapped_lines:
                        formatted_lyrics.append(wrapped_line)
                        lines_on_page += 1
                        self.logger.debug(f"format_lyrics: Added wrapped line: '{wrapped_line}'. Lines on page: {lines_on_page}")
                        # Add empty line after punctuation immediately
                        if wrapped_line.endswith(("!", "?", ".")) and not wrapped_line == "~":
                            formatted_lyrics.append("~")
                            lines_on_page += 1
                            self.logger.debug(f"format_lyrics: Added empty line after punctuation. Lines on page now: {lines_on_page}")
                        if lines_on_page == 4:
                            lines_on_page = 0
                            page_number += 1
                            self.logger.debug(f"format_lyrics: Page full. New page number: {page_number}")
                    current_line = ""
                text = text[1:]

            current_line += text + " "
            # self.logger.debug(f"format_lyrics: Current line: '{current_line}'")

            is_last_before_instrumental = any(
                inst["sync"] > sync_times[i] and (i == len(sync_times) - 1 or sync_times[i + 1] > inst["sync"]) for inst in instrumentals
            )

            if is_last_before_instrumental or i == len(lyrics_data) - 1:
                if current_line:
                    wrapped_lines = get_wrapped_text(current_line.strip(), font, CDG_VISIBLE_WIDTH).split("\n")
                    for wrapped_line in wrapped_lines:
                        formatted_lyrics.append(wrapped_line)
                        lines_on_page += 1
                        self.logger.debug(
                            f"format_lyrics: Added wrapped line at end of section: '{wrapped_line}'. Lines on page: {lines_on_page}"
                        )
                        if lines_on_page == 4:
                            lines_on_page = 0
                            page_number += 1
                            self.logger.debug(f"format_lyrics: Page full. New page number: {page_number}")
                    current_line = ""

                if is_last_before_instrumental:
                    self.logger.debug(f"format_lyrics: is_last_before_instrumental: True lines_on_page: {lines_on_page}")
                    # Calculate remaining lines needed to reach next full page
                    remaining_lines = 4 - (lines_on_page % 4) if lines_on_page % 4 != 0 else 0
                    if remaining_lines > 0:
                        formatted_lyrics.extend(["~"] * remaining_lines)
                        self.logger.debug(f"format_lyrics: Added {remaining_lines} empty lines to complete current page")

                    # Reset the counter and increment page
                    lines_on_page = 0
                    page_number += 1
                    self.logger.debug(f"format_lyrics: Reset lines_on_page to 0. New page number: {page_number}")

        return "\n".join(formatted_lyrics)

    def generate_cdg_from_lrc(
        self,
        lrc_file: str,
        audio_file: str,
        title: str,
        artist: str,
        cdg_styles: dict,
    ) -> Tuple[str, str, str]:
        """Generate a CDG file from an LRC file and audio file.

        Args:
            lrc_file: Path to the LRC file
            audio_file: Path to the audio file
            title: Title of the song
            artist: Artist name
            cdg_styles: Dictionary containing CDG style parameters

        Returns:
            Tuple containing paths to (cdg_file, mp3_file, zip_file)
        """
        self._validate_and_setup_font(cdg_styles)

        # Parse LRC file and convert to lyrics_data format
        lyrics_data = self._parse_lrc(lrc_file)

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
