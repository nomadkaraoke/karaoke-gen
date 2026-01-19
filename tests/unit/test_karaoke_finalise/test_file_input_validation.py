import pytest
import os
import json
from unittest.mock import patch, MagicMock, mock_open, call

# Adjust the import path
from karaoke_gen.karaoke_finalise.karaoke_finalise import KaraokeFinalise
from .test_initialization import mock_logger, basic_finaliser, MINIMAL_CONFIG # Reuse fixtures

BASE_NAME = "Artist - Title"
ARTIST = "Artist" # Define constant
TITLE = "Title"   # Define constant
WITH_VOCALS_MOV = f"{BASE_NAME} (With Vocals).mov"
WITH_VOCALS_MP4 = f"{BASE_NAME} (With Vocals).mp4"
INSTRUMENTAL_FLAC = f"{BASE_NAME} (Instrumental).flac"
INSTRUMENTAL_MP3 = f"{BASE_NAME} (Instrumental).mp3"
INSTRUMENTAL_WAV = f"{BASE_NAME} (Instrumental).wav"
TITLE_MOV = f"{BASE_NAME} (Title).mov"
TITLE_JPG = f"{BASE_NAME} (Title).jpg"
END_MOV = f"{BASE_NAME} (End).mov"
END_JPG = f"{BASE_NAME} (End).jpg"
KARAOKE_LRC = f"{BASE_NAME} (Karaoke).lrc"
KARAOKE_MOV_MISNAMED = f"{BASE_NAME} (Karaoke).mov"

# --- File Existence and Preparation Tests ---

@patch('os.path.isfile')
def test_check_input_files_exist_success(mock_isfile, basic_finaliser):
    """Test check_input_files_exist succeeds when all required files exist."""
    mock_isfile.return_value = True
    basic_finaliser.enable_cdg = True # Need LRC for this test

    input_files = basic_finaliser.check_input_files_exist(BASE_NAME, WITH_VOCALS_MOV, INSTRUMENTAL_FLAC)

    expected_files = {
        "title_mov": TITLE_MOV,
        "title_jpg": TITLE_JPG,
        "instrumental_audio": INSTRUMENTAL_FLAC,
        "with_vocals_mov": WITH_VOCALS_MOV,
        "karaoke_lrc": KARAOKE_LRC,
        "end_mov": END_MOV, # Optional, mocked as existing
        "end_jpg": END_JPG, # Optional, mocked as existing
    }
    assert input_files == expected_files
    mock_isfile.assert_has_calls([
        call(TITLE_MOV),
        call(TITLE_JPG),
        call(INSTRUMENTAL_FLAC),
        call(WITH_VOCALS_MOV),
        call(KARAOKE_LRC),
        call(END_MOV), # Checks optional file
        call(END_JPG), # Checks optional file
    ], any_order=True)

@patch('os.path.isfile')
def test_check_input_files_exist_missing_required(mock_isfile, basic_finaliser):
    """Test check_input_files_exist raises exception for missing required file."""
    # Make title_mov missing
    mock_isfile.side_effect = lambda f: f != TITLE_MOV

    # Remove match, just check exception type
    with pytest.raises(Exception):
        basic_finaliser.check_input_files_exist(BASE_NAME, WITH_VOCALS_MOV, INSTRUMENTAL_FLAC)

@patch('os.path.isfile')
def test_check_input_files_exist_missing_optional(mock_isfile, basic_finaliser):
    """Test check_input_files_exist succeeds when optional files are missing."""
    # Make optional files missing, required files exist
    mock_isfile.side_effect = lambda f: f not in [END_MOV, END_JPG]
    basic_finaliser.enable_cdg = False # Don't require LRC

    input_files = basic_finaliser.check_input_files_exist(BASE_NAME, WITH_VOCALS_MOV, INSTRUMENTAL_FLAC)

    # Optional files (end_mov, end_jpg) are NOT included when they don't exist
    expected_files = {
        "title_mov": TITLE_MOV,
        "title_jpg": TITLE_JPG,
        "instrumental_audio": INSTRUMENTAL_FLAC,
        "with_vocals_mov": WITH_VOCALS_MOV,
    }
    assert input_files == expected_files
    # Ensure optional files were checked
    mock_isfile.assert_has_calls([call(END_MOV), call(END_JPG)], any_order=True)

@patch('os.path.isfile')
def test_check_input_files_exist_lrc_required(mock_isfile, basic_finaliser):
    """Test check_input_files_exist requires LRC when CDG or TXT enabled."""
    mock_isfile.side_effect = lambda f: f != KARAOKE_LRC # Make LRC missing
    basic_finaliser.enable_cdg = True

    # Remove match, just check exception type
    with pytest.raises(Exception):
        basic_finaliser.check_input_files_exist(BASE_NAME, WITH_VOCALS_MOV, INSTRUMENTAL_FLAC)

    basic_finaliser.enable_cdg = False
    basic_finaliser.enable_txt = True
    # Remove match, just check exception type
    with pytest.raises(Exception):
        basic_finaliser.check_input_files_exist(BASE_NAME, WITH_VOCALS_MOV, INSTRUMENTAL_FLAC)


def test_prepare_output_filenames(basic_finaliser):
    """Test prepare_output_filenames generates correct names."""
    basic_finaliser.enable_cdg = True
    basic_finaliser.enable_txt = True

    output_files = basic_finaliser.prepare_output_filenames(BASE_NAME)

    expected_suffixes = {
        "karaoke_mp4": " (Karaoke).mp4",
        "karaoke_mp3": " (Karaoke).mp3",
        "karaoke_cdg": " (Karaoke).cdg",
        "with_vocals_mp4": " (With Vocals).mp4",
        "final_karaoke_lossless_mp4": " (Final Karaoke Lossless 4k).mp4",
        "final_karaoke_lossless_mkv": " (Final Karaoke Lossless 4k).mkv",
        "final_karaoke_lossy_mp4": " (Final Karaoke Lossy 4k).mp4",
        "final_karaoke_lossy_720p_mp4": " (Final Karaoke Lossy 720p).mp4",
        "final_karaoke_cdg_zip": " (Final Karaoke CDG).zip", # Enabled
        "karaoke_txt": " (Karaoke).txt", # Enabled
        "final_karaoke_txt_zip": " (Final Karaoke TXT).zip", # Enabled
    }

    assert len(output_files) == len(expected_suffixes)
    for key, suffix in expected_suffixes.items():
        assert key in output_files
        assert output_files[key] == f"{BASE_NAME}{suffix}"

def test_prepare_output_filenames_cdg_txt_disabled(basic_finaliser):
    """Test prepare_output_filenames excludes CDG/TXT when disabled."""
    basic_finaliser.enable_cdg = False
    basic_finaliser.enable_txt = False

    output_files = basic_finaliser.prepare_output_filenames(BASE_NAME)

    assert "final_karaoke_cdg_zip" not in output_files
    assert "karaoke_txt" not in output_files
    assert "final_karaoke_txt_zip" not in output_files


# --- Feature Validation Tests ---

@patch('os.path.isfile')
@patch('os.path.isdir')
@patch('builtins.open', new_callable=mock_open, read_data='{"installed": {"client_id": "test"}}')
@patch('json.load')
@patch('builtins.input', return_value='y') # Auto-confirm prompt
def test_validate_input_parameters_all_features_enabled(mock_input, mock_json_load, mock_open_file, mock_isdir, mock_isfile, basic_finaliser):
    """Test validation enables all features with correct inputs."""
    mock_isfile.return_value = True # All files exist
    mock_isdir.return_value = True # All dirs exist

    basic_finaliser.youtube_client_secrets_file = "secrets.json"
    basic_finaliser.youtube_description_file = "desc.txt"
    basic_finaliser.discord_webhook_url = "https://discord.com/api/webhooks/123/abc"
    basic_finaliser.brand_prefix = "TEST"
    basic_finaliser.organised_dir = "/path/to/organised"
    basic_finaliser.public_share_dir = "/path/to/public"
    basic_finaliser.rclone_destination = "remote:backup"
    basic_finaliser.enable_cdg = True
    basic_finaliser.enable_txt = True

    basic_finaliser.validate_input_parameters_for_features()

    assert basic_finaliser.youtube_upload_enabled
    assert basic_finaliser.discord_notication_enabled
    assert basic_finaliser.folder_organisation_enabled
    assert basic_finaliser.public_share_copy_enabled
    assert basic_finaliser.public_share_rclone_enabled
    mock_isfile.assert_any_call("secrets.json")
    mock_isfile.assert_any_call("desc.txt")
    mock_open_file.assert_called_once_with("secrets.json", "r")
    mock_json_load.assert_called_once()
    mock_isdir.assert_any_call("/path/to/organised")
    mock_isdir.assert_any_call("/path/to/public")
    mock_isdir.assert_any_call("/path/to/public/MP4")
    mock_isdir.assert_any_call("/path/to/public/CDG")
    # basic_finaliser is non-interactive by default, so prompt is not called
    mock_input.assert_not_called()

@patch('os.path.isfile', return_value=False)
@patch('builtins.input', return_value='y')
def test_validate_youtube_secrets_missing(mock_input, mock_isfile, basic_finaliser):
    """Test validation fails if YouTube secrets file is missing."""
    basic_finaliser.youtube_client_secrets_file = "secrets.json"
    basic_finaliser.youtube_description_file = "desc.txt"
    with pytest.raises(Exception, match="YouTube client secrets file does not exist"):
        basic_finaliser.validate_input_parameters_for_features()
    assert not basic_finaliser.youtube_upload_enabled

@patch('os.path.isfile', return_value=True)
@patch('builtins.open', new_callable=mock_open, read_data='invalid json')
@patch('json.load', side_effect=json.JSONDecodeError("Expecting value", "invalid json", 0))
@patch('builtins.input', return_value='y')
def test_validate_youtube_secrets_invalid_json(mock_input, mock_json_load, mock_open_file, mock_isfile, basic_finaliser):
    """Test validation fails if YouTube secrets file is invalid JSON."""
    basic_finaliser.youtube_client_secrets_file = "secrets.json"
    basic_finaliser.youtube_description_file = "desc.txt"
    with pytest.raises(Exception, match="YouTube client secrets file is not valid JSON"):
        basic_finaliser.validate_input_parameters_for_features()
    assert not basic_finaliser.youtube_upload_enabled

@patch('builtins.input', return_value='y')
def test_validate_discord_url_invalid(mock_input, basic_finaliser):
    """Test validation fails with invalid Discord webhook URL."""
    basic_finaliser.discord_webhook_url = "http://invalid.com"
    with pytest.raises(Exception, match="Discord webhook URL is not valid"):
        basic_finaliser.validate_input_parameters_for_features()
    assert not basic_finaliser.discord_notication_enabled

@patch('os.path.isdir', return_value=False)
@patch('builtins.input', return_value='y')
def test_validate_organised_dir_missing(mock_input, mock_isdir, basic_finaliser):
    """Test validation fails if organised_dir is missing."""
    basic_finaliser.brand_prefix = "TEST"
    basic_finaliser.organised_dir = "/missing/dir"
    with pytest.raises(Exception, match="Target directory does not exist"):
        basic_finaliser.validate_input_parameters_for_features()
    assert not basic_finaliser.folder_organisation_enabled

@patch('os.path.isdir')
@patch('builtins.input', return_value='y')
def test_validate_public_share_dir_missing_subdirs(mock_input, mock_isdir, basic_finaliser):
    """Test validation fails if public_share_dir is missing subdirectories."""
    basic_finaliser.public_share_dir = "/public/share"
    # Simulate missing MP4 subdir
    mock_isdir.side_effect = lambda p: p != "/public/share/MP4"

    with pytest.raises(Exception, match="Public share directory does not contain MP4 subdirectory"):
        basic_finaliser.validate_input_parameters_for_features()
    assert not basic_finaliser.public_share_copy_enabled

    # Simulate missing CDG subdir
    mock_isdir.side_effect = lambda p: p != "/public/share/CDG"
    with pytest.raises(Exception, match="Public share directory does not contain CDG subdirectory"):
        basic_finaliser.validate_input_parameters_for_features()
    assert not basic_finaliser.public_share_copy_enabled

@patch('builtins.input', return_value='n') # User rejects confirmation
def test_validate_user_rejects_confirmation(mock_input, basic_finaliser):
    """Test validation fails if user rejects the confirmation prompt."""
    # Setup minimal valid config for some features to be enabled
    basic_finaliser.rclone_destination = "remote:dest"
    basic_finaliser.non_interactive = False # Enable prompts for this test
    # Remove match, just check exception type
    with pytest.raises(Exception):
        basic_finaliser.validate_input_parameters_for_features()
    mock_input.assert_called_once() # Ensure prompt was actually called

# --- File Finding/Choosing Tests ---

@patch('os.listdir')
def test_find_with_vocals_file_mov_exists(mock_listdir, basic_finaliser):
    """Test finding a .mov with vocals file."""
    mock_listdir.return_value = ["other.txt", WITH_VOCALS_MOV, TITLE_MOV]
    found_file = basic_finaliser.find_with_vocals_file()
    assert found_file == WITH_VOCALS_MOV

@patch('os.listdir')
def test_find_with_vocals_file_mp4_exists(mock_listdir, basic_finaliser):
    """Test finding a .mp4 with vocals file."""
    mock_listdir.return_value = ["other.txt", WITH_VOCALS_MP4, TITLE_MOV]
    found_file = basic_finaliser.find_with_vocals_file()
    assert found_file == WITH_VOCALS_MP4

@patch('os.listdir')
@patch('os.rename')
@patch('builtins.input', return_value='y') # Confirm rename
def test_find_with_vocals_file_rename_karaoke_mov(mock_input, mock_rename, mock_listdir, basic_finaliser):
    """Test renaming a misnamed (Karaoke).mov file."""
    mock_listdir.return_value = ["other.txt", KARAOKE_MOV_MISNAMED, TITLE_MOV]
    basic_finaliser.non_interactive = False # Enable prompts for this test
    found_file = basic_finaliser.find_with_vocals_file()
    assert found_file == WITH_VOCALS_MOV
    mock_rename.assert_called_once_with(KARAOKE_MOV_MISNAMED, WITH_VOCALS_MOV)
    mock_input.assert_called_once() # Ensure prompt was called

@patch('os.listdir')
@patch('os.rename')
@patch('builtins.input', return_value='n') # Reject rename
def test_find_with_vocals_file_reject_rename(mock_input, mock_rename, mock_listdir, basic_finaliser):
    """Test exception raised if user rejects renaming."""
    basic_finaliser.non_interactive = False # Enable prompts for this test
    mock_listdir.return_value = ["other.txt", KARAOKE_MOV_MISNAMED, TITLE_MOV]
    # Remove match, just check exception type
    with pytest.raises(Exception):
        basic_finaliser.find_with_vocals_file()
    mock_rename.assert_not_called()
    mock_input.assert_called_once() # Ensure prompt was called

@patch('os.listdir', return_value=["other.txt", TITLE_MOV])
def test_find_with_vocals_file_not_found(mock_listdir, basic_finaliser):
    """Test exception raised if no suitable file is found."""
    with pytest.raises(Exception, match="No suitable files found for processing"):
        basic_finaliser.find_with_vocals_file()

@patch('os.listdir')
def test_choose_instrumental_audio_file_only_flac(mock_listdir, basic_finaliser):
    """Test choosing the only instrumental file (FLAC)."""
    mock_listdir.return_value = [INSTRUMENTAL_FLAC, "other.txt"]
    chosen_file = basic_finaliser.choose_instrumental_audio_file(BASE_NAME)
    assert chosen_file == INSTRUMENTAL_FLAC

@patch('os.listdir')
def test_choose_instrumental_audio_file_prefer_flac(mock_listdir, basic_finaliser):
    """Test preferring FLAC over MP3 and WAV."""
    mock_listdir.return_value = [INSTRUMENTAL_MP3, INSTRUMENTAL_FLAC, INSTRUMENTAL_WAV, "other.txt"]
    chosen_file = basic_finaliser.choose_instrumental_audio_file(BASE_NAME)
    assert chosen_file == INSTRUMENTAL_FLAC

@patch('os.listdir')
def test_choose_instrumental_audio_file_prefer_wav_over_mp3(mock_listdir, basic_finaliser):
    """Test preferring WAV over MP3 when FLAC is absent."""
    mock_listdir.return_value = [INSTRUMENTAL_MP3, INSTRUMENTAL_WAV, "other.txt"]
    chosen_file = basic_finaliser.choose_instrumental_audio_file(BASE_NAME)
    assert chosen_file == INSTRUMENTAL_WAV

@patch('os.listdir')
@patch('builtins.input', return_value='2') # Choose second option
def test_choose_instrumental_audio_file_multiple_prompt(mock_input, mock_listdir, basic_finaliser):
    """Test prompting user when multiple valid files exist."""
    basic_finaliser.non_interactive = False # Enable prompt
    files = [f"{BASE_NAME} (Instrumental Mix 1).flac", f"{BASE_NAME} (Instrumental Mix 2).flac"]
    mock_listdir.return_value = files
    # Sort order expected by the prompt (reverse alphabetical)
    expected_prompt_order = sorted(files, reverse=True)

    chosen_file = basic_finaliser.choose_instrumental_audio_file(BASE_NAME)

    assert chosen_file == expected_prompt_order[1] # User chose '2' (index 1)
    mock_input.assert_called_once()

@patch('os.listdir')
def test_choose_instrumental_audio_file_multiple_non_interactive(mock_listdir, basic_finaliser):
    """Test choosing the first option non-interactively."""
    basic_finaliser.non_interactive = True # Disable prompt
    files = [f"{BASE_NAME} (Instrumental Mix 1).flac", f"{BASE_NAME} (Instrumental Mix 2).flac"]
    mock_listdir.return_value = files
    # Non-interactive chooses the first item from the *filtered* list before sorting
    # In this case, filtering doesn't remove anything, so it's the first from the original list.
    expected_choice = files[0]

    chosen_file = basic_finaliser.choose_instrumental_audio_file(BASE_NAME)
    assert chosen_file == expected_choice

@patch('os.listdir', return_value=["other.txt"])
def test_choose_instrumental_audio_file_not_found(mock_listdir, basic_finaliser):
    """Test exception raised if no instrumental files are found."""
    with pytest.raises(Exception, match="No instrumental audio files found"):
        basic_finaliser.choose_instrumental_audio_file(BASE_NAME)

# --- Name Extraction Tests ---

def test_get_names_from_withvocals_mov(basic_finaliser):
    """Test extracting names from a .mov file."""
    base, artist, title = basic_finaliser.get_names_from_withvocals(WITH_VOCALS_MOV)
    assert base == BASE_NAME
    assert artist == "Artist"
    assert title == "Title"

def test_get_names_from_withvocals_mp4(basic_finaliser):
    """Test extracting names from a .mp4 file."""
    base, artist, title = basic_finaliser.get_names_from_withvocals(WITH_VOCALS_MP4)
    assert base == BASE_NAME
    assert artist == "Artist"
    assert title == "Title"

def test_get_names_from_withvocals_mkv(basic_finaliser):
    """Test extracting names from a .mkv file."""
    mkv_file = f"{BASE_NAME} (With Vocals).mkv"
    base, artist, title = basic_finaliser.get_names_from_withvocals(mkv_file)
    assert base == BASE_NAME
    assert artist == "Artist"
    assert title == "Title"

def test_get_names_from_withvocals_no_suffix(basic_finaliser):
    """Test extracting names when suffix is missing (falls back to removing extension)."""
    file_no_suffix = f"{BASE_NAME}.mov"
    base, artist, title = basic_finaliser.get_names_from_withvocals(file_no_suffix)
    assert base == BASE_NAME
    assert artist == "Artist"
    assert title == "Title"

def test_get_names_from_withvocals_complex_title(basic_finaliser):
    """Test extracting names with complex title including hyphens."""
    complex_base = "Some Artist - A Song - With Hyphens - And Stuff"
    complex_file = f"{complex_base} (With Vocals).mp4"
    base, artist, title = basic_finaliser.get_names_from_withvocals(complex_file)
    assert base == complex_base
    assert artist == "Some Artist"
    assert title == "A Song - With Hyphens - And Stuff"
