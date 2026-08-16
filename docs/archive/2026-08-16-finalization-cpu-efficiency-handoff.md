# Handoff: Investigate prod finalization CPU efficiency / utilization — 2026-08-16

**Status:** OPEN investigation (not started). Written for a fresh Claude session.
**Origin:** surfaced by the encode-worker multi-instance-type benchmark (see
`docs/archive/2026-08-15-encoding-instance-type-diversity-plan.md` and memory
`project_gen_encoding_benchmark_findings`).

## The question

Prod video **finalization** (the `/encode` path that produces
`mp4_4k_lossless + mp4_4k_lossy + mp4_720p`) may be **under-utilizing the CPU**:
the encode workers are now **32-vCPU** `*-highcpu-32` VMs, but the job config
still passes `ffmpeg_threads: 8` with a **stale comment referencing the retired
`c4-standard-8`** worker. If that 8 actually caps ffmpeg to 8 of 32 cores, every
render is leaving ~75% of the machine idle — a bigger, cross-cutting win than any
instance-type ranking, and it would apply to **all** machine families.

But it is NOT yet confirmed that `ffmpeg_threads: 8` even reaches the finalization
ffmpeg commands — resolving that ambiguity is step 1.

## What to determine (in order)

1. **Is `ffmpeg_threads` actually applied to the finalization encodes?**
   - Config is set at `backend/workers/video_worker.py:131`
     (`"ffmpeg_threads": 8,  # c4-standard-8 has 8 vCPUs`) and travels in
     `encoding_config` → `POST /encode` → `run_encoding()` in
     `backend/services/gce_encoding/main.py:955` → `LocalEncodingService`
     (in the installed **karaoke-gen wheel**).
   - The actual finalization ffmpeg commands live in the wheel at
     `karaoke_gen/karaoke_finalise/karaoke_finalise.py` — candidates:
     `:917` (4k **lossless**, `pcm_s16le`), `:899` (4k lossy, aac), `:956`
     (720p). **None of these obviously carry a `-threads` flag in a grep** —
     trace whether `config["ffmpeg_threads"]` is injected into them (via
     `LocalEncodingService`) or whether it is **dead config** and ffmpeg is
     already defaulting to `-threads 0` (all cores).
   - NOTE separate code paths that DO hardcode threads (don't confuse them with
     finalization): `backend/services/gce_encoding/main.py:372` hardcodes
     `-threads 8` in the **preview** (`/encode-preview`, 480×270) path; and
     `backend/services/local_preview_encoding_service.py:310` uses `-threads 0`
     (all) in the Cloud-Run **local** preview fallback.

2. **Measure real CPU utilization during a finalization.** SSH to a worker while
   it encodes and watch cores:
   ```bash
   gcloud compute ssh encoding-worker-fallback-c4a --zone=us-central1-a --project=nomadkaraoke \
     --command="mpstat -P ALL 2 3"   # or: top -bn1 | head -20 ; nproc
   ```
   Drive an encode with the canonical input (recreate it — it was cleaned up; see
   "Reproducing the benchmark" below). If all 32 cores are busy → `ffmpeg_threads`
   is not the bottleneck (it's dead config or already 0). If ~8 cores are busy →
   confirmed cap; raising it should speed finalization.

3. **Check whether the 3 output formats encode SERIALLY or in PARALLEL** within one
   job (`encode_all_formats` in the wheel). If serial, total time ≈ sum of the
   three; parallelizing independent format encodes (or one `-threads 0` encode)
   could use idle cores — but see the RAM constraint below.

4. **If under-utilized, decide the fix and validate it:**
   - Likely simplest: set `ffmpeg_threads` to `0` (all cores) or `32`, or make it
     dynamic (`nproc`). Confirm the wheel passes it through; if not, patch the
     wheel's `LocalEncodingService` to honor it (and/or default to `-threads 0`).
   - Re-benchmark before/after on the SAME input+VM to quantify the win.

## Hard constraints — do NOT regress these

- **Heavy-lane serialization stays.** `ENCODING_HEAVY_CONCURRENCY=1` (one heavy
  encode at a time per worker) exists because 3 **concurrent** 4K encodes OOM-killed
  the 32 GB worker (incident 2026-08-15, see `docs/LESSONS-LEARNED.md` "Concurrent
  encodes OOM-killed the worker" + memory `project_gen_encoding_worker_serialization`).
  Raising *threads within a single encode* is **orthogonal** to that and does not
  reintroduce concurrency — but it DOES raise a single encode's **peak RAM** (more
  parallel ffmpeg slices / parallel format encodes). Watch RSS on a 32 GB floor VM
  (n2/n2d fallbacks are 32 GB; c4d/c4/n4d are 60–64 GB). A single 4K lossless encode
  was ~18 GB; leave headroom.
- **Lossless 4K is the heavy one** (`pcm_s16le`, huge intermediate) — profile that
  format specifically.

## Reproducing the benchmark (for before/after numbers)

The benchmark used a real completed job's inputs as a canonical `/encode` input.
It was deleted after the run (`gs://…/bench/` cleaned up). To recreate:
```bash
J=gs://karaoke-gen-storage-nomadkaraoke/jobs/ffc17eb7   # "Glen Campbell - A Better Place"
I=gs://karaoke-gen-storage-nomadkaraoke/bench/encode-input
gsutil -q cp "$J/videos/with_vocals.mkv" "$I/videos/with_vocals.mkv"     # 119 MB rendered 4K karaoke video
gsutil -q cp "$J/screens/title.png"  "$I/screens/title.png"
gsutil -q cp "$J/screens/end.png"    "$I/screens/end.png"
gsutil -q cp "$J/stems/instrumental_clean.flac" "$I/stems/instrumental_clean.flac"
gsutil -q cp "$J/style/style_params.json" "$I/style/style_params.json"
gsutil -q cp "$J/lyrics/karaoke.ass" "$I/lyrics/karaoke.ass"
```
`POST /encode` body (worker port 8080, `X-API-Key` from
`gcloud secrets versions access latest --secret=encoding-worker-api-key`):
```json
{"job_id":"cpu-probe-1","input_gcs_path":"gs://…/bench/encode-input/",
 "output_gcs_path":"gs://…/bench/out/probe/",
 "encoding_config":{"formats":["mp4_4k_lossless","mp4_4k_lossy","mp4_720p"],
   "base_name":"Glen Campbell - A Better Place","artist":"Glen Campbell",
   "title":"A Better Place","instrumental_selection":"clean",
   "existing_instrumental":null,"ffmpeg_threads":8}}
```
Poll `GET /status/{job_id}` to `complete`. Baseline medians from 2026-08-16 (full
3-format finalization of this ~4-min song, `ffmpeg_threads:8`):
c4-Emerald ≈ **139s**, n2d-Milan ≈ **139s**, n2-CascadeLake ≈ **247s**, n2-IceLake
≈ **92s** (c4d unmeasured — could not start a 2nd c4d in the stockout). The huge
n2 spread (92↔247s, same SKU, different CPU platform) is a separate finding —
consider `min_cpu_platform` pinning; see memory `project_gen_encoding_benchmark_findings`.

**Operational hygiene:** benchmark VMs are stopped fallbacks — start, probe, then
STOP them (never the live-serving primary; the idle-shutdown fn also handles it).
Delete `gs://…/bench/**` when done. Auth for VM start/stop: `gcloud` as
`admin@nomadkaraoke.com`.

## Expected outcome

Either: (a) confirm CPU is already saturated (`ffmpeg_threads` is dead config) →
close this out and just fix the stale comment; or (b) confirm an ~2–4× idle-core
gap → raise threads / parallelize formats, validate the speedup and RAM headroom,
ship it. Bump `pyproject.toml` version; if the fix is in the wheel's
`LocalEncodingService`, it ships via the normal wheel → `ensure_latest_wheel` path.
