import warnings

# Trigger CI on ephemeral runners after the dispatcher e2 fix.
# Suppress specific SyntaxWarnings from third-party packages
warnings.filterwarnings("ignore", category=SyntaxWarning, module="pydub.*")
warnings.filterwarnings("ignore", category=SyntaxWarning, module="syrics.*")

# Lazy imports to avoid loading heavy dependencies when not needed.
# KaraokePrep has many dependencies (lyrics_transcriber, etc.) which may not
# be available in all contexts (e.g., backend which only needs audio_fetcher).
# 
# Use explicit imports instead:
#   from karaoke_gen.karaoke_gen import KaraokePrep
#   from karaoke_gen.audio_fetcher import FlacFetcher, AudioSearchResult, ...

__all__ = [
    "KaraokePrep",
    # Audio fetcher
    "FlacFetcher",
    "AudioSearchResult",
    "AudioFetchResult",
    "AudioFetcherError",
    "NoResultsError",
    "DownloadError",
    "UserCancelledError",
]


def __getattr__(name):
    """Lazy import for heavy modules."""
    if name == "KaraokePrep":
        from .karaoke_gen import KaraokePrep
        return KaraokePrep
    elif name in ("FlacFetcher", "AudioSearchResult", "AudioFetchResult", 
                  "AudioFetcherError", "NoResultsError", "DownloadError", 
                  "UserCancelledError"):
        from . import audio_fetcher
        return getattr(audio_fetcher, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
