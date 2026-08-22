# Nomad master fast-sync to kjbox — diagnosis + plan (2026-07-02)

## Symptom
New Nomad releases made on karaoke-gen are expected to appear on kjbox at
`/opt/nomad/downloads/NOMAD-720p/` "within minutes". In reality the box is stuck
at **NOMAD-1497** while Google Drive already has **NOMAD-1500** (made today).

## Root cause (evidence-backed) — NOT a broken component
The sync is a **3-hop pipeline**. Two hops are continuous; the middle hop is **daily**:

| Hop | Mechanism | Cadence | State 2026-07-02 15:00 ET |
|-----|-----------|---------|---------------------------|
| 1. gen → **Google Drive** `MP4-720p/` | `gdrive_service.upload_to_public_share()` at finalize | immediate | ✅ has NOMAD-1500 |
| 2. Drive → **GCS** `gs://nomadkaraoke-divebar-files/files/Nomad Karaoke/MP4-720p/` | `divebar-sync` VM via `divebar-sync-vm-daily` scheduler | **once/day @ 03:00 ET** | ❌ stuck at 1497 |
| 3. GCS → **kjbox** `NOMAD-720p/` | `nomad-master-sync.timer` (`sync_masters.py`) | every 5 min | ✅ mirrors GCS (1497) |

Evidence:
- GCS masters folder latest object = `NOMAD-1497` (1496 objects) — read via `claude-readonly` SA.
- `divebar-sync` VM `lastStartTimestamp = 2026-07-02T03:00 ET`, now `TERMINATED` (self-terminates after rsync). It captured Drive up to 1497; 1498–1500 were made later today.
- kjbox timer healthy: fires every 5 min, `{changed:False, copied:0, error:None}` = correctly in sync with a stale GCS. Its clean result is **correct**, not a bug.
- karaoke-gen has **no** code that writes to the GCS divebar bucket (`grep` of `backend/` for `nomadkaraoke-divebar-files` / `MP4-720p` GCS → only Drive uploads). GCS is populated *exclusively* by the daily VM.

**Conclusion:** the "within minutes" guarantee only ever covered hop 3. Hop 2 is a
~24h bottleneck that was never built for freshness. Fresh releases are delayed up to ~1 day.

(Red herring ruled out: `master_sync_source` absent from device `config.json` — the sync
works, so `load_config()` supplies it from `config.py` defaults. Not the issue.)

## Options
- **A. Direct gen→GCS push at finalize (RECOMMENDED).** When gen uploads the 720p master
  to Drive, also upload it to the same GCS object the daily VM would produce. Existing kjbox
  5-min timer then delivers within minutes. Idempotent with the daily VM (same object name+size
  → rsync skips). No kjbox change. gen-side code + 1 IAM grant.
- B. Trigger the sync VM per release. Reuses machinery but boots an e2-medium VM each time
  (~1–2 min + full-tree rsync); heavy, can thrash on bursts.
- C. Run the daily VM hourly. Trivial cron change but delay up to ~1h (not "minutes") and pays
  VM boot ×24/day.

## Recommended design — Option A

### Where
`backend/workers/video_worker_orchestrator.py::_upload_to_gdrive()` (line ~874) and the sibling
call in `backend/workers/video_worker.py` (~1121). Both call
`gdrive.upload_to_public_share(root_folder_id=..., brand_code=..., base_name=..., output_files=...)`.

Cleanest: add the GCS push **inside `upload_to_public_share()`** (single place, both callers
covered) OR a small helper invoked right after it. Prefer inside, guarded, non-fatal.

### GCS object path (must match the daily VM exactly for idempotency)
`gs://nomadkaraoke-divebar-files/files/Nomad Karaoke/MP4-720p/{brand_code} - {safe_base_name}.mp4`
- Matches observed names, e.g. `.../MP4-720p/NOMAD-0001 - This Is Me Smiling - Prettier.mp4`.
- `filename_base = f"{brand_code} - {sanitize_filename(base_name)}"` — already computed in the method.

### Gating (correctness)
- Only push when `brand_prefix == "NOMAD"` (i.e. brand_code `NOMAD-####`).
  **Exclude `NOMADNP`** (private tracks) — they must not enter the public masters mirror.
- Only the 720p artifact (that's all kjbox mirrors). Skip 4K/CDG.

### Config
- `DIVEBAR_FILES_BUCKET` env (default `nomadkaraoke-divebar-files`).
- `NOMAD_MASTER_GCS_PREFIX` (default `files/Nomad Karaoke/MP4-720p`).
- Feature flag `NOMAD_MASTER_FAST_SYNC_ENABLED` (default true) for easy kill-switch.

### Reliability
- Wrap in try/except; on failure append a `distribution_warnings` entry and continue
  (exactly like the existing Drive/Dropbox uploads — never fail the pipeline). The daily VM
  remains the backfill safety net, so a missed fast-push self-heals within 24h.
- Reuse `google.cloud.storage` (already a gen dep). Upload via `blob.upload_from_filename`.

### Delete / replace symmetry (REQUIRED — Andrew's #1)
The whole existing mirror chain is **additive / never-delete**, so deletes & renames already
orphan files today:
- `divebar_file_sync.py` (VM) only ever downloads Drive→GCS; it never deletes a GCS object. A file
  gone from Drive is marked `missing:drive_404` in BigQuery *only if it was still pending*; anything
  already in GCS is never revisited → stale forever.
- kjbox `sync_masters.py` is explicitly additive ("never deletes local files").
So Drive deletes/renames leave orphans in **both** GCS and kjbox-local right now. Option A doesn't
introduce this, but it propagates new files faster and gives gen the clean hook to fix it for Nomad
masters.

gen already deletes Drive outputs at these events — mirror each with a GCS delete of the deterministic
master object `files/Nomad Karaoke/MP4-720p/{brand_code} - {name}.mp4`:
- Job delete: `services/job_manager.py:354`, `api/routes/jobs.py:396`
- Re-finalize / re-process (delete old outputs before re-upload): `api/routes/jobs.py:2270`
- Admin delete: `api/routes/admin.py:1700`
- Visibility change public→private: `services/visibility_change_service.py:267`

Rename/edit case: if artist/title changes, the object NAME changes. gen must delete the **old** object
name (reconstruct from the job's previously-stored output filename / `gdrive_files` metadata) and write
the new one — otherwise the rename orphans the old GCS object (the daily VM won't clean it either).

**Delete policy (RESOLVED by Andrew 2026-07-02):**
- **Community uploads: keep-forever in GCS — do NOT delete.** Deliberate: preserves valid karaoke
  video data even if a user removes it from their Drive. (This is why the general mirror is additive.)
- **Nomad Karaoke masters: deletes ARE allowed** — we control the source, and the goal is that the
  *latest edited version* is what ends up on kjbox. So Nomad deletes/renames SHOULD propagate all the
  way to kjbox-local (an orphaned old-named file would keep playing the stale cut).
- ⇒ Delete symmetry is **Nomad-brand only** (same gate as the push). Never delete community objects.

**Recommended mechanism for kjbox-local propagation:** make the NOMAD-720p mirror a *true mirror* of
its GCS source — change `sync_masters.py`'s rsync to `--delete-unmatched-destination-objects` (scoped
to the Nomad masters folder we fully control, NOT the general additive community policy). Then gen just
deletes/replaces the GCS object and kjbox auto-reconciles (deletes old, pulls new) on its next 5-min
tick — no separate gen→kjbox delete channel needed. This is the kjbox half of the change.
- ⚠️ **SAFETY GUARD (critical):** `--delete-unmatched` + an empty/failed source listing = wipe the
  whole local mirror. Guard: refuse to run the delete-mode rsync if the GCS source lists 0 objects
  (or drops below a sanity threshold vs. current local count); on any listing/auth error, fall back to
  additive-only. Never let a transient auth failure (like today's expired token) delete 1500 files.

### IAM (infra — needs `pulumi up` before merge)
Add binding: `backend_service_account` → `roles/storage.objectCreator` (create-only is enough;
use `objectAdmin` if we later want overwrite/idempotent re-put) on bucket
`nomadkaraoke-divebar-files`. Put it in `infrastructure/modules/iam/worker_sas.py`
(`grant_backend_compute_permissions`) or alongside the divebar bucket definition. ~5 lines.
- `grant_backend_compute_permissions` today grants only encoding-worker VM lifecycle — no bucket write.

### Testing
- Unit: mock `storage.Client`; assert (a) Nomad 720p → `upload_from_filename` called with the exact
  `files/Nomad Karaoke/MP4-720p/{filename_base}.mp4` blob name; (b) NOMADNP → NOT pushed;
  (c) non-Nomad brand → NOT pushed; (d) GCS failure → warning appended, pipeline continues.
- Idempotency note: gen writes the identical object name the VM would → next daily rsync no-ops.
- Prod E2E (post-deploy, once): finalize a throwaway NOMAD test job, assert object appears in GCS
  within seconds and on kjbox `NOMAD-720p/` within ~5 min.

## Immediate gap (1498–1500 not yet in GCS)
Independent of the durable fix. To backfill now (needs admin gcloud; readonly SA can't):
```bash
gcloud scheduler jobs run divebar-sync-vm-daily --location=us-central1 --project=nomadkaraoke
```
This starts the `divebar-sync` VM immediately → mirrors today's Drive releases to GCS → kjbox
picks them up within 5 min. Otherwise they flow automatically at tonight's 03:00 ET run.

## Phase 4 — Refresh-catalog sequential fix (infra: `divebar_lookup` CF)

Confirmed root cause of the failed manual test (2026-07-02): `_refresh()` (main.py:443) fires all 3
scheduler jobs via `client.run_job()` in a loop with **no wait**. The sync VM (`divebar-sync-vm-daily`)
only copies files already in the BigQuery index, but it launches ~concurrently with the index rebuild
(`divebar-mirror-daily`) and finishes (~3 min) before the index adds the new rows → just-published
files get indexed (searchable) but miss the GCS sync pass. The **nightly path avoids this by time gaps**
(mirror 02:00 → sync 03:00 → xref 06:00 ET); the button collapses those gaps.

Dependency truth: **sync VM depends on the index; xref depends on the index (catalog rows), NOT on the
GCS file sync.** So correct order = run index → (on completion) run sync-VM **and** xref.

**Recommended fix — chain-on-completion (robust, non-blocking, also hardens nightly):**
- `_refresh()` triggers ONLY `divebar-mirror-daily` (the index) and returns immediately (button stays snappy).
- The index builder (`divebar_mirror`), at the end of a successful run, triggers `divebar-sync-vm-daily`
  and `divebar-xref-rebuild-daily`.
- Nightly can then be simplified to a single index trigger (the chain follows), or left as-is (the extra
  staggered triggers become harmless no-ops / redundant). Keep nightly schedulers for now; just add the
  chain so BOTH paths sequence correctly.
- Alternative (simpler, worse UX) considered & rejected: make `_refresh()` block — synchronously await
  the index (poll `MAX(synced_at)` in `divebar_catalog`) then fire sync+xref. Rejected: blocks the button
  for minutes and risks the CF timeout.

## Implementation notes locked in (from code reads)
- **gen GCS client**: `StorageService` is hardwired to `settings.gcs_bucket_name` (gen's default bucket),
  so it can't target the divebar bucket. Add a dedicated `backend/services/nomad_master_mirror.py`
  (own `storage.Client`, `bucket=settings.divebar_files_bucket`) with `push_720p(local, filename)` and
  `delete_720p(filename)` — isolated, mockable, non-fatal. Config: `DIVEBAR_FILES_BUCKET`
  (`nomadkaraoke-divebar-files`), `NOMAD_MASTER_GCS_PREFIX` (`files/Nomad Karaoke/MP4-720p`),
  `NOMAD_MASTER_FAST_SYNC_ENABLED` (kill switch).
- **Push call site**: inside `gdrive_service.upload_to_public_share()` right after the 720p Drive upload
  (single place, covers both worker callers), guarded + non-fatal.
- **Brand gate**: `brand_code` prefix before first `-` == `"NOMAD"` exactly (so `NOMADNP-####` private is
  excluded). NB: in the BigQuery index `brand_code` is literally just `"NOMAD"` and the release number
  lives only in the filename — but gen's job-side `brand_code` here IS the full `NOMAD-####`.
- **Object name**: `{NOMAD_MASTER_GCS_PREFIX}/{brand_code} - {sanitize_filename(base_name)}.mp4` — matches
  the daily VM's output exactly → idempotent.

## Rollout
1. `pulumi up` locally for the IAM grant (verify no unrelated drift).
2. Code + unit tests; `make test`.
3. `/test-review` → `/docs-review` → `/coderabbit` → version bump → `/pr`.
4. Merge → deploy → prod E2E with a NOMAD test job.

## Decision log
1. Approach: **A — direct gen→GCS push (CONFIRMED by Andrew).**
2. Scope: **Nomad-brand only (CONFIRMED)** — push AND delete are Nomad-gated; community objects are
   never touched (keep-forever policy). Generalizing to all brands is out of scope.
3. Delete policy: **CONFIRMED** — Nomad deletes/renames propagate to GCS *and* kjbox-local (latest
   edit wins on the box); community = keep-forever. See "Delete/replace symmetry".
4. Current-gap backfill (1498–1500): Andrew triggered it via the kjbox "Refresh catalog" button
   2026-07-02 ~15:xx ET to verify the existing pipeline end-to-end (no code change).

## This is a two-repo change (kjbox + gen)
- **gen**: direct GCS push on Nomad finalize; delete/replace GCS object on Nomad job delete + rename;
  +1 Pulumi IAM grant (backend SA → objectAdmin on divebar bucket, need overwrite+delete not just create).
- **kjbox**: `sync_masters.py` → true-mirror rsync (`--delete-unmatched-destination-objects`) with the
  empty-source safety guard, so GCS deletes/renames reconcile onto the box.

## Still open
- Exact IAM role: `objectAdmin` (needs delete+overwrite for the delete/replace path) vs narrower.
- kjbox true-mirror safety-guard threshold (refuse delete if source count 0 / << local count).
