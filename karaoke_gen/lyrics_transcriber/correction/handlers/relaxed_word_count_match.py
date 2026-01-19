from typing import List, Tuple, Dict, Any, Optional
import logging

from karaoke_gen.lyrics_transcriber.types import GapSequence, WordCorrection
from karaoke_gen.lyrics_transcriber.correction.handlers.base import GapCorrectionHandler
from karaoke_gen.lyrics_transcriber.correction.handlers.word_operations import WordOperations


class RelaxedWordCountMatchHandler(GapCorrectionHandler):
    """Handles gaps where at least one reference source has matching word count."""

    def __init__(self, logger: Optional[logging.Logger] = None):
        super().__init__(logger)
        self.logger = logger or logging.getLogger(__name__)

    def can_handle(self, gap: GapSequence, data: Optional[Dict[str, Any]] = None) -> Tuple[bool, Dict[str, Any]]:
        # Must have reference words
        if not gap.reference_word_ids:
            self.logger.debug("No reference word IDs available.")
            return False, {}

        if not self._validate_data(data):
            return False, {}

        # Check if any source has matching word count
        for source, ref_word_ids in gap.reference_word_ids.items():
            if len(ref_word_ids) == gap.length:
                self.logger.debug(f"Source '{source}' has matching word count.")
                return True, {
                    "matching_source": source,
                    "reference_word_ids": ref_word_ids,
                    "word_map": data["word_map"],
                    "anchor_sequences": data.get("anchor_sequences", []),
                }

        self.logger.debug("No source with matching word count found.")
        return False, {}

    def handle(self, gap: GapSequence, data: Optional[Dict[str, Any]] = None) -> List[WordCorrection]:
        """Handle the gap using word count matching."""
        if not self._validate_data(data):
            return []

        corrections = []
        matching_source = data["matching_source"]
        reference_word_ids = data["reference_word_ids"]
        word_map = data["word_map"]
        anchor_sequences = data.get("anchor_sequences", [])

        # Use the centralized method to calculate reference positions
        reference_positions = WordOperations.calculate_reference_positions(
            gap, sources=[matching_source], anchor_sequences=anchor_sequences
        )
        self.logger.debug(f"Calculated reference positions: {reference_positions}")

        # Since we found a source with matching word count, we can correct using that source
        for i, (orig_word_id, ref_word_id) in enumerate(zip(gap.transcribed_word_ids, reference_word_ids)):
            # Get the actual words from the word map
            if orig_word_id not in word_map:
                self.logger.error(f"Original word ID {orig_word_id} not found in word_map")
                continue
            orig_word = word_map[orig_word_id]

            if ref_word_id not in word_map:
                self.logger.error(f"Reference word ID {ref_word_id} not found in word_map")
                continue
            ref_word = word_map[ref_word_id]

            if orig_word.text.lower() != ref_word.text.lower():
                correction = WordOperations.create_word_replacement_correction(
                    original_word=orig_word.text,
                    corrected_word=ref_word.text,
                    original_position=gap.transcription_position + i,
                    source=matching_source,
                    confidence=1.0,
                    reason=f"Source '{matching_source}' had matching word count",
                    reference_positions=reference_positions,
                    handler="RelaxedWordCountMatchHandler",
                    original_word_id=orig_word_id,
                    corrected_word_id=ref_word_id,  # Use the reference word's ID
                )
                corrections.append(correction)
                self.logger.debug(f"Correction made: {correction}")

        return corrections
