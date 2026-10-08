"""
Themes can leave the title and/or end screen out of rendered videos
(``intro.enabled`` / ``end.enabled`` = false).

Covers the cloud pipeline end to end: the screens worker records which screens
it made (``state_data.screens_included``), the render prerequisites and /retry
ladder accept a missing omitted screen, the orchestrator/encoding config carry
explicit include flags, and the encoders (GCE worker + LocalEncodingService)
concatenate only the included segments, without rebuilding an omitted screen
from a theme background PNG that the encoder's loose globs would match.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.workers.style_helper import SCREENS_INCLUDED_KEY, screen_included


# --- marker helper --------------------------------------------------------------


def test_screen_included_defaults_to_true_for_older_jobs():
    assert screen_included({}, "title") and screen_included(None, "end")
    assert screen_included({SCREENS_INCLUDED_KEY: {"title": True}}, "end")
    assert not screen_included({SCREENS_INCLUDED_KEY: {"title": False, "end": True}}, "title")


# --- screens worker -------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("include_title,include_end", [(False, True), (True, False), (False, False), (True, True)])
async def test_screens_worker_skips_omitted_screens_and_records_them(tmp_path, include_title, include_end):
    from datetime import datetime, timezone

    from backend.models.job import Job, JobStatus
    from backend.workers import screens_worker as sw

    job = Job(
        job_id="j1", artist="A", title="T", status=JobStatus.DOWNLOADING,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        state_data={"lyrics_complete": True, "audio_complete": True},
    )
    jm = MagicMock()
    jm.get_job.return_value = job
    jm.transition_to_state.return_value = True
    style_config = MagicMock(include_title_screen=include_title, include_end_screen=include_end)

    async def load_style(*args, **kwargs):
        return style_config

    async def make_png(name):
        png = tmp_path / f"{name}.png"
        png.write_bytes(b"png")
        return str(png)

    async def noop(*args, **kwargs):
        return {}

    title_gen = MagicMock(side_effect=lambda **kw: make_png("title"))
    end_gen = MagicMock(side_effect=lambda **kw: make_png("end"))
    upload = MagicMock(side_effect=noop)
    with patch.object(sw, "JobManager", return_value=jm), \
         patch.object(sw, "StorageService"), \
         patch.object(sw, "get_settings"), \
         patch.object(sw, "create_job_logger"), \
         patch.object(sw, "setup_job_logging"), \
         patch.object(sw, "_validate_prerequisites", return_value=True), \
         patch("backend.services.stems_restore.maybe_start_stems_restore", return_value="not_needed"), \
         patch.object(sw, "load_style_config", side_effect=load_style), \
         patch.object(sw, "_create_video_generator"), \
         patch.object(sw, "_generate_title_screen", title_gen), \
         patch.object(sw, "_generate_end_screen", end_gen), \
         patch.object(sw, "_upload_screens", upload), \
         patch.object(sw, "_apply_countdown_padding_if_needed", side_effect=noop), \
         patch.object(sw, "_transcode_review_audio", side_effect=noop), \
         patch("backend.services.auto_approval.executor.maybe_auto_complete_review", side_effect=noop), \
         patch("backend.services.auto_approval.pre_apply.ensure_and_pre_apply", side_effect=noop):
        result = await sw.generate_screens("j1")
        assert result is True, jm.mark_job_failed.call_args

    assert title_gen.called is include_title
    assert end_gen.called is include_end
    kwargs = upload.call_args.kwargs
    assert (kwargs["title_screen_path"] is not None) is include_title
    assert (kwargs["end_screen_path"] is not None) is include_end
    jm.update_state_data.assert_any_call("j1", SCREENS_INCLUDED_KEY, {"title": include_title, "end": include_end})


def test_upload_screens_skips_an_omitted_screen(tmp_path):
    from backend.workers.screens_worker import _upload_screens_sync

    (tmp_path / "End.png").write_bytes(b"png")
    (tmp_path / "End.jpg").write_bytes(b"jpg")
    storage, jm = MagicMock(), MagicMock()
    storage.upload_file.side_effect = lambda local, gcs: f"gs://b/{gcs}"
    _upload_screens_sync("j1", jm, storage, None, str(tmp_path / "End.png"))
    assert [c.args[1] for c in storage.upload_file.call_args_list] == ["jobs/j1/screens/end.png", "jobs/j1/screens/end.jpg"]
    jm.update_file_url.assert_any_call("j1", "screens", "end_png", "gs://b/jobs/j1/screens/end.png")
    jm.update_file_url.assert_any_call("j1", "screens", "end_jpg", "gs://b/jobs/j1/screens/end.jpg")


# --- render prerequisites + retry ladder -----------------------------------------


def _video_job(screens, included=None):
    state = {"instrumental_selection": "clean"}
    if included is not None:
        state[SCREENS_INCLUDED_KEY] = included
    return SimpleNamespace(
        job_id="j1",
        state_data=state,
        file_urls={"screens": screens, "videos": {"with_vocals": "gs://v"}, "stems": {"instrumental_clean": "gs://i"}},
    )


@pytest.mark.parametrize(
    "screens,included,ok",
    [
        ({"title_png": "x", "end_png": "y"}, None, True),
        ({"end_png": "y"}, None, False),  # older job: a missing title is still an error
        ({"end_png": "y"}, {"title": False, "end": True}, True),
        ({"title_png": "x"}, {"title": True, "end": False}, True),
        ({}, {"title": False, "end": False}, True),
        ({}, {"title": True, "end": False}, False),
    ],
)
def test_video_prerequisites_accept_omitted_screens(screens, included, ok):
    from backend.workers import video_worker

    with patch.object(video_worker, "validate_worker_can_run", return_value=None):
        assert video_worker._validate_prerequisites(_video_job(screens, included)) is ok


@pytest.mark.parametrize(
    "file_urls,state_data,expected",
    [
        ({"screens": {"title_jpg": "x"}}, {}, True),
        ({"screens": {}}, {}, False),
        # Title omitted by the theme and the screens worker finished
        ({"screens": {"end_png": "y"}}, {SCREENS_INCLUDED_KEY: {"title": False}, "screens_progress": {"stage": "complete"}}, True),
        # ...but a re-render cleared screens_progress: the stale marker alone isn't enough
        ({"screens": {}}, {SCREENS_INCLUDED_KEY: {"title": False}}, False),
    ],
)
def test_retry_screens_check_understands_omitted_title(file_urls, state_data, expected):
    from backend.api.routes.jobs import _has_title_screen

    assert _has_title_screen(file_urls, state_data) is expected


# --- orchestrator + encoding config ---------------------------------------------


def _orch_job(included):
    return SimpleNamespace(
        job_id="j1", artist="A", title="T",
        state_data={"instrumental_selection": "clean", SCREENS_INCLUDED_KEY: included},
        file_urls={"screens": {"end_png": "gs://b/jobs/j1/screens/end.png"}, "videos": {"with_vocals": "gs://b/v.mkv"}},
    )


def test_orchestrator_config_drops_omitted_screen_paths(tmp_path):
    from backend.workers import video_worker_orchestrator as vwo

    dist = MagicMock(enable_youtube_upload=False, brand_prefix=None, discord_webhook_url=None,
                     youtube_description=None, dropbox_path=None, gdrive_folder_id=None)
    with patch("backend.services.job_defaults_service.get_effective_distribution_for_job", return_value=dist):
        config = vwo.create_orchestrator_config_from_job(_orch_job({"title": False, "end": True}), str(tmp_path))
    assert config.title_video_path is None
    assert config.end_video_path.endswith("A - T (End).png")
    assert config.include_title_screen is False and config.include_end_screen is True


@pytest.mark.asyncio
async def test_orchestrator_passes_include_flags_to_the_encoder():
    from backend.workers.video_worker_orchestrator import OrchestratorConfig, VideoWorkerOrchestrator

    config = OrchestratorConfig(
        job_id="j1", artist="A", title="T", title_video_path=None, karaoke_video_path="k.mkv",
        instrumental_audio_path="i.flac", include_title_screen=False, include_end_screen=True,
    )
    orch = VideoWorkerOrchestrator(config=config, job_manager=MagicMock(), storage=MagicMock())
    backend = MagicMock()
    backend.name = "local"
    backend.encode = MagicMock(side_effect=RuntimeError("stop"))
    orch._get_encoding_backend = MagicMock(return_value=backend)
    with pytest.raises(RuntimeError, match="stop"):
        await orch._run_encoding()
    encoding_input = backend.encode.call_args.args[0]
    assert encoding_input.title_video_path is None
    assert encoding_input.options["include_title_screen"] is False
    assert encoding_input.options["include_end_screen"] is True


@pytest.mark.asyncio
async def test_original_vocals_guide_has_no_intro_offset_without_a_title_card():
    from backend.workers.video_worker_orchestrator import OrchestratorConfig, VideoWorkerOrchestrator

    config = OrchestratorConfig(
        job_id="j1", artist="A", title="T", title_video_path=None, karaoke_video_path="k",
        instrumental_audio_path="i", include_title_screen=False,
    )
    orch = VideoWorkerOrchestrator(config=config, job_manager=MagicMock(), storage=MagicMock())
    assert await orch._resolve_intro_seconds() == 0.0


@pytest.mark.asyncio
async def test_gce_backend_sends_include_flags_only_when_a_screen_is_omitted():
    from backend.services.encoding_interface import EncodingInput, GCEEncodingBackend

    def make_input(**flags):
        return EncodingInput(
            title_video_path=None, karaoke_video_path="k", instrumental_audio_path="i", artist="A", title="T",
            options={"job_id": "j1", "input_gcs_path": "gs://b/in/", "output_gcs_path": "gs://b/out/", **flags},
        )

    sent = []

    async def encode_videos(job_id, input_gcs_path, output_gcs_path, encoding_config, **kwargs):
        sent.append(encoding_config)
        raise RuntimeError("stop")

    gce = GCEEncodingBackend()
    service = MagicMock()
    service.is_enabled = True
    service.encode_videos = encode_videos
    gce._service = service
    with patch.object(GCEEncodingBackend, "_get_service", return_value=service):
        await gce.encode(make_input(include_title_screen=False, include_end_screen=True))
        await gce.encode(make_input(include_title_screen=True, include_end_screen=True))
    assert sent[0]["include_title_screen"] is False and "include_end_screen" not in sent[0]
    assert "include_title_screen" not in sent[1] and "include_end_screen" not in sent[1]


# --- encoders -------------------------------------------------------------------


@pytest.mark.parametrize(
    "with_title,with_end,n",
    [(True, True, 3), (False, True, 2), (True, False, 2), (False, False, 1)],
)
def test_lossless_concat_includes_only_present_segments(tmp_path, with_title, with_end, n):
    from backend.services.local_encoding_service import LocalEncodingService

    end = tmp_path / "end.mov"
    end.write_bytes(b"x")
    service = LocalEncodingService(logger=MagicMock())
    with patch.object(service, "_execute_command_with_fallback", return_value=True) as run:
        assert service.encode_lossless_mp4(
            str(tmp_path / "title.mov") if with_title else None,
            str(tmp_path / "karaoke.mkv"),
            str(tmp_path / "out.mp4"),
            str(end) if with_end else None,
        )
    gpu, cpu = run.call_args.args[:2]
    for cmd in (gpu, cpu):
        assert f"concat=n={n}:v=1:a=1" in cmd
        assert cmd.count(" -i ") == n
        assert ("title.mov" in cmd) is with_title
        assert ("end.mov" in cmd) is with_end
        assert "karaoke.mkv" in cmd
    # Segment order: title, karaoke, end
    assert cpu.index("karaoke.mkv") > (cpu.index("title.mov") if with_title else -1)
    if with_end:
        assert cpu.index("end.mov") > cpu.index("karaoke.mkv")


def _gce_work_dir(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    (work / "style").mkdir(parents=True)
    (work / "videos").mkdir()
    (work / "stems").mkdir()
    # Theme assets the encoder's loose "*title*.png" / "*end*.png" globs would match
    (work / "style" / "cdg_title_background.png").write_bytes(b"png" * 1000)
    (work / "style" / "end_background.png").write_bytes(b"png" * 1000)
    (work / "videos" / "with_vocals.mkv").write_bytes(b"mkv" * 1000)
    (work / "stems" / "instrumental_clean.flac").write_bytes(b"flac" * 1000)
    return work


@pytest.mark.parametrize("include_title,include_end", [(False, False), (False, True), (True, False)])
def test_gce_worker_never_rebuilds_an_omitted_screen_from_theme_pngs(tmp_path, include_title, include_end):
    from backend.services.gce_encoding import main

    work = _gce_work_dir(tmp_path)
    (work / "screens").mkdir()
    for screen, included in (("title", include_title), ("end", include_end)):
        if included:
            (work / "screens" / f"{screen}.png").write_bytes(b"png" * 1000)

    made = []

    def fake_mov(png, mov, duration=5):
        made.append(png.name)
        mov.write_bytes(b"mov" * 1000)
        return mov

    service = MagicMock()
    service.encode_all_formats.return_value = MagicMock(success=True)
    main.jobs["j1"] = {"status": "pending", "progress": 0}
    with patch.object(main, "generate_mov_from_png", side_effect=fake_mov), \
         patch.object(main, "render_portrait_into_outputs"), \
         patch.object(main, "_persist"), \
         patch("backend.services.local_encoding_service.LocalEncodingService", return_value=service):
        main.run_encoding("j1", work, {
            "artist": "A", "title": "T",
            "include_title_screen": include_title, "include_end_screen": include_end,
        })
    config = service.encode_all_formats.call_args.args[0]
    assert (config.title_video is not None) is include_title
    assert (config.end_video is not None) is include_end
    assert sorted(made) == sorted(f"{s}.png" for s, inc in (("title", include_title), ("end", include_end)) if inc)
    staged = sorted(p.name for p in (work / "outputs").glob("*.mov"))
    assert ("A - T (Title).mov" in staged) is include_title
    assert ("A - T (End).mov" in staged) is include_end


def test_gce_worker_still_requires_a_title_when_included(tmp_path):
    from backend.services.gce_encoding import main

    work = _gce_work_dir(tmp_path)
    (work / "style" / "cdg_title_background.png").unlink()
    main.jobs["j2"] = {"status": "pending", "progress": 0}
    with patch.object(main, "_persist"), \
         patch.object(main, "generate_mov_from_png", side_effect=lambda png, mov, duration=5: mov), \
         patch("backend.services.local_encoding_service.LocalEncodingService"):
        with pytest.raises(Exception, match="No title video"):
            main.run_encoding("j2", work, {"artist": "A", "title": "T"})
