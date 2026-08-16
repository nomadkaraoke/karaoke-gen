# Troubleshooting

Operational runbooks for known production issues.

---

## Job stuck at `downloading_audio` status

**Cause:** Before v0.130.0, audio downloads ran as FastAPI BackgroundTasks. Cloud Run would terminate "idle" instances mid-download. Since v0.130.0, downloads use a Cloud Run Job (`audio-download-job`) and a Cloud Scheduler recovery job runs every 5 minutes to fail stuck downloads automatically.

**Auto-recovery:** The `recover-stuck-downloads` scheduler (`/api/internal/recover-stuck-jobs`, every 5 min) detects jobs stuck in `downloading_audio` for >10 minutes and, since v0.192.3:
- **Torrent sources (RED/OPS)** → parks the job in `download_pending_retry` and keeps re-attempting the download for up to **24 hours** (handles rare tracks with few/intermittent seeders and transient tracker outages), then fails permanently with a clear message. No manual action needed. See `download_pending_retry` below.
- **Other sources (YouTube/Spotify/URL)** → fails the job (deterministic); use the admin retry button to re-attempt.

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

Since v0.184.2, in-flight status polls are also **pinned to the worker that accepted the job** (`get_job_status(..., worker_url=)`), so a blue-green deploy that swaps the `active_url` primary pointer mid-render no longer migrates the poll to a worker that never received the job. If you still see `lost contact with worker after 5 consecutive poll failures: Encoding job <ID> not found`, confirm via step 2 above that the worker the job was submitted to (not necessarily the current primary) still has the job — a `not found` from the *current* primary while the job is alive on the old primary was the pre-v0.184.2 failure mode (incident 2026-06-16, job d3af33ae).

Since **v0.194.0**, a mid-run job loss is also **auto-recovered**: the encoding worker processes heavy renders/encodes **one at a time** (`heavy_executor`, `ENCODING_HEAVY_CONCURRENCY=1`) so it can no longer OOM from concurrent 4K encodes (the trigger for the 2026-08-15 Arctic Monkeys batch failure — 3 concurrent encodes OOM-killed the 32 GB fallback worker and restarted it, wiping its in-memory jobs). If the worker *does* restart mid-render (deploy/crash), `wait_for_completion` now raises `EncodingJobLostError` and the render/encode worker **resubmits the job under a fresh `<id>_retry_<hex8>`** (bounded by `ENCODING_RESUBMIT_MAX=2`) instead of failing. To confirm serialization is live on a worker: `curl -s localhost:8080/health` shows `queue_length` growing while `active_jobs` stays 1 under load. **Note:** `main.py` ships in the wheel but the running uvicorn process only picks up worker-side changes (executor split, `queue_position`) on a **fresh boot / service restart** — `ensure_latest_wheel` alone does not reload the app process.

Since **v0.194.2**, the CI deploy handles that restart automatically even during a c4d Spot stockout. The old blue-green only targeted the c4d primary/secondary; when both were down it rolled back and **never refreshed the serving n2 fallback** (recorded as `active_override_vm`), so worker-side changes didn't reach prod until a manual restart (`primary_version` in the config doc went stale). The deploy is now **capacity-aware** (`infrastructure/encoding-worker/deploy_promote.py`): it validates the new wheel on a *fresh* green worker selected from the **ranked 6-family pool** (`select_green_candidates` → the shared `encoding_worker_preference.ordered_candidates` — fastest-first with capacity cooldown; the c4d secondary if it can start, else the fastest available fallback family, never the current override so it keeps serving) — then **promotes** the green (primary/secondary swap for a c4d green, or sets `active_override` for a fallback green) and drains+stops the retired worker. A c4d green also **clears a stale override** so traffic returns to the fresh primary. Zero-downtime, and it works while c4d is exhausted. Last resort if no separate green can start: an in-place restart of the serving override (brief blip, covered by auto-resubmit).

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

## Render fails: `No module named '<pkg>'` (e.g. tenacity) on encoding worker

**Symptoms:** A render fails with `OutputGenerator not available: No module named 'tenacity'. The karaoke-gen wheel must be installed. Check that ensure_latest_wheel() succeeded.` (or another missing package). Retries hit the same VM and fail identically.

**Largely fixed in v0.195.0 (dep-bake):** the Packer image now bakes the **full** karaoke-gen dependency tree (with CPU-only torch) into the venv at build time, so a freshly-provisioned VM of any family boots self-sufficient — no first-job dependency install. This failure mode should now only appear on a VM whose boot disk predates the dep-bake image, or a venv left partial by an interrupted repair. The `ensure_latest_wheel()` runtime backstop below still exists for those cases.

**Original cause (pre-v0.195.0):** A freshly-provisioned VM came up with only a handful of packages baked in, and boot installed the wheel with `--no-deps`. The full dependency tree (torch, langchain, tenacity, …) was installed lazily at the first job by `ensure_latest_wheel()`; a failed/partial install left the venv importable-but-incomplete and the render died on the first missing import. First seen 2026-08-13 (job 233b9536) on a fresh `n2-highcpu-32` fallback added in #903.

**Fixed in v0.192.7:** `ensure_latest_wheel()` now downloads only the single latest wheel (was: copying the entire ~6 GiB `wheels/` dir every job with a 60 s timeout), retries the install (3× at 900 s), and **verifies the render/encode import chain** before returning — a clean `pip` exit is no longer trusted. Callers fail the job with a clear retryable error instead of proceeding. Fresh workers pick this up via the wheel-at-boot.

**Also fixed in v0.194.1 (concurrency race):** `ensure_latest_wheel()` runs on the *light* lane (>1 thread) at the start of every job, so on a fresh boot several jobs would call it at once and race on the shared `/tmp` wheel path (`gsutil cp` clobbered mid-copy) and the venv (two `pip install`s at once) — one job then failed with `wheel/dependencies not ready ... retry on a healthy worker` (2026-08-15, job 374dec26, 3 jobs on a fresh n2f boot). Now it's guarded by a process-wide lock **and** short-circuits when the installed version already matches the latest GCS wheel (the common case — startup.sh installs it at boot). So only the *first* job after a new wheel actually installs; the rest wait briefly then take the verified fast path. A single job failing this way can simply be retried once a worker is warm.

**Manual repair (if a running VM is stuck on an old wheel):** the venv is root-owned, so use `sudo`:

```bash
# Identify the running worker
gcloud compute instances list --project=nomadkaraoke --filter="name~encoding AND status=RUNNING"

# Repair: install the latest versioned wheel WITH deps, then verify
gcloud compute ssh <worker-name> --zone=<zone> --project=nomadkaraoke --tunnel-through-iap --command='
  V=$(gsutil ls "gs://karaoke-gen-storage-nomadkaraoke/wheels/karaoke_gen-*.whl" | grep -v current | sort -V | tail -1)
  WHEEL="/tmp/$(basename "$V")"   # keep the PEP 427 filename so pip accepts it
  gsutil cp "$V" "$WHEEL"
  sudo /opt/encoding-worker/venv/bin/python -m pip install --upgrade "$WHEEL"
  /opt/encoding-worker/venv/bin/python -c "import tenacity; from karaoke_gen.lyrics_transcriber.output.generator import OutputGenerator; from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations; from backend.services.local_encoding_service import LocalEncodingService; print(\"OK\")"
'
```

**Re-render a job that already failed past review:** a failed job is terminal, and admin `/jobs/{id}/reset` has no `review_complete` target. Reset to `awaiting_review`, then re-submit the review (reuses the existing GCS corrections):

```bash
ADMIN_TOKEN=$(gcloud secrets versions access latest --secret=admin-tokens --project=nomadkaraoke | cut -d',' -f1)
curl -s -X POST "https://api.nomadkaraoke.com/api/admin/jobs/<id>/reset" \
  -H "X-Admin-Token: $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"target_state": "awaiting_review"}'
# instrumental_selection is "clean" or "with_backing" (read prior value from Firestore state_data before reset)
curl -s -X POST "https://api.nomadkaraoke.com/api/review/<id>/complete?review_token=<token>" \
  -H "Content-Type: application/json" -d '{"instrumental_selection": "with_backing"}'
```

Do **not** reset to `instrumental_selected` — that triggers the final video worker, which needs the (missing) lyrics video and fails "prerequisites not met"; and triggering render-video from `instrumental_selected` gets superseded (invalid `instrumental_selected → rendering_video`).

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

## Job stuck at `rendering_video` (orphaned render)

**Symptoms:** Job frozen at `rendering_video` (step 7/10) for a long time with **no error** and **no Retry button** (it's a processing state, not `failed`).

**Cause:** The render worker started (VM up, rendering underway) then died mid-render — Cloud Run instance recycle / SIGKILL / OOM / lost GCE VM — without unregistering. `updated_at` stops advancing. Before v0.192.3 nothing recovered this: the capacity-retry cron only looks at `render_pending_capacity`, `recover-stuck-jobs` only looked at `downloading_audio`, and the render Cloud Task has finite attempts.

**Auto-recovery (v0.192.3+):** `recover-stuck-jobs` (every 5 min) now flags a job in `rendering_video` with no progress for **>45 min** (`rendering_video_stuck`) and re-parks it into `render_pending_capacity`, so the existing `retry-pending-render-jobs` cron resets it to `review_complete` and re-renders (same 24h ceiling). No manual action needed.

**Manual unstick (if needed):** don't re-trigger the render worker while status is `rendering_video` (it starts mid-state and fails with "Invalid state transition"). Instead let it fail (or re-park it), then use the admin **Retry** which resumes cleanly from `review_complete`. If a ghost `render_progress.stage='running'` blocks re-trigger, clear it first (`update({'state_data.render_progress': {'stage': 'pending'}})`).

---

## Job flips to `failed` right after an admin reset (reset/render race)

**Symptoms:** An operator hit an admin **Reset** button (e.g. "Review") on a job that was `rendering_video`, and moments later the job went to `failed` with a message like *"Video render failed: Invalid state transition for job …: in_review -> instrumental_selected"*. The reset itself looked fine, but the job died on its own.

**Cause (fixed in v0.192.4):** The reset moved the job backwards while the render worker was still running on the encoder. When that render finished it attempted its normal terminal transition (`rendering_video -> instrumental_selected`), which was now illegal, and the worker's generic error handler flipped the job to `failed` — clobbering the operator's reset. First seen on job `7f457087`.

**Auto-handling (v0.192.4+):** Long-running render/video workers are now fenced against supersession — a reset or a newer trigger causes the in-flight worker to **discard its stale result quietly** instead of failing the job. Two nets: a **status fence** (job no longer in the status the worker owns) and a **generation fence** (`state_data.worker_generation`, bumped by every reset and every render/video trigger). An `InvalidStateTransitionError` at the terminal step is treated as supersession, not failure. So resetting a rendering job is now safe at any timing — no manual action needed.

**If you still see a `failed` job from an older occurrence:** use the admin **Retry** endpoint. For a job that has corrections + screens but no committed `instrumental_selection`, it returns cleanly to `awaiting_review` so the review (and instrumental choice) can be re-submitted.

---

## Job in `download_pending_retry`

**Symptoms:** Job status is `download_pending_retry` (introduced v0.192.3), step 3/10, message *"Still finding a good source for this track — … We'll keep trying automatically for up to 24 hours; no action needed."*

**Background:** A **torrent** (RED/OPS) download stalled (few/no seeders or a brief tracker outage). Rather than dead-end the job at `failed` after ~1h, `recover-stuck-jobs` parks it here and re-triggers the download on later ticks for up to **24h**, then fails permanently with a "too rare to source right now" message. This is expected, self-healing behaviour — a seedless release simply may never complete. `state_data.download_retry` holds `{first_seen_at, attempt_count}`. To source it a different way, retry with an alternate release/source.

---

## Job stuck in `render_pending_capacity`

**Symptoms:** Job status is `render_pending_capacity` (introduced 2026-05-05). User-facing message says *"Encoding capacity is temporarily unavailable. Your job will retry automatically — no action needed."* (As of v0.192.3 a mid-render stall can also land here — see "orphaned render" above.)

**Background — what this state means:** GCE returned `ZONE_RESOURCE_POOL_EXHAUSTED` or transient `503 SERVICE_UNAVAILABLE` from `compute.instances.start` on every encoding-worker VM. The render worker parks the job in `RENDER_PENDING_CAPACITY` instead of failing it; Cloud Scheduler retries it every 5 min via `/api/internal/retry-pending-render-jobs`. Hard timeout is 24 hours (then transitions to `failed` with a clear permanent-failure message).

The fallback fleet is diversified across **6 machine families** (broadened to the full pool in v0.195.0): `c4d-highcpu-32` primaries (`encoding-worker-a`/`-b`) in `us-central1-c`, plus 8 stopped fallbacks — `c4d` (`-fallback-a`/`-b`, zones a/b), `n2` (`-n2c`/`-n2f`, zones c/f), `c4` (`-c4a`, zone a), `n4d` (`-n4db`, zone b), `c2d` (`-c2df`, zone f), `n2d` (`-n2da`, zone a). Candidates are tried **fastest-first with a 15-min capacity cooldown** (a family that stocks out is demoted, then re-probed) — see `backend/services/encoding_worker_preference.py`. A single-family region-wide stockout (which took out c4d a/b/c at once on 2026-08-12) now still finds capacity in the 5 other families. If you see EVERY family return stockout, that's a genuinely severe regional event — wait, or add a cross-region fallback. NOTE: creating a *new* fallback VM (or re-creating a deleted one) still needs a one-time boot allocation, so a deep enough crunch can even block provisioning — a `pulumi up` may show the missing VM as "to create / errored" until capacity returns.

**Related symptom — preview 524 / "NetworkError":** the same capacity exhaustion also makes `POST /api/review/{id}/preview-video` fall back to slow local encoding (~130–160 s), which exceeds Cloudflare's ~100 s edge timeout → the browser shows a **524** or Firefox *"NetworkError when attempting to fetch resource"* even though the backend returns 200. If a user reports the review preview failing, check for a concurrent stockout; the already-rendered preview mp4 (if any) is fetchable via `GET /api/review/{id}/preview-video/{hash}` (302 → signed GCS URL) with a Bearer admin token. Completing the review does **not** require the preview.

**Check how many retries have run:**
```bash
python3 -c "
import os; os.environ['GOOGLE_CLOUD_PROJECT']='nomadkaraoke'
from google.cloud import firestore
d = firestore.Client(project='nomadkaraoke').collection('jobs').document('JOB_ID').get().to_dict()
print((d.get('state_data') or {}).get('render_pending_capacity'))
"
```

**Force an immediate retry** (Cloud Scheduler runs every 5 min, but you can poke it manually):
```bash
ADMIN_TOKEN=$(gcloud secrets versions access latest --secret=admin-tokens --project=nomadkaraoke | cut -d',' -f1)
curl -X POST https://api.nomadkaraoke.com/api/internal/retry-pending-render-jobs \
  -H "X-Admin-Token: $ADMIN_TOKEN"
```

**Verify GCE has any capacity right now** (the real error reason is in the Compute op, not the app's "503"):
```bash
# See the actual stockout reason (ZONE_RESOURCE_POOL_EXHAUSTED / stockout) + vmType
gcloud logging read 'protoPayload.methodName:"instances.start" AND "ZONE_RESOURCE_POOL_EXHAUSTED"' \
  --project=nomadkaraoke --freshness=30m --limit=5 \
  --format='value(timestamp, protoPayload.resourceName)'

# Try starting a fallback in each of the 6 families; if ALL refuse, it's severe — wait.
# Synchronous (no --async) so the start op surfaces stockout inline, and we capture
# gcloud's own exit status rather than piping (a pipe would return sed's status).
for VM_ZONE in \
  "encoding-worker-fallback-a:us-central1-a" \
  "encoding-worker-fallback-b:us-central1-b" \
  "encoding-worker-fallback-n2c:us-central1-c" \
  "encoding-worker-fallback-n2f:us-central1-f" \
  "encoding-worker-fallback-c4a:us-central1-a" \
  "encoding-worker-fallback-n4db:us-central1-b" \
  "encoding-worker-fallback-c2df:us-central1-f" \
  "encoding-worker-fallback-n2da:us-central1-a"; do
  VM="${VM_ZONE%%:*}"; ZONE="${VM_ZONE##*:}"
  if OUT=$(gcloud compute instances start "$VM" --zone="$ZONE" --project=nomadkaraoke 2>&1); then
    echo "$VM: OK (started — remember to stop it)"
  else
    echo "$VM: FAILED — $(echo "$OUT" | tr '\n' ' ')"
  fi
done
```

**Configuration knobs:** `MAX_PER_TICK = 1` and `MAX_WAIT_SECONDS = 24*3600` constants in `backend/api/routes/internal.py::retry_pending_render_jobs`.

**Not an incident:** a *primary* warmup hitting `503 SERVICE_UNAVAILABLE` / capacity exhaustion is self-healing — the encoding flow's `ensure_any_running()` falls back to an alternate-zone worker and the job completes. As of 0.174.12 the `/internal/encoding-worker/warmup/{job_id}` endpoint logs this at **WARNING** (`"Primary encoding worker warmup failed (...); encoding will fall back..."`), specifically so it stays below the error monitor's `severity>=ERROR` filter and does **not** page Discord. Only a genuine, unexpected warmup failure (non-`EncodingWorkerStartError`) logs at ERROR. If you see the WARNING in logs with a job that still completed, no action is needed.

**403 on `/internal/encoding-worker/warmup` or `/heartbeat`:** as of 0.188.9 these endpoints are `{job_id}`-scoped and gated on `require_review_auth` (not `require_admin`). The lyrics-review page calls them on load / while editing so the encoding VM is warm by the time the reviewer previews a render. If you see 403s in the browser console, the caller lacks review access to that job — before 0.188.9 they required admin, so **every non-admin customer 403'd and the JIT pre-warm never fired** (renders paid the full cold-boot latency).

---

## Job failed: "Video render failed: TimeoutError()"

**Background:** This was the original signature of the 2026-05-05 capacity bug — empty exception message because `f"...{e}"` produced empty string for bare `TimeoutError()`. **As of 0.174.x the empty-message version should not happen** (we use `repr(e)` as fallback). If you see this exact form on a job after 2026-05-06, the render worker hit a `TimeoutError` *not* preceded by a typed `EncodingWorkerStartError` — most likely the URL re-resolve isn't engaging or a Cloud Run instance was killed mid-flight.

**Check what the render actually saw:**
```bash
gcloud logging read \
  "jsonPayload.message=~\"JOB_ID\" AND timestamp>=\"YYYY-MM-DDTHH:MM:SSZ\"" \
  --project=nomadkaraoke --limit=50 --format='value(timestamp,jsonPayload.message)'
```

Look for these markers:
- `Submitting render-video job to GCE worker: http://X.X.X.X:8080/render-video` — initial URL
- `Trying candidate N/3: VM ... in us-central1-X` — multi-zone iteration starting
- `Set active_override to ...` — fallback engaged
- `Re-resolved encoding URL after warmup: A -> B` — added in 0.174.3, confirms the URL is updating between retries
- `WORKER_END worker=render-video status=pending_capacity` — happy parking path
- `WORKER_END worker=render-video status=error` — failed instead of parked (bug)

**Most likely cause if no `Re-resolved` log appeared:** The job was rendered on a pre-0.174.3 revision. Just retry — the new revision is now serving.

**Recovery:** retry the job via the admin endpoint or `/retry-pending-render-jobs`. The system is self-healing as long as at least one zone has capacity.

---

## Job stuck in `rendering_video` for hours (no failure log)

**Symptoms:** Status frozen at `rendering_video` (progress 75%) for >30 minutes. No `WORKER_END worker=render-video` log entry exists. Last log is mid-retry like *"GCE worker connection failed (attempt 7/8)"*.

**Cause:** The Cloud Run instance handling the render task was killed (autoscaler scaled down, OOM, or revision rollover) before the retry loop completed and could write a failure state.

**Mitigation in place since v0.174.4:** The FastAPI shutdown hook now waits up to 480s for the render worker to finish, then parks any still-active render jobs in `RENDER_PENDING_CAPACITY` (last_code: `WORKER_SHUTDOWN`) before exit. The `/api/internal/retry-pending-render-jobs` Cloud Scheduler job picks them up within 5 minutes. Jobs should now self-recover. If you see `WORKER_END worker=render-video status=shutdown_parked` in logs, that's the new graceful-shutdown path.

**Recovery (only if a job somehow still gets stuck — e.g. SIGKILL hit before park completed):** manually transition to `review_complete` and re-trigger the render. The instrumental selection in `state_data` is preserved so no user re-work is needed:

```bash
ADMIN_TOKEN=$(gcloud secrets versions access latest --secret=admin-tokens --project=nomadkaraoke | cut -d',' -f1)
JID=YOUR_JOB_ID
python3 << EOF
import os; os.environ['GOOGLE_CLOUD_PROJECT']='nomadkaraoke'
from google.cloud import firestore
from datetime import datetime, UTC
db = firestore.Client(project='nomadkaraoke')
now = datetime.now(UTC)
db.collection('jobs').document('$JID').update({
    'status': 'review_complete', 'progress': 70, 'updated_at': now,
    'error_message': None, 'error_details': None,
    'state_data.render_progress': {'stage': 'pending'},
})
EOF
curl -X POST https://api.nomadkaraoke.com/api/internal/workers/render-video \
  -H "X-Admin-Token: $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d "{\"job_id\":\"$JID\"}"
```

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

## Verifying GCE encoding worker cold-start fix

**Background:** The encoding worker VM auto-stops when idle to save cost. When a render request hits a TERMINATED VM, the backend awaits a readiness gate (`EncodingWorkerManager.wait_for_worker_ready`) instead of relying on the deploy-restart-sized 90s retry window. This was added in v0.172.3 to fix the 2026-04-24 incident (job 2c577535) where a cold VM exceeded the retry budget.

**To verify in production after a deploy:**

1. Get the primary VM name from Firestore config:
   ```bash
   python3 -c "
   import os; os.environ['GOOGLE_CLOUD_PROJECT']='nomadkaraoke'
   from google.cloud import firestore
   db = firestore.Client(project='nomadkaraoke')
   print(db.collection('config').document('encoding-worker').get().to_dict()['primary_vm'])
   "
   ```

2. Stop it:
   ```bash
   gcloud compute instances stop <vm-name> --zone=us-central1-c --project=nomadkaraoke
   ```

3. Submit a test render through the dashboard, or trigger one with a known review-complete job.

4. Watch the backend logs for the readiness-wait sequence:
   ```bash
   gcloud logging read 'resource.labels.service_name="karaoke-backend" AND textPayload=~"Waiting for VM|Worker at .* is healthy|Cold-started VM"' \
     --project=nomadkaraoke --freshness=10m --order=asc \
     --format='value(timestamp,textPayload)'
   ```

   Expected sequence:
   - `Encoding worker unreachable — started VM <name> as fallback`
   - `Waiting for VM <name> (status=STAGING)` (every 30s during VM boot)
   - `VM <name> reached RUNNING`
   - `Waiting for worker /health at ...` (every 30s during service init)
   - `Worker at .../health is healthy`
   - `Cold-started VM <name> is now ready`
   - Job proceeds to encoding without retry exhaustion.

5. If the readiness wait times out (~5 min: 120s waiting for VM RUNNING, then 180s waiting for /health), the log will show `Cold-start readiness wait timed out: ...`. The retry loop then continues with attempts 1-7 (~90s of backoff: 5+10+15+15+15+15+15), so the worst-case end-to-end before the job fails is ~6.5 minutes. Investigate VM boot:
   ```bash
   gcloud compute instances get-serial-port-output <vm-name> --zone=us-central1-c --project=nomadkaraoke | tail -100
   gcloud compute ssh <vm-name> --zone=us-central1-c --project=nomadkaraoke --command="sudo systemctl status encoding-worker"
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
