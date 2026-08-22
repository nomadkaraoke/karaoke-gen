# "With Vocals" preview encode — faster, smaller, more portable (2026-08-16)

**Status:** ✅ SHIPPED (v0.196.0). Follow-up from the finalization-CPU investigation
(`2026-08-16-finalization-cpu-efficiency-handoff.md`), which identified the "With
Vocals" conversion as the single longest finalization stage.

## Problem

`convert_mov_to_mp4` produces the **"(With Vocals).mp4"** sing-along deliverable from
the renderer's source video. Process-level measurement on a live c4-highcpu-32 worker
showed this was the **longest single finalization stage (~59s)** — longer than the
lossless master itself.

Probing a real prod job (`faafd5ad`) revealed the encode was *accidentally* bad:
- **No `-pix_fmt`** → inherited the renderer's `High 4:4:4 Predictive / yuv444p`. This
  is the **only** 4:4:4 file in the deliverable set (every other output is `yuv420p`),
  and 4:4:4 H.264 is rejected by many browsers/TVs/phones.
- **Default `medium` preset** → slow.
- **`-c:a copy`** (LocalEncodingService path) → **FLAC-in-MP4**, which is non-standard,
  despite the customer-facing spec (`template_service.py`) documenting the file as
  "4k H264/**AAC**".

## Safety check — is the "With Vocals" mp4 used downstream?

**No.** Confirmed in both encode paths (cloud `encode_all_formats` and local
`KaraokeFinalise.remux_and_encode_output_video_files`): every later output (Karaoke.mp4,
Lossless/Lossy/720p, MKV) derives from the **source** `with_vocals` file (via
`remux_with_instrumental` → `Karaoke.mp4` → concat) and carries the **instrumental**
audio. YouTube upload uses `final_karaoke_lossless_mkv`. The "(With Vocals).mp4" is
written once and only uploaded/delivered — a leaf. So its encode can be chosen purely
for the deliverable.

## Fix

`convert_mov_to_mp4` software (CPU) command — the path that runs on prod workers
(no NVENC) — changed to:

```
-c:v libx264 -pix_fmt yuv420p -preset veryfast -c:a aac -ar 48000 -b:a 320k -movflags +faststart
```

Applied to **both** implementations (kept in sync):
- `backend/services/local_encoding_service.py::LocalEncodingService.convert_mov_to_mp4`
  (cloud worker — prod)
- `karaoke_gen/karaoke_finalise/karaoke_finalise.py::KaraokeFinalise.convert_mov_to_mp4`
  (local CLI; already emitted AAC, now also 4:2:0 + veryfast + 320k)

GPU/NVENC command: emits AAC 320k; `h264_nvenc` encodes 4:2:0 natively so no `-pix_fmt`
needed. (Prod workers are CPU-only; NVENC is a local/GPU path.)

## Measured result (real source: Arctic Monkeys – Riot Van, 138s 4K)

| Metric | Before (prod) | After |
|---|---|---|
| Encode time | 56 s | **29 s** (~2×) |
| File size | 22 MB | **14 MB** (~36% smaller) |
| Video | h264 High 4:4:4 / yuv444p | **h264 High / yuv420p** |
| Audio | FLAC-in-mp4 (~639 kbps) | **AAC-LC 48 kHz / 320 kbps** |

This is the longest finalization stage, so ~halving it cuts **~20-25% off total
finalization wall-time**. The file gets **smaller** (AAC 320k ≪ FLAC ~639k, and 4:2:0
< 4:4:4), so there is no size/CPU tradeoff — every axis improves, plus universal
playback and spec-alignment.

## Notes / non-goals
- Quality: for karaoke content (static background + text) `veryfast` vs `medium` is
  visually negligible; CRF-based, comparable bitrate.
- The finalise-only **user-upload** path (a user's own `.mov`/`.mkv`, arbitrary codec)
  still re-encodes correctly — the command is codec-agnostic (no stream-copy assumption).
- Not touched: whether a "reference" file needs to be 4K at all (a 1080p variant would
  be smaller still) — a product decision, left for later.
