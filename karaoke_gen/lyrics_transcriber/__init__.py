from karaoke_gen.lyrics_transcriber.core.config import TranscriberConfig, LyricsConfig, OutputConfig
from karaoke_gen.lyrics_transcriber.core.controller import LyricsTranscriber
from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("lyrics-transcriber")
except PackageNotFoundError:
    __version__ = "unknown"

__all__ = ["LyricsTranscriber", "TranscriberConfig", "LyricsConfig", "OutputConfig"]
