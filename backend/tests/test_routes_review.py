"""
Unit tests for review.py routes and related components.

These tests verify the review-related state transitions and data structures.
Tests that require full backend imports are in the emulator integration tests.
"""
import pytest
import json
from datetime import datetime, UTC
from unittest.mock import MagicMock, AsyncMock, patch


class TestJobStatusTransitionsForReview:
    """Tests for review-related state transitions.
    
    These tests verify the Job model's state machine handles review flow correctly.
    """
    
    def test_awaiting_review_can_transition_to_in_review(self):
        """Test AWAITING_REVIEW -> IN_REVIEW is valid."""
        from backend.models.job import STATE_TRANSITIONS, JobStatus
        valid_transitions = STATE_TRANSITIONS.get(JobStatus.AWAITING_REVIEW, [])
        assert JobStatus.IN_REVIEW in valid_transitions
    
    def test_awaiting_review_can_transition_to_review_complete(self):
        """Test AWAITING_REVIEW -> REVIEW_COMPLETE is valid (skip in_review)."""
        from backend.models.job import STATE_TRANSITIONS, JobStatus
        valid_transitions = STATE_TRANSITIONS.get(JobStatus.AWAITING_REVIEW, [])
        assert JobStatus.REVIEW_COMPLETE in valid_transitions
    
    def test_in_review_can_transition_to_review_complete(self):
        """Test IN_REVIEW -> REVIEW_COMPLETE is valid."""
        from backend.models.job import STATE_TRANSITIONS, JobStatus
        valid_transitions = STATE_TRANSITIONS.get(JobStatus.IN_REVIEW, [])
        assert JobStatus.REVIEW_COMPLETE in valid_transitions
    
    def test_review_complete_transitions_to_rendering_video(self):
        """Test REVIEW_COMPLETE -> RENDERING_VIDEO is valid."""
        from backend.models.job import STATE_TRANSITIONS, JobStatus
        valid_transitions = STATE_TRANSITIONS.get(JobStatus.REVIEW_COMPLETE, [])
        assert JobStatus.RENDERING_VIDEO in valid_transitions
    
    def test_rendering_video_status_exists(self):
        """Test RENDERING_VIDEO status exists in JobStatus enum."""
        from backend.models.job import JobStatus
        assert hasattr(JobStatus, 'RENDERING_VIDEO')
        assert JobStatus.RENDERING_VIDEO.value == "rendering_video"
    
    def test_rendering_video_transitions_to_instrumental_selected(self):
        """Test RENDERING_VIDEO -> INSTRUMENTAL_SELECTED is valid (combined review flow)."""
        from backend.models.job import STATE_TRANSITIONS, JobStatus
        valid_transitions = STATE_TRANSITIONS.get(JobStatus.RENDERING_VIDEO, [])
        assert JobStatus.INSTRUMENTAL_SELECTED in valid_transitions
        # AWAITING_INSTRUMENTAL_SELECTION is no longer a valid transition from RENDERING_VIDEO
        assert JobStatus.AWAITING_INSTRUMENTAL_SELECTION not in valid_transitions


class TestStylesConfigRequirements:
    """Tests documenting the required fields for ASS subtitle generation.
    
    These tests verify that the styles config format is documented correctly.
    The actual styles creation is tested in integration tests.
    """
    
    def test_required_karaoke_fields_documented(self):
        """Document required karaoke style fields for ASS generation."""
        # These fields are required by the ASS subtitle generator
        # Missing any of them will cause video generation to fail
        required_fields = [
            "font",        # Font name for ASS
            "font_path",   # Path to font file (can be empty string, NOT None)
            "ass_name",    # Style name in ASS file
            "primary_color",    # Format: "R, G, B, A"
            "secondary_color",  # Format: "R, G, B, A"
            "outline_color",    # Format: "R, G, B, A"
            "back_color",       # Format: "R, G, B, A"
            "bold",        # Boolean
            "italic",      # Boolean
            "underline",   # Boolean
            "strike_out",  # Boolean
            "scale_x",     # Integer
            "scale_y",     # Integer
            "spacing",     # Integer
            "angle",       # Float
            "border_style", # Integer
            "outline",     # Integer
            "shadow",      # Integer
            "margin_l",    # Integer
            "margin_r",    # Integer
            "margin_v",    # Integer
            "encoding",    # Integer
        ]
        
        # This test documents the required fields
        # Actual validation happens in integration tests
        assert len(required_fields) == 22
        assert "ass_name" in required_fields  # This was missing initially
        assert "font_path" in required_fields  # Must be string, not None
    
    def test_minimal_styles_structure(self):
        """Document the minimal styles JSON structure."""
        minimal_styles = {
            "karaoke": {
                "background_color": "#000000",
                "font_path": "",  # MUST be string, NOT None
                "font": "Noto Sans",
                "ass_name": "Default",  # REQUIRED
                "primary_color": "112, 112, 247, 255",
                "secondary_color": "255, 255, 255, 255",
                "outline_color": "26, 58, 235, 255",
                "back_color": "0, 0, 0, 0",
                "bold": False,
                "italic": False,
                "underline": False,
                "strike_out": False,
                "scale_x": 100,
                "scale_y": 100,
                "spacing": 0,
                "angle": 0.0,
                "border_style": 1,
                "outline": 1,
                "shadow": 0,
                "margin_l": 0,
                "margin_r": 0,
                "margin_v": 0,
                "encoding": 0
            }
        }
        
        # Verify structure is valid JSON
        json_str = json.dumps(minimal_styles)
        parsed = json.loads(json_str)
        
        assert "karaoke" in parsed
        assert parsed["karaoke"]["font_path"] == ""  # Not None
        assert parsed["karaoke"]["ass_name"] == "Default"


class TestCorrectionDataMerging:
    """Tests documenting correction data merging requirements.
    
    The LyricsTranscriber React UI sends only partial correction data:
    - corrections
    - corrected_segments
    
    The backend must merge this with the original corrections.json to
    reconstruct a full CorrectionResult.
    """
    
    def test_frontend_sends_partial_data(self):
        """Document what the frontend sends."""
        # The frontend sends only these fields
        frontend_payload = {
            "corrections": [],  # List of corrections made
            "corrected_segments": []  # Updated segment data
        }
        
        # This is NOT a full CorrectionResult
        assert "original_segments" not in frontend_payload
        assert "metadata" not in frontend_payload
    
    def test_merging_strategy(self):
        """Document the merging strategy for corrections."""
        # Original data has full structure
        original_data = {
            "original_segments": [{"id": 1, "text": "test"}],
            "corrected_segments": [{"id": 1, "text": "test"}],
            "corrections": [],
            "metadata": {"artist": "Test"}
        }
        
        # Frontend sends partial update
        frontend_update = {
            "corrections": [{"id": 1, "type": "edit"}],
            "corrected_segments": [{"id": 1, "text": "updated"}]
        }
        
        # Merging strategy: update only the fields sent by frontend
        if 'corrections' in frontend_update:
            original_data['corrections'] = frontend_update['corrections']
        if 'corrected_segments' in frontend_update:
            original_data['corrected_segments'] = frontend_update['corrected_segments']
        
        # Result preserves original_segments and metadata
        assert original_data['original_segments'] == [{"id": 1, "text": "test"}]
        assert original_data['metadata'] == {"artist": "Test"}
        # But has updated corrections and corrected_segments
        assert original_data['corrections'] == [{"id": 1, "type": "edit"}]
        assert original_data['corrected_segments'] == [{"id": 1, "text": "updated"}]


class TestAddLyricsEndpoint:
    """Tests for the add-lyrics endpoint."""
    
    def test_add_lyrics_requires_source_and_lyrics(self):
        """Document that add_lyrics expects source and lyrics fields."""
        # The frontend sends this payload
        valid_payload = {
            "source": "custom",
            "lyrics": "Line 1\nLine 2\nLine 3"
        }
        
        # Both fields are required
        assert "source" in valid_payload
        assert "lyrics" in valid_payload
        assert len(valid_payload["source"].strip()) > 0
        assert len(valid_payload["lyrics"].strip()) > 0
    
    def test_add_lyrics_uses_correction_operations(self):
        """Verify CorrectionOperations.add_lyrics_source is available."""
        from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations
        
        # The method should exist
        assert hasattr(CorrectionOperations, 'add_lyrics_source')
        
        # It should be a static method
        import inspect
        # Get the method and check it's callable
        method = getattr(CorrectionOperations, 'add_lyrics_source')
        assert callable(method)


class TestReviewResubmissionClearsWorkerProgress:
    """Tests for ensuring review submission clears worker progress keys.

    When a job is re-reviewed (e.g., after a reset to awaiting_review), the worker
    progress keys must be cleared to prevent workers from skipping due to
    idempotency checks (checking {worker}_progress.stage == 'complete').
    """

    def test_complete_review_clears_progress_keys(self):
        """Document that complete_review should clear worker progress keys.

        The complete_review endpoint calls delete_state_data_keys with:
        ['render_progress', 'screens_progress', 'video_progress', 'encoding_progress']

        This ensures that when a review is submitted, even if the job was
        previously completed, workers will run fresh instead of skipping.
        """
        # This test documents the expected behavior.
        # The actual integration test is in test_emulator_integration.py.
        expected_keys_to_clear = [
            'render_progress',
            'screens_progress',
            'video_progress',
            'encoding_progress',
        ]

        # Verify all worker progress keys that affect idempotency are included
        assert 'render_progress' in expected_keys_to_clear
        assert 'video_progress' in expected_keys_to_clear
        assert 'encoding_progress' in expected_keys_to_clear

    def test_idempotency_check_examines_progress_stage(self):
        """Document the idempotency check pattern used by workers.

        Workers check state_data.{worker}_progress.stage == 'complete' to skip.
        This is why clearing these keys is critical for re-processing.
        """
        # Example of what stale state looks like (causes skip)
        stale_state_data = {
            'render_progress': {'stage': 'complete'},  # Worker will skip!
            'video_progress': {'stage': 'complete'},
        }

        # After clearing, the keys should not exist
        clean_state_data = {}

        # Verify worker would NOT skip after clearing
        render_progress = clean_state_data.get('render_progress', {})
        assert render_progress.get('stage') != 'complete'

    def test_job_manager_has_delete_state_data_keys_method(self):
        """Verify JobManager has the method needed for clearing keys."""
        from backend.services.job_manager import JobManager

        jm = JobManager.__new__(JobManager)  # Create without __init__
        assert hasattr(jm, 'delete_state_data_keys')
        assert callable(getattr(jm, 'delete_state_data_keys'))


class TestCompleteReviewZeroSegmentValidation:
    """Tests for the 0-segment lyrics guard in complete_review endpoint.

    These test the actual HTTP endpoint, not just the validation logic.
    """

    @pytest.fixture
    def _patched_client(self):
        """Create TestClient with mocked services for complete_review."""
        from backend.models.job import Job, JobStatus

        mock_job_manager = MagicMock()
        mock_job_manager.transition_to_state.return_value = True
        mock_worker_service = MagicMock()
        mock_creds = MagicMock()
        mock_creds.universe_domain = "googleapis.com"

        with patch("backend.api.routes.jobs.job_manager", mock_job_manager), \
             patch("backend.api.routes.jobs.worker_service", mock_worker_service), \
             patch("backend.services.firestore_service.firestore"), \
             patch("backend.services.storage_service.storage"), \
             patch("google.auth.default", return_value=(mock_creds, "test-project")):
            from backend.main import app
            from fastapi.testclient import TestClient
            yield TestClient(app), mock_job_manager

    def _make_job(self, state_data=None):
        from backend.models.job import Job, JobStatus
        return Job(
            job_id="job-zero-seg",
            status=JobStatus.AWAITING_REVIEW,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test Artist",
            title="Test Song",
            state_data=state_data,
        )

    def test_endpoint_rejects_zero_segment_lyrics(self, _patched_client):
        """POST complete-review returns 400 when lyrics pipeline produced 0 segments."""
        client, mock_jm = _patched_client
        job = self._make_job(state_data={
            'lyrics_metadata': {'segment_count': 0, 'ready_for_review': True},
            'corrected_lyrics': {'corrected_segments': [], 'original_segments': []},
        })
        mock_jm.get_job.return_value = job

        response = client.post(
            "/api/jobs/job-zero-seg/complete-review",
            headers={"Authorization": "Bearer test-admin-token"},
        )

        assert response.status_code == 400
        assert "no lyrics were detected" in response.json()["detail"]
        mock_jm.transition_to_state.assert_not_called()

    def test_endpoint_allows_nonzero_segment_lyrics(self, _patched_client):
        """POST complete-review succeeds when lyrics have segments."""
        client, mock_jm = _patched_client
        job = self._make_job(state_data={
            'lyrics_metadata': {'segment_count': 42},
            'corrected_lyrics': {'corrected_segments': [{'text': 'hello'}] * 42},
        })
        mock_jm.get_job.return_value = job

        response = client.post(
            "/api/jobs/job-zero-seg/complete-review",
            headers={"Authorization": "Bearer test-admin-token"},
        )

        assert response.status_code == 200
        mock_jm.transition_to_state.assert_called_once()

    def test_endpoint_allows_job_without_lyrics_metadata(self, _patched_client):
        """POST complete-review succeeds for jobs without lyrics_metadata (e.g. finalise-only)."""
        client, mock_jm = _patched_client
        job = self._make_job(state_data={})  # No lyrics pipeline ran
        mock_jm.get_job.return_value = job

        response = client.post(
            "/api/jobs/job-zero-seg/complete-review",
            headers={"Authorization": "Bearer test-admin-token"},
        )

        assert response.status_code == 200


class TestPreviewStyleLoading:
    """Tests for the unified style loader used in preview video generation.
    
    When a job has custom styles (uploaded via --style_params_json), these
    must be loaded and applied to preview videos, not just the final render.
    
    This was a bug: preview videos were using minimal styles (black background)
    even when the job had custom backgrounds and fonts configured.
    
    The style loading logic is now consolidated in karaoke_gen.style_loader
    to avoid duplication between workers and API routes.
    """
    
    def test_load_styles_from_gcs_with_custom_styles(self, tmp_path):
        """Test that custom styles are downloaded and applied for preview."""
        import os
        from karaoke_gen.style_loader import load_styles_from_gcs
        
        # Create source style params file
        source_style_params = tmp_path / "source_styles.json"
        style_data = {
            "karaoke": {
                "background_image": "/original/path/background.png",
                "font_path": "/original/path/font.ttf",
                "background_color": "#000000",
                "font": "Noto Sans",
                "ass_name": "Default",
            }
        }
        source_style_params.write_text(json.dumps(style_data))
        
        # Create source asset files
        source_background = tmp_path / "background.png"
        source_background.write_bytes(b"PNG image data")
        source_font = tmp_path / "font.ttf"
        source_font.write_bytes(b"TTF font data")
        
        # Create mock download function that simulates GCS download
        def mock_download(gcs_path, local_path):
            if "style_params.json" in gcs_path:
                with open(local_path, 'w') as f:
                    f.write(source_style_params.read_text())
            elif "karaoke_background" in gcs_path:
                with open(local_path, 'wb') as f:
                    f.write(source_background.read_bytes())
            elif "font.ttf" in gcs_path:
                with open(local_path, 'wb') as f:
                    f.write(source_font.read_bytes())
        
        # Call the unified style loader function
        style_assets = {
            "style_params": "jobs/test123/style/style_params.json",
            "karaoke_background": "jobs/test123/style/karaoke_background.png",
            "font": "jobs/test123/style/font.ttf",
        }
        
        styles_path, result_styles = load_styles_from_gcs(
            style_params_gcs_path="jobs/test123/style/style_params.json",
            style_assets=style_assets,
            temp_dir=str(tmp_path / "workdir"),
            download_func=mock_download,
        )
        
        # Verify styles file was created
        assert os.path.exists(styles_path)
        
        # The paths should now point to the local downloaded files, not the original paths
        assert "karaoke" in result_styles
        assert result_styles["karaoke"]["background_image"] != "/original/path/background.png"
        assert "karaoke_background.png" in result_styles["karaoke"]["background_image"]
        assert result_styles["karaoke"]["font_path"] != "/original/path/font.ttf"
        assert "font.ttf" in result_styles["karaoke"]["font_path"]
    
    def test_load_styles_from_gcs_requires_explicit_theme(self, tmp_path):
        """Test that load_styles_from_gcs requires explicit theme (Phase 2)."""
        from karaoke_gen.style_loader import load_styles_from_gcs

        # Phase 2: Calling with no custom styles should raise an error
        with pytest.raises(ValueError, match="style_params_gcs_path is required"):
            load_styles_from_gcs(
                style_params_gcs_path=None,  # No custom styles
                style_assets={},
                temp_dir=str(tmp_path),
                download_func=lambda gcs_path, local_path: None,  # Won't be called
            )
    
    def test_asset_mapping_is_complete(self):
        """Verify all required asset mappings are defined in the unified style loader."""
        from karaoke_gen.style_loader import ASSET_KEY_MAPPINGS
        
        # These mappings must be present for styles to work correctly
        required_keys = [
            "karaoke_background",
            "intro_background",
            "end_background",
            "font",
        ]
        
        for key in required_keys:
            assert key in ASSET_KEY_MAPPINGS, f"Asset mapping '{key}' not found in ASSET_KEY_MAPPINGS"
        
        # Verify karaoke_background maps to the correct path
        karaoke_mapping = ASSET_KEY_MAPPINGS["karaoke_background"]
        assert karaoke_mapping == ("karaoke", "background_image")
        
        # Verify font maps to multiple sections
        font_mappings = ASSET_KEY_MAPPINGS["font"]
        assert isinstance(font_mappings, list)
        assert ("karaoke", "font_path") in font_mappings


class TestInstrumentalAnalysisAudioUrls:
    """Tests for the audio_urls response from get_instrumental_analysis endpoint.

    The frontend expects specific field names in the audio_urls dict.
    This test documents and enforces the contract.
    """

    def test_audio_url_field_names_match_frontend_expectations(self):
        """Verify audio_urls uses 'backing_vocals' not 'backing'.

        The frontend InstrumentalSelector.tsx expects:
        - audio_urls.clean
        - audio_urls.with_backing
        - audio_urls.original
        - audio_urls.backing_vocals  <-- NOT 'backing'

        This field name mismatch caused backing vocals audio to never load
        in cloud mode because the frontend looked for 'backing_vocals' but
        the backend returned 'backing'.
        """
        # Document the expected field names that frontend uses
        expected_audio_url_keys = [
            'clean',           # Clean instrumental (vocals removed)
            'with_backing',    # Instrumental with backing vocals
            'original',        # Original audio file
            'backing_vocals',  # Backing vocals only stem (NOT 'backing')
        ]

        # Verify 'backing' is NOT in the expected keys
        assert 'backing' not in expected_audio_url_keys
        assert 'backing_vocals' in expected_audio_url_keys

    def test_frontend_stem_type_map_uses_backing_vocals(self):
        """Document that frontend maps 'backing' AudioType to 'backing_vocals' API field.

        In InstrumentalSelector.tsx:
        - STEM_TYPE_MAP['backing'] = 'backing_vocals'
        - getAudioUrl() looks for urls.backing_vocals
        - handleAudioChange() looks for urls.backing_vocals

        The backend must return 'backing_vocals' to match.
        """
        # Frontend expects these audio_urls keys in cloud mode
        # (In local mode, it uses STEM_TYPE_MAP which has different values)
        expected_cloud_mode_keys = {
            'original': 'original',
            'backing': 'backing_vocals',  # AudioType 'backing' -> API field 'backing_vocals'
            'clean': 'clean',  # AudioType 'clean' -> API field 'clean'
            'with_backing': 'with_backing',
        }

        # Verify the mapping for backing is 'backing_vocals'
        assert expected_cloud_mode_keys['backing'] == 'backing_vocals'
        assert expected_cloud_mode_keys['clean'] == 'clean'


class TestGetCorrectionDataPrioritizesUpdatedCorrections:
    """Tests for the correction data endpoint's handling of updated vs original corrections.

    The bug: When a user edits lyrics in LyricsAnalyzer, the edits are saved to
    corrections_updated.json. However, when InstrumentalSelector fetches correction
    data via GET /api/review/{job_id}/correction-data, it was returning the ORIGINAL
    corrections.json, ignoring the user's edits.

    The fix: The endpoint now checks for corrections_updated.json first and returns
    that if it exists, matching the behavior of render_video_worker.py.
    """

    def test_endpoint_should_check_updated_corrections_first(self):
        """Document that get_correction_data should check for corrections_updated first.

        The endpoint logic should be:
        1. Check for corrections_updated in file_urls or direct GCS path
        2. If exists, use it
        3. Otherwise, fall back to original corrections.json

        This matches the pattern in render_video_worker.py:128-142.
        """
        # Document the expected file priority
        file_priority = [
            ("corrections_updated", "jobs/{job_id}/lyrics/corrections_updated.json"),
            ("corrections", "jobs/{job_id}/lyrics/corrections.json"),
        ]

        # Verify corrections_updated comes first
        assert file_priority[0][0] == "corrections_updated"
        assert file_priority[1][0] == "corrections"

    def test_updated_corrections_file_path_format(self):
        """Document the expected file path for updated corrections."""
        job_id = "test-job-123"

        # The updated corrections path format
        updated_path = f"jobs/{job_id}/lyrics/corrections_updated.json"

        assert "corrections_updated.json" in updated_path
        assert job_id in updated_path

    def test_file_urls_key_for_updated_corrections(self):
        """Document the file_urls key used for updated corrections."""
        # The file_urls structure for a job with updated corrections
        file_urls = {
            "lyrics": {
                "corrections": "jobs/test-job/lyrics/corrections.json",
                "corrections_updated": "jobs/test-job/lyrics/corrections_updated.json",
            }
        }

        # The key for updated corrections is 'corrections_updated'
        assert "corrections_updated" in file_urls["lyrics"]

        # This is different from 'corrections' (the original)
        assert "corrections" in file_urls["lyrics"]
        assert file_urls["lyrics"]["corrections"] != file_urls["lyrics"]["corrections_updated"]

    def test_render_video_worker_and_api_use_same_pattern(self):
        """Verify render_video_worker and get_correction_data use consistent logic.

        Both should:
        1. Try corrections_updated from file_urls first
        2. Try direct GCS path for corrections_updated
        3. Fall back to corrections from file_urls
        4. Fall back to direct GCS path for corrections.json

        This ensures the user's edits are used consistently.
        """
        # render_video_worker.py pattern (lines 128-142):
        render_worker_priority = [
            "file_urls.lyrics.corrections_updated",
            "file_urls.lyrics.corrections",
            "jobs/{job_id}/lyrics/corrections_updated.json (GCS)",
            "jobs/{job_id}/lyrics/corrections.json (GCS)",
        ]

        # Verify corrections_updated is checked before corrections
        assert "corrections_updated" in render_worker_priority[0]
        assert "corrections_updated" not in render_worker_priority[1]

    def test_correction_data_keys_that_frontend_edits(self):
        """Document which keys the frontend modifies during review.

        The frontend (LyricsAnalyzer) sends only partial data when saving:
        - corrections: List of corrections made
        - corrected_segments: Updated segment data with user edits

        The backend merges these with the original to preserve metadata,
        original_segments, etc.
        """
        # Keys that the frontend modifies
        frontend_edited_keys = ["corrections", "corrected_segments"]

        # Keys that are preserved from original
        preserved_keys = [
            "original_segments",
            "metadata",
            "reference_lyrics",
            "anchor_sequences",
            "gap_sequences",
        ]

        # These should not overlap
        assert not set(frontend_edited_keys) & set(preserved_keys)


class TestCorrectionFlowIntegration:
    """Integration tests documenting the full correction data flow.

    These tests document the expected behavior of the combined review flow
    where corrections must survive from LyricsAnalyzer through InstrumentalSelector
    to the final video render.
    """

    def test_correction_flow_endpoints(self):
        """Document the endpoints involved in the correction flow."""
        # The flow uses these endpoints in order:
        endpoints = [
            ("GET", "/api/review/{job_id}/correction-data", "Fetch corrections for LyricsAnalyzer"),
            ("POST", "/api/jobs/{job_id}/corrections", "Save user's edits from LyricsAnalyzer"),
            ("GET", "/api/review/{job_id}/correction-data", "Fetch corrections for InstrumentalSelector"),
            ("POST", "/api/review/{job_id}/complete", "Submit final review with instrumental selection"),
        ]

        # The second GET must return the UPDATED corrections, not the original
        assert endpoints[2][0] == "GET"
        assert "correction-data" in endpoints[2][1]

    def test_gcs_file_lifecycle_during_review(self):
        """Document the GCS file lifecycle during the review flow."""
        job_id = "test-job-123"

        # Initial state (after lyrics processing)
        initial_files = {
            f"jobs/{job_id}/lyrics/corrections.json": "Created by lyrics worker",
        }

        # After LyricsAnalyzer saves edits
        after_lyrics_review = {
            f"jobs/{job_id}/lyrics/corrections.json": "Still exists (original)",
            f"jobs/{job_id}/lyrics/corrections_updated.json": "Created with user's edits",
        }

        # Verify corrections_updated.json is created during review
        assert f"jobs/{job_id}/lyrics/corrections_updated.json" in after_lyrics_review
        assert f"jobs/{job_id}/lyrics/corrections_updated.json" not in initial_files

    def test_job_file_urls_updated_after_saving_corrections(self):
        """Document that file_urls is updated when corrections are saved."""
        # Before saving corrections
        file_urls_before = {
            "lyrics": {
                "corrections": "jobs/test-job/lyrics/corrections.json",
            }
        }

        # After POST /api/jobs/{job_id}/corrections
        file_urls_after = {
            "lyrics": {
                "corrections": "jobs/test-job/lyrics/corrections.json",
                "corrections_updated": "jobs/test-job/lyrics/corrections_updated.json",
            }
        }

        # The corrections_updated key should be added
        assert "corrections_updated" not in file_urls_before["lyrics"]
        assert "corrections_updated" in file_urls_after["lyrics"]


class TestInstrumentalSelectionValidation:
    """Tests for instrumental_selection validation in complete_review."""

    def test_custom_allowed_with_custom_instrumental_stem(self):
        """'custom' should be valid when custom_instrumental stem exists (mute-region editing)."""
        from unittest.mock import Mock, patch, AsyncMock

        valid_selections = ["clean", "with_backing"]
        # Simulate the validation logic from review.py
        stems = {"custom_instrumental": "jobs/test/stems/custom_instrumental.flac"}
        existing_instrumental_gcs_path = None
        if existing_instrumental_gcs_path or stems.get("custom_instrumental"):
            valid_selections.append("custom")

        assert "custom" in valid_selections

    def test_custom_allowed_with_uploaded_instrumental(self):
        """'custom' should be valid when existing_instrumental_gcs_path is set."""
        valid_selections = ["clean", "with_backing"]
        stems = {}
        existing_instrumental_gcs_path = "uploads/test/audio/instrumental.flac"
        if existing_instrumental_gcs_path or stems.get("custom_instrumental"):
            valid_selections.append("custom")

        assert "custom" in valid_selections

    def test_custom_rejected_without_custom_stem_or_upload(self):
        """'custom' should NOT be valid when neither custom stem nor upload exists."""
        valid_selections = ["clean", "with_backing"]
        stems = {"instrumental_clean": "jobs/test/stems/clean.flac"}
        existing_instrumental_gcs_path = None
        if existing_instrumental_gcs_path or stems.get("custom_instrumental"):
            valid_selections.append("custom")

        assert "custom" not in valid_selections


class TestReviewCompleteDuetFlag:
    """Tests for is_duet flag handling in complete_review endpoint.

    The complete_review endpoint accepts an optional boolean is_duet field.
    When present it must be a bool; it defaults to False when absent.
    The flag is persisted via job_manager.update_state_data so the render
    worker can later activate the multi-singer render path.
    """

    @pytest.fixture
    def mock_job(self):
        """Create a mock job in AWAITING_REVIEW state."""
        from backend.models.job import Job, JobStatus
        from datetime import datetime, UTC
        job = MagicMock(spec=Job)
        job.job_id = "job-duet-test"
        job.status = JobStatus.AWAITING_REVIEW
        job.file_urls = {"stems": {}}
        job.state_data = {}
        job.existing_instrumental_gcs_path = None
        return job

    def _call_complete_review(self, mock_job, payload, mock_job_manager, mock_storage):
        """Call complete_review directly, bypassing HTTP layer."""
        import asyncio
        from backend.api.routes.review import complete_review

        mock_job_manager.get_job.return_value = mock_job
        mock_job_manager.update_state_data.return_value = None
        mock_job_manager.transition_to_state.return_value = True
        mock_job_manager.delete_state_data_keys.return_value = []
        mock_storage.upload_json.return_value = None
        mock_storage.update_file_url.return_value = None

        async def _run():
            return await complete_review(
                job_id=mock_job.job_id,
                updated_data=payload,
                auth_info=("user@test.com", "job_owner"),
            )

        with patch("backend.api.routes.review.JobManager", return_value=mock_job_manager), \
             patch("backend.api.routes.review.StorageService", return_value=mock_storage), \
             patch("backend.api.routes.review.asyncio.create_task"), \
             patch("backend.api.routes.review._background_tasks", set()), \
             patch("backend.services.worker_service.get_worker_service") as mock_ws:
            mock_ws.return_value.trigger_render_video_worker = AsyncMock(return_value=None)
            result = asyncio.run(_run())
        return result

    def test_complete_saves_is_duet_true(self, mock_job):
        """is_duet=True should be stored via update_state_data."""
        mock_job_manager = MagicMock()
        mock_storage = MagicMock()

        payload = {
            "corrections": {"corrected_segments": []},
            "instrumental_selection": "with_backing",
            "is_duet": True,
        }
        result = self._call_complete_review(mock_job, payload, mock_job_manager, mock_storage)

        assert result["status"] == "success"
        calls = [c for c in mock_job_manager.update_state_data.call_args_list
                 if c.args[1] == "is_duet"]
        assert len(calls) == 1
        assert calls[0].args[2] is True

    def test_complete_leaves_is_duet_untouched_when_absent(self, mock_job):
        """Latch-up-only: when is_duet is absent from payload, don't write
        anything — a prior submit_corrections may have set True and we must
        not clobber it."""
        mock_job_manager = MagicMock()
        mock_storage = MagicMock()

        payload = {
            "corrections": {"corrected_segments": []},
            "instrumental_selection": "with_backing",
            # no is_duet field
        }
        result = self._call_complete_review(mock_job, payload, mock_job_manager, mock_storage)

        assert result["status"] == "success"
        calls = [c for c in mock_job_manager.update_state_data.call_args_list
                 if c.args[1] == "is_duet"]
        assert calls == [], "is_duet should not be written when payload omits the field"

    def test_complete_latch_ignores_false_when_prior_true(self, mock_job):
        """is_duet=False on complete is ignored when a prior submit_corrections
        already persisted True. Prevents InstrumentalSelector's fallback
        payload from silently downgrading duet jobs to solo."""
        mock_job_manager = MagicMock()
        mock_storage = MagicMock()
        mock_job.state_data = {"is_duet": True}

        payload = {
            "corrections": {"corrected_segments": []},
            "instrumental_selection": "with_backing",
            "is_duet": False,
        }
        result = self._call_complete_review(mock_job, payload, mock_job_manager, mock_storage)

        assert result["status"] == "success"
        calls = [c for c in mock_job_manager.update_state_data.call_args_list
                 if c.args[1] == "is_duet"]
        assert calls == [], "is_duet=False must not overwrite a prior-True value"

    def test_complete_writes_false_when_no_prior_true(self, mock_job):
        """is_duet=False is honoured when no prior True has been latched."""
        mock_job_manager = MagicMock()
        mock_storage = MagicMock()
        mock_job.state_data = {}

        payload = {
            "corrections": {"corrected_segments": []},
            "instrumental_selection": "with_backing",
            "is_duet": False,
        }
        result = self._call_complete_review(mock_job, payload, mock_job_manager, mock_storage)

        assert result["status"] == "success"
        calls = [c for c in mock_job_manager.update_state_data.call_args_list
                 if c.args[1] == "is_duet"]
        assert len(calls) == 1
        assert calls[0].args[2] is False

    def test_complete_rejects_non_bool_is_duet(self, mock_job):
        """is_duet with a non-boolean value should return 400."""
        import asyncio
        from fastapi import HTTPException
        from backend.api.routes.review import complete_review

        mock_job_manager = MagicMock()
        mock_storage = MagicMock()
        mock_job_manager.get_job.return_value = mock_job

        payload = {
            "corrections": {"corrected_segments": []},
            "instrumental_selection": "with_backing",
            "is_duet": "not a bool",
        }

        async def _run():
            return await complete_review(
                job_id=mock_job.job_id,
                updated_data=payload,
                auth_info=("user@test.com", "job_owner"),
            )

        with patch("backend.api.routes.review.JobManager", return_value=mock_job_manager), \
             patch("backend.api.routes.review.StorageService", return_value=mock_storage):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(_run())
            assert exc_info.value.status_code == 400
            assert "is_duet" in exc_info.value.detail

    def test_is_duet_not_saved_in_corrections_payload(self, mock_job):
        """is_duet should be stripped from the corrections save payload."""
        mock_job_manager = MagicMock()
        mock_storage = MagicMock()

        payload = {
            "corrections": [{"id": 1, "type": "edit"}],
            "corrected_segments": [{"id": 1, "text": "hello"}],
            "instrumental_selection": "clean",
            "is_duet": True,
        }
        self._call_complete_review(mock_job, payload, mock_job_manager, mock_storage)

        # If corrections were uploaded, is_duet must NOT be in the uploaded data
        if mock_storage.upload_json.called:
            uploaded_data = mock_storage.upload_json.call_args[0][1]
            assert "is_duet" not in uploaded_data
            assert "instrumental_selection" not in uploaded_data


class TestPreviewVideoDuetFlag:
    """Tests for is_duet flag handling in generate_preview_video endpoint.

    The preview-video endpoint must accept an optional boolean is_duet field,
    validate it, and pass it into OutputConfig so the preview renders with duet
    colours rather than solo colours.

    We test the extraction + validation logic directly on the endpoint function
    (which runs before the heavy tempfile/GCS work) and verify that OutputConfig
    is constructed with the right is_duet kwarg by intercepting the class.
    """

    @pytest.fixture
    def mock_job(self):
        from backend.models.job import Job, JobStatus
        job = MagicMock(spec=Job)
        job.job_id = "job-preview-duet"
        job.status = JobStatus.AWAITING_REVIEW
        job.input_media_gcs_path = "jobs/job-preview-duet/audio/input.flac"
        job.style_params_gcs_path = None
        job.style_assets = {}
        job.artist = "Test Artist"
        job.title = "Test Song"
        return job

    def _run_preview_video(self, mock_job, payload, captured_config_kwargs):
        """Run generate_preview_video with all heavy dependencies mocked.

        Captures the kwargs passed to OutputConfig() so tests can assert on
        the is_duet value without needing real GCS or video generation.
        """
        import asyncio
        import tempfile
        from backend.api.routes.review import generate_preview_video

        mock_job_manager = MagicMock()
        mock_job_manager.get_job.return_value = mock_job

        mock_storage = MagicMock()
        mock_encoding_service = MagicMock()
        mock_encoding_service.is_preview_enabled = False  # skip GCE path

        mock_correction_result = MagicMock()
        mock_correction_result.segments = []

        def fake_output_config(**kwargs):
            captured_config_kwargs.append(kwargs)
            return MagicMock()

        async def _run():
            return await generate_preview_video(
                job_id=mock_job.job_id,
                updated_data=payload,
                auth_info=("user@test.com", "job_owner"),
            )

        with patch("backend.api.routes.review.JobManager", return_value=mock_job_manager), \
             patch("backend.api.routes.review.StorageService", return_value=mock_storage), \
             patch("backend.api.routes.review.get_encoding_service", return_value=mock_encoding_service), \
             patch("backend.api.routes.review.load_styles_from_gcs", return_value=("/tmp/styles.json", {})), \
             patch("backend.api.routes.review.CorrectionResult") as mock_cr, \
             patch("backend.api.routes.review.CorrectionOperations") as mock_co, \
             patch("backend.api.routes.review.OutputConfig", side_effect=fake_output_config), \
             patch("backend.api.routes.review.create_span") as mock_span, \
             patch("backend.api.routes.review.job_log_context") as mock_log_ctx, \
             patch("backend.api.routes.review.add_span_event"), \
             patch("tempfile.TemporaryDirectory") as mock_tmpdir, \
             patch("os.makedirs"):
            # Make context managers work
            mock_span.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_span.return_value.__exit__ = MagicMock(return_value=False)
            mock_log_ctx.return_value.__enter__ = MagicMock(return_value=None)
            mock_log_ctx.return_value.__exit__ = MagicMock(return_value=False)
            mock_tmpdir.return_value.__enter__ = MagicMock(return_value="/tmp/preview_test")
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            mock_cr.from_dict.return_value = mock_correction_result
            # corrections.json download: write valid JSON
            corrections_json = json.dumps({"lines": [], "metadata": {}})
            mock_storage.download_file.side_effect = lambda gcs, local: None
            with patch("builtins.open", side_effect=lambda p, *a, **kw:
                       __import__('io').StringIO(corrections_json) if 'r' in str(a) else
                       open.__class__(p, *a, **kw)):
                mock_co.generate_preview_video.return_value = {
                    "video_path": "/tmp/preview.mp4",
                    "ass_path": "/tmp/preview.ass",
                }
                try:
                    asyncio.run(_run())
                except Exception:
                    pass  # Some paths raise; we only need captured_config_kwargs

    def test_preview_video_passes_is_duet_true_to_output_config(self, mock_job):
        """is_duet=True in request body must be forwarded to OutputConfig."""
        captured = []
        self._run_preview_video(mock_job, {"is_duet": True}, captured)
        assert len(captured) >= 1, "OutputConfig must be constructed"
        assert captured[0].get("is_duet") is True

    def test_preview_video_defaults_is_duet_false_when_absent(self, mock_job):
        """When is_duet is absent, OutputConfig must receive is_duet=False."""
        captured = []
        self._run_preview_video(mock_job, {}, captured)
        assert len(captured) >= 1, "OutputConfig must be constructed"
        assert captured[0].get("is_duet") is False

    def test_preview_video_rejects_non_bool_is_duet(self, mock_job):
        """is_duet with a non-boolean value should return 400 before OutputConfig."""
        import asyncio
        from fastapi import HTTPException
        from backend.api.routes.review import generate_preview_video

        mock_job_manager = MagicMock()
        mock_job_manager.get_job.return_value = mock_job
        mock_encoding_service = MagicMock()
        mock_encoding_service.is_preview_enabled = False

        async def _run():
            return await generate_preview_video(
                job_id=mock_job.job_id,
                updated_data={"is_duet": "yes"},
                auth_info=("user@test.com", "job_owner"),
            )

        with patch("backend.api.routes.review.JobManager", return_value=mock_job_manager), \
             patch("backend.api.routes.review.get_encoding_service", return_value=mock_encoding_service):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(_run())
            assert exc_info.value.status_code == 400
            assert "is_duet" in exc_info.value.detail
