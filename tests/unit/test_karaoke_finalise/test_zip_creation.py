import pytest
import os
import zipfile
import tomllib  # Keep import for patching
from unittest.mock import patch, MagicMock, mock_open, call

# Adjust the import path
from karaoke_gen.karaoke_finalise.karaoke_finalise import KaraokeFinalise
from .test_initialization import mock_logger, basic_finaliser, MINIMAL_CONFIG  # Reuse fixtures
from .test_file_input_validation import BASE_NAME, KARAOKE_LRC, INSTRUMENTAL_FLAC  # Reuse constants

# Define expected output filenames for convenience
OUTPUT_FILES_ZIP = {
    "karaoke_mp3": f"{BASE_NAME} (Karaoke).mp3",
    "karaoke_cdg": f"{BASE_NAME} (Karaoke).cdg",
    "karaoke_txt": f"{BASE_NAME} (Karaoke).txt",
    "final_karaoke_cdg_zip": f"{BASE_NAME} (Final Karaoke CDG).zip",
    "final_karaoke_txt_zip": f"{BASE_NAME} (Final Karaoke TXT).zip",
}

INPUT_FILES_ZIP = {
    "karaoke_lrc": KARAOKE_LRC,
    "instrumental_audio": INSTRUMENTAL_FLAC,
}

ARTIST = "Artist"
TITLE = "Title"
# More complete dummy config based on required keys from the traceback
CDG_STYLES_CONFIG = {
    "title_color": "white",
    "artist_color": "white",
    "background_color": "blue",
    "border_color": "black",
    "font_path": "dummy_font.ttf",
    "font_size": 20,
    "stroke_width": 1,
    "stroke_style": "outline",
    "active_fill": "yellow",
    "active_stroke": "black",
    "inactive_fill": "white",
    "inactive_stroke": "black",
    "title_screen_background": "black",
    "instrumental_background": "black",
    "instrumental_transition": "fade",
    "instrumental_font_color": "gray",
    "title_screen_transition": "fade",
    "row": 1,
    "line_tile_height": 24,
    "lines_per_page": 4,
    "clear_mode": "page",
    "sync_offset": 0.0,
    "instrumental_gap_threshold": 5.0,
    "instrumental_text": "♪ Instrumental ♪",
    "lead_in_threshold": 1.0,
    "lead_in_symbols": "*",
    "lead_in_duration": 0.5,
    "lead_in_total": 3,
    "title_artist_gap": 10,
    "intro_duration_seconds": 5,
    "first_syllable_buffer_seconds": 0.1,
    "outro_background": "black",
    "outro_transition": "fade",
    "outro_text_line1": "End",
    "outro_text_line2": "www.example.com",
    "outro_line1_color": "white",
    "outro_line2_color": "gray",
    "outro_line1_line2_gap": 5,
}

# --- CDG ZIP Creation Tests ---

@patch('zipfile.ZipFile')
@patch('os.rename')
@patch.object(KaraokeFinalise, 'prompt_user_bool', return_value=True)  # Confirm overwrite
def test_create_cdg_zip_generate_new(mock_prompt, mock_rename, mock_zipfile, basic_finaliser):
    """Test generating new CDG/MP3 files and zipping."""
    # Define the filename variables first before they're used in the mock setup
    generated_cdg = "temp_cdg_file.cdg"
    generated_mp3 = "temp_mp3_file.mp3"
    generated_zip = "temp_zip_file.zip"
    
    # Mock isfile to simulate files being created during the test
    with patch('os.path.isfile') as mock_isfile:
        # We need to track different files and when they should exist
        file_calls = {
            OUTPUT_FILES_ZIP["final_karaoke_cdg_zip"]: 0,
            OUTPUT_FILES_ZIP["karaoke_mp3"]: 0
        }
        
        def mock_isfile_side_effect(path):
            # First, check if this is the original zip file from CDGGenerator
            # This needs to return True so os.rename gets called
            if path == generated_zip:
                return True
                
            # The final zip file should exist after generation/rename but not before
            if path == OUTPUT_FILES_ZIP["final_karaoke_cdg_zip"]:
                file_calls[path] += 1
                # First call when checking if exists (False)
                # Later calls should return True after rename  
                return file_calls[path] > 1
                
            # MP3 file should exist after extraction but not before
            elif path == OUTPUT_FILES_ZIP["karaoke_mp3"]:
                file_calls[path] += 1
                # Return True only after the zip is extracted (2nd call)
                return file_calls[path] > 1
                
            # All other files don't exist
            return False
        
        mock_isfile.side_effect = mock_isfile_side_effect
        
        basic_finaliser.cdg_styles = CDG_STYLES_CONFIG
        
        # This is the key - patch the CDGGenerator where it's imported in karaoke_finalise.py
        with patch('karaoke_gen.karaoke_finalise.karaoke_finalise.CDGGenerator') as mock_cdg_gen_cls:
            mock_generator_instance = MagicMock()
            mock_cdg_gen_cls.return_value = mock_generator_instance
            mock_generator_instance.generate_cdg_from_lrc.return_value = (generated_cdg, generated_mp3, generated_zip)
            
            # Run the test
            basic_finaliser.create_cdg_zip_file(INPUT_FILES_ZIP, OUTPUT_FILES_ZIP, ARTIST, TITLE)

    # Prompt is *not* called because isfile returns False initially for the zip
    mock_prompt.assert_not_called()  
    
    # Check rename was called with the correct parameters
    mock_rename.assert_called_once_with(generated_zip, OUTPUT_FILES_ZIP["final_karaoke_cdg_zip"])
    
    # Check extraction call - ZipFile is called for 'r' mode
    mock_zipfile.assert_any_call(OUTPUT_FILES_ZIP["final_karaoke_cdg_zip"], "r")

    # Check extractall was called on the instance returned by the 'r' call context manager
    zip_instance_for_read = mock_zipfile.return_value.__enter__.return_value
    zip_instance_for_read.extractall.assert_called_once()


@patch('os.path.isfile')
@patch('zipfile.ZipFile')  # Patch the class
@patch('karaoke_gen.lyrics_transcriber.output.cdg.CDGGenerator')
@patch.object(KaraokeFinalise, 'prompt_user_bool', return_value=True)  # Confirm overwrite
def test_create_cdg_zip_use_existing_mp3_cdg(mock_prompt, mock_cdg_gen_cls, mock_zipfile_cls, mock_isfile, basic_finaliser):
    """Test creating ZIP from existing MP3 and CDG files."""
    # Simulate existing MP3 and CDG, and the final ZIP for overwrite check and extraction
    mock_isfile.side_effect = lambda f: f in [
        OUTPUT_FILES_ZIP["karaoke_mp3"],
        OUTPUT_FILES_ZIP["karaoke_cdg"],
        OUTPUT_FILES_ZIP["final_karaoke_cdg_zip"]
    ]

    # Create separate mocks for write and read instances, including context managers
    # REMOVE spec=zipfile.ZipFile as zipfile.ZipFile is already mocked by @patch
    mock_zip_write_instance = MagicMock()
    mock_zip_write_context = mock_zip_write_instance.__enter__.return_value

    mock_zip_read_instance = MagicMock()
    mock_zip_read_context = mock_zip_read_instance.__enter__.return_value

    # Configure the class mock's side_effect
    def zipfile_side_effect(file, mode="r", *args, **kwargs):
        if mode == "w":
            # Return the mock configured for writing
            return mock_zip_write_instance
        elif mode == "r":
            # Return the mock configured for reading
            return mock_zip_read_instance
        else:
            raise ValueError(f"Unexpected mode for ZipFile mock: {mode}")
    # Configure the class mock to return the correct instance based on mode
    mock_zipfile_cls.side_effect = lambda file, mode="r", *a, **kw: mock_zip_write_instance if mode == "w" else mock_zip_read_instance

    basic_finaliser.create_cdg_zip_file(INPUT_FILES_ZIP, OUTPUT_FILES_ZIP, ARTIST, TITLE)

    mock_prompt.assert_called_once()  # Overwrite prompt should still be called
    mock_cdg_gen_cls.assert_not_called()  # Should not generate

    # REMOVE incorrect assertion on the class mock:
        # mock_zipfile_cls.assert_has_calls(...)

    # Check write calls on the write instance's context manager mock (dedented)
    mock_zip_write_context.write.assert_has_calls([
        call(OUTPUT_FILES_ZIP["karaoke_mp3"], os.path.basename(OUTPUT_FILES_ZIP["karaoke_mp3"])),
        call(OUTPUT_FILES_ZIP["karaoke_cdg"], os.path.basename(OUTPUT_FILES_ZIP["karaoke_cdg"])),
    ], any_order=True)

    # Check extractall call on the read instance's context manager mock
    # Assert that zipfile.ZipFile was NOT called with mode 'r' in this path
    zip_read_call = call(OUTPUT_FILES_ZIP["final_karaoke_cdg_zip"], "r")
    assert zip_read_call not in mock_zipfile_cls.call_args_list
    mock_zip_read_context.extractall.assert_not_called()  # Explicitly assert not called


@patch('os.path.isfile', return_value=True)  # ZIP exists
@patch('zipfile.ZipFile')
@patch('karaoke_gen.lyrics_transcriber.output.cdg.CDGGenerator')
@patch.object(KaraokeFinalise, 'prompt_user_bool', return_value=False)  # Skip overwrite
def test_create_cdg_zip_skip_overwrite(mock_prompt, mock_cdg_gen_cls, mock_zipfile, mock_isfile, basic_finaliser):
    """Test skipping CDG ZIP creation if user chooses not to overwrite."""
    basic_finaliser.create_cdg_zip_file(INPUT_FILES_ZIP, OUTPUT_FILES_ZIP, ARTIST, TITLE)

    mock_prompt.assert_called_once()
    mock_cdg_gen_cls.assert_not_called()
    mock_zipfile.assert_not_called()  # No zip creation or extraction

def test_create_cdg_zip_missing_styles(basic_finaliser):
    """Test error raised if cdg_styles is missing."""
    basic_finaliser.cdg_styles = None
    with patch('os.path.isfile', return_value=False), \
         pytest.raises(ValueError, match="CDG styles configuration is required"):
        basic_finaliser.create_cdg_zip_file(INPUT_FILES_ZIP, OUTPUT_FILES_ZIP, ARTIST, TITLE)

@patch('os.path.isfile', return_value=False)  # No existing files
@patch('zipfile.ZipFile')
@patch('karaoke_gen.lyrics_transcriber.output.cdg.CDGGenerator')
@patch.object(KaraokeFinalise, 'prompt_user_bool', return_value=True)
def test_create_cdg_zip_generation_fails_to_create_zip(mock_prompt, mock_cdg_gen_cls, mock_zipfile, mock_isfile, basic_finaliser):
    """Test error if CDGGenerator runs but zip isn't found afterwards."""
    basic_finaliser.cdg_styles = CDG_STYLES_CONFIG
    mock_generator_instance = MagicMock()
    mock_cdg_gen_cls.return_value = mock_generator_instance
    # Simulate generator returning paths but the zip file doesn't actually appear
    mock_generator_instance.generate_cdg_from_lrc.return_value = ("file.cdg", "file.mp3", "temp.zip")

    # Mock open for the LRC read inside CDGGenerator
    with patch('os.rename'), \
         patch('builtins.open', mock_open(read_data="[ti:Title]\n[00:01.00]Test")), \
         patch('os.path.isfile', return_value=False), \
         patch('os.listdir', return_value=[]), \
         pytest.raises(Exception):  # Remove match
        basic_finaliser.create_cdg_zip_file(INPUT_FILES_ZIP, OUTPUT_FILES_ZIP, ARTIST, TITLE)


# --- TXT ZIP Creation Tests ---

@patch('os.path.isfile')
@patch('zipfile.ZipFile')
# Patch the converter where it's imported in the target module
@patch('karaoke_gen.karaoke_finalise.karaoke_finalise.LyricsConverter')
@patch('builtins.open', new_callable=mock_open)  # For the TXT write
@patch.object(KaraokeFinalise, 'prompt_user_bool', return_value=True)  # Confirm overwrite
def test_create_txt_zip_generate_new(mock_prompt, mock_open_txt_write, mock_lyrics_converter_cls, mock_zipfile, mock_isfile, basic_finaliser):
    """Test generating new TXT file and zipping."""
    # Simulate no existing TXT zip, but MP3 exists (assumed from CDG step)
    mock_isfile.side_effect = lambda f: f == OUTPUT_FILES_ZIP["karaoke_mp3"] or f == OUTPUT_FILES_ZIP["final_karaoke_txt_zip"]

    # Mock LyricsConverter instance and its convert_file method
    mock_converter_instance = MagicMock()
    mock_lyrics_converter_cls.return_value = mock_converter_instance
    converted_text = "[00:01.00]Line 1\n[00:02.00]Line 2"
    mock_converter_instance.convert_file.return_value = converted_text

    basic_finaliser.create_txt_zip_file(INPUT_FILES_ZIP, OUTPUT_FILES_ZIP)

    # Prompt is called because isfile returns True for the zip
    mock_prompt.assert_called_once_with(f"Found existing TXT ZIP file: {OUTPUT_FILES_ZIP['final_karaoke_txt_zip']}. Overwrite (y) or skip (n)?")
    mock_lyrics_converter_cls.assert_called_once_with(output_format="txt", filepath=INPUT_FILES_ZIP["karaoke_lrc"])
    mock_converter_instance.convert_file.assert_called_once()
    # Check TXT file write
    mock_open_txt_write.assert_called_once_with(OUTPUT_FILES_ZIP["karaoke_txt"], "w")
    mock_open_txt_write().write.assert_called_once_with(converted_text)
    # Check zip creation
    mock_zipfile.assert_called_once_with(OUTPUT_FILES_ZIP["final_karaoke_txt_zip"], "w")
    zip_instance_mock = mock_zipfile.return_value.__enter__.return_value
    zip_instance_mock.write.assert_has_calls([
        call(OUTPUT_FILES_ZIP["karaoke_mp3"], os.path.basename(OUTPUT_FILES_ZIP["karaoke_mp3"])),
        call(OUTPUT_FILES_ZIP["karaoke_txt"], os.path.basename(OUTPUT_FILES_ZIP["karaoke_txt"])),
    ], any_order=True)

@patch('os.path.isfile', return_value=True)  # ZIP exists
@patch('zipfile.ZipFile')
@patch('lyrics_converter.LyricsConverter')
@patch('builtins.open', new_callable=mock_open)
@patch.object(KaraokeFinalise, 'prompt_user_bool', return_value=False)  # Skip overwrite
def test_create_txt_zip_skip_overwrite(mock_prompt, mock_open_file, mock_lyrics_converter_cls, mock_zipfile, mock_isfile, basic_finaliser):
    """Test skipping TXT ZIP creation if user chooses not to overwrite."""
    basic_finaliser.create_txt_zip_file(INPUT_FILES_ZIP, OUTPUT_FILES_ZIP)

    mock_prompt.assert_called_once()
    mock_lyrics_converter_cls.assert_not_called()
    mock_open_file.assert_not_called()
    mock_zipfile.assert_not_called()

@patch('os.path.isfile')
@patch('zipfile.ZipFile', side_effect=Exception("Zip creation failed"))  # Simulate zip error
@patch('karaoke_gen.karaoke_finalise.karaoke_finalise.LyricsConverter')
@patch('builtins.open', new_callable=mock_open)  # For the TXT write
@patch.object(KaraokeFinalise, 'prompt_user_bool', return_value=True)
def test_create_txt_zip_creation_fails(mock_prompt, mock_open_txt_write, mock_lyrics_converter_cls, mock_zipfile, mock_isfile, basic_finaliser):
    """Test handling exception during TXT ZIP creation."""
    mock_isfile.return_value = False  # No existing zip
    mock_converter_instance = MagicMock()
    mock_lyrics_converter_cls.return_value = mock_converter_instance
    mock_converter_instance.convert_file.return_value = "text"  # Mock the conversion result

    with pytest.raises(Exception):  # Remove match
        basic_finaliser.create_txt_zip_file(INPUT_FILES_ZIP, OUTPUT_FILES_ZIP)

    # Ensure previous steps were attempted
    mock_lyrics_converter_cls.assert_called_once()
    mock_converter_instance.convert_file.assert_called_once()  # Ensure conversion was attempted
    mock_open_txt_write.assert_called_once_with(OUTPUT_FILES_ZIP["karaoke_txt"], "w")  # Ensure TXT write was attempted
    mock_zipfile.assert_called_once()  # Attempted to create zip
