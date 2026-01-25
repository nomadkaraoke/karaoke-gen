"""
Unit tests for Pydantic models.

Tests validate that our data models work correctly, including:
- Basic field validation
- Nested dictionary structures (file_urls, state_data)
- Serialization/deserialization round-trips (simulating Firestore)
- Real-world data structures from workers

These tests catch type mismatches that would cause runtime errors when
reading data back from Firestore.
"""
import pytest
import json
from datetime import datetime, UTC
from pydantic import ValidationError

from backend.models.job import (
    Job, JobCreate, JobStatus, TimelineEvent
)


class TestJobModel:
    """Test Job Pydantic model - the bug we just fixed!"""
    
    def test_input_media_gcs_path_field_exists(self):
        """
        Test that Job model has input_media_gcs_path field.
        
        This test would have caught the bug where we tried to use
        job.input_media_gcs_path but it wasn't defined in the model!
        """
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            input_media_gcs_path="uploads/test123/file.flac"
        )
        
        # This should NOT raise AttributeError
        assert hasattr(job, 'input_media_gcs_path')
        assert job.input_media_gcs_path == "uploads/test123/file.flac"
    
    def test_input_media_gcs_path_optional(self):
        """Test that input_media_gcs_path is optional."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC)
        )
        
        assert job.input_media_gcs_path is None
    
    def test_pydantic_includes_input_media_gcs_path_in_dict(self):
        """
        Test that Pydantic includes input_media_gcs_path in serialization.
        
        This ensures Pydantic doesn't silently ignore the field.
        """
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            input_media_gcs_path="uploads/test123/file.flac"
        )
        
        job_dict = job.model_dump()
        
        # Pydantic should include it
        assert "input_media_gcs_path" in job_dict
        assert job_dict["input_media_gcs_path"] == "uploads/test123/file.flac"
    
    def test_create_minimal_job(self):
        """Test creating a job with minimal required fields."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC)
        )
        
        assert job.job_id == "test123"
        assert job.status == JobStatus.PENDING
        assert job.progress == 0
        assert job.url is None
        assert job.input_media_gcs_path is None
    
    def test_create_job_with_url(self):
        """Test creating a job with YouTube URL."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            url="https://youtube.com/watch?v=test",
            artist="Test Artist",
            title="Test Song"
        )
        
        assert job.url == "https://youtube.com/watch?v=test"
        assert job.artist == "Test Artist"
        assert job.title == "Test Song"
    
    def test_create_job_with_uploaded_file(self):
        """Test creating a job with uploaded file."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            input_media_gcs_path="uploads/test123/file.flac",
            artist="Test Artist",
            title="Test Song"
        )
        
        assert job.input_media_gcs_path == "uploads/test123/file.flac"
        assert job.url is None


class TestJobCreate:
    """Test JobCreate validation model."""
    
    def test_create_with_url(self):
        """Test creating job with YouTube URL."""
        job_create = JobCreate(
            url="https://youtube.com/watch?v=test",
            artist="Test Artist",
            title="Test Song"
        )
        
        assert job_create.url == "https://youtube.com/watch?v=test"
        assert job_create.artist == "Test Artist"
        assert job_create.title == "Test Song"
    
    def test_create_minimal(self):
        """Test creating job with minimal fields."""
        job_create = JobCreate()
        
        assert job_create.url is None
        assert job_create.artist is None
        assert job_create.title is None


class TestJobStatus:
    """Test JobStatus enum."""
    
    def test_critical_statuses_defined(self):
        """Test that critical statuses exist."""
        critical_statuses = [
            "pending", "downloading",
            "separating_stage1", "separating_stage2", "audio_complete",
            "transcribing", "correcting", "lyrics_complete",
            "generating_screens", "applying_padding",
            "awaiting_review", "in_review", "review_complete",
            "awaiting_instrumental_selection", "instrumental_selected",
            "generating_video", "encoding", "packaging",
            "complete", "failed"
        ]
        
        actual_statuses = [status.value for status in JobStatus]
        
        for status in critical_statuses:
            assert status in actual_statuses, f"Missing critical status: {status}"


class TestTimelineEvent:
    """Test TimelineEvent model."""
    
    def test_create_timeline_event(self):
        """Test creating a timeline event."""
        event = TimelineEvent(
            status="pending",
            timestamp="2025-12-01T08:00:00Z",
            progress=0,
            message="Job created"
        )
        
        assert event.status == "pending"
        assert event.timestamp == "2025-12-01T08:00:00Z"
        assert event.progress == 0
        assert event.message == "Job created"
    
    def test_timeline_event_optional_fields(self):
        """Test that progress and message are optional."""
        event = TimelineEvent(
            status="pending",
            timestamp="2025-12-01T08:00:00Z"
        )
        
        assert event.progress is None
        assert event.message is None


class TestModelValidation:
    """Test model validation rules."""
    
    def test_invalid_job_status(self):
        """Test that invalid status is rejected."""
        with pytest.raises((ValidationError, ValueError)):
            Job(
                job_id="test123",
                status="invalid_status",  # Not a valid JobStatus
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC)
            )
    
    def test_missing_required_fields(self):
        """Test that missing required fields are rejected."""
        with pytest.raises(ValidationError):
            Job(
                job_id="test123"
                # Missing required fields: status, created_at, updated_at
            )


class TestFileUrlsNestedStructure:
    """
    Test that file_urls field correctly handles nested dictionaries.
    
    THIS IS THE TEST THAT WOULD HAVE CAUGHT THE Dict[str, str] vs Dict[str, Any] BUG!
    
    Workers store nested structures like:
    {
        "stems": {"instrumental_clean": "gs://...", "vocals": "gs://..."},
        "lyrics": {"corrections": "gs://...", "lrc": "gs://..."},
        ...
    }
    
    If file_urls is typed as Dict[str, str], this will fail on deserialization.
    """
    
    def test_file_urls_with_nested_stems(self):
        """Test that file_urls accepts nested stems dictionary."""
        job = Job(
            job_id="test123",
            status=JobStatus.AUDIO_COMPLETE,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            file_urls={
                "stems": {
                    "instrumental_clean": "gs://bucket/jobs/test123/stems/instrumental_clean.flac",
                    "instrumental_with_backing": "gs://bucket/jobs/test123/stems/instrumental_with_backing.flac",
                    "vocals_clean": "gs://bucket/jobs/test123/stems/vocals_clean.flac",
                    "lead_vocals": "gs://bucket/jobs/test123/stems/lead_vocals.flac",
                    "backing_vocals": "gs://bucket/jobs/test123/stems/backing_vocals.flac",
                    "bass": "gs://bucket/jobs/test123/stems/bass.flac",
                    "drums": "gs://bucket/jobs/test123/stems/drums.flac",
                }
            }
        )
        
        assert job.file_urls["stems"]["instrumental_clean"] == "gs://bucket/jobs/test123/stems/instrumental_clean.flac"
        assert len(job.file_urls["stems"]) == 7
    
    def test_file_urls_with_nested_lyrics(self):
        """Test that file_urls accepts nested lyrics dictionary."""
        job = Job(
            job_id="test123",
            status=JobStatus.LYRICS_COMPLETE,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            file_urls={
                "lyrics": {
                    "corrections": "gs://bucket/jobs/test123/lyrics/corrections.json",
                    "audio": "gs://bucket/jobs/test123/lyrics/audio.flac",
                    "lrc": "gs://bucket/jobs/test123/lyrics/karaoke.lrc",
                    "uncorrected": "gs://bucket/jobs/test123/lyrics/uncorrected.txt",
                }
            }
        )
        
        assert job.file_urls["lyrics"]["corrections"] == "gs://bucket/jobs/test123/lyrics/corrections.json"
    
    def test_file_urls_full_worker_structure(self):
        """
        Test with the full structure that workers actually create.
        
        This simulates what actually gets stored in Firestore after
        all workers have run.
        """
        full_file_urls = {
            "input": {
                "audio": "gs://bucket/jobs/test123/input/waterloo.flac"
            },
            "stems": {
                "instrumental_clean": "gs://bucket/jobs/test123/stems/instrumental_clean.flac",
                "instrumental_with_backing": "gs://bucket/jobs/test123/stems/instrumental_with_backing.flac",
                "vocals_clean": "gs://bucket/jobs/test123/stems/vocals_clean.flac",
                "lead_vocals": "gs://bucket/jobs/test123/stems/lead_vocals.flac",
                "backing_vocals": "gs://bucket/jobs/test123/stems/backing_vocals.flac",
            },
            "lyrics": {
                "corrections": "gs://bucket/jobs/test123/lyrics/corrections.json",
                "lrc": "gs://bucket/jobs/test123/lyrics/karaoke.lrc",
            },
            "screens": {
                "title": "gs://bucket/jobs/test123/screens/title.mov",
                "end": "gs://bucket/jobs/test123/screens/end.mov",
            },
            "videos": {
                "with_vocals": "gs://bucket/jobs/test123/videos/with_vocals.mkv",
            },
            "finals": {
                "lossless_4k_mp4": "gs://bucket/jobs/test123/finals/lossless_4k.mp4",
                "lossy_720p_mp4": "gs://bucket/jobs/test123/finals/lossy_720p.mp4",
            }
        }
        
        job = Job(
            job_id="test123",
            status=JobStatus.COMPLETE,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            file_urls=full_file_urls
        )
        
        # Verify nested access works
        assert job.file_urls["stems"]["instrumental_clean"].endswith("instrumental_clean.flac")
        assert job.file_urls["lyrics"]["corrections"].endswith("corrections.json")
        assert job.file_urls["finals"]["lossy_720p_mp4"].endswith("lossy_720p.mp4")
    
    def test_file_urls_roundtrip_serialization(self):
        """
        Test that file_urls survives serialization/deserialization.
        
        This simulates what happens when we:
        1. Create a Job with nested file_urls
        2. Serialize to dict (for Firestore)
        3. Deserialize back to Job (reading from Firestore)
        
        THIS IS THE KEY TEST - it would catch the Dict[str, str] bug!
        """
        original_file_urls = {
            "stems": {
                "instrumental_clean": "gs://bucket/stems/clean.flac",
                "vocals": "gs://bucket/stems/vocals.flac",
            },
            "lyrics": {
                "corrections": "gs://bucket/lyrics/corrections.json",
            }
        }
        
        # Create job
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            file_urls=original_file_urls
        )
        
        # Serialize to dict (simulates writing to Firestore)
        job_dict = job.model_dump()
        
        # Convert to JSON and back (simulates Firestore storage)
        json_str = json.dumps(job_dict, default=str)
        restored_dict = json.loads(json_str)
        
        # Deserialize back to Job (simulates reading from Firestore)
        restored_job = Job(**restored_dict)
        
        # Verify nested structure survived
        assert restored_job.file_urls["stems"]["instrumental_clean"] == "gs://bucket/stems/clean.flac"
        assert restored_job.file_urls["stems"]["vocals"] == "gs://bucket/stems/vocals.flac"
        assert restored_job.file_urls["lyrics"]["corrections"] == "gs://bucket/lyrics/corrections.json"


class TestStateDataNestedStructure:
    """Test that state_data field correctly handles nested dictionaries."""
    
    def test_state_data_with_instrumental_options(self):
        """Test state_data with instrumental options structure."""
        job = Job(
            job_id="test123",
            status=JobStatus.AWAITING_INSTRUMENTAL_SELECTION,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            state_data={
                "instrumental_options": {
                    "clean": "gs://bucket/jobs/test123/stems/instrumental_clean.flac",
                    "with_backing": "gs://bucket/jobs/test123/stems/instrumental_with_backing.flac",
                },
                "audio_complete": True,
                "lyrics_complete": True,
            }
        )
        
        assert job.state_data["instrumental_options"]["clean"].endswith("clean.flac")
        assert job.state_data["audio_complete"] is True
    
    def test_state_data_with_lyrics_metadata(self):
        """Test state_data with lyrics metadata structure."""
        job = Job(
            job_id="test123",
            status=JobStatus.LYRICS_COMPLETE,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            state_data={
                "lyrics_metadata": {
                    "line_count": 42,
                    "has_corrections": True,
                    "ready_for_review": True,
                },
                "corrected_lyrics": {
                    "lines": [
                        {"text": "Hello world", "start_time": 1.0, "end_time": 2.0}
                    ],
                    "metadata": {"song": "Test Song"}
                }
            }
        )
        
        assert job.state_data["lyrics_metadata"]["line_count"] == 42
        assert len(job.state_data["corrected_lyrics"]["lines"]) == 1
    
    def test_state_data_roundtrip_serialization(self):
        """Test that state_data survives serialization/deserialization."""
        original_state_data = {
            "instrumental_selection": "clean",
            "review_notes": "Looks good!",
            "complex_nested": {
                "level1": {
                    "level2": {
                        "value": 123
                    }
                }
            }
        }
        
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            state_data=original_state_data
        )
        
        # Serialize and deserialize
        job_dict = job.model_dump()
        json_str = json.dumps(job_dict, default=str)
        restored_dict = json.loads(json_str)
        restored_job = Job(**restored_dict)
        
        # Verify nested structure survived
        assert restored_job.state_data["complex_nested"]["level1"]["level2"]["value"] == 123


class TestWorkerIdsNestedStructure:
    """Test that worker_ids field correctly handles nested dictionaries."""
    
    def test_worker_ids_with_all_workers(self):
        """Test worker_ids with all worker types."""
        job = Job(
            job_id="test123",
            status=JobStatus.GENERATING_VIDEO,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            worker_ids={
                "audio_worker": "cloud-run-request-12345",
                "lyrics_worker": "cloud-run-request-67890",
                "screens_worker": "cloud-run-request-11111",
                "video_worker": "cloud-run-request-22222",
            }
        )
        
        assert job.worker_ids["audio_worker"] == "cloud-run-request-12345"
        assert len(job.worker_ids) == 4


class TestJobModelIntegration:
    """
    Integration tests that simulate real Firestore interactions.
    
    These tests verify that the Job model works correctly when used
    the same way as our actual services.
    """
    
    def test_simulate_job_manager_update_file_url(self):
        """
        Simulate what JobManager.update_file_url does.
        
        This is the pattern that caused the bug - updating nested file_urls.
        """
        # Start with a fresh job
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            file_urls={}
        )
        
        # Simulate update_file_url('test123', 'stems', 'instrumental_clean', 'gs://...')
        # This creates nested structure: {"stems": {"instrumental_clean": "gs://..."}}
        if "stems" not in job.file_urls:
            job.file_urls["stems"] = {}
        job.file_urls["stems"]["instrumental_clean"] = "gs://bucket/stems/clean.flac"
        
        # Simulate Firestore roundtrip
        job_dict = job.model_dump()
        restored_job = Job(**job_dict)
        
        # This is what was failing with Dict[str, str]!
        assert restored_job.file_urls["stems"]["instrumental_clean"] == "gs://bucket/stems/clean.flac"
    
    def test_simulate_audio_worker_completion(self):
        """Simulate the data structure created when audio worker completes."""
        # This is the actual structure created by audio_worker.py
        file_urls = {
            "stems": {
                "instrumental_clean": "gs://bucket/jobs/abc123/stems/instrumental_clean.flac",
                "vocals_clean": "gs://bucket/jobs/abc123/stems/vocals_clean.flac",
                "lead_vocals": "gs://bucket/jobs/abc123/stems/lead_vocals.flac",
                "backing_vocals": "gs://bucket/jobs/abc123/stems/backing_vocals.flac",
                "instrumental_with_backing": "gs://bucket/jobs/abc123/stems/instrumental_with_backing.flac",
            }
        }
        
        state_data = {
            "instrumental_options": {
                "clean": "jobs/abc123/stems/instrumental_clean.flac",
                "with_backing": "jobs/abc123/stems/instrumental_with_backing.flac",
            }
        }
        
        job = Job(
            job_id="abc123",
            status=JobStatus.AUDIO_COMPLETE,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="ABBA",
            title="Waterloo",
            file_urls=file_urls,
            state_data=state_data
        )
        
        # Roundtrip
        restored_job = Job(**job.model_dump())
        
        assert len(restored_job.file_urls["stems"]) == 5
        assert "instrumental_options" in restored_job.state_data
    
    def test_simulate_lyrics_worker_completion(self):
        """Simulate the data structure created when lyrics worker completes."""
        file_urls = {
            "lyrics": {
                "corrections": "gs://bucket/jobs/abc123/lyrics/corrections.json",
                "lrc": "gs://bucket/jobs/abc123/lyrics/karaoke.lrc",
                "uncorrected": "gs://bucket/jobs/abc123/lyrics/uncorrected.txt",
            }
        }
        
        state_data = {
            "lyrics_metadata": {
                "line_count": 50,
                "has_corrections": True,
                "ready_for_review": True,
            }
        }
        
        job = Job(
            job_id="abc123",
            status=JobStatus.LYRICS_COMPLETE,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            file_urls=file_urls,
            state_data=state_data
        )
        
        # Roundtrip
        restored_job = Job(**job.model_dump())
        
        assert restored_job.file_urls["lyrics"]["corrections"].endswith("corrections.json")
        assert restored_job.state_data["lyrics_metadata"]["line_count"] == 50


class TestExistingInstrumentalModelFields:
    """Test existing instrumental field in Job and JobCreate models (Batch 3)."""
    
    def test_job_has_existing_instrumental_gcs_path(self):
        """Test that Job model has existing_instrumental_gcs_path field."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            existing_instrumental_gcs_path="uploads/test123/audio/existing_instrumental.flac"
        )
        
        assert hasattr(job, 'existing_instrumental_gcs_path')
        assert job.existing_instrumental_gcs_path == "uploads/test123/audio/existing_instrumental.flac"
    
    def test_job_existing_instrumental_defaults_to_none(self):
        """Test that existing_instrumental_gcs_path defaults to None."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC)
        )
        
        assert job.existing_instrumental_gcs_path is None
    
    def test_job_create_has_existing_instrumental_gcs_path(self):
        """Test that JobCreate model has existing_instrumental_gcs_path field."""
        from backend.models.job import JobCreate
        
        job_create = JobCreate(
            artist="Artist",
            title="Title",
            existing_instrumental_gcs_path="uploads/test/audio/existing_instrumental.mp3"
        )
        
        assert job_create.existing_instrumental_gcs_path == "uploads/test/audio/existing_instrumental.mp3"
    
    def test_job_create_existing_instrumental_optional(self):
        """Test that existing_instrumental_gcs_path is optional in JobCreate."""
        from backend.models.job import JobCreate
        
        job_create = JobCreate(
            artist="Artist",
            title="Title"
        )
        
        assert job_create.existing_instrumental_gcs_path is None
    
    def test_existing_instrumental_roundtrip_serialization(self):
        """Test that existing_instrumental_gcs_path survives serialization/deserialization."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            existing_instrumental_gcs_path="uploads/test123/audio/existing_instrumental.wav"
        )
        
        # Serialize to dict (simulates writing to Firestore)
        job_dict = job.model_dump()
        
        # Convert to JSON and back (simulates Firestore storage)
        json_str = json.dumps(job_dict, default=str)
        restored_dict = json.loads(json_str)
        
        # Deserialize back to Job (simulates reading from Firestore)
        restored_job = Job(**restored_dict)
        
        assert restored_job.existing_instrumental_gcs_path == "uploads/test123/audio/existing_instrumental.wav"
    
    def test_job_with_full_audio_config(self):
        """Test Job with complete audio configuration including existing instrumental."""
        job = Job(
            job_id="test123",
            status=JobStatus.AUDIO_COMPLETE,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test Artist",
            title="Test Song",
            # Audio model configuration
            clean_instrumental_model="model_bs_roformer_ep_317_sdr_12.9755.ckpt",
            backing_vocals_models=["mel_band_roformer_karaoke.ckpt"],
            other_stems_models=["htdemucs_6s.yaml"],
            # Existing instrumental
            existing_instrumental_gcs_path="uploads/test123/audio/my_instrumental.flac",
        )
        
        assert job.clean_instrumental_model == "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
        assert job.existing_instrumental_gcs_path == "uploads/test123/audio/my_instrumental.flac"


class TestTwoPhaseWorkflowFields:
    """Test two-phase workflow fields in Job and JobCreate models (Batch 6)."""
    
    def test_job_has_prep_only_field(self):
        """Test that Job model has prep_only field."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            prep_only=True
        )
        
        assert hasattr(job, 'prep_only')
        assert job.prep_only is True
    
    def test_job_prep_only_defaults_to_false(self):
        """Test that prep_only defaults to False."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC)
        )
        
        assert job.prep_only is False
    
    def test_job_has_finalise_only_field(self):
        """Test that Job model has finalise_only field."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            finalise_only=True
        )
        
        assert hasattr(job, 'finalise_only')
        assert job.finalise_only is True
    
    def test_job_finalise_only_defaults_to_false(self):
        """Test that finalise_only defaults to False."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC)
        )
        
        assert job.finalise_only is False
    
    def test_job_has_keep_brand_code_field(self):
        """Test that Job model has keep_brand_code field."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            keep_brand_code="NOMAD-1234"
        )
        
        assert hasattr(job, 'keep_brand_code')
        assert job.keep_brand_code == "NOMAD-1234"
    
    def test_job_keep_brand_code_defaults_to_none(self):
        """Test that keep_brand_code defaults to None."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC)
        )
        
        assert job.keep_brand_code is None
    
    def test_job_create_has_two_phase_workflow_fields(self):
        """Test that JobCreate model has two-phase workflow fields."""
        job_create = JobCreate(
            artist="Artist",
            title="Title",
            prep_only=True,
            finalise_only=False,
            keep_brand_code="BRAND-0001"
        )
        
        assert job_create.prep_only is True
        assert job_create.finalise_only is False
        assert job_create.keep_brand_code == "BRAND-0001"
    
    def test_two_phase_workflow_roundtrip_serialization(self):
        """Test that two-phase workflow fields survive serialization/deserialization."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            prep_only=True,
            finalise_only=False,
            keep_brand_code="NOMAD-5678"
        )
        
        # Serialize to dict (simulates writing to Firestore)
        job_dict = job.model_dump()
        
        # Convert to JSON and back (simulates Firestore storage)
        json_str = json.dumps(job_dict, default=str)
        restored_dict = json.loads(json_str)
        
        # Deserialize back to Job (simulates reading from Firestore)
        restored_job = Job(**restored_dict)
        
        assert restored_job.prep_only is True
        assert restored_job.finalise_only is False
        assert restored_job.keep_brand_code == "NOMAD-5678"


class TestPrepCompleteStatus:
    """Test PREP_COMPLETE status and state transitions (Batch 6)."""
    
    def test_prep_complete_status_exists(self):
        """Test that PREP_COMPLETE status exists in JobStatus enum."""
        assert hasattr(JobStatus, 'PREP_COMPLETE')
        assert JobStatus.PREP_COMPLETE.value == "prep_complete"
    
    def test_job_can_have_prep_complete_status(self):
        """Test that Job can be created with PREP_COMPLETE status."""
        job = Job(
            job_id="test123",
            status=JobStatus.PREP_COMPLETE,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC)
        )
        
        assert job.status == JobStatus.PREP_COMPLETE
    
    def test_state_transitions_review_complete_to_prep_complete(self):
        """Test that REVIEW_COMPLETE can transition to PREP_COMPLETE."""
        from backend.models.job import STATE_TRANSITIONS
        
        valid_transitions = STATE_TRANSITIONS.get(JobStatus.REVIEW_COMPLETE, [])
        assert JobStatus.PREP_COMPLETE in valid_transitions
    
    def test_state_transitions_prep_complete_to_awaiting_review(self):
        """Test that PREP_COMPLETE can transition to AWAITING_REVIEW (to continue combined review)."""
        from backend.models.job import STATE_TRANSITIONS

        valid_transitions = STATE_TRANSITIONS.get(JobStatus.PREP_COMPLETE, [])
        assert JobStatus.AWAITING_REVIEW in valid_transitions

    def test_state_transitions_rendering_video_to_instrumental_selected(self):
        """Test that RENDERING_VIDEO transitions directly to INSTRUMENTAL_SELECTED (combined review flow)."""
        from backend.models.job import STATE_TRANSITIONS

        valid_transitions = STATE_TRANSITIONS.get(JobStatus.RENDERING_VIDEO, [])
        assert JobStatus.INSTRUMENTAL_SELECTED in valid_transitions
        # AWAITING_INSTRUMENTAL_SELECTION is no longer a valid destination
        assert JobStatus.AWAITING_INSTRUMENTAL_SELECTION not in valid_transitions
    
    def test_critical_statuses_include_prep_complete(self):
        """Test that PREP_COMPLETE is in the list of job statuses."""
        all_statuses = [status.value for status in JobStatus]
        assert "prep_complete" in all_statuses


class TestJobWithFullTwoPhaseConfig:
    """Integration tests for two-phase workflow job configuration."""
    
    def test_prep_only_job_full_config(self):
        """Test creating a prep-only job with full configuration."""
        job = Job(
            job_id="prep123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test Artist",
            title="Test Song",
            prep_only=True,
            enable_cdg=True,
            enable_txt=True,
            brand_prefix="NOMAD",
        )
        
        assert job.prep_only is True
        assert job.finalise_only is False
        assert job.brand_prefix == "NOMAD"
    
    def test_finalise_only_job_full_config(self):
        """Test creating a finalise-only job with full configuration."""
        job = Job(
            job_id="final123",
            status=JobStatus.AWAITING_INSTRUMENTAL_SELECTION,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test Artist",
            title="Test Song",
            finalise_only=True,
            keep_brand_code="NOMAD-1234",
            enable_youtube_upload=True,
            dropbox_path="/Karaoke/Tracks",
            state_data={
                "audio_complete": True,
                "lyrics_complete": True,
                "finalise_only": True,
            }
        )
        
        assert job.finalise_only is True
        assert job.keep_brand_code == "NOMAD-1234"
        assert job.state_data["finalise_only"] is True
    
    def test_prep_complete_job_with_file_urls(self):
        """Test a job that has reached PREP_COMPLETE status with all prep outputs."""
        job = Job(
            job_id="prepcomp123",
            status=JobStatus.PREP_COMPLETE,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="ABBA",
            title="Waterloo",
            prep_only=True,
            file_urls={
                "stems": {
                    "instrumental_clean": "gs://bucket/jobs/prepcomp123/stems/instrumental_clean.flac",
                    "instrumental_with_backing": "gs://bucket/jobs/prepcomp123/stems/instrumental_with_backing.flac",
                },
                "videos": {
                    "with_vocals": "gs://bucket/jobs/prepcomp123/videos/with_vocals.mkv",
                },
                "screens": {
                    "title": "gs://bucket/jobs/prepcomp123/screens/title.mov",
                    "end": "gs://bucket/jobs/prepcomp123/screens/end.mov",
                },
                "lyrics": {
                    "lrc": "gs://bucket/jobs/prepcomp123/lyrics/karaoke.lrc",
                    "ass": "gs://bucket/jobs/prepcomp123/lyrics/karaoke.ass",
                },
            }
        )
        
        assert job.status == JobStatus.PREP_COMPLETE
        assert job.prep_only is True
        assert "with_vocals" in job.file_urls["videos"]
        assert "title" in job.file_urls["screens"]


class TestMadeForYouFields:
    """Tests for made-for-you order tracking fields."""

    def test_job_has_made_for_you_field(self):
        """Test Job model has made_for_you field with default False."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        assert hasattr(job, 'made_for_you')
        assert job.made_for_you is False

    def test_job_made_for_you_true(self):
        """Test Job model can set made_for_you to True."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            made_for_you=True,
        )
        assert job.made_for_you is True

    def test_job_has_customer_email_field(self):
        """Test Job model has customer_email field with default None."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        assert hasattr(job, 'customer_email')
        assert job.customer_email is None

    def test_job_customer_email_set(self):
        """Test Job model can set customer_email."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            customer_email="customer@example.com",
        )
        assert job.customer_email == "customer@example.com"

    def test_job_has_customer_notes_field(self):
        """Test Job model has customer_notes field with default None."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        assert hasattr(job, 'customer_notes')
        assert job.customer_notes is None

    def test_job_customer_notes_set(self):
        """Test Job model can set customer_notes."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            customer_notes="Please make it extra special!",
        )
        assert job.customer_notes == "Please make it extra special!"

    def test_job_create_has_made_for_you_fields(self):
        """Test JobCreate model has all made-for-you fields."""
        job_create = JobCreate(
            artist="Test Artist",
            title="Test Song",
        )
        assert hasattr(job_create, 'made_for_you')
        assert hasattr(job_create, 'customer_email')
        assert hasattr(job_create, 'customer_notes')
        assert job_create.made_for_you is False
        assert job_create.customer_email is None
        assert job_create.customer_notes is None

    def test_job_create_with_made_for_you_config(self):
        """Test JobCreate with full made-for-you configuration."""
        job_create = JobCreate(
            artist="Seether",
            title="Tonight",
            user_email="admin@nomadkaraoke.com",
            made_for_you=True,
            customer_email="customer@example.com",
            customer_notes="Wedding anniversary!",
        )
        assert job_create.made_for_you is True
        assert job_create.customer_email == "customer@example.com"
        assert job_create.customer_notes == "Wedding anniversary!"
        assert job_create.user_email == "admin@nomadkaraoke.com"

    def test_made_for_you_serialization_roundtrip(self):
        """Test made-for-you fields survive dict serialization."""
        job = Job(
            job_id="test123",
            status=JobStatus.AWAITING_AUDIO_SELECTION,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            made_for_you=True,
            customer_email="customer@example.com",
            customer_notes="Test notes",
            user_email="admin@nomadkaraoke.com",
        )

        job_dict = job.model_dump()

        assert job_dict['made_for_you'] is True
        assert job_dict['customer_email'] == "customer@example.com"
        assert job_dict['customer_notes'] == "Test notes"

    def test_made_for_you_job_with_distribution_settings(self):
        """Test made-for-you job with distribution settings."""
        job = Job(
            job_id="test123",
            status=JobStatus.AWAITING_AUDIO_SELECTION,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            made_for_you=True,
            customer_email="customer@example.com",
            user_email="admin@nomadkaraoke.com",
            enable_youtube_upload=True,
            dropbox_path="/Production/Ready To Upload",
            gdrive_folder_id="1ABC123",
            brand_prefix="NOMAD",
        )

        assert job.made_for_you is True
        assert job.enable_youtube_upload is True
        assert job.dropbox_path == "/Production/Ready To Upload"
        assert job.gdrive_folder_id == "1ABC123"
        assert job.brand_prefix == "NOMAD"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

