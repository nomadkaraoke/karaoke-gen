"""
Unit tests for tenant_admin_service.create_tenant / list_tenants.

Uses an in-memory fake StorageService so no GCS access is needed. Verifies:
- slug derivation, reserved-id and duplicate rejection
- the derived theme copies the default theme's assets (self-contained),
  applies colour overrides, and points backgrounds at bare basenames
- the tenant config gets B2B defaults and registers in the theme registry
"""
import io
import json

import pytest
from google.api_core.exceptions import PreconditionFailed

from backend.models.theme import ColorOverrides, hex_to_rgba
from backend.services import tenant_admin_service as tas
from backend.services.theme_service import ThemeService
from backend.services.tenant_service import TenantService


class FakeStorage:
    """Minimal in-memory stand-in for StorageService."""

    def __init__(self):
        self.blobs: dict[str, bytes] = {}

    def upload_json(self, path, data, if_generation_match=None):
        if if_generation_match == 0 and path in self.blobs:
            raise PreconditionFailed("exists")
        self.blobs[path] = json.dumps(data).encode()
        return path

    def download_json(self, path):
        if path not in self.blobs:
            raise FileNotFoundError(path)
        return json.loads(self.blobs[path].decode())

    def file_exists(self, path):
        return path in self.blobs

    def list_files(self, prefix):
        return [p for p in self.blobs if p.startswith(prefix)]

    def copy_blob(self, src, dst):
        self.blobs[dst] = self.blobs.get(src, b"")
        return dst

    def upload_fileobj(self, fileobj, path, content_type=None):
        self.blobs[path] = fileobj.read()
        return path

    def generate_signed_url(self, path, expiration_minutes=60):
        return f"https://signed/{path}"


DEFAULT_STYLE = {
    "intro": {"artist_color": "#111111", "title_color": "#222222", "background_image": "intro_bg.png"},
    "karaoke": {"primary_color": "1,1,1,255", "secondary_color": "2,2,2,255", "background_image": "kbg.jpg"},
    "end": {"artist_color": "#111111", "title_color": "#222222"},
    "cdg": {"active_fill": "#111111", "inactive_fill": "#222222"},
}


@pytest.fixture
def fake_storage():
    s = FakeStorage()
    s.blobs["themes/_metadata.json"] = json.dumps(
        {"version": 1, "themes": [{"id": "nomad", "name": "Nomad", "description": "d", "is_default": True}]}
    ).encode()
    s.blobs["themes/nomad/style_params.json"] = json.dumps(DEFAULT_STYLE).encode()
    s.blobs["themes/nomad/assets/intro_bg.png"] = b"introbg"
    s.blobs["themes/nomad/assets/kbg.jpg"] = b"karaokebg"
    s.blobs["themes/nomad/assets/Oswald-SemiBold.ttf"] = b"font"
    return s


@pytest.fixture(autouse=True)
def patch_singletons(fake_storage, monkeypatch):
    """Point the service's tenant/theme singletons at the fake storage."""
    monkeypatch.setattr(tas, "get_tenant_service", lambda: TenantService(storage=fake_storage))
    monkeypatch.setattr(tas, "get_theme_service", lambda: ThemeService(storage=fake_storage))
    # Ensure logo gs:// url is deterministic
    monkeypatch.setattr(tas.settings, "gcs_bucket_name", "test-bucket", raising=False)
    return fake_storage


def test_slugify_tenant_id():
    assert tas.slugify_tenant_id("Randy Vild") == "randy-vild"
    assert tas.slugify_tenant_id("  Café  Del  Mar! ") == "caf-del-mar"


def test_create_tenant_happy_path(fake_storage):
    colors = ColorOverrides(sung_lyrics_color="#7070f7", title_color="#ffdf6b")
    config = tas.create_tenant(
        name="Randy Vild",
        colors=colors,
        dropbox_path="/Karaoke/Tracks-RandyVild",
        brand_prefix="RVILD",
        backgrounds={"karaoke_background": (b"newbg", "png")},
        storage=fake_storage,
    )

    assert config.id == "randy-vild"
    assert config.subdomain == "randy-vild.nomadkaraoke.com"
    assert config.defaults.locked_theme == "randy-vild"
    assert config.defaults.theme_id == "randy-vild"
    assert config.defaults.brand_prefix == "RVILD"

    # B2B defaults
    assert config.features.audio_search is False
    assert config.features.youtube_upload is False
    assert config.features.gdrive_upload is False
    assert config.features.bulk_upload is True
    assert config.features.dropbox_upload is True  # dropbox_path set
    assert config.features.theme_selection is False

    # Config persisted
    assert "tenants/randy-vild/config.json" in fake_storage.blobs

    # Theme derived + self-contained (default assets copied in)
    assert "themes/randy-vild/assets/Oswald-SemiBold.ttf" in fake_storage.blobs
    assert "themes/randy-vild/assets/intro_bg.png" in fake_storage.blobs
    # Admin-provided background uploaded + referenced by BARE basename
    assert fake_storage.blobs["themes/randy-vild/assets/karaoke_background.png"] == b"newbg"
    style = json.loads(fake_storage.blobs["themes/randy-vild/style_params.json"].decode())
    assert style["karaoke"]["background_image"] == "karaoke_background.png"

    # Colour overrides applied
    assert style["karaoke"]["primary_color"] == hex_to_rgba("#7070f7")
    assert style["intro"]["title_color"] == "#ffdf6b"

    # Registered in theme registry
    registry = json.loads(fake_storage.blobs["themes/_metadata.json"].decode())
    assert any(t["id"] == "randy-vild" for t in registry["themes"])


def test_create_tenant_download_only_when_no_dropbox(fake_storage):
    config = tas.create_tenant(name="Solo Client", storage=fake_storage)
    assert config.features.dropbox_upload is False
    assert config.defaults.dropbox_path is None


def test_allowed_domains_gate_email_restriction(fake_storage):
    config = tas.create_tenant(
        name="Domain Client",
        allowed_email_domains=["client.com", "Client.com", " label.com "],
        storage=fake_storage,
    )
    assert config.auth.allowed_email_domains == ["client.com", "label.com"]
    assert config.auth.require_email_domain is True

    open_config = tas.create_tenant(name="Open Client", storage=fake_storage)
    assert open_config.auth.allowed_email_domains == []
    assert open_config.auth.require_email_domain is False


def test_reserved_id_rejected(fake_storage):
    with pytest.raises(tas.TenantValidationError):
        tas.create_tenant(name="Admin", tenant_id="admin", storage=fake_storage)


def test_short_id_rejected(fake_storage):
    with pytest.raises(tas.TenantValidationError):
        tas.create_tenant(name="X", tenant_id="x", storage=fake_storage)


def test_duplicate_tenant_rejected(fake_storage):
    fake_storage.blobs["tenants/dup/config.json"] = json.dumps({"id": "dup"}).encode()
    with pytest.raises(tas.TenantConflictError):
        tas.create_tenant(name="Dup", tenant_id="dup", storage=fake_storage)


def test_missing_default_theme_errors(fake_storage):
    # Remove the default flag
    fake_storage.blobs["themes/_metadata.json"] = json.dumps({"version": 1, "themes": []}).encode()
    with pytest.raises(ValueError):
        tas.create_tenant(name="No Theme", storage=fake_storage)


def test_list_tenants(fake_storage):
    tas.create_tenant(name="Bravo", storage=fake_storage)
    tas.create_tenant(name="Alpha", storage=fake_storage)
    listed = tas.list_tenants(storage=fake_storage)
    names = [t["name"] for t in listed]
    assert names == ["Alpha", "Bravo"]  # sorted by name
    assert all("id" in t and "subdomain" in t for t in listed)
