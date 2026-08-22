# Plan: Restore missing files in Dropbox track output folders

**Date:** 2026-06-17
**Branch:** `feat/sess-20260617-1501-dropbox-missing-files`
**Status:** Implemented (v0.186.0) — Parts A/B/C done + a bundled CLI ffmpeg-hang fix (see end).

## Problem

Track output folders uploaded to Dropbox (e.g. `NOMAD-1184 - Eddie Money - I'll Get By`)
are missing two file types that used to be present (last good folder ~Feb 2026):

1. **Screen videos** — the 5-second `.mov` clips generated from the title/end screen
   images: `<artist> - <title> (Title).mov` and `<artist> - <title> (End).mov`.
   The folder still has the `(Title).png`/`.jpg`/`(End).png` images, just not the `.mov`s.
2. **Original input audio** — the source file used for separation/transcription/with-vocals,
   e.g. `<artist> - <title> (flacfetch).flac` or `<artist> - <title> (uploaded).mp3`.

## Root cause

The Dropbox upload (`VideoWorkerOrchestrator._upload_to_dropbox`,
`backend/workers/video_worker_orchestrator.py`) uploads **the entire `output_dir`** wholesale
via `dropbox.upload_folder(...)`. There is no curated file list — whatever is in `output_dir`
gets uploaded. So both files are missing simply because they are **no longer in `output_dir`**
by the time distribution runs. The pipeline migrated from a monolith (one working dir held
everything) to distributed GCE workers, narrowing what lands in `output_dir`:

### `.mov` screen videos — broke with PRs #647/#650 (2026-03-31)
- Old: `screens_worker` generated `(Title).mov`/`(End).mov` locally; `video_worker` pulled
  them into `output_dir`.
- Now: to fit Cloud Run's 2 GiB cap (#640), `screens_worker` emits **PNG only**
  (`intro_video_duration=0`), and `video_worker`/orchestrator download the **PNG**.
- The GCE encoder (`backend/services/gce_encoding/main.py`, `generate_mov_from_png`)
  **still generates** `title.mov`/`end.mov` from the PNGs — but as throwaway intermediates in
  a `screens/` temp dir with generic names. Only the `outputs/` dir is uploaded to GCS
  `finals/`, and the orchestrator's `_download_gce_encoded_files` only maps back 5 named
  finals (`lossless_4k`, `lossy_4k`, `mkv`, `720p`, `with_vocals`). The standalone `.mov`s
  are never returned.

### Original input audio — never copied into `output_dir` in the distributed pipeline
- Audio is downloaded by `audio_worker` early and stored in GCS:
  - Uploaded files → persisted to `jobs/{job_id}/input/{filename}` (because `uploads/` has a
    7-day lifecycle; `jobs/` is permanent).
  - URL/flacfetch jobs → `input_media_gcs_path` / `file_urls['input']`.
- The old monolith left this file in the working dir → it ended up in the Dropbox folder.
- The new orchestrator never copies it into `output_dir`.

## Decisions (from user, 2026-06-17)
- **Scope:** Forward fix for all future jobs **+ backfill original audio** for past jobs where
  it still exists in GCS. **No `.mov` backfill** (can't regenerate without re-encoding).
- **Audio in Dropbox:** Include the full original audio file (it's the archival master), always.

## Fix design

Both fixes converge on "ensure the file is in `output_dir` before `_upload_to_dropbox`."

### Part A — Restore `.mov` screen videos (forward only)

Extend the existing GCE-finals mechanism so the standalone `.mov`s come back like any other final.

1. **GCE worker** (`backend/services/gce_encoding/main.py`, `run_encoding`):
   After the title/end `.mov`s are generated (they already are, for concat), copy them into the
   `outputs/` dir with proper names `{base_name} (Title).mov` / `{base_name} (End).mov` before
   `output_files` is collected. They then upload to GCS `finals/` automatically via
   `output_dir.glob("*")`.
2. **`EncodingOutput`** (`backend/services/encoding_interface.py`): add `title_mov_path`,
   `end_mov_path`.
3. **`GCEEncodingBackend.encode`** (same file): in the filename→key mapping, match
   `(title).mov` / `(end).mov` → new keys (`title_mov`, `end_mov`); populate the new
   `EncodingOutput` fields.
4. **Orchestrator** (`_download_gce_encoded_files`): add `('title_mov_path', ...)`,
   `('end_mov_path', ...)` to `file_mappings` so they download into `output_dir`. Failure to
   download is non-fatal (consistent with existing per-file handling).
5. **Local encoding path** (`LocalEncodingService` / `LocalEncodingBackend`): verify during
   implementation whether the local fallback already leaves the `.mov`s in `output_dir`; if not,
   mirror the behavior. Production is GCE, so this is secondary.

### Part B — Restore original input audio (forward)

In the orchestrator's distribution stage, **before** `_upload_to_dropbox`, fetch the original
source audio from GCS into `output_dir` with the historical source-tagged name.

- New helper (e.g. `_stage_original_audio_for_upload`) that:
  - Resolves the GCS source: `job.input_media_gcs_path` (uploads persisted to `jobs/`), else
    `file_urls['input']`/`['input']['audio']`.
  - Derives the source suffix from `job.audio_source_type`:
    - `file_upload` → `(uploaded)`
    - `audio_search` → `(flacfetch)`
    - `youtube_url` → `(YouTube)`
    - (matches the historical monolith convention: `{artist_title} (flacfetch)` etc.)
  - Downloads to `output_dir/{base_name} ({suffix}){ext}`, preserving the real extension
    (`.flac`/`.mp3`/`.webm`/...).
  - Non-fatal on failure (warn, continue) — Dropbox is already best-effort.

### Part C — Backfill original audio for past jobs (audio only)

One-off script (`backend/scripts/` or `scripts/`) — dry-run by default:
- Query completed jobs since the cutoff (~Mar 2026) that have a `dropbox_link`/`brand_code`.
- For each: locate original audio in GCS (skip if gone). Locate the Dropbox folder via
  `{dropbox_path}/{brand_code} - {artist} - {title}`. Upload the audio with the source-tagged
  name **only if not already present**.
- Log a summary (restored / skipped-missing-audio / skipped-already-present / errors).

## Test plan
- **Unit:** `GCEEncodingBackend.encode` maps `.mov` filenames → new `EncodingOutput` fields;
  source-suffix derivation for each `audio_source_type`; helper builds correct GCS source path
  and output filename per source type.
- **Orchestrator:** `_download_gce_encoded_files` downloads the two new `.mov` mappings;
  `_stage_original_audio_for_upload` places the file in `output_dir` before upload (assert the
  whole-folder upload then includes it). Mock storage/Dropbox.
- **GCE worker:** `run_encoding` copies `.mov`s into `outputs/` with proper names (assert
  collected `output_files` includes them).
- **Manual:** run one real job end-to-end, confirm the Dropbox folder now contains
  `(Title).mov`, `(End).mov`, and `(flacfetch).flac`/`(uploaded).mp3`. Backfill dry-run on a
  small batch, then live on a couple of known folders.

## Files to touch
- `backend/services/gce_encoding/main.py` (A1)
- `backend/services/encoding_interface.py` (A2, A3)
- `backend/workers/video_worker_orchestrator.py` (A4, B)
- `backend/services/local_encoding_service.py` (A5, verify)
- new backfill script (C)
- tests under `backend/tests/`

## Bundled fix: ffmpeg interactive-prompt hang in `--finalise-only` (CLI)

Found while regenerating the missing `.mov`s locally. `karaoke_finalise.py` only added
`-y` to `ffmpeg_base_command` when `non_interactive=True`. In interactive mode, a
pre-existing output (e.g. a prior `(With Vocals).mp4`) made ffmpeg emit
`... already exists. Overwrite? [y/N]` to captured stderr and block on stdin until the
600s subprocess timeout — silently aborting finalisation at step 2/6 (only CDG/TXT zips
produced; no Final video files). The output's mtime never changed, proving no encode ran.

Fix (in `karaoke_gen/karaoke_finalise/karaoke_finalise.py`):
1. `-y` is now **unconditional** (re-runs overwrite intermediates idempotently).
2. All ffmpeg `subprocess.run` calls pass `stdin=subprocess.DEVNULL` (defense-in-depth so
   ffmpeg can never block on a prompt — `execute_command` + both branches of
   `execute_command_with_fallback`).

Tests: `tests/unit/test_karaoke_finalise_ffmpeg_prompt.py`; updated two existing tests that
asserted the old conditional-`-y` / no-stdin behaviour.

## Daily E2E verification (added per request)

So the daily GHA E2E catches this class of regression automatically:
- `audio_download_worker` now records the original audio in `file_urls['input']['audio']`
  (URL/search jobs previously only set `input_media_gcs_path`), matching the upload path —
  so original audio shows consistently in the admin files manifest.
- `_upload_results` (video_worker) registers `title_mov`/`end_mov` under `file_urls['finals']`
  (uploaded to `jobs/{id}/finals/title_mov.mov` etc.), so the screen MOVs are queryable +
  downloadable in the admin UI.
- `happy-path-real-user.spec.ts` (daily E2E Stage 2) gained **STEP 10.5**: after completion it
  calls `GET /api/admin/jobs/{id}/files` and asserts the manifest contains
  `finals/title_mov`, `finals/end_mov`, `input/audio`, and `finals/lossless_4k_mp4` — failing
  loudly if any expected output is missing (vs. the old check that only opened one download).

Tests: `test_upload_results_screen_movs.py` (mov registration + skip-when-missing),
extended `test_audio_download_worker.py` (input/audio recorded).

## Implementation notes (as built)
- Part A files: `gce_encoding/main.py` (copy MOVs into `outputs/`), `encoding_interface.py`
  (`title_mov_path`/`end_mov_path` + list→dict mapping), `video_worker_orchestrator.py`
  (`OrchestratorResult.title_mov/end_mov` + download mappings).
- Part B: `backend/services/original_audio.py` (new helper), orchestrator
  `_stage_original_audio_for_upload` + two `OrchestratorConfig` fields wired in
  `create_orchestrator_config_from_job`. Staged only when a folder upload will occur.
- Part C: `backend/scripts/backfill_original_audio.py` (dry-run default; `--live`),
  `DropboxService.file_exists`.
- Local encoding backend leaves `title_mov_path`/`end_mov_path` as None (production is GCE;
  the local CLI/monolith already writes `.mov`s into the track dir itself).

## Risks / notes
- Dropbox storage grows (large FLACs re-included) — intended per decision.
- `.mov` regeneration on GCE adds a couple of small copies, negligible cost.
- Backfill depends on original audio still existing in GCS; older jobs may have none → skipped.
- Keep all new steps non-fatal so distribution never fails for an archival-file hiccup.
