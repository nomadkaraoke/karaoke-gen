"""
Tests for audio search functionality (Batch 5).

Tests the audio search service, API routes, and job model fields.
"""
import pytest
import os
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from datetime import datetime

from backend.models.job import Job, JobCreate, JobStatus, STATE_TRANSITIONS
from backend.services.audio_search_service import (
    AudioSearchService,
    AudioSearchResult,
    AudioDownloadResult,
    AudioSearchError,
    NoResultsError,
    DownloadError,
    get_audio_search_service,
)


class TestJobModelAudioSearchFields:
    """Test Job model has audio search fields."""
    
    def test_job_has_audio_search_fields(self):
        """Test Job model has audio search configuration fields."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            artist="ABBA",
            title="Waterloo",
            audio_search_artist="ABBA",
            audio_search_title="Waterloo",
            auto_download=True,
        )
        
        assert job.audio_search_artist == "ABBA"
        assert job.audio_search_title == "Waterloo"
        assert job.auto_download is True
    
    def test_job_audio_search_fields_default_values(self):
        """Test default values for audio search fields."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        
        assert job.audio_search_artist is None
        assert job.audio_search_title is None
        assert job.auto_download is False
    
    def test_job_create_has_audio_search_fields(self):
        """Test JobCreate model has audio search fields."""
        job_create = JobCreate(
            artist="ABBA",
            title="Waterloo",
            audio_search_artist="ABBA",
            audio_search_title="Waterloo",
            auto_download=True,
        )
        
        assert job_create.audio_search_artist == "ABBA"
        assert job_create.audio_search_title == "Waterloo"
        assert job_create.auto_download is True


class TestYouTubeFieldMapping:
    """Test YouTube description field mapping between API and workers.
    
    CRITICAL: This test class exists because we had a bug where:
    - The audio_search API endpoint set `youtube_description`
    - But video_worker.py reads `youtube_description_template`
    
    These tests ensure both fields are properly set so YouTube uploads work.
    """
    
    def test_job_has_both_youtube_description_fields(self):
        """Test Job model has both youtube_description and youtube_description_template."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            artist="Test",
            title="Song",
            enable_youtube_upload=True,
            youtube_description="This is a karaoke video",
            youtube_description_template="This is a karaoke video",
        )
        
        # Both fields should exist and be set
        assert job.youtube_description == "This is a karaoke video"
        assert job.youtube_description_template == "This is a karaoke video"
        assert job.enable_youtube_upload is True
    
    def test_job_create_has_youtube_description_template(self):
        """Test JobCreate model has youtube_description_template field."""
        job_create = JobCreate(
            artist="Test",
            title="Song",
            enable_youtube_upload=True,
            youtube_description="This is a karaoke video",
            youtube_description_template="This is a karaoke video",
        )
        
        assert job_create.youtube_description_template == "This is a karaoke video"
    
    def test_video_worker_reads_youtube_description_template(self):
        """Document what field video_worker expects.
        
        This is a documentation test - if video_worker changes what field
        it reads, this test should be updated to match.
        """
        # video_worker.py uses this pattern:
        # if youtube_credentials and getattr(job, 'youtube_description_template', None):
        #     youtube_desc_path = os.path.join(temp_dir, "youtube_description.txt")
        #     with open(youtube_desc_path, 'w') as f:
        #         f.write(job.youtube_description_template)
        #
        # So the job MUST have youtube_description_template set for YouTube upload to work
        
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            artist="Test",
            title="Song",
            enable_youtube_upload=True,
            youtube_description_template="Template text",
        )
        
        # Simulate what video_worker does
        template = getattr(job, 'youtube_description_template', None)
        assert template is not None, "youtube_description_template must be set for YouTube upload"
        assert template == "Template text"


class TestJobStatusAudioSearchStates:
    """Test Job status includes audio search states."""
    
    def test_audio_search_statuses_exist(self):
        """Test audio search status values exist."""
        assert hasattr(JobStatus, 'SEARCHING_AUDIO')
        assert hasattr(JobStatus, 'AWAITING_AUDIO_SELECTION')
        assert hasattr(JobStatus, 'DOWNLOADING_AUDIO')
        
        assert JobStatus.SEARCHING_AUDIO == "searching_audio"
        assert JobStatus.AWAITING_AUDIO_SELECTION == "awaiting_audio_selection"
        assert JobStatus.DOWNLOADING_AUDIO == "downloading_audio"
    
    def test_state_transitions_include_audio_search_flow(self):
        """Test state transitions include audio search flow."""
        # PENDING can go to SEARCHING_AUDIO
        assert JobStatus.SEARCHING_AUDIO in STATE_TRANSITIONS[JobStatus.PENDING]
        
        # SEARCHING_AUDIO can go to AWAITING_AUDIO_SELECTION or DOWNLOADING_AUDIO
        assert JobStatus.AWAITING_AUDIO_SELECTION in STATE_TRANSITIONS[JobStatus.SEARCHING_AUDIO]
        assert JobStatus.DOWNLOADING_AUDIO in STATE_TRANSITIONS[JobStatus.SEARCHING_AUDIO]
        
        # AWAITING_AUDIO_SELECTION can go to DOWNLOADING_AUDIO
        assert JobStatus.DOWNLOADING_AUDIO in STATE_TRANSITIONS[JobStatus.AWAITING_AUDIO_SELECTION]
        
        # DOWNLOADING_AUDIO can go to DOWNLOADING
        assert JobStatus.DOWNLOADING in STATE_TRANSITIONS[JobStatus.DOWNLOADING_AUDIO]


class TestAudioSearchResult:
    """Test AudioSearchResult dataclass.
    
    AudioSearchResult is now imported from karaoke_gen.audio_fetcher.
    These tests verify the backend can use it correctly.
    """
    
    def test_create_audio_search_result(self):
        """Test creating an AudioSearchResult."""
        result = AudioSearchResult(
            title="Waterloo",
            artist="ABBA",
            provider="YouTube",
            url="https://youtube.com/watch?v=abc123",
            duration=180,
            quality="FLAC",
            source_id="abc123",
            index=0,
        )
        
        assert result.title == "Waterloo"
        assert result.artist == "ABBA"
        assert result.provider == "YouTube"
        assert result.url == "https://youtube.com/watch?v=abc123"
        assert result.duration == 180
        assert result.quality == "FLAC"
        assert result.source_id == "abc123"
        assert result.index == 0
    
    def test_to_dict(self):
        """Test converting to dict for serialization."""
        result = AudioSearchResult(
            title="Waterloo",
            artist="ABBA",
            provider="YouTube",
            url="https://youtube.com/watch?v=abc123",
            index=0,
        )
        
        data = result.to_dict()
        
        assert data['title'] == "Waterloo"
        assert data['artist'] == "ABBA"
        assert data['provider'] == "YouTube"
        assert data['url'] == "https://youtube.com/watch?v=abc123"
        assert data['index'] == 0
        # raw_result should NOT be in serialized dict
        assert 'raw_result' not in data
    
    def test_from_dict(self):
        """Test creating from dict."""
        data = {
            'title': "Waterloo",
            'artist': "ABBA",
            'provider': "YouTube",
            'url': "https://youtube.com/watch?v=abc123",
            'duration': 180,
            'quality': "FLAC",
            'source_id': "abc123",
            'index': 0,
        }
        
        result = AudioSearchResult.from_dict(data)
        
        assert result.title == "Waterloo"
        assert result.artist == "ABBA"
        assert result.provider == "YouTube"
        assert result.index == 0
        assert result.raw_result is None  # Not set from dict


class TestAudioSearchServiceInit:
    """Test AudioSearchService initialization."""
    
    @patch.dict('os.environ', {'RED_API_KEY': '', 'RED_API_URL': '', 'OPS_API_KEY': '', 'OPS_API_URL': ''}, clear=False)
    def test_init_with_no_keys(self):
        """Test initialization without API keys."""
        # Pass explicit None to override environment
        service = AudioSearchService(red_api_key=None, red_api_url=None, ops_api_key=None, ops_api_url=None)
        
        # Service should have a FlacFetcher internally
        assert service._fetcher is not None
        assert service._cached_results == []
    
    def test_init_with_keys(self):
        """Test initialization with API keys and URLs."""
        service = AudioSearchService(
            red_api_key="test_red_key",
            red_api_url="https://red.url",
            ops_api_key="test_ops_key",
            ops_api_url="https://ops.url",
        )
        
        # Keys and URLs are passed to FlacFetcher
        assert service._fetcher._red_api_key == "test_red_key"
        assert service._fetcher._red_api_url == "https://red.url"
        assert service._fetcher._ops_api_key == "test_ops_key"
        assert service._fetcher._ops_api_url == "https://ops.url"
    
    def test_init_reads_from_environment(self):
        """Test initialization reads keys from environment variables."""
        # Just test that the service can be initialized
        service = AudioSearchService()
        
        # The service should have a FlacFetcher
        assert service._fetcher is not None


class TestAudioSearchServiceSearch:
    """Test AudioSearchService.search() method."""
    
    def test_search_returns_results(self):
        """Test search returns AudioSearchResult list."""
        # Create mock result
        mock_result = AudioSearchResult(
            title="Waterloo",
            artist="ABBA",
            provider="YouTube",
            url="https://youtube.com/watch?v=abc123",
            duration=180,
            quality="FLAC",
            source_id="abc123",
            index=0,
        )
        
        service = AudioSearchService(red_api_key=None, red_api_url=None, ops_api_key=None, ops_api_url=None)
        # IMPORTANT: Clear remote client to force local mode, otherwise real API calls are made
        service._remote_client = None
        service._fetcher = Mock()
        service._fetcher.search.return_value = [mock_result]
        
        results = service.search("ABBA", "Waterloo")
        
        assert len(results) == 1
        assert results[0].title == "Waterloo"
        assert results[0].artist == "ABBA"
        assert results[0].provider == "YouTube"
        assert results[0].index == 0
    
    def test_search_no_results_raises_error(self):
        """Test search raises NoResultsError when no results."""
        service = AudioSearchService(red_api_key=None, red_api_url=None, ops_api_key=None, ops_api_url=None)
        service._remote_client = None  # Force local mode to use mocked fetcher
        service._fetcher = Mock()
        service._fetcher.search.side_effect = NoResultsError("No results found")
        
        with pytest.raises(NoResultsError) as exc_info:
            service.search("Unknown Artist", "Unknown Song")
        
        assert "No results found" in str(exc_info.value)
    
    def test_search_multiple_results(self):
        """Test search returns multiple results with correct indices."""
        mock_results = []
        for i in range(3):
            mock_results.append(AudioSearchResult(
                title=f"Song {i}",
                artist="Artist",
                provider=["YouTube", "RED", "OPS"][i],
                url=f"https://example.com/{i}",
                duration=180 + i * 10,
                quality=["320kbps", "FLAC", "FLAC"][i],
                source_id=str(i),
                index=i,
            ))
        
        service = AudioSearchService(red_api_key=None, red_api_url=None, ops_api_key=None, ops_api_url=None)
        service._remote_client = None  # Force local mode to use mocked fetcher
        service._fetcher = Mock()
        service._fetcher.search.return_value = mock_results
        
        results = service.search("Artist", "Song")
        
        assert len(results) == 3
        assert results[0].index == 0
        assert results[1].index == 1
        assert results[2].index == 2


class TestAudioSearchServiceSelectBest:
    """Test AudioSearchService.select_best() method."""
    
    def test_select_best_delegates_to_fetcher(self):
        """Test select_best uses FlacFetcher's select_best."""
        mock_results = [
            AudioSearchResult(
                title="Waterloo",
                artist="ABBA",
                provider="YouTube",
                url="https://youtube.com/watch?v=abc123",
                index=0,
            ),
            AudioSearchResult(
                title="Waterloo",
                artist="ABBA",
                provider="RED",
                url="https://example.com/456",
                index=1,
            ),
        ]
        
        service = AudioSearchService(red_api_key=None, red_api_url=None, ops_api_key=None, ops_api_url=None)
        service._fetcher = Mock()
        service._fetcher.select_best.return_value = 1
        
        best_index = service.select_best(mock_results)
        
        assert best_index == 1
        service._fetcher.select_best.assert_called_once_with(mock_results)
    
    def test_select_best_empty_list_returns_zero(self):
        """Test select_best returns 0 for empty list."""
        service = AudioSearchService(red_api_key=None, red_api_url=None, ops_api_key=None, ops_api_url=None)
        service._fetcher = Mock()
        service._fetcher.select_best.return_value = 0
        
        best_index = service.select_best([])
        
        assert best_index == 0


class TestAudioSearchServiceDownload:
    """Test AudioSearchService.download() method."""
    
    def test_download_without_search_raises_error(self):
        """Test download without prior search raises error."""
        service = AudioSearchService(red_api_key=None, red_api_url=None, ops_api_key=None, ops_api_url=None)
        
        with pytest.raises(DownloadError) as exc_info:
            service.download(0, "/tmp")
        
        assert "No cached result" in str(exc_info.value)
    
    def test_download_after_search(self):
        """Test download after search works."""
        import tempfile
        
        # Create a temp file to simulate downloaded file
        temp_dir = tempfile.mkdtemp()
        temp_file = os.path.join(temp_dir, "test.flac")
        with open(temp_file, 'w') as f:
            f.write("test")
        
        mock_result = AudioSearchResult(
            title="Waterloo",
            artist="ABBA",
            provider="YouTube",
            url="https://youtube.com/watch?v=abc123",
            duration=180,
            quality="FLAC",
            source_id="abc123",
            index=0,
        )
        
        mock_fetch_result = AudioDownloadResult(
            filepath=temp_file,
            artist="ABBA",
            title="Waterloo",
            provider="YouTube",
            duration=180,
            quality="FLAC",
        )
        
        service = AudioSearchService(red_api_key=None, red_api_url=None, ops_api_key=None, ops_api_url=None)
        service._remote_client = None  # Force local mode to use mocked fetcher
        service._fetcher = Mock()
        service._fetcher.search.return_value = [mock_result]
        service._fetcher.download.return_value = mock_fetch_result
        
        service.search("ABBA", "Waterloo")
        result = service.download(0, temp_dir)
        
        assert result.filepath == temp_file
        assert result.artist == "ABBA"
        assert result.title == "Waterloo"
        assert result.provider == "YouTube"
        
        # Cleanup
        os.remove(temp_file)
        os.rmdir(temp_dir)
    
    def test_download_invalid_index_raises_error(self):
        """Test download with invalid index raises error."""
        mock_result = AudioSearchResult(
            title="Waterloo",
            artist="ABBA",
            provider="YouTube",
            url="https://youtube.com/watch?v=abc123",
            index=0,
        )

        service = AudioSearchService(red_api_key=None, red_api_url=None, ops_api_key=None, ops_api_url=None)
        service._fetcher = Mock()
        service._fetcher.search.return_value = [mock_result]
        # Ensure no remote client so we test the local cache path
        service._remote_client = None

        service.search("ABBA", "Waterloo")

        with pytest.raises(DownloadError) as exc_info:
            service.download(99, "/tmp")

        assert "No cached result for index 99" in str(exc_info.value)


class TestGetAudioSearchService:
    """Test get_audio_search_service singleton."""
    
    def test_get_audio_search_service_returns_instance(self):
        """Test get_audio_search_service returns an instance."""
        # Reset singleton
        import backend.services.audio_search_service as module
        module._audio_search_service = None
        
        service = get_audio_search_service()
        
        assert service is not None
        assert isinstance(service, AudioSearchService)
    
    def test_get_audio_search_service_singleton(self):
        """Test get_audio_search_service returns same instance."""
        service1 = get_audio_search_service()
        service2 = get_audio_search_service()
        
        assert service1 is service2


class TestRemoteDownloadPath:
    """Test remote download functionality when flacfetch service is configured.
    
    These tests verify the code path that uses the remote flacfetch service
    for torrent downloads (RED/OPS providers). This would have caught
    the 'extra_info' attribute error.
    
    The tests directly set up the service state without calling search() to avoid
    the complexity of mocking async remote search operations.
    """
    
    def test_download_uses_remote_for_red_provider(self):
        """Test download routes to remote service for RED provider.
        
        This test would have caught: 'AudioSearchResult' object has no attribute 'extra_info'
        """
        # Create a RED result (torrent source)
        mock_result = AudioSearchResult(
            title="Waterloo",
            artist="ABBA",
            provider="RED",  # Torrent provider - should use remote
            url="",  # No URL for torrent sources
            quality="FLAC 16bit CD",
            seeders=50,
            target_file="Waterloo.flac",
            index=0,
        )
        
        # Create service with mocked remote client
        service = AudioSearchService(red_api_key=None, red_api_url=None, ops_api_key=None, ops_api_url=None)
        service._fetcher = Mock()
        
        # Directly set cached results (simulating after search)
        service._cached_results = [mock_result]
        service._remote_search_id = "remote_search_123"  # Remote search was performed
        
        # Mock the remote client
        mock_remote_client = Mock()
        service._remote_client = mock_remote_client
        
        # Mock the remote download method
        mock_download_result = AudioDownloadResult(
            filepath="gs://bucket/uploads/job123/audio/Waterloo.flac",
            artist="ABBA",
            title="Waterloo",
            provider="RED",
            quality="FLAC 16bit CD",
        )
        service._download_remote = Mock(return_value=mock_download_result)
        
        # Call download - should route to remote
        result = service.download(0, "/tmp", gcs_path="uploads/job123/audio/")

        # Verify remote download was called (includes search_id as last parameter)
        service._download_remote.assert_called_once_with(0, "/tmp", None, "uploads/job123/audio/", "remote_search_123")
        assert result.filepath == "gs://bucket/uploads/job123/audio/Waterloo.flac"
    
    def test_download_uses_remote_for_ops_provider(self):
        """Test download routes to remote service for OPS provider."""
        mock_result = AudioSearchResult(
            title="Waterloo",
            artist="ABBA",
            provider="OPS",  # Torrent provider
            url="",
            quality="FLAC 16bit CD",
            seeders=30,
            index=0,
        )
        
        service = AudioSearchService(red_api_key=None, red_api_url=None, ops_api_key=None, ops_api_url=None)
        service._fetcher = Mock()
        
        # Directly set cached results
        service._cached_results = [mock_result]
        service._remote_search_id = "remote_search_456"
        
        # Mock remote client
        mock_remote_client = Mock()
        service._remote_client = mock_remote_client
        
        # Mock remote download
        mock_download_result = AudioDownloadResult(
            filepath="gs://bucket/test.flac",
            artist="ABBA",
            title="Waterloo",
            provider="OPS",
            quality="FLAC",
        )
        service._download_remote = Mock(return_value=mock_download_result)
        
        # Call download
        result = service.download(0, "/tmp")
        
        # Should use remote for OPS
        service._download_remote.assert_called_once()
    
    def test_download_uses_local_for_youtube_even_with_remote_client(self):
        """Test YouTube downloads use local even when remote client is configured."""
        mock_result = AudioSearchResult(
            title="Waterloo",
            artist="ABBA",
            provider="YouTube",  # NOT a torrent provider
            url="https://youtube.com/watch?v=abc123",
            quality="Opus 128kbps",
            index=0,
        )
        
        service = AudioSearchService(red_api_key=None, red_api_url=None, ops_api_key=None, ops_api_url=None)
        service._fetcher = Mock()
        
        # Directly set cached results
        service._cached_results = [mock_result]
        service._remote_search_id = "remote_search_789"  # Remote search was performed
        
        # Mock remote client (configured but shouldn't be used for YouTube)
        mock_remote_client = Mock()
        service._remote_client = mock_remote_client
        
        # Mock local download
        mock_fetch_result = AudioDownloadResult(
            filepath="/tmp/test.opus",
            artist="ABBA",
            title="Waterloo",
            provider="YouTube",
            quality="Opus 128kbps",
        )
        service._fetcher.download.return_value = mock_fetch_result
        
        # Mock _download_remote to ensure it's NOT called
        service._download_remote = Mock()
        
        # Call download
        result = service.download(0, "/tmp")
        
        # Should use local for YouTube
        service._download_remote.assert_not_called()
        service._fetcher.download.assert_called_once()
    
    def test_download_uses_local_when_no_remote_client(self):
        """Test download uses local when remote client is not configured."""
        mock_result = AudioSearchResult(
            title="Waterloo",
            artist="ABBA",
            provider="RED",  # Would normally use remote
            url="",
            quality="FLAC",
            index=0,
        )
        
        service = AudioSearchService(red_api_key=None, red_api_url=None, ops_api_key=None, ops_api_url=None)
        service._fetcher = Mock()
        
        # Directly set cached results
        service._cached_results = [mock_result]
        
        # No remote client
        service._remote_client = None
        service._remote_search_id = None  # No remote search
        
        # Mock local download
        mock_fetch_result = AudioDownloadResult(
            filepath="/tmp/test.flac",
            artist="ABBA",
            title="Waterloo",
            provider="RED",
            quality="FLAC",
        )
        service._fetcher.download.return_value = mock_fetch_result
        
        # Call download
        result = service.download(0, "/tmp")
        
        # Should use local even though it's RED
        service._fetcher.download.assert_called_once()
    
    def test_download_uses_local_when_no_remote_search_id(self):
        """Test download uses local when search wasn't done remotely."""
        mock_result = AudioSearchResult(
            title="Waterloo",
            artist="ABBA",
            provider="RED",
            url="",
            quality="FLAC",
            index=0,
        )
        
        service = AudioSearchService(red_api_key=None, red_api_url=None, ops_api_key=None, ops_api_url=None)
        service._fetcher = Mock()
        
        # Directly set cached results
        service._cached_results = [mock_result]
        
        # Remote client configured but search was local (fallback scenario)
        service._remote_client = Mock()
        service._remote_search_id = None  # Search was local, not remote
        
        # Mock local download
        mock_fetch_result = AudioDownloadResult(
            filepath="/tmp/test.flac",
            artist="ABBA",
            title="Waterloo",
            provider="RED",
            quality="FLAC",
        )
        service._fetcher.download.return_value = mock_fetch_result
        
        # Call download
        result = service.download(0, "/tmp")
        
        # Should use local since no remote search ID
        service._fetcher.download.assert_called_once()
    
    def test_torrent_provider_routing_checks_both_conditions(self):
        """Test that torrent provider routing requires BOTH remote_search_id AND remote_client.
        
        This test verifies the logical AND condition in the download routing.
        """
        mock_result = AudioSearchResult(
            title="Waterloo",
            artist="ABBA",
            provider="RED",
            url="",
            quality="FLAC",
            index=0,
        )
        
        service = AudioSearchService(red_api_key=None, red_api_url=None, ops_api_key=None, ops_api_url=None)
        service._fetcher = Mock()
        service._cached_results = [mock_result]
        
        # Test: remote_client set but no remote_search_id -> should use local
        service._remote_client = Mock()
        service._remote_search_id = None
        
        mock_fetch_result = AudioDownloadResult(
            filepath="/tmp/test.flac", artist="ABBA", title="Waterloo",
            provider="RED", quality="FLAC",
        )
        service._fetcher.download.return_value = mock_fetch_result
        service._download_remote = Mock()
        
        service.download(0, "/tmp")
        service._download_remote.assert_not_called()
        service._fetcher.download.assert_called_once()
        
        # Reset mocks
        service._fetcher.download.reset_mock()
        service._download_remote.reset_mock()
        
        # Test: remote_search_id set but no remote_client -> should use local
        service._remote_client = None
        service._remote_search_id = "search_123"
        
        service.download(0, "/tmp")
        service._download_remote.assert_not_called()
        service._fetcher.download.assert_called_once()
    
    def test_download_does_not_access_nonexistent_attributes(self):
        """Test that download() doesn't try to access nonexistent attributes.
        
        This test would have caught the 'extra_info' attribute error:
        AttributeError: 'AudioSearchResult' object has no attribute 'extra_info'
        
        AudioSearchResult only has: title, artist, url, provider, duration,
        quality, source_id, index, seeders, target_file, raw_result
        """
        mock_result = AudioSearchResult(
            title="Waterloo",
            artist="ABBA",
            provider="RED",
            url="",
            quality="FLAC 16bit CD",
            seeders=50,
            index=0,
        )
        
        # Verify AudioSearchResult doesn't have extra_info
        assert not hasattr(mock_result, 'extra_info')
        
        service = AudioSearchService(red_api_key=None, red_api_url=None, ops_api_key=None, ops_api_url=None)
        service._fetcher = Mock()
        service._cached_results = [mock_result]
        service._remote_search_id = "search_123"
        service._remote_client = Mock()
        
        # Mock _download_remote to avoid actual network call
        mock_download_result = AudioDownloadResult(
            filepath="/tmp/test.flac",
            artist="ABBA",
            title="Waterloo", 
            provider="RED",
            quality="FLAC",
        )
        service._download_remote = Mock(return_value=mock_download_result)
        
        # This should NOT raise AttributeError: 'AudioSearchResult' object has no attribute 'extra_info'
        result = service.download(0, "/tmp")
        
        # Should succeed and use remote download for RED provider
        service._download_remote.assert_called_once()
        assert result.filepath == "/tmp/test.flac"


class TestAudioSearchThemeSupport:
    """Test theme support in audio search requests.

    These tests verify that theme_id and color_overrides are properly:
    1. Accepted in AudioSearchRequest model
    2. Passed through to JobCreate
    3. Result in correct CDG/TXT defaults when theme is set
    4. Theme style is PREPARED (copied to job folder) when theme_id is set
    """

    def test_audio_search_request_accepts_theme_id(self):
        """Test AudioSearchRequest model has theme_id field."""
        from backend.api.routes.audio_search import AudioSearchRequest

        request = AudioSearchRequest(
            artist="Test Artist",
            title="Test Song",
            theme_id="nomad"
        )

        assert request.theme_id == "nomad"

    def test_audio_search_request_accepts_color_overrides(self):
        """Test AudioSearchRequest model has color_overrides field."""
        from backend.api.routes.audio_search import AudioSearchRequest

        request = AudioSearchRequest(
            artist="Test Artist",
            title="Test Song",
            theme_id="nomad",
            color_overrides={
                "artist_color": "#ff0000",
                "title_color": "#00ff00",
            }
        )

        assert request.color_overrides["artist_color"] == "#ff0000"
        assert request.color_overrides["title_color"] == "#00ff00"

    def test_audio_search_request_theme_defaults_cdg_txt(self):
        """Test that when theme_id is set, CDG/TXT defaults to enabled.

        This is the key behavior: selecting a theme should automatically
        enable CDG and TXT output formats.
        """
        from backend.api.routes.audio_search import AudioSearchRequest
        from backend.services.job_defaults_service import resolve_cdg_txt_defaults

        # When theme_id is set, enable_cdg/enable_txt should default to True
        request = AudioSearchRequest(
            artist="Test Artist",
            title="Test Song",
            theme_id="nomad"
            # enable_cdg and enable_txt are None (not specified)
        )

        resolved_cdg, resolved_txt = resolve_cdg_txt_defaults(
            request.theme_id, request.enable_cdg, request.enable_txt
        )

        assert resolved_cdg is True, "CDG should be enabled by default when theme is set"
        assert resolved_txt is True, "TXT should be enabled by default when theme is set"

    def test_audio_search_request_no_theme_no_cdg_txt(self):
        """Test that without theme_id, CDG/TXT defaults to disabled."""
        from backend.api.routes.audio_search import AudioSearchRequest
        from backend.services.job_defaults_service import resolve_cdg_txt_defaults

        request = AudioSearchRequest(
            artist="Test Artist",
            title="Test Song"
            # No theme_id, no enable_cdg, no enable_txt
        )

        resolved_cdg, resolved_txt = resolve_cdg_txt_defaults(
            request.theme_id, request.enable_cdg, request.enable_txt
        )

        assert resolved_cdg is False, "CDG should be disabled by default without theme"
        assert resolved_txt is False, "TXT should be disabled by default without theme"

    def test_explicit_cdg_txt_overrides_theme_default(self):
        """Test that explicit enable_cdg/enable_txt values override theme defaults."""
        from backend.api.routes.audio_search import AudioSearchRequest
        from backend.services.job_defaults_service import resolve_cdg_txt_defaults

        # Theme set (would default to True), but explicitly disabled
        request = AudioSearchRequest(
            artist="Test Artist",
            title="Test Song",
            theme_id="nomad",
            enable_cdg=False,
            enable_txt=False,
        )

        resolved_cdg, resolved_txt = resolve_cdg_txt_defaults(
            request.theme_id, request.enable_cdg, request.enable_txt
        )

        assert resolved_cdg is False, "Explicit False should override theme default"
        assert resolved_txt is False, "Explicit False should override theme default"

    def test_explicit_cdg_txt_enables_without_theme(self):
        """Test that explicit True enables CDG/TXT even without theme."""
        from backend.api.routes.audio_search import AudioSearchRequest
        from backend.services.job_defaults_service import resolve_cdg_txt_defaults

        # No theme (would default to False), but explicitly enabled
        request = AudioSearchRequest(
            artist="Test Artist",
            title="Test Song",
            enable_cdg=True,
            enable_txt=True,
        )

        resolved_cdg, resolved_txt = resolve_cdg_txt_defaults(
            request.theme_id, request.enable_cdg, request.enable_txt
        )

        assert resolved_cdg is True, "Explicit True should enable CDG without theme"
        assert resolved_txt is True, "Explicit True should enable TXT without theme"

    def test_job_create_receives_theme_from_audio_search(self):
        """Test that JobCreate model can receive theme_id and color_overrides."""
        job_create = JobCreate(
            artist="Test Artist",
            title="Test Song",
            theme_id="nomad",
            color_overrides={
                "artist_color": "#ff0000",
                "sung_lyrics_color": "#00ff00"
            },
            enable_cdg=True,
            enable_txt=True,
        )

        assert job_create.theme_id == "nomad"
        assert job_create.color_overrides["artist_color"] == "#ff0000"
        assert job_create.color_overrides["sung_lyrics_color"] == "#00ff00"
        assert job_create.enable_cdg is True
        assert job_create.enable_txt is True

    def test_job_model_stores_theme_configuration(self):
        """Test Job model stores theme configuration correctly."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            artist="Test Artist",
            title="Test Song",
            theme_id="nomad",
            color_overrides={
                "artist_color": "#ff0000"
            },
            enable_cdg=True,
            enable_txt=True,
        )

        assert job.theme_id == "nomad"
        assert job.color_overrides["artist_color"] == "#ff0000"
        assert job.enable_cdg is True
        assert job.enable_txt is True


class TestAudioSearchThemePreparation:
    """Test that audio search endpoint properly prepares theme styles.

    CRITICAL: This test class was added to catch the bug where audio_search.py
    accepted theme_id but never called prepare_job_style(), resulting in jobs
    with theme_id set but no style_params_gcs_path or style_assets.

    The symptom: Preview videos show black background instead of themed background.
    Job ID ffb0b8fa in production demonstrated this issue.
    """

    def test_audio_search_with_theme_calls_prepare_job_style(self):
        """Test that creating a job via audio search with theme_id prepares the style.

        CRITICAL: This test verifies that when a job is created via the audio-search
        endpoint with a theme_id (and no custom style files), the code calls
        prepare_job_style() to set:
        1. style_params_gcs_path (pointing to the copied style_params.json)
        2. style_assets (populated with asset mappings)

        Without this, LyricsTranscriber won't have access to the theme's styles
        and preview videos will have black backgrounds instead of themed ones.
        """
        # Import the endpoint module to access internal functions
        from backend.api.routes import audio_search as audio_search_module

        # Check if there's an import of prepare_job_style from theme_service
        source_code_path = audio_search_module.__file__
        with open(source_code_path, 'r') as f:
            source_code = f.read()

        # Check for either:
        # 1. Import of _prepare_theme_for_job from file_upload
        # 2. Import of prepare_job_style from theme_service
        # 3. Inline call to theme_service.prepare_job_style
        has_theme_prep_import = (
            '_prepare_theme_for_job' in source_code or
            'prepare_job_style' in source_code
        )

        assert has_theme_prep_import, (
            "audio_search.py does not import or call prepare_job_style() or _prepare_theme_for_job(). "
            "When theme_id is provided without custom style files, the endpoint MUST call "
            "prepare_job_style() to copy the theme's style_params.json to the job folder. "
            "Without this, LyricsTranscriber won't have style configuration and preview "
            "videos will have black backgrounds instead of themed ones."
        )

    def test_audio_search_code_handles_theme_preparation(self):
        """Verify audio_search.py has theme preparation logic similar to file_upload.py.

        This test compares audio_search.py with file_upload.py to ensure they have
        similar theme preparation patterns. file_upload.py correctly prepares themes,
        audio_search.py should do the same.
        """
        from backend.api.routes import audio_search as audio_search_module
        from backend.api.routes import file_upload as file_upload_module

        # Read source code of both modules
        with open(audio_search_module.__file__, 'r') as f:
            audio_search_code = f.read()

        with open(file_upload_module.__file__, 'r') as f:
            file_upload_code = f.read()

        # file_upload.py has this pattern for theme preparation:
        # if body.theme_id and not has_style_params_upload:
        #     style_params_path, style_assets, youtube_desc = _prepare_theme_for_job(...)
        assert '_prepare_theme_for_job' in file_upload_code, \
            "file_upload.py should have _prepare_theme_for_job function"

        # audio_search.py should have similar pattern
        # Look for either the function or a similar conditional check
        # STRICT CHECK: Look for actual theme preparation logic being CALLED, not just mentioned
        has_theme_preparation_call = (
            # Check for import or call of theme preparation function
            '_prepare_theme_for_job(' in audio_search_code or
            'prepare_job_style(' in audio_search_code
        )

        assert has_theme_preparation_call, (
            "audio_search.py does not CALL theme preparation logic. "
            "It accepts theme_id but never prepares the theme style like file_upload.py does. "
            "Add a call to _prepare_theme_for_job() when theme_id is set and no style files are uploaded."
        )


class TestFlacfetchIntegration:
    """
    Integration tests that verify flacfetch imports work correctly.
    
    These tests ensure the backend code is compatible with the installed
    flacfetch version and would catch issues like renamed classes.
    
    Note: Some tests are marked with pytest.importorskip for flacfetch
    since it may not be installed in all test environments (e.g., CI).
    """
    
    def test_flacfetch_imports_work(self):
        """Test that all flacfetch imports in audio_search_service work.
        
        This test would have caught the YouTubeProvider -> YoutubeProvider rename.
        """
        flacfetch = pytest.importorskip("flacfetch")
        
        # These imports match what karaoke_gen.audio_fetcher does
        from flacfetch.core.manager import FetchManager
        from flacfetch.providers.youtube import YoutubeProvider
        from flacfetch.core.models import TrackQuery
        
        # Verify the classes exist and are callable
        assert FetchManager is not None
        assert YoutubeProvider is not None
        assert TrackQuery is not None
    
    def test_karaoke_gen_audio_fetcher_imports_work(self):
        """Test that karaoke_gen.audio_fetcher imports work.
        
        Since backend now imports from karaoke_gen, this is the critical test.
        This should work even without flacfetch installed (lazy import).
        """
        from karaoke_gen.audio_fetcher import (
            FlacFetcher,
            AudioSearchResult,
            AudioFetchResult,
            AudioFetcherError,
            NoResultsError,
            DownloadError,
        )
        
        assert FlacFetcher is not None
        assert AudioSearchResult is not None
        assert AudioFetchResult is not None
        assert AudioFetcherError is not None
        assert NoResultsError is not None
        assert DownloadError is not None
    
    def test_audio_search_service_can_initialize_fetcher(self):
        """Test AudioSearchService can initialize its FlacFetcher.
        
        This verifies the actual initialization code path works.
        """
        pytest.importorskip("flacfetch")
        
        service = AudioSearchService(
            red_api_key=None,
            red_api_url=None,
            ops_api_key=None,
        )
        
        # Verify FlacFetcher was initialized
        assert service._fetcher is not None
        
        # FlacFetcher can initialize its manager (tests actual flacfetch)
        manager = service._fetcher._get_manager()
        assert manager is not None
    
    def test_shared_classes_are_same_as_karaoke_gen(self):
        """Verify backend uses the same classes as karaoke_gen."""
        from karaoke_gen.audio_fetcher import (
            AudioSearchResult as KGAudioSearchResult,
            AudioFetchResult as KGAudioFetchResult,
            NoResultsError as KGNoResultsError,
            DownloadError as KGDownloadError,
        )
        
        # These should be the exact same classes, not copies
        assert AudioSearchResult is KGAudioSearchResult
        assert AudioDownloadResult is KGAudioFetchResult
        assert NoResultsError is KGNoResultsError
        assert DownloadError is KGDownloadError


class TestAudioSearchApiRouteDownload:
    """
    Tests for _download_and_start_processing function in audio_search routes.
    
    This tests the API layer's handling of the download flow, including:
    - Correct routing between local and remote downloads
    - GCS path handling for remote downloads
    - Error handling
    """
    
    @pytest.fixture
    def mock_job_manager(self):
        """Mock the job manager singleton."""
        with patch('backend.api.routes.audio_search.job_manager') as mock:
            yield mock
    
    @pytest.fixture
    def mock_storage_service(self):
        """Mock the storage service singleton."""
        with patch('backend.api.routes.audio_search.storage_service') as mock:
            yield mock
    
    def test_remote_download_passes_gcs_path(self, mock_job_manager, mock_storage_service):
        """
        Test that remote torrent downloads include GCS path for direct upload.
        
        This was the bug: we were calling download() without gcs_path for
        torrent sources, causing flacfetch VM to return a local path that
        Cloud Run couldn't access.
        """
        from backend.api.routes.audio_search import _download_and_start_processing
        import asyncio
        
        # Setup mock job with RED search result
        mock_job = Mock()
        mock_job.state_data = {
            'audio_search_results': [{
                'title': 'Unwanted',
                'artist': 'Avril Lavigne',
                'provider': 'RED',  # Torrent source
                'quality': 'FLAC 16bit CD',
            }]
        }
        mock_job.audio_search_artist = 'Avril Lavigne'
        mock_job.audio_search_title = 'Unwanted'
        mock_job_manager.get_job.return_value = mock_job
        
        # Create mock audio search service with remote client
        mock_audio_service = Mock()
        mock_audio_service.is_remote_enabled.return_value = True
        
        # Mock download to return GCS path
        mock_download_result = Mock()
        mock_download_result.filepath = "gs://bucket/uploads/job123/audio/Avril Lavigne - Unwanted.flac"
        mock_audio_service.download.return_value = mock_download_result
        
        # Create mock background tasks
        mock_bg_tasks = Mock()
        
        # Run the async function
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                _download_and_start_processing(
                    job_id="job123",
                    selection_index=0,
                    audio_search_service=mock_audio_service,
                    background_tasks=mock_bg_tasks,
                )
            )
        finally:
            loop.close()
        
        # CRITICAL: Verify download was called with gcs_path for remote torrent source
        mock_audio_service.download.assert_called_once()
        call_kwargs = mock_audio_service.download.call_args.kwargs
        
        # The gcs_path should be set for remote torrent downloads
        assert 'gcs_path' in call_kwargs
        assert call_kwargs['gcs_path'] == "uploads/job123/audio/"
    
    def test_youtube_remote_download_uses_youtube_service(self, mock_job_manager, mock_storage_service):
        """
        Test that YouTube downloads use YouTubeDownloadService.

        After the consolidation refactor, all YouTube downloads go through
        YouTubeDownloadService instead of audio_search_service.download_by_id().
        """
        from backend.api.routes.audio_search import _download_and_start_processing
        import asyncio

        # Setup mock job with YouTube search result that has source_id
        mock_job = Mock()
        mock_job.state_data = {
            'audio_search_results': [{
                'title': 'Demons',
                'artist': 'Kenny Chesney',
                'provider': 'YouTube',
                'quality': 'Opus 128kbps',
                'source_id': 'qmTjyGH9ro4',  # YouTube video ID (11 chars)
                'url': 'https://www.youtube.com/watch?v=qmTjyGH9ro4',
            }]
        }
        mock_job.audio_search_artist = 'Kenny Chesney'
        mock_job.audio_search_title = 'Demons'
        mock_job_manager.get_job.return_value = mock_job

        # Create mock audio search service (not used for YouTube anymore)
        mock_audio_service = Mock()
        mock_audio_service.is_remote_enabled.return_value = True

        # Create mock YouTubeDownloadService
        mock_youtube_service = Mock()
        mock_youtube_service.download_by_id = AsyncMock(
            return_value="uploads/job456/audio/Kenny Chesney - Demons.webm"
        )
        mock_youtube_service._extract_video_id = Mock(return_value="qmTjyGH9ro4")

        # Create mock background tasks
        mock_bg_tasks = Mock()

        # Patch get_youtube_download_service to return our mock
        with patch('backend.api.routes.audio_search.get_youtube_download_service', return_value=mock_youtube_service):
            # Run the async function
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    _download_and_start_processing(
                        job_id="job456",
                        selection_index=0,
                        audio_search_service=mock_audio_service,
                        background_tasks=mock_bg_tasks,
                    )
                )
            finally:
                loop.close()

        # CRITICAL: Verify YouTubeDownloadService.download_by_id was called
        mock_youtube_service.download_by_id.assert_called_once()
        call_kwargs = mock_youtube_service.download_by_id.call_args.kwargs

        # Verify correct arguments
        assert call_kwargs['video_id'] == 'qmTjyGH9ro4'
        assert call_kwargs['job_id'] == 'job456'
        assert call_kwargs['artist'] == 'Kenny Chesney'
        assert call_kwargs['title'] == 'Demons'

        # audio_search_service.download_by_id should NOT be called for YouTube
        mock_audio_service.download_by_id.assert_not_called()

    def test_youtube_download_always_uses_youtube_service(self, mock_job_manager, mock_storage_service):
        """
        Test that YouTube downloads always use YouTubeDownloadService,
        regardless of remote enabled status. The service handles fallback internally.
        """
        from backend.api.routes.audio_search import _download_and_start_processing
        import asyncio

        # Setup mock job with YouTube search result (use valid 11-char video ID)
        mock_job = Mock()
        mock_job.state_data = {
            'audio_search_results': [{
                'title': 'Unwanted',
                'artist': 'Avril Lavigne',
                'provider': 'YouTube',
                'quality': 'Opus 128kbps',
                'source_id': 'abc123xyz45',  # Valid 11-char ID
                'url': 'https://www.youtube.com/watch?v=abc123xyz45',
            }]
        }
        mock_job.audio_search_artist = 'Avril Lavigne'
        mock_job.audio_search_title = 'Unwanted'
        mock_job_manager.get_job.return_value = mock_job

        # Create mock audio search service (not used for YouTube)
        mock_audio_service = Mock()
        mock_audio_service.is_remote_enabled.return_value = False

        # Create mock YouTubeDownloadService
        mock_youtube_service = Mock()
        mock_youtube_service.download_by_id = AsyncMock(
            return_value="uploads/job789/audio/Avril Lavigne - Unwanted.opus"
        )
        mock_youtube_service._extract_video_id = Mock(return_value="abc123xyz45")

        # Create mock background tasks
        mock_bg_tasks = Mock()

        # Patch get_youtube_download_service to return our mock
        with patch('backend.api.routes.audio_search.get_youtube_download_service', return_value=mock_youtube_service):
            # Run the async function
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    _download_and_start_processing(
                        job_id="job789",
                        selection_index=0,
                        audio_search_service=mock_audio_service,
                        background_tasks=mock_bg_tasks,
                    )
                )
            finally:
                loop.close()

            # YouTubeDownloadService.download_by_id should always be called for YouTube
            mock_youtube_service.download_by_id.assert_called_once()
    
    def test_handles_gcs_path_response_correctly(self, mock_job_manager, mock_storage_service):
        """Test that GCS path responses are parsed correctly."""
        from backend.api.routes.audio_search import _download_and_start_processing
        import asyncio
        
        # Setup mock job with RED search result
        mock_job = Mock()
        mock_job.state_data = {
            'audio_search_results': [{
                'title': 'Test',
                'artist': 'Test Artist',
                'provider': 'RED',
                'quality': 'FLAC',
            }]
        }
        mock_job_manager.get_job.return_value = mock_job
        
        # Create mock audio search service
        mock_audio_service = Mock()
        mock_audio_service.is_remote_enabled.return_value = True
        
        # Mock download to return full GCS path
        mock_download_result = Mock()
        mock_download_result.filepath = "gs://karaoke-gen-bucket/uploads/job789/audio/test.flac"
        mock_audio_service.download.return_value = mock_download_result
        
        # Run the async function
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                _download_and_start_processing(
                    job_id="job789",
                    selection_index=0,
                    audio_search_service=mock_audio_service,
                    background_tasks=Mock(),
                )
            )
        finally:
            loop.close()
        
        # Verify job was updated with correct GCS path (without gs://bucket/ prefix)
        update_calls = mock_job_manager.update_job.call_args_list
        
        # Find the call that sets input_media_gcs_path
        gcs_path_set = False
        for call in update_calls:
            if 'input_media_gcs_path' in call.args[1]:
                gcs_path = call.args[1]['input_media_gcs_path']
                # The path stored should be the relative path, not the full gs:// URL
                assert gcs_path == "uploads/job789/audio/test.flac"
                gcs_path_set = True
                break
        
        assert gcs_path_set, "input_media_gcs_path was not set in job update"
    
    def test_torrent_download_requires_remote(self, mock_job_manager, mock_storage_service):
        """
        Test that torrent sources (RED/OPS) require remote flacfetch.

        Torrent downloads can only happen on the flacfetch VM which has:
        - BitTorrent peer connectivity
        - Private tracker credentials
        - Long-running seeding capability

        Cloud Run cannot download torrents, so remote must be enabled.
        """
        from backend.api.routes.audio_search import _download_and_start_processing
        from backend.services.audio_search_service import DownloadError
        import asyncio
        import pytest

        # Setup mock job with RED search result
        mock_job = Mock()
        mock_job.state_data = {
            'audio_search_results': [{
                'title': 'Test',
                'artist': 'Test',
                'provider': 'RED',  # Torrent source requires remote
                'quality': 'FLAC',
            }]
        }
        mock_job_manager.get_job.return_value = mock_job

        # Create mock audio search service WITHOUT remote client
        mock_audio_service = Mock()
        mock_audio_service.is_remote_enabled.return_value = False  # REMOTE DISABLED

        loop = asyncio.new_event_loop()
        try:
            # Should raise an error because RED requires remote
            with pytest.raises(Exception) as exc_info:
                loop.run_until_complete(
                    _download_and_start_processing(
                        job_id="job_no_remote",
                        selection_index=0,
                        audio_search_service=mock_audio_service,
                        background_tasks=Mock(),
                    )
                )
            # Verify the error message mentions remote flacfetch
            assert "remote flacfetch" in str(exc_info.value).lower() or "flacfetch" in str(exc_info.value).lower()
        finally:
            loop.close()
