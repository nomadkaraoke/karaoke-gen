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

    def download_bytes(self, path):
        return self.blobs[path]

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


def test_save_keeps_distinct_cdg_font_and_copies_it_when_bundled(monkeypatch):
    storage = FakeStorage()
    calls = {}
    monkeypatch.setattr(tts, "update_tenant", lambda tenant_id, **kw: calls.update(kw))
    style = _style()
    style["cdg"]["font_path"] = "Oswald-SemiBold.ttf"
    tts.save_tenant_theme(_config(), style, storage=storage)
    assert calls["style_params"]["cdg"]["font_path"] == "Oswald-SemiBold.ttf"
    assert calls["style_params"]["karaoke"]["font_path"] == "AvenirNext-Bold.ttf"
    assert "Oswald-SemiBold.ttf" in calls["assets"]


def test_save_rejects_an_unreadable_distinct_cdg_font(monkeypatch):
    storage = FakeStorage()
    storage.blobs["themes/randy-vild/assets/Custom-abc12345.ttf"] = b"not really a font"
    monkeypatch.setattr(tts, "update_tenant", lambda tenant_id, **kw: None)
    style = _style()
    style["cdg"]["font_path"] = "Custom-abc12345.ttf"
    with pytest.raises(TenantValidationError):
        tts.save_tenant_theme(_config(), style, storage=storage)


def test_get_theme_for_editor_lists_images_and_fonts():
    storage = FakeStorage()
    storage.blobs["themes/randy-vild/assets/Custom-abc12345.ttf"] = b"font"
    data = tts.get_theme_for_editor(_config(), storage=storage)
    assert data["images"] == ["bg.jpg"]
    assert "Custom-abc12345.ttf" in data["fonts"] and "AvenirNext-Bold.ttf" in data["fonts"]


# --- review hardening -----------------------------------------------------------


def test_missing_intro_font_cannot_leave_a_path_in_other_sections():
    """Regression: with intro.font unset, karaoke.font_path used to survive
    unsanitised and reach ffmpeg's fontsdir / libass."""
    style = _style()
    style["intro"]["font"] = None
    style["karaoke"]["font_path"] = "/proc/self/environ"
    style["end"]["font"] = "/etc/passwd"
    styles, font = tts.sanitize_style_params(style, available_assets=ASSETS)
    assert font == tts.DEFAULT_FONT
    for section, field in tts.FONT_FIELDS:
        assert styles[section][field] == tts.DEFAULT_FONT


@pytest.mark.parametrize("field,value", [("ass_name", "Nomad\nDialogue: 0"), ("font", "Evil, Font"), ("font", "x" * 81)])
def test_ass_text_fields_cannot_inject(field, value):
    style = _style()
    style["karaoke"][field] = value
    with pytest.raises(TenantValidationError):
        tts.sanitize_style_params(style, available_assets=ASSETS)


def test_all_renderer_required_karaoke_keys_are_filled():
    from karaoke_gen.style_loader import DEFAULT_KARAOKE_STYLE

    styles, _ = tts.sanitize_style_params(_style(), available_assets=ASSETS)
    for key in tts.REQUIRED_KARAOKE_KEYS:
        assert styles["karaoke"][key] is not None
    assert styles["karaoke"]["bold"] == DEFAULT_KARAOKE_STYLE["bold"]


def test_existing_image_is_forced_off():
    style = _style()
    style["intro"]["existing_image"] = "bg.jpg"
    styles, _ = tts.sanitize_style_params(style, available_assets=ASSETS)
    assert styles["intro"]["existing_image"] is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s["karaoke"].update(singers={"1": {"primary_color": "999, 0, 0, 255"}}),
        lambda s: s["karaoke"].update(singers=["nope"]),
        lambda s: s["karaoke"].update(scale_x=5000),
        lambda s: s["intro"].update(title_region="inf,0,100,100"),
    ],
)
def test_more_invalid_values_rejected(mutate):
    style = _style()
    mutate(style)
    with pytest.raises(TenantValidationError):
        tts.sanitize_style_params(style, available_assets=ASSETS)


def test_huge_image_upload_rejected():
    buf = io.BytesIO()
    Image.new("1", (9000, 10)).save(buf, "PNG")  # tiny file, oversized dimensions
    with pytest.raises(TenantValidationError, match="too large"):
        tts.store_uploaded_asset(_config(), "wide.png", buf.getvalue(), storage=FakeStorage())


def test_shared_theme_is_not_editable():
    shared = _config().model_copy(update={"defaults": TenantDefaults(theme_id="nomad", locked_theme="nomad")})
    with pytest.raises(tts.ThemeNotEditableError):
        tts.get_theme_for_editor(shared, storage=FakeStorage())
    with pytest.raises(tts.ThemeNotEditableError):
        tts.save_tenant_theme(shared, _style(), storage=FakeStorage())
    with pytest.raises(tts.ThemeNotEditableError):
        tts.store_uploaded_asset(shared, "a.png", _png_bytes(), storage=FakeStorage())


def test_missing_theme_file_is_not_found():
    storage = FakeStorage()
    del storage.blobs["themes/randy-vild/style_params.json"]
    storage.download_json = lambda path: (_ for _ in ()).throw(FileNotFoundError(path))
    with pytest.raises(tts.ThemeNotFoundError):
        tts.get_theme_for_editor(_config(), storage=storage)


@pytest.mark.parametrize("section", ["intro", "end"])
def test_screen_enabled_flag_must_be_boolean(section):
    style = _style()
    style[section]["enabled"] = False
    styles, _ = tts.sanitize_style_params(style, available_assets=ASSETS)
    assert styles[section]["enabled"] is False

    style[section]["enabled"] = "no"
    with pytest.raises(TenantValidationError, match=f"{section}.enabled"):
        tts.sanitize_style_params(style, available_assets=ASSETS)


def test_sanitize_keeps_a_distinct_available_cdg_font():
    style = _style()
    style["cdg"]["font_path"] = "Oswald-SemiBold.ttf"  # bundled, condensed for CDG lyrics
    styles, font = tts.sanitize_style_params(style, available_assets=ASSETS)
    assert font == "AvenirNext-Bold.ttf"
    assert styles["karaoke"]["font_path"] == "AvenirNext-Bold.ttf"
    assert styles["cdg"]["font_path"] == "Oswald-SemiBold.ttf"


@pytest.mark.parametrize("bad", ["/abs/Oswald-SemiBold.ttf", "NotAFont-zzz.ttf", "bg.jpg"])
def test_sanitize_replaces_unsafe_or_unknown_cdg_font_with_main_font(bad):
    style = _style()
    style["cdg"]["font_path"] = bad
    styles, _ = tts.sanitize_style_params(style, available_assets=ASSETS)
    assert styles["cdg"]["font_path"] == "AvenirNext-Bold.ttf"


# --- CD+G styles ----------------------------------------------------------------


def _image_bytes(size, fmt="PNG", color="blue"):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, fmt)
    return buf.getvalue()


def _cdg_style(**cdg):
    style = _style()
    style["cdg"] = {
        "font_path": "Oswald-SemiBold.ttf", "font_size": 18, "title_color": "#ffff00", "artist_color": "#ffff00",
        "active_fill": "#006CF9", "inactive_fill": "#ffff00", "title_separator_text": "MADE FAMOUS BY",
        "title_block_top": 56, **cdg,
    }
    return style


def test_sanitize_accepts_cdg_settings():
    styles, _ = tts.sanitize_style_params(_cdg_style(), available_assets=ASSETS)
    assert styles["cdg"]["title_separator_text"] == "MADE FAMOUS BY"
    assert styles["cdg"]["active_fill"] == "#006CF9"


@pytest.mark.parametrize(
    "field, value",
    [
        ("active_fill", "blue"),
        ("title_color", "#ff00"),
        ("border_color", "rgb(0,0,0)"),
        ("font_size", 200),
        ("title_block_top", -1),
        ("title_font_size", "28"),
        ("stroke_width", 9),
        ("title_separator_text", "X" * 41),
        ("title_separator_text", "two\nlines"),
        ("outro_text_line1", 5),
    ],
)
def test_sanitize_rejects_bad_cdg_values(field, value):
    with pytest.raises(TenantValidationError):
        tts.sanitize_style_params(_cdg_style(**{field: value}), available_assets=ASSETS)


def test_sanitize_rejects_unuploaded_cdg_background():
    with pytest.raises(TenantValidationError, match="hasn't been uploaded"):
        tts.sanitize_style_params(_cdg_style(title_screen_background="logo.png"), available_assets=ASSETS)


def test_fit_cdg_image_letterboxes_to_the_cdg_screen():
    fitted = Image.open(io.BytesIO(tts.fit_cdg_image(_image_bytes((1920, 1080), "JPEG"))))
    assert fitted.size == (300, 216) and fitted.format == "PNG"
    # 16:9 into 300x216 leaves black bars top and bottom
    assert fitted.getpixel((150, 2)) == (0, 0, 0)
    assert fitted.getpixel((150, 108))[2] > 200


def test_cdg_upload_is_fitted_and_stored_as_png():
    storage = FakeStorage()
    name = tts.store_uploaded_asset(_config(), "Logo Card.jpg", _image_bytes((900, 648), "JPEG"), storage=storage, purpose="cdg")
    assert name.startswith("Logo_Card-cdg-") and name.endswith(".png")
    stored = Image.open(io.BytesIO(storage.blobs[f"themes/randy-vild/assets/{name}"]))
    assert stored.size == (300, 216)


def test_ordinary_upload_is_not_resized():
    storage = FakeStorage()
    name = tts.store_uploaded_asset(_config(), "bg.png", _image_bytes((900, 648)), storage=storage)
    assert Image.open(io.BytesIO(storage.blobs[f"themes/randy-vild/assets/{name}"])).size == (900, 648)


def test_save_rejects_cdg_background_of_the_wrong_size(monkeypatch):
    storage = FakeStorage()
    storage.blobs["themes/randy-vild/assets/big.png"] = _image_bytes((640, 480))
    monkeypatch.setattr(tts, "update_tenant", lambda tenant_id, **kw: None)
    with pytest.raises(TenantValidationError, match="300x216"):
        tts.save_tenant_theme(_config(), _cdg_style(title_screen_background="big.png"), storage=storage)


def test_save_accepts_cdg_background_of_the_right_size(monkeypatch):
    storage = FakeStorage()
    storage.blobs["themes/randy-vild/assets/card.png"] = _image_bytes((300, 216))
    calls = {}
    monkeypatch.setattr(tts, "update_tenant", lambda tenant_id, **kw: calls.update(kw))
    tts.save_tenant_theme(_config(), _cdg_style(title_screen_background="card.png", outro_background="card.png"), storage=storage)
    assert calls["style_params"]["cdg"]["title_screen_background"] == "card.png"



def test_editor_lists_only_300x216_images_for_cdg():
    storage = FakeStorage()
    storage.blobs["themes/randy-vild/assets/card.png"] = _image_bytes((300, 216))
    storage.blobs["themes/randy-vild/assets/big.png"] = _image_bytes((1920, 1080))

    class _Blob:
        def __init__(self, data):
            self.data = data

        def download_as_bytes(self, start=0, end=None):
            return self.data[start:(end + 1) if end is not None else None]

    class _Bucket:
        def get_blob(self, path):
            return _Blob(storage.blobs[path]) if path in storage.blobs else None

    storage.bucket = _Bucket()
    data = tts.get_theme_for_editor(_config(), storage=storage)
    assert "big.png" in data["images"] and "card.png" in data["images"]
    assert data["cdg_images"] == ["card.png"]  # bg.jpg isn't a real image here, big.png is 1920x1080
