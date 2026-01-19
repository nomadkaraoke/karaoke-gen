from dataclasses import dataclass
from typing import List, Optional, Literal, Tuple
import logging
from datetime import timedelta

from karaoke_gen.lyrics_transcriber.output.ass.style import Style
from karaoke_gen.lyrics_transcriber.output.ass.event import Event
from karaoke_gen.lyrics_transcriber.output.ass.lyrics_screen import ScreenConfig


@dataclass
class SectionScreen:
    """Special screen for instrumental sections."""

    section_type: Literal["INTRO", "OUTRO", "INSTRUMENTAL"]
    start_time: float
    end_time: float
    video_size: Tuple[int, int]
    line_height: int
    logger: Optional[logging.Logger] = None
    config: Optional[ScreenConfig] = None

    def __post_init__(self):
        self._initialize_logger_and_config()
        self._calculate_duration()
        self._adjust_timing()
        self._create_text()

    def _initialize_logger_and_config(self):
        """Initialize logger and config with defaults if not provided."""
        if self.logger is None:
            self.logger = logging.getLogger(__name__)
        if self.config is None:
            self.config = ScreenConfig(line_height=self.line_height)
        
        self.config.video_width = self.video_size[0]
        self.config.video_height = self.video_size[1]

    def _calculate_duration(self):
        """Calculate duration before any timing adjustments."""
        self.original_duration = round(self.end_time - self.start_time)

    def _adjust_timing(self):
        """Apply timing adjustments based on section type."""
        if self.section_type == "INTRO":
            self.start_time = 1.0  # Start after 1 second
            self.end_time = self.end_time - 5.0  # End 5 seconds before next section

    def _create_text(self):
        """Create the section text with duration."""
        self.text = f"♪ {self.section_type} ({self.original_duration} seconds) ♪"

    def _calculate_start_time(self, previous_active_lines: Optional[List[Tuple[float, int, str]]] = None) -> float:
        """Calculate start time accounting for previous lines."""
        start_time = self.start_time
        if previous_active_lines:
            latest_end = max(end + (self.config.fade_out_ms / 1000) for end, _, _ in previous_active_lines)
            start_time = max(start_time, latest_end)
        return start_time

    def _calculate_vertical_position(self) -> int:
        """Calculate vertical position for centered text."""
        return (self.video_size[1] - self.line_height) // 2

    def _create_event(self, style: Style, start_time: float) -> Event:
        """Create an ASS event with proper formatting."""
        event = Event()
        event.type = "Dialogue"
        event.Layer = 0
        event.Style = style
        event.Start = start_time
        event.End = self.end_time
        event.MarginV = self._calculate_vertical_position()

        # Add karaoke timing for the entire duration
        duration = int((self.end_time - start_time) * 100)  # Convert to centiseconds
        event.Text = f"{{\\fad({self.config.fade_in_ms},{self.config.fade_out_ms})}}" f"{{\\an8}}{{\\K{duration}}}{self.text}"
        return event

    def as_ass_events(
        self,
        style: Style,
        previous_active_lines: Optional[List[Tuple[float, int, str]]] = None,
        previous_instrumental_end: Optional[float] = None,
    ) -> Tuple[List[Event], List[Tuple[float, int, str]]]:
        """Create ASS events for section markers with karaoke highlighting."""
        self.logger.debug(f"Creating section marker event for {self.section_type}")

        start_time = self._calculate_start_time(previous_active_lines)
        event = self._create_event(style, start_time)

        self.logger.debug(f"Created section event: {event.Text} ({event.Start}s - {event.End}s)")
        return [event], []  # No active lines to track for sections

    @property
    def start_ts(self) -> timedelta:
        """Get start timestamp."""
        return timedelta(seconds=self.start_time)

    @property
    def end_ts(self) -> timedelta:
        """Get end timestamp."""
        return timedelta(seconds=self.end_time)

    def __str__(self):
        return f"{self.section_type} {self.start_ts} - {self.end_ts}"
