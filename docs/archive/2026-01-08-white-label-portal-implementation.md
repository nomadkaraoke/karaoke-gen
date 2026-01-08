# White-Label B2B Portal Implementation

**Date**: 2026-01-08
**Status**: Complete
**First Tenant**: Vocal Star

## Overview

Implemented multi-tenant white-label portal infrastructure allowing B2B customers to have their own branded karaoke generation experience at custom subdomains.

## First Customer: Vocal Star

**Requirements**:
- Subdomain: `vocalstar.nomadkaraoke.com`
- File upload only (no audio search, no YouTube URL input)
- Download only distribution (no YouTube/Dropbox/GDrive upload)
- Theme locked to "vocalstar" (yellow/blue color scheme)
- Auth restricted to `@vocal-star.com` and `@vocalstarmusic.com` email domains
- Custom branding: logo, colors, site title, tagline

## Architecture

### Tenant Detection Flow

```
Request → Frontend detectTenantFromUrl() → Backend TenantMiddleware → TenantService → GCS Config
```

**Frontend** (`frontend/lib/tenant.ts`):
- Zustand store for tenant state
- `detectTenantFromUrl()` extracts subdomain from hostname
- Strict patterns: `{tenant}.nomadkaraoke.com` or `{tenant}.gen.nomadkaraoke.com`
- Query param `?tenant=X` for local development only

**Backend** (`backend/middleware/tenant.py`):
- `TenantMiddleware` extracts tenant from:
  1. `X-Tenant-ID` header (from frontend)
  2. Query param (dev only - disabled in production via `IS_PRODUCTION` check)
  3. Host header subdomain
- Attaches `tenant_id` and `tenant_config` to `request.state`

**Config Storage** (`backend/services/tenant_service.py`):
- Loads from GCS: `tenants/{tenant_id}/config.json`
- In-memory cache with 5-minute TTL
- Signed URL generation for logos

### GCS Structure

```
tenants/{tenant_id}/
├── config.json          # TenantConfig
└── logo.jpg             # Tenant logo

themes/{theme_id}/
├── style_params.json    # Theme configuration
└── assets/
    ├── intro_background.png
    ├── karaoke_background.jpg
    ├── end_background.png
    ├── Oswald-SemiBold.ttf
    ├── cdg_instrumental_background.gif
    └── cdg_title_background.gif
```

### Config Schema

```python
TenantConfig:
  id: str
  name: str
  subdomain: str
  is_active: bool
  branding: TenantBranding
    logo_url: str | None
    logo_height: int
    primary_color: str
    secondary_color: str
    accent_color: str | None
    background_color: str | None
    favicon_url: str | None
    site_title: str
    tagline: str | None
  features: TenantFeatures
    audio_search: bool
    file_upload: bool
    youtube_url: bool
    youtube_upload: bool
    dropbox_upload: bool
    gdrive_upload: bool
    theme_selection: bool
    color_overrides: bool
    enable_cdg: bool
    enable_4k: bool
    admin_access: bool
  defaults: TenantDefaults
    theme_id: str | None
    locked_theme: str | None
    distribution_mode: str
  auth: TenantAuth
    allowed_email_domains: list[str]
    require_email_domain: bool
    sender_email: str | None
```

## Implementation Details

### Backend Changes

1. **Tenant Models** (`backend/models/tenant.py`):
   - Pydantic models for config schema
   - `locked_theme` field for forcing specific theme

2. **Tenant Service** (`backend/services/tenant_service.py`):
   - GCS config loading with caching
   - Logo signed URL generation
   - `tenant_exists()` check for subdomain validation

3. **Tenant API** (`backend/api/routes/tenant.py`):
   - `GET /api/tenant/config` - auto-detect or explicit tenant
   - `GET /api/tenant/config/{tenant_id}` - specific tenant lookup

4. **Middleware** (`backend/middleware/tenant.py`):
   - Priority-based tenant detection
   - Production security: query param disabled
   - `get_tenant_from_request()` helper

5. **Auth Updates** (`backend/services/auth_service.py`):
   - Email domain validation for tenant-restricted auth
   - Sender email override from tenant config

6. **Feature Enforcement**:
   - Audio search endpoint checks `features.audio_search`
   - Returns 403 if feature disabled for tenant

7. **Job/User Scoping**:
   - `tenant_id` field added to `JobCreate`
   - Jobs created with tenant context from request

### Frontend Changes

1. **Tenant Store** (`frontend/lib/tenant.ts`):
   - Zustand store with tenant state
   - `useTenant()` hook for components
   - Default Nomad Karaoke branding fallback
   - CSS variable application via `applyTenantBranding()`

2. **TenantProvider** (`frontend/components/tenant-provider.tsx`):
   - Wraps app in layout
   - Fetches config on mount
   - Prevents hydration issues (no module-level init)

3. **TenantLogo** (`frontend/components/tenant-logo.tsx`):
   - Dynamic logo based on tenant
   - Falls back to Nomad Karaoke logo
   - Supports GCS signed URLs for tenant logos

4. **Feature-Flagged UI** (`frontend/components/job/JobSubmission.tsx`):
   - Tabs conditionally rendered based on `features`
   - Active tab sync when features change
   - `useMemo` for available tabs list

5. **Locked Themes**:
   - `defaults.locked_theme` hides theme selector
   - Uses locked theme for all jobs

6. **Output Links**:
   - Filtered based on `features.youtube_upload`, `features.dropbox_upload`, etc.

### Security Measures

1. **Query param disabled in production**:
   ```python
   IS_PRODUCTION = os.environ.get("ENV", "").lower() == "production"
   if not IS_PRODUCTION:
       query_tenant = request.query_params.get("tenant")
   ```

2. **Strict subdomain patterns**:
   ```typescript
   const isValidPattern =
     parts.length === 3 ||
     (parts.length === 4 && parts[1] === "gen")
   ```

3. **PII protection in logs**:
   ```python
   def _mask_email(email: str) -> str:
       # "andrew@vocal-star.com" → "an***@vo***.com"
   ```

4. **is_active check**: Inactive tenants treated as default

## Setup Script

`scripts/setup-vocalstar-tenant.py`:
- Uploads theme assets to GCS
- Creates theme config (`style_params.json`)
- Uploads tenant logo
- Creates tenant config (`config.json`)
- Updates theme registry

**Usage**:
```bash
# Set resources path (or pass as argument)
export VOCALSTAR_RESOURCES=/path/to/VocalStar/Resources
python scripts/setup-vocalstar-tenant.py

# Or with argument
python scripts/setup-vocalstar-tenant.py /path/to/VocalStar/Resources
```

## Files Changed

### Backend
- `backend/models/tenant.py` - New
- `backend/services/tenant_service.py` - New
- `backend/api/routes/tenant.py` - New
- `backend/middleware/tenant.py` - New
- `backend/main.py` - Added middleware and routes
- `backend/models/job.py` - Added tenant_id field
- `backend/services/auth_service.py` - Email domain validation
- `backend/api/routes/users.py` - PII masking in logs
- `backend/api/routes/audio_search.py` - Feature check

### Frontend
- `frontend/lib/tenant.ts` - New
- `frontend/components/tenant-provider.tsx` - New
- `frontend/components/tenant-logo.tsx` - New
- `frontend/components/app-layout.tsx` - TenantProvider wrapper
- `frontend/components/job/JobSubmission.tsx` - Feature flags
- `frontend/components/job/OutputLinks.tsx` - Distribution filtering

### Scripts
- `scripts/setup-vocalstar-tenant.py` - New

## Testing

- Frontend build verified (TypeScript compilation)
- CodeRabbit review: 3 cycles, all issues fixed
- Manual testing with `?tenant=vocalstar` query param

### Unit Tests Added

**Backend tests (110 tests)**:
- `test_tenant_models.py` - 28 tests for Pydantic models (TenantConfig, branding, features, defaults, auth, public config, `is_email_allowed()`, `get_sender_email()`, `from_config()`)
- `test_tenant_service.py` - 30 tests for TenantService (config loading, caching, subdomain resolution)
- `test_tenant_middleware.py` - 35 tests for TenantMiddleware (header/query param/subdomain detection, NON_TENANT_SUBDOMAINS filtering)
- `test_tenant_api.py` - 17 tests for `/api/tenant/config` endpoints

**Frontend tests (22 tests)**:
- `tenant.test.ts` - Tests for Zustand store, `getTenantHeaders()`, `isFeatureEnabled()`

**Key testing patterns**:
- API route tests must patch `get_tenant_service` in BOTH routes AND middleware modules since middleware runs first
- Zustand tests use `useTenant.getState().setTenant()` instead of direct `setState()` to properly handle computed properties
- `test_job_creation_regression.py` fixed to mock `request.state.tenant_config = None`

## Next Steps

1. Configure DNS: CNAME for `vocalstar.nomadkaraoke.com`
2. Update Cloudflare Pages for subdomain
3. Run setup script to upload Vocal Star assets
4. Test at `https://vocalstar.nomadkaraoke.com`

## Lessons Learned

1. **Config-driven multitenancy** scales better than code branches
2. **Query params for dev, headers for prod** prevents spoofing
3. **Feature flags belong in config**, not environment variables
4. **Zustand + Next.js**: Don't auto-init at module level, use useEffect
5. **Tab state must react to feature changes** - sync active tab with available tabs
