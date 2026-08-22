# Portrait (9:16) Karaoke Video Output — Design

**Date:** 2026-06-14
**Status:** Design (approved direction; pending spec review → implementation plan)
**Author:** Claude (with Andrew)
**Worktree:** `karaoke-gen-portrait-video` · branch `feat/sess-20260614-1550-portrait-video`

## 1. Problem & Goal

Customers have asked for **portrait-format** karaoke videos that are easier to view on
a phone and to share on social media (Reels / TikTok / Shorts). Today every job produces
only **landscape 16:9** finals.

**Goal:** add a portrait (9:16) karaoke video as an **additional output file** during
finalisation, stored in **GCS** and uploaded to the **final Dropbox brand-code folder
only** — *not* Google Drive or YouTube.

## 2. Decisions (confirmed with Andrew, 2026-06-14)

| Decision | Choice |
|---|---|
| **Approach** | Re-render at portrait resolution (NOT a transform of the finished landscape video) |
| **Layout** | Branded header (wordmark + song/artist) → lyrics in lower-center → `nomadkaraoke.com` footer, on a dark theme background |
| **Output scope** | **Instrumental karaoke only** — one portrait file per job (the version people sing to / share) |
| **Rollout** | **Default-on for every job** (no opt-in toggle) |
| **Destinations** | GCS output + Dropbox brand-code folder only |
| **Resolution** | 1080×1920 (H.264 / AAC, `+faststart`) |

## 3. Key Finding That Drives the Design

The karaoke video is `ffmpeg` compositing *background + instrumental + an ASS subtitle
filter*. **The ASS file bakes absolute pixel coordinates at 3840×2160** — lyrics centered
at x=1920, lines stacked at fixed Y positions (`\an8\pos(1920,477)` …). Because of this,
you **cannot** turn a finished landscape video into a good portrait one:

| Approach | Result | Evidence |
|---|---|---|
| Letterbox (pad 16:9 → 9:16) | ❌ Lyrics tiny, ~68% black bars | `comparison.png` (panel 1) |
| Center-crop (16:9 → 9:16) | ❌ Start/end of **every** line cut off | `comparison.png` (panel 2) |
| **Re-render at 1080×1920 with re-wrapped lyrics** | ✅ Large, readable, on-brand | `branded_still.png`, `comparison.png` (panel 3) |

The lyrics must be **re-laid-out** for the narrow frame. Validated empirically on a real
prod job (**piri – dog**, `052b94ab`) — see §8.

**Enabling fact:** the existing generator is already fully parameterized for this. The
portrait render reuses the *same engine* and the *same per-job corrected-lyrics data*;
it is an additive render path, not a new rendering system.

## 4. Architecture

Insert a **portrait render sub-step into `karaoke_finalise.process()`**, after the
landscape encodes and before/within the distribution (GCS + Dropbox) step. Finalisation
already operates on the job's local files (With Vocals video, instrumental, title/end
screens) and can receive a corrections-JSON path, so all inputs are in reach.

```
finalise.process()
  ├─ (existing) detect inputs, CDG/TXT, landscape encodes (lossless/lossy 4k, 720p)
  ├─ (NEW) render_portrait_video()                      ← this feature
  │     ├─ build portrait lyrics ASS  (1080×1920, re-wrapped)
  │     ├─ build portrait background  (branded, 1080×1920)
  │     ├─ build portrait title/end screens (1080×1920)
  │     └─ ffmpeg: title + [bg + ass + instrumental] + end  → portrait MP4
  └─ (existing) upload finals → GCS + Dropbox brand-code folder   (portrait file joins the set)
```

### 4.1 Components

1. **Portrait lyrics ASS generator.** Reuse `SubtitlesGenerator` / `SegmentResizer` at a
   portrait resolution tuple. Source of word-timed lyrics = the job's
   `corrections_updated.json` (`corrected_segments`). *(Recommended over transforming the
   existing landscape `karaoke.ass`, which would re-implement wrapping/positioning.)*
   Add a **portrait resolution preset** (the current `resolution_map` is landscape-only).
   - Recommended params from experiments (1080×1920): `font_size ≈ 88`, `line_height ≈ 118`,
     `max_visible_lines = 4`, `max_line_length ≈ 19`, `top_padding ≈ 709` (centers the
     lyric block in the open zone below the header).
2. **Portrait background.** A branded 1080×1920 background: dark theme gradient + neon
   Nomad wordmark + song/artist + `nomadkaraoke.com` footer + subtle neon border. Generated
   per-job (artist/title baked in), reusing theme brand assets. (Prototype: `build_bg.py`.)
3. **Portrait title/end screens.** The current `video_generator.py` renders 4K-landscape
   title/end via PIL with region-based text placement. Add portrait variants (portrait
   regions / canvas). Reuse the same theme params (`style_params.json`).
4. **Render + mux.** `ffmpeg` looping the portrait background + ASS subtitle filter +
   instrumental, concatenated with portrait title/end. H.264 / AAC, `yuv420p`, `+faststart`.
5. **Upload.** The portrait MP4 joins the outputs dict and the existing GCS + Dropbox
   upload (brand-code folder). Filename suffix e.g. `(Final Karaoke Portrait 1080x1920).mp4`.

### 4.2 Data flow

`corrections_updated.json` + `style_params.json` + instrumental (+ theme assets)
→ portrait ASS + portrait bg + portrait title/end
→ ffmpeg → portrait MP4 → GCS + Dropbox.

## 5. Error Handling & Rollout

- **Additive and non-fatal.** Portrait render failure must **not** fail the job or block
  the landscape outputs/distribution. On error: log, capture to the error monitor, and
  continue. The portrait file is a bonus, never a gate.
- **Default-on** for every finalised job. Adds ~15–30 s render + ~10 MB storage per job
  (see §7). No user toggle in v1.
- Idempotency: re-running finalise regenerates and re-uploads the portrait file
  deterministically (same naming), consistent with existing finals.

## 6. Testing Strategy

Per `docs/TESTING.md`:
- **Unit:** portrait param selection (resolution preset, font/line-height/top-padding);
  line-balancing/wrapping helper (no orphaned single words); filename/suffix; outputs-dict
  inclusion.
- **Integration:** render a **short** portrait clip from fixture corrected-lyrics + a few
  seconds of audio; assert dimensions 1080×1920, non-trivial duration, subtitle burn-in
  present (frame hash / pixel sample), `+faststart`.
- **Regression:** portrait output is added to the finalise outputs dict and is included in
  the GCS + Dropbox upload set; a portrait-render exception does NOT fail the job.
- **Production E2E (run-once):** finalise a real job, confirm a portrait MP4 lands in GCS
  and the Dropbox brand-code folder and is *absent* from Drive/YouTube.

## 7. Cost / Performance

Measured locally: a full **3:19** song rendered to 1080×1920 (libx264 `fast`, crf 20) in
**~19 s** (≈10× realtime), ~9.6 MB. Per-job overhead is modest and acceptable for
default-on. Negligible relative to the existing 4K landscape encodes.

## 8. Validation Evidence (real prod job)

Job **piri – dog** (`052b94ab`). Assets pulled from
`gs://karaoke-gen-storage-nomadkaraoke/jobs/052b94ab/` (corrected lyrics, theme params,
instrumental, title/end, landscape final). Artifacts in
`docs/archive/2026-06-14-portrait-video-experiments/`:
- `comparison.png` — the three approaches side-by-side (letterbox / crop / re-render)
- `branded_still.png` — the recommended portrait re-render (full frame)
- `render_portrait.py`, `build_bg.py`, `montage.py` — reproducible experiment scripts

## 9. Open Polish Items (for implementation)

- **Line-balancing** in the wrapper: the quick `max_line_length` pass occasionally orphans
  a short word (e.g. "No,"). Add balanced wrapping (avoid trailing 1–2-char lines).
- **Vertical fill** at sparse moments (1–2 active lines) — acceptable, but consider a
  subtle scroll/anchor so the block doesn't float.
- **Portrait title/end** region tuning (the landscape regions don't map 1:1).
- **Background**: ship the dark-gradient + wordmark direction; the neon-brick variant was
  considered and not chosen for v1.
- Confirm the **encoding-worker / finalise runtime** has the `lyrics_transcriber` generator
  + `corrections_updated.json` available locally (it receives a corrections-JSON path
  today); wire the fetch if needed.

## 10. Out of Scope (v1)

- With-vocals portrait, multiple portrait resolutions, per-job opt-in toggle, Drive/YouTube
  upload of portrait, short-form "highlight clip" auto-cutting.
