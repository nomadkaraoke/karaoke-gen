# Plan: review page always loads fast with audio + waveform (Option B — same-origin proxy)

**Date:** 2026-09-17
**Repo:** karaoke-gen (backend + frontend)
**Chosen approach:** Option B (from the handoff) — promote the byte-proxy to the prod default; remove IAM `signBlob` from the review hot path entirely.
**Prereq (safety net, stays in place):** v0.228.1 signing timeout + bounded admission + fail-soft in `storage_service`.

## Spec (Andrew, verbatim)

> "…re-architect or harden the system in whatever way we need to make the lyrics review page always load **quickly** with **all features available at the initial load time, including audio and waveform**."

Target: `/app/jobs/#/{id}/review` loads fast AND with audio + waveform on first paint — not the degraded fail-soft path.

## Root cause (confirmed)

`GET /api/review/{id}/correction-data` signs, on the hot path:
- 2× instrumental OGG URLs (`_build_instrumental_options`)
- 1× `backing_vocals_waveform` PNG URL

Each `generate_signed_url` is a network round-trip to IAM `signBlob` (Cloud Run has no private key). Healthy = 100–500 ms/URL; stalled = ~120 s hangs → thread-pool exhaustion → outage (the 2026-09-17 incident).

## Key findings from investigation

1. **`backing_vocals_waveform_url` is never consumed by the frontend** (grep: 0 hits). The waveform is drawn from the Bearer-auth JSON endpoint `GET /{id}/waveform-data`. → Drop the waveform signing block from `correction-data` (removes 1 signBlob + dead code).
2. **`instrumental_options[].audio_url`** is used as a **raw `<audio>` src** (`InstrumentalSelectorEmbedded.tsx`, `PreviewVideoSection.tsx`). A raw media src can't send a Bearer header → auth must ride in the query string (`?token=`), exactly like the existing, proven `getVocalsAudioUrl()` / `getAudioUrl()`.
3. **Proven prod pattern:** `/audio/vocals?token=<localStorage token>` already serves review audio bytes via `require_review_auth` (query `token`). Mirroring it is correct-by-construction.
4. **No reliable backend absolute-URL source** (`request.base_url` untrusted behind Cloudflare; no configured public API URL). → the **frontend** builds the absolute proxy URL from `API_BASE_URL` + `?token=getAccessToken()`.
5. The combined review page uses `InstrumentalSelectorEmbedded` (correction-data), NOT the standalone `InstrumentalSelector` (`/instrumental-analysis`). The latter is a separate page — out of scope for this PR (same-pattern follow-up if desired).

## Design

### Backend (`backend/api/routes/review.py`)
1. **New endpoint** `GET /{job_id}/instrumental-audio/{option_id}` (`option_id` ∈ `clean` | `with_backing`):
   - `require_review_auth` (same auth as the rest of review; token via query for raw-src use).
   - Resolves the stem GCS path server-side from `job.file_urls['stems']` (`instrumental_clean` / `instrumental_with_backing`) — no client-supplied paths.
   - Serves the transcoded OGG bytes with **HTTP Range (206)** support via the existing `_ranged_response` (needed for `<audio>` seeking).
   - Small **bounded** in-process LRU cache (cap N, evict oldest) so Range seeks don't re-download from GCS each time — prod-safe (unlike the dev `_DEV_AUDIO_CACHE`, which is unbounded).
   - No signing anywhere in this path.
2. **`_build_instrumental_options`** (non-dev branch): stop calling `generate_signed_url`. Set `audio_url` to the **relative** proxy path `/api/review/{job_id}/instrumental-audio/{option_id}` (truthy → existing `o.audio_url` presence checks keep working). The dev branch (`REVIEW_AUDIO_PROXY`) is unchanged.
3. **`get_correction_data`**: remove the `backing_vocals_waveform_url` signing block (dead field).
4. `storage_service` signing timeout/admission knobs stay untouched (safety net).

Net: `correction-data` performs **zero** `signBlob` calls on the hot path.

### Frontend (`frontend/`)
1. Add `getInstrumentalAudioUrl(optionId)` on the review api client → `${API_BASE_URL}/api/review/${jobId}/instrumental-audio/${optionId}?token=${encodeURIComponent(getAccessToken())}` (mirrors `getVocalsAudioUrl`).
2. `InstrumentalSelectorEmbedded.tsx` — build the play src from `option.id` via the helper instead of `option.audio_url`.
3. `PreviewVideoSection.tsx` — build the synced stem src from `selectedOption.id` via the helper. Proxy URLs don't expire → the expiry/`refreshInstrumentalUrls` machinery becomes unnecessary (simplify or leave as a harmless no-op).
4. Presence checks on `o.audio_url` (ReviewChangesModal, LyricsAnalyzer) keep working (backend returns a truthy path).

### Deploy skew
Minor, self-healing: during the backend↔frontend deploy gap, only the **instrumental preview player** for jobs opened in that window may not play (lyrics + everything else fine); reload fixes it. Far less severe than the signBlob-stall outage being removed.

## Tests
- **Backend:** new endpoint streams OGG bytes + Range 206 + 416; `require_review_auth` (401 without token); `option_id` → stem resolution + 404 unknown/missing; `correction-data` returns proxy `audio_url` and **does not** call `generate_signed_url` for instrumentals/waveform (assert via mock); waveform field absent.
- **Frontend:** `getInstrumentalAudioUrl` shape; components use the helper URL as src.
- **Prod E2E** (`frontend/e2e/production/`): open a review → assert `correction-data` returns non-null instrumental `audio_url`, the proxy endpoint returns `200 audio/ogg`, and it plays. Assert load is fast.

## Key files
- `backend/api/routes/review.py` — `_build_instrumental_options`, `get_correction_data`, new `instrumental-audio` endpoint, `_ranged_response`.
- `backend/services/audio_transcoding_service.py` — `get_review_audio_bytes_async` (byte source).
- `frontend/lib/api.ts` — new helper (near `getVocalsAudioUrl`).
- `frontend/components/instrumental-review/InstrumentalSelectorEmbedded.tsx`, `frontend/components/lyrics-review/PreviewVideoSection.tsx`.
