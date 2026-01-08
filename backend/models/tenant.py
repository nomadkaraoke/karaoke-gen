"""
Tenant data models for white-label B2B portals.

Tenants are B2B customers who get their own branded portal with custom
configuration. Each tenant has:
- Custom branding (logo, colors, site title)
- Feature flags (enable/disable audio search, distribution, etc.)
- Default settings (theme, distribution mode)
- Auth configuration (allowed email domains, fixed tokens)

Tenant configs are stored in GCS at tenants/{tenant_id}/config.json
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class TenantBranding(BaseModel):
    """Branding configuration for a tenant's portal."""

    logo_url: Optional[str] = Field(
        None, description="URL to tenant's logo image (PNG with transparency preferred)"
    )
    logo_height: int = Field(40, description="Logo height in pixels for header display")
    primary_color: str = Field(
        "#ff5bb8", description="Primary brand color (hex format)"
    )
    secondary_color: str = Field(
        "#8b5cf6", description="Secondary brand color (hex format)"
    )
    accent_color: Optional[str] = Field(
        None, description="Accent color for highlights (hex format)"
    )
    background_color: Optional[str] = Field(
        None, description="Custom background color (defaults to dark theme)"
    )
    favicon_url: Optional[str] = Field(None, description="URL to custom favicon")
    site_title: str = Field(
        "Karaoke Generator", description="Browser tab title"
    )
    tagline: Optional[str] = Field(
        None, description="Optional tagline shown below logo"
    )


class TenantFeatures(BaseModel):
    """Feature flags controlling what's available in the tenant's portal."""

    # Audio input methods
    audio_search: bool = Field(
        True, description="Enable audio search (flacfetch integration)"
    )
    file_upload: bool = Field(True, description="Enable direct file upload")
    youtube_url: bool = Field(True, description="Enable YouTube URL input")

    # Distribution options
    youtube_upload: bool = Field(True, description="Enable YouTube upload option")
    dropbox_upload: bool = Field(True, description="Enable Dropbox upload option")
    gdrive_upload: bool = Field(True, description="Enable Google Drive upload option")

    # Customization options
    theme_selection: bool = Field(
        True, description="Allow user to select theme (false = use default)"
    )
    color_overrides: bool = Field(
        True, description="Allow user to customize colors"
    )

    # Output formats
    enable_cdg: bool = Field(True, description="Generate CDG format output")
    enable_4k: bool = Field(True, description="Generate 4K video output")

    # Advanced features
    admin_access: bool = Field(
        False, description="Allow access to admin dashboard"
    )


class TenantDefaults(BaseModel):
    """Default settings applied to all jobs for this tenant."""

    theme_id: Optional[str] = Field(
        None, description="Default theme ID (required if theme_selection=false)"
    )
    locked_theme: Optional[str] = Field(
        None,
        description="If set, users cannot change theme - always uses this theme ID"
    )
    distribution_mode: str = Field(
        "all",
        description="Distribution mode: 'all', 'download_only', or 'cloud_only'"
    )
    brand_prefix: Optional[str] = Field(
        None, description="Prefix for output filenames (e.g., 'VSTAR')"
    )
    youtube_description_template: Optional[str] = Field(
        None, description="Default YouTube description template"
    )


class TenantAuth(BaseModel):
    """Authentication configuration for a tenant."""

    allowed_email_domains: List[str] = Field(
        default_factory=list,
        description="Email domains allowed for magic link auth (e.g., ['vocal-star.com'])"
    )
    require_email_domain: bool = Field(
        True,
        description="If true, only allowed domains can sign up. If false, domains get auto-approved."
    )
    fixed_token_ids: List[str] = Field(
        default_factory=list,
        description="IDs of fixed API tokens for this tenant (tokens stored in auth_tokens)"
    )
    sender_email: Optional[str] = Field(
        None,
        description="Email sender address for this tenant (e.g., 'vocalstar@nomadkaraoke.com')"
    )


class TenantConfig(BaseModel):
    """Complete configuration for a white-label tenant."""

    id: str = Field(..., description="Unique tenant identifier (e.g., 'vocalstar')")
    name: str = Field(..., description="Display name (e.g., 'Vocal Star')")
    subdomain: str = Field(
        ..., description="Full subdomain (e.g., 'vocalstar.nomadkaraoke.com')"
    )
    is_active: bool = Field(True, description="Whether tenant portal is active")

    branding: TenantBranding = Field(default_factory=TenantBranding)
    features: TenantFeatures = Field(default_factory=TenantFeatures)
    defaults: TenantDefaults = Field(default_factory=TenantDefaults)
    auth: TenantAuth = Field(default_factory=TenantAuth)

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def get_sender_email(self) -> str:
        """Get the email sender address for this tenant."""
        if self.auth.sender_email:
            return self.auth.sender_email
        # Default pattern: {tenant_id}@nomadkaraoke.com
        return f"{self.id}@nomadkaraoke.com"

    def is_email_allowed(self, email: str) -> bool:
        """Check if an email address is allowed for this tenant."""
        if not self.auth.allowed_email_domains:
            # No domain restrictions
            return True

        email_lower = email.lower()
        for domain in self.auth.allowed_email_domains:
            if email_lower.endswith(f"@{domain.lower()}"):
                return True

        return not self.auth.require_email_domain


class TenantPublicConfig(BaseModel):
    """
    Public tenant configuration returned to frontend.

    This excludes sensitive auth details like token IDs.
    """

    id: str
    name: str
    subdomain: str
    is_active: bool
    branding: TenantBranding
    features: TenantFeatures
    defaults: TenantDefaults
    # Auth info limited to what frontend needs
    allowed_email_domains: List[str] = Field(default_factory=list)

    @classmethod
    def from_config(cls, config: TenantConfig) -> "TenantPublicConfig":
        """Create public config from full config."""
        return cls(
            id=config.id,
            name=config.name,
            subdomain=config.subdomain,
            is_active=config.is_active,
            branding=config.branding,
            features=config.features,
            defaults=TenantDefaults(
                theme_id=config.defaults.theme_id,
                locked_theme=config.defaults.locked_theme,
                distribution_mode=config.defaults.distribution_mode,
                # Don't expose brand_prefix or youtube_description_template
            ),
            allowed_email_domains=config.auth.allowed_email_domains,
        )


class TenantConfigResponse(BaseModel):
    """Response from GET /api/tenant/config endpoint."""

    tenant: Optional[TenantPublicConfig] = Field(
        None, description="Tenant config if found, null for default Nomad Karaoke"
    )
    is_default: bool = Field(
        True, description="True if using default Nomad Karaoke config"
    )
