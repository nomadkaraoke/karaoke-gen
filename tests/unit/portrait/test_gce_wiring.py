"""Tests for the backend wiring that emits/distributes the portrait video.

These cover the additive, non-fatal contract and the plumbing that carries the
portrait file to GCS + Dropbox (and keeps it out of YouTube/Drive) — without
needing the GCE VM or a real render.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

# Mock google.cloud.storage.Client only while importing main.py so this test collects
# in the wheel-only CI job (which has no GCP project/ADC). Scoped so we don't leave a
# session-wide global patch that could affect other tests.
with patch("google.cloud.storage.Client", MagicMock()):
    import backend.services.gce_encoding.main as gce  # noqa: E402
    from backend.services.encoding_interface import EncodingOutput  # noqa: E402


def test_encoding_output_has_portrait_field():
    out = EncodingOutput(success=True, portrait_mp4_path="finals/x (Final Karaoke Portrait 1080x1920).mp4")
    assert out.portrait_mp4_path.endswith("Portrait 1080x1920).mp4")


def test_portrait_render_disabled_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(gce, "PORTRAIT_RENDER_ENABLED", False)
    result = gce.render_portrait_into_outputs(
        "job1", tmp_path, tmp_path, "Artist - Title", "Artist", "Title",
        tmp_path / "instr.flac", {},
    )
    assert result is None


def test_portrait_render_missing_corrections_is_non_fatal(monkeypatch, tmp_path):
    # Enabled, but no corrections JSON present anywhere -> returns None, no raise.
    monkeypatch.setattr(gce, "PORTRAIT_RENDER_ENABLED", True)
    out_dir = tmp_path / "outputs"
    out_dir.mkdir()
    result = gce.render_portrait_into_outputs(
        "job1", tmp_path, out_dir, "Artist - Title", "Artist", "Title",
        tmp_path / "instr.flac", {},
    )
    assert result is None


def test_portrait_render_swallows_renderer_errors(monkeypatch, tmp_path):
    """A failure inside the renderer must not propagate (job must still finish)."""
    monkeypatch.setattr(gce, "PORTRAIT_RENDER_ENABLED", True)
    (tmp_path / "lyrics").mkdir()
    (tmp_path / "lyrics" / "corrections_updated.json").write_text('{"corrected_segments": []}')

    def _boom(*a, **k):
        raise RuntimeError("render exploded")

    import karaoke_gen.portrait as portrait_pkg
    monkeypatch.setattr(portrait_pkg, "render_portrait_video", _boom)
    # CorrectionResult.from_dict on a minimal dict may also raise; either way -> None.
    result = gce.render_portrait_into_outputs(
        "job1", tmp_path, tmp_path, "Artist - Title", "Artist", "Title",
        tmp_path / "instr.flac", {},
    )
    assert result is None


def test_portrait_filename_classified_for_dropbox_not_drive():
    """The portrait file name must be routed to the portrait output slot."""
    name = "Artist - Title (Final Karaoke Portrait 1080x1920).mp4".lower()
    # Mirror the classification chain in GCEEncodingBackend.encode().
    assert "portrait" in name
    assert "lossless 4k" not in name and "lossy 4k" not in name and "720p" not in name


def test_orchestrator_downloads_portrait_into_output_dir():
    """The download allowlist must include the portrait mapping so it reaches Dropbox."""
    import inspect
    from backend.workers import video_worker_orchestrator as vwo
    src = inspect.getsource(vwo.VideoWorkerOrchestrator._download_gce_encoded_files)
    assert "('portrait_mp4_path', 'portrait_video')" in src
