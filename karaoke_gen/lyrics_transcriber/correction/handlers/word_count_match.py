from typing import List, Tuple, Dict, Any, Optional
import logging

from karaoke_gen.lyrics_transcriber.types import GapSequence, WordCorrection
from karaoke_gen.lyrics_transcriber.correction.handlers.base import GapCorrectionHandler
from karaoke_gen.lyrics_transcriber.correction.handlers.word_operations import WordOperations


class WordCountMatchHandler(GapCorrectionHandler):
    """Handles gaps where reference sources agree and have matching word counts."""

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

        ref_word_lists = list(gap.reference_word_ids.values())

        # All sources must have same number of words as gap
        if not all(len(words) == gap.length for words in ref_word_lists):
            self.logger.debug("Not all sources have the same number of words as the gap.")
            return False, {}

        # If we have multiple sources, they must all agree
        if len(ref_word_lists) > 1 and not all(words == ref_word_lists[0] for words in ref_word_lists[1:]):
            self.logger.debug("Not all sources agree on the words.")
            return False, {}

        self.logger.debug("All sources agree and have matching word counts.")
        return True, {"word_map": data["word_map"]}

    def handle(self, gap: GapSequence, data: Optional[Dict[str, Any]] = None) -> List[WordCorrection]:
        if not self._validate_data(data):
            return []

        corrections = []
        word_map = data["word_map"]
        source = list(gap.reference_word_ids.keys())[0]
        reference_word_ids = gap.reference_word_ids[source]
        sources = ", ".join(gap.reference_word_ids.keys())

        reference_positions = WordOperations.calculate_reference_positions(gap)

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
                    source=sources,
                    confidence=1.0,
                    reason="Reference sources had same word count as gap",
                    reference_positions=reference_positions,
                    handler="WordCountMatchHandler",
                    original_word_id=orig_word_id,
                    corrected_word_id=ref_word_id,  # Use the reference word's ID
                )
                corrections.append(correction)
                self.logger.debug(f"Correction made: {correction}")

        return corrections
