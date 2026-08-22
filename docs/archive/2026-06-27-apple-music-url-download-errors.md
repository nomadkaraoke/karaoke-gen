# Apple Music URL → cascading download-failure alerts (2026-06-27)

## Incident

Four "New Error Pattern" alerts fired simultaneously (first seen `2026-06-27T20:32:01Z`):

1. `karaoke-backend` — `URL download failed: ... Unsupported URL: https://music.apple.com/...` (×2)
2. `karaoke-backend` — `Job <ID> failed: Download failed: ... Unsupported URL: https://music.apple.com/...` (×2)
3. `audio-separation-job` — `Exception: Failed to download audio file` (×6)
4. `lyrics-transcription-job` — `Exception: Failed to download audio file` (×6)

All four were **one incident**: a single user tried to make a karaoke track from an
Apple Music link (Hailey Whitters – "Gone Country"), submitted it twice (jobs
`7f36304a` + `cb145971`), and clicked **Retry** once.

### Confirmed timeline (job `7f36304a`)

| Time (UTC) | Event |
|---|---|
| 20:23:02 | Job created from `https://music.apple.com/...` |
| 20:23:07 | flacfetch → `Unsupported URL` → job **failed**, HTTP 500 to user |
| 20:23:20 | User resubmits same URL → job `cb145971`, fails identically |
| 20:23:31 | User clicks **Retry** → `POST /api/jobs/7f36304a/retry` → *"Has input audio, restarting from beginning"* |
| 20:23:32 | Retry triggers **audio-separation-job + lyrics-transcription-job** (NOT a re-download) |
| 20:24–20:28 | Workers find no audio in GCS → "Failed to download audio file" → Cloud Run retries each task several times → the ×6/×6 counts |

## Root causes (two bugs)

### Bug A — DRM streaming URLs accepted, then fail deep with a cryptic error
`_validate_url` (`backend/api/routes/file_upload.py`) has a supported-domain allowlist
but falls through to `return True` for everything else ("let yt-dlp try"). Apple Music
/ Spotify / Tidal / Amazon Music / Deezer / Pandora are DRM-protected and can *never* be
downloaded, so a job got created and died at the download stage with an HTTP 500 +
"Unsupported URL".

### Bug B — retrying a URL job that failed *before download* fires the wrong workers
`backend/api/routes/jobs.py` retry path used
`elif job.input_media_gcs_path or job.url:` to "restart from beginning". A URL job whose
download never succeeded still has `job.url` set (but **no** audio in GCS), so Retry
jumped straight to the audio/lyrics workers. They can't find audio → "Failed to download
audio file" → Cloud Run retries them → wasted GPU + noisy alerts. Latent for *any*
URL-download failure (private/geo YouTube, transient flacfetch errors), not just Apple Music.

## Fixes

- **Bug A:** new `_unsupported_url_platform(url)` helper + a known-DRM-host map; the
  `create-from-url` endpoint now returns **400** with a friendly i18n message
  (`audioSearch.drmUrlUnsupported`, translated to all 33 locales) before a job is created.
  YouTube Music (`music.youtube.com`) is deliberately *not* blocked (it's downloadable).
  - The message suggests downloading the audio with a third-party tool and then
    uploading the file. For platforms with a verified downloader
    (`_DRM_DOWNLOADER_SUGGESTIONS`) it links to it — Apple Music →
    `https://am-dl.pages.dev` — via the `drmUrlUnsupportedWithLink` variant.
  - URLs in submit-error messages are now clickable via a new
    `LinkifiedText` component (`frontend/components/ui/linkified-text.tsx`), wired
    into the guided flow and the URL-tab submit error.
  - **v0.188.3:** `_DRM_DOWNLOADER_SUGGESTIONS` now maps each platform to a *list*
    of downloader links (Apple Music → `am-dl.pages.dev`, `aplmate.com`; Spotify →
    `spotdown.org`, `spotmate.online`). Platforms without a known tool (Tidal,
    Amazon Music, Deezer, Pandora) fall back to a Google search link for
    "`<platform> downloader`" via the new `drmUrlUnsupportedSearch` message. The
    `_drm_unsupported_detail(locale, platform)` helper builds the right message;
    `LinkifiedText` renders the multiple URLs as clickable links (no frontend
    change needed).
  - **v0.188.3 (early validation):** the guided flow previously only surfaced the
    DRM rejection at *final submit* — after the user walked the whole flow. Added
    a lightweight `POST /api/jobs/validate-url` endpoint (reuses
    `_unsupported_url_platform` + `_drm_unsupported_detail`, single source of
    truth) that `AudioSourceStep`'s "Use this URL" button now calls, showing the
    guidance immediately (with a "Checking…" spinner) and blocking advancement.
    Fails open on network error — the final submit still validates server-side.
- **Bug B:** the retry "restart from beginning" branch is now gated on
  `job.input_media_gcs_path` only. URL jobs without downloaded audio fall through to the
  existing clean **"resubmit"** 400 instead of firing doomed workers — matching the
  endpoint's documented intent ("re-runs from beginning if input audio exists").

## Tests

- `TestUnsupportedDrmUrl` (`test_file_upload.py`) — detection across Apple Music / Spotify
  / Tidal / Amazon Music / Deezer / Pandora (+ subdomains); YouTube/YouTube Music/Vimeo/
  SoundCloud/generic/None not flagged.
- `TestRetryUrlJobWithoutDownload` (`test_routes_jobs.py`) — URL job without audio → 400
  resubmit + **no** workers scheduled; URL job *with* audio in GCS → restarts + workers
  scheduled.

## Not done (deliberately out of scope)

- **Re-download on retry for URL jobs.** There is no worker that re-downloads a *generic*
  `job.url` (the download is inline in `create-from-url`; `audio_download_worker` only
  handles audio-search sources: YouTube/RED/OPS/Spotify-by-id). For transient URL failures
  the user must resubmit. A future enhancement could extract the inline download into a
  reusable worker so Retry can re-fetch.
- **Worker-side hard exit.** `audio_worker.py` detects the bad state but logs
  "Continue anyway"; fixing the retry trigger (Bug B) removes the amplification at its
  source, so the worker guard was left as-is to contain risk.
