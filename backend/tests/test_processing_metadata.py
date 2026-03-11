"""
Tests for processing_metadata field and related functionality.

Covers:
- Job model has processing_metadata field
- processing_metadata is in SUMMARY_FIELD_PATHS (regression test)
- update_processing_metadata helper works correctly
- AudioShake transcriber stores asset_id in metadata
- Corrector passes through transcription metadata
- extract_request_metadata includes auth context when provided
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

from backend.models.job import Job, JobStatus


class TestProcessingMetadataField:
    """Test the processing_metadata field on the Job model."""

    def test_job_has_processing_metadata_field(self):
        """processing_metadata should exist and default to empty dict."""
        job = Job(
            job_id="test-123",
            status=JobStatus.PENDING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        assert hasattr(job, 'processing_metadata')
        assert job.processing_metadata == {}

    def test_processing_metadata_serializes(self):
        """processing_metadata should serialize to JSON correctly."""
        job = Job(
            job_id="test-123",
            status=JobStatus.PENDING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            processing_metadata={
                "transcription": {
                    "provider": "audioshake",
                    "audioshake_task_id": "task-abc",
                    "audioshake_asset_id": "asset-xyz",
                },
            },
        )
        data = job.model_dump(mode='json')
        assert data['processing_metadata']['transcription']['audioshake_task_id'] == "task-abc"
        assert data['processing_metadata']['transcription']['audioshake_asset_id'] == "asset-xyz"

    def test_processing_metadata_deserializes(self):
        """processing_metadata should deserialize from dict correctly."""
        data = {
            "job_id": "test-123",
            "status": "pending",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "processing_metadata": {
                "separation": {
                    "provider": "modal",
                    "duration_seconds": 420.5,
                },
            },
        }
        job = Job(**data)
        assert job.processing_metadata["separation"]["provider"] == "modal"
        assert job.processing_metadata["separation"]["duration_seconds"] == 420.5


class TestSummaryFieldPaths:
    """Regression tests for SUMMARY_FIELD_PATHS."""

    def test_processing_metadata_in_summary_field_paths(self):
        """processing_metadata must be in SUMMARY_FIELD_PATHS for dashboard queries."""
        from backend.services.firestore_service import FirestoreService
        assert 'processing_metadata' in FirestoreService.SUMMARY_FIELD_PATHS


class TestUpdateProcessingMetadata:
    """Test the JobManager.update_processing_metadata helper."""

    @patch('backend.services.job_manager.FirestoreService')
    @patch('backend.services.job_manager.StorageService')
    def test_update_processing_metadata_uses_dot_notation(self, mock_storage_cls, mock_firestore_cls):
        """update_processing_metadata should use Firestore dot-notation for nested updates."""
        from backend.services.job_manager import JobManager

        mock_firestore = MagicMock()
        mock_firestore_cls.return_value = mock_firestore

        jm = JobManager()
        jm.update_processing_metadata("job-123", "transcription", {
            "provider": "audioshake",
            "audioshake_task_id": "task-abc",
        })

        # Should call update_job with dot-notation key
        mock_firestore.update_job.assert_called_once()
        call_args = mock_firestore.update_job.call_args
        assert call_args[0][0] == "job-123"
        updates = call_args[0][1]
        assert "processing_metadata.transcription" in updates
        assert updates["processing_metadata.transcription"]["provider"] == "audioshake"

    @patch('backend.services.job_manager.FirestoreService')
    @patch('backend.services.job_manager.StorageService')
    def test_update_processing_metadata_deep_dot_notation(self, mock_storage_cls, mock_firestore_cls):
        """Deeper dot-notation paths (e.g. timing.worker_seconds) avoid overwriting sibling fields."""
        from backend.services.job_manager import JobManager

        mock_firestore = MagicMock()
        mock_firestore_cls.return_value = mock_firestore

        jm = JobManager()
        jm.update_processing_metadata("job-123", "timing.lyrics_worker_seconds", 42.5)

        call_args = mock_firestore.update_job.call_args
        updates = call_args[0][1]
        assert "processing_metadata.timing.lyrics_worker_seconds" in updates
        assert updates["processing_metadata.timing.lyrics_worker_seconds"] == 42.5

    @patch('backend.services.job_manager.FirestoreService')
    @patch('backend.services.job_manager.StorageService')
    def test_update_processing_metadata_swallows_errors(self, mock_storage_cls, mock_firestore_cls):
        """update_processing_metadata should not raise on failure."""
        from backend.services.job_manager import JobManager

        mock_firestore = MagicMock()
        mock_firestore.update_job.side_effect = Exception("Firestore error")
        mock_firestore_cls.return_value = mock_firestore

        jm = JobManager()
        # Should not raise
        jm.update_processing_metadata("job-123", "timing", {"foo": 1})


class TestAudioShakeAssetId:
    """Test that AudioShake transcriber stores asset_id in metadata."""

    def test_convert_result_format_includes_asset_id(self):
        """_convert_result_format should include asset_id from task_data.assetId."""
        from karaoke_gen.lyrics_transcriber.transcribers.audioshake import AudioShakeTranscriber

        # Create a minimal transcriber instance
        transcriber = AudioShakeTranscriber.__new__(AudioShakeTranscriber)
        transcriber.logger = MagicMock()
        transcriber._last_asset_id = "fallback-asset-id"

        raw_data = {
            "task_data": {
                "id": "task-123",
                "assetId": "asset-456",
                "duration": 120.5,
                "targets": [],
            },
            "transcription": {
                "text": "hello world",
                "lines": [
                    {
                        "text": "hello world",
                        "words": [
                            {"text": "hello", "start": 0.0, "end": 0.5},
                            {"text": "world", "start": 0.6, "end": 1.0},
                        ],
                    }
                ],
                "metadata": {"language": "en"},
            },
        }

        result = transcriber._convert_result_format(raw_data)
        assert result.metadata["task_id"] == "task-123"
        assert result.metadata["asset_id"] == "asset-456"
        assert result.metadata["language"] == "en"

    def test_convert_result_format_fallback_asset_id(self):
        """asset_id should fall back to _last_asset_id if not in task_data."""
        from karaoke_gen.lyrics_transcriber.transcribers.audioshake import AudioShakeTranscriber

        transcriber = AudioShakeTranscriber.__new__(AudioShakeTranscriber)
        transcriber.logger = MagicMock()
        transcriber._last_asset_id = "fallback-asset-id"

        raw_data = {
            "task_data": {
                "id": "task-123",
                # No assetId in task_data
                "targets": [],
            },
            "transcription": {
                "text": "hello",
                "lines": [
                    {
                        "text": "hello",
                        "words": [{"text": "hello", "start": 0.0, "end": 0.5}],
                    }
                ],
                "metadata": {},
            },
        }

        result = transcriber._convert_result_format(raw_data)
        assert result.metadata["asset_id"] == "fallback-asset-id"


class TestCorrectorMetadataPassthrough:
    """Test that corrector passes through transcription metadata."""

    def test_correction_result_includes_transcription_metadata(self):
        """CorrectionResult.metadata should include transcription_metadata from primary transcription."""
        from karaoke_gen.lyrics_transcriber.correction.corrector import LyricsCorrector
        from karaoke_gen.lyrics_transcriber.types import (
            TranscriptionResult, TranscriptionData, LyricsSegment, Word
        )

        # Create a minimal corrector
        corrector = LyricsCorrector(cache_dir="/tmp", enabled_handlers=[])

        # Create transcription result with metadata
        word = Word(id="w1", text="hello", start_time=0.0, end_time=0.5)
        segment = LyricsSegment(id="s1", text="hello", words=[word], start_time=0.0, end_time=0.5)
        transcription_data = TranscriptionData(
            text="hello",
            words=[word],
            segments=[segment],
            source="AudioShake",
            metadata={
                "task_id": "task-123",
                "asset_id": "asset-456",
                "language": "en",
            },
        )
        transcription_result = TranscriptionResult(
            name="AudioShake",
            priority=1,
            result=transcription_data,
        )

        # Run corrector with no reference lyrics (will skip correction but still build result)
        result = corrector.run(
            transcription_results=[transcription_result],
            lyrics_results={},
        )

        # Check that transcription_metadata is passed through
        assert "transcription_metadata" in result.metadata
        assert result.metadata["transcription_metadata"]["task_id"] == "task-123"
        assert result.metadata["transcription_metadata"]["asset_id"] == "asset-456"


class TestExtractRequestMetadataAuthContext:
    """Test that extract_request_metadata includes auth context."""

    def _make_request(self):
        """Create a minimal mock request."""
        request = MagicMock()
        request.headers = {
            "user-agent": "test-agent",
            "x-forwarded-for": "1.2.3.4",
        }
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        return request

    def test_auth_context_included_when_provided(self):
        """Auth context should be in metadata when auth_result is provided."""
        from backend.api.routes.file_upload import extract_request_metadata

        auth_result = MagicMock()
        auth_result.auth_method = "session"
        auth_result.user_type = MagicMock()
        auth_result.user_type.value = "unlimited"
        auth_result.remaining_uses = 42

        metadata = extract_request_metadata(self._make_request(), auth_result=auth_result)
        assert metadata['auth_method'] == "session"
        assert metadata['user_type'] == "unlimited"
        assert metadata['credits_at_creation'] == 42

    def test_no_auth_context_without_auth_result(self):
        """No auth context should be added when auth_result is None."""
        from backend.api.routes.file_upload import extract_request_metadata

        metadata = extract_request_metadata(self._make_request())
        assert 'auth_method' not in metadata
        assert 'user_type' not in metadata
        assert 'credits_at_creation' not in metadata

    def test_audio_search_extract_includes_auth(self):
        """audio_search extract_request_metadata should also support auth_result."""
        from backend.api.routes.audio_search import extract_request_metadata

        auth_result = MagicMock()
        auth_result.auth_method = "api_key"
        auth_result.user_type = MagicMock()
        auth_result.user_type.value = "limited"
        auth_result.remaining_uses = 5

        metadata = extract_request_metadata(self._make_request(), auth_result=auth_result)
        assert metadata['auth_method'] == "api_key"
        assert metadata['user_type'] == "limited"
        assert metadata['credits_at_creation'] == 5

    def test_auth_without_remaining_uses(self):
        """Should handle auth_result with no remaining_uses (e.g., admin)."""
        from backend.api.routes.file_upload import extract_request_metadata

        auth_result = MagicMock(spec=['auth_method', 'user_type'])
        auth_result.auth_method = "admin_token"
        auth_result.user_type = MagicMock()
        auth_result.user_type.value = "admin"

        metadata = extract_request_metadata(self._make_request(), auth_result=auth_result)
        assert metadata['auth_method'] == "admin_token"
        assert metadata['user_type'] == "admin"
        assert 'credits_at_creation' not in metadata

    def test_auth_with_none_user_type(self):
        """Should handle auth_result where user_type is None."""
        from backend.api.routes.file_upload import extract_request_metadata

        auth_result = MagicMock()
        auth_result.auth_method = "session"
        auth_result.user_type = None
        auth_result.remaining_uses = 10

        metadata = extract_request_metadata(self._make_request(), auth_result=auth_result)
        assert metadata['auth_method'] == "session"
        assert metadata['user_type'] is None
        assert metadata['credits_at_creation'] == 10


class TestStoreLyricsProcessingMetadata:
    """Test the _store_lyrics_processing_metadata helper function."""

    def test_extracts_audioshake_ids_from_corrections(self, tmp_path):
        """Should extract task_id and asset_id from corrections.json metadata."""
        import json
        from backend.workers.lyrics_worker import _store_lyrics_processing_metadata

        # Create a mock corrections file
        lyrics_dir = tmp_path / "lyrics"
        lyrics_dir.mkdir()
        corrections_file = lyrics_dir / "Artist - Title (Lyrics Corrections).json"
        corrections_data = {
            "metadata": {
                "transcription_metadata": {
                    "task_id": "task-abc",
                    "asset_id": "asset-xyz",
                    "language": "en",
                },
                "enabled_handlers": ["anchor_sequence", "gap_word_count"],
                "correction_ratio": 0.95,
                "total_words": 100,
                "agentic_routing": "rule-based",
                "anchor_sequences_count": 5,
                "gap_sequences_count": 3,
            },
            "corrections_made": 5,
            "corrected_segments": [
                {"words": [{"text": "w1"}, {"text": "w2"}]},
                {"words": [{"text": "w3"}]},
            ],
            "reference_lyrics": {"genius": {}, "spotify": {}},
        }
        corrections_file.write_text(json.dumps(corrections_data))

        job_manager = MagicMock()
        job_log = MagicMock()

        _store_lyrics_processing_metadata(
            job_manager=job_manager,
            job_id="test-job",
            transcription_result={"lyrics_dir": str(lyrics_dir)},
            transcription_duration=45.6,
            cache_stats={"downloaded": 2, "not_found": 1},
            job_log=job_log,
        )

        # Check transcription metadata was stored
        transcription_call = [
            c for c in job_manager.update_processing_metadata.call_args_list
            if c[0][1] == "transcription"
        ]
        assert len(transcription_call) == 1
        trans_data = transcription_call[0][0][2]
        assert trans_data["provider"] == "audioshake"
        assert trans_data["audioshake_task_id"] == "task-abc"
        assert trans_data["audioshake_asset_id"] == "asset-xyz"
        assert trans_data["segment_count"] == 2
        assert trans_data["word_count"] == 3
        assert trans_data["duration_seconds"] == 45.6

        # Check correction metadata was stored
        correction_call = [
            c for c in job_manager.update_processing_metadata.call_args_list
            if c[0][1] == "correction"
        ]
        assert len(correction_call) == 1
        corr_data = correction_call[0][0][2]
        assert corr_data["corrections_made"] == 5
        assert corr_data["handlers_applied"] == ["anchor_sequence", "gap_word_count"]
        assert corr_data["reference_sources_found"] == ["genius", "spotify"]

        # Check cache stats were stored
        cache_call = [
            c for c in job_manager.update_processing_metadata.call_args_list
            if c[0][1] == "cache"
        ]
        assert len(cache_call) == 1
        cache_data = cache_call[0][0][2]
        assert cache_data["transcription_cache_downloaded"] == 2
        assert cache_data["transcription_cache_not_found"] == 1

    def test_handles_missing_corrections_file(self):
        """Should not raise when corrections file is not found."""
        from backend.workers.lyrics_worker import _store_lyrics_processing_metadata

        job_manager = MagicMock()
        job_log = MagicMock()

        _store_lyrics_processing_metadata(
            job_manager=job_manager,
            job_id="test-job",
            transcription_result={"lyrics_dir": "/nonexistent/path"},
            transcription_duration=30.0,
            cache_stats=None,
            job_log=job_log,
        )

        # Should still store duration even without corrections file
        transcription_call = [
            c for c in job_manager.update_processing_metadata.call_args_list
            if c[0][1] == "transcription"
        ]
        assert len(transcription_call) == 1
        assert transcription_call[0][0][2]["duration_seconds"] == 30.0

    def test_swallows_exceptions(self):
        """Should not raise on unexpected errors."""
        from backend.workers.lyrics_worker import _store_lyrics_processing_metadata

        job_manager = MagicMock()
        job_manager.update_processing_metadata.side_effect = Exception("boom")
        job_log = MagicMock()

        # Should not raise
        _store_lyrics_processing_metadata(
            job_manager=job_manager,
            job_id="test-job",
            transcription_result={"lyrics_dir": ""},
            transcription_duration=10.0,
            cache_stats=None,
            job_log=job_log,
        )
        job_log.warning.assert_called()

    def test_no_audioshake_ids_when_lyrics_dir_missing(self):
        """Without lyrics_dir, corrections.json can't be found — only duration is stored.

        This is the exact bug that shipped in v0.142.0: transcribe_lyrics() didn't
        include lyrics_dir in its return dict, so _store_lyrics_processing_metadata
        could never find the corrections file and AudioShake IDs were silently lost.
        """
        from backend.workers.lyrics_worker import _store_lyrics_processing_metadata

        job_manager = MagicMock()
        job_log = MagicMock()

        # Simulate the pre-fix result dict — no lyrics_dir key at all
        _store_lyrics_processing_metadata(
            job_manager=job_manager,
            job_id="test-job",
            transcription_result={"lrc_filepath": "/tmp/output.lrc"},  # no lyrics_dir!
            transcription_duration=45.0,
            cache_stats=None,
            job_log=job_log,
        )

        # Only duration should be stored — no AudioShake IDs, no correction stats
        transcription_call = [
            c for c in job_manager.update_processing_metadata.call_args_list
            if c[0][1] == "transcription"
        ]
        assert len(transcription_call) == 1
        trans_data = transcription_call[0][0][2]
        assert trans_data["duration_seconds"] == 45.0
        assert "audioshake_task_id" not in trans_data
        assert "provider" not in trans_data

        # No correction metadata should be stored
        correction_calls = [
            c for c in job_manager.update_processing_metadata.call_args_list
            if c[0][1] == "correction"
        ]
        assert len(correction_calls) == 0


class TestTranscribeLyricsReturnsLyricsDir:
    """Contract test: transcribe_lyrics() must include lyrics_dir in its return dict.

    This is the test that would have caught the v0.142.0 bug. The previous test suite
    only tested _store_lyrics_processing_metadata in isolation with a hand-crafted input
    that included lyrics_dir — it never verified the actual caller provides that key.

    Testing lesson: When function A calls function B, don't just test B with ideal inputs.
    Also verify A actually produces those inputs. Test the contract between caller and callee.
    """

    @patch('karaoke_gen.lyrics_processor.LyricsTranscriber')
    @patch('karaoke_gen.lyrics_processor.load_dotenv')
    def test_transcribe_lyrics_returns_lyrics_dir(self, mock_dotenv, mock_transcriber_cls, tmp_path):
        """transcribe_lyrics() result must contain lyrics_dir pointing to the lyrics subdirectory."""
        from karaoke_gen.lyrics_processor import LyricsProcessor

        # Set up mock transcriber result
        mock_results = MagicMock()
        mock_results.lrc_filepath = str(tmp_path / "lyrics" / "output.lrc")
        mock_results.ass_filepath = str(tmp_path / "lyrics" / "output.ass")
        mock_results.video_filepath = str(tmp_path / "lyrics" / "output.mkv")
        mock_results.transcription_corrected = None  # skip correction output
        mock_results.countdown_padding_added = False
        mock_results.countdown_padding_seconds = 0.0
        mock_results.padded_audio_filepath = None

        mock_transcriber_cls.return_value.process.return_value = mock_results

        # Create necessary dirs and a dummy audio file
        lyrics_dir = tmp_path / "lyrics"
        lyrics_dir.mkdir(exist_ok=True)
        (tmp_path / "lyrics" / "output.lrc").write_text("[00:00.00] test")
        (tmp_path / "lyrics" / "output.mkv").write_bytes(b"fake")
        lrc_dest = tmp_path / "Artist - Title (Karaoke).lrc"
        mkv_dest = tmp_path / "Artist - Title (With Vocals).mkv"

        processor = LyricsProcessor.__new__(LyricsProcessor)
        processor.logger = MagicMock()
        processor.lyrics_file = None
        processor.render_video = False
        processor.skip_transcription = False
        processor.skip_transcription_review = True
        processor.style_params_json = None
        processor.subtitle_offset_ms = 0

        with patch.dict('os.environ', {
            'AUDIOSHAKE_API_TOKEN': 'fake-token',
        }):
            result = processor.transcribe_lyrics(
                input_audio_wav=str(tmp_path / "audio.wav"),
                artist="Artist",
                title="Title",
                track_output_dir=str(tmp_path),
            )

        # THE KEY ASSERTION: lyrics_dir must be in the result
        assert "lyrics_dir" in result, (
            "transcribe_lyrics() must return lyrics_dir so _store_lyrics_processing_metadata "
            "can find corrections.json. This was the v0.142.0 bug."
        )
        assert result["lyrics_dir"] == str(tmp_path / "lyrics")

    def test_lyrics_dir_matches_corrections_file_location(self, tmp_path):
        """The lyrics_dir returned by transcribe_lyrics must be where corrections.json lives.

        This verifies the contract: _store_lyrics_processing_metadata looks for corrections
        files in result['lyrics_dir'], and transcribe_lyrics writes them to {track_output_dir}/lyrics.
        """
        import json
        from backend.workers.lyrics_worker import _store_lyrics_processing_metadata

        # Simulate a result dict with lyrics_dir set correctly
        lyrics_dir = tmp_path / "lyrics"
        lyrics_dir.mkdir()

        # Write a corrections file in the same location transcribe_lyrics would
        corrections_file = lyrics_dir / "Artist - Title (Lyrics Corrections).json"
        corrections_file.write_text(json.dumps({
            "metadata": {
                "transcription_metadata": {"task_id": "t1", "asset_id": "a1"},
                "enabled_handlers": [],
            },
            "corrections_made": 0,
            "corrected_segments": [],
        }))

        job_manager = MagicMock()
        job_log = MagicMock()

        # Use the result dict shape that transcribe_lyrics now produces
        _store_lyrics_processing_metadata(
            job_manager=job_manager,
            job_id="test-job",
            transcription_result={
                "lyrics_dir": str(lyrics_dir),
                "lrc_filepath": str(tmp_path / "output.lrc"),
            },
            transcription_duration=30.0,
            cache_stats=None,
            job_log=job_log,
        )

        # AudioShake IDs should be extracted successfully
        transcription_call = [
            c for c in job_manager.update_processing_metadata.call_args_list
            if c[0][1] == "transcription"
        ]
        assert len(transcription_call) == 1
        trans_data = transcription_call[0][0][2]
        assert trans_data["provider"] == "audioshake"
        assert trans_data["audioshake_task_id"] == "t1"
        assert trans_data["audioshake_asset_id"] == "a1"


class TestStoreAudioSourceMetadata:
    """Test the _store_audio_source_metadata helper function."""

    @patch('subprocess.run')
    def test_parses_ffprobe_output(self, mock_run):
        """Should parse ffprobe JSON output and store audio properties."""
        import json
        from backend.workers.audio_worker import _store_audio_source_metadata

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "format": {
                    "format_name": "flac",
                    "duration": "210.4",
                    "size": "26541900",
                    "bit_rate": "1008000",
                },
                "streams": [
                    {
                        "codec_type": "audio",
                        "codec_name": "flac",
                        "sample_rate": "44100",
                        "channels": 2,
                        "bits_per_raw_sample": "16",
                    }
                ],
            }),
        )

        job_manager = MagicMock()
        job_log = MagicMock()

        _store_audio_source_metadata(job_manager, "job-123", "/fake/audio.flac", job_log)

        job_manager.update_processing_metadata.assert_called_once()
        call_args = job_manager.update_processing_metadata.call_args
        assert call_args[0][1] == "audio_source"
        data = call_args[0][2]
        assert data["input_format"] == "flac"
        assert data["input_codec"] == "flac"
        assert data["input_sample_rate"] == 44100
        assert data["input_channels"] == 2
        assert data["input_bit_depth"] == 16
        assert data["input_duration_seconds"] == 210.4
        assert data["input_file_size_bytes"] == 26541900
        assert data["input_bitrate_kbps"] == 1008

    @patch('subprocess.run')
    def test_handles_ffprobe_failure(self, mock_run):
        """Should not raise when ffprobe fails."""
        from backend.workers.audio_worker import _store_audio_source_metadata

        mock_run.return_value = MagicMock(returncode=1, stderr="No such file")

        job_manager = MagicMock()
        job_log = MagicMock()

        _store_audio_source_metadata(job_manager, "job-123", "/bad/path", job_log)
        job_manager.update_processing_metadata.assert_not_called()
        job_log.warning.assert_called()

    @patch('subprocess.run')
    def test_handles_missing_audio_stream(self, mock_run):
        """Should handle files with no audio stream gracefully."""
        import json
        from backend.workers.audio_worker import _store_audio_source_metadata

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "format": {"format_name": "mp4", "duration": "30.0", "size": "1000"},
                "streams": [{"codec_type": "video", "codec_name": "h264"}],
            }),
        )

        job_manager = MagicMock()
        job_log = MagicMock()

        _store_audio_source_metadata(job_manager, "job-123", "/fake/video.mp4", job_log)
        job_manager.update_processing_metadata.assert_called_once()
        data = job_manager.update_processing_metadata.call_args[0][2]
        # Should have format-level data but no audio stream data
        assert data["input_format"] == "mp4"
        assert "input_codec" not in data


class TestStoreVideoProcessingMetadata:
    """Test the _store_video_processing_metadata helper function."""

    def test_stores_encoding_and_distribution_metadata(self, tmp_path):
        """Should store encoding time, formats, and distribution info."""
        from backend.workers.video_worker import _store_video_processing_metadata

        # Create mock result with some files existing
        result = MagicMock()
        result.encoding_time_seconds = 180.5
        result.youtube_url = "https://youtube.com/watch?v=abc"
        result.youtube_upload_queued = False
        result.dropbox_link = "https://dropbox.com/s/xxx"
        result.gdrive_files = {"4k": "file-id-1"}
        result.brand_code = "NOMAD0001"

        # Create a temp file to simulate output
        lossless = tmp_path / "output.mp4"
        lossless.write_bytes(b"x" * 1000)
        result.final_video = str(lossless)
        result.final_video_mkv = None
        result.final_video_lossy = None
        result.final_video_720p = None

        job_manager = MagicMock()

        _store_video_processing_metadata(job_manager, "job-123", result, 300.0)

        calls = {c[0][1]: c[0][2] for c in job_manager.update_processing_metadata.call_args_list}

        # Check encoding
        assert "encoding" in calls
        assert calls["encoding"]["encoding_time_seconds"] == 180.5
        assert "lossless_4k_mp4" in calls["encoding"]["output_formats"]
        assert calls["encoding"]["output_sizes_bytes"]["lossless_4k_mp4"] == 1000

        # Check distribution
        assert "distribution" in calls
        assert calls["distribution"]["youtube_video_url"] == "https://youtube.com/watch?v=abc"
        assert calls["distribution"]["brand_code"] == "NOMAD0001"
        assert calls["distribution"]["gdrive_file_count"] == 1

        # Check timing (uses deep dot-notation to avoid overwriting other workers' timing)
        assert "timing.video_worker_seconds" in calls
        assert calls["timing.video_worker_seconds"] == 300.0

    def test_swallows_exceptions(self):
        """Should not raise on errors."""
        from backend.workers.video_worker import _store_video_processing_metadata

        job_manager = MagicMock()
        job_manager.update_processing_metadata.side_effect = Exception("boom")
        result = MagicMock()
        result.encoding_time_seconds = None

        # Should not raise
        _store_video_processing_metadata(job_manager, "job-123", result, 100.0)


class TestAudioShakeStartTranscriptionAssetId:
    """Test that start_transcription stores _last_asset_id."""

    def test_start_transcription_stores_last_asset_id(self):
        """start_transcription should store _last_asset_id for metadata fallback."""
        from karaoke_gen.lyrics_transcriber.transcribers.audioshake import AudioShakeTranscriber

        transcriber = AudioShakeTranscriber.__new__(AudioShakeTranscriber)
        transcriber.logger = MagicMock()

        # Mock the API and upload optimizer
        transcriber.api = MagicMock()
        transcriber.api.upload_file.return_value = "asset-from-upload"
        transcriber.api.create_task.return_value = "task-from-create"
        transcriber.upload_optimizer = MagicMock()
        transcriber.upload_optimizer.prepare_for_upload.return_value = ("/fake/path", None)

        task_id = transcriber.start_transcription("/fake/audio.flac")

        assert task_id == "task-from-create"
        assert transcriber._last_asset_id == "asset-from-upload"
