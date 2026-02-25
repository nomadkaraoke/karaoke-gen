# Plan: Private (Non-Published) Tracks

**Created:** 2026-02-24
**Branch:** feat/sess-20260224-1149-private-tracks
**Status:** Implemented

## Overview

Add a `is_private` flag to jobs that changes distribution behavior:
- **Dropbox**: Uses `Tracks-NonPublished` folder (not `Tracks-Organized`) with brand code `NOMADNP` (not `NOMAD`)
- **YouTube**: Skipped entirely
- **Google Drive**: Skipped entirely
- **Email**: Completion email includes Dropbox link but no YouTube section
- **Discord**: Still notified (for internal tracking)

Available to all users at job creation and toggleable by admin on existing jobs.

## Requirements

- [ ] All users can mark a job as "Private (Non-Published)" when creating it
- [ ] Admin can toggle `is_private` on any existing job via admin dashboard
- [ ] Private jobs upload to Dropbox at `Tracks-NonPublished` with `NOMADNP-XXXX` brand codes
- [ ] Private jobs skip YouTube and Google Drive uploads entirely
- [ ] Completion emails omit YouTube section when no YouTube URL (private jobs + any other edge case)
- [ ] When admin toggles a completed job to private, existing outputs are auto-deleted
- [ ] Discord notifications still fire for private jobs
- [ ] Existing (non-private) jobs are completely unaffected (backward compatible)

## Technical Approach

### Core Design: Single Boolean Flag with Runtime Overrides

Store `is_private: bool = False` on the job model. At distribution time, a helper function returns effective distribution parameters based on this flag. This means:

- **Toggling is_private automatically changes all distribution behavior** - no need to manually update dropbox_path, brand_prefix, enable_youtube_upload, gdrive_folder_id
- **Job fields still store the "normal" defaults** - the override happens at the config-to-orchestrator boundary
- **Admin can flip one field** and everything adapts

### Key Helper Function

```python
# In job_defaults_service.py
def get_effective_distribution_for_job(job) -> EffectiveDistributionSettings:
    """Return distribution settings, applying private overrides if is_private=True."""
    if getattr(job, 'is_private', False):
        settings = get_settings()
        return EffectiveDistributionSettings(
            dropbox_path=settings.default_private_dropbox_path,
            brand_prefix=settings.default_private_brand_prefix,
            enable_youtube_upload=False,
            gdrive_folder_id=None,
            # Keep other settings from job (discord, youtube_description, etc.)
            discord_webhook_url=getattr(job, 'discord_webhook_url', None),
            youtube_description=None,
        )
    # Non-private: return job's own settings
    return EffectiveDistributionSettings(
        dropbox_path=getattr(job, 'dropbox_path', None),
        brand_prefix=getattr(job, 'brand_prefix', None),
        enable_youtube_upload=getattr(job, 'enable_youtube_upload', False),
        gdrive_folder_id=getattr(job, 'gdrive_folder_id', None),
        discord_webhook_url=getattr(job, 'discord_webhook_url', None),
        youtube_description=getattr(job, 'youtube_description_template', None),
    )
```

This helper is used in both distribution paths (orchestrator and native fallback).

### Distribution Paths That Need Changes

Two code paths handle distribution (see LESSONS-LEARNED: "Fix Both Sides of Dual Code Paths"):

1. **Orchestrator path** (`video_worker_orchestrator.py`): Primary path. `create_orchestrator_config_from_job()` uses the helper to build config.
2. **Native distribution path** (`video_worker.py` → `_handle_native_distribution()`): Fallback path. Also uses the helper instead of reading job fields directly.

### Email Template Change

Add conditional YouTube section removal in `render_template()`, matching the existing pattern for `feedback_url`:

```python
# Remove YouTube section if no URL provided
if not variables.get("youtube_url"):
    result = re.sub(
        r"\n*Here's the link for the karaoke video published to YouTube:\n\{youtube_url\}\n*",
        '\n',
        result,
        flags=re.DOTALL
    )
    variables["youtube_url"] = ""
```

And change `render_job_completion()` to pass `None` instead of `"[YouTube URL not available]"` when youtube_url is empty.

### Admin Toggle Auto-Delete

When admin PATCH updates `is_private` from False to True on a job that has existing outputs, reuse the existing delete-outputs logic to remove YouTube + GDrive + Dropbox outputs. Admin can then "Reprocess" to re-distribute to the private Dropbox path.

## Implementation Steps

### Phase 1: Backend Model & Config (foundation)

1. [ ] **Add `is_private` to Job model** (`backend/models/job.py`)
   - Add `is_private: bool = False` field after `made_for_you` field
   - Add to `JobCreate` model as well

2. [ ] **Add private distribution config** (`backend/config.py`)
   - Add `default_private_dropbox_path: Optional[str] = os.getenv("DEFAULT_PRIVATE_DROPBOX_PATH")`
   - Add `default_private_brand_prefix: Optional[str] = os.getenv("DEFAULT_PRIVATE_BRAND_PREFIX", "NOMADNP")`

3. [ ] **Add helper function** (`backend/services/job_defaults_service.py`)
   - Add `get_effective_distribution_for_job(job)` function that returns distribution settings with private overrides applied

### Phase 2: Backend API (job creation + admin)

4. [ ] **Add `is_private` to request models** (`backend/models/requests.py`)
   - Add `is_private: Optional[bool] = None` to `URLSubmissionRequest`
   - Add `is_private: Optional[bool] = None` to `AudioSearchRequest`

5. [ ] **Update URL creation endpoint** (`backend/api/routes/jobs.py`)
   - Accept and pass through `is_private` field to `JobCreate`

6. [ ] **Update file upload endpoint** (`backend/api/routes/file_upload.py`)
   - Accept `is_private` Form parameter
   - Pass through to `JobCreate`

7. [ ] **Update audio search endpoint** (`backend/api/routes/audio_search.py`)
   - Accept `is_private` in `AudioSearchRequest`
   - Pass through to `JobCreate`

8. [ ] **Update admin endpoints** (`backend/api/routes/admin.py`)
   - Add `is_private: Optional[bool] = None` to `JobUpdateRequest`
   - Add `"is_private"` to `EDITABLE_JOB_FIELDS`
   - In the PATCH handler: detect when `is_private` changes from False→True on a completed job, auto-delete existing outputs (call existing delete-outputs service logic)

### Phase 3: Distribution Pipeline

9. [ ] **Update orchestrator config creation** (`backend/workers/video_worker_orchestrator.py`)
   - In `create_orchestrator_config_from_job()`: Use `get_effective_distribution_for_job()` helper to determine dropbox_path, brand_prefix, enable_youtube_upload, gdrive_folder_id instead of reading directly from job

10. [ ] **Update native distribution fallback** (`backend/workers/video_worker.py`)
    - In `_handle_native_distribution()`: Use `get_effective_distribution_for_job()` helper instead of reading directly from job fields

### Phase 4: Email Template

11. [ ] **Add YouTube section conditional removal** (`backend/services/template_service.py`)
    - In `render_template()`: Add regex to remove YouTube paragraph when `youtube_url` is empty (matching existing `feedback_url` pattern)
    - In `render_job_completion()`: Pass `None` for youtube_url when empty (instead of `"[YouTube URL not available]"`)

### Phase 5: Frontend

12. [ ] **Add private checkbox to job creation form** (`frontend/components/job/JobSubmission.tsx`)
    - Add "Private (Non-Published)" checkbox below the existing "Skip lyrics review" checkbox
    - Show for all users (not just admin)
    - Include brief description text: "Private tracks go to Dropbox only, not YouTube or Google Drive"

13. [ ] **Update API interfaces** (`frontend/lib/api.ts`)
    - Add `is_private?: boolean` to all job creation option interfaces
    - Add `is_private` to `JobUpdateRequest` interface
    - Pass through in `uploadJob()`, `createJobFromUrl()`, `searchAudio()` methods

14. [ ] **Add private toggle to admin job detail** (`frontend/app/admin/jobs/page.tsx`)
    - Add `is_private` as an inline-editable field in the job overview section (use Switch component)
    - Show current state with visual indicator (e.g., lock icon for private jobs)
    - When toggling to private on a completed job, show confirmation dialog about auto-deleting outputs

### Phase 6: Environment Configuration

15. [ ] **Update CI/deployment config** (`.github/workflows/ci.yml` or Cloud Run env)
    - Add `DEFAULT_PRIVATE_DROPBOX_PATH` environment variable (e.g., `/Karaoke/Tracks-NonPublished`)
    - Add `DEFAULT_PRIVATE_BRAND_PREFIX` environment variable (e.g., `NOMADNP`)
    - These should be configured in Cloud Run service and CI test environment

### Phase 7: Testing

16. [ ] **Unit tests for private distribution logic** (`backend/tests/test_private_tracks.py`)
    - Test `get_effective_distribution_for_job()` with private and non-private jobs
    - Test `create_orchestrator_config_from_job()` applies private overrides correctly
    - Test `_run_distribution()` skips YouTube and GDrive when private config applied
    - Test `_upload_to_dropbox()` uses private path and brand prefix
    - Test brand code generation with NOMADNP prefix in Tracks-NonPublished
    - Test `_handle_native_distribution()` respects private overrides
    - Test non-private jobs are completely unaffected (regression tests)

17. [ ] **Unit tests for email changes** (add to `backend/tests/test_email_service.py` or `test_template_service.py`)
    - Test YouTube section removed from email when youtube_url is None
    - Test YouTube section present when youtube_url is provided
    - Test Dropbox section still present for private jobs
    - Test full email rendering for private job scenario

18. [ ] **Unit tests for admin toggle** (add to `backend/tests/test_admin.py` or new file)
    - Test admin can update is_private field
    - Test toggling is_private to True on completed job triggers output deletion
    - Test toggling is_private to True on non-completed job does NOT delete outputs
    - Test toggling is_private to False does nothing special

19. [ ] **Integration test** (`backend/tests/emulator/` or `test_distribution_services.py`)
    - Test full distribution pipeline with is_private=True: verify Dropbox path, brand prefix, YouTube skip, GDrive skip

20. [ ] **Frontend E2E regression test** (`frontend/e2e/regression/`)
    - Test private checkbox appears in job creation form
    - Test private toggle in admin dashboard

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `backend/models/job.py` | Modify | Add `is_private: bool = False` to Job and JobCreate |
| `backend/config.py` | Modify | Add `default_private_dropbox_path` and `default_private_brand_prefix` |
| `backend/services/job_defaults_service.py` | Modify | Add `get_effective_distribution_for_job()` helper |
| `backend/models/requests.py` | Modify | Add `is_private` to URL and AudioSearch request models |
| `backend/api/routes/jobs.py` | Modify | Accept and pass `is_private` |
| `backend/api/routes/file_upload.py` | Modify | Accept and pass `is_private` |
| `backend/api/routes/audio_search.py` | Modify | Accept and pass `is_private` |
| `backend/api/routes/admin.py` | Modify | Add `is_private` to editable fields + auto-delete logic |
| `backend/workers/video_worker_orchestrator.py` | Modify | Use helper in `create_orchestrator_config_from_job()` |
| `backend/workers/video_worker.py` | Modify | Use helper in `_handle_native_distribution()` |
| `backend/services/template_service.py` | Modify | Conditional YouTube section removal |
| `frontend/components/job/JobSubmission.tsx` | Modify | Add private checkbox |
| `frontend/lib/api.ts` | Modify | Add `is_private` to interfaces and API calls |
| `frontend/app/admin/jobs/page.tsx` | Modify | Add private toggle to admin detail |
| `backend/tests/test_private_tracks.py` | Create | Comprehensive unit tests for private track logic |

## Testing Strategy

### Unit Tests (~25-30 tests in `test_private_tracks.py`)
- `get_effective_distribution_for_job()` with private=True and False
- `create_orchestrator_config_from_job()` private overrides (YouTube=False, GDrive=None, private Dropbox path, NOMADNP brand)
- Distribution stage skips: YouTube not called, GDrive not called, Dropbox IS called with correct path
- Brand code generation uses NOMADNP prefix and Tracks-NonPublished path
- Native distribution fallback applies same overrides
- **Regression tests**: Non-private jobs have unchanged behavior (same paths, same brand code, YouTube+GDrive+Dropbox all fire)

### Email Template Tests (~5 tests)
- YouTube section stripped when youtube_url is None
- YouTube section preserved when youtube_url has value
- Dropbox section always present
- Full private job email rendering (Dropbox + no YouTube)
- Full non-private job email rendering (both present) - regression

### Admin Toggle Tests (~5 tests)
- is_private editable via PATCH
- Toggle to private on completed job deletes outputs
- Toggle to private on pending/in-progress job does NOT delete
- Toggle to non-private does nothing special

### Frontend Tests
- E2E: Private checkbox visible and functional in job creation
- E2E: Admin toggle visible and includes confirmation dialog

### Manual Verification
- Create a private job end-to-end, verify Dropbox folder in Tracks-NonPublished with NOMADNP code
- Verify completion email has Dropbox link but no YouTube section
- Toggle existing completed job to private, verify outputs deleted

## Regression Risk Analysis

| Risk | Mitigation |
|------|-----------|
| Non-private jobs affected | `is_private` defaults to `False`; helper returns unchanged values for non-private. Explicit regression tests. |
| Dual code paths (orchestrator vs native) | Both paths use same helper function. Tests cover both paths. (Lesson: "Fix Both Sides of Dual Code Paths") |
| Email template regex breaks other content | Regex targets specific YouTube paragraph text. Tests verify non-YouTube content untouched. |
| Brand code namespace collision | NOMADNP codes are in a completely separate Dropbox folder (Tracks-NonPublished), no overlap with NOMAD codes in Tracks-Organized |
| Admin auto-delete on wrong job | Only triggers when `is_private` changes from False→True AND job has terminal status (complete). Confirmation dialog in frontend. |
| Made-for-you orders | MFY webhook doesn't set is_private (defaults False). Admin can toggle after. No change to MFY flow. |
| Existing Firestore documents | Missing `is_private` field defaults to `False` via model default. No migration needed. |

## Open Questions

- [x] Who sees private toggle? → **Everyone** (all users at creation, admin on existing)
- [x] Auto-delete on toggle? → **Yes**, auto-delete all outputs when toggling to private on completed job
- [x] Email format? → **Remove YouTube section** when no YouTube URL (improves all edge cases)
- [x] Discord? → **Still notify** (useful for internal tracking)

## Rollback Plan

1. **Feature flag**: The `is_private` field defaults to `False`, so setting `DEFAULT_PRIVATE_DROPBOX_PATH` to empty/None effectively disables private distribution
2. **No schema migration needed**: Missing `is_private` field on existing Firestore documents defaults to `False`
3. **Email change is backward-compatible**: The YouTube section removal only happens when youtube_url is None, which is already an edge case showing "[YouTube URL not available]"
4. **Frontend**: Private checkbox is additive UI, removing it just means users can't create new private jobs
