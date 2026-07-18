import logging
import re
import unicodedata
from typing import List, Optional

from karaoke_gen.lyrics_transcriber.types import LyricsSegment, Word
from karaoke_gen.lyrics_transcriber.utils.word_utils import WordUtils


# East-Asian Wide ('W') and Fullwidth ('F') glyphs are far wider than a Latin
# character in the karaoke render. ``max_line_length`` (40) was calibrated for Latin
# text — measuring the real fonts confirms ~41 Latin chars fill the 4K frame width at
# the effective render size (libass renders at ~0.70x the nominal Fontsize; see
# lyrics_line.ASS_FONT_SCALE). So a raw character count badly under-estimates CJK
# width: a line well under 40 *characters* is far wider than the frame, and libass
# smart-wraps it onto a second physical row that overlaps the slot below — the lyrics
# overlap this metric fixes.
#
# Calibration: a CJK glyph plus the space the renderer emits after every word token
# (lyrics_line.py `transformed_text + " "`) measures ~2.5x the average width of a
# Latin character (Hiragino/Noto vs Montserrat-Bold, consistent at 175px and 250px).
# The CJK glyph advance itself is ~1em for any CJK font (full-width by design), so the
# only per-font variance is the space glyph; WIDE_RATIO = 2.7 adds ~15% headroom over
# the measured 2.5 to stay clear of the frame edge with the production Noto Sans CJK
# Bold font (~14 glyphs/line, ~3.2k of 3840px). Pure-Latin text — where display width
# equals ``len`` — is unchanged.
WIDE_RATIO = 2.7


def _char_width(ch: str) -> float:
    """Width of a single character in half-width units (Wide/Fullwidth → WIDE_RATIO)."""
    return WIDE_RATIO if unicodedata.east_asian_width(ch) in ("W", "F") else 1.0


def display_width(text: str) -> float:
    """Approximate rendered width of ``text`` in half-width character units.

    East-Asian Wide ('W') and Fullwidth ('F') code points count as ``WIDE_RATIO``
    units; every other character counts as 1. For pure ASCII/Latin/European text this
    equals ``len(text)`` exactly, so text without wide glyphs is treated identically to
    before this metric existed.
    """
    return sum(_char_width(ch) for ch in text)


class SegmentResizer:
    """Handles resizing of lyrics segments to ensure proper line lengths and natural breaks.

    This class processes lyrics segments and splits them into smaller segments when they exceed
    a maximum line length. It attempts to split at natural break points like sentence endings,
    commas, or conjunctions to maintain readability.

    Example:
        resizer = SegmentResizer(max_line_length=36)
        segments = [
            LyricsSegment(
                text="This is a very long sentence that needs to be split into multiple lines for better readability",
                words=[...],  # List of Word objects with timing information
                start_time=0.0,
                end_time=5.0
            )
        ]
        resized = resizer.resize_segments(segments)
        # Results in:
        # [
        #     LyricsSegment(text="This is a very long sentence", ...),
        #     LyricsSegment(text="that needs to be split", ...),
        #     LyricsSegment(text="into multiple lines", ...),
        #     LyricsSegment(text="for better readability", ...)
        # ]
    """

    def __init__(self, max_line_length: int = 36, logger: Optional[logging.Logger] = None):
        """Initialize the SegmentResizer.

        Args:
            max_line_length: Maximum allowed length for a single line of text
            logger: Optional logger for debugging information
        """
        self.max_line_length = max_line_length
        self.logger = logger or logging.getLogger(__name__)

    def _display_width(self, text: str) -> float:
        """Visual width of ``text`` in half-width units (see module ``display_width``)."""
        return display_width(text)

    @staticmethod
    def _contains_wide(text: str) -> bool:
        """True if ``text`` has any East-Asian Wide/Fullwidth (CJK) glyph."""
        return any(unicodedata.east_asian_width(ch) in ("W", "F") for ch in text)

    def resize_segments(self, segments: List[LyricsSegment]) -> List[LyricsSegment]:
        """Main entry point for resizing segments.

        Takes a list of potentially long segments and splits them into smaller ones
        while preserving word timing information.

        Example:
            Input segment: "Hello world, this is a test. And here's another sentence."
            Output segments: [
                "Hello world, this is a test.",
                "And here's another sentence."
            ]

        Args:
            segments: List of LyricsSegment objects to process

        Returns:
            List of resized LyricsSegment objects
        """
        self._log_input_segments(segments)

        # Guarantee valid, in-bounds word timing before doing anything else.
        # Transcription occasionally emits words with missing/zero/negative
        # timestamps; when a segment is later split, the derived sub-segment
        # inherits words[0].start_time, so a bogus value (e.g. 0.0) becomes the
        # sub-segment's start_time and corrupts downstream screen ordering.
        segments = [self._sanitize_segment_timings(segment) for segment in segments]

        resized_segments: List[LyricsSegment] = []

        for segment_idx, segment in enumerate(segments):
            cleaned_segment = self._create_cleaned_segment(segment)

            # Only split if the segment is wider than max_line_length (visual width,
            # so full-width CJK glyphs are counted at ~2x a Latin character)
            if self._display_width(cleaned_segment.text) <= self.max_line_length:
                resized_segments.append(cleaned_segment)
                continue

            # Process oversized segments
            resized_segments.extend(self._split_oversized_segment(segment_idx, segment))

        self._log_output_segments(resized_segments)
        return resized_segments

    def _clean_text(self, text: str) -> str:
        """Clean text by removing newlines and extra whitespace.

        Example:
            Input: "Hello\n  World  \n!"
            Output: "Hello World !"

        Args:
            text: String to clean

        Returns:
            Cleaned string with normalized whitespace
        """
        return " ".join(text.replace("\n", " ").split())

    def _create_cleaned_segment(self, segment: LyricsSegment) -> LyricsSegment:
        """Create a new segment with cleaned text while preserving timing info.

        Example:
            Input: LyricsSegment(text="Hello\n  World\n", words=[...])
            Output: LyricsSegment(text="Hello World", words=[...])
        """
        cleaned_text = self._clean_text(segment.text)
        return LyricsSegment(
            id=segment.id,  # Preserve the original segment ID
            text=cleaned_text,
            words=segment.words,
            start_time=segment.start_time,
            end_time=segment.end_time,
            singer=segment.singer,
        )

    def _create_cleaned_word(self, word: Word) -> Word:
        """Create a new word with cleaned text."""
        cleaned_text = self._clean_text(word.text)
        return Word(
            id=word.id,  # Preserve the original word ID
            text=cleaned_text,
            start_time=word.start_time,
            end_time=word.end_time,
            confidence=word.confidence if hasattr(word, "confidence") else None,
            created_during_correction=getattr(word, "created_during_correction", False),
            singer=word.singer,
        )

    def _sanitize_segment_timings(self, segment: LyricsSegment) -> LyricsSegment:
        """Return a copy of ``segment`` whose word timings are valid and in-bounds.

        A word is considered invalid if its ``start_time`` falls outside the
        segment's ``[start_time, end_time]`` window, or if its ``end_time`` is
        missing/before its ``start_time``/after the segment end. Only invalid
        timings are corrected — already-valid words are left untouched — so the
        karaoke highlight sweep is preserved wherever the source data is good.

        A corrected start clamps to the later of the segment start and the
        previous word's end (keeping words non-decreasing); a corrected end
        clamps to ``[start, segment_end]``.
        """
        seg_start = segment.start_time
        seg_end = segment.end_time if segment.end_time >= seg_start else seg_start

        sanitized_words: List[Word] = []
        prev_end = seg_start
        changed = False
        for word in segment.words:
            start, end = word.start_time, word.end_time

            if start is None or start < seg_start or start > seg_end:
                start = min(max(prev_end, seg_start), seg_end)
                changed = True

            if end is None or end < start or end > seg_end:
                end = min(max(start, end if end is not None else start), seg_end)
                if end < start:
                    end = start
                changed = True

            if start != word.start_time or end != word.end_time:
                self.logger.debug(
                    f"Sanitized word '{word.text}' timing {word.start_time}-{word.end_time} "
                    f"-> {start}-{end} (segment {seg_start:.2f}-{seg_end:.2f})"
                )
                sanitized_words.append(
                    Word(
                        id=word.id,
                        text=word.text,
                        start_time=start,
                        end_time=end,
                        confidence=word.confidence if hasattr(word, "confidence") else None,
                        created_during_correction=getattr(word, "created_during_correction", False),
                        singer=word.singer,
                    )
                )
            else:
                sanitized_words.append(word)
            prev_end = end

        if not changed:
            return segment

        return LyricsSegment(
            id=segment.id,
            text=segment.text,
            words=sanitized_words,
            start_time=segment.start_time,
            end_time=segment.end_time,
            singer=segment.singer,
        )

    def _split_oversized_segment(self, segment_idx: int, segment: LyricsSegment) -> List[LyricsSegment]:
        """Split an oversized segment into multiple segments at natural break points.

        Example:
            Input: "This is a long sentence. Here's another one."
            Output: [
                LyricsSegment(text="This is a long sentence.", ...),
                LyricsSegment(text="Here's another one.", ...)
            ]
        """
        segment_text = self._clean_text(segment.text)

        self.logger.info(f"Processing oversized segment {segment_idx}: '{segment_text}'")

        # Wide-script (CJK) text has no reliable whitespace break points, and the
        # text-position splitter re-matches Word tokens by substring — a width-based
        # cut can land inside a multi-character token (e.g. 立ち向かう), desyncing the
        # matcher. Pack directly by Word token instead: boundary-safe by construction
        # and display-width aware (each token measured with the inter-token space the
        # renderer emits). Latin/European text (no wide glyphs) keeps the original
        # natural-break text splitter, so its output is unchanged.
        if segment.words and self._contains_wide(segment_text):
            packed = self._pack_words_into_lines(segment.words, singer=segment.singer)
            self.logger.debug(f"CJK segment packed into {len(packed)} lines by word token")
            return packed

        split_lines = self._process_segment_text(segment_text)
        self.logger.debug(f"Split into {len(split_lines)} lines: {split_lines}")

        return self._create_segments_from_lines(segment_text, split_lines, segment.words, singer=segment.singer)

    def _create_segments_from_lines(
        self,
        segment_text: str,
        split_lines: List[str],
        words: List[Word],
        singer: Optional[int] = None,
    ) -> List[LyricsSegment]:
        """Create segments from split lines while preserving word timing.

        Matches words to their corresponding lines based on text position and
        creates new segments with the correct timing information.

        Example:
            segment_text: "Hello world, how are you"
            split_lines: ["Hello world,", "how are you"]
            words: [Word("Hello", 0.0, 1.0), Word("world", 1.0, 2.0), ...]

        Returns segments with words properly assigned to each line.
        """
        segments: List[LyricsSegment] = []
        words_to_process = words.copy()
        current_pos = 0

        for line in split_lines:
            line_words = []
            line_text = line.strip()
            remaining_line = line_text

            # Keep processing words until we've found all words for this line
            while words_to_process and remaining_line:
                word = words_to_process[0]
                word_clean = self._clean_text(word.text)

                # Check if the cleaned word appears in the remaining line text
                if word_clean in remaining_line:
                    word_pos = remaining_line.find(word_clean)
                    if word_pos != -1:
                        line_words.append(words_to_process.pop(0))
                        # Remove the word and any following spaces from remaining line
                        remaining_line = remaining_line[word_pos + len(word_clean) :].strip()
                        continue

                # If we can't find the word in the remaining line, we're done with this line
                break

            if line_words:
                segments.append(self._create_segment_from_words(line, line_words, singer=singer))
                current_pos += len(line) + 1  # +1 for the space between lines

        # If we have any remaining words (word-to-line matching desynced — e.g. a
        # punctuation mark attached to a word token straddled a split boundary),
        # pack them into lines that still respect max_line_length instead of one
        # unbounded segment. An over-length line wraps at render time and overlaps
        # the slot below it, so the length guarantee must hold here too.
        if words_to_process:
            segments.extend(self._pack_words_into_lines(words_to_process, singer=singer))

        return segments

    def _pack_words_into_lines(self, words: List[Word], singer: Optional[int] = None) -> List[LyricsSegment]:
        """Greedily group words into segments no longer than max_line_length.

        Works directly on word tokens (not re-matched text) so it cannot desync.
        A single word longer than the limit is emitted on its own line.
        """
        segments: List[LyricsSegment] = []
        current_words: List[Word] = []
        current_text = ""

        for word in words:
            word_text = self._clean_text(word.text)
            candidate = f"{current_text} {word_text}".strip() if current_text else word_text
            if current_words and self._display_width(candidate) > self.max_line_length:
                segments.append(self._create_segment_from_words(current_text, current_words, singer=singer))
                current_words = [word]
                current_text = word_text
            else:
                current_words.append(word)
                current_text = candidate

        if current_words:
            segments.append(self._create_segment_from_words(current_text, current_words, singer=singer))

        return segments

    def _create_line_segment(
        self, line_idx: int, line: str, segment_text: str, available_words: List[Word], current_pos: int
    ) -> Optional[LyricsSegment]:
        """Create a single segment from a line of text."""
        line_pos = segment_text.find(line, current_pos)
        if line_pos == -1:
            self.logger.error(f"Failed to find line '{line}' in segment text '{segment_text}' " f"starting from position {current_pos}")
            return None

        line_words = self._find_words_for_line(line, line_pos, len(line), segment_text, available_words, current_pos)

        if line_words:
            return self._create_segment_from_words(line, line_words)
        else:
            self.logger.warning(f"No words found for line '{line}'")
            return None

    def _find_words_for_line(
        self, line: str, line_pos: int, line_length: int, segment_text: str, available_words: List[Word], current_pos: int
    ) -> List[Word]:
        """Find words that belong to a specific line."""
        line_words = []
        line_text = line.strip()
        remaining_text = line_text

        for word in available_words:
            # Skip if word isn't in remaining text
            if word.text not in remaining_text:
                continue

            # Find position of word in line
            word_pos = remaining_text.find(word.text)
            if word_pos != -1:
                line_words.append(word)
                # Remove processed text up to and including this word
                remaining_text = remaining_text[word_pos + len(word.text) :].strip()

            if not remaining_text:  # All words found
                break

        return line_words

    def _create_segment_from_words(
        self,
        line: str,
        words: List[Word],
        singer: Optional[int] = None,
    ) -> LyricsSegment:
        """Create a new segment from a list of words."""
        cleaned_text = self._clean_text(line)
        return LyricsSegment(
            id=WordUtils.generate_id(),  # Generate new ID for split segments
            text=cleaned_text,
            words=words,
            start_time=words[0].start_time,
            end_time=words[-1].end_time,
            singer=singer,
        )

    def _process_segment_text(self, text: str) -> List[str]:
        """Process segment text to determine optimal split points."""
        self.logger.debug(f"Processing segment text: '{text}'")
        processed_lines: List[str] = []
        remaining_text = text.strip()

        while remaining_text:
            self.logger.debug(f"Remaining text to process: '{remaining_text}'")

            # If remaining text is within limit, add it and we're done
            if len(remaining_text) <= self.max_line_length:
                processed_lines.append(remaining_text)
                break

            # Find best split point
            split_point = self._find_best_split_point(remaining_text)
            first_part = remaining_text[:split_point].strip()
            second_part = remaining_text[split_point:].strip()

            # Only split if:
            # 1. We found a valid split point
            # 2. First part isn't too long
            # 3. Both parts are non-empty
            if split_point < len(remaining_text) and len(first_part) <= self.max_line_length and first_part and second_part:

                processed_lines.append(first_part)
                remaining_text = second_part
            else:
                # If we can't find a good split, keep the whole text
                processed_lines.append(remaining_text)
                break

        return processed_lines

    def _find_best_split_point(self, line: str) -> int:
        """Find the best split point that creates natural, well-balanced segments."""
        self.logger.debug(f"Finding best split point for line: '{line}' (length: {len(line)})")

        # If line is within max length, don't split
        if len(line) <= self.max_line_length:
            return len(line)

        break_points = self._find_break_points(line)
        best_point = None
        best_score = float("-inf")

        # Try each break point and score it
        for priority, points in enumerate(break_points):
            for point in sorted(points):  # Sort points to prefer earlier ones in same priority
                if point <= 0 or point >= len(line):
                    continue

                first_part = line[:point].strip()

                # Skip if first part is too long
                if len(first_part) > self.max_line_length:
                    continue

                # Score this break point
                score = self._score_break_point(line, point, priority)
                if score > best_score:
                    best_score = score
                    best_point = point

        # If no good break points found, fall back to last space before max_length
        if best_point is None:
            last_space = line.rfind(" ", 0, self.max_line_length)
            if last_space != -1:
                return last_space

        return best_point if best_point is not None else self.max_line_length

    def _score_break_point(self, line: str, point: int, priority: int) -> float:
        """Score a potential break point based on multiple factors.

        Factors considered:
        1. Priority of the break point type (sentence > clause > comma, etc.)
        2. Balance of segment lengths
        3. Proximity to target length

        Example:
            line: "This is a sentence. And more text."
            point: 18 (after "sentence.")
            priority: 0 (sentence break)

        Returns a score where higher is better. Score components:
        - Base score (100-20*priority): 100 for priority 0
        - Length ratio bonus (0-10): Based on segment balance
        - Target length bonus (0-5): Based on proximity to ideal length
        """
        first_segment = line[:point].strip()
        second_segment = line[point:].strip()

        # Base score starts with priority
        score = 100 - (priority * 20)  # Priorities 0-4 give scores 100,80,60,40,20

        # Length ratio bonus
        length_ratio = min(len(first_segment), len(second_segment)) / max(len(first_segment), len(second_segment))
        score += length_ratio * 10

        # Target length bonus
        target_length = self.max_line_length * 0.7
        first_length_score = 1 - abs(len(first_segment) - target_length) / self.max_line_length
        score += first_length_score * 5

        return score

    def _find_break_points(self, line: str) -> List[List[int]]:
        """Find potential break points in order of preference.

        Returns a list of lists, where each inner list contains break points
        of the same priority. Break points are indices where text should be split.

        Priority order:
        1. Sentence endings (., !, ?)
        2. Major clause breaks (;, -)
        3. Comma breaks
        4. Coordinating conjunctions (and, but, or)
        5. Prepositions/articles (in, at, the, a)

        Example:
            Input: "Hello, world. This is a test"
            Output: [
                [12],  # sentence break after "world."
                [],   # no semicolons or dashes
                [5],  # comma after "Hello,"
                [],   # no conjunctions
                [15]  # preposition "is"
            ]
        """
        break_points = []

        # Priority 1: Sentence endings
        sentence_breaks = []
        for punct in [".", "!", "?"]:
            for match in re.finditer(rf"\{punct}\s+", line):
                sentence_breaks.append(match.start() + 1)
        break_points.append(sentence_breaks)

        # Priority 2: Major clause breaks (semicolons, dashes)
        major_breaks = []
        # A semicolon is attached to the preceding word token (e.g. "around;"),
        # so break *after* it — otherwise the split line ends with "around" while
        # the word is "around;", desyncing word-to-line matching downstream.
        for match in re.finditer(re.escape(";"), line):
            major_breaks.append(match.start() + 1)  # Position after the semicolon
        for match in re.finditer(re.escape(" - "), line):
            major_breaks.append(match.start())  # Position before the dash
        break_points.append(major_breaks)

        # Priority 3: Comma breaks, typically marking natural pauses
        comma_breaks = []
        for match in re.finditer(r",\s+", line):
            comma_breaks.append(match.start() + 1)  # Position after the comma
        break_points.append(comma_breaks)

        # Priority 4: Coordinating conjunctions with surrounding spaces
        conjunction_breaks = []
        for conj in [" and ", " but ", " or "]:
            for match in re.finditer(re.escape(conj), line):
                conjunction_breaks.append(match.start())  # Position before the conjunction
        break_points.append(conjunction_breaks)

        # Priority 5: Prepositions or articles with surrounding spaces (last resort)
        minor_breaks = []
        for prep in [" in ", " at ", " the ", " a "]:
            for match in re.finditer(re.escape(prep), line):
                minor_breaks.append(match.start())  # Position before the preposition
        break_points.append(minor_breaks)

        return break_points

    @staticmethod
    def _fmt_time(value: Optional[float]) -> str:
        """Format a timing value for logging, tolerating None.

        A debug log line must never crash the pipeline: ``f"{None:.2f}"`` raises
        ``unsupported format string passed to NoneType.__format__``.
        """
        return f"{value:.2f}" if value is not None else "None"

    def _log_input_segments(self, segments: List[LyricsSegment]) -> None:
        """Log input segment information."""
        self.logger.info(f"Starting segment resize. Input: {len(segments)} segments")
        for idx, segment in enumerate(segments):
            self.logger.debug(
                f"Input segment {idx}: text='{segment.text}', "
                f"words={len(segment.words)} words, "
                f"time={self._fmt_time(segment.start_time)}-{self._fmt_time(segment.end_time)}"
            )

    def _log_output_segments(self, segments: List[LyricsSegment]) -> None:
        """Log output segment information."""
        self.logger.info(f"Finished resizing. Output: {len(segments)} segments")
        for idx, segment in enumerate(segments):
            self.logger.debug(
                f"Output segment {idx}: text='{segment.text}', "
                f"words={len(segment.words)} words, "
                f"time={self._fmt_time(segment.start_time)}-{self._fmt_time(segment.end_time)}"
            )
