"""Build the padded original-vocals guide that feeds kjbox's "Original Vocals" slider.

At render time karaoke-gen already has the isolated ``mixed_vocals`` stem (a byproduct
of making the karaoke instrumental) and knows the exact intro offset (the master is
``[silent title-card intro] + song + [tail]`` and the intro length is the job's style
``intro_video_duration``). So the guide is simply ``silence[intro] + vocals``, capped to
the master's duration — no cross-correlation, no human review, no alignment measurement.
This is byte-compatible with what the kjbox retro-fit ``pad_vocals.sh`` produced.

Every function here is best-effort: it returns ``None`` / ``0`` on any failure and never
raises, so a guide problem can never break the distribution pipeline.
"""
import logging
import os
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


def probe_duration(path: str, ffprobe_path: str = "ffprobe") -> Optional[float]:
    """Return a media file's duration in seconds, or ``None`` if it can't be measured."""
    try:
        if not path or not os.path.isfile(path):
            return None
        result = subprocess.run(
            [
                ffprobe_path, "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", path,
            ],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return None
        return float(result.stdout.strip())
    except Exception as e:  # noqa: BLE001 - never fatal
        logger.warning("Could not probe duration for %s: %s", path, e)
        return None


def build_original_vocals_guide(
    mixed_vocals_path: str,
    intro_seconds: float,
    dest_path: str,
    master_duration: Optional[float] = None,
    ffmpeg_path: str = "ffmpeg",
) -> Optional[str]:
    """Emit ``silence[intro] + mixed_vocals`` as a FLAC guide aligned to the master.

    Args:
        mixed_vocals_path: The isolated vocals stem (all vocals: lead + backing).
        intro_seconds: The master's silent title-card length (job's ``intro_video_duration``).
        dest_path: Where to write the guide (``.flac``).
        master_duration: If given, the output is capped to this length (``-t``) so the
            guide can never bleed past the master. Omit to leave the vocals' own length.
        ffmpeg_path: ffmpeg binary.

    Returns:
        ``dest_path`` on success, else ``None`` (missing input / ffmpeg failure). Never raises.
    """
    try:
        if not mixed_vocals_path or not os.path.isfile(mixed_vocals_path):
            logger.warning("Vocals guide: missing mixed-vocals stem %s", mixed_vocals_path)
            return None
        if intro_seconds is None or intro_seconds < 0:
            logger.warning("Vocals guide: invalid intro_seconds %r", intro_seconds)
            return None

        delay_ms = int(round(float(intro_seconds) * 1000))
        os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
        tmp_path = dest_path + ".part"

        cmd = [
            ffmpeg_path, "-y", "-hide_banner", "-nostats", "-loglevel", "error",
            "-i", mixed_vocals_path,
            "-af", f"adelay={delay_ms}:all=1",
        ]
        if master_duration and master_duration > 0:
            cmd += ["-t", f"{float(master_duration):.3f}"]
        # ``-f flac`` is mandatory: the ``.part`` extension would otherwise make the
        # muxer guess the container from the extension and fail.
        cmd += ["-c:a", "flac", "-f", "flac", tmp_path]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0 or not os.path.isfile(tmp_path):
            logger.warning(
                "Vocals guide: ffmpeg failed (rc=%s) for %s: %s",
                result.returncode, mixed_vocals_path, (result.stderr or "")[-200:].strip(),
            )
            _quiet_remove(tmp_path)
            return None

        os.replace(tmp_path, dest_path)
        logger.info("Built original-vocals guide: %s", dest_path)
        return dest_path
    except Exception as e:  # noqa: BLE001 - never fatal to the pipeline
        logger.warning("Vocals guide build failed for %s: %s", mixed_vocals_path, e)
        _quiet_remove(dest_path + ".part")
        return None


def _quiet_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
