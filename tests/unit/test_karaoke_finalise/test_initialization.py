import pytest
import logging
import os
import sys
from unittest.mock import patch, MagicMock, mock_open

# Adjust the import path based on your project structure
# Assuming tests are run from the project root
from karaoke_gen.karaoke_finalise.karaoke_finalise import KaraokeFinalise

# Basic configuration for tests
MINIMAL_CONFIG = {
    "dry_run": False,
    "instrumental_format": "flac",
    "enable_cdg": False,
    "enable_txt": False,
    "non_interactive": True, # Avoid prompts in most tests
}

@pytest.fixture
def mock_logger():
    """Fixture for a mocked logger."""
    return MagicMock(spec=logging.Logger)

@pytest.fixture
def basic_finaliser(mock_logger):
    """Fixture for a basic KaraokeFinalise instance with mocked logger."""
    with patch.object(KaraokeFinalise, 'detect_best_aac_codec', return_value='aac'):
         # Mock detect_best_aac_codec during init
        finaliser = KaraokeFinalise(logger=mock_logger, **MINIMAL_CONFIG)
    return finaliser

# --- Initialization Tests ---

def test_init_defaults(mock_logger):
    """Test default values are set correctly during initialization."""
    with patch.object(KaraokeFinalise, 'detect_best_aac_codec', return_value='aac'):
        finaliser = KaraokeFinalise(logger=mock_logger, **MINIMAL_CONFIG)

    assert finaliser.logger == mock_logger
    assert finaliser.dry_run == MINIMAL_CONFIG["dry_run"]
    assert finaliser.instrumental_format == MINIMAL_CONFIG["instrumental_format"]
    assert finaliser.enable_cdg == MINIMAL_CONFIG["enable_cdg"]
    assert finaliser.enable_txt == MINIMAL_CONFIG["enable_txt"]
    assert finaliser.non_interactive == MINIMAL_CONFIG["non_interactive"]
    assert finaliser.brand_prefix is None
    assert finaliser.organised_dir is None
    assert finaliser.public_share_dir is None
    assert finaliser.youtube_client_secrets_file is None
    assert finaliser.youtube_description_file is None
    assert finaliser.rclone_destination is None
    assert finaliser.discord_webhook_url is None
    assert finaliser.email_template_file is None
    assert finaliser.cdg_styles is None
    assert finaliser.keep_brand_code is False
    assert finaliser.aac_codec == 'aac' # Mocked value
    assert finaliser.mp4_flags == "-pix_fmt yuv420p -movflags +faststart+frag_keyframe+empty_moov"

    # Check feature flags default to False
    assert not finaliser.youtube_upload_enabled
    assert not finaliser.discord_notication_enabled
    assert not finaliser.folder_organisation_enabled
    assert not finaliser.public_share_copy_enabled
    assert not finaliser.public_share_rclone_enabled

def test_init_custom_logger_setup():
    """Test logger setup when no logger is provided."""
    with patch('logging.getLogger') as mock_get_logger, \
         patch('logging.StreamHandler') as mock_stream_handler, \
         patch('logging.Formatter') as mock_formatter, \
         patch.object(KaraokeFinalise, 'detect_best_aac_codec', return_value='aac'):

        mock_logger_instance = MagicMock()
        mock_get_logger.return_value = mock_logger_instance
        mock_handler_instance = MagicMock()
        mock_stream_handler.return_value = mock_handler_instance
        mock_formatter_instance = MagicMock()
        mock_formatter.return_value = mock_formatter_instance

        finaliser = KaraokeFinalise(log_level=logging.INFO, **MINIMAL_CONFIG)

        mock_get_logger.assert_called_once_with('karaoke_gen.karaoke_finalise.karaoke_finalise')
        mock_logger_instance.setLevel.assert_called_once_with(logging.INFO)
        mock_stream_handler.assert_called_once()
        mock_formatter.assert_called_once_with("%(asctime)s - %(levelname)s - %(module)s - %(message)s")
        mock_handler_instance.setFormatter.assert_called_once_with(mock_formatter_instance)
        mock_logger_instance.addHandler.assert_called_once_with(mock_handler_instance)
        assert finaliser.logger == mock_logger_instance

def test_init_ffmpeg_command_debug(mock_logger):
    """Test ffmpeg command includes loglevel verbose in debug mode."""
    with patch.object(KaraokeFinalise, 'detect_best_aac_codec', return_value='aac'):
        finaliser = KaraokeFinalise(logger=mock_logger, log_level=logging.DEBUG, **MINIMAL_CONFIG)
    assert " -loglevel verbose" in finaliser.ffmpeg_base_command
    assert " -loglevel fatal" not in finaliser.ffmpeg_base_command
    if MINIMAL_CONFIG["non_interactive"]:
        assert finaliser.ffmpeg_base_command.endswith(" -y")

def test_init_ffmpeg_command_info(mock_logger):
    """Test ffmpeg command includes loglevel fatal in non-debug mode."""
    with patch.object(KaraokeFinalise, 'detect_best_aac_codec', return_value='aac'):
        finaliser = KaraokeFinalise(logger=mock_logger, log_level=logging.INFO, **MINIMAL_CONFIG)
    assert " -loglevel fatal" in finaliser.ffmpeg_base_command
    assert " -loglevel verbose" not in finaliser.ffmpeg_base_command
    if MINIMAL_CONFIG["non_interactive"]:
        assert finaliser.ffmpeg_base_command.endswith(" -y")

def test_init_ffmpeg_command_frozen(mock_logger):
    """Test ffmpeg command uses bundled path when frozen."""
    # Add create=True because sys.frozen doesn't normally exist
    with patch('sys.frozen', True, create=True), \
         patch('sys._MEIPASS', '/path/to/frozen/app', create=True), \
         patch.object(KaraokeFinalise, 'detect_best_aac_codec', return_value='aac'):
        finaliser = KaraokeFinalise(logger=mock_logger, **MINIMAL_CONFIG)
    expected_path = os.path.join('/path/to/frozen/app', 'ffmpeg.exe')
    assert finaliser.ffmpeg_base_command.startswith(f"{expected_path} ")

def test_init_ffmpeg_command_not_frozen(mock_logger):
    """Test ffmpeg command uses system path when not frozen."""
    # Add create=True because sys.frozen doesn't normally exist
    with patch('sys.frozen', False, create=True), \
         patch.object(KaraokeFinalise, 'detect_best_aac_codec', return_value='aac'):
        finaliser = KaraokeFinalise(logger=mock_logger, **MINIMAL_CONFIG)
    assert finaliser.ffmpeg_base_command.startswith("ffmpeg ")

def test_init_non_interactive_ffmpeg(mock_logger):
    """Test ffmpeg command includes -y when non_interactive is True."""
    config = MINIMAL_CONFIG.copy()
    config["non_interactive"] = True
    with patch.object(KaraokeFinalise, 'detect_best_aac_codec', return_value='aac'):
        finaliser = KaraokeFinalise(logger=mock_logger, **config)
    assert finaliser.ffmpeg_base_command.strip().endswith(" -y")

def test_init_interactive_ffmpeg(mock_logger):
    """Test ffmpeg command includes -y even when non_interactive is False.

    -y is now unconditional: without it, a pre-existing output file makes ffmpeg
    block on an invisible "Overwrite? [y/N]" prompt until the subprocess timeout.
    """
    config = MINIMAL_CONFIG.copy()
    config["non_interactive"] = False
    with patch.object(KaraokeFinalise, 'detect_best_aac_codec', return_value='aac'):
        finaliser = KaraokeFinalise(logger=mock_logger, **config)
    assert finaliser.ffmpeg_base_command.strip().endswith(" -y")


# --- AAC Codec Detection Tests ---

@patch('os.popen')
def test_detect_best_aac_codec_aac_at(mock_popen, mock_logger):
    """Test detection of aac_at codec."""
    mock_popen.return_value.read.return_value = "Codecs:\n D..... aac\n DEA.L. aac_at\n D..... libfdk_aac"
    finaliser = KaraokeFinalise(logger=mock_logger, **MINIMAL_CONFIG)
    assert finaliser.aac_codec == "aac_at"
    mock_logger.info.assert_any_call("Using aac_at codec (best quality)")

@patch('os.popen')
def test_detect_best_aac_codec_libfdk_aac(mock_popen, mock_logger):
    """Test detection of libfdk_aac codec when aac_at is not present."""
    mock_popen.return_value.read.return_value = "Codecs:\n D..... aac\n D..... libfdk_aac"
    finaliser = KaraokeFinalise(logger=mock_logger, **MINIMAL_CONFIG)
    assert finaliser.aac_codec == "libfdk_aac"
    mock_logger.info.assert_any_call("Using libfdk_aac codec (good quality)")

@patch('os.popen')
def test_detect_best_aac_codec_aac_default(mock_popen, mock_logger):
    """Test detection falls back to basic aac codec."""
    mock_popen.return_value.read.return_value = "Codecs:\n D..... aac"
    finaliser = KaraokeFinalise(logger=mock_logger, **MINIMAL_CONFIG)
    assert finaliser.aac_codec == "aac"
    mock_logger.info.assert_any_call("Using built-in aac codec (basic quality)")

@patch('os.popen')
def test_detect_best_aac_codec_none_found(mock_popen, mock_logger):
    """Test detection falls back to basic aac when no AAC codecs are listed."""
    mock_popen.return_value.read.return_value = "Codecs:\n D..... mp3\n D..... flac"
    finaliser = KaraokeFinalise(logger=mock_logger, **MINIMAL_CONFIG)
    assert finaliser.aac_codec == "aac"
    mock_logger.info.assert_any_call("Using built-in aac codec (basic quality)")
