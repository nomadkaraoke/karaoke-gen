import os
import pytest

# Gemini Developer API key: never let a test fall through to Secret Manager.
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
import tempfile
import logging
from unittest.mock import MagicMock
from karaoke_gen.karaoke_gen import KaraokePrep
from karaoke_gen.style_loader import (
    DEFAULT_INTRO_STYLE,
    DEFAULT_END_STYLE,
    DEFAULT_KARAOKE_STYLE,
    DEFAULT_CDG_STYLE,
)
import inspect

@pytest.fixture
def mock_logger():
    """Return a mock logger for testing."""
    return MagicMock(spec=logging.Logger)

@pytest.fixture
def mock_ffmpeg():
    """Return a mock for os.system to avoid executing ffmpeg commands."""
    return MagicMock()

@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield temp_dir

@pytest.fixture
def complete_theme():
    """
    Return a complete theme with all required sections and fields.

    This is the standard fixture for testing code that requires themes.
    Use this instead of relying on DEFAULT_* constants with fallback logic.
    """
    return {
        "intro": DEFAULT_INTRO_STYLE.copy(),
        "end": DEFAULT_END_STYLE.copy(),
        "karaoke": DEFAULT_KARAOKE_STYLE.copy(),
        "cdg": DEFAULT_CDG_STYLE.copy(),
    }

@pytest.fixture
def basic_karaoke_gen(mock_logger, mock_ffmpeg, temp_dir):
    """Return a basic KaraokePrep instance for testing."""
    with MagicMock() as mock_os_system:
        # Create a KaraokePrep instance with required artist/title and a mock logger
        karaoke_gen = KaraokePrep(
            logger=mock_logger,
            artist="Test Artist",
            title="Test Title",
            output_dir=temp_dir,
        )
        
        # Replace the ffmpeg_base_command with a mock
        karaoke_gen.ffmpeg_base_command = "mock_ffmpeg"
        
        # Mock os.system to avoid executing commands
        karaoke_gen._os_system = mock_os_system
        
        yield karaoke_gen

def pytest_collection_modifyitems(items):
    """Mark async tests with asyncio marker."""
    for item in items:
        if inspect.iscoroutinefunction(item.obj):
            item.add_marker(pytest.mark.asyncio)
