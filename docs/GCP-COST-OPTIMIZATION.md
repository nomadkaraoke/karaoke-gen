# GCP Cost Optimization Analysis

**Date:** February 10, 2026
**Current Spend:** ~$6,000/month (~$200/day)
**Target:** Sustainable burn rate

## Executive Summary

Analyzed GCP billing data and identified $1,440+/month in immediate savings through GitHub runner auto-scaling (implemented). Additional opportunities identified for Cloud Run optimization (~$3,200/month potential savings).

## Cost Breakdown (Before Optimization)

| Service | Monthly Cost | % of Total | Notes |
|---------|--------------|------------|-------|
| **Cloud Run (karaoke-backend)** | ~$3,200 | 53% | concurrency=1, 4 min instances, 8 CPU, 16GB RAM |
| **GitHub Actions Runners** | ~$1,440 | 24% | 20 VMs running 24/7 |
| **GCE Encoding Worker** | ~$700 | 12% | c4d-highcpu-32 running 24/7 |
| **Cloud Storage** | ~$400 | 7% | 2.3TB storage |
| **Artifact Registry** | ~$78 | 1% | 390GB Docker images |
| **Other Services** | ~$182 | 3% | NAT, Load Balancers, etc. |
| **Total** | **~$6,000** | 100% | |

## Implemented Optimizations

### ✅ GitHub Actions Runners Auto-Scaling

**Status:** Fixed and operational (March 17, 2026)

**Changes Made:**
- Reduced from 20 VMs to 3 general + 1 build + 3 GPU runners
- Implemented auto-start on CI job queue (via GitHub webhook)
- Implemented auto-stop after 1 hour idle (via Cloud Scheduler)
- Removed external IPs (using Cloud NAT instead)

**March 2026 Fix:** The initial auto-scaling implementation had critical bugs that prevented idle shutdown — runners were running 24/7 despite the auto-stop mechanism. See `docs/SELF-HOSTED-RUNNERS.md` for full details. Bugs fixed: metadata writes not awaited, "set now and keep" infinite loop, pending jobs resetting all timestamps, missing IAM permission (`iam.serviceAccountUser`), no STOPPING state handling, sequential (slow) stops.

**Cost Impact:**
- **Before:** 7 VMs running 24/7 = ~$1,648/month
- **After (fixed):** ~$50-150/month (estimated, with proper idle shutdown)
- **Savings:** ~$1,500/month

**Implementation:**
- PRs: [#376](https://github.com/nomadkaraoke/karaoke-gen/pull/376), [#383](https://github.com/nomadkaraoke/karaoke-gen/pull/383), [#385](https://github.com/nomadkaraoke/karaoke-gen/pull/385)
- Infrastructure: `infrastructure/modules/runner_manager.py`, `infrastructure/compute/github_runners.py`
- Webhook: Configured at org level for all repos
- Monitoring: Cloud Function logs, Cloud Scheduler jobs

**Technical Details:**
```
Function: github-runner-manager
Memory: 512M
Idle Timeout: 1 hour
Check Frequency: Every 15 minutes
Webhook URL: https://us-central1-nomadkaraoke.cloudfunctions.net/github-runner-manager
```

## Pending Optimization Opportunities

### 🔍 Cloud Run Configuration (High Priority)

**Current Issue:**
- `concurrency=1` forces one request per instance
- `min-instances=4` keeps 4 instances always running
- High CPU/RAM allocation (8 CPU, 16GB) per instance

**Analysis Required:**
The `concurrency=1` setting may have been added deliberately for performance reasons. Investigation needed to determine:
1. Why was `concurrency=1` set? (performance issue, or unnecessary?)
2. Does `render_video_worker` run FFmpeg locally in Cloud Run? (CPU-intensive)
3. Can we increase concurrency without causing OOM or CPU contention?

**Potential Savings:** ~$2,400-3,000/month (if we can increase concurrency and reduce min-instances)

**Next Steps:**
1. Review git history for when `concurrency=1` was added and why
2. Profile render_video_worker memory/CPU usage during FFmpeg operations
3. Test with `concurrency=2-4` in staging environment
4. Monitor for OOM errors or performance degradation

### 🔍 GCE Encoding Worker

**Current State (updated v0.195.0 — the opportunities below are largely SHIPPED):**
- No longer a single 24/7 VM. Since 2026-09-26 it is 3 VMs: a **Spot**
  c4d-highcpu-16 blue-green pair + 1 stopped on-demand c2d-highcpu-16 fallback
  (was 10 × 32-vCPU on-demand VMs — see docs/archive/2026-09-26-encoding-cost-cuts.md).
- **Idle auto-shutdown is live** (JIT start + heartbeat on the lyrics-review page;
  a Cloud Function checks every 2 min and stops idle VMs after 5 min). When idle, cost is just boot disks
  (50 GB since 2026-09-26, ~$4-5/VM/mo; was 100 GB / ~$10 — see
  docs/archive/2026-09-26-encoding-worker-disk-cost.md); compute is billed only while encoding — so the historical
  "~$700/mo running 24/7" no longer applies.

**Opportunities (status):**
1. **Spot Instance:** Could save 60-91% — NOT adopted; on-demand chosen so a
   fallback can always be *started* when needed (Spot preemption would undermine
   the anti-stockout pool during the industry-wide crunch). Kept as a future lever.
2. **Auto-scaling / scale-to-zero:** ✅ IMPLEMENTED via JIT start + idle-shutdown
   Cloud Function (start/stop on demand rather than queue-depth-based).

**Recommendation:** Stay on-demand (already scales to zero via idle-shutdown, which
captured most of the savings). Do NOT move to Spot for now — Spot preemption plus
the industry-wide stockout would undermine the anti-stockout fallback pool. Revisit
Spot only as a future experiment once capacity/resiliency is validated.

### 🔧 Artifact Registry Cleanup (Quick Win)

**Current State:**
- 390GB of Docker images
- Cost: ~$78/month
- Many old images likely unused

**Action Items:**
```bash
# List images and their sizes
gcloud artifacts docker images list \
  us-central1-docker.pkg.dev/nomadkaraoke/karaoke-repo/karaoke-backend \
  --include-tags --format="table(package,version,create_time,size)"

# Set lifecycle policy to delete images older than 30 days (keep 10 most recent)
gcloud artifacts repositories update karaoke-repo \
  --location=us-central1 \
  --cleanup-policy-dry-run \
  --cleanup-policies='tagState=tagged,olderThan=30d,keep=10'
```

**Potential Savings:** ~$40-60/month

### 🔧 Billing Alerts (Risk Management)

**Status:** No billing alerts configured

**Recommendation:** Set up budget alerts
```bash
# Alert at 50%, 80%, 100% of $5,000/month budget
gcloud billing budgets create \
  --billing-account=BILLING_ACCOUNT_ID \
  --display-name="Monthly Budget Alert" \
  --budget-amount=5000 \
  --threshold-rule=percent=50 \
  --threshold-rule=percent=80 \
  --threshold-rule=percent=100
```

## Summary

### Immediate Results (Implemented)
- **$1,390/month saved** through GitHub runner auto-scaling
- **New monthly spend:** ~$4,600/month (24% reduction)

### Potential Additional Savings
- Cloud Run optimization: $2,400-3,000/month (requires investigation)
- Encoding Worker Spot: $420-630/month (moderate risk)
- Artifact Registry cleanup: $40-60/month (quick win)
- **Total potential:** $2,860-3,690/month additional savings

### Target State
If all optimizations implemented:
- **Current:** $6,000/month
- **After runner optimization:** $4,600/month
- **After all optimizations:** $1,200-1,800/month
- **Total reduction:** 70-80%

## References

- [GCP Pricing Calculator](https://cloud.google.com/products/calculator)
- [Cloud Run Pricing](https://cloud.google.com/run/pricing)
- [Spot VM Pricing](https://cloud.google.com/compute/docs/instances/spot)
- [GitHub Self-Hosted Runners](https://docs.github.com/en/actions/hosting-your-own-runners)

## Change Log

- **2026-02-10:** Initial analysis and GitHub runner optimization implemented
- **2026-02-10:** Runners reduced from 20 → 3 with auto-scaling, saving $1,390/month
- **2026-03-03:** Added dedicated on-demand build runner (`e2-standard-8`) for Docker deploys to prevent spot preemption during builds (~$0.27/hr only when deploying)
- **2026-10-01:** Retired the `gha-build-*` build runner (~$21/mo at ~120 deploys/mo). `deploy-backend` runs on free `ubuntu-latest`; images build in Cloud Build us-central1 (same-region AR pulls, e2-standard-2 free tier, ~7 build-min/deploy). Not built on the hosted runner because pulling the 4.2 GB CPU + 13 GB GPU bases is internet egress (~$2/deploy).

## 2026-09-26 cuts (post-credit-expiry, target: whole project < $300/mo)

| Change | Where | Est. saving |
|--------|-------|-------------|
| Deleted serverless VPC connector `cloud-run-connector` (flacfetch is off-GCP on a public URL) | `__main__.py`, `cloud_run.py`, `ci.yml` (`--clear-vpc-connector`) | ~$12/mo + Network Intelligence resource-hours |
| `karaoke-backend` min-instances 2 → 1 (accepted: big bursts may hit a ~15s cold start) | `ci.yml` | ~$25/mo |
| Autoclass on `karaoke-gen-storage-nomadkaraoke` (terminal NEARLINE) and `nomadkaraoke-divebar-files` (terminal ARCHIVE) | `storage.py`, `divebar_mirror.py` | ~$9–17/mo once objects cool (30d+) |
| `nomadkaraoke-data` (raw Spotify ETL, 158 GiB) → ARCHIVE after 30d | karaoke-decide `infrastructure/__main__.py` | ~$3/mo |
| DR git bundles weekly + incremental (only repos pushed since last S3 upload) | `functions/backup_to_aws/` | cross-cloud egress |
| Error monitor every 15 min → hourly (lookback 60 min) | `config.py`, `backend/services/error_monitor/config.py` | Cloud Run Job + Logging API |
| Firestore PITR disabled (nightly export remains) | `database.py` | PITR storage |
| `recover-stuck-downloads` / `retry-pending-render-jobs` every 5 → 10 min | `__main__.py` | ~half their Firestore reads |

Kept (at the time): the `audio-separator` Cloud Run GPU service — shut down in round 2 below.

## 2026-10-01 cuts, round 2 (run-rate ~$400 → target < $300/mo)

| Change | Where | Est. saving |
|--------|-------|-------------|
| `karaoke-backend` min-instances 1 → 0 (Andrew accepts a ~15s cold start on the first request after idle). With `--cpu-throttling` (request-based billing), idle non-min instances are free, so the 60s `/api/health` uptime check and the `*/10` schedulers keeping it warm don't cost idle charges | `ci.yml` | ~$24/mo |
| `audio-separator` Cloud Run GPU service (us-east4) + its SA, IAM, output bucket and `audio-separator` AR repo (~42 GB) **deleted**; python-audio-separator's `deploy-to-cloudrun.yml` made manual-only | `__main__.py` (flag `audioSeparatorServiceEnabled`, default off) | ~$22/mo |
| `backup-to-aws` no longer copies job files to S3 (they rely on GCS versioning + soft-delete); freshness monitor no longer checks `gcs/job-files/` | `functions/backup_to_aws/main.py`, `dr-backup-freshness.yml`, `DISASTER-RECOVERY.md` | ~$15/mo egress |
| Artifact Registry cleanup: per package keep the 20 most recent versions (≈10 tagged deploy images; each build also leaves a cache manifest) + tags `latest`/`content-`/`cache`, delete everything else (tagged *or* untagged) older than 7 days. **Applied in DRY-RUN mode** (`CLEANUP_POLICY_DRY_RUN = True` in `artifact_registry.py`) — flip to `False` + targeted `pulumi up` on `karaoke-artifact-repo` / `karaoke-backend-gpu-artifact-repo` after reviewing the dry-run audit logs. After the next GPU base rebuild, delete the superseded `karaoke-backend-gpu-base:content-daa414741653` tag by hand (the `content-` KEEP rule would otherwise keep it forever). Before, tagged per-commit images were never deleted (~265 + ~244 tagged backend versions had piled up). Legacy 13 GB `karaoke-backend-gpu-base` package in us-east4 deleted (gcloud). `gcf-artifacts` (Google-managed, not in Pulumi) got a gcloud policy (also dry-run for now; enable with `gcloud artifacts repositories set-cleanup-policies gcf-artifacts --location=us-central1 --policy=<file> --no-dry-run`): keep 3 most recent + all tagged, delete untagged > 7d | `artifact_registry.py`, `gpu_artifact_registry.py` | ~$8–10/mo |
| Secret Manager: 12 superseded versions disabled → destroyed (none pinned; all consumers use `:latest`) | gcloud (out of Pulumi) | < $1/mo |

### Audio separator service (shut down, on-demand redeploy)

The standalone `audio-separator` API (L4 GPU Cloud Run service, us-east4) is still
defined in `infrastructure/modules/audio_separator_service.py`, gated behind a Pulumi
config flag. To bring it back:

```bash
cd infrastructure
STACK=nomadkaraoke/karaoke-gen-infrastructure/prod
PULUMI="env -u GOOGLE_APPLICATION_CREDENTIALS GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token --account=admin@nomadkaraoke.com) pulumi"
pulumi config set audioSeparatorServiceEnabled true --stack $STACK
# 1) Create ONLY the (empty) Artifact Registry repo first — the service references
#    api:latest, which must exist before Cloud Run will accept the service.
$PULUMI up --stack $STACK --target 'urn:pulumi:prod::karaoke-gen-infrastructure::gcp:artifactregistry/repository:Repository::audio-separator-artifact-repo'
# 2) Build + push api:<sha> and api:latest (from a python-audio-separator checkout)
gcloud builds submit --config cloudbuild.yaml --region=us-east4 --project=nomadkaraoke --substitutions=SHORT_SHA=$(git rev-parse --short=8 HEAD)
# 3) Create the service, SA, IAM and output bucket
$PULUMI up --stack $STACK
# Later image updates: gh workflow run deploy-to-cloudrun.yml --repo nomadkaraoke/python-audio-separator
export AUDIO_SEPARATOR_API_URL=$(gcloud run services describe audio-separator --region us-east4 --project nomadkaraoke --format='value(status.url)')
```

The output bucket is protected by the project's `deny-destructive-operations` IAM deny
policy. When you shut the service down again (`pulumi config rm audioSeparatorServiceEnabled`
+ `pulumi up`), delete the bucket first with
`gcloud storage rm -r gs://nomadkaraoke-audio-separator-outputs --impersonate-service-account=break-glass@nomadkaraoke.iam.gserviceaccount.com`,
then `pulumi refresh --target <bucket urn>`.

### Out-of-Pulumi changes (2026-10-01)

- `gcloud artifacts packages delete karaoke-backend-gpu-base --repository=karaoke-backend-gpu --location=us-east4`
  (13 GB legacy base; the base has lived in us-central1 `karaoke-repo` since 2026-09-27; the CI one-time-copy fallback that read it was removed).
- `gcloud artifacts repositories set-cleanup-policies gcf-artifacts --location=us-central1` (policy above).
- Secret versions disabled, then destroyed: `encoding-worker-fallback-vms` v1–4, `flacfetch-api-url` v1,
  `github-runner-pat` v1, `pushbullet-api-key` v1–2, `rapidapi-key` v1, `spotify-cookie` v1,
  `discord-releases-webhook` v1–2. App-rotated secrets (`dropbox-oauth-credentials`, `spotify-oauth-token`,
  `youtube-cookies`) keep their own retention and were left alone, as was `gemini-api-key`.
- `gs://nomadkaraoke-audio-separator-outputs` deleted via break-glass SA (IAM deny policy blocks Pulumi).
