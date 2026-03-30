import os
import shutil
import pytest
import glob
import json
import tempfile
from unittest.mock import MagicMock, patch, call, mock_open
import datetime as dt # Use alias to avoid conflict
import fcntl
from pydub import AudioSegment
from karaoke_gen.karaoke_gen import KaraokePrep

class TestAudio:
    def test_separate_audio(self, basic_karaoke_gen, temp_dir):
        """Test separating audio into instrumental and vocals."""
        # Setup
        audio_file = os.path.join(temp_dir, "input.wav")
        with open(audio_file, "w") as f:
            f.write("mock audio content")
        
        model_name = "test_model.ckpt"
        artist_title = "Test Artist - Test Title"
        track_output_dir = temp_dir
        instrumental_path = os.path.join(temp_dir, f"{artist_title} (Instrumental {model_name}).flac")
        vocals_path = os.path.join(temp_dir, f"{artist_title} (Vocals {model_name}).flac")
        
        # Mock Separator
        mock_separator = MagicMock()
        mock_separator.load_model.return_value = None
        mock_separator.separate.return_value = [
            f"{artist_title} (Vocals)_test_model.flac",
            f"{artist_title} (Instrumental)_test_model.flac",
            f"{artist_title}_(Piano)_test_model.flac"
        ]
        
        # Mock dependencies
        with patch('karaoke_gen.audio_processor.Separator', return_value=mock_separator), \
             patch('os.rename') as mock_rename:
            
            # Call the method
            basic_karaoke_gen.audio_processor.separate_audio(
                audio_file=audio_file,
                model_name=model_name,
                artist_title=artist_title,
                track_output_dir=track_output_dir,
                instrumental_path=instrumental_path,
                vocals_path=vocals_path
            )
            
            # Verify Separator.load_model was called with correct arguments
            mock_separator.load_model.assert_called_once_with(model_filename=model_name)
            
            # Verify Separator.separate was called with correct arguments
            mock_separator.separate.assert_called_once_with(audio_file)
            
            # Verify os.rename was called for each output file
            assert mock_rename.call_count == 3
            mock_rename.assert_any_call(
                f"{artist_title} (Vocals)_test_model.flac",
                vocals_path
            )
            mock_rename.assert_any_call(
                f"{artist_title} (Instrumental)_test_model.flac",
                instrumental_path
            )
    
    def test_separate_audio_invalid_audio_file(self, basic_karaoke_gen):
        """Test separating audio with an invalid audio file."""
        with pytest.raises(Exception, match="Error: Invalid audio source provided."):
            basic_karaoke_gen.audio_processor.separate_audio(
                audio_file=None,
                model_name="test_model.ckpt",
                artist_title="Test Artist - Test Title",
                track_output_dir="output_dir",
                instrumental_path="instrumental.flac",
                vocals_path="vocals.flac"
            )
    
    def test_process_audio_separation(self, basic_karaoke_gen, temp_dir):
        """Test the process_audio_separation method."""
        # Setup
        audio_file = os.path.join(temp_dir, "input.wav")
        with open(audio_file, "w") as f:
            f.write("mock audio content")
        
        artist_title = "Test Artist - Test Title"
        track_output_dir = temp_dir
        
        # Create stems directory
        stems_dir = os.path.join(track_output_dir, "stems")
        os.makedirs(stems_dir, exist_ok=True)
        
        # Mock Separator
        mock_separator = MagicMock()
        mock_separator.load_model.return_value = None
        
        # Mock output files for different models
        clean_model = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
        backing_model = "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"
        other_model = "htdemucs_6s.yaml"
        
        # Determine intermediate filenames based on input to _separate_backing_vocals
        # The input path for backing vocal separation is the output vocals from the clean separation
        clean_vocals_output_path = os.path.join(stems_dir, f"{artist_title} (Vocals {clean_model}).flac")
        # audio-separator appends _(Vocals) or _(Instrumental) to the *input* filename stem (without extension), relative to the input file's directory
        clean_vocals_input_stem = clean_vocals_output_path[:-len(basic_karaoke_gen.lossless_output_format)-1] # Remove .flac
        intermediate_bv_vocals = f"{clean_vocals_input_stem}_(Vocals).{basic_karaoke_gen.lossless_output_format}" 
        intermediate_bv_instrumental = f"{clean_vocals_input_stem}_(Instrumental).{basic_karaoke_gen.lossless_output_format}"

        mock_separator.separate.side_effect = [
            # 1. Clean instrumental model outputs (called by _separate_clean_instrumental)
            # These are output relative to the *input* audio file's directory (temp_dir)
            [
                # These names need the model name appended by the separator convention
                os.path.join(temp_dir, f"{artist_title}_(Vocals)_{clean_model}.{basic_karaoke_gen.lossless_output_format}"), 
                os.path.join(temp_dir, f"{artist_title}_(Instrumental)_{clean_model}.{basic_karaoke_gen.lossless_output_format}") 
            ],
            # 2. Other stems model outputs (called by _separate_other_stems)
            # These are also output relative to the *input* audio file's directory (temp_dir)
            [
                os.path.join(temp_dir, f"{artist_title}_(Piano)_{other_model}.{basic_karaoke_gen.lossless_output_format}"),
                os.path.join(temp_dir, f"{artist_title}_(Guitar)_{other_model}.{basic_karaoke_gen.lossless_output_format}")
            ],
            # 3. Backing vocals model outputs (called by _separate_backing_vocals)
            # These are output relative to the *vocals input* file's directory (stems_dir)
            [
                intermediate_bv_vocals, # e.g., .../stems/Artist - Title (Vocals model_clean)_(Vocals).flac
                intermediate_bv_instrumental # e.g., .../stems/Artist - Title (Vocals model_clean)_(Instrumental).flac
            ]
        ]
        
        # Mock dependencies
        with patch('karaoke_gen.audio_processor.Separator', return_value=mock_separator), \
             patch('os.rename') as mock_rename, \
             patch('os.path.exists', return_value=True), \
             patch('os.getpid', return_value=12345), \
             patch('psutil.pid_exists', return_value=False), \
             patch('datetime.datetime') as mock_datetime, \
             patch('json.dump'), \
             patch('fcntl.flock'), \
             patch('os.remove'), \
             patch('os.system'), \
             patch('builtins.open', mock_open(read_data='{"pid": 123, "start_time": "2023-01-01T11:00:00", "track": "Old Track"}')) as mock_file_open, \
             patch.object(basic_karaoke_gen.audio_processor, '_normalize_audio_files') as mock_normalize_files, \
             patch.object(basic_karaoke_gen.file_handler, '_file_exists') as mock_file_exists:

            # Configure _file_exists side effect: False initially, then True for normalization checks
            # Needs to return False for:
            # 1. clean instrumental path check (_separate_clean_instrumental)
            # 2. clean vocals path check (_separate_clean_instrumental)
            # 3. other stem piano path check (_separate_other_stems loop 1)
            # 4. other stem guitar path check (_separate_other_stems loop 1) - Assuming 1 'other' model
            # 5. lead vocals path check (_separate_backing_vocals)
            # 6. backing vocals path check (_separate_backing_vocals)
            # 7. combined instrumental path check (_generate_combined_instrumentals)
            # Then True for normalization checks:
            # 8. backing lead vocals path check (inner loop in _separate_backing_vocals)
            # 9. backing backing vocals path check (inner loop in _separate_backing_vocals)
            # 10. combined instrumental path check (_generate_combined_instrumentals)
            # Then True for normalization checks:
            # 11. clean instrumental path check (in _normalize_audio_files)
            # 12. combined instrumental path check (in _normalize_audio_files)
            mock_file_exists.side_effect = [False] * 9 + [True] * 2 # Adjusted from 7 to 9 False values

            # Configure the mock datetime object
            mock_datetime.now.return_value = dt.datetime.fromisoformat("2023-01-01T12:00:00")
            mock_datetime.fromisoformat.side_effect = lambda *args, **kwargs: dt.datetime.fromisoformat(*args, **kwargs)
            
            # Call the method
            result = basic_karaoke_gen.audio_processor.process_audio_separation(
                audio_file=audio_file,
                artist_title=artist_title,
                track_output_dir=track_output_dir
            )
            
            # Verify the result structure
            assert "clean_instrumental" in result
            assert "other_stems" in result
            assert "backing_vocals" in result
            assert "combined_instrumentals" in result
            
            # Verify Separator.load_model was called for each model
            assert mock_separator.load_model.call_count == 3
            
            # Verify Separator.separate was called for each separation
            assert mock_separator.separate.call_count == 3
            
            # Verify _normalize_audio_files was called once
            assert mock_normalize_files.call_count == 1
    
    def test_process_audio_separation_with_skip_env_var(self, basic_karaoke_gen, temp_dir):
        """Test process_audio_separation with KARAOKE_GEN_SKIP_AUDIO_SEPARATION environment variable."""
        # Setup
        audio_file = os.path.join(temp_dir, "input.wav")
        with open(audio_file, "w") as f:
            f.write("mock audio content")
        
        artist_title = "Test Artist - Test Title"
        track_output_dir = temp_dir
        
        # Mock environment variable
        with patch.dict('os.environ', {'KARAOKE_GEN_SKIP_AUDIO_SEPARATION': '1'}), \
             patch('fcntl.flock'), \
             patch('psutil.pid_exists', return_value=False), \
             patch('builtins.open', mock_open()) as mock_file_open:
            
            # Call the method
            result = basic_karaoke_gen.audio_processor.process_audio_separation(
                audio_file=audio_file,
                artist_title=artist_title,
                track_output_dir=track_output_dir
            )
            
            # Verify the result structure
            assert "clean_instrumental" in result
            assert "other_stems" in result
            assert "backing_vocals" in result
            assert "combined_instrumentals" in result
            
            # Verify all result sections are empty
            assert result["clean_instrumental"] == {}
            assert result["other_stems"] == {}
            assert result["backing_vocals"] == {}
            assert result["combined_instrumentals"] == {}
    
    def test_normalize_audio(self, basic_karaoke_gen, temp_dir):
        """Test normalizing audio."""
        # Setup
        input_path = os.path.join(temp_dir, "input.flac")
        output_path = os.path.join(temp_dir, "output.flac")
        
        # Create a mock AudioSegment
        mock_audio = MagicMock(spec=AudioSegment)
        mock_audio.max_dBFS = -6.0
        mock_audio.apply_gain.return_value = mock_audio
        mock_audio.rms = 100  # Non-zero RMS
        
        # Mock dependencies
        with patch('pydub.AudioSegment.from_file', return_value=mock_audio):
            # Call the method
            basic_karaoke_gen.audio_processor._normalize_audio(input_path, output_path)
            
            # Verify AudioSegment.from_file was called with correct arguments
            AudioSegment.from_file.assert_called_once_with(input_path, format="flac")
            
            # Verify apply_gain was called with correct arguments (target_level - current_level)
            mock_audio.apply_gain.assert_called_once_with(0.0 - (-6.0))
            
            # Verify export was called with correct arguments
            mock_audio.export.assert_called_once_with(output_path, format="flac")
    
    def test_normalize_audio_silent_result(self, basic_karaoke_gen, temp_dir):
        """Test normalizing audio when the result would be silent."""
        # Setup
        input_path = os.path.join(temp_dir, "input.flac")
        output_path = os.path.join(temp_dir, "output.flac")
        
        # Create a mock AudioSegment
        mock_audio = MagicMock(spec=AudioSegment)
        mock_audio.max_dBFS = -6.0
        
        # First mock has RMS of 0 (silent)
        mock_normalized = MagicMock(spec=AudioSegment)
        mock_normalized.rms = 0
        
        mock_audio.apply_gain.return_value = mock_normalized
        
        # Mock dependencies
        with patch('pydub.AudioSegment.from_file', return_value=mock_audio):
            # Call the method
            basic_karaoke_gen.audio_processor._normalize_audio(input_path, output_path)
            
            # Verify AudioSegment.from_file was called with correct arguments
            AudioSegment.from_file.assert_called_once_with(input_path, format="flac")
            
            # Verify apply_gain was called with correct arguments
            mock_audio.apply_gain.assert_called_once_with(0.0 - (-6.0))
            
            # Verify export was called with the original audio (not the silent one)
            mock_audio.export.assert_called_once_with(output_path, format="flac")
    
    def test_file_exists(self, basic_karaoke_gen):
        """Test the _file_exists helper method."""
        # Test with existing file
        with patch('os.path.isfile', return_value=True):
            assert basic_karaoke_gen.file_handler._file_exists("existing_file.txt") is True
        
        # Test with non-existing file
        with patch('os.path.isfile', return_value=False):
            assert basic_karaoke_gen.file_handler._file_exists("non_existing_file.txt") is False
