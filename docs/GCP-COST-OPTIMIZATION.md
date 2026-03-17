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

**Current State:**
- c4d-highcpu-32 VM running 24/7
- Cost: ~$700/month

**Opportunities:**
1. **Spot Instance:** Could save 60-91% (~$420-630/month savings)
   - Risk: May be preempted during video encoding jobs
   - Mitigation: Implement job restart logic
2. **Auto-scaling:** Start/stop based on video queue depth
   - Could scale to zero when no jobs queued
   - Startup time: ~2-3 minutes (acceptable for async processing)

**Recommendation:** Test Spot instance first (lower risk), then consider auto-scaling

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
