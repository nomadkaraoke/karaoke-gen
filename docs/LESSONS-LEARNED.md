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

### Verify Active Worktree
Before making changes in a multi-worktree setup, verify which directory the user's dev server is running from.

### Theme-Aware Styling
Search for ALL non-theme-aware color patterns (`text-slate-*`, `bg-gray-*`) and fix in one pass. Use CSS variables like `text-muted-foreground`, `bg-card`, `border-border`.

### Backend Must Apply Defaults
When UI removes options, backend must apply sensible defaults. Don't rely on each endpoint to remember - centralize in core data layer.

### Centralize Job Creation Logic
When multiple code paths create jobs (file upload, audio search, webhooks), use a shared service for default resolution. The made-for-you webhook handler diverged from regular job creation, missing CDG/TXT defaults because it didn't call the shared `resolve_cdg_txt_defaults()` function. Fix: Create `job_defaults_service.py` with centralized helpers used by ALL job creation paths.

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

### Google Drive Query Escaping
Escape special chars in Google Drive API queries: `'` → `\'`, `\` → `\\\\`.

### FFmpeg Path Escaping
For subprocess without shell, FFmpeg filter paths need: apostrophes escaped as `'\\''`, special chars as `\\[char]`.

### Unicode in HTTP Headers
Sanitize user input (artist/title) to ASCII for HTTP headers. Smart quotes, em dashes from copy/paste cause encoding errors.

### Fonts in Docker
Base Docker images have no fonts. Install `fonts-noto-core`, `fonts-noto-cjk` for video rendering.

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

### Version Sort for Artifact Selection
When downloading multiple versioned files (wheels, tarballs), use `sort -V | tail -1` not `ls -t | head -1`. Files downloaded simultaneously have the same timestamp, so time-based sorting picks arbitrarily. Version sorting (`sort -V`) correctly identifies the highest semantic version.

---

## Performance Patterns

### Reuse LLM Model Instances
Create model instance ONCE before loops. Each `AgenticCorrector.from_model()` has 2s+ overhead.

### Thread-Safe Lazy Init
Use double-checked locking (`if not X: with lock: if not X: init()`) for shared resources in parallel code.

### Preload at Startup
Load SpaCy models, NLTK data, Langfuse handlers at container startup, not lazily during requests. Saves 60-200s on cold starts.

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
