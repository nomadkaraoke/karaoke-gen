# Audio Separator Cloud Run GPU Architecture

**Date:** 2026-03-25 (updated 2026-03-26)
**Status:** Implemented and deployed
**Repos:** python-audio-separator, karaoke-gen (infrastructure)

## Problem

The audio separator service had two issues:
1. **Client timeout**: The POST timeout (300s) was too short for ensemble presets on large WAV files (>5 min processing)
2. **No multi-instance scaling**: In-memory job status store and no concurrency protection prevented scaling

## Solution: Synchronous + Cloud Run Autoscaling (Modal Pattern)

The architecture mirrors how Modal handled this: **each separation request occupies one GPU instance** for its full duration, and the platform (Cloud Run) handles scaling to multiple instances for concurrent jobs.

### How it works

1. Client POSTs to `/separate` with `timeout=1800s`
2. Server downloads audio (from GCS URI or upload), runs separation synchronously
3. Server uploads output files to GCS, updates Firestore with status
4. Server returns completed result to client
5. Cloud Run sees the request as "active" for 3-5 minutes, so with `concurrency=1`, it scales to new instances for new requests
6. Up to 5 GPU instances run in parallel (Cloud Run GPU limit)

### Key infrastructure

| Component | Detail |
|-----------|--------|
| **Cloud Run GPU** | L4, us-east4, concurrency=1, max_instances=5 |
| **Firestore** | `audio_separation_jobs` collection — job status for observability |
| **GCS** | `nomadkaraoke-audio-separator-outputs` bucket — output files (1-day lifecycle) |
| **Client timeout** | 1800s POST timeout (matches Cloud Run request timeout) |

### Why synchronous, not fire-and-forget

We initially tried fire-and-forget (background threads + polling), but Cloud Run can't see background threads as "busy". With `concurrency=50` and fire-and-forget, all requests routed to one instance and queued behind a GPU semaphore — defeating the purpose of multi-instance scaling. Making the endpoint synchronous lets Cloud Run's autoscaler work naturally.

### Why no GPU semaphore

With `concurrency=1`, Cloud Run guarantees only one active request per instance. Each instance has its own GPU. No application-level serialization needed.

## Cross-repo changes

### python-audio-separator
- `deploy_cloudrun.py` — synchronous endpoint, Firestore status, GCS output upload
- `api_client.py` — 1800s POST timeout
- `job_store.py` — Firestore-backed job status store
- `output_store.py` — GCS output file management
- `Dockerfile.cloudrun` — google-cloud-firestore dependency

### karaoke-gen (infrastructure)
- `audio_separator_service.py` — concurrency=1, GCS bucket, Firestore IAM, env vars
- `database.py` — Firestore composite index for cleanup queries

## Phase 2: Migrate to Cloud Run Jobs

The current synchronous-service approach works but is not the ideal architecture. Cloud Run Jobs would be a natural fit — karaoke-gen already uses them for the audio worker itself. Benefits:
- No HTTP timeout concern (Jobs run up to 24h, no client connection needed)
- Native scaling (each execution gets its own container/GPU)
- Fire-and-forget + poll pattern (submit job, poll Firestore, download from GCS)
- Eliminates the `concurrency=1` workaround for autoscaling

The Firestore job store and GCS output store built in this session are the right foundations — they'd be reused directly by the Jobs-based approach.

## Lessons learned

1. **Cloud Run can't track background threads** — fire-and-forget makes the instance look idle, so the autoscaler doesn't scale. If you need the platform to scale for you, keep the request active.
2. **Match the platform's scaling model** — Modal's `.spawn()` gave each job its own container. Cloud Run's `concurrency=1` with synchronous processing achieves the same effect.
3. **The GPU semaphore was the wrong abstraction** — it serialized work on one instance instead of distributing across instances. Let the platform handle concurrency.
