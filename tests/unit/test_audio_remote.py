import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock, call
import logging

from karaoke_gen.audio_processor import AudioProcessor


class TestRemoteAudioSeparation(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG)
        
        # Create a temporary directory for testing
        self.temp_dir = tempfile.mkdtemp()
        
        # Initialize AudioProcessor with test parameters
        self.audio_processor = AudioProcessor(
            logger=self.logger,
            log_level=logging.DEBUG,
            log_formatter=None,
            model_file_dir="/tmp/test-models",
            lossless_output_format="FLAC",
            clean_instrumental_model="model_bs_roformer_ep_317_sdr_12.9755.ckpt",
            backing_vocals_models=["mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"],
            other_stems_models=["htdemucs_6s.yaml"],
            ffmpeg_base_command="ffmpeg"
        )

    def tearDown(self):
        # Clean up temporary directory
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch.dict(os.environ, {'AUDIO_SEPARATOR_API_URL': 'https://test-api.com'})
    @patch('karaoke_gen.audio_processor.REMOTE_API_AVAILABLE', True)
    @patch('karaoke_gen.audio_processor.AudioSeparatorAPIClient')
    def test_remote_api_detection(self, mock_client_class):
        """Test that remote API is detected and two-stage separation works with custom_output_names."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        # Create stems dir and expected output files
        stems_dir = os.path.join(self.temp_dir, "stems")
        os.makedirs(stems_dir, exist_ok=True)

        # File prefix uses artist_title (no job_id set on this processor)
        prefix = "Test Artist - Test Song"
        fmt = "flac"
        vocals_path = os.path.join(stems_dir, f"{prefix}_mixed_vocals.{fmt}")
        instrumental_path = os.path.join(stems_dir, f"{prefix}_mixed_instrumental.{fmt}")
        lead_vocals_path = os.path.join(stems_dir, f"{prefix}_lead_vocals.{fmt}")
        backing_vocals_path = os.path.join(stems_dir, f"{prefix}_backing_vocals.{fmt}")

        # Create fake files that the API would "download"
        for path in [vocals_path, instrumental_path]:
            with open(path, "w") as f:
                f.write("fake")

        # Stage 1 response
        stage1_response = {
            "status": "completed",
            "downloaded_files": [vocals_path, instrumental_path],
        }
        # Stage 2 response — create the files too
        for path in [lead_vocals_path, backing_vocals_path]:
            with open(path, "w") as f:
                f.write("fake")
        stage2_response = {
            "status": "completed",
            "downloaded_files": [lead_vocals_path, backing_vocals_path],
        }

        mock_client.separate_audio_and_wait.side_effect = [stage1_response, stage2_response]

        # Create a fake input file
        input_file = os.path.join(self.temp_dir, "test_audio.wav")
        with open(input_file, "w") as f:
            f.write("fake input")

        with patch.object(self.audio_processor, '_generate_combined_instrumentals', return_value={}), \
             patch.object(self.audio_processor, '_normalize_audio_files'):
            result = self.audio_processor.process_audio_separation(
                input_file,
                "Test Artist - Test Song",
                self.temp_dir,
            )

        # Verify remote API was called twice (stage 1 and stage 2)
        self.assertTrue(mock_client_class.called)
        self.assertEqual(mock_client.separate_audio_and_wait.call_count, 2)

        # Verify stage 1 used custom_output_names
        stage1_kwargs = mock_client.separate_audio_and_wait.call_args_list[0][1]
        self.assertIn("custom_output_names", stage1_kwargs)
        self.assertIn("mixed_vocals", stage1_kwargs["custom_output_names"]["Vocals"])
        self.assertIn("mixed_instrumental", stage1_kwargs["custom_output_names"]["Instrumental"])

        # Verify stage 2 used custom_output_names
        stage2_kwargs = mock_client.separate_audio_and_wait.call_args_list[1][1]
        self.assertIn("custom_output_names", stage2_kwargs)
        self.assertIn("lead_vocals", stage2_kwargs["custom_output_names"]["Vocals"])
        self.assertIn("backing_vocals", stage2_kwargs["custom_output_names"]["Instrumental"])

    @patch.dict(os.environ, {}, clear=True)  # Clear AUDIO_SEPARATOR_API_URL
    def test_local_fallback_when_no_env_var(self):
        """Test that local processing is used when environment variable is not set."""
        with patch('karaoke_gen.audio_processor.Separator') as mock_separator_class:
            mock_separator = MagicMock()
            mock_separator_class.return_value = mock_separator
            mock_separator.separate.return_value = []
            
            # Mock the locking mechanism and file operations
            mock_open = MagicMock()
            with patch('tempfile.gettempdir', return_value='/tmp'), \
                 patch('fcntl.flock'), \
                 patch('os.getpid', return_value=12345), \
                 patch('os.path.exists', return_value=False), \
                 patch('builtins.open', mock_open), \
                 patch('json.dump'), \
                 patch('os.makedirs'), \
                 patch.object(self.audio_processor, '_generate_combined_instrumentals', return_value={"mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt": "/tmp/combined.flac"}), \
                 patch.object(self.audio_processor, '_normalize_audio_files'), \
                 patch.object(self.audio_processor, '_separate_clean_instrumental', return_value={"vocals": "/tmp/vocals.flac", "instrumental": "/tmp/instrumental.flac"}), \
                 patch.object(self.audio_processor, '_separate_other_stems', return_value={}), \
                 patch.object(self.audio_processor, '_separate_backing_vocals', return_value={"mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt": {"lead_vocals": "/tmp/lead.flac", "backing_vocals": "/tmp/backing.flac"}}):
                
                result = self.audio_processor.process_audio_separation(
                    "/tmp/test_audio.wav", 
                    "Test Artist - Test Song", 
                    self.temp_dir
                )
            
            # Verify local Separator was used
            mock_separator_class.assert_called()

    @patch.dict(os.environ, {'AUDIO_SEPARATOR_API_URL': 'https://test-api.com'})
    @patch('karaoke_gen.audio_processor.REMOTE_API_AVAILABLE', False)
    def test_local_fallback_when_remote_unavailable(self):
        """Test that local processing is used when remote API is unavailable."""
        with patch('karaoke_gen.audio_processor.Separator') as mock_separator_class:
            mock_separator = MagicMock()
            mock_separator_class.return_value = mock_separator
            mock_separator.separate.return_value = []
            
            # Mock the locking mechanism and file operations
            mock_open = MagicMock()
            with patch('tempfile.gettempdir', return_value='/tmp'), \
                 patch('fcntl.flock'), \
                 patch('os.getpid', return_value=12345), \
                 patch('os.path.exists', return_value=False), \
                 patch('builtins.open', mock_open), \
                 patch('json.dump'), \
                 patch('os.makedirs'), \
                 patch.object(self.audio_processor, '_generate_combined_instrumentals', return_value={"mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt": "/tmp/combined.flac"}), \
                 patch.object(self.audio_processor, '_normalize_audio_files'), \
                 patch.object(self.audio_processor, '_separate_clean_instrumental', return_value={"vocals": "/tmp/vocals.flac", "instrumental": "/tmp/instrumental.flac"}), \
                 patch.object(self.audio_processor, '_separate_other_stems', return_value={}), \
                 patch.object(self.audio_processor, '_separate_backing_vocals', return_value={"mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt": {"lead_vocals": "/tmp/lead.flac", "backing_vocals": "/tmp/backing.flac"}}):
                
                result = self.audio_processor.process_audio_separation(
                    "/tmp/test_audio.wav", 
                    "Test Artist - Test Song", 
                    self.temp_dir
                )
            
            # Verify local Separator was used as fallback
            mock_separator_class.assert_called()

    @patch.dict(os.environ, {'AUDIO_SEPARATOR_API_URL': 'https://test-api.com'})
    @patch('karaoke_gen.audio_processor.REMOTE_API_AVAILABLE', True)
    @patch('karaoke_gen.audio_processor.AudioSeparatorAPIClient')
    def test_fallback_on_remote_error(self, mock_client_class):
        """Test that local processing is used when remote API fails."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        # Mock API failure
        mock_client.separate_audio_and_wait.side_effect = Exception("API Error")
        
        with patch('karaoke_gen.audio_processor.Separator') as mock_separator_class:
            mock_separator = MagicMock()
            mock_separator_class.return_value = mock_separator
            mock_separator.separate.return_value = []
            
            # Mock the locking mechanism and file operations
            mock_open = MagicMock()
            with patch('tempfile.gettempdir', return_value='/tmp'), \
                 patch('fcntl.flock'), \
                 patch('os.getpid', return_value=12345), \
                 patch('os.path.exists', return_value=False), \
                 patch('builtins.open', mock_open), \
                 patch('json.dump'), \
                 patch('os.makedirs'), \
                 patch.object(self.audio_processor, '_generate_combined_instrumentals', return_value={"mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt": "/tmp/combined.flac"}), \
                 patch.object(self.audio_processor, '_normalize_audio_files'), \
                 patch.object(self.audio_processor, '_separate_clean_instrumental', return_value={"vocals": "/tmp/vocals.flac", "instrumental": "/tmp/instrumental.flac"}), \
                 patch.object(self.audio_processor, '_separate_other_stems', return_value={}), \
                 patch.object(self.audio_processor, '_separate_backing_vocals', return_value={"mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt": {"lead_vocals": "/tmp/lead.flac", "backing_vocals": "/tmp/backing.flac"}}):
                
                result = self.audio_processor.process_audio_separation(
                    "/tmp/test_audio.wav", 
                    "Test Artist - Test Song", 
                    self.temp_dir
                )
        
        # Verify remote API was attempted first
        mock_client.separate_audio_and_wait.assert_called_once()
        
        # Verify local Separator was used as fallback
        mock_separator_class.assert_called()

    def test_organize_remote_separation_results(self):
        """Test the organization of downloaded files from remote API."""
        # Test stage 1 organization (clean instrumental + other stems)
        stage1_files = [
            "/tmp/test_audio_(Vocals)_model_bs_roformer_ep_317_sdr_12.9755.flac",
            "/tmp/test_audio_(Instrumental)_model_bs_roformer_ep_317_sdr_12.9755.flac",
            "/tmp/test_audio_(Drums)_htdemucs_6s.flac",
            "/tmp/test_audio_(Bass)_htdemucs_6s.flac",
            "/tmp/test_audio_(Other)_htdemucs_6s.flac"
        ]
        
        artist_title = "Test Artist - Test Song"
        stems_dir = os.path.join(self.temp_dir, "stems")
        
        with patch('shutil.move') as mock_move, \
             patch('os.makedirs'):
            
            result = self.audio_processor._organize_stage1_remote_results(
                stage1_files, artist_title, self.temp_dir, stems_dir
            )
        
        # Verify stage 1 file organization
        self.assertIn("clean_instrumental", result)
        self.assertIn("other_stems", result)
        
        # Verify that files were moved for both clean instrumental and other stems
        self.assertTrue(mock_move.called)
        # Should have moved: vocals, instrumental, drums, bass, other (5 files)
        self.assertEqual(mock_move.call_count, 5)
        
        # Test stage 2 organization (backing vocals)
        stage2_files = [
            "/tmp/test_vocals_(Vocals)_mel_band_roformer_karaoke_aufr33_viperx_sdr_10.flac",
            "/tmp/test_vocals_(Instrumental)_mel_band_roformer_karaoke_aufr33_viperx_sdr_10.flac"
        ]
        
        with patch('shutil.move') as mock_move_stage2:
            
            backing_vocals_result = self.audio_processor._organize_stage2_remote_results(
                stage2_files, artist_title, stems_dir
            )
        
        # Verify stage 2 file organization
        self.assertIn("mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt", backing_vocals_result)
        
        # Verify that files were moved for backing vocals (lead vocals + backing vocals = 2 files)
        self.assertTrue(mock_move_stage2.called)
        self.assertEqual(mock_move_stage2.call_count, 2)


    @patch.dict(os.environ, {'AUDIO_SEPARATOR_API_URL': 'https://test-api.com'})
    @patch('karaoke_gen.audio_processor.REMOTE_API_AVAILABLE', True)
    @patch('karaoke_gen.audio_processor.AudioSeparatorAPIClient')
    def test_remote_api_download_failure(self, mock_client_class):
        """Test that download failures cause clear exceptions."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        # Mock Stage 1 success but Stage 2 download failure (like the user's issue)
        mock_client.separate_audio_and_wait.side_effect = [
            # Stage 1: Success with downloads
            {
                "status": "completed",
                "downloaded_files": [
                    "/tmp/test_file_(Vocals)_model_bs_roformer_ep_317_sdr_12.9755.flac",
                    "/tmp/test_file_(Instrumental)_model_bs_roformer_ep_317_sdr_12.9755.flac",
                    "/tmp/test_file_(Drums)_htdemucs_6s.flac",
                ]
            },
            # Stage 2: Success but no downloads (like the 404 issue)
            {
                "status": "completed", 
                "downloaded_files": []  # No files downloaded due to 404s
            }
        ]
        
        # Mock file operations
        with patch('os.path.exists', return_value=True), \
             patch('shutil.move'), \
             patch('os.makedirs'):
            
            # This should raise an exception due to Stage 2 download failure
            with self.assertRaises(Exception) as context:
                self.audio_processor.process_audio_separation(
                    "/tmp/test_audio.wav", 
                    "Test Artist - Test Song", 
                    self.temp_dir
                )
        
        # Verify the exception message is clear and helpful
        error_message = str(context.exception)
        self.assertIn("Stage 2 completed but no files were downloaded", error_message)


    @patch.dict(os.environ, {'AUDIO_SEPARATOR_API_URL': 'https://test-api.com'})
    @patch('karaoke_gen.audio_processor.REMOTE_API_AVAILABLE', True)
    @patch('karaoke_gen.audio_processor.AudioSeparatorAPIClient')
    def test_stage1_download_failure(self, mock_client_class):
        """Test that Stage 1 download failures cause clear exceptions."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        # Mock Stage 1 job success but no downloads
        mock_client.separate_audio_and_wait.return_value = {
            "status": "completed",
            "downloaded_files": []  # No files downloaded
        }
        
        # Mock file operations  
        with patch('os.path.exists', return_value=True), \
             patch('os.makedirs'):
            
            # This should raise an exception due to Stage 1 download failure
            with self.assertRaises(Exception) as context:
                self.audio_processor.process_audio_separation(
                    "/tmp/test_audio.wav", 
                    "Test Artist - Test Song", 
                    self.temp_dir
                )
        
        # Verify the exception message is clear and helpful
        error_message = str(context.exception)
        self.assertIn("Stage 1 completed but no files were downloaded", error_message)


if __name__ == '__main__':
    unittest.main() 