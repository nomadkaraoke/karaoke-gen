import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import logging
from tqdm import tqdm
from functools import partial
from pathlib import Path
import json
import hashlib

# Tracing utilities (optional - gracefully handles missing dependency)
from karaoke_gen.lyrics_transcriber.utils.tracing import create_span, add_span_attribute, add_span_event

from karaoke_gen.lyrics_transcriber.types import LyricsData, PhraseScore, PhraseType, AnchorSequence, GapSequence, ScoredAnchor, TranscriptionResult, Word
from karaoke_gen.lyrics_transcriber.correction.phrase_analyzer import PhraseAnalyzer
from karaoke_gen.lyrics_transcriber.correction.text_utils import clean_text
from karaoke_gen.lyrics_transcriber.utils.word_utils import WordUtils


class AnchorSequenceTimeoutError(Exception):
    """Raised when anchor sequence computation exceeds timeout."""
    pass


class AnchorSequenceFinder:
    """Identifies and manages anchor sequences between transcribed and reference lyrics."""

    def __init__(
        self,
        cache_dir: Union[str, Path],
        min_sequence_length: int = 3,
        min_sources: int = 1,
        timeout_seconds: int = 600,  # 10 minutes default timeout
        max_iterations_per_ngram: int = 1000,  # Maximum iterations for while loop
        progress_check_interval: int = 50,  # Check progress every N iterations
        logger: Optional[logging.Logger] = None,
    ):
        init_start = time.time()
        self.min_sequence_length = min_sequence_length
        self.min_sources = min_sources
        self.timeout_seconds = timeout_seconds
        self.max_iterations_per_ngram = max_iterations_per_ngram
        self.progress_check_interval = progress_check_interval
        self.logger = logger or logging.getLogger(__name__)

        self.logger.info("Initializing AnchorSequenceFinder...")
        self.phrase_analyzer = PhraseAnalyzer(logger=self.logger)
        self.used_positions = {}

        # Initialize cache directory
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        init_elapsed = time.time() - init_start
        self.logger.info(f"Initialized AnchorSequenceFinder in {init_elapsed:.2f}s (cache: {self.cache_dir}, timeout: {timeout_seconds}s)")

    def _check_timeout(self, start_time: float, operation_name: str = "operation"):
        """Check if timeout has occurred and raise exception if so."""
        if self.timeout_seconds > 0:
            elapsed_time = time.time() - start_time
            if elapsed_time > self.timeout_seconds:
                raise AnchorSequenceTimeoutError(f"{operation_name} exceeded {self.timeout_seconds} seconds (elapsed: {elapsed_time:.1f}s)")

    def _clean_text(self, text: str) -> str:
        """Clean text by removing punctuation and normalizing whitespace."""
        # self.logger.debug(f"_clean_text called with text length: {len(text)}")
        return clean_text(text)

    def _find_ngrams(self, words: List[str], n: int) -> List[Tuple[List[str], int]]:
        """Generate n-grams with their starting positions."""
        # self.logger.debug(f"_find_ngrams called with {len(words)} words, n={n}")
        return [(words[i : i + n], i) for i in range(len(words) - n + 1)]

    def _build_ngram_index(
        self, 
        references: Dict[str, List[str]], 
        n: int
    ) -> Dict[Tuple[str, ...], Dict[str, List[int]]]:
        """
        Build a hash-based index mapping n-grams to their positions in each reference.
        
        Args:
            references: Dict mapping source names to lists of cleaned words
            n: The n-gram length to index
            
        Returns:
            Dict mapping n-gram tuples to {source: [positions]} dicts
        """
        index: Dict[Tuple[str, ...], Dict[str, List[int]]] = {}
        
        for source, words in references.items():
            for i in range(len(words) - n + 1):
                ngram_tuple = tuple(words[i:i + n])
                if ngram_tuple not in index:
                    index[ngram_tuple] = {}
                if source not in index[ngram_tuple]:
                    index[ngram_tuple][source] = []
                index[ngram_tuple][source].append(i)
        
        return index

    def _find_matching_sources_indexed(
        self, 
        ngram: List[str], 
        ngram_index: Dict[Tuple[str, ...], Dict[str, List[int]]]
    ) -> Dict[str, int]:
        """
        Find which sources contain the given n-gram using pre-built index (O(1) lookup).
        
        Args:
            ngram: List of words to find
            ngram_index: Pre-built index from _build_ngram_index()
            
        Returns:
            Dict mapping source names to first unused position
        """
        matches = {}
        ngram_tuple = tuple(ngram)
        
        # O(1) lookup in the index
        if ngram_tuple not in ngram_index:
            return matches
        
        source_positions = ngram_index[ngram_tuple]
        
        # For each source that contains this n-gram, find first unused position
        for source, positions in source_positions.items():
            used = self.used_positions.get(source, set())
            for pos in positions:
                if pos not in used:
                    matches[source] = pos
                    break
        
        return matches

    def _find_matching_sources(self, ngram: List[str], references: Dict[str, List[str]], n: int) -> Dict[str, int]:
        """Find which sources contain the given n-gram and at what positions (legacy O(n) method)."""
        # self.logger.debug(f"_find_matching_sources called for ngram: '{' '.join(ngram)}'")
        matches = {}
        all_positions = {source: [] for source in references}

        # First, find all positions in each source
        for source, words in references.items():
            for i in range(len(words) - n + 1):
                if words[i : i + n] == ngram:
                    all_positions[source].append(i)

        # Then, try to find an unused position for each source
        for source, positions in all_positions.items():
            used = self.used_positions.get(source, set())
            # Try each position in order
            for pos in positions:
                if pos not in used:
                    matches[source] = pos
                    break

        return matches

    def _filter_used_positions(self, matches: Dict[str, int]) -> Dict[str, int]:
        """Filter out positions that have already been used.

        Args:
            matches: Dict mapping source IDs to positions

        Returns:
            Dict mapping source IDs to unused positions
        """
        self.logger.debug(f"_filter_used_positions called with {len(matches)} matches")
        return {source: pos for source, pos in matches.items() if pos not in self.used_positions.get(source, set())}

    def _create_anchor(
        self, ngram: List[str], trans_pos: int, matching_sources: Dict[str, int], total_sources: int
    ) -> Optional[AnchorSequence]:
        """Create an anchor sequence if it meets the minimum sources requirement."""
        self.logger.debug(f"_create_anchor called for ngram: '{' '.join(ngram)}' at position {trans_pos}")
        if len(matching_sources) >= self.min_sources:
            confidence = len(matching_sources) / total_sources
            # Use new API to avoid setting _words field
            anchor = AnchorSequence(
                id=WordUtils.generate_id(),
                transcribed_word_ids=[WordUtils.generate_id() for _ in ngram],
                transcription_position=trans_pos,
                reference_positions=matching_sources,
                reference_word_ids={source: [WordUtils.generate_id() for _ in ngram] 
                                   for source in matching_sources.keys()},
                confidence=confidence
            )
            self.logger.debug(f"Found anchor sequence: '{' '.join(ngram)}' (confidence: {confidence:.2f})")
            return anchor
        return None

    def _get_cache_key(self, transcribed: str, references: Dict[str, LyricsData], transcription_result: TranscriptionResult) -> str:
        """Generate a unique cache key for the input combination."""
        # Create a string that uniquely identifies the inputs, including word IDs
        ref_texts = []
        for source, lyrics in sorted(references.items()):
            # Include both text and ID for each word to ensure cache uniqueness
            words_with_ids = [f"{w.text}:{w.id}" for s in lyrics.segments for w in s.words]
            ref_texts.append(f"{source}:{','.join(words_with_ids)}")

        # Also include transcription word IDs to ensure complete matching
        trans_words_with_ids = [f"{w.text}:{w.id}" for s in transcription_result.result.segments for w in s.words]

        input_str = f"{transcribed}|" f"{','.join(trans_words_with_ids)}|" f"{','.join(ref_texts)}"
        return hashlib.md5(input_str.encode()).hexdigest()

    def _save_to_cache(self, cache_path: Path, anchors: List[ScoredAnchor]) -> None:
        """Save results to cache file."""
        self.logger.debug(f"Saving to cache: {cache_path}")
        # Convert to dictionary format that matches the expected loading format
        cache_data = [{"anchor": anchor.anchor.to_dict(), "phrase_score": anchor.phrase_score.to_dict()} for anchor in anchors]
        with open(cache_path, "w") as f:
            json.dump(cache_data, f, indent=2)

    def _load_from_cache(self, cache_path: Path) -> Optional[List[ScoredAnchor]]:
        """Load results from cache if available."""
        try:
            self.logger.debug(f"Attempting to load from cache: {cache_path}")
            with open(cache_path, "r") as f:
                cached_data = json.load(f)

            self.logger.info("Loading anchors from cache")
            try:
                # Log the raw dictionary data instead of the object
                # if cached_data:
                #     self.logger.debug(f"Cached data structure: {json.dumps(cached_data[0], indent=2)}")

                # Convert cached data back to ScoredAnchor objects
                anchors = []
                for data in cached_data:
                    if "anchor" not in data or "phrase_score" not in data:
                        raise KeyError("Missing required keys: anchor, phrase_score")

                    anchor = AnchorSequence.from_dict(data["anchor"])
                    phrase_score = PhraseScore.from_dict(data["phrase_score"])
                    anchors.append(ScoredAnchor(anchor=anchor, phrase_score=phrase_score))

                return anchors

            except KeyError as e:
                self.logger.error(f"Cache format mismatch. Missing key: {e}")
                # Log the raw data for debugging
                if cached_data:
                    self.logger.error(f"First cached anchor data: {json.dumps(cached_data[0], indent=2)}")
                self.logger.error("Expected keys: anchor, phrase_score")
                self.logger.warning(f"Cache format mismatch: {e}. Recomputing.")
                return None

        except (FileNotFoundError, json.JSONDecodeError) as e:
            self.logger.debug(f"Cache miss or invalid cache file: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Unexpected error loading cache: {type(e).__name__}: {e}")
            return None

    def _process_ngram_length_no_state(
        self,
        n: int,
        trans_words: List[str],
        all_words: List[Word],
        ref_texts_clean: Dict[str, List[str]],
        ref_words: Dict[str, List[Word]],
        min_sources: int,
    ) -> List[AnchorSequence]:
        """Process a single n-gram length without modifying shared state (thread-safe).

        This version doesn't track used positions - overlap filtering happens later
        in _remove_overlapping_sequences. This allows parallel processing of different
        n-gram lengths.
        """
        candidate_anchors = []

        # Build hash-based index for O(1) lookups
        ngram_index = self._build_ngram_index(ref_texts_clean, n)

        # Generate n-grams from transcribed text
        trans_ngrams = self._find_ngrams(trans_words, n)

        # Single pass through all transcription n-grams
        for ngram, trans_pos in trans_ngrams:
            # Use indexed lookup (O(1) instead of O(n))
            ngram_tuple = tuple(ngram)
            if ngram_tuple not in ngram_index:
                continue

            # Find matches in all sources (no used_positions check - handled later)
            matches = {}
            source_positions = ngram_index[ngram_tuple]
            for source, positions in source_positions.items():
                if positions:
                    matches[source] = positions[0]  # Take first position

            if len(matches) >= min_sources:
                # Get Word IDs for transcribed words
                transcribed_word_ids = [w.id for w in all_words[trans_pos : trans_pos + n]]

                # Get Word IDs for reference words
                reference_word_ids = {
                    source: [w.id for w in ref_words[source][pos : pos + n]]
                    for source, pos in matches.items()
                }

                anchor = AnchorSequence(
                    id=WordUtils.generate_id(),
                    transcribed_word_ids=transcribed_word_ids,
                    transcription_position=trans_pos,
                    reference_positions=matches,
                    reference_word_ids=reference_word_ids,
                    confidence=len(matches) / len(ref_texts_clean),
                )
                candidate_anchors.append(anchor)

        return candidate_anchors

    def _process_ngram_length(
        self,
        n: int,
        trans_words: List[str],
        all_words: List[Word],
        ref_texts_clean: Dict[str, List[str]],
        ref_words: Dict[str, List[Word]],
        min_sources: int,
    ) -> List[AnchorSequence]:
        """Process a single n-gram length to find matching sequences using hash-based index."""
        self.logger.debug(f"🔍 N-GRAM {n}: Starting processing with {len(trans_words)} transcription words")
        
        candidate_anchors = []
        used_trans_positions: Set[int] = set()
        
        # Build hash-based index for O(1) lookups
        ngram_index = self._build_ngram_index(ref_texts_clean, n)
        self.logger.debug(f"🔍 N-GRAM {n}: Built index with {len(ngram_index)} unique n-grams")

        # Generate n-grams from transcribed text
        trans_ngrams = self._find_ngrams(trans_words, n)
        self.logger.debug(f"🔍 N-GRAM {n}: Processing {len(trans_ngrams)} transcription n-grams")

        # Single pass through all transcription n-grams
        for ngram, trans_pos in trans_ngrams:
            # Skip if we've already used this transcription position
            if trans_pos in used_trans_positions:
                continue

            # Use indexed lookup (O(1) instead of O(n))
            matches = self._find_matching_sources_indexed(ngram, ngram_index)
            
            if len(matches) >= min_sources:
                # Get Word IDs for transcribed words
                transcribed_word_ids = [w.id for w in all_words[trans_pos : trans_pos + n]]

                # Get Word IDs for reference words
                reference_word_ids = {
                    source: [w.id for w in ref_words[source][pos : pos + n]] 
                    for source, pos in matches.items()
                }

                # Mark transcription position as used
                used_trans_positions.add(trans_pos)
                
                # Mark reference positions as used
                for source, pos in matches.items():
                    if source not in self.used_positions:
                        self.used_positions[source] = set()
                    self.used_positions[source].add(pos)

                anchor = AnchorSequence(
                    id=WordUtils.generate_id(),
                    transcribed_word_ids=transcribed_word_ids,
                    transcription_position=trans_pos,
                    reference_positions=matches,
                    reference_word_ids=reference_word_ids,
                    confidence=len(matches) / len(ref_texts_clean),
                )
                candidate_anchors.append(anchor)
        
        self.logger.debug(f"🔍 N-GRAM {n}: Found {len(candidate_anchors)} anchors")
        return candidate_anchors

    def find_anchors(
        self,
        transcribed: str,
        references: Dict[str, LyricsData],
        transcription_result: TranscriptionResult,
    ) -> List[ScoredAnchor]:
        """Find anchor sequences that appear in both transcription and references with timeout protection."""
        start_time = time.time()

        # Wrap entire operation in a tracing span
        with create_span("anchor_search.find_anchors", {
            "transcription.text_length": len(transcribed),
            "reference.source_count": len(references),
            "timeout_seconds": self.timeout_seconds,
        }) as span:
            return self._find_anchors_impl(transcribed, references, transcription_result, start_time, span)

    def _find_anchors_impl(
        self,
        transcribed: str,
        references: Dict[str, LyricsData],
        transcription_result: TranscriptionResult,
        start_time: float,
        span,
    ) -> List[ScoredAnchor]:
        """Internal implementation of find_anchors wrapped in tracing span."""
        try:
            self.logger.info(f"🔍 ANCHOR SEARCH: Starting find_anchors with timeout {self.timeout_seconds}s")
            self.logger.info(f"🔍 ANCHOR SEARCH: Transcribed text length: {len(transcribed)}")
            self.logger.info(f"🔍 ANCHOR SEARCH: Reference sources: {list(references.keys())}")
            
            cache_key = self._get_cache_key(transcribed, references, transcription_result)
            cache_path = self.cache_dir / f"anchors_{cache_key}.json"
            self.logger.info(f"🔍 ANCHOR SEARCH: Cache key: {cache_key}")

            # Try to load from cache
            self.logger.info(f"🔍 ANCHOR SEARCH: Checking cache at {cache_path}")
            if cached_data := self._load_from_cache(cache_path):
                self.logger.info("🔍 ANCHOR SEARCH: ✅ Cache hit! Loading anchors from cache")
                if span:
                    span.set_attribute("cache_hit", True)
                    span.set_attribute("cached_anchors", len(cached_data))
                    span.set_attribute("duration_seconds", time.time() - start_time)
                try:
                    # Convert cached_data to dictionary before logging
                    if cached_data:
                        first_anchor = {"anchor": cached_data[0].anchor.to_dict(), "phrase_score": cached_data[0].phrase_score.to_dict()}
                    return cached_data
                except Exception as e:
                    self.logger.error(f"🔍 ANCHOR SEARCH: ❌ Error loading cache: {type(e).__name__}: {e}")
                    if cached_data:
                        try:
                            first_anchor = {"anchor": cached_data[0].anchor.to_dict(), "phrase_score": cached_data[0].phrase_score.to_dict()}
                            self.logger.error(f"First cached anchor data: {json.dumps(first_anchor, indent=2)}")
                        except:
                            self.logger.error("Could not serialize first cached anchor for logging")

            # If not in cache or cache format invalid, perform the computation
            self.logger.info(f"🔍 ANCHOR SEARCH: Cache miss - computing anchors")
            
            # Reset used positions for fresh computation
            self.used_positions = {}

            # Check timeout before starting computation
            self._check_timeout(start_time, "anchor computation initialization")
            self.logger.info(f"🔍 ANCHOR SEARCH: ✅ Timeout check passed - initialization")

            # Get all words from transcription
            self.logger.info(f"🔍 ANCHOR SEARCH: Extracting words from transcription result...")
            all_words = []
            for segment in transcription_result.result.segments:
                all_words.extend(segment.words)
            self.logger.info(f"🔍 ANCHOR SEARCH: ✅ Extracted {len(all_words)} words from transcription")

            # Clean and split texts
            self.logger.info(f"🔍 ANCHOR SEARCH: Cleaning transcription words...")
            trans_words = [self._clean_text(w.text) for w in all_words]
            self.logger.info(f"🔍 ANCHOR SEARCH: ✅ Cleaned {len(trans_words)} transcription words")
            
            self.logger.info(f"🔍 ANCHOR SEARCH: Processing reference sources...")
            ref_texts_clean = {
                source: self._clean_text(" ".join(w.text for s in lyrics.segments for w in s.words)).split()
                for source, lyrics in references.items()
            }
            ref_words = {source: [w for s in lyrics.segments for w in s.words] for source, lyrics in references.items()}
            
            for source, words in ref_texts_clean.items():
                self.logger.info(f"🔍 ANCHOR SEARCH: Reference '{source}': {len(words)} words")

            # Check timeout after preprocessing
            self._check_timeout(start_time, "anchor computation preprocessing")
            self.logger.info(f"🔍 ANCHOR SEARCH: ✅ Timeout check passed - preprocessing")

            # Filter out very short reference sources for n-gram length calculation
            self.logger.info(f"🔍 ANCHOR SEARCH: Calculating n-gram lengths...")
            valid_ref_lengths = [
                len(words) for words in ref_texts_clean.values()
                if len(words) >= self.min_sequence_length
            ]

            if not valid_ref_lengths:
                self.logger.warning("🔍 ANCHOR SEARCH: ❌ No reference sources long enough for anchor detection")
                return []

            # Calculate max length using only valid reference sources
            max_length = min(len(trans_words), min(valid_ref_lengths))
            n_gram_lengths = range(max_length, self.min_sequence_length - 1, -1)
            self.logger.info(f"🔍 ANCHOR SEARCH: N-gram lengths to process: {list(n_gram_lengths)} (max_length: {max_length})")

            # Process n-gram lengths in parallel with timeout
            self.logger.info(f"🔍 ANCHOR SEARCH: Setting up parallel processing...")
            process_length_partial = partial(
                self._process_ngram_length,
                trans_words=trans_words,
                all_words=all_words,  # Pass the Word objects
                ref_texts_clean=ref_texts_clean,
                ref_words=ref_words,
                min_sources=self.min_sources,
            )

            # Process n-gram lengths in parallel for better performance
            # The overlap filtering at the end handles deduplication, so we don't
            # need to track used_positions during processing
            candidate_anchors = []

            # Check timeout before processing
            self._check_timeout(start_time, "n-gram processing start")

            # Determine parallelization strategy
            import os
            from concurrent.futures import ThreadPoolExecutor, as_completed

            # Use parallel processing by default, can be disabled via env var
            use_parallel = os.getenv("ANCHOR_SEARCH_SEQUENTIAL", "0").lower() not in {"1", "true", "yes"}
            max_workers = int(os.getenv("ANCHOR_SEARCH_WORKERS", "4"))

            if use_parallel and len(n_gram_lengths) > 1:
                self.logger.info(f"🔍 ANCHOR SEARCH: Starting PARALLEL n-gram processing ({len(n_gram_lengths)} lengths, {max_workers} workers)")

                # Process in parallel - each n-gram length is independent
                # since we don't track used_positions during processing
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    # Submit all tasks
                    future_to_n = {
                        executor.submit(
                            self._process_ngram_length_no_state,
                            n, trans_words, all_words, ref_texts_clean, ref_words, self.min_sources
                        ): n
                        for n in n_gram_lengths
                    }

                    completed = 0
                    last_progress_log_time = time.time()
                    progress_log_interval = 30  # Log every 30 seconds

                    for future in as_completed(future_to_n):
                        n = future_to_n[future]
                        completed += 1

                        # Time-based progress logging (every 30 seconds)
                        current_time = time.time()
                        if current_time - last_progress_log_time >= progress_log_interval:
                            elapsed = current_time - start_time
                            progress_pct = (completed / len(n_gram_lengths)) * 100
                            self.logger.info(
                                f"🔍 ANCHOR SEARCH: Progress {completed}/{len(n_gram_lengths)} "
                                f"({progress_pct:.1f}%) - {elapsed:.1f}s elapsed"
                            )
                            last_progress_log_time = current_time

                        # Check timeout periodically
                        if self.timeout_seconds > 0:
                            elapsed_time = time.time() - start_time
                            if elapsed_time > self.timeout_seconds:
                                self.logger.warning(f"🔍 ANCHOR SEARCH: ⏰ Timeout reached, stopping ({completed}/{len(n_gram_lengths)} completed)")
                                # Cancel remaining futures
                                for f in future_to_n.keys():
                                    f.cancel()
                                break

                        try:
                            anchors = future.result()
                            candidate_anchors.extend(anchors)
                            if completed % 20 == 0:
                                self.logger.debug(f"🔍 ANCHOR SEARCH: Progress {completed}/{len(n_gram_lengths)} lengths processed")
                        except Exception as e:
                            self.logger.warning(f"🔍 ANCHOR SEARCH: ⚠️ n-gram length {n} failed: {str(e)}")
            else:
                # Sequential fallback
                self.logger.info(f"🔍 ANCHOR SEARCH: Starting sequential n-gram processing ({len(n_gram_lengths)} lengths)")

                batch_size = 10
                batch_results = []

                for i, n in enumerate(n_gram_lengths):
                    try:
                        # Check timeout periodically
                        if self.timeout_seconds > 0:
                            elapsed_time = time.time() - start_time
                            if elapsed_time > self.timeout_seconds:
                                self.logger.warning(f"🔍 ANCHOR SEARCH: ⏰ Timeout reached at n-gram {n}, stopping")
                                break

                        anchors = self._process_ngram_length(
                            n, trans_words, all_words, ref_texts_clean, ref_words, self.min_sources
                        )
                        candidate_anchors.extend(anchors)

                        # Batch logging
                        batch_results.append((n, len(anchors)))

                        # Log progress every batch_size results or on the last result
                        if (i + 1) % batch_size == 0 or (i + 1) == len(n_gram_lengths):
                            total_anchors_in_batch = sum(anchor_count for _, anchor_count in batch_results)
                            n_gram_ranges = [str(ng) for ng, _ in batch_results]
                            range_str = f"{n_gram_ranges[0]}-{n_gram_ranges[-1]}" if len(n_gram_ranges) > 1 else n_gram_ranges[0]
                            self.logger.debug(f"🔍 ANCHOR SEARCH: Completed n-gram lengths {range_str} - found {total_anchors_in_batch} anchors")
                            batch_results = []

                    except Exception as e:
                        self.logger.warning(f"🔍 ANCHOR SEARCH: ⚠️ n-gram length {n} failed: {str(e)}")
                        batch_results.append((n, 0))
                        continue

            self.logger.info(f"🔍 ANCHOR SEARCH: ✅ Found {len(candidate_anchors)} candidate anchors in {time.time() - start_time:.1f}s")
            
            # Check timeout before expensive filtering operation
            self._check_timeout(start_time, "overlap filtering start")
            self.logger.info(f"🔍 ANCHOR SEARCH: 🔄 Starting overlap filtering...")
            
            filtered_anchors = self._remove_overlapping_sequences(candidate_anchors, transcribed, transcription_result)
            self.logger.info(f"🔍 ANCHOR SEARCH: ✅ Filtering completed - {len(filtered_anchors)} anchors before gap extension")

            # Extend anchors into single-word gaps
            filtered_anchors = self._extend_anchors_into_single_word_gaps(
                filtered_scored=filtered_anchors,
                all_words=all_words,
                ref_texts_clean=ref_texts_clean,
                ref_words=ref_words,
            )
            self.logger.info(f"🔍 ANCHOR SEARCH: ✅ Gap extension completed - {len(filtered_anchors)} final anchors")

            # Save to cache
            self.logger.info(f"🔍 ANCHOR SEARCH: 💾 Saving results to cache...")
            self._save_to_cache(cache_path, filtered_anchors)
            
            total_time = time.time() - start_time
            self.logger.info(f"🔍 ANCHOR SEARCH: 🎉 Anchor sequence computation completed successfully in {total_time:.1f}s")

            # Add span attributes for metrics
            if span:
                span.set_attribute("candidate_anchors", len(candidate_anchors))
                span.set_attribute("filtered_anchors", len(filtered_anchors))
                span.set_attribute("duration_seconds", total_time)
                span.set_attribute("cache_hit", False)

            return filtered_anchors
            
        except AnchorSequenceTimeoutError:
            elapsed_time = time.time() - start_time
            self.logger.error(f"🔍 ANCHOR SEARCH: ⏰ TIMEOUT after {elapsed_time:.1f}s (limit: {self.timeout_seconds}s)")
            raise
        except Exception as e:
            elapsed_time = time.time() - start_time
            self.logger.error(f"🔍 ANCHOR SEARCH: ❌ FAILED after {elapsed_time:.1f}s: {str(e)}")
            self.logger.error(f"🔍 ANCHOR SEARCH: Exception type: {type(e).__name__}")
            import traceback
            self.logger.error(f"🔍 ANCHOR SEARCH: Traceback: {traceback.format_exc()}")
            raise
        finally:
            # No cleanup needed - span is managed by context manager in find_anchors()
            pass

    def _score_sequence(self, words: List[str], context: str) -> PhraseScore:
        """Score a sequence based on its phrase quality"""
        self.logger.debug(f"_score_sequence called for: '{' '.join(words)}'")
        return self.phrase_analyzer.score_phrase(words, context)

    def _get_sequence_priority(self, scored_anchor: ScoredAnchor) -> Tuple[float, float, float, float, int]:
        """Get priority tuple for sorting sequences.

        Returns tuple of:
        - Number of sources matched (higher is better)
        - Length bonus (length * 0.2) to favor longer sequences
        - Break score (higher is better)
        - Total score (higher is better)
        - Negative position (earlier is better)

        Position bonus: Add 1.0 to total score for sequences at position 0
        """
        # self.logger.debug(f"_get_sequence_priority called for anchor: '{scored_anchor.anchor.text}'")
        position_bonus = 1.0 if scored_anchor.anchor.transcription_position == 0 else 0.0
        length_bonus = len(scored_anchor.anchor.transcribed_word_ids) * 0.2  # Changed from words to transcribed_word_ids

        return (
            len(scored_anchor.anchor.reference_positions),  # More sources is better
            length_bonus,  # Longer sequences preferred
            scored_anchor.phrase_score.natural_break_score,  # Better breaks preferred
            scored_anchor.phrase_score.total_score + position_bonus,  # Add bonus for position 0
            -scored_anchor.anchor.transcription_position,  # Earlier positions preferred
        )

    def _sequences_overlap(self, seq1: AnchorSequence, seq2: AnchorSequence) -> bool:
        """Check if two sequences overlap in either transcription or references.

        Args:
            seq1: First sequence
            seq2: Second sequence

        Returns:
            True if sequences overlap in transcription or share any reference positions
        """
        # Check transcription overlap
        seq1_trans_range = range(
            seq1.transcription_position, seq1.transcription_position + len(seq1.transcribed_word_ids)
        )  # Changed from words
        seq2_trans_range = range(
            seq2.transcription_position, seq2.transcription_position + len(seq2.transcribed_word_ids)
        )  # Changed from words
        trans_overlap = bool(set(seq1_trans_range) & set(seq2_trans_range))

        # Check reference overlap - only consider positions in shared sources
        shared_sources = set(seq1.reference_positions.keys()) & set(seq2.reference_positions.keys())
        ref_overlap = any(seq1.reference_positions[source] == seq2.reference_positions[source] for source in shared_sources)

        return trans_overlap or ref_overlap

    def _remove_overlapping_sequences(
        self,
        anchors: List[AnchorSequence],
        context: str,
        transcription_result: TranscriptionResult,
    ) -> List[ScoredAnchor]:
        """Remove overlapping sequences using phrase analysis with timeout protection."""
        self.logger.info(f"🔍 FILTERING: Starting overlap removal for {len(anchors)} anchors")
        
        if not anchors:
            self.logger.info(f"🔍 FILTERING: No anchors to process")
            return []

        self.logger.info(f"🔍 FILTERING: Scoring {len(anchors)} anchors")

        # Create word map for scoring
        word_map = {w.id: w for s in transcription_result.result.segments for w in s.words}
        self.logger.debug(f"🔍 FILTERING: Created word map with {len(word_map)} words")

        # Add word map to each anchor for scoring
        for i, anchor in enumerate(anchors):
            # For backwards compatibility, only add transcribed_words if all IDs exist in word_map
            try:
                anchor.transcribed_words = [word_map[word_id] for word_id in anchor.transcribed_word_ids]
                # Also set _words for backwards compatibility with text display
                anchor._words = [word_map[word_id].text for word_id in anchor.transcribed_word_ids]
            except KeyError:
                # This can happen in tests using backwards compatible constructors
                # Create dummy Word objects with the text from _words if available
                if hasattr(anchor, '_words') and anchor._words is not None:
                    from karaoke_gen.lyrics_transcriber.types import Word
                    from karaoke_gen.lyrics_transcriber.utils.word_utils import WordUtils
                    anchor.transcribed_words = [
                        Word(
                            id=word_id,
                            text=text,
                            start_time=i * 1.0,
                            end_time=(i + 1) * 1.0,
                            confidence=1.0
                        )
                        for i, (word_id, text) in enumerate(zip(anchor.transcribed_word_ids, anchor._words))
                    ]
                else:
                    # Create generic word objects for scoring
                    from karaoke_gen.lyrics_transcriber.types import Word
                    anchor.transcribed_words = [
                        Word(
                            id=word_id,
                            text=f"word_{i}",
                            start_time=i * 1.0,
                            end_time=(i + 1) * 1.0,
                            confidence=1.0
                        )
                        for i, word_id in enumerate(anchor.transcribed_word_ids)
                    ]

        start_time = time.time()

        # Score anchors sequentially using simple rule-based scoring
        # (Avoids expensive spaCy NLP and works in cloud environments)
        scored_anchors = []
        self.logger.debug(f"🔍 FILTERING: Scoring {len(anchors)} anchors sequentially")

        for i, anchor in enumerate(anchors):
            try:
                # Simple rule-based scoring based on anchor properties
                phrase_score = self._simple_score_anchor(anchor)
                scored_anchors.append(ScoredAnchor(anchor=anchor, phrase_score=phrase_score))
            except Exception as e:
                # Fallback to default score on error
                self.logger.debug(f"🔍 FILTERING: Scoring failed for anchor {i}: {e}")
                phrase_score = PhraseScore(
                    phrase_type=PhraseType.COMPLETE,
                    natural_break_score=1.0,
                    length_score=1.0
                )
                scored_anchors.append(ScoredAnchor(anchor=anchor, phrase_score=phrase_score))

        scoring_time = time.time() - start_time
        self.logger.debug(f"🔍 FILTERING: Scoring completed in {scoring_time:.2f}s, scored {len(scored_anchors)} anchors")

        # Sort anchors by priority (highest first)
        self.logger.debug(f"🔍 FILTERING: Sorting anchors by priority...")
        scored_anchors.sort(key=self._get_sequence_priority, reverse=True)

        # O(N) overlap filtering using covered positions set
        self.logger.debug(f"🔍 FILTERING: Filtering {len(scored_anchors)} overlapping sequences")
        filtered_scored = []
        covered_positions: Set[int] = set()

        for scored_anchor in scored_anchors:
            anchor = scored_anchor.anchor
            start_pos = anchor.transcription_position
            end_pos = start_pos + anchor.length
            
            # Check if any position in this anchor's range is already covered
            anchor_positions = set(range(start_pos, end_pos))
            if not anchor_positions & covered_positions:  # No overlap with covered
                filtered_scored.append(scored_anchor)
                covered_positions.update(anchor_positions)

        self.logger.debug(f"🔍 FILTERING: Kept {len(filtered_scored)} non-overlapping anchors out of {len(scored_anchors)}")
        return filtered_scored

    def _simple_score_anchor(self, anchor: AnchorSequence) -> PhraseScore:
        """
        Simple rule-based scoring for anchors without expensive NLP.
        
        Scoring criteria:
        - Longer sequences are preferred (length_score)
        - Sequences matching more reference sources are preferred (natural_break_score)
        - All sequences treated as COMPLETE type for simplicity
        """
        # Length score: normalize to 0-1 range (3-15 words typical)
        length = anchor.length
        length_score = min(1.0, (length - 2) / 10.0)  # 3 words = 0.1, 12 words = 1.0
        
        # Source match score: more sources = higher score
        num_sources = len(anchor.reference_positions)
        natural_break_score = min(1.0, num_sources / 3.0)  # 1 source = 0.33, 3+ sources = 1.0
        
        return PhraseScore(
            phrase_type=PhraseType.COMPLETE,
            natural_break_score=natural_break_score,
            length_score=length_score
        )

    @staticmethod
    def _score_anchor_static(anchor: AnchorSequence, context: str) -> ScoredAnchor:
        """Static version of _score_anchor for multiprocessing compatibility."""
        # Create analyzer only once per process
        if not hasattr(AnchorSequenceFinder._score_anchor_static, "_phrase_analyzer"):
            AnchorSequenceFinder._score_anchor_static._phrase_analyzer = PhraseAnalyzer(logger=logging.getLogger(__name__))

        # Get the words from the transcribed word IDs
        # We need to pass in the actual words for scoring
        words = [w.text for w in anchor.transcribed_words]  # This needs to be passed in

        phrase_score = AnchorSequenceFinder._score_anchor_static._phrase_analyzer.score_phrase(words, context)
        return ScoredAnchor(anchor=anchor, phrase_score=phrase_score)

    @staticmethod
    def _score_batch_static(anchors: List[AnchorSequence], context: str) -> List[ScoredAnchor]:
        """Score a batch of anchors for better timeout handling."""
        # Create analyzer only once per process
        if not hasattr(AnchorSequenceFinder._score_batch_static, "_phrase_analyzer"):
            AnchorSequenceFinder._score_batch_static._phrase_analyzer = PhraseAnalyzer(logger=logging.getLogger(__name__))

        scored_anchors = []
        for anchor in anchors:
            try:
                words = [w.text for w in anchor.transcribed_words]
                phrase_score = AnchorSequenceFinder._score_batch_static._phrase_analyzer.score_phrase(words, context)
                scored_anchors.append(ScoredAnchor(anchor=anchor, phrase_score=phrase_score))
            except Exception:
                # Add basic score for failed anchor
                phrase_score = PhraseScore(
                    phrase_type=PhraseType.COMPLETE,
                    natural_break_score=1.0,
                    length_score=1.0
                )
                scored_anchors.append(ScoredAnchor(anchor=anchor, phrase_score=phrase_score))
        
        return scored_anchors

    def _get_reference_words(self, source: str, ref_words: List[str], start_pos: Optional[int], end_pos: Optional[int]) -> List[str]:
        """Get words from reference text between two positions.

        Args:
            source: Reference source identifier
            ref_words: List of words from the reference text
            start_pos: Starting position (None for beginning)
            end_pos: Ending position (None for end)

        Returns:
            List of words between the positions
        """
        if start_pos is None:
            start_pos = 0
        if end_pos is None:
            end_pos = len(ref_words)
        return ref_words[start_pos:end_pos]

    def find_gaps(
        self,
        transcribed: str,
        anchors: List[ScoredAnchor],
        references: Dict[str, LyricsData],
        transcription_result: TranscriptionResult,
    ) -> List[GapSequence]:
        """Find gaps between anchor sequences in the transcribed text."""
        # Get all words from transcription
        all_words = []
        for segment in transcription_result.result.segments:
            all_words.extend(segment.words)

        # Clean and split reference texts
        ref_texts_clean = {
            source: self._clean_text(" ".join(w.text for s in lyrics.segments for w in s.words)).split()
            for source, lyrics in references.items()
        }
        ref_words = {source: [w for s in lyrics.segments for w in s.words] for source, lyrics in references.items()}

        # Create gaps with Word IDs
        gaps = []
        sorted_anchors = sorted(anchors, key=lambda x: x.anchor.transcription_position)

        # Handle initial gap
        if sorted_anchors:
            first_anchor = sorted_anchors[0].anchor
            first_anchor_pos = first_anchor.transcription_position
            if first_anchor_pos > 0:
                gap_word_ids = [w.id for w in all_words[:first_anchor_pos]]
                if gap := self._create_initial_gap(
                    id=WordUtils.generate_id(),
                    transcribed_word_ids=gap_word_ids,
                    transcription_position=0,
                    following_anchor_id=first_anchor.id,
                    ref_texts_clean=ref_texts_clean,
                    ref_words=ref_words,
                    following_anchor=first_anchor,
                ):
                    gaps.append(gap)

        # Handle gaps between anchors
        for i in range(len(sorted_anchors) - 1):
            current_anchor = sorted_anchors[i].anchor
            next_anchor = sorted_anchors[i + 1].anchor
            gap_start = current_anchor.transcription_position + len(current_anchor.transcribed_word_ids)
            gap_end = next_anchor.transcription_position

            if gap_end > gap_start:
                gap_word_ids = [w.id for w in all_words[gap_start:gap_end]]
                if between_gap := self._create_between_gap(
                    id=WordUtils.generate_id(),
                    transcribed_word_ids=gap_word_ids,
                    transcription_position=gap_start,
                    preceding_anchor_id=current_anchor.id,
                    following_anchor_id=next_anchor.id,
                    ref_texts_clean=ref_texts_clean,
                    ref_words=ref_words,
                    preceding_anchor=current_anchor,
                    following_anchor=next_anchor,
                ):
                    gaps.append(between_gap)

        # Handle final gap
        if sorted_anchors:
            last_anchor = sorted_anchors[-1].anchor
            last_pos = last_anchor.transcription_position + len(last_anchor.transcribed_word_ids)
            if last_pos < len(all_words):
                gap_word_ids = [w.id for w in all_words[last_pos:]]
                if final_gap := self._create_final_gap(
                    id=WordUtils.generate_id(),
                    transcribed_word_ids=gap_word_ids,
                    transcription_position=last_pos,
                    preceding_anchor_id=last_anchor.id,
                    ref_texts_clean=ref_texts_clean,
                    ref_words=ref_words,
                    preceding_anchor=last_anchor,
                ):
                    gaps.append(final_gap)

        return gaps

    def _create_initial_gap(
        self,
        id: str,
        transcribed_word_ids: List[str],
        transcription_position: int,
        following_anchor_id: str,
        ref_texts_clean: Dict[str, List[str]],
        ref_words: Dict[str, List[Word]],
        following_anchor: AnchorSequence,
    ) -> Optional[GapSequence]:
        """Create gap sequence before the first anchor.

        The gap includes all reference words from the start of each reference
        up to the position where the following anchor starts in that reference.
        """
        if transcribed_word_ids:  # If there are words in the gap
            # Get reference word IDs for the gap
            reference_word_ids = {}
            for source, words in ref_words.items():
                if source in ref_texts_clean:
                    # Get the position where the following anchor starts in this source
                    if source in following_anchor.reference_positions:
                        end_pos = following_anchor.reference_positions[source]
                        # Include all words from start up to the anchor
                        reference_word_ids[source] = [w.id for w in words[:end_pos]]
                    else:
                        # If this source doesn't contain the following anchor,
                        # we can't determine the gap content for it
                        reference_word_ids[source] = []

            return GapSequence(
                id=id,
                transcribed_word_ids=transcribed_word_ids,
                transcription_position=transcription_position,
                preceding_anchor_id=None,
                following_anchor_id=following_anchor_id,
                reference_word_ids=reference_word_ids,
            )
        return None

    def _create_between_gap(
        self,
        id: str,
        transcribed_word_ids: List[str],
        transcription_position: int,
        preceding_anchor_id: str,
        following_anchor_id: str,
        ref_texts_clean: Dict[str, List[str]],
        ref_words: Dict[str, List[Word]],
        preceding_anchor: AnchorSequence,
        following_anchor: AnchorSequence,
    ) -> Optional[GapSequence]:
        """Create gap sequence between two anchors.

        For each reference source, the gap includes all words between the end of the
        preceding anchor and the start of the following anchor in that source.
        """
        # Get reference word IDs for the gap
        reference_word_ids = {}
        for source, words in ref_words.items():
            if source in ref_texts_clean:
                # Only process sources that contain both anchors
                if source in preceding_anchor.reference_positions and source in following_anchor.reference_positions:
                    start_pos = preceding_anchor.reference_positions[source] + len(preceding_anchor.reference_word_ids[source])
                    end_pos = following_anchor.reference_positions[source]
                    # Include all words between the anchors
                    reference_word_ids[source] = [w.id for w in words[start_pos:end_pos]]
                else:
                    # If this source doesn't contain both anchors,
                    # we can't determine the gap content for it
                    reference_word_ids[source] = []

        return GapSequence(
            id=id,
            transcribed_word_ids=transcribed_word_ids,
            transcription_position=transcription_position,
            preceding_anchor_id=preceding_anchor_id,
            following_anchor_id=following_anchor_id,
            reference_word_ids=reference_word_ids,
        )

    def _create_final_gap(
        self,
        id: str,
        transcribed_word_ids: List[str],
        transcription_position: int,
        preceding_anchor_id: str,
        ref_texts_clean: Dict[str, List[str]],
        ref_words: Dict[str, List[Word]],
        preceding_anchor: AnchorSequence,
    ) -> Optional[GapSequence]:
        """Create gap sequence after the last anchor.

        For each reference source, includes all words from the end of the
        preceding anchor to the end of that reference.
        """
        # Get reference word IDs for the gap
        reference_word_ids = {}
        for source, words in ref_words.items():
            if source in ref_texts_clean:
                if source in preceding_anchor.reference_positions:
                    start_pos = preceding_anchor.reference_positions[source] + len(preceding_anchor.reference_word_ids[source])
                    # Include all words from end of last anchor to end of reference
                    reference_word_ids[source] = [w.id for w in words[start_pos:]]
                else:
                    # If this source doesn't contain the preceding anchor,
                    # we can't determine the gap content for it
                    reference_word_ids[source] = []

        return GapSequence(
            id=id,
            transcribed_word_ids=transcribed_word_ids,
            transcription_position=transcription_position,
            preceding_anchor_id=preceding_anchor_id,
            following_anchor_id=None,
            reference_word_ids=reference_word_ids,
        )

    def _extend_anchors_into_single_word_gaps(
        self,
        filtered_scored: List[ScoredAnchor],
        all_words: List[Word],
        ref_texts_clean: Dict[str, List[str]],
        ref_words: Dict[str, List[Word]],
    ) -> List[ScoredAnchor]:
        """
        Extend anchors to absorb single-word gaps when validated against references.

        This post-processing step reduces unnecessary gaps by extending adjacent
        anchors to cover single-word gaps that exist in all reference sources.

        Args:
            filtered_scored: List of scored anchors after overlap filtering
            all_words: List of Word objects from the transcription
            ref_texts_clean: Dict mapping source names to lists of cleaned words (strings)
            ref_words: Dict mapping source names to lists of Word objects

        Returns:
            List of scored anchors with some extended to absorb single-word gaps
        """
        if len(filtered_scored) < 2:
            return filtered_scored

        # Sort anchors by transcription position
        sorted_anchors = sorted(filtered_scored, key=lambda x: x.anchor.transcription_position)

        # Track which anchors have been extended (by index in sorted list)
        extended_anchors: Dict[int, ScoredAnchor] = {}
        absorbed_gaps: Set[int] = set()  # Gap positions that have been absorbed

        # Process each pair of adjacent anchors
        for i in range(len(sorted_anchors) - 1):
            current_anchor = sorted_anchors[i]
            next_anchor = sorted_anchors[i + 1]

            # Calculate gap
            current_end = current_anchor.anchor.transcription_position + current_anchor.anchor.length
            next_start = next_anchor.anchor.transcription_position
            gap_size = next_start - current_end

            # Only process single-word gaps
            if gap_size != 1:
                continue

            gap_position = current_end

            # Skip if this gap was already absorbed by a previous extension
            if gap_position in absorbed_gaps:
                continue

            # Use clean_text for consistent normalization with reference words
            gap_word = clean_text(all_words[gap_position].text)

            # First try: direct position validation
            forward_valid = self._validate_extension(
                anchor=current_anchor.anchor,
                gap_word=gap_word,
                direction="forward",
                ref_texts_clean=ref_texts_clean,
            )

            backward_valid = self._validate_extension(
                anchor=next_anchor.anchor,
                gap_word=gap_word,
                direction="backward",
                ref_texts_clean=ref_texts_clean,
            )

            # Second try: validate gap content holistically
            # (handles cases where reference gap has different structure)
            if not forward_valid and not backward_valid:
                gap_content_valid = self._validate_gap_content(
                    gap_words=[gap_word],
                    preceding_anchor=current_anchor.anchor,
                    following_anchor=next_anchor.anchor,
                    ref_texts_clean=ref_texts_clean,
                )
                if gap_content_valid:
                    # Prefer forward extension when gap content matches
                    forward_valid = True

            if forward_valid and backward_valid:
                # Both valid - prefer the longer anchor (or forward as tiebreaker)
                if self._prefer_forward_extension(current_anchor, next_anchor):
                    new_anchor = self._apply_forward_extension(
                        current_anchor, gap_position, all_words, ref_words
                    )
                    extended_anchors[i] = new_anchor
                else:
                    new_anchor = self._apply_backward_extension(
                        next_anchor, gap_position, all_words, ref_words
                    )
                    extended_anchors[i + 1] = new_anchor
                absorbed_gaps.add(gap_position)
            elif forward_valid:
                new_anchor = self._apply_forward_extension(
                    current_anchor, gap_position, all_words, ref_words
                )
                extended_anchors[i] = new_anchor
                absorbed_gaps.add(gap_position)
            elif backward_valid:
                new_anchor = self._apply_backward_extension(
                    next_anchor, gap_position, all_words, ref_words
                )
                extended_anchors[i + 1] = new_anchor
                absorbed_gaps.add(gap_position)
            # If neither valid, leave gap as-is

        # Build result list with extended anchors
        result = []
        for i, scored_anchor in enumerate(sorted_anchors):
            if i in extended_anchors:
                result.append(extended_anchors[i])
            else:
                result.append(scored_anchor)

        if absorbed_gaps:
            self.logger.info(f"🔍 GAP EXTENSION: Extended {len(absorbed_gaps)} single-word gaps into adjacent anchors")

        return result

    def _validate_gap_content(
        self,
        gap_words: List[str],
        preceding_anchor: AnchorSequence,
        following_anchor: AnchorSequence,
        ref_texts_clean: Dict[str, List[str]],
    ) -> bool:
        """
        Validate gap content by checking if it matches what's between anchors in references.

        This is a more flexible validation than _validate_extension - it checks if the
        gap content matches the reference gap content even if sizes differ slightly
        (e.g., "I'm" vs "i am", or contraction differences).

        Args:
            gap_words: List of cleaned gap words from transcription
            preceding_anchor: The anchor before the gap
            following_anchor: The anchor after the gap
            ref_texts_clean: Dict mapping source names to lists of cleaned words

        Returns:
            True if gap content matches in at least min_sources references
        """
        valid_sources = 0
        gap_content = " ".join(gap_words)

        for source, ref_words_list in ref_texts_clean.items():
            # Both anchors must exist in this source
            if source not in preceding_anchor.reference_positions:
                continue
            if source not in following_anchor.reference_positions:
                continue

            # Calculate reference gap bounds
            ref_gap_start = (
                preceding_anchor.reference_positions[source]
                + len(preceding_anchor.reference_word_ids.get(source, []))
            )
            ref_gap_end = following_anchor.reference_positions[source]

            # Get reference gap content
            if ref_gap_start >= ref_gap_end:
                # No gap or invalid bounds in reference
                continue

            ref_gap_words = ref_words_list[ref_gap_start:ref_gap_end]
            ref_gap_content = " ".join(ref_gap_words)

            # Compare gap contents
            if self._gap_content_matches(gap_content, ref_gap_content):
                valid_sources += 1

        return valid_sources >= self.min_sources

    def _gap_content_matches(self, trans_gap: str, ref_gap: str) -> bool:
        """
        Check if transcription gap content matches reference gap content.

        Handles common variations like contractions expanding to multiple words.

        Args:
            trans_gap: Cleaned gap content from transcription (e.g., "im")
            ref_gap: Cleaned gap content from reference (e.g., "i am")

        Returns:
            True if contents are equivalent
        """
        # Direct match
        if trans_gap == ref_gap:
            return True

        # Normalize common contractions for comparison
        trans_normalized = self._normalize_contractions(trans_gap)
        ref_normalized = self._normalize_contractions(ref_gap)

        return trans_normalized == ref_normalized

    def _normalize_contractions(self, text: str) -> str:
        """
        Normalize common contractions to a canonical form.

        Args:
            text: Cleaned text (already lowercase, no apostrophes)

        Returns:
            Text with contractions normalized
        """
        # Map contracted forms to expanded forms
        # Note: text is already cleaned (apostrophes removed), so "don't" -> "dont"
        contractions = {
            "im": "i am",
            "ive": "i have",
            "id": "i would",
            "ill": "i will",
            "youre": "you are",
            "youve": "you have",
            "youd": "you would",
            "youll": "you will",
            "hes": "he is",
            "shes": "she is",
            "its": "it is",
            "were": "we are",
            "weve": "we have",
            "wed": "we would",
            "well": "we will",
            "theyre": "they are",
            "theyve": "they have",
            "theyd": "they would",
            "theyll": "they will",
            "dont": "do not",
            "doesnt": "does not",
            "didnt": "did not",
            "cant": "can not",
            "couldnt": "could not",
            "wouldnt": "would not",
            "shouldnt": "should not",
            "wont": "will not",
            "isnt": "is not",
            "arent": "are not",
            "wasnt": "was not",
            "werent": "were not",
            "hasnt": "has not",
            "havent": "have not",
            "hadnt": "had not",
            "thats": "that is",
            "whats": "what is",
            "whos": "who is",
            "heres": "here is",
            "theres": "there is",
            "wheres": "where is",
            "lets": "let us",
            "cause": "because",
        }

        # Try to match and expand the text
        if text in contractions:
            return contractions[text]

        return text

    def _validate_extension(
        self,
        anchor: AnchorSequence,
        gap_word: str,
        direction: str,
        ref_texts_clean: Dict[str, List[str]],
    ) -> bool:
        """
        Validate whether a gap word can extend an anchor in the given direction.

        For forward extension: check if ref_word[anchor_end_position] matches gap_word
        For backward extension: check if ref_word[anchor_start_position - 1] matches gap_word

        Args:
            anchor: The anchor to potentially extend
            gap_word: The word in the gap (cleaned with clean_text)
            direction: "forward" or "backward"
            ref_texts_clean: Dict mapping source names to lists of cleaned words

        Returns:
            True if the extension is valid in at least min_sources references
        """
        valid_sources = 0
        gap_word_normalized = self._normalize_contractions(gap_word)

        for source, ref_words in ref_texts_clean.items():
            if source not in anchor.reference_positions:
                continue

            ref_start = anchor.reference_positions[source]
            ref_length = len(anchor.reference_word_ids.get(source, []))

            if direction == "forward":
                # Check the word after the anchor in this reference
                check_pos = ref_start + ref_length
                if check_pos >= len(ref_words):
                    continue
                ref_word = ref_words[check_pos]
            else:  # backward
                # Check the word before the anchor in this reference
                check_pos = ref_start - 1
                if check_pos < 0:
                    continue
                ref_word = ref_words[check_pos]

            # Compare with normalization for contractions
            ref_word_normalized = self._normalize_contractions(ref_word)
            if ref_word == gap_word or ref_word_normalized == gap_word_normalized:
                valid_sources += 1

        return valid_sources >= self.min_sources

    def _prefer_forward_extension(
        self,
        current_anchor: ScoredAnchor,
        next_anchor: ScoredAnchor,
    ) -> bool:
        """
        Decide whether to prefer forward extension when both directions are valid.

        Prefers extending the longer anchor, with forward as tiebreaker.

        Args:
            current_anchor: The anchor before the gap
            next_anchor: The anchor after the gap

        Returns:
            True to extend current (forward), False to extend next (backward)
        """
        current_length = current_anchor.anchor.length
        next_length = next_anchor.anchor.length

        # Prefer longer anchor
        if current_length > next_length:
            return True
        elif next_length > current_length:
            return False
        else:
            # Same length - prefer forward (arbitrary but consistent)
            return True

    def _apply_forward_extension(
        self,
        scored_anchor: ScoredAnchor,
        gap_position: int,
        all_words: List[Word],
        ref_words: Dict[str, List[Word]],
    ) -> ScoredAnchor:
        """
        Extend an anchor forward to include the gap word.

        Creates a new ScoredAnchor with the gap word added to the end.

        Args:
            scored_anchor: The anchor to extend
            gap_position: Position of the gap word in transcription
            all_words: List of Word objects from the transcription
            ref_words: Dict mapping source names to lists of Word objects

        Returns:
            New ScoredAnchor with extended anchor
        """
        anchor = scored_anchor.anchor
        gap_word = all_words[gap_position]

        # Create new word ID lists
        new_transcribed_word_ids = list(anchor.transcribed_word_ids) + [gap_word.id]
        new_words = list(anchor._words or anchor.words) + [gap_word.text]

        # Extend reference word IDs
        new_reference_word_ids = {}
        for source, word_ids in anchor.reference_word_ids.items():
            ref_start = anchor.reference_positions[source]
            ref_end = ref_start + len(word_ids)

            if source in ref_words and ref_end < len(ref_words[source]):
                new_ref_word = ref_words[source][ref_end]
                new_reference_word_ids[source] = list(word_ids) + [new_ref_word.id]
            else:
                # Can't extend in this source - keep original
                new_reference_word_ids[source] = list(word_ids)

        # Create new anchor
        new_anchor = AnchorSequence(
            id=anchor.id,  # Keep same ID
            transcribed_word_ids=new_transcribed_word_ids,
            transcription_position=anchor.transcription_position,
            reference_positions=dict(anchor.reference_positions),
            reference_word_ids=new_reference_word_ids,
            confidence=anchor.confidence,
            _words=new_words,
        )

        return ScoredAnchor(anchor=new_anchor, phrase_score=scored_anchor.phrase_score)

    def _apply_backward_extension(
        self,
        scored_anchor: ScoredAnchor,
        gap_position: int,
        all_words: List[Word],
        ref_words: Dict[str, List[Word]],
    ) -> ScoredAnchor:
        """
        Extend an anchor backward to include the gap word.

        Creates a new ScoredAnchor with the gap word added to the beginning.

        Args:
            scored_anchor: The anchor to extend
            gap_position: Position of the gap word in transcription
            all_words: List of Word objects from the transcription
            ref_words: Dict mapping source names to lists of Word objects

        Returns:
            New ScoredAnchor with extended anchor
        """
        anchor = scored_anchor.anchor
        gap_word = all_words[gap_position]

        # Create new word ID lists (prepend gap word)
        new_transcribed_word_ids = [gap_word.id] + list(anchor.transcribed_word_ids)
        new_words = [gap_word.text] + list(anchor._words or anchor.words)

        # Extend reference word IDs and update positions
        new_reference_word_ids = {}
        new_reference_positions = {}
        for source, word_ids in anchor.reference_word_ids.items():
            ref_start = anchor.reference_positions[source]
            new_ref_start = ref_start - 1

            if source in ref_words and new_ref_start >= 0:
                new_ref_word = ref_words[source][new_ref_start]
                new_reference_word_ids[source] = [new_ref_word.id] + list(word_ids)
                new_reference_positions[source] = new_ref_start
            else:
                # Can't extend in this source - keep original
                new_reference_word_ids[source] = list(word_ids)
                new_reference_positions[source] = ref_start

        # Create new anchor with updated position
        new_anchor = AnchorSequence(
            id=anchor.id,  # Keep same ID
            transcribed_word_ids=new_transcribed_word_ids,
            transcription_position=gap_position,  # New position is the gap position
            reference_positions=new_reference_positions,
            reference_word_ids=new_reference_word_ids,
            confidence=anchor.confidence,
            _words=new_words,
        )

        return ScoredAnchor(anchor=new_anchor, phrase_score=scored_anchor.phrase_score)
