"""
Regression tests for OutputConfig.cache_dir / output_dir resolution timing.

Background: the cloud lyrics worker sets LYRICS_TRANSCRIBER_CACHE_DIR to a
per-job temp dir at *runtime* (after the config module is already imported) so
that the transcriber writes its AudioShake/lyrics cache into the same directory
the GCS cache-sync agent reads/writes. Previously OutputConfig.cache_dir used a
bare `os.getenv(...)` dataclass default, which is evaluated once at import time
and therefore ignored the worker's runtime value. That silently broke the
cross-container cache and made every job spend a fresh AudioShake credit.

These tests lock in that cache_dir (and output_dir) are resolved when an
OutputConfig is *instantiated*, honoring the current environment.
"""
import os
from unittest.mock import patch

from karaoke_gen.lyrics_transcriber.core.config import OutputConfig


STYLES = "/tmp/does-not-need-to-exist-styles.json"


def test_cache_dir_reads_env_set_after_import():
    """cache_dir must reflect LYRICS_TRANSCRIBER_CACHE_DIR set at runtime."""
    runtime_dir = "/tmp/job-abc123/lyrics-cache"
    with patch.dict(os.environ, {"LYRICS_TRANSCRIBER_CACHE_DIR": runtime_dir}):
        config = OutputConfig(output_styles_json=STYLES)
    assert config.cache_dir == runtime_dir


def test_cache_dir_falls_back_to_home_when_env_unset():
    """Without the env var, cache_dir falls back to ~/lyrics-transcriber-cache."""
    env = {k: v for k, v in os.environ.items() if k != "LYRICS_TRANSCRIBER_CACHE_DIR"}
    with patch.dict(os.environ, env, clear=True):
        config = OutputConfig(output_styles_json=STYLES)
    expected = os.path.join(os.path.expanduser("~"), "lyrics-transcriber-cache")
    assert config.cache_dir == expected


def test_cache_dir_is_reevaluated_per_instance():
    """Two instances constructed under different env values must differ."""
    with patch.dict(os.environ, {"LYRICS_TRANSCRIBER_CACHE_DIR": "/tmp/first"}):
        first = OutputConfig(output_styles_json=STYLES)
    with patch.dict(os.environ, {"LYRICS_TRANSCRIBER_CACHE_DIR": "/tmp/second"}):
        second = OutputConfig(output_styles_json=STYLES)
    assert first.cache_dir == "/tmp/first"
    assert second.cache_dir == "/tmp/second"


def test_explicit_cache_dir_argument_wins():
    """An explicitly passed cache_dir overrides the env-derived default."""
    with patch.dict(os.environ, {"LYRICS_TRANSCRIBER_CACHE_DIR": "/tmp/env-dir"}):
        config = OutputConfig(output_styles_json=STYLES, cache_dir="/tmp/explicit")
    assert config.cache_dir == "/tmp/explicit"


def test_output_dir_defaults_to_cwd_at_instantiation(tmp_path, monkeypatch):
    """output_dir default should track the cwd at construction, not import."""
    monkeypatch.chdir(tmp_path)
    config = OutputConfig(output_styles_json=STYLES)
    assert os.path.realpath(config.output_dir) == os.path.realpath(str(tmp_path))
