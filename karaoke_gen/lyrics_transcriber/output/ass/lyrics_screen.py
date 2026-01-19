from dataclasses import dataclass
from typing import List, Optional, Tuple
import logging
from datetime import timedelta

from karaoke_gen.lyrics_transcriber.output.ass.style import Style
from karaoke_gen.lyrics_transcriber.output.ass.event import Event
from karaoke_gen.lyrics_transcriber.output.ass.lyrics_line import LyricsLine
from karaoke_gen.lyrics_transcriber.output.ass.config import ScreenConfig, LineTimingInfo, LineState


class PositionStrategy:
    """Handles calculation of vertical positions for lines."""

    def __init__(self, video_size: Tuple[int, int], config: ScreenConfig):
        self.video_size = video_size
        self.config = config

    def calculate_line_positions(self) -> List[int]:
        """Calculate vertical positions for all possible lines."""
        return PositionCalculator.calculate_line_positions(self.config)


class PositionCalculator:
    """Shared position calculation logic."""

    @staticmethod
    def calculate_first_line_position(config: ScreenConfig) -> int:
        """Calculate vertical position of first line."""
        total_height = config.max_visible_lines * config.line_height
        return config.top_padding + (config.video_height - total_height - config.top_padding) // 4

    @staticmethod
    def calculate_line_positions(config: ScreenConfig) -> List[int]:
        """Calculate vertical positions for all possible lines."""
        first_pos = PositionCalculator.calculate_first_line_position(config)
        return [first_pos + (i * config.line_height) for i in range(config.max_visible_lines)]

    @staticmethod
    def position_to_line_index(y_position: int, config: ScreenConfig) -> int:
        """Convert y-position to 0-based line index."""
        first_pos = PositionCalculator.calculate_first_line_position(config)
        return (y_position - first_pos) // config.line_height

    @staticmethod
    def line_index_to_position(index: int, config: ScreenConfig) -> int:
        """Convert 0-based line index to y-position."""
        first_pos = PositionCalculator.calculate_first_line_position(config)
        return first_pos + (index * config.line_height)


class TimingStrategy:
    """Handles calculation of line timings during screen transitions."""

    def __init__(self, config: ScreenConfig, logger: Optional[logging.Logger] = None):
        self.config = config
        self.logger = logger or logging.getLogger(__name__)

    def calculate_line_timings(
        self,
        current_lines: List[LyricsLine],
        previous_active_lines: Optional[List[Tuple[float, int, str]]] = None,
        previous_instrumental_end: Optional[float] = None,
    ) -> List[LineTimingInfo]:
        """Calculate timing information for each line in the screen."""
        previous_active_lines = previous_active_lines or []

        # If no previous lines, determine appropriate start time
        if not previous_active_lines:
            if previous_instrumental_end is not None:
                # For post-instrumental, start right after the instrumental
                return self._calculate_simultaneous_timings(current_lines, fade_in_start=previous_instrumental_end)
            else:
                # For first screen, start at 0
                return self._calculate_simultaneous_timings(current_lines, fade_in_start=0.0)

        # Create a map of position -> clear time from previous lines
        position_clear_times = {}
        for end_time, y_position, text in previous_active_lines:
            # Clear time is now just when the fade out ends
            clear_time = end_time + (self.config.fade_out_ms / 1000)
            position_clear_times[y_position] = clear_time

            # Add buffer time for the position above the active line
            # Use the end time without fade for the line above to give more reading time
            line_index = PositionCalculator.position_to_line_index(y_position, self.config)
            # fmt: off
            if line_index > 0:  # If not the top line
                position_above = PositionCalculator.line_index_to_position(line_index - 1, self.config)
                position_clear_times[position_above] = max(
                    position_clear_times.get(position_above, 0),
                    end_time  # Use end time without fade for position above
                )
            # fmt: on

            self.logger.debug(f"    Position {line_index + 1} occupied by '{text}' until {clear_time:.2f}s")

        # Calculate timings for each line
        timings = []
        positions = PositionCalculator.calculate_line_positions(self.config)
        for i, (line, position) in enumerate(zip(current_lines, positions)):
            # Fade in as soon as the position is available
            fade_in_time = position_clear_times.get(position, 0)

            # Calculate remaining timing information
            end_time = line.segment.end_time + self.config.post_roll_time
            fade_out_time = end_time + (self.config.fade_out_ms / 1000)
            # Clear time is now just the fade out time
            clear_time = fade_out_time

            # fmt: off
            timing = LineTimingInfo(
                fade_in_time=fade_in_time,
                end_time=end_time,
                fade_out_time=fade_out_time,
                clear_time=clear_time
            )
            # fmt: on
            timings.append(timing)

            line_index = PositionCalculator.position_to_line_index(position, self.config)
            self.logger.debug(
                f"    Line {line_index + 1}: '{line.segment.text}' "
                f"fades in at {fade_in_time:.2f}s "
                f"(position available at {position_clear_times.get(position, 0):.2f}s)"
            )

        return timings

    def _calculate_simultaneous_timings(self, lines: List[LyricsLine], fade_in_start: float) -> List[LineTimingInfo]:
        """Calculate timings for screens where all lines appear together."""
        return [
            LineTimingInfo(
                fade_in_time=fade_in_start,
                end_time=line.segment.end_time + self.config.post_roll_time,
                fade_out_time=line.segment.end_time + self.config.post_roll_time + (self.config.fade_out_ms / 1000),
                clear_time=line.segment.end_time + self.config.post_roll_time + (self.config.fade_out_ms / 1000),
            )
            for line in lines
        ]


@dataclass
class LyricsScreen:
    """Represents a screen of lyrics (multiple lines)."""

    video_size: Tuple[int, int]
    line_height: int
    lines: List[LyricsLine] = None  # Make lines optional
    logger: Optional[logging.Logger] = None
    post_instrumental: bool = False
    config: Optional[ScreenConfig] = None

    def __post_init__(self):
        if self.logger is None:
            self.logger = logging.getLogger(__name__)
        if self.config is None:
            self.config = ScreenConfig(line_height=self.line_height)
        else:
            # Ensure line_height is consistent
            self.config.line_height = self.line_height

        # Initialize empty lines list if None
        if self.lines is None:
            self.lines = []

        # Update video height in config
        self.config.video_width = self.video_size[0]
        self.config.video_height = self.video_size[1]

        # Initialize strategies
        self.position_strategy = PositionStrategy(self.video_size, self.config)
        self.timing_strategy = TimingStrategy(self.config, self.logger)

    def as_ass_events(
        self,
        style: Style,
        previous_active_lines: Optional[List[Tuple[float, int, str]]] = None,
        previous_instrumental_end: Optional[float] = None,
    ) -> Tuple[List[Event], List[Tuple[float, int, str]]]:
        """Convert screen to ASS events. Returns (events, active_lines)."""
        events = []
        active_lines = []
        previous_active_lines = previous_active_lines or []

        # Find the latest end time from previous lines
        previous_end_time = None
        if previous_active_lines:
            previous_end_time = max(end_time for end_time, _, _ in previous_active_lines)

        # Log active lines from previous screen
        if previous_active_lines:
            self.logger.debug("  Active lines from previous screen:")
            for end, pos, text in previous_active_lines:
                # Convert y-position back to line index (0-based)
                line_index = PositionCalculator.position_to_line_index(pos, self.config)
                clear_time = end + (self.config.fade_out_ms / 1000)
                self.logger.debug(
                    f"    Line {line_index + 1}: '{text}' "
                    f"(ends {end:.2f}s, fade out {end + (self.config.fade_out_ms / 1000):.2f}s, clear {clear_time:.2f}s)"
                )

        # Calculate positions and timings
        positions = self.position_strategy.calculate_line_positions()
        # fmt: off
        timings = self.timing_strategy.calculate_line_timings(
            current_lines=self.lines,
            previous_active_lines=previous_active_lines,
            previous_instrumental_end=previous_instrumental_end
        )
        # fmt: on

        # Create line states and events
        for i, (line, timing) in enumerate(zip(self.lines, timings)):
            y_position = positions[i]

            # Create line state
            line_state = LineState(text=line.segment.text, timing=timing, y_position=y_position)

            # Create ASS events with previous end time info
            # fmt: off
            line_events = line.create_ass_events(
                state=line_state, 
                style=style, 
                config=self.config,
                previous_end_time=previous_end_time
            )
            # fmt: on
            events.extend(line_events)

            # Track this line's end time for the next screen
            previous_end_time = timing.end_time
            active_lines.append((timing.end_time, y_position, line.segment.text))

            # Log line placement with index
            self.logger.debug(f"    Line {i + 1}: '{line.segment.text}'")

        return events, active_lines

    @property
    def start_ts(self) -> timedelta:
        """Get screen start timestamp."""
        return timedelta(seconds=min(line.segment.start_time for line in self.lines))

    @property
    def end_ts(self) -> timedelta:
        """Get screen end timestamp."""
        latest_ts = max(line.segment.end_time for line in self.lines)
        return timedelta(seconds=latest_ts + self.config.post_roll_time)

    def __str__(self):
        return "\n".join([f"{self.start_ts} - {self.end_ts}:", *[f"\t{line}" for line in self.lines]])
