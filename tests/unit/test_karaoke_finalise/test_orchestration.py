import pytest
import os
from unittest.mock import patch, MagicMock, call, ANY

# Adjust the import path
from karaoke_gen.karaoke_finalise.karaoke_finalise import KaraokeFinalise
from .test_initialization import mock_logger, basic_finaliser, MINIMAL_CONFIG # Reuse fixtures
from .test_file_input_validation import BASE_NAME, ARTIST, TITLE, WITH_VOCALS_MOV, INSTRUMENTAL_FLAC # Reuse constants
from .test_ffmpeg_commands import OUTPUT_FILES as FFMPEG_OUTPUT_FILES # Reuse constants
from .test_zip_creation import OUTPUT_FILES_ZIP # Reuse constants
from .test_file_organisation import OUTPUT_FILES_ORG, BRAND_PREFIX, ORGANISED_DIR, PUBLIC_SHARE_DIR, RCLONE_DEST, ORGANISED_RCLONE_ROOT # Reuse constants
from .test_youtube_integration import YOUTUBE_SECRETS_FILE, YOUTUBE_DESC_FILE, OUTPUT_FILES_YT, INPUT_FILES_YT # Reuse constants
from .test_notifications_email import DISCORD_WEBHOOK_URL, EMAIL_TEMPLATE_FILE # Reuse constants

# Combine output file dictionaries for process tests
ALL_OUTPUT_FILES = {**FFMPEG_OUTPUT_FILES, **OUTPUT_FILES_ZIP, **OUTPUT_FILES_ORG, **OUTPUT_FILES_YT}
# Combine input file dictionaries
ALL_INPUT_FILES = {
    "title_mov": f"{BASE_NAME}{KaraokeFinalise().suffixes['title_mov']}",
    "title_jpg": f"{BASE_NAME}{KaraokeFinalise().suffixes['title_jpg']}",
    "instrumental_audio": INSTRUMENTAL_FLAC,
    "with_vocals_mov": WITH_VOCALS_MOV,
    "karaoke_lrc": f"{BASE_NAME}{KaraokeFinalise().suffixes['karaoke_lrc']}",
    "end_mov": f"{BASE_NAME}{KaraokeFinalise().suffixes['end_mov']}",
    "end_jpg": f"{BASE_NAME}{KaraokeFinalise().suffixes['end_jpg']}",
}


@pytest.fixture
def finaliser_for_process(mock_logger):
    """Fixture for a finaliser configured for full process tests."""
    config = MINIMAL_CONFIG.copy()
    config.update({
        "brand_prefix": BRAND_PREFIX,
        "organised_dir": ORGANISED_DIR,
        "public_share_dir": PUBLIC_SHARE_DIR,
        "rclone_destination": RCLONE_DEST,
        "organised_dir_rclone_root": ORGANISED_RCLONE_ROOT,
        "youtube_client_secrets_file": YOUTUBE_SECRETS_FILE,
        "youtube_description_file": YOUTUBE_DESC_FILE,
        "discord_webhook_url": DISCORD_WEBHOOK_URL,
        "email_template_file": EMAIL_TEMPLATE_FILE,
        "enable_cdg": True,
        "enable_txt": True,
        "cdg_styles": {"style": "default"}, # Need styles for CDG generation call
    })
    with patch.object(KaraokeFinalise, 'detect_best_aac_codec', return_value='aac'):
        finaliser = KaraokeFinalise(logger=mock_logger, **config)
    # Assume features are enabled after validation for these tests
    finaliser.youtube_upload_enabled = True
    finaliser.discord_notication_enabled = True
    finaliser.folder_organisation_enabled = True
    finaliser.public_share_copy_enabled = True
    finaliser.public_share_rclone_enabled = True
    return finaliser

# --- execute_optional_features Tests ---

@patch.object(KaraokeFinalise, 'upload_final_mp4_to_youtube_with_title_thumbnail')
@patch.object(KaraokeFinalise, 'post_discord_notification')
@patch.object(KaraokeFinalise, 'get_next_brand_code', return_value=f"{BRAND_PREFIX}-0001")
@patch.object(KaraokeFinalise, 'get_existing_brand_code')
@patch.object(KaraokeFinalise, 'move_files_to_brand_code_folder')
@patch.object(KaraokeFinalise, 'copy_final_files_to_public_share_dirs')
@patch.object(KaraokeFinalise, 'sync_public_share_dir_to_rclone_destination')
@patch.object(KaraokeFinalise, 'generate_organised_folder_sharing_link')
def test_execute_optional_features_all_enabled_new_code(
    mock_gen_link, mock_sync, mock_copy, mock_move, mock_get_existing, mock_get_next,
    mock_discord, mock_youtube, finaliser_for_process):
    """Test execute_optional_features with all features enabled and generating a new brand code."""
    finaliser_for_process.keep_brand_code = False
    replace_existing_yt = False
    # Manually set the path as the mocked move won't do it
    finaliser_for_process.new_brand_code_dir_path = os.path.join(ORGANISED_DIR, f"{BRAND_PREFIX}-0001 - {ARTIST} - {TITLE}")

    finaliser_for_process.execute_optional_features(ARTIST, TITLE, BASE_NAME, ALL_INPUT_FILES, ALL_OUTPUT_FILES, replace_existing_yt)

    mock_youtube.assert_called_once_with(ARTIST, TITLE, ALL_INPUT_FILES, ALL_OUTPUT_FILES, replace_existing_yt)
    mock_discord.assert_called_once()
    mock_get_next.assert_called_once()
    mock_get_existing.assert_not_called()
    mock_move.assert_called_once_with(f"{BRAND_PREFIX}-0001", ARTIST, TITLE, ALL_OUTPUT_FILES)
    # Assume move updates the paths, check subsequent calls use the updated path implicitly if needed
    mock_copy.assert_called_once_with(f"{BRAND_PREFIX}-0001", BASE_NAME, ALL_OUTPUT_FILES)
    mock_sync.assert_called_once()
    mock_gen_link.assert_called_once()

@patch.object(KaraokeFinalise, 'upload_final_mp4_to_youtube_with_title_thumbnail')
@patch.object(KaraokeFinalise, 'post_discord_notification')
@patch.object(KaraokeFinalise, 'get_next_brand_code')
@patch.object(KaraokeFinalise, 'get_existing_brand_code', return_value=f"{BRAND_PREFIX}-9999")
@patch.object(KaraokeFinalise, 'move_files_to_brand_code_folder')
@patch.object(KaraokeFinalise, 'copy_final_files_to_public_share_dirs')
@patch.object(KaraokeFinalise, 'sync_public_share_dir_to_rclone_destination')
@patch.object(KaraokeFinalise, 'generate_organised_folder_sharing_link')
@patch('os.getcwd', return_value=f"/path/to/{BRAND_PREFIX}-9999 - {ARTIST} - {TITLE}") # Mock current dir for keep_brand_code
@patch('os.path.basename', return_value=f"{BRAND_PREFIX}-9999 - {ARTIST} - {TITLE}")
def test_execute_optional_features_all_enabled_keep_code(
    mock_basename, mock_getcwd, mock_gen_link, mock_sync, mock_copy, mock_move, mock_get_existing, mock_get_next,
    mock_discord, mock_youtube, finaliser_for_process):
    """Test execute_optional_features with all features enabled and keeping the existing brand code."""
    finaliser_for_process.keep_brand_code = True
    replace_existing_yt = True
    expected_brand_code = f"{BRAND_PREFIX}-9999"
    expected_dir_name = f"{expected_brand_code} - {ARTIST} - {TITLE}"
    expected_dir_path = mock_getcwd.return_value

    finaliser_for_process.execute_optional_features(ARTIST, TITLE, BASE_NAME, ALL_INPUT_FILES, ALL_OUTPUT_FILES, replace_existing_yt)

    mock_youtube.assert_called_once_with(ARTIST, TITLE, ALL_INPUT_FILES, ALL_OUTPUT_FILES, replace_existing_yt)
    mock_discord.assert_called_once()
    mock_get_next.assert_not_called()
    mock_get_existing.assert_called_once()
    mock_move.assert_not_called() # Should not move if keeping code
    # Check state was set correctly without moving
    assert finaliser_for_process.brand_code == expected_brand_code
    assert finaliser_for_process.new_brand_code_dir == expected_dir_name
    assert finaliser_for_process.new_brand_code_dir_path == expected_dir_path
    mock_copy.assert_called_once_with(expected_brand_code, BASE_NAME, ALL_OUTPUT_FILES)
    mock_sync.assert_called_once()
    mock_gen_link.assert_called_once()

@patch.object(KaraokeFinalise, 'upload_final_mp4_to_youtube_with_title_thumbnail')
@patch.object(KaraokeFinalise, 'post_discord_notification')
@patch.object(KaraokeFinalise, 'get_next_brand_code')
@patch.object(KaraokeFinalise, 'move_files_to_brand_code_folder')
@patch.object(KaraokeFinalise, 'copy_final_files_to_public_share_dirs')
@patch.object(KaraokeFinalise, 'sync_public_share_dir_to_rclone_destination')
@patch.object(KaraokeFinalise, 'generate_organised_folder_sharing_link')
def test_execute_optional_features_some_disabled(
    mock_gen_link, mock_sync, mock_copy, mock_move, mock_get_next,
    mock_discord, mock_youtube, finaliser_for_process):
    """Test execute_optional_features with some features disabled."""
    # Disable some features
    finaliser_for_process.discord_notication_enabled = False
    finaliser_for_process.public_share_rclone_enabled = False
    finaliser_for_process.keep_brand_code = False
    # Manually set the path as the mocked move won't do it
    # Use the return value of the mocked get_next_brand_code
    mock_get_next.return_value = f"{BRAND_PREFIX}-0002" # Example next code
    finaliser_for_process.new_brand_code_dir_path = os.path.join(ORGANISED_DIR, f"{BRAND_PREFIX}-0002 - {ARTIST} - {TITLE}")


    finaliser_for_process.execute_optional_features(ARTIST, TITLE, BASE_NAME, ALL_INPUT_FILES, ALL_OUTPUT_FILES, False)

    mock_youtube.assert_called_once()
    mock_discord.assert_not_called() # Disabled
    mock_get_next.assert_called_once()
    mock_move.assert_called_once()
    mock_copy.assert_called_once()
    mock_sync.assert_not_called() # Disabled
    mock_gen_link.assert_called_once() # Still called even if sync disabled

@patch.object(KaraokeFinalise, 'upload_final_mp4_to_youtube_with_title_thumbnail', side_effect=Exception("YT Upload Failed"))
@patch.object(KaraokeFinalise, 'post_discord_notification')
@patch('builtins.input', return_value="manual_video_id") # Provide manual ID when prompted
def test_execute_optional_features_youtube_fails(mock_input, mock_discord, mock_youtube, finaliser_for_process):
    """Test handling when YouTube upload fails and user provides manual ID."""
    # Disable other features to isolate YouTube failure
    finaliser_for_process.folder_organisation_enabled = False
    finaliser_for_process.public_share_copy_enabled = False
    finaliser_for_process.public_share_rclone_enabled = False
    # Enable interactive mode so manual input prompt is shown
    finaliser_for_process.non_interactive = False

    finaliser_for_process.execute_optional_features(ARTIST, TITLE, BASE_NAME, ALL_INPUT_FILES, ALL_OUTPUT_FILES, False)

    mock_youtube.assert_called_once()
    mock_input.assert_called_once_with("Enter the manually uploaded YouTube video ID: ")
    assert finaliser_for_process.youtube_video_id == "manual_video_id"
    assert finaliser_for_process.youtube_url == f"https://www.youtube.com/watch?v=manual_video_id"
    mock_discord.assert_called_once() # Discord should still be called after manual ID entry


@patch.object(KaraokeFinalise, 'upload_final_mp4_to_youtube_with_title_thumbnail')
@patch.object(KaraokeFinalise, 'post_discord_notification')
@patch.object(KaraokeFinalise, 'get_next_brand_code', return_value=f"{BRAND_PREFIX}-0001")
@patch.object(KaraokeFinalise, 'move_files_to_brand_code_folder')
@patch.object(KaraokeFinalise, 'copy_final_files_to_public_share_dirs')
@patch.object(KaraokeFinalise, 'sync_public_share_dir_to_rclone_destination')
@patch.object(KaraokeFinalise, 'generate_organised_folder_sharing_link')
def test_execute_optional_features_discord_failure_continues(
    mock_gen_link, mock_sync, mock_copy, mock_move, mock_get_next,
    mock_discord, mock_youtube, finaliser_for_process):
    """Test that Discord notification failure doesn't crash the job."""
    # Simulate Discord notification failure
    mock_discord.side_effect = Exception("401 Unauthorized: Invalid webhook URL")
    finaliser_for_process.keep_brand_code = False
    finaliser_for_process.new_brand_code_dir_path = os.path.join(ORGANISED_DIR, f"{BRAND_PREFIX}-0001 - {ARTIST} - {TITLE}")

    # Should not raise an exception - should continue with other features
    finaliser_for_process.execute_optional_features(ARTIST, TITLE, BASE_NAME, ALL_INPUT_FILES, ALL_OUTPUT_FILES, False)

    # Discord was called (and failed)
    mock_discord.assert_called_once()
    # But other features should still run
    mock_youtube.assert_called_once()
    mock_get_next.assert_called_once()
    mock_move.assert_called_once()
    mock_copy.assert_called_once()
    mock_sync.assert_called_once()
    mock_gen_link.assert_called_once()
    # Logger should have logged the error (warning)
    finaliser_for_process.logger.error.assert_called()
    finaliser_for_process.logger.warning.assert_called()


# --- process Method Tests ---

@patch.object(KaraokeFinalise, 'validate_input_parameters_for_features')
@patch.object(KaraokeFinalise, 'find_with_vocals_file', return_value=WITH_VOCALS_MOV)
@patch.object(KaraokeFinalise, 'get_names_from_withvocals', return_value=(BASE_NAME, ARTIST, TITLE))
@patch.object(KaraokeFinalise, 'choose_instrumental_audio_file', return_value=INSTRUMENTAL_FLAC)
@patch.object(KaraokeFinalise, 'check_input_files_exist', return_value=ALL_INPUT_FILES)
@patch.object(KaraokeFinalise, 'prepare_output_filenames', return_value=ALL_OUTPUT_FILES)
@patch.object(KaraokeFinalise, 'create_cdg_zip_file')
@patch.object(KaraokeFinalise, 'create_txt_zip_file')
@patch.object(KaraokeFinalise, 'remux_and_encode_output_video_files')
@patch.object(KaraokeFinalise, 'execute_optional_features')
@patch.object(KaraokeFinalise, 'draft_completion_email')
def test_process_full_success(
    mock_draft_email, mock_exec_opt, mock_remux_encode, mock_create_txt, mock_create_cdg,
    mock_prep_out, mock_check_in, mock_choose_instr, mock_get_names, mock_find_vocals,
    mock_validate, finaliser_for_process):
    """Test the full process method with all features enabled."""
    replace_existing = True
    # Mock return values for execute_optional_features state
    finaliser_for_process.youtube_url = "mock_yt_url"
    finaliser_for_process.brand_code = "mock_brand_code"
    finaliser_for_process.new_brand_code_dir_path = "mock_new_dir_path"
    finaliser_for_process.brand_code_dir_sharing_link = "mock_share_link"

    result = finaliser_for_process.process(replace_existing=replace_existing)

    # Check methods called in order
    mock_validate.assert_called_once()
    mock_find_vocals.assert_called_once()
    mock_get_names.assert_called_once_with(WITH_VOCALS_MOV)
    mock_choose_instr.assert_called_once_with(BASE_NAME)
    mock_check_in.assert_called_once_with(BASE_NAME, WITH_VOCALS_MOV, INSTRUMENTAL_FLAC)
    mock_prep_out.assert_called_once_with(BASE_NAME)
    mock_create_cdg.assert_called_once_with(ALL_INPUT_FILES, ALL_OUTPUT_FILES, ARTIST, TITLE)
    mock_create_txt.assert_called_once_with(ALL_INPUT_FILES, ALL_OUTPUT_FILES)
    mock_remux_encode.assert_called_once_with(WITH_VOCALS_MOV, ALL_INPUT_FILES, ALL_OUTPUT_FILES)
    mock_exec_opt.assert_called_once_with(ARTIST, TITLE, BASE_NAME, ALL_INPUT_FILES, ALL_OUTPUT_FILES, replace_existing)
    mock_draft_email.assert_called_once_with(ARTIST, TITLE, "mock_yt_url", "mock_share_link")

    # Check result dictionary
    expected_result = {
        "artist": ARTIST,
        "title": TITLE,
        "video_with_vocals": ALL_OUTPUT_FILES["with_vocals_mp4"],
        "video_with_instrumental": ALL_OUTPUT_FILES["karaoke_mp4"],
        "final_video": ALL_OUTPUT_FILES["final_karaoke_lossless_mp4"],
        "final_video_mkv": ALL_OUTPUT_FILES["final_karaoke_lossless_mkv"],
        "final_video_lossy": ALL_OUTPUT_FILES["final_karaoke_lossy_mp4"],
        "final_video_720p": ALL_OUTPUT_FILES["final_karaoke_lossy_720p_mp4"],
        "youtube_url": "mock_yt_url",
        "brand_code": "mock_brand_code",
        "new_brand_code_dir_path": "mock_new_dir_path",
        "brand_code_dir_sharing_link": "mock_share_link",
        "final_karaoke_cdg_zip": ALL_OUTPUT_FILES["final_karaoke_cdg_zip"], # CDG enabled
        "final_karaoke_txt_zip": ALL_OUTPUT_FILES["final_karaoke_txt_zip"], # TXT enabled
    }
    assert result == expected_result

@patch.object(KaraokeFinalise, 'validate_input_parameters_for_features')
@patch.object(KaraokeFinalise, 'find_with_vocals_file', return_value=WITH_VOCALS_MOV)
@patch.object(KaraokeFinalise, 'get_names_from_withvocals', return_value=(BASE_NAME, ARTIST, TITLE))
@patch.object(KaraokeFinalise, 'choose_instrumental_audio_file', return_value=INSTRUMENTAL_FLAC)
@patch.object(KaraokeFinalise, 'check_input_files_exist', return_value=ALL_INPUT_FILES)
@patch.object(KaraokeFinalise, 'prepare_output_filenames', return_value=ALL_OUTPUT_FILES)
@patch.object(KaraokeFinalise, 'create_cdg_zip_file')
@patch.object(KaraokeFinalise, 'create_txt_zip_file')
@patch.object(KaraokeFinalise, 'remux_and_encode_output_video_files')
@patch.object(KaraokeFinalise, 'execute_optional_features')
@patch.object(KaraokeFinalise, 'draft_completion_email')
def test_process_some_disabled(
    mock_draft_email, mock_exec_opt, mock_remux_encode, mock_create_txt, mock_create_cdg,
    mock_prep_out, mock_check_in, mock_choose_instr, mock_get_names, mock_find_vocals,
    mock_validate, finaliser_for_process):
    """Test the process method with CDG, TXT, and Email disabled."""
    finaliser_for_process.enable_cdg = False
    finaliser_for_process.enable_txt = False
    finaliser_for_process.email_template_file = None # Disable email

    result = finaliser_for_process.process()

    mock_validate.assert_called_once()
    mock_find_vocals.assert_called_once()
    # ... other calls ...
    mock_create_cdg.assert_not_called() # Disabled
    mock_create_txt.assert_not_called() # Disabled
    mock_remux_encode.assert_called_once()
    mock_exec_opt.assert_called_once()
    mock_draft_email.assert_not_called() # Disabled

    # Check result dictionary excludes disabled features
    assert "final_karaoke_cdg_zip" not in result
    assert "final_karaoke_txt_zip" not in result

@patch.object(KaraokeFinalise, 'validate_input_parameters_for_features')
@patch.object(KaraokeFinalise, 'find_with_vocals_file', return_value=WITH_VOCALS_MOV)
@patch.object(KaraokeFinalise, 'get_names_from_withvocals', return_value=(BASE_NAME, ARTIST, TITLE))
@patch.object(KaraokeFinalise, 'choose_instrumental_audio_file', return_value=INSTRUMENTAL_FLAC)
@patch.object(KaraokeFinalise, 'check_input_files_exist', return_value=ALL_INPUT_FILES)
@patch.object(KaraokeFinalise, 'prepare_output_filenames', return_value=ALL_OUTPUT_FILES)
@patch.object(KaraokeFinalise, 'create_cdg_zip_file')
@patch.object(KaraokeFinalise, 'create_txt_zip_file')
@patch.object(KaraokeFinalise, 'remux_and_encode_output_video_files')
@patch.object(KaraokeFinalise, 'execute_optional_features')
@patch.object(KaraokeFinalise, 'draft_completion_email')
def test_process_dry_run(
    mock_draft_email, mock_exec_opt, mock_remux_encode, mock_create_txt, mock_create_cdg,
    mock_prep_out, mock_check_in, mock_choose_instr, mock_get_names, mock_find_vocals,
    mock_validate, finaliser_for_process):
    """Test process method in dry run mode doesn't execute modifying steps."""
    finaliser_for_process.dry_run = True

    finaliser_for_process.process()

    # Check initial steps are called
    finaliser_for_process.logger.warning.assert_called_with("Dry run enabled. No actions will be performed.")
    mock_validate.assert_called_once()
    mock_find_vocals.assert_called_once()
    mock_get_names.assert_called_once()
    mock_choose_instr.assert_called_once()
    mock_check_in.assert_called_once()
    mock_prep_out.assert_called_once()

    # Check modifying steps: CDG/TXT creation *are* called but should handle dry run internally.
    # The main process dry run check happens *after* these.
    mock_create_cdg.assert_called_once()
    mock_create_txt.assert_called_once()
    # These subsequent steps ARE called, but handle dry_run internally.
    # The test mocks these methods, so we just check they were called.
    mock_remux_encode.assert_called_once()
    mock_exec_opt.assert_called_once()
    mock_draft_email.assert_called_once()


@patch.object(KaraokeFinalise, 'validate_input_parameters_for_features')
@patch.object(KaraokeFinalise, 'find_with_vocals_file', return_value=WITH_VOCALS_MOV)
@patch.object(KaraokeFinalise, 'get_names_from_withvocals', return_value=(BASE_NAME, ARTIST, TITLE))
@patch.object(KaraokeFinalise, 'choose_instrumental_audio_file', return_value=INSTRUMENTAL_FLAC)
@patch.object(KaraokeFinalise, 'check_input_files_exist', return_value=ALL_INPUT_FILES)
@patch.object(KaraokeFinalise, 'prepare_output_filenames', return_value=ALL_OUTPUT_FILES)
@patch.object(KaraokeFinalise, 'create_cdg_zip_file')
@patch.object(KaraokeFinalise, 'create_txt_zip_file')
@patch.object(KaraokeFinalise, 'remux_and_encode_output_video_files')
@patch.object(KaraokeFinalise, 'execute_optional_features')
@patch.object(KaraokeFinalise, 'draft_completion_email')
def test_process_no_video_with_cdg_txt_enabled(
    mock_draft_email, mock_exec_opt, mock_remux_encode, mock_create_txt, mock_create_cdg,
    mock_prep_out, mock_check_in, mock_choose_instr, mock_get_names, mock_find_vocals,
    mock_validate, finaliser_for_process):
    """Test process method with --no-video flag and CDG/TXT enabled."""
    finaliser_for_process.enable_cdg = True
    finaliser_for_process.enable_txt = True
    finaliser_for_process.no_video = True

    result = finaliser_for_process.process()

    # Check initial steps are called
    mock_validate.assert_called_once()
    mock_find_vocals.assert_called_once()
    mock_get_names.assert_called_once_with(WITH_VOCALS_MOV)
    mock_choose_instr.assert_called_once_with(BASE_NAME)
    mock_check_in.assert_called_once_with(BASE_NAME, WITH_VOCALS_MOV, INSTRUMENTAL_FLAC)
    mock_prep_out.assert_called_once_with(BASE_NAME)

    # CDG and TXT generation should happen
    mock_create_cdg.assert_called_once_with(ALL_INPUT_FILES, ALL_OUTPUT_FILES, ARTIST, TITLE)
    mock_create_txt.assert_called_once_with(ALL_INPUT_FILES, ALL_OUTPUT_FILES)

    # Video encoding and distribution should be skipped
    mock_remux_encode.assert_not_called()
    mock_exec_opt.assert_not_called()
    mock_draft_email.assert_not_called()

    # Check result dictionary includes CDG/TXT and has video keys set to None
    assert result["artist"] == ARTIST
    assert result["title"] == TITLE
    assert result["final_karaoke_cdg_zip"] == ALL_OUTPUT_FILES["final_karaoke_cdg_zip"]
    assert result["final_karaoke_txt_zip"] == ALL_OUTPUT_FILES["final_karaoke_txt_zip"]
    assert result["video_with_vocals"] is None
    assert result["video_with_instrumental"] is None
    assert result["final_video"] is None
    assert result["final_video_mkv"] is None
    assert result["final_video_lossy"] is None
    assert result["final_video_720p"] is None
    assert result["youtube_url"] is None
    assert result["brand_code"] is None
    assert result["new_brand_code_dir_path"] is None
    assert result["brand_code_dir_sharing_link"] is None


@patch.object(KaraokeFinalise, 'validate_input_parameters_for_features')
@patch.object(KaraokeFinalise, 'find_with_vocals_file', return_value=WITH_VOCALS_MOV)
@patch.object(KaraokeFinalise, 'get_names_from_withvocals', return_value=(BASE_NAME, ARTIST, TITLE))
@patch.object(KaraokeFinalise, 'choose_instrumental_audio_file', return_value=INSTRUMENTAL_FLAC)
@patch.object(KaraokeFinalise, 'check_input_files_exist', return_value=ALL_INPUT_FILES)
@patch.object(KaraokeFinalise, 'prepare_output_filenames', return_value=ALL_OUTPUT_FILES)
@patch.object(KaraokeFinalise, 'create_cdg_zip_file')
@patch.object(KaraokeFinalise, 'create_txt_zip_file')
@patch.object(KaraokeFinalise, 'remux_and_encode_output_video_files')
@patch.object(KaraokeFinalise, 'execute_optional_features')
@patch.object(KaraokeFinalise, 'draft_completion_email')
def test_process_no_video_nothing_enabled(
    mock_draft_email, mock_exec_opt, mock_remux_encode, mock_create_txt, mock_create_cdg,
    mock_prep_out, mock_check_in, mock_choose_instr, mock_get_names, mock_find_vocals,
    mock_validate, finaliser_for_process):
    """Test process method with --no-video flag and no output formats enabled."""
    finaliser_for_process.enable_cdg = False
    finaliser_for_process.enable_txt = False
    finaliser_for_process.no_video = True

    result = finaliser_for_process.process()

    # Check initial steps are called
    mock_validate.assert_called_once()
    mock_find_vocals.assert_called_once()
    mock_get_names.assert_called_once_with(WITH_VOCALS_MOV)
    mock_choose_instr.assert_called_once_with(BASE_NAME)
    mock_check_in.assert_called_once_with(BASE_NAME, WITH_VOCALS_MOV, INSTRUMENTAL_FLAC)
    mock_prep_out.assert_called_once_with(BASE_NAME)

    # CDG and TXT generation should NOT happen (disabled)
    mock_create_cdg.assert_not_called()
    mock_create_txt.assert_not_called()

    # Video encoding and distribution should be skipped
    mock_remux_encode.assert_not_called()
    mock_exec_opt.assert_not_called()
    mock_draft_email.assert_not_called()

    # Check result dictionary includes artist/title and video keys set to None
    assert result["artist"] == ARTIST
    assert result["title"] == TITLE
    assert "final_karaoke_cdg_zip" not in result
    assert "final_karaoke_txt_zip" not in result
    assert result["video_with_vocals"] is None
    assert result["video_with_instrumental"] is None
    assert result["final_video"] is None
    assert result["final_video_mkv"] is None
    assert result["final_video_lossy"] is None
    assert result["final_video_720p"] is None
    assert result["youtube_url"] is None
    assert result["brand_code"] is None
    assert result["new_brand_code_dir_path"] is None
    assert result["brand_code_dir_sharing_link"] is None
