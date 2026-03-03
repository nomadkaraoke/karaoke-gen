# Singa Whitelabel Tenant Setup - 2026-03-03

## Summary

Added Singa (singa.com) as the second B2B whitelabel tenant on the karaoke generation platform, following Vocal Star. Also fixed critical infrastructure bugs in the tenant system and migrated frontend CI deployment from GitHub Pages to Cloudflare Pages.

## Key Changes

- **New tenant: Singa** — Green/black branding, file-upload-only mode, locked to "singa" theme, restricted to @singa.com emails
- **Setup script** (`scripts/setup-singa-tenant.py`) — Generates all branding assets programmatically with Pillow (4K backgrounds, CDG backgrounds, logo), downloads Inter font, creates theme and tenant configs, uploads everything to GCS
- **TenantLandingPage component** — Minimal landing page for tenant portals showing only logo + sign-in button + domain hint
- **Zustand getter bug fix** — `Object.assign` during `set()` was converting getter properties to stale static values (see LESSONS-LEARNED.md)
- **React hydration error #300 fix** — Conditional return placed between hooks violated Rules of Hooks
- **gs:// URL conversion** — Frontend fallback converts legacy GCS paths to backend asset proxy endpoint
- **Cloudflare Pages `_headers`** — Cache control for proper CDN behavior
- **CORS update** — Added `singa.nomadkaraoke.com` to GCS CORS origins
- **wrangler.toml format fix** — Corrected TOML syntax

## Decisions Made

- **Inter font instead of BasierSquare** — Singa's proprietary font isn't available; Inter SemiBold is a close open-source match
- **Programmatic asset generation** — All backgrounds generated with Pillow, no external design files needed. This makes the setup script fully self-contained.
- **Backend asset proxy for logos** — Tenant logos served via `GET /api/tenant/asset/{tenant_id}/{filename}` rather than direct GCS URLs, avoiding CORS/auth complexity
- **Frontend gs:// fallback** — Added conversion in tenant-logo.tsx to handle both old cached configs (with gs:// paths) and new configs (with HTTPS URLs)
- **Computed values outside Zustand store** — Moved branding/features/defaults computation to a wrapper function rather than store getters, preventing the Object.assign destruction issue

## GCS Paths

```
themes/singa/
├── style_params.json
└── assets/
    ├── intro_background.png (3840x2160)
    ├── karaoke_background.png (3840x2160)
    ├── end_background.png (3840x2160)
    ├── Inter-SemiBold.ttf
    ├── cdg_instrumental_background.gif (300x216)
    └── cdg_title_background.gif (300x216)

tenants/singa/
├── config.json
└── logo.png
```

## Manual Steps Completed

1. DNS: CNAME `singa.nomadkaraoke.com` → Cloudflare Pages
2. Cloudflare Pages: Added `singa.nomadkaraoke.com` as custom domain
3. GCS: Uploaded all assets and configs via setup script

## Future Considerations

- GitHub Pages is still enabled in repo settings (dormant) — should be disabled in GitHub UI
- If more tenants are added, consider a shared base class or template for setup scripts
- The backend tenant config cache has a 5-minute TTL — config changes take up to 5 min to propagate
- Service worker `karaoke-gen-static-v1` cache can serve stale JS — may need cache-busting strategy for tenant portal updates
