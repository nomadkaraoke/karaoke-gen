import pytest
import os
import shutil
import subprocess
import time
from unittest.mock import patch, MagicMock, call, ANY

# Adjust the import path
from karaoke_gen.karaoke_finalise.karaoke_finalise import KaraokeFinalise
from .test_initialization import mock_logger, basic_finaliser, MINIMAL_CONFIG # Reuse fixtures
from .test_file_input_validation import BASE_NAME, ARTIST, TITLE # Reuse constants
from .test_ffmpeg_commands import OUTPUT_FILES as FFMPEG_OUTPUT_FILES # Reuse constants

# Define expected output filenames relevant to organisation
OUTPUT_FILES_ORG = {
    "final_karaoke_lossy_mp4": FFMPEG_OUTPUT_FILES["final_karaoke_lossy_mp4"],
    "final_karaoke_lossy_720p_mp4": FFMPEG_OUTPUT_FILES["final_karaoke_lossy_720p_mp4"],
    "final_karaoke_cdg_zip": f"{BASE_NAME} (Final Karaoke CDG).zip", # Assume CDG enabled for copy tests
}

BRAND_PREFIX = "XYZ"
ORGANISED_DIR = "/path/to/organised"
PUBLIC_SHARE_DIR = "/path/to/public"
RCLONE_DEST = "remote:public_share"
ORGANISED_RCLONE_ROOT = "remote:organised"

@pytest.fixture
def finaliser_for_org(mock_logger):
    """Fixture for a finaliser configured for organisation tasks."""
    config = MINIMAL_CONFIG.copy()
    config.update({
        "brand_prefix": BRAND_PREFIX,
        "organised_dir": ORGANISED_DIR,
        "public_share_dir": PUBLIC_SHARE_DIR,
        "rclone_destination": RCLONE_DEST,
        "organised_dir_rclone_root": ORGANISED_RCLONE_ROOT,
        "enable_cdg": True, # Needed for zip copy test
    })
    with patch.object(KaraokeFinalise, 'detect_best_aac_codec', return_value='aac'):
        finaliser = KaraokeFinalise(logger=mock_logger, **config)
    # Manually enable features for testing specific methods
    finaliser.folder_organisation_enabled = True
    finaliser.public_share_copy_enabled = True
    finaliser.public_share_rclone_enabled = True
    return finaliser

# --- Brand Code Tests ---

@patch('os.listdir')
@patch('os.path.isdir', return_value=True)
def test_get_next_brand_code_first(mock_isdir, mock_listdir, finaliser_for_org):
    """Test getting the first brand code when no previous exist."""
    mock_listdir.return_value = ["some_other_file.txt", "NOT-A-BRAND-DIR"]
    next_code = finaliser_for_org.get_next_brand_code()
    assert next_code == f"{BRAND_PREFIX}-0001"
    mock_isdir.assert_called_once_with(ORGANISED_DIR)

@patch('os.listdir')
@patch('os.path.isdir', return_value=True)
def test_get_next_brand_code_existing(mock_isdir, mock_listdir, finaliser_for_org):
    """Test getting the next brand code when others exist (legacy range, gaps preserved)."""
    mock_listdir.return_value = [
        f"{BRAND_PREFIX}-0001 - Some Artist - Title",
        f"{BRAND_PREFIX}-0005 - Another Artist - Song",  # Gap at 0002-0004 preserved
        f"{BRAND_PREFIX}-0003 - Test - Track",
        "OTHER-0001 - Ignored",
    ]
    next_code = finaliser_for_org.get_next_brand_code()
    assert next_code == f"{BRAND_PREFIX}-0006"  # Max was 5, next is 6 (legacy gaps preserved)
    mock_isdir.assert_called_once_with(ORGANISED_DIR)


@patch('os.listdir')
@patch('os.path.isdir', return_value=True)
def test_get_next_brand_code_fills_gap_above_1000(mock_isdir, mock_listdir, finaliser_for_org):
    """Test that gaps at 1001+ ARE filled."""
    mock_listdir.return_value = [
        f"{BRAND_PREFIX}-1001 - Artist1 - Song1",
        f"{BRAND_PREFIX}-1002 - Artist2 - Song2",
        f"{BRAND_PREFIX}-1005 - Artist3 - Song3",  # Gap at 1003, 1004
    ]
    next_code = finaliser_for_org.get_next_brand_code()
    assert next_code == f"{BRAND_PREFIX}-1003"  # Fills gap at 1003
    mock_isdir.assert_called_once_with(ORGANISED_DIR)


@patch('os.listdir')
@patch('os.path.isdir', return_value=True)
def test_get_next_brand_code_mixed_legacy_and_new(mock_isdir, mock_listdir, finaliser_for_org):
    """Test with both legacy (<1000) and new (>=1000) codes."""
    mock_listdir.return_value = [
        f"{BRAND_PREFIX}-0001 - Artist1 - Song1",
        f"{BRAND_PREFIX}-0005 - Artist2 - Song2",  # Legacy gap - preserved
        f"{BRAND_PREFIX}-1001 - Artist3 - Song3",
        f"{BRAND_PREFIX}-1003 - Artist4 - Song4",  # Gap at 1002 - should be filled
    ]
    next_code = finaliser_for_org.get_next_brand_code()
    assert next_code == f"{BRAND_PREFIX}-1002"  # Fills gap at 1002, ignores legacy gap
    mock_isdir.assert_called_once_with(ORGANISED_DIR)

@patch('os.path.isdir', return_value=False)
def test_get_next_brand_code_dir_missing(mock_isdir, finaliser_for_org):
    """Test get_next_brand_code raises error if organised_dir is missing."""
    with pytest.raises(Exception, match=f"Target directory does not exist: {ORGANISED_DIR}"):
        finaliser_for_org.get_next_brand_code()

@patch('os.getcwd', return_value=f"/some/path/{BRAND_PREFIX}-0010 - {ARTIST} - {TITLE}")
@patch('os.path.basename', return_value=f"{BRAND_PREFIX}-0010 - {ARTIST} - {TITLE}")
def test_get_existing_brand_code_success(mock_basename, mock_getcwd, finaliser_for_org):
    """Test successfully extracting existing brand code."""
    brand_code = finaliser_for_org.get_existing_brand_code()
    assert brand_code == f"{BRAND_PREFIX}-0010"
    mock_getcwd.assert_called_once()
    mock_basename.assert_called_once_with(mock_getcwd.return_value)

@patch('os.getcwd', return_value="/some/path/NoBrandCode - Artist - Title")
@patch('os.path.basename', return_value="NoBrandCode - Artist - Title")
def test_get_existing_brand_code_invalid_format(mock_basename, mock_getcwd, finaliser_for_org):
    """Test error when current directory format is wrong."""
    with pytest.raises(Exception, match="Could not extract valid brand code"):
        finaliser_for_org.get_existing_brand_code()

# --- File Moving/Copying Tests ---

@patch('os.getcwd', return_value=f"/current/workdir/{ARTIST} - {TITLE}")
@patch('os.path.dirname', return_value="/current/workdir")
@patch('os.chdir')
@patch('os.rename')
@patch('os.path.join', side_effect=os.path.join) # Use real join
@patch('os.path.basename', side_effect=os.path.basename) # Use real basename
def test_move_files_to_brand_code_folder(mock_basename, mock_join, mock_rename, mock_chdir, mock_dirname, mock_getcwd, finaliser_for_org):
    """Test moving files to the new brand code directory."""
    brand_code = f"{BRAND_PREFIX}-0001"
    current_dir = mock_getcwd.return_value
    parent_dir = mock_dirname.return_value
    new_dir_name = f"{brand_code} - {ARTIST} - {TITLE}"
    expected_new_path = os.path.join(ORGANISED_DIR, new_dir_name)

    # Make a copy to modify
    output_files_copy = OUTPUT_FILES_ORG.copy()

    finaliser_for_org.move_files_to_brand_code_folder(brand_code, ARTIST, TITLE, output_files_copy)

    assert finaliser_for_org.new_brand_code_dir == new_dir_name
    assert finaliser_for_org.new_brand_code_dir_path == expected_new_path
    # getcwd is called at the start and after chdir
    assert mock_getcwd.call_count == 2
    mock_dirname.assert_called_once_with(current_dir)
    # Ensure the assertion uses the exact value returned by the mock
    mock_chdir.assert_called_once_with(mock_dirname.return_value)
    mock_rename.assert_called_once_with(current_dir, expected_new_path)

    # Check output_files paths were updated
    for key, original_path in OUTPUT_FILES_ORG.items():
        original_basename = os.path.basename(original_path)
        expected_updated_path = os.path.join(expected_new_path, original_basename)
        assert output_files_copy[key] == expected_updated_path

@patch('os.getcwd', return_value=f"/current/workdir/{ARTIST} - {TITLE}")
@patch('os.path.dirname', return_value="/current/workdir")
@patch('os.chdir')
@patch('os.rename')
def test_move_files_to_brand_code_folder_dry_run(mock_rename, mock_chdir, mock_dirname, mock_getcwd, finaliser_for_org):
    """Test move files dry run."""
    finaliser_for_org.dry_run = True
    brand_code = f"{BRAND_PREFIX}-0001"
    current_dir = mock_getcwd.return_value
    new_dir_name = f"{brand_code} - {ARTIST} - {TITLE}"
    expected_new_path = os.path.join(ORGANISED_DIR, new_dir_name)
    output_files_copy = OUTPUT_FILES_ORG.copy() # Paths won't be updated in dry run

    finaliser_for_org.move_files_to_brand_code_folder(brand_code, ARTIST, TITLE, output_files_copy)

    mock_rename.assert_not_called()
    finaliser_for_org.logger.info.assert_any_call(f"DRY RUN: Would move original directory {current_dir} to: {expected_new_path}")
    # Paths ARE updated even in dry run by the current implementation
    expected_updated_paths = {}
    for key, original_path in OUTPUT_FILES_ORG.items():
        original_basename = os.path.basename(original_path)
        expected_updated_paths[key] = os.path.join(expected_new_path, original_basename)
    assert output_files_copy == expected_updated_paths


@patch('os.path.isdir', return_value=True)
@patch('os.makedirs')
@patch('shutil.copy2')
def test_copy_final_files_to_public_share_dirs(mock_copy, mock_makedirs, mock_isdir, finaliser_for_org):
    """Test copying files to public share directories."""
    brand_code = f"{BRAND_PREFIX}-0001"
    base_name_no_brand = f"{ARTIST} - {TITLE}" # Base name without brand code

    dest_mp4_dir = os.path.join(PUBLIC_SHARE_DIR, "MP4")
    dest_720p_dir = os.path.join(PUBLIC_SHARE_DIR, "MP4-720p")
    dest_cdg_dir = os.path.join(PUBLIC_SHARE_DIR, "CDG")

    expected_dest_mp4 = os.path.join(dest_mp4_dir, f"{brand_code} - {base_name_no_brand}.mp4")
    expected_dest_720p = os.path.join(dest_720p_dir, f"{brand_code} - {base_name_no_brand}.mp4")
    expected_dest_zip = os.path.join(dest_cdg_dir, f"{brand_code} - {base_name_no_brand}.zip")

    finaliser_for_org.copy_final_files_to_public_share_dirs(brand_code, base_name_no_brand, OUTPUT_FILES_ORG)

    mock_isdir.assert_has_calls([
        call(PUBLIC_SHARE_DIR),
        call(dest_mp4_dir),
        call(dest_720p_dir), # Check for 720p dir
        call(dest_cdg_dir),
    ], any_order=True)
    mock_makedirs.assert_has_calls([
        call(dest_mp4_dir, exist_ok=True),
        call(dest_720p_dir, exist_ok=True),
        call(dest_cdg_dir, exist_ok=True),
    ], any_order=True)
    mock_copy.assert_has_calls([
        call(OUTPUT_FILES_ORG["final_karaoke_lossy_mp4"], expected_dest_mp4),
        call(OUTPUT_FILES_ORG["final_karaoke_lossy_720p_mp4"], expected_dest_720p),
        call(OUTPUT_FILES_ORG["final_karaoke_cdg_zip"], expected_dest_zip),
    ], any_order=True)

@patch('os.path.isdir', return_value=False)
def test_copy_final_files_public_dir_missing(mock_isdir, finaliser_for_org):
    """Test error if public share dir is missing."""
    with pytest.raises(Exception, match=f"Public share directory does not exist: {PUBLIC_SHARE_DIR}"):
        finaliser_for_org.copy_final_files_to_public_share_dirs("CODE", BASE_NAME, OUTPUT_FILES_ORG)

@patch('os.path.isdir', side_effect=lambda p: p != os.path.join(PUBLIC_SHARE_DIR, "MP4-720p")) # 720p dir missing
@patch('os.makedirs')
def test_copy_final_files_public_subdir_missing(mock_makedirs, mock_isdir, finaliser_for_org):
    """Test error if public share subdirectories are missing."""
    with pytest.raises(Exception, match="Public share directory does not contain MP4-720p subdirectory"):
        finaliser_for_org.copy_final_files_to_public_share_dirs("CODE", BASE_NAME, OUTPUT_FILES_ORG)

@patch('os.path.isdir', return_value=True) # Patch isdir to pass initial checks
def test_copy_final_files_no_brand_code(mock_isdir, finaliser_for_org):
    """Test error if brand code is None."""
    with pytest.raises(Exception, match="New track prefix was not set"):
        finaliser_for_org.copy_final_files_to_public_share_dirs(None, BASE_NAME, OUTPUT_FILES_ORG)
    # Ensure isdir was called for the initial checks before the brand_code check failed
    mock_isdir.assert_any_call(PUBLIC_SHARE_DIR)
    mock_isdir.assert_any_call(os.path.join(PUBLIC_SHARE_DIR, "MP4"))
    mock_isdir.assert_any_call(os.path.join(PUBLIC_SHARE_DIR, "MP4-720p"))
    mock_isdir.assert_any_call(os.path.join(PUBLIC_SHARE_DIR, "CDG"))


@patch('os.path.isdir', return_value=True)
@patch('os.makedirs')
@patch('shutil.copy2')
def test_copy_final_files_dry_run(mock_copy, mock_makedirs, mock_isdir, finaliser_for_org):
    """Test copy files dry run."""
    finaliser_for_org.dry_run = True
    brand_code = f"{BRAND_PREFIX}-0001"
    base_name_no_brand = f"{ARTIST} - {TITLE}"

    finaliser_for_org.copy_final_files_to_public_share_dirs(brand_code, base_name_no_brand, OUTPUT_FILES_ORG)

    mock_copy.assert_not_called()
    # Check that the log message contains the expected dry run text
    dry_run_log_found = any("DRY RUN: Would copy final MP4" in call_args[0][0] for call_args in finaliser_for_org.logger.info.call_args_list)
    assert dry_run_log_found, "Expected dry run log message for copying files not found."


# --- Rclone Sync / Link Tests ---

@patch('os.walk')
@patch('os.remove')
@patch('os.path.join', side_effect=os.path.join)
@patch.object(KaraokeFinalise, 'execute_command')
@patch('shlex.quote', side_effect=lambda x: f"'{x}'")  # Mock shlex.quote for predictable output
def test_sync_public_share_dir_to_rclone(mock_quote, mock_execute, mock_join, mock_remove, mock_walk, finaliser_for_org):
    """Test syncing public share dir, including .DS_Store removal."""
    # Simulate finding a .DS_Store file
    mock_walk.return_value = [
        (PUBLIC_SHARE_DIR, ['MP4', 'CDG'], ['file1.txt']),
        (os.path.join(PUBLIC_SHARE_DIR, 'MP4'), [], ['video.mp4', '.DS_Store']),
        (os.path.join(PUBLIC_SHARE_DIR, 'CDG'), [], ['song.zip']),
    ]
    ds_store_path = os.path.join(PUBLIC_SHARE_DIR, 'MP4', '.DS_Store')

    finaliser_for_org.sync_public_share_dir_to_rclone_destination()

    mock_walk.assert_called_once_with(PUBLIC_SHARE_DIR)
    mock_remove.assert_called_once_with(ds_store_path)
    finaliser_for_org.logger.info.assert_any_call(f"Deleted .DS_Store file: {ds_store_path}")
    expected_cmd = f"rclone copy -v --ignore-existing '{PUBLIC_SHARE_DIR}' '{RCLONE_DEST}'"
    mock_execute.assert_called_once_with(expected_cmd, "Copying to cloud destination")
    mock_quote.assert_has_calls([
        call(PUBLIC_SHARE_DIR),
        call(RCLONE_DEST)
    ], any_order=True)

@patch('time.sleep')
@patch('subprocess.run')
@patch('shlex.quote', side_effect=lambda x: f"'{x}'") # Simple quote mock
def test_generate_organised_folder_sharing_link(mock_quote, mock_run, mock_sleep, finaliser_for_org):
    """Test generating the organised folder sharing link."""
    brand_code = f"{BRAND_PREFIX}-0001"
    finaliser_for_org.new_brand_code_dir = f"{brand_code} - {ARTIST} - {TITLE}" # Set this as if move happened
    expected_link = "https://example.com/share_link"
    mock_run.return_value = subprocess.CompletedProcess(
        args="mock command", returncode=0, stdout=expected_link + "\n", stderr=""
    )

    finaliser_for_org.generate_organised_folder_sharing_link()

    expected_rclone_path = f"{ORGANISED_RCLONE_ROOT}/{finaliser_for_org.new_brand_code_dir}"
    expected_cmd = f"rclone link '{expected_rclone_path}'"

    mock_sleep.assert_called_once_with(5)
    mock_quote.assert_called_once_with(expected_rclone_path)
    mock_run.assert_called_once_with(expected_cmd, shell=True, check=True, capture_output=True, text=True)
    assert finaliser_for_org.brand_code_dir_sharing_link == expected_link

@patch('time.sleep')
@patch('subprocess.run', side_effect=subprocess.CalledProcessError(1, "cmd", stderr="Link failed"))
@patch('shlex.quote', side_effect=lambda x: f"'{x}'")
def test_generate_organised_folder_sharing_link_failure(mock_quote, mock_run, mock_sleep, finaliser_for_org):
    """Test handling failure during sharing link generation."""
    brand_code = f"{BRAND_PREFIX}-0001"
    finaliser_for_org.new_brand_code_dir = f"{brand_code} - {ARTIST} - {TITLE}"

    # No exception expected, should log error
    finaliser_for_org.generate_organised_folder_sharing_link()

    assert finaliser_for_org.brand_code_dir_sharing_link is None
    # Check that the log message contains the expected error text
    error_log_found = any("Failed to get organised folder sharing link" in call_args[0][0] for call_args in finaliser_for_org.logger.error.call_args_list)
    assert error_log_found, "Expected error log message for failed link generation not found."
    # Check the exact stderr log message format
    stderr_log_found = any("Command output (stderr): Link failed" in call_args[0][0] for call_args in finaliser_for_org.logger.error.call_args_list)
    assert stderr_log_found, "Expected stderr log message 'Command output (stderr): Link failed' not found."


@patch('time.sleep')
@patch('subprocess.run')
@patch('shlex.quote')
def test_generate_organised_folder_sharing_link_dry_run(mock_quote, mock_run, mock_sleep, finaliser_for_org):
    """Test sharing link generation dry run."""
    finaliser_for_org.dry_run = True
    brand_code = f"{BRAND_PREFIX}-0001"
    finaliser_for_org.new_brand_code_dir = f"{brand_code} - {ARTIST} - {TITLE}"

    link = finaliser_for_org.generate_organised_folder_sharing_link()

    assert link == "https://file-sharing-service.com/example" # Default dry run link
    mock_sleep.assert_not_called()
    mock_run.assert_not_called()
    # Check that the log message contains the expected dry run text
    dry_run_log_found = any("DRY RUN: Would get sharing link with:" in call_args[0][0] for call_args in finaliser_for_org.logger.info.call_args_list)
    assert dry_run_log_found, "Expected dry run log message for sharing link not found."
