"""
Unit tests for AudioEditor.

Tests cover:
- Muting single and multiple regions
- Region boundary handling
- Custom instrumental creation
- Region normalization and merging
- Error handling
"""

import os
import pytest
from pydub import AudioSegment

from karaoke_gen.instrumental_review import (
    AudioEditor,
    MuteRegion,
)
from karaoke_gen.instrumental_review.models import CustomInstrumentalResult


class TestAudioEditorInit:
    """Tests for AudioEditor initialization."""
    
    def test_default_output_format(self):
        """Test editor creates with FLAC as default format."""
        editor = AudioEditor()
        assert editor.output_format == "flac"
    
    def test_custom_output_format(self):
        """Test editor accepts custom output format."""
        editor = AudioEditor(output_format="wav")
        assert editor.output_format == "wav"


class TestMuteRegionModel:
    """Tests for MuteRegion model."""
    
    def test_duration_property(self):
        """Test duration_seconds property calculation."""
        region = MuteRegion(start_seconds=1.0, end_seconds=3.0)
        assert region.duration_seconds == 2.0
    
    def test_overlaps_returns_true_for_overlapping(self):
        """Test overlaps method for overlapping regions."""
        region1 = MuteRegion(start_seconds=1.0, end_seconds=3.0)
        region2 = MuteRegion(start_seconds=2.0, end_seconds=4.0)
        
        assert region1.overlaps(region2) is True
        assert region2.overlaps(region1) is True
    
    def test_overlaps_returns_false_for_non_overlapping(self):
        """Test overlaps method for non-overlapping regions."""
        region1 = MuteRegion(start_seconds=1.0, end_seconds=2.0)
        region2 = MuteRegion(start_seconds=3.0, end_seconds=4.0)
        
        assert region1.overlaps(region2) is False
        assert region2.overlaps(region1) is False
    
    def test_overlaps_edge_case_adjacent(self):
        """Test overlaps method for adjacent regions (touching but not overlapping)."""
        region1 = MuteRegion(start_seconds=1.0, end_seconds=2.0)
        region2 = MuteRegion(start_seconds=2.0, end_seconds=3.0)
        
        # Adjacent regions should not be considered overlapping
        assert region1.overlaps(region2) is False
    
    def test_merge_regions(self):
        """Test merge method combines overlapping regions."""
        region1 = MuteRegion(start_seconds=1.0, end_seconds=3.0)
        region2 = MuteRegion(start_seconds=2.0, end_seconds=4.0)
        
        merged = region1.merge(region2)
        
        assert merged.start_seconds == 1.0
        assert merged.end_seconds == 4.0


class TestMuteSingleRegion:
    """Tests for muting a single region."""
    
    def test_mute_single_region_creates_output(
        self, clean_instrumental_path, loud_audio_path, temp_dir
    ):
        """Muting a single region should create output file."""
        editor = AudioEditor()
        output_path = os.path.join(temp_dir, "custom_instrumental.flac")
        
        mute_regions = [MuteRegion(start_seconds=2.0, end_seconds=4.0)]
        
        result = editor.create_custom_instrumental(
            clean_instrumental_path=clean_instrumental_path,
            backing_vocals_path=loud_audio_path,
            mute_regions=mute_regions,
            output_path=output_path,
        )
        
        assert os.path.exists(output_path)
        assert result.output_path == output_path
    
    def test_mute_single_region_duration_correct(
        self, clean_instrumental_path, loud_audio_path, temp_dir
    ):
        """Output duration should match input duration."""
        editor = AudioEditor()
        output_path = os.path.join(temp_dir, "custom_instrumental.flac")
        
        mute_regions = [MuteRegion(start_seconds=2.0, end_seconds=4.0)]
        
        result = editor.create_custom_instrumental(
            clean_instrumental_path=clean_instrumental_path,
            backing_vocals_path=loud_audio_path,
            mute_regions=mute_regions,
            output_path=output_path,
        )
        
        # Check output duration (should be ~10 seconds)
        output_audio = AudioSegment.from_file(output_path)
        assert abs(len(output_audio) / 1000.0 - 10.0) < 0.5
    
    def test_mute_region_statistics(
        self, clean_instrumental_path, loud_audio_path, temp_dir
    ):
        """Result should include correct mute statistics."""
        editor = AudioEditor()
        output_path = os.path.join(temp_dir, "custom_instrumental.flac")
        
        mute_regions = [MuteRegion(start_seconds=2.0, end_seconds=4.0)]
        
        result = editor.create_custom_instrumental(
            clean_instrumental_path=clean_instrumental_path,
            backing_vocals_path=loud_audio_path,
            mute_regions=mute_regions,
            output_path=output_path,
        )
        
        assert len(result.mute_regions_applied) == 1
        assert result.total_muted_duration_seconds == 2.0


class TestMuteMultipleRegions:
    """Tests for muting multiple regions."""
    
    def test_mute_multiple_regions(
        self, clean_instrumental_path, loud_audio_path, temp_dir
    ):
        """Multiple mute regions should all be applied."""
        editor = AudioEditor()
        output_path = os.path.join(temp_dir, "custom_instrumental.flac")
        
        mute_regions = [
            MuteRegion(start_seconds=1.0, end_seconds=2.0),
            MuteRegion(start_seconds=5.0, end_seconds=6.0),
            MuteRegion(start_seconds=8.0, end_seconds=9.0),
        ]
        
        result = editor.create_custom_instrumental(
            clean_instrumental_path=clean_instrumental_path,
            backing_vocals_path=loud_audio_path,
            mute_regions=mute_regions,
            output_path=output_path,
        )
        
        assert os.path.exists(output_path)
        assert len(result.mute_regions_applied) == 3
        assert result.total_muted_duration_seconds == 3.0
    
    def test_overlapping_regions_are_merged(
        self, clean_instrumental_path, loud_audio_path, temp_dir
    ):
        """Overlapping mute regions should be merged."""
        editor = AudioEditor()
        output_path = os.path.join(temp_dir, "custom_instrumental.flac")
        
        # These overlap: [1-3] and [2-4] should become [1-4]
        mute_regions = [
            MuteRegion(start_seconds=1.0, end_seconds=3.0),
            MuteRegion(start_seconds=2.0, end_seconds=4.0),
        ]
        
        result = editor.create_custom_instrumental(
            clean_instrumental_path=clean_instrumental_path,
            backing_vocals_path=loud_audio_path,
            mute_regions=mute_regions,
            output_path=output_path,
        )
        
        # Should be merged into 1 region
        assert len(result.mute_regions_applied) == 1
        assert result.mute_regions_applied[0].start_seconds == 1.0
        assert result.mute_regions_applied[0].end_seconds == 4.0


class TestMuteRegionBoundaries:
    """Tests for mute region boundary handling."""
    
    def test_mute_at_start(
        self, clean_instrumental_path, loud_audio_path, temp_dir
    ):
        """Muting at the start of audio should work."""
        editor = AudioEditor()
        output_path = os.path.join(temp_dir, "custom_instrumental.flac")
        
        mute_regions = [MuteRegion(start_seconds=0.0, end_seconds=2.0)]
        
        result = editor.create_custom_instrumental(
            clean_instrumental_path=clean_instrumental_path,
            backing_vocals_path=loud_audio_path,
            mute_regions=mute_regions,
            output_path=output_path,
        )
        
        assert os.path.exists(output_path)
        assert result.total_muted_duration_seconds == 2.0
    
    def test_mute_at_end(
        self, clean_instrumental_path, loud_audio_path, temp_dir
    ):
        """Muting at the end of audio should work."""
        editor = AudioEditor()
        output_path = os.path.join(temp_dir, "custom_instrumental.flac")
        
        mute_regions = [MuteRegion(start_seconds=8.0, end_seconds=10.0)]
        
        result = editor.create_custom_instrumental(
            clean_instrumental_path=clean_instrumental_path,
            backing_vocals_path=loud_audio_path,
            mute_regions=mute_regions,
            output_path=output_path,
        )
        
        assert os.path.exists(output_path)
    
    def test_mute_region_clamped_to_audio_length(
        self, clean_instrumental_path, loud_audio_path, temp_dir
    ):
        """Mute region extending beyond audio should be clamped."""
        editor = AudioEditor()
        output_path = os.path.join(temp_dir, "custom_instrumental.flac")
        
        # Region extends beyond 10 second audio
        mute_regions = [MuteRegion(start_seconds=8.0, end_seconds=15.0)]
        
        result = editor.create_custom_instrumental(
            clean_instrumental_path=clean_instrumental_path,
            backing_vocals_path=loud_audio_path,
            mute_regions=mute_regions,
            output_path=output_path,
        )
        
        # Should not raise error
        assert os.path.exists(output_path)


class TestCustomInstrumentalCombination:
    """Tests for combining clean instrumental with muted backing vocals."""
    
    def test_output_combines_both_tracks(
        self, clean_instrumental_path, mixed_audio_path, temp_dir
    ):
        """Output should be combination of clean instrumental and edited backing."""
        editor = AudioEditor()
        output_path = os.path.join(temp_dir, "custom_instrumental.flac")
        
        mute_regions = [MuteRegion(start_seconds=3.0, end_seconds=5.0)]
        
        result = editor.create_custom_instrumental(
            clean_instrumental_path=clean_instrumental_path,
            backing_vocals_path=mixed_audio_path,
            mute_regions=mute_regions,
            output_path=output_path,
        )
        
        assert os.path.exists(output_path)
        output_audio = AudioSegment.from_file(output_path)
        
        # Output should be stereo or have similar properties
        assert len(output_audio) > 0
    
    def test_no_mute_regions_still_combines(
        self, clean_instrumental_path, mixed_audio_path, temp_dir
    ):
        """Empty mute regions should still combine tracks."""
        editor = AudioEditor()
        output_path = os.path.join(temp_dir, "custom_instrumental.flac")
        
        result = editor.create_custom_instrumental(
            clean_instrumental_path=clean_instrumental_path,
            backing_vocals_path=mixed_audio_path,
            mute_regions=[],
            output_path=output_path,
        )
        
        assert os.path.exists(output_path)
        assert len(result.mute_regions_applied) == 0


class TestApplyMuteToSingleTrack:
    """Tests for apply_mute_to_single_track method."""
    
    def test_mute_single_track(self, loud_audio_path, temp_dir):
        """Should mute regions in a single track."""
        editor = AudioEditor()
        output_path = os.path.join(temp_dir, "muted_backing.flac")
        
        mute_regions = [MuteRegion(start_seconds=2.0, end_seconds=4.0)]
        
        result_path = editor.apply_mute_to_single_track(
            audio_path=loud_audio_path,
            mute_regions=mute_regions,
            output_path=output_path,
        )
        
        assert os.path.exists(result_path)
        assert result_path == output_path


class TestErrorHandling:
    """Tests for error handling."""
    
    def test_nonexistent_clean_instrumental_raises_error(
        self, loud_audio_path, temp_dir
    ):
        """Non-existent clean instrumental should raise FileNotFoundError."""
        editor = AudioEditor()
        output_path = os.path.join(temp_dir, "custom_instrumental.flac")
        
        with pytest.raises(FileNotFoundError):
            editor.create_custom_instrumental(
                clean_instrumental_path="/nonexistent/clean.flac",
                backing_vocals_path=loud_audio_path,
                mute_regions=[],
                output_path=output_path,
            )
    
    def test_nonexistent_backing_vocals_raises_error(
        self, clean_instrumental_path, temp_dir
    ):
        """Non-existent backing vocals should raise FileNotFoundError."""
        editor = AudioEditor()
        output_path = os.path.join(temp_dir, "custom_instrumental.flac")
        
        with pytest.raises(FileNotFoundError):
            editor.create_custom_instrumental(
                clean_instrumental_path=clean_instrumental_path,
                backing_vocals_path="/nonexistent/backing.flac",
                mute_regions=[],
                output_path=output_path,
            )
    
    def test_invalid_region_start_raises_error(
        self, clean_instrumental_path, loud_audio_path, temp_dir
    ):
        """Negative region start should raise ValidationError at model level."""
        from pydantic import ValidationError
        
        # Pydantic validates at model creation time
        with pytest.raises(ValidationError):
            MuteRegion(start_seconds=-1.0, end_seconds=2.0)
    
    def test_invalid_region_end_before_start_raises_error(
        self, clean_instrumental_path, loud_audio_path, temp_dir
    ):
        """Region end before start should raise ValueError."""
        editor = AudioEditor()
        output_path = os.path.join(temp_dir, "custom_instrumental.flac")
        
        mute_regions = [MuteRegion(start_seconds=5.0, end_seconds=3.0)]
        
        with pytest.raises(ValueError):
            editor.create_custom_instrumental(
                clean_instrumental_path=clean_instrumental_path,
                backing_vocals_path=loud_audio_path,
                mute_regions=mute_regions,
                output_path=output_path,
            )


class TestPreviewWithMutes:
    """Tests for preview_with_mutes method."""
    
    def test_preview_returns_audio_segment(
        self, clean_instrumental_path, loud_audio_path
    ):
        """Preview should return AudioSegment in memory."""
        editor = AudioEditor()
        
        mute_regions = [MuteRegion(start_seconds=2.0, end_seconds=4.0)]
        
        preview = editor.preview_with_mutes(
            clean_instrumental_path=clean_instrumental_path,
            backing_vocals_path=loud_audio_path,
            mute_regions=mute_regions,
        )
        
        assert isinstance(preview, AudioSegment)
        assert len(preview) > 0
    
    def test_preview_can_save_to_file(
        self, clean_instrumental_path, loud_audio_path, temp_dir
    ):
        """Preview should optionally save to file."""
        editor = AudioEditor()
        output_path = os.path.join(temp_dir, "preview.flac")
        
        mute_regions = [MuteRegion(start_seconds=2.0, end_seconds=4.0)]
        
        preview = editor.preview_with_mutes(
            clean_instrumental_path=clean_instrumental_path,
            backing_vocals_path=loud_audio_path,
            mute_regions=mute_regions,
            output_path=output_path,
        )
        
        assert os.path.exists(output_path)


class TestBackingVocalsMixLevel:
    """Backing vocals are mixed in quieter than the clean instrumental."""

    def test_backing_vocals_attenuated_in_custom_instrumental(self, loud_audio_path, temp_dir):
        from karaoke_gen.mix_levels import BACKING_VOCALS_MIX_GAIN, BACKING_VOCALS_MIX_GAIN_DB

        silent = AudioSegment.silent(duration=len(AudioSegment.from_file(loud_audio_path)), frame_rate=44100)
        silent_path = os.path.join(temp_dir, "silent_instrumental.flac")
        silent.export(silent_path, format="flac")
        output_path = os.path.join(temp_dir, "custom.flac")

        AudioEditor().create_custom_instrumental(
            clean_instrumental_path=silent_path,
            backing_vocals_path=loud_audio_path,
            mute_regions=[],
            output_path=output_path,
        )

        original = AudioSegment.from_file(loud_audio_path)
        output = AudioSegment.from_file(output_path)
        assert BACKING_VOCALS_MIX_GAIN < 1
        assert output.dBFS == pytest.approx(original.dBFS + BACKING_VOCALS_MIX_GAIN_DB, abs=0.5)
