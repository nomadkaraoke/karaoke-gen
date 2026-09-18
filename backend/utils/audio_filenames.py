"""Helpers for naming local temp copies of audio files downloaded from GCS.

ffmpeg falls back to extension-based format guessing when content probing is
inconclusive (e.g. an MP3 whose multi-megabyte ID3 tag fills the probe
buffer). Downloading a `.mp3` GCS object to a temp file named `foo.flac`
makes ffmpeg commit to the flac demuxer and fail with "Could not find codec
parameters ... 0 channels". Always name local copies with the source
object's real extension.
"""

from pathlib import PurePosixPath

# Extensions we recognise as audio containers. Anything else (or no
# extension) falls back to no suffix, which forces pure content probing.
_AUDIO_SUFFIXES = {
    ".flac", ".mp3", ".wav", ".ogg", ".opus", ".m4a", ".aac",
    ".mp4", ".wma", ".aiff", ".aif", ".webm", ".mka", ".mkv",
}


_AUDIO_CONTENT_TYPES = {
    ".flac": "audio/flac",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".mp4": "audio/mp4",
    ".wma": "audio/x-ms-wma",
    ".aiff": "audio/aiff",
    ".aif": "audio/aiff",
    ".webm": "audio/webm",
    ".mka": "audio/x-matroska",
    ".mkv": "audio/x-matroska",
}


def audio_content_type(gcs_path: str) -> str:
    """Return the MIME type for an audio object based on its extension.

    Unknown extensions get application/octet-stream rather than a wrong
    audio/* type.
    """
    suffix = PurePosixPath(gcs_path).suffix.lower()
    return _AUDIO_CONTENT_TYPES.get(suffix, "application/octet-stream")


def local_audio_filename(gcs_path: str, stem: str = "audio") -> str:
    """Return a local filename for a downloaded audio object.

    Keeps the source object's extension (lowercased) so ffmpeg's
    extension-based fallback never mismatches the content. Unknown or
    missing extensions yield a bare name, which makes ffmpeg rely solely
    on content probing.

    >>> local_audio_filename("jobs/x/input/Song (192 Kbps).mp3", "audio")
    'audio.mp3'
    >>> local_audio_filename("jobs/x/stems/backing_vocals.flac", "backing_vocals")
    'backing_vocals.flac'
    """
    suffix = PurePosixPath(gcs_path).suffix.lower()
    if suffix in _AUDIO_SUFFIXES:
        return f"{stem}{suffix}"
    return stem
