"""Quick version (fastgen-style draft video) for kjbox make-it jobs."""
import os
import shutil
import subprocess
from datetime import datetime, UTC
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from backend.services.quick_version import renderer as r
from backend.services.quick_version import service as qs


# --------------------------------------------------------------------------- #
# Lyrics parsing / tiers
# --------------------------------------------------------------------------- #
class TestLyrics:
    def test_parse_synced_sorts_and_expands_multi_stamps(self):
        lrc = "[00:12.50]second\n[00:05.00][01:00.00]chorus\nno stamp line\n"
        assert r.parse_synced(lrc) == [(5.0, "chorus"), (12.5, "second"), (60.0, "chorus")]

    def test_payload_prefers_synced(self):
        got = r.lyrics_from_lrclib_payload({"syncedLyrics": "[00:01.00]hi", "plainLyrics": "hi"})
        assert got.kind == "synced" and got.timed == [(1.0, "hi")] and got.plain == "hi"

    def test_payload_plain_and_empty(self):
        assert r.lyrics_from_lrclib_payload({"syncedLyrics": " ", "plainLyrics": "a\nb"}).kind == "plain"
        assert r.lyrics_from_lrclib_payload({"syncedLyrics": None, "plainLyrics": ""}) is None

    def test_resolve_tiers(self):
        synced = r.Lyrics("synced", "a", [(1.0, "a"), (2.0, " ")])
        assert r.resolve_timed_lines(synced) == ([("a", 1.0)], "synced")
        plain = r.Lyrics("plain", "a\n\nb")
        assert r.resolve_timed_lines(plain) == ([("a", None), ("", None), ("b", None)], "constant")
        assert r.resolve_timed_lines(None) == ([], "none")


def _resp(status, payload):
    return SimpleNamespace(status_code=status, json=lambda: payload)


class TestFetchLrclib:
    def _session(self, get_side_effect):
        sess = MagicMock()
        sess.headers = {}
        sess.get.side_effect = get_side_effect
        return sess

    def test_exact_synced_hit_skips_search(self):
        sess = self._session([_resp(200, {"syncedLyrics": "[00:01.00]x"})])
        with patch.object(r.requests, "Session", return_value=sess):
            got = r.fetch_lrclib_lyrics("A", "T", 180.4)
        assert got.kind == "synced"
        assert sess.get.call_count == 1
        assert sess.get.call_args.kwargs["params"]["duration"] == 180

    def test_exact_plain_then_search_synced_wins(self):
        sess = self._session([
            _resp(200, {"plainLyrics": "plain words"}),
            _resp(200, [{"plainLyrics": "p"}, {"syncedLyrics": "[00:02.00]s"}]),
        ])
        with patch.object(r.requests, "Session", return_value=sess):
            got = r.fetch_lrclib_lyrics("A", "T", None)
        assert got.kind == "synced" and got.timed == [(2.0, "s")]

    def test_falls_back_to_exact_plain(self):
        sess = self._session([_resp(200, {"plainLyrics": "plain words"}), _resp(200, [])])
        with patch.object(r.requests, "Session", return_value=sess):
            got = r.fetch_lrclib_lyrics("A", "T", None)
        assert got.kind == "plain" and got.plain == "plain words"

    def test_network_errors_return_none(self):
        err = r.requests.RequestException("down")
        sess = self._session([err, err])
        with patch.object(r.requests, "Session", return_value=sess):
            assert r.fetch_lrclib_lyrics("A", "T", 100) is None


# --------------------------------------------------------------------------- #
# Layout + scroll expression + ffmpeg command
# --------------------------------------------------------------------------- #
class TestLayout:
    def test_gaps_inserted_only_for_long_breaks(self):
        lines = [("a", 1.0), ("b", 3.0), ("c", 20.0)]
        assert r.insert_instrumental_gaps(lines, 5.0) == [("a", 1.0), ("b", 3.0), ("", None), ("c", 20.0)]
        assert r.insert_instrumental_gaps(lines, 0) == lines

    def test_visual_lines_wrap_keeps_anchor_on_first_row(self):
        vis = r.build_visual_lines("abba", "waterloo", [("one two three four", 7.0), ("", None)], wrap=8)
        assert vis[2:4] == [("ABBA", None), ("WATERLOO", None)]
        body = vis[6:-3]
        assert body[0] == ("one two", 7.0)
        assert all(a is None for _, a in body[1:])
        assert body[-1] == ("", None)

    def test_scroll_expr_time_anchored(self):
        lines = [("", None), ("a", 10.0), ("b", 20.0)]
        expr, timed = r.build_scroll_y_expr(lines, [0, 100, 150], 60.0, 480, 400, 0.5)
        assert timed
        assert expr.startswith("480.00+")
        assert "max(0\\,t-10.000)" in expr  # commas escaped for the filtergraph

    def test_scroll_expr_constant_crawl_without_anchors(self):
        expr, timed = r.build_scroll_y_expr([("a", None)], [50], 100.0, 480, 1000, 0.42)
        assert not timed
        assert expr == "480.00+(-14.80000)*(t-0.000)"

    def test_scroll_expr_degenerate_duration(self):
        expr, timed = r.build_scroll_y_expr([("a", None)], [50], 0.0, 480, 1000, 0.42)
        assert (expr, timed) == ("480.00", False)

    def test_ffmpeg_cmd_mixes_guide_vocals(self):
        cmd = r.build_ffmpeg_cmd("i.flac", "v.flac", 0.3, "c.png", "Y", "o.mp4", 854, 480, 24, 180.0)
        filt = cmd[cmd.index("-filter_complex") + 1]
        assert "amix=inputs=2:normalize=0" in filt and "volume=0.300" in filt
        assert cmd[-1] == "o.mp4" and "+faststart" in cmd

    def test_ffmpeg_cmd_without_vocals_maps_instrumental(self):
        cmd = r.build_ffmpeg_cmd("i.flac", None, 0.3, "c.png", "Y", "o.mp4", 854, 480, 24, 180.0)
        assert "2:a" in cmd and "v.flac" not in cmd

    def test_ffmpeg_cmd_bounds_every_stream_to_duration(self):
        """ffmpeg 4.4 + amix never ends on -shortest alone (prod 2026-09-28)."""
        cmd = r.build_ffmpeg_cmd("i.flac", "v.flac", 0.3, "c.png", "Y", "o.mp4", 854, 480, 24, 195.04)
        assert "color=c=black:s=854x480:r=24:d=195.040" in cmd
        png = cmd.index("c.png")
        assert cmd[png - 3:png - 1] == ["-t", "195.040"]
        assert cmd[-4:-2] == ["-t", "195.040"]

    def test_font_is_bundled(self):
        assert os.path.exists(r._FONT_PATH)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_render_quick_video_end_to_end(tmp_path):
    """Real PIL + ffmpeg render from a generated tone with supplied synced lyrics."""
    audio = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                    str(audio)], check=True)
    lyrics = r.Lyrics("synced", "hello\nworld", [(1.0, "hello"), (3.0, "world")])
    out = tmp_path / "quick.mp4"
    res = r.render_quick_video(instrumental_path=str(audio), vocals_path=None, artist="A", title="T",
                               out_path=str(out), workdir=str(tmp_path), lyrics=lyrics)
    assert out.exists() and out.stat().st_size > 1000
    assert res.lyrics_tier == "synced" and res.line_count == 2
    assert 5.5 < res.duration_seconds < 6.5


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_render_without_lyrics_still_produces_video(tmp_path):
    audio = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=3", str(audio)],
                   check=True)
    out = tmp_path / "q.mp4"
    res = r.render_quick_video(instrumental_path=str(audio), vocals_path=None, artist="A", title="T",
                               out_path=str(out), workdir=str(tmp_path), fetch_lyrics=False)
    assert res.lyrics_tier == "none" and out.exists()


# --------------------------------------------------------------------------- #
# Service: gating, separation, render thread
# --------------------------------------------------------------------------- #
def _job(client_id="kjbox", quick=None):
    return SimpleNamespace(
        job_id="j1", artist="ABBA", title="Waterloo",
        request_metadata={"client_id": client_id} if client_id else {},
        state_data={"quick_version": quick} if quick else {},
    )


class TestGating:
    def test_kjbox_jobs_only(self):
        assert qs.should_build_quick_version(_job("kjbox"))
        assert qs.should_build_quick_version(_job("kjbox-nomadpc"))
        assert not qs.should_build_quick_version(_job("web"))
        assert not qs.should_build_quick_version(_job(None))

    def test_skips_when_already_ready(self):
        assert not qs.should_build_quick_version(_job(quick={"status": "ready"}))
        assert qs.should_build_quick_version(_job(quick={"status": "failed"}))

    def test_env_kill_switch(self, monkeypatch):
        monkeypatch.setenv("KJBOX_QUICK_VERSION_ENABLED", "false")
        assert not qs.should_build_quick_version(_job())


class TestClassify:
    @pytest.mark.parametrize("name,kind", [
        ("quick_instrumental.flac", "instrumental"),
        ("quick_vocals.flac", "vocals"),
        ("song_(Instrumental)_model.flac", "instrumental"),
        ("song_(No Vocal)_model.flac", "instrumental"),
        ("vocals song_(Vocals)_model.flac", "vocals"),
        ("random.flac", None),
    ])
    def test_classify(self, name, kind):
        assert qs._classify_output(name) == kind


class _FakeSeparator:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _FakeSeparator.instances.append(self)

    def load_model(self, model_filename):
        self.model = model_filename

    def separate(self, path, custom_output_names=None):
        self.custom = custom_output_names
        out = self.kwargs["output_dir"]
        for n in ("quick_instrumental.flac", "quick_vocals.flac"):
            open(os.path.join(out, n), "w").close()
        return ["quick_instrumental.flac", "quick_vocals.flac"]


class TestSeparateQuick:
    def test_single_model_fast_params(self, tmp_path):
        _FakeSeparator.instances.clear()
        inst, voc = qs.separate_quick("in.flac", str(tmp_path), "/models", separator_factory=_FakeSeparator)
        sep = _FakeSeparator.instances[0]
        assert sep.model == qs.DEFAULT_QUICK_MODEL
        assert sep.kwargs["model_file_dir"] == "/models"
        assert sep.kwargs["mdxc_params"]["overlap"] == qs.QUICK_MDXC_OVERLAP
        assert sep.custom["Instrumental"] == "quick_instrumental"
        assert inst.endswith("quick_instrumental.flac") and voc.endswith("quick_vocals.flac")

    def test_model_override_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QUICK_VERSION_MODEL", "other.onnx")
        _FakeSeparator.instances.clear()
        qs.separate_quick("in.flac", str(tmp_path), None, separator_factory=_FakeSeparator)
        assert _FakeSeparator.instances[0].model == "other.onnx"
        assert "model_file_dir" not in _FakeSeparator.instances[0].kwargs

    def test_falls_back_to_baked_model_when_default_fails(self, tmp_path):
        class FirstFails(_FakeSeparator):
            def load_model(self, model_filename):
                if model_filename == qs.DEFAULT_QUICK_MODEL:
                    raise RuntimeError("download failed")
                self.model = model_filename
        _FakeSeparator.instances.clear()
        inst, _ = qs.separate_quick("in.flac", str(tmp_path), "/models", separator_factory=FirstFails)
        assert _FakeSeparator.instances[-1].model == qs.FALLBACK_QUICK_MODEL
        assert inst.endswith("quick_instrumental.flac")

    def test_both_models_failing_raises(self, tmp_path):
        class AllFail(_FakeSeparator):
            def load_model(self, model_filename):
                raise RuntimeError("nope")
        with pytest.raises(RuntimeError):
            qs.separate_quick("in.flac", str(tmp_path), "/models", separator_factory=AllFail)

    def test_no_instrumental_raises(self, tmp_path):
        class Empty(_FakeSeparator):
            def separate(self, path, custom_output_names=None):
                return []
        with pytest.raises(RuntimeError):
            qs.separate_quick("in.flac", str(tmp_path), None, separator_factory=Empty)


def _states(job_manager):
    return [c.args[2]["status"] for c in job_manager.update_state_data.call_args_list if c.args[1] == "quick_version"]


class TestStartQuickVersion:
    def test_non_kjbox_job_is_skipped(self, tmp_path):
        jm = Mock()
        assert qs.start_quick_version(_job("web"), "a.flac", str(tmp_path), "/models", jm, Mock()) is None
        jm.update_state_data.assert_not_called()

    def test_success_uploads_and_marks_ready(self, tmp_path):
        jm, storage = Mock(), Mock()

        def fake_render(**kw):
            open(kw["out_path"], "wb").write(b"x" * 10)
            return r.RenderResult(kw["out_path"], "synced", 42, 165.7)

        with patch.object(qs, "render_quick_video", side_effect=fake_render):
            job = qs.start_quick_version(_job(), "a.flac", str(tmp_path), "/models", jm, storage,
                                         separator_factory=_FakeSeparator)
            assert job is not None and job.join(10)
        assert job.ok is True
        storage.upload_file.assert_called_once()
        assert storage.upload_file.call_args.args[1] == "jobs/j1/quick/quick.mp4"
        jm.update_file_url.assert_called_once_with("j1", "quick", "video_mp4", "jobs/j1/quick/quick.mp4")
        assert _states(jm) == ["separating", "rendering", "ready"]
        ready = jm.update_state_data.call_args_list[-1].args[2]
        assert ready["lyrics_tier"] == "synced" and ready["line_count"] == 42 and "ready_at" in ready

    def test_separation_failure_is_non_fatal(self, tmp_path):
        jm = Mock()

        class Boom(_FakeSeparator):
            def load_model(self, model_filename):
                raise RuntimeError("cuda oom")

        assert qs.start_quick_version(_job(), "a.flac", str(tmp_path), "/m", jm, Mock(),
                                      separator_factory=Boom) is None
        assert _states(jm) == ["separating", "failed"]
        assert "cuda oom" in jm.update_state_data.call_args_list[-1].args[2]["error"]

    def test_render_failure_marks_failed(self, tmp_path):
        jm, storage = Mock(), Mock()
        with patch.object(qs, "render_quick_video", side_effect=RuntimeError("ffmpeg died")):
            job = qs.start_quick_version(_job(), "a.flac", str(tmp_path), "/m", jm, storage,
                                         separator_factory=_FakeSeparator)
            assert job.join(10)
        assert job.ok is False
        storage.upload_file.assert_not_called()
        assert _states(jm)[-1] == "failed"


# --------------------------------------------------------------------------- #
# Audio worker wiring
# --------------------------------------------------------------------------- #
class TestAudioWorkerWiring:
    def _job(self):
        from backend.models.job import Job, JobStatus
        return Job(job_id="j1", status=JobStatus.DOWNLOADING, created_at=datetime.now(UTC),
                   updated_at=datetime.now(UTC), artist="A", title="T",
                   request_metadata={"client_id": "kjbox"})

    @pytest.mark.asyncio
    async def test_quick_render_joined_even_when_ensemble_fails(self, monkeypatch, tmp_path):
        monkeypatch.delenv("AUDIO_SEPARATOR_API_URL", raising=False)
        monkeypatch.setenv("MODEL_DIR", "/models")
        jm = MagicMock()
        jm.get_job.return_value = self._job()
        render = Mock()
        render.join.return_value = True
        audio = tmp_path / "a.flac"
        audio.write_bytes(b"x")

        with patch("backend.workers.audio_worker.JobManager", return_value=jm), \
             patch("backend.workers.audio_worker.StorageService"), \
             patch("backend.workers.audio_worker.download_audio", return_value=str(audio)), \
             patch("backend.workers.audio_worker._store_audio_source_metadata"), \
             patch("backend.workers.audio_worker.start_quick_version", return_value=render) as start, \
             patch("backend.workers.audio_worker.create_audio_processor", side_effect=RuntimeError("ensemble")):
            from backend.workers.audio_worker import process_audio_separation
            assert await process_audio_separation("j1") is False

        start.assert_called_once()
        assert start.call_args.args[3] == "/models"
        render.join.assert_called_once()
        jm.mark_job_failed.assert_called_once()

    @pytest.mark.asyncio
    async def test_render_timeout_marks_failed(self, monkeypatch, tmp_path):
        monkeypatch.delenv("AUDIO_SEPARATOR_API_URL", raising=False)
        monkeypatch.setenv("MODEL_DIR", "/models")
        jm = MagicMock()
        jm.get_job.return_value = self._job()
        render = Mock()
        render.join.return_value = False
        audio = tmp_path / "a.flac"
        audio.write_bytes(b"x")
        with patch("backend.workers.audio_worker.JobManager", return_value=jm), \
             patch("backend.workers.audio_worker.StorageService"), \
             patch("backend.workers.audio_worker.download_audio", return_value=str(audio)), \
             patch("backend.workers.audio_worker._store_audio_source_metadata"), \
             patch("backend.workers.audio_worker.start_quick_version", return_value=render), \
             patch("backend.workers.audio_worker.create_audio_processor", side_effect=RuntimeError("x")):
            from backend.workers.audio_worker import process_audio_separation
            await process_audio_separation("j1")
        quick_calls = [c.args[2] for c in jm.update_state_data.call_args_list if c.args[1] == "quick_version"]
        assert quick_calls and quick_calls[-1]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_remote_api_mode_skips_quick_version(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUDIO_SEPARATOR_API_URL", "https://sep")
        monkeypatch.delenv("MODEL_DIR", raising=False)
        jm = MagicMock()
        jm.get_job.return_value = self._job()
        audio = tmp_path / "a.flac"
        audio.write_bytes(b"x")
        with patch("backend.workers.audio_worker.JobManager", return_value=jm), \
             patch("backend.workers.audio_worker.StorageService"), \
             patch("backend.workers.audio_worker.download_audio", return_value=str(audio)), \
             patch("backend.workers.audio_worker._store_audio_source_metadata"), \
             patch("backend.workers.audio_worker.start_quick_version") as start, \
             patch("backend.workers.audio_worker.create_audio_processor", side_effect=RuntimeError("x")):
            from backend.workers.audio_worker import process_audio_separation
            await process_audio_separation("j1")
        start.assert_not_called()
