# Plan: White Label B2B Karaoke Portals

**Created:** 2026-01-08
**Branch:** feat/sess-20260108-0358-white-label-portal
**Status:** Draft

## Overview

Add multi-tenant white label portal capability to karaoke-gen, enabling B2B customers like Vocal Star to access a customized version of the generator app on their own subdomain with their branding, theme, and restricted feature set.

**First customer: Vocal Star**
- Subdomain: `vocalstar.nomadkaraoke.com`
- Workflow: Upload original + instrumental audio (no audio search)
- Theme: Custom Vocal Star karaoke theme (already exists as JSON)
- Distribution: Download only (no Dropbox/GDrive/YouTube)
- Auth: Magic link for `@vocal-star.com` / `@vocalstarmusic.com` domains, plus fixed API token option

## Requirements

### Functional Requirements
- [ ] Tenant identification via subdomain (e.g., `vocalstar.nomadkaraoke.com`)
- [ ] Per-tenant frontend branding (logo, colors, metadata)
- [ ] Per-tenant feature flags (hide audio search, hide distribution options)
- [ ] Per-tenant default theme (always use Vocal Star theme)
- [ ] Per-tenant allowed email domains for magic link auth
- [ ] Per-tenant fixed API token support (for automation)
- [ ] Jobs scoped to tenant (Vocal Star users only see Vocal Star jobs)
- [ ] Users scoped to tenant

### Non-Functional Requirements
- [ ] Minimal code duplication - single codebase serves all tenants
- [ ] Easy to add new tenants via config, not code changes
- [ ] Graceful fallback - default to Nomad Karaoke if no tenant match
- [ ] No performance impact on existing single-tenant flow

## Technical Approach

### Architecture Decision: Config-Driven Multi-Tenancy

Rather than building a full tenant management system (overkill for ~1 customer/year), we'll use a **config-driven approach**:

1. **Tenant configs stored in GCS** at `tenants/{tenant_id}/config.json`
2. **Frontend detects subdomain** and fetches tenant config from API
3. **Backend middleware** extracts tenant from request and applies config
4. **Firestore collections** get `tenant_id` field for isolation

This approach:
- Requires no database migrations for new tenants
- Configs can be managed via simple JSON files
- Easy to test locally with query param override (`?tenant=vocalstar`)
- Can evolve to admin UI later if needed

### Tenant Config Schema

```json
{
  "id": "vocalstar",
  "name": "Vocal Star",
  "subdomain": "vocalstar.nomadkaraoke.com",
  "branding": {
    "logo_url": "gs://karaoke-gen-storage/tenants/vocalstar/logo.png",
    "primary_color": "#ffff00",
    "secondary_color": "#006CF9",
    "favicon_url": "gs://karaoke-gen-storage/tenants/vocalstar/favicon.ico",
    "site_title": "Vocal Star Karaoke Generator"
  },
  "features": {
    "audio_search": false,
    "file_upload": true,
    "youtube_upload": false,
    "dropbox_upload": false,
    "gdrive_upload": false,
    "theme_selection": false
  },
  "defaults": {
    "theme_id": "vocalstar",
    "distribution_mode": "download_only"
  },
  "auth": {
    "allowed_email_domains": ["vocal-star.com", "vocalstarmusic.com"],
    "fixed_tokens": ["<will be in Secret Manager>"]
  }
}
```

### Key Implementation Details

#### 1. Subdomain Detection (Frontend)
- Next.js middleware checks `request.headers.host`
- If subdomain matches tenant, store tenant ID in cookie/context
- Fetch `/api/tenant/config` to get branding/features
- Apply CSS variables dynamically for colors
- Swap logo component based on config

#### 2. Tenant Middleware (Backend)
- FastAPI middleware extracts tenant from:
  - `X-Tenant-ID` header (set by frontend)
  - Or subdomain from `Host` header
  - Or query param `?tenant=` (dev only)
- Attaches `tenant_id` to request state
- Tenant config loaded from GCS (cached 5 min like themes)

#### 3. Auth Enhancement
- `AuthService.validate_token_full()` already returns `user_email`
- Add tenant validation: check if user's email domain is in tenant's allowed list
- Support fixed tokens per tenant (stored in `auth_tokens` with `tenant_id`)
- Magic links include tenant context for proper redirect

#### 4. Data Isolation
- Add `tenant_id: Optional[str]` to User and Job models
- Default to `None` for existing Nomad Karaoke users (backwards compatible)
- Query filters: `where('tenant_id', '==', tenant_id or None)`
- No schema migration needed - new field, optional

#### 5. Theme Integration
- Upload Vocal Star theme to `themes/vocalstar/` in GCS
- Upload assets (backgrounds, fonts) to `themes/vocalstar/assets/`
- Tenant config specifies `default_theme_id: "vocalstar"`
- When `theme_selection: false`, UI hides theme picker and uses default

## Implementation Steps

### Phase 1: Backend Tenant Foundation (Day 1-2)

1. [ ] **Create tenant model and config loader**
   - Create `backend/models/tenant.py` with `TenantConfig`, `TenantBranding`, `TenantFeatures`
   - Create `backend/services/tenant_service.py` with GCS config loading and caching
   - Add `GET /api/tenant/config` endpoint (unauthenticated, returns public config)
   - Add `GET /api/tenant/config/{tenant_id}` for explicit tenant fetch

2. [ ] **Add tenant middleware**
   - Create `backend/api/middleware/tenant.py`
   - Extract tenant from subdomain or header
   - Attach to `request.state.tenant_id` and `request.state.tenant_config`
   - Add `get_tenant()` dependency for routes

3. [ ] **Update data models**
   - Add `tenant_id: Optional[str] = None` to `User` model
   - Add `tenant_id: Optional[str] = None` to `Job` model
   - Update Firestore indexes in Pulumi for tenant-scoped queries

4. [ ] **Update auth for tenant awareness**
   - Modify `AuthService` to validate email domain against tenant config
   - Add tenant context to magic link generation
   - Support per-tenant fixed tokens in `auth_tokens` collection

### Phase 2: Upload Vocal Star Theme (Day 2)

5. [ ] **Prepare Vocal Star theme for GCS**
   - Convert local paths in `karaoke-prep-styles-vocalstar.json` to GCS paths
   - Upload background images to `themes/vocalstar/assets/`
   - Upload Oswald font to `themes/vocalstar/assets/`
   - Upload `style_params.json` to `themes/vocalstar/`
   - Create preview image and upload to `themes/vocalstar/preview.png`
   - Add Vocal Star to `themes/_metadata.json`

6. [ ] **Create Vocal Star tenant config**
   - Create `tenants/vocalstar/config.json` in GCS
   - Upload Vocal Star logo to `tenants/vocalstar/logo.png`
   - Upload favicon to `tenants/vocalstar/favicon.ico`

### Phase 3: Frontend Tenant Support (Day 3-4)

7. [ ] **Add tenant context provider**
   - Create `frontend/lib/tenant.ts` with tenant config types
   - Create `frontend/hooks/useTenant.ts` for tenant state
   - Create `TenantProvider` component that fetches config on mount
   - Detect subdomain in `_app.tsx` or middleware

8. [ ] **Dynamic branding**
   - Create `TenantLogo` component that renders tenant or default logo
   - Update `globals.css` to support CSS variable overrides
   - Add `applyTenantColors()` function to set CSS variables from config
   - Update page metadata (title, favicon) from tenant config

9. [ ] **Feature-flagged UI**
   - Wrap audio search in `{tenant.features.audio_search && ...}`
   - Wrap theme selector in `{tenant.features.theme_selection && ...}`
   - Wrap distribution options based on tenant features
   - Show simplified form for Vocal Star (just file upload fields)

10. [ ] **Update magic link flow**
    - Pass tenant context when requesting magic link
    - Magic link redirect goes to tenant subdomain
    - Verify endpoint extracts tenant from link context

### Phase 4: Backend Feature Enforcement (Day 4-5)

11. [ ] **Enforce tenant features in API**
    - Audio search endpoint: return 403 if `audio_search: false`
    - File upload: check `file_upload: true`
    - Distribution settings: ignore/error if disabled features requested
    - Theme selection: use default if `theme_selection: false`

12. [ ] **Job creation with tenant context**
    - `JobCreate` handler sets `tenant_id` from request state
    - Distribution config respects tenant defaults
    - Theme defaults to tenant's default theme

13. [ ] **Query scoping**
    - Update `JobManager.get_jobs()` to filter by tenant_id
    - Update `UserService` queries to include tenant_id
    - Admin endpoints bypass tenant filter (see all tenants)

### Phase 5: Infrastructure & Deployment (Day 5-6)

14. [ ] **DNS setup**
    - Add `vocalstar.nomadkaraoke.com` CNAME to Cloudflare
    - Configure Cloudflare Pages to accept wildcard subdomain
    - Add SSL certificate for subdomain

15. [ ] **Backend CORS update**
    - Add `vocalstar.nomadkaraoke.com` to allowed origins
    - Or use pattern matching for `*.nomadkaraoke.com`

16. [ ] **Pulumi updates**
    - Add Firestore indexes for tenant_id queries
    - Add any new secrets (tenant fixed tokens)

### Phase 6: Testing & Documentation (Day 6-7)

17. [ ] **Testing**
    - Unit tests for TenantService config loading
    - Unit tests for tenant middleware
    - E2E test for Vocal Star flow (upload audio, generate, download)
    - Test magic link works for @vocal-star.com email

18. [ ] **Documentation**
    - Update CLAUDE.md with tenant info
    - Create `docs/WHITE-LABEL.md` with tenant setup guide
    - Document how to add new tenants

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `backend/models/tenant.py` | Create | Tenant config models |
| `backend/services/tenant_service.py` | Create | Tenant config loading/caching |
| `backend/api/routes/tenant.py` | Create | Tenant config API endpoint |
| `backend/api/middleware/tenant.py` | Create | Tenant extraction middleware |
| `backend/api/dependencies.py` | Modify | Add `get_tenant()` dependency |
| `backend/models/user.py` | Modify | Add `tenant_id` field |
| `backend/models/job.py` | Modify | Add `tenant_id` field |
| `backend/services/auth_service.py` | Modify | Tenant email domain validation |
| `backend/services/user_service.py` | Modify | Tenant-aware user queries |
| `backend/services/job_manager.py` | Modify | Tenant-aware job queries |
| `backend/api/routes/file_upload.py` | Modify | Tenant feature enforcement |
| `backend/api/routes/audio_search.py` | Modify | Tenant feature enforcement |
| `backend/main.py` | Modify | Add tenant middleware |
| `frontend/lib/tenant.ts` | Create | Tenant types and API |
| `frontend/hooks/useTenant.ts` | Create | Tenant context hook |
| `frontend/components/TenantProvider.tsx` | Create | Tenant context provider |
| `frontend/components/TenantLogo.tsx` | Create | Dynamic logo component |
| `frontend/app/layout.tsx` | Modify | Add TenantProvider |
| `frontend/app/globals.css` | Modify | CSS variable support for tenant colors |
| `frontend/app/app/page.tsx` | Modify | Feature-flag UI sections |
| `infrastructure/__main__.py` | Modify | Add tenant Firestore indexes |
| `docs/WHITE-LABEL.md` | Create | Tenant setup documentation |

## Testing Strategy

### Unit Tests
- `TenantService`: Config loading, caching, validation
- `TenantMiddleware`: Subdomain extraction, header extraction
- `AuthService`: Tenant email domain validation
- `JobManager`: Tenant-scoped queries

### Integration Tests (Emulator)
- Create job with tenant context
- Query jobs filtered by tenant
- Auth with tenant-allowed email domain
- Auth rejection for wrong email domain

### E2E Tests
- Vocal Star happy path: Upload audio files, review lyrics, download video
- Auth flow: Magic link for @vocal-star.com, verify redirect to subdomain
- Feature hiding: Verify audio search not shown on Vocal Star portal

## Open Questions

- [x] **Q: Should tenant configs be in GCS or Firestore?**
  A: GCS - simpler, matches theme pattern, easy to edit JSON directly

- [x] **Q: How to handle localhost development for specific tenant?**
  A: Query param override `?tenant=vocalstar` for local testing

- [x] **Q: Should admin portal be tenant-aware?**
  A: No - admins see all tenants. Admin routes bypass tenant filter.

- [x] **Q: Do we need tenant-specific email sender identity?**
  A: Yes - use `{tenant}@nomadkaraoke.com` (e.g., `vocalstar@nomadkaraoke.com`). Catchall is configured.

- [x] **Q: Vocal Star logo and favicon - do you have these assets?**
  A: Logo provided as `vocalstar-logo.jpg` - yellow star, white text on black. Will convert to PNG with transparency for web use.

## Rollback Plan

Since this is additive (new optional `tenant_id` field), rollback is straightforward:

1. Revert frontend to not check tenant
2. Revert backend middleware to not extract tenant
3. Existing data unaffected (tenant_id = null = Nomad Karaoke)
4. No schema migration to reverse

For DNS: Simply remove CNAME record if needed.

## Success Criteria

- [ ] Vocal Star user can access `vocalstar.nomadkaraoke.com`
- [ ] Portal shows Vocal Star branding (logo, colors)
- [ ] Audio search is hidden; only file upload available
- [ ] Theme is locked to Vocal Star style
- [ ] Distribution options hidden; download only
- [ ] Magic link works for @vocal-star.com emails
- [ ] Fixed API token works for automation
- [ ] Generated videos use Vocal Star theme
- [ ] Nomad Karaoke portal unchanged at gen.nomadkaraoke.com
