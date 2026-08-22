"""Regression tests for the ffmpeg interactive-prompt hang in finalisation.

Bug: ``karaoke-gen --finalise-only`` (interactive) ran ffmpeg without ``-y`` and
without a closed stdin. When an output file already existed (e.g. a pre-existing
``(With Vocals).mp4`` from a prior run), ffmpeg printed ``... already exists.
Overwrite? [y/N]`` to the captured stderr and blocked reading stdin until the
600s timeout, silently aborting finalisation at step 2/6.

Fixes:
  1. ``-y`` is always part of ffmpeg_base_command (not only in non-interactive mode),
     so re-runs overwrite intermediate outputs idempotently.
  2. ffmpeg subprocess calls pass ``stdin=subprocess.DEVNULL`` so ffmpeg can never
     block waiting on a prompt (defense-in-depth).
"""

import logging
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from karaoke_gen.karaoke_finalise.karaoke_finalise import KaraokeFinalise


@pytest.fixture
def kwargs():
    return {
        "logger": MagicMock(spec=logging.Logger),
        "enable_cdg": False,
        "enable_txt": False,
        "no_video": False,
        "dry_run": True,  # safe init; individual tests flip dry_run off as needed
    }


class TestFfmpegPromptHang:
    def test_base_command_has_y_in_interactive_mode(self, kwargs):
        kwargs["non_interactive"] = False
        kf = KaraokeFinalise(**kwargs)
        assert " -y" in kf.ffmpeg_base_command

    def test_base_command_has_y_in_non_interactive_mode(self, kwargs):
        kwargs["non_interactive"] = True
        kf = KaraokeFinalise(**kwargs)
        assert " -y" in kf.ffmpeg_base_command

    def test_execute_command_closes_stdin(self, kwargs):
        kwargs["non_interactive"] = True
        kf = KaraokeFinalise(**kwargs)
        kf.dry_run = False
        with patch(
            "karaoke_gen.karaoke_finalise.karaoke_finalise.subprocess.run"
        ) as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            kf.execute_command("ffmpeg -i in.mov out.mp4", "convert")
        assert mock_run.call_args.kwargs.get("stdin") is subprocess.DEVNULL

    def test_execute_command_with_fallback_cpu_closes_stdin(self, kwargs):
        kwargs["non_interactive"] = True
        kf = KaraokeFinalise(**kwargs)
        kf.dry_run = False
        kf.nvenc_available = False  # force the CPU path
        with patch(
            "karaoke_gen.karaoke_finalise.karaoke_finalise.subprocess.run"
        ) as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            kf.execute_command_with_fallback(
                "ffmpeg gpu", "ffmpeg cpu", "encode"
            )
        assert mock_run.call_args.kwargs.get("stdin") is subprocess.DEVNULL
