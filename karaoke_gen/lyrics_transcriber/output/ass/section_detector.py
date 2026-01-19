from typing import List, Optional, Tuple
import logging

from karaoke_gen.lyrics_transcriber.types import LyricsSegment
from karaoke_gen.lyrics_transcriber.output.ass import LyricsScreen, SectionScreen


class SectionDetector:
    """Detects and creates section screens between lyrics."""

    def __init__(self, gap_threshold: float = 10.0, logger: Optional[logging.Logger] = None):
        self.gap_threshold = gap_threshold
        self.logger = logger or logging.getLogger(__name__)
        self.intro_padding = 0.0  # No padding for intro
        self.outro_padding = 5.0  # End 5s before song ends
        self.instrumental_start_padding = 1.0  # Start 1s after previous segment
        self.instrumental_end_padding = 5.0  # End 5s before next segment

    def process_segments(
        self, segments: List[LyricsSegment], video_size: Tuple[int, int], line_height: int, song_duration: float
    ) -> List[LyricsScreen]:
        """Process segments and insert section screens where appropriate.

        Args:
            segments: List of lyric segments
            video_size: Tuple of (width, height) for video resolution
            line_height: Height of each line in pixels
            song_duration: Total duration of the song in seconds
        """
        if not segments:
            return []

        screens: List[LyricsScreen] = []

        # Check for intro
        if segments[0].start_time >= self.gap_threshold:
            self.logger.debug(f"Detected intro section: 0.0 - {segments[0].start_time:.2f}s")
            screens.append(
                SectionScreen(
                    section_type="INTRO",
                    start_time=0.0,
                    end_time=segments[0].start_time - self.intro_padding,
                    video_size=video_size,
                    line_height=line_height,
                    logger=self.logger,
                )
            )

        # Check for instrumental sections between segments
        for i in range(len(segments) - 1):
            gap = segments[i + 1].start_time - segments[i].end_time
            if gap >= self.gap_threshold:
                instrumental_start = segments[i].end_time + self.instrumental_start_padding
                instrumental_end = segments[i + 1].start_time - self.instrumental_end_padding

                # Only create section if there's meaningful duration after padding
                if instrumental_end > instrumental_start:
                    self.logger.debug(f"Detected instrumental section: {instrumental_start:.2f} - {instrumental_end:.2f}s")
                    screens.append(
                        SectionScreen(
                            section_type="INSTRUMENTAL",
                            start_time=instrumental_start,
                            end_time=instrumental_end,
                            video_size=video_size,
                            line_height=line_height,
                            logger=self.logger,
                        )
                    )

        # Check for outro
        if segments:  # Only add outro if there are segments
            last_segment = segments[-1]
            outro_duration = song_duration - last_segment.end_time
            if outro_duration >= self.gap_threshold:
                outro_start = last_segment.end_time + self.instrumental_start_padding
                outro_end = song_duration - self.outro_padding  # End 5s before song ends
                self.logger.debug(f"Detected outro section: {outro_start:.2f}s - {outro_end:.2f}s")
                screens.append(
                    SectionScreen(
                        section_type="OUTRO",
                        start_time=outro_start,
                        end_time=outro_end,
                        video_size=video_size,
                        line_height=line_height,
                        logger=self.logger,
                    )
                )

        return screens
