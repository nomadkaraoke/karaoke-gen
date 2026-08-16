# Handoff: Investigate prod finalization CPU efficiency / utilization — 2026-08-16

**Status:** ✅ RESOLVED 2026-08-16 — outcome (a): CPU already well-utilized (NOT
capped at 8 cores); removed dead `ffmpeg_threads` config. Empirical measurement +
resolution appended at the bottom under "RESOLUTION". The original investigation
brief follows unchanged for context.

**Status (original):** OPEN investigation (not started). Written for a fresh Claude session —
self-contained; all facts you need are inlined below or in the linked repo docs.
**Origin:** surfaced by the encode-worker multi-instance-type benchmark
(`docs/archive/2026-08-15-encoding-instance-type-diversity-plan.md`; benchmark
baselines inlined under "Reproducing the benchmark" below).

## The question

Prod video **finalization** (the `/encode` path that produces
`mp4_4k_lossless + mp4_4k_lossy + mp4_720p`) — is it using the 32-vCPU
`*-highcpu-32` workers efficiently? The trigger was the stale
`"ffmpeg_threads": 8` (with a comment referencing the retired `c4-standard-8`)
in the job config, which looked like it might cap ffmpeg to 8 of 32 cores.

**Static analysis has already answered the first-order question (do NOT re-derive
it — verify then move on):** `ffmpeg_threads` is **dead config**. It is set at
`backend/workers/video_worker.py:131` but **no code reads it** (grep for consumers
returns nothing; `run_encoding()`'s `EncodingConfig(...)` at
`backend/services/gce_encoding/main.py:1111` never passes it), and the actual
finalization ffmpeg commands in `backend/services/local_encoding_service.py`
(`encode_all_formats` → `encode_lossless_mp4`/`encode_lossy_mp4`/`encode_720p`,
around lines 343 / 396 / 471) carry **no `-threads` flag at all** → ffmpeg falls
back to its default (`-threads 0`, i.e. auto ≈ all cores). So finalization is
**almost certainly NOT capped at 8 cores**; the "leaving 75% idle" fear is likely
unfounded. (Separate, unrelated paths that DO hardcode threads — don't confuse
them: `main.py:372` uses `-threads 8` in the **preview** `/encode-preview`
480×270 path; `local_preview_encoding_service.py:310` uses `-threads 0`.)

So this is now a **narrower efficiency + cleanup** task, not a "fix the cap" task.

## What to determine (in order)

1. **Confirm the static finding empirically** (x264's `-threads 0` "auto" is a
   heuristic — it does NOT always scale to all cores; historically it caps frame
   threads, with diminishing returns past ~16). Drive a real finalization and
   capture **process-level** evidence, not just host-wide load:
   - the **deployed ffmpeg argv** for each format (`ps -eo pid,args | grep ffmpeg`
     on the worker mid-encode, or add a log line) — confirm no `-threads` and see
     what x264 chose;
   - **per-process / per-thread** CPU% and RSS (`top -H -p <ffmpeg-pid>`,
     `pidstat -t -p <pid> 2`), NOT `mpstat -P ALL` alone — host-wide utilization
     can't tell an 8-thread cap from serial `encode_all_formats` stages or
     unrelated work;
   - **per-format stage timings** (which of lossless/lossy/720p dominates).
   Run all of this against the SAME worker you drive the encode to (see the
   binding note in "Reproducing the benchmark").

2. **Check whether the 3 formats encode SERIALLY or in PARALLEL** in
   `encode_all_formats` (`backend/services/local_encoding_service.py`). If serial,
   wall-time ≈ sum of the three even if each saturates the cores — the real lever
   may be **overlapping independent format encodes** rather than more threads. But
   see the RAM constraint below before parallelizing.

3. **Decide the fix (likely small) and validate it:**
   - **Cleanup regardless:** remove the dead `ffmpeg_threads` field + stale comment
     at `backend/workers/video_worker.py:131` (it misleads; that is the "documentation-
     only" close-out CodeRabbit flagged). If you'd rather *use* it, wire it through
     `EncodingConfig` → the `local_encoding_service.py` ffmpeg commands as an explicit
     `-threads`; otherwise delete it.
   - If step 1 shows real idle cores (x264 auto under-scales), the win is an explicit
     high `-threads` (or `-x264-params threads=N`) or overlapping formats — validate
     with a before/after re-benchmark on the SAME input+VM.
   - If step 1 shows cores already saturated, close this out as "already efficient;
     removed dead config" and stop.

## Hard constraints — do NOT regress these

- **Heavy-lane serialization stays.** `ENCODING_HEAVY_CONCURRENCY=1` (one heavy
  encode at a time per worker) exists because 3 **concurrent** 4K encodes OOM-killed
  the 32 GB worker (incident 2026-08-15; full write-up in `docs/LESSONS-LEARNED.md`
  → "Concurrent encodes OOM-killed the worker → lost renders").
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
**Bind the encode to the worker you observe.** Pick ONE stopped fallback (e.g.
`encoding-worker-fallback-c4a`, us-central1-a), start it, and send BOTH the
`/encode` POST and the `/status` polls **directly to that worker's external IP**
(`http://<IP>:8080/…`, not through the backend/`api.nomadkaraoke.com` — the
backend may route to a different active worker). SSH the SAME VM for the
process-level probes in step 1, so the CPU/RSS you measure is the encode you drove.
Get the IP: `gcloud compute instances describe <vm> --zone=<z>
--format='value(networkInterfaces[0].accessConfigs[0].natIP)'`.

`POST http://<IP>:8080/encode` body (`X-API-Key` from
`gcloud secrets versions access latest --secret=encoding-worker-api-key`):
```json
{"job_id":"cpu-probe-1","input_gcs_path":"gs://…/bench/encode-input/",
 "output_gcs_path":"gs://…/bench/out/probe/",
 "encoding_config":{"formats":["mp4_4k_lossless","mp4_4k_lossy","mp4_720p"],
   "base_name":"Glen Campbell - A Better Place","artist":"Glen Campbell",
   "title":"A Better Place","instrumental_selection":"clean",
   "existing_instrumental":null}}
```
(`ffmpeg_threads` deliberately omitted from the body above — it is dead config;
see "The question".) Poll `GET http://<IP>:8080/status/{job_id}` to `complete`. Baseline medians from 2026-08-16 (full
3-format finalization of this ~4-min song; the runs sent `ffmpeg_threads:8` in the
body but the worker ignored it, so these reflect ffmpeg's default all-core threading):
c4-Emerald ≈ **139s**, n2d-Milan ≈ **139s**, n2-CascadeLake ≈ **247s**, n2-IceLake
≈ **92s** (c4d unmeasured — could not start a 2nd c4d in the stockout). The huge
n2 spread — **92s vs 247s for the identical `n2-highcpu-32` SKU** — is because n2
spans Intel Ice Lake (fast) and Cascade Lake (slow) and GCE assigns either; that
intra-family variance exceeds the between-family differences. Separate actionable
finding: pin `min_cpu_platform` (e.g. "Intel Ice Lake" / "AMD Milan") on n2/n2d
fallbacks for predictable speed, or rank them lower to reflect the gamble. Also
note the counterintuitive result that c4 (newest Intel Emerald) ≈ n2d (Milan) and
did NOT beat n2 Ice Lake for this lossless-4K-heavy workload — so "newest = fastest"
does not hold here; measure, don't assume.

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

---

## RESOLUTION (2026-08-16) — outcome (a): already efficient; dead config removed

**Verdict: the "leaving 75% idle" fear is unfounded. Finalization uses ~21–25 of a
worker's 32 vCPUs during every heavy libx264 stage. No threading fix warranted.**

### 1. `ffmpeg_threads` is dead config — confirmed statically AND empirically
- `grep -rn ffmpeg_threads backend/` → exactly one hit (the setter at
  `video_worker.py:131`), zero consumers. `EncodingConfig(...)` at
  `gce_encoding/main.py:1111` never passes it.
- **Empirically**, the deployed ffmpeg argv captured mid-encode (`ps`/probe on the
  worker) carry **no `-threads` flag** on any finalization command — x264 runs its
  all-core auto default. Sample argv observed:
  - `… -i with_vocals.mkv -c:v libx264 -c:a copy … (With Vocals).mp4`
  - `… concat=n=3… -c:v libx264 -c:a pcm_s16le … (Final Karaoke Lossless 4k).mp4`
  - `… -i …Lossless 4k.mp4 -c:v copy -c:a aac … (Lossy 4k).mp4`
  - `… -c:v libx264 -vf scale=1280:720 -preset medium -tune animation … (Lossy 720p).mp4`

### 2. Process-level CPU measurement (the point of this task)
Method: started `encoding-worker-fallback-c4a` (c4-highcpu-32, 32 vCPU / 62 GB),
recreated the bench input, POSTed `/encode` **directly to the VM IP**, and sampled
`top -bH -d 2` on that same VM for the whole run. The authoritative signal is the
system-wide `%Cpu(s)` summary line (0–100% across all 32 cores):

| Stage (serial)            | ffmpeg cmd                         | `%Cpu us` (of 32) | ≈cores busy | peak RSS | ~duration |
|---------------------------|------------------------------------|-------------------|-------------|----------|-----------|
| "With Vocals" mp4 convert | `libx264 -c:a copy` (mkv→mp4)      | 64–70%            | ~21         | ~11 GB   | ~59 s     |
| **Lossless 4K concat**    | `libx264 -c:a pcm_s16le`           | 72–75%            | ~23         | ~9 GB    | ~45 s     |
| Lossy 4K                  | `-c:v copy -c:a aac` (stream copy) | 3–5%              | I/O-bound   | <0.1 GB  | ~4 s      |
| MKV (YouTube)             | `-c:v copy -c:a flac` (copy)       | low               | I/O-bound   | —        | ~2 s      |
| 720p                      | `libx264 scale=1280:720 -preset medium` | 76–78%       | ~25         | ~1.2 GB  | ~12 s     |

Full run: ~122 s of encoding + ~35 s GCS download/upload = **~158 s wall** (matches
the ~139 s c4 baseline plus I/O). An 8-core cap would show `us≈25%, idle≈75%`; we
observed the **opposite** (`us≈66–78%, idle≈22–34%`). Not capped.

### 3. Why the residual ~25–30% idle is NOT recoverable by more threads
The heavy processes already spawn 90–162 OS threads (`nlwp`); x264's auto default is
~1.5×cores. The idle is the structural ceiling of x264 **frame-threading**
(inter-frame dependencies + a partially-serial lookahead), not a thread cap. Forcing
`-threads 32`/`-x264-params threads=N` above auto gives negligible speed and can hurt
quality. **Formats are serial and dependency-chained** (`encode_all_formats` steps
3→{4,5,6}; lossy/mkv/720p all consume the lossless output), and the only overlap-able
work after lossless is two near-free stream copies (lossy, mkv) — so parallelizing
formats buys ~nothing and would raise peak RAM against the 32 GB fallback floor +
the `ENCODING_HEAVY_CONCURRENCY=1` OOM constraint. Not worth it.

### 4. What shipped
- Removed the dead `"ffmpeg_threads": 8` field + stale `c4-standard-8` comment at
  `video_worker.py:131`, replaced with a NOTE explaining the measured all-core
  behaviour (so nobody re-opens this). No behaviour change. Version bump only.

### 5. Follow-up worth its own task (NOT done here — out of scope, needs verification)
The single biggest CPU cost is the **"With Vocals" preview** — a full 4K libx264
re-encode (~59 s, longer than the lossless master itself). The source
`with_vocals.mkv` is **already H.264** (High 4:4:4 Predictive, 3840×2160, `yuv444p`).
If nothing downstream needs a re-encode / a different `pix_fmt`, this could become a
container remux (`-c:v copy`) and save ~40–50 s/job — a far bigger win than any
threading tweak. Requires confirming: (a) do players/consumers need `yuv420p` (4:4:4
mp4 is poorly supported), and (b) does `convert_mov_to_mp4` intentionally normalize
pix_fmt? Left for a dedicated investigation.

### Operational note
Bench VM stopped, `gs://…/bench/**` deleted after the run, per hygiene.
