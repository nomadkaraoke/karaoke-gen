# Unified browser upload flow — plan (2026-09-26)

## Why
- Job 8729f19a: large audio upload (signed-URL flow) was silently abandoned — the only
  signal was the "Creating..." button text, and the job card already said "Setting up".
  The job sat PENDING forever with the credit spent.
- Job 2d0c1ae0: instrumental-review "Alternate Instrumental" upload of a 44 MB WAV failed
  invisibly — it's a multipart POST through Cloud Run (32 MiB request cap), no progress, and
  the post-upload preview URL (`/api/jobs/{id}/audio-stream/...`) has no backend route.
- Singer-songwriters with their own instrumental must currently go through vocal separation
  + instrumental review; `existing_instrumental` (tenant-only in the UI) already skips both.

## Shared frontend upload layer (DRY)
- `lib/upload.ts`: `putFileToSignedUrl` (XHR PUT, progress, clear errors),
  `uploadFilesToSignedUrls` (sequential multi-file, aggregate progress + current file),
  `getAudioFileDuration` (best-effort via `<audio>` metadata).
- `hooks/useUploadTask.ts`: progress state + `beforeunload` guard while active; `run(fn)`.
- `components/upload/UploadProgressModal.tsx`: non-dismissable modal (progress, speed, ETA,
  file N of M, keep-tab-open warning); phase labels overridable per caller.
- Used by: guided job creation, tenant single job, instrumental review upload.
  Tenant bulk keeps its per-row resumable table but uses the shared `beforeunload` hook.

## Backend
1. `create-with-upload-urls` accepts + persists `requires_audio_edit` (was silently dropped).
2. `uploads-complete`: ownership check; on duration mismatch cancel the job (refund) so it
   can't be stranded, then return the 400.
3. Instrumental review: `POST /api/jobs/{id}/instrumental-upload-url` →
   signed PUT to `jobs/{id}/uploads/custom_instrumental_source{ext}`;
   `POST /api/jobs/{id}/instrumental-upload-complete` → shared processing helper (duration
   check, FLAC, `stems.custom_instrumental`) and returns a signed `audio_url` for playback.
   Multipart endpoint kept (CLI/back-compat), same helper, also returns `audio_url`.
4. Previous work: `state_data.awaiting_upload`, stale-upload sweep (cancel + refund after 2h).

## Guided flow
- Always use the signed-URL path (single code path; gets awaiting-upload label + cleanup).
- Private + uploaded-audio jobs: optional "Use my own instrumental" file on the Customize
  step; client-side duration pre-check; sent as `existing_instrumental`.

## Tests
Backend unit tests for new endpoints/fixes; Jest for upload lib, hook, modal, instrumental
selector upload, guided-flow instrumental option; local Playwright run against the real
frontend with a throttled PUT server.
