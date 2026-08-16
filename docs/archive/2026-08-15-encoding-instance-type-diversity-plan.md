# Encoding Worker — Mature Multi-Instance-Type Strategy (Plan)

**Date:** 2026-08-15
**Branch:** `feat/sess-20260815-1915-encoding-instance-type-diversity`
**Status:** IMPLEMENTED (v0.195.0) — code + IaC + Packer landed; operator rollout steps below still pending.
**Context:** memory `project_gen_encoding_instance_type_diversity` + `_worker_serialization` + `_machine_family_diversity` + `_wheel_deps_missing`

## Goal

Replace "c4d primary + single n2 fallback family" with a **ranked pool of ≥5 encode-capable
x86_64 instance types** and **availability-aware selection**, so a render never stalls even
when 2+ instance types are fully stocked out in us-central1 — while still **preferring the
fastest type (c4d) whenever it's available**. Resilience, not abandoning c4d.

---

## Current system (verified against code, 2026-08-15)

| Piece | File | Behaviour |
|---|---|---|
| Runtime candidate order | `backend/services/encoding_service.py::_build_worker_candidates` | `[primary] + ENCODING_WORKER_FALLBACK_VMS (secret order)` |
| Runtime fall-through | `backend/services/encoding_worker_manager.py::ensure_any_running` / `_ensure_any_running_once` | Iterates candidates; capacity/transient error → next; sets `active_override_*` on fallback success |
| Deploy green pick | `infrastructure/encoding-worker/deploy_promote.py::select_green_candidates` | secondary first, then fallbacks, **n2-before-c4d**, excluding current override |
| Idle shutdown | `infrastructure/functions/encoding_worker_idle/main.py::_parse_fallback_vms` | Stops idle primary/secondary/fallbacks (secret-driven) |
| IaC pool | `infrastructure/config.py::EncodingWorkerConfig.FALLBACKS` + `compute/encoding_worker_vm.py` | 2× c4d (a/b) + 2× n2 (n2c/n2f) fallbacks, each `{machine_type, disk_type}` |
| Secret | `encoding-worker-fallback-vms` | `[{vm,zone,ip}×4]` — **no machine_type today** |
| ffmpeg | `infrastructure/packer/scripts/provision.sh:59` | johnvansickle **amd64-static** → x86_64 only |

**Live state now:** primary `encoding-worker-a` (c4d, us-central1-c) @0.194.2 serving; no override
(c4d capacity currently back). Fallback fleet = c4d a/b (zones a,b) + n2 n2c/n2f (zones c,f).
2 machine families, 6 VMs total.

**Two order heuristics exist independently** (runtime = secret order; deploy = n2-first) — the
plan unifies them behind one pure, scored module.

---

## Ground-truth SKU menu (us-central1 a/b/c/f, all present)

x86_64, 32 vCPU, ≥32 GB, highcpu — verified via `gcloud compute machine-types list`:

| Type | vCPU | RAM | Silicon | Disk support | Pool notes |
|---|---|---|---|---|---|
| **c4d-highcpu-32** | 32 | 60 | AMD EPYC Turin (Zen5) | hyperdisk-balanced only | **current primary, fastest**; in the crunch |
| **c4-highcpu-32** | 32 | 64 | Intel Emerald Rapids | hyperdisk-balanced only | newest Intel perf tier, also in-demand |
| **n4-highcpu-32** | 32 | 64 | Intel Emerald Rapids (Titanium) | hyperdisk-balanced only | flexible/deep Intel |
| **n4d-highcpu-32** | 32 | 64 | AMD (Titanium) | hyperdisk-balanced only | flexible/deep AMD |
| **c2d-highcpu-32** | 32 | 64 | AMD Milan (Zen3) | pd-balanced | mature, **deep** |
| **n2d-highcpu-32** | 32 | 32 | AMD Rome/Milan | pd-balanced | **deep**, 32 GB floor |
| **n2-highcpu-32** | 32 | 32 | Intel Cascade/Ice Lake | pd-balanced | **current fallback**, floor |

Excluded:
- `n1-highcpu-32` = **28.8 GB** → below the 32 GB serialized-4K floor.
- `e2-highcpu-32` = 32 GB but E2 is cost/shared-tier, variable perf → not for fast finalization.
- `c4a-/n4a-highcpu-32` (Arm Axion, 64 GB) → **needs a separate Arm static ffmpeg** (provision.sh
  bakes amd64 only). Documented as a future pool-doubler, NOT in this phase.
- `c3-highcpu-44` (44/88) / `c3d-highcpu-30` (30/59) — no exact 32-vCPU SKU; optional extra pools
  if we want more breadth later (both support pd + hyperdisk).

### Chosen pool (≥5, spanning ≥3 independent silicon pools)

Two tiers by intent:

- **Tier A — fast, newest-gen (may stock out together in a crunch):** `c4d` (keep primary), `c4`, `n4d`
- **Tier B — deep, mature, independent pools (the insurance):** `c2d`, `n2d`, `n2` (already deployed)

= **6 types across 4 zones and 4 distinct silicon lineages** (AMD Turin, Intel Emerald, AMD
Milan, Intel Cascade, AMD Rome). Even if ALL of Tier A (all newest-gen, the crunch cohort) is
exhausted at once, three independent mature pools keep serving. Meets "no disruption if 2+ types
stocked out."

Zone spread (one type per zone where possible to avoid same-type-same-zone correlation):
c4d→c (primary, keep) · c4→a · n4d→b · c2d→f · n2d→a · n2→c/f (keep n2c/n2f).

---

## Design

### 1. Shared, scored preference module (single source of truth)

New pure module `backend/services/encoding_worker_preference.py` (importable by BOTH backend and
`infrastructure/encoding-worker/deploy_promote.py` — deploy already `sys.path`-inserts that dir;
we vendor/import the pure function so runtime + deploy can never drift).

```python
SPEED_RANK = {          # lower = faster; seed from arch, refine with benchmark (§2)
  "c4d-highcpu-32": 10,
  "c4-highcpu-32":  20,
  "n4d-highcpu-32": 30,
  "c2d-highcpu-32": 50,
  "n2d-highcpu-32": 60,
  "n2-highcpu-32":  70,
}
COOLDOWN_SECONDS = 900  # a type that just stocked out is demoted for 15 min, then re-probed

def ordered_candidates(pool, capacity_state, now):
    """pool: [{vm,zone,ip,machine_type,kind}]. capacity_state: {type|type@zone: last_stockout_iso}.
    Returns pool ranked fastest-first, but any candidate whose (type[,zone]) stocked out within
    COOLDOWN is stable-partitioned to the back (keeping speed order within each group)."""
    def speed(c): return SPEED_RANK.get(c["machine_type"], _infer_rank(c))
    base = sorted(pool, key=speed)                       # fastest first — c4d stays top
    cooled = lambda c: _recently_out(capacity_state, c, now, COOLDOWN_SECONDS)
    return [c for c in base if not cooled(c)] + [c for c in base if cooled(c)]
```

Why this shape:
- **Fastest-first base order** → keeps preferring c4d/c4 whenever they're up (the whole reason c4d
  was chosen). No behavioural change on a healthy day.
- **Cooldown demotion** → the "availability-aware" layer. When c4d throws
  `ZONE_RESOURCE_POOL_EXHAUSTED`, we record it and stop paying the ~2 min-per-deploy /
  per-render probe cost on a known-dead type — but we DON'T remove it, we re-probe after 15 min so
  we snap back to c4d the moment capacity returns.
- **Deterministic + pure** → fully unit-testable; identical logic drives runtime and deploy.

Consumers:
- Runtime: `_build_worker_candidates` builds the pool (primary + all fallbacks, each carrying
  `machine_type`) and calls `ordered_candidates`. `ensure_any_running` iterates unchanged.
- Deploy: `select_green_candidates` builds the pool minus the current `active_override_vm`
  (guarantees a fresh green) and calls the SAME `ordered_candidates`. Drops the ad-hoc "n2-first"
  rule — the cooldown state now handles "don't waste time on the stocked-out fast type."

### 2. Capacity-state feedback (availability awareness)

- New Firestore map on `config/encoding-worker`: `capacity_state = { "<type>@<zone>": "<iso>" }`.
- Write `<iso>=now` in `_ensure_any_running_once` when a candidate raises
  `EncodingWorkerCapacityError`; clear a type's entry on a successful start of that type.
- Read in both consumers. Stale entries self-expire via COOLDOWN (no GC needed).
- Deploy's `Select & start green` step reads it too (already reads config), so CI stops probing a
  dead fast type first during a sustained stockout.
- Backward-compatible: absent map ⇒ pure speed order (today's behaviour).

### 3. Benchmark to build the real ranking

Seed `SPEED_RANK` from architecture now; replace with measured wall-time. Harness:

- `infrastructure/encoding-worker/benchmark_types.py` (manual, not CI — it starts real VMs):
  1. For each type: ensure its fallback VM is RUNNING + `/health` 200.
  2. POST an identical **canonical 4K encode** (pin one real production render's inputs in GCS;
     reuse the CI `test-assets` encode input but at full 4K/length for representativeness).
  3. Poll `/status`; record ffmpeg wall-time (worker already times jobs). Median of 3.
  4. Emit `ranking.json` → hand-copy medians into `SPEED_RANK` (as relative-to-c4d if preferred).
  5. Stop all started VMs.
- Re-run occasionally / after a new type is added. Document expected order: Turin ≳ Emerald ≈
  Genoa > Milan > Rome > Cascade (verify — don't assume).

### 4. IaC changes (Pulumi, purely additive)

- Extend `EncodingWorkerConfig.FALLBACKS` with the new entries, each `{suffix, zone_suffix,
  machine_type, disk_type}`:
  - `c4` → `{suffix:"c4a", zone:"a", machine:"c4-highcpu-32", disk:"hyperdisk-balanced"}`
  - `n4d`→ `{suffix:"n4db", zone:"b", machine:"n4d-highcpu-32", disk:"hyperdisk-balanced"}`
  - `c2d`→ `{suffix:"c2df", zone:"f", machine:"c2d-highcpu-32", disk:"pd-balanced"}`
  - `n2d`→ `{suffix:"n2da", zone:"a", machine:"n2d-highcpu-32", disk:"pd-balanced"}`
  (suffix names avoid collision; disk_type matches each family's capability — **c4/c4d/n4/n4d =
  hyperdisk-balanced ONLY; c2d/n2/n2d = pd-balanced**. Getting this wrong = pulumi error.)
- **Do NOT touch** existing a/b/n2c/n2f entries or the c4d a/b Address `description` strings
  (changing them forces an Address replace → cascades to replacing the c4d VM, which needs the
  exact scarce capacity — see `_machine_family_diversity`). Verify `pulumi preview` shows only
  `+N to create` (new IPs + new stopped VMs), zero replace.
- `create_encoding_worker_fallback_vms` already reads `machine_type`/`disk_type` per entry and
  provisions `desired_status=TERMINATED` — no code change beyond the config list.

### 5. Secret schema: add `machine_type`

- `encoding-worker-fallback-vms` entries become `{vm,zone,ip,machine_type}` (append the 4 new
  VMs' entries too). `machine_type` lets the preference module score without a name lookup.
- Backward-compatible reads: `_build_worker_candidates`, `_parse_fallback_vms`,
  `select_green_candidates` treat `machine_type` as optional; when absent, `_infer_rank` derives
  it from the vm-name/type substring (c4d/c4/n4d/c2d/n2d/n2). Primary/secondary machine_type
  (c4d) is known from config.
- Carry `machine_type` through `EncodingWorkerCandidate` (add field, default None).

### 6. Wheel-deps + ffmpeg companion checks

- **ffmpeg:** all chosen types are x86_64 → baked amd64 static ffmpeg works unchanged. Arm
  excluded precisely because it wouldn't. (No image change needed for this phase.)
- **Wheel deps** (`_wheel_deps_missing`): every fresh type VM hits first-render full-tree install;
  already covered by `ensure_latest_wheel` (single-wheel + `verify_wheel_imports` + 900s×3). But
  adding 4 fresh types multiplies first-render exposure → **strongly recommend the durable
  follow-up: bake the full dep tree (CPU-only torch) into the Packer image** so every type boots
  self-sufficient. One image-family rebuild covers all types (all share the `encoding-worker`
  image). Track as a fast-follow; not strictly blocking because the runtime guard exists.

---

## Architecture assessment: per-VM start/stop vs MIG vs reservations

| Option | Verdict |
|---|---|
| **Per-VM start/stop (today) + broadened ranked pool** | **RECOMMENDED for this phase.** Low-risk incremental extension of the system just hardened in #908–#910. Reuses `ensure_any_running`, `deploy_promote`, idle-shutdown, the secret, static IPs, IP-addressed blue-green. Delivers the resilience goal. Cost when idle ≈ boot disk only (~$10/mo/VM × ~4 new = ~$40/mo). |
| **Regional MIG + instance flexibility** | **Right long-term shape per GCP guidance, but DEFER.** A MIG lists ranked machine types and lets GCP find capacity across type×zone natively (add-a-type = one policy line, no VM sprawl). BUT it invalidates the just-stabilized model: MIG instances have ephemeral names/IPs → the entire Firestore primary/secondary/override addressing (routes by IP) breaks → needs an internal LB / service discovery (extra hop, cost); blue-green deploy (IP swap) becomes rolling-update; on-demand scale-to-zero isn't native (keep a warm instance = cost, or bolt on a scaler); the 1-heavy-encode serialization + in-memory queue + client-resubmit assume a stable addressable worker. High blast radius days after stabilizing. **Trigger to revisit:** we need horizontal autoscaling (>1 concurrent worker per burst) OR per-VM sprawl becomes unmanageable (>~10 types). |
| **Reservation / CUD for a fast baseline** | **Optional lever — user's cost call.** A 1× `c4d-highcpu-32` reservation in us-central1-c *guarantees* the primary can always start, eliminating the exact stockout behind #908/#910. Purely additive (attach to the primary), no code change, instantly removable when demand subsides. Cost ≈ full on-demand of one c4d-highcpu-32 (~$650–800/mo) whether running or not; 1yr CUD lowers rate but commits spend. Given demand is "expected to subside," a *temporary* reservation during the crunch is the pragmatic middle. |

**Recommendation:** ship the broadened ranked pool + adaptive selection now (per-VM model);
offer a temporary c4d reservation as an independent toggle; keep MIG documented as the future
path with explicit triggers.

---

## Testing strategy (per `docs/TESTING.md`)

- **Unit (pure, highest value):** `encoding_worker_preference` — fastest-first ordering, cooldown
  demotion + stable-partition, re-probe after COOLDOWN, missing-`machine_type` inference,
  empty/degenerate pools. `test_deploy_promote.py` — updated to assert new ordering + fresh-green
  exclusion + machine_type carry-through.
- **Unit:** `test_encoding_worker_manager.py` — multi-type fall-through, capacity_state
  write-on-stockout / clear-on-success.
- **Infra:** `test_encoding_worker_config.py` — every FALLBACK entry has a disk_type valid for its
  family (hyperdisk-only vs pd), unique VM/IP names, expected zone spread.
- **Emulator/integration:** capacity_state Firestore round-trip; idle-shutdown parses new entries.
- **Prod E2E (post-deploy, once):** start each new type, `/health` 200, run canonical encode,
  confirm output — codifies that each type actually encodes. (Doubles as the benchmark seed run.)

## Rollout (operator, needs WRITE creds — my ADC is read-only)

1. Merge code (preference module + consumers + tests) — no infra yet, behaviour identical (pool
   still 2 families until secret grows).
2. `cd infrastructure && pulumi up` (stack prod) → creates 4 new IPs + 4 stopped VMs. **Verify
   `+8 to create`, zero replace** before applying.
3. Add a new `encoding-worker-fallback-vms` secret version = existing 4 + 4 new entries, each with
   `machine_type`. Cloud Run svc + `video-encoding-job` + idle fn read `:latest` (cycle/redeploy to
   pick up).
4. Run `benchmark_types.py` → fill real `SPEED_RANK` medians → small follow-up PR.
5. (Optional) `bake full deps into Packer` fast-follow. (Optional) create c4d reservation.

## Decisions (locked 2026-08-15)

1. **Pool size — 6 types** (c4d, c4, n4d, c2d, n2d, n2). 4 silicon lineages × 4 zones.
2. **Reservation — NO.** Rely on the broadened pool; no guaranteed c4d baseline (revisit only if
   stockouts persist). No reservation/CUD in this effort.
3. **MIG — DEFER.** Ship per-VM start/stop; MIG documented as future path with triggers above.
4. **Packer dep-bake — INCLUDE IN THIS EFFORT.** Bake the full CPU-only-torch dep tree into the
   `encoding-worker` image so all 4 new type VMs (and existing ones) boot self-sufficient, instead
   of relying on first-render `ensure_latest_wheel`. See scope note below.

### Scope note: Packer dep-bake (now in-scope)

- `infrastructure/packer/scripts/provision.sh` currently bakes only
  `fastapi uvicorn google-cloud-storage aiofiles aiohttp packaging` (per `_wheel_deps_missing`).
  Extend it to `pip install` the full karaoke-gen dependency set with **CPU-only torch**
  (`--index-url https://download.pytorch.org/whl/cpu` for torch) into `/opt/encoding-worker/venv`
  at build time — the encode/render path needs the generator→correction/langchain chain
  (tenacity etc.) but NOT CUDA, so CPU torch keeps the image far smaller than the ~6.4 GB GPU tree.
- Keep `ensure_latest_wheel` as the runtime code-update path (it still upgrades the karaoke-gen
  wheel itself on each new version), but a fresh VM will now already have the whole dep tree →
  first render no longer risks a partial/timed-out full-tree install.
- Requires: image rebuild (`build-runner-images.yml`/Packer) + a Pulumi apply that repoints the
  `encoding-worker` image family. Re-apply the `gcloud compute disks update` IOPS tune after any
  rebuild that recreates disks (per `encoding_worker_vm.py` comments).
- **Sequencing:** rebuild the image FIRST (all types boot self-sufficient), then `pulumi up` the
  new VMs against the new image family, then grow the secret. This way the 4 new types never hit
  the fragile first-render install path at all.
- Verify amd64 CPU-torch wheels resolve for Python 3.13 during the Packer build (fail the build if
  `verify_wheel_imports`-equivalent import check fails, so a broken image never ships).
