import os
import pytest
from unittest.mock import MagicMock, patch, call, DEFAULT
import shutil
from karaoke_gen.karaoke_gen import KaraokePrep
from karaoke_gen.utils import sanitize_filename

class TestLyrics:
    def test_find_best_split_point_with_comma(self, basic_karaoke_gen):
        """Test finding the best split point with a comma near the middle."""
        line = "This is a test line, with a comma near the middle of the sentence"
        split_point = basic_karaoke_gen.lyrics_processor.find_best_split_point(line)
        
        # The split should be after the comma
        assert line[:split_point].strip() == "This is a test line,"
        assert line[split_point:].strip() == "with a comma near the middle of the sentence"
    
    def test_find_best_split_point_with_and(self, basic_karaoke_gen):
        """Test finding the best split point with 'and' near the middle."""
        line = "This is a test line and this is the second part of the sentence"
        split_point = basic_karaoke_gen.lyrics_processor.find_best_split_point(line)
        
        # The split should be after 'and'
        assert line[:split_point].strip() == "This is a test line and"
        assert line[split_point:].strip() == "this is the second part of the sentence"
    
    def test_find_best_split_point_at_middle_word(self, basic_karaoke_gen):
        """Test finding the best split point at the middle word."""
        line = "This is a test line without any good split points"
        split_point = basic_karaoke_gen.lyrics_processor.find_best_split_point(line)

        # The split should be at the middle word
        assert line[:split_point].strip() == "This is a test line"
        assert line[split_point:].strip() == "without any good split points"
    
    def test_find_best_split_point_forced_split(self, basic_karaoke_gen):
        """Test finding the best split point with forced split at max length."""
        # Create a very long line without good split points
        line = "Thisisaverylonglinewithoutanyspacesorpunctuationthatexceedsthemaximumlengthallowedforasingleline"
        split_point = basic_karaoke_gen.lyrics_processor.find_best_split_point(line)
        
        # The split should be at the maximum length (36)
        assert split_point == 36
        assert len(line[:split_point]) == 36
    
    def test_process_line_short(self, basic_karaoke_gen):
        """Test processing a line that's already short enough."""
        line = "This is a short line"
        processed = basic_karaoke_gen.lyrics_processor.process_line(line)
        
        assert processed == [line]
    
    def test_process_line_with_parentheses(self, basic_karaoke_gen):
        """Test processing a line with parentheses."""
        line = "This is a line with (some parenthetical text) that should be split"
        processed = basic_karaoke_gen.lyrics_processor.process_line(line)
        
        assert processed[0] == "This is a line with"
        assert processed[1] == "(some parenthetical text)"
        assert processed[2] == "that should be split"
    
    def test_process_line_with_parentheses_and_comma(self, basic_karaoke_gen):
        """Test processing a line with parentheses followed by a comma."""
        line = "This is a line with (some parenthetical text), that should be split"
        processed = basic_karaoke_gen.lyrics_processor.process_line(line)
        
        assert processed[0] == "This is a line with"
        assert processed[1] == "(some parenthetical text),"
        assert processed[2] == "that should be split"
    
    def test_process_line_long(self, basic_karaoke_gen):
        """Test processing a long line that needs multiple splits."""
        line = "This is a very long line that needs to be split into multiple lines because it exceeds the maximum length allowed for a single line"
        processed = basic_karaoke_gen.lyrics_processor.process_line(line)
        
        # Should be split into multiple lines
        assert len(processed) > 1
        # Each line should be 36 characters or less
        for p in processed:
            assert len(p) <= 36
    
    def test_transcribe_lyrics_existing_files_parent_dir(self, basic_karaoke_gen, temp_dir):
        """Test transcribing lyrics when files already exist in parent directory."""
        # Setup
        track_output_dir = os.path.join(temp_dir, "track")
        os.makedirs(track_output_dir, exist_ok=True)
        
        # Create mock existing files
        artist = "Test Artist"
        title = "Test Title"
        sanitized_artist = sanitize_filename(artist)
        sanitized_title = sanitize_filename(title)
        
        parent_video_path = os.path.join(track_output_dir, f"{sanitized_artist} - {sanitized_title} (With Vocals).mkv")
        parent_lrc_path = os.path.join(track_output_dir, f"{sanitized_artist} - {sanitized_title} (Karaoke).lrc")
        
        # Create the files
        with open(parent_video_path, "w") as f:
            f.write("mock video content")
        with open(parent_lrc_path, "w") as f:
            f.write("mock lrc content")
        
        # Test with mocked os.path.exists
        with patch('os.path.exists', return_value=True):
            # Call the method on the lyrics_processor
            result = basic_karaoke_gen.lyrics_processor.transcribe_lyrics(None, artist, title, track_output_dir)
            
            # Verify
            assert result["lrc_filepath"] == parent_lrc_path
            assert result["ass_filepath"] == parent_video_path
    
    def test_transcribe_lyrics_existing_files_lyrics_dir(self, basic_karaoke_gen, temp_dir):
        """Test transcribing lyrics when files already exist in lyrics directory."""
        # Setup
        track_output_dir = os.path.join(temp_dir, "track")
        lyrics_dir = os.path.join(track_output_dir, "lyrics")
        os.makedirs(lyrics_dir, exist_ok=True)
        
        # Create mock existing files
        artist = "Test Artist"
        title = "Test Title"
        sanitized_artist = sanitize_filename(artist)
        sanitized_title = sanitize_filename(title)
        
        lyrics_video_path = os.path.join(lyrics_dir, f"{sanitized_artist} - {sanitized_title} (With Vocals).mkv")
        lyrics_lrc_path = os.path.join(lyrics_dir, f"{sanitized_artist} - {sanitized_title} (Karaoke).lrc")
        
        parent_video_path = os.path.join(track_output_dir, f"{sanitized_artist} - {sanitized_title} (With Vocals).mkv")
        parent_lrc_path = os.path.join(track_output_dir, f"{sanitized_artist} - {sanitized_title} (Karaoke).lrc")
        
        # Create the files
        with open(lyrics_video_path, "w") as f:
            f.write("mock video content")
        with open(lyrics_lrc_path, "w") as f:
            f.write("mock lrc content")
        
        # Test with mocked os.path.exists and shutil.copy2
        with patch('os.path.exists', side_effect=lambda path: path in [lyrics_video_path, lyrics_lrc_path]), \
             patch('shutil.copy2') as mock_copy2:
             # Call the method on the lyrics_processor
            result = basic_karaoke_gen.lyrics_processor.transcribe_lyrics(None, artist, title, track_output_dir)
            
            # Verify copy2 was called with correct arguments
            mock_copy2.assert_any_call(lyrics_video_path, parent_video_path)
            mock_copy2.assert_any_call(lyrics_lrc_path, parent_lrc_path)
            
            # Verify the correct file paths were returned
            assert result["lrc_filepath"] == parent_lrc_path
            assert result["ass_filepath"] == parent_video_path
    
    def test_transcribe_lyrics_new_transcription(self, basic_karaoke_gen, temp_dir):
        """Test transcribing lyrics with a new transcription."""
        # Setup
        track_output_dir = os.path.join(temp_dir, "track")
        os.makedirs(track_output_dir, exist_ok=True)
        
        artist = "Test Artist"
        title = "Test Title"
        input_audio_wav = os.path.join(temp_dir, "input.wav")
        
        # Create mock input file
        with open(input_audio_wav, "w") as f:
            f.write("mock audio content")
        
        # Mock LyricsTranscriber
        mock_transcriber = MagicMock()
        mock_transcriber_instance = MagicMock()
        mock_transcriber.return_value = mock_transcriber_instance
        
        # Mock transcription results
        mock_results = MagicMock()
        mock_results.lrc_filepath = os.path.join(track_output_dir, "lyrics", "test.lrc")
        mock_results.ass_filepath = os.path.join(track_output_dir, "lyrics", "test.ass")
        mock_results.video_filepath = os.path.join(track_output_dir, "lyrics", "test.mkv")
        mock_results.corrected_txt = os.path.join(track_output_dir, "lyrics", "test.txt")
        mock_results.transcription_corrected = MagicMock()
        mock_results.transcription_corrected.corrected_segments = [
            MagicMock(text="Line 1"),
            MagicMock(text="Line 2")
        ]
        # Add to_dict method for the correction data serialization
        mock_results.transcription_corrected.to_dict.return_value = {
            "corrected_segments": [{"text": "Line 1"}, {"text": "Line 2"}],
            "metadata": {"test": "data"}
        }
        
        mock_transcriber_instance.process.return_value = mock_results
        
        # Mock environment variables
        mock_env = {
            "AUDIOSHAKE_API_TOKEN": "test_token",
            "GENIUS_API_TOKEN": "test_token",
            "SPOTIFY_COOKIE_SP_DC": "test_cookie",
            "RUNPOD_API_KEY": "test_key",
            "WHISPER_RUNPOD_ID": "test_id"
        }
        
        # Create the lyrics directory that the code expects to exist
        lyrics_dir = os.path.join(track_output_dir, "lyrics")
        os.makedirs(lyrics_dir, exist_ok=True)
        
        # Test with mocked dependencies
        with patch('karaoke_gen.lyrics_processor.LyricsTranscriber', mock_transcriber), \
             patch('os.path.exists', return_value=False), \
             patch('shutil.copy2') as mock_copy2, \
             patch('os.getenv', side_effect=lambda key, default=None: mock_env.get(key, default)), \
             patch('karaoke_gen.lyrics_processor.load_dotenv'):
            
            # Call the method on the lyrics_processor
            result = basic_karaoke_gen.lyrics_processor.transcribe_lyrics(input_audio_wav, artist, title, track_output_dir)
            
            # Verify LyricsTranscriber was initialized with correct arguments
            mock_transcriber.assert_called_once()
            call_args = mock_transcriber.call_args[1]
            assert call_args["audio_filepath"] == input_audio_wav
            assert call_args["artist"] == artist
            assert call_args["title"] == title
            
            # Verify process was called
            mock_transcriber_instance.process.assert_called_once()
            
            # Verify the correct file paths were returned
            assert result["lrc_filepath"] == mock_results.lrc_filepath
            assert result["ass_filepath"] == mock_results.ass_filepath
            assert result["corrected_lyrics_text"] == "Line 1\nLine 2"
            assert result["corrected_lyrics_text_filepath"] == mock_results.corrected_txt
    
    def test_backup_existing_outputs(self, basic_karaoke_gen, temp_dir):
        """Test backing up existing outputs."""
        # Setup
        track_output_dir = os.path.join(temp_dir, "track")
        os.makedirs(track_output_dir, exist_ok=True)
        
        artist = "Test Artist"
        title = "Test Title"
        base_name = f"{artist} - {title}"
        
        # Create mock files to backup
        input_audio_wav = os.path.join(track_output_dir, f"{base_name}.wav")
        with_vocals_file = os.path.join(track_output_dir, f"{base_name} (With Vocals).mkv")
        karaoke_file = os.path.join(track_output_dir, f"{base_name} (Karaoke).lrc")
        final_karaoke_file = os.path.join(track_output_dir, f"{base_name} (Final Karaoke).mp4")
        
        # Create lyrics directory and files
        lyrics_dir = os.path.join(track_output_dir, "lyrics")
        os.makedirs(lyrics_dir, exist_ok=True)
        lyrics_file = os.path.join(lyrics_dir, "test.lrc")
        
        # Create the files
        for file_path in [input_audio_wav, with_vocals_file, karaoke_file, final_karaoke_file, lyrics_file]:
            with open(file_path, "w") as f:
                f.write(f"mock content for {os.path.basename(file_path)}")
        
        # Test with mocked shutil functions
        with patch('shutil.move') as mock_move, \
             patch('shutil.copytree') as mock_copytree, \
             patch('shutil.rmtree') as mock_rmtree:
            
            result = basic_karaoke_gen.file_handler.backup_existing_outputs(track_output_dir, artist, title)
            
            # Verify the correct input audio file was returned
            assert result == input_audio_wav
            
            # Verify version directory was created
            version_dir = os.path.join(track_output_dir, "version-1")
            
        # Verify files were moved to version directory
        if not basic_karaoke_gen.dry_run:
            mock_move.assert_any_call(with_vocals_file, os.path.join(version_dir, os.path.basename(with_vocals_file)))
            mock_move.assert_any_call(karaoke_file, os.path.join(version_dir, os.path.basename(karaoke_file)))
            mock_move.assert_any_call(final_karaoke_file, os.path.join(version_dir, os.path.basename(final_karaoke_file)))
            
            # Verify lyrics directory was copied and removed
            mock_copytree.assert_called_once_with(lyrics_dir, os.path.join(version_dir, "lyrics"))
            mock_rmtree.assert_called_once_with(lyrics_dir)
    
    def test_backup_existing_outputs_no_input_audio(self, basic_karaoke_gen, temp_dir):
        """Test backing up existing outputs when input audio file is not found."""
        # Setup
        track_output_dir = os.path.join(temp_dir, "track")
        os.makedirs(track_output_dir, exist_ok=True)
        
        artist = "Test Artist"
        title = "Test Title"
        
        # Create an alternative WAV file
        alt_wav_file = os.path.join(track_output_dir, "alternative.wav")
        with open(alt_wav_file, "w") as f:
            f.write("mock audio content")
        
        # Test with mocked glob.glob
        with patch('glob.glob', return_value=[alt_wav_file]), \
             patch('shutil.move'), \
             patch('shutil.copytree'), \
             patch('shutil.rmtree'), \
             patch('os.path.exists', return_value=False):
            
            result = basic_karaoke_gen.file_handler.backup_existing_outputs(track_output_dir, artist, title)
            
            # Verify the alternative WAV file was returned
            assert result == alt_wav_file
    
    def test_backup_existing_outputs_no_wav_files(self, basic_karaoke_gen, temp_dir):
        """Test backing up existing outputs when no WAV files are found."""
        # Setup
        track_output_dir = os.path.join(temp_dir, "track")
        os.makedirs(track_output_dir, exist_ok=True)
        
        artist = "Test Artist"
        title = "Test Title"
        
        # Test with mocked glob.glob and os.path.exists
        with patch('glob.glob', return_value=[]), \
             patch('os.path.exists', return_value=False):
            
            with pytest.raises(Exception, match=f"No input audio file found in {track_output_dir}"):
                basic_karaoke_gen.file_handler.backup_existing_outputs(track_output_dir, artist, title)


class TestTranscriptionProviderValidation:
    """Tests for transcription provider validation and error messaging."""
    
    def test_check_transcription_providers_audioshake_configured(self, basic_karaoke_gen):
        """Test provider check when AudioShake is configured."""
        with patch.dict(os.environ, {"AUDIOSHAKE_API_TOKEN": "test_token"}, clear=False), \
             patch('karaoke_gen.lyrics_processor.load_dotenv'):
            result = basic_karaoke_gen.lyrics_processor._check_transcription_providers()
            assert "AudioShake" in result["configured"]
    
    def test_check_transcription_providers_whisper_configured(self, basic_karaoke_gen):
        """Test provider check when Whisper is configured."""
        with patch.dict(os.environ, {"RUNPOD_API_KEY": "test_key", "WHISPER_RUNPOD_ID": "test_id"}, clear=False), \
             patch('karaoke_gen.lyrics_processor.load_dotenv'):
            result = basic_karaoke_gen.lyrics_processor._check_transcription_providers()
            assert "Whisper (RunPod)" in result["configured"]
    
    def test_check_transcription_providers_both_configured(self, basic_karaoke_gen):
        """Test provider check when both providers are configured."""
        with patch.dict(os.environ, {
            "AUDIOSHAKE_API_TOKEN": "test_token",
            "RUNPOD_API_KEY": "test_key",
            "WHISPER_RUNPOD_ID": "test_id"
        }, clear=False), \
             patch('karaoke_gen.lyrics_processor.load_dotenv'):
            result = basic_karaoke_gen.lyrics_processor._check_transcription_providers()
            assert "AudioShake" in result["configured"]
            assert "Whisper (RunPod)" in result["configured"]
    
    def test_check_transcription_providers_none_configured(self, basic_karaoke_gen):
        """Test provider check when no providers are configured."""
        # Mock whisper_timestamped import to simulate it not being installed
        def mock_import(name, *args, **kwargs):
            if name == 'whisper_timestamped':
                raise ImportError("No module named 'whisper_timestamped'")
            return original_import(name, *args, **kwargs)

        import builtins
        original_import = builtins.__import__

        with patch.dict(os.environ, {}, clear=True), \
             patch('karaoke_gen.lyrics_processor.load_dotenv'), \
             patch.object(builtins, '__import__', side_effect=mock_import):
            result = basic_karaoke_gen.lyrics_processor._check_transcription_providers()
            assert len(result["configured"]) == 0
            assert len(result["missing"]) > 0
    
    def test_check_transcription_providers_whisper_partial_key_only(self, basic_karaoke_gen):
        """Test provider check when only RunPod API key is set."""
        with patch.dict(os.environ, {"RUNPOD_API_KEY": "test_key"}, clear=True), \
             patch('karaoke_gen.lyrics_processor.load_dotenv'):
            result = basic_karaoke_gen.lyrics_processor._check_transcription_providers()
            assert "Whisper (RunPod)" not in result["configured"]
            # Should show partial configuration message
            assert any("WHISPER_RUNPOD_ID" in msg for msg in result["missing"])
    
    def test_check_transcription_providers_whisper_partial_id_only(self, basic_karaoke_gen):
        """Test provider check when only Whisper RunPod ID is set."""
        with patch.dict(os.environ, {"WHISPER_RUNPOD_ID": "test_id"}, clear=True), \
             patch('karaoke_gen.lyrics_processor.load_dotenv'):
            result = basic_karaoke_gen.lyrics_processor._check_transcription_providers()
            assert "Whisper (RunPod)" not in result["configured"]
            # Should show partial configuration message
            assert any("RUNPOD_API_KEY" in msg for msg in result["missing"])
    
    def test_build_transcription_provider_error_message(self, basic_karaoke_gen):
        """Test error message building."""
        missing = ["AudioShake (AUDIOSHAKE_API_TOKEN)", "Whisper (RUNPOD_API_KEY + WHISPER_RUNPOD_ID)"]
        error_msg = basic_karaoke_gen.lyrics_processor._build_transcription_provider_error_message(missing)
        
        # Check key elements are in the message
        assert "No transcription providers configured" in error_msg
        assert "AudioShake" in error_msg
        assert "Whisper" in error_msg
        assert "AUDIOSHAKE_API_TOKEN" in error_msg
        assert "RUNPOD_API_KEY" in error_msg
        assert "--skip-lyrics" in error_msg
        assert "README.md" in error_msg
    
    def test_transcribe_lyrics_raises_when_no_providers(self, basic_karaoke_gen, temp_dir):
        """Test that transcribe_lyrics raises ValueError when no providers configured."""
        track_output_dir = os.path.join(temp_dir, "track")
        os.makedirs(track_output_dir, exist_ok=True)

        # Make sure skip_transcription is False
        basic_karaoke_gen.lyrics_processor.skip_transcription = False

        # Mock whisper_timestamped import to simulate it not being installed
        def mock_import(name, *args, **kwargs):
            if name == 'whisper_timestamped':
                raise ImportError("No module named 'whisper_timestamped'")
            return original_import(name, *args, **kwargs)

        import builtins
        original_import = builtins.__import__

        with patch.dict(os.environ, {}, clear=True), \
             patch('os.path.exists', return_value=False), \
             patch('karaoke_gen.lyrics_processor.load_dotenv'), \
             patch.object(builtins, '__import__', side_effect=mock_import):

            with pytest.raises(ValueError, match="No transcription providers configured"):
                basic_karaoke_gen.lyrics_processor.transcribe_lyrics(
                    "/path/to/audio.wav", "Artist", "Title", track_output_dir
                )
    
    def test_transcribe_lyrics_skips_validation_when_files_exist(self, basic_karaoke_gen, temp_dir):
        """Test that existing files bypass provider validation."""
        track_output_dir = os.path.join(temp_dir, "track")
        os.makedirs(track_output_dir, exist_ok=True)
        
        artist = "Test Artist"
        title = "Test Title"
        sanitized_artist = sanitize_filename(artist)
        sanitized_title = sanitize_filename(title)
        
        # Create existing files
        parent_video_path = os.path.join(track_output_dir, f"{sanitized_artist} - {sanitized_title} (With Vocals).mkv")
        parent_lrc_path = os.path.join(track_output_dir, f"{sanitized_artist} - {sanitized_title} (Karaoke).lrc")
        
        with open(parent_video_path, "w") as f:
            f.write("mock video")
        with open(parent_lrc_path, "w") as f:
            f.write("[00:01.00]Test lyrics")
        
        # No providers configured, but existing files should work
        with patch.dict(os.environ, {}, clear=True), \
             patch('karaoke_gen.lyrics_processor.load_dotenv'):
            
            # Should NOT raise - existing files bypass validation
            result = basic_karaoke_gen.lyrics_processor.transcribe_lyrics(
                "/path/to/audio.wav", artist, title, track_output_dir
            )
            
            assert result["lrc_filepath"] == parent_lrc_path
            assert result["ass_filepath"] == parent_video_path
    
    def test_transcribe_lyrics_skips_validation_when_transcription_disabled(self, basic_karaoke_gen, temp_dir):
        """Test that provider validation is skipped when skip_transcription=True."""
        track_output_dir = os.path.join(temp_dir, "track")
        os.makedirs(track_output_dir, exist_ok=True)
        
        # Set skip_transcription to True
        basic_karaoke_gen.lyrics_processor.skip_transcription = True
        
        # Mock the LyricsTranscriber
        mock_result = MagicMock()
        mock_result.lrc_filepath = "/path/to/output.lrc"
        mock_result.ass_filepath = None
        mock_result.video_filepath = None
        mock_result.transcription_corrected = None
        
        with patch.dict(os.environ, {}, clear=True), \
             patch('os.path.exists', return_value=False), \
             patch('os.makedirs'), \
             patch('shutil.copy2'), \
             patch('karaoke_gen.lyrics_processor.load_dotenv'), \
             patch('karaoke_gen.lyrics_processor.LyricsTranscriber') as mock_transcriber:
            
            mock_instance = MagicMock()
            mock_instance.process.return_value = mock_result
            mock_transcriber.return_value = mock_instance
            
            # Should NOT raise - skip_transcription bypasses validation
            result = basic_karaoke_gen.lyrics_processor.transcribe_lyrics(
                "/path/to/audio.wav", "Artist", "Title", track_output_dir
            )
            
            # Verify transcriber was called
            mock_transcriber.assert_called_once()
