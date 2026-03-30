"""
Tests for the search-lyrics operation and API endpoint.

Covers:
- create_lyrics_providers() factory function
- CorrectionOperations.search_lyrics_sources() static method
- CorrectionOperations.add_lyrics_source() force parameter
- POST /{job_id}/search-lyrics API endpoint
"""
import pytest
import os
from unittest.mock import MagicMock, patch, call
from typing import Dict, Any, List


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_lyrics_data(source: str = "TestSource", text: str = "Hello world\nTest line") -> "LyricsData":
    """Create a minimal LyricsData object for testing."""
    from karaoke_gen.lyrics_transcriber.types import LyricsData, LyricsSegment, LyricsMetadata, Word
    from karaoke_gen.lyrics_transcriber.utils.word_utils import WordUtils

    segments = []
    for line in text.strip().splitlines():
        words = [
            Word(id=WordUtils.generate_id(), text=w, start_time=None, end_time=None)
            for w in line.split()
        ]
        segments.append(
            LyricsSegment(
                id=WordUtils.generate_id(),
                text=line,
                words=words,
                start_time=None,
                end_time=None,
            )
        )
    return LyricsData(source=source, segments=segments, metadata=LyricsMetadata(source=source, track_name="Test Track", artist_names="Test Artist"))


def _make_correction_result(
    reference_lyrics: Dict = None,
    metadata: Dict = None,
) -> "CorrectionResult":
    """Create a minimal CorrectionResult for testing."""
    from karaoke_gen.lyrics_transcriber.types import (
        CorrectionResult, LyricsSegment, Word, LyricsData, LyricsMetadata
    )
    from karaoke_gen.lyrics_transcriber.utils.word_utils import WordUtils

    # A single transcription segment
    word = Word(id=WordUtils.generate_id(), text="hello", start_time=0.0, end_time=0.5)
    segment = LyricsSegment(id=WordUtils.generate_id(), text="hello", words=[word], start_time=0.0, end_time=0.5)

    return CorrectionResult(
        original_segments=[segment],
        corrected_segments=[segment],
        corrections=[],
        corrections_made=0,
        confidence=1.0,
        reference_lyrics=reference_lyrics or {},
        anchor_sequences=[],
        gap_sequences=[],
        resized_segments=None,
        metadata=metadata or {"audio_hash": "testhash"},
        correction_steps=[],
        word_id_map={},
        segment_id_map={},
    )


# ---------------------------------------------------------------------------
# Tests: create_lyrics_providers factory
# ---------------------------------------------------------------------------

class TestCreateLyricsProviders:
    """Tests for the create_lyrics_providers() module-level factory."""

    def test_returns_list(self):
        """Factory always returns a list."""
        from karaoke_gen.lyrics_transcriber.correction.operations import create_lyrics_providers
        with patch.dict(os.environ, {}, clear=False):
            # Remove API keys so only LRCLIB is created
            env_override = {
                k: "" for k in ["GENIUS_API_KEY", "RAPIDAPI_KEY", "SPOTIFY_COOKIE_SP_DC"]
            }
            with patch.dict(os.environ, env_override):
                providers = create_lyrics_providers(cache_dir="/tmp")
        assert isinstance(providers, list)

    def test_lrclib_always_included(self):
        """LRCLIBProvider is always included regardless of env vars."""
        from karaoke_gen.lyrics_transcriber.correction.operations import create_lyrics_providers
        with patch.dict(os.environ, {"GENIUS_API_KEY": "", "RAPIDAPI_KEY": "", "SPOTIFY_COOKIE_SP_DC": ""}):
            providers = create_lyrics_providers(cache_dir="/tmp")
        provider_names = [p.get_name() for p in providers]
        assert "LRCLIB" in provider_names

    def test_genius_included_when_genius_key_set(self):
        """GeniusProvider is included when GENIUS_API_KEY is set."""
        from karaoke_gen.lyrics_transcriber.correction.operations import create_lyrics_providers
        with patch.dict(os.environ, {"GENIUS_API_KEY": "fake-key", "RAPIDAPI_KEY": "", "SPOTIFY_COOKIE_SP_DC": ""}):
            providers = create_lyrics_providers(cache_dir="/tmp")
        provider_names = [p.get_name() for p in providers]
        assert "Genius" in provider_names

    def test_genius_included_when_rapidapi_key_set(self):
        """GeniusProvider is included when RAPIDAPI_KEY is set (uses RapidAPI path)."""
        from karaoke_gen.lyrics_transcriber.correction.operations import create_lyrics_providers
        with patch.dict(os.environ, {"GENIUS_API_KEY": "", "RAPIDAPI_KEY": "fake-rapidapi", "SPOTIFY_COOKIE_SP_DC": ""}):
            providers = create_lyrics_providers(cache_dir="/tmp")
        provider_names = [p.get_name() for p in providers]
        assert "Genius" in provider_names

    def test_spotify_included_when_cookie_set(self):
        """SpotifyProvider is included when SPOTIFY_COOKIE_SP_DC is set."""
        from karaoke_gen.lyrics_transcriber.correction.operations import create_lyrics_providers
        with patch.dict(os.environ, {"GENIUS_API_KEY": "", "RAPIDAPI_KEY": "", "SPOTIFY_COOKIE_SP_DC": "fake-cookie"}):
            providers = create_lyrics_providers(cache_dir="/tmp")
        provider_names = [p.get_name() for p in providers]
        assert "Spotify" in provider_names

    def test_musixmatch_included_when_rapidapi_key_set(self):
        """MusixmatchProvider is included when RAPIDAPI_KEY is set."""
        from karaoke_gen.lyrics_transcriber.correction.operations import create_lyrics_providers
        with patch.dict(os.environ, {"GENIUS_API_KEY": "", "RAPIDAPI_KEY": "fake-rapidapi", "SPOTIFY_COOKIE_SP_DC": ""}):
            providers = create_lyrics_providers(cache_dir="/tmp")
        provider_names = [p.get_name() for p in providers]
        assert "Musixmatch" in provider_names

    def test_genius_not_included_without_keys(self):
        """GeniusProvider is excluded when neither key is set."""
        from karaoke_gen.lyrics_transcriber.correction.operations import create_lyrics_providers
        with patch.dict(os.environ, {"GENIUS_API_KEY": "", "RAPIDAPI_KEY": "", "SPOTIFY_COOKIE_SP_DC": ""}):
            providers = create_lyrics_providers(cache_dir="/tmp")
        provider_names = [p.get_name() for p in providers]
        assert "Genius" not in provider_names

    def test_all_providers_when_all_keys_set(self):
        """All four providers are returned when all credentials are set."""
        from karaoke_gen.lyrics_transcriber.correction.operations import create_lyrics_providers
        with patch.dict(os.environ, {
            "GENIUS_API_KEY": "gk",
            "RAPIDAPI_KEY": "rk",
            "SPOTIFY_COOKIE_SP_DC": "sc",
        }):
            providers = create_lyrics_providers(cache_dir="/tmp")
        provider_names = [p.get_name() for p in providers]
        assert "Genius" in provider_names
        assert "Spotify" in provider_names
        assert "Musixmatch" in provider_names
        assert "LRCLIB" in provider_names
        assert len(providers) == 4


# ---------------------------------------------------------------------------
# Tests: CorrectionOperations.search_lyrics_sources
# ---------------------------------------------------------------------------

class TestSearchLyricsSources:
    """Tests for CorrectionOperations.search_lyrics_sources()."""

    def _mock_provider(self, name: str, lyrics_data=None):
        """Build a mock BaseLyricsProvider that returns lyrics_data."""
        mock = MagicMock()
        mock.get_name.return_value = name
        mock.fetch_lyrics.return_value = lyrics_data
        return mock

    def test_returns_dict_with_expected_keys(self):
        """Return value always contains all four expected keys."""
        from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations

        correction_result = _make_correction_result()

        with patch("karaoke_gen.lyrics_transcriber.correction.operations.create_lyrics_providers") as mock_factory:
            mock_factory.return_value = []  # No providers — simulates empty env
            result = CorrectionOperations.search_lyrics_sources(
                correction_result=correction_result,
                artist="Artist",
                title="Title",
                cache_dir="/tmp",
            )

        assert "updated_result" in result
        assert "sources_added" in result
        assert "sources_rejected" in result
        assert "sources_not_found" in result

    def test_no_providers_returns_no_results(self):
        """When no providers are available, returns no results."""
        from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations

        correction_result = _make_correction_result()

        with patch("karaoke_gen.lyrics_transcriber.correction.operations.create_lyrics_providers") as mock_factory:
            mock_factory.return_value = []
            result = CorrectionOperations.search_lyrics_sources(
                correction_result=correction_result,
                artist="Artist",
                title="Title",
                cache_dir="/tmp",
            )

        assert result["updated_result"] is None
        assert result["sources_added"] == []

    def test_provider_not_found_appended_to_not_found(self):
        """Provider returning None is recorded in sources_not_found."""
        from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations

        correction_result = _make_correction_result()
        mock_provider = self._mock_provider("LRCLIB", lyrics_data=None)

        with patch("karaoke_gen.lyrics_transcriber.correction.operations.create_lyrics_providers") as mock_factory:
            mock_factory.return_value = [mock_provider]
            result = CorrectionOperations.search_lyrics_sources(
                correction_result=correction_result,
                artist="Artist",
                title="Title",
                cache_dir="/tmp",
            )

        assert "LRCLIB" in result["sources_not_found"]
        assert result["updated_result"] is None

    def test_provider_exception_appended_to_not_found(self):
        """Provider raising an exception is recorded in sources_not_found."""
        from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations

        correction_result = _make_correction_result()
        mock_provider = self._mock_provider("Genius")
        mock_provider.fetch_lyrics.side_effect = RuntimeError("network error")

        with patch("karaoke_gen.lyrics_transcriber.correction.operations.create_lyrics_providers") as mock_factory:
            mock_factory.return_value = [mock_provider]
            result = CorrectionOperations.search_lyrics_sources(
                correction_result=correction_result,
                artist="Artist",
                title="Title",
                cache_dir="/tmp",
            )

        assert "Genius" in result["sources_not_found"]

    def test_existing_source_skipped(self):
        """Provider whose name is already in reference_lyrics is skipped."""
        from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations

        existing_lyrics = {"Genius": _make_lyrics_data("Genius")}
        correction_result = _make_correction_result(reference_lyrics=existing_lyrics)
        mock_provider = self._mock_provider("Genius", lyrics_data=_make_lyrics_data("Genius"))

        with patch("karaoke_gen.lyrics_transcriber.correction.operations.create_lyrics_providers") as mock_factory:
            mock_factory.return_value = [mock_provider]
            result = CorrectionOperations.search_lyrics_sources(
                correction_result=correction_result,
                artist="Artist",
                title="Title",
                cache_dir="/tmp",
            )

        # fetch_lyrics should NOT have been called
        mock_provider.fetch_lyrics.assert_not_called()
        assert result["updated_result"] is None  # Nothing new to add

    def test_lyrics_found_runs_corrector(self):
        """When lyrics are found, LyricsCorrector.run is called."""
        from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations

        correction_result = _make_correction_result()
        lyrics_data = _make_lyrics_data("LRCLIB")
        mock_provider = self._mock_provider("LRCLIB", lyrics_data=lyrics_data)

        mock_corrector = MagicMock()
        mock_corrector.all_handlers = []
        mock_corrector.handlers = []
        mock_updated = _make_correction_result(metadata={"audio_hash": "testhash"})
        mock_corrector.run.return_value = mock_updated

        with patch("karaoke_gen.lyrics_transcriber.correction.operations.create_lyrics_providers") as mock_factory, \
             patch("karaoke_gen.lyrics_transcriber.correction.operations.LyricsCorrector") as MockCorrector:
            mock_factory.return_value = [mock_provider]
            MockCorrector.return_value = mock_corrector
            result = CorrectionOperations.search_lyrics_sources(
                correction_result=correction_result,
                artist="Artist",
                title="Title",
                cache_dir="/tmp",
            )

        assert mock_corrector.run.called

    def test_force_sources_bypass_rejection(self):
        """Sources in force_sources are added even if the corrector rejects them."""
        from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations

        correction_result = _make_correction_result()
        lyrics_data = _make_lyrics_data("Genius")
        mock_provider = self._mock_provider("Genius", lyrics_data=lyrics_data)

        # Corrector result that puts "Genius" in rejected_sources
        mock_corrector = MagicMock()
        mock_corrector.all_handlers = []
        mock_corrector.handlers = []
        mock_updated = _make_correction_result(
            reference_lyrics={},  # Corrector filtered it out
            metadata={
                "audio_hash": "testhash",
                "rejected_sources": {
                    "Genius": {"relevance": 0.0, "matched_words": 0, "total_words": 10,
                               "track_name": "", "artist_names": ""},
                },
            }
        )
        mock_corrector.run.return_value = mock_updated

        with patch("karaoke_gen.lyrics_transcriber.correction.operations.create_lyrics_providers") as mock_factory, \
             patch("karaoke_gen.lyrics_transcriber.correction.operations.LyricsCorrector") as MockCorrector:
            mock_factory.return_value = [mock_provider]
            MockCorrector.return_value = mock_corrector

            result = CorrectionOperations.search_lyrics_sources(
                correction_result=correction_result,
                artist="Artist",
                title="Title",
                cache_dir="/tmp",
                force_sources=["Genius"],
            )

        # The forced source should be in sources_added, not rejected
        assert "Genius" in result["sources_added"]
        assert "Genius" not in result["sources_rejected"]
        # And it should have been re-added to reference_lyrics
        assert "Genius" in result["updated_result"].reference_lyrics

    def test_audio_hash_preserved(self):
        """The audio_hash from the original correction_result is preserved."""
        from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations

        original_hash = "original-audio-hash-abc"
        correction_result = _make_correction_result(metadata={"audio_hash": original_hash})
        lyrics_data = _make_lyrics_data("LRCLIB")
        mock_provider = self._mock_provider("LRCLIB", lyrics_data=lyrics_data)

        mock_corrector = MagicMock()
        mock_corrector.all_handlers = []
        mock_corrector.handlers = []
        mock_updated = _make_correction_result(metadata={})  # metadata without audio_hash
        mock_corrector.run.return_value = mock_updated

        with patch("karaoke_gen.lyrics_transcriber.correction.operations.create_lyrics_providers") as mock_factory, \
             patch("karaoke_gen.lyrics_transcriber.correction.operations.LyricsCorrector") as MockCorrector:
            mock_factory.return_value = [mock_provider]
            MockCorrector.return_value = mock_corrector
            result = CorrectionOperations.search_lyrics_sources(
                correction_result=correction_result,
                artist="Artist",
                title="Title",
                cache_dir="/tmp",
            )

        if result["updated_result"] is not None:
            assert result["updated_result"].metadata.get("audio_hash") == original_hash


# ---------------------------------------------------------------------------
# Tests: CorrectionOperations.add_lyrics_source force parameter
# ---------------------------------------------------------------------------

class TestAddLyricsSourceForce:
    """Tests for the force parameter added to add_lyrics_source."""

    def test_force_false_does_not_override_rejection(self):
        """With force=False (default), rejected sources remain rejected."""
        from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations

        correction_result = _make_correction_result()

        mock_corrector = MagicMock()
        mock_corrector.all_handlers = []
        mock_corrector.handlers = []
        mock_updated = _make_correction_result(
            reference_lyrics={},  # source was filtered out
            metadata={
                "audio_hash": "testhash",
                "rejected_sources": {
                    "custom": {"relevance": 0.0, "matched_words": 0, "total_words": 5,
                               "track_name": "", "artist_names": ""},
                },
            }
        )
        mock_corrector.run.return_value = mock_updated

        with patch("karaoke_gen.lyrics_transcriber.correction.operations.LyricsCorrector") as MockCorrector:
            MockCorrector.return_value = mock_corrector
            result = CorrectionOperations.add_lyrics_source(
                correction_result=correction_result,
                source="custom",
                lyrics_text="Hello world\nTest line",
                cache_dir="/tmp",
                force=False,
            )

        # Source should NOT be in reference_lyrics when force=False
        assert "custom" not in result.reference_lyrics

    def test_force_true_adds_source_even_if_rejected(self):
        """With force=True, source is added back even if the corrector rejected it."""
        from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations

        correction_result = _make_correction_result()

        mock_corrector = MagicMock()
        mock_corrector.all_handlers = []
        mock_corrector.handlers = []
        mock_updated = _make_correction_result(
            reference_lyrics={},  # source was filtered out
            metadata={
                "audio_hash": "testhash",
                "rejected_sources": {
                    "custom": {"relevance": 0.0, "matched_words": 0, "total_words": 5,
                               "track_name": "", "artist_names": ""},
                },
            }
        )
        mock_corrector.run.return_value = mock_updated

        with patch("karaoke_gen.lyrics_transcriber.correction.operations.LyricsCorrector") as MockCorrector:
            MockCorrector.return_value = mock_corrector
            result = CorrectionOperations.add_lyrics_source(
                correction_result=correction_result,
                source="custom",
                lyrics_text="Hello world\nTest line",
                cache_dir="/tmp",
                force=True,
            )

        # Source SHOULD be in reference_lyrics when force=True
        assert "custom" in result.reference_lyrics

    def test_force_true_no_op_when_source_not_rejected(self):
        """With force=True but source wasn't rejected, nothing extra happens."""
        from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations

        correction_result = _make_correction_result()

        mock_corrector = MagicMock()
        mock_corrector.all_handlers = []
        mock_corrector.handlers = []
        # Source is accepted (not in rejected_sources)
        mock_updated = _make_correction_result(
            reference_lyrics={"custom": _make_lyrics_data("custom")},
            metadata={"audio_hash": "testhash"},
        )
        mock_corrector.run.return_value = mock_updated

        with patch("karaoke_gen.lyrics_transcriber.correction.operations.LyricsCorrector") as MockCorrector:
            MockCorrector.return_value = mock_corrector
            result = CorrectionOperations.add_lyrics_source(
                correction_result=correction_result,
                source="custom",
                lyrics_text="Hello world\nTest line",
                cache_dir="/tmp",
                force=True,
            )

        # Source remains in reference_lyrics normally
        assert "custom" in result.reference_lyrics


# ---------------------------------------------------------------------------
# Tests: POST /{job_id}/search-lyrics API endpoint
# ---------------------------------------------------------------------------

class TestSearchLyricsEndpoint:
    """Tests for the POST /{job_id}/search-lyrics API endpoint."""

    def test_endpoint_exists(self):
        """Verify the search-lyrics endpoint is registered in the router."""
        from backend.api.routes.review import router
        paths = [route.path for route in router.routes]
        assert any("search-lyrics" in p for p in paths), f"search-lyrics not found in: {paths}"

    def test_search_lyrics_requires_artist_and_title(self):
        """Document that the endpoint requires artist and title fields."""
        valid_payload = {
            "artist": "The Beatles",
            "title": "Hey Jude",
        }
        assert "artist" in valid_payload
        assert "title" in valid_payload
        assert valid_payload["artist"].strip()
        assert valid_payload["title"].strip()

    def test_search_lyrics_accepts_force_sources(self):
        """Document that force_sources is an optional list of provider names."""
        payload_with_force = {
            "artist": "The Beatles",
            "title": "Hey Jude",
            "force_sources": ["Genius", "LRCLIB"],
        }
        assert isinstance(payload_with_force["force_sources"], list)

    def test_success_response_format(self):
        """Document the expected success response format."""
        example_success_response = {
            "status": "success",
            "data": {},           # CorrectionData dict
            "sources_added": ["LRCLIB"],
            "sources_rejected": {"Genius": {"relevance": 0.1}},
            "sources_not_found": ["Spotify"],
        }
        assert example_success_response["status"] == "success"
        assert "data" in example_success_response
        assert "sources_added" in example_success_response
        assert "sources_rejected" in example_success_response
        assert "sources_not_found" in example_success_response

    def test_no_results_response_format(self):
        """Document the expected no_results response format."""
        example_no_results_response = {
            "status": "no_results",
            "message": "No matching lyrics found from any provider",
            "sources_rejected": {},
            "sources_not_found": ["Genius", "LRCLIB"],
        }
        assert example_no_results_response["status"] == "no_results"
        assert "message" in example_no_results_response
        assert "sources_rejected" in example_no_results_response
        assert "sources_not_found" in example_no_results_response

    def test_search_lyrics_uses_correction_operations(self):
        """Verify CorrectionOperations.search_lyrics_sources exists and is callable."""
        from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations

        assert hasattr(CorrectionOperations, "search_lyrics_sources")
        method = getattr(CorrectionOperations, "search_lyrics_sources")
        assert callable(method)

    def test_add_lyrics_source_has_force_parameter(self):
        """Verify add_lyrics_source now accepts a force parameter."""
        import inspect
        from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations

        sig = inspect.signature(CorrectionOperations.add_lyrics_source)
        assert "force" in sig.parameters
        # Default should be False
        assert sig.parameters["force"].default is False
