"""
Admin-side tenant provisioning.

Turns the hand-run ``scripts/setup-vocalstar-tenant.py`` recipe into a reusable
function the admin panel can call. Creating a tenant:

1. Derives a self-contained theme from the default Nomad theme:
   - copies the default theme's assets (fonts, CDG/backgrounds) into the new
     theme folder so every ``background_image`` basename resolves against the
     tenant theme's OWN ``assets/`` (this is the fix for the tenant-E2E
     "background image not found" class of bug),
   - applies the admin's colour overrides,
   - overlays any admin-provided background images.
2. Registers the theme in ``themes/_metadata.json``.
3. Writes ``tenants/{id}/config.json`` with sensible B2B defaults (create-only,
   so a concurrent/duplicate create can't clobber an existing tenant).

The tenant can then be driven immediately (no DNS) via the admin preview path
``?preview_tenant=<id>`` on the main domain; a real subdomain is an optional
later step (Cloudflare Pages custom domain).
"""

import copy
import io
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from google.api_core.exceptions import PreconditionFailed

from backend.config import settings
from backend.middleware.tenant import NON_TENANT_SUBDOMAINS
from backend.models.tenant import (
    TenantAuth,
    TenantBranding,
    TenantConfig,
    TenantDefaults,
    TenantFeatures,
)
from backend.models.theme import ColorOverrides
from backend.services.storage_service import StorageService
from backend.services.theme_service import METADATA_FILE, THEMES_PREFIX, get_theme_service
from backend.services.tenant_service import (
    DEFAULT_SENDER_EMAIL,
    TENANTS_PREFIX,
    get_tenant_service,
)

logger = logging.getLogger(__name__)

BASE_DOMAIN = "nomadkaraoke.com"

# Reserved IDs that must never become tenants (they are real subdomains / paths).
# Kept in sync with middleware.tenant.NON_TENANT_SUBDOMAINS.
RESERVED_TENANT_IDS = set(NON_TENANT_SUBDOMAINS)

# form field -> style_params section whose background_image it overrides
BACKGROUND_SECTIONS = {
    "karaoke_background": "karaoke",
    "intro_background": "intro",
    "end_background": "end",
}

# allowed image extension -> content type
IMAGE_CONTENT_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")


class TenantValidationError(ValueError):
    """Raised when tenant inputs are invalid (bad slug, reserved id, etc.)."""


class TenantConflictError(ValueError):
    """Raised when a tenant with the same id already exists."""


class TenantNotFoundError(ValueError):
    """Raised when the requested tenant does not exist."""


# Top-level sections a valid theme style_params document may contain.
STYLE_PARAM_SECTIONS = {"intro", "karaoke", "end", "cdg"}


def slugify_tenant_id(name: str) -> str:
    """Derive a tenant id slug from a display name."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug


def _validate_tenant_id(tenant_id: str) -> None:
    if not _SLUG_RE.match(tenant_id):
        raise TenantValidationError(
            "Tenant id must be 3-40 chars, lowercase letters/numbers/hyphens, "
            "and start/end with a letter or number."
        )
    if tenant_id in RESERVED_TENANT_IDS:
        raise TenantValidationError(f"'{tenant_id}' is a reserved subdomain and cannot be used.")


def _content_type_for(ext: str) -> str:
    return IMAGE_CONTENT_TYPES.get(ext.lower(), "application/octet-stream")


def _safe_asset_name(name: str) -> str:
    """Reduce an uploaded filename to a safe bare basename (no path traversal)."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    return base.lstrip(".") or "asset"


def _validate_style_params(style_params: object) -> Dict:
    """Validate a full theme style_params document supplied by an admin."""
    if not isinstance(style_params, dict):
        raise TenantValidationError("Theme style_params must be a JSON object.")
    if not style_params:
        raise TenantValidationError("Theme style_params cannot be empty.")
    unknown = set(style_params) - STYLE_PARAM_SECTIONS
    if unknown:
        raise TenantValidationError(
            f"Unknown theme section(s): {', '.join(sorted(unknown))}. "
            f"Allowed: {', '.join(sorted(STYLE_PARAM_SECTIONS))}."
        )
    for section, value in style_params.items():
        if not isinstance(value, dict):
            raise TenantValidationError(f"Theme section '{section}' must be a JSON object.")
    return style_params


def _copy_default_theme_assets(storage: StorageService, base_theme_id: str, theme_id: str) -> None:
    """Copy the default theme's assets into the new theme so it is self-contained."""
    src_prefix = f"{THEMES_PREFIX}/{base_theme_id}/assets/"
    dst_prefix = f"{THEMES_PREFIX}/{theme_id}/assets/"
    for path in storage.list_files(src_prefix):
        basename = path.rsplit("/", 1)[-1]
        if not basename:  # skip the folder placeholder
            continue
        storage.copy_blob(path, f"{dst_prefix}{basename}")


def _register_theme_metadata(storage: StorageService, theme_id: str, name: str) -> None:
    """Add the new theme to themes/_metadata.json (read-modify-write, idempotent)."""
    try:
        registry = storage.download_json(METADATA_FILE)
    except Exception:
        registry = {"version": 1, "themes": []}

    themes = registry.setdefault("themes", [])
    if any(t.get("id") == theme_id for t in themes):
        return
    themes.append(
        {
            "id": theme_id,
            "name": name,
            "description": f"{name} tenant theme",
            "is_default": False,
        }
    )
    storage.upload_json(METADATA_FILE, registry)


def create_tenant(
    *,
    name: str,
    tenant_id: Optional[str] = None,
    subdomain: Optional[str] = None,
    allowed_email_domains: Optional[List[str]] = None,
    colors: Optional[ColorOverrides] = None,
    style_params_override: Optional[Dict] = None,
    tagline: Optional[str] = None,
    distribution_mode: str = "download_only",
    dropbox_path: Optional[str] = None,
    brand_prefix: Optional[str] = None,
    backgrounds: Optional[Dict[str, Tuple[bytes, str]]] = None,
    logo: Optional[Tuple[bytes, str]] = None,
    storage: Optional[StorageService] = None,
) -> TenantConfig:
    """
    Provision a new white-label tenant (theme + config) in GCS.

    Args:
        name: Display name (e.g. "Randy Vild").
        tenant_id: Slug id; derived from name if omitted.
        subdomain: Full subdomain; defaults to ``{id}.nomadkaraoke.com``.
        allowed_email_domains: Domains permitted to log in (empty = no restriction;
            admins can always sign in).
        colors: Lyric/title/artist colour overrides applied to the theme.
        tagline: Optional portal tagline.
        distribution_mode: "download_only" (default), "all", or "cloud_only".
        dropbox_path: If set, outputs are delivered to this Dropbox folder.
        brand_prefix: Output filename prefix (e.g. "RVILD").
        backgrounds: {field -> (bytes, ext)} for karaoke/intro/end backgrounds.
        logo: (bytes, ext) for the portal logo.
        storage: Injected StorageService (for tests).

    Returns:
        The persisted TenantConfig.

    Raises:
        TenantValidationError, TenantConflictError, ValueError.
    """
    name = (name or "").strip()
    if not name:
        raise TenantValidationError("Tenant name is required.")

    tenant_id = (tenant_id or slugify_tenant_id(name)).strip().lower()
    _validate_tenant_id(tenant_id)

    subdomain = (subdomain or f"{tenant_id}.{BASE_DOMAIN}").strip().lower()

    storage = storage or StorageService()
    tenant_service = get_tenant_service()
    theme_service = get_theme_service()

    if tenant_service.tenant_exists(tenant_id):
        raise TenantConflictError(f"Tenant '{tenant_id}' already exists.")

    # --- Derive theme from the default Nomad theme ---------------------------
    base_theme_id = theme_service.get_default_theme_id()
    if not base_theme_id:
        raise ValueError("No default theme is configured; cannot derive a tenant theme.")
    base_style = theme_service.get_theme_style_params(base_theme_id)
    if base_style is None:
        raise ValueError(f"Default theme '{base_theme_id}' style params could not be loaded.")

    theme_id = tenant_id  # 1:1 theme per tenant, mirrors the setup scripts

    _copy_default_theme_assets(storage, base_theme_id, theme_id)

    if style_params_override is not None:
        # Admin supplied a full theme document — it is authoritative. Default
        # assets are still copied above so any inherited basenames resolve.
        style_params = copy.deepcopy(_validate_style_params(style_params_override))
    else:
        style_params = copy.deepcopy(base_style)
        if colors and colors.has_overrides():
            style_params = theme_service.apply_color_overrides(style_params, colors)

    for field, (data, ext) in (backgrounds or {}).items():
        section = BACKGROUND_SECTIONS.get(field)
        if not section:
            continue
        ext = ext.lower().lstrip(".")
        filename = f"{field}.{ext}"
        storage.upload_fileobj(
            io.BytesIO(data),
            f"{THEMES_PREFIX}/{theme_id}/assets/{filename}",
            content_type=_content_type_for(ext),
        )
        style_params.setdefault(section, {})["background_image"] = filename

    storage.upload_json(f"{THEMES_PREFIX}/{theme_id}/style_params.json", style_params)
    _register_theme_metadata(storage, theme_id, name)

    # --- Logo ----------------------------------------------------------------
    logo_url: Optional[str] = None
    if logo:
        logo_data, logo_ext = logo
        logo_ext = logo_ext.lower().lstrip(".")
        logo_path = f"{TENANTS_PREFIX}/{tenant_id}/logo.{logo_ext}"
        storage.upload_fileobj(
            io.BytesIO(logo_data), logo_path, content_type=_content_type_for(logo_ext)
        )
        logo_url = f"gs://{settings.gcs_bucket_name}/{logo_path}"

    # --- Tenant config -------------------------------------------------------
    domains = sorted({d.strip().lower() for d in (allowed_email_domains or []) if d.strip()})
    now = datetime.now(timezone.utc)

    branding = TenantBranding(
        logo_url=logo_url,
        site_title=f"{name} Karaoke Generator",
        tagline=tagline or None,
    )
    if colors:
        if colors.title_color:
            branding.primary_color = colors.title_color
        if colors.sung_lyrics_color:
            branding.secondary_color = colors.sung_lyrics_color
        if colors.artist_color:
            branding.accent_color = colors.artist_color

    config = TenantConfig(
        id=tenant_id,
        name=name,
        subdomain=subdomain,
        is_active=True,
        branding=branding,
        features=TenantFeatures(
            audio_search=False,  # tenant provides their own audio
            file_upload=True,
            bulk_upload=True,
            youtube_url=False,
            youtube_upload=False,  # B2B: never publish to YouTube
            dropbox_upload=bool(dropbox_path),
            gdrive_upload=False,
            theme_selection=False,  # always use the tenant theme
            color_overrides=False,
            enable_cdg=True,
            enable_4k=True,
            admin_access=False,
        ),
        defaults=TenantDefaults(
            theme_id=theme_id,
            locked_theme=theme_id,
            distribution_mode=distribution_mode,
            brand_prefix=(brand_prefix or None),
            dropbox_path=(dropbox_path or None),
            gdrive_folder_id=None,
        ),
        auth=TenantAuth(
            allowed_email_domains=domains,
            require_email_domain=bool(domains),
            fixed_token_ids=[],
            sender_email=DEFAULT_SENDER_EMAIL,
        ),
        created_at=now,
        updated_at=now,
    )

    config_path = f"{TENANTS_PREFIX}/{tenant_id}/config.json"
    try:
        storage.upload_json(config_path, config.model_dump(mode="json"), if_generation_match=0)
    except PreconditionFailed as exc:
        raise TenantConflictError(f"Tenant '{tenant_id}' already exists.") from exc

    tenant_service.invalidate_cache(tenant_id)
    theme_service.invalidate_cache()

    logger.info(f"Created tenant '{tenant_id}' (theme '{theme_id}', subdomain '{subdomain}')")
    return config


def _theme_id_for(config: TenantConfig) -> str:
    """The theme id backing a tenant (locked theme, else default theme, else id)."""
    return config.defaults.locked_theme or config.defaults.theme_id or config.id


def get_default_style_params(storage: Optional[StorageService] = None) -> Dict:
    """Return the default Nomad theme's full style_params (a starting template)."""
    theme_service = get_theme_service()
    base_theme_id = theme_service.get_default_theme_id()
    if not base_theme_id:
        raise ValueError("No default theme is configured.")
    style = theme_service.get_theme_style_params(base_theme_id)
    if style is None:
        raise ValueError(f"Default theme '{base_theme_id}' style params could not be loaded.")
    return style


def get_tenant_detail(tenant_id: str, storage: Optional[StorageService] = None) -> Dict[str, object]:
    """Return a tenant's full config, its theme style_params, and its asset list."""
    storage = storage or StorageService()
    tenant_service = get_tenant_service()
    config = tenant_service.get_tenant_config(tenant_id, force_refresh=True)
    if not config:
        raise TenantNotFoundError(f"Tenant '{tenant_id}' not found.")

    theme_id = _theme_id_for(config)
    try:
        style_params = storage.download_json(f"{THEMES_PREFIX}/{theme_id}/style_params.json")
    except Exception:
        style_params = {}
    assets = sorted(
        p.rsplit("/", 1)[-1]
        for p in storage.list_files(f"{THEMES_PREFIX}/{theme_id}/assets/")
        if p.rsplit("/", 1)[-1]
    )
    return {
        "tenant": config.model_dump(mode="json"),
        "theme_id": theme_id,
        "style_params": style_params,
        "assets": assets,
    }


def _merge_config(config: TenantConfig, updates: Dict) -> TenantConfig:
    """Merge a partial update dict over an existing TenantConfig (id is immutable)."""
    base = config.model_dump(mode="json")
    for key, value in updates.items():
        if key == "id":
            continue
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    base["id"] = config.id  # never allow id to change
    return TenantConfig(**base)


def update_tenant(
    tenant_id: str,
    *,
    config_updates: Optional[Dict] = None,
    style_params: Optional[Dict] = None,
    assets: Optional[Dict[str, Tuple[bytes, str]]] = None,
    logo: Optional[Tuple[bytes, str]] = None,
    storage: Optional[StorageService] = None,
) -> TenantConfig:
    """
    Update an existing tenant: merge config fields, replace the full theme
    style_params, and/or add/replace theme assets (backgrounds, fonts).

    Iteration loop for a client: edit the theme JSON here, re-render jobs.

    Args:
        tenant_id: Tenant to update.
        config_updates: Partial TenantConfig fields to merge (one-level deep).
        style_params: Full theme document to write (replaces existing).
        assets: {target_filename -> (bytes, ext)} written to the theme's assets/.
        logo: (bytes, ext) -> tenants/{id}/logo.<ext> + branding.logo_url.
        storage: Injected StorageService (for tests).

    Raises:
        TenantNotFoundError, TenantValidationError.
    """
    storage = storage or StorageService()
    tenant_service = get_tenant_service()
    theme_service = get_theme_service()

    config = tenant_service.get_tenant_config(tenant_id, force_refresh=True)
    if not config:
        raise TenantNotFoundError(f"Tenant '{tenant_id}' not found.")

    theme_id = _theme_id_for(config)

    # 1. Assets (uploaded files -> theme assets, keyed by their target basename)
    for name, (data, ext) in (assets or {}).items():
        safe = _safe_asset_name(name)
        storage.upload_fileobj(
            io.BytesIO(data),
            f"{THEMES_PREFIX}/{theme_id}/assets/{safe}",
            content_type=_content_type_for(ext),
        )

    # 2. Full theme style_params replace
    if style_params is not None:
        _validate_style_params(style_params)
        storage.upload_json(f"{THEMES_PREFIX}/{theme_id}/style_params.json", style_params)

    # 3. Logo
    merged_updates = dict(config_updates or {})
    if logo:
        logo_data, logo_ext = logo
        logo_ext = logo_ext.lower().lstrip(".")
        logo_path = f"{TENANTS_PREFIX}/{tenant_id}/logo.{logo_ext}"
        storage.upload_fileobj(
            io.BytesIO(logo_data), logo_path, content_type=_content_type_for(logo_ext)
        )
        branding = dict(merged_updates.get("branding") or {})
        branding["logo_url"] = f"gs://{settings.gcs_bucket_name}/{logo_path}"
        merged_updates["branding"] = branding

    # 4. Config merge
    if merged_updates:
        config = _merge_config(config, merged_updates)

    config.updated_at = datetime.now(timezone.utc)
    storage.upload_json(f"{TENANTS_PREFIX}/{tenant_id}/config.json", config.model_dump(mode="json"))

    tenant_service.invalidate_cache(tenant_id)
    theme_service.invalidate_cache()

    logger.info(f"Updated tenant '{tenant_id}' (theme '{theme_id}')")
    return config


def list_tenants(storage: Optional[StorageService] = None) -> List[Dict[str, object]]:
    """Return a summary list of all tenants for the admin UI."""
    storage = storage or StorageService()
    summaries: List[Dict[str, object]] = []
    for path in storage.list_files(f"{TENANTS_PREFIX}/"):
        if not path.endswith("/config.json"):
            continue
        try:
            data = storage.download_json(path)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(f"Skipping unreadable tenant config {path}: {exc}")
            continue
        defaults = data.get("defaults") or {}
        summaries.append(
            {
                "id": data.get("id"),
                "name": data.get("name"),
                "subdomain": data.get("subdomain"),
                "is_active": data.get("is_active", True),
                "locked_theme": defaults.get("locked_theme"),
                "dropbox_path": defaults.get("dropbox_path"),
                "created_at": data.get("created_at"),
            }
        )
    summaries.sort(key=lambda t: str(t.get("name") or t.get("id") or "").lower())
    return summaries
