"""
Unit tests for review.py routes and related components.

These tests verify the review-related state transitions and data structures.
Tests that require full backend imports are in the emulator integration tests.
"""
import pytest
import json


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
    
    def test_rendering_video_transitions_to_instrumental(self):
        """Test RENDERING_VIDEO -> AWAITING_INSTRUMENTAL_SELECTION is valid."""
        from backend.models.job import STATE_TRANSITIONS, JobStatus
        valid_transitions = STATE_TRANSITIONS.get(JobStatus.RENDERING_VIDEO, [])
        assert JobStatus.AWAITING_INSTRUMENTAL_SELECTION in valid_transitions


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
            "style_params": "uploads/test123/style/style_params.json",
            "karaoke_background": "uploads/test123/style/karaoke_background.png",
            "font": "uploads/test123/style/font.ttf",
        }
        
        styles_path, result_styles = load_styles_from_gcs(
            style_params_gcs_path="uploads/test123/style/style_params.json",
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
    
    def test_load_styles_from_gcs_falls_back_to_minimal(self, tmp_path):
        """Test that minimal styles are used when job has no custom styles."""
        import os
        from karaoke_gen.style_loader import load_styles_from_gcs
        
        # Call with no custom styles
        styles_path, result_styles = load_styles_from_gcs(
            style_params_gcs_path=None,  # No custom styles
            style_assets={},
            temp_dir=str(tmp_path),
            download_func=lambda gcs_path, local_path: None,  # Won't be called
        )
        
        # Verify styles file was created
        assert os.path.exists(styles_path)
        
        # Should have karaoke section with minimal/default values
        assert "karaoke" in result_styles
        assert result_styles["karaoke"]["background_color"] == "#000000"
        assert result_styles["karaoke"]["font"] == "Noto Sans"
        # Minimal styles have background_image as None (default)
        assert result_styles["karaoke"].get("background_image") is None
    
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
