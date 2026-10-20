"""Reference lyrics relevance scoring and filtering.

Computes how well each reference source matches the transcription based on
anchor sequence coverage. Sources below the relevance threshold are filtered
out to prevent wrong-song lyrics from polluting corrections.

Anchor coverage undercounts songs that repeat the same lines: correct references for
Wolfmother "Apple Tree", Deacon Blue, UB40 "Wedding Day" etc. scored 25-30% and were
dropped. A source the anchor filter rejects is therefore rescued when its word-trigram
overlap with the transcription (repeats counted, filler like "na na na" excluded) is high.
Calibrated on 459 production jobs (2026-10-19): wrong-song pairs max 9.4% (p99.99 5.5%,
491k cross-job pairs), the 22 sources it rescues were all the correct song (26-88%).
The rescue can only add sources; it never rejects one the anchor filter keeps.
"""
import logging
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from karaoke_gen.lyrics_transcriber.types import AnchorSequence, LyricsData

# Default threshold — determined by empirical analysis of production jobs.
# See scripts/analyze_reference_relevance.py and the design spec for methodology.
MIN_REFERENCE_RELEVANCE = 0.30  # Empirical: wrong-song max 3.5%, correct-song min 26.8%

# N-gram rescue (see module docstring): ~2.7x the worst wrong-song score seen.
MIN_NGRAM_RELEVANCE = 0.25
MIN_NGRAM_REFERENCE = 20  # distinct-word trigrams in the reference; tiny references score by chance
MIN_NGRAM_MATCHES = 10
MIN_NGRAM_DISTINCT_MATCHES = 15  # distinct matched trigrams: a shared refrain alone can't pass (rescues had 22+, bar one 8-phrase D&B track left rejected)
_NGRAM = 3


@dataclass
class SourceRelevanceResult:
    """Relevance scoring result for a single reference source."""
    source: str
    relevance: float  # 0.0 to 1.0 — fraction of reference words in anchors
    matched_words: int
    total_words: int
    track_name: str = ""
    artist_names: str = ""
    ngram_relevance: Optional[float] = None  # set when the n-gram rescue was evaluated


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


def _is_word_char(ch: str) -> bool:
    # Letters/digits plus combining marks (Devanagari vowel signs etc.), which \w drops.
    return ch.isalnum() or ch == "'" or unicodedata.category(ch).startswith("M")


def _words(text: str) -> list:
    text = unicodedata.normalize("NFC", text.lower().replace("\u2019", "'"))
    words, current = [], []
    for ch in text:
        if _is_word_char(ch):
            current.append(ch)
        elif current:
            words.append("".join(current).strip("'"))
            current = []
    if current:
        words.append("".join(current).strip("'"))
    return [w for w in words if w]


def _trigrams(text: str) -> Counter:
    """Word trigrams with three distinct words (so "na na na" / "oh oh oh" don't count)."""
    words = _words(text)
    grams = (tuple(words[i:i + _NGRAM]) for i in range(len(words) - _NGRAM + 1))
    return Counter(g for g in grams if len(set(g)) == _NGRAM)


def compute_ngram_relevance(lyrics_data: LyricsData, transcribed_text: str) -> Tuple[float, int, int, int]:
    """(score, matched, total, distinct_matched): share of the reference's trigrams, repeats
    included, found in the transcription, and how many different trigrams that match covers."""
    reference_text = " ".join(w.text for seg in lyrics_data.segments for w in seg.words)
    ref = _trigrams(reference_text)
    total = sum(ref.values())
    if not total:
        return 0.0, 0, 0, 0
    common = ref & _trigrams(transcribed_text)
    matched = sum(common.values())
    return matched / total, matched, total, len(common)


def _ngram_rescues(score: float, matched: int, total: int, distinct_matched: int) -> bool:
    return (score >= MIN_NGRAM_RELEVANCE and total >= MIN_NGRAM_REFERENCE
            and matched >= MIN_NGRAM_MATCHES and distinct_matched >= MIN_NGRAM_DISTINCT_MATCHES)


def filter_irrelevant_sources(
    lyrics_results: Dict[str, LyricsData],
    anchor_sequences: List[AnchorSequence],
    min_relevance: float = MIN_REFERENCE_RELEVANCE,
    logger: logging.Logger = None,
    transcribed_text: Optional[str] = None,
) -> Tuple[Dict[str, LyricsData], Dict[str, SourceRelevanceResult]]:
    """Filter out reference sources below the relevance threshold.

    Args:
        lyrics_results: Dict mapping source name to LyricsData.
        anchor_sequences: Anchor sequences from the anchor finder.
        min_relevance: Minimum relevance score to keep a source.
        logger: Optional logger.
        transcribed_text: The transcription. When given, a source below ``min_relevance``
            is still kept if its trigram overlap passes the n-gram rescue.

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
            if transcribed_text:
                score, matched, total, distinct = compute_ngram_relevance(lyrics_data, transcribed_text)
                result.ngram_relevance = score
                if _ngram_rescues(score, matched, total, distinct):
                    filtered[source_name] = lyrics_data
                    logger.info(
                        f"Source '{source_name}' kept by n-gram rescue: anchor relevance "
                        f"{result.relevance:.1%} ({result.matched_words}/{result.total_words} words), "
                        f"trigram overlap {score:.1%} ({matched}/{total})"
                    )
                    continue
            rejected[source_name] = result
            ngram = f", trigram overlap {result.ngram_relevance:.1%}" if result.ngram_relevance is not None else ""
            logger.info(
                f"Source '{source_name}' rejected by relevance filter: "
                f"{result.relevance:.1%} ({result.matched_words}/{result.total_words} words){ngram}"
            )

    return filtered, rejected
