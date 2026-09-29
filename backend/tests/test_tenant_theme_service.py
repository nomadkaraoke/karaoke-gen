"""
Tenant self-service theme editing: sanitisation of untrusted style_params,
content-addressed uploads, and save (font consistency + bundled font copy).
"""
import io
import json

import pytest
from PIL import Image

from backend.models.tenant import TenantAuth, TenantConfig, TenantDefaults
from backend.services import tenant_theme_service as tts
from backend.services.tenant_admin_service import TenantValidationError


def _config():
    return TenantConfig(
        id="randy-vild",
        name="Randy Vild",
        subdomain="randy-vild.nomadkaraoke.com",
        defaults=TenantDefaults(theme_id="randy-vild", locked_theme="randy-vild"),
        auth=TenantAuth(allowed_emails=["randyvild@gmail.com"], require_email_domain=True),
    )


def _style(**karaoke):
    return {
        "intro": {"font": "AvenirNext-Bold.ttf", "background_image": "bg.jpg", "title_color": "#ffffff"},
        "karaoke": {"background_image": "bg.jpg", "primary_color": "61, 149, 197, 255", **karaoke},
        "end": {},
        "cdg": {},
    }


ASSETS = ["bg.jpg", "Custom-abc12345.ttf"]


# --- sanitize_style_params ----------------------------------------------------


def test_sanitize_applies_one_font_everywhere_and_fills_ass_keys():
    styles, font = tts.sanitize_style_params(_style(), available_assets=ASSETS)
    assert font == "AvenirNext-Bold.ttf"
    assert styles["karaoke"]["font_path"] == "AvenirNext-Bold.ttf"
    assert styles["end"]["font"] == "AvenirNext-Bold.ttf"
    assert styles["cdg"]["font_path"] == "AvenirNext-Bold.ttf"
    for key in ("underline", "strike_out", "angle", "encoding", "ass_name"):
        assert key in styles["karaoke"]


@pytest.mark.parametrize(
    "bad",
    ["/etc/passwd", "gs://other-bucket/x.jpg", "../x.jpg", "themes/other/assets/bg.jpg", "C:\\x.jpg"],
)
def test_sanitize_rejects_paths(bad):
    style = _style()
    style["karaoke"]["background_image"] = bad
    with pytest.raises(TenantValidationError):
        tts.sanitize_style_params(style, available_assets=ASSETS)


def test_sanitize_rejects_unuploaded_asset_and_unknown_font():
    style = _style()
    style["intro"]["background_image"] = "not-uploaded.jpg"
    with pytest.raises(TenantValidationError, match="hasn't been uploaded"):
        tts.sanitize_style_params(style, available_assets=ASSETS)
    style = _style()
    style["intro"]["font"] = "Comic.ttf"
    with pytest.raises(TenantValidationError, match="isn't available"):
        tts.sanitize_style_params(style, available_assets=ASSETS)


def test_sanitize_accepts_uploaded_font():
    style = _style()
    style["intro"]["font"] = "Custom-abc12345.ttf"
    styles, font = tts.sanitize_style_params(style, available_assets=ASSETS)
    assert font == styles["karaoke"]["font_path"] == "Custom-abc12345.ttf"


@pytest.mark.parametrize(
    "section,field,value",
    [
        ("karaoke", "font_size", 5000),
        ("karaoke", "max_line_length", 2),
        ("karaoke", "top_padding", "lots"),
        ("karaoke", "primary_color", "#ff0000"),
        ("karaoke", "secondary_color", "300, 0, 0, 255"),
        ("intro", "title_color", "red"),
        ("intro", "title_region", "0,0,5000,100"),
        ("intro", "artist_region", "nope"),
    ],
)
def test_sanitize_rejects_out_of_range_values(section, field, value):
    style = _style()
    style[section][field] = value
    with pytest.raises(TenantValidationError):
        tts.sanitize_style_params(style, available_assets=ASSETS)


def test_sanitize_rejects_unknown_sections():
    style = _style()
    style["evil"] = {}
    with pytest.raises(TenantValidationError):
        tts.sanitize_style_params(style, available_assets=ASSETS)


# --- uploads + save -------------------------------------------------------------


class FakeStorage:
    def __init__(self):
        self.blobs = {
            "themes/randy-vild/style_params.json": json.dumps(_style()).encode(),
            "themes/randy-vild/assets/bg.jpg": b"jpg",
        }

    def list_files(self, prefix):
        return [p for p in self.blobs if p.startswith(prefix)]

    def file_exists(self, path):
        return path in self.blobs

    def download_json(self, path):
        return json.loads(self.blobs[path])

    def download_file(self, path, local):
        with open(local, "wb") as fh:
            fh.write(self.blobs[path])

    def upload_fileobj(self, fileobj, path, content_type=None, cache_control=None):
        self.blobs[path] = fileobj.read()
        self.last_cache_control = cache_control


def _png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, "PNG")
    return buf.getvalue()


def test_upload_is_content_addressed_and_no_store():
    storage = FakeStorage()
    data = _png_bytes()
    name = tts.store_uploaded_asset(_config(), "My Background.png", data, storage=storage)
    assert name.startswith("My_Background-") and name.endswith(".png")
    assert storage.blobs[f"themes/randy-vild/assets/{name}"] == data
    assert storage.last_cache_control == "no-store"
    # Same content -> same name (idempotent); different content never overwrites it
    assert tts.store_uploaded_asset(_config(), "My Background.png", data, storage=storage) == name


def test_upload_rejects_non_images_and_bad_fonts():
    storage = FakeStorage()
    with pytest.raises(TenantValidationError):
        tts.store_uploaded_asset(_config(), "evil.png", b"not an image", storage=storage)
    with pytest.raises(TenantValidationError):
        tts.store_uploaded_asset(_config(), "font.ttf", b"not a font", storage=storage)
    with pytest.raises(TenantValidationError):
        tts.store_uploaded_asset(_config(), "script.svg", b"<svg/>", storage=storage)


def test_save_copies_bundled_font_sets_family_and_never_touches_config(monkeypatch):
    storage = FakeStorage()
    calls = {}

    def fake_update_tenant(tenant_id, **kwargs):
        calls["tenant_id"] = tenant_id
        calls.update(kwargs)

    monkeypatch.setattr(tts, "update_tenant", fake_update_tenant)
    styles = tts.save_tenant_theme(_config(), _style(), storage=storage)

    assert calls["tenant_id"] == "randy-vild"
    assert "config_updates" not in calls and "logo" not in calls  # tenants can't edit their config
    assert styles["karaoke"]["font"].startswith("Avenir Next")  # libass family name
    assert "AvenirNext-Bold.ttf" in calls["assets"]  # bundled font copied into the theme
    assert calls["style_params"]["karaoke"]["font_path"] == "AvenirNext-Bold.ttf"


def test_get_theme_for_editor_lists_images_and_fonts():
    storage = FakeStorage()
    storage.blobs["themes/randy-vild/assets/Custom-abc12345.ttf"] = b"font"
    data = tts.get_theme_for_editor(_config(), storage=storage)
    assert data["images"] == ["bg.jpg"]
    assert "Custom-abc12345.ttf" in data["fonts"] and "AvenirNext-Bold.ttf" in data["fonts"]
