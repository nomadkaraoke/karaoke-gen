from dataclasses import dataclass
from typing import Optional, Tuple, List
import logging
from datetime import timedelta
from PIL import Image, ImageDraw, ImageFont
import os

from karaoke_gen.lyrics_transcriber.types import LyricsSegment
from karaoke_gen.lyrics_transcriber.output.ass.event import Event
from karaoke_gen.lyrics_transcriber.output.ass.style import Style
from karaoke_gen.lyrics_transcriber.output.ass.config import LineState, ScreenConfig


@dataclass
class LyricsLine:
    """Represents a single line of lyrics with timing and karaoke information."""

    segment: LyricsSegment
    screen_config: ScreenConfig
    logger: Optional[logging.Logger] = None
    previous_end_time: Optional[float] = None

    def __post_init__(self):
        """Ensure logger is initialized"""
        if self.logger is None:
            self.logger = logging.getLogger(__name__)

    def _get_font(self, style: Style) -> ImageFont.FreeTypeFont:
        """Get the font for text measurements."""
        # ASS renders fonts about 70% of their actual size
        ASS_FONT_SCALE = 0.70

        # Scale down the font size to match ASS rendering
        adjusted_size = int(style.Fontsize * ASS_FONT_SCALE)
        self.logger.debug(f"Adjusting font size from {style.Fontsize} to {adjusted_size} to match ASS rendering")

        try:
            # Use the Fontpath property from Style class
            if style.Fontpath and os.path.exists(style.Fontpath):
                return ImageFont.truetype(style.Fontpath, size=adjusted_size)
            self.logger.warning(f"Could not load font {style.Fontpath}, using default")
            return ImageFont.load_default()
        except (OSError, AttributeError) as e:
            self.logger.warning(f"Font error ({e}), using default")
            return ImageFont.load_default()

    def _get_text_dimensions(self, text: str, font: ImageFont.FreeTypeFont) -> Tuple[int, int]:
        """Get the pixel dimensions of rendered text."""
        # Create an image the same size as the video frame
        img = Image.new("RGB", (self.screen_config.video_width, self.screen_config.video_height), color="black")
        draw = ImageDraw.Draw(img)

        # Get the bounding box
        bbox = draw.textbbox((0, 0), text, font=font)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]

        self.logger.debug(f"Text dimensions for '{text}': width={width}px, height={height}px")
        self.logger.debug(f"Video dimensions: {self.screen_config.video_width}x{self.screen_config.video_height}")
        return width, height

    # fmt: off
    def _create_lead_in_text(self, state: LineState) -> Tuple[str, bool]:
        """Create lead-in indicator text if needed.
        
        Returns:
            Tuple of (text, has_lead_in)
        """
        has_lead_in = (self.previous_end_time is None or 
                      self.segment.start_time - self.previous_end_time >= self.screen_config.lead_in_gap_threshold)
        
        if not has_lead_in:
            return "", False
            
        # Add a hyphen with karaoke timing for the last 2 seconds before the line
        lead_in_start = max(state.timing.fade_in_time, self.segment.start_time - 2.0)
        gap_before_highlight = int((lead_in_start - state.timing.fade_in_time) * 100)
        highlight_duration = int((self.segment.start_time - lead_in_start) * 100)
        
        text = ""
        # Add initial gap if needed
        if gap_before_highlight > 0:
            text += f"{{\\k{gap_before_highlight}}}"
        # Add the hyphen with highlight
        text += f"{{\\kf{highlight_duration}}}→ "
        
        return text, True

    def _create_lead_in_event(self, state: LineState, style: Style, video_width: int, config: ScreenConfig) -> Optional[Event]:
        """Create a separate event for the lead-in indicator if needed."""
        # Check if lead-in is enabled
        if not config.lead_in_enabled:
            return None
            
        # Check if there's a sufficient gap to show lead-in
        if not (self.previous_end_time is None or 
                self.segment.start_time - self.previous_end_time >= config.lead_in_gap_threshold):
            return None
            
        self.logger.debug(f"Creating lead-in indicator for line: '{self.segment.text}'")
        
        # Calculate all timing points
        line_start = self.segment.start_time
        appear_time = line_start - 3.0  # Start 3 seconds before line
        fade_in_end = appear_time + 0.8  # 800ms fade in
        fade_out_start = line_start - 0.3  # Start fade 300ms before reaching final position
        fade_out_end = line_start + 0.2  # Complete fade 200ms after line starts (500ms total fade)
        
        self.logger.debug(f"Timing calculations:")
        self.logger.debug(f"  Line starts at: {line_start:.2f}s")
        self.logger.debug(f"  Rectangle appears at: {appear_time:.2f}s")
        self.logger.debug(f"  Fade in completes at: {fade_in_end:.2f}s")
        self.logger.debug(f"  Fade out starts at: {fade_out_start:.2f}s")
        self.logger.debug(f"  Rectangle reaches final position at: {line_start:.2f}s")
        self.logger.debug(f"  Rectangle fully faded out at: {fade_out_end:.2f}s")
        
        # Calculate dimensions and positions using configurable percentages
        font = self._get_font(style)
        # Apply case transformation to match the actual rendered text
        main_text = self._apply_case_transform(self.segment.text)
        main_width, main_height = self._get_text_dimensions(main_text, font)
        rect_width = int(self.screen_config.video_width * (config.lead_in_width_percent / 100))
        rect_height = int(self.screen_config.video_height * (config.lead_in_height_percent / 100))
        # Calculate where the left edge of the centered text will be
        text_left = self.screen_config.video_width//2 - main_width//2
        # Apply horizontal offset if configured
        horizontal_offset = int(self.screen_config.video_width * (config.lead_in_horiz_offset_percent / 100))
        final_x_position = text_left + horizontal_offset
        # Apply vertical offset if configured
        vertical_offset = int(self.screen_config.video_height * (config.lead_in_vert_offset_percent / 100))
        final_y_position = state.y_position + main_height + vertical_offset
        
        self.logger.debug(f"Position calculations:")
        self.logger.debug(f"  Video dimensions: {self.screen_config.video_width}x{self.screen_config.video_height}")
        self.logger.debug(f"  Original text: '{self.segment.text}'")
        self.logger.debug(f"  Transformed text: '{main_text}'")
        self.logger.debug(f"  Main text width: {main_width}px")
        self.logger.debug(f"  Main text height: {main_height}px")
        self.logger.debug(f"  Rectangle dimensions: {rect_width}x{rect_height}px (from {config.lead_in_width_percent}% x {config.lead_in_height_percent}%)")
        self.logger.debug(f"  Text left edge: {text_left}px")
        self.logger.debug(f"  Horizontal offset: {horizontal_offset}px ({config.lead_in_horiz_offset_percent}% of screen width)")
        self.logger.debug(f"  Final X position: {final_x_position}px")
        self.logger.debug(f"  Vertical offset: {vertical_offset}px ({config.lead_in_vert_offset_percent}% of screen height)")
        self.logger.debug(f"  Final Y position: {final_y_position}px")
        self.logger.debug(f"  Vertical position: {state.y_position}px")
        
        # Create main indicator event
        main_event = Event()
        main_event.type = "Dialogue"
        main_event.Layer = 0
        main_event.Style = style
        main_event.Start = appear_time
        main_event.End = fade_out_end
        
        # Calculate movement duration in milliseconds
        move_duration = int((line_start - appear_time) * 1000)
        
        # Build the indicator rectangle text with configurable styling
        main_text = (
            f"{{\\an8}}"  # center-bottom alignment
            f"{{\\move(0,{final_y_position},{final_x_position},{final_y_position},0,{move_duration})}}"  # Move until line start
            f"{{\\c{config.get_lead_in_color_ass_format()}}}"  # Configurable lead-in color in ASS format
            f"{{\\alpha{config.get_lead_in_opacity_ass_format()}}}"  # Configurable opacity
            f"{{\\fad(800,500)}}"  # 800ms fade in, 500ms fade out
        )
        
        # Add outline if thickness > 0
        if config.lead_in_outline_thickness > 0:
            main_text += (
                f"{{\\3c{config.get_lead_in_outline_color_ass_format()}}}"  # Outline color
                f"{{\\bord{config.lead_in_outline_thickness}}}"  # Outline thickness
            )
        else:
            main_text += f"{{\\bord0}}"  # No outline
        
        # Add the rectangle shape
        main_text += f"{{\\p1}}m {-rect_width} {-rect_height} l 0 {-rect_height} 0 0 {-rect_width} 0{{\\p0}}"  # Draw up from bottom
        
        main_event.Text = main_text
        
        return [main_event]

    def create_ass_events(
        self, 
        state: LineState, 
        style: Style, 
        config: ScreenConfig,
        previous_end_time: Optional[float] = None
    ) -> List[Event]:
        """Create ASS events for this line. Returns [main_event] or [lead_in_event, main_event]."""
        self.previous_end_time = previous_end_time
        events = []
        
        # Create lead-in event if needed
        lead_in_event = self._create_lead_in_event(state, style, config.video_width, config)
        if lead_in_event:
            events.extend(lead_in_event)
        
        # Create main lyrics event
        main_event = Event()
        main_event.type = "Dialogue"
        main_event.Layer = 0
        main_event.Style = style
        main_event.Start = state.timing.fade_in_time
        main_event.End = state.timing.end_time

        # Use absolute positioning
        x_pos = config.video_width // 2  # Center horizontally

        # Main lyrics text with positioning and fade
        text = (
            f"{{\\an8}}{{\\pos({x_pos},{state.y_position})}}"
            f"{{\\fad({config.fade_in_ms},{config.fade_out_ms})}}"
        )

        # Add the main lyrics text with karaoke timing
        text += self._create_ass_text(timedelta(seconds=state.timing.fade_in_time))

        main_event.Text = text
        events.append(main_event)

        return events

    def _apply_case_transform(self, text: str) -> str:
        """Apply case transformation to text based on screen config setting."""
        transform = getattr(self.screen_config, 'text_case_transform', 'none')
        
        if transform == "uppercase":
            return text.upper()
        elif transform == "lowercase":
            return text.lower()
        elif transform == "propercase":
            return text.title()
        else:  # "none" or any other value
            return text

    def _create_ass_text(self, start_ts: timedelta) -> str:
        """Create the ASS text with karaoke timing tags."""
        # Initial delay before first word
        first_word_time = self.segment.start_time
        
        # Add initial delay for regular lines
        start_time = max(0, (first_word_time - start_ts.total_seconds()) * 100)
        text = r"{\k" + str(int(round(start_time))) + r"}"

        prev_end_time = first_word_time

        for word in self.segment.words:
            # Add gap between words if needed
            gap = word.start_time - prev_end_time
            if gap > 0.1:  # Only add gap if significant
                text += r"{\k" + str(int(round(gap * 100))) + r"}"

            # Add the word with its duration
            duration = int(round((word.end_time - word.start_time) * 100))
            # Apply case transformation to the word text
            transformed_text = self._apply_case_transform(word.text)
            text += r"{\kf" + str(duration) + r"}" + transformed_text + " "

            prev_end_time = word.end_time  # Track the actual end time of the word

        return text.rstrip()

    def __str__(self):
        return f"{{{self.segment.text}}}"
