# Direct GPU Audio Separation in Audio Worker

**Date:** 2026-03-26
**Status:** Approved, ready for implementation
**Repos:** karaoke-gen (primary), python-audio-separator (read-only reference)
**Branch:** feat/sess-20260326-1339-audio-sep-direct-gpu

## Problem

The audio worker Cloud Run Job calls a separate Cloud Run Service (audio-separator) over HTTP for GPU-accelerated stem separation. This architecture has proven fragile:

- 1800s HTTP timeout is the max Cloud Run allows; large files can exceed it
- Holding connections open for 5-10 minutes wastes resources
- `concurrency=1` forces a new $1/hr GPU instance per request
- Cold start delays add to the client's wait time
- A recent production outage (2026-03-25) was caused by the fire-and-forget/semaphore interaction with Cloud Run's autoscaler

## Solution

Run the `Separator` class directly inside the audio worker Cloud Run Job with an L4 GPU attached. This eliminates the HTTP hop entirely.

### Architecture Change

```
Current (3 hops):
  karaoke-gen backend → Cloud Run Job (audio worker, CPU, us-central1)
    → HTTP POST → Cloud Run Service (separator, GPU, us-east4)

New (1 hop):
  karaoke-gen backend → Cloud Run Job (audio worker, GPU, us-east4)
    → Separator class runs locally
```

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Docker image | Separate `karaoke-backend-gpu` image | Keeps regular backend lean; audio worker gets exactly what it needs |
| GPU region | us-east4 | L4 GPU quota already exists there; batch job tolerates cross-region latency |
| Model loading | Bake into image | Faster cold starts; model set rarely changes |
| Rollback strategy | Keep old service running | Phase 1 adds new path; old service removed in phase 2 |
| Code path | Reuse existing local Separator path | Already works; minimizes code changes and risk |

## Components

### 1. New Docker image: `karaoke-backend-gpu`

A GPU-capable base image for the audio worker. Based on `nvidia/cuda:12.6.3-runtime-ubuntu22.04`, includes everything from `karaoke-backend-base` plus GPU-specific dependencies.

**GPU dependencies (from audio-separator's Dockerfile.cloudrun):**
- PyTorch with CUDA support
- ONNX Runtime GPU (`onnxruntime-gpu`)
- `audio-separator[gpu]` package
- CUDA runtime libraries (already in base NVIDIA image)

**Baked-in models (~1.5GB total):**
- `instrumental_clean` preset models (Fv7z + Resurrection)
- `karaoke` preset models (3 karaoke models)
- Downloaded at build time via `scripts/download_preset_models.py` pattern from python-audio-separator

**Shared with karaoke-backend-base:**
- Python 3.11 (note: audio-separator uses 3.12; need to verify compatibility or align)
- FFmpeg (static build), sox, libsndfile
- Noto fonts (CJK, emoji, etc.)
- spaCy `en_core_web_sm` model
- All pip dependencies from karaoke-gen's pyproject.toml

**New files:**
- `backend/Dockerfile.gpu-base` — GPU base image definition
- `backend/cloudbuild-gpu-base.yaml` — Cloud Build config
- `backend/scripts/download_models.py` — Model download script for build time

**Artifact Registry:**
- New repo `karaoke-backend-gpu` in `us-east4` (co-located with GPU instances)

### 2. Infrastructure changes

All in `infrastructure/modules/cloud_run.py`:

**Audio worker Cloud Run Job modifications:**
- Move from `us-central1` to `us-east4` (GPU quota)
- Add L4 GPU: `nvidia.com/gpu: "1"`
- Increase resources: CPU 2→4, memory 4Gi→16Gi
- Change image to `us-east4-docker.pkg.dev/nomadkaraoke/karaoke-backend-gpu/karaoke-backend:latest`
- Remove `AUDIO_SEPARATOR_API_URL` env var (absence triggers local Separator path)
- Add `MODEL_DIR=/models` env var (tells Separator where baked-in models live)

**New resources in `infrastructure/`:**
- Artifact Registry repo for `karaoke-backend-gpu` in us-east4
- IAM for the existing audio-separator service account to pull from the new repo

### 3. Code changes

**`backend/workers/audio_worker.py`:**
- Remove the hard error when `AUDIO_SEPARATOR_API_URL` is not set (lines ~330-333)
- When `AUDIO_SEPARATOR_API_URL` is absent, configure `create_audio_processor()` with `model_file_dir` pointing to the baked-in models directory

**`backend/workers/audio_worker.py` — `create_audio_processor()`:**
- Accept optional `model_file_dir` parameter
- Pass `instrumental_preset` and `karaoke_preset` through to the processor (currently only used in remote path; the local path needs to use them too)
- When `model_file_dir` is set, the processor uses local `Separator`; when not set, it errors

**`karaoke_gen/audio_processor.py`:**
- In the local code path (lines ~200+), support `ensemble_preset` on the `Separator` constructor instead of only using explicit model lists
- The existing local path currently uses `self.clean_instrumental_model` / `self.backing_vocals_models` to call individual separation methods. With presets, we pass `ensemble_preset` to `Separator()` and call `separator.separate()` once — the preset handles model selection internally.
- Keep the file-lock mechanism (harmless in Cloud Run Jobs; one container = no contention)

### 4. What stays unchanged (phase 1)

- Audio-separator Cloud Run Service (kept for rollback)
- Firestore `audio_separation_jobs` collection and GCS output bucket
- Remote API code path in `audio_processor.py` (`_process_audio_separation_remote`)
- CI/CD for the existing `karaoke-backend` image
- All other Cloud Run Jobs (lyrics, video workers)

### 5. CI/CD

- Add a Cloud Build trigger for the GPU base image (triggered by changes to `backend/Dockerfile.gpu-base` or `pyproject.toml`)
- The existing app-layer build (`backend/Dockerfile`) works unchanged — just needs `BASE_IMAGE` build arg set to the GPU base for audio worker deploys
- GitHub Actions workflow addition: build and push the GPU image on merge to main

## Testing Strategy

**Unit tests:**
- Verify `create_audio_processor()` configures correctly with and without `AUDIO_SEPARATOR_API_URL`
- Verify `model_file_dir` is passed through when set
- Verify preset parameters reach the `Separator` constructor

**Local integration:**
- Build the GPU image locally (requires NVIDIA Docker runtime)
- Run a test separation with a sample audio file

**Production verification:**
- Submit a test job after deploy
- Verify all stems are generated: clean instrumental, vocals, lead vocals, backing vocals, combined instrumentals
- Compare output quality with a job processed through the old HTTP path

## Rollback Plan

If issues arise after deployment:
1. Add `AUDIO_SEPARATOR_API_URL` back to the audio worker job env vars
2. The code falls through to the remote API path
3. The old audio-separator service is still running (scale-to-zero, no cost)

No code changes needed for rollback — just an infra env var change via Pulumi.

## Phase 2 (follow-up PR)

After the direct GPU path is verified in production:
- Remove the audio-separator Cloud Run Service infrastructure
- Remove `AUDIO_SEPARATOR_API_URL` references from code
- Remove `_process_audio_separation_remote()` method
- Remove `AudioSeparatorAPIClient` import
- Clean up Firestore job store / GCS output bucket if no longer needed for observability

## Python Version

The audio-separator package requires Python >= 3.10. The GPU base image will use Python 3.11 (same as the existing karaoke-gen base) for consistency. The audio-separator's Dockerfile.cloudrun uses 3.12, but that's not a requirement — it was just the version chosen for that image.
