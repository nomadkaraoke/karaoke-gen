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
from backend.services.storage_service import NO_STORE_CACHE_CONTROL, StorageService
from backend.services.theme_service import METADATA_FILE, THEMES_PREFIX, get_theme_service
from backend.services.tenant_domain_service import (
    TenantDomainConflictError,
    TenantDomainError,
    TenantDomainService,
    get_tenant_domain_service,
)
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
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DOMAIN_RE = re.compile(r"^(?!-)[a-z0-9-]+(\.[a-z0-9-]+)+$")
_BRAND_PREFIX_RE = re.compile(r"^[A-Z][A-Z0-9]{1,11}$")


class TenantValidationError(ValueError):
    """Raised when tenant inputs are invalid (bad slug, reserved id, etc.)."""


class TenantConflictError(ValueError):
    """Raised when a tenant with the same id already exists."""


class TenantNotFoundError(ValueError):
    """Raised when the requested tenant does not exist."""


class TenantProvisioningError(RuntimeError):
    """Raised when the tenant's subdomain (Cloudflare) could not be set up/removed."""


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


def _normalize_emails(emails: Optional[List[str]]) -> List[str]:
    cleaned = sorted({(e or "").strip().lower() for e in (emails or []) if (e or "").strip()})
    bad = [e for e in cleaned if not _EMAIL_RE.match(e)]
    if bad:
        raise TenantValidationError(f"Invalid email address(es): {', '.join(bad)}")
    return cleaned


def _normalize_domains(domains: Optional[List[str]]) -> List[str]:
    cleaned = sorted(
        {(d or "").strip().lower().lstrip("@") for d in (domains or []) if (d or "").strip()}
    )
    bad = [d for d in cleaned if not _DOMAIN_RE.match(d)]
    if bad:
        raise TenantValidationError(f"Invalid email domain(s): {', '.join(bad)}")
    return cleaned


def _normalize_delivery(
    dropbox_path: Optional[str], brand_prefix: Optional[str]
) -> Tuple[Optional[str], Optional[str]]:
    """
    Normalize + validate the tenant's Dropbox delivery settings.

    The video worker only delivers to Dropbox when BOTH a path and a brand prefix
    are set (outputs are filed as ``<PREFIX>-0001 - Artist - Title``), so one
    without the other would silently fall back to download-only.
    """
    path = (dropbox_path or "").strip().rstrip("/")
    if path and not path.startswith("/"):
        path = f"/{path}"
    prefix = (brand_prefix or "").strip().upper()
    if bool(path) != bool(prefix):
        raise TenantValidationError(
            "Dropbox delivery needs both a Dropbox path and a brand prefix "
            "(outputs are filed as <PREFIX>-0001 - Artist - Title)."
        )
    if prefix and not _BRAND_PREFIX_RE.match(prefix):
        raise TenantValidationError(
            "Brand prefix must be 2-12 uppercase letters/numbers, starting with a letter."
        )
    return (path or None), (prefix or None)


def _check_brand_prefix_available(
    prefix: str, tenant_id: str, storage: StorageService
) -> None:
    """Reject a brand prefix used by the consumer defaults or another tenant.

    Brand-code counters are keyed by prefix alone, so sharing one would
    interleave numbering across two different Dropbox folders.
    """
    reserved = {
        p.upper()
        for p in (settings.default_brand_prefix, settings.default_private_brand_prefix)
        if p
    }
    if prefix in reserved:
        raise TenantValidationError(f"Brand prefix '{prefix}' is reserved for Nomad Karaoke.")
    for other in list_tenants(storage=storage):
        if other.get("id") != tenant_id and (other.get("brand_prefix") or "").upper() == prefix:
            raise TenantConflictError(
                f"Brand prefix '{prefix}' is already used by tenant '{other.get('id')}'."
            )


def get_dropbox_service():
    """Lazy accessor — dropbox_service pulls in secretmanager, kept off the startup import graph."""
    from backend.services.dropbox_service import get_dropbox_service as _get_dropbox_service

    return _get_dropbox_service()


def _ensure_dropbox_folder(path: str) -> None:
    """Create the tenant's Dropbox output folder so delivery works from job one."""
    try:
        dropbox = get_dropbox_service()
        if not dropbox.is_configured:
            raise TenantProvisioningError(
                "Dropbox isn't configured on the backend; can't create the output folder."
            )
        created = dropbox.ensure_folder(path)
    except TenantProvisioningError:
        raise
    except Exception as exc:
        raise TenantProvisioningError(f"Couldn't create Dropbox folder '{path}': {exc}") from exc
    logger.info(f"Dropbox output folder {path} {'created' if created else 'already exists'}")


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
    allowed_emails: Optional[List[str]] = None,
    colors: Optional[ColorOverrides] = None,
    style_params_override: Optional[Dict] = None,
    tagline: Optional[str] = None,
    distribution_mode: str = "download_only",
    dropbox_path: Optional[str] = None,
    brand_prefix: Optional[str] = None,
    backgrounds: Optional[Dict[str, Tuple[bytes, str]]] = None,
    logo: Optional[Tuple[bytes, str]] = None,
    storage: Optional[StorageService] = None,
    domain_service: Optional[TenantDomainService] = None,
) -> TenantConfig:
    """
    Provision a new white-label tenant: theme + config in GCS, then its
    ``{id}.nomadkaraoke.com`` subdomain in Cloudflare. If the subdomain can't be
    provisioned the GCS side is rolled back, so a tenant either works end-to-end
    or doesn't exist.

    Args:
        name: Display name (e.g. "Randy Vild").
        tenant_id: Slug id; derived from name if omitted.
        subdomain: Full subdomain; must be ``{id}.nomadkaraoke.com`` (the portal
            edge function and backend middleware derive the tenant id from the
            first hostname label). Defaults to that.
        allowed_email_domains: Domains permitted to log in.
        allowed_emails: Individual addresses permitted to log in (e.g. a client's
            gmail). Both lists empty = open portal; admins can always sign in.
        colors: Lyric/title/artist colour overrides applied to the theme.
        tagline: Optional portal tagline.
        distribution_mode: "download_only" (default), "all", or "cloud_only".
        dropbox_path: If set, outputs are delivered to this Dropbox folder.
        brand_prefix: Output filename prefix (e.g. "RVILD").
        backgrounds: {field -> (bytes, ext)} for karaoke/intro/end backgrounds.
        logo: (bytes, ext) for the portal logo.
        storage: Injected StorageService (for tests).
        domain_service: Injected TenantDomainService (for tests).

    Returns:
        The persisted TenantConfig.

    Raises:
        TenantValidationError, TenantConflictError, TenantProvisioningError, ValueError.
    """
    name = (name or "").strip()
    if not name:
        raise TenantValidationError("Tenant name is required.")

    tenant_id = (tenant_id or slugify_tenant_id(name)).strip().lower()
    _validate_tenant_id(tenant_id)

    expected_subdomain = f"{tenant_id}.{BASE_DOMAIN}"
    subdomain = (subdomain or expected_subdomain).strip().lower()
    if subdomain != expected_subdomain:
        raise TenantValidationError(
            f"Subdomain must be '{expected_subdomain}' — the portal derives the tenant "
            "id from the first label of the hostname."
        )

    storage = storage or StorageService()
    domain_service = domain_service or get_tenant_domain_service()
    tenant_service = get_tenant_service()
    theme_service = get_theme_service()

    if tenant_service.tenant_exists(tenant_id):
        raise TenantConflictError(f"Tenant '{tenant_id}' already exists.")

    dropbox_path, brand_prefix = _normalize_delivery(dropbox_path, brand_prefix)
    if brand_prefix:
        _check_brand_prefix_available(brand_prefix, tenant_id, storage)

    theme_id = tenant_id  # 1:1 theme per tenant, mirrors the setup scripts

    # The tenant id doubles as the theme id, and theme writes are not create-only.
    # Guard against an id that collides with an existing theme (e.g. the default
    # 'nomad' theme) — otherwise provisioning would overwrite that theme's
    # style_params and break every job/tenant using it.
    if storage.file_exists(f"{THEMES_PREFIX}/{theme_id}/style_params.json"):
        raise TenantConflictError(
            f"A theme named '{theme_id}' already exists; choose a different tenant id."
        )

    # Fail fast (before any write) if the hostname is owned by a non-tenant
    # DNS record (e.g. an existing product subdomain) or Cloudflare is unusable.
    try:
        domain_service.check_available(subdomain)
    except TenantDomainConflictError as exc:
        raise TenantConflictError(str(exc)) from exc
    except TenantDomainError as exc:
        raise TenantProvisioningError(str(exc)) from exc

    # Create the Dropbox output folder up front (idempotent) so the first job
    # delivers there and the completion email carries a real folder link.
    if dropbox_path:
        _ensure_dropbox_folder(dropbox_path)

    # --- Derive theme from the default Nomad theme ---------------------------
    base_theme_id = theme_service.get_default_theme_id()
    if not base_theme_id:
        raise ValueError("No default theme is configured; cannot derive a tenant theme.")
    base_style = theme_service.get_theme_style_params(base_theme_id)
    if base_style is None:
        raise ValueError(f"Default theme '{base_theme_id}' style params could not be loaded.")

    # --- Build the tenant config (logo_url filled in after reservation) -------
    domains = _normalize_domains(allowed_email_domains)
    emails = _normalize_emails(allowed_emails)
    now = datetime.now(timezone.utc)

    branding = TenantBranding(
        logo_url=None,
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
            allowed_emails=emails,
            require_email_domain=bool(domains or emails),
            fixed_token_ids=[],
            sender_email=DEFAULT_SENDER_EMAIL,
        ),
        created_at=now,
        updated_at=now,
    )

    # --- Reserve the tenant id FIRST (atomic create-only write) --------------
    # This must happen before any theme/asset/logo write, so a concurrent or
    # retried create for an existing id fails immediately without clobbering
    # the live tenant's theme.
    config_path = f"{TENANTS_PREFIX}/{tenant_id}/config.json"
    try:
        storage.upload_json(config_path, config.model_dump(mode="json"), if_generation_match=0)
    except PreconditionFailed as exc:
        raise TenantConflictError(f"Tenant '{tenant_id}' already exists.") from exc

    # --- Build the theme (rollback the reservation if anything fails) --------
    try:
        _copy_default_theme_assets(storage, base_theme_id, theme_id)

        if style_params_override is not None:
            # Admin supplied a full theme document — authoritative. Default
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
                cache_control=NO_STORE_CACHE_CONTROL,
            )
            style_params.setdefault(section, {})["background_image"] = filename

        storage.upload_json(f"{THEMES_PREFIX}/{theme_id}/style_params.json", style_params)
        _register_theme_metadata(storage, theme_id, name)

        if logo:
            logo_data, logo_ext = logo
            logo_ext = logo_ext.lower().lstrip(".")
            logo_path = f"{TENANTS_PREFIX}/{tenant_id}/logo.{logo_ext}"
            storage.upload_fileobj(
                io.BytesIO(logo_data),
                logo_path,
                content_type=_content_type_for(logo_ext),
                cache_control=NO_STORE_CACHE_CONTROL,
            )
            config.branding.logo_url = f"gs://{settings.gcs_bucket_name}/{logo_path}"
            config.updated_at = datetime.now(timezone.utc)
            storage.upload_json(config_path, config.model_dump(mode="json"))
    except Exception:
        # Roll back the reservation so a failed create doesn't leave a
        # half-provisioned tenant behind.
        try:
            storage.delete_file(config_path, ignore_missing=True)
        except Exception:  # pragma: no cover - best effort
            logger.warning(f"Failed to roll back tenant reservation for '{tenant_id}'")
        raise

    tenant_service.invalidate_cache(tenant_id)
    theme_service.invalidate_cache()

    # --- Subdomain (Cloudflare Pages custom domain + CNAME) ------------------
    try:
        domain_service.provision(subdomain)
    except Exception as exc:
        logger.error(f"Provisioning subdomain {subdomain} failed; rolling back tenant '{tenant_id}': {exc}")
        try:
            _delete_tenant_storage(storage, tenant_id, theme_id)
        except Exception:  # pragma: no cover - best effort
            logger.exception(f"Rollback of tenant '{tenant_id}' storage failed")
        try:  # a half-provisioned Pages domain (CNAME failed) must not linger
            domain_service.deprovision(subdomain)
        except Exception:  # pragma: no cover - best effort
            logger.exception(f"Rollback of subdomain {subdomain} failed")
        tenant_service.invalidate_cache(tenant_id)
        theme_service.invalidate_cache()
        raise TenantProvisioningError(f"Could not set up {subdomain}: {exc}") from exc

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

    config_updates = dict(config_updates or {})
    new_subdomain = config_updates.pop("subdomain", None)  # never persisted as sent
    if new_subdomain is not None and str(new_subdomain).strip().lower() != config.subdomain:
        raise TenantValidationError(
            "The portal subdomain can't be changed (it is derived from the tenant id)."
        )

    # 1. Assets (uploaded files -> theme assets, keyed by their target basename)
    for name, (data, ext) in (assets or {}).items():
        safe = _safe_asset_name(name)
        storage.upload_fileobj(
            io.BytesIO(data),
            f"{THEMES_PREFIX}/{theme_id}/assets/{safe}",
            content_type=_content_type_for(ext),
            cache_control=NO_STORE_CACHE_CONTROL,
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
            io.BytesIO(logo_data),
            logo_path,
            content_type=_content_type_for(logo_ext),
            cache_control=NO_STORE_CACHE_CONTROL,
        )
        branding = dict(merged_updates.get("branding") or {})
        branding["logo_url"] = f"gs://{settings.gcs_bucket_name}/{logo_path}"
        merged_updates["branding"] = branding

    # 4. Dropbox delivery (path + prefix validated together; folder created;
    #    the feature flag always follows the path so the two can't drift)
    defaults_updates = merged_updates.get("defaults")
    if isinstance(defaults_updates, dict) and (
        "dropbox_path" in defaults_updates or "brand_prefix" in defaults_updates
    ):
        defaults_updates = dict(defaults_updates)
        dropbox_path, brand_prefix = _normalize_delivery(
            defaults_updates.get("dropbox_path", config.defaults.dropbox_path),
            defaults_updates.get("brand_prefix", config.defaults.brand_prefix),
        )
        if brand_prefix:
            _check_brand_prefix_available(brand_prefix, tenant_id, storage)
        if dropbox_path and dropbox_path != config.defaults.dropbox_path:
            _ensure_dropbox_folder(dropbox_path)
        defaults_updates["dropbox_path"] = dropbox_path
        defaults_updates["brand_prefix"] = brand_prefix
        merged_updates["defaults"] = defaults_updates
        features = dict(merged_updates.get("features") or {})
        features["dropbox_upload"] = bool(dropbox_path)
        merged_updates["features"] = features

    # 5. Config merge (access lists normalized; any allowlist => enforced)
    auth_updates = merged_updates.get("auth")
    if isinstance(auth_updates, dict):
        auth_updates = dict(auth_updates)
        if "allowed_emails" in auth_updates:
            auth_updates["allowed_emails"] = _normalize_emails(auth_updates["allowed_emails"])
        if "allowed_email_domains" in auth_updates:
            auth_updates["allowed_email_domains"] = _normalize_domains(
                auth_updates["allowed_email_domains"]
            )
        merged_updates["auth"] = auth_updates
    if merged_updates:
        config = _merge_config(config, merged_updates)
    if isinstance(auth_updates, dict) and "require_email_domain" not in auth_updates:
        config.auth.require_email_domain = bool(
            config.auth.allowed_email_domains or config.auth.allowed_emails
        )

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
        if not data.get("id"):
            # A config without an id can't be addressed by the UI (would produce
            # a null React key and a request to /api/admin/tenants/null) — skip.
            logger.warning(f"Skipping tenant config without an id: {path}")
            continue
        defaults = data.get("defaults") or {}
        summaries.append(
            {
                "id": data.get("id"),
                "name": data.get("name"),
                "subdomain": data.get("subdomain"),
                "is_active": data.get("is_active", True),
                "locked_theme": defaults.get("locked_theme"),
                "theme_id": defaults.get("theme_id"),
                "dropbox_path": defaults.get("dropbox_path"),
                "brand_prefix": defaults.get("brand_prefix"),
                "created_at": data.get("created_at"),
            }
        )
    summaries.sort(key=lambda t: str(t.get("name") or t.get("id") or "").lower())
    return summaries


def _unregister_theme_metadata(storage: StorageService, theme_id: str) -> None:
    """Remove a theme from themes/_metadata.json (read-modify-write, idempotent)."""
    try:
        registry = storage.download_json(METADATA_FILE)
    except Exception:
        return
    themes = registry.get("themes") or []
    kept = [t for t in themes if t.get("id") != theme_id]
    if len(kept) != len(themes):
        registry["themes"] = kept
        storage.upload_json(METADATA_FILE, registry)


def _delete_tenant_storage(storage: StorageService, tenant_id: str, theme_id: Optional[str]) -> None:
    """Delete a tenant's theme (files + registry entry) and config/logo from GCS."""
    if theme_id:
        _unregister_theme_metadata(storage, theme_id)
        storage.delete_folder(f"{THEMES_PREFIX}/{theme_id}/")
    # Config last: while it exists the tenant is still addressable for a retry.
    storage.delete_folder(f"{TENANTS_PREFIX}/{tenant_id}/")


def delete_tenant(
    tenant_id: str,
    *,
    storage: Optional[StorageService] = None,
    domain_service: Optional[TenantDomainService] = None,
) -> None:
    """
    Delete a tenant completely: its subdomain (Cloudflare Pages domain + CNAME),
    its theme (assets, style_params, registry entry) and its config/logo.

    Jobs already created for the tenant are kept (finished outputs stay
    downloadable) but can no longer be re-rendered with the deleted theme.

    The theme is kept if it is the default theme or another tenant still uses it.

    Raises:
        TenantNotFoundError, TenantProvisioningError (nothing in GCS is deleted
        if the subdomain can't be removed, so the delete can simply be retried).
    """
    storage = storage or StorageService()
    domain_service = domain_service or get_tenant_domain_service()
    tenant_service = get_tenant_service()
    theme_service = get_theme_service()

    config = tenant_service.get_tenant_config(tenant_id, force_refresh=True)
    if not config:
        raise TenantNotFoundError(f"Tenant '{tenant_id}' not found.")

    # Always the canonical host (subdomain changes are rejected, but legacy
    # configs could differ) — deprovision only ever touches OUR records.
    hostname = TenantDomainService.hostname_for(tenant_id)
    try:
        domain_service.deprovision(hostname)
    except TenantDomainError as exc:
        raise TenantProvisioningError(f"Could not remove {hostname}: {exc}") from exc

    # Delete the tenant's own 1:1 theme (named after the tenant — the one
    # create_tenant made) even if the tenant has since switched to another
    # theme; never delete a theme another tenant uses or the default theme.
    theme_id: Optional[str] = tenant_id
    shared = any(
        t.get("id") != tenant_id and theme_id in (t.get("locked_theme"), t.get("theme_id"))
        for t in list_tenants(storage)
    )
    if shared or theme_id == theme_service.get_default_theme_id():
        logger.info(f"Keeping theme '{theme_id}' while deleting tenant '{tenant_id}'")
        theme_id = None

    _delete_tenant_storage(storage, tenant_id, theme_id)
    # delete_folder swallows errors — verify, so a failed delete isn't a 204.
    if storage.file_exists(f"{TENANTS_PREFIX}/{tenant_id}/config.json"):
        raise TenantProvisioningError(
            f"Subdomain removed but tenant '{tenant_id}' storage could not be deleted; retry the delete."
        )
    tenant_service.invalidate_cache(tenant_id)
    theme_service.invalidate_cache()
    logger.info(f"Deleted tenant '{tenant_id}' (subdomain '{hostname}', theme '{theme_id}')")


def get_tenant_domain_status(
    tenant_id: str, *, domain_service: Optional[TenantDomainService] = None
) -> Optional[Dict[str, object]]:
    """Cloudflare provisioning state of the tenant's subdomain (None if unavailable)."""
    domain_service = domain_service or get_tenant_domain_service()
    try:
        return domain_service.status(TenantDomainService.hostname_for(tenant_id)).to_dict()
    except TenantDomainError as exc:
        logger.warning(f"Could not read domain status for tenant '{tenant_id}': {exc}")
        return None


def provision_tenant_domain(
    tenant_id: str, *, domain_service: Optional[TenantDomainService] = None
) -> Dict[str, object]:
    """(Re)provision an existing tenant's subdomain — for tenants created before
    automation, or to retry after a transient Cloudflare failure."""
    domain_service = domain_service or get_tenant_domain_service()
    if not get_tenant_service().tenant_exists(tenant_id):
        raise TenantNotFoundError(f"Tenant '{tenant_id}' not found.")
    hostname = TenantDomainService.hostname_for(tenant_id)
    try:
        return domain_service.provision(hostname).to_dict()
    except TenantDomainConflictError as exc:
        raise TenantConflictError(str(exc)) from exc
    except TenantDomainError as exc:
        raise TenantProvisioningError(str(exc)) from exc
