# Lessons Learned

Key insights for future AI agents working on this codebase.

> **Full archive**: For detailed code examples and extended explanations, see [archive/2026-01-09-lessons-learned-archive.md](archive/2026-01-09-lessons-learned-archive.md)

---

## UX Patterns

### Fixture-Driven UI Verification
For UI that renders differently based on data (e.g., audio search result tiers), fetch real production data as fixtures and build a Playwright review tool. The `npm run fixtures:review-ui` pattern (41 fixtures, console-based `next()` advancement) was far more effective than manual testing or screenshots — caught edge cases with non-Latin filenames, single-character filenames, and underscore-separated names that unit tests alone would have missed.

### Filename Matching Normalization
When comparing filenames to search titles, treat underscores/hyphens/dots as word separators BEFORE stripping non-alphanumeric chars. Also require a minimum substring length (3 chars) to prevent false matches like filename "K" matching title "KoREH". For non-Latin scripts (Hebrew, etc.), return an indeterminate result rather than a false match or false mismatch.

### Song Suggestions: Don't Block Input, Enhance Later (Mar 2026)
Autocomplete on the artist/title input fields confused users — it wasn't clear why they should wait for results or what selecting one would do. Better approach: let users type freely on Step 1, then show catalog suggestions **in parallel with the audio search** on Step 2 (while they're already waiting). This way suggestions feel like a helpful enhancement rather than an obstacle. Two modes work well: (1) exact match → green "Song found" panel for formatting correction, (2) no match + poor audio results → amber "Did you mean?" fuzzy correction banner.

### Fuzzy Search: Artist Tiebreaker for Title-Based Fallback (Mar 2026)
When fuzzy-matching by title returns multiple artists with the same song name (e.g. "Bruises" by Lewis Capaldi vs Fox Stevenson), use the user's garbled artist input as a tiebreaker — `stringSimilarity(userInput, track.artist_name) * 0.2` as a bonus score. Also: Spotify's `q` parameter searches track names, not artist names, so `searchCatalogTracks(artist, artist, 20)` (artist as both query and filter) returns nothing. Use `searchCatalogTracks(artist, undefined, 20)` to get the artist's tracks via general search.

---

## Architecture Decisions

### Separate Collections for Multi-App Projects
When multiple apps share a GCP project, use distinct Firestore collection names (e.g., `gen_users`, `decide_users`) from day one to avoid schema conflicts.

### LyricsTranscriber: Library, Not Server
Use LyricsTranscriber as a library (call `transcribe()`, `correct()`, save JSON to GCS), NOT the blocking `ReviewServer`. Skip `server.start()` entirely.

### Video Generation Timing
Lyrics worker saves JSON → Human reviews → Render worker generates video. Video must include human-corrected lyrics, so generation happens AFTER review.

### Parallel Processing
Audio separation and lyrics transcription run in parallel. Both can fail independently. Screens generation waits for both to complete.

### GCE Instance Selection for FFmpeg
AMD EPYC (C4D series) significantly outperforms Intel Xeon (C4 series) for CPU-bound FFmpeg encoding with libass. C4D-highcpu-32 is ~5x faster than C4-standard-8. C4D requires `hyperdisk-balanced` disk type.

### Large Documents: GCS + Firestore Metadata (Mar 2026)
When a document's payload can exceed Firestore's 1MB limit (e.g., CorrectionData at 100KB+ per session), store the payload in GCS and keep only metadata in Firestore. Use SHA-256 hash deduplication to avoid re-uploading identical data. For cross-document queries (e.g., searching sessions across jobs), use Firestore collection group queries on the metadata subcollection with Python-side text filtering — Firestore doesn't support LIKE/substring queries natively.

### Queue Instead of Skip for External API Limits (Mar 2026)
When an external API has a daily quota (e.g., YouTube Data API v3's 10,000 units/day), **never silently skip** the operation when the limit is reached. Instead: (1) Track actual quota units consumed, not just operation counts — a hard "10 uploads/day" limit was far too conservative when each upload costs ~300 of 10,000 available units, (2) Queue failed operations for automatic retry when quota resets — users expected YouTube uploads and got nothing, with no notification, (3) Send follow-up notifications when deferred operations complete. **GCP Cloud Monitoring as single source of truth**: Initially we self-tracked per-operation costs in Firestore, but the estimates drifted from reality (4,493 estimated vs 2,682 actual). GCP Cloud Monitoring's `serviceruntime.googleapis.com/quota/rate/net_usage` metric has only ~7 min delay — viable as the primary data source. Use a lightweight Firestore "pending buffer" for the delay window, with entries auto-expiring after 10 min. Sum raw per-minute datapoints (no aggregation) to match the GCP Console number. Use the external API's timezone for reset calculations (YouTube resets at midnight Pacific Time, not UTC).

### Deferred Job Creation with Search Sessions (Mar 2026)
When a multi-step wizard searches for resources before the user confirms (e.g., audio search in Step 2, job settings in Step 3), don't create the backend resource during the search step. Instead, store a short-lived session (Firestore TTL: 30 min) with the search results, and create the resource only at the final confirm step with all values set correctly. Benefits: no orphan records, no wrong-value patching, no ghost entries in lists, no spurious notifications. The session pattern works well: `search-standalone` returns `search_session_id` + results, `create-from-search` takes `search_session_id` + user choices → creates job with final values. Sessions auto-expire via Firestore TTL; no cleanup needed on back/abandon.

### Combined Review Flow (Jan 2026)
When two sequential human review steps can be combined into one, do it. We originally had separate steps: (1) lyrics review, (2) instrumental selection. This doubled user friction and email notifications. By the time users enter lyrics review, audio separation is already complete - the instrumental stems are ready. We combined both into a single "Combined Review" session: users review lyrics AND select instrumental track on the same page, submit once. **Benefits**: Better UX (one interaction vs two), faster turnaround, reduced drop-off, simpler email notifications, cleaner codebase. **Implementation pattern**: Move analysis that was done after review (backing vocals analysis) to before review (in screens_worker). Include instrumental options in the correction-data endpoint. Require instrumental selection in the review completion request body. Remove the separate `AWAITING_INSTRUMENTAL_SELECTION` state from normal flow (keep for edge cases like finalise-only jobs for DB compatibility).

---

## Common Gotchas

### Gate Resource Recycling on Full Cleanup Confirmation (Feb 2026)
**What happened**: Brand code NOMAD-1271 was recycled and reused for a real song while old E2E test job files still existed in Google Drive, creating a duplicate in the public share.

**Root causes**:
1. `if gdrive_files:` skips cleanup when `gdrive_files = {}` (empty dict is falsy) — any falsy check on a collection will silently skip work
2. Brand code recycled after Dropbox cleanup success, *before* confirming GDrive was clean — partial cleanup allowed resource reuse with orphaned files

**Fix**: Use `if gdrive_files is not None` (or check explicitly). Gate resource recycling on **all** cleanup steps succeeding, not just the first one. When a resource has been distributed to N places, it's only safe to recycle when all N locations are confirmed clean.

**Pattern**: Treat resource IDs (brand codes, sequential IDs) like distributed transactions — either all distribution points are cleaned, or the ID stays reserved.

### Never Use JavaScript Getters in Zustand Stores (Mar 2026)
**What happened**: Tenant branding, features, and defaults always returned initial defaults instead of loaded tenant data. The Singa portal showed the Nomad Karaoke pink theme and default logo instead of Singa's green branding.

**Root cause**: The Zustand store defined computed values as JavaScript `get` property descriptors:
```typescript
// BROKEN - getter destroyed by Zustand's set() internals
const useStore = create((set, get) => ({
  tenant: null,
  get branding() { return get().tenant?.branding ?? DEFAULT_BRANDING },
}))
```
When Zustand calls `set()`, it uses `Object.assign({}, currentState, partialUpdate)`. `Object.assign` **invokes** getters and copies their return **values** as plain properties. After the first `set({ isLoading: true })`, `branding` became a frozen static copy of `DEFAULT_BRANDING` — all subsequent state updates were invisible to it.

**Fix**: Move computed values outside the store. Use standalone functions that compute from state on each access:
```typescript
function getBranding(state) { return state.tenant?.branding ?? DEFAULT_BRANDING }
export function useTenant() {
  const store = useTenantStore()
  return { ...store, branding: getBranding(store) }
}
```

**Pattern**: Never use `get` property descriptors in objects that will be spread or `Object.assign`'d. Zustand, Redux, and other state libraries that merge objects will silently destroy getters. Use wrapper functions or selector hooks instead.

### Conditional Returns Must Come After All Hooks (Mar 2026)
**What happened**: React Error #300 (hydration mismatch) in production on tenant portals. The error pointed to a hooks ordering violation.

**Root cause**: In `page.tsx`, a conditional early return (`if (isMounted && tenantInitialized && !isDefaultTenant) { return <TenantLandingPage /> }`) was placed between two `useEffect` hooks. When the condition was true, React saw fewer hooks than the initial render, violating the Rules of Hooks.

**Fix**: Move ALL conditional returns to AFTER the last hook call in the component. Add a comment: `// Must be AFTER all hooks to comply with React Rules of Hooks`.

**Pattern**: In Next.js static exports with client-side conditional rendering (e.g., `isMounted` guards), the conditional return is tempting to place early for readability. Don't. React requires the same hooks to execute in the same order on every render. Place conditional returns at the very end of the hooks section, never between hooks.

### Fail Fast, Don't Fall Back
Silent fallbacks hide configuration errors. When critical configuration is missing (themes, credentials, etc.), raise clear errors instead of falling back to defaults. Better to fail loudly during testing than silently produce incorrect output in production. Example: Theme validation (v0.109.0) now raises `ValueError` on incomplete themes instead of merging with defaults. This ensures all cloud jobs use complete, explicit themes with no silent degradation.

### Verify Active Worktree
Before making changes in a multi-worktree setup, verify which directory the user's dev server is running from.

### Theme-Aware Styling
Search for ALL non-theme-aware color patterns (`text-slate-*`, `bg-gray-*`) and fix in one pass. Use CSS variables like `text-muted-foreground`, `bg-card`, `border-border`.

### Backend Must Apply Defaults
When UI removes options, backend must apply sensible defaults. Don't rely on each endpoint to remember - centralize in core data layer.

### Centralize Job Creation Logic
When multiple code paths create jobs (file upload, audio search, webhooks), use a shared service for default resolution. The made-for-you webhook handler diverged from regular job creation, missing CDG/TXT defaults because it didn't call the shared `resolve_cdg_txt_defaults()` function. Fix: Create `job_defaults_service.py` with centralized helpers used by ALL job creation paths.

### Make State Transitions Loud, Not Silent (Feb 2026)
**What happened**: Jobs 06cfea29 and 984da08b got stuck at `pending` forever. The Made-For-You webhook handler triggered workers without first transitioning from PENDING to DOWNLOADING. The state machine returned `False` silently, workers ran, but job status never advanced.

**Root cause**: `transition_to_state()` returned `False` on invalid transitions, but callers didn't check the return value. Silent failures let bugs hide in production.

**Fix implemented**:
1. **Make failures loud**: `transition_to_state()` now raises `InvalidStateTransitionError` by default (use `raise_on_invalid=False` for soft failures)
2. **Centralize the pattern**: Created `start_job_processing()` helper that atomically transitions + triggers workers - all handlers use this instead of implementing independently
3. **Runtime consistency checks**: Added `job_health_service.py` with `check_job_consistency()` to detect stuck jobs (audio_complete but status=pending)
4. **Worker validation**: Workers now log warnings if triggered when job is in unexpected status

**Pattern**: When a function can fail, prefer exceptions over boolean returns. Silent failures accumulate into production bugs. Use centralized helpers for multi-step operations (state change + side effects) to prevent divergent implementations.

### Validate Pipeline Stage Outputs Before Proceeding (Feb 2026)
**What happened**: Jobs 5b6aba25 and 5161b069 completed with `enable_cdg=True` but no CDG ZIP was produced. The orchestrator silently caught a `FileNotFoundError` during CDG generation and proceeded to 11+ minutes of GPU encoding, distribution, and notifications — all for an incomplete output.

**Root cause**: CDG generation failed because `instrumental_selection: custom` had no case in the filename construction (fell through to "Backing" suffix). The `FileNotFoundError` was caught by a broad `except Exception` that just logged and continued, since "CDG is optional."

**Fix**: Add validation gates between pipeline stages. After packaging completes but before expensive encoding begins, verify that all enabled outputs were actually produced. If `enable_cdg=True` but no CDG ZIP exists, raise `RuntimeError` immediately. This fails fast, saves encoding costs, and makes the error visible.

**Pattern**: When a pipeline stage produces optional outputs that were explicitly requested, validate their existence before proceeding to the next stage. "Optional" means "can be disabled," not "can silently fail when enabled."

### Fix Both Sides of Dual Code Paths
When fixing a bug in a system with multiple code paths (e.g., legacy vs orchestrator, local vs cloud), verify ALL paths are fixed. PR #271 fixed the GCE worker to READ `instrumental_selection` but only checked the legacy path which was already SENDING it. The orchestrator path (production default) wasn't sending it. **Pattern**: If a component receives config from multiple callers, check ALL callers when fixing the receiving side. Write integration tests that cover each path.

### Test Real Code, Not Just Mocked Endpoints (Jan 2026)
**What happened**: Job `5a69afd1` failed in production with `ImportError: cannot import name 'run_video_worker' from 'backend.workers.video_worker'`. This function never existed - the import was incorrect from the start. The bug was introduced in commit 29be83cb (Jan 25, 2026) and reached production the same day.

**Why tests didn't catch it**: `test_internal_api.py` mocked the entire worker function, so the import statement inside the function was never executed:
```python
patch('backend.api.routes.internal.process_render_video', mock_render)
```
This verified the HTTP endpoint worked but bypassed all real worker code, including imports.

**The fix**: Added `test_render_video_worker_integration.py` with AST-based import validation that parses the worker source code and verifies every import actually exists. This test runs on every CI build and would have caught the bug before deployment.

**Pattern for preventing similar bugs**:
1. **Mock minimally**: Don't mock entire worker functions - mock only external dependencies (Firestore, GCS, APIs)
2. **Use AST validation**: For critical worker modules, add tests that parse the source and validate all imports exist
3. **Integration tests**: Use emulator-based integration tests (in `backend/tests/emulator/`) that execute real worker code paths

**Correct worker coordination pattern**:
```python
# ✅ CORRECT: Use WorkerService
from backend.services.worker_service import get_worker_service
worker_service = get_worker_service()
await worker_service.trigger_video_worker(job_id)

# ❌ WRONG: Import non-existent function directly
from backend.workers.video_worker import run_video_worker
await run_video_worker(job_id, job_manager, storage)
```

**Example 1:** The `gcs_path` parameter bug for remote flacfetch downloads was fixed for RED/OPS torrent sources in December 2025, but the same bug existed for YouTube sources. The fix only addressed one branch of the conditional, leaving YouTube downloads broken when remote was enabled. Always search for ALL code paths that might need the same fix.

**Example 2:** The countdown audio sync fix (PR #328) only fixed the writer side (`render_video_worker` updating `lyrics_metadata.has_countdown_padding`). The orchestrator path that READS that state and pads the instrumental wasn't updated. PR #338 added the reader-side fix: `OrchestratorConfig` now has `countdown_padding_seconds`, `create_orchestrator_config_from_job` reads from lyrics_metadata, and GCE worker pads instrumental audio. **Lesson**: When adding cross-worker state, trace the data flow end-to-end through ALL code paths (legacy KaraokeFinalise path AND orchestrator path).

**Example 3:** CDG sync fix (Feb 2026). Video rendering adds 3s countdown padding to audio AND shifts LRC timestamps by +3s. But CDG generation uses instrumental audio (no padding) while still using the countdown-shifted LRC timestamps, causing CDG lyrics to appear 3s late. **Root cause**: The LRC file was designed for countdown-padded video audio, but CDG uses unpadded instrumental. **Fix**: Pass `lrc_has_countdown_padding` flag to CDG generator, which strips the countdown segment and shifts timestamps back. **Pattern**: When one output format (video) transforms shared data (LRC), check if other consumers (CDG) need the original or transformed version.

### Worker Idempotency Must Complete the Lifecycle
When implementing idempotency checks that set `stage='running'` at start, workers MUST also set `stage='complete'` on success. Without the completion update, retries or reprocessing attempts will be blocked because the stage is permanently stuck at `'running'`. The fix in v0.108.14 added completion markers to render_video, video, and screens workers.

### pip Wheel Filename Validation (Feb 2026)
**What happened**: Job 749141f8 failed with "Cannot connect to host 136.119.50.148:8080" during encoding. Investigation revealed the GCE encoding worker service had crashed and was restarting. The crash was caused by pip rejecting `karaoke_gen-current.whl` with "Invalid wheel filename (wrong number of parts)".

**Root cause**: The `ensure_latest_wheel()` function in `backend/services/gce_encoding/main.py` downloaded all wheels from GCS using a wildcard pattern (`karaoke_gen-*.whl`), which included `karaoke_gen-current.whl`. This file is a convenience symlink/copy for CI deployment but lacks the version and platform tags required by PEP 427 wheel naming standard (`{distribution}-{version}-{python}-{abi}-{platform}.whl`). When pip tried to install it, it failed, causing the worker to crash.

**Why it was confusing**: The actual error ("Cannot connect") masked the real problem (wheel installation failure). The service was healthy but kept restarting due to the pip error, making it temporarily unavailable when Cloud Run tried to connect.

**Fix**: Filter out wheels containing '-current' before selecting which to install:
```python
wheels = glob.glob("/tmp/karaoke_gen-*.whl")
# Filter out karaoke_gen-current.whl (not a valid PEP 427 wheel name)
wheels = [w for w in wheels if '-current' not in w]
```

**Pattern**: When downloading artifacts with wildcards, filter results to exclude non-standard naming patterns before processing. CI convenience files (symlinks, "latest", "current") often don't follow the same naming conventions as versioned releases. Always validate artifact names match expected patterns before attempting to use them.

### Semantic Version Sorting (Feb 2026)
**What happened**: PR #384 fixed the invalid wheel filename crash but exposed a version sorting bug. Production encoder started showing "Encoder: v0.99.9" instead of the latest v0.116.1. The GCE encoding worker was running an extremely old version from months ago.

**Root cause**: After filtering out invalid wheels, the code used `sorted(wheels)[-1]` to select the "latest" wheel. This does **alphabetical sorting**, not semantic version sorting. String comparison puts "karaoke_gen-0.99.9-..." AFTER "karaoke_gen-0.116.1-..." because '9' > '1' in the second character position.

**Why it's tricky**: For some version ranges, alphabetical sorting happens to work correctly (0.100.0 > 0.99.0), but fails for others (0.99.9 > 0.116.1). This makes the bug intermittent and hard to notice until a specific version combination triggers it.

**Fix**: Extract version numbers from filenames and sort using `packaging.version.Version`:
```python
from packaging.version import Version
import re

def extract_version(wheel_path):
    """Extract version from wheel filename like karaoke_gen-0.116.1-py3-none-any.whl"""
    match = re.search(r'karaoke_gen-([0-9.]+)-', wheel_path)
    if match:
        return Version(match.group(1))
    return Version("0.0.0")  # Fallback for unparseable filenames

wheels.sort(key=extract_version, reverse=True)
latest_wheel = wheels[0]
```

**Pattern**: When selecting "latest" from a list of versioned artifacts (wheels, releases, tags), always use semantic version comparison, not string/alphabetical sorting. The `packaging` library is part of Python's standard packaging infrastructure and handles all semantic versioning edge cases (major.minor.patch, pre-releases, etc.). String sorting only works accidentally for some version ranges.

### Clear Worker Progress Keys When Reprocessing
When resetting a job or re-reviewing a completed job, all worker progress keys (`*_progress`) must be cleared from `state_data`. Workers check `state_data.{worker}_progress.stage == 'complete'` for idempotency - if stale keys exist from a previous run, workers will skip execution even though the job needs reprocessing. **Pattern**: Any operation that intends to re-run workers (admin reset, review resubmission) must explicitly clear progress keys using `job_manager.delete_state_data_keys()`. See `backend/api/routes/review.py:complete_review()` and `backend/api/routes/admin.py:clear_worker_state()`.

### Defense in Depth
Enforce critical requirements at multiple layers (e.g., reject at creation in JobManager + safety net at processing time).

### Retry Transient Failures (Feb 2026)
HTTP calls to services that can restart (VMs, containers) need retry logic with exponential backoff for connection errors.

**Implementation patterns:**
- Use exponential backoff with cap: 10s → 20s → 40s → 60s (capped)
- Retry on network errors (`httpx.RequestError`) and 5xx server errors
- Do NOT retry on 4xx client errors (bad request, auth failure, not found)
- Make retry configuration runtime-adjustable via environment variables
- Let exceptions bubble through retry logic before wrapping in custom error types
- Log each retry attempt with attempt number and wait time

**State persistence for retry capability:**
When operations can fail before completion, persist operation parameters BEFORE attempting the operation. This enables retry even if the operation fails. For example, when downloading audio, save the source info (provider, source_id, target_file, download_url) to the job document before calling the download service. If download fails, the retry endpoint can use these saved parameters to retry.

**Example (flacfetch client):**
```python
# Manual retry logic with exponential backoff
async def with_retry(func, *args, **kwargs):
    max_attempts = 9
    for attempt in range(1, max_attempts + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            if not should_retry(e):  # Don't retry 4xx errors
                raise
            if attempt >= max_attempts:
                raise
            wait_time = min(10 * (2 ** (attempt - 1)), 60)
            await asyncio.sleep(wait_time)
    raise last_exception

# Wrap implementation, handle errors after retry logic
async def download_by_id(...):
    try:
        return await with_retry(self._download_by_id_impl, ...)
    except httpx.RequestError as e:
        raise FlacfetchServiceError(f"Download failed after retries: {e}")
```

See `backend/services/flacfetch_client.py` and `backend/tests/test_flacfetch_client.py` for full implementation.

### Cross-Domain localStorage
Auth tokens in localStorage are domain-isolated. Keep auth on a single domain or use cookies with `domain=.example.com`.

### Standalone HTML Pages Need Auth Fallback
Standalone HTML pages (like instrumental review) that use magic link tokens should also check localStorage for user auth tokens. Priority: full auth token (doesn't expire) > magic link token (expires). This prevents logged-in users from being blocked when their magic link expires.

### Local Mode Detection Must Check Routing Pattern
When detecting local CLI mode on localhost, check both port AND routing pattern. Hash-based routing (`#/{jobId}/route`) indicates cloud mode even on localhost:3000, while path-based routing (`/local/route`) indicates local CLI mode. This allows E2E tests to run in cloud mode on localhost without being misdetected as local CLI mode. **Implementation**: `isLocalMode()` should return false when it detects the hash-based routing pattern `#/?[^/]+/(review|instrumental)`, regardless of hostname/port.

### Tab Visibility Refresh
Use `visibilitychange` event for immediate refresh when user returns to tab, not just polling intervals.

### Pydantic Silent Failures
Setting a field that doesn't exist in a Pydantic model silently does nothing. Verify fields exist.

### Firestore Consistency
Update job, then verify update is visible before triggering workers to avoid reading stale data.

### Stale Object References
After calling async functions that update the database, re-fetch objects before making additional updates to avoid overwriting changes.

### Cross-Worker State Communication
When deferring processing from one worker to another, you MUST update the job state to communicate what was done. The countdown padding bug regressed multiple times because `render_video_worker` added countdown padding to vocals/audio but didn't update `lyrics_metadata.has_countdown_padding`, so `video_worker` didn't know to pad the instrumental, causing audio desync. **Pattern**: If Worker A performs an action that Worker B must know about, Worker A MUST write that info to `job.state_data` immediately after the action. Don't rely on implicit assumptions between workers.

### Google Drive Query Escaping
Escape special chars in Google Drive API queries: `'` → `\'`, `\` → `\\\\`.

### Google Drive Stale Connection on Cloud Run (Feb 2026)
**What happened**: Every GDrive upload silently failed for ~1 month (64 errors, 12+ jobs). Errors: `[Errno 32] Broken pipe` or `[SSL: UNEXPECTED_EOF_WHILE_READING]`. Jobs completed with `gdrive_files: {}` and no alert was sent.

**Root cause**: The singleton `GoogleDriveService` caches its HTTP connection. When Cloud Run containers sit idle between jobs, the underlying TCP connection goes stale. The next API call hits a dead socket. The orchestrator catches all exceptions in distribution and continues silently.

**Fix** (v0.119.7):
1. Added tenacity retry with `_reset_service()` (sets `self._service = None` to force fresh connection) on transient errors (`BrokenPipeError`, `ConnectionResetError`, `ssl.SSLError`)
2. Added `distribution_warnings` field to `OrchestratorResult` to track non-fatal upload failures
3. Added Pushbullet notification when distribution uploads fail, so operator is alerted immediately

**Pattern**: Any singleton service that caches HTTP connections should have retry logic with connection reset for stale connection errors. Cloud Run containers can idle for minutes between requests, killing keep-alive connections. Also, never silently swallow distribution failures - always track and alert on them.

### FFmpeg Path Escaping
For subprocess without shell, FFmpeg filter paths need: apostrophes escaped as `'\\''`, special chars as `\\[char]`.

### Unicode in HTTP Headers
Sanitize user input (artist/title) to ASCII for HTTP headers. Smart quotes, em dashes from copy/paste cause encoding errors. For CJK characters (Chinese, Japanese, Korean) that can't be replaced with ASCII equivalents, use RFC 5987 `filename*=UTF-8''` encoding in Content-Disposition headers instead of trying to force latin-1.

### CJK Characters in Title Cards (Mar 2026)
**What happened**: Job `f8a36b60` with Chinese artist/title rendered question marks on the title card, and download links failed with `'latin-1' codec can't encode characters`.

**Root cause (title card)**: The title screen uses `Montserrat-Bold.ttf` which has no CJK glyphs. PIL renders missing glyphs as question marks. The karaoke subtitle renderer already uses Noto Sans (with `fonts-noto-cjk` installed in Docker), but the title card `VideoGenerator` had no font fallback.

**Root cause (downloads)**: `sanitize_filename()` correctly preserves CJK characters (they're valid in filenames), but the `Content-Disposition` header used simple `filename="..."` which requires latin-1 encoding. CJK characters can't be encoded in latin-1.

**Fix**: (1) Added CJK font fallback in `VideoGenerator._render_all_text()` - detects CJK characters per text element and uses system Noto Sans CJK font via `fontconfig` (`fc-match`). (2) Added RFC 5987 encoding for Content-Disposition headers when filename contains non-latin-1 characters.

**Pattern**: When rendering text with PIL, the configured font may not support all scripts. Check if text contains characters outside the font's coverage and fall back to a system font with broader Unicode support (Noto Sans CJK). For HTTP headers, always handle the case where filenames contain characters beyond latin-1.

### Unicode in File Paths (Feb 2026)
**What happened**: Job `1eab1172` with Russian title "я куплю тебе дом" (Cyrillic) failed with FFmpeg exit code 183. The temp subtitle file path contained Unicode characters: `temp_subtitles_zivert___я_куплю_тебе_дом_1770150285852.ass`.

**Root cause**: Python's `str.isalnum()` returns `True` for ALL Unicode alphanumeric characters (Cyrillic, Arabic, Chinese, etc.), not just ASCII. The code used `"".join(c if c.isalnum() else "_" for c in output_prefix)` to sanitize filenames, which allowed Cyrillic letters through because they're "alphanumeric" in Unicode. FFmpeg cannot handle Unicode characters in filter argument paths, causing file I/O errors.

**Fix**: Replace `c.isalnum()` with `c.isascii() and c.isalnum()` to only allow ASCII alphanumeric characters (a-z, A-Z, 0-9). This ensures temp file paths are safe for FFmpeg and other external tools.

**Locations fixed** (v0.115.2):
- `karaoke_gen/lyrics_transcriber/output/video.py:222` - `generate_video()` temp subtitle path
- `karaoke_gen/lyrics_transcriber/output/video.py:282` - `generate_preview_video()` temp subtitle path
- `karaoke_gen/utils/remote_cli.py:1970` - output folder name sanitization

**Pattern**: When sanitizing filenames or paths for external tools, always use ASCII-only checks. Don't assume `isalnum()`, `isalpha()`, or `isdigit()` are ASCII-only - they match all Unicode categories.

**Test coverage**: Added 10 comprehensive tests covering Cyrillic, Chinese, Arabic, emoji, and accented characters to prevent regression.

### Admin Job Restart Must Use Valid State Transitions (Feb 2026)
**What happened**: After fixing the Unicode bug (v0.115.2), job `1eab1172` was restarted using the admin "Full Restart" button. The restart triggered successfully, but then immediately failed with: "Invalid state transition for job 1eab1172: transcribing -> JobStatus.GENERATING_SCREENS. Valid transitions: ['correcting', 'failed']"

**Root cause**: The admin restart endpoint (`backend/api/routes/admin.py:2161`) was setting the job status to `transcribing` when `preserve_audio_stems=True`, then triggering the screens worker. The screens worker immediately tries to transition to `GENERATING_SCREENS` state. However, the state machine (`backend/models/job.py:STATE_TRANSITIONS`) doesn't allow `transcribing -> generating_screens` - only `transcribing -> correcting` or `transcribing -> failed`.

**Why this happened**: The code was written intuitively ("we're preserving audio stems and want to restart from screens, so set to transcribing") without checking the state machine's allowed transitions. The comment even said "Allows screens worker to run" but that was incorrect.

**Fix** (v0.115.3): Change restart endpoint to set status to `downloading` instead of `transcribing`. Looking at STATE_TRANSITIONS:
- `downloading` → `generating_screens` ✓ (valid)
- `audio_complete` → `generating_screens` ✓ (valid)
- `lyrics_complete` → `generating_screens` ✓ (valid)
- `transcribing` → `generating_screens` ✗ (invalid)

Using `downloading` is the safest choice because it's the most flexible entry state that allows transitioning to screens generation.

**Pattern**: When writing admin operations or restart logic that sets job status and triggers workers, always check `backend/models/job.py:STATE_TRANSITIONS` to ensure the target state allows transitioning to whatever state the worker will try to enter. Don't make assumptions about valid transitions - verify against the state machine definition.

### AudioShake Language Auto-Detection (Feb 2026)
**What happened**: Job `1eab1172` with Russian lyrics ("я куплю тебе дом") was transcribed by AudioShake, but the lyrics were translated to English instead of being transcribed in Russian. The transcription showed English words instead of Cyrillic characters.

**Root cause**: `karaoke_gen/lyrics_transcriber/transcribers/audioshake.py:152` was hardcoding `"language": "en"` in the AudioShake API request. This told AudioShake to transcribe/translate everything as English, regardless of the actual audio language.

**Fix** (v0.115.4): Remove the hardcoded language parameter from the API request. AudioShake supports language auto-detection when the `language` field is omitted. Tested with API call - omitting the language field allows AudioShake to auto-detect from 100+ supported languages (Russian, Chinese, Spanish, etc.).

```python
# Before (forced English):
data = {
    "targets": [{
        "model": "alignment",
        "formats": ["json"],
        "language": "en"  # ← Hardcoded
    }]
}

# After (auto-detect):
data = {
    "targets": [{
        "model": "alignment",
        "formats": ["json"]
        # Language omitted - AudioShake auto-detects
    }]
}
```

**Pattern**: When integrating with transcription/translation APIs, check if they support language auto-detection before hardcoding a language. Many modern APIs (Whisper, AudioShake, etc.) can auto-detect language, which makes the system work globally without configuration. Only hardcode a language when you need to force a specific output language or when the API requires it.

### Fonts in Docker
Base Docker images have no fonts. Install `fonts-noto-core`, `fonts-noto-cjk` for video rendering.

### Validate External API Format Support
Don't assume external APIs support all common formats. AudioShake only supports `.wav`, `.mp3`, `.aac`, `.flac`, `.aiff`, `.mp4`, `.mov` - NOT `.webm`, `.ogg`, `.m4a`, `.opus`. When remote flacfetch downloads YouTube audio as `.webm`, the lyrics worker must convert to FLAC before uploading. **Pattern**: Use a whitelist of known-supported formats and convert everything else, rather than trying to upload directly and hoping it works. Always check the API's supported format documentation.

### Cloud Run CPU Throttling Kills Background Tasks
Cloud Run throttles CPU to near-zero when the main request handler returns, even if background tasks are running. This caused lyrics processing (running as a FastAPI background task) to slow from 17-52 seconds to 8+ minutes, and instances being terminated mid-processing. **Fix**: Add `--no-cpu-throttling` flag to `gcloud run deploy`. Keep `--cpu-boost` for faster cold starts. **Diagnosis**: Look for "Application shutdown" in logs during long operations, and compare processing times (27x slowdown is a telltale sign).

### Cloud Run Premature Shutdown with Parallel Workers
When multiple workers run in parallel via BackgroundTasks, Cloud Run can shut down when one worker completes, killing others mid-processing. This happens because each worker endpoint returns quickly after spawning its BackgroundTask - when the audio worker's task completes, Cloud Run sees the container as idle. **Initial Fix**: Implement a `WorkerRegistry` that tracks active workers per job. Register at worker start, unregister in finally block. Add shutdown handler in FastAPI lifespan that calls `worker_registry.wait_for_completion(timeout=600)` before allowing shutdown. **Pattern**: `backend/workers/registry.py` provides the global registry; workers import and call `await worker_registry.register(job_id, "audio")` / `unregister()`.

### Cloud Run Jobs for Long-Running Workers (Better Solution)
The WorkerRegistry approach helped but didn't fully solve the problem - job c94cc9d6 was still killed mid-processing because Cloud Run sent "Shutting down user disabled instance" while lyrics worker was still running. **Better fix**: Use Cloud Run Jobs for long-running workers (lyrics, audio separation, audio download). Cloud Run Jobs run to completion without HTTP request lifecycle concerns. Workers have CLI entry points (`python -m backend.workers.lyrics_worker --job-id abc123`) and are triggered via `google.cloud.run_v2.JobsClient.run_job()`. Jobs can run up to 24 hours vs 30-minute HTTP timeout. Keep fast workers (screens, render) on HTTP with WorkerRegistry. **Pattern**: See `infrastructure/modules/cloud_run.py` for job definitions and `backend/services/worker_service.py` for triggering logic.

### Audio Downloads Must Use Cloud Run Jobs (Not BackgroundTasks)
Audio downloads via the guided flow (create-from-search, select audio source) used FastAPI `BackgroundTasks` to run downloads after the HTTP response returned. Cloud Run terminated "idle" instances (no active HTTP requests) within 1-2 minutes, silently killing downloads mid-progress. Jobs 51b8231d and 89e497b1 got permanently stuck at `downloading_audio` with no error. **Fix**: Move audio downloads to a Cloud Run Job (`audio-download-job`) just like lyrics and audio separation workers. The `audio_download_worker.py` extracts the download logic into a standalone process. **Defense in depth**: A Cloud Scheduler job runs every 5 minutes to detect and fail jobs stuck in `downloading_audio` for >10 minutes, enabling automatic retry.

### Add Progress Logging for Long Operations
Operations over ~30 seconds should log progress periodically (time-based, not count-based). Count-based logging like "log every 20 items" can miss entirely if items complete slowly. Time-based logging (every 30s) ensures visibility regardless of processing speed.

---

### AuthResult Has Tuple Unpacking But Not Subscripting

**Problem**: The `AuthResult` class returned by `require_admin` supports tuple unpacking via `__iter__` but NOT subscript access via `__getitem__`. Using `auth_data[0]` raises `TypeError: 'AuthResult' object is not subscriptable`.

**Misleading code**: Existing admin.py endpoints had incorrect type hints:
```python
auth_data: Tuple[str, UserType, int] = Depends(require_admin)  # WRONG type hint
admin_email = auth_data[0]  # Would get is_valid (bool), NOT email!
```

**Correct approach**: Access attributes directly:
```python
auth_data: AuthResult = Depends(require_admin)  # Correct type hint
admin_email = auth_data.user_email or "unknown"  # Use attribute access
```

**Why `auth_data[0]` appeared to work**: The `__iter__` method returns `(is_valid, user_type, remaining_uses, message)` - NOT email. Code using `auth_data[0]` was silently getting a boolean, not the admin email. This likely never caused visible failures because the value was only used in logs.

**Key insight**: When a class has `__iter__` for backward-compatible tuple unpacking but not `__getitem__`, prefer attribute access over index access. Type hints can be misleading if the codebase hasn't been updated consistently.

### Field Mapping Gaps in Manual Constructors (Feb 2026)
**What happened**: The `is_private` field was added to the `JobCreate` Pydantic model and the `Job` model, but `job_manager.create_job()` manually maps fields from `JobCreate` to `Job()` — and the `is_private` field was omitted from that mapping. The API accepted the field, the model had it, but it silently defaulted to `False` in Firestore because the constructor never included it.

**Root cause**: The constructor uses explicit field-by-field mapping (`Job(title=data.title, artist=data.artist, ...)`) rather than something like `data.model_dump()`. When a new field is added to the input model, it's easy to forget to add it to the manual mapping.

**Fix**: Add `is_private=data.is_private` to the `Job()` constructor in `create_job()`.

**Pattern**: When adding new fields to Pydantic models, trace the full path from API request -> model -> persistence to ensure the field is explicitly mapped at every step. Automated regression tests that verify round-trip persistence are essential — a test that creates a job with `is_private=True` and reads it back would have caught this immediately.

### Return Value Contract Mismatches (Feb 2026)
**What happened**: `job_manager.update_job()` returns `None` (no explicit return), but `admin.py` checked `if not success:` treating `None` as failure. Since `not None` is `True`, this caused ALL admin PATCH updates to return 500 errors — not just the private toggle.

**Root cause**: The calling code assumed `update_job()` returned a boolean success indicator, but the actual contract is void (returns `None`, raises on error). `success = await job_manager.update_job(...)` assigns `None`, and `if not success:` always evaluates to `True`.

**Fix**: Use `try/except` for void functions rather than checking return values:
```python
# ❌ WRONG - update_job returns None, not a boolean
success = await job_manager.update_job(job_id, updates)
if not success:
    raise HTTPException(status_code=500, ...)

# ✅ CORRECT - catch exceptions from void functions
try:
    await job_manager.update_job(job_id, updates)
except Exception as e:
    raise HTTPException(status_code=500, ...)
```

**Pattern**: When calling a function and checking its return value, verify what it actually returns. Use `try/except` for void functions rather than checking return values. This is closely related to the "Mocks Must Match Real Return Values" lesson — mocks that return `True` for a void function will hide this class of bug entirely.

---

## Testing Insights

### New Features Must Test the Summary Projection (Mar 2026)
When adding new fields that the dashboard needs, you must add them to **both** `SUMMARY_FIELD_PATHS` (Firestore projection) and `_SUMMARY_STATE_DATA_KEYS` (Python prune allowlist). The dashboard uses `fields=summary` which projects only listed fields — any unlisted field comes back as `undefined`. Unit tests that mock the job object directly won't catch this because they bypass the projection layer entirely. **Always add a regression test** asserting the field is in `SUMMARY_FIELD_PATHS` when introducing new fields the frontend depends on. The Change Visibility feature shipped with `is_private` missing from the projection, causing the button to always show "Make Private".

### Test Webhook Handlers with Unit Tests
Webhook handlers contain critical business logic. Test exact parameters passed to service methods, not just that "something happened."

### Test DTO-to-Entity Mapping
Testing webhook creates correct DTO isn't enough. Also test that manager/service copies all fields to entity.

### E2E Mocks Must Match Types
Mock responses must match TypeScript interfaces exactly. Silent failures from type mismatches are hard to debug.

### Mocks Hide API Contract Mismatches
When E2E tests use mocked API endpoints, the mocks define what you *think* the API is, not what it *actually* is. If frontend code calls a non-existent endpoint (`/api/jobs/{id}/lyrics`), a mock responding to that path will pass—but production will 404. **Pattern**: (1) Add unit tests that verify exact endpoint URLs, (2) Add at least one integration test that calls the real backend, (3) When mocks fail in ways that don't match prod, suspect a contract mismatch. Example: The "Add Reference Lyrics" feature had frontend calling `/api/jobs/{id}/lyrics` while backend expected `/api/review/{id}/add-lyrics`. Mocks used the wrong path too, so regression tests passed while production failed with 404.

### YouTube URL Downloads Must Happen Before Workers (Jan 2026)

**Problem**: Job 811ec4e5 (made_for_you order with YouTube URL) failed with "Failed to download audio file". Workers were triggered but `input_media_gcs_path` was null.

**Root cause**: The `_handle_made_for_you_order` webhook handler triggered workers directly for YouTube URLs without downloading audio first via `YouTubeDownloadService`. The correct flow (used in `file_upload.py` and `audio_search.py`) is:
1. Detect YouTube URL
2. Use `YouTubeDownloadService.download()` to download audio to GCS
3. Update job with `input_media_gcs_path`
4. THEN trigger workers

**Why tests didn't catch it**: The test `test_youtube_url_order_skips_search` mocked the entire `worker_service`, verifying only that `trigger_audio_worker.assert_called_once()`. The mock hid the fact that workers require `input_media_gcs_path` to be set, which only happens after `YouTubeDownloadService.download()` completes.

**Pattern for preventing similar bugs**:
1. **Test prerequisites, not just calls**: When testing worker triggers, verify all fields workers need are set BEFORE triggers
2. **Use call-order tracking**: Track sequence of operations to verify `download → update job → trigger workers`
3. **AST import validation**: Verify the handler imports and uses required services (same pattern as `test_job_creation_regression.py`)

**Example test pattern**:
```python
call_order = []

mock_youtube_service.download = AsyncMock(
    side_effect=lambda **kwargs: call_order.append('download')
)
mock_worker_service.trigger_audio_worker = AsyncMock(
    side_effect=lambda x: call_order.append('audio_worker')
)

# After calling handler...
assert 'download' in call_order, "YouTube download must be called"
download_index = call_order.index('download')
worker_index = call_order.index('audio_worker')
assert download_index < worker_index, "Download must happen BEFORE workers"
```

### Made-For-You Jobs Must Transition to DOWNLOADING Before Workers (Feb 2026)

**Problem**: Made-for-you orders with YouTube URLs (jobs 06cfea29, 984da08b) got stuck at `pending` status even though all processing completed successfully. Workers ran, `state_data` showed `audio_complete: true`, `lyrics_complete: true`, `screens_progress.stage: complete`, but the `status` field remained `pending`.

**Root cause**: The `_handle_made_for_you_order` function in `users.py` triggered workers for YouTube URL orders **without first transitioning the job from PENDING to DOWNLOADING**. The job state machine defines strict valid transitions:
- `PENDING` → `DOWNLOADING`, `SEARCHING_AUDIO`, `FAILED`, `CANCELLED` (only these are valid)
- Workers try: `PENDING` → `GENERATING_SCREENS` → `AWAITING_REVIEW` (INVALID!)

All status transitions after workers complete fail silently because they're invalid from `PENDING`.

**Why it was subtle**:
1. Workers completed successfully (no errors in logs)
2. `state_data` updates worked (they bypass state machine validation)
3. Only the `status` field failed to update (uses `transition_to_state` which validates)

**Fix**:
1. Immediate: Add `transition_to_state(job_id, JobStatus.DOWNLOADING)` before triggering workers
2. Robustness: Make `transition_to_state()` raise `InvalidStateTransitionError` by default instead of returning `False` silently
3. Pattern: Create centralized `job_manager.start_job_processing(job_id)` helper that handles transition + worker triggers atomically

**Robustness changes** (see `docs/archive/2026-02-02-state-machine-robustness-plan.md`):
- `InvalidStateTransitionError` exception with full context (job_id, from_status, to_status, valid_transitions)
- `validate_state_transition()` and `transition_to_state()` now have `raise_on_invalid=True` default
- `start_job_processing()` centralizes the pattern: validate prerequisites → transition → trigger workers

**Pattern**: Always verify state machine flow in webhook handlers. Use centralized helpers like `start_job_processing()` instead of duplicating transition + worker logic.

**Tests added**:
- `test_youtube_url_calls_start_job_processing_after_download` - verifies proper sequence
- `TestInvalidStateTransitionError`, `TestValidateStateTransitionRaisesBehavior`, `TestStartJobProcessing` in `test_job_manager.py`

### Use data-testid for E2E
Prefer `data-testid` over label/text selectors. They're immune to label changes and won't break when similar fields are added.

### Debounced Inputs for Formatted Controlled Components

**Problem**: Controlled number inputs with immediate formatting (e.g., `.toFixed(2)`) on every keystroke cause cursor jumping. When user types "2", the value updates to "2.00", and the cursor jumps to the end. User can't type multi-digit numbers like "12.34" smoothly.

**Root cause**: React's controlled input pattern updates `value` prop on every `onChange`, triggering re-render. When the value changes from "2" to "2.00", React resets the cursor position to the end.

**Solution**: Create a debounced input component with local state:
1. Maintain local string state while input is focused
2. Debounce updates to parent (e.g., 300ms) for live preview updates
3. Format and sync immediately on blur
4. Ignore parent updates while focused (user has control)

**Implementation pattern**:
```tsx
const [localValue, setLocalValue] = useState('')
const [isFocused, setIsFocused] = useState(false)

// Only sync from parent when not focused
useEffect(() => {
  if (!isFocused) {
    setLocalValue(value?.toFixed(2) ?? '')
  }
}, [value, isFocused])

// Debounce updates to parent
const debouncedUpdate = useCallback((val: string) => {
  if (debounceRef.current) clearTimeout(debounceRef.current)
  debounceRef.current = setTimeout(() => {
    onChange(parseFloat(val) || null)
  }, 300)
}, [onChange])

// Show local value when focused, formatted parent value when not
<Input
  value={isFocused ? localValue : (value?.toFixed(2) ?? '')}
  onFocus={(e) => { setIsFocused(true); e.target.select() }}
  onChange={(e) => { setLocalValue(e.target.value); debouncedUpdate(e.target.value) }}
  onBlur={() => { setIsFocused(false); onChange(parseFloat(localValue) || null) }}
/>
```

**Benefits**:
- Smooth typing experience (no cursor jumping)
- Live preview updates after debounce delay
- Formatted display when not editing
- Immediate sync on blur (no lost edits)

**When to use**: Any controlled input that needs formatting but must accept intermediate invalid states (partial numbers, decimals being typed, etc.). Common for number inputs with decimal formatting, currency inputs, percentage inputs.

**Testing**: Verify (1) typing multi-digit values doesn't jump cursor, (2) debounce fires after delay, (3) blur syncs immediately, (4) external updates respected when not focused. See `frontend/components/lyrics-review/__tests__/TimeInput.test.tsx` for comprehensive test suite.

### Mocks Must Match Real Return Values (Feb 2026)

**Problem**: PR #371 introduced a bug where admin reset always returned 500. The code checked `if not success:` after calling `update_job()`, but `update_job()` returns `None`, not a boolean. Since `not None` is `True`, the error was always raised.

**Why tests didn't catch it**: All 22 test mocks had:
```python
mock_jm.update_job.return_value = True  # WRONG - real method returns None
```

The mock returned `True`, so `if not True:` was `False` and tests passed. But in production, `if not None:` is `True`, causing 500 errors.

**Root cause**: Mocks were written based on an assumed API contract (boolean return) rather than the actual contract (void return, raises on error).

**Fix**:
1. Updated all mocks to `return_value = None` with comment explaining why
2. Added emulator integration test (`test_admin_reset_integration.py`) that uses real `JobManager`
3. Added explicit test `test_update_job_returns_none_not_boolean` to document the API contract

**Pattern for preventing this**:
1. **Mocks should match real signatures**: Check the actual method before setting `return_value`. Void methods should mock `return_value = None`.
2. **Add emulator tests for critical paths**: Integration tests with real services catch contract mismatches mocks hide.
3. **Document API contracts in tests**: Add explicit tests for non-obvious behaviors (`update_job` returns None, raises on failure).

**Code pattern**:
```python
# ❌ WRONG - assumes boolean return
mock_jm.update_job.return_value = True

# ✅ CORRECT - matches real API
mock_jm.update_job.return_value = None  # Real API: returns None, raises on error
```

### Emulator Tests Catch Real Bugs
Firestore emulator tests catch issues (like missing indexes) that unit tests with mocks miss.

### Playwright: Assertions Not Timeouts
Use `expect(locator).toBeVisible()` instead of `page.waitForTimeout()`. Assertions auto-retry; timeouts are flaky.

### Playwright: 'load' Not 'networkidle'
Use `waitUntil: 'load'` for audio-heavy pages. `'networkidle'` can timeout waiting for streaming audio.

### Always Test the Full Data Flow, Not Just Individual Components

**Problem (Jan 2026)**: Lyrics corrections made during human review were being silently discarded. Final videos rendered with raw transcription instead of user-corrected lyrics.

**Why tests didn't catch it**:
1. **Component tests were isolated**: LyricsAnalyzer tests verified corrections saved to server. InstrumentalSelector tests verified submission worked. But no test verified the *connection* between them.
2. **E2E tests used mocks**: Mock API responses always returned consistent data, hiding the fact that the real API returned different data than what was saved.
3. **Backend tests checked structure, not behavior**: The `test_routes_review.py` tests documented expected behavior but didn't actually test the endpoint logic.

**Root cause**:
- `POST /api/jobs/{job_id}/corrections` saved to `corrections_updated.json` ✓
- `GET /api/review/{job_id}/correction-data` returned `corrections.json` (original) ✗
- `POST /api/review/{job_id}/complete` received original data and overwrote the user's edits ✗

**The fix**: Make `get_correction_data` check for `corrections_updated.json` first (same pattern as `render_video_worker.py`).

**Prevention pattern**:
1. **Integration tests for data round-trips**: Test that data saved by component A is retrievable by component B
2. **Contract tests**: Verify endpoints return what other endpoints expect
3. **Real backend E2E tests**: At least one test per flow that hits the actual backend, not mocks
4. **When two workers use the same data, verify they use it consistently**: Both `get_correction_data` and `render_video_worker` needed to check `corrections_updated.json` first

**Test added**:
```python
def test_corrections_preserved_through_combined_review():
    # 1. Create job with original corrections
    # 2. Submit edited corrections (saves to corrections_updated.json)
    # 3. Fetch correction data (should return corrections_updated content!)
    # 4. Verify returned data contains the EDITED corrections
```

---

## Deployment Notes

### Cloud Run Timeouts
Set explicit `--timeout` for long operations. Default is 5 minutes. Max is 60 minutes for Cloud Run services.

### Cloud Tasks dispatch_deadline
Cloud Tasks has separate timeout from Cloud Run. Set `dispatch_deadline` on tasks for long-running operations.

### Two-Layer Timeout Pattern
For indefinite loops (like polling), use outer timeout (total duration) + inner timeout (per-iteration) with exponential backoff.

### Secret Manager Access
Service account needs `roles/secretmanager.secretAccessor`. Mount as env var or volume. Use GCP workload identity, not key files.

### Vertex AI Auth
Use `project` parameter with `ChatGoogleGenerativeAI` to trigger Vertex AI backend with ADC. Don't require `GOOGLE_API_KEY`.

### Gemini 3 Requires Global Location
Gemini 3 models require `location="global"`, not regional endpoints like `us-central1`.

### LangChain: REST over gRPC
Use `langchain-google-genai` (REST) instead of `langchain-google-vertexai` (gRPC) to avoid silent hangs.

### Pulumi CI: Skip Preview
Use `pulumi up --skip-preview --yes` when CI service account can't run preview. Preview requires broad read permissions.

### Pulumi: Always Apply Locally Before Merging PRs
**Never rely solely on CI for Pulumi deploys.** Run `pulumi up` locally first, then merge the PR (CI re-runs as a no-op). This prevents partial state corruption when CI runners are preempted or fail mid-apply. Recovering from a partially-applied Pulumi state requires `pulumi cancel` → `pulumi refresh` → `pulumi import` → `pulumi up`, which is far more painful than just applying locally first.

### Spot VMs and Long-Running CI Jobs Don't Mix
Spot/preemptible VMs can be terminated at any time. Docker builds (10+ min) and Pulumi deploys are especially vulnerable — the PR #464 Pulumi deploy was itself preempted mid-apply, leaving state partially applied (3 runners deleted but not recreated). Fix: use a dedicated on-demand runner (`github-build-runner`) for `deploy-backend` jobs via the `docker-build` label. Recovery from interrupted Pulumi: `pulumi cancel` → `pulumi refresh` → `pulumi up` (may need `pulumi import` for resources created but not yet in state).

### Startup Scripts Must Be Idempotent (Mar 2026)
**Spot/preemptible VMs re-run startup scripts on every restart.** The GitHub runner startup script used `set -e` and non-idempotent commands (e.g., `gpg --dearmor -o file` fails if file exists). When all 3 runners were preempted and restarted simultaneously, the startup script died at the Docker GPG key step, leaving zero runners registered with GitHub. All self-hosted CI jobs queued indefinitely.

**Fix**: Remove `set -e`, add existence checks for all software (Docker, Python, Node, Java, Poetry, gcloud), use `install_gpg_key()` helper that skips if file exists, and always re-run the runner registration section. Also pin runner version and check for upgrades.

**Also**: Keep runner version updated — GitHub enforces minimum versions (e.g., 2.329.0+). The startup script had 2.321.0 hardcoded, which was too old to register even after the idempotency fix.

### Docker Disk Management
Self-hosted runners need aggressive disk cleanup. Use threshold-based (70%) not age-based cleanup.

### Version Sort for Artifact Selection (SUPERSEDED)
**Note:** This lesson is superseded by the "Immutable Deployment Pattern" below, which eliminates version sorting entirely.

Original lesson: When downloading multiple versioned files (wheels, tarballs), use `sort -V | tail -1` not `ls -t | head -1`. Files downloaded simultaneously have the same timestamp, so time-based sorting picks arbitrarily.

### Immutable Deployment Pattern for GCE Workers
When deploying to long-running VMs (like the encoding worker), avoid baking deployment logic into Packer images:

**Problem:** The encoding worker had recurring version mismatch issues because:
1. `startup.sh` was baked into the Packer image
2. Fixing logic required image rebuild + VM recreation
3. CI only warned on version mismatch, didn't fail
4. Multiple deployment vectors (CI, Pulumi, manual SSH) caused drift

**Solution:** Self-updating startup script from GCS:
1. Packer image contains minimal `bootstrap.sh` (~15 lines, rarely changes)
2. `bootstrap.sh` downloads real `startup.sh` from GCS on every service start
3. CI uploads `startup.sh`, wheel, and version manifest to GCS
4. CI **strictly verifies** deployed version - fails on mismatch

**Benefits:**
- Logic changes via CI, no Packer rebuild needed
- Fixed wheel path (`karaoke_gen-current.whl`) eliminates sorting bugs
- Version verification is mandatory, not optional
- Single source of truth (GCS), no drift from manual changes

See `infrastructure/encoding-worker/README.md` for implementation details.

---

## Performance Patterns

### Reuse LLM Model Instances
Create model instance ONCE before loops. Each `AgenticCorrector.from_model()` has 2s+ overhead.

### Thread-Safe Lazy Init
Use double-checked locking (`if not X: with lock: if not X: init()`) for shared resources in parallel code.

### Preload at Startup
Load SpaCy models, NLTK data, Langfuse handlers at container startup, not lazily during requests. Saves 60-200s on cold starts.

### Langfuse v3 OTEL Isolation
Langfuse v3 is built on OpenTelemetry. If `CallbackHandler()` is created without a pre-configured `Langfuse` client, it installs itself as the **global** OTEL tracer provider, capturing ALL spans (FastAPI requests, HTTP clients, etc.) - not just LLM calls. Fix: Initialize `Langfuse(tracer_provider=TracerProvider())` with an isolated provider BEFORE creating `CallbackHandler()`. See: https://github.com/orgs/langfuse/discussions/9136

### Cold Start Mitigation
Set `min-instances > 0` for Cloud Run services with heavy initialization.

### Never Call sync-over-async from FastAPI async Routes (Feb 2026)
Sync methods that internally create new event loops (`asyncio.new_event_loop()` + `loop.run_until_complete()`) will **block the FastAPI event loop** when called from `async def` routes or background tasks. The response body gets queued but never flushed because the blocked loop can't process I/O — causing 20-36s endpoint latency instead of <1s. **Fix**: Either (a) call the underlying async client directly with `await` (preferred — e.g., `await flacfetch_client.download_by_id()`), or (b) wrap sync calls in `await asyncio.to_thread()` / `loop.run_in_executor()` to run them in a thread pool. The `nest_asyncio` library is NOT a reliable workaround — it patches CPython internals and can cause deadlocks. See `audio_search.py` and `youtube_download_service.py` for the correct async patterns.

---

## Data & Storage

### Firestore 1MB Limit
Use subcollections for unbounded data (logs, events). Embedded arrays hit 1MB limit. When saving large objects to Firestore `state_data`, strip fields not needed for Firestore queries (e.g. `reference_lyrics`) and store full data in GCS instead. The Genius API occasionally returns non-lyrics content (screenplays, articles) that can bloat documents past 1MB.

### Firestore Timezone-Aware
Firestore returns timezone-aware datetimes. Compare with `datetime.now(timezone.utc)`, not naive `datetime.now()`.

### GCS Blob Paths vs URIs
Methods that accept paths typically want `bucket/path`, not `gs://bucket/path`.

### Library Caches Need GCS Sync
External library caches (like LyricsTranscriber) need explicit sync to GCS for persistence across Cloud Run instances.

### Cloud Run Horizontal Scaling
In-memory caches aren't shared across instances. Use GCS, Firestore, or Redis for shared state.

### Firestore Cache Invalidation
Firestore caches don't auto-invalidate. When upstream data changes (e.g., flacfetch update), provide admin UI to clear caches.

---

## Worker Patterns

### Sequential vs Parallel Workers
If worker B needs output from worker A, trigger sequentially. Use parallel only when inputs are independent.

### Extract Auth Into Jobs
Auth provides identity, not just access control. Store `user_email` on job for ownership association.

### Alternative Paths Need All Features
When adding alternative implementations (GCE vs local encoding), audit ALL side effects. Use orchestrator pattern to ensure all stages run.

### External Response Validation
Add defensive type checking for all external service responses. Lists vs dicts, missing fields cause cryptic failures.

### Brand Code Allocation Must Be Atomic (Feb 2026)
Two concurrent jobs both got brand code `NOMADNP-0012` because `get_next_brand_code()` scanned Dropbox folders to find the next number — a classic TOCTOU race condition. Jobs processed 0.86s apart both saw the same state. **Fix**: `brand_code_service.py` uses Firestore transactions to atomically allocate codes. Counter doc per prefix (`NOMAD`, `NOMADNP`) with `next_number` and `recycled` pool. E2E cleanup recycles numbers via `recycle_brand_code()`.

---

## Frontend Patterns

### Module-Level State in Next.js
Don't use module-level variables for state. They persist between navigations. Use React state or context.

### Zustand Module-Level Init
Zustand stores initialize once at module load. `setTimeout(fetch, 0)` runs on first import, not each navigation.

### Hydration-Safe Init
In Next.js with SSR, never auto-initialize async state at module load. Use `useEffect` after hydration.

### Feature Flags Sync Tab State
When feature flags hide tabs, sync `activeTab` state to remain valid: `useEffect(() => { if (!available.includes(active)) setActive(available[0]) }, [])`.

---

## Multitenancy

### Config-Driven Feature Flags
Store tenant config in GCS (`tenants/{id}/config.json`). Feature flags, branding, defaults in config, not code.

### Tenant Detection
Use subdomain detection in middleware. Disable query param detection in production to prevent spoofing.

### Middleware Mocking
When mocking tenant middleware, patch both the middleware import path AND the service factory function.

### Resilient Remote Worker Interactions (Feb 2026)

When a Cloud Run poller calls a stateful GCE worker, deployments can kill the poller mid-encoding. The worker completes but nobody receives the result — the job gets permanently stuck. Worse, re-submitting gets a 409 because the worker's in-memory dict still has the job.

**Three-part fix pattern:**
1. **Make worker endpoints idempotent** — return cached results for completed jobs, allow retry on failure, return status for in-progress jobs (instead of hard 409 reject)
2. **Handle all response types on the caller side** — `cached` → return immediately, `in_progress` → join the existing poll, `accepted` → normal flow. On 409 fallback, check job status before giving up.
3. **Add stuck-job detection** — flag jobs in `encoding` status >50 min without `updated_at` advancing. This catches the case where the poller dies entirely.

This generalizes the preview encoding pattern (`/encode-preview` was already idempotent; `/encode` was not).

### Deployment-Safe Encoding: Graceful Drain + Extended Retries (Mar 2026)

Even with idempotent endpoints (PR #413) and basic retry logic (PR #242), jobs can still fail during deployments. The original retry config (3 retries, ~14s total) was insufficient — a worker restart (download wheel, install, start uvicorn) takes 30-90 seconds. Job `25173cb3` failed on 2026-03-04 because it hit the encoding stage right as CI restarted the worker.

**Two-layer defense:**

1. **CI-side graceful drain** — Before restarting the encoding worker, CI polls `/health` for `active_jobs`. If jobs are running, it waits up to 10 minutes for them to finish. This is the primary protection — most deployments will simply wait for active work to complete before restarting.

2. **Client-side extended retries** — Increased from 3 retries / ~14s to 7 retries / ~90s with 5s initial backoff (capped at 15s). This is the safety net — if a job arrives during the brief restart window, it retries long enough for the worker to come back up.

3. **Poll failure tolerance** — Status polling now tolerates up to 5 consecutive failures (same pattern as flacfetch PR #446) instead of immediately failing the job on a single network blip. The worker restart takes the health endpoint offline temporarily, which was killing the poller even though the encoding was still running.

**Key insight:** Defense in depth matters. The CI drain prevents *most* conflicts, extended retries handle the *remaining edge cases*, and poll tolerance protects *jobs already in progress*. No single layer is sufficient alone.

### Deployment-Safe Encoding: Cloud Run Jobs (Mar 2026)

The three-layer defense above protects against the **GCE encoding worker** being restarted, but not against the **Cloud Run orchestrator** being killed. Job `43c0d519` (2026-03-08) got stuck because a Cloud Run Service deployment rolled out a new revision while the video worker was running as a `BackgroundTask`. Since the HTTP response had already been sent, Cloud Run treated the request as complete and killed the old instance — terminating the encoding orchestration mid-flight.

**Root cause:** `BackgroundTask` work runs after the HTTP response, so Cloud Run can kill it during deployment drain. The three-layer defense only protects the GCE worker connection, not the orchestrator's lifecycle.

**Fix:** Re-enabled Cloud Run Jobs for video encoding (`USE_CLOUD_RUN_JOBS_FOR_VIDEO=true`). This was built in PR #155 (Dec 2025) but the env var was dropped during the CI migration from `cloudbuild.yaml` to GitHub Actions. Cloud Run Jobs run to completion (up to 24h), immune to Service deployment rollouts.

**Key insight:** When migrating CI systems (cloudbuild.yaml → GitHub Actions), audit env vars carefully. A missing feature flag can silently disable critical protections. The infrastructure (Pulumi) and code existed but were inert without the env var.

---

## What We'd Do Differently

1. **Add Pydantic model field tests first** - Catches silent field issues immediately
2. **Use emulator tests from day one** - Faster feedback than deploying
3. **Design for async human review upfront** - Avoided rearchitecting later
4. **Keep docs minimal and current** - Less documentation, always accurate
5. **Check gitignore early for new directories** - Especially frontend `lib/` dirs

---

## Incident Insights

### NOMAD-1276: Validator Timing Race Condition (2026-03-02)

**Problem:** Post-job GDrive validator fired immediately after upload, before E2E test cleanup could delete test files. Validator saw all 1,276 files (including test files), reported "no issues," then cleanup deleted the test files, leaving NOMAD-1276 as a gap.

**Key lessons:**
1. **Fire-and-forget validation is risky** — When a validator runs alongside cleanup, the order matters. A 5-minute Cloud Tasks delay is a simple, reliable fix.
2. **Multi-step UI state can silently lose data** — The guided flow's `is_private` checkbox was in Step 3, but the job was created in Step 2 with the default value. Fixed by decoupling search from job creation: search now returns a session ID, and the job is created only at Step 3 confirm with all final values. See `docs/archive/2026-03-02-decouple-search-from-job-creation.md`.
3. **Cloud Run service naming matters for debugging** — The service is `karaoke-backend`, not `karaoke-gen-api`. Wrong name returns empty logs, wasting investigation time.
4. **Cross-folder gap detection needs global context** — Per-folder max misses trailing gaps when folders have different highest numbers. Use the global max across all folders as the upper bound.

### Verifying Request Bodies via Cloud Trace Request Size (2026-03-07)

**Problem:** A job was created with `is_private=true` but the user believed they selected "Published." No request body logging existed to prove what the frontend actually sent.

**Solution:** Cloud Trace captures `/http/request/size` on every span. By computing the exact byte length of the JSON body with `is_private: true` vs `is_private: false` (compact serialization, no spaces), the sizes matched exactly — 143 bytes for `true`, confirming the frontend sent `is_private: true`.

**Prevention:** Added two observability mechanisms:
1. **`creation_params`** — A dict stored on the job Firestore document capturing the user's original choices (`is_private`, `is_admin`, `created_from`). Persists forever, no log retention concerns.
2. **OTel span attributes** — `job.is_private`, `job.source`, `job_id` added to all 4 job creation endpoint spans. Searchable in Cloud Trace for 30 days.

**Key insight:** When investigating "what did the client send?", Cloud Trace request size can serve as a forensic tool even without request body logging. Compact JSON serialization (`JSON.stringify` with no spaces) is deterministic, so byte-level size matching is reliable.

### Timeline Metadata for Job Traceability (2026-03-07)

**Problem:** After shipping the "edit completed tracks" feature, tracing what happened to edited jobs was very difficult. Timeline events only had `status`, `timestamp`, `progress`, `message` — no output details. Cleanup operations (YouTube deletion, Dropbox removal) weren't logged to Firestore. Previous outputs were overwritten in `state_data` with no history. API endpoints (edit, admin-delete) logged to Cloud Run stdout but not to the job's Firestore log subcollection.

**Solution:**
1. **`TimelineEvent.metadata`** — Optional `Dict[str, Any]` field on timeline events. Completion events now capture `brand_code`, `youtube_url`, `dropbox_link`, `gdrive_file_ids`, `duration_seconds`. Edit/delete events capture `previous_outputs` and `cleanup_results`.
2. **`log_to_job()` helper** — Module-level function in `firestore_service.py` that writes to the job's Firestore log subcollection. Wraps errors silently so logging never breaks the caller. Used by API endpoints that previously only logged to stdout.
3. **Plumbed `timeline_metadata`** through `job_manager.transition_to_state()` → `firestore_service.update_job_status()`.

**Key insight:** The job's Firestore document (timeline + log subcollection) should be the single source of truth for "what happened to this job." Cloud Logging has retention limits and requires knowing which service to search. Timeline metadata makes the job self-documenting — any agent or human can read the timeline and understand the full lifecycle without cross-referencing logs.

### Store Processing Metadata When You Have It (Mar 2026)

**Problem:** During a data export for AudioShake lyrics correction feedback, we discovered that AudioShake `task_id` and `asset_id` were only held in memory during processing and never persisted. Recovery required paginating the AudioShake API and cross-referencing Cloud Logging — a multi-hour effort for data that was trivially available during processing.

**Solution:** Added `processing_metadata` as a top-level dict on the Job model, separate from `state_data` (mutable workflow state). Each worker writes its own section (transcription, separation, rendering, etc.) via `job_manager.update_processing_metadata(job_id, section, data)`, which uses Firestore dot-notation to atomically update one section without overwriting others. All metadata writes are wrapped in try/except — failures are logged but never fail jobs.

**Key insight:** "Store it when you have it." The cost of persisting a few extra fields is negligible; the cost of not having them later (log spelunking, API pagination, one-off recovery scripts) is enormous. When data flows through a worker during processing, capture the provenance metadata before it's lost. See `docs/archive/2026-03-10-store-job-processing-metadata.md` for the full design.

