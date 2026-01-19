import os
import logging
from typing import List, Optional, Tuple, Union
import subprocess
import json

from karaoke_gen.lyrics_transcriber.output.ass.section_screen import SectionScreen
from karaoke_gen.lyrics_transcriber.types import LyricsSegment, Word
from karaoke_gen.lyrics_transcriber.output.ass import LyricsScreen, LyricsLine
from karaoke_gen.lyrics_transcriber.output.ass.ass import ASS
from karaoke_gen.lyrics_transcriber.output.ass.style import Style
from karaoke_gen.lyrics_transcriber.output.ass.constants import ALIGN_TOP_CENTER
from karaoke_gen.lyrics_transcriber.output.ass import LyricsScreen
from karaoke_gen.lyrics_transcriber.output.ass.section_detector import SectionDetector
from karaoke_gen.lyrics_transcriber.output.ass.config import ScreenConfig


class SubtitlesGenerator:
    """Handles generation of subtitle files in various formats."""

    def __init__(
        self,
        output_dir: str,
        video_resolution: Tuple[int, int],
        font_size: int,
        line_height: int,
        styles: dict,
        subtitle_offset_ms: int = 0,
        logger: Optional[logging.Logger] = None,
    ):
        """Initialize SubtitleGenerator.

        Args:
            output_dir: Directory where output files will be written
            video_resolution: Tuple of (width, height) for video resolution
            font_size: Font size for subtitles
            line_height: Line height for subtitle positioning
            styles: Dictionary of style configurations
            subtitle_offset_ms: Offset for subtitle timing in milliseconds
            logger: Optional logger instance
        """
        self.output_dir = output_dir
        self.video_resolution = video_resolution
        self.font_size = font_size
        self.styles = styles
        self.subtitle_offset_ms = subtitle_offset_ms
        
        # Create ScreenConfig with potential overrides from styles
        karaoke_styles = styles.get("karaoke", {})
        config_params = {
            "line_height": line_height,
            "video_width": video_resolution[0],
            "video_height": video_resolution[1]
        }
        
        # Add any overrides from styles
        screen_config_props = [
            "max_visible_lines",
            "top_padding",
            "screen_gap_threshold",
            "post_roll_time",
            "fade_in_ms",
            "fade_out_ms",
            "lead_in_color",
            "text_case_transform",
            # New lead-in indicator configuration options
            "lead_in_enabled",
            "lead_in_width_percent",
            "lead_in_height_percent",
            "lead_in_opacity_percent",
            "lead_in_outline_thickness",
            "lead_in_outline_color",
            "lead_in_gap_threshold",
            "lead_in_horiz_offset_percent",
            "lead_in_vert_offset_percent",
        ]
        
        for prop in screen_config_props:
            if prop in karaoke_styles:
                config_params[prop] = karaoke_styles[prop]
        
        self.config = ScreenConfig(**config_params)
        self.logger = logger or logging.getLogger(__name__)

    def _get_output_path(self, output_prefix: str, extension: str) -> str:
        """Generate full output path for a file."""
        return os.path.join(self.output_dir, f"{output_prefix}.{extension}")

    def _get_audio_duration(self, audio_filepath: str, segments: Optional[List[LyricsSegment]] = None) -> float:
        """Get audio duration using ffprobe."""
        try:
            probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", audio_filepath]
            probe_output = subprocess.check_output(probe_cmd, universal_newlines=True)
            probe_data = json.loads(probe_output)
            duration = float(probe_data["format"]["duration"])
            self.logger.debug(f"Detected audio duration: {duration:.2f}s")
            return duration
        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as e:
            self.logger.error(f"Failed to get audio duration: {e}")
            # Fallback to last segment end time plus buffer
            if segments:
                duration = segments[-1].end_time + 30.0
                self.logger.warning(f"Using fallback duration: {duration:.2f}s")
                return duration
            return 0.0

    def generate_ass(self, segments: List[LyricsSegment], output_prefix: str, audio_filepath: str) -> str:
        self.logger.info("Generating ASS format subtitles")
        output_path = self._get_output_path(f"{output_prefix} (Karaoke)", "ass")

        try:
            self.logger.debug(f"Processing {len(segments)} segments")
            song_duration = self._get_audio_duration(audio_filepath, segments)

            screens = self._create_screens(segments, song_duration)
            self.logger.debug(f"Created {len(screens)} initial screens")

            lyric_subtitles_ass = self._create_styled_subtitles(screens, self.video_resolution, self.font_size)
            self.logger.debug("Created styled subtitles")

            lyric_subtitles_ass.write(output_path)
            self.logger.info(f"ASS file generated: {output_path}")
            return output_path

        except Exception as e:
            self.logger.error(f"Failed to generate ASS file: {str(e)}", exc_info=True)
            raise

    def _create_screens(self, segments: List[LyricsSegment], song_duration: float) -> List[LyricsScreen]:
        """Create screens from segments with detailed logging."""
        self.logger.debug("Creating screens from segments")

        # Apply timing offset to segments if needed
        if self.subtitle_offset_ms != 0:
            self.logger.info(f"Subtitle offset: {self.subtitle_offset_ms}ms")

            offset_seconds = self.subtitle_offset_ms / 1000.0
            segments = [
                LyricsSegment(
                    id=seg.id,  # Preserve original segment ID
                    text=seg.text,
                    words=[
                        Word(
                            id=word.id,  # Preserve original word ID
                            text=word.text,
                            start_time=max(0, word.start_time + offset_seconds),
                            end_time=word.end_time + offset_seconds,
                            confidence=word.confidence,
                            created_during_correction=getattr(word, "created_during_correction", False),  # Preserve correction flag
                        )
                        for word in seg.words
                    ],
                    start_time=max(0, seg.start_time + offset_seconds),
                    end_time=seg.end_time + offset_seconds,
                )
                for seg in segments
            ]
            self.logger.info(f"Applied {self.subtitle_offset_ms}ms offset to segment timings")

        # Create section screens and get instrumental boundaries
        section_screens = self._create_section_screens(segments, song_duration)
        instrumental_times = self._get_instrumental_times(section_screens)

        # Create regular lyric screens
        lyric_screens = self._create_lyric_screens(segments, instrumental_times)

        # Merge and process all screens
        all_screens = self._merge_and_process_screens(section_screens, lyric_screens)

        # Log final results
        self._log_final_screens(all_screens)

        return all_screens

    def _create_section_screens(self, segments: List[LyricsSegment], song_duration: float) -> List[SectionScreen]:
        """Create section screens using SectionDetector."""
        section_detector = SectionDetector(logger=self.logger)
        return section_detector.process_segments(segments, self.video_resolution, self.config.line_height, song_duration)

    def _get_instrumental_times(self, section_screens: List[SectionScreen]) -> List[Tuple[float, float]]:
        """Extract instrumental section time boundaries."""
        instrumental_times = [
            (s.start_time, s.end_time) for s in section_screens if isinstance(s, SectionScreen) and s.section_type == "INSTRUMENTAL"
        ]

        self.logger.debug(f"Found {len(instrumental_times)} instrumental sections:")
        for start, end in instrumental_times:
            self.logger.debug(f"  {start:.2f}s - {end:.2f}s")

        return instrumental_times

    def _create_lyric_screens(self, segments: List[LyricsSegment], instrumental_times: List[Tuple[float, float]]) -> List[LyricsScreen]:
        """Create regular lyric screens, handling instrumental boundaries."""
        screens: List[LyricsScreen] = []
        current_screen: Optional[LyricsScreen] = None

        for i, segment in enumerate(segments):
            self.logger.debug(f"Processing segment {i}: {segment.start_time:.2f}s - {segment.end_time:.2f}s")

            # Skip segments in instrumental sections
            if self._is_in_instrumental_section(segment, instrumental_times):
                continue

            # Check if we need a new screen
            if self._should_start_new_screen(current_screen, segment, instrumental_times):
                # fmt: off
                current_screen = LyricsScreen(
                    video_size=self.video_resolution,
                    line_height=self.config.line_height,
                    config=self.config,
                    logger=self.logger
                )
                # fmt: on
                screens.append(current_screen)
                self.logger.debug("  Created new screen")

            # Add line to current screen
            line = LyricsLine(logger=self.logger, segment=segment, screen_config=self.config)
            current_screen.lines.append(line)
            self.logger.debug(f"  Added line to screen (now has {len(current_screen.lines)} lines)")

        return screens

    def _is_in_instrumental_section(self, segment: LyricsSegment, instrumental_times: List[Tuple[float, float]]) -> bool:
        """Check if a segment falls within any instrumental section."""
        for inst_start, inst_end in instrumental_times:
            if segment.start_time >= inst_start and segment.start_time < inst_end:
                self.logger.debug(f"  Skipping segment - falls within instrumental {inst_start:.2f}s - {inst_end:.2f}s")
                return True
        return False

    def _should_start_new_screen(
        self, current_screen: Optional[LyricsScreen], segment: LyricsSegment, instrumental_times: List[Tuple[float, float]]
    ) -> bool:
        """Determine if a new screen should be started."""
        if current_screen is None:
            return True

        if len(current_screen.lines) >= self.config.max_visible_lines:
            return True

        # Check if this segment is first after any instrumental section
        if current_screen.lines:
            prev_segment = current_screen.lines[-1].segment
            for inst_start, inst_end in instrumental_times:
                if prev_segment.end_time <= inst_start and segment.start_time >= inst_end:
                    self.logger.debug(f"  Forcing new screen - first segment after instrumental {inst_start:.2f}s - {inst_end:.2f}s")
                    return True

        return False

    def _merge_and_process_screens(
        self, section_screens: List[SectionScreen], lyric_screens: List[LyricsScreen]
    ) -> List[Union[SectionScreen, LyricsScreen]]:
        """Merge section and lyric screens in chronological order."""
        # Sort all screens by start time
        return sorted(section_screens + lyric_screens, key=lambda s: s.start_ts)

    def _log_final_screens(self, screens: List[Union[SectionScreen, LyricsScreen]]) -> None:
        """Log details of all final screens."""
        self.logger.debug("Final screens created:")
        for i, screen in enumerate(screens):
            self.logger.debug(f"Screen {i + 1}:")
            if isinstance(screen, SectionScreen):
                self.logger.debug(f"  Section: {screen.section_type}")
                self.logger.debug(f"  Text: {screen.text}")
                self.logger.debug(f"  Time: {screen.start_time:.2f}s - {screen.end_time:.2f}s")
            else:
                self.logger.debug(f"  Number of lines: {len(screen.lines)}")
                for j, line in enumerate(screen.lines):
                    self.logger.debug(f"    Line {j + 1} ({line.segment.start_time:.2f}s - {line.segment.end_time:.2f}s): {line}")

    def _create_styled_ass_instance(self, resolution, fontsize):
        a = ASS()
        a.set_resolution(resolution)

        a.styles_format = [
            "Name",  # The name of the Style. Case sensitive. Cannot include commas.
            "Fontname",  # The fontname as used by Windows. Case-sensitive.
            "Fontpath",  # The path to the font file.
            "Fontsize",  # Font size
            "PrimaryColour",  # This is the colour that a subtitle will normally appear in.
            "SecondaryColour",  # This colour may be used instead of the Primary colour when a subtitle is automatically shifted to prevent an onscreen collsion, to distinguish the different subtitles.
            "OutlineColour",  # This colour may be used instead of the Primary or Secondary colour when a subtitle is automatically shifted to prevent an onscreen collsion, to distinguish the different subtitles.
            "BackColour",  # This is the colour of the subtitle outline or shadow, if these are used
            "Bold",  # This defines whether text is bold (true) or not (false). -1 is True, 0 is False
            "Italic",  # This defines whether text is italic (true) or not (false). -1 is True, 0 is False
            "Underline",  # [-1 or 0]
            "StrikeOut",  # [-1 or 0]
            "ScaleX",  # Modifies the width of the font. [percent]
            "ScaleY",  # Modifies the height of the font. [percent]
            "Spacing",  # Extra space between characters. [pixels]
            "Angle",  # The origin of the rotation is defined by the alignment. Can be a floating point number. [degrees]
            "BorderStyle",  # 1=Outline + drop shadow, 3=Opaque box
            "Outline",  # If BorderStyle is 1,  then this specifies the width of the outline around the text, in pixels. Values may be 0, 1, 2, 3 or 4.
            "Shadow",  # If BorderStyle is 1,  then this specifies the depth of the drop shadow behind the text, in pixels. Values may be 0, 1, 2, 3 or 4. Drop shadow is always used in addition to an outline - SSA will force an outline of 1 pixel if no outline width is given.
            "Alignment",  # This sets how text is "justified" within the Left/Right onscreen margins, and also the vertical placing. Values may be 1=Left, 2=Centered, 3=Right. Add 4 to the value for a "Toptitle". Add 8 to the value for a "Midtitle". eg. 5 = left-justified toptitle
            "MarginL",  # This defines the Left Margin in pixels. It is the distance from the left-hand edge of the screen.The three onscreen margins (MarginL, MarginR, MarginV) define areas in which the subtitle text will be displayed.
            "MarginR",  # This defines the Right Margin in pixels. It is the distance from the right-hand edge of the screen.
            "MarginV",  # MarginV. This defines the vertical Left Margin in pixels. For a subtitle, it is the distance from the bottom of the screen. For a toptitle, it is the distance from the top of the screen. For a midtitle, the value is ignored - the text will be vertically centred
            "Encoding",  #
        ]

        # Get font settings from styles
        karaoke_styles = self.styles.get("karaoke", {})
        font_path = karaoke_styles.get("font_path")

        style = Style()

        style.type = "Style"
        style.Name = self.styles["karaoke"]["ass_name"]
        style.Fontname = self.styles["karaoke"]["font"]
        style.Fontpath = font_path
        style.Fontsize = fontsize

        style.Alignment = ALIGN_TOP_CENTER

        # Convert color strings to tuples of integers
        def parse_color(color_str):
            return tuple(int(x.strip()) for x in color_str.split(","))

        style.PrimaryColour = parse_color(self.styles["karaoke"]["primary_color"])
        style.SecondaryColour = parse_color(self.styles["karaoke"]["secondary_color"])
        style.OutlineColour = parse_color(self.styles["karaoke"]["outline_color"])
        style.BackColour = parse_color(self.styles["karaoke"]["back_color"])

        # Convert boolean strings to integers (-1 for True, 0 for False)
        def parse_bool(value):
            return -1 if value else 0

        style.Bold = parse_bool(self.styles["karaoke"]["bold"])
        style.Italic = parse_bool(self.styles["karaoke"]["italic"])
        style.Underline = parse_bool(self.styles["karaoke"]["underline"])
        style.StrikeOut = parse_bool(self.styles["karaoke"]["strike_out"])

        # Convert numeric strings to appropriate types
        style.ScaleX = int(self.styles["karaoke"]["scale_x"])
        style.ScaleY = int(self.styles["karaoke"]["scale_y"])
        style.Spacing = int(self.styles["karaoke"]["spacing"])
        style.Angle = float(self.styles["karaoke"]["angle"])
        style.BorderStyle = int(self.styles["karaoke"]["border_style"])
        style.Outline = int(self.styles["karaoke"]["outline"])
        style.Shadow = int(self.styles["karaoke"]["shadow"])
        style.MarginL = int(self.styles["karaoke"]["margin_l"])
        style.MarginR = int(self.styles["karaoke"]["margin_r"])
        style.MarginV = int(self.styles["karaoke"]["margin_v"])
        style.Encoding = int(self.styles["karaoke"]["encoding"])

        a.add_style(style)

        a.events_format = ["Layer", "Style", "Start", "End", "MarginV", "Text"]
        return a, style

    def _create_styled_subtitles(
        self,
        screens: List[Union[SectionScreen, LyricsScreen]],
        resolution: Tuple[int, int],
        fontsize: int,
    ) -> ASS:
        """Create styled ASS subtitles from all screens."""
        ass_file, style = self._create_styled_ass_instance(resolution, fontsize)

        active_lines = []
        previous_instrumental_end = None

        for screen in screens:
            if isinstance(screen, SectionScreen):
                # Create section marker events (returns tuple of ([event], []))
                section_events, _ = screen.as_ass_events(style=style)
                for event in section_events:  # Now we're iterating over the list of events
                    ass_file.add(event)

                previous_instrumental_end = screen.end_time
                active_lines = []
                self.logger.debug(f"Found instrumental section ending at {screen.end_time:.2f}s")
                continue

            # Process screen and get its events
            self.logger.debug(f"Processing screen with instrumental_end={previous_instrumental_end}")
            # fmt: off
            events, active_lines = screen.as_ass_events(
                style=style, 
                previous_active_lines=active_lines,
                previous_instrumental_end=previous_instrumental_end
            )
            # fmt: on

            # Only reset instrumental end after we've processed the first post-instrumental screen
            if previous_instrumental_end is not None:
                self.logger.debug("Clearing instrumental end time after processing post-instrumental screen")
                previous_instrumental_end = None

            # Add all events to ASS file
            for event in events:
                ass_file.add(event)

        return ass_file
