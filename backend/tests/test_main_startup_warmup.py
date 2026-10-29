"""Tests for the background startup warmup (cold-start work, 2026-09-22).

The NLP preloads and credential validation used to run inline in lifespan
startup, adding ~7.5s to every Cloud Run cold start (requests are held until
lifespan startup returns). They now run in a daemon thread so readiness isn't
gated on them. These tests pin that wiring.
"""

import threading
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.main as main_module


def test_background_warmup_runs_all_preloads_and_credential_check():
    with (
        patch.object(main_module, "preload_spacy_model") as spacy_mock,
        patch.object(main_module, "preload_all_nltk_resources") as nltk_mock,
        patch.object(main_module, "preload_langfuse_handler") as langfuse_mock,
        patch.object(main_module, "validate_credentials_on_startup") as creds_mock,
    ):
        main_module._run_background_warmup()

    spacy_mock.assert_called_once_with("en_core_web_sm")
    nltk_mock.assert_called_once()
    langfuse_mock.assert_called_once()
    creds_mock.assert_called_once()


def test_background_warmup_survives_individual_failures():
    """One preload failing must not stop the others (matches old inline behavior)."""
    with (
        patch.object(
            main_module, "preload_spacy_model", side_effect=RuntimeError("boom")
        ),
        patch.object(
            main_module, "preload_all_nltk_resources", side_effect=RuntimeError("boom")
        ),
        patch.object(main_module, "preload_langfuse_handler") as langfuse_mock,
        patch.object(
            main_module, "validate_credentials_on_startup", side_effect=RuntimeError
        ),
    ):
        main_module._run_background_warmup()  # must not raise

    langfuse_mock.assert_called_once()


def test_lifespan_starts_warmup_thread_without_blocking_readiness():
    """App startup must return while the (slow) warmup is still running."""
    warmup_started = threading.Event()
    warmup_release = threading.Event()

    def slow_warmup():
        warmup_started.set()
        # Simulate the ~7.5s of preloads; readiness must not wait for this.
        warmup_release.wait(timeout=10)

    with patch.object(main_module, "_run_background_warmup", slow_warmup):
        try:
            with TestClient(main_module.app):
                # Lifespan startup returned; the warmup must be in flight
                # (started but deliberately not finished).
                assert warmup_started.wait(timeout=5)
                assert not warmup_release.is_set()
        finally:
            warmup_release.set()



def test_lazy_route_modules_import_before_readiness_off_loop():
    """httpx & co. are imported in a worker thread during lifespan startup, before
    the first request (cold-start importlib stalls, 2026-10-08)."""
    import importlib

    seen = []
    real_import = importlib.import_module

    def tracking_import(name, *a, **kw):
        if name in main_module._LAZY_ROUTE_IMPORTS:
            seen.append((name, threading.current_thread().name))
        return real_import(name, *a, **kw)

    with patch.object(main_module, "_run_background_warmup"), \
         patch.object(importlib, "import_module", side_effect=tracking_import):
        with TestClient(main_module.app):
            pass

    assert [name for name, _ in seen] == list(main_module._LAZY_ROUTE_IMPORTS)
    # asyncio.to_thread's default-executor workers, i.e. not the event-loop thread.
    assert all(thread.startswith("asyncio_") for _, thread in seen), seen


def test_lazy_route_import_failure_is_non_fatal():
    import importlib

    with patch.object(importlib, "import_module", side_effect=ImportError("nope")):
        main_module._import_lazy_route_modules()  # logs, doesn't raise


def test_slow_lazy_route_import_does_not_block_startup(caplog):
    import logging
    import time

    caplog.set_level(logging.ERROR)
    with patch.object(main_module, "_run_background_warmup"), \
         patch.object(main_module, "LAZY_ROUTE_IMPORT_TIMEOUT_S", 0.05), \
         patch.object(main_module, "_import_lazy_route_modules", side_effect=lambda: time.sleep(0.5)):
        with TestClient(main_module.app):
            pass
    assert "exceeded 0.05s; continuing startup" in caplog.text


def test_lazy_route_imports_include_flacfetch_and_yt_dlp():
    """select_best's lazy flacfetch -> yt_dlp import froze the loop ~11s (2026-10-23/26)."""
    for module in ("flacfetch.core.manager", "flacfetch.downloaders.youtube", "yt_dlp"):
        assert module in main_module._LAZY_ROUTE_IMPORTS


class _FakeIncludedRouter:
    def __init__(self, low_priority):
        self._low_priority = low_priority
        self.low_priority_calls = 0

    def effective_low_priority_routes(self):
        self.low_priority_calls += 1
        return self._low_priority


def test_warm_route_contexts_walks_every_route_context(monkeypatch):
    """Newer FastAPI builds route contexts (regexes, schemas) lazily on the first
    request, which the OTel route walk did ON the loop (2026-10-23: 11s stall)."""
    import fastapi.routing
    from types import SimpleNamespace

    included = _FakeIncludedRouter(low_priority=["lp1"])
    walked = []

    def fake_iter_route_contexts(routes):
        walked.append(list(routes))
        yield from ("ctx1", "ctx2", "ctx3")

    monkeypatch.setattr(fastapi.routing, "iter_route_contexts", fake_iter_route_contexts, raising=False)
    app = SimpleNamespace(routes=[included, object()])

    assert main_module._warm_route_contexts(app) == 4
    assert walked == [app.routes]
    assert included.low_priority_calls == 1


def test_warm_route_contexts_noop_on_fastapi_without_lazy_contexts(monkeypatch):
    import fastapi.routing
    from types import SimpleNamespace

    monkeypatch.delattr(fastapi.routing, "iter_route_contexts", raising=False)
    assert main_module._warm_route_contexts(SimpleNamespace(routes=[])) == 0


def test_route_context_warmup_failure_is_non_fatal(caplog):
    import logging

    caplog.set_level(logging.WARNING)
    with patch.object(main_module, "_warm_route_contexts", side_effect=RuntimeError("internals moved")):
        main_module._warm_route_contexts_safely(main_module.app)  # logs, doesn't raise
    assert "Route-context warmup failed" in caplog.text


def test_lifespan_warms_route_contexts_off_loop_before_readiness():
    seen = []

    def record(application):
        seen.append((application, threading.current_thread().name))
        return 0

    with patch.object(main_module, "_run_background_warmup"), \
         patch.object(main_module, "_warm_route_contexts", side_effect=record):
        with TestClient(main_module.app):
            assert seen  # done before startup returned

    assert seen[0][0] is main_module.app
    assert seen[0][1].startswith("asyncio_"), seen
