# Reference Lyrics Search & Relevance Filtering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add relevance filtering to reject wrong-song reference lyrics, a search-with-alternate-artist/title endpoint, and improved frontend UX with tabbed Search/Paste modal and empty-state inline UI.

**Architecture:** Relevance filtering is added inside `LyricsCorrector.run()` after anchor finding — sources below a configurable threshold are removed before corrections are computed. A new `search-lyrics` API endpoint creates lyrics providers with user-supplied artist/title, fetches from all providers, and runs through the same correction pipeline. The frontend `AddLyricsModal` is redesigned with Search/Paste tabs, and the Reference panel shows an inline add-lyrics UI when no valid sources exist.

**Tech Stack:** Python (FastAPI, pytest), TypeScript (Next.js, React, Jest/RTL), Firestore, GCS

**Spec:** `docs/superpowers/specs/2026-03-29-reference-lyrics-search-design.md`

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `karaoke_gen/lyrics_transcriber/correction/relevance.py` | Per-source relevance scoring and filtering logic |
| `tests/correction/test_relevance.py` | Unit tests for relevance filtering |
| `backend/tests/test_search_lyrics.py` | Unit tests for the search-lyrics endpoint |
| `frontend/components/lyrics-review/modals/SearchLyricsTab.tsx` | Search tab component (artist/title inputs, results display) |
| `frontend/components/lyrics-review/modals/PasteLyricsTab.tsx` | Paste tab component (extracted from current AddLyricsModal) |
| `frontend/components/lyrics-review/ReferenceEmptyState.tsx` | Inline add-lyrics UI for empty reference panel |
| `scripts/analyze_reference_relevance.py` | One-off script to analyze prod jobs for threshold tuning |

### Modified Files

| File | Changes |
|------|---------|
| `karaoke_gen/lyrics_transcriber/correction/corrector.py` | Call relevance filter after anchor finding, remove filtered sources before corrections |
| `karaoke_gen/lyrics_transcriber/correction/operations.py` | New `search_lyrics_sources()` static method; add `force` param to `add_lyrics_source()` |
| `backend/api/routes/review.py` | New `POST /{job_id}/search-lyrics` endpoint; add `force` param to `add-lyrics` |
| `frontend/components/lyrics-review/modals/AddLyricsModal.tsx` | Rewrite as tabbed container for SearchLyricsTab + PasteLyricsTab |
| `frontend/components/lyrics-review/ReferenceView.tsx` | Show ReferenceEmptyState when no sources exist |
| `frontend/components/lyrics-review/LyricsAnalyzer.tsx` | Add `handleSearchLyrics` handler, wire up new modal props |
| `frontend/lib/api.ts` | New `searchLyrics()` method |
| `frontend/lib/lyrics-review/types.ts` | New `SearchLyricsResponse` and `RejectedSource` types |

---

## Task 0: Empirical Threshold Analysis

**Files:**
- Create: `scripts/analyze_reference_relevance.py`

This task determines the right `MIN_REFERENCE_RELEVANCE` threshold from real production data. Must be done first since the threshold value is used in all subsequent tasks.

- [ ] **Step 0.1: Write the analysis script**

```python
# scripts/analyze_reference_relevance.py
"""
Analyze reference lyrics relevance across production jobs.

Pulls ~30 jobs from Firestore, downloads their corrections.json from GCS,
computes per-source relevance scores, and generates a report for human review.

Usage:
    python scripts/analyze_reference_relevance.py
"""
import json
import os
import sys
import tempfile
from collections import defaultdict

from google.cloud import firestore, storage


def get_total_words(source_data: dict) -> int:
    """Count total words across all segments in a reference source."""
    total = 0
    for segment in source_data.get("segments", []):
        total += len(segment.get("words", []))
    return total


def get_anchor_word_ids_for_source(anchor_sequences: list, source_name: str) -> set:
    """Get all reference word IDs that appear in anchor sequences for a given source."""
    word_ids = set()
    for anchor in anchor_sequences:
        ref_word_ids = anchor.get("reference_word_ids", {})
        if source_name in ref_word_ids:
            word_ids.update(ref_word_ids[source_name])
    return word_ids


def compute_relevance(source_data: dict, anchor_sequences: list, source_name: str) -> float:
    """Compute relevance score: fraction of reference words appearing in anchors."""
    total_words = get_total_words(source_data)
    if total_words == 0:
        return 0.0
    matched_word_ids = get_anchor_word_ids_for_source(anchor_sequences, source_name)
    return len(matched_word_ids) / total_words


def main():
    db = firestore.Client(project="nomadkaraoke")
    gcs = storage.Client(project="nomadkaraoke")
    bucket = gcs.bucket("karaoke-gen-outputs")

    # Query jobs that have corrections and are in review states
    jobs_ref = db.collection("jobs")
    query = jobs_ref.where("status", "in", ["awaiting_review", "in_review", "review_completed"]).limit(100)
    jobs = list(query.stream())

    print(f"Found {len(jobs)} jobs in review states")

    results = []

    for job_doc in jobs:
        job = job_doc.to_dict()
        job_id = job_doc.id
        corrections_path = job.get("file_urls", {}).get("lyrics", {}).get("corrections")
        if not corrections_path:
            continue

        # Download corrections.json
        blob = bucket.blob(corrections_path)
        if not blob.exists():
            continue

        try:
            data = json.loads(blob.download_as_text())
        except Exception as e:
            print(f"  Error downloading {job_id}: {e}")
            continue

        reference_lyrics = data.get("reference_lyrics", {})
        anchor_sequences = data.get("anchor_sequences", [])
        original_segments = data.get("original_segments", [])

        if not reference_lyrics or not anchor_sequences:
            continue

        # Get first 2 lines of transcription for context
        trans_preview = " | ".join(
            seg.get("text", "")[:60] for seg in original_segments[:2]
        )

        artist = data.get("metadata", {}).get("artist", "unknown")
        title = data.get("metadata", {}).get("title", "unknown")

        for source_name, source_data in reference_lyrics.items():
            relevance = compute_relevance(source_data, anchor_sequences, source_name)
            total_words = get_total_words(source_data)
            matched_count = len(get_anchor_word_ids_for_source(anchor_sequences, source_name))

            # Get first 2 lines of reference for context
            ref_preview = " | ".join(
                seg.get("text", "")[:60] for seg in source_data.get("segments", [])[:2]
            )

            results.append({
                "job_id": job_id,
                "artist": artist,
                "title": title,
                "source": source_name,
                "relevance": relevance,
                "matched_words": matched_count,
                "total_words": total_words,
                "trans_preview": trans_preview,
                "ref_preview": ref_preview,
            })

        if len(results) >= 60:  # ~30 jobs × 2 sources each
            break

    # Sort by relevance score to make threshold analysis easy
    results.sort(key=lambda r: r["relevance"])

    # Print report
    print(f"\n{'='*120}")
    print(f"REFERENCE LYRICS RELEVANCE ANALYSIS — {len(results)} source entries from {len(set(r['job_id'] for r in results))} jobs")
    print(f"{'='*120}\n")

    for r in results:
        print(f"Job: {r['job_id'][:12]}  Source: {r['source']:<12}  "
              f"Relevance: {r['relevance']:.1%} ({r['matched_words']}/{r['total_words']} words)")
        print(f"  Track: {r['artist']} - {r['title']}")
        print(f"  Transcription: {r['trans_preview'][:80]}")
        print(f"  Reference:     {r['ref_preview'][:80]}")
        print()

    # Summary stats
    scores = [r["relevance"] for r in results]
    if scores:
        print(f"\n{'='*120}")
        print(f"SUMMARY")
        print(f"  Min: {min(scores):.1%}  Max: {max(scores):.1%}  Mean: {sum(scores)/len(scores):.1%}")
        print(f"  Scores < 10%: {sum(1 for s in scores if s < 0.10)}")
        print(f"  Scores < 20%: {sum(1 for s in scores if s < 0.20)}")
        print(f"  Scores < 30%: {sum(1 for s in scores if s < 0.30)}")
        print(f"  Scores < 40%: {sum(1 for s in scores if s < 0.40)}")
        print(f"  Scores < 50%: {sum(1 for s in scores if s < 0.50)}")
        print(f"{'='*120}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 0.2: Run the analysis script**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-reference-lyrics-search && python scripts/analyze_reference_relevance.py`

Review the output with the user. For each low-scoring source, the user classifies it as "correct song" or "wrong song". Determine the threshold that best separates the two groups.

- [ ] **Step 0.3: Record the threshold**

Based on the analysis, set the `MIN_REFERENCE_RELEVANCE` constant value. This value is used in Task 1.

- [ ] **Step 0.4: Commit**

```bash
git add scripts/analyze_reference_relevance.py
git commit -m "feat: add reference lyrics relevance analysis script"
```

---

## Task 1: Relevance Filtering Module

**Files:**
- Create: `karaoke_gen/lyrics_transcriber/correction/relevance.py`
- Create: `tests/correction/test_relevance.py`

This task creates the standalone relevance scoring and filtering logic. It's a pure function module with no side effects — given anchor sequences and reference lyrics, it returns which sources pass and which are rejected.

- [ ] **Step 1.1: Write failing tests for relevance scoring**

```python
# tests/correction/test_relevance.py
import pytest
from karaoke_gen.lyrics_transcriber.correction.relevance import (
    compute_source_relevance,
    filter_irrelevant_sources,
    SourceRelevanceResult,
)
from karaoke_gen.lyrics_transcriber.types import (
    AnchorSequence,
    LyricsData,
    LyricsMetadata,
    LyricsSegment,
    Word,
)


def _make_words(texts: list[str], id_prefix: str = "w") -> list[Word]:
    """Create Word objects from text strings."""
    return [
        Word(id=f"{id_prefix}_{i}", text=t, start_time=0.0, end_time=0.0)
        for i, t in enumerate(texts)
    ]


def _make_lyrics_data(word_texts: list[list[str]], source: str) -> LyricsData:
    """Create LyricsData from a list of lines (each line is a list of word strings)."""
    segments = []
    for i, line_words in enumerate(word_texts):
        words = _make_words(line_words, id_prefix=f"{source}_s{i}_w")
        segments.append(
            LyricsSegment(
                id=f"{source}_seg_{i}",
                text=" ".join(line_words),
                words=words,
                start_time=0.0,
                end_time=0.0,
            )
        )
    return LyricsData(
        segments=segments,
        metadata=LyricsMetadata(source=source, track_name="Test", artist_names="Test"),
        source=source,
    )


def _make_anchor(
    trans_word_ids: list[str],
    reference_word_ids: dict[str, list[str]],
    confidence: float = 1.0,
) -> AnchorSequence:
    """Create an AnchorSequence with given word ID mappings."""
    return AnchorSequence(
        id=f"anchor_{trans_word_ids[0]}",
        transcribed_word_ids=trans_word_ids,
        transcription_position=0,
        reference_positions={s: 0 for s in reference_word_ids},
        reference_word_ids=reference_word_ids,
        confidence=confidence,
    )


class TestComputeSourceRelevance:
    def test_high_match_returns_high_score(self):
        """Source with 8/10 words in anchors scores 0.8."""
        lyrics = _make_lyrics_data([["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]], "genius")
        all_word_ids = [w.id for seg in lyrics.segments for w in seg.words]
        # 8 of 10 words matched
        anchors = [
            _make_anchor(["t1", "t2"], {"genius": all_word_ids[0:2]}),
            _make_anchor(["t3", "t4", "t5"], {"genius": all_word_ids[2:5]}),
            _make_anchor(["t6", "t7", "t8"], {"genius": all_word_ids[5:8]}),
        ]
        result = compute_source_relevance("genius", lyrics, anchors)
        assert result.relevance == pytest.approx(0.8)
        assert result.matched_words == 8
        assert result.total_words == 10

    def test_no_match_returns_zero(self):
        """Source with no words in any anchors scores 0.0."""
        lyrics = _make_lyrics_data([["a", "b", "c"]], "spotify")
        anchors = [
            _make_anchor(["t1"], {"genius": ["other_id"]}),  # Different source
        ]
        result = compute_source_relevance("spotify", lyrics, anchors)
        assert result.relevance == 0.0
        assert result.matched_words == 0

    def test_empty_source_returns_zero(self):
        """Source with zero words scores 0.0."""
        lyrics = _make_lyrics_data([], "empty")
        result = compute_source_relevance("empty", lyrics, [])
        assert result.relevance == 0.0


class TestFilterIrrelevantSources:
    def test_filters_below_threshold(self):
        """Sources below threshold are removed from lyrics_results."""
        good_lyrics = _make_lyrics_data([["a", "b", "c", "d", "e"]], "genius")
        bad_lyrics = _make_lyrics_data([["x", "y", "z", "q", "r"]], "spotify")
        good_word_ids = [w.id for seg in good_lyrics.segments for w in seg.words]

        lyrics_results = {"genius": good_lyrics, "spotify": bad_lyrics}
        anchors = [
            _make_anchor(["t1", "t2", "t3", "t4"], {"genius": good_word_ids[0:4]}),
        ]

        filtered, rejected = filter_irrelevant_sources(
            lyrics_results, anchors, min_relevance=0.5
        )
        assert "genius" in filtered
        assert "spotify" not in filtered
        assert "spotify" in rejected
        assert rejected["spotify"].relevance == 0.0

    def test_keeps_all_above_threshold(self):
        """All sources above threshold are kept."""
        lyrics_a = _make_lyrics_data([["a", "b"]], "src_a")
        lyrics_b = _make_lyrics_data([["a", "b"]], "src_b")
        a_ids = [w.id for seg in lyrics_a.segments for w in seg.words]
        b_ids = [w.id for seg in lyrics_b.segments for w in seg.words]

        anchors = [
            _make_anchor(["t1", "t2"], {"src_a": a_ids, "src_b": b_ids}),
        ]
        filtered, rejected = filter_irrelevant_sources(
            {"src_a": lyrics_a, "src_b": lyrics_b}, anchors, min_relevance=0.5
        )
        assert len(filtered) == 2
        assert len(rejected) == 0

    def test_empty_input_returns_empty(self):
        """No sources in, no sources out."""
        filtered, rejected = filter_irrelevant_sources({}, [], min_relevance=0.5)
        assert filtered == {}
        assert rejected == {}
```

- [ ] **Step 1.2: Run tests to verify they fail**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-reference-lyrics-search && python -m pytest tests/correction/test_relevance.py -v`
Expected: FAIL — module does not exist yet.

- [ ] **Step 1.3: Implement the relevance module**

```python
# karaoke_gen/lyrics_transcriber/correction/relevance.py
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
MIN_REFERENCE_RELEVANCE = 0.30  # TODO: Update after Task 0 analysis


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

    # Collect all reference word IDs for this source across all anchors
    all_ref_word_ids = set()
    for anchor in anchor_sequences:
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
```

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-reference-lyrics-search && python -m pytest tests/correction/test_relevance.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 1.5: Commit**

```bash
git add karaoke_gen/lyrics_transcriber/correction/relevance.py tests/correction/test_relevance.py
git commit -m "feat: add reference lyrics relevance scoring and filtering module"
```

---

## Task 2: Integrate Relevance Filtering into LyricsCorrector

**Files:**
- Modify: `karaoke_gen/lyrics_transcriber/correction/corrector.py:107-221`
- Modify: `tests/correction/test_relevance.py` (add integration-style test)

This task wires the relevance filter into the correction pipeline so that all code paths (initial processing, add-lyrics, search-lyrics) benefit from filtering.

- [ ] **Step 2.1: Write failing test for corrector filtering**

Add to `tests/correction/test_relevance.py`:

```python
class TestCorrectorRelevanceIntegration:
    """Test that LyricsCorrector filters irrelevant sources."""

    def test_corrector_removes_irrelevant_source(self, tmp_path):
        """A source with 0% match should be excluded from the CorrectionResult."""
        from karaoke_gen.lyrics_transcriber.correction.corrector import LyricsCorrector
        from karaoke_gen.lyrics_transcriber.types import TranscriptionData, TranscriptionResult

        # Transcription: "hello world foo bar"
        trans_words = _make_words(["hello", "world", "foo", "bar"], "trans")
        trans_segment = LyricsSegment(
            id="trans_seg_0", text="hello world foo bar",
            words=trans_words, start_time=0.0, end_time=1.0,
        )
        transcription = TranscriptionData(
            segments=[trans_segment],
            words=trans_words,
            text="hello world foo bar",
            source="test",
        )

        # Good source: matching words
        good_lyrics = _make_lyrics_data([["hello", "world", "foo", "bar"]], "good_source")
        # Bad source: completely different words
        bad_lyrics = _make_lyrics_data([["alpha", "beta", "gamma", "delta"]], "bad_source")

        corrector = LyricsCorrector(cache_dir=str(tmp_path))
        result = corrector.run(
            transcription_results=[TranscriptionResult(name="test", priority=1, result=transcription)],
            lyrics_results={"good_source": good_lyrics, "bad_source": bad_lyrics},
        )

        # Good source should be in result, bad source should not
        assert "good_source" in result.reference_lyrics
        assert "bad_source" not in result.reference_lyrics

        # Rejected sources should be logged in metadata
        assert "rejected_sources" in result.metadata
        assert "bad_source" in result.metadata["rejected_sources"]
```

- [ ] **Step 2.2: Run test to verify it fails**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-reference-lyrics-search && python -m pytest tests/correction/test_relevance.py::TestCorrectorRelevanceIntegration -v`
Expected: FAIL — corrector doesn't filter sources yet.

- [ ] **Step 2.3: Modify LyricsCorrector.run() to apply relevance filtering**

In `karaoke_gen/lyrics_transcriber/correction/corrector.py`, add the import at the top:

```python
from karaoke_gen.lyrics_transcriber.correction.relevance import filter_irrelevant_sources
```

Then modify the `run()` method. After the anchor finding (line 157) and before gap finding, insert the relevance filter. Replace lines 156-160 with:

```python
                anchor_sequences = self.anchor_finder.find_anchors(transcribed_text, lyrics_results, primary_transcription_result)

                # Filter irrelevant reference sources based on anchor match quality
                filtered_lyrics, rejected_sources = filter_irrelevant_sources(
                    lyrics_results, anchor_sequences, logger=self.logger,
                )

                # If sources were filtered, re-run anchor finding with only valid sources
                if len(filtered_lyrics) < len(lyrics_results):
                    self.logger.info(
                        f"Relevance filter: {len(lyrics_results)} -> {len(filtered_lyrics)} sources "
                        f"(rejected: {', '.join(rejected_sources.keys())})"
                    )
                    if filtered_lyrics:
                        # Re-find anchors with only the valid sources
                        self._anchor_finder = None  # Reset to clear cached state
                        anchor_sequences = self.anchor_finder.find_anchors(
                            transcribed_text, filtered_lyrics, primary_transcription_result
                        )
                    else:
                        anchor_sequences = []

                    # Update lyrics_results to only include filtered sources
                    lyrics_results = filtered_lyrics

                gap_sequences = self.anchor_finder.find_gaps(transcribed_text, anchor_sequences, lyrics_results, primary_transcription_result)
                if anchor_span:
                    anchor_span.set_attribute("anchor_count", len(anchor_sequences))
                    anchor_span.set_attribute("gap_count", len(gap_sequences))
                    anchor_span.set_attribute("sources_rejected", len(rejected_sources))
```

Then in the metadata section (around line 192), add the rejected sources info. After the `result_metadata` dict is built, add:

```python
            # Add rejected sources to metadata for debugging and frontend display
            if rejected_sources:
                result_metadata["rejected_sources"] = {
                    name: {
                        "relevance": result.relevance,
                        "matched_words": result.matched_words,
                        "total_words": result.total_words,
                        "track_name": result.track_name,
                        "artist_names": result.artist_names,
                    }
                    for name, result in rejected_sources.items()
                }
```

Also update the `self.reference_lyrics = lyrics_results` on line 146 — move it to AFTER the filtering so handlers use the filtered set:

Remove line 146 (`self.reference_lyrics = lyrics_results`) and add it after the filtering block:

```python
                    # Update lyrics_results to only include filtered sources
                    lyrics_results = filtered_lyrics

                # Store filtered reference lyrics for use in word map
                self.reference_lyrics = lyrics_results
```

- [ ] **Step 2.4: Run test to verify it passes**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-reference-lyrics-search && python -m pytest tests/correction/test_relevance.py -v`
Expected: All tests PASS.

- [ ] **Step 2.5: Run full backend test suite to check for regressions**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-reference-lyrics-search && python -m pytest tests/ backend/tests/ -v --timeout=60 2>&1 | tail -50`
Expected: All existing tests PASS. If any fail due to the new filtering behavior, investigate — some tests may have reference lyrics that don't match their transcription and now get filtered. Fix by either adjusting test data or setting the threshold appropriately for test scenarios.

- [ ] **Step 2.6: Commit**

```bash
git add karaoke_gen/lyrics_transcriber/correction/corrector.py tests/correction/test_relevance.py
git commit -m "feat: integrate relevance filtering into LyricsCorrector pipeline"
```

---

## Task 3: Search Lyrics Operation + API Endpoint

**Files:**
- Modify: `karaoke_gen/lyrics_transcriber/correction/operations.py`
- Modify: `backend/api/routes/review.py`
- Modify: `backend/config.py` (if needed for provider config access)
- Create: `backend/tests/test_search_lyrics.py`

This task adds the backend capability to search for lyrics with an alternate artist/title and the API endpoint that the frontend calls.

- [ ] **Step 3.1: Write failing test for search_lyrics_sources operation**

```python
# backend/tests/test_search_lyrics.py
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations
from karaoke_gen.lyrics_transcriber.types import (
    CorrectionResult, LyricsData, LyricsMetadata, LyricsSegment, Word,
    TranscriptionData, TranscriptionResult,
)


def _make_words(texts, prefix="w"):
    return [Word(id=f"{prefix}_{i}", text=t, start_time=0.0, end_time=0.0) for i, t in enumerate(texts)]


def _make_correction_result(transcription_words=None):
    """Create a minimal CorrectionResult for testing."""
    if transcription_words is None:
        transcription_words = ["hello", "world", "this", "is", "a", "test"]
    words = _make_words(transcription_words, "trans")
    segment = LyricsSegment(
        id="seg_0", text=" ".join(transcription_words),
        words=words, start_time=0.0, end_time=1.0,
    )
    return CorrectionResult(
        original_segments=[segment],
        corrected_segments=[segment],
        corrections=[],
        corrections_made=0,
        confidence=1.0,
        reference_lyrics={},
        anchor_sequences=[],
        gap_sequences=[],
        resized_segments=[],
        metadata={"enabled_handlers": []},
        correction_steps=[],
        word_id_map={},
        segment_id_map={},
    )


def _make_lyrics_data(word_texts, source):
    segments = []
    for i, line in enumerate(word_texts):
        words = _make_words(line, f"{source}_s{i}")
        segments.append(LyricsSegment(
            id=f"{source}_seg_{i}", text=" ".join(line),
            words=words, start_time=0.0, end_time=0.0,
        ))
    return LyricsData(
        segments=segments,
        metadata=LyricsMetadata(source=source, track_name="Test Song", artist_names="Test Artist"),
        source=source,
    )


class TestSearchLyricsSources:
    def test_returns_added_and_rejected_sources(self, tmp_path):
        """search_lyrics_sources should return which sources were added vs rejected."""
        correction_result = _make_correction_result(["hello", "world", "foo", "bar"])

        # Mock providers: one returns matching lyrics, one returns non-matching
        good_lyrics = _make_lyrics_data([["hello", "world", "foo", "bar"]], "genius")
        bad_lyrics = _make_lyrics_data([["alpha", "beta", "gamma", "delta"]], "spotify")

        mock_providers = {
            "genius": MagicMock(**{"fetch_lyrics.return_value": good_lyrics, "get_name.return_value": "genius"}),
            "spotify": MagicMock(**{"fetch_lyrics.return_value": bad_lyrics, "get_name.return_value": "spotify"}),
        }

        with patch(
            "karaoke_gen.lyrics_transcriber.correction.operations.create_lyrics_providers",
            return_value=mock_providers,
        ):
            updated_result, sources_added, sources_rejected, sources_not_found = (
                CorrectionOperations.search_lyrics_sources(
                    correction_result=correction_result,
                    artist="Test Artist",
                    title="Test Song",
                    cache_dir=str(tmp_path),
                )
            )

        assert "genius" in sources_added
        assert "spotify" in sources_rejected
        assert "genius" in updated_result.reference_lyrics

    def test_force_sources_bypass_threshold(self, tmp_path):
        """Sources in force_sources should be added even with low relevance."""
        correction_result = _make_correction_result(["hello", "world", "foo", "bar"])
        bad_lyrics = _make_lyrics_data([["alpha", "beta", "gamma", "delta"]], "spotify")

        mock_providers = {
            "spotify": MagicMock(**{"fetch_lyrics.return_value": bad_lyrics, "get_name.return_value": "spotify"}),
        }

        with patch(
            "karaoke_gen.lyrics_transcriber.correction.operations.create_lyrics_providers",
            return_value=mock_providers,
        ):
            updated_result, sources_added, sources_rejected, sources_not_found = (
                CorrectionOperations.search_lyrics_sources(
                    correction_result=correction_result,
                    artist="Test",
                    title="Test",
                    cache_dir=str(tmp_path),
                    force_sources=["spotify"],
                )
            )

        assert "spotify" in sources_added
        assert len(sources_rejected) == 0

    def test_no_results_from_providers(self, tmp_path):
        """When no provider returns lyrics, all are in sources_not_found."""
        correction_result = _make_correction_result()

        mock_providers = {
            "genius": MagicMock(**{"fetch_lyrics.return_value": None, "get_name.return_value": "genius"}),
            "lrclib": MagicMock(**{"fetch_lyrics.return_value": None, "get_name.return_value": "lrclib"}),
        }

        with patch(
            "karaoke_gen.lyrics_transcriber.correction.operations.create_lyrics_providers",
            return_value=mock_providers,
        ):
            updated_result, sources_added, sources_rejected, sources_not_found = (
                CorrectionOperations.search_lyrics_sources(
                    correction_result=correction_result,
                    artist="Unknown",
                    title="Unknown",
                    cache_dir=str(tmp_path),
                )
            )

        assert len(sources_added) == 0
        assert len(sources_rejected) == 0
        assert "genius" in sources_not_found
        assert "lrclib" in sources_not_found
```

- [ ] **Step 3.2: Run tests to verify they fail**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-reference-lyrics-search && python -m pytest backend/tests/test_search_lyrics.py -v`
Expected: FAIL — `search_lyrics_sources` and `create_lyrics_providers` don't exist yet.

- [ ] **Step 3.3: Create provider factory function and implement search_lyrics_sources**

First, add the provider factory. Create or add to an appropriate location — adding to `operations.py` since it's the only consumer:

Add these imports to the top of `karaoke_gen/lyrics_transcriber/correction/operations.py`:

```python
from karaoke_gen.lyrics_transcriber.lyrics.base_lyrics_provider import BaseLyricsProvider, LyricsProviderConfig
from karaoke_gen.lyrics_transcriber.lyrics.genius import GeniusProvider
from karaoke_gen.lyrics_transcriber.lyrics.spotify import SpotifyProvider
from karaoke_gen.lyrics_transcriber.lyrics.musixmatch import MusixmatchProvider
from karaoke_gen.lyrics_transcriber.lyrics.lrclib import LRCLIBProvider
from karaoke_gen.lyrics_transcriber.correction.relevance import (
    compute_source_relevance,
    filter_irrelevant_sources,
    SourceRelevanceResult,
)
```

Then add the factory function before the `CorrectionOperations` class:

```python
def create_lyrics_providers(
    cache_dir: str,
    logger: Optional[logging.Logger] = None,
) -> Dict[str, BaseLyricsProvider]:
    """Create all available lyrics providers from environment config.

    Providers requiring API keys are only included if the keys are set.
    """
    import os

    config = LyricsProviderConfig(
        genius_api_token=os.getenv("GENIUS_API_KEY"),
        rapidapi_key=os.getenv("RAPIDAPI_KEY"),
        spotify_cookie=os.getenv("SPOTIFY_COOKIE_SP_DC"),
        cache_dir=cache_dir,
    )

    providers: Dict[str, BaseLyricsProvider] = {}

    # LRCLIB is always available (no API key required)
    providers["lrclib"] = LRCLIBProvider(config=config, logger=logger)

    if config.genius_api_token:
        providers["genius"] = GeniusProvider(config=config, logger=logger)

    if config.spotify_cookie:
        providers["spotify"] = SpotifyProvider(config=config, logger=logger)

    if config.rapidapi_key:
        providers["musixmatch"] = MusixmatchProvider(config=config, logger=logger)

    return providers
```

Then add the `search_lyrics_sources` method to `CorrectionOperations`:

```python
    @staticmethod
    def search_lyrics_sources(
        correction_result: CorrectionResult,
        artist: str,
        title: str,
        cache_dir: str,
        force_sources: Optional[List[str]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> tuple:
        """Search for lyrics with alternate artist/title and add passing sources.

        Args:
            correction_result: Current correction result.
            artist: Artist name to search with.
            title: Track title to search with.
            cache_dir: Cache directory for providers and correction.
            force_sources: Source names to add regardless of relevance threshold.
            logger: Optional logger.

        Returns:
            Tuple of (updated_result, sources_added, sources_rejected, sources_not_found).
            - updated_result: CorrectionResult with new sources added (or original if none added).
            - sources_added: List of source names that were added.
            - sources_rejected: Dict of source name -> SourceRelevanceResult for rejected sources.
            - sources_not_found: List of source names that returned no lyrics.
        """
        if not logger:
            logger = logging.getLogger(__name__)

        force_sources = force_sources or []
        sources_added = []
        sources_rejected = {}
        sources_not_found = []

        # Create providers and fetch lyrics
        providers = create_lyrics_providers(cache_dir=cache_dir, logger=logger)
        logger.info(f"Searching {len(providers)} providers for '{artist}' - '{title}'")

        fetched_lyrics = {}
        for name, provider in providers.items():
            # Skip sources already in the correction result
            if name in correction_result.reference_lyrics:
                logger.info(f"Skipping '{name}' — already in reference lyrics")
                continue

            try:
                lyrics_data = provider.fetch_lyrics(artist, title)
                if lyrics_data:
                    fetched_lyrics[name] = lyrics_data
                    logger.info(f"Provider '{name}' returned lyrics ({len(lyrics_data.segments)} segments)")
                else:
                    sources_not_found.append(name)
                    logger.info(f"Provider '{name}' returned no results")
            except Exception as e:
                sources_not_found.append(name)
                logger.warning(f"Provider '{name}' failed: {e}")

        if not fetched_lyrics:
            return correction_result, sources_added, sources_rejected, sources_not_found

        # Combine existing + new sources for correction
        combined_lyrics = correction_result.reference_lyrics.copy()
        combined_lyrics.update(fetched_lyrics)

        # Create transcription data from original segments
        transcription_data = TranscriptionData(
            segments=correction_result.original_segments,
            words=[w for seg in correction_result.original_segments for w in seg.words],
            text="\n".join(seg.text for seg in correction_result.original_segments),
            source="original",
        )

        # Get enabled handlers from metadata
        enabled_handlers = None
        if correction_result.metadata:
            enabled_handlers = correction_result.metadata.get("enabled_handlers")

        # Store audio hash
        audio_hash = correction_result.metadata.get("audio_hash") if correction_result.metadata else None

        # Run correction with all sources — the corrector's relevance filter handles rejection
        corrector = LyricsCorrector(
            cache_dir=cache_dir,
            enabled_handlers=enabled_handlers,
            logger=logger,
        )

        # For force_sources, we need to temporarily bypass the filter.
        # We do this by running correction normally, then checking what got rejected.
        updated_result = corrector.run(
            transcription_results=[TranscriptionResult(name="original", priority=1, result=transcription_data)],
            lyrics_results=combined_lyrics,
            metadata=correction_result.metadata,
        )

        # Check which new sources survived vs were rejected
        rejected_meta = updated_result.metadata.get("rejected_sources", {})
        for name in fetched_lyrics:
            if name in updated_result.reference_lyrics:
                sources_added.append(name)
            elif name in rejected_meta:
                if name in force_sources:
                    # Force-add: re-add to reference lyrics and re-run correction
                    sources_added.append(name)
                else:
                    sources_rejected[name] = SourceRelevanceResult(
                        source=name,
                        relevance=rejected_meta[name]["relevance"],
                        matched_words=rejected_meta[name]["matched_words"],
                        total_words=rejected_meta[name]["total_words"],
                        track_name=rejected_meta[name].get("track_name", ""),
                        artist_names=rejected_meta[name].get("artist_names", ""),
                    )

        # If there are force sources that were rejected, re-run with them included
        if any(name in force_sources for name in rejected_meta):
            force_lyrics = {
                name: fetched_lyrics[name]
                for name in force_sources
                if name in fetched_lyrics and name not in updated_result.reference_lyrics
            }
            if force_lyrics:
                force_combined = updated_result.reference_lyrics.copy()
                force_combined.update(force_lyrics)

                # Re-run correction skipping the relevance filter won't work since it's
                # built into the corrector. Instead, add them via add_lyrics_source.
                for name, lyrics_data in force_lyrics.items():
                    lyrics_text = lyrics_data.get_full_text()
                    updated_result = CorrectionOperations.add_lyrics_source(
                        correction_result=updated_result,
                        source=name,
                        lyrics_text=lyrics_text,
                        cache_dir=cache_dir,
                        logger=logger,
                        force=True,
                    )

        # Update metadata
        if not updated_result.metadata:
            updated_result.metadata = {}
        updated_result.metadata.update({
            "available_handlers": corrector.all_handlers,
            "enabled_handlers": [getattr(h, "name", h.__class__.__name__) for h in corrector.handlers],
        })
        if audio_hash:
            updated_result.metadata["audio_hash"] = audio_hash

        return updated_result, sources_added, sources_rejected, sources_not_found
```

Also update `add_lyrics_source` to accept a `force` parameter. Add `force: bool = False` to its signature and pass it through to skip relevance filtering. In the method, after the `corrector.run()` call, if `force=True` and the source was rejected, re-add it:

In `add_lyrics_source`, change the signature:

```python
    @staticmethod
    def add_lyrics_source(
        correction_result: CorrectionResult,
        source: str,
        lyrics_text: str,
        cache_dir: str,
        logger: Optional[logging.Logger] = None,
        force: bool = False,
    ) -> CorrectionResult:
```

After the `corrector.run()` call (around line 184), add:

```python
        # If force=True and the source was rejected by relevance filter, re-add it
        if force and source not in updated_result.reference_lyrics:
            logger.info(f"Force-adding source '{source}' despite low relevance")
            updated_result.reference_lyrics[source] = lyrics_data
            # Rejected sources metadata should note this was force-added
            if "rejected_sources" in updated_result.metadata:
                rejected_info = updated_result.metadata["rejected_sources"].pop(source, None)
                if rejected_info:
                    if "force_added_sources" not in updated_result.metadata:
                        updated_result.metadata["force_added_sources"] = {}
                    updated_result.metadata["force_added_sources"][source] = rejected_info
```

- [ ] **Step 3.4: Run tests to verify they pass**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-reference-lyrics-search && python -m pytest backend/tests/test_search_lyrics.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 3.5: Add the API endpoint**

In `backend/api/routes/review.py`, add the new endpoint after the existing `add_lyrics` endpoint. Add near the other imports:

```python
from karaoke_gen.lyrics_transcriber.correction.relevance import SourceRelevanceResult
```

Then add the endpoint:

```python
@router.post("/{job_id}/search-lyrics")
async def search_lyrics(
    job_id: str,
    data: Dict[str, Any],
    auth_info: Tuple[str, str] = Depends(require_review_auth),
):
    """Search for lyrics with alternate artist/title.

    Request body:
        artist: Artist name to search with.
        title: Track title to search with.
        force_sources: Optional list of source names to add regardless of threshold.
    """
    with create_span("review.search_lyrics", {"job_id": job_id}) as span:
        try:
            with job_log_context(job_id):
                job_manager = JobManager()
                job = job_manager.get_job(job_id)

                if job.status not in [JobStatus.AWAITING_REVIEW, JobStatus.IN_REVIEW]:
                    raise HTTPException(status_code=400, detail=f"Job must be in review state, currently: {job.status}")

                artist = data.get("artist", "").strip()
                title = data.get("title", "").strip()
                force_sources = data.get("force_sources", [])

                if not artist or not title:
                    raise HTTPException(status_code=400, detail="Artist and title are required")

                # Download current corrections
                storage = StorageService()
                corrections_path = job.file_urls.get("lyrics", {}).get("corrections")
                if not corrections_path:
                    raise HTTPException(status_code=404, detail="No corrections file found")

                original_data = storage.download_json(corrections_path)
                correction_result = CorrectionResult.from_dict(original_data)

                # Create temp cache dir
                import tempfile
                cache_dir = tempfile.mkdtemp(prefix="search_lyrics_")

                # Search and add sources
                updated_result, sources_added, sources_rejected, sources_not_found = (
                    CorrectionOperations.search_lyrics_sources(
                        correction_result=correction_result,
                        artist=artist,
                        title=title,
                        cache_dir=cache_dir,
                        force_sources=force_sources,
                        logger=logger,
                    )
                )

                if sources_added:
                    # Add metadata
                    if not updated_result.metadata:
                        updated_result.metadata = {}
                    audio_hash = original_data.get("metadata", {}).get("audio_hash")
                    if audio_hash:
                        updated_result.metadata["audio_hash"] = audio_hash
                    artist_meta = original_data.get("metadata", {}).get("artist")
                    if artist_meta:
                        updated_result.metadata["artist"] = artist_meta
                    title_meta = original_data.get("metadata", {}).get("title")
                    if title_meta:
                        updated_result.metadata["title"] = title_meta

                    # Upload updated corrections
                    updated_data = updated_result.to_dict()
                    storage.upload_json(corrections_path, updated_data)

                    return {
                        "status": "success",
                        "data": updated_data,
                        "sources_added": sources_added,
                        "sources_rejected": {
                            name: {
                                "relevance": r.relevance,
                                "matched_words": r.matched_words,
                                "total_words": r.total_words,
                                "track_name": r.track_name,
                                "artist_names": r.artist_names,
                            }
                            for name, r in sources_rejected.items()
                        },
                        "sources_not_found": sources_not_found,
                    }
                else:
                    return {
                        "status": "no_results",
                        "message": "No lyrics sources passed the relevance threshold",
                        "sources_rejected": {
                            name: {
                                "relevance": r.relevance,
                                "matched_words": r.matched_words,
                                "total_words": r.total_words,
                                "track_name": r.track_name,
                                "artist_names": r.artist_names,
                            }
                            for name, r in sources_rejected.items()
                        },
                        "sources_not_found": sources_not_found,
                    }

        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Error searching lyrics for job {job_id}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to search lyrics: {str(e)}")
```

- [ ] **Step 3.6: Run full backend tests**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-reference-lyrics-search && python -m pytest backend/tests/ tests/ -v --timeout=60 2>&1 | tail -50`
Expected: All tests PASS.

- [ ] **Step 3.7: Commit**

```bash
git add karaoke_gen/lyrics_transcriber/correction/operations.py backend/api/routes/review.py backend/tests/test_search_lyrics.py
git commit -m "feat: add search-lyrics endpoint and search_lyrics_sources operation"
```

---

## Task 4: Frontend Types and API Client

**Files:**
- Modify: `frontend/lib/lyrics-review/types.ts`
- Modify: `frontend/lib/api.ts`

This task adds the TypeScript types and API method for the search-lyrics endpoint.

- [ ] **Step 4.1: Add frontend types**

Add to `frontend/lib/lyrics-review/types.ts`:

```typescript
export interface RejectedSource {
  relevance: number
  matched_words: number
  total_words: number
  track_name: string
  artist_names: string
}

export interface SearchLyricsResponse {
  status: 'success' | 'no_results'
  data?: CorrectionData
  message?: string
  sources_added: string[]
  sources_rejected: Record<string, RejectedSource>
  sources_not_found: string[]
}
```

- [ ] **Step 4.2: Add searchLyrics method to API client**

Add to `frontend/lib/api.ts`, in the API client class near the `addLyrics` method:

```typescript
  async searchLyrics(
    artist: string,
    title: string,
    forceSources: string[] = [],
  ): Promise<SearchLyricsResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/review/${this.jobId}/search-lyrics`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...this.getAuthHeaders(),
        },
        body: JSON.stringify({
          artist,
          title,
          force_sources: forceSources,
        }),
      },
    )
    return handleResponse<SearchLyricsResponse>(response)
  }
```

Note: Unlike `addLyrics` which unwraps `result.data`, `searchLyrics` returns the full response so the frontend can inspect `status`, `sources_rejected`, etc.

- [ ] **Step 4.3: Commit**

```bash
git add frontend/lib/lyrics-review/types.ts frontend/lib/api.ts
git commit -m "feat: add SearchLyricsResponse types and searchLyrics API method"
```

---

## Task 5: SearchLyricsTab Component

**Files:**
- Create: `frontend/components/lyrics-review/modals/SearchLyricsTab.tsx`

This task builds the Search tab — artist/title inputs, search button, loading state, and the rejected-sources-with-checkboxes UI for the no-results case.

- [ ] **Step 5.1: Create the SearchLyricsTab component**

```tsx
// frontend/components/lyrics-review/modals/SearchLyricsTab.tsx
'use client'

import { useState } from 'react'
import { Box, TextField, Button, Typography, Checkbox, FormControlLabel, CircularProgress, Alert } from '@mui/material'
import { SearchLyricsResponse, RejectedSource } from '@/lib/lyrics-review/types'

interface SearchLyricsTabProps {
  defaultArtist: string
  defaultTitle: string
  onSearch: (artist: string, title: string, forceSources: string[]) => Promise<SearchLyricsResponse>
  onClose: () => void
  disabled?: boolean
}

export default function SearchLyricsTab({
  defaultArtist,
  defaultTitle,
  onSearch,
  onClose,
  disabled = false,
}: SearchLyricsTabProps) {
  const [artist, setArtist] = useState(defaultArtist)
  const [title, setTitle] = useState(defaultTitle)
  const [isSearching, setIsSearching] = useState(false)
  const [searchResult, setSearchResult] = useState<SearchLyricsResponse | null>(null)
  const [selectedForce, setSelectedForce] = useState<Set<string>>(new Set())
  const [error, setError] = useState<string | null>(null)

  const handleSearch = async (forceSources: string[] = []) => {
    if (!artist.trim() || !title.trim()) return

    setIsSearching(true)
    setError(null)
    setSearchResult(null)
    setSelectedForce(new Set())

    try {
      const result = await onSearch(artist.trim(), title.trim(), forceSources)
      if (result.status === 'success') {
        // Success — modal will close via parent
        return
      }
      // No results — show rejected sources
      setSearchResult(result)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Search failed')
    } finally {
      setIsSearching(false)
    }
  }

  const handleForceAdd = async () => {
    if (selectedForce.size === 0) return
    await handleSearch(Array.from(selectedForce))
  }

  const toggleForceSource = (source: string) => {
    setSelectedForce(prev => {
      const next = new Set(prev)
      if (next.has(source)) {
        next.delete(source)
      } else {
        next.add(source)
      }
      return next
    })
  }

  const rejectedEntries = searchResult
    ? Object.entries(searchResult.sources_rejected)
    : []
  const notFoundSources = searchResult?.sources_not_found ?? []

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <Box>
        <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
          Artist
        </Typography>
        <TextField
          fullWidth
          size="small"
          value={artist}
          onChange={e => setArtist(e.target.value)}
          disabled={isSearching || disabled}
        />
      </Box>

      <Box>
        <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
          Title
        </Typography>
        <TextField
          fullWidth
          size="small"
          value={title}
          onChange={e => setTitle(e.target.value)}
          disabled={isSearching || disabled}
        />
      </Box>

      <Typography variant="caption" color="text.secondary">
        Tip: Try removing &quot;feat.&quot;, &quot;remix&quot;, or other extras from the title if the
        original search didn&apos;t find the right lyrics.
      </Typography>

      {error && <Alert severity="error">{error}</Alert>}

      {searchResult && searchResult.status === 'no_results' && (
        <Box
          sx={{
            bgcolor: 'error.dark',
            borderRadius: 1,
            p: 2,
            border: '1px solid',
            borderColor: 'error.main',
          }}
        >
          <Typography variant="subtitle2" color="error.light" gutterBottom>
            No relevant lyrics found
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            These sources were found but had low word match with your transcription. This could mean
            wrong song — or the transcription may just be inaccurate.
          </Typography>

          {rejectedEntries.map(([source, info]) => (
            <FormControlLabel
              key={source}
              sx={{
                display: 'flex',
                bgcolor: 'action.hover',
                borderRadius: 1,
                px: 1,
                py: 0.5,
                mb: 0.5,
                mx: 0,
              }}
              control={
                <Checkbox
                  checked={selectedForce.has(source)}
                  onChange={() => toggleForceSource(source)}
                  size="small"
                />
              }
              label={
                <Box>
                  <Typography variant="body2">
                    <strong>{source}</strong>
                    <Typography component="span" variant="body2" color="error.light" sx={{ ml: 1 }}>
                      {Math.round(info.relevance * 100)}% word match
                    </Typography>
                  </Typography>
                  {info.track_name && (
                    <Typography variant="caption" color="text.secondary">
                      Found: &quot;{info.track_name}&quot;
                      {info.artist_names ? ` by ${info.artist_names}` : ''}
                    </Typography>
                  )}
                </Box>
              }
            />
          ))}

          {notFoundSources.map(source => (
            <Box
              key={source}
              sx={{
                bgcolor: 'action.disabledBackground',
                borderRadius: 1,
                px: 2,
                py: 1,
                mb: 0.5,
                opacity: 0.5,
              }}
            >
              <Typography variant="body2" color="text.disabled">
                <strong>{source}</strong> — No results found
              </Typography>
            </Box>
          ))}

          <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
            Select any sources above to add them anyway, or try a different search.
          </Typography>
        </Box>
      )}

      <Box sx={{ display: 'flex', justifyContent: 'flex-end', gap: 1, mt: 1 }}>
        <Button variant="outlined" onClick={onClose} disabled={isSearching}>
          Cancel
        </Button>
        {searchResult && selectedForce.size > 0 && (
          <Button
            variant="contained"
            onClick={handleForceAdd}
            disabled={isSearching}
          >
            {isSearching ? <CircularProgress size={20} /> : `Add Selected (${selectedForce.size})`}
          </Button>
        )}
        <Button
          variant="contained"
          onClick={() => handleSearch()}
          disabled={isSearching || !artist.trim() || !title.trim()}
          color="primary"
        >
          {isSearching ? (
            <>
              <CircularProgress size={20} sx={{ mr: 1 }} />
              Searching...
            </>
          ) : searchResult ? (
            'Search Again'
          ) : (
            'Search All Providers'
          )}
        </Button>
      </Box>
    </Box>
  )
}
```

- [ ] **Step 5.2: Commit**

```bash
git add frontend/components/lyrics-review/modals/SearchLyricsTab.tsx
git commit -m "feat: add SearchLyricsTab component for lyrics provider search"
```

---

## Task 6: PasteLyricsTab Component

**Files:**
- Create: `frontend/components/lyrics-review/modals/PasteLyricsTab.tsx`

Extract the existing paste functionality from AddLyricsModal into its own tab component.

- [ ] **Step 6.1: Create PasteLyricsTab component**

```tsx
// frontend/components/lyrics-review/modals/PasteLyricsTab.tsx
'use client'

import { useState } from 'react'
import { Box, TextField, Button, Typography, CircularProgress, Alert } from '@mui/material'

interface PasteLyricsTabProps {
  onAdd: (source: string, lyrics: string) => Promise<void>
  onClose: () => void
  disabled?: boolean
}

export default function PasteLyricsTab({ onAdd, onClose, disabled = false }: PasteLyricsTabProps) {
  const [source, setSource] = useState('')
  const [lyrics, setLyrics] = useState('')
  const [isAdding, setIsAdding] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleAdd = async () => {
    if (!source.trim() || !lyrics.trim()) return

    setIsAdding(true)
    setError(null)
    try {
      await onAdd(source.trim(), lyrics.trim())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to add lyrics')
      setIsAdding(false)
    }
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <Box>
        <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
          Source Name
        </Typography>
        <TextField
          fullWidth
          size="small"
          placeholder="e.g., manual, genius, azlyrics..."
          value={source}
          onChange={e => setSource(e.target.value)}
          disabled={isAdding || disabled}
        />
      </Box>

      <Box>
        <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
          Lyrics
        </Typography>
        <TextField
          fullWidth
          multiline
          rows={10}
          placeholder="Paste lyrics here..."
          value={lyrics}
          onChange={e => setLyrics(e.target.value)}
          disabled={isAdding || disabled}
          sx={{ '& textarea': { fontFamily: 'monospace' } }}
        />
      </Box>

      {error && <Alert severity="error">{error}</Alert>}

      <Box sx={{ display: 'flex', justifyContent: 'flex-end', gap: 1, mt: 1 }}>
        <Button variant="outlined" onClick={onClose} disabled={isAdding}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={handleAdd}
          disabled={isAdding || !source.trim() || !lyrics.trim()}
        >
          {isAdding ? (
            <>
              <CircularProgress size={20} sx={{ mr: 1 }} />
              Adding...
            </>
          ) : (
            'Add Lyrics'
          )}
        </Button>
      </Box>
    </Box>
  )
}
```

- [ ] **Step 6.2: Commit**

```bash
git add frontend/components/lyrics-review/modals/PasteLyricsTab.tsx
git commit -m "feat: add PasteLyricsTab component for manual lyrics entry"
```

---

## Task 7: Rewrite AddLyricsModal with Tabs

**Files:**
- Modify: `frontend/components/lyrics-review/modals/AddLyricsModal.tsx`
- Modify: `frontend/components/lyrics-review/LyricsAnalyzer.tsx`

This task rewrites the modal as a tabbed container and wires up the new search handler in LyricsAnalyzer.

- [ ] **Step 7.1: Rewrite AddLyricsModal**

Replace the entire content of `frontend/components/lyrics-review/modals/AddLyricsModal.tsx`:

```tsx
// frontend/components/lyrics-review/modals/AddLyricsModal.tsx
'use client'

import { useState } from 'react'
import { Dialog, DialogTitle, DialogContent, IconButton, Tabs, Tab, Box } from '@mui/material'
import CloseIcon from '@mui/icons-material/Close'
import SearchLyricsTab from './SearchLyricsTab'
import PasteLyricsTab from './PasteLyricsTab'
import { SearchLyricsResponse } from '@/lib/lyrics-review/types'

interface AddLyricsModalProps {
  open: boolean
  onClose: () => void
  onAdd: (source: string, lyrics: string) => Promise<void>
  onSearch: (artist: string, title: string, forceSources: string[]) => Promise<SearchLyricsResponse>
  defaultArtist: string
  defaultTitle: string
}

export default function AddLyricsModal({
  open,
  onClose,
  onAdd,
  onSearch,
  defaultArtist,
  defaultTitle,
}: AddLyricsModalProps) {
  const [tab, setTab] = useState(0)

  const handleClose = () => {
    setTab(0) // Reset to search tab
    onClose()
  }

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        Add Reference Lyrics
        <IconButton onClick={handleClose} size="small">
          <CloseIcon />
        </IconButton>
      </DialogTitle>
      <DialogContent>
        <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
          <Tab label="Search" />
          <Tab label="Paste" />
        </Tabs>

        {tab === 0 && (
          <SearchLyricsTab
            defaultArtist={defaultArtist}
            defaultTitle={defaultTitle}
            onSearch={onSearch}
            onClose={handleClose}
          />
        )}

        {tab === 1 && (
          <PasteLyricsTab onAdd={onAdd} onClose={handleClose} />
        )}
      </DialogContent>
    </Dialog>
  )
}
```

- [ ] **Step 7.2: Update LyricsAnalyzer to wire up search handler**

In `frontend/components/lyrics-review/LyricsAnalyzer.tsx`:

Add the import for `SearchLyricsResponse`:

```typescript
import { SearchLyricsResponse } from '@/lib/lyrics-review/types'
```

Add the `handleSearchLyrics` callback near `handleAddLyrics` (around line 1035):

```typescript
  const handleSearchLyrics = useCallback(
    async (artist: string, title: string, forceSources: string[]): Promise<SearchLyricsResponse> => {
      if (!apiClient) {
        return { status: 'no_results', message: 'No API client', sources_added: [], sources_rejected: {}, sources_not_found: [] }
      }

      const result = await apiClient.searchLyrics(artist, title, forceSources)

      if (result.status === 'success' && result.data) {
        updateDataWithHistory(result.data, 'search lyrics')
      }

      return result
    },
    [apiClient, updateDataWithHistory],
  )
```

Update the `AddLyricsModal` usage (around line 1373) to pass the new props:

```tsx
<AddLyricsModal
  open={isAddLyricsModalOpen}
  onClose={() => setIsAddLyricsModalOpen(false)}
  onAdd={handleAddLyrics}
  onSearch={handleSearchLyrics}
  defaultArtist={data.metadata?.artist || ''}
  defaultTitle={data.metadata?.title || ''}
/>
```

- [ ] **Step 7.3: Run frontend tests**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-reference-lyrics-search/frontend && npm test 2>&1 | tail -30`
Expected: Tests pass. Some tests may need updating if they directly render AddLyricsModal with the old props. Fix any failures by updating the test props to include the new required `onSearch`, `defaultArtist`, `defaultTitle` props.

- [ ] **Step 7.4: Commit**

```bash
git add frontend/components/lyrics-review/modals/AddLyricsModal.tsx frontend/components/lyrics-review/LyricsAnalyzer.tsx
git commit -m "feat: rewrite AddLyricsModal with Search/Paste tabs"
```

---

## Task 8: Reference Panel Empty State

**Files:**
- Create: `frontend/components/lyrics-review/ReferenceEmptyState.tsx`
- Modify: `frontend/components/lyrics-review/ReferenceView.tsx`

This task adds the inline add-lyrics UI when no valid reference sources exist.

- [ ] **Step 8.1: Create ReferenceEmptyState component**

```tsx
// frontend/components/lyrics-review/ReferenceEmptyState.tsx
'use client'

import { useState } from 'react'
import { Box, Typography, Tabs, Tab, Alert } from '@mui/material'
import SearchLyricsTab from './modals/SearchLyricsTab'
import PasteLyricsTab from './modals/PasteLyricsTab'
import { SearchLyricsResponse } from '@/lib/lyrics-review/types'

interface ReferenceEmptyStateProps {
  defaultArtist: string
  defaultTitle: string
  onAdd: (source: string, lyrics: string) => Promise<void>
  onSearch: (artist: string, title: string, forceSources: string[]) => Promise<SearchLyricsResponse>
}

export default function ReferenceEmptyState({
  defaultArtist,
  defaultTitle,
  onAdd,
  onSearch,
}: ReferenceEmptyStateProps) {
  const [tab, setTab] = useState(0)

  return (
    <Box sx={{ p: 2 }}>
      <Alert severity="info" sx={{ mb: 2 }}>
        <Typography variant="subtitle2" gutterBottom>
          No valid reference lyrics found
        </Typography>
        <Typography variant="body2">
          The automatic search didn&apos;t find lyrics matching this track. This often happens with
          remixes, live versions, or tracks with &quot;feat.&quot; in the name. Try searching with a
          simplified artist/title, or paste lyrics manually.
        </Typography>
      </Alert>

      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
        <Tab label="Search" />
        <Tab label="Paste" />
      </Tabs>

      {tab === 0 && (
        <SearchLyricsTab
          defaultArtist={defaultArtist}
          defaultTitle={defaultTitle}
          onSearch={onSearch}
          onClose={() => {}} // No-op — not a modal, nothing to close
        />
      )}

      {tab === 1 && (
        <PasteLyricsTab
          onAdd={onAdd}
          onClose={() => {}} // No-op
        />
      )}
    </Box>
  )
}
```

- [ ] **Step 8.2: Integrate into ReferenceView**

In `frontend/components/lyrics-review/ReferenceView.tsx`, add the import:

```typescript
import ReferenceEmptyState from './ReferenceEmptyState'
import { SearchLyricsResponse } from '@/lib/lyrics-review/types'
```

Add new props to the component's props interface:

```typescript
  onSearchLyrics?: (artist: string, title: string, forceSources: string[]) => Promise<SearchLyricsResponse>
  defaultArtist?: string
  defaultTitle?: string
```

In the component body, after computing `availableSources`, add a check for the empty state. Find where the reference lyrics segments are rendered and wrap with a conditional:

```tsx
{availableSources.length === 0 && onAddLyrics && onSearchLyrics ? (
  <ReferenceEmptyState
    defaultArtist={defaultArtist || ''}
    defaultTitle={defaultTitle || ''}
    onAdd={onAddLyrics}
    onSearch={onSearchLyrics}
  />
) : (
  // ... existing reference lyrics rendering
)}
```

- [ ] **Step 8.3: Pass new props through from LyricsAnalyzer**

In `frontend/components/lyrics-review/LyricsAnalyzer.tsx`, update the `ReferenceView` usage to pass the new props:

```tsx
<ReferenceView
  // ... existing props ...
  onSearchLyrics={handleSearchLyrics}
  defaultArtist={data.metadata?.artist || ''}
  defaultTitle={data.metadata?.title || ''}
/>
```

- [ ] **Step 8.4: Run frontend tests**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-reference-lyrics-search/frontend && npm test 2>&1 | tail -30`
Expected: All tests PASS.

- [ ] **Step 8.5: Commit**

```bash
git add frontend/components/lyrics-review/ReferenceEmptyState.tsx frontend/components/lyrics-review/ReferenceView.tsx frontend/components/lyrics-review/LyricsAnalyzer.tsx
git commit -m "feat: add inline empty state for Reference panel when no valid sources exist"
```

---

## Task 9: Full Integration Testing

**Files:**
- Run existing test suites
- Manual verification with dev server

- [ ] **Step 9.1: Run full backend test suite**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-reference-lyrics-search && python -m pytest tests/ backend/tests/ -v --timeout=120 2>&1 | tail -80`
Expected: All tests PASS.

- [ ] **Step 9.2: Run full frontend test suite**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-reference-lyrics-search/frontend && npm test 2>&1 | tail -50`
Expected: All tests PASS.

- [ ] **Step 9.3: Run full make test**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-reference-lyrics-search && make test 2>&1 | tail -500`
Expected: All tests PASS.

- [ ] **Step 9.4: Version bump**

Bump `tool.poetry.version` in `pyproject.toml` for the new feature.

- [ ] **Step 9.5: Final commit**

```bash
git add pyproject.toml
git commit -m "chore: bump version for reference lyrics search feature"
```
