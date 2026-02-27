# Troubleshooting

Operational runbooks for known production issues.

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
