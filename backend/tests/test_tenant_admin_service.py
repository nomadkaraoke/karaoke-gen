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
from backend.services.tenant_domain_service import (
    DomainStatus,
    TenantDomainConflictError,
    TenantDomainError,
)
from backend.services.tenant_service import TenantService


class FakeStorage:
    """Minimal in-memory stand-in for StorageService."""

    def __init__(self):
        self.blobs: dict[str, bytes] = {}
        self.cache_control: dict[str, str | None] = {}

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

    def upload_fileobj(self, fileobj, path, content_type=None, cache_control=None):
        self.blobs[path] = fileobj.read()
        self.cache_control[path] = cache_control
        return path

    def delete_folder(self, prefix):
        doomed = [p for p in self.blobs if p.startswith(prefix)]
        for p in doomed:
            del self.blobs[p]
        return len(doomed)

    def delete_file(self, path, ignore_missing=False):
        existed = path in self.blobs
        self.blobs.pop(path, None)
        if not existed and not ignore_missing:
            raise FileNotFoundError(path)
        return existed

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


class FakeDomains:
    """Stand-in for TenantDomainService recording Cloudflare calls."""

    def __init__(self):
        self.provisioned = []
        self.deprovisioned = []
        self.checked = []
        self.fail_check = None
        self.fail_provision = None
        self.fail_deprovision = None

    def check_available(self, hostname):
        self.checked.append(hostname)
        if self.fail_check:
            raise self.fail_check

    def provision(self, hostname):
        if self.fail_provision:
            raise self.fail_provision
        self.provisioned.append(hostname)
        return DomainStatus(hostname=hostname, dns_ok=True, pages_status="initializing")

    def deprovision(self, hostname):
        if self.fail_deprovision:
            raise self.fail_deprovision
        self.deprovisioned.append(hostname)

    def status(self, hostname):
        return DomainStatus(hostname=hostname, dns_ok=hostname in self.provisioned, pages_status="active")


@pytest.fixture
def fake_domains(monkeypatch):
    d = FakeDomains()
    monkeypatch.setattr(tas, "get_tenant_domain_service", lambda: d)
    return d


@pytest.fixture(autouse=True)
def patch_singletons(fake_storage, fake_domains, monkeypatch):
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
    # Uploaded theme assets may later be overwritten in place — never edge-cache them
    assert fake_storage.cache_control["themes/randy-vild/assets/karaoke_background.png"] == "no-store"
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


def test_conflict_does_not_clobber_existing_theme(fake_storage):
    """A create for an existing id must fail before touching the live theme."""
    tas.create_tenant(name="Existing", colors=ColorOverrides(title_color="#abcabc"), storage=fake_storage)
    theme_before = fake_storage.blobs["themes/existing/style_params.json"]

    with pytest.raises(tas.TenantConflictError):
        tas.create_tenant(
            name="Existing",
            tenant_id="existing",
            style_params_override={"intro": {"title_color": "#000000"}},
            storage=fake_storage,
        )
    # Live theme untouched
    assert fake_storage.blobs["themes/existing/style_params.json"] == theme_before


def test_id_colliding_with_existing_theme_rejected(fake_storage):
    """A tenant id that matches an existing theme (e.g. the default) must not clobber it."""
    theme_before = fake_storage.blobs["themes/nomad/style_params.json"]
    with pytest.raises(tas.TenantConflictError):
        tas.create_tenant(
            name="Nomad",
            tenant_id="nomad",
            style_params_override={"intro": {"title_color": "#000000"}},
            storage=fake_storage,
        )
    assert fake_storage.blobs["themes/nomad/style_params.json"] == theme_before
    assert "tenants/nomad/config.json" not in fake_storage.blobs


def test_create_rolls_back_reservation_on_theme_failure(fake_storage, monkeypatch):
    """If theme provisioning fails after reservation, the config is rolled back."""
    def boom(*args, **kwargs):
        raise RuntimeError("theme boom")

    monkeypatch.setattr(tas, "_register_theme_metadata", boom)
    with pytest.raises(RuntimeError):
        tas.create_tenant(name="Boomer", storage=fake_storage)
    assert "tenants/boomer/config.json" not in fake_storage.blobs


def test_list_tenants_skips_config_without_id(fake_storage):
    fake_storage.blobs["tenants/broken/config.json"] = json.dumps({"name": "No Id"}).encode()
    tas.create_tenant(name="Good", storage=fake_storage)
    listed = tas.list_tenants(storage=fake_storage)
    ids = [t["id"] for t in listed]
    assert "good" in ids
    assert all(t["id"] for t in listed)  # no null-id rows


def test_missing_default_theme_errors(fake_storage):
    # Remove the default flag
    fake_storage.blobs["themes/_metadata.json"] = json.dumps({"version": 1, "themes": []}).encode()
    with pytest.raises(ValueError):
        tas.create_tenant(name="No Theme", storage=fake_storage)


def test_create_with_full_style_params_override(fake_storage):
    override = {
        "intro": {"title_color": "#abcdef", "background_image": "custom.png"},
        "karaoke": {"primary_color": "9,9,9,255"},
    }
    tas.create_tenant(name="Override Co", style_params_override=override, storage=fake_storage)
    style = json.loads(fake_storage.blobs["themes/override-co/style_params.json"].decode())
    assert style["intro"]["title_color"] == "#abcdef"
    assert style["karaoke"]["primary_color"] == "9,9,9,255"
    # default assets still copied so inherited basenames resolve
    assert "themes/override-co/assets/Oswald-SemiBold.ttf" in fake_storage.blobs


def test_create_with_invalid_style_params_override_rejected(fake_storage):
    with pytest.raises(tas.TenantValidationError):
        tas.create_tenant(name="Bad", style_params_override={"bogus": {}}, storage=fake_storage)


def test_get_tenant_detail(fake_storage):
    tas.create_tenant(name="Randy Vild", colors=ColorOverrides(title_color="#ffdf6b"), storage=fake_storage)
    detail = tas.get_tenant_detail("randy-vild", storage=fake_storage)
    assert detail["tenant"]["id"] == "randy-vild"
    assert detail["theme_id"] == "randy-vild"
    assert detail["style_params"]["intro"]["title_color"] == "#ffdf6b"
    assert "Oswald-SemiBold.ttf" in detail["assets"]


def test_get_tenant_detail_missing_raises(fake_storage):
    with pytest.raises(tas.TenantNotFoundError):
        tas.get_tenant_detail("nope", storage=fake_storage)


def test_update_tenant_full_theme_and_config(fake_storage):
    tas.create_tenant(name="Randy Vild", storage=fake_storage)

    new_style = {
        "intro": {"title_color": "#123456", "background_image": "intro_bg.png"},
        "karaoke": {"primary_color": "5,5,5,255", "background_image": "kbg.jpg"},
        "end": {},
        "cdg": {},
    }
    updated = tas.update_tenant(
        "randy-vild",
        config_updates={"name": "Randy Vild Deluxe", "defaults": {"brand_prefix": "RVD"}},
        style_params=new_style,
        assets={"kbg.jpg": (b"replacement", "jpg")},
        storage=fake_storage,
    )
    assert updated.name == "Randy Vild Deluxe"
    assert updated.defaults.brand_prefix == "RVD"
    assert updated.id == "randy-vild"  # id immutable
    # theme replaced
    style = json.loads(fake_storage.blobs["themes/randy-vild/style_params.json"].decode())
    assert style["intro"]["title_color"] == "#123456"
    # asset replaced
    assert fake_storage.blobs["themes/randy-vild/assets/kbg.jpg"] == b"replacement"
    # Overwritten in place, so it must be written no-store to be visible immediately
    assert fake_storage.cache_control["themes/randy-vild/assets/kbg.jpg"] == "no-store"


def test_update_tenant_rejects_bad_style_params(fake_storage):
    tas.create_tenant(name="Randy Vild", storage=fake_storage)
    with pytest.raises(tas.TenantValidationError):
        tas.update_tenant("randy-vild", style_params={"nope": {}}, storage=fake_storage)


def test_update_tenant_missing_raises(fake_storage):
    with pytest.raises(tas.TenantNotFoundError):
        tas.update_tenant("ghost", config_updates={"name": "x"}, storage=fake_storage)


def test_get_default_style_params(fake_storage):
    params = tas.get_default_style_params(storage=fake_storage)
    assert set(params.keys()) == {"intro", "karaoke", "end", "cdg"}


def test_list_tenants(fake_storage):
    tas.create_tenant(name="Bravo", storage=fake_storage)
    tas.create_tenant(name="Alpha", storage=fake_storage)
    listed = tas.list_tenants(storage=fake_storage)
    names = [t["name"] for t in listed]
    assert names == ["Alpha", "Bravo"]  # sorted by name
    assert all("id" in t and "subdomain" in t for t in listed)


# --- Subdomain provisioning (Cloudflare) + delete + access lists -------------


def test_create_provisions_subdomain(fake_storage, fake_domains):
    tas.create_tenant(name="Randy Vild", storage=fake_storage)
    assert fake_domains.checked == ["randy-vild.nomadkaraoke.com"]
    assert fake_domains.provisioned == ["randy-vild.nomadkaraoke.com"]


def test_create_rejects_subdomain_not_matching_id(fake_storage, fake_domains):
    with pytest.raises(tas.TenantValidationError, match="randy-vild.nomadkaraoke.com"):
        tas.create_tenant(name="Randy Vild", subdomain="other.nomadkaraoke.com", storage=fake_storage)
    assert "tenants/randy-vild/config.json" not in fake_storage.blobs


def test_create_dns_conflict_fails_before_any_write(fake_storage, fake_domains):
    fake_domains.fail_check = TenantDomainConflictError("decide.nomadkaraoke.com already has DNS records")
    before = dict(fake_storage.blobs)
    with pytest.raises(tas.TenantConflictError):
        tas.create_tenant(name="Decide", storage=fake_storage)
    assert fake_storage.blobs == before
    assert fake_domains.provisioned == []


def test_create_cloudflare_unusable_fails_before_any_write(fake_storage, fake_domains):
    fake_domains.fail_check = TenantDomainError("token not configured")
    before = dict(fake_storage.blobs)
    with pytest.raises(tas.TenantProvisioningError):
        tas.create_tenant(name="Randy Vild", storage=fake_storage)
    assert fake_storage.blobs == before


def test_create_rolls_back_everything_when_provisioning_fails(fake_storage, fake_domains):
    fake_domains.fail_provision = TenantDomainError("cloudflare 500")
    before = dict(fake_storage.blobs)
    with pytest.raises(tas.TenantProvisioningError):
        tas.create_tenant(name="Randy Vild", storage=fake_storage)
    # No config, no theme files, registry back to the original themes
    assert not any(p.startswith("tenants/randy-vild/") for p in fake_storage.blobs)
    assert not any(p.startswith("themes/randy-vild/") for p in fake_storage.blobs)
    registry = json.loads(fake_storage.blobs["themes/_metadata.json"].decode())
    assert [t["id"] for t in registry["themes"]] == ["nomad"]
    assert set(fake_storage.blobs) == set(before)
    # A half-provisioned Pages domain is cleaned up too
    assert fake_domains.deprovisioned == ["randy-vild.nomadkaraoke.com"]


def test_delete_tenant_removes_domain_theme_and_config(fake_storage, fake_domains):
    tas.create_tenant(name="Randy Vild", logo=(b"logo", "png"), storage=fake_storage)
    tas.delete_tenant("randy-vild", storage=fake_storage)

    assert fake_domains.deprovisioned == ["randy-vild.nomadkaraoke.com"]
    assert not any(p.startswith("tenants/randy-vild/") for p in fake_storage.blobs)
    assert not any(p.startswith("themes/randy-vild/") for p in fake_storage.blobs)
    registry = json.loads(fake_storage.blobs["themes/_metadata.json"].decode())
    assert [t["id"] for t in registry["themes"]] == ["nomad"]
    # Default theme untouched
    assert "themes/nomad/style_params.json" in fake_storage.blobs
    # Id is reusable afterwards
    tas.create_tenant(name="Randy Vild", storage=fake_storage)


def test_delete_tenant_missing_raises(fake_storage):
    with pytest.raises(tas.TenantNotFoundError):
        tas.delete_tenant("nope", storage=fake_storage)


def test_delete_aborts_without_touching_gcs_if_domain_removal_fails(fake_storage, fake_domains):
    tas.create_tenant(name="Randy Vild", storage=fake_storage)
    fake_domains.fail_deprovision = TenantDomainError("cloudflare down")
    with pytest.raises(tas.TenantProvisioningError):
        tas.delete_tenant("randy-vild", storage=fake_storage)
    assert "tenants/randy-vild/config.json" in fake_storage.blobs
    assert "themes/randy-vild/style_params.json" in fake_storage.blobs


def test_delete_keeps_theme_shared_with_another_tenant(fake_storage):
    tas.create_tenant(name="Randy Vild", storage=fake_storage)
    tas.create_tenant(name="Randy Two", tenant_id="randy-two", storage=fake_storage)
    tas.update_tenant(
        "randy-two", config_updates={"defaults": {"locked_theme": "randy-vild"}}, storage=fake_storage
    )
    tas.delete_tenant("randy-vild", storage=fake_storage)
    assert "tenants/randy-vild/config.json" not in fake_storage.blobs
    assert "themes/randy-vild/style_params.json" in fake_storage.blobs


def test_provision_tenant_domain_backfill(fake_storage, fake_domains):
    tas.create_tenant(name="Randy Vild", storage=fake_storage)
    fake_domains.provisioned.clear()
    status = tas.provision_tenant_domain("randy-vild")
    assert status["hostname"] == "randy-vild.nomadkaraoke.com"
    assert fake_domains.provisioned == ["randy-vild.nomadkaraoke.com"]


def test_provision_tenant_domain_missing_tenant(fake_storage):
    with pytest.raises(tas.TenantNotFoundError):
        tas.provision_tenant_domain("nope")


def test_get_tenant_domain_status_swallows_cloudflare_errors(fake_domains, monkeypatch):
    def boom(hostname):
        raise TenantDomainError("down")

    monkeypatch.setattr(fake_domains, "status", boom)
    assert tas.get_tenant_domain_status("randy-vild") is None


def test_create_with_allowed_emails_restricts_access(fake_storage):
    config = tas.create_tenant(
        name="Randy Vild", allowed_emails=[" Randy@Gmail.com ", "randy@gmail.com"], storage=fake_storage
    )
    assert config.auth.allowed_emails == ["randy@gmail.com"]
    assert config.auth.require_email_domain is True
    assert config.is_email_allowed("RANDY@gmail.com")
    assert not config.is_email_allowed("someone@gmail.com")
    assert config.is_email_allowed("andrew@nomadkaraoke.com")  # admins always


def test_create_rejects_invalid_email(fake_storage):
    with pytest.raises(tas.TenantValidationError, match="not-an-email"):
        tas.create_tenant(name="Randy Vild", allowed_emails=["not-an-email"], storage=fake_storage)


def test_update_access_lists_normalized_and_enforced(fake_storage):
    tas.create_tenant(name="Randy Vild", storage=fake_storage)  # open portal
    updated = tas.update_tenant(
        "randy-vild",
        config_updates={"auth": {"allowed_emails": ["Randy@Gmail.com"], "allowed_email_domains": ["@Label.com"]}},
        storage=fake_storage,
    )
    assert updated.auth.allowed_emails == ["randy@gmail.com"]
    assert updated.auth.allowed_email_domains == ["label.com"]
    assert updated.auth.require_email_domain is True
    assert updated.is_email_allowed("exec@label.com")
    assert not updated.is_email_allowed("random@gmail.com")

    reopened = tas.update_tenant(
        "randy-vild", config_updates={"auth": {"allowed_emails": [], "allowed_email_domains": []}},
        storage=fake_storage,
    )
    assert reopened.auth.require_email_domain is False
    # Empty allowlist = admins only (never an open portal)
    assert not reopened.is_email_allowed("random@gmail.com")
    assert reopened.is_email_allowed("andrew@nomadkaraoke.com")


def test_update_rejects_invalid_domain(fake_storage):
    tas.create_tenant(name="Randy Vild", storage=fake_storage)
    with pytest.raises(tas.TenantValidationError):
        tas.update_tenant(
            "randy-vild", config_updates={"auth": {"allowed_email_domains": ["not a domain"]}},
            storage=fake_storage,
        )


def test_update_rejects_subdomain_change(fake_storage):
    tas.create_tenant(name="Randy Vild", storage=fake_storage)
    with pytest.raises(tas.TenantValidationError, match="can't be changed"):
        tas.update_tenant("randy-vild", config_updates={"subdomain": "other.nomadkaraoke.com"}, storage=fake_storage)
    # Same value is fine (the UI may echo it back)
    tas.update_tenant("randy-vild", config_updates={"subdomain": "randy-vild.nomadkaraoke.com"}, storage=fake_storage)


def test_delete_always_deprovisions_canonical_host(fake_storage, fake_domains):
    tas.create_tenant(name="Randy Vild", storage=fake_storage)
    cfg_path = "tenants/randy-vild/config.json"
    cfg = json.loads(fake_storage.blobs[cfg_path].decode())
    cfg["subdomain"] = "legacy.example.com"  # legacy/odd config
    fake_storage.blobs[cfg_path] = json.dumps(cfg).encode()
    tas.delete_tenant("randy-vild", storage=fake_storage)
    assert fake_domains.deprovisioned == ["randy-vild.nomadkaraoke.com"]


def test_delete_keeps_theme_not_owned_by_tenant(fake_storage):
    """A tenant pointing at some other theme (not its 1:1 theme) never deletes it."""
    tas.create_tenant(name="Randy Vild", storage=fake_storage)
    fake_storage.blobs["themes/foo/style_params.json"] = b"{}"
    tas.update_tenant(
        "randy-vild", config_updates={"defaults": {"locked_theme": "foo", "theme_id": "foo"}}, storage=fake_storage
    )
    tas.delete_tenant("randy-vild", storage=fake_storage)
    assert "themes/foo/style_params.json" in fake_storage.blobs


def test_update_does_not_persist_subdomain_echo(fake_storage):
    tas.create_tenant(name="Randy Vild", storage=fake_storage)
    updated = tas.update_tenant(
        "randy-vild", config_updates={"subdomain": "  Randy-Vild.nomadkaraoke.com "}, storage=fake_storage
    )
    assert updated.subdomain == "randy-vild.nomadkaraoke.com"


def test_delete_removes_own_theme_even_after_switching_themes(fake_storage):
    tas.create_tenant(name="Randy Vild", storage=fake_storage)
    fake_storage.blobs["themes/foo/style_params.json"] = b"{}"
    tas.update_tenant("randy-vild", config_updates={"defaults": {"locked_theme": "foo"}}, storage=fake_storage)
    tas.delete_tenant("randy-vild", storage=fake_storage)
    assert not any(p.startswith("themes/randy-vild/") for p in fake_storage.blobs)
    assert "themes/foo/style_params.json" in fake_storage.blobs
    # id reusable
    tas.create_tenant(name="Randy Vild", storage=fake_storage)


def test_delete_reports_storage_failure(fake_storage, monkeypatch):
    tas.create_tenant(name="Randy Vild", storage=fake_storage)
    monkeypatch.setattr(fake_storage, "delete_folder", lambda prefix: 0)  # GCS failure swallowed
    with pytest.raises(tas.TenantProvisioningError, match="retry"):
        tas.delete_tenant("randy-vild", storage=fake_storage)
