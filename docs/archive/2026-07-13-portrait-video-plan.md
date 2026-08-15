# Plan: Portrait (9:16) Karaoke Video Output

**Created:** 2026-07-13
**Branch:** `feat/sess-20260614-1550-portrait-video`
**Status:** Draft
**Design:** `docs/archive/2026-06-14-portrait-video-design.md` (approved)

## Overview

Add a **portrait 1080×1920 karaoke video** as an additional output of every finalised job,
stored in GCS and uploaded to the Dropbox brand-code folder only (not Drive/YouTube). It is
a **re-render** at portrait resolution with re-wrapped lyrics — not a transform of the
finished landscape video (proven necessary in the design experiments). It reuses the
existing `SubtitlesGenerator`/`VideoGenerator` engine and the job's already-present
corrected-lyrics + theme data.

## Requirements

- [ ] Produce `{base} (Final Karaoke Portrait 1080x1920).mp4` (H.264/AAC, yuv420p, +faststart) for the **instrumental karaoke** version.
- [ ] Portrait layout: branded header (wordmark + song/artist), lyrics in the lower-center, `nomadkaraoke.com` footer, dark theme background.
- [ ] Lyrics re-wrapped for the narrow frame with **balanced** wrapping (no orphaned 1–2-char lines).
- [ ] Uploaded to GCS (`jobs/{id}/finals/`) and the Dropbox brand-code folder; included in job result/state so the dashboard/email can surface it.
- [ ] **Default-on** for every job; **non-fatal** (portrait failure must never fail the job or block landscape outputs/distribution).
- [ ] Render cost stays modest (~15–30s/job, ~10 MB).

## Technical Approach

### Insertion point (confirmed)
The GCE encoder `backend/services/gce_encoding/main.py::run_render_video` **already** loads a
`CorrectionResult` (corrections JSON), the countdown-padded audio, and the theme styles, and
runs `OutputGenerator` to render the landscape with-vocals video + `karaoke.ass`. So every
input a portrait render needs is already resolved on the encoder — **no new data path**.

Add a **self-contained portrait render op**, `run_render_portrait`, on the GCE encoder,
orchestrated as an additional step after the existing render/encode. It:
- Reuses the same asset-loading as `run_render_video` (corrections → segments, style,
  countdown processing) but targets the **instrumental** audio + the portrait screens.
- Is guarded try/except → on failure, log + report to error-monitor + continue (bonus output);
  it never blocks the landscape outputs or distribution.

The orchestrator (`backend/workers/video_worker_orchestrator.py` — same place the
original-vocals guide is emitted) triggers the portrait op and records/uploads the result,
mirroring that existing pattern. The shared ffmpeg assembly lives in a reusable helper so the
local CLI path can call it too.

### New component: portrait render
A `PortraitRenderer` (new module, e.g. `karaoke_gen/portrait/portrait_renderer.py`) that:
1. **Portrait lyrics ASS** — load `corrected_segments` from `corrections_updated.json`
   (fallback: the landscape `lyrics/karaoke.ass` is present if corrections JSON is absent —
   see Open Questions), re-wrap via `SegmentResizer(max_line_length≈19)` with an added
   balanced-wrap pass, then `SubtitlesGenerator(video_resolution=(1080,1920), font_size≈88,
   line_height≈118, styles{max_visible_lines:4, top_padding≈709})`.
2. **Portrait background** — 1080×1920 branded PNG: dark gradient + neon Nomad wordmark +
   song/artist + `nomadkaraoke.com` footer + subtle border. Port `build_bg.py` (experiment)
   to a themed generator driven by `style_params.json` + brand assets.
3. **Portrait title/end screens** — add portrait variants to `video_generator.py`
   (currently 4K-landscape, region-based). Reuse theme params; portrait regions.
4. **Assemble** — ffmpeg: title + [bg loop + `ass=` filter + instrumental] + end → portrait MP4.
5. Return the local path; orchestrator uploads to GCS + Dropbox and records it in the result.

### Add a portrait resolution path
`generator.py::_get_video_params` only knows landscape presets (4k/1080p/720p/360p). Rather
than overload that string map, the `PortraitRenderer` constructs `SubtitlesGenerator`/
`VideoGenerator` **directly** with the `(1080,1920)` tuple + explicit font/line-height (as the
experiment does), avoiding changes to the landscape presets.

## Implementation Steps

1. [ ] **Balanced wrapper** — add balanced line-wrapping (unit-tested) so no orphan lines.
3. [ ] **Portrait ASS generation** — `PortraitRenderer.build_ass()` from corrected segments;
   unit test dimensions/positions/wrap.
4. [ ] **Portrait background generator** — themed 1080×1920 bg from style params + brand assets.
5. [ ] **Portrait title/end screens** — portrait variants in `video_generator.py`.
6. [ ] **Assemble portrait MP4** — ffmpeg concat/mux in `local_encoding_service` (or renderer);
   integration test on a short fixture.
7. [ ] **Wire into orchestrator** — call renderer, upload to GCS `finals/` + Dropbox brand
   folder, add to result/state; guard non-fatal.
8. [ ] **Tests** — unit + integration + regression (non-fatal; included in upload set); prod E2E.
9. [ ] **Version bump** + docs (`README.md` status, `ARCHITECTURE.md` data flow, LESSONS-LEARNED).

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `karaoke_gen/portrait/portrait_renderer.py` | Create | Portrait ASS + bg + title/end + assemble |
| `karaoke_gen/portrait/wrap.py` | Create | Balanced line-wrapping helper |
| `karaoke_gen/lyrics_transcriber/output/video_generator.py` | Modify | Portrait title/end screen variants |
| `backend/services/gce_encoding/main.py` | Modify | `run_render_portrait` op (reuses corrections/style loading) |
| `backend/workers/video_worker_orchestrator.py` | Modify | Trigger portrait op, upload GCS+Dropbox, record result, non-fatal guard |
| `tests/.../test_portrait_*.py` | Create | Unit + integration + regression |
| `frontend/e2e/production/portrait-output.spec.ts` | Create | Prod E2E: portrait in GCS+Dropbox, absent from Drive/YouTube |
| `pyproject.toml` | Modify | Version bump |
| `docs/README.md`, `docs/ARCHITECTURE.md`, `docs/LESSONS-LEARNED.md` | Modify | Status/data-flow/learnings |

## Testing Strategy

Per `docs/TESTING.md`:
- **Unit:** balanced wrapper (no orphans); portrait param selection; ASS output is 1080×1920,
  centered, correct visible-line count; filename/suffix; result-dict inclusion.
- **Integration:** render a short portrait clip from fixture corrected-lyrics + a few seconds
  of audio → assert 1080×1920, non-trivial duration, subtitle burn-in present, +faststart.
- **Regression:** portrait output added to result + upload set; a portrait-render exception
  does NOT fail the job (mock renderer to raise, assert job still completes with landscape).
- **Production E2E (run-once):** finalise a real job; portrait MP4 present in GCS + Dropbox
  brand folder, absent from Drive/YouTube.

## Open Questions

- [x] **#1 — Lyric data source at encode time.** RESOLVED: `gce_encoding/main.py::run_render_video`
  already downloads the corrections JSON (`original_corrections_gcs_path` +
  `updated_corrections_gcs_path`), applies user corrections → `CorrectionResult`, and runs
  countdown processing. The portrait op reuses this exact loading. No new data path needed.
- [ ] **#2 — Background asset source.** Reuse the experiment's wordmark-crop approach vs a
  clean brand PNG from the theme assets. Pick the cleaner brand-managed asset if one exists.
- [ ] **#3 — Filename/label** convention for the portrait file (proposed:
  `… (Final Karaoke Portrait 1080x1920).mp4`).

## Rollback Plan

Feature is additive and non-fatal. Rollback = env flag `PORTRAIT_RENDER_ENABLED=false`
(gate the orchestrator call) or revert the PR; landscape pipeline is untouched. No schema or
infra changes required beyond the new output file.
