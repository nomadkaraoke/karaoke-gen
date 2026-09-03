# Admin "Create Tenant" + Saved Themes — Plan

**Date:** 2026-08-31
**Worktree:** `karaoke-gen-saved-themes-bulk`
**Origin (Andrew, verbatim):** Making ~15 tracks (an album) for one client who wants karaoke
versions of every track. Wants (1) users to save customisation options (colors, background image)
as their own theme on their profile, selectable from the Custom Video Style dialog (default Nomad
theme for anyone with none); and (2) a way to handle "a bunch of tracks, audio provided up front,
same theme/background, client-supplied instrumental per track" — "essentially a tenant use case but
there's no admin panel mechanism to easily create a new tenant".

## Decisions (Andrew, via AskUserQuestion)
1. **Build the album batch first.** Saved user themes follows as a second PR (shares the theme core).
2. **Album batch = admin create-tenant + reuse the existing tenant bulk flow** (not extending
   consumer Bulk mode).

## Why this is small: the batch pipeline already exists
The **tenant bulk flow** (`TenantBulkFlow.tsx` + `/api/tenant/bulk/analyze`, shipped v0.200.0 #943)
already does everything the album needs:
- Folder pick → LLM pairs each track's **mixed audio ↔ client-supplied instrumental**.
- Editable review table.
- GCS-**resumable chunked uploads** (`existing_instrumental` flow → **separation skipped**).
- The tenant's **locked theme** (colors + background) applied to **every** job.
- Jobs forced `is_private`, delivered to the tenant's Dropbox, never YouTube/GDrive.

The **only** missing piece is what Andrew named: **no admin UI/API to mint a tenant**. Today a
tenant is created by hand-running `scripts/setup-vocalstar-tenant.py` (uploads theme assets +
`themes/{id}/style_params.json`, writes `tenants/{id}/config.json`, registers in
`themes/_metadata.json`). This PR turns that script into an admin endpoint + form.

## Access without DNS (key finding)
`lib/tenant.ts::detectTenantFromUrl()` supports **`?preview_tenant=<id>` on any domain for admins**.
So immediately after creating the tenant, Andrew can drive its bulk flow at
`https://gen.nomadkaraoke.com/app?preview_tenant=<id>` — **no Cloudflare custom-domain / DNS needed**.
DNS + Pages custom domain remains a later, optional step if the client ever wants their own portal.

## Backend
New `backend/services/tenant_admin_service.py`:
- `slugify_tenant_id(name)` → lowercase `[a-z0-9-]`, reject reserved (`gen,api,www,buy,admin,app,beta`
  — from `middleware/tenant.py::NON_TENANT_SUBDOMAINS`), reject if `tenant_service.tenant_exists`.
- `create_tenant(...)`:
  1. `base = theme_service.get_theme_style_params(get_default_theme_id())` (error if none).
  2. **Copy default theme's `assets/*` into `themes/{new}/assets/`** so the new theme is
     self-contained (fonts, CDG backgrounds, default backgrounds all resolve as bare basenames —
     this is the fix for tenant-E2E bug #6 asset-resolution).
  3. `apply_color_overrides(base, ColorOverrides(...))`.
  4. For each provided background (karaoke/intro/end): upload to `themes/{new}/assets/<key>.<ext>`
     and set `style_params[section]["background_image"] = "<key>.<ext>"` (basename).
  5. `upload_json("themes/{new}/style_params.json", style_params)`.
  6. Register `{id,name,description,is_default:false}` in `themes/_metadata.json` (read-modify-write).
  7. Upload logo (optional) to `tenants/{new}/logo.<ext>`.
  8. Build `TenantConfig` (B2B defaults: audio_search off, youtube/gdrive off, dropbox if path,
     theme_selection off, locked_theme={new}, is_private forced downstream, brand_prefix,
     distribution_mode, allowed_email_domains, sender_email). Write `tenants/{new}/config.json`
     with `if_generation_match=0` (create-only).
  9. `tenant_service.invalidate_cache(new)` + `theme_service.invalidate_cache()`.

New `backend/api/routes/tenant_admin.py` (`prefix="/api/admin/tenants"`, `Depends(require_admin)`):
- `GET /` → list tenants (id/name/subdomain/is_active/created_at) from `list_files("tenants/")`.
- `POST /` → **multipart** create (fields + optional `karaoke_background`/`intro_background`/
  `end_background`/`logo` UploadFiles). Returns created public config + the `?preview_tenant=` URL.
Register in `main.py`.

## Frontend
- `frontend/app/admin/tenants/page.tsx` — list + "Create tenant" dialog (name→auto slug/subdomain,
  allowed email domains, 4 color pickers mirroring Custom Video Style, karaoke/intro background
  uploads, dropbox path / download-only, brand prefix). On success: toast + copyable
  `?preview_tenant=` link + "Open bulk flow" button.
- `frontend/components/admin/admin-sidebar.tsx` — add **Tenants** nav item (Building2 icon).
- `frontend/lib/api.ts` — `adminApi.listTenants()` + `adminApi.createTenant(FormData)`.
- Admin is English-only (no `[locale]` counterpart) → **no i18n locale files touched**.

## Tests
- Backend: `test_tenant_admin_service.py` (slug/reserved/duplicate, theme derivation copies assets +
  applies colors + sets background basename, config B2B defaults, create-only clobber guard) with a
  fake StorageService; `test_tenant_admin_route.py` (require_admin gate, multipart happy path, 409 on
  dup, 400 on reserved slug).
- Frontend: `admin-tenants-page.test.tsx` (render list, submit create, shows preview link).

## Out of scope (this PR) — the follow-up
Saved **user** themes (profile `saved_themes`, `PUT /users/me`, theme picker in Custom Video Style
dialog). Reuses the same theme-derivation core built here.
