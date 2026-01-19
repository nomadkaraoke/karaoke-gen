import pytest
import asyncio
import argparse
import os
import logging
import sys
from unittest.mock import patch, MagicMock, AsyncMock, mock_open, call

# Import the module/functions to test
from karaoke_gen.utils import gen_cli

# Mark all tests in this module as asyncio
pytestmark = pytest.mark.asyncio

# Sample style params JSON
SAMPLE_STYLE_JSON = '{"cdg": {"some_style": "value"}}'

# Mock return value for KaraokeFinalise.process
MOCK_FINAL_TRACK = {
    "artist": "Test Artist",
    "title": "Test Title",
    "video_with_vocals": "test_wv.mp4",
    "video_with_instrumental": "test_wi.mp4",
    "final_video": "test_final.mp4",
    "final_video_mkv": "test_final.mkv",
    "final_video_lossy": "test_final_lossy.mp4",
    "final_video_720p": "test_final_720p.mp4",
    "final_karaoke_cdg_zip": "test_cdg.zip",
    "final_karaoke_txt_zip": "test_txt.zip",
    "brand_code": "TEST-0001",
    "new_brand_code_dir_path": "/path/to/TEST-0001 - Test Artist - Test Title",
    "youtube_url": "https://youtube.com/watch?v=1234",
    "brand_code_dir_sharing_link": "https://share.link/folder",
}

# Mock return value for KaraokePrep.process
MOCK_PREP_TRACK = {
    "artist": "Test Artist",
    "title": "Test Title",
    "input_media": "input.mp3",
    "input_audio_wav": "input.wav",
    "input_still_image": "input.jpg",
    "lyrics": "lyrics.txt",
    "processed_lyrics": "lyrics.json",
    "track_output_dir": "/fake/output/Test Artist - Test Title",
    "separated_audio": {
        "clean_instrumental": {"instrumental": "inst.flac", "vocals": "vocals.flac"},
        "other_stems": {"model1": {"bass": "bass.flac", "drums": "drums.flac"}},
        "backing_vocals": {"model2": {"backing": "backing.flac"}},
        "combined_instrumentals": {"model1": "combined_inst.flac"},
    },
}


@pytest.fixture
def mock_base_args():
    """Fixture for common mock command line arguments."""
    # Most args will be added/overridden in tests
    return argparse.Namespace(
        args=[], # Positional args
        prep_only=False,
        finalise_only=False,
        edit_lyrics=False,
        test_email_template=False,
        skip_transcription=False,
        skip_separation=False,
        skip_lyrics=False,
        lyrics_only=False,
        log_level="info",
        dry_run=False,
        render_bounding_boxes=False,
        filename_pattern=None,
        output_dir=".",
        no_track_subfolders=True, # Corresponds to create_track_subfolders=True
        lossless_output_format="FLAC",
        output_png=True,
        output_jpg=True,
        clean_instrumental_model="model_bs_roformer_ep_317_sdr_12.9755.ckpt",
        backing_vocals_models=["mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"],
        other_stems_models=["htdemucs_6s.yaml"],
        model_file_dir="/tmp/audio-separator-models", # Default value might vary
        existing_instrumental=None,
        instrumental_format="flac",
        lyrics_artist=None,
        lyrics_title=None,
        lyrics_file=None,
        subtitle_offset_ms=0,
        skip_transcription_review=False,
        style_params_json=None,
        style_override=None,
        background_video=None,
        background_video_darkness=0,
        auto_download=False,  # New flacfetch parameter
        enable_cdg=False,
        enable_txt=False,
        brand_prefix=None,
        organised_dir=None,
        organised_dir_rclone_root=None,
        public_share_dir=None,
        youtube_client_secrets_file=None,
        youtube_description_file=None,
        rclone_destination=None,
        discord_webhook_url=None,
        email_template_file=None,
        keep_brand_code=False,
        no_video=False,
        yes=False, # non_interactive
    )

@pytest.fixture
def mock_logger():
    """Fixture for a mock logger."""
    logger = MagicMock(spec=logging.Logger)
    logger.level = logging.INFO # Default level
    return logger

@pytest.fixture(autouse=True)
def mock_pyperclip():
    """Automatically mock pyperclip."""
    with patch("karaoke_gen.utils.gen_cli.pyperclip", MagicMock()) as mock_clip:
        yield mock_clip

@pytest.fixture(autouse=True)
def mock_sleep():
    """Automatically mock time.sleep."""
    with patch("karaoke_gen.utils.gen_cli.time.sleep", MagicMock()) as mock_sl:
        yield mock_sl

# --- Test Argument Parsing Logic ---

@patch("karaoke_gen.utils.gen_cli.is_url", return_value=True)
@patch("karaoke_gen.utils.gen_cli.is_file", return_value=False)
@patch("karaoke_gen.utils.gen_cli.KaraokePrep") # Use default MagicMock for class
async def test_arg_parsing_url_only(mock_kprep_class, mock_isfile, mock_isurl, mock_base_args, mock_logger, capsys):
    """Test URL-only argument parsing."""
    mock_base_args.args = ["https://example.com/song.mp3"]
    mock_kprep_instance = MagicMock()
    mock_kprep_class.return_value = mock_kprep_instance
    mock_kprep_instance.process = AsyncMock(return_value=[MOCK_PREP_TRACK])

    with patch("karaoke_gen.utils.gen_cli.argparse.ArgumentParser") as mock_parser:
        mock_parser.return_value.parse_args.return_value = mock_base_args
        await gen_cli.async_main()

    mock_kprep_class.assert_called_once()
    # Verify that input_media=URL is passed
    assert mock_kprep_class.call_args.kwargs["input_media"] == "https://example.com/song.mp3"
    mock_kprep_instance.process.assert_awaited_once()
    # Verify warning in log (captured in stderr since logger.propagate = False)
    captured = capsys.readouterr()
    assert "Input media provided without Artist and Title" in captured.err

@patch("karaoke_gen.utils.gen_cli.is_url", return_value=True)
@patch("karaoke_gen.utils.gen_cli.is_file", return_value=False)
@patch("karaoke_gen.utils.gen_cli.KaraokePrep") # Use default MagicMock for class
async def test_arg_parsing_url_artist_title(mock_kprep_class, mock_isfile, mock_isurl, mock_base_args, mock_logger):
    """Test parsing: URL, Artist, Title."""
    mock_base_args.args = ["http://example.com/video.mp4", "URL Artist", "URL Title"]
    mock_kprep_instance = MagicMock()
    mock_kprep_class.return_value = mock_kprep_instance
    mock_kprep_instance.process = AsyncMock(return_value=[MOCK_PREP_TRACK])

    with patch("karaoke_gen.utils.gen_cli.argparse.ArgumentParser") as mock_parser:
        mock_parser.return_value.parse_args.return_value = mock_base_args
        await gen_cli.async_main()

    mock_kprep_class.assert_called_once()
    call_kwargs = mock_kprep_class.call_args.kwargs
    assert call_kwargs["input_media"] == "http://example.com/video.mp4"
    assert call_kwargs["artist"] == "URL Artist"
    mock_kprep_instance.process.assert_awaited_once()
    assert call_kwargs["title"] == "URL Title"

@patch("karaoke_gen.utils.gen_cli.is_url", return_value=False)
@patch("karaoke_gen.utils.gen_cli.is_file", return_value=True)
@patch("karaoke_gen.utils.gen_cli.KaraokePrep") # Use default MagicMock for class
async def test_arg_parsing_file_artist_title(mock_kprep_class, mock_isfile, mock_isurl, mock_base_args, mock_logger):
    """Test parsing: Local File, Artist, Title."""
    mock_base_args.args = ["/path/to/song.mp3", "File Artist", "File Title"]
    mock_kprep_instance = MagicMock()
    mock_kprep_class.return_value = mock_kprep_instance
    mock_kprep_instance.process = AsyncMock(return_value=[MOCK_PREP_TRACK])

    with patch("karaoke_gen.utils.gen_cli.argparse.ArgumentParser") as mock_parser:
        mock_parser.return_value.parse_args.return_value = mock_base_args
        await gen_cli.async_main()

    mock_kprep_class.assert_called_once()
    call_kwargs = mock_kprep_class.call_args.kwargs
    assert call_kwargs["input_media"] == "/path/to/song.mp3"
    assert call_kwargs["artist"] == "File Artist"
    mock_kprep_instance.process.assert_awaited_once()
    assert call_kwargs["title"] == "File Title"

@patch("karaoke_gen.utils.gen_cli.is_url", return_value=False)
@patch("karaoke_gen.utils.gen_cli.is_file", return_value=False)
@patch("karaoke_gen.utils.gen_cli.os.path.isdir", return_value=False)
@patch("karaoke_gen.utils.gen_cli.KaraokePrep") # Use default MagicMock for class
async def test_arg_parsing_artist_title_only(mock_kprep_class, mock_isdir, mock_isfile, mock_isurl, mock_base_args, mock_logger, capsys):
    """Test Artist and Title only argument parsing."""
    mock_base_args.args = ["Test Artist", "Test Title"]
    mock_kprep_instance = MagicMock()
    mock_kprep_class.return_value = mock_kprep_instance
    mock_kprep_instance.process = AsyncMock(return_value=[MOCK_PREP_TRACK])

    with patch("karaoke_gen.utils.gen_cli.argparse.ArgumentParser") as mock_parser:
        mock_parser.return_value.parse_args.return_value = mock_base_args
        await gen_cli.async_main()

    mock_kprep_class.assert_called_once()
    # Verify that artist and title are passed, but not input_media
    assert mock_kprep_class.call_args.kwargs["artist"] == "Test Artist"
    assert mock_kprep_class.call_args.kwargs["title"] == "Test Title"
    assert mock_kprep_class.call_args.kwargs["input_media"] is None
    mock_kprep_instance.process.assert_awaited_once()
    # Verify message about flacfetch search is shown (in stderr since logger.propagate = False)
    captured = capsys.readouterr()
    assert "flacfetch will search for" in captured.err

@patch("karaoke_gen.utils.gen_cli.is_url", return_value=False)
@patch("karaoke_gen.utils.gen_cli.is_file", return_value=False)
@patch("karaoke_gen.utils.gen_cli.os.path.isdir", return_value=True)
@patch("karaoke_gen.utils.gen_cli.KaraokePrep") # Use default MagicMock for class
async def test_arg_parsing_folder_artist_pattern(mock_kprep_class, mock_isdir, mock_isfile, mock_isurl, mock_base_args, mock_logger):
    """Test parsing: Folder, Artist, Pattern."""
    mock_base_args.args = ["/path/to/folder", "Folder Artist"]
    mock_base_args.filename_pattern = r"(?P<index>\d+) - (?P<title>.+)\.mp3"
    mock_kprep_instance = MagicMock()
    mock_kprep_class.return_value = mock_kprep_instance
    mock_kprep_instance.process = AsyncMock(return_value=[MOCK_PREP_TRACK])

    with patch("karaoke_gen.utils.gen_cli.argparse.ArgumentParser") as mock_parser:
        mock_parser.return_value.parse_args.return_value = mock_base_args
        await gen_cli.async_main()

    mock_kprep_class.assert_called_once()
    call_kwargs = mock_kprep_class.call_args.kwargs
    assert call_kwargs["input_media"] == "/path/to/folder"
    assert call_kwargs["artist"] == "Folder Artist"
    mock_kprep_instance.process.assert_awaited_once()
    assert call_kwargs["filename_pattern"] == r"(?P<index>\d+) - (?P<title>.+)\.mp3"

@patch("karaoke_gen.utils.gen_cli.is_url", return_value=False)
@patch("karaoke_gen.utils.gen_cli.is_file", return_value=False)
@patch("karaoke_gen.utils.gen_cli.os.path.isdir", return_value=True)
@patch("karaoke_gen.utils.gen_cli.sys.exit")
async def test_arg_parsing_folder_missing_pattern(mock_exit, mock_isdir, mock_isfile, mock_isurl, mock_base_args, mock_logger):
    """Test parsing exits if folder provided without filename_pattern."""
    mock_base_args.args = ["/path/to/folder", "Folder Artist"]
    mock_base_args.filename_pattern = None # Missing pattern
    with patch("karaoke_gen.utils.gen_cli.argparse.ArgumentParser") as mock_parser, \
         patch("karaoke_gen.utils.gen_cli.logging.getLogger", return_value=mock_logger):
        mock_parser.return_value.parse_args.return_value = mock_base_args
        await gen_cli.async_main()

    # Just check that logger.error was called and exit was called
    assert mock_logger.error.called
    mock_exit.assert_called_once_with(1)

# --- Test Workflow Modes ---

@patch("karaoke_gen.utils.gen_cli.KaraokePrep") # Use default MagicMock for class
@patch("karaoke_gen.utils.gen_cli.KaraokeFinalise") # Should not be called
async def test_workflow_prep_only(mock_kfinalise, mock_kprep_class, mock_base_args, mock_logger):
    """Test --prep-only workflow."""
    mock_base_args.args = ["Artist", "Title"]
    mock_base_args.prep_only = True
    mock_kprep_instance = MagicMock()
    mock_kprep_class.return_value = mock_kprep_instance
    mock_kprep_instance.process = AsyncMock(return_value=[MOCK_PREP_TRACK]) # Simulate prep output

    with patch("karaoke_gen.utils.gen_cli.argparse.ArgumentParser") as mock_parser, \
         patch("karaoke_gen.utils.gen_cli.logging.getLogger", return_value=mock_logger):
        mock_parser.return_value.parse_args.return_value = mock_base_args
        await gen_cli.async_main()

    mock_kprep_class.assert_called_once()
    mock_kprep_instance.process.assert_awaited_once()
    mock_kfinalise.assert_not_called() # Finalise should be skipped
    # We'll just verify that the app exits correctly without checking specific log messages
    assert mock_logger.info.called


@patch("karaoke_gen.utils.gen_cli.KaraokePrep") # Should not be called
@patch("karaoke_gen.utils.gen_cli.KaraokeFinalise")
@patch("builtins.open", new_callable=mock_open) # Mock open for style JSON if CDG enabled
@patch("karaoke_gen.utils.gen_cli.run_instrumental_review", return_value="selected_instrumental.flac")
async def test_workflow_finalise_only(mock_run_review, mock_open, mock_kfinalise, mock_kprep, mock_base_args, mock_logger):
    """Test --finalise-only workflow."""
    mock_base_args.finalise_only = True
    mock_kfinalise_instance = mock_kfinalise.return_value
    mock_kfinalise_instance.process = MagicMock(return_value=MOCK_FINAL_TRACK)

    with patch("karaoke_gen.utils.gen_cli.argparse.ArgumentParser") as mock_parser, \
         patch("karaoke_gen.utils.gen_cli.logging.getLogger", return_value=mock_logger):
        mock_parser.return_value.parse_args.return_value = mock_base_args
        await gen_cli.async_main()

    mock_kprep.assert_not_called() # Prep should be skipped
    mock_kfinalise.assert_called_once()
    mock_kfinalise_instance.process.assert_called_once()
    # Just verify that we do log something
    assert mock_logger.info.called


@patch("karaoke_gen.utils.gen_cli.KaraokePrep") # Use default MagicMock for class
@patch("karaoke_gen.utils.gen_cli.KaraokeFinalise")
@patch("karaoke_gen.utils.gen_cli.os.path.basename", return_value="Edit Artist - Edit Title")
@patch("karaoke_gen.utils.gen_cli.os.getcwd", return_value="/fake/path/Edit Artist - Edit Title")
@patch("builtins.open", new_callable=mock_open) # Mock open for style JSON if CDG enabled
@patch("karaoke_gen.utils.gen_cli.run_instrumental_review", return_value="selected_instrumental.flac")
async def test_workflow_edit_lyrics(mock_run_review, mock_open, mock_getcwd, mock_basename, mock_kfinalise, mock_kprep_class, mock_base_args, mock_logger):
    """Test --edit-lyrics workflow."""
    mock_base_args.edit_lyrics = True
    mock_base_args.enable_cdg = False # Simplify for now

    # Set up the KaraokePrep mock properly
    mock_kprep_instance = MagicMock() # Instance mock
    mock_kprep_class.return_value = mock_kprep_instance
    # Mock the file_handler attribute and its backup_existing_outputs method
    mock_file_handler = MagicMock()
    mock_file_handler.backup_existing_outputs = MagicMock(return_value="/fake/path/Edit Artist - Edit Title/input.wav")
    mock_kprep_instance.file_handler = mock_file_handler
    mock_kprep_instance.process = AsyncMock(return_value=[MOCK_PREP_TRACK]) # Async method

    mock_kfinalise_instance = mock_kfinalise.return_value
    mock_kfinalise_instance.process = MagicMock(return_value=MOCK_FINAL_TRACK)

    with patch("karaoke_gen.utils.gen_cli.argparse.ArgumentParser") as mock_parser:
        mock_parser.return_value.parse_args.return_value = mock_base_args
        await gen_cli.async_main()

    # Check Prep call
    mock_kprep_class.assert_called_once()
    prep_call_kwargs = mock_kprep_class.call_args.kwargs
    assert prep_call_kwargs["artist"] == "Edit Artist"
    assert prep_call_kwargs["title"] == "Edit Title"
    assert prep_call_kwargs["input_media"] is None  # Set to None initially
    assert prep_call_kwargs["skip_separation"] is True # Should skip separation in edit mode
    assert prep_call_kwargs["skip_lyrics"] is False
    assert prep_call_kwargs["skip_transcription"] is False
    assert prep_call_kwargs["create_track_subfolders"] is False # Already in folder

    # Assert that the mocked method on the file_handler was called
    mock_kprep_instance.file_handler.backup_existing_outputs.assert_called_once()
    mock_kprep_instance.process.assert_awaited_once()

    # Check Finalise call
    mock_kfinalise.assert_called_once()
    finalise_call_kwargs = mock_kfinalise.call_args.kwargs
    assert finalise_call_kwargs["keep_brand_code"] is True # Should keep brand code
    assert finalise_call_kwargs["non_interactive"] is False # Default unless -y

    mock_kfinalise_instance.process.assert_called_once_with(replace_existing=True) # Should replace


@patch("karaoke_gen.utils.gen_cli.KaraokeFinalise")
async def test_workflow_test_email_template(mock_kfinalise, mock_base_args, mock_logger):
    """Test --test_email_template workflow."""
    mock_base_args.test_email_template = True
    mock_base_args.email_template_file = "template.txt"
    mock_kfinalise_instance = mock_kfinalise.return_value

    with patch("karaoke_gen.utils.gen_cli.argparse.ArgumentParser") as mock_parser, \
         patch("karaoke_gen.utils.gen_cli.logging.getLogger", return_value=mock_logger):
        mock_parser.return_value.parse_args.return_value = mock_base_args
        await gen_cli.async_main()

    mock_kfinalise.assert_called_once()
    finalise_call_kwargs = mock_kfinalise.call_args.kwargs
    assert finalise_call_kwargs["email_template_file"] == "template.txt"
    mock_kfinalise_instance.test_email_template.assert_called_once()
    assert mock_logger.info.called


@patch("karaoke_gen.utils.gen_cli.KaraokePrep") # Use default MagicMock for class
@patch("karaoke_gen.utils.gen_cli.KaraokeFinalise")
@patch("karaoke_gen.utils.gen_cli.run_instrumental_review", return_value="selected_instrumental.flac")
async def test_workflow_lyrics_only(mock_run_review, mock_kfinalise, mock_kprep_class, mock_base_args, mock_logger):
    """Test --lyrics-only workflow sets environment variables and skips."""
    mock_base_args.args = ["Artist", "Title"]
    mock_base_args.lyrics_only = True
    mock_kprep_instance = MagicMock()
    mock_kprep_class.return_value = mock_kprep_instance
    mock_kprep_instance.process = AsyncMock(return_value=[MOCK_PREP_TRACK])
    mock_kfinalise_instance = mock_kfinalise.return_value
    mock_kfinalise_instance.process = MagicMock(return_value=MOCK_FINAL_TRACK)

    with patch("karaoke_gen.utils.gen_cli.argparse.ArgumentParser") as mock_parser, \
         patch("karaoke_gen.utils.gen_cli.logging.getLogger", return_value=mock_logger), \
         patch.dict("karaoke_gen.utils.gen_cli.os.environ", {}, clear=True), \
         patch("karaoke_gen.utils.gen_cli.os.environ.get") as mock_environ_get:
        mock_parser.return_value.parse_args.return_value = mock_base_args
        # Set up the mock to return "1" for our environment variables
        mock_environ_get.side_effect = lambda key, default=None: "1" if key in ["KARAOKE_GEN_SKIP_AUDIO_SEPARATION", "KARAOKE_GEN_SKIP_TITLE_END_SCREENS"] else default
        await gen_cli.async_main()

    # Since we can't reliably test the os.environ, check that skip_separation was set
    mock_kprep_class.assert_called_once()
    prep_kwargs = mock_kprep_class.call_args.kwargs
    assert prep_kwargs["skip_separation"] is True
    mock_kprep_instance.process.assert_awaited_once()
    assert mock_logger.info.called


# --- Test Finalise CDG Style Loading ---

@patch("karaoke_gen.utils.gen_cli.KaraokePrep") # Use default MagicMock for class
@patch("karaoke_gen.utils.gen_cli.KaraokeFinalise")
@patch("builtins.open", new_callable=mock_open, read_data=SAMPLE_STYLE_JSON)
@patch("karaoke_gen.utils.gen_cli.os.chdir") # Mock chdir
@patch("karaoke_gen.utils.gen_cli.os.path.exists", return_value=True) # Assume track dir exists
@patch("karaoke_gen.utils.gen_cli.run_instrumental_review", return_value="selected_instrumental.flac")
async def test_finalise_cdg_style_loading(mock_run_review, mock_exists, mock_chdir, mock_open, mock_kfinalise, mock_kprep_class, mock_base_args):
    """Test that CDG styles are loaded correctly when --enable_cdg is used."""
    mock_base_args.args = ["Artist", "Title"]
    mock_base_args.enable_cdg = True
    mock_base_args.style_params_json = "/fake/styles.json"
    expected_cdg_styles = {"some_style": "value"}

    mock_kprep_instance = MagicMock()
    mock_kprep_class.return_value = mock_kprep_instance
    mock_kprep_instance.process = AsyncMock(return_value=[MOCK_PREP_TRACK])
    mock_kfinalise.return_value.process = MagicMock(return_value=MOCK_FINAL_TRACK)

    with patch("karaoke_gen.utils.gen_cli.argparse.ArgumentParser") as mock_parser:
        mock_parser.return_value.parse_args.return_value = mock_base_args
        await gen_cli.async_main()

    mock_kprep_instance.process.assert_awaited_once()
    # Check open was called for the style file
    mock_open.assert_called_with("/fake/styles.json", "r")

    # Check KaraokeFinalise was called with the loaded styles
    mock_kfinalise.assert_called_once()
    finalise_call_kwargs = mock_kfinalise.call_args.kwargs
    assert finalise_call_kwargs["enable_cdg"] is True
    assert finalise_call_kwargs["cdg_styles"] == expected_cdg_styles


@patch("karaoke_gen.utils.gen_cli.KaraokePrep") # Use default MagicMock for class
@patch("karaoke_gen.utils.gen_cli.KaraokeFinalise")
@patch("builtins.open", side_effect=FileNotFoundError)
@patch("karaoke_gen.utils.gen_cli.os.chdir")
@patch("karaoke_gen.utils.gen_cli.os.path.exists", return_value=True)
@patch("karaoke_gen.utils.gen_cli.sys.exit")
@patch("karaoke_gen.utils.gen_cli.run_instrumental_review", return_value="selected_instrumental.flac")
async def test_finalise_cdg_style_file_not_found(mock_run_review, mock_exit, mock_exists, mock_chdir, mock_open, mock_kfinalise, mock_kprep_class, mock_base_args, mock_logger):
    """Test exit if CDG enabled but style file not found."""
    mock_base_args.args = ["Artist", "Title"]
    mock_base_args.enable_cdg = True
    mock_base_args.style_params_json = "/fake/styles.json"

    mock_kprep_instance = MagicMock()
    mock_kprep_class.return_value = mock_kprep_instance
    mock_kprep_instance.process = AsyncMock(return_value=[MOCK_PREP_TRACK])

    with patch("karaoke_gen.utils.gen_cli.argparse.ArgumentParser") as mock_parser, \
         patch("karaoke_gen.utils.gen_cli.logging.getLogger", return_value=mock_logger):
        mock_parser.return_value.parse_args.return_value = mock_base_args
        await gen_cli.async_main()

    mock_kprep_instance.process.assert_awaited_once()
    mock_open.assert_called_with("/fake/styles.json", "r")
    assert mock_logger.error.called
    mock_exit.assert_called_once_with(1)
    mock_kfinalise.assert_not_called()


@patch("karaoke_gen.utils.gen_cli.KaraokePrep") # Use default MagicMock for class
@patch("karaoke_gen.utils.gen_cli.KaraokeFinalise")
@patch("builtins.open", new_callable=mock_open, read_data="invalid json") # Invalid JSON
@patch("karaoke_gen.utils.gen_cli.os.chdir")
@patch("karaoke_gen.utils.gen_cli.os.path.exists", return_value=True)
@patch("karaoke_gen.utils.gen_cli.sys.exit")
@patch("karaoke_gen.utils.gen_cli.run_instrumental_review", return_value="selected_instrumental.flac")
async def test_finalise_cdg_style_invalid_json(mock_run_review, mock_exit, mock_exists, mock_chdir, mock_open, mock_kfinalise, mock_kprep_class, mock_base_args, mock_logger):
    """Test exit if CDG enabled but style file has invalid JSON."""
    mock_base_args.args = ["Artist", "Title"]
    mock_base_args.enable_cdg = True
    mock_base_args.style_params_json = "/fake/styles.json"

    mock_kprep_instance = MagicMock()
    mock_kprep_class.return_value = mock_kprep_instance
    mock_kprep_instance.process = AsyncMock(return_value=[MOCK_PREP_TRACK])

    with patch("karaoke_gen.utils.gen_cli.argparse.ArgumentParser") as mock_parser, \
         patch("karaoke_gen.utils.gen_cli.logging.getLogger", return_value=mock_logger):
        mock_parser.return_value.parse_args.return_value = mock_base_args
        await gen_cli.async_main()

    mock_kprep_instance.process.assert_awaited_once()
    mock_open.assert_called_with("/fake/styles.json", "r")
    # Assert sys.exit was called
    mock_exit.assert_called_once_with(1)
    # Verify KaraokeFinalise was not instantiated (we should exit before that)
    mock_kfinalise.assert_not_called()


@patch("karaoke_gen.utils.gen_cli.KaraokePrep") # Use default MagicMock for class
@patch("karaoke_gen.utils.gen_cli.KaraokeFinalise")
async def test_error_handling_kprep_failure(mock_kfinalise, mock_kprep_class, mock_base_args, mock_logger):
    """Test error handling if KaraokePrep.process fails."""
    mock_base_args.args = ["Artist", "Title"]
    mock_kprep_instance = MagicMock()
    mock_kprep_class.return_value = mock_kprep_instance
    mock_kprep_instance.process = AsyncMock(side_effect=Exception("KPrep Failed!"))

    with patch("karaoke_gen.utils.gen_cli.argparse.ArgumentParser") as mock_parser:
        mock_parser.return_value.parse_args.return_value = mock_base_args
        # Expect the exception to propagate
        with pytest.raises(Exception, match="KPrep Failed!"):
            await gen_cli.async_main()

    mock_kprep_class.assert_called_once()
    mock_kprep_instance.process.assert_awaited_once()
    mock_kfinalise.assert_not_called() # Should not reach finalise


@patch("karaoke_gen.utils.gen_cli.KaraokePrep") # Use default MagicMock for class
@patch("karaoke_gen.utils.gen_cli.KaraokeFinalise")
@patch("karaoke_gen.utils.gen_cli.os.chdir")
@patch("karaoke_gen.utils.gen_cli.os.path.exists", return_value=True)
@patch("karaoke_gen.utils.gen_cli.run_instrumental_review", return_value="selected_instrumental.flac")
async def test_error_handling_kfinalise_failure(mock_run_review, mock_exists, mock_chdir, mock_kfinalise, mock_kprep_class, mock_base_args, mock_logger, capsys):
    """Test error handling if KaraokeFinalise.process fails."""
    mock_base_args.args = ["Artist", "Title"]
    mock_kprep_instance = MagicMock()
    mock_kprep_class.return_value = mock_kprep_instance
    mock_kprep_instance.process = AsyncMock(return_value=[MOCK_PREP_TRACK]) # Prep succeeds
    mock_kfinalise_instance = mock_kfinalise.return_value
    mock_kfinalise_instance.process.side_effect = Exception("KFinalise Failed!")

    with patch("karaoke_gen.utils.gen_cli.argparse.ArgumentParser") as mock_parser:
        mock_parser.return_value.parse_args.return_value = mock_base_args
        # Expect the exception to propagate
        with pytest.raises(Exception, match="KFinalise Failed!"):
            await gen_cli.async_main()

    mock_kprep_class.assert_called_once()
    mock_kprep_instance.process.assert_awaited_once()
    mock_chdir.assert_called_once_with(MOCK_PREP_TRACK["track_output_dir"]) # Should chdir before finalise
    mock_kfinalise.assert_called_once() # Finalise is called
    mock_kfinalise_instance.process.assert_called_once() # Process is called
    
    # Check the error message in log (in stderr since logger.propagate = False)
    captured = capsys.readouterr()
    assert "An error occurred during finalisation, see stack trace below: KFinalise Failed!" in captured.err


@patch("karaoke_gen.utils.gen_cli.KaraokePrep") # Use default MagicMock for class
@patch("karaoke_gen.utils.gen_cli.KaraokeFinalise")
@patch("karaoke_gen.utils.gen_cli.run_instrumental_review", return_value="selected_instrumental.flac")
async def test_argument_passthrough(mock_run_review, mock_kfinalise, mock_kprep_class, mock_base_args):
    """Test that various arguments are passed correctly to KaraokePrep/Finalise."""
    mock_base_args.args = ["Artist", "Title"]
    mock_base_args.render_bounding_boxes = True
    mock_base_args.skip_separation = True
    mock_base_args.skip_lyrics = True
    mock_base_args.skip_transcription = True
    mock_base_args.skip_transcription_review = True
    mock_base_args.subtitle_offset_ms = -100
    mock_base_args.existing_instrumental = "/path/to/inst.wav"
    mock_base_args.lyrics_artist = "Override Artist"
    mock_base_args.lyrics_title = "Override Title"
    mock_base_args.lyrics_file = "/path/to/lyrics.txt"
    mock_base_args.style_params_json = "/path/to/styles.json"
    mock_base_args.instrumental_format = "mp3"
    mock_base_args.yes = True # non_interactive
    mock_base_args.no_video = True

    mock_kprep_instance = MagicMock()
    mock_kprep_class.return_value = mock_kprep_instance
    mock_kprep_instance.process = AsyncMock(return_value=[MOCK_PREP_TRACK])
    mock_kfinalise.return_value.process = MagicMock(return_value=MOCK_FINAL_TRACK)

    with patch("karaoke_gen.utils.gen_cli.argparse.ArgumentParser") as mock_parser, \
         patch("karaoke_gen.utils.gen_cli.os.chdir"), \
         patch("karaoke_gen.utils.gen_cli.os.path.exists", return_value=True):
        mock_parser.return_value.parse_args.return_value = mock_base_args
        await gen_cli.async_main()

    # Check KaraokePrep args
    mock_kprep_class.assert_called_once()
    prep_kwargs = mock_kprep_class.call_args.kwargs
    assert prep_kwargs["render_bounding_boxes"] is True
    assert prep_kwargs["skip_separation"] is True
    assert prep_kwargs["skip_lyrics"] is True
    assert prep_kwargs["skip_transcription"] is True
    assert prep_kwargs["skip_transcription_review"] is True
    assert prep_kwargs["subtitle_offset_ms"] == -100
    assert prep_kwargs["existing_instrumental"] == "/path/to/inst.wav"
    assert prep_kwargs["lyrics_artist"] == "Override Artist"
    assert prep_kwargs["lyrics_title"] == "Override Title"
    assert prep_kwargs["lyrics_file"] == "/path/to/lyrics.txt"
    assert prep_kwargs["style_params_json"] == "/path/to/styles.json"
    # Verify --no-video sets render_video=False in KaraokePrep
    assert prep_kwargs["render_video"] is False
    mock_kprep_instance.process.assert_awaited_once()

    # Check KaraokeFinalise args
    mock_kfinalise.assert_called_once()
    finalise_kwargs = mock_kfinalise.call_args.kwargs
    assert finalise_kwargs["instrumental_format"] == "mp3"
    assert finalise_kwargs["non_interactive"] is True
    assert finalise_kwargs["no_video"] is True


@patch("karaoke_gen.utils.gen_cli.KaraokePrep") # Use default MagicMock for class
@patch("karaoke_gen.utils.gen_cli.KaraokeFinalise")
@patch("karaoke_gen.utils.gen_cli.os.chdir")
@patch("karaoke_gen.utils.gen_cli.os.path.exists", return_value=True)
@patch("karaoke_gen.utils.gen_cli.pyperclip.copy")
@patch("karaoke_gen.utils.gen_cli.run_instrumental_review", return_value="selected_instrumental.flac")
async def test_clipboard_copy_success(mock_run_review, mock_copy, mock_exists, mock_chdir, mock_kfinalise, mock_kprep_class, mock_base_args, mock_logger, capsys):
    """Test logging when clipboard copy succeeds."""
    mock_base_args.args = ["Artist", "Title"]
    
    # Set up the KaraokePrep mock properly
    mock_kprep_instance = MagicMock()
    mock_kprep_class.return_value = mock_kprep_instance
    mock_kprep_instance.process = AsyncMock(return_value=[MOCK_PREP_TRACK])
    
    # Ensure mock final track has URLs
    mock_final_track_with_urls = MOCK_FINAL_TRACK.copy()
    mock_final_track_with_urls["youtube_url"] = "http://youtu.be/fake"
    mock_final_track_with_urls["brand_code_dir_sharing_link"] = "http://share.link/fake"
    mock_kfinalise.return_value.process = MagicMock(return_value=mock_final_track_with_urls)

    with patch("karaoke_gen.utils.gen_cli.argparse.ArgumentParser") as mock_parser:
        mock_parser.return_value.parse_args.return_value = mock_base_args
        await gen_cli.async_main()

    mock_kprep_instance.process.assert_awaited_once()
    # Check log capture for success messages (in stderr since logger.propagate = False)
    captured = capsys.readouterr()
    assert "(Folder link copied to clipboard)" in captured.err
    assert "(YouTube URL copied to clipboard)" in captured.err
    
    mock_copy.assert_has_calls([
        call("http://share.link/fake"),
        call("http://youtu.be/fake")
    ], any_order=True)


@patch("karaoke_gen.utils.gen_cli.KaraokePrep") # Use default MagicMock for class
@patch("karaoke_gen.utils.gen_cli.KaraokeFinalise")
@patch("karaoke_gen.utils.gen_cli.os.chdir")
@patch("karaoke_gen.utils.gen_cli.os.path.exists", return_value=True)
@patch("karaoke_gen.utils.gen_cli.pyperclip.copy", side_effect=Exception("Clipboard Error"))
@patch("karaoke_gen.utils.gen_cli.run_instrumental_review", return_value="selected_instrumental.flac")
async def test_clipboard_copy_failure(mock_run_review, mock_copy, mock_exists, mock_chdir, mock_kfinalise, mock_kprep_class, mock_base_args, mock_logger, capsys):
    """Test logging when clipboard copy fails."""
    mock_base_args.args = ["Artist", "Title"]
    
    # Set up the KaraokePrep mock properly
    mock_kprep_instance = MagicMock()
    mock_kprep_class.return_value = mock_kprep_instance
    mock_kprep_instance.process = AsyncMock(return_value=[MOCK_PREP_TRACK])
    
    # Ensure mock final track has URLs
    mock_final_track_with_urls = MOCK_FINAL_TRACK.copy()
    mock_final_track_with_urls["youtube_url"] = "http://youtu.be/fake"
    mock_final_track_with_urls["brand_code_dir_sharing_link"] = "http://share.link/fake"
    mock_kfinalise.return_value.process = MagicMock(return_value=mock_final_track_with_urls)

    with patch("karaoke_gen.utils.gen_cli.argparse.ArgumentParser") as mock_parser:
        mock_parser.return_value.parse_args.return_value = mock_base_args
        await gen_cli.async_main()

    mock_kprep_instance.process.assert_awaited_once()
    # Check log capture for warning messages (in stderr since logger.propagate = False)
    captured = capsys.readouterr()
    assert "Failed to copy folder link to clipboard: Clipboard Error" in captured.err
    assert "Failed to copy YouTube URL to clipboard: Clipboard Error" in captured.err
    
    # Verify clipboard calls were attempted
    mock_copy.assert_has_calls([
        call("http://share.link/fake"),
        call("http://youtu.be/fake")
    ], any_order=True)


@patch("karaoke_gen.utils.gen_cli.KaraokePrep")
@patch("karaoke_gen.utils.gen_cli.run_instrumental_review", return_value="selected_instrumental.flac")
async def test_style_override_parsing(mock_run_review, mock_kprep_class, mock_base_args):
    """Test that --style_override arguments are parsed correctly."""
    mock_base_args.args = ["Artist", "Title"]
    mock_base_args.style_override = ["intro.background_image=new_image.png", "end.video_duration=10"]

    mock_kprep_instance = MagicMock()
    mock_kprep_class.return_value = mock_kprep_instance
    mock_kprep_instance.process = AsyncMock(return_value=[MOCK_PREP_TRACK])

    with patch("karaoke_gen.utils.gen_cli.argparse.ArgumentParser") as mock_parser, \
         patch("karaoke_gen.utils.gen_cli.os.chdir"), \
         patch("karaoke_gen.utils.gen_cli.os.path.exists", return_value=True), \
         patch("karaoke_gen.utils.gen_cli.KaraokeFinalise"):
        mock_parser.return_value.parse_args.return_value = mock_base_args
        await gen_cli.async_main()

    mock_kprep_class.assert_called_once()
    prep_kwargs = mock_kprep_class.call_args.kwargs
    expected_overrides = {
        "intro.background_image": "new_image.png",
        "end.video_duration": "10"
    }
    assert prep_kwargs["style_overrides"] == expected_overrides


# --- Test review_ui_url environment variable handling ---

@patch("karaoke_gen.utils.gen_cli.run_instrumental_review", return_value="clean")
@patch("karaoke_gen.utils.gen_cli.is_url", return_value=False)
@patch("karaoke_gen.utils.gen_cli.is_file", return_value=False)
@patch("karaoke_gen.utils.gen_cli.KaraokePrep")
async def test_review_ui_url_default_hosted_not_set_env_var(mock_kprep_class, mock_isfile, mock_isurl, mock_run_review, mock_base_args):
    """Test that default hosted review UI URL does NOT set LYRICS_REVIEW_UI_URL env var.
    
    This allows the ReviewServer to use its bundled local frontend by default.
    """
    mock_base_args.args = ["Artist", "Title"]
    # Simulate the default hosted URL from cli_args
    mock_base_args.review_ui_url = "https://gen.nomadkaraoke.com/lyrics"

    mock_kprep_instance = MagicMock()
    mock_kprep_class.return_value = mock_kprep_instance
    mock_kprep_instance.process = AsyncMock(return_value=[MOCK_PREP_TRACK])

    # Clear the env var first
    env_backup = os.environ.get('LYRICS_REVIEW_UI_URL')
    if 'LYRICS_REVIEW_UI_URL' in os.environ:
        del os.environ['LYRICS_REVIEW_UI_URL']

    try:
        with patch("karaoke_gen.utils.gen_cli.argparse.ArgumentParser") as mock_parser, \
             patch("karaoke_gen.utils.gen_cli.os.chdir"), \
             patch("karaoke_gen.utils.gen_cli.os.path.exists", return_value=True), \
             patch("karaoke_gen.utils.gen_cli.KaraokeFinalise"):
            mock_parser.return_value.parse_args.return_value = mock_base_args
            await gen_cli.async_main()

        # Verify the env var was NOT set (so ReviewServer uses local frontend)
        assert 'LYRICS_REVIEW_UI_URL' not in os.environ or os.environ.get('LYRICS_REVIEW_UI_URL') != mock_base_args.review_ui_url
    finally:
        # Restore env var
        if env_backup:
            os.environ['LYRICS_REVIEW_UI_URL'] = env_backup
        elif 'LYRICS_REVIEW_UI_URL' in os.environ:
            del os.environ['LYRICS_REVIEW_UI_URL']


@patch("karaoke_gen.utils.gen_cli.run_instrumental_review", return_value="clean")
@patch("karaoke_gen.utils.gen_cli.is_url", return_value=False)
@patch("karaoke_gen.utils.gen_cli.is_file", return_value=False)
@patch("karaoke_gen.utils.gen_cli.KaraokePrep")
async def test_review_ui_url_custom_dev_server_sets_env_var(mock_kprep_class, mock_isfile, mock_isurl, mock_run_review, mock_base_args):
    """Test that custom dev server URL (e.g., localhost:5173) DOES set LYRICS_REVIEW_UI_URL env var.
    
    This allows using a Vite dev server for local frontend development.
    """
    mock_base_args.args = ["Artist", "Title"]
    # Simulate a custom dev server URL
    mock_base_args.review_ui_url = "http://localhost:5173"

    mock_kprep_instance = MagicMock()
    mock_kprep_class.return_value = mock_kprep_instance
    mock_kprep_instance.process = AsyncMock(return_value=[MOCK_PREP_TRACK])

    # Clear the env var first
    env_backup = os.environ.get('LYRICS_REVIEW_UI_URL')
    if 'LYRICS_REVIEW_UI_URL' in os.environ:
        del os.environ['LYRICS_REVIEW_UI_URL']

    try:
        with patch("karaoke_gen.utils.gen_cli.argparse.ArgumentParser") as mock_parser, \
             patch("karaoke_gen.utils.gen_cli.os.chdir"), \
             patch("karaoke_gen.utils.gen_cli.os.path.exists", return_value=True), \
             patch("karaoke_gen.utils.gen_cli.KaraokeFinalise"):
            mock_parser.return_value.parse_args.return_value = mock_base_args
            await gen_cli.async_main()

        # Verify the env var WAS set to the custom URL
        assert os.environ.get('LYRICS_REVIEW_UI_URL') == "http://localhost:5173"
    finally:
        # Restore env var
        if env_backup:
            os.environ['LYRICS_REVIEW_UI_URL'] = env_backup
        elif 'LYRICS_REVIEW_UI_URL' in os.environ:
            del os.environ['LYRICS_REVIEW_UI_URL']


# --- Test _resolve_path_for_cwd helper function ---

class TestResolvePathForCwd:
    """Tests for the _resolve_path_for_cwd helper function.
    
    This function resolves paths that were created relative to the original working
    directory after os.chdir(track_dir) has been called.
    """
    
    def test_absolute_path_unchanged(self):
        """Absolute paths should be returned unchanged."""
        from karaoke_gen.utils.gen_cli import _resolve_path_for_cwd
        
        path = "/absolute/path/to/file.flac"
        track_dir = "./Artist - Title"
        result = _resolve_path_for_cwd(path, track_dir)
        assert result == path
    
    def test_relative_path_starting_with_track_dir(self):
        """Paths starting with track_dir should have track_dir stripped."""
        from karaoke_gen.utils.gen_cli import _resolve_path_for_cwd
        
        path = "./Artist - Title/stems/file.flac"
        track_dir = "./Artist - Title"
        result = _resolve_path_for_cwd(path, track_dir)
        assert result == "stems/file.flac"
    
    def test_relative_path_with_different_format(self):
        """Paths without ./ prefix should also be handled."""
        from karaoke_gen.utils.gen_cli import _resolve_path_for_cwd
        
        path = "Artist - Title/stems/file.flac"
        track_dir = "Artist - Title"
        result = _resolve_path_for_cwd(path, track_dir)
        assert result == "stems/file.flac"
    
    def test_path_not_starting_with_track_dir(self):
        """Paths not starting with track_dir should be returned unchanged."""
        from karaoke_gen.utils.gen_cli import _resolve_path_for_cwd
        
        path = "different/path/to/file.flac"
        track_dir = "./Artist - Title"
        result = _resolve_path_for_cwd(path, track_dir)
        assert result == path
    
    def test_path_with_complex_track_name(self):
        """Test with a track name containing special characters."""
        from karaoke_gen.utils.gen_cli import _resolve_path_for_cwd
        
        path = "./Four Lanes Male Choir - The White Rose/stems/backing_vocals.flac"
        track_dir = "./Four Lanes Male Choir - The White Rose"
        result = _resolve_path_for_cwd(path, track_dir)
        assert result == "stems/backing_vocals.flac"
    
    def test_path_exactly_matching_track_dir(self):
        """Test when path exactly matches track_dir (edge case)."""
        from karaoke_gen.utils.gen_cli import _resolve_path_for_cwd
        
        path = "./Artist - Title"
        track_dir = "./Artist - Title"
        result = _resolve_path_for_cwd(path, track_dir)
        assert result == "."
    
    def test_normalized_path_comparison(self):
        """Test that path normalization works correctly."""
        from karaoke_gen.utils.gen_cli import _resolve_path_for_cwd
        
        # Paths with extra slashes or dots should be normalized
        path = "./Artist - Title//stems/./file.flac"
        track_dir = "./Artist - Title/"
        result = _resolve_path_for_cwd(path, track_dir)
        # After normalization: "Artist - Title/stems/file.flac" vs "Artist - Title"
        assert result == "stems/file.flac"


# --- Test auto_select_instrumental helper function ---

class TestAutoSelectInstrumental:
    """Tests for the auto_select_instrumental function.
    
    This function automatically selects the best instrumental file when
    --skip_instrumental_review is used.
    """
    
    def test_prefers_padded_combined_over_non_padded(self, tmp_path, mock_logger):
        """Padded combined instrumental should be preferred."""
        from karaoke_gen.utils.gen_cli import auto_select_instrumental
        
        # Create test files
        combined = tmp_path / "Artist - Title (Instrumental +BV model.ckpt).flac"
        combined_padded = tmp_path / "Artist - Title (Instrumental +BV model.ckpt) (Padded).flac"
        combined.touch()
        combined_padded.touch()
        
        track = {
            "track_output_dir": str(tmp_path),
            "separated_audio": {
                "combined_instrumentals": {
                    "model.ckpt": str(combined)
                }
            }
        }
        
        # Change to temp directory (simulating the chdir in gen_cli)
        original_dir = os.getcwd()
        os.chdir(tmp_path)
        try:
            result = auto_select_instrumental(track, str(tmp_path), mock_logger)
            assert "(Padded)" in result
        finally:
            os.chdir(original_dir)
    
    def test_prefers_combined_over_clean(self, tmp_path, mock_logger):
        """Combined instrumental (+BV) should be preferred over clean."""
        from karaoke_gen.utils.gen_cli import auto_select_instrumental
        
        # Create test files
        clean = tmp_path / "Artist - Title (Instrumental model.ckpt).flac"
        combined = tmp_path / "Artist - Title (Instrumental +BV model.ckpt).flac"
        clean.touch()
        combined.touch()
        
        track = {
            "track_output_dir": str(tmp_path),
            "separated_audio": {
                "clean_instrumental": {
                    "instrumental": str(clean)
                },
                "combined_instrumentals": {
                    "model.ckpt": str(combined)
                }
            }
        }
        
        original_dir = os.getcwd()
        os.chdir(tmp_path)
        try:
            result = auto_select_instrumental(track, str(tmp_path), mock_logger)
            assert "+BV" in result
        finally:
            os.chdir(original_dir)
    
    def test_falls_back_to_clean_when_no_combined(self, tmp_path, mock_logger):
        """Should use clean instrumental if no combined available."""
        from karaoke_gen.utils.gen_cli import auto_select_instrumental
        
        # Create test file
        clean = tmp_path / "Artist - Title (Instrumental model.ckpt).flac"
        clean.touch()
        
        track = {
            "track_output_dir": str(tmp_path),
            "separated_audio": {
                "clean_instrumental": {
                    "instrumental": str(clean)
                },
                "combined_instrumentals": {}
            }
        }
        
        original_dir = os.getcwd()
        os.chdir(tmp_path)
        try:
            result = auto_select_instrumental(track, str(tmp_path), mock_logger)
            assert "Instrumental" in result
        finally:
            os.chdir(original_dir)
    
    def test_raises_error_when_no_instrumental_found(self, tmp_path, mock_logger):
        """Should raise FileNotFoundError when no instrumental available."""
        from karaoke_gen.utils.gen_cli import auto_select_instrumental
        
        track = {
            "track_output_dir": str(tmp_path),
            "separated_audio": {
                "clean_instrumental": {},
                "combined_instrumentals": {}
            }
        }
        
        original_dir = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(FileNotFoundError, match="No instrumental file found"):
                auto_select_instrumental(track, str(tmp_path), mock_logger)
        finally:
            os.chdir(original_dir)
    
    def test_searches_directory_as_fallback(self, tmp_path, mock_logger):
        """Should search directory if separated_audio is missing data."""
        from karaoke_gen.utils.gen_cli import auto_select_instrumental
        
        # Create test file in directory but not in separated_audio
        instrumental = tmp_path / "Artist - Title (Instrumental model.ckpt).flac"
        instrumental.touch()
        
        track = {
            "track_output_dir": str(tmp_path),
            "separated_audio": {}  # Empty - nothing in separated_audio
        }
        
        original_dir = os.getcwd()
        os.chdir(tmp_path)
        try:
            result = auto_select_instrumental(track, str(tmp_path), mock_logger)
            assert "Instrumental" in result
        finally:
            os.chdir(original_dir)


# --- Test custom instrumental (--existing_instrumental) handling ---

@patch("karaoke_gen.utils.gen_cli.is_url", return_value=False)
@patch("karaoke_gen.utils.gen_cli.is_file", return_value=False)
@patch("karaoke_gen.utils.gen_cli.KaraokePrep")
async def test_custom_instrumental_skips_review(mock_kprep_class, mock_isfile, mock_isurl, mock_base_args, tmp_path):
    """Test that when --existing_instrumental is provided, the instrumental review is skipped.
    
    When a custom instrumental is provided via --existing_instrumental, the separation
    is skipped and the custom file should be used directly without requiring the
    instrumental review UI (which would fail due to missing backing vocals).
    """
    mock_base_args.args = ["Artist", "Title"]
    mock_base_args.skip_instrumental_review = False  # Review is NOT skipped, but custom instrumental should bypass it
    
    # Create a mock custom instrumental file
    custom_instrumental_path = tmp_path / "Artist - Title (Instrumental Custom).wav"
    custom_instrumental_path.touch()
    
    # Track with custom instrumental (as generated by KaraokePrep when --existing_instrumental is used)
    mock_track_with_custom = {
        "artist": "Test Artist",
        "title": "Test Title",
        "input_media": "input.mp3",
        "track_output_dir": str(tmp_path),
        "separated_audio": {
            "Custom": {
                "instrumental": str(custom_instrumental_path),
                "vocals": None,
            }
        },
    }
    
    mock_kprep_instance = MagicMock()
    mock_kprep_class.return_value = mock_kprep_instance
    mock_kprep_instance.process = AsyncMock(return_value=[mock_track_with_custom])

    with patch("karaoke_gen.utils.gen_cli.argparse.ArgumentParser") as mock_parser, \
         patch("karaoke_gen.utils.gen_cli.os.chdir"), \
         patch("karaoke_gen.utils.gen_cli.os.path.exists", return_value=True), \
         patch("karaoke_gen.utils.gen_cli.run_instrumental_review") as mock_review, \
         patch("karaoke_gen.utils.gen_cli.KaraokeFinalise") as mock_finalise:
        
        mock_parser.return_value.parse_args.return_value = mock_base_args
        mock_finalise_instance = MagicMock()
        mock_finalise.return_value = mock_finalise_instance
        mock_finalise_instance.process.return_value = MOCK_FINAL_TRACK
        
        await gen_cli.async_main()

        # Verify instrumental review was NOT called (because custom instrumental was used)
        mock_review.assert_not_called()
        
        # Verify KaraokeFinalise was called with the custom instrumental
        mock_finalise_instance.process.assert_called_once()


@patch("karaoke_gen.utils.gen_cli.is_url", return_value=False)
@patch("karaoke_gen.utils.gen_cli.is_file", return_value=False)
@patch("karaoke_gen.utils.gen_cli.KaraokePrep")
async def test_custom_instrumental_missing_file_exits(mock_kprep_class, mock_isfile, mock_isurl, mock_base_args, tmp_path):
    """Test that if custom instrumental file is missing, the CLI exits with error.
    
    If a custom instrumental path is recorded in the track but the file no longer
    exists, the CLI should exit with an error rather than silently falling back.
    """
    mock_base_args.args = ["Artist", "Title"]
    
    missing_file_path = str(tmp_path / "missing_instrumental.wav")
    
    # Track with custom instrumental pointing to non-existent file
    mock_track_with_custom = {
        "artist": "Test Artist",
        "title": "Test Title",
        "track_output_dir": str(tmp_path),
        "separated_audio": {
            "Custom": {
                "instrumental": missing_file_path,  # Does not exist
                "vocals": None,
            }
        },
    }
    
    mock_kprep_instance = MagicMock()
    mock_kprep_class.return_value = mock_kprep_instance
    mock_kprep_instance.process = AsyncMock(return_value=[mock_track_with_custom])

    def mock_exists(path):
        # Return True for track_dir, False for the missing instrumental file
        if str(path) == missing_file_path:
            return False
        return True

    with patch("karaoke_gen.utils.gen_cli.argparse.ArgumentParser") as mock_parser, \
         patch("karaoke_gen.utils.gen_cli.os.chdir"), \
         patch("karaoke_gen.utils.gen_cli.os.path.exists", side_effect=mock_exists), \
         patch("karaoke_gen.utils.gen_cli.KaraokeFinalise") as mock_finalise, \
         pytest.raises(SystemExit) as exc_info:
        
        mock_parser.return_value.parse_args.return_value = mock_base_args
        await gen_cli.async_main()

    # Should exit with code 1 (error)
    assert exc_info.value.code == 1
