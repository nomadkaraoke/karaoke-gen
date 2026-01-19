from typing import List, Optional, Tuple, Dict, Any
import logging

from karaoke_gen.lyrics_transcriber.types import GapSequence, WordCorrection, Word
from karaoke_gen.lyrics_transcriber.correction.handlers.base import GapCorrectionHandler
from karaoke_gen.lyrics_transcriber.correction.handlers.word_operations import WordOperations


class ExtendAnchorHandler(GapCorrectionHandler):
    """Handles gaps where some words match reference text but there are extra words.

    This handler looks for cases where:
    1. One or more words in the gap match words in the same position in at least one reference source
    2. The gap may contain additional words that aren't in the reference

    When such matches are found, it:
    1. Validates all matching words (creates corrections that keep the same words)
    2. Leaves all non-matching words unchanged for other handlers to process

    The confidence of validations is based on the ratio of reference sources that agree.
    For example, if 2 out of 4 sources have the matching word, confidence will be 0.5.

    Examples:
        Gap: "hello world extra words"
        References:
            genius: ["hello", "world"]
            spotify: ["hello", "world"]
        Result:
            - Validate "hello" (confidence=1.0)
            - Validate "world" (confidence=1.0)
            - Leave "extra" and "words" unchanged

        Gap: "martyr youre a"
        References:
            genius: ["martyr"]
            spotify: ["mother"]
        Result:
            - Validate "martyr" (confidence=0.5, source="genius")
            - Leave "youre" and "a" unchanged
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)

    def can_handle(self, gap: GapSequence, data: Optional[Dict[str, Any]] = None) -> Tuple[bool, Dict[str, Any]]:
        """Check if this gap can be handled by extending anchor sequences."""
        # Must have reference word IDs
        if not gap.reference_word_ids:
            self.logger.debug("No reference word IDs available.")
            return False, {}

        # Gap must have word IDs
        if not gap.transcribed_word_ids:
            self.logger.debug("No word IDs in the gap to process.")
            return False, {}

        # Must have word map to resolve IDs to actual words
        if not self._validate_data(data):
            return False, {}
            
        word_map = data["word_map"]

        # At least one word must match between gap and any reference source by text content
        has_match = False
        for i, trans_word_id in enumerate(gap.transcribed_word_ids):
            if trans_word_id not in word_map:
                continue
            trans_word = word_map[trans_word_id]
            
            # Check if this word matches any reference word at the same position
            for ref_word_ids in gap.reference_word_ids.values():
                if i < len(ref_word_ids):
                    ref_word_id = ref_word_ids[i]
                    if ref_word_id in word_map:
                        ref_word = word_map[ref_word_id]
                        if trans_word.text.lower() == ref_word.text.lower():
                            has_match = True
                            break
            if has_match:
                break

        self.logger.debug(f"Can handle gap: {has_match}")
        return has_match, {"word_map": word_map}

    def handle(self, gap: GapSequence, data: Optional[Dict[str, Any]] = None) -> List[WordCorrection]:
        corrections = []

        # Get word lookup map from data
        if not self._validate_data(data):
            return []
            
        word_map = data["word_map"]

        # Process each word in the gap that has a corresponding reference position
        for i, word_id in enumerate(gap.transcribed_word_ids):
            # Get the actual word object
            if word_id not in word_map:
                self.logger.error(f"Word ID {word_id} not found in word_map")
                continue
            word = word_map[word_id]

            # Find reference sources that have a matching word (by text) at this position
            matching_sources = []
            corrected_word_id = None
            
            for source, ref_word_ids in gap.reference_word_ids.items():
                if i < len(ref_word_ids):
                    ref_word_id = ref_word_ids[i]
                    if ref_word_id in word_map:
                        ref_word = word_map[ref_word_id]
                        if word.text.lower() == ref_word.text.lower():
                            matching_sources.append(source)
                            if corrected_word_id is None:
                                corrected_word_id = ref_word_id

            if not matching_sources:
                self.logger.debug(f"Skipping word '{word.text}' at position {i} - no matching references")
                continue

            # Word matches reference(s) at this position - validate it
            confidence = len(matching_sources) / len(gap.reference_word_ids)
            sources = ", ".join(matching_sources)

            # Get base reference positions
            base_reference_positions = WordOperations.calculate_reference_positions(gap, matching_sources)

            # Adjust reference positions based on the word's position in the reference text
            reference_positions = {}
            for source in matching_sources:
                if source in base_reference_positions:
                    reference_positions[source] = base_reference_positions[source] + i

            corrections.append(
                WordOperations.create_word_replacement_correction(
                    original_word=word.text,
                    corrected_word=word.text,
                    original_position=gap.transcription_position + i,
                    source=sources,
                    confidence=confidence,
                    reason="Matched reference source(s)",
                    reference_positions=reference_positions,
                    handler="ExtendAnchorHandler",
                    original_word_id=word_id,
                    corrected_word_id=corrected_word_id,
                )
            )
            self.logger.debug(f"Validated word '{word.text}' with confidence {confidence} from sources: {sources}")

        return corrections
