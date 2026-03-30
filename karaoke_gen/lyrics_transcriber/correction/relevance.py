"""Reference lyrics relevance scoring and filtering.

Computes how well each reference source matches the transcription based on
anchor sequence coverage. Sources below the relevance threshold are filtered
out to prevent wrong-song lyrics from polluting corrections.
"""
import logging
from dataclasses import dataclass
from typing import Dict, List, Tuple

from karaoke_gen.lyrics_transcriber.types import AnchorSequence, LyricsData

# Default threshold — determined by empirical analysis of production jobs.
# See scripts/analyze_reference_relevance.py and the design spec for methodology.
MIN_REFERENCE_RELEVANCE = 0.30  # Empirical: wrong-song max 3.5%, correct-song min 26.8%


@dataclass
class SourceRelevanceResult:
    """Relevance scoring result for a single reference source."""
    source: str
    relevance: float  # 0.0 to 1.0 — fraction of reference words in anchors
    matched_words: int
    total_words: int
    track_name: str = ""
    artist_names: str = ""


def compute_source_relevance(
    source_name: str,
    lyrics_data: LyricsData,
    anchor_sequences: List[AnchorSequence],
) -> SourceRelevanceResult:
    """Compute relevance score for a single reference source.

    Relevance = (reference words appearing in anchor sequences) / (total reference words).
    """
    total_words = sum(len(seg.words) for seg in lyrics_data.segments)
    if total_words == 0:
        return SourceRelevanceResult(
            source=source_name,
            relevance=0.0,
            matched_words=0,
            total_words=0,
            track_name=lyrics_data.metadata.track_name or "",
            artist_names=lyrics_data.metadata.artist_names or "",
        )

    # Collect all reference word IDs for this source across all anchors.
    # Handle both AnchorSequence and ScoredAnchor (which wraps AnchorSequence).
    all_ref_word_ids = set()
    for anchor_or_scored in anchor_sequences:
        anchor = getattr(anchor_or_scored, "anchor", anchor_or_scored)
        source_word_ids = anchor.reference_word_ids.get(source_name, [])
        all_ref_word_ids.update(source_word_ids)

    # Count how many of this source's word IDs appear in anchors
    source_word_ids = {w.id for seg in lyrics_data.segments for w in seg.words}
    matched = len(all_ref_word_ids & source_word_ids)

    return SourceRelevanceResult(
        source=source_name,
        relevance=matched / total_words,
        matched_words=matched,
        total_words=total_words,
        track_name=lyrics_data.metadata.track_name or "",
        artist_names=lyrics_data.metadata.artist_names or "",
    )


def filter_irrelevant_sources(
    lyrics_results: Dict[str, LyricsData],
    anchor_sequences: List[AnchorSequence],
    min_relevance: float = MIN_REFERENCE_RELEVANCE,
    logger: logging.Logger = None,
) -> Tuple[Dict[str, LyricsData], Dict[str, SourceRelevanceResult]]:
    """Filter out reference sources below the relevance threshold.

    Args:
        lyrics_results: Dict mapping source name to LyricsData.
        anchor_sequences: Anchor sequences from the anchor finder.
        min_relevance: Minimum relevance score to keep a source.
        logger: Optional logger.

    Returns:
        Tuple of (filtered_lyrics_results, rejected_sources).
        filtered_lyrics_results: Sources that passed the threshold.
        rejected_sources: Dict mapping source name to SourceRelevanceResult for rejected sources.
    """
    if not logger:
        logger = logging.getLogger(__name__)

    filtered = {}
    rejected = {}

    for source_name, lyrics_data in lyrics_results.items():
        result = compute_source_relevance(source_name, lyrics_data, anchor_sequences)

        if result.relevance >= min_relevance:
            filtered[source_name] = lyrics_data
            logger.info(
                f"Source '{source_name}' passed relevance filter: "
                f"{result.relevance:.1%} ({result.matched_words}/{result.total_words} words)"
            )
        else:
            rejected[source_name] = result
            logger.info(
                f"Source '{source_name}' rejected by relevance filter: "
                f"{result.relevance:.1%} ({result.matched_words}/{result.total_words} words)"
            )

    return filtered, rejected
