from typing import List, Optional, Tuple, Union, Dict, Any
import logging
from pathlib import Path
from copy import deepcopy
import os
import shortuuid
import time

from karaoke_gen.lyrics_transcriber.correction.handlers.levenshtein import LevenshteinHandler
from karaoke_gen.lyrics_transcriber.correction.handlers.no_space_punct_match import NoSpacePunctuationMatchHandler
from karaoke_gen.lyrics_transcriber.correction.handlers.relaxed_word_count_match import RelaxedWordCountMatchHandler
from karaoke_gen.lyrics_transcriber.correction.handlers.repeat import RepeatCorrectionHandler
from karaoke_gen.lyrics_transcriber.correction.handlers.sound_alike import SoundAlikeHandler
from karaoke_gen.lyrics_transcriber.correction.handlers.syllables_match import SyllablesMatchHandler
from karaoke_gen.lyrics_transcriber.correction.handlers.word_count_match import WordCountMatchHandler
from karaoke_gen.lyrics_transcriber.types import (
    CorrectionStep,
    GapSequence,
    LyricsData,
    TranscriptionResult,
    CorrectionResult,
    LyricsSegment,
    WordCorrection,
    Word,
)
from karaoke_gen.lyrics_transcriber.correction.anchor_sequence import AnchorSequenceFinder
from karaoke_gen.lyrics_transcriber.correction.handlers.base import GapCorrectionHandler
from karaoke_gen.lyrics_transcriber.correction.handlers.extend_anchor import ExtendAnchorHandler
from karaoke_gen.lyrics_transcriber.utils.word_utils import WordUtils
from karaoke_gen.lyrics_transcriber.utils.tracing import create_span, add_span_attribute, add_span_event


class LyricsCorrector:
    """
    Coordinates lyrics correction process using multiple correction handlers.
    """

    def __init__(
        self,
        cache_dir: Union[str, Path],
        handlers: Optional[List[GapCorrectionHandler]] = None,
        enabled_handlers: Optional[List[str]] = None,
        anchor_finder: Optional[AnchorSequenceFinder] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.logger = logger or logging.getLogger(__name__)
        self._anchor_finder = anchor_finder
        self._cache_dir = Path(cache_dir)

        # Define default enabled handlers - excluding LLM, Repeat, SoundAlike, and Levenshtein
        DEFAULT_ENABLED_HANDLERS = [
            "ExtendAnchorHandler",
            "WordCountMatchHandler",
            "SyllablesMatchHandler",
            "RelaxedWordCountMatchHandler",
            "NoSpacePunctuationMatchHandler",
        ]

        # Create all handlers but respect enabled_handlers if provided
        # Note: Legacy LLMHandler removed - use AgenticCorrector via USE_AGENTIC_AI=1 instead
        all_handlers = [
            ("ExtendAnchorHandler", ExtendAnchorHandler(logger=self.logger)),
            ("WordCountMatchHandler", WordCountMatchHandler(logger=self.logger)),
            ("SyllablesMatchHandler", SyllablesMatchHandler(logger=self.logger)),
            ("RelaxedWordCountMatchHandler", RelaxedWordCountMatchHandler(logger=self.logger)),
            ("NoSpacePunctuationMatchHandler", NoSpacePunctuationMatchHandler(logger=self.logger)),
            ("RepeatCorrectionHandler", RepeatCorrectionHandler(logger=self.logger)),
            ("SoundAlikeHandler", SoundAlikeHandler(logger=self.logger)),
            ("LevenshteinHandler", LevenshteinHandler(logger=self.logger)),
        ]

        # Store all handler information
        self.all_handlers = [
            {
                "id": handler_id,
                "name": handler_id,
                "description": handler.__class__.__doc__ or "",
                "enabled": handler_id in (enabled_handlers if enabled_handlers is not None else DEFAULT_ENABLED_HANDLERS),
            }
            for handler_id, handler in all_handlers
        ]
        
        # Add AgenticCorrector if agentic AI is enabled
        use_agentic_env = os.getenv("USE_AGENTIC_AI", "0").lower() in {"1", "true", "yes"}
        if use_agentic_env:
            self.all_handlers.append({
                "id": "AgenticCorrector",
                "name": "Agentic AI Corrector",
                "description": "AI-powered classification and correction of lyric gaps using LLM reasoning",
                "enabled": True,
            })

        if handlers:
            self.handlers = handlers
        else:
            # Use provided enabled_handlers if available, otherwise use defaults
            handler_filter = enabled_handlers if enabled_handlers is not None else DEFAULT_ENABLED_HANDLERS
            self.handlers = [h[1] for h in all_handlers if h[0] in handler_filter]

    @property
    def anchor_finder(self) -> AnchorSequenceFinder:
        """Lazy load the anchor finder instance, initializing it if not already set."""
        if self._anchor_finder is None:
            self._anchor_finder = AnchorSequenceFinder(cache_dir=self._cache_dir, logger=self.logger)
        return self._anchor_finder

    def run(
        self,
        transcription_results: List[TranscriptionResult],
        lyrics_results: Dict[str, LyricsData],
        metadata: Optional[Dict[str, Any]] = None,
        agentic_deadline: Optional[float] = None,
    ) -> CorrectionResult:
        """Execute the correction process.

        Args:
            transcription_results: List of transcription results to correct.
            lyrics_results: Dictionary of lyrics data from various sources.
            metadata: Optional metadata including artist, title, audio file hash.
            agentic_deadline: Optional Unix timestamp (from time.time()). If agentic
                correction is still running after this time, it will abort and return
                uncorrected results for human review.

        Note:
            The deadline is checked between gap iterations, not during LLM processing.
            A single long-running LLM call may exceed the deadline. The caller should
            wrap this method with an outer timeout (e.g., asyncio.wait_for) as a safety
            net for hung operations.
        """
        start_time = time.time()

        # Optional agentic routing flag from environment; default off for safety
        agentic_enabled = os.getenv("USE_AGENTIC_AI", "").lower() in {"1", "true", "yes"}

        with create_span("lyrics_corrector.run", {
            "agentic_enabled": agentic_enabled,
            "reference_sources": len(lyrics_results),
            "transcription_count": len(transcription_results),
        }) as span:
            self.logger.info(f"🤖 AGENTIC MODE: {'ENABLED' if agentic_enabled else 'DISABLED'} (USE_AGENTIC_AI={os.getenv('USE_AGENTIC_AI', 'NOT_SET')})")
            if not transcription_results:
                self.logger.error("No transcription results available")
                raise ValueError("No primary transcription data available")

            # Store reference lyrics for use in word map
            self.reference_lyrics = lyrics_results

            # Get primary transcription
            primary_transcription_result = sorted(transcription_results, key=lambda x: x.priority)[0]
            primary_transcription = primary_transcription_result.result
            transcribed_text = " ".join(" ".join(w.text for w in segment.words) for segment in primary_transcription.segments)

            # Find anchor sequences and gaps
            self.logger.debug("Finding anchor sequences and gaps")
            with create_span("lyrics_corrector.find_anchors_and_gaps") as anchor_span:
                anchor_sequences = self.anchor_finder.find_anchors(transcribed_text, lyrics_results, primary_transcription_result)
                gap_sequences = self.anchor_finder.find_gaps(transcribed_text, anchor_sequences, lyrics_results, primary_transcription_result)
                if anchor_span:
                    anchor_span.set_attribute("anchor_count", len(anchor_sequences))
                    anchor_span.set_attribute("gap_count", len(gap_sequences))

            # Store anchor sequences for use in correction handlers
            self._anchor_sequences = anchor_sequences

            # Process corrections with metadata and optional deadline for agentic timeout
            with create_span("lyrics_corrector.process_corrections", {
                "gap_count": len(gap_sequences),
                "agentic_enabled": agentic_enabled,
            }) as process_span:
                corrections, corrected_segments, correction_steps, word_id_map, segment_id_map = self._process_corrections(
                    primary_transcription.segments, gap_sequences, metadata=metadata, deadline=agentic_deadline
                )
                if process_span:
                    process_span.set_attribute("corrections_count", len(corrections))

            # Calculate correction ratio
            total_words = sum(len(segment.words) for segment in corrected_segments)
            corrections_made = len(corrections)
            correction_ratio = 1 - (corrections_made / total_words if total_words > 0 else 0)

            # Get the currently enabled handler IDs using the handler's name attribute if available
            enabled_handlers = [getattr(handler, "name", handler.__class__.__name__) for handler in self.handlers]

            # Add final span attributes
            if span:
                span.set_attribute("total_words", total_words)
                span.set_attribute("corrections_made", corrections_made)
                span.set_attribute("correction_ratio", correction_ratio)
                span.set_attribute("duration_seconds", time.time() - start_time)

            result = CorrectionResult(
                original_segments=primary_transcription.segments,
                corrected_segments=corrected_segments,
                corrections=corrections,
                corrections_made=corrections_made,
                confidence=correction_ratio,
                reference_lyrics=lyrics_results,
                anchor_sequences=anchor_sequences,
                resized_segments=[],
                gap_sequences=gap_sequences,
                metadata={
                    "anchor_sequences_count": len(anchor_sequences),
                    "gap_sequences_count": len(gap_sequences),
                    "total_words": total_words,
                    "correction_ratio": correction_ratio,
                    "available_handlers": self.all_handlers,
                    "enabled_handlers": enabled_handlers,
                    "agentic_routing": "agentic" if agentic_enabled else "rule-based",
                },
                correction_steps=correction_steps,
                word_id_map=word_id_map,
                segment_id_map=segment_id_map,
            )
            return result

    def _preserve_formatting(self, original: str, new_word: str) -> str:
        """Preserve original word's formatting when applying correction."""
        # Find leading/trailing whitespace
        leading_space = " " if original != original.lstrip() else ""
        trailing_space = " " if original != original.rstrip() else ""
        return leading_space + new_word.strip() + trailing_space

    def _process_corrections(
        self, segments: List[LyricsSegment], gap_sequences: List[GapSequence], metadata: Optional[Dict[str, Any]] = None,
        deadline: Optional[float] = None
    ) -> Tuple[List[WordCorrection], List[LyricsSegment], List[CorrectionStep], Dict[str, str], Dict[str, str]]:
        """Process corrections using handlers.

        Args:
            segments: List of lyrics segments to process.
            gap_sequences: List of gap sequences to correct.
            metadata: Optional metadata including artist, title, audio file hash.
            deadline: Optional Unix timestamp (from time.time()). When agentic mode is
                enabled and this deadline is exceeded, remaining gaps are skipped and
                the method returns with whatever corrections have been made (likely none).

        Returns:
            Tuple of (corrections, corrected_segments, correction_steps, word_id_map, segment_id_map).

        The correction flow works as follows:
        1. First pass: Process all gaps
           - Iterate through each gap sequence
           - Try handlers until one can handle the gap
           - Store all corrections in the gap
        2. Second pass: Apply corrections to segments
           - Iterate through segments and words
           - Look up any corrections that apply to each word
           - Create new segments with corrected words

        This two-pass approach separates the concerns of:
        a) Finding and making corrections (gap-centric)
        b) Applying those corrections to the original text (segment-centric)
        """
        # Generate a unique session ID for this correction task
        # This groups all traces in Langfuse for easy debugging
        session_id = f"lyrics-correction-{shortuuid.uuid()}"
        self.logger.info(f"Starting correction process with {len(gap_sequences)} gaps (session: {session_id})")
        
        correction_steps = []
        all_corrections = []
        word_id_map = {}
        segment_id_map = {}

        # Create word map for handlers - include both transcribed and reference words
        word_map = {w.id: w for s in segments for w in s.words}  # Transcribed words

        # Add reference words from all sources
        for source, lyrics_data in self.reference_lyrics.items():
            for segment in lyrics_data.segments:
                for word in segment.words:
                    if word.id not in word_map:  # Don't overwrite transcribed words
                        word_map[word.id] = word

        # Build a linear position map for words to support agentic proposals
        linear_position_map = {}
        _pos_idx = 0
        for s in segments:
            for w in s.words:
                linear_position_map[w.id] = _pos_idx
                _pos_idx += 1

        # Base handler data that all handlers need
        base_handler_data = {
            "word_map": word_map,
            "anchor_sequences": self._anchor_sequences,
            "audio_file_hash": metadata.get("audio_file_hash") if metadata else None,
        }

        # Check if we're in agentic-only mode
        use_agentic_env = os.getenv("USE_AGENTIC_AI", "").lower() in {"1", "true", "yes"}
        
        # Import agentic modules once if needed
        _AgenticCorrector = None
        _adapt = None
        _ModelRouter = None
        
        # Pre-initialized agentic corrector (created once, reused for all gaps)
        _agentic_agent = None

        if use_agentic_env:
            try:
                from karaoke_gen.lyrics_transcriber.correction.agentic.agent import AgenticCorrector as _AgenticCorrector
                from karaoke_gen.lyrics_transcriber.correction.agentic.adapter import adapt_proposals_to_word_corrections as _adapt
                from karaoke_gen.lyrics_transcriber.correction.agentic.router import ModelRouter as _ModelRouter
                self.logger.info("🤖 Agentic modules imported successfully - running in AGENTIC-ONLY mode")

                # Create agent ONCE and reuse for all gaps (avoids repeated model initialization)
                _router = _ModelRouter()
                model_id = _router.choose_model("gap", uncertainty=0.5)  # Use default uncertainty
                self.logger.info(f"🤖 Creating single AgenticCorrector with model: {model_id}")
                _agentic_agent = _AgenticCorrector.from_model(
                    model=model_id,
                    session_id=session_id,
                    cache_dir=str(self._cache_dir)
                )
                self.logger.info("🤖 AgenticCorrector initialized and ready for all gaps")
            except Exception as e:
                self.logger.error(f"🤖 Failed to import agentic modules but USE_AGENTIC_AI=1: {e}")
                raise RuntimeError(f"Agentic AI correction is enabled but required modules could not be imported: {e}") from e

        # === TEMPORARY: Gap extraction for manual review ===
        if os.getenv("DUMP_GAPS") == "1":
            import yaml
            
            # Build a flat list of all transcribed words for context
            all_transcribed_words = []
            for seg in segments:
                all_transcribed_words.extend(seg.words)
            
            # Create word position map
            word_position = {w.id: idx for idx, w in enumerate(all_transcribed_words)}
            
            gaps_data = []
            for i, gap in enumerate(gap_sequences, 1):
                gap_words = []
                gap_positions = []
                
                for word_id in gap.transcribed_word_ids:
                    if word_id in word_map:
                        word = word_map[word_id]
                        gap_words.append({
                            "id": word_id,
                            "text": word.text,
                            "start_time": round(getattr(word, 'start_time', 0), 3),
                            "end_time": round(getattr(word, 'end_time', 0), 3)
                        })
                        if word_id in word_position:
                            gap_positions.append(word_position[word_id])
                
                # Get context words (10 before and 10 after)
                preceding_words_list = []
                following_words_list = []
                
                if gap_positions:
                    first_gap_pos = min(gap_positions)
                    last_gap_pos = max(gap_positions)
                    
                    # Get 10 words before the gap
                    start_pos = max(0, first_gap_pos - 10)
                    if start_pos == 0:
                        preceding_words_list.append("<song_start>")
                    for idx in range(start_pos, first_gap_pos):
                        if idx < len(all_transcribed_words):
                            preceding_words_list.append(all_transcribed_words[idx].text)
                    
                    # Get 10 words after the gap
                    end_pos = min(len(all_transcribed_words), last_gap_pos + 11)
                    for idx in range(last_gap_pos + 1, end_pos):
                        if idx < len(all_transcribed_words):
                            following_words_list.append(all_transcribed_words[idx].text)
                    if end_pos == len(all_transcribed_words):
                        following_words_list.append("<song_end>")
                
                # Convert to strings
                preceding_words = " ".join(preceding_words_list)
                following_words = " ".join(following_words_list)
                
                # Get reference context from all sources using anchor sequences
                reference_contexts = {}
                
                # Find which anchor sequence this gap belongs to
                parent_anchor = None
                for anchor in self._anchor_sequences:
                    if hasattr(anchor, 'gaps') and gap in anchor.gaps:
                        parent_anchor = anchor
                        break
                
                for source, lyrics_data in self.reference_lyrics.items():
                    if lyrics_data and lyrics_data.segments:
                        # Get all reference words
                        ref_words = []
                        for seg in lyrics_data.segments:
                            ref_words.extend([w.text for w in seg.words])
                        
                        if parent_anchor and hasattr(parent_anchor, 'reference_word_ids'):
                            # Use anchor's reference word IDs to find the correct position
                            # Get the reference words from this anchor's context
                            anchor_ref_word_ids = parent_anchor.reference_word_ids.get(source, [])
                            
                            if anchor_ref_word_ids:
                                # Find position of anchor's reference words
                                ref_word_map = {w.id: idx for idx, w in enumerate(
                                    [w for seg in lyrics_data.segments for w in seg.words]
                                )}
                                
                                # Get indices of anchor words in reference
                                anchor_indices = [ref_word_map[wid] for wid in anchor_ref_word_ids if wid in ref_word_map]
                                
                                if anchor_indices:
                                    # Use the anchor position to get context
                                    anchor_start = min(anchor_indices)
                                    anchor_end = max(anchor_indices)
                                    
                                    # Get 20 words before and after the anchor region
                                    context_start = max(0, anchor_start - 20)
                                    context_end = min(len(ref_words), anchor_end + 21)
                                    
                                    context_words = ref_words[context_start:context_end]
                                    reference_contexts[source] = " ".join([w.text if hasattr(w, 'text') else str(w) for w in context_words])
                                    continue
                        
                        # Fallback: estimate position by time percentage
                        if gap_words and gap_words[0].get('start_time'):
                            # Try to get song duration from segments
                            last_word_time = 0
                            for seg in segments:
                                if seg.words:
                                    last_word_time = max(last_word_time, seg.words[-1].end_time)
                            
                            if last_word_time > 0:
                                gap_time = gap_words[0]['start_time']
                                time_percentage = gap_time / last_word_time
                                
                                # Use percentage to estimate position in reference
                                estimated_idx = int(len(ref_words) * time_percentage)
                                context_start = max(0, estimated_idx - 20)
                                context_end = min(len(ref_words), estimated_idx + 21)
                                
                                context_words = ref_words[context_start:context_end]
                                reference_contexts[source] = " ".join([w.text if hasattr(w, 'text') else str(w) for w in context_words])
                            else:
                                # Ultimate fallback: entire reference lyrics
                                reference_contexts[source] = " ".join([w.text if hasattr(w, 'text') else str(w) for w in ref_words])
                        else:
                            # No time info, use entire reference lyrics
                            reference_contexts[source] = " ".join([w.text if hasattr(w, 'text') else str(w) for w in ref_words])
                
                gap_text = " ".join([w["text"] for w in gap_words])
                
                gaps_data.append({
                    "gap_id": i,
                    "position": gap.transcription_position,
                    "preceding_words": preceding_words,
                    "gap_text": gap_text,
                    "following_words": following_words,
                    "transcribed_words": gap_words,
                    "reference_contexts": reference_contexts,
                    "word_count": len(gap_words),
                    "annotations": {
                        "your_decision": "",
                        "action_type": "# NO_ACTION | REPLACE | DELETE | INSERT | MERGE | SPLIT",
                        "target_word_ids": [],
                        "replacement_text": "",
                        "notes": ""
                    }
                })
            
            with open("gaps_review.yaml", 'w') as f:
                f.write("# Gap Review Data for Manual Annotation\n")
                f.write(f"# Total gaps: {len(gaps_data)}\n")
                f.write("#\n")
                f.write("# For each gap, fill in the annotations section:\n")
                f.write("#   your_decision: Brief description of what should happen\n")
                f.write("#   action_type: NO_ACTION | REPLACE | DELETE | INSERT | MERGE | SPLIT\n")
                f.write("#   target_word_ids: Which word IDs to operate on (from transcribed_words)\n")
                f.write("#   replacement_text: The corrected text (if applicable)\n")
                f.write("#   notes: Any additional reasoning or context\n")
                f.write("#\n\n")
                yaml.dump({"gaps": gaps_data}, f, default_flow_style=False, allow_unicode=True, width=120, sort_keys=False)
            
            self.logger.info(f"📝 Dumped {len(gaps_data)} gaps to gaps_review.yaml - review and annotate!")
            import sys
            sys.exit(0)
        # === END TEMPORARY CODE ===

        # AGENTIC-ONLY MODE: Process all gaps in parallel for better performance
        if use_agentic_env:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            from karaoke_gen.lyrics_transcriber.correction.agentic.providers.config import ProviderConfig

            # Get parallel processing config
            _config = ProviderConfig.from_env()
            max_workers = _config.max_parallel_gaps

            # Wrap agentic processing in a tracing span
            add_span_event("agentic_processing_started", {"gap_count": len(gap_sequences), "max_workers": max_workers})
            self.logger.info(f"🤖 Processing {len(gap_sequences)} gaps in parallel (max_workers={max_workers})")

            # Pre-compute shared data structures once (not per-gap)
            all_transcribed_words = []
            for seg in segments:
                all_transcribed_words.extend(seg.words)
            word_position = {w.id: idx for idx, w in enumerate(all_transcribed_words)}

            # Build reference contexts once (same for all gaps)
            reference_contexts = {}
            for source, lyrics_data in self.reference_lyrics.items():
                if lyrics_data and lyrics_data.segments:
                    ref_words = []
                    for seg in lyrics_data.segments:
                        ref_words.extend([w.text for w in seg.words])
                    reference_contexts[source] = " ".join(ref_words)

            # Get artist and title once
            artist = metadata.get("artist") if metadata else None
            title = metadata.get("title") if metadata else None

            # Prepare all gap inputs upfront
            gap_inputs = []
            for i, gap in enumerate(gap_sequences, 1):
                # Prepare gap words data
                gap_words_data = []
                for word_id in gap.transcribed_word_ids:
                    if word_id in word_map:
                        word = word_map[word_id]
                        gap_words_data.append({
                            "id": word_id,
                            "text": word.text,
                            "start_time": getattr(word, 'start_time', 0),
                            "end_time": getattr(word, 'end_time', 0)
                        })

                # Compute context words
                gap_positions = [word_position[wid] for wid in gap.transcribed_word_ids if wid in word_position]
                preceding_words = ""
                following_words = ""

                if gap_positions:
                    first_gap_pos = min(gap_positions)
                    last_gap_pos = max(gap_positions)

                    # Get 10 words before
                    start_pos = max(0, first_gap_pos - 10)
                    preceding_list = [all_transcribed_words[idx].text for idx in range(start_pos, first_gap_pos) if idx < len(all_transcribed_words)]
                    preceding_words = " ".join(preceding_list)

                    # Get 10 words after
                    end_pos = min(len(all_transcribed_words), last_gap_pos + 11)
                    following_list = [all_transcribed_words[idx].text for idx in range(last_gap_pos + 1, end_pos) if idx < len(all_transcribed_words)]
                    following_words = " ".join(following_list)

                gap_inputs.append({
                    'index': i,
                    'gap': gap,
                    'gap_id': f"gap_{i}",
                    'gap_words': gap_words_data,
                    'preceding_words': preceding_words,
                    'following_words': following_words,
                    'reference_contexts': reference_contexts,
                    'artist': artist,
                    'title': title
                })

            # Function to process a single gap (runs in thread pool)
            def process_single_gap(gap_input):
                """Process a single gap and return proposals. Thread-safe."""
                idx = gap_input['index']
                try:
                    proposals = _agentic_agent.propose_for_gap(
                        gap_id=gap_input['gap_id'],
                        gap_words=gap_input['gap_words'],
                        preceding_words=gap_input['preceding_words'],
                        following_words=gap_input['following_words'],
                        reference_contexts=gap_input['reference_contexts'],
                        artist=gap_input['artist'],
                        title=gap_input['title']
                    )
                    return {'index': idx, 'gap': gap_input['gap'], 'proposals': proposals, 'error': None}
                except Exception as e:
                    return {'index': idx, 'gap': gap_input['gap'], 'proposals': None, 'error': str(e)}

            # Process gaps in parallel
            results = [None] * len(gap_inputs)
            completed_count = 0
            errors = []

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all tasks
                future_to_input = {executor.submit(process_single_gap, g): g for g in gap_inputs}

                # Collect results as they complete
                for future in as_completed(future_to_input):
                    # Check deadline
                    if deadline and time.time() > deadline:
                        self.logger.warning(
                            f"⏰ AGENTIC TIMEOUT: Deadline exceeded after processing {completed_count}/{len(gap_sequences)} gaps. "
                            "Cancelling remaining gaps - human review will correct any issues."
                        )
                        # Cancel remaining futures (use list() to avoid mutating dict during iteration)
                        for f in list(future_to_input.keys()):
                            f.cancel()
                        break

                    result = future.result()
                    idx = result['index'] - 1  # Convert 1-based to 0-based
                    results[idx] = result
                    completed_count += 1

                    if result['error']:
                        errors.append(f"Gap {result['index']}: {result['error']}")
                        self.logger.error(f"🤖 Gap {result['index']} failed: {result['error']}")
                    else:
                        proposal_count = len(result['proposals']) if result['proposals'] else 0
                        self.logger.info(f"🤖 Gap {result['index']}/{len(gap_sequences)} completed ({proposal_count} proposals)")

            self.logger.info(f"🤖 Parallel processing complete: {completed_count}/{len(gap_sequences)} gaps processed")
            add_span_event("agentic_processing_completed", {
                "gaps_processed": completed_count,
                "gaps_total": len(gap_sequences),
                "errors": len(errors),
            })

            # If any errors occurred, fail fast
            if errors:
                raise RuntimeError(f"Agentic AI correction failed for {len(errors)} gaps: {'; '.join(errors)}")

            # Apply corrections sequentially (must be in order due to segment modifications)
            for result in results:
                if result is None:
                    continue  # Skipped due to deadline

                i = result['index']
                gap = result['gap']
                _proposals = result['proposals']

                _agentic_corrections = _adapt(_proposals, word_map, linear_position_map) if _proposals else []

                if _agentic_corrections:
                    self.logger.info(f"🤖 Applying {len(_agentic_corrections)} agentic corrections for gap {i}")
                    affected_word_ids = [w.id for w in self._get_affected_words(gap, segments)]
                    affected_segment_ids = [s.id for s in self._get_affected_segments(gap, segments)]
                    updated_segments = self._apply_corrections_to_segments(self._get_affected_segments(gap, segments), _agentic_corrections)
                    for correction in _agentic_corrections:
                        if correction.word_id and correction.corrected_word_id:
                            word_id_map[correction.word_id] = correction.corrected_word_id
                    for old_seg, new_seg in zip(self._get_affected_segments(gap, segments), updated_segments):
                        segment_id_map[old_seg.id] = new_seg.id
                    step = CorrectionStep(
                        handler_name="AgenticCorrector",
                        affected_word_ids=affected_word_ids,
                        affected_segment_ids=affected_segment_ids,
                        corrections=_agentic_corrections,
                        segments_before=self._get_affected_segments(gap, segments),
                        segments_after=updated_segments,
                        created_word_ids=[w.id for w in self._get_new_words(updated_segments, affected_word_ids)],
                        deleted_word_ids=[id for id in affected_word_ids if not self._word_exists(id, updated_segments)],
                    )
                    correction_steps.append(step)
                    all_corrections.extend(_agentic_corrections)
                    # Log corrections made
                    for correction in _agentic_corrections:
                        self.logger.info(
                            f"Made correction: '{correction.original_word}' -> '{correction.corrected_word}' "
                            f"(confidence: {correction.confidence:.2f}, reason: {correction.reason})"
                        )
                else:
                    self.logger.debug(f"🤖 No agentic corrections needed for gap {i}")

        # RULE-BASED MODE: Process gaps sequentially
        for i, gap in enumerate(gap_sequences, 1):
            # Skip if we already processed in agentic mode
            if use_agentic_env:
                continue

            self.logger.info(f"Processing gap {i}/{len(gap_sequences)} at position {gap.transcription_position}")

            # Get the actual words for logging
            gap_words = [word_map[word_id] for word_id in gap.transcribed_word_ids]
            self.logger.debug(f"Gap text: '{' '.join(w.text for w in gap_words)}'")

            # RULE-BASED MODE: Try each handler in order
            for handler in self.handlers:
                handler_name = handler.__class__.__name__
                can_handle, handler_data = handler.can_handle(gap, base_handler_data)

                if can_handle:
                    # Merge base handler data with specific handler data
                    handler_data = {**base_handler_data, **(handler_data or {})}

                    corrections = handler.handle(gap, handler_data)
                    if corrections:
                        self.logger.info(f"Handler {handler_name} made {len(corrections)} corrections")
                        # Track affected IDs
                        affected_word_ids = [w.id for w in self._get_affected_words(gap, segments)]
                        affected_segment_ids = [s.id for s in self._get_affected_segments(gap, segments)]

                        # Apply corrections and get updated segments
                        updated_segments = self._apply_corrections_to_segments(self._get_affected_segments(gap, segments), corrections)

                        # Update ID maps
                        for correction in corrections:
                            if correction.word_id and correction.corrected_word_id:
                                word_id_map[correction.word_id] = correction.corrected_word_id

                        # Map segment IDs
                        for old_seg, new_seg in zip(self._get_affected_segments(gap, segments), updated_segments):
                            segment_id_map[old_seg.id] = new_seg.id

                        # Create correction step
                        step = CorrectionStep(
                            handler_name=handler_name,
                            affected_word_ids=affected_word_ids,
                            affected_segment_ids=affected_segment_ids,
                            corrections=corrections,
                            segments_before=self._get_affected_segments(gap, segments),
                            segments_after=updated_segments,
                            created_word_ids=[w.id for w in self._get_new_words(updated_segments, affected_word_ids)],
                            deleted_word_ids=[id for id in affected_word_ids if not self._word_exists(id, updated_segments)],
                        )
                        correction_steps.append(step)
                        all_corrections.extend(corrections)

                        # Log correction details
                        for correction in corrections:
                            self.logger.info(
                                f"Made correction: '{correction.original_word}' -> '{correction.corrected_word}' "
                                f"(confidence: {correction.confidence:.2f}, reason: {correction.reason})"
                            )
                        break  # Stop trying other handlers once we've made corrections
                    else:
                        self.logger.debug(f"Handler {handler_name} found no corrections needed")
                else:
                    self.logger.debug(f"Handler {handler_name} cannot handle gap")

        # Create final result with correction history
        corrected_segments = self._apply_all_corrections(segments, all_corrections)
        self.logger.info(f"Correction process completed with {len(all_corrections)} total corrections")
        return all_corrections, corrected_segments, correction_steps, word_id_map, segment_id_map

    def _get_new_words(self, segments: List[LyricsSegment], original_word_ids: List[str]) -> List[Word]:
        """Find words that were created during correction."""
        return [w for s in segments for w in s.words if w.id not in original_word_ids]

    def _word_exists(self, word_id: str, segments: List[LyricsSegment]) -> bool:
        """Check if a word ID still exists in the segments."""
        return any(w.id == word_id for s in segments for w in s.words)

    def _apply_corrections_to_segments(self, segments: List[LyricsSegment], corrections: List[WordCorrection]) -> List[LyricsSegment]:
        """Apply corrections to create new segments."""
        # Create word ID map for quick lookup
        word_map = {w.id: w for s in segments for w in s.words}

        # Group corrections by original_position to handle splits
        correction_map = {}
        for c in corrections:
            if c.original_position not in correction_map:
                correction_map[c.original_position] = []
            correction_map[c.original_position].append(c)

        corrected_segments = []
        current_word_idx = 0

        for segment in segments:
            corrected_words = []
            for word in segment.words:
                if current_word_idx in correction_map:
                    word_corrections = sorted(correction_map[current_word_idx], key=lambda x: x.split_index or 0)

                    # Check if any correction has a valid split_total
                    total_splits = next((c.split_total for c in word_corrections if c.split_total is not None), None)

                    if total_splits:
                        # Handle word split
                        split_duration = (word.end_time - word.start_time) / total_splits

                        for i, correction in enumerate(word_corrections):
                            start_time = word.start_time + (i * split_duration)
                            end_time = start_time + split_duration

                            # Update corrected_position as we create new words
                            correction.corrected_position = len(corrected_words)
                            new_word = Word(
                                id=correction.corrected_word_id or WordUtils.generate_id(),
                                text=self._preserve_formatting(correction.original_word, correction.corrected_word),
                                start_time=start_time,
                                end_time=end_time,
                                confidence=correction.confidence,
                                created_during_correction=True,
                            )
                            corrected_words.append(new_word)
                    else:
                        # Handle single word replacement
                        correction = word_corrections[0]
                        if not correction.is_deletion:
                            # Update corrected_position
                            correction.corrected_position = len(corrected_words)
                            new_word = Word(
                                id=correction.corrected_word_id or WordUtils.generate_id(),
                                text=self._preserve_formatting(correction.original_word, correction.corrected_word),
                                start_time=word.start_time,
                                end_time=word.end_time,
                                confidence=correction.confidence,
                                created_during_correction=True,
                            )
                            corrected_words.append(new_word)
                else:
                    corrected_words.append(word)
                current_word_idx += 1

            if corrected_words:
                corrected_segments.append(
                    LyricsSegment(
                        id=segment.id,  # Preserve original segment ID
                        text=" ".join(w.text for w in corrected_words),
                        words=corrected_words,
                        start_time=segment.start_time,
                        end_time=segment.end_time,
                    )
                )

        return corrected_segments

    def _get_affected_segments(self, gap: GapSequence, segments: List[LyricsSegment]) -> List[LyricsSegment]:
        """Get segments that contain words from the gap sequence."""
        affected_segments = []
        gap_word_ids = set(gap.transcribed_word_ids)

        for segment in segments:
            # Check if any words in this segment are part of the gap
            if any(w.id in gap_word_ids for w in segment.words):
                affected_segments.append(segment)
            elif affected_segments:  # We've passed the gap
                break

        return affected_segments

    def _get_affected_words(self, gap: GapSequence, segments: List[LyricsSegment]) -> List[Word]:
        """Get words that are part of the gap sequence."""
        # Create a map of word IDs to Word objects for quick lookup
        word_map = {w.id: w for s in segments for w in s.words}

        # Get the actual Word objects using the IDs
        return [word_map[word_id] for word_id in gap.transcribed_word_ids]

    def _apply_all_corrections(self, segments: List[LyricsSegment], corrections: List[WordCorrection]) -> List[LyricsSegment]:
        """Apply all corrections to create final corrected segments."""
        # Make a deep copy to avoid modifying original segments
        working_segments = deepcopy(segments)

        # Apply corrections in order
        return self._apply_corrections_to_segments(working_segments, corrections)
