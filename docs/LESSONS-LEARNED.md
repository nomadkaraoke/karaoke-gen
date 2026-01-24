# Lessons Learned

Key insights for future AI agents working on this codebase.

> **Full archive**: For detailed code examples and extended explanations, see [archive/2026-01-09-lessons-learned-archive.md](archive/2026-01-09-lessons-learned-archive.md)

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

---

## Common Gotchas

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

### Fix Both Sides of Dual Code Paths
When fixing a bug in a system with multiple code paths (e.g., legacy vs orchestrator, local vs cloud), verify ALL paths are fixed. PR #271 fixed the GCE worker to READ `instrumental_selection` but only checked the legacy path which was already SENDING it. The orchestrator path (production default) wasn't sending it. **Pattern**: If a component receives config from multiple callers, check ALL callers when fixing the receiving side. Write integration tests that cover each path.

**Example 1:** The `gcs_path` parameter bug for remote flacfetch downloads was fixed for RED/OPS torrent sources in December 2025, but the same bug existed for YouTube sources. The fix only addressed one branch of the conditional, leaving YouTube downloads broken when remote was enabled. Always search for ALL code paths that might need the same fix.

**Example 2:** The countdown audio sync fix (PR #328) only fixed the writer side (`render_video_worker` updating `lyrics_metadata.has_countdown_padding`). The orchestrator path that READS that state and pads the instrumental wasn't updated. PR #338 added the reader-side fix: `OrchestratorConfig` now has `countdown_padding_seconds`, `create_orchestrator_config_from_job` reads from lyrics_metadata, and GCE worker pads instrumental audio. **Lesson**: When adding cross-worker state, trace the data flow end-to-end through ALL code paths (legacy KaraokeFinalise path AND orchestrator path).

### Worker Idempotency Must Complete the Lifecycle
When implementing idempotency checks that set `stage='running'` at start, workers MUST also set `stage='complete'` on success. Without the completion update, retries or reprocessing attempts will be blocked because the stage is permanently stuck at `'running'`. The fix in v0.108.14 added completion markers to render_video, video, and screens workers.

### Clear Worker Progress Keys When Reprocessing
When resetting a job or re-reviewing a completed job, all worker progress keys (`*_progress`) must be cleared from `state_data`. Workers check `state_data.{worker}_progress.stage == 'complete'` for idempotency - if stale keys exist from a previous run, workers will skip execution even though the job needs reprocessing. **Pattern**: Any operation that intends to re-run workers (admin reset, review resubmission) must explicitly clear progress keys using `job_manager.delete_state_data_keys()`. See `backend/api/routes/review.py:complete_review()` and `backend/api/routes/admin.py:clear_worker_state()`.

### Defense in Depth
Enforce critical requirements at multiple layers (e.g., reject at creation in JobManager + safety net at processing time).

### Retry Transient Failures
HTTP calls to services that can restart (VMs, containers) need retry logic with exponential backoff (2s → 4s → 8s) for connection errors.

### Cross-Domain localStorage
Auth tokens in localStorage are domain-isolated. Keep auth on a single domain or use cookies with `domain=.example.com`.

### Standalone HTML Pages Need Auth Fallback
Standalone HTML pages (like instrumental review) that use magic link tokens should also check localStorage for user auth tokens. Priority: full auth token (doesn't expire) > magic link token (expires). This prevents logged-in users from being blocked when their magic link expires.

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

### FFmpeg Path Escaping
For subprocess without shell, FFmpeg filter paths need: apostrophes escaped as `'\\''`, special chars as `\\[char]`.

### Unicode in HTTP Headers
Sanitize user input (artist/title) to ASCII for HTTP headers. Smart quotes, em dashes from copy/paste cause encoding errors.

### Fonts in Docker
Base Docker images have no fonts. Install `fonts-noto-core`, `fonts-noto-cjk` for video rendering.

### Validate External API Format Support
Don't assume external APIs support all common formats. AudioShake only supports `.wav`, `.mp3`, `.aac`, `.flac`, `.aiff`, `.mp4`, `.mov` - NOT `.webm`, `.ogg`, `.m4a`, `.opus`. When remote flacfetch downloads YouTube audio as `.webm`, the lyrics worker must convert to FLAC before uploading. **Pattern**: Use a whitelist of known-supported formats and convert everything else, rather than trying to upload directly and hoping it works. Always check the API's supported format documentation.

### Cloud Run CPU Throttling Kills Background Tasks
Cloud Run throttles CPU to near-zero when the main request handler returns, even if background tasks are running. This caused lyrics processing (running as a FastAPI background task) to slow from 17-52 seconds to 8+ minutes, and instances being terminated mid-processing. **Fix**: Add `--no-cpu-throttling` flag to `gcloud run deploy`. Keep `--cpu-boost` for faster cold starts. **Diagnosis**: Look for "Application shutdown" in logs during long operations, and compare processing times (27x slowdown is a telltale sign).

### Cloud Run Premature Shutdown with Parallel Workers
When multiple workers run in parallel via BackgroundTasks, Cloud Run can shut down when one worker completes, killing others mid-processing. This happens because each worker endpoint returns quickly after spawning its BackgroundTask - when the audio worker's task completes, Cloud Run sees the container as idle. **Fix**: Implement a `WorkerRegistry` that tracks active workers per job. Register at worker start, unregister in finally block. Add shutdown handler in FastAPI lifespan that calls `worker_registry.wait_for_completion(timeout=600)` before allowing shutdown. **Pattern**: `backend/workers/registry.py` provides the global registry; workers import and call `await worker_registry.register(job_id, "audio")` / `unregister()`.

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

## Testing Insights

### Test Webhook Handlers with Unit Tests
Webhook handlers contain critical business logic. Test exact parameters passed to service methods, not just that "something happened."

### Test DTO-to-Entity Mapping
Testing webhook creates correct DTO isn't enough. Also test that manager/service copies all fields to entity.

### E2E Mocks Must Match Types
Mock responses must match TypeScript interfaces exactly. Silent failures from type mismatches are hard to debug.

### Use data-testid for E2E
Prefer `data-testid` over label/text selectors. They're immune to label changes and won't break when similar fields are added.

### Emulator Tests Catch Real Bugs
Firestore emulator tests catch issues (like missing indexes) that unit tests with mocks miss.

### Playwright: Assertions Not Timeouts
Use `expect(locator).toBeVisible()` instead of `page.waitForTimeout()`. Assertions auto-retry; timeouts are flaky.

### Playwright: 'load' Not 'networkidle'
Use `waitUntil: 'load'` for audio-heavy pages. `'networkidle'` can timeout waiting for streaming audio.

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

---

## Data & Storage

### Firestore 1MB Limit
Use subcollections for unbounded data (logs, events). Embedded arrays hit 1MB limit.

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

---

## What We'd Do Differently

1. **Add Pydantic model field tests first** - Catches silent field issues immediately
2. **Use emulator tests from day one** - Faster feedback than deploying
3. **Design for async human review upfront** - Avoided rearchitecting later
4. **Keep docs minimal and current** - Less documentation, always accurate
5. **Check gitignore early for new directories** - Especially frontend `lib/` dirs

