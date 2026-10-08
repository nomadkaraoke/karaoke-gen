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


def test_warmup_pre_imports_lazy_route_modules():
    """httpx & co. are imported off the loop at boot (cold-start importlib stalls, 2026-10-08)."""
    import importlib

    with (
        patch.object(main_module, "preload_spacy_model"),
        patch.object(main_module, "preload_all_nltk_resources"),
        patch.object(main_module, "preload_langfuse_handler"),
        patch.object(main_module, "validate_credentials_on_startup"),
        patch.object(importlib, "import_module", wraps=importlib.import_module) as imp,
    ):
        main_module._run_background_warmup()

    imported = [c.args[0] for c in imp.call_args_list]
    assert imported[: len(main_module._LAZY_ROUTE_IMPORTS)] == list(main_module._LAZY_ROUTE_IMPORTS)
    assert "httpx" in main_module._LAZY_ROUTE_IMPORTS


def test_lazy_route_import_failure_does_not_stop_warmup():
    import importlib

    with (
        patch.object(main_module, "preload_spacy_model") as spacy_mock,
        patch.object(main_module, "preload_all_nltk_resources"),
        patch.object(main_module, "preload_langfuse_handler"),
        patch.object(main_module, "validate_credentials_on_startup"),
        patch.object(importlib, "import_module", side_effect=ImportError("nope")),
    ):
        main_module._run_background_warmup()

    spacy_mock.assert_called_once()
