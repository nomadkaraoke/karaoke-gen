from typing import List, Dict, Optional, Tuple, Any
from karaoke_gen.lyrics_transcriber.types import GapSequence, WordCorrection
from karaoke_gen.lyrics_transcriber.correction.handlers.base import GapCorrectionHandler
from karaoke_gen.lyrics_transcriber.correction.handlers.word_operations import WordOperations
import logging


class RepeatCorrectionHandler(GapCorrectionHandler):
    """Handler that applies corrections that were previously made by other handlers."""

    def __init__(self, logger: Optional[logging.Logger] = None, confidence_threshold: float = 0.7):
        super().__init__(logger)
        self.logger = logger or logging.getLogger(__name__)
        self.confidence_threshold = confidence_threshold
        self.previous_corrections: List[WordCorrection] = []

    def can_handle(self, gap: GapSequence, data: Optional[Dict[str, Any]] = None) -> Tuple[bool, Dict[str, Any]]:
        """Check if any words in the gap match previous corrections."""
        if not self._validate_data(data):
            return False, {}

        return bool(self.previous_corrections), {"word_map": data["word_map"], "anchor_sequences": data.get("anchor_sequences", [])}

    def set_previous_corrections(self, corrections: List[WordCorrection]) -> None:
        """Store corrections from previous handlers to use as reference."""
        self.previous_corrections = corrections

    def handle(self, gap: GapSequence, data: Optional[Dict[str, Any]] = None) -> List[WordCorrection]:
        """Apply previous corrections to matching words in the current gap."""
        if not self._validate_data(data):
            return []

        word_map = data["word_map"]
        corrections = []

        # Use the centralized method to calculate reference positions
        reference_positions = WordOperations.calculate_reference_positions(gap, anchor_sequences=data.get("anchor_sequences", []))

        # Build a map of original words to their corrections
        correction_map: Dict[str, List[WordCorrection]] = {}
        for corr in self.previous_corrections:
            if corr.confidence >= self.confidence_threshold:
                correction_map.setdefault(corr.original_word.lower(), []).append(corr)

        # Check each word in the gap
        for i, word_id in enumerate(gap.transcribed_word_ids):
            if word_id not in word_map:
                self.logger.error(f"Word ID {word_id} not found in word map")
                continue

            word = word_map[word_id]
            word_lower = word.text.lower()

            if word_lower in correction_map:
                # Get the most common correction for this word
                prev_corrections = correction_map[word_lower]
                best_correction = max(
                    prev_corrections,
                    key=lambda c: (sum(1 for pc in prev_corrections if pc.corrected_word == c.corrected_word), c.confidence),
                )

                self.logger.debug(
                    f"Applying previous correction: {word.text} -> {best_correction.corrected_word} "
                    f"(confidence: {best_correction.confidence:.2f})"
                )

                corrections.append(
                    WordCorrection(
                        original_word=word.text,
                        corrected_word=best_correction.corrected_word,
                        segment_index=0,
                        original_position=gap.transcription_position + i,
                        confidence=best_correction.confidence * 0.9,  # Slightly lower confidence for repeats
                        source=best_correction.source,
                        reason=f"RepeatCorrectionHandler: Matches previous correction",
                        alternatives={best_correction.corrected_word: 1},
                        is_deletion=best_correction.is_deletion,
                        reference_positions=reference_positions,
                        length=best_correction.length,
                        split_index=best_correction.split_index,
                        split_total=best_correction.split_total,
                        handler="RepeatCorrectionHandler",
                        word_id=word_id,
                        corrected_word_id=best_correction.corrected_word_id,
                    )
                )

        return corrections
