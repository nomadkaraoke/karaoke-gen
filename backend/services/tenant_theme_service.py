"""
Tenant self-service theme editing (used by /api/tenant/theme).

Tenant users edit their own tenant's theme, so their input is untrusted and is
validated more strictly than the admin console's:

- every asset field must be a bare basename that exists in the theme's assets
  (or, for the font, a bundled karaoke_gen font) — no absolute/gs:// paths;
- the font is kept consistent with the render pipeline, which downloads ONE font
  asset (``intro.font``) and applies it to intro/karaoke/end/cdg: all four fields
  are set to it, ``karaoke.font`` is set to the family name libass matches, and a
  bundled font is copied into the theme's assets on save so render jobs find it;
- ASS style keys ``build_karaoke_styles`` needs without defaults are filled in;
- numeric layout values are bounded.

Uploads are stored content-addressed (``<stem>-<sha8>.<ext>``) so an upload can
never overwrite an asset that saved themes / existing jobs reference.
"""

import copy
import hashlib
import io
import logging
import os
import re
import tempfile
from typing import Dict, List, Optional, Tuple

from backend.services.storage_service import NO_STORE_CACHE_CONTROL, StorageService
from backend.services.tenant_admin_service import (
    IMAGE_CONTENT_TYPES,
    TenantValidationError,
    _content_type_for,
    _safe_asset_name,
    _theme_id_for,
    _validate_style_params,
    update_tenant,
)
from backend.services.theme_preview_service import BUNDLED_FONTS_DIR, bundled_fonts, font_family_name
from backend.services.theme_service import THEMES_PREFIX

logger = logging.getLogger(__name__)

FONT_CONTENT_TYPES = {"ttf": "font/ttf", "otf": "font/otf"}
MAX_IMAGE_SIDE = 8000  # 4K backgrounds are 3840x2160; this leaves headroom
MAX_IMAGE_PIXELS = 40_000_000  # decoded size bound (PIL only errors at ~179 MP)
UPLOAD_EXTENSIONS = set(IMAGE_CONTENT_TYPES) | set(FONT_CONTENT_TYPES)

# (section, field) pairs that hold image asset basenames.
# (existing_image is not editable: neither the preview nor production resolves it.)
IMAGE_FIELDS = (
    ("intro", "background_image"),
    ("karaoke", "background_image"),
    ("end", "background_image"),
    ("cdg", "instrumental_background"),
    ("cdg", "title_screen_background"),
    ("cdg", "outro_background"),
)
FONT_FIELDS = (("intro", "font"), ("karaoke", "font_path"), ("end", "font"), ("cdg", "font_path"))

DEFAULT_FONT = "AvenirNext-Bold.ttf"  # bundled; used when a draft names no font

# Every key lyrics_transcriber.output.ass.style.build_karaoke_styles reads without a
# default — a theme missing one previews fine but crashes every real render.
REQUIRED_KARAOKE_KEYS = (
    "font", "ass_name", "primary_color", "secondary_color", "outline_color", "back_color",
    "bold", "italic", "underline", "strike_out", "scale_x", "scale_y", "spacing", "angle",
    "border_style", "outline", "shadow", "margin_l", "margin_r", "margin_v", "encoding",
)

# Free-text values written verbatim into the ASS header / style line.
ASS_TEXT_FIELDS = (("karaoke", "font"), ("karaoke", "ass_name"))
_ASS_UNSAFE = re.compile(r"[,\r\n{}\\]")

NUMERIC_BOUNDS = {
    ("karaoke", "scale_x"): (10, 400),
    ("karaoke", "scale_y"): (10, 400),
    ("karaoke", "spacing"): (-50, 200),
    ("karaoke", "angle"): (-360, 360),
    ("karaoke", "border_style"): (1, 4),
    ("karaoke", "margin_l"): (0, 3840),
    ("karaoke", "margin_r"): (0, 3840),
    ("karaoke", "margin_v"): (0, 2160),
    ("karaoke", "encoding"): (0, 255),
    ("karaoke", "font_size"): (40, 600),
    ("karaoke", "top_padding"): (0, 2000),
    ("karaoke", "max_line_length"): (10, 80),
    ("karaoke", "outline"): (0, 20),
    ("karaoke", "shadow"): (0, 20),
    ("intro", "video_duration"): (1, 30),
    ("end", "video_duration"): (1, 30),
}

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_RGBA = re.compile(r"^\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*$")
HEX_COLOR_FIELDS = (
    ("intro", "background_color"), ("intro", "title_color"), ("intro", "artist_color"), ("intro", "extra_text_color"),
    ("end", "background_color"), ("end", "title_color"), ("end", "artist_color"), ("end", "extra_text_color"),
    ("karaoke", "background_color"),
)
RGBA_COLOR_FIELDS = (
    ("karaoke", "primary_color"), ("karaoke", "secondary_color"),
    ("karaoke", "outline_color"), ("karaoke", "back_color"),
)
REGION_FIELDS = (
    ("intro", "title_region"), ("intro", "artist_region"), ("intro", "extra_text_region"),
    ("end", "title_region"), ("end", "artist_region"), ("end", "extra_text_region"),
)


def _theme_assets(storage: StorageService, theme_id: str) -> List[str]:
    return sorted(
        p.rsplit("/", 1)[-1]
        for p in storage.list_files(f"{THEMES_PREFIX}/{theme_id}/assets/")
        if p.rsplit("/", 1)[-1]
    )


def _basename_only(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TenantValidationError(f"{where} must be a file name.")
    name = value.strip()
    if "/" in name or "\\" in name or name.startswith(".") or ":" in name:
        raise TenantValidationError(f"{where} must be one of your theme's uploaded files, not a path.")
    return name


def _check_region(value: object, where: str) -> None:
    if value is None:
        return
    try:
        x, y, w, h = (int(float(p)) for p in str(value).split(","))
    except (ValueError, OverflowError) as exc:
        raise TenantValidationError(f"{where} must be 'x, y, width, height'.") from exc
    if min(x, y) < 0 or w <= 0 or h <= 0 or x + w > 3840 or y + h > 2160:
        raise TenantValidationError(f"{where} must fit inside the 3840x2160 frame.")


def sanitize_style_params(
    style_params: object, *, available_assets: List[str]
) -> Tuple[Dict, Optional[str]]:
    """Validate + normalise tenant-supplied style_params.

    Returns (clean_style_params, font_basename). Raises TenantValidationError.
    """
    styles = copy.deepcopy(_validate_style_params(style_params))
    for section in ("intro", "karaoke", "end", "cdg"):
        styles.setdefault(section, {})

    assets = set(available_assets)
    fonts_ok = assets | set(bundled_fonts())

    for section in ("intro", "end"):
        if "existing_image" in styles[section]:
            styles[section]["existing_image"] = None

    for section, field in IMAGE_FIELDS:
        value = styles[section].get(field)
        if value in (None, ""):
            if field in styles[section]:
                styles[section][field] = None
            continue
        name = _basename_only(value, f"{section}.{field}")
        if name not in assets:
            raise TenantValidationError(f"{section}.{field} '{name}' hasn't been uploaded to this theme.")
        styles[section][field] = name

    # One font for every section (the render pipeline applies intro.font everywhere).
    # Always set, so no section can keep a path of its own (e.g. an absolute
    # karaoke.font_path would otherwise reach the ffmpeg fontsdir / libass).
    font = _basename_only(styles["intro"].get("font") or DEFAULT_FONT, "intro.font")
    if font not in fonts_ok:
        raise TenantValidationError(f"Font '{font}' isn't available — upload it or pick a built-in font.")
    for section, field in FONT_FIELDS:
        styles[section][field] = font

    from karaoke_gen.style_loader import DEFAULT_KARAOKE_STYLE

    for key in REQUIRED_KARAOKE_KEYS:
        if styles["karaoke"].get(key) is None:
            styles["karaoke"][key] = DEFAULT_KARAOKE_STYLE[key]
    for section, field in ASS_TEXT_FIELDS:
        value = styles[section].get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > 80 or _ASS_UNSAFE.search(value):
            raise TenantValidationError(f"{section}.{field} must be a short name without commas or line breaks.")

    singers = styles["karaoke"].get("singers")
    if singers is not None:
        if not isinstance(singers, dict) or not all(isinstance(v, dict) for v in singers.values()):
            raise TenantValidationError("karaoke.singers must map singer keys to colour settings.")
        for key, colours in singers.items():
            for name, value in colours.items():
                if name.endswith("_color") and (
                    not _RGBA.match(str(value)) or any(int(p) > 255 for p in str(value).split(","))
                ):
                    raise TenantValidationError(f"karaoke.singers.{key}.{name} must be 'r, g, b, a' (0-255).")

    for (section, field), (lo, hi) in NUMERIC_BOUNDS.items():
        if field in styles[section] and styles[section][field] is not None:
            value = styles[section][field]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
                raise TenantValidationError(f"{section}.{field} must be a number between {lo} and {hi}.")

    for section, field in HEX_COLOR_FIELDS:
        value = styles[section].get(field)
        if value is not None and not _HEX.match(str(value)):
            raise TenantValidationError(f"{section}.{field} must be a colour like #ff5bb8.")
    for section, field in RGBA_COLOR_FIELDS:
        value = styles[section].get(field)
        if value is not None:
            if not _RGBA.match(str(value)) or any(int(p) > 255 for p in str(value).split(",")):
                raise TenantValidationError(f"{section}.{field} must be 'r, g, b, a' (0-255).")
    for section, field in REGION_FIELDS:
        _check_region(styles[section].get(field), f"{section}.{field}")

    return styles, font


def _font_local_path(storage: StorageService, theme_id: str, font: str, workdir: str) -> str:
    blob_path = f"{THEMES_PREFIX}/{theme_id}/assets/{font}"
    if storage.file_exists(blob_path):
        local = os.path.join(workdir, font)
        storage.download_file(blob_path, local)
        return local
    return os.path.join(BUNDLED_FONTS_DIR, font)


class ThemeNotEditableError(TenantValidationError):
    """The tenant uses a shared/default theme it may not modify."""


def _editable_theme_id(config) -> str:
    """Tenants may only edit their own 1:1 theme (the one the admin console
    creates, named after the tenant) — never a shared or default theme."""
    theme_id = _theme_id_for(config)
    if theme_id != config.id:
        raise ThemeNotEditableError(
            "This portal uses a shared theme, so it can't be edited here. Contact Nomad Karaoke."
        )
    return theme_id


class ThemeNotFoundError(TenantValidationError):
    """The tenant's theme files are missing."""


def get_theme_for_editor(config, storage: Optional[StorageService] = None) -> Dict[str, object]:
    storage = storage or StorageService()
    theme_id = _editable_theme_id(config)
    try:
        style_params = storage.download_json(f"{THEMES_PREFIX}/{theme_id}/style_params.json")
    except Exception as exc:
        raise ThemeNotFoundError("This portal's theme couldn't be found. Contact Nomad Karaoke.") from exc
    assets = _theme_assets(storage, theme_id)
    fonts = sorted(set(bundled_fonts()) | {a for a in assets if a.lower().endswith((".ttf", ".otf"))})
    images = [a for a in assets if a.rsplit(".", 1)[-1].lower() in IMAGE_CONTENT_TYPES]
    return {"theme_id": theme_id, "style_params": style_params, "images": images, "fonts": fonts}


def store_uploaded_asset(
    config, filename: str, data: bytes, storage: Optional[StorageService] = None
) -> str:
    """Store an uploaded image/font content-addressed; return its basename."""
    storage = storage or StorageService()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in UPLOAD_EXTENSIONS:
        raise TenantValidationError("Upload a PNG, JPG, GIF or WEBP image, or a TTF/OTF font.")
    if ext in FONT_CONTENT_TYPES:
        # Must be a real font PIL/libass can load.
        from PIL import ImageFont

        try:
            ImageFont.truetype(io.BytesIO(data), 20)
        except Exception as exc:
            raise TenantValidationError("That font file couldn't be read.") from exc
    else:
        from PIL import Image

        try:
            with Image.open(io.BytesIO(data)) as img:
                width, height = img.size
                img.verify()
        except Exception as exc:
            raise TenantValidationError("That image file couldn't be read.") from exc
        if width > MAX_IMAGE_SIDE or height > MAX_IMAGE_SIDE or width * height > MAX_IMAGE_PIXELS:
            raise TenantValidationError(
                f"That image is too large ({width}x{height}). Use up to {MAX_IMAGE_SIDE}x{MAX_IMAGE_SIDE} pixels."
            )

    stem = _safe_asset_name(filename).rsplit(".", 1)[0][:60] or "asset"
    digest = hashlib.sha256(data).hexdigest()[:8]
    name = f"{stem}-{digest}.{ext}"
    theme_id = _editable_theme_id(config)
    content_type = FONT_CONTENT_TYPES.get(ext) or _content_type_for(ext)
    storage.upload_fileobj(
        io.BytesIO(data),
        f"{THEMES_PREFIX}/{theme_id}/assets/{name}",
        content_type=content_type,
        cache_control=NO_STORE_CACHE_CONTROL,
    )
    return name


def prepare_preview_styles(config, style_params: object, storage: Optional[StorageService] = None) -> Dict:
    storage = storage or StorageService()
    theme_id = _editable_theme_id(config)
    styles, _font = sanitize_style_params(style_params, available_assets=_theme_assets(storage, theme_id))
    return styles


def save_tenant_theme(config, style_params: object, storage: Optional[StorageService] = None) -> Dict:
    """Validate, finalise (font family / bundled font copy) and save the theme."""
    storage = storage or StorageService()
    theme_id = _editable_theme_id(config)
    styles, font = sanitize_style_params(style_params, available_assets=_theme_assets(storage, theme_id))

    assets: Dict[str, Tuple[bytes, str]] = {}
    with tempfile.TemporaryDirectory() as workdir:
        local = _font_local_path(storage, theme_id, font, workdir)
        try:
            styles["karaoke"]["font"] = font_family_name(local)
        except Exception as exc:
            raise TenantValidationError(f"Font '{font}' couldn't be read.") from exc
        if not storage.file_exists(f"{THEMES_PREFIX}/{theme_id}/assets/{font}"):
            # Bundled font: copy into the theme so render jobs download it.
            with open(local, "rb") as fh:
                assets[font] = (fh.read(), font.rsplit(".", 1)[-1])

    update_tenant(config.id, style_params=styles, assets=assets or None, storage=storage)
    logger.info(f"Tenant '{config.id}' saved theme '{theme_id}' (font={font})")
    return styles
