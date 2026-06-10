"""
Fallback audit (Theme 7): production must fail loudly on missing required config
rather than silently running on dev defaults (wrong GCP project / GCS bucket /
localhost worker URL / console-only email), and the tenant query-param override
must be production-safe by default.
"""
import importlib

import pytest

from backend.config import is_production, validate_production_config
from backend.services.email_service import EmailService


# ---- is_production() ----

def test_is_production_via_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("K_SERVICE", raising=False)
    assert is_production() is True


def test_is_production_via_k_service(monkeypatch):
    """Cloud Run sets K_SERVICE automatically — production-safe even if ENVIRONMENT is unset."""
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("K_SERVICE", "karaoke-api")
    assert is_production() is True


def test_is_production_false_in_dev(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    assert is_production() is False


# ---- validate_production_config() ----

def test_validate_raises_when_required_var_missing_in_prod(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "nomadkaraoke")
    monkeypatch.setenv("GCS_BUCKET_NAME", "karaoke-gen-storage-nomadkaraoke")
    monkeypatch.delenv("CLOUD_RUN_SERVICE_URL", raising=False)  # missing!

    with pytest.raises(RuntimeError, match="CLOUD_RUN_SERVICE_URL"):
        validate_production_config()


def test_validate_passes_when_all_required_set_in_prod(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "nomadkaraoke")
    monkeypatch.setenv("GCS_BUCKET_NAME", "karaoke-gen-storage-nomadkaraoke")
    monkeypatch.setenv("CLOUD_RUN_SERVICE_URL", "https://api.nomadkaraoke.com")

    validate_production_config()  # must not raise


def test_validate_noop_in_dev(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("CLOUD_RUN_SERVICE_URL", raising=False)
    validate_production_config()  # no-op, must not raise


# ---- email provider (7.5) ----

def test_email_provider_raises_in_prod_without_postmark(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("POSTMARK_SERVER_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="POSTMARK_SERVER_TOKEN"):
        EmailService()


def test_email_provider_console_in_dev_without_postmark(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("POSTMARK_SERVER_TOKEN", raising=False)
    svc = EmailService()  # must not raise — console fallback is fine in dev
    assert svc.is_configured() is False


# ---- tenant gate (7.4) ----

def test_tenant_gate_production_safe_with_k_service(monkeypatch):
    """K_SERVICE present (Cloud Run) → IS_PRODUCTION True → query-param override disabled."""
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("K_SERVICE", "karaoke-api")
    import backend.middleware.tenant as tenant_mod
    try:
        importlib.reload(tenant_mod)
        assert tenant_mod.IS_PRODUCTION is True
    finally:
        # Restore module to a clean (dev) state so other tests aren't affected.
        monkeypatch.delenv("K_SERVICE", raising=False)
        importlib.reload(tenant_mod)
