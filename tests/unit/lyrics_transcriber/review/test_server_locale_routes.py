"""Regression tests for locale-prefixed routes on the local ReviewServer.

The i18n static export generates `/{locale}/...` mirrors of every page and the
frontend's LocaleRedirect component unconditionally bounces legacy non-locale
paths (e.g. `/app/jobs/local/review`) to `/{locale}/...`. Before this fix the
review server only registered handlers for the non-locale paths, so every
browser load of the local review UI 404'd as soon as the redirect ran.
"""
import logging
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from karaoke_gen.lyrics_transcriber.review.server import ReviewServer


@pytest.fixture
def fake_frontend(tmp_path: Path) -> Path:
    """Build a minimal static-export layout the server can serve from."""
    root = tmp_path / "out"
    root.mkdir()
    (root / "index.html").write_text("<html>root</html>")

    # Non-locale legacy paths
    (root / "app" / "jobs" / "local" / "review").mkdir(parents=True)
    (root / "app" / "jobs" / "local" / "review" / "index.html").write_text(
        "<html><head></head><body>local review</body></html>"
    )
    (root / "app" / "jobs").mkdir(parents=True, exist_ok=True)
    (root / "app" / "jobs" / "index.html").write_text("<html>jobs index</html>")
    (root / "app" / "index.html").write_text("<html>app index</html>")
    (root / "favicon.ico").write_text("icon")

    # Locale mirrors (`en`, `es`) — the LocaleRedirect target
    for locale in ("en", "es"):
        (root / locale / "app" / "jobs" / "local" / "review").mkdir(parents=True)
        (root / locale / "app" / "jobs" / "local" / "review" / "index.html").write_text(
            f"<html><head></head><body>{locale} local review</body></html>"
        )
        (root / locale / "app" / "jobs").mkdir(parents=True, exist_ok=True)
        (root / locale / "app" / "jobs" / "index.html").write_text(f"<html>{locale} jobs</html>")
        (root / locale / "app" / "index.html").write_text(f"<html>{locale} app</html>")
        (root / locale / "index.html").write_text(f"<html>{locale} root</html>")

    # Empty `_next` so the server's static mount doesn't blow up
    (root / "_next" / "static" / "chunks").mkdir(parents=True)

    # A noise dir that looks nothing like a locale — must be ignored
    (root / "admin").mkdir()
    return root


@pytest.fixture
def client(fake_frontend, monkeypatch):
    """Instantiate the ReviewServer with a minimal fake frontend and return a TestClient.

    We patch the ReviewServer's frontend accessors so it serves from the temp dir
    rather than the packaged `karaoke_gen/nextjs_frontend/out/` assets.
    """
    # Patch the Next.js assets accessors to point at our fake build
    monkeypatch.setattr(
        "karaoke_gen.nextjs_frontend.get_nextjs_assets_dir",
        lambda: fake_frontend,
    )
    monkeypatch.setattr(
        "karaoke_gen.nextjs_frontend.is_nextjs_frontend_available",
        lambda: True,
    )

    # The ReviewServer constructor needs an OutputConfig with cache_dir set
    # (for FeedbackStore initialization). The optional stores swallow exceptions
    # so we only need an existing directory.
    from karaoke_gen.lyrics_transcriber.core.config import OutputConfig
    cache_dir = fake_frontend.parent / "cache"
    cache_dir.mkdir()
    output_config = OutputConfig(
        output_styles_json="",
        cache_dir=str(cache_dir),
        output_dir=str(cache_dir),
    )

    server = ReviewServer(
        correction_result=None,  # Not used by the SPA routes we're testing
        output_config=output_config,
        audio_filepath="/fake/audio.wav",
        logger=logging.getLogger("test"),
    )
    return TestClient(server.app)


class TestLocaleDiscovery:
    def test_discovers_two_letter_locale_dirs(self, fake_frontend):
        locales = ReviewServer._discover_locales(str(fake_frontend))
        assert locales == {"en", "es"}

    def test_ignores_non_locale_dirs(self, fake_frontend):
        locales = ReviewServer._discover_locales(str(fake_frontend))
        assert "admin" not in locales
        assert "_next" not in locales
        assert "app" not in locales

    def test_handles_missing_frontend_dir(self):
        assert ReviewServer._discover_locales("/nonexistent/path") == set()


class TestLocalReviewLocalizedRoute:
    """The primary regression: `/en/app/jobs/local/review` must return 200."""

    def test_en_localized_review_returns_200(self, client):
        r = client.get("/en/app/jobs/local/review")
        assert r.status_code == 200
        assert "en local review" in r.text

    def test_es_localized_review_returns_200(self, client):
        r = client.get("/es/app/jobs/local/review")
        assert r.status_code == 200
        assert "es local review" in r.text

    def test_unknown_locale_returns_404(self, client):
        r = client.get("/xx/app/jobs/local/review")
        assert r.status_code == 404

    def test_non_locale_legacy_route_still_works(self, client):
        """The original /app/jobs/local/review must still serve for backward compat."""
        r = client.get("/app/jobs/local/review")
        assert r.status_code == 200
        assert "local review" in r.text


class TestLocalizedJobsSpaRoutes:
    def test_localized_jobs_path_serves_jobs_index(self, client):
        r = client.get("/en/app/jobs/some-random-spa-path")
        assert r.status_code == 200
        assert "en jobs" in r.text

    def test_unknown_locale_on_jobs_path_returns_404(self, client):
        r = client.get("/xx/app/jobs/whatever")
        assert r.status_code == 404


class TestLocalizedAppRoutes:
    def test_localized_app_path_serves_app_index(self, client):
        r = client.get("/en/app/referrals")
        assert r.status_code == 200
        assert "en app" in r.text

    def test_unknown_locale_on_app_path_returns_404(self, client):
        r = client.get("/xx/app/referrals")
        assert r.status_code == 404


class TestLocaleRootRoute:
    def test_known_locale_root_serves_locale_index(self, client):
        r = client.get("/en")
        assert r.status_code == 200
        assert "en root" in r.text

    def test_unknown_locale_root_returns_404(self, client):
        r = client.get("/xx")
        assert r.status_code == 404


class TestLegacyRoutesUnchanged:
    """Confirm the non-locale routes still work exactly as before this fix."""

    def test_root_serves_index_html(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "root" in r.text

    def test_legacy_jobs_spa_route(self, client):
        r = client.get("/app/jobs/some-path")
        assert r.status_code == 200
        assert "jobs index" in r.text

    def test_legacy_app_route(self, client):
        r = client.get("/app/other-page")
        assert r.status_code == 200
        assert "app index" in r.text
