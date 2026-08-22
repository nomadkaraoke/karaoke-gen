"""
Unit tests for audio_fetcher module.

Tests cover the AudioFetcher abstraction layer, FlacFetchAudioFetcher implementation,
and RemoteFlacFetchAudioFetcher for remote API access.
"""

import pytest
import os
import tempfile
import logging
from unittest.mock import MagicMock, patch, PropertyMock

from karaoke_gen.audio_fetcher import (
    AudioSearchResult,
    AudioFetchResult,
    AudioFetcher,
    AudioFetcherError,
    NoResultsError,
    DownloadError,
    UserCancelledError,
    FlacFetchAudioFetcher,
    RemoteFlacFetchAudioFetcher,
    create_audio_fetcher,
    HTTPX_AVAILABLE,
)
import karaoke_gen.audio_fetcher as audio_fetcher_module

# Import flacfetch models for creating test Release objects
from flacfetch.core.models import Release, Quality, AudioFormat, MediaSource


def create_test_release(
    title: str = "Test Song",
    artist: str = "Test Artist",
    source_name: str = "YouTube",
    duration_seconds: int = 180,
    seeders: int = None,
    is_lossless: bool = False,
) -> Release:
    """Create a real Release object for testing.
    
    This is needed because flacfetch's print_releases function does
    type comparisons that fail with MagicMock objects.
    """
    if is_lossless:
        quality = Quality(format=AudioFormat.FLAC, bit_depth=16, media=MediaSource.CD)
    else:
        quality = Quality(format=AudioFormat.MP3, bitrate=320, media=MediaSource.WEB)
    
    return Release(
        title=title,
        artist=artist,
        quality=quality,
        source_name=source_name,
        duration_seconds=duration_seconds,
        seeders=seeders,
    )


class TestAudioSearchResult:
    """Tests for AudioSearchResult dataclass."""

    def test_basic_creation(self):
        """Test creating an AudioSearchResult with required fields."""
        result = AudioSearchResult(
            title="Test Song",
            artist="Test Artist",
            url="https://example.com/song",
            provider="YouTube",
        )
        assert result.title == "Test Song"
        assert result.artist == "Test Artist"
        assert result.url == "https://example.com/song"
        assert result.provider == "YouTube"
        assert result.duration is None
        assert result.quality is None
        assert result.source_id is None
        assert result.index == 0  # Default index
        assert result.raw_result is None

    def test_full_creation(self):
        """Test creating an AudioSearchResult with all fields."""
        raw = {"some": "data"}
        result = AudioSearchResult(
            title="Test Song",
            artist="Test Artist",
            url="https://example.com/song",
            provider="RED",
            duration=240,
            quality="FLAC",
            source_id="123456",
            index=5,
            raw_result=raw,
        )
        assert result.duration == 240
        assert result.quality == "FLAC"
        assert result.source_id == "123456"
        assert result.index == 5
        assert result.raw_result == raw

    def test_to_dict_basic(self):
        """Test to_dict with minimal fields."""
        result = AudioSearchResult(
            title="Test Song",
            artist="Test Artist",
            url="https://example.com/song",
            provider="YouTube",
        )
        data = result.to_dict()
        
        assert data["title"] == "Test Song"
        assert data["artist"] == "Test Artist"
        assert data["url"] == "https://example.com/song"
        assert data["provider"] == "YouTube"
        assert data["duration"] is None
        assert data["quality"] is None
        assert data["source_id"] is None
        assert data["index"] == 0
        # raw_result should NOT be in serialized dict
        assert "raw_result" not in data

    def test_to_dict_full(self):
        """Test to_dict with all fields populated."""
        raw = {"some": "data"}
        result = AudioSearchResult(
            title="Test Song",
            artist="Test Artist",
            url="https://example.com/song",
            provider="RED",
            duration=240,
            quality="FLAC 24bit",
            source_id="abc123",
            index=3,
            raw_result=raw,
        )
        data = result.to_dict()
        
        assert data["title"] == "Test Song"
        assert data["artist"] == "Test Artist"
        assert data["url"] == "https://example.com/song"
        assert data["provider"] == "RED"
        assert data["duration"] == 240
        assert data["quality"] == "FLAC 24bit"
        assert data["source_id"] == "abc123"
        assert data["index"] == 3
        # raw_result should NOT be in serialized dict
        assert "raw_result" not in data

    def test_to_dict_with_release_raw_result(self):
        """Test to_dict when raw_result has to_dict() that returns release fields."""
        # Create a mock raw_result that mimics flacfetch Release behavior
        class MockRelease:
            def to_dict(self):
                return {
                    'year': 2024,
                    'label': 'Test Records',
                    'edition_info': 'Remastered',
                    'release_type': 'Album',
                    'channel': 'TestChannel',
                    'view_count': 1000000,
                    'size_bytes': 52428800,
                    'target_file_size': 26214400,
                    'track_pattern': 'Test Song',
                    'match_score': 0.95,
                    'formatted_size': '50.0 MB',
                    'formatted_duration': '3:30',
                    'formatted_views': '1.0M',
                    'is_lossless': True,
                    'quality_str': 'FLAC 24bit WEB',
                    'quality': {'format': 'FLAC', 'bit_depth': 24, 'media': 'WEB'},
                }
        
        result = AudioSearchResult(
            title="Test Song",
            artist="Test Artist",
            url="https://example.com/song",
            provider="RED",
            raw_result=MockRelease(),
        )
        data = result.to_dict()
        
        # Basic fields
        assert data["title"] == "Test Song"
        assert data["provider"] == "RED"
        
        # Release-specific fields should be included
        assert data["year"] == 2024
        assert data["label"] == "Test Records"
        assert data["edition_info"] == "Remastered"
        assert data["release_type"] == "Album"
        assert data["channel"] == "TestChannel"
        assert data["view_count"] == 1000000
        assert data["size_bytes"] == 52428800
        assert data["target_file_size"] == 26214400
        assert data["track_pattern"] == "Test Song"
        assert data["match_score"] == 0.95
        assert data["formatted_size"] == "50.0 MB"
        assert data["formatted_duration"] == "3:30"
        assert data["formatted_views"] == "1.0M"
        assert data["is_lossless"] == True
        assert data["quality_str"] == "FLAC 24bit WEB"
        # quality should be renamed to quality_data
        assert data["quality_data"] == {'format': 'FLAC', 'bit_depth': 24, 'media': 'WEB'}
        # raw_result should NOT be in serialized dict
        assert "raw_result" not in data

    def test_from_dict_basic(self):
        """Test from_dict with minimal fields."""
        data = {
            "title": "Test Song",
            "artist": "Test Artist",
            "url": "https://example.com/song",
            "provider": "YouTube",
        }
        result = AudioSearchResult.from_dict(data)
        
        assert result.title == "Test Song"
        assert result.artist == "Test Artist"
        assert result.url == "https://example.com/song"
        assert result.provider == "YouTube"
        assert result.duration is None
        assert result.quality is None
        assert result.source_id is None
        assert result.index == 0  # Default
        assert result.raw_result is None  # Always None from dict

    def test_from_dict_full(self):
        """Test from_dict with all fields."""
        data = {
            "title": "Test Song",
            "artist": "Test Artist",
            "url": "https://example.com/song",
            "provider": "RED",
            "duration": 240,
            "quality": "FLAC 24bit",
            "source_id": "abc123",
            "index": 7,
            "seeders": 15,
            "target_file": "02. Test Song.flac",
        }
        result = AudioSearchResult.from_dict(data)
        
        assert result.title == "Test Song"
        assert result.artist == "Test Artist"
        assert result.url == "https://example.com/song"
        assert result.provider == "RED"
        assert result.duration == 240
        assert result.quality == "FLAC 24bit"
        assert result.source_id == "abc123"
        assert result.index == 7
        assert result.seeders == 15
        assert result.target_file == "02. Test Song.flac"
        assert result.raw_result is None  # Never restored from dict

    def test_from_dict_with_defaults(self):
        """Test from_dict uses defaults for missing fields."""
        data = {}
        result = AudioSearchResult.from_dict(data)
        
        assert result.title == ""
        assert result.artist == ""
        assert result.url == ""
        assert result.provider == "Unknown"
        assert result.index == 0
        assert result.raw_result is None

    def test_roundtrip_serialization(self):
        """Test to_dict -> from_dict roundtrip preserves data."""
        original = AudioSearchResult(
            title="Test Song",
            artist="Test Artist",
            url="https://example.com/song",
            provider="RED",
            duration=240,
            quality="FLAC",
            source_id="abc123",
            index=2,
            raw_result={"will": "be lost"},
        )
        
        data = original.to_dict()
        restored = AudioSearchResult.from_dict(data)
        
        assert restored.title == original.title
        assert restored.artist == original.artist
        assert restored.url == original.url
        assert restored.provider == original.provider
        assert restored.duration == original.duration
        assert restored.quality == original.quality
        assert restored.source_id == original.source_id
        assert restored.index == original.index
        # raw_result is NOT preserved
        assert restored.raw_result is None


class TestAudioFetchResult:
    """Tests for AudioFetchResult dataclass."""

    def test_basic_creation(self):
        """Test creating an AudioFetchResult with required fields."""
        result = AudioFetchResult(
            filepath="/path/to/song.flac",
            artist="Test Artist",
            title="Test Song",
            provider="YouTube",
        )
        assert result.filepath == "/path/to/song.flac"
        assert result.artist == "Test Artist"
        assert result.title == "Test Song"
        assert result.provider == "YouTube"
        assert result.duration is None
        assert result.quality is None

    def test_full_creation(self):
        """Test creating an AudioFetchResult with all fields."""
        result = AudioFetchResult(
            filepath="/path/to/song.flac",
            artist="Test Artist",
            title="Test Song",
            provider="RED",
            duration=180,
            quality="FLAC 24bit",
        )
        assert result.duration == 180
        assert result.quality == "FLAC 24bit"

    def test_to_dict_basic(self):
        """Test to_dict with minimal fields."""
        result = AudioFetchResult(
            filepath="/path/to/song.flac",
            artist="Test Artist",
            title="Test Song",
            provider="YouTube",
        )
        data = result.to_dict()
        
        assert data["filepath"] == "/path/to/song.flac"
        assert data["artist"] == "Test Artist"
        assert data["title"] == "Test Song"
        assert data["provider"] == "YouTube"
        assert data["duration"] is None
        assert data["quality"] is None

    def test_to_dict_full(self):
        """Test to_dict with all fields populated."""
        result = AudioFetchResult(
            filepath="/path/to/song.flac",
            artist="Test Artist",
            title="Test Song",
            provider="RED",
            duration=180,
            quality="FLAC 24bit",
        )
        data = result.to_dict()
        
        assert data["filepath"] == "/path/to/song.flac"
        assert data["artist"] == "Test Artist"
        assert data["title"] == "Test Song"
        assert data["provider"] == "RED"
        assert data["duration"] == 180
        assert data["quality"] == "FLAC 24bit"

    def test_from_dict_basic(self):
        """Test from_dict with minimal fields."""
        data = {
            "filepath": "/path/to/song.flac",
            "artist": "Test Artist",
            "title": "Test Song",
            "provider": "YouTube",
        }
        result = AudioFetchResult.from_dict(data)
        
        assert result.filepath == "/path/to/song.flac"
        assert result.artist == "Test Artist"
        assert result.title == "Test Song"
        assert result.provider == "YouTube"
        assert result.duration is None
        assert result.quality is None

    def test_from_dict_full(self):
        """Test from_dict with all fields."""
        data = {
            "filepath": "/path/to/song.flac",
            "artist": "Test Artist",
            "title": "Test Song",
            "provider": "RED",
            "duration": 180,
            "quality": "FLAC 24bit",
        }
        result = AudioFetchResult.from_dict(data)
        
        assert result.filepath == "/path/to/song.flac"
        assert result.artist == "Test Artist"
        assert result.title == "Test Song"
        assert result.provider == "RED"
        assert result.duration == 180
        assert result.quality == "FLAC 24bit"

    def test_from_dict_with_defaults(self):
        """Test from_dict uses defaults for missing fields."""
        data = {}
        result = AudioFetchResult.from_dict(data)
        
        assert result.filepath == ""
        assert result.artist == ""
        assert result.title == ""
        assert result.provider == "Unknown"
        assert result.duration is None
        assert result.quality is None

    def test_roundtrip_serialization(self):
        """Test to_dict -> from_dict roundtrip preserves data."""
        original = AudioFetchResult(
            filepath="/path/to/song.flac",
            artist="Test Artist",
            title="Test Song",
            provider="RED",
            duration=180,
            quality="FLAC 24bit",
        )
        
        data = original.to_dict()
        restored = AudioFetchResult.from_dict(data)
        
        assert restored.filepath == original.filepath
        assert restored.artist == original.artist
        assert restored.title == original.title
        assert restored.provider == original.provider
        assert restored.duration == original.duration
        assert restored.quality == original.quality


class TestExceptions:
    """Tests for custom exceptions."""

    def test_audio_fetcher_error(self):
        """Test AudioFetcherError can be raised and caught."""
        with pytest.raises(AudioFetcherError) as exc_info:
            raise AudioFetcherError("Test error")
        assert str(exc_info.value) == "Test error"

    def test_no_results_error(self):
        """Test NoResultsError inherits from AudioFetcherError."""
        with pytest.raises(AudioFetcherError):
            raise NoResultsError("No results found")
        
        with pytest.raises(NoResultsError) as exc_info:
            raise NoResultsError("No results found")
        assert str(exc_info.value) == "No results found"

    def test_download_error(self):
        """Test DownloadError inherits from AudioFetcherError."""
        with pytest.raises(AudioFetcherError):
            raise DownloadError("Download failed")
        
        with pytest.raises(DownloadError) as exc_info:
            raise DownloadError("Download failed")
        assert str(exc_info.value) == "Download failed"

    def test_user_cancelled_error(self):
        """Test UserCancelledError inherits from AudioFetcherError."""
        with pytest.raises(AudioFetcherError):
            raise UserCancelledError("User cancelled")
        
        with pytest.raises(UserCancelledError) as exc_info:
            raise UserCancelledError("User cancelled")
        assert str(exc_info.value) == "User cancelled"


class TestCheckInterrupt:
    """Tests for the _check_interrupt function."""
    
    def test_check_interrupt_does_nothing_when_not_requested(self):
        """Test _check_interrupt passes when no interrupt requested."""
        # Ensure flag is False
        audio_fetcher_module._interrupt_requested = False
        # Should not raise
        audio_fetcher_module._check_interrupt()
    
    def test_check_interrupt_raises_when_requested(self):
        """Test _check_interrupt raises UserCancelledError when interrupt requested."""
        try:
            audio_fetcher_module._interrupt_requested = True
            with pytest.raises(UserCancelledError, match="cancelled by user"):
                audio_fetcher_module._check_interrupt()
        finally:
            # Reset the flag
            audio_fetcher_module._interrupt_requested = False


class TestFlacFetchAudioFetcher:
    """Tests for FlacFetchAudioFetcher implementation."""

    @pytest.fixture
    def mock_logger(self):
        """Create a mock logger."""
        return MagicMock(spec=logging.Logger)

    @pytest.fixture
    def fetcher(self, mock_logger):
        """Create a FlacFetchAudioFetcher instance with mocked dependencies."""
        return FlacFetchAudioFetcher(logger=mock_logger)

    @patch.dict(os.environ, {}, clear=True)
    def test_init_default(self):
        """Test default initialization without environment variables."""
        # Clear env vars to test default behavior
        with patch.dict(os.environ, {"RED_API_KEY": "", "RED_API_URL": "", "OPS_API_KEY": "", "OPS_API_URL": ""}, clear=False):
            fetcher = FlacFetchAudioFetcher()
            assert fetcher._manager is None

    def test_init_with_api_keys(self, mock_logger):
        """Test initialization with API keys and URLs."""
        fetcher = FlacFetchAudioFetcher(
            logger=mock_logger,
            red_api_key="red_key",
            red_api_url="https://red.api.url",
            ops_api_key="ops_key",
            ops_api_url="https://ops.api.url",
        )
        assert fetcher._red_api_key == "red_key"
        assert fetcher._red_api_url == "https://red.api.url"
        assert fetcher._ops_api_key == "ops_key"
        assert fetcher._ops_api_url == "https://ops.api.url"

    @patch.dict(os.environ, {"RED_API_KEY": "env_red_key", "RED_API_URL": "https://env.red.url", "OPS_API_KEY": "env_ops_key", "OPS_API_URL": "https://env.ops.url"})
    def test_init_from_environment(self, mock_logger):
        """Test initialization reads API keys and URLs from environment."""
        fetcher = FlacFetchAudioFetcher(logger=mock_logger)
        assert fetcher._red_api_key == "env_red_key"
        assert fetcher._red_api_url == "https://env.red.url"
        assert fetcher._ops_api_key == "env_ops_key"
        assert fetcher._ops_api_url == "https://env.ops.url"

    @patch('karaoke_gen.audio_fetcher.FlacFetchAudioFetcher._get_manager')
    def test_search_success(self, mock_get_manager, fetcher):
        """Test successful search returns results."""
        # Set up mock using correct flacfetch Release attribute names
        mock_manager = MagicMock()
        mock_result = MagicMock()
        mock_result.title = "Found Song"
        mock_result.artist = "Found Artist"
        mock_result.download_url = "https://example.com/song"
        mock_result.source_name = "YouTube"
        mock_result.duration_seconds = 200
        mock_result.quality = MagicMock(__str__=MagicMock(return_value="320kbps"))
        mock_result.info_hash = "abc123"
        mock_manager.search.return_value = [mock_result]
        mock_get_manager.return_value = mock_manager

        # Execute
        results = fetcher.search("Test Artist", "Test Song")

        # Verify
        assert len(results) == 1
        assert results[0].title == "Found Song"
        assert results[0].artist == "Found Artist"
        assert results[0].provider == "YouTube"
        assert results[0].raw_result == mock_result

    @patch('karaoke_gen.audio_fetcher.FlacFetchAudioFetcher._get_manager')
    def test_search_no_results(self, mock_get_manager, fetcher):
        """Test search raises NoResultsError when no results found."""
        mock_manager = MagicMock()
        mock_manager.search.return_value = []
        mock_get_manager.return_value = mock_manager

        with pytest.raises(NoResultsError) as exc_info:
            fetcher.search("Unknown Artist", "Unknown Song")
        
        assert "No results found" in str(exc_info.value)

    @patch('karaoke_gen.audio_fetcher.FlacFetchAudioFetcher._get_manager')
    def test_download_success(self, mock_get_manager, fetcher, tmp_path):
        """Test successful download returns AudioFetchResult."""
        # Set up mock
        mock_manager = MagicMock()
        downloaded_path = str(tmp_path / "song.flac")
        mock_manager.download.return_value = downloaded_path
        mock_get_manager.return_value = mock_manager

        # Create search result
        raw_result = MagicMock()
        search_result = AudioSearchResult(
            title="Test Song",
            artist="Test Artist",
            url="https://example.com/song",
            provider="YouTube",
            duration=180,
            quality="FLAC",
            raw_result=raw_result,
        )

        # Execute
        result = fetcher.download(search_result, str(tmp_path))

        # Verify
        assert result.filepath == downloaded_path
        assert result.artist == "Test Artist"
        assert result.title == "Test Song"
        assert result.provider == "YouTube"
        mock_manager.download.assert_called_once()

    @patch('karaoke_gen.audio_fetcher.FlacFetchAudioFetcher._get_manager')
    def test_download_failure(self, mock_get_manager, fetcher, tmp_path):
        """Test download raises DownloadError on failure."""
        mock_manager = MagicMock()
        mock_manager.download.return_value = None
        mock_get_manager.return_value = mock_manager

        raw_result = MagicMock()
        search_result = AudioSearchResult(
            title="Test Song",
            artist="Test Artist",
            url="https://example.com/song",
            provider="YouTube",
            raw_result=raw_result,
        )

        with pytest.raises(DownloadError) as exc_info:
            fetcher.download(search_result, str(tmp_path))
        
        assert "Download returned no file path" in str(exc_info.value)

    @patch('karaoke_gen.audio_fetcher.FlacFetchAudioFetcher._get_manager')
    def test_download_exception(self, mock_get_manager, fetcher, tmp_path):
        """Test download wraps exceptions in DownloadError."""
        mock_manager = MagicMock()
        mock_manager.download.side_effect = Exception("Network error")
        mock_get_manager.return_value = mock_manager

        raw_result = MagicMock()
        search_result = AudioSearchResult(
            title="Test Song",
            artist="Test Artist",
            url="https://example.com/song",
            provider="YouTube",
            raw_result=raw_result,
        )

        with pytest.raises(DownloadError) as exc_info:
            fetcher.download(search_result, str(tmp_path))
        
        assert "Failed to download" in str(exc_info.value)

    @patch('karaoke_gen.audio_fetcher.FlacFetchAudioFetcher._get_manager')
    def test_search_and_download_auto_select(self, mock_get_manager, fetcher, tmp_path):
        """Test search_and_download with auto_select=True uses select_best."""
        # Set up mock using correct flacfetch Release attribute names
        mock_manager = MagicMock()
        mock_result = MagicMock()
        mock_result.title = "Test Song"
        mock_result.artist = "Test Artist"
        mock_result.source_name = "YouTube"
        mock_result.duration_seconds = 180
        mock_result.quality = MagicMock(__str__=MagicMock(return_value="FLAC 16bit CD"))
        mock_manager.search.return_value = [mock_result]
        mock_manager.select_best.return_value = mock_result
        downloaded_path = str(tmp_path / "song.flac")
        mock_manager.download.return_value = downloaded_path
        mock_get_manager.return_value = mock_manager

        # Execute
        result = fetcher.search_and_download(
            artist="Test Artist",
            title="Test Song",
            output_dir=str(tmp_path),
            auto_select=True,
        )

        # Verify
        mock_manager.select_best.assert_called_once()
        assert result.filepath == downloaded_path
        assert result.artist == "Test Artist"
        assert result.title == "Test Song"

    @patch('karaoke_gen.audio_fetcher.FlacFetchAudioFetcher._get_manager')
    @patch('builtins.input', return_value='1')
    def test_search_and_download_interactive_select(self, mock_input, mock_get_manager, fetcher, tmp_path, capsys):
        """Test search_and_download with interactive selection."""
        # Use real Release object to avoid MagicMock comparison issues in print_releases
        test_release = create_test_release(
            title="Test Song",
            artist="Test Artist",
            source_name="YouTube",
            duration_seconds=180,
        )
        
        mock_manager = MagicMock()
        mock_manager.search.return_value = [test_release]
        downloaded_path = str(tmp_path / "song.flac")
        mock_manager.download.return_value = downloaded_path
        mock_get_manager.return_value = mock_manager

        # Execute
        result = fetcher.search_and_download(
            artist="Test Artist",
            title="Test Song",
            output_dir=str(tmp_path),
            auto_select=False,
        )

        # Verify
        mock_manager.select_best.assert_not_called()
        assert result.filepath == downloaded_path
        
        # Check output shows options (flacfetch uses "Found X releases")
        captured = capsys.readouterr()
        assert "Found" in captured.out and "releases" in captured.out

    @patch('karaoke_gen.audio_fetcher.FlacFetchAudioFetcher._get_manager')
    def test_search_and_download_no_results(self, mock_get_manager, fetcher, tmp_path):
        """Test search_and_download raises NoResultsError when no results."""
        mock_manager = MagicMock()
        mock_manager.search.return_value = []
        mock_get_manager.return_value = mock_manager

        with pytest.raises(NoResultsError):
            fetcher.search_and_download(
                artist="Unknown",
                title="Unknown",
                output_dir=str(tmp_path),
                auto_select=True,
            )

    @patch('builtins.input', return_value='0')
    def test_interactive_select_cancel(self, mock_input, fetcher):
        """Test interactive selection raises UserCancelledError when user cancels."""
        # Use real Release object to avoid MagicMock comparison issues
        test_release = create_test_release(title="Test", artist="Artist")

        with pytest.raises(UserCancelledError):
            fetcher._interactive_select([test_release], "Artist", "Test")

    @patch('builtins.input', side_effect=['invalid', '1'])
    def test_interactive_select_invalid_then_valid(self, mock_input, fetcher):
        """Test interactive selection handles invalid input gracefully."""
        # Use real Release object to avoid MagicMock comparison issues
        test_release = create_test_release(title="Test", artist="Artist")

        result = fetcher._interactive_select([test_release], "Artist", "Test")
        assert result == test_release

    @patch('builtins.input', side_effect=['5', '1'])
    def test_interactive_select_out_of_range_then_valid(self, mock_input, fetcher, capsys):
        """Test interactive selection handles out-of-range input."""
        # Use real Release object to avoid MagicMock comparison issues
        test_release = create_test_release(title="Test", artist="Artist")

        result = fetcher._interactive_select([test_release], "Artist", "Test")
        assert result == test_release
        
        captured = capsys.readouterr()
        # flacfetch's CLIHandler outputs "Invalid selection." for out-of-range
        assert "Invalid selection" in captured.out

    @patch('builtins.input', side_effect=KeyboardInterrupt)
    def test_interactive_select_keyboard_interrupt(self, mock_input, fetcher):
        """Test interactive selection raises UserCancelledError on keyboard interrupt."""
        # Use real Release object to avoid MagicMock comparison issues
        test_release = create_test_release(title="Test", artist="Artist")

        with pytest.raises(UserCancelledError):
            fetcher._interactive_select([test_release], "Artist", "Test")

    @patch('flacfetch.interface.cli.CLIHandler')
    @patch('builtins.input', return_value='1')
    def test_interactive_select_fallback_on_cli_handler_error(self, mock_input, mock_cli_handler, fetcher):
        """Test interactive selection falls back to basic when CLIHandler fails."""
        # Make CLIHandler raise AttributeError to trigger fallback
        mock_cli_handler.side_effect = AttributeError("CLIHandler error")
        
        # Use real Release object
        test_release = create_test_release(title="Test", artist="Artist")
        
        # Should fall back to _basic_interactive_select and succeed
        result = fetcher._interactive_select([test_release], "Artist", "Test")
        assert result == test_release

    @patch('karaoke_gen.audio_fetcher.FlacFetchAudioFetcher._get_manager')
    def test_select_best_returns_index(self, mock_get_manager, fetcher):
        """Test select_best returns the index of the best result."""
        # Create mock raw results
        raw_result_1 = MagicMock()
        raw_result_2 = MagicMock()
        raw_result_3 = MagicMock()
        
        # Create AudioSearchResults with raw_result
        results = [
            AudioSearchResult(
                title="Song 1", artist="Artist", url="url1", provider="YouTube",
                index=0, raw_result=raw_result_1,
            ),
            AudioSearchResult(
                title="Song 2", artist="Artist", url="url2", provider="RED",
                index=1, raw_result=raw_result_2,
            ),
            AudioSearchResult(
                title="Song 3", artist="Artist", url="url3", provider="OPS",
                index=2, raw_result=raw_result_3,
            ),
        ]
        
        # Mock manager.select_best to return the second raw result (index 1)
        mock_manager = MagicMock()
        mock_manager.select_best.return_value = raw_result_2
        mock_get_manager.return_value = mock_manager
        
        # Execute
        best_index = fetcher.select_best(results)
        
        # Verify
        assert best_index == 1
        mock_manager.select_best.assert_called_once()

    @patch('karaoke_gen.audio_fetcher.FlacFetchAudioFetcher._get_manager')
    def test_select_best_empty_list(self, mock_get_manager, fetcher):
        """Test select_best returns 0 for empty list."""
        best_index = fetcher.select_best([])
        assert best_index == 0

    @patch('karaoke_gen.audio_fetcher.FlacFetchAudioFetcher._get_manager')
    def test_select_best_single_result(self, mock_get_manager, fetcher):
        """Test select_best returns 0 for single result."""
        raw_result = MagicMock()
        results = [
            AudioSearchResult(
                title="Song", artist="Artist", url="url", provider="YouTube",
                index=0, raw_result=raw_result,
            ),
        ]
        
        mock_manager = MagicMock()
        mock_manager.select_best.return_value = raw_result
        mock_get_manager.return_value = mock_manager
        
        best_index = fetcher.select_best(results)
        
        assert best_index == 0

    @patch('karaoke_gen.audio_fetcher.FlacFetchAudioFetcher._get_manager')
    def test_select_best_without_raw_results(self, mock_get_manager, fetcher):
        """Test select_best returns 0 when results have no raw_result."""
        results = [
            AudioSearchResult(
                title="Song 1", artist="Artist", url="url1", provider="YouTube",
                index=0, raw_result=None,
            ),
            AudioSearchResult(
                title="Song 2", artist="Artist", url="url2", provider="RED",
                index=1, raw_result=None,
            ),
        ]
        
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        
        best_index = fetcher.select_best(results)
        
        # Should return 0 as fallback since no raw results
        assert best_index == 0

    @patch('karaoke_gen.audio_fetcher.FlacFetchAudioFetcher._get_manager')
    def test_select_best_handles_exception(self, mock_get_manager, fetcher):
        """Test select_best returns 0 when manager.select_best raises exception."""
        raw_result = MagicMock()
        results = [
            AudioSearchResult(
                title="Song", artist="Artist", url="url", provider="YouTube",
                index=0, raw_result=raw_result,
            ),
        ]
        
        mock_manager = MagicMock()
        mock_manager.select_best.side_effect = Exception("Some error")
        mock_get_manager.return_value = mock_manager
        
        # Should not raise, should return 0 as fallback
        best_index = fetcher.select_best(results)
        
        assert best_index == 0

    @patch('karaoke_gen.audio_fetcher.FlacFetchAudioFetcher._get_manager')
    def test_search_sets_index_on_results(self, mock_get_manager, fetcher):
        """Test search() sets the correct index on each result."""
        # Create multiple mock results
        mock_results = []
        for i in range(3):
            mock_result = MagicMock()
            mock_result.title = f"Song {i}"
            mock_result.artist = "Artist"
            mock_result.download_url = f"https://example.com/{i}"
            mock_result.source_name = "YouTube"
            mock_result.duration_seconds = 180
            mock_result.quality = None
            mock_result.info_hash = str(i)
            mock_results.append(mock_result)
        
        mock_manager = MagicMock()
        mock_manager.search.return_value = mock_results
        mock_get_manager.return_value = mock_manager
        
        results = fetcher.search("Artist", "Song")
        
        # Verify indices are set correctly
        assert len(results) == 3
        assert results[0].index == 0
        assert results[1].index == 1
        assert results[2].index == 2


class TestFlacFetcherAlias:
    """Tests for FlacFetcher alias."""
    
    def test_flacfetcher_alias_exists(self):
        """Test FlacFetcher alias is available."""
        from karaoke_gen.audio_fetcher import FlacFetcher
        assert FlacFetcher is not None

    def test_flacfetcher_is_flacfetchaudiofetcher(self):
        """Test FlacFetcher is the same as FlacFetchAudioFetcher."""
        from karaoke_gen.audio_fetcher import FlacFetcher
        assert FlacFetcher is FlacFetchAudioFetcher

    def test_flacfetcher_can_instantiate(self):
        """Test FlacFetcher alias can be used to create instances."""
        from karaoke_gen.audio_fetcher import FlacFetcher
        fetcher = FlacFetcher()
        assert isinstance(fetcher, FlacFetchAudioFetcher)

    def test_flacfetcher_with_parameters(self):
        """Test FlacFetcher alias passes parameters correctly."""
        from karaoke_gen.audio_fetcher import FlacFetcher
        fetcher = FlacFetcher(
            red_api_key="test_key",
            red_api_url="https://red.url",
            ops_api_key="ops_key",
            ops_api_url="https://ops.url",
        )
        assert fetcher._red_api_key == "test_key"
        assert fetcher._red_api_url == "https://red.url"
        assert fetcher._ops_api_key == "ops_key"
        assert fetcher._ops_api_url == "https://ops.url"


class TestCreateAudioFetcher:
    """Tests for create_audio_fetcher factory function."""

    @patch.dict(os.environ, {"FLACFETCH_API_URL": "", "FLACFETCH_API_KEY": ""}, clear=False)
    def test_creates_flacfetch_fetcher(self):
        """Test factory creates FlacFetchAudioFetcher when no remote config."""
        fetcher = create_audio_fetcher()
        assert isinstance(fetcher, FlacFetchAudioFetcher)

    @patch.dict(os.environ, {"FLACFETCH_API_URL": "", "FLACFETCH_API_KEY": ""}, clear=False)
    def test_passes_parameters(self):
        """Test factory passes parameters to constructor."""
        logger = MagicMock()
        fetcher = create_audio_fetcher(
            logger=logger,
            red_api_key="red_key",
            red_api_url="https://red.url",
            ops_api_key="ops_key",
            ops_api_url="https://ops.url",
        )
        assert fetcher.logger == logger
        assert fetcher._red_api_key == "red_key"
        assert fetcher._red_api_url == "https://red.url"
        assert fetcher._ops_api_key == "ops_key"
        assert fetcher._ops_api_url == "https://ops.url"


class TestFlacFetchManagerInitialization:
    """Tests for lazy FetchManager initialization."""

    def test_manager_is_lazily_initialized(self):
        """Test FetchManager is lazily initialized (not created at init time)."""
        fetcher = FlacFetchAudioFetcher()
        # Manager should be None until first use
        assert fetcher._manager is None

    def test_api_keys_stored_at_init(self):
        """Test API keys and URLs are stored at init for later use."""
        fetcher = FlacFetchAudioFetcher(
            red_api_key="red_key",
            red_api_url="https://red.url",
            ops_api_key="ops_key",
            ops_api_url="https://ops.url",
        )
        assert fetcher._red_api_key == "red_key"
        assert fetcher._red_api_url == "https://red.url"
        assert fetcher._ops_api_key == "ops_key"
        assert fetcher._ops_api_url == "https://ops.url"


class TestGetManagerIntegration:
    """
    Integration tests for _get_manager that verify real flacfetch imports and registration.
    
    These tests catch issues like:
    - Import errors from renamed classes (e.g., YouTubeProvider vs YoutubeProvider)
    - Missing downloader registration
    - API changes in flacfetch
    """

    def test_get_manager_imports_work(self):
        """Test that _get_manager can import all required flacfetch modules.
        
        This test would have caught the YouTubeProvider -> YoutubeProvider rename.
        """
        fetcher = FlacFetchAudioFetcher()
        
        # Actually call _get_manager to verify imports work
        manager = fetcher._get_manager()
        
        # Verify manager was created
        assert manager is not None
        assert fetcher._manager is manager  # Should be cached
        
        # Verify calling again returns same instance (lazy init)
        manager2 = fetcher._get_manager()
        assert manager is manager2

    def test_get_manager_registers_youtube_provider_and_downloader(self):
        """Test that YouTube provider AND downloader are registered.
        
        This test would have caught the missing downloader registration.
        """
        fetcher = FlacFetchAudioFetcher()
        manager = fetcher._get_manager()
        
        # Verify YouTube provider is registered
        provider_names = [p.name for p in manager.providers]
        assert "YouTube" in provider_names
        
        # Verify YouTube downloader is registered
        assert "YouTube" in manager._downloader_map, \
            "YouTube downloader must be registered to download from YouTube"

    def test_get_manager_registers_red_with_api_key_url_and_transmission(self):
        """Test that RED provider is registered when API key, URL AND Transmission are available."""
        fetcher = FlacFetchAudioFetcher(red_api_key="test_key", red_api_url="https://red.url")
        
        # Mock Transmission as available
        with patch.object(fetcher, '_check_transmission_available', return_value=True):
            manager = fetcher._get_manager()
        
        # Verify RED provider is registered
        provider_names = [p.name for p in manager.providers]
        assert "RED" in provider_names
        
        # Verify RED downloader is registered (if TorrentDownloader is available)
        try:
            from flacfetch.downloaders.torrent import TorrentDownloader
            assert "RED" in manager._downloader_map, \
                "RED downloader must be registered when TorrentDownloader is available"
        except ImportError:
            # TorrentDownloader not available, which is okay
            pass

    def test_get_manager_skips_red_without_transmission(self):
        """Test that RED provider is NOT registered when Transmission is unavailable."""
        fetcher = FlacFetchAudioFetcher(red_api_key="test_key", red_api_url="https://red.url")
        
        # Mock Transmission as unavailable (the default on Cloud Run)
        with patch.object(fetcher, '_check_transmission_available', return_value=False):
            manager = fetcher._get_manager()
        
        # Verify RED provider is NOT registered (can't download from it)
        provider_names = [p.name for p in manager.providers]
        assert "RED" not in provider_names, \
            "RED provider should NOT be registered when Transmission is unavailable"
        
        # YouTube should still be registered
        assert "YouTube" in provider_names

    def test_get_manager_registers_ops_with_api_key_url_and_transmission(self):
        """Test that OPS provider is registered when API key, URL AND Transmission are available."""
        fetcher = FlacFetchAudioFetcher(ops_api_key="test_key", ops_api_url="https://ops.url")
        
        # Mock Transmission as available
        with patch.object(fetcher, '_check_transmission_available', return_value=True):
            manager = fetcher._get_manager()
        
        # Verify OPS provider is registered
        provider_names = [p.name for p in manager.providers]
        assert "OPS" in provider_names
        
        # Verify OPS downloader is registered (if TorrentDownloader is available)
        try:
            from flacfetch.downloaders.torrent import TorrentDownloader
            assert "OPS" in manager._downloader_map, \
                "OPS downloader must be registered when TorrentDownloader is available"
        except ImportError:
            pass

    def test_get_manager_skips_ops_without_transmission(self):
        """Test that OPS provider is NOT registered when Transmission is unavailable."""
        fetcher = FlacFetchAudioFetcher(ops_api_key="test_key", ops_api_url="https://ops.url")
        
        # Mock Transmission as unavailable
        with patch.object(fetcher, '_check_transmission_available', return_value=False):
            manager = fetcher._get_manager()
        
        # Verify OPS provider is NOT registered
        provider_names = [p.name for p in manager.providers]
        assert "OPS" not in provider_names, \
            "OPS provider should NOT be registered when Transmission is unavailable"
        
        # YouTube should still be registered
        assert "YouTube" in provider_names

    def test_get_manager_with_all_providers_and_transmission(self):
        """Test manager setup with all providers configured when Transmission is available."""
        fetcher = FlacFetchAudioFetcher(
            red_api_key="red_key",
            red_api_url="https://red.url",
            ops_api_key="ops_key",
            ops_api_url="https://ops.url",
        )
        
        # Mock Transmission as available
        with patch.object(fetcher, '_check_transmission_available', return_value=True):
            manager = fetcher._get_manager()
        
        provider_names = [p.name for p in manager.providers]
        
        # All three providers should be registered when Transmission is available
        assert "RED" in provider_names
        assert "OPS" in provider_names
        assert "YouTube" in provider_names
        
        # YouTube downloader should always be registered
        assert "YouTube" in manager._downloader_map

    def test_get_manager_only_youtube_without_transmission(self):
        """Test that only YouTube is registered when Transmission is unavailable."""
        fetcher = FlacFetchAudioFetcher(
            red_api_key="red_key",
            red_api_url="https://red.url",
            ops_api_key="ops_key",
            ops_api_url="https://ops.url",
        )
        
        # Mock Transmission as unavailable (simulates Cloud Run environment)
        with patch.object(fetcher, '_check_transmission_available', return_value=False):
            manager = fetcher._get_manager()
        
        provider_names = [p.name for p in manager.providers]
        
        # Only YouTube should be registered
        assert "RED" not in provider_names
        assert "OPS" not in provider_names
        assert "YouTube" in provider_names
        
        # YouTube downloader should always be registered
        assert "YouTube" in manager._downloader_map


class TestTransmissionAvailabilityCheck:
    """Tests for the Transmission daemon availability check."""

    def test_check_transmission_available_returns_true_when_connected(self):
        """Test that check returns True when Transmission is responsive."""
        fetcher = FlacFetchAudioFetcher()
        
        mock_client = MagicMock()
        mock_client.session_stats.return_value = {}  # Successful response
        
        with patch('transmission_rpc.Client', return_value=mock_client):
            result = fetcher._check_transmission_available()
        
        assert result is True
        # Result should be cached
        assert fetcher._transmission_available is True

    def test_check_transmission_available_returns_false_on_connection_error(self):
        """Test that check returns False when Transmission connection fails."""
        fetcher = FlacFetchAudioFetcher()
        
        with patch('transmission_rpc.Client', side_effect=Exception("Cannot connect")):
            result = fetcher._check_transmission_available()
        
        assert result is False
        # Result should be cached
        assert fetcher._transmission_available is False

    def test_check_transmission_available_caches_result(self):
        """Test that Transmission availability is only checked once."""
        fetcher = FlacFetchAudioFetcher()
        
        mock_client = MagicMock()
        
        with patch('transmission_rpc.Client', return_value=mock_client) as mock_constructor:
            # First call - should connect
            result1 = fetcher._check_transmission_available()
            # Second call - should use cache
            result2 = fetcher._check_transmission_available()
        
        # Should only create client once (cached after first call)
        assert mock_constructor.call_count == 1
        assert result1 == result2

    def test_check_transmission_uses_environment_variables(self):
        """Test that Transmission host/port can be configured via env vars."""
        fetcher = FlacFetchAudioFetcher()
        
        mock_client = MagicMock()
        
        with patch.dict(os.environ, {'TRANSMISSION_HOST': 'custom-host', 'TRANSMISSION_PORT': '9999'}):
            with patch('transmission_rpc.Client', return_value=mock_client) as mock_constructor:
                fetcher._check_transmission_available()
        
        # Verify custom host/port were used
        mock_constructor.assert_called_once_with(host='custom-host', port=9999, timeout=5)


class TestFlacFetchReleaseModelCompatibility:
    """
    Tests to verify our code is compatible with flacfetch's Release model.
    
    These tests use real flacfetch models to catch attribute name mismatches.
    """

    def test_release_model_has_expected_attributes(self):
        """Verify flacfetch Release model has the attributes we expect.
        
        This serves as a canary test - if flacfetch changes attribute names,
        this test will fail immediately.
        """
        from flacfetch.core.models import Release, Quality, AudioFormat, MediaSource
        
        # Create a real Release object
        quality = Quality(format=AudioFormat.FLAC, bit_depth=16, media=MediaSource.CD)
        release = Release(
            title="Test Song",
            artist="Test Artist",
            quality=quality,
            source_name="RED",
        )
        
        # Verify the attributes we use in audio_fetcher.py exist
        assert hasattr(release, 'title')
        assert hasattr(release, 'artist')
        assert hasattr(release, 'source_name')  # NOT 'provider'
        assert hasattr(release, 'quality')
        assert hasattr(release, 'download_url')
        assert hasattr(release, 'duration_seconds')  # NOT 'duration'
        assert hasattr(release, 'info_hash')  # NOT 'id'
        assert hasattr(release, 'seeders')
        assert hasattr(release, 'target_file')
        
        # Verify quality can be converted to string
        quality_str = str(quality)
        assert isinstance(quality_str, str)
        assert "FLAC" in quality_str

    def test_search_converts_release_attributes_correctly(self):
        """Test that search() correctly maps Release attributes to AudioSearchResult."""
        from flacfetch.core.models import Release, Quality, AudioFormat, MediaSource
        
        fetcher = FlacFetchAudioFetcher()
        
        # Create a real Release object with all fields populated
        quality = Quality(format=AudioFormat.FLAC, bit_depth=24, media=MediaSource.WEB)
        real_release = Release(
            title="Real Song",
            artist="Real Artist",
            quality=quality,
            source_name="RED",
            download_url="https://example.com/download",
            duration_seconds=240,
            info_hash="abc123hash",
            seeders=50,
            target_file="01 - Real Song.flac",
        )
        
        # Mock the manager to return our real release
        with patch.object(fetcher, '_get_manager') as mock_get_manager:
            mock_manager = MagicMock()
            mock_manager.search.return_value = [real_release]
            mock_get_manager.return_value = mock_manager
            
            results = fetcher.search("Real Artist", "Real Song")
        
        # Verify correct attribute mapping
        assert len(results) == 1
        result = results[0]
        
        assert result.title == "Real Song"
        assert result.artist == "Real Artist"
        assert result.provider == "RED"  # source_name -> provider
        assert result.duration == 240  # duration_seconds -> duration
        assert result.url == "https://example.com/download"  # download_url -> url
        assert result.source_id == "abc123hash"  # info_hash -> source_id
        assert "FLAC" in result.quality  # quality converted to string
        assert result.raw_result is real_release


class TestFlacFetchCLIHandlerCompatibility:
    """Tests to verify compatibility with flacfetch's CLIHandler for rich output."""

    def test_cli_handler_import_works(self):
        """Verify we can import CLIHandler from flacfetch."""
        from flacfetch.interface.cli import CLIHandler
        
        handler = CLIHandler(target_artist="Test Artist")
        assert handler is not None

    def test_cli_handler_works_with_real_releases(self):
        """Test that CLIHandler can display real Release objects.
        
        This ensures we're passing proper Release objects, not mocks with wrong attributes.
        """
        from flacfetch.core.models import Release, Quality, AudioFormat, MediaSource
        from flacfetch import print_releases, format_release_line
        
        quality = Quality(format=AudioFormat.FLAC, bit_depth=16, media=MediaSource.CD)
        release = Release(
            title="Test Song",
            artist="Test Artist", 
            quality=quality,
            source_name="RED",
            seeders=25,
        )
        
        # Test format_release_line works with a real Release
        try:
            import io
            import sys
            
            # Capture stdout
            captured = io.StringIO()
            sys.stdout = captured
            try:
                # Test single line formatting
                line = format_release_line(1, release, target_artist="Test Artist")
                print(line)
            finally:
                sys.stdout = sys.__stdout__
            
            output = captured.getvalue()
            assert "Test Artist" in output or "Test Song" in output
        except AttributeError as e:
            pytest.fail(f"format_release_line failed with AttributeError: {e}")


class TestDownloadWithCustomFilename:
    """Tests for download with custom filename."""

    @patch('karaoke_gen.audio_fetcher.FlacFetchAudioFetcher._get_manager')
    def test_download_uses_custom_filename(self, mock_get_manager, tmp_path):
        """Test download uses provided custom filename."""
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        downloaded_path = str(tmp_path / "custom_name.flac")
        mock_manager.download.return_value = downloaded_path

        fetcher = FlacFetchAudioFetcher()
        raw_result = MagicMock()
        search_result = AudioSearchResult(
            title="Original Title",
            artist="Original Artist",
            url="https://example.com/song",
            provider="YouTube",
            raw_result=raw_result,
        )

        result = fetcher.download(
            search_result,
            str(tmp_path),
            output_filename="custom_name",
        )

        # Verify custom filename was passed
        mock_manager.download.assert_called_once_with(
            raw_result,
            output_path=str(tmp_path),
            output_filename="custom_name",
        )

    @patch('karaoke_gen.audio_fetcher.FlacFetchAudioFetcher._get_manager')
    def test_download_generates_filename_from_metadata(self, mock_get_manager, tmp_path):
        """Test download generates filename from artist/title if not provided."""
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        downloaded_path = str(tmp_path / "Artist - Title.flac")
        mock_manager.download.return_value = downloaded_path

        fetcher = FlacFetchAudioFetcher()
        raw_result = MagicMock()
        search_result = AudioSearchResult(
            title="Title",
            artist="Artist",
            url="https://example.com/song",
            provider="YouTube",
            raw_result=raw_result,
        )

        result = fetcher.download(search_result, str(tmp_path))

        # Verify filename was generated from metadata
        mock_manager.download.assert_called_once_with(
            raw_result,
            output_path=str(tmp_path),
            output_filename="Artist - Title",
        )


class TestOutputDirectoryCreation:
    """Tests for output directory handling."""

    @patch('karaoke_gen.audio_fetcher.FlacFetchAudioFetcher._get_manager')
    def test_download_creates_output_directory(self, mock_get_manager, tmp_path):
        """Test download creates output directory if it doesn't exist."""
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        
        new_dir = tmp_path / "new_directory"
        downloaded_path = str(new_dir / "song.flac")
        mock_manager.download.return_value = downloaded_path

        fetcher = FlacFetchAudioFetcher()
        raw_result = MagicMock()
        search_result = AudioSearchResult(
            title="Test",
            artist="Artist",
            url="https://example.com/song",
            provider="YouTube",
            raw_result=raw_result,
        )

        # Directory should be created
        assert not new_dir.exists()
        result = fetcher.download(search_result, str(new_dir))
        assert new_dir.exists()


class TestInterruptibleDownload:
    """Tests for the interruptible download functionality."""

    def test_interruptible_download_returns_filepath_on_success(self):
        """Test that _interruptible_download returns filepath when download succeeds."""
        fetcher = FlacFetchAudioFetcher()
        
        mock_manager = MagicMock()
        mock_manager.download.return_value = "/path/to/downloaded.flac"
        mock_selected = MagicMock()
        
        result = fetcher._interruptible_download(
            mock_manager, mock_selected, "/output", "filename"
        )
        
        assert result == "/path/to/downloaded.flac"
        mock_manager.download.assert_called_once_with(
            mock_selected,
            output_path="/output",
            output_filename="filename",
        )

    def test_interruptible_download_propagates_errors(self):
        """Test that _interruptible_download raises DownloadError on failure."""
        fetcher = FlacFetchAudioFetcher()
        
        mock_manager = MagicMock()
        mock_manager.download.side_effect = Exception("Download failed")
        mock_selected = MagicMock()
        
        with pytest.raises(Exception, match="Download failed"):
            fetcher._interruptible_download(
                mock_manager, mock_selected, "/output", "filename"
            )

    def test_interruptible_download_handles_interrupt(self):
        """Test that _interruptible_download handles Ctrl+C gracefully."""
        import signal
        import threading
        import time
        
        fetcher = FlacFetchAudioFetcher()
        
        # Create a mock download that takes a long time
        def slow_download(*args, **kwargs):
            time.sleep(10)  # Simulate slow download
            return "/path/to/file.flac"
        
        mock_manager = MagicMock()
        mock_manager.download.side_effect = slow_download
        mock_selected = MagicMock()
        mock_selected.name = "Test Release"
        
        # Mock the cleanup to avoid needing real transmission
        with patch.object(fetcher, '_cleanup_transmission_torrents'):
            # Start download in a thread and send interrupt
            def send_interrupt():
                time.sleep(0.3)  # Wait for download to start
                # Simulate Ctrl+C by setting the global flag
                import karaoke_gen.audio_fetcher as af
                af._interrupt_requested = True
            
            interrupt_thread = threading.Thread(target=send_interrupt, daemon=True)
            interrupt_thread.start()
            
            with pytest.raises(UserCancelledError, match="cancelled by user"):
                fetcher._interruptible_download(
                    mock_manager, mock_selected, "/output", "filename"
                )

    def test_interruptible_download_calls_cleanup_on_cancel(self):
        """Test that cleanup is called when download is cancelled."""
        import threading
        import time
        
        fetcher = FlacFetchAudioFetcher()
        
        def slow_download(*args, **kwargs):
            time.sleep(10)
            return "/path/to/file.flac"
        
        mock_manager = MagicMock()
        mock_manager.download.side_effect = slow_download
        mock_selected = MagicMock()
        mock_selected.name = "Test Release"
        
        with patch.object(fetcher, '_cleanup_transmission_torrents') as mock_cleanup:
            def send_interrupt():
                time.sleep(0.3)
                import karaoke_gen.audio_fetcher as af
                af._interrupt_requested = True
            
            interrupt_thread = threading.Thread(target=send_interrupt, daemon=True)
            interrupt_thread.start()
            
            with pytest.raises(UserCancelledError):
                fetcher._interruptible_download(
                    mock_manager, mock_selected, "/output", "filename"
                )
            
            # Verify cleanup was called with the selected release
            mock_cleanup.assert_called_once_with(mock_selected)


class TestTransmissionCleanup:
    """Tests for Transmission torrent cleanup functionality."""

    def test_cleanup_removes_matching_incomplete_torrent(self):
        """Test that cleanup removes incomplete torrents matching the release name."""
        fetcher = FlacFetchAudioFetcher()
        
        mock_selected = MagicMock()
        mock_selected.name = "Test Artist - Test Song"
        
        # Create mock torrents
        incomplete_torrent = MagicMock()
        incomplete_torrent.id = 1
        incomplete_torrent.name = "Test Artist - Test Song [FLAC]"
        incomplete_torrent.progress = 50.0
        
        complete_torrent = MagicMock()
        complete_torrent.id = 2
        complete_torrent.name = "Other Album"
        complete_torrent.progress = 100.0
        
        mock_client = MagicMock()
        mock_client.get_torrents.return_value = [incomplete_torrent, complete_torrent]
        
        with patch('transmission_rpc.Client', return_value=mock_client):
            fetcher._cleanup_transmission_torrents(mock_selected)
        
        # Should only remove the matching incomplete torrent
        mock_client.remove_torrent.assert_called_once_with(1, delete_data=True)

    def test_cleanup_does_not_remove_complete_torrents(self):
        """Test that cleanup only removes incomplete torrents."""
        fetcher = FlacFetchAudioFetcher()
        
        mock_selected = MagicMock()
        mock_selected.name = "Test Artist - Test Song"
        
        complete_torrent = MagicMock()
        complete_torrent.id = 1
        complete_torrent.name = "Test Artist - Test Song [FLAC]"
        complete_torrent.progress = 100.0  # Complete
        
        mock_client = MagicMock()
        mock_client.get_torrents.return_value = [complete_torrent]
        
        with patch('transmission_rpc.Client', return_value=mock_client):
            fetcher._cleanup_transmission_torrents(mock_selected)
        
        # Should not remove complete torrents
        mock_client.remove_torrent.assert_not_called()

    def test_cleanup_handles_connection_errors_gracefully(self):
        """Test that cleanup doesn't raise if Transmission connection fails."""
        fetcher = FlacFetchAudioFetcher()
        
        mock_selected = MagicMock()
        mock_selected.name = "Test Song"
        
        with patch('transmission_rpc.Client', side_effect=Exception("Connection refused")):
            # Should not raise - cleanup failures are non-fatal
            fetcher._cleanup_transmission_torrents(mock_selected)

    def test_cleanup_handles_missing_release_name(self):
        """Test that cleanup handles releases without a name attribute."""
        fetcher = FlacFetchAudioFetcher()
        
        mock_selected = MagicMock(spec=[])  # No attributes
        
        mock_client = MagicMock()
        
        with patch('transmission_rpc.Client', return_value=mock_client):
            # Should not raise - just return early
            fetcher._cleanup_transmission_torrents(mock_selected)
        
        # Should not even try to get torrents if no name
        mock_client.get_torrents.assert_not_called()

    def test_cleanup_uses_title_as_fallback_for_name(self):
        """Test that cleanup falls back to 'title' if 'name' not present."""
        fetcher = FlacFetchAudioFetcher()
        
        mock_selected = MagicMock()
        del mock_selected.name  # Remove name attribute
        mock_selected.title = "Test Title"
        
        incomplete_torrent = MagicMock()
        incomplete_torrent.id = 1
        incomplete_torrent.name = "Test Title [FLAC]"
        incomplete_torrent.progress = 25.0
        
        mock_client = MagicMock()
        mock_client.get_torrents.return_value = [incomplete_torrent]
        
        with patch('transmission_rpc.Client', return_value=mock_client):
            fetcher._cleanup_transmission_torrents(mock_selected)
        
        # Should match on title and remove
        mock_client.remove_torrent.assert_called_once_with(1, delete_data=True)

    def test_cleanup_uses_environment_variables(self):
        """Test that cleanup uses TRANSMISSION_HOST/PORT env vars."""
        fetcher = FlacFetchAudioFetcher()
        
        mock_selected = MagicMock()
        mock_selected.name = "Test"
        
        mock_client = MagicMock()
        mock_client.get_torrents.return_value = []
        
        with patch.dict(os.environ, {'TRANSMISSION_HOST': 'remote-host', 'TRANSMISSION_PORT': '9999'}):
            with patch('transmission_rpc.Client', return_value=mock_client) as mock_constructor:
                fetcher._cleanup_transmission_torrents(mock_selected)
        
        mock_constructor.assert_called_once_with(host='remote-host', port=9999, timeout=5)


# ============================================================================
# Tests for RemoteFlacFetchAudioFetcher
# ============================================================================

class TestRemoteFlacFetchAudioFetcherInit:
    """Tests for RemoteFlacFetchAudioFetcher initialization."""

    def test_init_with_required_params(self):
        """Test initialization with required API URL and key."""
        fetcher = RemoteFlacFetchAudioFetcher(
            api_url="http://localhost:8080",
            api_key="test_key",
        )
        assert fetcher.api_url == "http://localhost:8080"
        assert fetcher.api_key == "test_key"
        assert fetcher.timeout == 60
        assert fetcher.download_timeout == 600

    def test_init_strips_trailing_slash_from_url(self):
        """Test that trailing slashes are removed from API URL."""
        fetcher = RemoteFlacFetchAudioFetcher(
            api_url="http://localhost:8080/",
            api_key="test_key",
        )
        assert fetcher.api_url == "http://localhost:8080"

    def test_init_with_custom_timeouts(self):
        """Test initialization with custom timeout values."""
        fetcher = RemoteFlacFetchAudioFetcher(
            api_url="http://localhost:8080",
            api_key="test_key",
            timeout=30,
            download_timeout=300,
        )
        assert fetcher.timeout == 30
        assert fetcher.download_timeout == 300

    def test_init_with_custom_logger(self):
        """Test initialization with custom logger."""
        logger = MagicMock(spec=logging.Logger)
        fetcher = RemoteFlacFetchAudioFetcher(
            api_url="http://localhost:8080",
            api_key="test_key",
            logger=logger,
        )
        assert fetcher.logger == logger


@pytest.mark.skipif(not HTTPX_AVAILABLE, reason="httpx not installed")
class TestRemoteFlacFetcherSearch:
    """Tests for RemoteFlacFetchAudioFetcher search functionality."""

    @pytest.fixture
    def fetcher(self):
        """Create a RemoteFlacFetchAudioFetcher instance."""
        return RemoteFlacFetchAudioFetcher(
            api_url="http://localhost:8080",
            api_key="test_key",
        )

    @patch('httpx.Client')
    def test_search_success(self, mock_client_class, fetcher):
        """Test successful search returns AudioSearchResult objects."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "search_id": "abc123",
            "results": [
                {
                    "title": "Test Song",
                    "artist": "Test Artist",
                    "provider": "YouTube",
                    "download_url": "https://example.com/song",
                    "duration_seconds": 180,
                    "quality_str": "320kbps",
                },
                {
                    "title": "Test Song",
                    "artist": "Test Artist",
                    "provider": "RED",
                    "download_url": "https://example.com/flac",
                    "duration_seconds": 180,
                    "quality_str": "FLAC 16bit",
                    "seeders": 50,
                },
            ],
        }
        mock_response.raise_for_status = MagicMock()
        
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        results = fetcher.search("Test Artist", "Test Song")

        assert len(results) == 2
        assert results[0].title == "Test Song"
        assert results[0].provider == "YouTube"
        assert results[0].index == 0
        assert results[1].provider == "RED"
        assert results[1].index == 1
        assert results[1].seeders == 50

    @patch('httpx.Client')
    def test_search_no_results(self, mock_client_class, fetcher):
        """Test search raises NoResultsError when API returns 404."""
        import httpx
        
        mock_response = MagicMock()
        mock_response.status_code = 404
        
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        with pytest.raises(NoResultsError):
            fetcher.search("Unknown Artist", "Unknown Song")

    @patch('httpx.Client')
    def test_search_empty_results(self, mock_client_class, fetcher):
        """Test search raises NoResultsError when results list is empty."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "search_id": "abc123",
            "results": [],
        }
        mock_response.raise_for_status = MagicMock()
        
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        with pytest.raises(NoResultsError):
            fetcher.search("Artist", "Song")

    @patch('httpx.Client')
    def test_search_stores_search_id(self, mock_client_class, fetcher):
        """Test search stores search_id for subsequent download."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "search_id": "stored_id_123",
            "results": [{"title": "Song", "artist": "Artist", "provider": "YouTube"}],
        }
        mock_response.raise_for_status = MagicMock()
        
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        fetcher.search("Artist", "Song")

        assert fetcher._last_search_id == "stored_id_123"


@pytest.mark.skipif(not HTTPX_AVAILABLE, reason="httpx not installed")
class TestRemoteFlacFetcherDownload:
    """Tests for RemoteFlacFetchAudioFetcher download functionality."""

    @pytest.fixture
    def fetcher(self):
        """Create a RemoteFlacFetchAudioFetcher instance with search completed."""
        f = RemoteFlacFetchAudioFetcher(
            api_url="http://localhost:8080",
            api_key="test_key",
        )
        f._last_search_id = "search123"
        return f

    def test_download_without_search_raises_error(self):
        """Test download raises DownloadError if search wasn't called first."""
        fetcher = RemoteFlacFetchAudioFetcher(
            api_url="http://localhost:8080",
            api_key="test_key",
        )
        search_result = AudioSearchResult(
            title="Song", artist="Artist", url="url", provider="YouTube", index=0,
        )
        
        with pytest.raises(DownloadError, match="No search performed"):
            fetcher.download(search_result, "/tmp")

    @patch.object(RemoteFlacFetchAudioFetcher, '_wait_and_stream_download')
    @patch('httpx.Client')
    def test_download_success(self, mock_client_class, mock_wait_stream, fetcher, tmp_path):
        """Test successful download returns AudioFetchResult."""
        # Mock download initiation
        mock_response = MagicMock()
        mock_response.json.return_value = {"download_id": "dl123"}
        mock_response.raise_for_status = MagicMock()
        
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client
        
        # Mock wait and stream
        downloaded_path = str(tmp_path / "song.flac")
        mock_wait_stream.return_value = downloaded_path
        
        search_result = AudioSearchResult(
            title="Test Song",
            artist="Test Artist",
            url="https://example.com/song",
            provider="RED",
            duration=180,
            quality="FLAC",
            index=0,
        )
        
        result = fetcher.download(search_result, str(tmp_path))
        
        assert result.filepath == downloaded_path
        assert result.artist == "Test Artist"
        assert result.title == "Test Song"
        assert result.provider == "RED"

    @patch.object(RemoteFlacFetchAudioFetcher, '_wait_and_stream_download')
    @patch('httpx.Client')
    def test_download_with_custom_filename(self, mock_client_class, mock_wait_stream, fetcher, tmp_path):
        """Test download uses custom filename when provided."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"download_id": "dl123"}
        mock_response.raise_for_status = MagicMock()
        
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client
        
        mock_wait_stream.return_value = str(tmp_path / "custom.flac")
        
        search_result = AudioSearchResult(
            title="Song", artist="Artist", url="url", provider="YouTube", index=0,
        )
        
        fetcher.download(search_result, str(tmp_path), output_filename="custom_name")
        
        # Verify custom filename was sent to API
        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["output_filename"] == "custom_name"


@pytest.mark.skipif(not HTTPX_AVAILABLE, reason="httpx not installed")
class TestRemoteFlacFetcherSelectBest:
    """Tests for RemoteFlacFetchAudioFetcher select_best functionality."""

    @pytest.fixture
    def fetcher(self):
        """Create a RemoteFlacFetchAudioFetcher instance."""
        return RemoteFlacFetchAudioFetcher(
            api_url="http://localhost:8080",
            api_key="test_key",
        )

    def test_select_best_prefers_flac(self, fetcher):
        """Test select_best prefers FLAC quality over lossy."""
        results = [
            AudioSearchResult(
                title="Song", artist="Artist", url="url1", provider="YouTube",
                quality="320kbps", index=0,
            ),
            AudioSearchResult(
                title="Song", artist="Artist", url="url2", provider="RED",
                quality="FLAC 16bit", index=1,
            ),
        ]
        
        best_index = fetcher.select_best(results)
        assert best_index == 1  # FLAC should win

    def test_select_best_prefers_lossless_keyword(self, fetcher):
        """Test select_best recognizes 'lossless' keyword."""
        results = [
            AudioSearchResult(
                title="Song", artist="Artist", url="url1", provider="YouTube",
                quality="256kbps", index=0,
            ),
            AudioSearchResult(
                title="Song", artist="Artist", url="url2", provider="OPS",
                quality="Lossless CD", index=1,
            ),
        ]
        
        best_index = fetcher.select_best(results)
        assert best_index == 1

    def test_select_best_considers_seeders(self, fetcher):
        """Test select_best considers seeder count."""
        results = [
            AudioSearchResult(
                title="Song", artist="Artist", url="url1", provider="RED",
                quality="FLAC", seeders=5, index=0,
            ),
            AudioSearchResult(
                title="Song", artist="Artist", url="url2", provider="RED",
                quality="FLAC", seeders=50, index=1,
            ),
        ]
        
        best_index = fetcher.select_best(results)
        assert best_index == 1  # More seeders

    def test_select_best_prefers_non_youtube(self, fetcher):
        """Test select_best prefers tracker sources over YouTube."""
        results = [
            AudioSearchResult(
                title="Song", artist="Artist", url="url1", provider="YouTube",
                quality="320kbps", index=0,
            ),
            AudioSearchResult(
                title="Song", artist="Artist", url="url2", provider="RED",
                quality="320kbps", index=1,
            ),
        ]
        
        best_index = fetcher.select_best(results)
        assert best_index == 1  # Non-YouTube preferred

    def test_select_best_empty_list(self, fetcher):
        """Test select_best returns 0 for empty list."""
        assert fetcher.select_best([]) == 0

    def test_select_best_handles_none_quality(self, fetcher):
        """Test select_best handles results with None quality."""
        results = [
            AudioSearchResult(
                title="Song", artist="Artist", url="url", provider="YouTube",
                quality=None, index=0,
            ),
        ]
        
        # Should not raise
        best_index = fetcher.select_best(results)
        assert best_index == 0


@pytest.mark.skipif(not HTTPX_AVAILABLE, reason="httpx not installed")
class TestRemoteFlacFetcherSearchAndDownload:
    """Tests for RemoteFlacFetchAudioFetcher search_and_download functionality."""

    @pytest.fixture
    def fetcher(self):
        """Create a RemoteFlacFetchAudioFetcher instance."""
        return RemoteFlacFetchAudioFetcher(
            api_url="http://localhost:8080",
            api_key="test_key",
        )

    @patch.object(RemoteFlacFetchAudioFetcher, 'download')
    @patch.object(RemoteFlacFetchAudioFetcher, 'search')
    def test_search_and_download_auto_select(self, mock_search, mock_download, fetcher, tmp_path):
        """Test search_and_download with auto_select=True."""
        # Mock search results
        mock_search.return_value = [
            AudioSearchResult(
                title="Song", artist="Artist", url="url1", provider="YouTube",
                quality="320kbps", index=0,
            ),
            AudioSearchResult(
                title="Song", artist="Artist", url="url2", provider="RED",
                quality="FLAC", index=1,
            ),
        ]
        
        # Mock download
        mock_download.return_value = AudioFetchResult(
            filepath="/path/to/song.flac",
            artist="Artist",
            title="Song",
            provider="RED",
        )
        
        result = fetcher.search_and_download(
            artist="Artist",
            title="Song",
            output_dir=str(tmp_path),
            auto_select=True,
        )
        
        # Should auto-select FLAC (index 1)
        mock_download.assert_called_once()
        selected = mock_download.call_args[0][0]
        assert selected.quality == "FLAC"

    @patch.object(RemoteFlacFetchAudioFetcher, 'download')
    @patch.object(RemoteFlacFetchAudioFetcher, '_interactive_select')
    @patch.object(RemoteFlacFetchAudioFetcher, 'search')
    def test_search_and_download_interactive(self, mock_search, mock_interactive, mock_download, fetcher, tmp_path):
        """Test search_and_download with interactive selection."""
        results = [
            AudioSearchResult(
                title="Song", artist="Artist", url="url", provider="YouTube", index=0,
            ),
        ]
        mock_search.return_value = results
        mock_interactive.return_value = results[0]
        mock_download.return_value = AudioFetchResult(
            filepath="/path/to/song.mp3",
            artist="Artist",
            title="Song",
            provider="YouTube",
        )
        
        fetcher.search_and_download(
            artist="Artist",
            title="Song",
            output_dir=str(tmp_path),
            auto_select=False,
        )
        
        mock_interactive.assert_called_once()


@pytest.mark.skipif(not HTTPX_AVAILABLE, reason="httpx not installed")
class TestRemoteFlacFetcherConvertApiResult:
    """Tests for RemoteFlacFetchAudioFetcher._convert_api_result_for_release method."""

    @pytest.fixture
    def fetcher(self):
        """Create a RemoteFlacFetchAudioFetcher instance."""
        return RemoteFlacFetchAudioFetcher(
            api_url="http://localhost:8080",
            api_key="test_key",
        )

    def test_maps_provider_to_source_name(self, fetcher):
        """Test that provider field is mapped to source_name."""
        api_result = {
            "provider": "RED",
            "title": "Test Song",
            "artist": "Test Artist",
            "quality": "FLAC 16bit CD",
            "quality_data": {"format": "FLAC", "bit_depth": 16, "media": "CD"},
        }
        
        converted = fetcher._convert_api_result_for_release(api_result)
        
        assert converted["source_name"] == "RED"
        assert converted["provider"] == "RED"  # Original preserved

    def test_maps_quality_data_to_quality(self, fetcher):
        """Test that quality_data is mapped to quality dict for Release.from_dict()."""
        api_result = {
            "provider": "OPS",
            "title": "Test Song",
            "quality": "FLAC 24bit WEB",
            "quality_data": {"format": "FLAC", "bit_depth": 24, "media": "WEB", "sample_rate": 96000},
        }
        
        converted = fetcher._convert_api_result_for_release(api_result)
        
        assert isinstance(converted["quality"], dict)
        assert converted["quality"]["format"] == "FLAC"
        assert converted["quality"]["bit_depth"] == 24
        assert converted["quality"]["media"] == "WEB"
        assert converted["quality"]["sample_rate"] == 96000

    def test_stores_original_quality_as_quality_str(self, fetcher):
        """Test that original quality string is stored as quality_str."""
        api_result = {
            "provider": "RED",
            "quality": "FLAC 16bit CD",
            "quality_data": {"format": "FLAC", "bit_depth": 16, "media": "CD"},
        }
        
        converted = fetcher._convert_api_result_for_release(api_result)
        
        assert converted["quality_str"] == "FLAC 16bit CD"

    def test_handles_missing_quality_data(self, fetcher):
        """Test fallback when quality_data is missing."""
        api_result = {
            "provider": "YouTube",
            "quality": "MP3 320kbps",
            # No quality_data
        }
        
        converted = fetcher._convert_api_result_for_release(api_result)
        
        assert isinstance(converted["quality"], dict)
        assert converted["quality"]["format"] == "MP3"
        assert converted["quality_str"] == "MP3 320kbps"

    def test_handles_none_quality_data(self, fetcher):
        """Test fallback when quality_data is None."""
        api_result = {
            "provider": "YouTube",
            "quality": "FLAC",
            "quality_data": None,
        }
        
        converted = fetcher._convert_api_result_for_release(api_result)
        
        assert isinstance(converted["quality"], dict)
        assert converted["quality"]["format"] == "FLAC"

    def test_parses_flac_from_quality_string(self, fetcher):
        """Test that FLAC format is parsed from quality string."""
        api_result = {"provider": "Unknown", "quality": "FLAC 16bit"}
        
        converted = fetcher._convert_api_result_for_release(api_result)
        
        assert converted["quality"]["format"] == "FLAC"

    def test_parses_mp3_from_quality_string(self, fetcher):
        """Test that MP3 format is parsed from quality string."""
        api_result = {"provider": "Unknown", "quality": "MP3 320kbps"}
        
        converted = fetcher._convert_api_result_for_release(api_result)
        
        assert converted["quality"]["format"] == "MP3"

    def test_parses_media_from_quality_string(self, fetcher):
        """Test that media type is parsed from quality string."""
        api_result = {"provider": "Unknown", "quality": "FLAC CD"}
        
        converted = fetcher._convert_api_result_for_release(api_result)
        
        assert converted["quality"]["media"] == "CD"

    def test_parses_web_media_from_quality_string(self, fetcher):
        """Test that WEB media type is parsed from quality string."""
        api_result = {"provider": "Unknown", "quality": "FLAC WEB"}
        
        converted = fetcher._convert_api_result_for_release(api_result)
        
        assert converted["quality"]["media"] == "WEB"

    def test_preserves_is_lossless_flag(self, fetcher):
        """Test that is_lossless flag is preserved from API result."""
        api_result = {
            "provider": "RED",
            "quality": "FLAC 16bit CD",
            "quality_data": {"format": "FLAC"},
            "is_lossless": True,
        }
        
        converted = fetcher._convert_api_result_for_release(api_result)
        
        assert converted["is_lossless"] is True

    def test_does_not_modify_original(self, fetcher):
        """Test that original dict is not modified."""
        api_result = {
            "provider": "RED",
            "quality": "FLAC 16bit CD",
        }
        original_quality = api_result["quality"]
        
        converted = fetcher._convert_api_result_for_release(api_result)
        
        # Original should not have source_name added
        assert "source_name" not in api_result
        assert api_result["quality"] == original_quality

    def test_handles_youtube_result(self, fetcher):
        """Test conversion of YouTube-style API result."""
        api_result = {
            "provider": "YouTube",
            "title": "Test Video",
            "artist": "Test Channel",
            "channel": "Test Channel",
            "quality": "MP3 320kbps",
            "quality_data": {"format": "MP3", "bitrate": 320},
            "is_lossless": False,
            "view_count": 1000000,
            "duration_seconds": 180,
        }
        
        converted = fetcher._convert_api_result_for_release(api_result)
        
        assert converted["source_name"] == "YouTube"
        assert converted["quality"]["format"] == "MP3"
        assert converted["quality"]["bitrate"] == 320
        assert converted["channel"] == "Test Channel"
        assert converted["view_count"] == 1000000


@pytest.mark.skipif(not HTTPX_AVAILABLE, reason="httpx not installed")
class TestRemoteFlacFetcherInteractiveSelect:
    """Tests for RemoteFlacFetchAudioFetcher interactive selection."""

    @pytest.fixture
    def fetcher(self):
        """Create a RemoteFlacFetchAudioFetcher instance."""
        return RemoteFlacFetchAudioFetcher(
            api_url="http://localhost:8080",
            api_key="test_key",
        )

    @patch('builtins.input', return_value='1')
    def test_basic_interactive_select_success(self, mock_input, fetcher, capsys):
        """Test basic interactive selection returns selected result."""
        results = [
            AudioSearchResult(
                title="Song 1", artist="Artist", url="url1", provider="YouTube",
                quality="320kbps", index=0,
            ),
            AudioSearchResult(
                title="Song 2", artist="Artist", url="url2", provider="RED",
                quality="FLAC", index=1,
            ),
        ]
        
        selected = fetcher._basic_interactive_select(results, "Artist", "Song")
        
        assert selected == results[0]
        
        # Check output shows options
        captured = capsys.readouterr()
        assert "Found 2 releases" in captured.out

    @patch('builtins.input', return_value='0')
    def test_basic_interactive_select_cancel(self, mock_input, fetcher):
        """Test interactive selection raises UserCancelledError on cancel."""
        results = [
            AudioSearchResult(
                title="Song", artist="Artist", url="url", provider="YouTube", index=0,
            ),
        ]
        
        with pytest.raises(UserCancelledError):
            fetcher._basic_interactive_select(results, "Artist", "Song")

    @patch('builtins.input', side_effect=KeyboardInterrupt)
    def test_basic_interactive_select_keyboard_interrupt(self, mock_input, fetcher):
        """Test interactive selection handles Ctrl+C."""
        results = [
            AudioSearchResult(
                title="Song", artist="Artist", url="url", provider="YouTube", index=0,
            ),
        ]
        
        with pytest.raises(UserCancelledError):
            fetcher._basic_interactive_select(results, "Artist", "Song")


class TestRemoteFlacFetcherAlias:
    """Tests for RemoteFlacFetcher alias."""
    
    def test_remote_flacfetcher_alias_exists(self):
        """Test RemoteFlacFetcher alias is available."""
        from karaoke_gen.audio_fetcher import RemoteFlacFetcher
        assert RemoteFlacFetcher is not None

    def test_remote_flacfetcher_is_remoteflacfetchaudiofetcher(self):
        """Test RemoteFlacFetcher is the same as RemoteFlacFetchAudioFetcher."""
        from karaoke_gen.audio_fetcher import RemoteFlacFetcher
        assert RemoteFlacFetcher is RemoteFlacFetchAudioFetcher


class TestCreateAudioFetcherRemoteSelection:
    """Tests for create_audio_fetcher factory selecting remote vs local."""

    @patch.dict(os.environ, {"FLACFETCH_API_URL": "http://localhost:8080", "FLACFETCH_API_KEY": "test_key"})
    def test_creates_remote_fetcher_from_env(self):
        """Test factory creates RemoteFlacFetchAudioFetcher when env vars are set."""
        fetcher = create_audio_fetcher()
        assert isinstance(fetcher, RemoteFlacFetchAudioFetcher)
        assert fetcher.api_url == "http://localhost:8080"
        assert fetcher.api_key == "test_key"

    def test_creates_remote_fetcher_from_args(self):
        """Test factory creates RemoteFlacFetchAudioFetcher when args are passed."""
        fetcher = create_audio_fetcher(
            flacfetch_api_url="http://custom:8080",
            flacfetch_api_key="custom_key",
        )
        assert isinstance(fetcher, RemoteFlacFetchAudioFetcher)
        assert fetcher.api_url == "http://custom:8080"
        assert fetcher.api_key == "custom_key"

    @patch.dict(os.environ, {"FLACFETCH_API_URL": "http://localhost:8080"}, clear=False)
    def test_half_configured_url_only_raises(self):
        """Half-configured remote (URL only) must fail loudly, not silently use local."""
        # Clear FLACFETCH_API_KEY if it exists
        with patch.dict(os.environ, {"FLACFETCH_API_KEY": ""}, clear=False):
            with pytest.raises(AudioFetcherError, match="FLACFETCH_API_KEY is not"):
                create_audio_fetcher()

    @patch.dict(os.environ, {"FLACFETCH_API_KEY": "test_key"}, clear=False)
    def test_half_configured_key_only_raises(self):
        """Half-configured remote (key only) must fail loudly, not silently use local."""
        # Clear FLACFETCH_API_URL if it exists
        with patch.dict(os.environ, {"FLACFETCH_API_URL": ""}, clear=False):
            with pytest.raises(AudioFetcherError, match="FLACFETCH_API_URL is not"):
                create_audio_fetcher()

    @patch.dict(os.environ, {}, clear=True)
    def test_creates_local_fetcher_by_default(self):
        """Test factory creates local fetcher when no remote config."""
        fetcher = create_audio_fetcher()
        assert isinstance(fetcher, FlacFetchAudioFetcher)

    @patch.dict(os.environ, {"FLACFETCH_API_URL": "http://localhost:8080", "FLACFETCH_API_KEY": "test_key"})
    def test_remote_fetcher_gets_logger(self):
        """Test factory passes logger to remote fetcher."""
        logger = MagicMock(spec=logging.Logger)
        fetcher = create_audio_fetcher(logger=logger)
        assert isinstance(fetcher, RemoteFlacFetchAudioFetcher)
        assert fetcher.logger == logger

    def test_args_override_env_vars(self):
        """Test explicit args override environment variables."""
        with patch.dict(os.environ, {"FLACFETCH_API_URL": "http://env:8080", "FLACFETCH_API_KEY": "env_key"}):
            fetcher = create_audio_fetcher(
                flacfetch_api_url="http://arg:9090",
                flacfetch_api_key="arg_key",
            )
            assert isinstance(fetcher, RemoteFlacFetchAudioFetcher)
            assert fetcher.api_url == "http://arg:9090"
            assert fetcher.api_key == "arg_key"


class TestFlacFetchAudioFetcherDownloadFromUrl:
    """Tests for FlacFetchAudioFetcher.download_from_url() method."""

    @pytest.fixture
    def mock_logger(self):
        """Create a mock logger."""
        return MagicMock(spec=logging.Logger)

    @pytest.fixture
    def fetcher(self, mock_logger):
        """Create a FlacFetchAudioFetcher instance with mocked dependencies."""
        return FlacFetchAudioFetcher(logger=mock_logger)

    @patch('karaoke_gen.audio_fetcher.FlacFetchAudioFetcher._get_manager')
    def test_download_from_url_youtube_success(self, mock_get_manager, fetcher, tmp_path):
        """Test successful download from YouTube URL."""
        mock_manager = MagicMock()
        downloaded_path = str(tmp_path / "song.webm")
        mock_manager.download_by_id.return_value = downloaded_path
        mock_get_manager.return_value = mock_manager

        result = fetcher.download_from_url(
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            output_dir=str(tmp_path),
            output_filename="Rick Astley - Never Gonna Give You Up",
            artist="Rick Astley",
            title="Never Gonna Give You Up",
        )

        assert result.filepath == downloaded_path
        assert result.artist == "Rick Astley"
        assert result.title == "Never Gonna Give You Up"
        assert result.provider == "YouTube"
        mock_manager.download_by_id.assert_called_once_with(
            source_name="YouTube",
            source_id="dQw4w9WgXcQ",
            output_path=str(tmp_path),
            output_filename="Rick Astley - Never Gonna Give You Up",
            download_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )

    @patch('karaoke_gen.audio_fetcher.FlacFetchAudioFetcher._get_manager')
    def test_download_from_url_youtu_be_short_url(self, mock_get_manager, fetcher, tmp_path):
        """Test download from youtu.be short URL."""
        mock_manager = MagicMock()
        downloaded_path = str(tmp_path / "song.webm")
        mock_manager.download_by_id.return_value = downloaded_path
        mock_get_manager.return_value = mock_manager

        result = fetcher.download_from_url(
            url="https://youtu.be/dQw4w9WgXcQ",
            output_dir=str(tmp_path),
        )

        assert result.filepath == downloaded_path
        assert result.provider == "YouTube"
        # Should extract video ID from short URL
        mock_manager.download_by_id.assert_called_once()
        call_args = mock_manager.download_by_id.call_args
        assert call_args.kwargs["source_id"] == "dQw4w9WgXcQ"

    @patch('karaoke_gen.audio_fetcher.FlacFetchAudioFetcher._get_manager')
    def test_download_from_url_embed_url(self, mock_get_manager, fetcher, tmp_path):
        """Test download from YouTube embed URL."""
        mock_manager = MagicMock()
        downloaded_path = str(tmp_path / "song.webm")
        mock_manager.download_by_id.return_value = downloaded_path
        mock_get_manager.return_value = mock_manager

        fetcher.download_from_url(
            url="https://www.youtube.com/embed/dQw4w9WgXcQ",
            output_dir=str(tmp_path),
        )

        call_args = mock_manager.download_by_id.call_args
        assert call_args.kwargs["source_id"] == "dQw4w9WgXcQ"

    @patch('karaoke_gen.audio_fetcher.FlacFetchAudioFetcher._get_manager')
    def test_download_from_url_generates_filename_from_artist_title(self, mock_get_manager, fetcher, tmp_path):
        """Test that filename is generated from artist and title if not provided."""
        mock_manager = MagicMock()
        downloaded_path = str(tmp_path / "song.webm")
        mock_manager.download_by_id.return_value = downloaded_path
        mock_get_manager.return_value = mock_manager

        fetcher.download_from_url(
            url="https://www.youtube.com/watch?v=test123test",
            output_dir=str(tmp_path),
            artist="Test Artist",
            title="Test Song",
        )

        call_args = mock_manager.download_by_id.call_args
        assert call_args.kwargs["output_filename"] == "Test Artist - Test Song"

    @patch('karaoke_gen.audio_fetcher.FlacFetchAudioFetcher._get_manager')
    def test_download_from_url_generates_filename_from_video_id(self, mock_get_manager, fetcher, tmp_path):
        """Test that filename falls back to video ID if no artist/title."""
        mock_manager = MagicMock()
        downloaded_path = str(tmp_path / "song.webm")
        mock_manager.download_by_id.return_value = downloaded_path
        mock_get_manager.return_value = mock_manager

        fetcher.download_from_url(
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            output_dir=str(tmp_path),
        )

        call_args = mock_manager.download_by_id.call_args
        assert call_args.kwargs["output_filename"] == "dQw4w9WgXcQ"

    @patch('karaoke_gen.audio_fetcher.FlacFetchAudioFetcher._get_manager')
    def test_download_from_url_raises_download_error_on_failure(self, mock_get_manager, fetcher, tmp_path):
        """Test that DownloadError is raised when download fails."""
        mock_manager = MagicMock()
        mock_manager.download_by_id.side_effect = Exception("Download failed")
        mock_get_manager.return_value = mock_manager

        with pytest.raises(DownloadError) as exc_info:
            fetcher.download_from_url(
                url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                output_dir=str(tmp_path),
            )

        assert "Failed to download from URL" in str(exc_info.value)

    @patch('karaoke_gen.audio_fetcher.FlacFetchAudioFetcher._get_manager')
    def test_download_from_url_raises_on_empty_filepath(self, mock_get_manager, fetcher, tmp_path):
        """Test that DownloadError is raised when download returns no filepath."""
        mock_manager = MagicMock()
        mock_manager.download_by_id.return_value = None
        mock_get_manager.return_value = mock_manager

        with pytest.raises(DownloadError) as exc_info:
            fetcher.download_from_url(
                url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                output_dir=str(tmp_path),
            )

        assert "Download returned no file path" in str(exc_info.value)

    @patch('karaoke_gen.audio_fetcher.FlacFetchAudioFetcher._get_manager')
    def test_download_from_url_creates_output_dir(self, mock_get_manager, fetcher, tmp_path):
        """Test that output directory is created if it doesn't exist."""
        mock_manager = MagicMock()
        mock_manager.download_by_id.return_value = str(tmp_path / "song.webm")
        mock_get_manager.return_value = mock_manager

        new_dir = tmp_path / "new_subdir"
        assert not new_dir.exists()

        fetcher.download_from_url(
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            output_dir=str(new_dir),
        )

        assert new_dir.exists()


class TestRemoteFlacFetchAudioFetcherDownloadFromUrl:
    """Tests for RemoteFlacFetchAudioFetcher.download_from_url() method."""

    @pytest.fixture
    def mock_logger(self):
        """Create a mock logger."""
        return MagicMock(spec=logging.Logger)

    @pytest.fixture
    def fetcher(self, mock_logger):
        """Create a RemoteFlacFetchAudioFetcher instance."""
        return RemoteFlacFetchAudioFetcher(
            api_url="http://localhost:8080",
            api_key="test_key",
            logger=mock_logger,
        )

    @patch('flacfetch.core.manager.FetchManager')
    @patch('flacfetch.providers.youtube.YoutubeProvider')
    @patch('flacfetch.downloaders.youtube.YoutubeDownloader')
    def test_download_from_url_uses_local_flacfetch(
        self, mock_yt_downloader, _mock_yt_provider, mock_fetch_manager, fetcher, tmp_path
    ):
        """Test that download_from_url uses local flacfetch (not remote API)."""
        mock_manager_instance = MagicMock()
        downloaded_path = str(tmp_path / "song.webm")
        mock_manager_instance.download_by_id.return_value = downloaded_path
        mock_fetch_manager.return_value = mock_manager_instance

        result = fetcher.download_from_url(
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            output_dir=str(tmp_path),
            artist="Rick Astley",
            title="Never Gonna Give You Up",
        )

        # Verify local flacfetch was used
        mock_fetch_manager.assert_called_once()
        mock_manager_instance.add_provider.assert_called_once()
        mock_manager_instance.register_downloader.assert_called_once_with("YouTube", mock_yt_downloader.return_value)
        mock_manager_instance.download_by_id.assert_called_once()

        assert result.filepath == downloaded_path
        assert result.artist == "Rick Astley"
        assert result.title == "Never Gonna Give You Up"
        assert result.provider == "YouTube"

    @patch('flacfetch.core.manager.FetchManager')
    @patch('flacfetch.providers.youtube.YoutubeProvider')
    @patch('flacfetch.downloaders.youtube.YoutubeDownloader')
    def test_download_from_url_extracts_video_id(
        self, _mock_yt_downloader, _mock_yt_provider, mock_fetch_manager, fetcher, tmp_path
    ):
        """Test that video ID is extracted from URL correctly."""
        mock_manager_instance = MagicMock()
        mock_manager_instance.download_by_id.return_value = str(tmp_path / "song.webm")
        mock_fetch_manager.return_value = mock_manager_instance

        fetcher.download_from_url(
            url="https://www.youtube.com/watch?v=abc123xyz99",
            output_dir=str(tmp_path),
        )

        call_args = mock_manager_instance.download_by_id.call_args
        assert call_args.kwargs["source_id"] == "abc123xyz99"

    @patch('flacfetch.core.manager.FetchManager')
    @patch('flacfetch.providers.youtube.YoutubeProvider')
    @patch('flacfetch.downloaders.youtube.YoutubeDownloader')
    def test_download_from_url_raises_on_failure(
        self, _mock_yt_downloader, _mock_yt_provider, mock_fetch_manager, fetcher, tmp_path
    ):
        """Test that DownloadError is raised on failure."""
        mock_manager_instance = MagicMock()
        mock_manager_instance.download_by_id.side_effect = Exception("Network error")
        mock_fetch_manager.return_value = mock_manager_instance

        with pytest.raises(DownloadError) as exc_info:
            fetcher.download_from_url(
                url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                output_dir=str(tmp_path),
            )

        assert "Failed to download from URL" in str(exc_info.value)

    def test_download_from_url_method_exists(self, fetcher):
        """Test that download_from_url method exists on RemoteFlacFetchAudioFetcher."""
        assert hasattr(fetcher, 'download_from_url')
        assert callable(fetcher.download_from_url)
