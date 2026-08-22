# Encoding worker capacity resilience — machine-type diversification (Option B)

**Date:** 2026-08-12
**Trigger:** Live incident — `POST /api/review/98bbd8b0/preview-video` returned 524 (Cloudflare
edge timeout) → browser "NetworkError". Final render for the same job also at risk.

## Root cause (confirmed via prod logs)

`c4d-highcpu-32` hit a **region-wide `ZONE_RESOURCE_POOL_EXHAUSTED` stockout** across
`us-central1-a`, `-b`, and `-c` simultaneously. The encoding-worker fleet is:

- primary pair `encoding-worker-{a,b}` — `c4d-highcpu-32`, `us-central1-c`
- fallbacks `encoding-worker-fallback-{a,b}` — `c4d-highcpu-32`, `us-central1-a` / `-b`

Every lane is the **same machine family in the same region**, so a single-family stockout
exhausts all of them. `ensure_any_running` iterated all candidates, all returned 503/stockout,
and:

- **Preview** fell back to slow local Cloud-Run encoding (~130–160 s) → exceeded Cloudflare's
  ~100 s edge timeout → **524 / NetworkError** (backend actually returned 200, but the client
  was already disconnected).
- **Render** parks in `RENDER_PENDING_CAPACITY` and auto-retries every 5 min (24 h window) —
  safe only if c4d capacity returns within 24 h.

Prior work (May 2026) already flagged this fragility: *gotcha #5 — "c4d-highcpu-32 capacity in
us-central1-f is unreliable."* We diversified **zones** but never **machine family**.

## Fix (Option B): diversify the fallback fleet across an independent capacity pool

Add fallback VMs on **`n2-highcpu-32`** (Intel Cascade/Ice Lake) — the deepest, most broadly
available pool on GCP — in `us-central1`. A c4d shortage cannot exhaust the n2 pool.

Decisions (confirmed with operator):
- Machine type: **n2-highcpu-32** (deepest pool). n2 does **not** support `hyperdisk-balanced`,
  so these VMs use **`pd-balanced`** boot disks (per-VM disk-type override).
- Region: **same region (us-central1)** — preserves GCS bucket locality; the idle-shutdown
  function already iterates fallback zones generically. Cross-region is a possible fast-follow.

The runtime **needs no logic change**: `ensure_any_running` already iterates arbitrary
`{vm, zone, ip}` candidates and `start_vm(zone=...)` is zone-generic. VMs are provisioned
**stopped**, so creating them needs no capacity; only an on-demand *start* draws from the n2 pool.

### New fleet (after change)
| VM | machine type | zone | disk |
|----|--------------|------|------|
| encoding-worker-fallback-a | c4d-highcpu-32 | us-central1-a | hyperdisk-balanced |
| encoding-worker-fallback-b | c4d-highcpu-32 | us-central1-b | hyperdisk-balanced |
| **encoding-worker-fallback-n2c** | **n2-highcpu-32** | us-central1-c | pd-balanced |
| **encoding-worker-fallback-n2f** | **n2-highcpu-32** | us-central1-f | pd-balanced |

## Change surface

1. `infrastructure/config.py` — add `MachineTypes.ENCODING_WORKER_ALT = "n2-highcpu-32"`;
   replace the parallel `FALLBACK_*` lists with a structured `FALLBACKS` list carrying
   `machine_type` + `disk_type` per VM (a/b kept byte-identical so Pulumi does not recreate them).
2. `infrastructure/compute/encoding_worker_vm.py` — build fallback IPs + VMs from `FALLBACKS`,
   honouring per-VM `machine_type` and `disk_type`.
3. Idle-shutdown function — **no change** (already zone-generic via the secret). Add a regression
   test covering an n2 fallback entry.
4. `encoding-worker-fallback-vms` **secret** — operator-set value: append the two n2 entries
   `{vm, zone, ip}` (with the newly-allocated static IPs) after `pulumi up`.
5. Tests — pure-Python config invariants (machine-type diversity present, n2⇒pd-balanced,
   unique names/IPs) + idle-shutdown n2 case.
6. Docs — `docs/LESSONS-LEARNED.md`, `docs/TROUBLESHOOTING.md`; version bump.

## Operational rollout (after merge-ready code)

1. `pulumi up` locally → provisions n2 IPs + VMs (stopped). Creating stopped VMs is not blocked
   by the c4d stockout.
2. Read the two allocated static IPs; add a new `encoding-worker-fallback-vms` secret version
   including the n2 entries.
3. Cloud Run picks up the new secret on next revision (or force a no-op deploy); the
   `video-encoding-job` and idle-shutdown function read `:latest` too.
4. Smoke test: manually `gcloud compute instances start` one n2 fallback → confirm `/health` 200
   and a test encode succeeds on n2, then stop it.
5. The live parked job (98bbd8b0) clears automatically once any capacity (n2 or recovered c4d)
   is reachable.

## Invariants to preserve (from May 2026 work)
- Both Cloud Run service AND `video-encoding-job` read `ENCODING_WORKER_FALLBACK_VMS`.
- `start_vm` inspects `operation.error_code` (never fire-and-forget).
- Capacity-like errors stay typed as `EncodingWorkerStartError` so the render worker parks.
- Idle-shutdown must stop the n2 fallbacks in their own zones (secret-driven — verified).
