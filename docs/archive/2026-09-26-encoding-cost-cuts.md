# Encoding-worker cost cuts (2026-09-26)

Part of the "whole GCP project under $300/mo" push (workspace plan:
`docs/archive/2026-09-26-gcp-under-300-plan.md`). The encoding VMs were costing
~$300/mo for ~5-20 jobs/day. Andrew approved all of these, including the speed
trade-offs.

## What changed

| | Before | After |
|---|---|---|
| Primary pair `encoding-worker-a`/`-b` | c4d-highcpu-32, on-demand | **c4d-highcpu-16, Spot** (STOP on preemption) |
| Fallbacks | 8 stopped VMs across 6 families (+8 static IPs, 8 boot disks) | **1**: `encoding-worker-fallback-c2df`, c2d-highcpu-16, **on-demand**, us-central1-f |
| Fleet | 10 VMs | 3 VMs |
| Idle shutdown | 15 min, checked every 5 min | **5 min, checked every 2 min** |
| CI worker blue-green deploy | every push to main (~5x/day, boots a green + test encode) | only when `infrastructure/encoding-worker/worker_code_paths.txt` paths changed since the serving version; manual override via workflow_dispatch input `force_encoding_worker_deploy` |
| Nomad Dropbox upload | whole output folder | minus the lossless 4K **MP4** (`DROPBOX_SKIP_OUTPUT_SUFFIXES`); tenant folders unchanged |

Dropped from scope by Andrew mid-task: moving review previews off the VMs
(`USE_GCE_PREVIEW_ENCODING` stays `true`) and removing the review-page warmup. The
warmup is deliberate (VM warm for preview + final render), so instead the review
page now heartbeats every 2 min while the tab is visible and was used in the last
15 min (`frontend/lib/lyrics-review/hooks/useEncodingWorkerKeepAlive.ts`), keeping
the VM inside the new 5-min idle window during a review. Previously the heartbeat
only fired on lyric edits.

## How it was applied (infra)

- **Spot conversion without recreating the VMs.** Pulumi (pulumi-gcp 9.22.0)
  treats a `scheduling` change as *replace*; recreating a c4d VM risks a stockout
  at create time (and the VM would be gone). Instead each VM was converted in
  place while STOPPED:
  `gcloud compute instances set-scheduling <vm> --zone us-central1-c --preemptible --provisioning-model=SPOT --instance-termination-action=STOP --no-restart-on-failure --maintenance-policy=TERMINATE`,
  then `pulumi refresh --target <vm>` (state now matches the code) and a targeted
  `pulumi up` for the in-place machine-type change (`allow_stopping_for_update`).
  Note `--preemptible` is required alongside `--provisioning-model=SPOT`.
- Fallback removal: targeted `pulumi up` deleted 7 VMs (+ their auto-delete boot
  disks) and 7 static IPs; c2df resized in place to c2d-highcpu-16.
- `encoding-worker-fallback-vms` secret → version 6 (only c2df). The value is
  managed manually (Pulumi only creates the secret).
- Idle function env + scheduler updated by Pulumi; `main.py` default also 5.

## Behaviour notes

- **Spot preemption mid-encode**: `EncodingService.wait_for_completion` now checks
  the pinned worker VM's status on a failed poll; if it's STOPPING/TERMINATED/etc.
  it raises `EncodingJobLostError`, so `run_with_lost_job_resubmit` resubmits the
  encode (fresh `_retry_` id; outputs overwrite the same GCS paths). Before, a
  vanished VM surfaced after ~7 min as a non-resubmitted "lost contact" failure.
- **Stale fallback entries** (a removed VM still listed in an instance's cached
  `ENCODING_WORKER_FALLBACK_VMS`) are now skipped (`NOT_FOUND` start error)
  instead of aborting the whole failover.
- `SPEED_RANK` is keyed by machine *family* now, so the ranking survives vCPU
  resizes.
- 16 vCPU: final encodes/renders will be slower (x264 kept ~66-78% of 32 vCPUs
  busy). RAM is ~30-32 GB — one 4K encode peaks ~18 GB and heavy jobs are
  serialized (`ENCODING_HEAVY_CONCURRENCY=1`), so no OOM risk.

## Dropbox lossless (item 7) — why only the MP4

No automated consumer reads lossless files from Dropbox (YouTube reads the local /
GCS MKV; GDrive, the NOMAD master mirror, kjbox and the AWS backup use GCS or
lossy files). But the lossless **MKV** is promised *in the Dropbox folder* by the
customer completion email (`backend/services/template_service.py`,
`backend/translations/*.json` `fileList`) and by the live Fiverr auto-reply bot
(`fiverrbot/fiverrbot/triage/prompts.py` "What's delivered"), and B2B tenant
VocalStar expects lossless. So the default skips only the lossless 4K MP4 (not
promised anywhere in Dropbox) and only for Nomad's own folders. To also skip the
MKV: update those two texts, then set
`DROPBOX_SKIP_OUTPUT_SUFFIXES=" (Final Karaoke Lossless 4k).mp4| (Final Karaoke Lossless 4k).mkv"`
on the `video-encoding-job` (Pulumi `infrastructure/modules/cloud_run.py`) or
change the default in `backend/config.py`.

## Open risks

- Spot capacity for c4d in us-central1-c can disappear; then all work lands on
  the single on-demand c2d fallback (slower, and a single lane).
- Two consecutive preemptions of the same encode exhaust `ENCODING_RESUBMIT_MAX=2`.
- Skipped CI worker deploys mean `config/encoding-worker.primary_version` can lag
  the backend version; VMs still boot the latest wheel from GCS.
