# White-Label Tenant Portals: Made Working End-to-End (Jun 2026)

## Context

The two B2B white-label tenant portals (`vocalstar.nomadkaraoke.com`, `singa.nomadkaraoke.com`)
had full data models, middleware, GCS config, themes, a simplified job form, a separate
Cloudflare deployment, and 280+ unit tests — and were documented as "production ready."
**But no tenant job had ever actually completed:** 0 of the 484 most recent prod jobs
carried a `tenant_id`. The feature looked done but was broken at every stage that had
never been exercised by a real end-to-end tenant job.

This session drove a real Vocal Star track (`Eddy Grant - I Don't Wanna Dance`, from their
bulk batch) through the full pipeline in production, fixing each blocker in turn, until a
tenant job completed: correct attribution → transcription → screens → review → render →
Dropbox delivery, with the locked theme and **no** YouTube / Google Drive.

## The seven blockers (each was the next never-exercised stage)

1. **Tenant attribution never happened (root cause).** The frontend API client never sent
   the `X-Tenant-ID` header (`getTenantHeaders()` existed but was unwired). Tenant portals
   call the shared `api.nomadkaraoke.com`, whose Host isn't the tenant subdomain, and the
   `?tenant=` param is prod-disabled — so the backend never detected the tenant and every
   sign-in/job silently became a consumer job. Fix: attach `X-Tenant-ID` from
   `window.__TENANT_CONFIG__` in `lib/api.ts` (`getBaseHeaders`), incl. the magic-link
   request. PR #824.

2. **Tenant config not authoritative.** Prod sets global `DEFAULT_DROPBOX_PATH` /
   `DEFAULT_GDRIVE_FOLDER_ID` / `DEFAULT_BRAND_PREFIX`; `_apply_tenant_overrides` only
   filled unset fields, so tenant jobs leaked into the shared consumer Dropbox folder and
   uploaded to the global Google Drive. Fix: tenant config is authoritative, gated by
   feature flags (gdrive/dropbox cleared when disabled). PR #824.

3. **Wrong file transcribed.** existing-instrumental jobs upload mixed + instrumental into
   the same `uploads/{job}/audio/` dir; `uploads-complete` resolved the `audio` slot via
   `files[0]`, which sorts to `existing_instrumental.mp3`. Transcription ran on the
   instrumental. Fix: `_select_mixed_audio_files` excludes the instrumental. PR #825.

4. **AudioShake rejected the audio.** Vocal Star's MP3s begin with ~731 leading null bytes
   before the first frame; ffmpeg tolerates it, AudioShake 400s ("Error fetching or
   processing asset"). Consumer jobs never hit this (they transcribe re-encoded stems).
   Fix: detect leading null padding on `.mp3/.aac` and losslessly remux (`ffmpeg -c copy`)
   before upload. Verified against the live AudioShake API. PR #826.

5. **Theme incomplete.** The strict theme validator requires every field; the tenant
   themes (created Jan 2026) were missing `extra_text_text_transform` (intro + end),
   added to the schema later → "Title screen generation failed." Fix: added the field to
   the live GCS themes and the setup scripts. PR #827.

6. **Theme assets not downloaded.** The setup scripts wrote full `gs://bucket/themes/.../assets/X`
   URLs, but `theme_service._build_style_assets_mapping` only resolves bare basenames (the
   consumer convention) → `style_assets` empty → assets never localized → encoder failed
   with "Video background image not found". Fix: rewrote the live themes + setup scripts to
   bare basenames. PR #827.

7. **Render prerequisites — "No instrumental selected".** existing-instrumental jobs have
   no instrumental-selection UI step; the orchestrator defaults to `'clean'` (a stem never
   produced) and `_validate_prerequisites` rejected the job. Fix: `complete-review`
   defaults `instrumental_selection` to `'custom'` when `existing_instrumental_gcs_path`
   is set. PR #827.

## Tenant config (final, both tenants)

- Locked theme (`vocalstar` / `singa`), no theme selection.
- `youtube_upload=false`, `gdrive_upload=false` (folder cleared), `dropbox_upload=true`.
- Dropbox: `/MediaUnsynced/Karaoke/Tracks-VocalStar`, `/MediaUnsynced/Karaoke/Tracks-Singa`.
- Jobs forced `is_private=true`; CDG + 4K enabled.

## Daily E2E

`scripts/e2e/tenant_e2e.py` + `.github/workflows/e2e-tenant-daily.yml` run a full job per
tenant daily (07:37 UTC, matrix vocalstar/singa): submit → approve review → render →
assert (completes, Dropbox link present, no YouTube, downloads available) → clean up the
job + uploads. Auth via WIF + `E2E_ADMIN_TOKEN`; test audio in `gs://.../e2e-tests/shared/`.

## Outcome

Both tenants pass the full prod E2E (PRs #824–#830): log in → submit (artist, title, mixed,
instrumental) → transcribe → review → render with the locked theme → encode → package →
Dropbox delivery, with **no YouTube, no Google Drive**, and all outputs (4K / 720p /
with-vocals MP4 + CDG + TXT) downloadable from the portal via `job.file_urls`.

The encoder blocker (bug #8) took three iterations: the staging had to land in the
**orchestrator** path (`video_worker_orchestrator._run_encoding`, not the legacy
`_encode_via_gce`) and use the filename the running encoder VM's `custom` branch globs for
(`custom_instrumental.<ext>`, since the live VM boots the 2026-01-23 image whose `custom`
branch predates `*existing_instrumental*` / `*Instrumental User*`). The instrumental is
server-side-copied from `uploads/{job}/audio/existing_instrumental.*` →
`jobs/{job}/custom_instrumental.<ext>`.

## Gotchas / lessons

- The tenant pipeline had never run end-to-end, so every stage hid a latent bug. "Has a
  real job of this shape ever completed in prod?" is a better readiness question than test
  count.
- Theme asset paths must be **bare basenames** (consumer convention), not `gs://` URLs.
- AudioShake needs clean audio at offset 0; user-provided files vary — remux defensively.
- Public tenant config API redacts `dropbox_path`/`gdrive_folder_id`/`brand_prefix`; read
  the stored GCS config for the real values.
