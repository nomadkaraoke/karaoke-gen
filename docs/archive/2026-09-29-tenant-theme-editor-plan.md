# Tenant self-service theme editor — plan (2026-09-29)

## Goal
Tenant portal users (every allowed user of the tenant) can edit their tenant's theme from their own
portal — a **"Theme & style"** item in the user menu opens an editor modal with a guided form, an
Advanced JSON tab, and **exact server-rendered previews** (title card + a karaoke frame with a
partly-sung line) that refresh as they edit. **Save applies to new jobs only** (jobs copy the theme at
creation, unchanged).

Decisions (Andrew, 2026-09-29): editors = every allowed tenant user; previews = server-rendered exact;
scope = guided form + advanced JSON; apply = new jobs only.

## Backend

### Membership guard (security-critical)
`X-Tenant-ID` is client-controlled and magic-link sessions carry no tenant id, so a new dependency
`require_tenant_member` (in `backend/api/routes/tenant_theme.py`):
- tenant config must resolve from the request (`request.state.tenant_config`) and be `is_active`;
- `auth_result.tenant_id` must be None or equal to that tenant id;
- `config.is_email_allowed(auth_result.user_email)` (allowlist; admins always pass).
Otherwise 403. (Same rule as `JobManager._is_tenant_billed`; do NOT copy `tenant_bulk.py`, which only
checks the tenant exists.)

### Endpoints — `/api/tenant/theme` (new router, registered in `main.py`)
- `GET` → `{theme_id, style_params, assets[], fonts[]}` (fonts = bundled `karaoke_gen.resources`
  fonts + uploaded .ttf/.otf assets).
- `POST /assets` (multipart, one file) → stores under `themes/<id>/assets/<stem>-<sha8>.<ext>`
  (content-addressed ⇒ never overwrites an asset a live job/theme uses; `no-store`), returns the
  basename. Images ≤15 MB, fonts ttf/otf. Reuses `_read_asset` validation.
- `POST /preview` (JSON `{style_params, sample?: {artist,title}}`) → `{title_card, karaoke_frame}`
  as `data:image/jpeg;base64` (1280×720). Draft is NOT saved.
- `PUT` (JSON `{style_params}`) → sanitize + validate, then `update_tenant(style_params=...)` with
  `config_updates=None, logo=None` (tenants can't touch their own config/allowlist).

### Sanitization (tenant saves + previews)
`_validate_style_params` is shallow; tenant input additionally:
- asset fields (`intro/end.background_image`, `intro/end.font`, `karaoke.background_image`,
  `karaoke.font_path`, cdg asset fields) must be **bare basenames** that exist in the theme's assets
  or bundled fonts (reject `/…`, `gs://…`, `..`);
- **font consistency**: render pipeline only downloads one `font` asset (from `intro.font`) and uses
  it for every section → the guided font picker sets `intro.font`, `end.font`, `karaoke.font_path`,
  `cdg.font_path` together and derives `karaoke.font` (the ASS family name libass matches) from the
  TTF via PIL `ImageFont.getname()`. Save rejects a karaoke `font_path` ≠ `intro.font`.
- numeric bounds (font_size 40–600, top_padding 0–2000, max_line_length 10–80, regions inside 3840×2160).

### Preview renderer — `backend/services/theme_preview_service.py`
- Resolve assets: download referenced theme assets (by basename) from GCS to a per-tenant temp cache
  keyed by generation; bundled fonts from `karaoke_gen.resources`.
- **Title card**: `VideoGenerator(...).create_title_video(artist, title, format, noext, video_path,
  intro_video_duration=0)` → PNG, format = `{**DEFAULT_INTRO_STYLE, **intro}` with local paths
  (same call production `screens_worker` makes). Downscale → JPEG.
- **Karaoke frame**: `k = {**DEFAULT_KARAOKE_STYLE, **karaoke}` (build_karaoke_styles needs
  underline/strike_out/angle/encoding/ass_name); fake `LyricsSegment`s (first start <10 s, gaps <10 s
  to avoid SectionDetector intro/instrumental screens) through `SegmentResizer(max_line_length)`;
  `SubtitlesGenerator(..., video_resolution=(3840,2160), font_size, line_height=font_size,
  styles={"karaoke": k}).generate_ass(...)`; ffmpeg single frame at a timestamp mid-way through
  line 2 (`-ss T -frames:v 1`, same bg input as `output/video.py`, `ass=…:fontsdir=…,scale=1280:-2`).
- Concurrency: module semaphore (1–2) + dedicated executor, 20 s timeout, like
  `review._render_preview_locally`. Cache identical drafts (hash of style_params+sample) briefly.

## Frontend
- `AuthStatus` menu: **"Theme & style"** item when `!useTenant().isDefault` → `TenantThemeEditorDialog`.
- `components/tenant-theme/`: dialog (wide), two tabs:
  - **Guided**: Title card (background image upload / colour, title colour, artist colour, text case,
    vertical position + size of title/artist), Karaoke (background image / colour, sung, unsung,
    outline colours, font size, lyrics top position, max line length), End screen (closing text +
    colour), Font (bundled list + upload).
  - **Advanced**: validated full-theme JSON editor.
  - Right/top: title-card + karaoke previews, debounced ~700 ms after edits, stale requests aborted,
    spinner overlay, error state. Save / Discard; unsaved-changes guard on close.
- `lib/api.ts`: `tenantThemeApi.{get, uploadAsset, preview, save}` via `getAuthHeaders()`.
- i18n: all strings in `messages/en.json` (`tenantTheme.*`), translate all 33 locales.

## Tests
- **pytest**: membership guard (non-member 403, spoofed `X-Tenant-ID` 403, inactive tenant 403,
  member/admin 200); sanitization (absolute/gs:// paths rejected, font consistency, bounds);
  asset upload naming (content-addressed, no overwrite); save uses `update_tenant` without
  config/logo; preview service renders real PNGs from a fixture theme (colour background + bundled
  font) — title card + karaoke frame non-empty and differ when sung colour changes (skip if ffmpeg
  absent locally; CI has it).
- **Jest**: dialog loads theme, editing a colour triggers a debounced preview call, Advanced JSON
  validation blocks save, save sends sanitized style_params, menu item only on tenant portals.
- **Playwright production E2E** (`e2e/production/tenant-theme-editor.spec.ts`): on the randy-vild
  portal as admin, open editor, change sung colour, previews refresh, Discard (no persistent change).
