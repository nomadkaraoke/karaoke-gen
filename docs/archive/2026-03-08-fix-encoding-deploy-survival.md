# Fix: Encoding Orchestration Killed by Cloud Run Deployments

**Date:** 2026-03-08
**Incident:** Job `43c0d519` (Westlife - Medley) stuck at `encoding` status for 4+ hours
**Branch:** `feat/sess-20260308-2013-fix-encoding-deploy-survival`

---

## Incident Analysis

### What Happened

1. **16:18:18** — Video worker HTTP request returned `200` after 80s. The encoding orchestration (submit to GCE, poll, download, upload) continued as a FastAPI `BackgroundTask`.
2. **16:19:19** — GitHub Actions deployed new Cloud Run revision `karaoke-backend-00573-glx`.
3. **16:19:25** — `DEPLOYMENT_ROLLOUT`: new instances started, old instances began draining.
4. **16:19:39** — Old instance (still running background encoding task) submitted encoding to GCE worker.
5. **16:20:10** — GCE worker connection failed (attempt 1/8). Old instance was being terminated.
6. **After 16:20:10** — No more logs. Instance killed. Job stuck permanently at `encoding` status.

### Why Existing Protections Didn't Help

The three-layer defense (PRs #413, #473) protects against the **GCE encoding worker** being restarted:

| Layer | What It Protects | Why It Didn't Help Here |
|-------|-----------------|------------------------|
| CI graceful drain | GCE worker restart waits for active FFmpeg jobs | The GCE worker wasn't restarted — Cloud Run was |
| Extended retries (90s) | Connection failures to GCE worker during restart | Cloud Run killed the instance before retries completed |
| Poll failure tolerance | Transient poll errors during GCE worker restart | The poller itself was killed, not just failing |

**The gap:** All protections assume the Cloud Run orchestrator stays alive. None protect against the orchestrator itself being killed during a deployment.

### Root Cause: `BackgroundTask` Pattern

The video worker endpoint (`internal.py:264`) uses FastAPI's `BackgroundTask`:

```python
background_tasks.add_task(generate_video, job_id)  # Fire-and-forget
return WorkerResponse(status="started", ...)        # HTTP response returns immediately
```

Cloud Run considers the request "complete" once the response is sent. During a deployment rollout:
- Old instances are **drained** (no new requests routed to them)
- Once no active HTTP requests remain, old instances are **terminated**
- `BackgroundTask` work is silently killed

The encoding orchestration (which takes 2-5+ minutes) runs entirely after the HTTP response, making it vulnerable to termination at any time.

### The Fix Already Exists (But Is Disabled)

**Cloud Run Jobs support was already built** in PR #155 (Dec 2025) for exactly this reason:

- **Infrastructure:** `video-encoding-job` Cloud Run Job provisioned via Pulumi (`infrastructure/modules/cloud_run.py:374`)
- **Code:** `worker_service.py:427` has `_trigger_cloud_run_job()` that dispatches to Cloud Run Jobs
- **Feature flag:** `USE_CLOUD_RUN_JOBS_FOR_VIDEO` env var controls the routing

Cloud Run Jobs run to completion (up to 24h) with no HTTP request lifecycle — they're immune to deployment rollouts.

**Why it's disabled:** The env var `USE_CLOUD_RUN_JOBS_FOR_VIDEO=true` was set in the old `cloudbuild.yaml` (commit `db2c5a03`) but was **dropped when CI migrated to GitHub Actions** (`.github/workflows/ci.yml`). The current CI env vars on line 1319 include `USE_GCE_ENCODING=true` but not `USE_CLOUD_RUN_JOBS_FOR_VIDEO=true`.

---

## Implementation Plan

### Option A: Re-enable Cloud Run Jobs for Video (Recommended)

**Effort:** Small (env var + verification)
**Risk:** Low (infrastructure and code already exist and were previously deployed)

#### Step 1: Add missing env var to CI

In `.github/workflows/ci.yml` line 1319, add `USE_CLOUD_RUN_JOBS_FOR_VIDEO=true` to the `--set-env-vars` string.

#### Step 2: Verify Cloud Run Job infrastructure is current

```bash
# Check the job exists and has the right image
gcloud run jobs describe video-encoding-job --region=us-central1 --project=nomadkaraoke
```

The Cloud Run Job uses the `latest` image tag, so it should already be using the current backend image. Verify:
- Image matches current backend image
- Timeout is 3600s (1 hour)
- Service account has correct permissions
- Environment variables include necessary secrets

#### Step 3: Ensure Cloud Run Job has all required env vars and secrets

The Cloud Run Job defined in Pulumi (`infrastructure/modules/cloud_run.py:407-416`) only has `GOOGLE_CLOUD_PROJECT` and `GCS_BUCKET_NAME`. It's missing critical env vars and secrets that the video worker needs:

- `USE_GCE_ENCODING=true`
- `ENCODING_WORKER_URL` and `ENCODING_WORKER_API_KEY` (secrets)
- `DEFAULT_DROPBOX_PATH`, `DEFAULT_GDRIVE_FOLDER_ID`, etc.
- `DEFAULT_ENABLE_YOUTUBE_UPLOAD`, `DEFAULT_BRAND_PREFIX`
- All the secrets the backend needs (Discord webhook, Stripe, etc.)

**This is likely why it was disabled** — the Cloud Run Job env was incomplete.

Options:
a. **Mirror all env vars/secrets from the Cloud Run Service** to the Job in Pulumi (tedious but correct)
b. **Have the Job fetch config from Secret Manager at runtime** (more elegant but requires code changes)
c. **Use a shared env config module in Pulumi** that both Service and Job reference

#### Step 4: Update the Cloud Run Job image in CI

The CI workflow deploys new images to Cloud Run Service but doesn't update the Cloud Run Job's image. Need to add a step:

```bash
gcloud run jobs update video-encoding-job \
  --image="${REGION}-docker.pkg.dev/${PROJECT_ID}/karaoke-repo/karaoke-backend:${VERSION}" \
  --region=us-central1 \
  --project=${PROJECT_ID}
```

#### Step 5: Test with a real job

1. Deploy the changes
2. Trigger a video worker for a test job
3. Verify it runs as a Cloud Run Job (check Cloud Run Jobs console)
4. Verify encoding completes and all outputs are uploaded

#### Step 6: Add deployment protection test

Create a test that:
- Triggers video encoding via Cloud Run Job
- Simulates a deployment (deploy new Cloud Run Service revision) mid-encoding
- Verifies the Job completes unaffected

### Option B: SIGTERM Handler + Re-enqueue (Alternative)

**Effort:** Medium
**Risk:** Medium (new code path, race conditions)

Add a SIGTERM handler to the Cloud Run Service that:
1. Catches SIGTERM during background task execution
2. Checks if encoding is in-flight
3. Re-triggers the video worker via Cloud Tasks (which will run on a new instance)

This is more complex and has edge cases (what if re-trigger also gets killed?). Option A is simpler.

### Option C: Keep Connection Open (Alternative)

**Effort:** Small
**Risk:** Low but has timeout implications

Instead of `background_tasks.add_task()`, run the video worker inline within the HTTP request handler. This keeps the connection active, preventing Cloud Run from killing the instance during drain.

```python
# Instead of:
background_tasks.add_task(generate_video, job_id)
return WorkerResponse(status="started", ...)

# Do:
success = await generate_video(job_id)
return WorkerResponse(status="completed" if success else "failed", ...)
```

**Pros:** Simple change, no infrastructure needed
**Cons:**
- Cloud Run request timeout is 1800s (30 min) — encoding can exceed this
- Cloud Tasks dispatch deadline might conflict
- The caller must keep the connection open for the full duration
- During drain, Cloud Run waits for active requests but has a grace period limit

---

## Recommendation

**Option A (re-enable Cloud Run Jobs)** is the right fix because:
1. Infrastructure already provisioned and code already written
2. Cloud Run Jobs are purpose-built for long-running batch work
3. Immune to deployment rollouts by design
4. Already used successfully for audio downloads (PR #496)

**However**, Step 3 (env vars/secrets) is the critical blocker — the Job needs the same configuration as the Service. This was likely why it was disabled after the cloudbuild.yaml migration.

**Immediate fix for job `43c0d519`:** Re-trigger the video worker manually:
```bash
curl -X POST "https://api.nomadkaraoke.com/api/internal/workers/video" \
  -H "X-Admin-Token: $(gcloud secrets versions access latest --secret=admin-tokens --project=nomadkaraoke)" \
  -H "Content-Type: application/json" \
  -d '{"job_id": "43c0d519"}'
```

---

## Files to Change

| File | Change |
|------|--------|
| `.github/workflows/ci.yml` | Add `USE_CLOUD_RUN_JOBS_FOR_VIDEO=true` to env vars |
| `.github/workflows/ci.yml` | Add step to update Cloud Run Job image on deploy |
| `infrastructure/modules/cloud_run.py` | Add all required env vars and secrets to `video-encoding-job` |
| `docs/LESSONS-LEARNED.md` | Add lesson about env vars dropped during CI migration |
| `docs/TROUBLESHOOTING.md` | Update "stuck at encoding" section with this new failure mode |

## Testing Strategy

- **Unit tests:** Verify `trigger_video_worker` routes to Cloud Run Jobs when `USE_CLOUD_RUN_JOBS_FOR_VIDEO=true`
- **Integration test:** Trigger video worker and verify Cloud Run Job is created
- **Production verification:** Run a test job end-to-end after deployment
