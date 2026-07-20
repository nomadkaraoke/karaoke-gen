# Ephemeral GitHub Actions Runners

Operational runbook for the create-on-demand GHA runner dispatcher. Replaces
the previous fixed pool of 7 long-lived VMs with single-use ephemeral VMs to
eliminate ~$230/mo of always-billed pd-ssd boot disks.

## STATUS — 2026-05-17

**Cutover live.** `RUNNER_MODE=ephemeral` deployed on the `github-runner-manager`
Cloud Function at 2026-05-17T04:56Z. Scheduler tick at 04:57:06 confirmed
routing to "ephemeral mode orphan cleanup" with no errors. The 7 legacy
runner VMs are still provisioned and in `TERMINATED` state — they cost only
their pd-ssd boot disks (~$32/mo each = ~$224/mo) until Phase 4 deletes them
after the 1-week soak.

**Current images** (use `--image-family=gha-runner-<variant>` — GCE auto-selects newest):

| Family | Latest image | Built |
|---|---|---|
| `gha-runner-general` | `gha-runner-general-20260517-040403` | 04:04 UTC |
| `gha-runner-build` | `gha-runner-build-20260517-043133` | 04:31 UTC |
| `gha-runner-gpu` | `gha-runner-gpu-20260517-033723` | 03:37 UTC |

Old images (pre-poetry-fix) still in the families but auto-superseded.

**PRs landed this session:** #765 (core), #766 (variant resolution), #767 (serial-console wait), #768 (gpu python — superseded), #769 (visibility), #770 (image-create + python 3.13.2 — superseded), #771 (gpu pyenv unification), #772 (apt-daily race).

### Self-serve status check

Paste this anytime to see the full picture:

```bash
echo "=== $(date -u +%FT%TZ) ===" && \
echo && echo "--- Active workflow runs ---" && \
gh run list --workflow=build-runner-images.yml --limit=3 && \
echo && echo "--- Live VMs (build + smoke + ephemeral runners) ---" && \
gcloud compute instances list --project=nomadkaraoke \
  --filter='labels.purpose=gha-runner-image-builder OR labels.purpose=gha-runner-smoke OR labels.purpose=gha-ephemeral-runner' \
  --format='table(name,status,creationTimestamp.date(),labels.purpose,labels.family)' && \
echo && echo "--- READY images ---" && \
gcloud compute images list --project=nomadkaraoke \
  --filter='family~"^gha-runner-(general|build|gpu)$" AND status=READY' \
  --format='table(name,family,creationTimestamp.date())' && \
echo && echo "--- Dispatcher mode + recent log lines ---" && \
gcloud functions describe github-runner-manager --gen2 --region=us-central1 \
  --project=nomadkaraoke --format='value(serviceConfig.environmentVariables.RUNNER_MODE)' && \
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="github-runner-manager"' \
  --project=nomadkaraoke --limit=10 --format='value(timestamp,textPayload)' --freshness=30m
```

To **watch the live console of any VM** (no SSH, works always):

```bash
gcloud compute instances get-serial-port-output <vm-name> \
  --zone=us-central1-a --project=nomadkaraoke --port=1 \
  | grep -E "runner-image|SMOKE-|###" | tail -50
```

## ⏰ Followups (1 week from cutover — 2026-05-24)

If the dispatcher has been creating ephemeral VMs successfully and the
monitoring alerts haven't fired, proceed with Phase 4 decommission:

**1. Confirm no issues during the soak.** Run the status check above plus:
```bash
# Count successful ephemeral VM dispatches (should be ≥ number of CI runs in past week)
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="github-runner-manager" AND textPayload:"Dispatched ephemeral runner"' \
  --project=nomadkaraoke --freshness=7d --format='value(timestamp)' | wc -l

# Check the 2 alert policies weren't firing
gcloud alpha monitoring policies list --project=nomadkaraoke \
  --filter='displayName:"GHA Runner Dispatcher"' --format='value(displayName,enabled)'

# Verify no long-lived ephemeral VMs
gcloud compute instances list --project=nomadkaraoke \
  --filter='labels.purpose=gha-ephemeral-runner' --format='table(name,status,creationTimestamp.date())'
```

**2. If everything looks clean, proceed with Phase 4** (delete the 7 legacy
VMs and their pd-ssd boot disks — this is what unlocks the ~$220/mo saving):

```bash
cd karaoke-gen/infrastructure
# Edit infrastructure/config.py:
#   NUM_GITHUB_RUNNERS = 0
#   NUM_GPU_RUNNERS = 0
# Edit infrastructure/__main__.py: comment out / remove the three calls
#   github_runners.create_github_runners(...)
#   github_runners.create_build_runner(...)
#   github_runners.create_gpu_runners(...)
# Edit infrastructure/modules/runner_manager.py: remove the idle-check scheduler
#   (no longer needed once ephemeral is the only mode).
# Edit infrastructure/functions/runner_manager/main.py: remove the legacy
#   start_runners / check_and_stop_idle_runners code paths and the
#   RUNNER_MODE branching (keep webhook signature verification).
pulumi up   # destructive — requires admin@ creds (claude-readonly blocks compute.instances.delete)

# Verify legacy VMs and disks are gone
gcloud compute instances list --filter="name~'github-(runner|gpu|build)'" --project=nomadkaraoke   # expect empty
gcloud compute disks list --filter="users:'github-'" --project=nomadkaraoke   # expect empty
```

**3. If issues emerged during the soak, roll back instead:**
```bash
cd karaoke-gen/infrastructure
unset GOOGLE_APPLICATION_CREDENTIALS   # use admin@ creds, not claude-readonly
pulumi config set karaoke-gen-infrastructure:runnerMode legacy
pulumi up --target 'urn:pulumi:prod::karaoke-gen-infrastructure::gcp:cloudfunctionsv2/function:Function::runner-manager-function'
```
Legacy VMs are still in Pulumi state and `TERMINATED`; the next workflow_job webhook will start them.

## Iteration-cost lessons learned from this session

Hard-won insights for the next person debugging GCE image builds:

1. **Always stream the provisioner log to /dev/ttyS0 (GCE serial console).**
   It's readable via `gcloud compute instances get-serial-port-output --port=1`
   with zero SSH/IAP dependency. SSH-over-IAP turned out to be wildly
   unreliable from a residential network — multiple iterations failed silently
   because I couldn't see what was happening on the build VM. Solved by
   `exec > >(tee /var/log/X.log /dev/ttyS0 2>/dev/null) 2>&1` at the top of the
   script + an ERR trap that writes `### FAILED rc=N line=L cmd=...` to the
   same channel. Wait steps poll the serial console for the marker, NOT SSH.

2. **`gcloud compute ssh --command='test -f /file'` can lie under IAP load.**
   Witnessed: false-positive marker detected at 4 min on a 25-min provisioner
   because the SSH session closed before the remote command completed but the
   exit code defaulted to 0. Trust **file content** (e.g., regex `^[0-9]{4}-`
   against an ISO timestamp), not exit codes alone.

3. **`gh run watch` aggressively polls the GitHub API and rate-limits quickly.**
   Use `gcloud`-based polling (image presence, VM presence) for waits whenever
   possible — they don't hit GitHub's quota.

4. **Compiling Python from source on Debian 12 is fragile.** Python 3.13.0 has
   an install-time ensurepip regression (`FileNotFoundError` on
   `_WHEEL_PKG_DIR`). 3.13.2 fixes that but produces a broken `_socket` C
   extension when `--enable-optimizations` is off. **Use pyenv for all variants
   instead** — pyenv's build wrapper has hardening that the raw `make install`
   path lacks.

5. **Fresh Debian VMs have an apt-daily race.** `apt-daily.service` and
   `apt-daily-upgrade.service` auto-start ~30s into boot and hold
   `/var/lib/apt/lists/lock` + `/var/lib/dpkg/lock` for several minutes. Any
   concurrent `apt-get` (especially `installdependencies.sh`) fails with
   `E: Could not get lock`. **Stop+disable+mask both timers early in the
   script**, then wait for the lock before doing anything apt-related.

6. **Don't auto-name boot disks then try to extract the name via a format
   selector.** I used `--format='value(disks[0].source.scope(disks).segment(1))'`
   which silently returned empty in one run and broke `gcloud compute images
   create --source-disk=`. The VM name == boot disk name by default; use it
   directly.

7. **Pulumi's `cloudfunctionsv2.Function` won't redeploy on source-bundle
   change unless you pin the generation.** Replacing the `BucketObject` updates
   GCS but doesn't trigger the function. Add
   `generation=source_archive.generation` to `storage_source` so Pulumi sees
   the change as a Function diff and redeploys.

8. **Smoke-test as the actual production user.** Earlier smoke tests SSH'd as
   the OS-login user, hit "permission denied" on the runner directory + docker
   socket, and falsely reported failures. Production runs `sudo -u runner
   ./run.sh ...`, so the smoke test must verify tools-as-runner. The v2 smoke
   test uses startup-script + `sudo -iu runner` for this.

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

Four GCE image families are maintained by
[`.github/workflows/build-runner-images.yml`](../.github/workflows/build-runner-images.yml):

| Family | Base | Bakes | Built on |
|---|---|---|---|
| `gha-runner-general` | Debian 12 | Docker, gcloud, runner v2.332, Python 3.13 (pyenv), Node 20, Java 21, FFmpeg, Poetry | n2-standard-8, 50GB pd-balanced |
| `gha-runner-build` | Debian 12 | Same as general + pre-pulled `karaoke-backend-base`/`:latest` + `fake-gcs-server` | n2-standard-8, 50GB pd-balanced |
| `gha-runner-gpu` | Debian 12 | NVIDIA driver + CUDA 12.4, Python 3.13 (compiled), FFmpeg+libsamplerate, audio-separator models (~14GB at `/opt/audio-separator-models`) | n1-standard-4 + T4, 200GB pd-balanced |
| `gha-runner-gpu-windows` | Windows Server 2022 | NVIDIA **GRID** driver (WDDM — required for DirectML; datacenter driver = TCC = no DirectX), Python 3.12, Git, FFmpeg, Poetry, runner (win-x64), DirectML test models at `C:\audio-separator-models` | n1-standard-4 + T4, 100GB pd-balanced |

The Windows variant is provisioned by
[`runner-image-provision.ps1`](../infrastructure/scripts/runner-image-provision.ps1)
delivered via the `windows-startup-script-ps1` metadata key (Windows ignores
`startup-script`). It exists for python-audio-separator's `windows-directml`
integration tests (RoFormer-on-Windows, issue #292 there).

**Label routing**: runners advertise `self-hosted, <os>, x64, gcp[, gpu]`.
The dispatcher resolves families with precedence windows → gpu → build →
general, so jobs MUST include an OS label in `runs-on`
(`[self-hosted, linux, gpu]` / `[self-hosted, windows, gpu]`) — GitHub
schedules onto any runner whose labels are a superset of the job's.

Each image is tagged `gha-runner-<variant>-<YYYYMMDD-HHMMSS>` and joined to the
matching image family. The dispatcher always selects from the family, so the
newest non-deprecated image wins automatically. The build workflow keeps the
newest 3 images per family and deprecates the rest.

Build cadence: monthly cron (`0 2 1 * *` UTC) + `workflow_dispatch`. Manual:

```bash
gh workflow run build-runner-images.yml -f variants=general,build
gh workflow run build-runner-images.yml -f variants=gpu
gh workflow run build-runner-images.yml          # all variants
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
   - `GHA Runner Dispatcher - VM Alive > 2h30m` (orphan cleanup didn't work)

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

## Follow-ups (not in initial PR)

* **Dedicated image-builder service account.** The build workflow currently
  uses the `github-actions-deployer@` SA (broad CI/CD perms). Best practice
  is a separate `gha-runner-image-builder@` SA with only `artifactregistry.reader`
  + `logging.logWriter`. The build VM doesn't need write perms anywhere
  — `gcloud compute images create` is invoked from the GHA runner, not the
  VM. Adds 1 SA + 2 IAM bindings + a new repo secret.
* **OIDC token verification for the scheduler entry point.** The function
  currently checks `Authorization: Bearer <jwt>` is present and non-trivially
  long, which keeps random callers out but doesn't cryptographically verify
  the token. Adding full verification needs `google-auth` in the function's
  requirements.txt and a one-time JWKS fetch; defer until we observe abuse.

## Known gotchas

* **A stale baked runner version silently kills every ephemeral runner** (incident
  2026-05-24). GitHub deprecates older `actions/runner` versions and returns
  **HTTP 403 on the broker poll** (`"Runner version vX.Y.Z is deprecated and
  cannot receive messages"`). Ephemeral/JIT runners run with `disableUpdate`, so
  they CANNOT self-update — the runner registers, gets rejected, the listener
  exits "no retry needed", and the EXIT-trap halts the VM. Net effect: VMs
  dispatch and boot fine, but **CI jobs sit `queued` forever** and each leaves a
  zombie `offline` JIT registration in the org runner list. **Diagnosis:** read a
  failed VM's `/home/runner/actions-runner/_diag/Runner_*.log` for the 403. (Note:
  serial console is unavailable once a VM self-halts, so capture logs by setting a
  dump startup-script and starting the stopped VM.) **Fix:** bump `RUNNER_VERSION`
  in `infrastructure/scripts/runner-image-provision.sh` (and legacy
  `compute/startup_scripts/github_runner.sh`) to the current release from
  <https://github.com/actions/runner/releases/latest>, then re-run
  `build-runner-images.yml` (workflow_dispatch — runs on GitHub-hosted runners, so
  it's not blocked by the broken self-hosted ones). The monthly rebuild cron
  (`0 2 1 * *`) is too slow vs. GitHub's deprecation pace — consider resolving
  "latest" at build time and/or alerting on deprecation 403s.
* **Source-bundle replacement doesn't auto-redeploy the Cloud Function** (fixed
  forward, but worth knowing). The original code uploaded a new source bundle
  via `BucketObject` replace, but the `cloudfunctionsv2.Function` resource
  pointed at the bucket+name without pinning a generation, so Pulumi saw no
  diff on the Function and the live function kept serving the previously
  staged copy. Fixed by adding `generation=source_archive.generation` to the
  Function's `storage_source`. If you ever see a Pulumi apply complete cleanly
  but the live function still serving old code, run
  `gcloud functions deploy github-runner-manager --gen2 --region=us-central1
  --project=nomadkaraoke --source=gs://nomadkaraoke-runner-manager-source/runner-manager-source.zip
  --runtime=python312 --entry-point=handle_request --quiet`.
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
