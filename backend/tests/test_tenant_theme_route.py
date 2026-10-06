"""
/api/tenant/theme — membership guard + endpoint wiring.

Tenant context can come from the client-controlled X-Tenant-ID header, so every
endpoint must re-check that the signed-in user is allowed on that tenant.
"""
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.dependencies import require_auth
from backend.api.routes import tenant_theme
from backend.models.tenant import TenantAuth, TenantConfig, TenantDefaults
from backend.services.auth_service import AuthResult, UserType
from backend.services.tenant_admin_service import TenantValidationError
from backend.services.theme_preview_service import PreviewImages, ThemePreviewError

app = FastAPI()
app.include_router(tenant_theme.router)

RANDY = TenantConfig(
    id="randy-vild",
    name="Randy Vild",
    subdomain="randy-vild.nomadkaraoke.com",
    defaults=TenantDefaults(theme_id="randy-vild", locked_theme="randy-vild"),
    auth=TenantAuth(allowed_emails=["randyvild@gmail.com"], require_email_domain=True),
)
INACTIVE = RANDY.model_copy(update={"is_active": False})

THEME = {"theme_id": "randy-vild", "style_params": {"intro": {}}, "images": ["bg.jpg"], "fonts": ["A.ttf"]}


def _auth(email, tenant_id=None):
    return AuthResult(
        is_valid=True, user_type=UserType.LIMITED, remaining_uses=0, message="ok",
        user_email=email, is_admin=email.endswith("@nomadkaraoke.com"), tenant_id=tenant_id,
    )


@pytest.fixture
def client_for():
    def make(email="randyvild@gmail.com", tenant=RANDY, token_tenant=None):
        app.dependency_overrides[require_auth] = lambda: _auth(email, token_tenant)
        patcher = patch.object(tenant_theme, "get_tenant_config_from_request", return_value=tenant)
        patcher.start()
        make.patchers.append(patcher)
        return TestClient(app)

    make.patchers = []
    yield make
    for p in make.patchers:
        p.stop()
    app.dependency_overrides.clear()


def test_member_can_get_theme(client_for):
    with patch.object(tenant_theme, "get_theme_for_editor", return_value=THEME):
        resp = client_for().get("/api/tenant/theme")
    assert resp.status_code == 200
    assert resp.json()["images"] == ["bg.jpg"]


def test_admin_can_edit_any_tenant(client_for):
    with patch.object(tenant_theme, "get_theme_for_editor", return_value=THEME):
        assert client_for(email="andrew@nomadkaraoke.com").get("/api/tenant/theme").status_code == 200


@pytest.mark.parametrize(
    "kwargs,status",
    [
        ({"email": "attacker@gmail.com"}, 403),  # spoofed X-Tenant-ID: not on allowlist
        ({"tenant": None}, 404),  # not a tenant portal
        ({"tenant": INACTIVE}, 404),  # deactivated tenant
        ({"token_tenant": "vocalstar"}, 403),  # token scoped to another tenant
    ],
)
def test_non_members_are_rejected_on_every_endpoint(client_for, kwargs, status):
    client = client_for(**kwargs)
    with patch.object(tenant_theme, "get_theme_for_editor") as get_theme, \
         patch.object(tenant_theme, "save_tenant_theme") as save, \
         patch.object(tenant_theme, "store_uploaded_asset") as upload, \
         patch.object(tenant_theme, "render_theme_preview") as render:
        assert client.get("/api/tenant/theme").status_code == status
        assert client.put("/api/tenant/theme", json={"style_params": {}}).status_code == status
        assert client.post("/api/tenant/theme/preview", json={"style_params": {}}).status_code == status
        assert client.post("/api/tenant/theme/assets", files={"file": ("a.png", b"x")}).status_code == status
    get_theme.assert_not_called(); save.assert_not_called(); upload.assert_not_called(); render.assert_not_called()


def test_preview_returns_data_urls(client_for):
    with patch.object(tenant_theme, "prepare_preview_styles", return_value={"intro": {}}), \
         patch.object(tenant_theme, "render_theme_preview", return_value=PreviewImages(b"t", b"k")) as render:
        resp = client_for().post(
            "/api/tenant/theme/preview",
            json={"style_params": {"intro": {}}, "sample": {"artist": "Randy Vild", "title": "What Goes Up"}},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title_card"].startswith("data:image/jpeg;base64,")
    assert render.call_args.kwargs["artist"] == "Randy Vild"


def test_preview_validation_and_render_errors(client_for):
    client = client_for()
    with patch.object(tenant_theme, "prepare_preview_styles", side_effect=TenantValidationError("bad path")):
        resp = client.post("/api/tenant/theme/preview", json={"style_params": {}})
        assert resp.status_code == 400 and "bad path" in resp.json()["detail"]
    with patch.object(tenant_theme, "prepare_preview_styles", return_value={}), \
         patch.object(tenant_theme, "render_theme_preview", side_effect=ThemePreviewError("font missing")):
        resp = client.post("/api/tenant/theme/preview", json={"style_params": {}})
        assert resp.status_code == 422


def test_save_and_upload(client_for):
    client = client_for()
    with patch.object(tenant_theme, "save_tenant_theme") as save, \
         patch.object(tenant_theme, "get_theme_for_editor", return_value=THEME), \
         patch.object(tenant_theme, "refresh_inflight_jobs", return_value={"updated": 0, "failed": 0}), \
         patch.object(tenant_theme, "outdated_jobs", return_value={"job_ids": []}):
        resp = client.put("/api/tenant/theme", json={"style_params": {"intro": {"title_color": "#ffffff"}}})
    assert resp.status_code == 200
    assert save.call_args.args[0].id == "randy-vild"
    with patch.object(tenant_theme, "save_tenant_theme", side_effect=TenantValidationError("nope")):
        assert client.put("/api/tenant/theme", json={"style_params": {}}).status_code == 400

    with patch.object(tenant_theme, "store_uploaded_asset", return_value="bg-1234abcd.png") as store:
        resp = client.post("/api/tenant/theme/assets", files={"file": ("bg.png", b"png-bytes")})
    assert resp.status_code == 200 and resp.json()["name"] == "bg-1234abcd.png"
    assert store.call_args.args[1] == "bg.png"
    assert client.post("/api/tenant/theme/assets", files={"file": ("e.png", b"")}).status_code == 400


def test_error_mapping_and_sample_limits(client_for):
    from backend.services.tenant_theme_service import ThemeNotEditableError, ThemeNotFoundError

    client = client_for()
    with patch.object(tenant_theme, "get_theme_for_editor", side_effect=ThemeNotFoundError("gone")):
        assert client.get("/api/tenant/theme").status_code == 404
    with patch.object(tenant_theme, "get_theme_for_editor", side_effect=ThemeNotEditableError("shared")):
        assert client.get("/api/tenant/theme").status_code == 403
    resp = client.post(
        "/api/tenant/theme/preview",
        json={"style_params": {}, "sample": {"lyrics": ["x" * 121]}},
    )
    assert resp.status_code == 422  # per-line length capped


def test_oversized_upload_rejected_without_calling_store(client_for, monkeypatch):
    monkeypatch.setattr(tenant_theme, "MAX_UPLOAD_BYTES", 10)
    with patch.object(tenant_theme, "store_uploaded_asset") as store:
        resp = client_for().post("/api/tenant/theme/assets", files={"file": ("big.png", b"x" * 11)})
    assert resp.status_code == 400 and "too large" in resp.json()["detail"]
    store.assert_not_called()


def test_timed_out_render_keeps_its_slot_until_it_finishes(client_for, monkeypatch):
    """A 504'd render still occupies a worker thread, so its semaphore slot must
    only be released once it really completes."""
    import threading
    import time

    monkeypatch.setattr(tenant_theme, "PREVIEW_TIMEOUT_S", 0.05)
    finished = threading.Event()

    def slow_render(*a, **k):
        time.sleep(0.4)
        finished.set()
        return PreviewImages(b"t", b"k")

    free_before = tenant_theme._PREVIEW_SEMAPHORE._value
    # `with` keeps TestClient's event loop alive across the request, like uvicorn's.
    with patch.object(tenant_theme, "prepare_preview_styles", return_value={}), \
         patch.object(tenant_theme, "render_theme_preview", side_effect=slow_render), \
         client_for() as client:
        resp = client.post("/api/tenant/theme/preview", json={"style_params": {}})
        assert resp.status_code == 504
        assert tenant_theme._PREVIEW_SEMAPHORE._value == free_before - 1  # still held
        assert finished.wait(2)
        for _ in range(50):
            if tenant_theme._PREVIEW_SEMAPHORE._value == free_before:
                break
            time.sleep(0.02)
    assert tenant_theme._PREVIEW_SEMAPHORE._value == free_before
