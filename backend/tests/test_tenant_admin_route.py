"""
Unit tests for the admin tenant-provisioning routes.

Tests GET/POST /api/admin/tenants. The heavy lifting (GCS writes) lives in
tenant_admin_service and is tested separately; here we patch the service to
verify request parsing, validation mapping, and response shaping.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import tenant_admin
from backend.api.routes.tenant_admin import router
from backend.api.dependencies import require_admin
from backend.services.auth_service import AuthResult, UserType
from backend.services.tenant_admin_service import (
    TenantConflictError,
    TenantNotFoundError,
    TenantValidationError,
)
from backend.models.tenant import TenantConfig, TenantDefaults, TenantFeatures


app = FastAPI()
app.include_router(router)


def get_mock_admin():
    return AuthResult(
        is_valid=True,
        user_type=UserType.ADMIN,
        remaining_uses=-1,
        message="ok",
        user_email="admin@nomadkaraoke.com",
        is_admin=True,
    )


@pytest.fixture
def client():
    app.dependency_overrides[require_admin] = get_mock_admin
    yield TestClient(app)
    app.dependency_overrides.clear()


def _sample_config(tenant_id="randy-vild"):
    return TenantConfig(
        id=tenant_id,
        name="Randy Vild",
        subdomain=f"{tenant_id}.nomadkaraoke.com",
        features=TenantFeatures(dropbox_upload=True),
        defaults=TenantDefaults(theme_id=tenant_id, locked_theme=tenant_id),
    )


def test_create_tenant_happy_path(client, monkeypatch):
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _sample_config()

    monkeypatch.setattr(tenant_admin, "create_tenant", fake_create)

    resp = client.post(
        "/api/admin/tenants",
        data={
            "name": "Randy Vild",
            "sung_lyrics_color": "#7070f7",
            "dropbox_path": "/Karaoke/Tracks-RandyVild",
            "allowed_email_domains": "client.com, label.com",
        },
        files={"karaoke_background": ("bg.png", b"imgbytes", "image/png")},
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["tenant"]["id"] == "randy-vild"
    assert body["preview_url"] == "https://gen.nomadkaraoke.com/en/app?preview_tenant=randy-vild"
    assert body["subdomain_url"] == "https://randy-vild.nomadkaraoke.com"

    # Service received parsed inputs
    assert captured["name"] == "Randy Vild"
    assert captured["colors"].sung_lyrics_color == "#7070f7"
    assert captured["allowed_email_domains"] == ["client.com", "label.com"]
    assert "karaoke_background" in captured["backgrounds"]
    data, ext = captured["backgrounds"]["karaoke_background"]
    assert data == b"imgbytes" and ext == "png"


def test_create_tenant_conflict_maps_to_409(client, monkeypatch):
    def fake_create(**kwargs):
        raise TenantConflictError("Tenant 'dup' already exists.")

    monkeypatch.setattr(tenant_admin, "create_tenant", fake_create)
    resp = client.post("/api/admin/tenants", data={"name": "Dup", "tenant_id": "dup"})
    assert resp.status_code == 409


def test_create_tenant_reserved_maps_to_400(client, monkeypatch):
    def fake_create(**kwargs):
        raise TenantValidationError("'admin' is a reserved subdomain and cannot be used.")

    monkeypatch.setattr(tenant_admin, "create_tenant", fake_create)
    resp = client.post("/api/admin/tenants", data={"name": "Admin", "tenant_id": "admin"})
    assert resp.status_code == 400


def test_create_tenant_bad_color_maps_to_400(client, monkeypatch):
    monkeypatch.setattr(tenant_admin, "create_tenant", lambda **k: _sample_config())
    resp = client.post("/api/admin/tenants", data={"name": "X", "artist_color": "not-a-hex"})
    assert resp.status_code == 400


def test_create_tenant_rejects_bad_image_type(client, monkeypatch):
    monkeypatch.setattr(tenant_admin, "create_tenant", lambda **k: _sample_config())
    resp = client.post(
        "/api/admin/tenants",
        data={"name": "X"},
        files={"logo": ("evil.svg", b"<svg/>", "image/svg+xml")},
    )
    assert resp.status_code == 400


def test_create_tenant_rejects_bad_distribution_mode(client, monkeypatch):
    monkeypatch.setattr(tenant_admin, "create_tenant", lambda **k: _sample_config())
    resp = client.post("/api/admin/tenants", data={"name": "X", "distribution_mode": "wat"})
    assert resp.status_code == 400


def test_create_tenant_with_full_style_params(client, monkeypatch):
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _sample_config()

    monkeypatch.setattr(tenant_admin, "create_tenant", fake_create)
    resp = client.post(
        "/api/admin/tenants",
        data={"name": "Randy Vild", "style_params": '{"intro": {"title_color": "#abcdef"}}'},
    )
    assert resp.status_code == 201, resp.text
    assert captured["style_params_override"] == {"intro": {"title_color": "#abcdef"}}


def test_create_tenant_bad_style_params_json(client, monkeypatch):
    monkeypatch.setattr(tenant_admin, "create_tenant", lambda **k: _sample_config())
    resp = client.post("/api/admin/tenants", data={"name": "X", "style_params": "{not json"})
    assert resp.status_code == 400


def test_get_tenant_detail(client, monkeypatch):
    monkeypatch.setattr(
        tenant_admin,
        "get_tenant_detail",
        lambda tid: {
            "tenant": {"id": tid, "name": "Randy Vild"},
            "theme_id": tid,
            "style_params": {"intro": {"title_color": "#fff"}},
            "assets": ["intro_bg.png"],
        },
    )
    resp = client.get("/api/admin/tenants/randy-vild")
    assert resp.status_code == 200
    body = resp.json()
    assert body["style_params"]["intro"]["title_color"] == "#fff"
    assert body["preview_url"].endswith("?preview_tenant=randy-vild")


def test_get_tenant_detail_404(client, monkeypatch):
    def raise_nf(tid):
        raise TenantNotFoundError("nope")

    monkeypatch.setattr(tenant_admin, "get_tenant_detail", raise_nf)
    resp = client.get("/api/admin/tenants/ghost")
    assert resp.status_code == 404


def test_theme_template(client, monkeypatch):
    monkeypatch.setattr(
        tenant_admin, "get_default_style_params", lambda: {"intro": {}, "karaoke": {}}
    )
    resp = client.get("/api/admin/tenants/_template")
    assert resp.status_code == 200
    assert "intro" in resp.json()["style_params"]


def test_update_tenant(client, monkeypatch):
    captured = {}

    def fake_update(tid, **kwargs):
        captured["tid"] = tid
        captured.update(kwargs)
        return _sample_config(tid)

    monkeypatch.setattr(tenant_admin, "update_tenant", fake_update)
    resp = client.put(
        "/api/admin/tenants/randy-vild",
        data={
            "config": '{"name": "Deluxe"}',
            "style_params": '{"intro": {"title_color": "#123456"}}',
        },
        files={"assets": ("kbg.jpg", b"img", "image/jpeg")},
    )
    assert resp.status_code == 200, resp.text
    assert captured["tid"] == "randy-vild"
    assert captured["config_updates"] == {"name": "Deluxe"}
    assert captured["style_params"] == {"intro": {"title_color": "#123456"}}
    assert "kbg.jpg" in captured["assets"]


def test_update_tenant_404(client, monkeypatch):
    def raise_nf(tid, **kwargs):
        raise TenantNotFoundError("nope")

    monkeypatch.setattr(tenant_admin, "update_tenant", raise_nf)
    resp = client.put("/api/admin/tenants/ghost", data={"config": "{}"})
    assert resp.status_code == 404


def test_update_tenant_bad_style_params(client, monkeypatch):
    def raise_val(tid, **kwargs):
        raise TenantValidationError("bad")

    monkeypatch.setattr(tenant_admin, "update_tenant", raise_val)
    resp = client.put("/api/admin/tenants/randy-vild", data={"style_params": '{"nope": {}}'})
    assert resp.status_code == 400


def test_list_tenants(client, monkeypatch):
    monkeypatch.setattr(
        tenant_admin,
        "list_tenants",
        lambda: [
            {"id": "a", "name": "Alpha", "subdomain": "a.nomadkaraoke.com", "is_active": True},
        ],
    )
    resp = client.get("/api/admin/tenants")
    assert resp.status_code == 200
    assert resp.json()["tenants"][0]["id"] == "a"
