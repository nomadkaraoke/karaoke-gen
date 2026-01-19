from typing import List, Optional, Tuple, Dict, Any
import string
import Levenshtein
import logging

from karaoke_gen.lyrics_transcriber.types import GapSequence, WordCorrection
from karaoke_gen.lyrics_transcriber.correction.handlers.base import GapCorrectionHandler
from karaoke_gen.lyrics_transcriber.correction.handlers.word_operations import WordOperations


class LevenshteinHandler(GapCorrectionHandler):
    """Handles corrections based on Levenshtein (edit distance) similarity between words.

    This handler looks for words that are similar in spelling to reference words in the same position.
    The similarity calculation includes:
    1. Basic Levenshtein ratio
    2. Bonus for words starting with the same letter
    3. Penalty for words starting with different letters
    4. Bonus for similar length words

    Examples:
        Gap: "wold" (misspelling)
        References:
            genius: ["world"]
            spotify: ["world"]
        Result:
            - Correct "wold" to "world" (high confidence due to small edit distance)

        Gap: "worde" (misspelling)
        References:
            genius: ["world"]
            spotify: ["words"]
        Result:
            - Correct "worde" to "world" (lower confidence due to disagreeing sources)
    """

    def __init__(self, similarity_threshold: float = 0.65, logger: Optional[logging.Logger] = None):
        self.similarity_threshold = similarity_threshold
        self.logger = logger or logging.getLogger(__name__)

    def can_handle(self, gap: GapSequence, data: Optional[Dict[str, Any]] = None) -> Tuple[bool, Dict[str, Any]]:
        """Check if we can handle this gap - we'll try if there are reference words."""
        if not data or "word_map" not in data:
            self.logger.error("No word_map provided in data")
            return False, {}

        word_map = data["word_map"]

        if not gap.reference_word_ids:
            self.logger.debug("No reference words available")
            return False, {}

        if not gap.transcribed_word_ids:
            self.logger.debug("No gap words available")
            return False, {}

        # Check if any word has sufficient similarity to reference
        for i, word_id in enumerate(gap.transcribed_word_ids):
            if word_id not in word_map:
                continue
            word = word_map[word_id]

            for source, ref_word_ids in gap.reference_word_ids.items():
                if i < len(ref_word_ids):
                    ref_word_id = ref_word_ids[i]
                    if ref_word_id not in word_map:
                        continue
                    ref_word = word_map[ref_word_id]

                    similarity = self._get_string_similarity(word.text, ref_word.text)
                    if similarity >= self.similarity_threshold:
                        self.logger.debug(f"Found similar word: '{word.text}' -> '{ref_word.text}' ({similarity:.2f})")
                        return True, {}

        self.logger.debug("No words meet similarity threshold")
        return False, {}

    def handle(self, gap: GapSequence, data: Optional[Dict[str, Any]] = None) -> List[WordCorrection]:
        """Try to correct words based on string similarity."""
        if not data or "word_map" not in data:
            self.logger.error("No word_map provided in data")
            return []

        word_map = data["word_map"]
        corrections = []

        # Process each word in the gap
        for i, word_id in enumerate(gap.transcribed_word_ids):
            if word_id not in word_map:
                continue
            word = word_map[word_id]

            # Skip if word is empty or just punctuation
            if not word.text.strip():
                continue

            # Skip exact matches
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

            # Find matching reference words at this position
            matches: Dict[str, Tuple[List[str], float, str]] = {}  # word -> (sources, similarity, word_id)

            for source, ref_word_ids in gap.reference_word_ids.items():
                if i >= len(ref_word_ids):
                    continue

                ref_word_id = ref_word_ids[i]
                if ref_word_id not in word_map:
                    continue
                ref_word = word_map[ref_word_id]

                similarity = self._get_string_similarity(word.text, ref_word.text)

                if similarity >= self.similarity_threshold:
                    self.logger.debug(f"Found match: '{word.text}' -> '{ref_word.text}' ({similarity:.2f})")
                    if ref_word.text not in matches:
                        matches[ref_word.text] = ([], similarity, ref_word_id)
                    matches[ref_word.text][0].append(source)

            # Create correction for best match if any found
            if matches:
                best_match, (sources, similarity, ref_word_id) = max(
                    matches.items(), key=lambda x: (len(x[1][0]), x[1][1])  # Sort by number of sources, then similarity
                )

                source_confidence = len(sources) / len(gap.reference_word_ids)
                final_confidence = similarity * source_confidence

                # Calculate reference positions
                reference_positions = WordOperations.calculate_reference_positions(gap, anchor_sequences=data.get("anchor_sequences", []))

                self.logger.debug(f"Creating correction: {word.text} -> {best_match} (confidence: {final_confidence})")
                corrections.append(
                    WordCorrection(
                        original_word=word.text,
                        corrected_word=best_match,
                        segment_index=0,
                        original_position=gap.transcription_position + i,
                        confidence=final_confidence,
                        source=", ".join(sources),
                        reason=f"String similarity ({final_confidence:.2f})",
                        alternatives={k: len(v[0]) for k, v in matches.items()},
                        is_deletion=False,
                        reference_positions=reference_positions,
                        length=1,
                        handler="LevenshteinHandler",
                        word_id=word_id,
                        corrected_word_id=ref_word_id,
                    )
                )

        return corrections

    def _clean_word(self, word: str) -> str:
        """Remove punctuation and standardize for comparison."""
        return word.strip().lower().strip(string.punctuation)

    def _get_string_similarity(self, word1: str, word2: str) -> float:
        """Calculate string similarity using Levenshtein ratio with adjustments."""
        # Clean words
        w1, w2 = self._clean_word(word1), self._clean_word(word2)
        if not w1 or not w2:
            return 0.0

        # Calculate Levenshtein ratio
        similarity = Levenshtein.ratio(w1, w2)

        # Boost similarity for words starting with the same letter
        if w1[0] == w2[0]:
            similarity = (similarity + 1) / 2
        else:
            # Penalize words starting with different letters
            similarity = similarity * 0.9

        # Boost for similar length words
        length_ratio = min(len(w1), len(w2)) / max(len(w1), len(w2))
        similarity = (similarity + length_ratio) / 2

        return similarity
