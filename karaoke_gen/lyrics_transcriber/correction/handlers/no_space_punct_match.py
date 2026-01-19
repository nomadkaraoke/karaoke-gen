from typing import List, Optional, Tuple, Dict, Any
import logging
import re

from karaoke_gen.lyrics_transcriber.types import GapSequence, WordCorrection
from karaoke_gen.lyrics_transcriber.correction.handlers.base import GapCorrectionHandler
from karaoke_gen.lyrics_transcriber.correction.handlers.word_operations import WordOperations


class NoSpacePunctuationMatchHandler(GapCorrectionHandler):
    """Handles gaps where reference text matches when spaces and punctuation are removed."""

    def __init__(self, logger: Optional[logging.Logger] = None):
        super().__init__(logger)
        self.logger = logger or logging.getLogger(__name__)

    def _remove_spaces_and_punct(self, words: List[str]) -> str:
        """Join words and remove all whitespace and punctuation."""
        text = "".join(words).lower()
        # Remove all punctuation including apostrophes
        return re.sub(r"[^\w\s]", "", text)

    def can_handle(self, gap: GapSequence, data: Optional[Dict[str, Any]] = None) -> Tuple[bool, Dict[str, Any]]:
        # Must have reference words
        if not gap.reference_word_ids:
            self.logger.debug("No reference word IDs available.")
            return False, {}

        # Get word lookup map from data
        if not data or "word_map" not in data:
            self.logger.error("No word_map provided in data")
            return False, {}

        word_map = data["word_map"]

        # Get the actual words from word IDs
        gap_words = []
        for word_id in gap.transcribed_word_ids:
            if word_id not in word_map:
                self.logger.error(f"Word ID {word_id} not found in word_map")
                return False, {}
            gap_words.append(word_map[word_id].text)

        # Get the gap text without spaces and punctuation
        gap_text = self._remove_spaces_and_punct(gap_words)

        # Check if any reference source matches when spaces and punctuation are removed
        for source, ref_word_ids in gap.reference_word_ids.items():
            ref_words = []
            for word_id in ref_word_ids:
                if word_id not in word_map:
                    self.logger.error(f"Reference word ID {word_id} not found in word_map")
                    continue
                ref_words.append(word_map[word_id].text)

            if not ref_words:
                continue

            ref_text = self._remove_spaces_and_punct(ref_words)
            if gap_text == ref_text:
                self.logger.debug("Found a matching reference source with spaces and punctuation removed.")
                return True, {
                    "matching_source": source,
                    "reference_word_ids": ref_word_ids,
                    "word_map": word_map,
                }

        self.logger.debug("No matching reference source found with spaces and punctuation removed.")
        return False, {}

    def handle(self, gap: GapSequence, data: Optional[Dict[str, Any]] = None) -> List[WordCorrection]:
        """Handle the gap using no-space punctuation matching."""
        if not data:
            can_handle, data = self.can_handle(gap)
            if not can_handle:
                return []

        corrections = []
        matching_source = data["matching_source"]
        reference_word_ids = data["reference_word_ids"]
        word_map = data["word_map"]

        # Calculate reference positions for the matching source
        reference_positions = WordOperations.calculate_reference_positions(gap, [matching_source])

        # Handle cases where number of words differ
        if len(gap.transcribed_word_ids) > len(reference_word_ids):
            # Multiple transcribed words -> fewer reference words
            # Get the actual words from word IDs
            gap_words = [word_map[word_id].text for word_id in gap.transcribed_word_ids]
            ref_word = word_map[reference_word_ids[0]].text

            corrections.extend(
                WordOperations.create_word_combine_corrections(
                    original_words=gap_words,
                    reference_word=ref_word,
                    original_position=gap.transcription_position,
                    source=matching_source,
                    confidence=1.0,
                    combine_reason="Words combined based on text match",
                    delete_reason="Word removed as part of text match combination",
                    reference_positions=reference_positions,
                    handler="NoSpacePunctuationMatchHandler",
                    original_word_ids=gap.transcribed_word_ids,
                    corrected_word_id=reference_word_ids[0],  # Use the reference word's ID
                )
            )
            self.logger.debug(f"Combined words into '{ref_word}'.")

        elif len(gap.transcribed_word_ids) < len(reference_word_ids):
            # Single transcribed word -> multiple reference words
            # Get the actual words
            gap_word = word_map[gap.transcribed_word_ids[0]].text
            ref_words = [word_map[word_id].text for word_id in reference_word_ids]

            corrections.extend(
                WordOperations.create_word_split_corrections(
                    original_word=gap_word,
                    reference_words=ref_words,
                    original_position=gap.transcription_position,
                    source=matching_source,
                    confidence=1.0,
                    reason="Split word based on text match",
                    reference_positions=reference_positions,
                    handler="NoSpacePunctuationMatchHandler",
                    original_word_id=gap.transcribed_word_ids[0],
                    corrected_word_ids=reference_word_ids,  # Use the reference word IDs
                )
            )
            self.logger.debug(f"Split word '{gap_word}' into {ref_words}.")

        else:
            # One-to-one replacement
            for i, (orig_word_id, ref_word_id) in enumerate(zip(gap.transcribed_word_ids, reference_word_ids)):
                orig_word = word_map[orig_word_id]
                ref_word = word_map[ref_word_id]

                if orig_word.text.lower() != ref_word.text.lower():
                    correction = WordOperations.create_word_replacement_correction(
                        original_word=orig_word.text,
                        corrected_word=ref_word.text,
                        original_position=gap.transcription_position + i,
                        source=matching_source,
                        confidence=1.0,
                        reason=f"Source '{matching_source}' matched when spaces and punctuation removed",
                        reference_positions=reference_positions,
                        handler="NoSpacePunctuationMatchHandler",
                        original_word_id=orig_word_id,
                        corrected_word_id=ref_word_id,
                    )
                    corrections.append(correction)
                    self.logger.debug(f"Correction made: {correction}")

        return corrections
