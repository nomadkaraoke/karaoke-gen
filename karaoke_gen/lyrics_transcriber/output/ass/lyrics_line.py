from dataclasses import dataclass
import functools
from typing import Optional, Tuple, List
import logging
from datetime import timedelta
from PIL import ImageFont
import os

from karaoke_gen.lyrics_transcriber.types import LyricsSegment
from karaoke_gen.lyrics_transcriber.output.ass.event import Event
from karaoke_gen.lyrics_transcriber.output.ass.style import Style
from karaoke_gen.lyrics_transcriber.output.ass.config import LineState, ScreenConfig
from karaoke_gen.lyrics_transcriber.output.ass.text_direction import is_rtl_text, rtl_karaoke_fill_tags
from karaoke_gen.lyrics_transcriber.output.segment_resizer import (
    display_width,
    grapheme_clusters,
    is_unspaced_script,
)
from karaoke_gen.utils.font_fallback import ass_font_scale, find_font_covering, missing_codepoints


@functools.lru_cache(maxsize=32)
def _measure_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    """Fonts are loaded once per (path, size): fallback CJK .ttc files are 20MB+."""
    try:
        return ImageFont.truetype(path, size=size)
    except OSError as e:
        logging.getLogger(__name__).warning(f"Font error ({e}), using default for measurement")
        return ImageFont.load_default()


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

    def _measure_text(self, text: str, style: Style) -> Tuple[int, int]:
        """Pixel (width, height) of ``text`` as libass will render it in ``style``.

        Mirrors libass: characters the style font covers use it; the rest use a
        fontconfig fallback (regular weight unless the style is bold), and each font is
        scaled from its own metrics (see ``ass_font_scale``). Measuring Hebrew/CJK with
        the style font alone would use .notdef widths.
        """
        font_path = style.Fontpath if style.Fontpath and os.path.exists(style.Fontpath) else None
        if font_path is None:
            self.logger.warning(f"Could not load font {style.Fontpath}, using default for measurement")
            font = ImageFont.load_default()
            bbox = font.getbbox(text)
            return bbox[2] - bbox[0], bbox[3] - bbox[1]

        # libass picks a fallback per glyph. Prefer one font for all missing characters
        # (usually the case: one script); otherwise resolve each character on its own,
        # e.g. a Hebrew line with a CJK character or a symbol the Hebrew font lacks.
        missing = missing_codepoints(font_path, text)
        bold = bool(style.Bold)
        shared_fallback = find_font_covering(missing, bold=bold) if missing else None
        per_char = {} if shared_fallback or not missing else {
            cp: find_font_covering(frozenset({cp}), bold=bold) or font_path for cp in missing
        }

        def font_for(ch: str) -> str:
            if ord(ch) not in missing:
                return font_path
            return shared_fallback or per_char[ord(ch)]

        # Group consecutive characters by the font that will draw them
        runs: List[Tuple[str, str]] = []
        for ch in text:
            path = font_for(ch)
            if runs and runs[-1][0] == path:
                runs[-1] = (path, runs[-1][1] + ch)
            else:
                runs.append((path, ch))

        # Width = sum of advances; height = union of ink extents on the shared baseline
        width, top, bottom = 0.0, 0, 0
        for path, run in runs:
            font = _measure_font(path, max(1, int(style.Fontsize * ass_font_scale(path))))
            width += font.getlength(run)
            bbox = font.getbbox(run, anchor="ls")
            top, bottom = min(top, bbox[1]), max(bottom, bbox[3])
        height = bottom - top

        self.logger.debug(f"Text dimensions for '{text}': width={width:.0f}px, height={height}px")
        return int(round(width)), height

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
        # Apply case transformation to match the actual rendered text
        main_text = self._apply_case_transform(self.segment.text)
        # RTL lines start on the right, so the lead-in comes in from the right edge
        rtl = is_rtl_text(main_text)
        main_width, main_height = self._measure_text(main_text, style)
        rect_width = int(self.screen_config.video_width * (config.lead_in_width_percent / 100))
        rect_height = int(self.screen_config.video_height * (config.lead_in_height_percent / 100))
        # Calculate where the edge of the centered text the singer starts from will be
        text_left = self.screen_config.video_width//2 - main_width//2
        text_right = text_left + main_width
        # Apply horizontal offset if configured (mirrored for RTL)
        horizontal_offset = int(self.screen_config.video_width * (config.lead_in_horiz_offset_percent / 100))
        if rtl:
            # With \an8 libass centres a drawing's width on the \move target, so the
            # LTR box ends rect_width/2 before text_left. Mirror that gap on the right
            # (shape drawn at x in [0, rect_width]) and slide in from off-screen right.
            final_x_position = text_right + rect_width - horizontal_offset
            start_x_position = self.screen_config.video_width + rect_width
        else:
            final_x_position = text_left + horizontal_offset
            start_x_position = 0
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
            f"{{\\move({start_x_position},{final_y_position},{final_x_position},{final_y_position},0,{move_duration})}}"  # Move until line start
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
        
        # Add the rectangle shape, drawn up from the bottom on the side facing away
        # from the text (left of an LTR line's start, right of an RTL line's start)
        if rtl:
            main_text += f"{{\\p1}}m 0 {-rect_height} l {rect_width} {-rect_height} {rect_width} 0 0 0{{\\p0}}"
        else:
            main_text += f"{{\\p1}}m {-rect_width} {-rect_height} l 0 {-rect_height} 0 0 {-rect_width} 0{{\\p0}}"
        
        main_event.Text = main_text
        
        return [main_event]

    def create_ass_events(
        self,
        state: LineState,
        style: Style,
        config: ScreenConfig,
        previous_end_time: Optional[float] = None,
        styles_by_singer: Optional[dict] = None,
    ) -> List[Event]:
        """Create ASS events for this line.

        If styles_by_singer is provided, the main event is tagged with the
        style for self.segment.singer (falling back to singer 1 when
        self.segment.singer is None). Otherwise the fallback `style` is used
        (solo / backward-compat path).
        """
        self.previous_end_time = previous_end_time
        events = []

        # Pick the style for this line
        line_style = style
        if styles_by_singer:
            singer_key = self.segment.singer if self.segment.singer is not None else 1
            line_style = styles_by_singer.get(singer_key, style)

        # Create lead-in event if needed
        lead_in_event = self._create_lead_in_event(state, line_style, config.video_width, config)
        if lead_in_event:
            events.extend(lead_in_event)

        # Create main lyrics event
        main_event = Event()
        main_event.type = "Dialogue"
        main_event.Layer = 0
        main_event.Style = line_style
        main_event.Start = state.timing.fade_in_time
        main_event.End = state.timing.end_time

        # Use absolute positioning
        x_pos = config.video_width // 2  # Center horizontally

        # Main lyrics text with positioning and fade
        text = (
            f"{{\\an8}}{{\\pos({x_pos},{state.y_position})}}"
            f"{{\\fad({config.fade_in_ms},{config.fade_out_ms})}}"
        )
        rtl_tags = rtl_karaoke_fill_tags(line_style.Angle) if is_rtl_text(self.segment.text) else ""
        text += rtl_tags

        # Add the main lyrics text with karaoke timing
        text += self._create_ass_text(
            timedelta(seconds=state.timing.fade_in_time),
            styles_by_singer=styles_by_singer,
            rtl_tags=rtl_tags,
        )

        main_event.Text = text
        events.append(main_event)

        translation_event = self._create_translation_event(state, config)
        if translation_event:
            events.append(translation_event)

        return events

    # Fraction of the frame width a translation may use before it's wrapped / scaled
    TRANSLATION_MAX_WIDTH_FRACTION = 0.92

    # Average glyph advance / font size, used when the style font file isn't available
    ESTIMATED_CHAR_WIDTH_RATIO = 0.6

    def _translation_width(self, text: str, style: Style) -> float:
        """Rendered width of a translation row; estimated from the character count
        when the font file is missing (PIL's default font would under-measure)."""
        if style.Fontpath and os.path.exists(style.Fontpath):
            return self._measure_text(text, style)[0]
        return display_width(text) * style.Fontsize * self.ESTIMATED_CHAR_WIDTH_RATIO

    @staticmethod
    def _clean_translation(text: str) -> str:
        """Plain text safe for an ASS Dialogue: no override blocks, escapes or newlines."""
        cleaned = text.replace("{", "(").replace("}", ")").replace("\\", " ")
        return " ".join(cleaned.split())

    @staticmethod
    def _split_rows(text: str, rows: int) -> List[str]:
        """Split ``text`` into ``rows`` lines of similar length at word boundaries.

        Unspaced scripts (CJK, Thai) split between characters.
        """
        if rows <= 1:
            return [text]
        tokens = text.split(" ")
        joiner = " "
        if len(tokens) < rows and is_unspaced_script(text):
            tokens, joiner = grapheme_clusters(text), ""
        if len(tokens) < rows:
            return [text]
        out: List[str] = []
        remaining = tokens
        for r in range(rows, 1, -1):
            total = len(joiner.join(remaining))
            target = total / r
            best_i, best_diff = 1, None
            for i in range(1, len(remaining) - r + 2):
                diff = abs(len(joiner.join(remaining[:i])) - target)
                if best_diff is None or diff < best_diff:
                    best_i, best_diff = i, diff
            out.append(joiner.join(remaining[:best_i]))
            remaining = remaining[best_i:]
        out.append(joiner.join(remaining))
        return out

    def _create_translation_event(self, state: LineState, config: ScreenConfig) -> Optional[Event]:
        """Static (un-highlighted) translation row(s) beneath the lyric line."""
        style = getattr(config, "translation_style", None)
        if style is None or not self.segment.translation:
            return None
        text = self._clean_translation(self._apply_case_transform(self.segment.translation))
        if not text:
            return None

        max_width = config.video_width * self.TRANSLATION_MAX_WIDTH_FRACTION
        rows = [text]
        widest = self._translation_width(text, style)
        if widest > max_width and config.translation_max_rows > 1:
            rows = self._split_rows(text, config.translation_max_rows)
            widest = max(self._translation_width(row, style) for row in rows)
        # Still too wide: shrink to fit rather than overflow the frame edges
        scale = 100
        if widest > max_width:
            scale = max(1, int(max_width / widest * 100))

        y = state.y_position + config.lyric_line_height + config.translation_gap
        tags = (
            f"{{\\an8}}{{\\pos({config.video_width // 2},{y})}}"
            f"{{\\fad({config.fade_in_ms},{config.fade_out_ms})}}"
        )
        if scale < 100:
            tags += f"{{\\fscx{scale}\\fscy{scale}}}"

        event = Event()
        event.type = "Dialogue"
        event.Layer = 0
        event.Style = style
        event.Start = state.timing.fade_in_time
        event.End = state.timing.end_time
        event.Text = tags + "\\N".join(rows)
        return event

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

    def _create_ass_text(
        self,
        start_ts: timedelta,
        styles_by_singer: Optional[dict] = None,
        rtl_tags: str = "",
    ) -> str:
        """Create the ASS text with karaoke timing tags and word-level singer overrides.

        ``rtl_tags``: the RTL fill tags the line starts with (empty for LTR); ``{\\r}``
        resets every override, so they are re-emitted after each reset.
        """
        reset = r"{\r}" + rtl_tags
        # Every tag is derived from one absolute centisecond timeline (relative to the
        # event start), so rounding and small inter-word gaps can't accumulate. Dropping
        # gaps <= 0.1s used to make the highlight run ahead of the vocal by the sum of
        # those gaps (0.2s+ by the end of some real lines).
        line_start = start_ts.total_seconds()

        def to_cs(t: float) -> int:
            return int(round((t - line_start) * 100))

        cursor = max(0, to_cs(self.segment.start_time))
        text = r"{\k" + str(cursor) + r"}"

        segment_singer = self.segment.singer if self.segment.singer is not None else 1
        current_inline_singer = None  # tracks whether we've emitted a color override

        for word in self.segment.words:
            # Pause the highlight for any gap before the word (overlaps clamp to 0)
            word_start = max(to_cs(word.start_time), cursor)
            if word_start > cursor:
                text += r"{\k" + str(word_start - cursor) + r"}"

            # Add the word with its duration
            word_end = max(to_cs(word.end_time), word_start)
            duration = word_end - word_start
            cursor = word_end
            # Apply case transformation to the word text
            # Defensive strip: Word.__post_init__ should already handle this,
            # but embedded newlines in ASS dialogue events cause silent word
            # loss so we guard against it at the output boundary too.
            clean_word_text = word.text.replace("\n", "").strip()
            transformed_text = self._apply_case_transform(clean_word_text)

            # Determine whether this word has a singer override
            word_singer = word.singer if word.singer is not None else segment_singer
            needs_override = (
                styles_by_singer is not None
                and word_singer != segment_singer
            )

            if needs_override and current_inline_singer != word_singer:
                override_style = styles_by_singer.get(word_singer)
                if override_style is not None:
                    # ASS inline format: &HBBGGRR&. Override primary (post-sung
                    # tint), secondary (pre-sung signature) and outline so the
                    # word is fully themed to its override singer, not just the
                    # after-sung tint.
                    def _bgr(rgba):
                        r, g, b = rgba[:3]
                        return f"{b:02X}{g:02X}{r:02X}"
                    text += r"{\1c&H" + _bgr(override_style.PrimaryColour) + r"&}"
                    text += r"{\2c&H" + _bgr(override_style.SecondaryColour) + r"&}"
                    text += r"{\3c&H" + _bgr(override_style.OutlineColour) + r"&}"
                    current_inline_singer = word_singer
                elif current_inline_singer is not None:
                    # Missing singer in map — reset to line's base style so the
                    # previous override color doesn't bleed onto this word.
                    text += reset
                    current_inline_singer = None
            elif not needs_override and current_inline_singer is not None:
                text += reset
                current_inline_singer = None

            text += r"{\kf" + str(duration) + r"}" + transformed_text + " "


        # Close any lingering override
        if current_inline_singer is not None:
            text += reset

        return text.rstrip()

    def __str__(self):
        return f"{{{self.segment.text}}}"
