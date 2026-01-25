"""
Tests for AnchorSequenceFinder using real production data.

These tests verify that the anchor sequence finder produces consistent results
when run on real data from production jobs. The fixtures contain:
- Original transcription segments with words
- Reference lyrics from multiple sources (Genius, Spotify, LRCLib, etc.)
- The anchor sequences and gaps that were computed in production

The goal is to:
1. Verify the current code produces matching results for production data
2. Track metrics (coverage, gap counts, anchor lengths) to measure improvements
3. Prevent regressions when modifying the anchor finding logic
"""

import json
import logging
import pytest
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import statistics

from karaoke_gen.lyrics_transcriber.types import (
    LyricsData,
    LyricsMetadata,
    LyricsSegment,
    TranscriptionData,
    TranscriptionResult,
    Word,
    AnchorSequence,
    GapSequence,
    ScoredAnchor,
)
from karaoke_gen.lyrics_transcriber.correction.anchor_sequence import AnchorSequenceFinder


# Path to fixture data
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "anchor_sequence_real_data"


@dataclass
class AnchorMetrics:
    """Metrics for evaluating anchor sequence quality."""

    job_id: str
    artist: str
    title: str

    # Counts
    total_anchors: int
    total_gaps: int
    total_words: int

    # Word coverage
    words_in_anchors: int
    words_in_gaps: int
    anchor_coverage_pct: float
    gap_coverage_pct: float

    # Anchor lengths
    max_anchor_length: int
    min_anchor_length: int
    avg_anchor_length: float
    median_anchor_length: float

    # Gap lengths
    max_gap_length: int
    min_gap_length: int
    avg_gap_length: float
    median_gap_length: float

    # Quality indicators
    single_word_gaps: int  # Gaps with just 1 word (often unnecessary)
    large_gaps: int  # Gaps with 5+ words (potentially problematic)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "artist": self.artist,
            "title": self.title,
            "total_anchors": self.total_anchors,
            "total_gaps": self.total_gaps,
            "total_words": self.total_words,
            "words_in_anchors": self.words_in_anchors,
            "words_in_gaps": self.words_in_gaps,
            "anchor_coverage_pct": round(self.anchor_coverage_pct, 2),
            "gap_coverage_pct": round(self.gap_coverage_pct, 2),
            "max_anchor_length": self.max_anchor_length,
            "min_anchor_length": self.min_anchor_length,
            "avg_anchor_length": round(self.avg_anchor_length, 2),
            "median_anchor_length": self.median_anchor_length,
            "max_gap_length": self.max_gap_length,
            "min_gap_length": self.min_gap_length,
            "avg_gap_length": round(self.avg_gap_length, 2),
            "median_gap_length": self.median_gap_length,
            "single_word_gaps": self.single_word_gaps,
            "large_gaps": self.large_gaps,
        }


def load_fixture(job_dir: Path) -> Optional[dict]:
    """Load a fixture from a job directory."""
    corrections_path = job_dir / "corrections.json"
    if not corrections_path.exists():
        return None

    with open(corrections_path) as f:
        return json.load(f)


def reconstruct_transcription_result(data: dict) -> Tuple[str, TranscriptionResult]:
    """
    Reconstruct TranscriptionResult from corrections.json data.

    Returns:
        Tuple of (transcribed_text, TranscriptionResult)
    """
    # Build segments from original_segments
    segments = []
    all_words = []

    for seg_data in data.get("original_segments", []):
        words = [Word.from_dict(w) for w in seg_data.get("words", [])]
        segment = LyricsSegment(
            id=seg_data["id"],
            text=seg_data["text"],
            words=words,
            start_time=seg_data.get("start_time", 0.0),
            end_time=seg_data.get("end_time", 0.0),
        )
        segments.append(segment)
        all_words.extend(words)

    # Build the full transcribed text
    transcribed_text = " ".join(w.text for w in all_words)

    # Create TranscriptionData
    transcription_data = TranscriptionData(
        segments=segments,
        words=all_words,
        text=transcribed_text,
        source="audioshake",
        metadata=None,
    )

    # Wrap in TranscriptionResult
    transcription_result = TranscriptionResult(
        name="audioshake",
        priority=1,
        result=transcription_data,
    )

    return transcribed_text, transcription_result


def reconstruct_references(data: dict) -> Dict[str, LyricsData]:
    """
    Reconstruct reference lyrics from corrections.json data.

    Returns:
        Dict mapping source name to LyricsData
    """
    references = {}

    ref_lyrics = data.get("reference_lyrics", {})
    for source, ref_data in ref_lyrics.items():
        if not ref_data:
            continue

        segments = []
        for seg_data in ref_data.get("segments", []):
            words = [Word.from_dict(w) for w in seg_data.get("words", [])]
            segment = LyricsSegment(
                id=seg_data["id"],
                text=seg_data["text"],
                words=words,
                start_time=seg_data.get("start_time") or 0.0,
                end_time=seg_data.get("end_time") or 0.0,
            )
            segments.append(segment)

        # Build metadata
        meta_data = ref_data.get("metadata", {})
        metadata = LyricsMetadata(
            source=source,
            track_name=meta_data.get("track_name", ""),
            artist_names=meta_data.get("artist_names", ""),
            album_name=meta_data.get("album_name"),
            duration_ms=meta_data.get("duration_ms"),
            explicit=meta_data.get("explicit"),
            language=meta_data.get("language"),
            is_synced=meta_data.get("is_synced", False),
            lyrics_provider=meta_data.get("lyrics_provider"),
            lyrics_provider_id=meta_data.get("lyrics_provider_id"),
            provider_metadata=meta_data.get("provider_metadata", {}),
        )

        lyrics_data = LyricsData(
            segments=segments,
            metadata=metadata,
            source=source,
        )
        references[source] = lyrics_data

    return references


def extract_anchor_words(anchor: dict) -> List[str]:
    """Extract the word list from an anchor, handling both old and new formats."""
    if "words" in anchor:
        return anchor["words"]
    elif "_words" in anchor and anchor["_words"]:
        return anchor["_words"]
    # If we have transcribed_word_ids but no words, we can't reconstruct
    return []


def extract_gap_words(gap: dict) -> List[str]:
    """Extract the word list from a gap, handling both old and new formats."""
    if "words" in gap:
        return gap["words"]
    elif "_words" in gap and gap["_words"]:
        return gap["_words"]
    return []


def compute_metrics_from_data(
    job_id: str,
    artist: str,
    title: str,
    anchors: List[dict],
    gaps: List[dict],
) -> AnchorMetrics:
    """Compute metrics from anchor and gap data."""

    anchor_lengths = []
    for anchor in anchors:
        words = extract_anchor_words(anchor)
        if words:
            anchor_lengths.append(len(words))
        elif "transcribed_word_ids" in anchor:
            anchor_lengths.append(len(anchor["transcribed_word_ids"]))

    gap_lengths = []
    for gap in gaps:
        words = extract_gap_words(gap)
        if words:
            gap_lengths.append(len(words))
        elif "transcribed_word_ids" in gap:
            gap_lengths.append(len(gap["transcribed_word_ids"]))

    words_in_anchors = sum(anchor_lengths)
    words_in_gaps = sum(gap_lengths)
    total_words = words_in_anchors + words_in_gaps

    return AnchorMetrics(
        job_id=job_id,
        artist=artist,
        title=title,
        total_anchors=len(anchors),
        total_gaps=len(gaps),
        total_words=total_words,
        words_in_anchors=words_in_anchors,
        words_in_gaps=words_in_gaps,
        anchor_coverage_pct=(words_in_anchors / total_words * 100) if total_words > 0 else 0,
        gap_coverage_pct=(words_in_gaps / total_words * 100) if total_words > 0 else 0,
        max_anchor_length=max(anchor_lengths) if anchor_lengths else 0,
        min_anchor_length=min(anchor_lengths) if anchor_lengths else 0,
        avg_anchor_length=statistics.mean(anchor_lengths) if anchor_lengths else 0,
        median_anchor_length=statistics.median(anchor_lengths) if anchor_lengths else 0,
        max_gap_length=max(gap_lengths) if gap_lengths else 0,
        min_gap_length=min(gap_lengths) if gap_lengths else 0,
        avg_gap_length=statistics.mean(gap_lengths) if gap_lengths else 0,
        median_gap_length=statistics.median(gap_lengths) if gap_lengths else 0,
        single_word_gaps=sum(1 for g in gap_lengths if g == 1),
        large_gaps=sum(1 for g in gap_lengths if g >= 5),
    )


def get_fixture_job_dirs() -> List[Path]:
    """Get all job directories in the fixtures folder."""
    if not FIXTURES_DIR.exists():
        return []

    job_dirs = []
    for item in FIXTURES_DIR.iterdir():
        if item.is_dir() and (item / "corrections.json").exists():
            job_dirs.append(item)

    return sorted(job_dirs)


def get_fixture_ids() -> List[str]:
    """Get fixture IDs for parametrization."""
    return [d.name for d in get_fixture_job_dirs()]


class TestAnchorSequenceRealData:
    """Tests using real production data fixtures."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Set up test fixtures."""
        self.logger = logging.getLogger(__name__)
        self.cache_dir = tempfile.mkdtemp()

    @pytest.mark.parametrize("fixture_name", get_fixture_ids())
    def test_fixture_loads_correctly(self, fixture_name: str):
        """Verify each fixture can be loaded and parsed."""
        fixture_dir = FIXTURES_DIR / fixture_name
        data = load_fixture(fixture_dir)

        assert data is not None, f"Failed to load fixture: {fixture_name}"
        assert "original_segments" in data, "Missing original_segments"
        assert "reference_lyrics" in data, "Missing reference_lyrics"
        assert "anchor_sequences" in data, "Missing anchor_sequences"
        assert "gap_sequences" in data, "Missing gap_sequences"

    @pytest.mark.parametrize("fixture_name", get_fixture_ids())
    def test_can_reconstruct_inputs(self, fixture_name: str):
        """Verify we can reconstruct the inputs for anchor finding."""
        fixture_dir = FIXTURES_DIR / fixture_name
        data = load_fixture(fixture_dir)

        if not data:
            pytest.skip(f"No fixture data for {fixture_name}")

        # Skip fixtures with no reference lyrics
        if not data.get("reference_lyrics"):
            pytest.skip(f"No reference lyrics in {fixture_name}")

        transcribed_text, transcription_result = reconstruct_transcription_result(data)
        references = reconstruct_references(data)

        # Verify transcription
        assert transcribed_text, "Transcribed text should not be empty"
        assert transcription_result.result.segments, "Should have transcription segments"
        assert transcription_result.result.words, "Should have transcription words"

        # Verify references
        assert references, "Should have at least one reference source"
        for source, lyrics_data in references.items():
            assert lyrics_data.segments, f"Reference {source} should have segments"

    @pytest.mark.parametrize("fixture_name", get_fixture_ids())
    def test_anchor_finder_runs_without_error(self, fixture_name: str):
        """Verify the anchor finder runs without errors on real data."""
        fixture_dir = FIXTURES_DIR / fixture_name
        data = load_fixture(fixture_dir)

        if not data:
            pytest.skip(f"No fixture data for {fixture_name}")

        # Skip fixtures with no reference lyrics
        if not data.get("reference_lyrics"):
            pytest.skip(f"No reference lyrics in {fixture_name}")

        transcribed_text, transcription_result = reconstruct_transcription_result(data)
        references = reconstruct_references(data)

        if not references:
            pytest.skip(f"No valid references in {fixture_name}")

        # Create finder with short timeout for tests
        finder = AnchorSequenceFinder(
            cache_dir=self.cache_dir,
            min_sequence_length=3,
            min_sources=1,
            timeout_seconds=60,
            logger=self.logger,
        )

        # This should not raise
        anchors = finder.find_anchors(
            transcribed=transcribed_text,
            references=references,
            transcription_result=transcription_result,
        )

        assert anchors is not None, "find_anchors should return a list"

    @pytest.mark.parametrize("fixture_name", get_fixture_ids())
    def test_production_metrics_baseline(self, fixture_name: str):
        """
        Record baseline metrics from production data.

        This test doesn't assert specific values but records metrics
        that can be compared when making changes.
        """
        fixture_dir = FIXTURES_DIR / fixture_name
        data = load_fixture(fixture_dir)

        if not data:
            pytest.skip(f"No fixture data for {fixture_name}")

        # Get metadata
        metadata_path = fixture_dir / "metadata.json"
        if metadata_path.exists():
            with open(metadata_path) as f:
                metadata = json.load(f)
            artist = metadata.get("artist", "Unknown")
            title = metadata.get("title", "Unknown")
            job_id = metadata.get("job_id", fixture_name)
        else:
            artist = "Unknown"
            title = "Unknown"
            job_id = fixture_name

        anchors = data.get("anchor_sequences", [])
        gaps = data.get("gap_sequences", [])

        if not anchors and not gaps:
            pytest.skip(f"No anchors or gaps in {fixture_name}")

        metrics = compute_metrics_from_data(job_id, artist, title, anchors, gaps)

        # Log metrics for visibility
        print(f"\n{artist} - {title}:")
        print(f"  Anchors: {metrics.total_anchors}, Gaps: {metrics.total_gaps}")
        print(f"  Coverage: {metrics.anchor_coverage_pct:.1f}% anchored, {metrics.gap_coverage_pct:.1f}% gaps")
        print(f"  Anchor lengths: min={metrics.min_anchor_length}, avg={metrics.avg_anchor_length:.1f}, max={metrics.max_anchor_length}")
        print(f"  Gap lengths: min={metrics.min_gap_length}, avg={metrics.avg_gap_length:.1f}, max={metrics.max_gap_length}")
        print(f"  Single-word gaps: {metrics.single_word_gaps}, Large gaps (5+): {metrics.large_gaps}")


class TestAnchorSequenceConsistency:
    """Tests to verify anchor finding produces consistent results."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Set up test fixtures."""
        self.logger = logging.getLogger(__name__)
        self.cache_dir = tempfile.mkdtemp()

    @pytest.mark.parametrize("fixture_name", get_fixture_ids())
    def test_anchor_count_matches_production(self, fixture_name: str):
        """
        Verify anchor counts are reasonable compared to production.

        Note: This test is VERY relaxed because algorithm improvements can change
        anchor counts significantly while improving coverage. The key test is
        test_coverage_does_not_regress - anchor counts are just informational.

        Bug fixes that improve text normalization (e.g., handling apostrophes
        consistently) can result in longer anchors being found, which means
        fewer total anchors but the same or better coverage.
        """
        fixture_dir = FIXTURES_DIR / fixture_name
        data = load_fixture(fixture_dir)

        if not data:
            pytest.skip(f"No fixture data for {fixture_name}")

        if not data.get("reference_lyrics"):
            pytest.skip(f"No reference lyrics in {fixture_name}")

        production_anchors = data.get("anchor_sequences", [])
        if not production_anchors:
            pytest.skip(f"No production anchors in {fixture_name}")

        transcribed_text, transcription_result = reconstruct_transcription_result(data)
        references = reconstruct_references(data)

        if not references:
            pytest.skip(f"No valid references in {fixture_name}")

        finder = AnchorSequenceFinder(
            cache_dir=self.cache_dir,
            min_sequence_length=3,
            min_sources=1,
            timeout_seconds=60,
            logger=self.logger,
        )

        new_anchors = finder.find_anchors(
            transcribed=transcribed_text,
            references=references,
            transcription_result=transcription_result,
        )

        production_count = len(production_anchors)
        new_count = len(new_anchors)

        # Very relaxed: allow up to 75% reduction (algorithm improvements create longer anchors)
        # or up to 50% increase, with a minimum variance of 5
        # The important metric is coverage, not anchor count
        min_allowed = max(int(production_count * 0.25), 3)
        max_allowed = int(production_count * 1.5) + 5

        assert min_allowed <= new_count <= max_allowed, (
            f"Anchor count outside expected range: production={production_count}, new={new_count}, "
            f"expected_range=[{min_allowed}, {max_allowed}]"
        )

    @pytest.mark.parametrize("fixture_name", get_fixture_ids())
    def test_coverage_does_not_regress(self, fixture_name: str):
        """
        Verify anchor coverage doesn't decrease significantly.

        This is the key metric - we want to increase coverage (reduce gaps)
        without breaking things.
        """
        fixture_dir = FIXTURES_DIR / fixture_name
        data = load_fixture(fixture_dir)

        if not data:
            pytest.skip(f"No fixture data for {fixture_name}")

        if not data.get("reference_lyrics"):
            pytest.skip(f"No reference lyrics in {fixture_name}")

        production_anchors = data.get("anchor_sequences", [])
        production_gaps = data.get("gap_sequences", [])

        if not production_anchors:
            pytest.skip(f"No production anchors in {fixture_name}")

        # Get metadata for reporting
        metadata_path = fixture_dir / "metadata.json"
        if metadata_path.exists():
            with open(metadata_path) as f:
                metadata = json.load(f)
            artist = metadata.get("artist", "Unknown")
            title = metadata.get("title", "Unknown")
            job_id = metadata.get("job_id", fixture_name)
        else:
            artist, title, job_id = "Unknown", "Unknown", fixture_name

        # Calculate production coverage
        production_metrics = compute_metrics_from_data(
            job_id, artist, title, production_anchors, production_gaps
        )

        transcribed_text, transcription_result = reconstruct_transcription_result(data)
        references = reconstruct_references(data)

        if not references:
            pytest.skip(f"No valid references in {fixture_name}")

        finder = AnchorSequenceFinder(
            cache_dir=self.cache_dir,
            min_sequence_length=3,
            min_sources=1,
            timeout_seconds=60,
            logger=self.logger,
        )

        new_anchors = finder.find_anchors(
            transcribed=transcribed_text,
            references=references,
            transcription_result=transcription_result,
        )

        # Calculate coverage from new anchors
        new_anchor_word_count = sum(a.anchor.length for a in new_anchors)
        total_words = production_metrics.total_words

        if total_words == 0:
            pytest.skip(f"No words in {fixture_name}")

        new_coverage = (new_anchor_word_count / total_words) * 100
        production_coverage = production_metrics.anchor_coverage_pct

        # Allow up to 5% regression in coverage
        min_allowed_coverage = production_coverage - 5.0

        print(f"\n{artist} - {title}:")
        print(f"  Production coverage: {production_coverage:.1f}%")
        print(f"  New coverage: {new_coverage:.1f}%")

        assert new_coverage >= min_allowed_coverage, (
            f"Coverage regressed: production={production_coverage:.1f}%, new={new_coverage:.1f}%, "
            f"min_allowed={min_allowed_coverage:.1f}%"
        )


class TestGapExtensionImprovements:
    """Tests that verify gap extension reduces single-word gaps."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Set up test fixtures."""
        self.logger = logging.getLogger(__name__)
        self.cache_dir = tempfile.mkdtemp()

    def test_single_word_gaps_reduced_per_job(self):
        """
        Verify that the new anchor finder reduces single-word gaps in each job.

        This test runs the anchor finder on each fixture and compares the
        single-word gap count to the production baseline.
        """
        improvements = []
        regressions = []

        for job_dir in get_fixture_job_dirs():
            data = load_fixture(job_dir)
            if not data or not data.get("reference_lyrics"):
                continue

            production_anchors = data.get("anchor_sequences", [])
            production_gaps = data.get("gap_sequences", [])

            if not production_anchors:
                continue

            # Calculate production single-word gaps
            production_single_word_gaps = sum(
                1 for gap in production_gaps
                if len(extract_gap_words(gap) or gap.get("transcribed_word_ids", [])) == 1
            )

            transcribed_text, transcription_result = reconstruct_transcription_result(data)
            references = reconstruct_references(data)

            if not references:
                continue

            finder = AnchorSequenceFinder(
                cache_dir=self.cache_dir,
                min_sequence_length=3,
                min_sources=1,
                timeout_seconds=60,
                logger=self.logger,
            )

            new_anchors = finder.find_anchors(
                transcribed=transcribed_text,
                references=references,
                transcription_result=transcription_result,
            )

            new_gaps = finder.find_gaps(
                transcribed=transcribed_text,
                anchors=new_anchors,
                references=references,
                transcription_result=transcription_result,
            )

            # Calculate new single-word gaps
            new_single_word_gaps = sum(
                1 for gap in new_gaps
                if len(gap.transcribed_word_ids) == 1
            )

            metadata_path = job_dir / "metadata.json"
            if metadata_path.exists():
                with open(metadata_path) as f:
                    metadata = json.load(f)
                job_name = f"{metadata.get('artist', 'Unknown')} - {metadata.get('title', 'Unknown')}"
            else:
                job_name = job_dir.name

            if new_single_word_gaps < production_single_word_gaps:
                improvements.append({
                    "job": job_name,
                    "production": production_single_word_gaps,
                    "new": new_single_word_gaps,
                    "reduction": production_single_word_gaps - new_single_word_gaps,
                })
            elif new_single_word_gaps > production_single_word_gaps:
                regressions.append({
                    "job": job_name,
                    "production": production_single_word_gaps,
                    "new": new_single_word_gaps,
                    "increase": new_single_word_gaps - production_single_word_gaps,
                })

        # Report improvements
        if improvements:
            print("\n" + "=" * 60)
            print("SINGLE-WORD GAP IMPROVEMENTS")
            print("=" * 60)
            total_reduction = sum(i["reduction"] for i in improvements)
            for i in improvements:
                print(f"{i['job']}: {i['production']} -> {i['new']} (-{i['reduction']})")
            print(f"\nTotal reduction: {total_reduction} single-word gaps")

        # Report any regressions
        if regressions:
            print("\n" + "-" * 60)
            print("REGRESSIONS (jobs with more single-word gaps):")
            print("-" * 60)
            for r in regressions:
                print(f"{r['job']}: {r['production']} -> {r['new']} (+{r['increase']})")

        # Assert no major regressions
        total_increase = sum(r["increase"] for r in regressions) if regressions else 0
        total_reduction = sum(i["reduction"] for i in improvements) if improvements else 0

        # Net improvement should be positive or at least not negative
        net_improvement = total_reduction - total_increase
        assert net_improvement >= 0, (
            f"Gap extension caused net regression: {total_reduction} reduced, "
            f"{total_increase} increased, net = {net_improvement}"
        )


class TestAggregateMetrics:
    """Tests that aggregate metrics across all fixtures."""

    def test_generate_metrics_report(self):
        """Generate a metrics report across all fixtures."""
        all_metrics = []

        for job_dir in get_fixture_job_dirs():
            data = load_fixture(job_dir)
            if not data:
                continue

            anchors = data.get("anchor_sequences", [])
            gaps = data.get("gap_sequences", [])

            if not anchors and not gaps:
                continue

            # Get metadata
            metadata_path = job_dir / "metadata.json"
            if metadata_path.exists():
                with open(metadata_path) as f:
                    metadata = json.load(f)
                artist = metadata.get("artist", "Unknown")
                title = metadata.get("title", "Unknown")
                job_id = metadata.get("job_id", job_dir.name)
            else:
                artist, title, job_id = "Unknown", "Unknown", job_dir.name

            metrics = compute_metrics_from_data(job_id, artist, title, anchors, gaps)
            all_metrics.append(metrics)

        if not all_metrics:
            pytest.skip("No valid fixtures found")

        # Aggregate metrics
        total_anchors = sum(m.total_anchors for m in all_metrics)
        total_gaps = sum(m.total_gaps for m in all_metrics)
        total_words = sum(m.total_words for m in all_metrics)
        total_words_in_anchors = sum(m.words_in_anchors for m in all_metrics)
        total_words_in_gaps = sum(m.words_in_gaps for m in all_metrics)

        overall_anchor_coverage = (total_words_in_anchors / total_words * 100) if total_words > 0 else 0
        overall_gap_coverage = (total_words_in_gaps / total_words * 100) if total_words > 0 else 0

        total_single_word_gaps = sum(m.single_word_gaps for m in all_metrics)
        total_large_gaps = sum(m.large_gaps for m in all_metrics)

        print("\n" + "=" * 60)
        print("AGGREGATE METRICS REPORT")
        print("=" * 60)
        print(f"Jobs analyzed: {len(all_metrics)}")
        print(f"Total anchors: {total_anchors}")
        print(f"Total gaps: {total_gaps}")
        print(f"Total words: {total_words}")
        print(f"\nOverall anchor coverage: {overall_anchor_coverage:.1f}%")
        print(f"Overall gap coverage: {overall_gap_coverage:.1f}%")
        print(f"\nSingle-word gaps: {total_single_word_gaps} ({total_single_word_gaps/total_gaps*100:.1f}% of gaps)" if total_gaps > 0 else "")
        print(f"Large gaps (5+ words): {total_large_gaps} ({total_large_gaps/total_gaps*100:.1f}% of gaps)" if total_gaps > 0 else "")

        # Per-job breakdown
        print("\n" + "-" * 60)
        print("Per-Job Breakdown:")
        print("-" * 60)
        for m in sorted(all_metrics, key=lambda x: x.anchor_coverage_pct, reverse=True):
            print(f"{m.artist} - {m.title}:")
            print(f"  Coverage: {m.anchor_coverage_pct:.1f}% | Gaps: {m.total_gaps} | Single-word: {m.single_word_gaps} | Large: {m.large_gaps}")

        # Save report to file
        report = {
            "aggregate": {
                "jobs_analyzed": len(all_metrics),
                "total_anchors": total_anchors,
                "total_gaps": total_gaps,
                "total_words": total_words,
                "overall_anchor_coverage_pct": round(overall_anchor_coverage, 2),
                "overall_gap_coverage_pct": round(overall_gap_coverage, 2),
                "total_single_word_gaps": total_single_word_gaps,
                "total_large_gaps": total_large_gaps,
            },
            "per_job": [m.to_dict() for m in all_metrics],
        }

        report_path = FIXTURES_DIR / "metrics_report.json"
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)

        print(f"\nReport saved to: {report_path}")

    @pytest.fixture(autouse=True)
    def setup(self):
        """Set up test fixtures."""
        self.logger = logging.getLogger(__name__)
        self.cache_dir = tempfile.mkdtemp()

    def test_new_finder_no_regression(self):
        """
        Verify the new anchor finder doesn't regress on coverage or gap count.

        Baseline metrics from production data:
        - Anchor coverage: 80.4%
        - Single-word gaps: 179

        The gap extension feature should improve or maintain these metrics.
        """
        total_words = 0
        total_words_in_anchors = 0
        total_single_word_gaps = 0

        for job_dir in get_fixture_job_dirs():
            data = load_fixture(job_dir)
            if not data or not data.get("reference_lyrics"):
                continue

            transcribed_text, transcription_result = reconstruct_transcription_result(data)
            references = reconstruct_references(data)

            if not references:
                continue

            finder = AnchorSequenceFinder(
                cache_dir=self.cache_dir,
                min_sequence_length=3,
                min_sources=1,
                timeout_seconds=60,
                logger=self.logger,
            )

            new_anchors = finder.find_anchors(
                transcribed=transcribed_text,
                references=references,
                transcription_result=transcription_result,
            )

            new_gaps = finder.find_gaps(
                transcribed=transcribed_text,
                anchors=new_anchors,
                references=references,
                transcription_result=transcription_result,
            )

            # Calculate metrics
            words_in_anchors = sum(a.anchor.length for a in new_anchors)
            words_in_gaps = sum(len(g.transcribed_word_ids) for g in new_gaps)
            job_total_words = words_in_anchors + words_in_gaps

            single_word_gaps = sum(1 for g in new_gaps if len(g.transcribed_word_ids) == 1)

            total_words += job_total_words
            total_words_in_anchors += words_in_anchors
            total_single_word_gaps += single_word_gaps

        if total_words == 0:
            pytest.skip("No valid fixtures found")

        overall_coverage = (total_words_in_anchors / total_words) * 100

        print("\n" + "=" * 60)
        print("NEW FINDER AGGREGATE METRICS")
        print("=" * 60)
        print(f"Overall anchor coverage: {overall_coverage:.1f}%")
        print(f"Total single-word gaps: {total_single_word_gaps}")
        print(f"Total words: {total_words}")
        print(f"Words in anchors: {total_words_in_anchors}")

        # Baseline: 80.4% coverage, 179 single-word gaps
        # Allow minor regression (within 2%) due to algorithm variations
        assert overall_coverage >= 78.0, (
            f"Anchor coverage {overall_coverage:.1f}% regressed significantly "
            f"from baseline of 80.4% (minimum 78% allowed)"
        )

        # Single-word gaps should not increase beyond baseline + 5%
        assert total_single_word_gaps <= 188, (
            f"Single-word gaps {total_single_word_gaps} increased beyond baseline "
            f"of 179 (maximum 188 allowed)"
        )

        # Check for improvement - gap extension should reduce single-word gaps
        if total_single_word_gaps < 179:
            gaps_reduced = 179 - total_single_word_gaps
            print(f"\n✅ Gap extension reduced single-word gaps by {gaps_reduced}")


if __name__ == "__main__":
    # Run with: python -m pytest tests/test_anchor_sequence_real_data.py -v -s
    pytest.main([__file__, "-v", "-s"])
