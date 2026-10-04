# Storage retention: operations

Policy + design: `docs/ARCHITECTURE.md` § Storage Retention. Code:
`backend/services/storage_retention.py`, `backend/workers/storage_retention_worker.py`.

## Runs

| Mode | Trigger | Deletes |
|---|---|---|
| `job_purge` | Cloud Scheduler `storage-retention-daily` (10:30 UTC) → `POST /api/internal/storage-retention/run` → Cloud Run Job `storage-retention-job` (us-east4) | Regenerable files of jobs completed >30 days ago, up to `STORAGE_RETENTION_MAX_JOBS_PER_RUN` (50) per run. Real deletion is on (`STORAGE_RETENTION_DRY_RUN=false` in the CI `--set-env-vars` of the backend deploy — the API passes the flag to the job; that's the only place it's set). |
| `orphan` | Manual: `POST /api/internal/storage-retention/orphans` (dry-run) / `?dry_run=false&confirm=delete-orphan-job-folders` | Everything except `input/` in `jobs/{id}/` folders with no record in `jobs`, `jobs-dev` or `youtube_upload_queue` (checked live per folder) and nothing modified in the last 30 days. |

Manual trigger (admin token):

```bash
TOK=$(gcloud secrets versions access latest --secret=admin-tokens --project=nomadkaraoke | cut -d, -f1)
curl -X POST https://api.nomadkaraoke.com/api/internal/storage-retention/run -H "X-Admin-Token: $TOK"
curl -X POST https://api.nomadkaraoke.com/api/internal/storage-retention/orphans -H "X-Admin-Token: $TOK"   # dry-run
```

## Where the record is

- `gs://karaoke-gen-storage-nomadkaraoke/storage-retention/reports/<ts>-{dry-run,run}.json`,
  `<ts>-orphans-{dry-run,run}.json`: the plan/outcome of each pass (dry-runs included).
- `gs://karaoke-gen-storage-nomadkaraoke/storage-retention/deletion-logs/<ts>-<mode>.jsonl`: **one line
  per object a real run deleted (or failed to)**:
  `timestamp, run_id, mode (job_purge|orphan), job_id, path, size_bytes, generation, category
  (finals|videos|stems|review-audio|previews|screens-mov|encoded|quick|orphan-nonInput), result
  (deleted|failed), error`. Rewritten after every job/folder, so a crash leaves a complete record up to
  the last finished job.
- `.../deletion-logs/<ts>-<mode>.summary.json`: totals by category, job count, GiB, failures.
- Each purged job's Firestore doc: `storage_purge` (files + generations + `deletion_log`),
  `renders_purged_at`, `stems_purged_at`.

The `storage-retention/` prefix is never touched by retention code (it only deletes inside
`jobs/{id}/`, enforced by `assert_deletable`) and no lifecycle rule matches live objects there
(the rules are: age 7 on `temp/`+`uploads/`, and noncurrent versions after 7 days). Logs are kept
forever.

## Reviewing

```bash
B=gs://karaoke-gen-storage-nomadkaraoke/storage-retention/deletion-logs
gcloud storage ls "$B/"                                        # all runs
RUN=20261004T150000Z-job_purge                                 # pick one from the listing
gcloud storage cat "$B/$RUN.summary.json" | jq .               # one run's totals
gcloud storage cat "$B/$RUN.jsonl" | jq -s 'map(select(.result=="deleted")) | group_by(.category)
  | map({category: .[0].category, objects: length, gib: (map(.size_bytes)|add/1073741824)})'
gcloud storage cat "$B/$RUN.jsonl" | jq -s 'group_by(.job_id) | map({job: .[0].job_id,
  gib: (map(.size_bytes)|add/1073741824)}) | sort_by(-.gib)'   # per job
gcloud storage cat "$B/$RUN.jsonl" | jq -c 'select(.result=="failed")'
JOB=abc12345
gcloud storage cat "$B/*.jsonl" | jq -c --arg j "$JOB" 'select(.job_id==$j)'   # everything deleted for a job
```

Or: `python scripts/storage_retention_report.py` (all runs), `... <run_id>` (one run, per category and
job), `... --find <job_id|path>` (which run deleted it, with a restore command).

## Restoring

The bucket keeps deleted objects as noncurrent versions for 7 days, then soft delete keeps them 7 more
days. With the `generation` from the log:

```bash
OBJ='jobs/abc12345/finals/lossy_4k_mp4.mp4'; GEN=1790000000000000   # from a result=="deleted" log line
# within ~7 days (noncurrent version):
gcloud storage cp --no-clobber "gs://karaoke-gen-storage-nomadkaraoke/$OBJ#$GEN" "gs://karaoke-gen-storage-nomadkaraoke/$OBJ"
# days 7-14 (soft-deleted):
gcloud storage restore "gs://karaoke-gen-storage-nomadkaraoke/$OBJ#$GEN"
```

After restoring a purged job's files, prefer simply using "Regenerate video" (or
`POST /api/admin/jobs/{id}/regenerate`) — it rebuilds everything and clears the purge markers.
