# Original-Vocals Guide write-path — Design Spec

**Status:** Approved (autonomous session 2026-07-09; brainstormed with Andrew before handoff)
**Repos:** karaoke-gen (emit + push + cleanup) · kjbox (device pull)
**Related:** kjbox `docs/ORIGINAL-VOCALS.md`, `docs/archive/2026-07-06-original-vocals-guide-design.md`; workspace `docs/archive/2026-07-09-karaoke-gen-vocals-writepath-handoff.md`

## Goal

Make the "Original Vocals" sing-along guide **self-maintaining**. When karaoke-gen
finalises a new public NOMAD master, it automatically emits an aligned original-vocals
guide and pushes it to GCS; the kjbox device pulls it into `NOMAD-vocals-padded/`, so the
feature "just works" for new tracks with no re-run of the kjbox retro-fit pipeline.

## Key insight (why this is simpler than the retro-fit)

At render time karaoke-gen **already knows everything** the retro-fit had to *measure*:

- The **isolated vocals stem** is a byproduct of making the karaoke instrumental
  (`karaoke_gen/audio_processor.py` separation → `mixed_vocals`).
- The **exact intro offset** is the job's own style value — the master is
  `[silent title-card intro] + song + [tail]`, and the intro length is
  `intro_video_duration` from the job's resolved style (default 5s, but read the
  *actual* value, never hardcode 5).

So the guide is simply `silence[intro] + mixed_vocals`, capped to the master's duration.
**No correlation, no human review, no `align_offsets`/`align_decisions`, no "exclude"** —
the vocals ARE the true original and the alignment is known, so every new public NOMAD
render gets a valid guide for free. This is byte-compatible with what the retro-fit
`pad_vocals.sh` produced (`adelay` + flac), but built for free instead of measured.

## Decisions

| # | Decision | Choice | Rationale |
|--|--|--|--|
| Scope | One spec, both repos | ✅ | Agree the GCS prefix/naming contract once. |
| Vocals stem | `mixed_vocals` (lead + backing) | ✅ | Always produced (Stage 1); matches retro-fit "main vocal, backing left in"; fullest sing-along reference. |
| Emit shape | Padded (`silence[intro]+vocals`), not raw | ✅ | Zero device compute; offset is known at render. |
| Offset | Job's actual `intro_video_duration` | ✅ | Not hardcoded 5s — long-intro styles exist. |
| Length | Cap to 720p master duration (`-t`) | ✅ | Defensive: guide can never bleed past the master. |
| GCS layout | New sibling prefix `files/Nomad Karaoke/vocals-padded/` | ✅ | Parallel to masters `MP4-720p/`; device maps it to `NOMAD-vocals-padded/`. |
| Device pull | Extend existing 5-min timer to `run_sync` twice | ✅ | `run_sync` unchanged (never raises → can't touch masters mirror); no new systemd unit on the live box. |
| Sync mode | **Additive-only** (`vocals_sync_delete_removed=False`) | ✅ | The device already holds 1,459 retro-fit guides absent from the (initially near-empty) GCS prefix; a reconcile would try to delete them. Additive-only removes that risk entirely. |
| Privates | Gate on `is_nomad_public_brand` | ✅ | Skip `NOMADNP`. |
| Cleanup | Delete GCS guide on brand recycle | ✅ | Parallel to `cleanup_nomad_masters`. |

### Known limitation (documented, out of scope)

With additive-only sync, a **recycled** public NOMAD brand code (re-rendered as a
different song) leaves its **old** guide on the device. The play-time resolver globs
`NOMAD-#### - *` and takes `sorted()[0]`, so it could serve the stale vocal under the new
song. Public-NOMAD brand recycling is rare, so this is accepted for now. Future fix:
one-off backfill of the 1,459 existing device guides → the GCS prefix, then flip the
vocals sync to reconcile-enabled so deletes propagate cleanly. Until then, GCS-side
cleanup (`delete_vocals_guides_by_brand`) keeps GCS correct but won't auto-remove a stale
device copy.

## Architecture / data flow

```
karaoke-gen finalize (server flow, backend/services/gdrive_service.py)
  has: mixed_vocals stem + job intro_video_duration + freshly built 720p master
  ├─ build_original_vocals_guide():  ffmpeg -af "adelay={intro_ms}:all=1" -t {master_dur}
  │       -c:a flac -f flac  →  "{brand} - {base_name}.flac"   (atomic .part rename)
  │       registered as output_files["original_vocals_guide"]
  └─ _upload_public_share_files():
        push_720p()          → gs://…/MP4-720p/{brand} - name.mp4       (existing)
        push_vocals_guide()  → gs://…/vocals-padded/{brand} - name.flac (NEW, best-effort)

kjbox device (existing 5-min timer: kj-controller/scripts/sync_masters.py :: main())
  ├─ run_sync(master config)  → NOMAD-720p/            (existing, unchanged)
  └─ run_sync(vocals config)  → NOMAD-vocals-padded/   (NEW, additive-only, no /rescan poke)

play time (unchanged): routes.py::_resolve_vocals_guide() globs "NOMAD-#### - *"
  in NOMAD-vocals-padded/ → "Original Vocals" slider works
```

## Components & interfaces

### karaoke-gen

**`build_original_vocals_guide(mixed_vocals_path, intro_seconds, master_duration, dest_path, ffmpeg=...) -> str | None`**
(new helper; lives next to the audio/finalize code that has the separation result + style)
- ffmpeg: `-i mixed_vocals -af "adelay={intro_seconds*1000}:all=1" -t {master_duration} -c:a flac -f flac {dest}.part`, then atomic `os.replace` to `{dest}`.
- `-f flac` is mandatory (the `.part` extension would otherwise confuse the muxer — carried-over gotcha).
- Returns dest path on success, `None` on any failure (best-effort; never raises to the pipeline). Missing inputs → `None`.
- Emits `{brand_code} - {safe_base_name}.flac` (mirrors master naming so the device's brand-prefix glob resolves it).

**`NomadMasterMirror.push_vocals_guide(local_path, filename) -> bool`** (sibling of `push_720p`)
- Uploads to `gs://{divebar_files_bucket}/{nomad_vocals_guide_gcs_prefix}/{filename}`.
- New setting `nomad_vocals_guide_gcs_prefix` (default `"files/Nomad Karaoke/vocals-padded"`).
- Best-effort; returns success.

**`NomadMasterMirror.delete_vocals_guides_by_brand(brand_code) -> int`** (sibling of `delete_masters_by_brand`)
- Deletes `{vocals_prefix}/{brand_code} - *` (requires full `NOMAD-####`, refuses bare/non-Nomad — same guardrail as the master delete).

**Wiring:**
- `_fast_sync_nomad_master`-adjacent (or a new `_fast_sync_vocals_guide`) in `gdrive_service.py` calls `push_vocals_guide` right after the 720p push, gated by `nomad_master_fast_sync_enabled` + `is_nomad_public_brand`, surfacing failure as a distribution warning.
- `cleanup_nomad_masters(brand_code)` gains a sibling call (or is extended) to also delete guides, so every brand-recycle / job-delete path that already cleans masters cleans guides too.

### kjbox

**`sync_masters.py :: main()`** — after the existing master `run_sync`, call `run_sync` again with a vocals config view:
- `source = config["vocals_sync_source"]` (default `gs://nomadkaraoke-divebar-files/files/Nomad Karaoke/vocals-padded/`)
- `dest = config["vocals_sync_dest"]` or derived `{download_folder}/NOMAD-vocals-padded`
- `vocals_sync_enabled` (default False until the device is configured), `vocals_sync_delete_removed=False` (additive-only), and skip the `/rescan` poke (guides are glob-resolved at play time, never indexed).
- `run_sync` itself is **unchanged**; the vocals invocation passes a config dict with the `master_sync_*` keys populated from the `vocals_sync_*` values (thin adapter in `main()`), so the isolation/never-raise contract is preserved verbatim.
- New config keys added to `config.py` defaults.

## Error handling & safety

- **Never fatal.** Emit + push are best-effort; the Drive upload and render already succeeded. A guide failure = a `distribution_warnings` entry (admin alert), nothing more. The nightly Drive→GCS VM remains the masters backfill net (guides are new-renders-only; no VM path yet, acceptable).
- **Masters mirror untouched.** kjbox `run_sync` is not modified; a vocals-sync error returns an error dict and cannot break the masters sync in the same `main()` run.
- **Additive-only** protects the existing device guides (see Decisions).
- **Gating** on `is_nomad_public_brand` keeps `NOMADNP` privates out of the public GCS mirror.

## Testing strategy

**karaoke-gen (pytest):**
- Unit `build_original_vocals_guide`: synthetic tone+silence input → assert output offset (leading silence ≈ intro), duration capped to master, flac muxed via `-f flac`; missing input → `None`; ffmpeg failure → `None` (no raise).
- Unit `push_vocals_guide` / `delete_vocals_guides_by_brand`: mocked `storage.Client` — correct blob prefix, brand gating, best-effort return, bare-brand refusal. Mirror the existing `nomad_master_mirror` tests.
- Unit wiring: guide push is gated + surfaced as a warning like the master push; non-Nomad / disabled → no-op.

**kjbox (pytest, `test_sync_masters.py`):**
- `main()` invokes `run_sync` for both master and vocals configs.
- Vocals invocation is additive-only (`delete_removed=False`) and skips `/rescan`.
- A vocals `run_sync` failure does not fail the masters sync (isolation).

## Rollback

- karaoke-gen: revert the PR; the new GCS prefix simply stops being written. Nothing consumes it, so no user-facing effect either way. `nomad_master_fast_sync_enabled=false` also disables the guide push (shared gate).
- kjbox: `vocals_sync_enabled=false` (default) makes the device change inert; not merged/deployed autonomously — left for a maintenance window.

## Release plan

- **karaoke-gen:** full ship to `main` (main-based; Cloud Run deploys on merge). Change is inert until kjbox lands, so safe.
- **kjbox:** implement + test + PR only. **Not merged/deployed** in this session — live production device; merge+deploy is Andrew's call during a maintenance window (kjbox `CLAUDE.md`).
