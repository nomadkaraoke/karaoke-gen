# Ephemeral GitHub Actions Runners

Operational runbook for the create-on-demand GHA runner dispatcher. Replaces
the previous fixed pool of 7 long-lived VMs with single-use ephemeral VMs to
eliminate ~$230/mo of always-billed pd-ssd boot disks.

## High-level architecture

```
GitHub webhook (workflow_job.queued)
        │
        ▼
runner-dispatcher Cloud Function (github-runner-manager)
        │  ─ pick image family from labels (general | build | gpu)
        │  ─ mint a JIT runner config from GitHub
        │  ─ compute.instances.insert with --auto-delete boot disk
        │  ─ return 200
        ▼
Fresh GCE VM boots from custom image
        │  ─ startup-script reads JIT config from metadata
        │  ─ runs ./run.sh --jitconfig (registers + runs one job + de-registers)
        │  ─ on exit, trap fires `shutdown -h +1`
        ▼
Boot disk auto-deletes; VM is gone

Every 15 min: orphan cleanup pass
        │  ─ list VMs labelled purpose=gha-ephemeral-runner
        │  ─ cross-reference with org runners list
        │  ─ delete VMs older than 30 min with no registration
        │  ─ delete VMs older than 2 h regardless (hung job protection)
        │  ─ de-register zombie GHA runners with no live VM
```

## Modes

Controlled by Pulumi config `karaoke-gen-infrastructure:runnerMode`:

* **`legacy`** (default) — keeps the original start/stop pool behaviour. The
  Cloud Function uses `start_runners` / `check_and_stop_idle_runners` against
  the existing `github-runner-{1,2,3}`, `github-build-runner`, and
  `github-gpu-runner-{1,2,3}` VMs. **No behavioural change** vs. pre-this-PR.

* **`ephemeral`** — webhook creates a fresh VM per job via JIT registration;
  scheduler runs orphan cleanup. The legacy VMs are not touched (and should be
  left in their existing TERMINATED state until Phase 4).

Switching modes is a two-step operation handled entirely by Pulumi:

```bash
pulumi config set karaoke-gen-infrastructure:runnerMode ephemeral
pulumi up --target 'urn:...:Function::runner-manager-function'
```

(replace `ephemeral` ↔ `legacy` to roll back; takes ~2 min for the function to
re-deploy).

## Image families

Three GCE image families are maintained by
[`.github/workflows/build-runner-images.yml`](../.github/workflows/build-runner-images.yml):

| Family | Base | Bakes | Built on |
|---|---|---|---|
| `gha-runner-general` | Debian 12 | Docker, gcloud, runner v2.332, Python 3.13 (pyenv), Node 20, Java 21, FFmpeg, Poetry | n2-standard-8, 50GB pd-balanced |
| `gha-runner-build` | Debian 12 | Same as general + pre-pulled `karaoke-backend-base`/`:latest` + `fake-gcs-server` | n2-standard-8, 50GB pd-balanced |
| `gha-runner-gpu` | Debian 12 | NVIDIA driver + CUDA 12.4, Python 3.13 (compiled), FFmpeg+libsamplerate, audio-separator models (~14GB at `/opt/audio-separator-models`) | n1-standard-4 + T4, 200GB pd-balanced |

Each image is tagged `gha-runner-<variant>-<YYYYMMDD-HHMMSS>` and joined to the
matching image family. The dispatcher always selects from the family, so the
newest non-deprecated image wins automatically. The build workflow keeps the
newest 3 images per family and deprecates the rest.

Build cadence: monthly cron (`0 2 1 * *` UTC) + `workflow_dispatch`. Manual:

```bash
gh workflow run build-runner-images.yml -f variants=general,build
gh workflow run build-runner-images.yml -f variants=gpu
gh workflow run build-runner-images.yml          # all three
```

Pulumi does **not** manage images — they're produced by the GHA workflow. The
runtime dispatcher resolves `projects/nomadkaraoke/global/images/family/gha-runner-<variant>`
directly, which means image lifecycle and code lifecycle are decoupled.

## Operator handoff: rollout sequence

The plan document is at
[`docs/archive/2026-05-16-ephemeral-gha-runners-plan.md`](archive/2026-05-16-ephemeral-gha-runners-plan.md)
(this repo) and at `nomadkaraoke/docs/archive/2026-05-16-ephemeral-gha-runners-plan.md`
(workspace root, original). Phases 1–2 (code) and 5 (monitoring) are landed in
this PR. Phases 3 and 4 (cutover + decommission) are operator-driven.

### Phase 1 — Build the images (one-time)

After this PR merges:

1. Trigger the workflow for the cheapest variant first to validate plumbing:
   ```bash
   gh workflow run build-runner-images.yml -f variants=general
   ```
   First build takes ~25 min on `general`/`build`, ~60 min on `gpu` (model
   downloads dominate).

2. Verify the image landed:
   ```bash
   gcloud compute images describe-from-family gha-runner-general \
     --project=nomadkaraoke \
     --format='value(name,creationTimestamp,labels)'
   ```

3. Repeat for `build` and `gpu`.

4. Smoke test by launching a one-off VM manually:
   ```bash
   gcloud compute instances create gha-smoke-test \
     --zone=us-central1-a \
     --machine-type=e2-standard-4 \
     --image-family=gha-runner-general \
     --image-project=nomadkaraoke \
     --metadata=enable-oslogin=TRUE
   gcloud compute ssh gha-smoke-test --zone=us-central1-a \
     --command='docker --version && python3 --version && /home/runner/actions-runner/bin/Runner.Listener --version'
   gcloud compute instances delete gha-smoke-test --zone=us-central1-a --quiet
   ```

### Phase 2 — Apply the dispatcher changes (Pulumi)

This PR adds:
- `RUNNER_MODE`, `GCP_FALLBACK_ZONE`, `GITHUB_ORG`, `RUNNER_SERVICE_ACCOUNT`
  env vars on `github-runner-manager`
- Replaces the Cloud Function source archive with the new
  `runner_manager/` + `ephemeral.py` code
- Adds 2 log-based metrics + 2 alert policies for runner observability

Apply locally (the workspace's ADC points at a read-only SA — Pulumi from
this terminal will fail with 403 on `storage.objects.create`, so use an
account with write access, e.g. `gcloud config configurations activate admin`):

```bash
cd karaoke-gen/infrastructure
pulumi preview --diff
# expected: ~2 changes (runner-manager-source replace, runner-manager-function update)
#           + 5 creates (log metrics, alert policies)
pulumi up --target 'urn:pulumi:prod::karaoke-gen-infrastructure::gcp:storage/bucketObject:BucketObject::runner-manager-source' \
          --target 'urn:pulumi:prod::karaoke-gen-infrastructure::gcp:cloudfunctionsv2/function:Function::runner-manager-function' \
          --target 'urn:pulumi:prod::karaoke-gen-infrastructure::gcp:logging/metric:Metric::runner-dispatcher-failures' \
          --target 'urn:pulumi:prod::karaoke-gen-infrastructure::gcp:logging/metric:Metric::runner-dispatcher-orphan-kills' \
          --target 'urn:pulumi:prod::karaoke-gen-infrastructure::gcp:monitoring/alertPolicy:AlertPolicy::runner-dispatcher-failures-alert' \
          --target 'urn:pulumi:prod::karaoke-gen-infrastructure::gcp:monitoring/alertPolicy:AlertPolicy::runner-dispatcher-long-lived-vm-alert'
```

After apply, `RUNNER_MODE` is still `legacy` — no behavioural change yet.

> **Why `--target`?** There's an unrelated pending `divebar-sync-vm` recreate
> from the May 16 cost sprint. Until that PR's deploy runs, scoping to these
> targets keeps this rollout isolated.

### Phase 3 — Cutover to ephemeral

**Only after Phase 1 produced all 3 images AND Phase 2 dispatcher deployed.**

1. Manual one-off test: invoke the dispatcher directly with a synthetic
   webhook payload (signature verification will reject this, but you can
   alternatively flip the mode just for this test by setting the env var on
   the function via `gcloud functions deploy`-style override, or use a real
   PR — see step 3).

2. Switch the dispatcher to ephemeral mode globally:
   ```bash
   pulumi config set karaoke-gen-infrastructure:runnerMode ephemeral
   pulumi up --target 'urn:pulumi:prod::karaoke-gen-infrastructure::gcp:cloudfunctionsv2/function:Function::runner-manager-function'
   ```
   The 7 legacy VMs are currently in `TERMINATED` state (the idle-check
   stopped them). With `RUNNER_MODE=ephemeral` the dispatcher no longer
   starts them — they sit idle until Phase 4 deletion.

3. Trigger a real test run by opening (or pushing to) any branch on
   `karaoke-gen` or `python-audio-separator`. Verify in Cloud Console that:
   - `runner-manager-function` invocations succeed (no 503s)
   - `gcloud compute instances list --filter=labels.purpose=gha-ephemeral-runner` shows VMs appearing and disappearing
   - The CI job completes within `1.5×` the prior wall clock
   - The orphan-cleanup pass (every 15 min via `runner-manager-idle-check`
     scheduler) prints `kept_vms: []` once jobs are done

4. Monitor the new alert policies for a week before Phase 4:
   - `GHA Runner Dispatcher - Create Failures` (any failure = page)
   - `GHA Runner Dispatcher - VM Alive > 2h` (orphan cleanup didn't work)

**Rollback:** `pulumi config set karaoke-gen-infrastructure:runnerMode legacy && pulumi up --target ...:runner-manager-function`.
Legacy VMs are still in Pulumi state; the next workflow_job webhook will
start them.

### Phase 4 — Decommission the legacy VMs (destructive)

**Only after Phase 3 has run cleanly for ≥1 week with real CI traffic.**

1. In `karaoke-gen/infrastructure/config.py`, set `NUM_GITHUB_RUNNERS = 0`
   and `NUM_GPU_RUNNERS = 0`.
2. In `karaoke-gen/infrastructure/__main__.py`, remove the
   `github_runners.create_github_runners(...)`, `create_build_runner(...)`,
   and `create_gpu_runners(...)` calls along with their references.
3. In `karaoke-gen/infrastructure/modules/runner_manager.py`, remove the
   idle-check scheduler (no longer needed once ephemeral is the only mode).
4. In `karaoke-gen/infrastructure/functions/runner_manager/main.py`, delete
   the legacy `start_runners` / `check_and_stop_idle_runners` paths and the
   `RUNNER_MODE` branching. Keep webhook signature verification.
5. `pulumi up` (locally, since legacy VM delete needs `compute.instances.delete`
   which `claude-readonly` blocks).
6. Verify:
   ```bash
   gcloud compute instances list --filter="name~'github-(runner|gpu|build)'" --project=nomadkaraoke
   # should return zero rows
   gcloud compute disks list --filter="users:'github-'" --project=nomadkaraoke
   # should return zero rows
   ```
7. Remove the unused `RUNNER_NAMES` and `GPU_RUNNER_NAMES` env vars from the
   Pulumi runner_manager module (cosmetic).

Savings realised: ~$220/mo (7×200GB pd-ssd at $32/mo each).

## Known gotchas

* **claude-readonly ADC blocks Pulumi storage writes.** The workspace's
  `GOOGLE_APPLICATION_CREDENTIALS` points at a read-only SA. Pulumi 403s on
  `storage.objects.create` when re-uploading the function source bundle.
  Apply Pulumi changes from a shell that uses `admin@nomadkaraoke.com` (or
  temporarily unset the env var). Do NOT switch to `gcloud` to bypass — see
  `feedback_claude_readonly_adc.md` in memory.
* **Pulumi state has a pending `divebar-sync-vm` recreate.** Unrelated to
  this work; will be applied by the cost-sprint PR's CI deploy.
* **us-east4 GPU fallback uses external IPs**, not Cloud NAT. There's no NAT
  in us-east4; provisioning one for a rarely-used fallback zone wasn't worth
  the complexity. Ephemeral external IPs are free while the VM is running.
* **Pre-existing test failure** in
  `infrastructure/functions/runner_manager/test_runner_manager.py::TestCheckGithubForPendingJobs::test_checks_both_queued_and_in_progress`.
  Assumes 2 GitHub API calls; code now iterates 2 repos × 2 statuses = 4.
  Unrelated to this PR.
* **Existing legacy test `test_runner_manager.py` still passes** in 25/26
  scenarios; the new `test_ephemeral.py` adds 20 tests, all passing.
* **Image build VM uses `n2-standard-8`** to speed up apt + downloads, even
  though runtime general/build VMs are `e2-standard-4`/`e2-standard-8`. This
  is intentional — image build is ~25 min on n2; ~45 min on e2.
* **The `--ephemeral` flag is implicit** when `./run.sh --jitconfig` is used.
  Don't pass `--ephemeral` explicitly with `--jitconfig` — GitHub's runner
  rejects the combination.
* **The provision script's GPU kernel-upgrade reboot** is the slowest part of
  the GPU build. The first build will reboot once mid-provisioning; the
  workflow tolerates this via SSH retry-with-grace.

## Files added by this work

* `karaoke-gen/infrastructure/scripts/runner-image-provision.sh` — provision logic
* `karaoke-gen/.github/workflows/build-runner-images.yml` — image build pipeline
* `karaoke-gen/infrastructure/functions/runner_manager/ephemeral.py` — new dispatcher
* `karaoke-gen/infrastructure/functions/runner_manager/test_ephemeral.py` — 20 tests
* `karaoke-gen/docs/EPHEMERAL-GHA-RUNNERS.md` — this file

## Files modified by this work

* `karaoke-gen/infrastructure/functions/runner_manager/main.py` — route on RUNNER_MODE
* `karaoke-gen/infrastructure/modules/runner_manager.py` — new env vars, runnerMode config
* `karaoke-gen/infrastructure/modules/monitoring.py` — runner-dispatcher alerts
* `karaoke-gen/infrastructure/__main__.py` — wire monitoring resources
