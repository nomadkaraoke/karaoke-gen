from typing import List, Tuple, Dict, Any, Optional
import spacy
import logging
import time
import pyphen
import nltk
from nltk.corpus import cmudict
import syllables
from spacy_syllables import SpacySyllables

from karaoke_gen.lyrics_transcriber.types import GapSequence, WordCorrection
from karaoke_gen.lyrics_transcriber.correction.handlers.base import GapCorrectionHandler
from karaoke_gen.lyrics_transcriber.correction.handlers.word_operations import WordOperations

# Try to import preloaders (may not exist in standalone library usage)
try:
    from backend.services.spacy_preloader import get_preloaded_model
    from backend.services.nltk_preloader import get_preloaded_cmudict

    _HAS_PRELOADER = True
except ImportError:
    _HAS_PRELOADER = False


class SyllablesMatchHandler(GapCorrectionHandler):
    """Handles gaps where number of syllables in reference text matches number of syllables in transcription."""

    def __init__(self, logger: Optional[logging.Logger] = None):
        super().__init__(logger)
        self.logger = logger or logging.getLogger(__name__)
        init_start = time.time()

        # Marking SpacySyllables as used to prevent unused import warning
        _ = SpacySyllables

        # Try to use preloaded model first (avoids 60+ second load on Cloud Run)
        if _HAS_PRELOADER:
            preloaded = get_preloaded_model("en_core_web_sm")
            if preloaded is not None:
                self.logger.info("Using preloaded SpaCy model for syllable analysis")
                self.nlp = preloaded
                # Add syllables component if not already present
                if "syllables" not in self.nlp.pipe_names:
                    self.nlp.add_pipe("syllables", after="tagger")
                self._init_nltk_resources()
                init_elapsed = time.time() - init_start
                self.logger.info(f"Initialized SyllablesMatchHandler in {init_elapsed:.2f}s (preloaded)")
                return

        # Fall back to loading model directly
        self.logger.info("Loading SpaCy model for syllable analysis (not preloaded)...")
        try:
            self.nlp = spacy.load("en_core_web_sm")
        except OSError:
            self.logger.info("Language model 'en_core_web_sm' not found. Attempting to download...")
            import subprocess

            try:
                subprocess.check_call(["python", "-m", "spacy", "download", "en_core_web_sm"])
                self.nlp = spacy.load("en_core_web_sm")
                self.logger.info("Successfully downloaded and loaded en_core_web_sm")
            except subprocess.CalledProcessError as e:
                raise OSError(
                    "Language model 'en_core_web_sm' could not be downloaded. "
                    "Please install it manually with: python -m spacy download en_core_web_sm"
                ) from e

        # Add syllables component to pipeline if not already present
        if "syllables" not in self.nlp.pipe_names:
            self.nlp.add_pipe("syllables", after="tagger")

        self._init_nltk_resources()
        init_elapsed = time.time() - init_start
        self.logger.info(f"Initialized SyllablesMatchHandler in {init_elapsed:.2f}s (lazy loaded)")

    def _init_nltk_resources(self):
        """Initialize NLTK resources (Pyphen and CMU dictionary)."""

        # Initialize Pyphen for English
        self.dic = pyphen.Pyphen(lang="en_US")

        # Try to use preloaded cmudict first (avoids 50-100+ second download on Cloud Run)
        if _HAS_PRELOADER:
            preloaded_cmudict = get_preloaded_cmudict()
            if preloaded_cmudict is not None:
                self.logger.debug("Using preloaded NLTK cmudict")
                self.cmudict = preloaded_cmudict
                return

        # Fall back to loading directly
        self.logger.info("Loading NLTK cmudict (not preloaded)...")
        try:
            self.cmudict = cmudict.dict()
        except LookupError:
            nltk.download("cmudict")
            self.cmudict = cmudict.dict()

    def _count_syllables_spacy(self, words: List[str]) -> int:
        """Count syllables using spacy_syllables."""
        text = " ".join(words)
        doc = self.nlp(text)
        total_syllables = sum(token._.syllables_count or 1 for token in doc)
        return total_syllables

    def _count_syllables_pyphen(self, words: List[str]) -> int:
        """Count syllables using pyphen."""
        total_syllables = 0
        for word in words:
            hyphenated = self.dic.inserted(word)
            syllables_count = len(hyphenated.split("-")) if hyphenated else 1
            total_syllables += syllables_count
        return total_syllables

    def _count_syllables_nltk(self, words: List[str]) -> int:
        """Count syllables using NLTK's CMU dictionary."""
        total_syllables = 0
        for word in words:
            word = word.lower()
            if word in self.cmudict:
                syllables_count = len([ph for ph in self.cmudict[word][0] if ph[-1].isdigit()])
                total_syllables += syllables_count
            else:
                total_syllables += 1
        return total_syllables

    def _count_syllables_lib(self, words: List[str]) -> int:
        """Count syllables using the syllables library."""
        total_syllables = 0
        for word in words:
            syllables_count = syllables.estimate(word)
            total_syllables += syllables_count
        return total_syllables

    def _count_syllables(self, words: List[str]) -> List[int]:
        """Count syllables using multiple methods."""
        spacy_count = self._count_syllables_spacy(words)
        pyphen_count = self._count_syllables_pyphen(words)
        nltk_count = self._count_syllables_nltk(words)
        syllables_count = self._count_syllables_lib(words)

        text = " ".join(words)
        self.logger.debug(
            f"Syllable counts for '{text}': spacy={spacy_count}, pyphen={pyphen_count}, nltk={nltk_count}, syllables={syllables_count}"
        )
        return [spacy_count, pyphen_count, nltk_count, syllables_count]

    def can_handle(self, gap: GapSequence, data: Optional[Dict[str, Any]] = None) -> Tuple[bool, Dict[str, Any]]:
        # Must have reference words
        if not gap.reference_word_ids:
            self.logger.debug("No reference word IDs available")
            return False, {}

        # Get word lookup map from data
        if not data or "word_map" not in data:
            self.logger.error("No word_map provided in data")
            return False, {}

        word_map = data["word_map"]

        # Get actual words from word IDs
        gap_words = []
        for word_id in gap.transcribed_word_ids:
            if word_id not in word_map:
                self.logger.error(f"Word ID {word_id} not found in word_map")
                return False, {}
            gap_words.append(word_map[word_id].text)

        # Get syllable counts for gap text using different methods
        gap_syllables = self._count_syllables(gap_words)

        # Check if any reference source has matching syllable count with any method
        for source, ref_word_ids in gap.reference_word_ids.items():
            # Get reference words from word map
            ref_words = []
            for word_id in ref_word_ids:
                if word_id not in word_map:
                    self.logger.error(f"Reference word ID {word_id} not found in word_map")
                    continue
                ref_words.append(word_map[word_id].text)

            if not ref_words:
                continue

            ref_syllables = self._count_syllables(ref_words)

            # If any counting method matches between gap and reference, we can handle it
            if any(gap_count == ref_count for gap_count in gap_syllables for ref_count in ref_syllables):
                self.logger.debug(f"Found matching syllable count in source '{source}'")
                return True, {
                    "gap_syllables": gap_syllables,
                    "matching_source": source,
                    "reference_word_ids": ref_word_ids,
                    "word_map": word_map,
                }

        self.logger.debug("No reference source had matching syllable count")
        return False, {}

    def handle(self, gap: GapSequence, data: Optional[Dict[str, Any]] = None) -> List[WordCorrection]:
        """Handle the gap using syllable matching."""
        if not data:
            can_handle, data = self.can_handle(gap)
            if not can_handle:
                return []

        corrections = []
        matching_source = data["matching_source"]
        reference_word_ids = data["reference_word_ids"]
        word_map = data["word_map"]

        # Get the actual words from word IDs
        gap_words = [word_map[word_id].text for word_id in gap.transcribed_word_ids]
        ref_words = [word_map[word_id].text for word_id in reference_word_ids]

        # Use the centralized method to calculate reference positions
        reference_positions = WordOperations.calculate_reference_positions(gap, [matching_source])

        # Since we matched syllable counts for the entire gap, we should handle all words
        if len(gap_words) > len(ref_words):
            # Multiple transcribed words -> fewer reference words
            # Try to distribute the reference words across the gap words
            words_per_ref = len(gap_words) / len(ref_words)

            for ref_idx, ref_word_id in enumerate(reference_word_ids):
                start_idx = int(ref_idx * words_per_ref)
                end_idx = int((ref_idx + 1) * words_per_ref)

                # Get the group of words to combine
                words_to_combine = gap_words[start_idx:end_idx]
                word_ids_to_combine = gap.transcribed_word_ids[start_idx:end_idx]
                corrections.extend(
                    WordOperations.create_word_combine_corrections(
                        original_words=words_to_combine,
                        reference_word=word_map[ref_word_id].text,
                        original_position=gap.transcription_position + start_idx,
                        source=matching_source,
                        confidence=0.8,
                        combine_reason="Words combined based on syllable match",
                        delete_reason="Word removed as part of syllable match combination",
                        reference_positions=reference_positions,
                        handler="SyllablesMatchHandler",
                        original_word_ids=word_ids_to_combine,
                        corrected_word_id=ref_word_id,
                    )
                )

        elif len(gap_words) < len(ref_words):
            # Single transcribed word -> multiple reference words
            words_per_gap = len(ref_words) / len(gap_words)

            for i, word_id in enumerate(gap.transcribed_word_ids):
                start_idx = int(i * words_per_gap)
                end_idx = int((i + 1) * words_per_gap)
                ref_word_ids_for_split = reference_word_ids[start_idx:end_idx]
                ref_words_for_split = [word_map[ref_id].text for ref_id in ref_word_ids_for_split]

                corrections.extend(
                    WordOperations.create_word_split_corrections(
                        original_word=word_map[word_id].text,
                        reference_words=ref_words_for_split,
                        original_position=gap.transcription_position + i,
                        source=matching_source,
                        confidence=0.8,
                        reason="Split word based on syllable match",
                        reference_positions=reference_positions,
                        handler="SyllablesMatchHandler",
                        original_word_id=word_id,
                        corrected_word_ids=ref_word_ids_for_split,
                    )
                )

        else:
            # One-to-one replacement
            for i, (orig_word_id, ref_word_id) in enumerate(zip(gap.transcribed_word_ids, reference_word_ids)):
                orig_word = word_map[orig_word_id]
                ref_word = word_map[ref_word_id]

                if orig_word.text.lower() != ref_word.text.lower():
                    corrections.append(
                        WordOperations.create_word_replacement_correction(
                            original_word=orig_word.text,
                            corrected_word=ref_word.text,
                            original_position=gap.transcription_position + i,
                            source=matching_source,
                            confidence=0.8,
                            reason=f"Source '{matching_source}' had matching syllable count",
                            reference_positions=reference_positions,
                            handler="SyllablesMatchHandler",
                            original_word_id=orig_word_id,
                            corrected_word_id=ref_word_id,
                        )
                    )

        return corrections
