# Custom Video Styling for Private Delivery Jobs - 2026-03-04

**Status:** Complete
**Branch:** `feat/sess-20260304-0018-custom-video-styling`

## Summary

Added UI for private-delivery users to customize their karaoke video styling: upload custom background images (karaoke + title card) and change artist/title text colors. Since private tracks never go to YouTube, there's no risk of publishing non-Nomad-branded content publicly. This enables fulfilling Fiverr custom orders where clients want branded karaoke videos.

## Key Decisions

- **Post-creation upload flow**: Style assets are uploaded after job creation via signed GCS URLs, not during creation. This keeps the `create-from-search` endpoint unchanged and avoids multipart complexity. Style assets aren't needed until ~10 min into processing (after separation + transcription), so there's plenty of time.

- **Non-blocking uploads**: After job creation succeeds, the success screen shows immediately. Style uploads happen in the background. If uploads fail, the job falls back to the default theme — no user-facing error since the job still works.

- **Canvas-based live previews**: Both the title card and karaoke background show accurate previews using HTML5 Canvas at 3840x2160 resolution (matching actual video output). The TitleCardPreview component was extended with optional `customBackgroundUrl`, `titleColor`, and `artistColor` props. A new `KaraokeBackgroundPreview` component shows the uploaded background with sample karaoke lyrics overlaid.

- **Cutoff status check**: Style uploads are rejected if the job has progressed past `GENERATING_SCREENS` status, since the rendering pipeline would have already consumed the style assets by that point.

- **Conditional UI**: The custom styling section only appears when the "Private (no YouTube upload)" checkbox is checked, keeping the default flow clean.

## Changes

### Backend (`backend/api/routes/file_upload.py`)
- `POST /api/jobs/{job_id}/style-upload-urls` — Returns signed GCS URLs for style asset uploads. Validates job ownership, cutoff status, and file types (PNG/JPG only).
- `POST /api/jobs/{job_id}/style-uploads-complete` — Verifies files exist in GCS, merges into `job.style_assets`, reloads the theme's `style_params.json` with color overrides and custom background paths.
- New constants: `STYLE_FILE_TYPES` (allowed style file types), `STYLE_UPLOAD_CUTOFF_STATUSES` (statuses past which uploads are rejected).

### Frontend API (`frontend/lib/api.ts`)
- `getStyleUploadUrls(jobId, files[])` — Requests signed upload URLs
- `uploadFileToSignedUrl(url, file, contentType)` — PUTs file directly to GCS
- `completeStyleUploads(jobId, uploadedFiles[], colorOverrides?)` — Finalizes uploads

### Frontend Components
- **`TitleCardPreview.tsx`** (modified) — Added `customBackgroundUrl`, `titleColor`, `artistColor` props
- **`KaraokeBackgroundPreview.tsx`** (new) — Canvas preview showing uploaded background with sample lyrics
- **`ImageUploadField.tsx`** (new) — Reusable drag-and-drop image upload with validation (PNG/JPG, max 10MB)
- **`CustomizeStep.tsx`** (modified) — Added "Custom Video Style" section with background uploads and color pickers, visible only when `isPrivate` is checked
- **`GuidedJobFlow.tsx`** (modified) — Added state for style assets/colors, background upload orchestration in `handleConfirm`

### Tests (64 total)
- `backend/tests/test_style_upload_endpoints.py` — 15 tests covering auth, validation, cutoff, asset merging, color overrides
- `frontend/components/job/__tests__/CustomizeStep.test.tsx` — 14 tests
- `frontend/components/job/__tests__/ImageUploadField.test.tsx` — 9 tests
- `frontend/components/job/__tests__/TitleCardPreview.test.tsx` — 2 new tests (custom colors, custom background)
- `frontend/__tests__/guided-flow.test.tsx` — 6 new tests (style upload API contracts)

## Architecture Notes

The backend already fully supported custom style assets (GCS storage, `style_assets` dict, rendering pipeline). The existing `_get_gcs_path_for_file()` function handles `style_*` file types by mapping them to `uploads/{job_id}/style/{asset_key}.{ext}`. The `complete_style_uploads` endpoint reuses `ThemeService.apply_color_overrides()` and `get_theme_style_params()` to properly merge custom assets with the theme's style configuration.

GCS file structure for custom styles:
```
uploads/{job_id}/style/
  karaoke_background.png    # Custom karaoke background
  intro_background.png      # Custom title card background
  style_params.json         # Modified theme params with custom paths/colors
```

## Future Considerations

- Could extend to support custom end card backgrounds (`style_end_background` is already in `STYLE_FILE_TYPES`)
- Could add sung/unsung lyrics color overrides (the `ColorOverrides` Pydantic model already supports `sung_lyrics_color` and `unsung_lyrics_color`)
- Tenant portals could have per-tenant default style assets
