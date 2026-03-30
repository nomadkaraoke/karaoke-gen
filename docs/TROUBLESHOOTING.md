# Troubleshooting

Operational runbooks for known production issues.

---

## Job stuck at `downloading_audio` status

**Cause:** Before v0.130.0, audio downloads ran as FastAPI BackgroundTasks. Cloud Run would terminate "idle" instances mid-download. Since v0.130.0, downloads use a Cloud Run Job (`audio-download-job`) and a Cloud Scheduler recovery job runs every 5 minutes to fail stuck downloads automatically.

**Auto-recovery:** The `recover-stuck-downloads` scheduler detects jobs stuck in `downloading_audio` for >10 minutes and fails them. Use the admin retry button to re-attempt.

**Manual recovery:**

```bash
# 1. Check for stuck downloads
curl -s "https://api.nomadkaraoke.com/api/health/job-consistency" \
  -H "X-Admin-Token: $(gcloud secrets versions access latest --secret=admin-tokens --project=nomadkaraoke)" \
  | python3 -m json.tool | grep -A3 "downloading_audio_stuck"

# 2. Trigger recovery manually (if scheduler hasn't run yet)
curl -X POST "https://api.nomadkaraoke.com/api/internal/recover-stuck-jobs" \
  -H "X-Admin-Token: $(gcloud secrets versions access latest --secret=admin-tokens --project=nomadkaraoke)"

# 3. Retry the failed job via admin dashboard or API
curl -X POST "https://api.nomadkaraoke.com/api/jobs/YOUR_JOB_ID/retry" \
  -H "X-Admin-Token: $(gcloud secrets versions access latest --secret=admin-tokens --project=nomadkaraoke)"
```

---

## Job stuck at `encoding` status

**Cause:** A Cloud Run deployment killed the poller mid-encoding. The GCE worker finished but nobody received the result. The health service will flag this as `encoding_stuck` after 50 minutes.

**Recovery:**

```bash
# 1. Confirm the job is flagged
curl -s "https://api.nomadkaraoke.com/api/health/job-consistency" \
  -H "X-Admin-Token: $(gcloud secrets versions access latest --secret=admin-tokens --project=nomadkaraoke)" \
  | python3 -m json.tool | grep -A3 "encoding_stuck"

# 2. Check what the GCE worker knows about the job
WORKER_URL=$(gcloud secrets versions access latest --secret=encoding-worker-url --project=nomadkaraoke)
WORKER_KEY=$(gcloud secrets versions access latest --secret=encoding-worker-api-key --project=nomadkaraoke)
curl -s "$WORKER_URL/status/YOUR_JOB_ID" -H "X-API-Key: $WORKER_KEY" | python3 -m json.tool

# 3. Re-trigger the video worker — it will pick up the cached GCE result if encoding
#    already finished, or rejoin the poll if it's still running
curl -X POST "https://api.nomadkaraoke.com/api/internal/workers/video" \
  -H "X-Admin-Token: $(gcloud secrets versions access latest --secret=admin-tokens --project=nomadkaraoke)" \
  -H "Content-Type: application/json" \
  -d '{"job_id": "YOUR_JOB_ID"}'
```

**Note:** SSH-restarting the encoding worker is **not needed** since v0.119.6 (PR #413). The `/encode` endpoint is now idempotent — re-triggering the video worker is sufficient.

**Prevention:** The video worker now runs as a **Cloud Run Job** (`USE_CLOUD_RUN_JOBS_FOR_VIDEO=true`), which runs to completion and is immune to Cloud Run Service deployment rollouts. This replaces the `BackgroundTask` pattern that was vulnerable to instance termination during deployments. Additionally, CI performs a graceful drain before restarting the GCE encoding worker, the encoding client retries for ~90s, and status polling tolerates up to 5 consecutive failures.

---

## CDG/TXT packages missing from completed job

**Cause:** Before v0.119.7, CDG/TXT generation failures were silently caught. Jobs would complete with `enable_cdg=True` but no CDG ZIP in outputs. Fixed in v0.119.7 with fail-fast validation — new jobs will now fail loudly if CDG/TXT generation fails.

**Recovery** (for jobs that already completed without CDG/TXT):

```bash
# Regenerate and distribute CDG/TXT packages for specific jobs
GCS_BUCKET_NAME=karaoke-gen-storage-nomadkaraoke \
GOOGLE_CLOUD_PROJECT=nomadkaraoke \
python -m scripts.regenerate_cdg JOB_ID [JOB_ID ...]

# Example:
GCS_BUCKET_NAME=karaoke-gen-storage-nomadkaraoke \
GOOGLE_CLOUD_PROJECT=nomadkaraoke \
python -m scripts.regenerate_cdg 5b6aba25 5161b069
```

The script is idempotent — re-running it skips regeneration if the CDG ZIP already exists in GCS and proceeds to any missing distribution steps (GDrive, Dropbox).

---

## GCE encoding worker on wrong wheel version

**Cause:** Worker picks up a new deploy but doesn't restart automatically.

```bash
# Check current wheel version
gcloud compute ssh encoding-worker --zone=us-central1-c --project=nomadkaraoke \
  --command="curl -s http://localhost:8080/health | python3 -m json.tool"

# Restart to pick up latest wheel
gcloud compute ssh encoding-worker --zone=us-central1-c --project=nomadkaraoke \
  --command="sudo systemctl restart encoding-worker"
```

---

## GDrive validator reports sequence gap

**Symptoms:** Email from "Nomad Karaoke GDrive Validator" reporting `SEQUENCE GAPS: MP4: missing XXXX`.

**Key principle: Never add to `KNOWN_GAPS`.** All known gaps are historical (pre-generator, 2024). Every new gap is a real bug.

**Full investigation and fix procedure:** See [docs/GDRIVE-VALIDATOR.md § Sequence Gap Detected](GDRIVE-VALIDATOR.md#sequence-gap-detected).

**Quick reference:**
1. Query Firestore for the missing brand code (`state_data.brand_code == 'NOMAD-XXXX'`)
2. If no job found, check Cloud Run logs for `"Allocated brand code: NOMAD-XXXX"` to find when/how it was consumed
3. Determine root cause from logs (job re-trigger, failed distribution, etc.)
4. Recycle the brand code number into `brand_code_counters/NOMAD.recycled` — the next public job will fill the gap
5. Report any orphan GDrive files to the user for manual cleanup

---

## Google Drive uploads missing (gdrive_files empty)

**Symptoms:** Jobs complete successfully but `state_data.gdrive_files` is empty `{}`. Cloud Run logs show `BrokenPipeError` or `SSL: UNEXPECTED_EOF_WHILE_READING`.

**Cause:** Stale HTTP connections in the singleton `GoogleDriveService`. Since v0.119.7, this is handled automatically with retry + connection reset.

**Backfill affected jobs:**
```bash
# Dry run - list affected jobs
GCS_BUCKET_NAME=karaoke-gen-storage-nomadkaraoke \
GOOGLE_CLOUD_PROJECT=nomadkaraoke \
python scripts/backfill_gdrive_uploads.py --all-missing --dry-run

# Run for real
GCS_BUCKET_NAME=karaoke-gen-storage-nomadkaraoke \
GOOGLE_CLOUD_PROJECT=nomadkaraoke \
python scripts/backfill_gdrive_uploads.py --all-missing

# Specific jobs
python scripts/backfill_gdrive_uploads.py --job-ids JOB1,JOB2
```

---

## Job failed: "CDG generation was enabled but failed"

**Cause:** The LRC file has no lyrics content (just metadata like `[re:MidiCo]`). This happens when AudioShake returns 0 segments — typically because the input audio has no vocals (e.g. user uploaded a karaoke track).

**Fix (v0.135+):** The video orchestrator now skips CDG/TXT gracefully when the LRC has no content. The `complete_review` endpoint also blocks 0-segment submissions with a user-friendly error, and the frontend shows a warning with guidance.

**For older jobs:** Retry won't help unless the user provides different audio or pastes lyrics manually via "Replace All".

---

## Job failed: "Audio separation failed: expected str, bytes or os.PathLike object, not NoneType"

**Cause (historical):** Modal API intermittently returned fewer stems than expected from stage 2 (backing vocals) separation. Missing stems caused NoneType crashes in downstream processing. This was resolved by migrating to Cloud Run GPU (see `docs/archive/2026-03-22-modal-to-gcp-migration-plan.md`).

**Fix (v0.135+):** Stage 2 now validates that all expected stems are present. Defensive null checks prevent confusing TypeError messages. Full tracebacks are logged with `exc_info=True`.

**Recovery:** Retry the job. With the Cloud Run GPU deployment, transient API failures are much less frequent than they were with Modal.

---

## Job failed: "Lyrics transcription failed: 502 Server Error: Bad Gateway for url: https://api.audioshake.ai/tasks"

**Cause:** AudioShake API returned a transient 5xx error.

**Fix (v0.135+):** AudioShake API calls now retry up to 5 times with exponential backoff (60s base, 3x multiplier, ~40 minutes total spread). Retries on 5xx, 429, connection errors, and timeouts.

**For older jobs:** Simply retry the job via admin dashboard.

---

## Encoding worker not starting

**Symptoms:** Encoding jobs time out waiting for the VM to respond. The `/api/internal/encoding-worker/start` call returns success but the VM never accepts connections.

**Check Firestore config exists:**
```bash
python3 -c "
import os; os.environ['GOOGLE_CLOUD_PROJECT']='nomadkaraoke'
from google.cloud import firestore
doc = firestore.Client(project='nomadkaraoke').collection('config').document('encoding-worker').get()
print(doc.to_dict() if doc.exists else 'MISSING - run seed script')
"
```

**Check VM status:**
```bash
gcloud compute instances describe encoding-worker-a \
  --zone=us-central1-c --project=nomadkaraoke --format='value(status)'
```

**Check backend service account permissions:** The backend Cloud Run SA needs `compute.instances.start` on the encoding worker VMs. Verify in IAM or check Cloud Run logs for permission denied errors when the start call is made.

---

## Deploy stuck in progress (`deploy_in_progress: true`)

**Symptoms:** CI deploys fail immediately with "deploy already in progress". The flag in Firestore was never cleared after a previous failed deploy.

**Auto-clear:** The Cloud Function that manages idle shutdown also clears stale `deploy_in_progress` flags after 20 minutes. Wait for the next Cloud Scheduler tick (every 5 minutes) and the function will clear it.

**Manual clear:**
```bash
python3 << 'EOF'
import os; os.environ['GOOGLE_CLOUD_PROJECT']='nomadkaraoke'
from google.cloud import firestore
firestore.Client(project='nomadkaraoke').collection('config').document('encoding-worker').update({
    'deploy_in_progress': False
})
print("Cleared")
EOF
```

---

## Both VMs running unexpectedly

**Symptoms:** GCP billing alert, or you notice both `encoding-worker-a` and `encoding-worker-b` are RUNNING.

**Check Cloud Function logs:**
```bash
gcloud logging read 'resource.type="cloud_function" resource.labels.function_name="encoding-worker-idle-shutdown"' \
  --project=nomadkaraoke --limit=20 --format="table(timestamp,textPayload)"
```

**Verify scheduler is firing:** Go to Cloud Scheduler console and check the `encoding-worker-idle-shutdown` job's last execution time and status.

**Check activity timestamps in Firestore:** Look at `config/encoding-worker` — the `activity_a` and `activity_b` fields show when each VM last reported activity. If a timestamp is very recent, that VM is actively being used (or the heartbeat is stuck).

**Force idle check:**
```bash
gcloud scheduler jobs run encoding-worker-idle-shutdown \
  --location=us-central1 --project=nomadkaraoke
```

---

## Encoding requests failing after deploy

**Symptoms:** Jobs fail at encoding immediately after a blue-green deploy swap. Logs show connection refused or 404 to the old primary IP.

**Cause:** The backend caches the encoding worker URL for 30 seconds. Requests in-flight during the swap may hit the old (now stopped) primary.

**Fix:** Wait 30 seconds after the swap for the cache to expire. In-flight jobs can be retried via the admin dashboard — re-triggering the video worker will pick up the new primary URL.

**Verify the swap happened:**
```bash
python3 -c "
import os; os.environ['GOOGLE_CLOUD_PROJECT']='nomadkaraoke'
from google.cloud import firestore
doc = firestore.Client(project='nomadkaraoke').collection('config').document('encoding-worker').get()
print('primary:', doc.to_dict().get('primary'))
"
```

---

## Surgical job repair: re-run audio separation without losing lyrics/review

**When to use:** Jobs where audio separation failed or produced incomplete stems, but the user has already completed lyrics review. The admin `/restart` endpoint clears ALL state including lyrics — use direct Firestore updates instead.

**Approach:** Clear only audio-related state, trigger the Cloud Run GPU audio job, then advance the job to the correct next step.

```python
import os
os.environ["GOOGLE_CLOUD_PROJECT"] = "nomadkaraoke"
from google.cloud import firestore, run_v2
from google.cloud.firestore_v1 import DELETE_FIELD, ArrayUnion
from datetime import datetime, timezone

db = firestore.Client(project="nomadkaraoke")
job_id = "YOUR_JOB_ID"

# Step 1: Clear audio state only (preserves lyrics, review, instrumental selection)
job_ref = db.collection("jobs").document(job_id)
job_ref.update({
    "status": "downloading",  # Required: idempotency checks reject terminal states
    "state_data.audio_progress": DELETE_FIELD,
    "state_data.audio_complete": False,
    "state_data.instrumental_options": DELETE_FIELD,
    "state_data.backing_vocals_analysis": DELETE_FIELD,
    "file_urls.stems": DELETE_FIELD,
    "error_message": DELETE_FIELD,
    "error_details": DELETE_FIELD,
    "progress": 15,
    "timeline": ArrayUnion([{
        "status": "downloading",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": "Admin repair: re-running audio separation",
    }]),
})

# Step 2: Trigger Cloud Run GPU audio job directly
# (bypasses internal HTTP endpoint which has idempotency checks)
client = run_v2.JobsClient()
client.run_job(request=run_v2.RunJobRequest(
    name="projects/nomadkaraoke/locations/us-east4/jobs/audio-separation-job",
    overrides=run_v2.RunJobRequest.Overrides(
        container_overrides=[run_v2.RunJobRequest.Overrides.ContainerOverride(
            args=["python", "-m", "backend.workers.audio_worker", "--job-id", job_id],
        )]
    )
))

# Step 3: After audio completes, advance the job based on its previous state:
# - If job was "failed" with lyrics: trigger screens worker → awaiting_review
# - If job was "instrumental_selected": set status back, trigger video worker
```

**Key gotchas:**
- Must set `status` to a non-terminal state (`"downloading"`) before triggering workers
- `mark_audio_complete()` only sets a flag — it does NOT trigger the next pipeline step
- After audio completes, you must manually trigger screens (for review path) or video (for post-review path)
- The admin `/restart` endpoint with `preserve_audio_stems=false` is the simpler option when lyrics don't need preserving

---

## Triggering Cloud Run Jobs directly (bypassing internal API)

**When to use:** The internal worker endpoints (`/api/internal/workers/audio`, etc.) have idempotency checks that reject jobs in terminal states (`failed`, `complete`, etc.). When repairing jobs, trigger Cloud Run Jobs directly via the API.

```python
from google.cloud import run_v2

client = run_v2.JobsClient()
client.run_job(request=run_v2.RunJobRequest(
    name="projects/nomadkaraoke/locations/{REGION}/jobs/{JOB_NAME}",
    overrides=run_v2.RunJobRequest.Overrides(
        container_overrides=[run_v2.RunJobRequest.Overrides.ContainerOverride(
            args=["python", "-m", "backend.workers.{MODULE}", "--job-id", "{JOB_ID}"],
        )]
    )
))
```

**Available Cloud Run Jobs:**

| Job Name | Region | Module | GPU |
|----------|--------|--------|-----|
| `audio-separation-job` | `us-east4` | `audio_worker` | L4 |
| `lyrics-transcription-job` | `us-central1` | `lyrics_worker` | No |
| `video-encoding-job` | `us-central1` | `video_worker` | No |

---

## CI does not update `lyrics-transcription-job` image

**Symptoms:** Deployed code fixes don't take effect for lyrics processing. The backend service shows the new version, but lyrics jobs still crash with the old bug.

**Cause:** The CI workflow (`.github/workflows/ci.yml`) updates `audio-separation-job` and `video-encoding-job` after deploy, but does NOT update `lyrics-transcription-job`. The job uses the `:latest` tag, but Cloud Run Jobs resolve the tag to a digest at update time — pushing a new `:latest` image doesn't automatically update running jobs.

**Manual fix after deploy:**
```bash
# Pin to the specific version tag (preferred)
gcloud run jobs update lyrics-transcription-job \
  --image us-central1-docker.pkg.dev/nomadkaraoke/karaoke-repo/karaoke-backend:vX.Y.Z \
  --region us-central1 --project nomadkaraoke

# Or force re-resolve :latest
gcloud run jobs update lyrics-transcription-job \
  --image us-central1-docker.pkg.dev/nomadkaraoke/karaoke-repo/karaoke-backend:latest \
  --region us-central1 --project nomadkaraoke
```

**Permanent fix:** Add `lyrics-transcription-job` update to CI deploy step in `.github/workflows/ci.yml` alongside the existing `video-encoding-job` and `audio-separation-job` updates (~line 1568).
