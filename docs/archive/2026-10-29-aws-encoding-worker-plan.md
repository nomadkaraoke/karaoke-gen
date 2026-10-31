# AWS encoding worker: implementation plan (2026-10-29)

**Goal:** run gen's encoding worker on EC2 Spot, paid for with the AWS Activate credits (acct 790139457751, expiring 2028-10-31). Then run distribution (Dropbox/YouTube/Drive uploads) from AWS too, so the large output egress moves off the GCP bill. **Constraint:** job→published must not get slower, and the move runs in parallel with GCP first.

Evidence: workspace `docs/archive/2026-10-29-aws-encoding-worker-migration-plan.md`. A c7a.4xlarge matched or beat the GCE c4d-highcpu-16 on 3 replayed prod jobs.

## Phase 1: AWS box as an encoding-worker candidate (this PR set)

> **Amended 2026-10-29 (after the first `pulumi up`): on-demand c7a.4xlarge, not Spot c7a.8xlarge.**
> - Creating the Spot c7a.8xlarge failed with `InsufficientInstanceCapacity`.
> - Spot placement scores were 1–3/10 for c7a/c7i/c6a/c8a 4–8xlarge across us-east-1 and us-east-2.
> - A worker that stops and starts on demand has to win capacity on every start, and a Spot interruption mid-job is costly, especially for Phase 2's video-worker runs.
> - On-demand c7a.4xlarge costs ~$0.82/h × ~100 h ≈ $82/mo of the ~$212/mo of credits. It matched or beat GCE in the benchmark.
> - The design below otherwise stands; read "Spot"/"8xlarge" in it as superseded.

The box runs the **unchanged worker HTTP service** (`backend.services.gce_encoding.main`). It joins the existing candidate pool, so failover, overrides, lost-job detection and cooldowns all apply unchanged.

### Design

**Instance**
- `aws-encoding-worker-1`: **c7a.8xlarge** (32 vCPU / 64 GB), us-east-2.
- A **persistent Spot** request with `stop` on interruption, matching GCE's STOP-on-preempt, plus an Elastic IP.
- Port 8080 open with API-key auth, the same as the GCE firewall today.
- The root EBS volume persists across stop/start, like the GCE boot disks. It is provisioned once by user-data (a port of packer `provision.sh`).
- **Why 8xlarge:** ~100 h/mo × ~$0.46/h ≈ $46/mo of *credits* (we accrue ~$205/mo of credits). It encodes faster than today's 16 vCPU and fits two heavy jobs in RAM.

**GCP auth**
- Workload Identity Federation: an `aws-encoding` pool plus an AWS provider for acct 790139457751.
- The `nomad-encoding-worker` role is bound to the existing **encoding-worker SA** (`roles/iam.workloadIdentityUser`). No SA keys.
- The box uses an `external_account` credential config (not secret) with IMDSv2, for both the client libraries and `gcloud`.

**Backend control of EC2**
- An IAM user `nomad-encoding-controller` limited to `ec2:Start/Stop/DescribeInstances` on instances tagged `Project=nomad-encoding`.
- Its keys go in Secret Manager `aws-encoding-controller-credentials`, the same pattern as backup-to-aws; the backend SA can read it.

**Registry**
- The box is an entry in the existing `encoding-worker-fallback-vms` secret:
  `{"vm":"aws-encoding-worker-1","zone":"us-east-2b","ip":<EIP>,"machine_type":"c7a-4xlarge","provider":"aws","instance_id":"i-…","region":"us-east-2"}`
- The `.` in the AWS type becomes `-`, so Firestore cooldown keys stay valid.

**Manager**
- `AwsComputeAdapter` (`backend/services/aws_encoding_compute.py`) mimics the `compute_v1.InstancesClient` surface the manager uses (`get/start/stop`). It maps EC2 states to GCE ones (`running→RUNNING`, `pending→STAGING`, `stopping→STOPPING`, `stopped→TERMINATED`), EC2 capacity errors to `CAPACITY_ERROR_CODES`, and `InvalidInstanceID.NotFound` to `NotFound`.
- `EncodingWorkerManager._compute_for(vm)` routes AWS VMs to the adapter. GCE code paths are untouched.

**Ranking**
- `SPEED_RANK["c7a"] = 90`, so by default AWS is a **last-resort** fallback (stage A).
- A new Firestore field `config/encoding-worker.speed_rank_overrides` (e.g. `{"c7a": 5}`) makes it **preferred** (stage C) with no deploy. Rollback deletes the field.

**Warmups**
- `worker_service._warmup_encoding_worker` and `POST /internal/encoding-worker/warmup` currently start only the GCE primary.
- They will use the same ranked path as the encode itself (`manager.ensure_preferred_running()`). Otherwise, with AWS preferred, they would warm a VM that isn't routed to.

**Exclusions from GCE-only machinery**
- CI blue-green green selection: `deploy_promote.select_green_candidates` and the jq in ci.yml.
- The idle Cloud Function's `_parse_fallback_vms`.
- The box gets new code through the existing per-job `ensure_latest_wheel` hot update, so it needs no blue-green deploy.

**Idle stop**
- A systemd timer **on the box**, independent of the uvicorn process, runs every 2 min.
- It stops the instance (`shutdown -h now`, instance-initiated shutdown = stop) when either:
  - `/health` reports `active_jobs == 0`, and `last_activity_at` is older than 5 min or the box isn't the routed worker; or
  - `/health` has failed for 15 min.
- This mirrors the idle function's rules.

**Worker boot on AWS**
- `startup-aws.sh` has no GCE metadata server. It fetches the API key and wheel with `gcloud` over WIF and sets `ENCODING_WORKER_VM_NAME`.
- `ensure_latest_wheel` uses `gsutil`, which doesn't support WIF credential files. On AWS (`ENCODING_WORKER_PROVIDER=aws`) it uses `gcloud storage`; the GCE path is unchanged.

### Rollout
- **A:** deploy and register the box at rank 90. It's only exercised on c4d+c2d stockouts.
- **B:** set `speed_rank_overrides {"c7a": 5}` for a supervised window. Run the daily E2E plus admin test jobs, and compare render/encode times and outputs (including the portrait parity check) against GCE.
- **C:** leave the override on; AWS is preferred. After a week clean, delete the c4d pair (Pulumi), leaving c2df as the GCP fallback.

### Testing
- **Unit:**
  - adapter (botocore `Stubber`): state map, capacity/NotFound mapping, start/stop idempotency
  - manager routing (`_compute_for`), `ensure_preferred_running`
  - preference `rank_overrides`, `c7a` family
  - fallback parsing passthrough of `provider`/`instance_id`/`region`
  - deploy_promote and idle-fn exclusion of aws entries
  - the idle watchdog decision function
  - `ensure_latest_wheel` command selection
  - Extend the existing test files listed in the GCE-coupling map.
- **Integration (prod, supervised):** stage B runs real jobs through the AWS worker. The prod daily E2E (payment + happy path) is the end-to-end check.

## Phase 2: distribution from AWS (implemented 2026-10-31, off by default)

The video worker (packaging, encode, Dropbox/YouTube/Drive uploads, notifications) can run **on the AWS box** instead of in the Cloud Run job `video-encoding-job`. The large uploads then leave from AWS (credits) instead of GCP (the ~$18/mo of Cloud Run internet egress), and the job's Cloud Run CPU (~$11/mo) goes away.

**Encoding path unchanged (output parity)**
- The video worker on the box still encodes through the worker HTTP API, so the deliverables (portrait included) are identical.
- `LocalEncodingBackend` was rejected: it lacks the worker's portrait render.

**Same-box handoff** (`backend/services/local_encoded_outputs.py`)
- Without it, the video worker would download every final back out of GCS: ~400 MB/job of out-of-pocket egress, which costs more than the move saves.
- The encoder stashes each output under `LOCAL_ENCODED_OUTPUTS_DIR`, keyed by its GCS blob path, and the video worker takes it from there.
- The video worker asks for it per encode (`stash_local_outputs`, set only where the env var exists), so GCE and Cloud Run are unaffected. Copies not taken within 6 h are purged on every stash and at worker boot.

**`POST /run-worker`** (worker)
- Spawns `python -m backend.workers.video_worker --job-id …` with the box's video-worker env.
- Status is reported through the usual `/status/{id}`. A run counts in `/health` `active_jobs`, so the idle watchdog won't stop the box mid-run.
- It answers 503 unless `/opt/encoding-worker/video-worker-env.json` exists.

**Env and identity**
- At boot, `video-worker-env.py` reads the **live** `video-encoding-job` definition and resolves its secret refs, so nothing is hand-copied and nothing drifts.
- It adds `LOCAL_ENCODED_OUTPUTS_DIR` and `CLOUD_RUN_JOB`.
- The run uses the job's own identity, `karaoke-backend@`, via a second WIF binding (`aws-encoding-worker-wif-backend-user`).

**Trigger** (`WorkerService._trigger_video_worker_on_aws`)
- It applies only when Firestore `config/encoding-worker.aws_video_worker` is true **and** the AWS worker ranks first.
- It then ensures the box is running, waits for `/health`, and POSTs `/run-worker`.
- **Any** doubt or failure returns False, and the caller starts the Cloud Run job as today.

**Not covered**
- Deferred YouTube uploads (the hourly queue processor on the API) still leave from GCP; volume is low.
- Video-worker stdout stays on the box at `/var/log/encoding-worker-runs/`. The job's Firestore worker logs are unaffected, but the error monitor doesn't see the box's stderr.
- With the box on-demand (see the amendment above), Spot-interruption recovery is moot. A systemd restart of the worker during a run would still kill it; the existing `encoding_stuck` flag at 50 min surfaces that.

**Enable (after stage B, needs `/gcp-admin` for the IAM binding)**
1. Apply the GCP WIF backend binding.
2. Reboot the box once (`stop`/`start`) so `startup-aws.sh` writes the backend credentials and the env.
3. Set `aws_video_worker: true`.
4. Roll back by setting it to false.

## Stage A status (2026-10-29/31)

**Live:**
- `aws-encoding-worker-1`: `i-0c21505d7017356bb`, c7a.4xlarge on-demand, us-east-2b, EIP `3.128.70.236`.
- Provisioned from v0.288.0. WIF auth works for both reads and writes, and the API key, wheel and static ffmpeg all fetched fine.
- Controller key stored as `aws-encoding-controller-credentials` v1. Verified: `AwsComputeAdapter` with that key reads the box's state.

**Live test through the worker's real HTTP API** (job 4b611a81, outputs written to `gs://…/bench/aws-live/`, no prod paths):
- render-video completed in 117s.
- The encode wrote all 9 deliverables; 8 of 9 are byte-identical in size to prod's GCE finals.
- **Portrait parity:** same duration, SSIM 0.993, and identical lyrics. The only difference is sub-pixel anti-aliasing of the header/footer text (title, artist, brand), from the newer `fonts-noto` on Ubuntu 24.04. Visually indistinguishable, so it doesn't block stage C.
- A status poll timed out once while the worker downloaded inputs. `process_job` downloads synchronously on the event loop; that is pre-existing and the same on GCE, and the backend's poller tolerates it.

**Remaining for stage A (needs `/gcp-admin`):** append `pulumi stack output fallback_vms_entry` to the `encoding-worker-fallback-vms` secret, then **redeploy the backend**. Running Cloud Run instances keep the value they loaded at startup. Stage B begins after that deploy, by setting `speed_rank_overrides`.
