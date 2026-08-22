# Plan: Original-Vocals guide write-path

**Created:** 2026-07-09
**Branch:** feat/sess-20260709-0013-vocals-writepath
**Spec:** `docs/superpowers/specs/2026-07-09-original-vocals-writepath-design.md`
**Status:** In progress (autonomous)

## Verified code map (server render flow)

| Fact | Location |
|--|--|
| `mixed_vocals` stem uploaded to GCS | `backend/workers/audio_worker.py:510` → `jobs/{job_id}/stems/vocals_clean.flac` |
| Finalize/upload hook (has brand_code, base_name, local 720p) | `backend/workers/video_worker_orchestrator.py:852 _upload_to_gdrive` |
| 720p master (local at finalize) | `self.result.final_video_720p` (orch:870) |
| Existing 720p GCS push seam | `backend/services/gdrive_service.py:315-336` (`_fast_sync_nomad_master` → `NomadMasterMirror.push_720p`) |
| Intro seconds from style | `karaoke_gen/style_loader.py:688 get_video_durations(style_params)` → intro (default 5) |
| Style load | `backend/workers/style_helper.py StyleHelper(job, storage, temp_dir).load()` (orch has `self.storage`, `self.job_manager`) |
| GCS helpers | `backend/services/storage_service.py` (`download_file`, `delete`) |
| Brand-recycle / master cleanup seam | `backend/services/nomad_master_mirror.py cleanup_nomad_masters()` |
| Settings | `backend/config.py` (`divebar_files_bucket`, `nomad_master_gcs_prefix`, `nomad_master_fast_sync_enabled`, `gcs_bucket_name`) |

## Implementation steps (karaoke-gen)

1. [ ] `backend/config.py`: add `nomad_vocals_guide_gcs_prefix` (default `"files/Nomad Karaoke/vocals-padded"`).
2. [ ] `backend/services/original_vocals_guide.py` (NEW, pure + testable):
   `build_original_vocals_guide(mixed_vocals_path, intro_seconds, master_duration, dest_path, ffmpeg_path="ffmpeg") -> str | None`
   — ffmpeg `-i vocals -af "adelay={ms}:all=1" -t {master_duration} -c:a flac -f flac {dest}.part` → atomic replace. Returns dest or None (missing input / ffmpeg fail → None, never raises).
3. [ ] `backend/services/nomad_master_mirror.py`: add `push_vocals_guide(local_path, filename)` + `delete_vocals_guides_by_brand(brand_code)` (siblings of the 720p methods, keyed on `nomad_vocals_guide_gcs_prefix`). Extend `cleanup_nomad_masters` (or add sibling call) to also delete guides.
4. [ ] `video_worker_orchestrator.py _upload_to_gdrive`: before building `output_files`, call new `await self._emit_original_vocals_guide(brand_code, safe_base_name)` which (only for `is_nomad_public_brand`): loads job+style→intro secs, downloads `vocals_clean.flac`, ffprobes 720p→master_dur, builds guide named `{brand} - {base}.flac`, returns path. Register as `output_files["original_vocals_guide"]`. Best-effort; failures → `self.result.distribution_warnings`.
5. [ ] `gdrive_service.py _upload_public_share_files`: after the 720p fast-sync block, push `output_files["original_vocals_guide"]` via a new `_fast_sync_vocals_guide(brand_code, path, filename)` (gated `is_nomad_public_brand` + `nomad_master_fast_sync_enabled`, best-effort, warning on failure).
6. [ ] Tests: `test_original_vocals_guide.py` (builder: offset/cap/`-f flac`/None paths), extend `test_nomad_master_mirror.py` (push/delete vocals prefix + gating), wiring test (guide registered + pushed + warned).
7. [ ] Version bump `pyproject.toml`.

## Implementation steps (kjbox — separate worktree, PR only)

8. [ ] `kj-controller/config.py`: add `vocals_sync_enabled` (False), `vocals_sync_source` (`gs://nomadkaraoke-divebar-files/files/Nomad Karaoke/vocals-padded/`), `vocals_sync_dest` (""→`{download_folder}/NOMAD-vocals-padded`), `vocals_sync_delete_removed` (False).
9. [ ] `kj-controller/scripts/sync_masters.py main()`: after master `run_sync`, build a vocals config view (map `vocals_sync_*`→`master_sync_*` keys, `delete_removed=False`, no rescan poke) and call `run_sync` again when `vocals_sync_enabled`. `run_sync` unchanged.
10. [ ] Tests: extend `test_sync_masters.py` — both syncs run, vocals additive-only, isolation on vocals failure.

## Testing
- karaoke-gen: `make test` (or targeted pytest for new files) — unit for builder (synthetic tone+silence), mirror (mocked storage), wiring.
- kjbox: `cd kj-controller && pytest tests/unit/test_sync_masters.py`.

## Release
- karaoke-gen: full ship to `main` (inert change). 
- kjbox: PR only; **not merged/deployed** (live device — Andrew's maintenance window).

## Rollback
- karaoke-gen: revert PR; `nomad_master_fast_sync_enabled=false` also disables guide push.
- kjbox: `vocals_sync_enabled=false` default → inert; not deployed.
