from typing import List, Dict, Tuple, Optional, Any
import logging
from metaphone import doublemetaphone
from karaoke_gen.lyrics_transcriber.types import GapSequence, WordCorrection
from karaoke_gen.lyrics_transcriber.correction.handlers.base import GapCorrectionHandler
from karaoke_gen.lyrics_transcriber.correction.handlers.word_operations import WordOperations


class SoundAlikeHandler(GapCorrectionHandler):
    """Handles gaps where words sound similar to reference words but are spelled differently.

    Uses Double Metaphone algorithm to detect sound-alike words. For each word in the gap,
    it checks if its phonetic encoding matches any reference word's encoding.

    The confidence of corrections is based on:
    1. The ratio of reference sources agreeing on the correction
    2. Whether the match was on primary (1.0) or secondary (0.8) metaphone code

    Examples:
        Gap: "shush look deep"
        References:
            genius: ["search", "look", "deep"]
            spotify: ["search", "look", "deep"]
        Result:
            - Correct "shush" to "search" (confidence based on metaphone match type)
            - Validate "look" and "deep" (exact matches)
    """

    def __init__(self, logger: Optional[logging.Logger] = None, similarity_threshold: float = 0.6):
        """Initialize the handler.

        Args:
            logger: Optional logger instance
            similarity_threshold: Minimum confidence threshold for matches (default: 0.6)
        """
        self.logger = logger or logging.getLogger(__name__)
        self.similarity_threshold = similarity_threshold

    def can_handle(self, gap: GapSequence, data: Optional[Dict[str, Any]] = None) -> Tuple[bool, Dict[str, Any]]:
        """Check if any gap word has a metaphone match with any reference word."""
        if not self._validate_data(data):
            return False, {}

        word_map = data["word_map"]

        # Must have reference words
        if not gap.reference_word_ids:
            self.logger.debug("No reference words available")
            return False, {}

        # Gap must have words
        if not gap.transcribed_word_ids:
            self.logger.debug("No gap words available")
            return False, {}

        # Check if any gap word has a metaphone match with any reference word
        for word_id in gap.transcribed_word_ids:
            if word_id not in word_map:
                continue
            word = word_map[word_id]
            word_codes = doublemetaphone(word.text)
            self.logger.debug(f"Gap word '{word.text}' has metaphone codes: {word_codes}")

            for source, ref_word_ids in gap.reference_word_ids.items():
                for ref_word_id in ref_word_ids:
                    if ref_word_id not in word_map:
                        continue
                    ref_word = word_map[ref_word_id]
                    ref_codes = doublemetaphone(ref_word.text)
                    self.logger.debug(f"Reference word '{ref_word.text}' has metaphone codes: {ref_codes}")
                    if self._codes_match(word_codes, ref_codes):
                        self.logger.debug(f"Found metaphone match between '{word.text}' and '{ref_word.text}'")
                        return True, {}

        self.logger.debug("No metaphone matches found")
        return False, {}

    def handle(self, gap: GapSequence, data: Optional[Dict[str, Any]] = None) -> List[WordCorrection]:
        """Process the gap and create corrections for sound-alike matches."""
        if not self._validate_data(data):
            return []

        word_map = data["word_map"]
        corrections = []

        # Use the centralized method to calculate reference positions
        reference_positions = WordOperations.calculate_reference_positions(gap, anchor_sequences=data.get("anchor_sequences", []))

        # For each word in the gap
        for i, word_id in enumerate(gap.transcribed_word_ids):
            if word_id not in word_map:
                continue
            word = word_map[word_id]
            word_codes = doublemetaphone(word.text)
            self.logger.debug(f"Processing '{word.text}' (codes: {word_codes})")

            # Skip if word exactly matches any reference
            exact_match = False
            for source, ref_word_ids in gap.reference_word_ids.items():
                if i < len(ref_word_ids):
                    ref_word_id = ref_word_ids[i]
                    if ref_word_id in word_map:
                        ref_word = word_map[ref_word_id]
                        if word.text.lower() == ref_word.text.lower():
                            exact_match = True
                            break
            if exact_match:
                continue

            # Find sound-alike matches in references
            matches: Dict[str, Tuple[List[str], float, str]] = {}  # Added word_id to tuple

            for source, ref_word_ids in gap.reference_word_ids.items():
                for j, ref_word_id in enumerate(ref_word_ids):
                    if ref_word_id not in word_map:
                        continue
                    ref_word = word_map[ref_word_id]
                    ref_codes = doublemetaphone(ref_word.text)

                    match_confidence = self._get_match_confidence(word_codes, ref_codes)
                    if match_confidence >= self.similarity_threshold:
                        # Special handling for short codes - don't apply position penalty
                        is_short_code = any(len(c) <= 2 for c in word_codes if c) or any(len(c) <= 2 for c in ref_codes if c)
                        position_multiplier = 1.0 if is_short_code or i == j else 0.8

                        adjusted_confidence = match_confidence * position_multiplier

                        if adjusted_confidence >= self.similarity_threshold:
                            if ref_word.text not in matches:
                                matches[ref_word.text] = ([], adjusted_confidence, ref_word_id)
                            matches[ref_word.text][0].append(source)

            # Create correction for best match if any found
            if matches:
                best_match, (sources, base_confidence, ref_word_id) = max(matches.items(), key=lambda x: (len(x[1][0]), x[1][1]))

                source_confidence = len(sources) / len(gap.reference_word_ids)
                final_confidence = base_confidence * source_confidence

                self.logger.debug(f"Found match: {word.text} -> {best_match} " f"(confidence: {final_confidence:.2f}, sources: {sources})")

                corrections.append(
                    WordCorrection(
                        original_word=word.text,
                        corrected_word=best_match,
                        segment_index=0,
                        original_position=gap.transcription_position + i,
                        confidence=final_confidence,
                        source=", ".join(sources),
                        reason=f"SoundAlikeHandler: Phonetic match ({final_confidence:.2f} confidence)",
                        alternatives={k: len(v[0]) for k, v in matches.items()},
                        is_deletion=False,
                        reference_positions=reference_positions,
                        length=1,
                        handler="SoundAlikeHandler",
                        word_id=word_id,
                        corrected_word_id=ref_word_id,
                    )
                )

        return corrections

    def _codes_match(self, codes1: Tuple[str, str], codes2: Tuple[str, str]) -> float:
        """Check if two sets of metaphone codes match and return match quality."""
        # Get all non-empty codes
        codes1_set = {c for c in codes1 if c}
        codes2_set = {c for c in codes2 if c}

        if not codes1_set or not codes2_set:
            return 0.0

        best_match = 0.0
        for code1 in codes1_set:
            for code2 in codes2_set:
                # Special case for very short codes (like 'A' for 'you')
                if len(code1) <= 2 or len(code2) <= 2:
                    if code1 == code2:
                        best_match = max(best_match, 1.0)
                    elif code1 in code2 or code2 in code1:
                        best_match = max(best_match, 0.8)
                    elif code1[0] == code2[0]:  # Match first character
                        best_match = max(best_match, 0.7)
                    continue

                # Skip if codes are too different in length
                length_diff = abs(len(code1) - len(code2))
                if length_diff > 3:
                    continue

                # Exact match
                if code1 == code2:
                    best_match = max(best_match, 1.0)
                    continue

                # Similar codes (allow 1-2 character differences)
                if len(code1) >= 2 and len(code2) >= 2:
                    # Compare first N characters where N is min length
                    min_len = min(len(code1), len(code2))

                    # Check for shared characters in any position
                    shared_chars = sum(1 for c in code1 if c in code2)
                    if shared_chars >= min(2, min_len):  # More lenient shared character requirement
                        match_quality = 0.7 + (0.1 * shared_chars / max(len(code1), len(code2)))
                        best_match = max(best_match, match_quality)
                        continue

                    # Compare aligned characters
                    differences = sum(1 for a, b in zip(code1[:min_len], code2[:min_len]) if a != b)
                    if differences <= 2:
                        match_quality = 0.85 - (differences * 0.1)
                        best_match = max(best_match, match_quality)
                        continue

                # Common prefix/suffix match with more lenient threshold
                common_prefix_len = 0
                for a, b in zip(code1, code2):
                    if a != b:
                        break
                    common_prefix_len += 1

                common_suffix_len = 0
                for a, b in zip(code1[::-1], code2[::-1]):
                    if a != b:
                        break
                    common_suffix_len += 1

                if common_prefix_len >= 1 or common_suffix_len >= 1:  # Even more lenient prefix/suffix requirement
                    match_quality = 0.7 + (0.1 * max(common_prefix_len, common_suffix_len))
                    best_match = max(best_match, match_quality)
                    continue

                # Substring match
                if len(code1) >= 2 and len(code2) >= 2:  # More lenient length requirement
                    # Look for shared substrings of length 2 or more
                    for length in range(min(len(code1), len(code2)), 1, -1):
                        for i in range(len(code1) - length + 1):
                            substring = code1[i : i + length]
                            if substring in code2:
                                match_quality = 0.7 + (0.1 * length / max(len(code1), len(code2)))
                                best_match = max(best_match, match_quality)
                                break

        return best_match

    def _get_match_confidence(self, codes1: Tuple[str, str], codes2: Tuple[str, str]) -> float:
        """Calculate confidence score for a metaphone code match."""
        match_quality = self._codes_match(codes1, codes2)
        if match_quality == 0:
            return 0.0

        # Get primary codes (first code of each tuple)
        code1, code2 = codes1[0], codes2[0]

        # Boost confidence for codes that share prefixes
        if code1 and code2 and len(code1) >= 2 and len(code2) >= 2:
            if code1[:2] == code2[:2]:
                match_quality = min(1.0, match_quality + 0.1)

        return match_quality
