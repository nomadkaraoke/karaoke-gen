# Lessons Learned

Key insights for future AI agents working on this codebase.

## Architecture Decisions

### Separate Collections for Multi-App Projects

**Problem**: karaoke-gen and karaoke-decide shared the same `users` Firestore collection with incompatible schemas. karaoke-gen users have `credits`, `role`, `is_active`; karaoke-decide users have `user_id`, `is_guest`, `quiz_*` fields.

**Symptoms**:
- Admin dashboard showed users from both apps mixed together
- Potential for data corruption if wrong app writes to wrong user
- Hard to run collection-wide queries (indexes, aggregations) for one app

**Solution**: Migrate karaoke-gen to dedicated `gen_users` collection. Created migration script that:
1. Identified karaoke-gen users by schema (has `credits` + `role` + `is_active`, lacks `user_id` + `is_guest`)
2. Copied 42 users to `gen_users` with same document IDs (email addresses)
3. Updated Pulumi indexes from `users` to `gen_users`
4. Left karaoke-decide's `users` collection untouched

**Key insight**: When multiple apps share a GCP project, use distinct collection names from day one. Prefixing collections with app name (e.g., `gen_users`, `decide_users`) avoids schema conflicts and makes ownership clear.

**Migration script**: `scripts/migrate_users_to_gen_users.py` - supports `--dry-run` and `--delete` flags.

### LyricsTranscriber: Library, Not Server

**Problem**: LyricsTranscriber's `ReviewServer` blocks waiting for human input - incompatible with async cloud architecture.

**Solution**: Use LyricsTranscriber as a library:
- Call `transcribe()` and `correct()` for processing
- Save `corrections.json` to GCS
- Skip `ReviewServer` entirely
- Call `OutputGenerator` only after human review completes

**What to use**: `LyricsTranscriber`, `CorrectionResult`, `OutputGenerator`, `OutputConfig`, `CorrectionOperations`

**What NOT to use**: `ReviewServer`, `server.start()`, blocking waits

### Video Generation Timing

**Wrong**: Generate video during lyrics worker, then review
**Right**: Lyrics worker saves JSON → Human reviews → Render worker generates video

Video must include human-corrected lyrics, so generation happens AFTER review.

### Parallel Processing

Audio separation and lyrics transcription run in parallel:
- Both can fail independently
- Progress tracked separately in `state_data`
- Screens generation waits for both to complete

### Cloud Run Performance: Cold Starts and FFmpeg

**Problem**: Cloud karaoke generation was ~3x slower than local CLI (15 min vs 5 min for a typical song).

**Root causes identified**:

1. **Cold start penalty**: Cloud Run with `min-instances: 0` caused 77-second cold starts for FFmpeg-heavy operations (title screen generation). Warm instances take only 6 seconds.

2. **FFmpeg version**: Debian's package (5.1.8) is outdated and built with security-focused `--toolchain=hardened` which reduces performance. Static builds (7.0.2) from johnvansickle.com are ~20-40% faster.

3. **Generic x86_64 build**: Debian packages use generic CPU instructions, not optimized for Cloud Run's Cascade Lake/Ice Lake processors.

**Solutions applied**:
- Set `min-instances: 4` to keep workers warm (eliminates cold start entirely)
- Use John Van Sickle's static FFmpeg 7.0.2 instead of Debian package
- Added `/api/health/detailed` endpoint to expose FFmpeg version for debugging

**Key insight**: For CPU-bound encoding workloads on Cloud Run:
- GPU acceleration often doesn't help (filters like libass aren't GPU-accelerated)
- Newer FFmpeg versions have significant encoding optimizations
- Static builds with CPU-specific tuning outperform generic distribution packages
- Cold starts hurt more than expected for initialization-heavy binaries like FFmpeg

**Debugging**: Use `scripts/compare_local_vs_remote.py` to benchmark local vs cloud performance.

## Common Gotchas

### Cross-Domain localStorage Isolation

**Problem**: Auth tokens stored in localStorage on one subdomain are invisible to other subdomains.

We had `buy.nomadkaraoke.com` for purchases and `gen.nomadkaraoke.com` for the app. Users who purchased credits on `buy.` appeared logged out when redirected to `gen.` because localStorage is domain-isolated.

**Symptoms**:
- User completes purchase, redirected to app, sees "auth required"
- Token exists in browser dev tools for one domain but not the other
- Works fine in local dev (same localhost origin)

**Solution**: Keep auth on a single domain. We consolidated `buy-site/` into the main `frontend/`:
- Landing/pricing at `/` (root)
- Main app at `/app`
- Single localStorage for all auth tokens

**Alternative** (if subdomains required): Use cookies with `domain=.nomadkaraoke.com` instead of localStorage, or implement a token exchange flow.

**Lesson**: Before splitting functionality across subdomains, verify your auth storage strategy works cross-domain. E2E tests that exercise the full user journey catch this; unit tests don't.

See `docs/archive/2025-12-29-buy-site-consolidation.md` for full details.

### Frontend Polling vs Tab Visibility

**Problem**: After completing review in a new tab (lyrics or instrumental selection), returning to the main frontend showed stale status for up to 10 seconds due to polling interval.

**Solution**: Use `visibilitychange` event to trigger immediate refresh when tab becomes visible:

```typescript
useEffect(() => {
  const handleVisibilityChange = () => {
    if (document.visibilityState === 'visible') {
      loadJobs()  // Immediate refresh
    }
  }
  document.addEventListener('visibilitychange', handleVisibilityChange)
  return () => document.removeEventListener('visibilitychange', handleVisibilityChange)
}, [loadJobs])
```

**Key insight**: Backend status updates are fast (~1 second). If UI appears slow to update after user action in another tab, the issue is likely frontend polling, not backend.

### Pydantic Model Fields

**Problem**: Setting a field that doesn't exist in the Pydantic model silently does nothing.

```python
# BAD - fails silently if field doesn't exist
job.input_media_gcs_path = "path/to/file"

# GOOD - explicit field in model
class Job(BaseModel):
    input_media_gcs_path: Optional[str] = None
```

**Lesson**: Always verify Pydantic model has the field before setting it.

### Styles Configuration

ASS subtitle generator requires ALL style fields present and properly typed:

```python
required_fields = [
    "font", "font_path", "ass_name",  # ass_name often missed
    "primary_color", "secondary_color", "outline_color", "back_color",
    "bold", "italic", "underline", "strike_out",
    "scale_x", "scale_y", "spacing", "angle",
    "border_style", "outline", "shadow",
    "margin_l", "margin_r", "margin_v", "encoding"
]

# Critical: font_path must be string, not None
"font_path": ""  # Correct
"font_path": None  # Causes ASS writer to fail
```

### Frontend Data Format

The React review UI sends partial correction data:

```python
# Frontend sends:
{"corrections": [...], "corrected_segments": [...]}

# Backend must merge with original corrections.json
```

### Firestore Consistency

**Problem**: Update job, trigger worker immediately, worker reads stale data.

**Solution**: Verify updates propagated before triggering workers:
```python
# Update job
await job_manager.update_job(job_id, updates)

# Verify update visible
job = await job_manager.get_job(job_id)
if job.input_media_gcs_path:
    await trigger_worker()
```

### Stale Object References After Async Operations

**Problem**: Fetching a job object, then calling async functions that update the job in the database, then using the original object's data to make another update - this overwrites the async function's changes.

```python
# BAD - loses distribution results
job = job_manager.get_job(job_id)  # Fetch job
await _handle_native_distribution(...)  # Updates job.state_data in DB
job_manager.update_job(job_id, {
    'state_data': {**job.state_data, 'foo': 'bar'}  # STALE! Overwrites dropbox_link!
})
```

**Solution**: Either re-fetch the job, or include async results explicitly:
```python
# GOOD - include results from async operation
job_manager.update_job(job_id, {
    'state_data': {
        **job.state_data,
        'dropbox_link': result.get('dropbox_link'),  # From distribution
        'gdrive_files': result.get('gdrive_files'),
    }
})
```

### Google Drive API Query Escaping

**Problem**: Single quotes in filenames break Google Drive API queries.

```python
# BAD - fails for "Nobody's Home"
query = f"name='{filename}' and '{parent_id}' in parents"
# Results in: name='NOMAD-1187 - Artist - Nobody's Home.mp4' → 400 Invalid Value
```

**Solution**: Escape single quotes with backslash:
```python
# GOOD
escaped = filename.replace("'", "\\'")
query = f"name='{escaped}' and '{parent_id}' in parents"
```

### FFmpeg Filter Path Escaping (subprocess without shell)

**Problem**: Song titles with apostrophes (e.g., "I'm With You") cause FFmpeg's ASS subtitle filter to fail because paths aren't escaped correctly.

```python
# BAD - FFmpeg strips the apostrophe, looks for wrong path
ass_filter = f"ass=./Avril Lavigne - I'm With You/cache/temp.ass"
# FFmpeg error: Could not create a libass track when reading file './Avril Lavigne - Im With You/cache/temp.ass'
```

**Wrong approach #1**: Simple backslash escaping like `\'` doesn't work because FFmpeg's filter parser requires more escaping.

**Wrong approach #2**: Shell-style escaping `'\''` (end quote, escaped quote, start quote) only works when passing through a shell. When using Python `subprocess` with a command list, there's no shell - arguments go directly to FFmpeg, so shell escaping patterns don't work.

**Solution**: Use FFmpeg filter escaping (for subprocess without shell):
```python
def _escape_ffmpeg_filter_path(path: str) -> str:
    """Escape a path for FFmpeg filter expressions (for subprocess without shell).

    FFmpeg filter escaping rules:
    - Backslashes: double them (\ -> \\)
    - Single quotes/apostrophes: escape with three backslashes (' -> \\\')
    - Spaces: escape with backslash ( -> \ )
    """
    # First escape existing backslashes (\ -> \\)
    escaped = path.replace("\\", "\\\\")
    # Escape single quotes (' -> \\\')
    # In Python source: 6 backslashes + apostrophe = 3 backslashes + apostrophe in string
    escaped = escaped.replace("'", "\\\\\\'")
    # Escape spaces
    escaped = escaped.replace(" ", "\\ ")
    return escaped

# Result: "I'm With You" becomes "I\\\'m\ With\ You"
# FFmpeg correctly interprets this as: I'm With You
```

**Symptoms**:
- Video generation fails with "Could not create a libass track"
- Error path is subtly different from the path in your code (missing apostrophe)
- Works fine for songs without special characters

**Key insight**: FFmpeg filter escaping is different from shell escaping. When using `subprocess.run(cmd_list)` (no `shell=True`), there's no shell to interpret the arguments. FFmpeg receives the strings directly and applies its own filter expression parsing rules. The `'\''` shell idiom does NOT work in this context.

**Lesson**: When debugging FFmpeg filter path issues, check whether you're using shell or subprocess. For subprocess without shell, escape spaces with `\ ` and apostrophes with `\\\'` (3 backslashes + quote in the actual string).

### Duplicate Code Leads to Inconsistent Behavior

**Problem**: Multiple endpoints computing the same defaults independently. `audio_search.py` had `effective_brand_prefix = body.brand_prefix or settings.default_brand_prefix` but `file_upload.py` was missing it for some endpoints.

**Result**: Jobs created via audio search had correct brand codes; jobs created via file upload had null brand_prefix, causing Dropbox uploads to fail and Google Drive uploads to use placeholder "TRACK-0000".

**Solution**: Added `_get_effective_distribution_settings()` helper function in `file_upload.py` to centralize default computation:
```python
@dataclass
class EffectiveDistributionSettings:
    dropbox_path: Optional[str]
    gdrive_folder_id: Optional[str]
    discord_webhook_url: Optional[str]
    brand_prefix: Optional[str]

def _get_effective_distribution_settings(...) -> EffectiveDistributionSettings:
    settings = get_settings()
    return EffectiveDistributionSettings(
        dropbox_path=dropbox_path or settings.default_dropbox_path,
        # ... etc
    )
```

**Lesson**: When you see the same logic repeated in multiple places, refactor into a helper function immediately. Otherwise bugs will be fixed in one place but not others.

### Python `or` Operator Can't Override True Defaults with False

**Problem**: Using Python's `or` operator for default logic prevents explicit `false` from overriding a `true` server default.

```python
# BAD - can't explicitly disable if server default is true
effective_value = body.enable_feature or settings.default_enable_feature
# When body.enable_feature=False and settings.default=True:
# False or True = True  (ignores explicit False!)
```

**Symptoms**:
- Feature can be enabled but never disabled via API
- Works fine when server default is `false` but breaks when default is `true`
- No errors - just ignores the explicit `false` value

**Solution**: Use `Optional[bool] = None` with explicit `is not None` check:

```python
# Request model
enable_feature: Optional[bool] = None  # None = use server default

# Default logic
effective_value = (
    body.enable_feature
    if body.enable_feature is not None
    else settings.default_enable_feature
)
```

This allows three states:
- `None` (not specified) → use server default
- `True` → explicitly enable
- `False` → explicitly disable (overrides server default)

**Lesson**: When a boolean field needs to support "use server default" behavior, use `Optional[bool] = None` instead of `bool = False`. The `or` operator conflates "not specified" with "explicitly false".

### Field Name Mismatches Between Endpoints

**Problem**: Different endpoints setting different field names for the same logical value, where consumers check only one field name.

Example: `youtube_description` vs `youtube_description_template`
- `audio_search.py` endpoint set `youtube_description_template`
- `file_upload.py` endpoint set only `youtube_description`
- `video_worker.py` checked `youtube_description_template`
- Result: YouTube uploads silently failed for remote CLI jobs

**Symptoms**:
- Feature works via one code path but silently fails via another
- No errors in logs (the field is just empty/None)
- Difficult to debug because both paths appear correct in isolation

**Solution**: When adding fields that control behavior:
1. Grep for all places that read the field
2. Grep for all places that write the field
3. Ensure all writers set what all readers expect

**Lesson**: Silent failures from field name mismatches are hard to catch. When a feature works in one flow but not another, check if different endpoints are setting different field names.

See `docs/archive/2025-12-30-cloud-output-structure-fix.md` for full details.

## Testing Insights

### E2E Mock Responses Must Match API Types Exactly

**Problem**: E2E tests with mocked API responses were failing silently because mock response format didn't match the TypeScript type the app expected.

The test mocked `/api/users/me` returning:
```javascript
{ body: { email: 'test@example.com', credits: 5 } }
```

But `UserProfileResponse` type expects:
```typescript
interface UserProfileResponse {
  user: UserPublic;  // { email, credits, role }
  has_session: boolean;
}
```

**Symptoms**:
- Tests passed initial auth check but `/api/jobs` was never called
- Mock server showed only 2 of 4 endpoints being called
- App redirected to `/` instead of loading job list
- No error messages (silent failure)

**Root cause**: The auth module's `fetchUser()` ran at page load and tried to access `response.user.email`. With the wrong mock format, this threw an error, the catch block called `clearAccessToken()`, and the app page saw no token and redirected.

**Solution**: Mock must return the exact structure the TypeScript type expects:
```javascript
{
  body: {
    user: { email: 'test@example.com', credits: 5, role: 'user' },
    has_session: true,
  },
}
```

**Lesson**: When writing E2E test mocks, check the TypeScript interface/type definition for the API response, not just what fields the UI displays. Silent failures from type mismatches are hard to debug.

### Emulator Tests Catch Real Bugs

Unit tests with mocks didn't catch the `input_media_gcs_path` bug. Emulator integration tests did because they use real Firestore behavior.

**Lesson**: Run `make test` which includes emulator tests before every commit.

### Test Isolation

Emulator tests need their own `conftest.py` that does NOT mock `google.cloud.*`. Mocks from unit tests can leak into integration tests.

### Playwright: Use Assertions, Not Timeouts

**Problem**: `waitForTimeout()` is flaky and slow:

```typescript
// BAD - arbitrary wait, still flaky
await page.waitForTimeout(2000);
const hasContent = await page.locator('.content').count();
```

**Solution**: Use Playwright's auto-retry assertions:

```typescript
// GOOD - waits only until condition met, retries automatically
await expect(page.getByText('Hello,')).toBeVisible({ timeout: 5000 });
```

**Why it matters**: Playwright assertions have built-in retry logic and fail fast when conditions aren't met. Arbitrary timeouts either waste time (waiting when condition is already met) or cause flaky tests (not waiting long enough on slow CI).

### Playwright: Use 'load' Instead of 'networkidle' for Audio-Heavy Pages

**Problem**: Pages with audio players or ongoing audio network requests never reach `networkidle` state.

```typescript
// BAD - hangs indefinitely on pages with audio players
await page.goto(url, { waitUntil: 'networkidle' });
```

**Symptom**: Test times out after 30-45 minutes waiting for navigation to complete, even though the page is fully rendered and usable.

**Solution**: Use `waitUntil: 'load'` plus a small fixed wait if needed:

```typescript
// GOOD - works with audio-heavy pages
await page.goto(url, { waitUntil: 'load' });
await page.waitForTimeout(3000);  // Allow JS to initialize
```

**Root cause**: Audio players make continuous network requests for streaming/buffering, preventing the network from ever becoming "idle".

### Playwright: HEAD Requests May Not Be Supported

**Problem**: Using `page.request.head()` to verify download URLs returns 405 Method Not Allowed on some endpoints.

```typescript
// BAD - many download endpoints don't support HEAD
const response = await page.request.head(downloadUrl);
// Returns 405 instead of 200
```

**Solution**: Use GET with `maxRedirects: 0` to check accessibility without downloading:

```typescript
// GOOD - works with all endpoints, doesn't follow redirects
const response = await page.request.get(downloadUrl, { maxRedirects: 0 });
expect(response.status() >= 200 && response.status() < 400).toBe(true);
```

**Why maxRedirects: 0**: Download URLs often redirect to cloud storage (GCS, S3). A 302/307 redirect means the URL is valid. Following the redirect would download the entire file.

### E2E Tests: Understand Job State Machine Flow

**Problem**: E2E test skipped instrumental selection because it checked for "rendering" in status, but rendering happens BEFORE instrumental selection, not after.

```typescript
// BAD - assumes rendering means instrumental is done
if (status.includes('rendering') || status.includes('encoding')) {
  console.log('Past instrumental stage');
  break;
}
```

**Actual job flow**:
```text
downloading → transcribing → in_review → rendering_video →
awaiting_instrumental_selection → encoding → complete
```

**Solution**: Only skip on statuses that are definitively past the target stage:

```typescript
// GOOD - encoding is definitely past instrumental
if (status.includes('encoding') && !status.includes('awaiting')) {
  console.log('Past instrumental stage');
  break;
}
```

**Lesson**: Before writing status-checking logic, trace through the actual state machine to understand which states come before/after your target state.

## Deployment Notes

### Cloud Run Timeouts

Default 5-minute timeout may not be enough for video encoding. Consider:
- Cloud Run Jobs for long-running tasks
- Breaking encoding into smaller chunks
- Increasing timeout for specific operations

### Cloud Tasks dispatch_deadline (Separate from Cloud Run Timeout)

**Problem**: Cloud Tasks HTTP handlers have a separate `dispatch_deadline` (default 10 minutes, max 30 minutes) that controls how long Cloud Tasks waits for the HTTP response. This is DIFFERENT from Cloud Run's service timeout.

**Symptom**: Worker killed silently after 10 minutes even though Cloud Run timeout was set to 30 minutes. Job appears stuck with no error.

**Solution**: Set explicit `dispatch_deadline` when creating Cloud Tasks:
```python
from google.protobuf import duration_pb2

task = {
    "http_request": {...},
    "dispatch_deadline": duration_pb2.Duration(seconds=1800),  # 30 min max
}
```

**Key insight**: For tasks that might take >30 minutes, Cloud Tasks HTTP targets won't work. Use Cloud Run Jobs instead (supports up to 24-hour execution).

| Timeout Type | Default | Max | What It Controls |
|-------------|---------|-----|------------------|
| Cloud Tasks `dispatch_deadline` | 10 min | 30 min | How long Cloud Tasks waits for HTTP response |
| Cloud Run service timeout | 5 min | 60 min | How long a single HTTP request can run |
| Cloud Run Jobs timeout | 10 min | 24 hr | How long a batch job execution can run |

See `docs/archive/2026-01-01-worker-timeout-fixes.md` for full details.

### Two-Layer Timeout Pattern for Long-Running Loops

**Problem**: A loop processing many items (e.g., 74 LLM calls at 15s each = 18+ minutes) can block indefinitely. Single outer timeout isn't enough because it may interrupt mid-operation and leave state inconsistent.

**Solution**: Use two layers of timeout protection:

```python
# Layer 1: Inner deadline check (cooperative, clean exit)
deadline = time.time() + timeout_seconds
for item in items:
    if time.time() > deadline:
        logger.warning("Deadline exceeded, breaking early")
        break  # Exit gracefully with partial/no results
    process(item)

# Layer 2: Outer asyncio.wait_for (safety net for hung operations)
result = await asyncio.wait_for(
    asyncio.to_thread(run_loop_with_deadline),
    timeout=timeout_seconds + 60  # Buffer for cleanup
)
```

**Why both layers**:
- Inner check: Handles normal case (many items). Exits cleanly between iterations.
- Outer timeout: Catches edge case (single operation hangs). Hard stop if inner check never runs.

**Design choice**: Use `break` not `raise` for inner timeout. This returns partial/uncorrected results rather than failing the job, allowing downstream human review to fix issues.

See `docs/archive/2025-12-31-agentic-timeout-implementation.md` for implementation details.

### Secret Manager Access

Cloud Run service account needs `Secret Manager Secret Accessor` role. This is managed via Pulumi in `infrastructure/`.

### Cloud Run Secret Mounting

**Problem**: Secrets created in Secret Manager aren't automatically available as environment variables in Cloud Run. The IAM role grants *access*, but secrets must be explicitly *mounted*.

**Wrong assumption**: "I added the secret to Secret Manager and gave the service account access, so the secret is available."

**Reality**: Cloud Run needs `--set-secrets` in the deploy command:
```bash
gcloud run deploy service-name \
  --set-secrets "ENV_VAR_NAME=secret-name:latest"
```

**Symptoms of missing mount**: Service silently falls back to defaults (e.g., email service uses console logging instead of SendGrid).

**Solution**: Add all required secrets to the CI deploy command (`.github/workflows/ci.yml`). Manual `gcloud run services update` fixes are overwritten on next deploy.

### Vertex AI Authentication

For Gemini via Vertex AI, use Application Default Credentials (ADC) rather than API keys:
- **Cloud Run**: Service account automatically authenticated - just grant `roles/aiplatform.user`
- **Local dev**: Run `gcloud auth application-default login`
- **Config**: Set `GOOGLE_CLOUD_PROJECT` and optionally `GCP_LOCATION` (defaults to `global`)

This approach uses GCP's free credits and existing IAM rather than separate API key management.

### Gemini 3 Models Require Global Location

**Problem**: Gemini 3 Flash (`gemini-3-flash-preview`) returns 404 errors when using regional Vertex AI endpoints.

```
404 Publisher Model `projects/PROJECT/locations/us-central1/publishers/google/models/gemini-3-flash-preview` was not found
```

**Solution**: Use `global` location instead of regional endpoints like `us-central1`:
```python
gcp_location: str = "global"  # Required for Gemini 3 models
```

**Additional gotcha**: Gemini 3 returns multimodal response format:
```python
# Gemini 2: response.content = "text"
# Gemini 3: response.content = [{'type': 'text', 'text': '...'}]
```

Code must handle both formats when invoking LangChain ChatVertexAI.

See `docs/archive/2025-12-30-gemini3-agentic-correction-fix.md` for full details.

### LangChain gRPC vs REST Providers

**Problem**: `langchain-google-vertexai` uses gRPC which can hang indefinitely during connection establishment with no timeout, causing cloud jobs to freeze silently.

**Symptoms**:
- Job hangs at "Initializing model..." with no further logs
- No timeout or error - just waits forever
- Hard to diagnose because no exception is raised

**Solution**: Use `langchain-google-genai` (REST-based) instead. This package supports BOTH Vertex AI (service account auth) and Google AI Studio (API key auth):
```python
# Before (gRPC, can hang)
from langchain_google_vertexai import ChatVertexAI
model = ChatVertexAI(model=model_name, project=project)

# After (REST, reliable) - Using Vertex AI with service account/ADC
from langchain_google_genai import ChatGoogleGenerativeAI
model = ChatGoogleGenerativeAI(model=model_name, project=project_id)  # Uses ADC

# Alternative - Using Google AI Studio with API key
model = ChatGoogleGenerativeAI(model=model_name, google_api_key=api_key)
```

**Key insight**: When `project` parameter is provided, `ChatGoogleGenerativeAI` automatically uses the Vertex AI backend with Application Default Credentials (ADC). On Cloud Run, this uses the attached service account - no API key needed.

**Additional protection**: Wrap model initialization in ThreadPoolExecutor timeout:
```python
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

with ThreadPoolExecutor(max_workers=1) as executor:
    future = executor.submit(create_model)
    try:
        model = future.result(timeout=30)  # Fail fast
    except FuturesTimeoutError:
        raise InitializationTimeoutError("Model init timed out")
```

**Why ThreadPoolExecutor**: Works cross-platform (Windows, Linux, macOS). Python's `signal.alarm` only works on Unix.

See `docs/archive/2025-12-30-langchain-vertexai-to-genai-migration.md` for full details.

### CI/CD: GitHub Actions

Migrated from Cloud Build to GitHub Actions for:
- Simpler configuration
- Better visibility
- Faster feedback

### Self-Hosted Runner on GCP

GitHub-hosted runners have ~14GB disk, which caused failures during Docker builds. Solution: self-hosted runner on GCP with 200GB SSD.

**Key learnings:**

1. **GPG in startup scripts**: Always use `gpg --batch` for non-interactive execution
2. **setup-python on self-hosted**: Needs Python in tool cache at `$RUNNER/_work/_tool/Python/<version>/x64/` with `x64.complete` marker
3. **Debian vs Ubuntu**: PPAs don't work on Debian; use upstream repos (Temurin, NodeSource)
4. **pyenv for Python**: More reliable than source builds or distro packages for specific versions

See `docs/archive/2025-12-28-self-hosted-github-runner.md` for full details.

### Pulumi CI Deployments Need Skip-Preview for Limited Service Accounts

**Problem**: Running `pulumi up --refresh` in CI requires the service account to have read permissions on ALL managed GCP resources. Pulumi refreshes state by querying each resource, and permission errors cause the deployment to fail before any changes are applied.

**Symptoms**:
```
Error when reading or editing Resource "project" with IAM Member: Role "roles/...":
Error retrieving IAM policy for project "...": googleapi: Error 403: The caller does not have permission
```

**Root cause**: `--refresh` (default behavior) makes read API calls to verify current state matches Pulumi's state. This requires permissions like:
- `resourcemanager.projects.getIamPolicy` (for IAM bindings)
- `secretmanager.secrets.get` (for secrets)
- `cloudtasks.queues.get` (for Cloud Tasks)
- `iam.workloadIdentityPools.get` (for Workload Identity)
- And many more for each resource type managed

**Solution**: Use `--skip-preview` for CI deployments where Pulumi state is authoritative:

```yaml
# In CI workflow
pulumi up --yes --skip-preview --stack org/project/stack
```

**Why this is safe**:
- Infrastructure is only modified through Pulumi (single source of truth)
- Preview/refresh is still useful during local development with broader permissions
- Reduces permissions surface for CI service account (principle of least privilege)

**Alternative** (if refresh is required): Grant the service account `roles/editor` or individual read roles for each resource type. This is less secure but allows drift detection.

**Lesson**: When setting up Pulumi in CI, either:
1. Grant broad read permissions and keep `--refresh` (detects drift)
2. Use minimal permissions and `--skip-preview` (trusts Pulumi state)

Choose based on your threat model. For most projects, trusting Pulumi state is acceptable since manual console changes are rare.

### Docker Disk Space Management on Self-Hosted Runners

**Problem**: Even with 200GB SSDs, self-hosted runners can fill up with Docker images. Each CI build creates new dangling images (~15GB each), and without aggressive cleanup, disk fills in days.

**Symptoms**:
- CI jobs fail with `No space left on device`
- Jobs that require Docker (build, deploy, emulator tests) fail
- Runners 11-20 at 100% while 1-10 at 30% (newer runners had less cleanup history)

**Root cause**: Original cleanup cron used `docker system prune -af --filter "until=168h"` which only removes images older than 7 days. With frequent CI builds, most dangling images are < 7 days old and never get cleaned.

**Solution** (two-layer approach):

1. **Hourly threshold-based cleanup** (on runners):
   ```bash
   # /etc/cron.hourly/docker-cleanup
   THRESHOLD=70
   USAGE=$(df / | tail -1 | awk '{print $5}' | tr -d '%')
   if [ "$USAGE" -gt "$THRESHOLD" ]; then
       docker system prune -af  # No age filter!
       docker builder prune -af
   fi
   ```

2. **Pre-job disk check** (in CI workflow):
   ```yaml
   - name: Check disk space
     run: |
       USAGE=$(df / | tail -1 | awk '{print $5}' | tr -d '%')
       if [ "$USAGE" -gt 70 ]; then
         docker system prune -af || true
         docker builder prune -af || true
       fi
       if [ "$USAGE" -gt 85 ]; then
         echo "::error::Disk usage exceeds 85%. Runner needs maintenance."
         exit 1
       fi
   ```

**Key insights**:
- Remove the age filter (`--filter "until=168h"`) - dangling images should always be cleaned
- Threshold-based cleanup is better than time-based (don't clean if disk is fine)
- Pre-job checks provide fail-fast behavior with clear error messages
- `docker builder prune -af` catches buildkit cache that `system prune` misses

### Fonts in Docker for Video Rendering

**Problem**: Video rendering showed replacement characters (squares with question marks) for special Unicode symbols like `♪` in intro/instrumental screens.

**Root cause**: The `python:3.11-slim` Docker base image has NO fonts installed. FFmpeg/libass falls back to whatever it can find, which often lacks support for musical symbols, emoji, or non-Latin scripts.

**Symptoms**:
- `♪ INTRO (19 seconds) ♪` displayed as `□ INTRO (19 seconds) □`
- Unicode characters render as tofu (replacement boxes)
- Only appears in cloud-generated videos, not local dev (which uses system fonts)

**Solution**: Install comprehensive Noto fonts in the Docker base image:
```dockerfile
RUN apt-get update && apt-get install -y \
    fonts-noto-core \
    fonts-noto-cjk \
    fonts-noto-extra \
    fonts-noto-color-emoji \
    fontconfig \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*
```

**Package breakdown**:
- `fonts-noto-core` (~10MB): Latin, Greek, Cyrillic, musical symbols
- `fonts-noto-cjk` (~150MB): Chinese, Japanese, Korean
- `fonts-noto-extra` (~50MB): Arabic, Hebrew, Thai, Hindi, other scripts
- `fonts-noto-color-emoji` (~10MB): Color emoji (limited libass support)
- `fontconfig`: Font configuration system (required for `fc-cache`)

**Note on color emoji**: FFmpeg's libass subtitle renderer doesn't fully support color emoji fonts - it renders text as vectors and can't display color bitmap glyphs. Color emoji appears as monochrome outlines or may not render at all in ASS subtitles.

**Lesson**: When building Docker images for video/subtitle rendering, install fonts explicitly. The default slim images have nothing, and font fallback behavior differs from local development machines.

### Reuse LLM Model Instances Across Operations

**Problem**: Creating a new LLM model instance for each operation in a loop causes massive overhead. Each instance creation triggers model initialization, connection establishment, and optional "warm-up" calls - adding 2+ seconds per operation.

**Example**: Agentic lyrics correction was creating a new `AgenticCorrector` for each gap (lyric segment needing correction):

```python
# BAD - 31 gaps × 2s initialization = 62s overhead
for gap in gaps_to_correct:
    agent = AgenticCorrector.from_model(model_id, ...)  # New instance every time!
    agent.correct_gap(gap)
```

**Symptoms**:
- Operations that should take ~1 minute take 5+ minutes
- Logs show "Initializing model..." or "Warming up..." repeatedly
- Each loop iteration has a consistent ~2s overhead before actual work
- Total time scales linearly with loop count, not with actual work

**Solution**: Create the model instance ONCE before the loop, then reuse:

```python
# GOOD - 1 initialization, reused for all gaps
agent = AgenticCorrector.from_model(model_id, ...)  # Create once
for gap in gaps_to_correct:
    agent.correct_gap(gap)  # Reuse existing instance
```

**Additional optimization**: If operations are independent, process them in parallel:

```python
# BETTER - parallel processing with shared model
agent = AgenticCorrector.from_model(model_id, ...)
with ThreadPoolExecutor(max_workers=5) as executor:
    futures = {executor.submit(agent.correct_gap, gap): gap for gap in gaps}
    for future in as_completed(futures):
        result = future.result()
```

**Results**:
- Before: 31 gaps → ~5 minutes (2s init + 3s work per gap)
- After: 20 gaps → ~55 seconds (1 init + parallel processing)
- Speedup: ~5-6x

**Why warm-up is often unnecessary**: The warm-up pattern (sending a trivial request to "prime" the model) made sense for cold-start scenarios in older systems. Modern LLM APIs:
- Don't have cold-start penalties per request
- Handle connection pooling internally
- May even rate-limit warm-up calls

If you're already making real requests, warm-up adds latency without benefit.

**Key insight**: LLM client instances are designed for reuse. They maintain connection pools, authentication state, and caching. Creating a new instance per request throws away these benefits.

**Configuration**: Added `AGENTIC_MAX_PARALLEL_GAPS` env var (default: 5) to control parallelism. Higher values increase throughput but may hit rate limits.

## Performance Observations

| Operation | Duration |
|-----------|----------|
| Audio separation | 5-8 min |
| Lyrics transcription | 2-3 min |
| Auto-correction | 30 sec |
| Screens generation | 30 sec |
| Video rendering | 10-15 min |
| Final encoding | 5-10 min |
| **Total** | 30-50 min |

Human review time varies (5-15 min typical).

### Gitignore Patterns for Monorepos

**Problem**: Root `.gitignore` had `lib/` to ignore Python lib directories, but this also ignored `buy-site/lib/` (frontend code).

**Solution**: Use negation patterns for frontend directories:
```gitignore
# Python lib directories
lib/
lib64/
# But allow frontend lib directories
!frontend/lib/
!buy-site/lib/
```

**Lesson**: When adding gitignore patterns, consider their effect on subdirectories, especially in monorepos with multiple languages.

### Firestore Transactions for Race Conditions

**Problem**: Magic link verification had a race condition - concurrent requests could both verify the same token before it was marked as used.

**Solution**: Use Firestore transactions for atomic read-check-update operations:
```python
@firestore.transactional
def verify_in_transaction(transaction):
    doc = doc_ref.get(transaction=transaction)
    # Check conditions...
    transaction.update(doc_ref, {'used': True})
```

### Firestore Returns Timezone-Aware Datetimes

**Problem**: Firestore stores datetimes as timezone-aware (UTC). Code using `datetime.utcnow()` (naive) for comparisons fails with `TypeError: can't compare offset-naive and offset-aware datetimes`.

**Symptoms**:
- Session validation fails silently (exception caught, returns error)
- Session exists and appears valid in database
- First request after login may work, subsequent requests fail (because `last_activity_at` is updated to TZ-aware on first access)

**Solution**: Always use timezone-aware datetimes:
```python
from datetime import datetime, timezone

# GOOD
now = datetime.now(timezone.utc)

# Normalize Firestore datetimes that might be naive (legacy data)
if session.expires_at.tzinfo is None:
    expires_at = session.expires_at.replace(tzinfo=timezone.utc)
```

**Note**: `datetime.utcnow()` is deprecated in Python 3.12+. Use `datetime.now(timezone.utc)` instead.

See `docs/archive/2025-12-30-auth-session-persistence-fix.md` for full details.

### Frontend Module-Level State in Next.js

**Problem**: Caching values in module-level variables can cause stale state in Next.js due to module caching and hydration timing.

```javascript
// BAD - may return stale value
let accessToken = localStorage.getItem('key');
export function getToken() { return accessToken; }

// GOOD - always fresh
export function getToken() {
  if (typeof window !== 'undefined') {
    return localStorage.getItem('key');
  }
  return null;
}
```

**Lesson**: For values that can change (auth tokens, user preferences), read fresh from storage on each access rather than caching in module scope.

### Zustand Module-Level Initialization Doesn't Re-run on Client Navigation

**Problem**: Module-level initialization code in zustand stores only runs once when the module is first imported. Client-side navigation (e.g., `router.push()`) doesn't reload modules, so initialization code doesn't re-run.

**Example bug**: Auth module had initialization that fetched user data if a token existed:
```typescript
// auth.ts - module level (runs once at import)
if (typeof window !== 'undefined') {
  const token = getAccessToken()
  if (token) {
    useAuth.getState().fetchUser()  // Only runs on initial page load!
  }
}
```

After beta enrollment:
1. User lands on `/` (landing page) - auth.ts imported, no token yet, `fetchUser()` not called
2. User completes enrollment, token stored via `setAccessToken()`
3. `router.push('/app')` - client-side navigation, modules already loaded
4. Auth module initialization doesn't re-run - `user` stays `null`
5. UI shows "Login" button instead of user credits

**Symptoms**:
- Feature works after page reload but not after client-side navigation
- Zustand store has correct persisted data (token) but not computed data (user profile)
- E2E tests that use reload pass; tests that use `router.push` fail

**Solution**: Components that need fresh data should trigger fetches in useEffect:
```typescript
// In /app page component
const { user, fetchUser } = useAuth()

useEffect(() => {
  const token = getAccessToken()
  if (token && !user) {
    fetchUser()  // Ensure user data is loaded regardless of how we got here
  }
}, [user, fetchUser])
```

**Lesson**: Don't rely on module-level initialization for data that might need to be fetched after client-side navigation. Use useEffect in components to ensure data is loaded when needed.

### GCP Organization Policies Block Service Account Keys

**Problem**: GCP organization policies can block service account key creation even when you have Owner access to the project.

```
ERROR: Key creation is not allowed on this service account.
violations: constraints/iam.disableServiceAccountKeyCreation
```

**Solution**: If you have Organization Policy Administrator role, temporarily disable the constraint:

```bash
# Disable at organization level (find org ID with: gcloud projects describe PROJECT --format="value(parent)")
cat > /tmp/policy.yaml << 'EOF'
name: organizations/ORG_ID/policies/iam.disableServiceAccountKeyCreation
spec:
  rules:
  - enforce: false
EOF
gcloud org-policies set-policy /tmp/policy.yaml

# Wait ~30 seconds for propagation
sleep 30

# Create key
gcloud iam service-accounts keys create key.json --iam-account=SA_EMAIL

# Re-enable immediately
cat > /tmp/policy.yaml << 'EOF'
name: organizations/ORG_ID/policies/iam.disableServiceAccountKeyCreation
spec:
  rules:
  - enforce: true
EOF
gcloud org-policies set-policy /tmp/policy.yaml

# Clean up
rm /tmp/policy.yaml key.json  # After copying key contents
```

**Note**: Also check for the newer managed constraint `iam.managed.disableServiceAccountKeyCreation` and disable both if needed.

**Lesson**: Service account keys are a security risk and should be avoided when possible. Use Workload Identity Federation instead. But for third-party services that require JSON keys (like LangFuse), this temporary policy override is the safest approach.

### Cloud Run Horizontal Scaling and In-Memory Cache

**Problem**: In-memory caches (singletons, module-level dicts) don't persist across Cloud Run instances. When scaling horizontally, request 1 may hit instance A and populate the cache, but request 2 may hit instance B which has an empty cache.

**Example**: `AudioSearchService` cached search results in `self._cached_results`. Search hit instance A, but download hit instance B:
```
Error: No cached result for index 0. Available indices: 0--1. Run search() first.
```

**Solution**: Never rely on in-memory state across requests. Persist to Firestore/GCS:
```python
# BAD - cache lost if next request hits different instance
self._cached_results = search_results
result = self._cached_results[selection_index]

# GOOD - persist in job state_data
job_manager.update_job(job_id, {'state_data': {'search_results': results}})
# Later...
job = job_manager.get_job(job_id)
result = job.state_data['search_results'][selection_index]
```

**Lesson**: Design for stateless request handling. Any state that must survive between requests must be persisted externally.

### Firestore Cache Has No Automatic Invalidation

**Problem**: Audio search results are cached in Firestore (`job.state_data.audio_search_results`) with no expiration or invalidation. When a library like flacfetch is updated with new providers or bug fixes, existing cached results remain stale forever.

**Symptoms**:
- Job shows only 5 YouTube results when CLI shows 59 results including lossless sources
- User cannot get better search results without admin intervention
- No "re-search" option in the UI

**Example**: Job `6ddbbef1` was searched when flacfetch had a bug returning only YouTube results. After flacfetch was fixed, the job still showed the old 5-result cache.

**Solution**: Added admin endpoints and UI to manage cached searches:
- `GET /api/admin/audio-searches` - List jobs with cached results
- `POST /api/admin/audio-searches/{job_id}/clear-cache` - Clear both Firestore and flacfetch GCS cache
- `DELETE /api/admin/cache` - Clear entire flacfetch cache
- `GET /api/admin/cache/stats` - View cache statistics
- Admin UI at `/admin/searches` shows cache stats and "Clear All Cache" button

**Key insight**: There are TWO caches involved:
1. **Firestore** (`job.state_data.audio_search_results`) - stores results for user selection UI
2. **Flacfetch GCS** - caches tracker search responses (30-day TTL)

Both must be cleared for a truly fresh search. The clear-cache endpoint now clears both automatically.

**Future consideration**: Add automatic cache invalidation based on:
- Time (TTL)
- flacfetch version change
- User-initiated "re-search" button

**Lesson**: When caching external API results, consider how/when the cache should be invalidated. Permanent caches need admin tooling to manage stale data. When multiple layers cache the same data, clearing one layer isn't enough - you need to clear all of them.

### GCS Downloads Preserve Directory Structure

**Problem**: When downloading files from GCS, the directory structure is preserved. Code that uses non-recursive glob patterns won't find files in subdirectories.

**Example**: GCE encoding worker downloaded from `gs://bucket/jobs/{job_id}/` but files were stored as:
- `videos/with_vocals.mkv`
- `stems/instrumental_clean.flac`

The original code used `work_dir.glob("*.mkv")` which only searched the root directory, causing "No video files found" errors.

**Solution**: Use recursive glob patterns and filter results:
```python
# BAD - only searches root directory
input_files = list(work_dir.glob("*.mkv"))

# GOOD - searches all subdirectories
input_files = list(work_dir.glob("**/*.mkv"))
# Filter out outputs directory to avoid re-encoding
input_files = [f for f in input_files if "outputs" not in str(f)]
```

**Related gotcha**: Case sensitivity matters. GCS stores filenames as-is, so if files are uploaded as `instrumental_clean.flac` (lowercase), searching for `*Instrumental*.flac` (capital I) won't find them. Use case-insensitive patterns or search for both cases.

**Lesson**: When writing code that processes files downloaded from GCS, always use recursive glob patterns and verify the expected directory structure matches what's actually stored.

### GCS Blob Paths vs Full URIs

**Problem**: When uploading files to GCS and returning paths to other services, there's confusion between "blob paths" (just the path within a bucket) and "full GCS URIs" (gs://bucket/path).

**Symptoms**:
- 404 errors when downloading files that were just uploaded
- Paths look like: `bucket/gs://bucket/path/file.mp4` (doubled prefix)
- Storage service prepends bucket name to what's already a full URI

**Example of the bug**:
```python
# GCE worker uploads to gs://bucket/jobs/id/encoded/
# Then returns full URI in response: "gs://bucket/jobs/id/encoded/file.mp4"
# Backend tries: storage.download_file("gs://bucket/jobs/id/encoded/file.mp4")
# Storage service prepends bucket: "bucket/gs://bucket/jobs/id/encoded/file.mp4"
# Result: 404 - No such object
```

**Solution**: Return blob paths (path within bucket), not full GCS URIs:
```python
# output_gcs_path = "gs://bucket/jobs/id/encoded/"
gcs_path = output_gcs_path.replace("gs://", "")
parts = gcs_path.split("/", 1)
bucket = parts[0]
prefix = parts[1].rstrip("/") if len(parts) > 1 else ""

# Return blob paths like: "jobs/id/encoded/file.mp4"
blob_path = f"{prefix}/{filename}" if prefix else filename
```

**Related gotcha**: Double slashes in paths. When the prefix already ends with "/" and you concatenate with another "/":
```python
# BAD - creates "encoded//file.mp4"
prefix = "jobs/id/encoded/"  # Has trailing slash
blob_name = f"{prefix}/{filename}"  # Adds another slash

# GOOD - strip trailing slash first
prefix = parts[1].rstrip("/")
blob_name = f"{prefix}/{filename}" if prefix else filename
```

**Lesson**: When building systems that pass GCS paths between services, establish a clear contract: either always use full URIs (`gs://bucket/path`) or always use blob paths (`path/within/bucket`). Document which format each API expects.

### Library Caches Need GCS Sync in Cloud Run

**Problem**: Libraries like LyricsTranscriber cache API responses to local directories (e.g., `~/lyrics-transcriber-cache`). In Cloud Run, each container is ephemeral - the cache is lost on every restart or scale-up, causing redundant API calls.

**Symptoms**:
- Same song processed multiple times always hits external APIs
- Higher API costs than expected (AudioShake charges per transcription)
- Slower processing even for repeated jobs

**Solution**: Sync library cache to/from GCS before/after each job:

```python
# Set cache dir via environment variable (library reads this)
cache_dir = os.path.join(temp_dir, "lyrics-cache")
os.environ["LYRICS_TRANSCRIBER_CACHE_DIR"] = cache_dir

# Download relevant cache files from GCS before processing
cache_service.sync_cache_from_gcs(cache_dir, audio_hash, lyrics_hash)

# ... run processing ...

# Upload new cache files to GCS after processing
cache_service.sync_cache_to_gcs(cache_dir, audio_hash, lyrics_hash)
```

**Key design decisions**:
1. Download only relevant files (by hash) - don't sync entire cache directory
2. Skip files that already exist in GCS on upload (same input = same output)
3. Use environment variable to configure library cache dir (avoids modifying library code)
4. Cache indefinitely (no TTL) - API responses don't change for same inputs

**Lesson**: When using third-party libraries with local caching in Cloud Run, check if they support configurable cache directories. If so, sync to GCS for persistence across instances.

### Sequential vs Parallel Worker Triggering

**Problem**: For URL-based jobs (YouTube), triggering both audio and lyrics workers in parallel caused failures. Lyrics worker needs the audio file, but audio worker hadn't downloaded it yet.

```python
# BAD - for URL jobs, audio isn't ready yet
background_tasks.add_task(_trigger_workers_parallel, job_id)  # Both start together
# Lyrics worker times out waiting for audio
```

**Solution**: For URL jobs, trigger workers sequentially:
```python
# GOOD - audio downloads first, then triggers lyrics
if job.url and not job.input_media_gcs_path:
    # Only trigger audio worker; it will trigger lyrics after download
    background_tasks.add_task(worker_service.trigger_audio_worker, job_id)
else:
    # For uploaded files, parallel is fine
    background_tasks.add_task(_trigger_workers_parallel, job_id)
```

**Lesson**: Understand data dependencies between workers. If worker B needs output from worker A, don't run them in parallel.

### Always Extract Auth Context Into Jobs

**Problem**: Job creation endpoints used `require_auth` but never extracted `auth_result.user_email` to store on the job. Jobs were created with `user_email=None`, so users couldn't see their own jobs.

```python
# BAD - auth used for access control but email not captured
@router.post("/jobs/upload")
async def upload(auth_result = Depends(require_auth)):
    job = JobCreate(...)  # user_email not set!
```

**Solution**: Extract user identity from auth and set on job:
```python
# GOOD - capture authenticated user's identity
@router.post("/jobs/upload")
async def upload(auth_result: AuthResult = Depends(require_auth)):
    effective_user_email = auth_result.user_email or form_user_email
    job = JobCreate(user_email=effective_user_email, ...)
```

**Lesson**: Auth provides identity, not just access control. If jobs need owner association, explicitly extract and store the user identifier.

See `docs/archive/2024-12-31-job-failure-investigation.md` for full details.

### Alternative Code Paths Must Implement All Features

**Problem**: Adding a GCE encoding option created a code path that completely bypassed the existing `KaraokeFinalise` class, which contained YouTube upload, Discord notifications, and CDG/TXT packaging. Jobs using GCE encoding silently missed these features.

```python
# BAD - two divergent code paths
if use_gce_encoding:
    await gce_encoding_worker.encode(job_id)  # Returns, does nothing else
    return  # YouTube upload, Discord, CDG never happen!
else:
    finalise = KaraokeFinalise(...)  # Has all the features
    await finalise.run()
```

**Symptoms**:
- Jobs complete successfully but YouTube URL is never set
- No Discord notifications for GCE-encoded jobs
- CDG/TXT files never generated for GCE jobs
- No errors in logs (the code path simply doesn't include those features)

**Solution**: Create an orchestrator that runs all stages regardless of encoding backend:

```python
# GOOD - unified pipeline with swappable encoding
orchestrator = VideoWorkerOrchestrator(config)
result = await orchestrator.run()
# Orchestrator always runs: packaging → encoding → organization → distribution → notifications
# The encoding step uses either local or GCE backend based on config
```

**Key insight**: When adding an alternative implementation (like GCE encoding), audit ALL side effects of the original code path. The new path must replicate them or delegate to shared code.

**Architecture pattern**: The orchestrator pattern separates "what stages run" from "how each stage runs". Stages are always executed; only the implementation varies.

See `docs/archive/2026-01-04-video-worker-orchestrator-refactor.md` for full details.

### Firestore Document 1MB Limit with Embedded Arrays

**Problem**: Firestore documents have a 1MB size limit. Storing logs as an embedded array (`worker_logs: [{...}, {...}, ...]`) within job documents caused job failures when logs exceeded the limit during long-running operations like video encoding.

**Example**: Job `501258e1` failed when logs reached 1.26MB (2,547 log entries), representing 98.6% of the maximum document size. The job update failed with:
```
Document cannot be written because its size exceeds the maximum allowed size
```

**Root cause**: Video encoding jobs generate thousands of log entries (FFmpeg output, progress updates). Each log entry is ~500 bytes. 2,000+ entries easily exceed 1MB.

**Solution**: Store logs in a Firestore subcollection instead of embedded array:

```python
# BAD - embedded array hits 1MB limit
doc_ref.update({
    "worker_logs": firestore.ArrayUnion([{"message": "..."}])
})

# GOOD - subcollection has no size limit per parent doc
logs_ref = db.collection("jobs").document(job_id).collection("logs")
logs_ref.document(log_id).set({
    "timestamp": now,
    "level": "INFO",
    "worker": "video",
    "message": "...",
    "ttl_expiry": now + timedelta(days=30)
})
```

**Key benefits of subcollection approach**:
1. Each log entry is its own document (no size limit concerns)
2. Natural association with parent job (path: `jobs/{job_id}/logs/{log_id}`)
3. Can add TTL via Firestore TTL policies for automatic cleanup
4. Efficient queries with composite indexes (worker + timestamp)
5. Concurrent workers can write without race conditions (unlike ArrayUnion which can conflict)

**Migration strategy**: Used feature flag (`USE_LOG_SUBCOLLECTION=true`) for rollback capability. New jobs use subcollection; old jobs with embedded arrays continue to work via fallback read logic.

**Lesson**: Never use embedded arrays for unbounded data. If an array can grow indefinitely based on operation duration or user input, use a subcollection instead. The 1MB limit applies to the entire document, including all fields.

### Splitting Fast Generation from Slow Encoding

**Problem**: Preview video generation took 60+ seconds on Cloud Run, causing poor UX during lyrics review. The bottleneck was FFmpeg encoding (CPU-bound), not the subtitle generation (fast text processing).

**Solution**: Split the operation into two phases with optional offloading:

1. **ASS Generation (Cloud Run)**: Generate ASS subtitle file locally (~2s). This is pure text processing with no CPU-intensive encoding.

2. **Video Encoding (GCE)**: Upload ASS to GCS and offload FFmpeg encoding to the high-performance GCE worker (~15-20s on C4-standard-8).

```python
# Generate ASS only (fast, local)
result = CorrectionOperations.generate_preview_video(
    ...,
    ass_only=True,  # Skip video encoding
)

# Upload ASS to GCS
storage.upload_file(result["ass_path"], f"jobs/{job_id}/previews/{preview_hash}.ass")

# Offload encoding to GCE
gce_result = await encoding_service.encode_preview_video(
    job_id=preview_hash,
    ass_gcs_path=f"gs://bucket/jobs/{job_id}/previews/{preview_hash}.ass",
    audio_gcs_path=audio_gcs_path,
    output_gcs_path=f"gs://bucket/jobs/{job_id}/previews/{preview_hash}.mp4",
)
```

**Key insight**: When a pipeline has both fast (text generation) and slow (media encoding) steps, design for separability:
- Add `_only` parameters to skip slow steps
- Return intermediate outputs (paths to generated files)
- Allow external services to handle the slow part
- Keep fallback to local processing for reliability

**Configuration**: Controlled by `USE_GCE_PREVIEW_ENCODING` env var. Falls back to local encoding if GCE fails or is disabled.

**Result**: Preview generation dropped from 60+ seconds to ~15-20 seconds.

### External Service Response Format Mismatches

**Problem**: The GCE encoding worker returned responses in unexpected formats, causing `'list' object has no attribute 'get'` errors that were hard to debug through the async pipeline.

**Issues encountered**:

1. **Status endpoint returning list instead of dict**: The `/status/{job_id}` endpoint sometimes returned a list `[{...}]` instead of a dict `{...}`. Calling `.get()` on the list caused failures.

2. **output_files as list of paths instead of dict**: The worker returned:
   ```json
   "output_files": ["jobs/id/finals/output_4k_lossless.mp4", "jobs/id/finals/output_720p.mp4"]
   ```
   But the backend expected:
   ```json
   "output_files": {"mp4_4k_lossless": "path/...", "mp4_720p": "path/..."}
   ```

**Symptoms**:
- Job fails late in pipeline (during encoding completion handling)
- Error message is cryptic (`'list' object has no attribute 'get'`)
- Hard to reproduce locally (depends on GCE worker state)
- Multiple similar errors from different root causes

**Solution**: Add defensive type checking for all external service responses:

```python
# Always check response type before accessing
if isinstance(status, list):
    logger.warning(f"GCE returned list instead of dict: {status}")
    status = status[0] if status and isinstance(status[0], dict) else {}
if not isinstance(status, dict):
    status = {}

# Convert list of paths to expected dict format
if isinstance(output_files, list):
    output_dict = {}
    for path in output_files:
        filename = path.split("/")[-1]
        if "4k_lossless" in filename:
            output_dict["mp4_4k_lossless"] = path
        # ... etc
    output_files = output_dict
```

**Key insight**: When integrating with external services, never assume the response format. Add explicit type checks and conversions, with logging when unexpected formats are encountered. This is especially important for services you don't control (internal microservices, third-party APIs).

**Debugging tip**: When hitting format errors in async pipelines, check the raw response from the external service directly (curl with API key) before assuming the bug is in your code.

### GCE Encoding Returns GCS Paths, Not Local Files

**Problem**: When using GCE encoding (remote high-performance encoder), the orchestrator received GCS blob paths like `jobs/{id}/finals/output_4k_lossless.mp4` instead of local file paths. Subsequent operations like YouTube upload checked `os.path.isfile()` which returned `False`, causing silent failures.

**Symptoms**:
- YouTube upload logged "No video file available for YouTube upload"
- Other file-based operations silently skipped
- Dropbox/GDrive uploads succeeded (they use different code paths)
- No errors in logs - just warnings that were easy to miss

**Root cause**: The `VideoWorkerOrchestrator._run_encoding()` method stored the paths returned by `GCEEncodingBackend` directly without downloading the files. For local encoding, the paths are already local. For GCE encoding, the paths are GCS blob paths that need to be downloaded first.

**Solution**: Added `_download_gce_encoded_files()` method that:
1. Detects when GCE backend was used (`encoding_backend.name == "gce"`)
2. Downloads each encoded file from GCS to the local temp directory
3. Updates result paths to point to local files

```python
# After encoding completes
if encoding_backend.name == "gce" and self.storage:
    await self._download_gce_encoded_files(output)
```

**Key insight**: When an operation can run on multiple backends (local vs remote), ensure the outputs are normalized to a common format before subsequent stages consume them. The orchestrator pattern should abstract away backend differences, not leak them to downstream consumers.

**Testing tip**: Test the full pipeline with each backend type, not just the encoding step in isolation. The bug only manifested when YouTube upload ran after GCE encoding - unit tests of each stage passed.

### Unify Encoding Logic with GCS Wheel Deployment

**Problem**: The GCE encoding worker had simplified, duplicated encoding logic that:
- Didn't concatenate title + karaoke + end screens
- Used generic filenames like `output_4k_lossless.mp4` instead of proper names
- Lacked feature parity with the local CLI's `LocalEncodingService`

This caused cloud-generated videos to be incomplete (no title/end screens) and have unhelpful filenames that didn't match the local CLI output.

**Solution**: Deploy the karaoke-gen wheel to GCS, have the GCE worker install and use `LocalEncodingService`:

1. **CI uploads wheel to GCS**: `deploy-backend` job uploads wheel to `gs://bucket/wheels/`

2. **Dynamic wheel loading**: GCE worker downloads and installs latest wheel at job start:
   ```python
   def ensure_latest_wheel():
       """Download and install latest karaoke-gen wheel from GCS."""
       subprocess.run(["gsutil", "cp", "gs://bucket/wheels/karaoke_gen-*.whl", "/tmp/"])
       wheel = sorted(glob.glob("/tmp/karaoke_gen-*.whl"))[-1]
       subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", wheel])

   async def process_job(job_id, request):
       ensure_latest_wheel()  # Get latest code at job start
       # ... rest of processing
   ```

3. **Single encoding path**: GCE worker imports and uses `LocalEncodingService`:
   ```python
   from backend.services.local_encoding_service import LocalEncodingService, EncodingConfig

   service = LocalEncodingService(logger=logger)
   config = EncodingConfig(
       title_video=str(title_video),
       karaoke_video=str(karaoke_video),
       output_lossless_4k_mp4=f"{base_name} (Final Karaoke Lossless 4k).mp4",
       # ... proper naming for all formats
   )
   result = service.encode_all_formats(config)
   ```

**Key benefits**:
1. **Single source of truth**: Same encoding logic used by CLI, Cloud Run, and GCE
2. **Hot updates without restart**: New code deployed on next job, not VM restart
3. **In-progress jobs unaffected**: Jobs use the code version they started with
4. **Proper output**: Titles/end screens concatenated, files named correctly

**No fallback by design**: We removed all fallback logic to ensure there's only ONE code path. If the wheel fails to load, the job fails clearly rather than producing inconsistent output.

**Lesson**: When multiple deployment targets (CLI, Cloud Run, GCE) need the same complex logic, package it as a library and deploy via a shared mechanism (GCS wheel). Duplicating logic leads to feature drift and inconsistent behavior.

### Cloud Distribution Must Match Local CLI Output Structure

**Problem**: Cloud-generated karaoke outputs had different file organization than local CLI, causing confusion and missing files in Dropbox uploads:

1. **lyrics/ folder filename differences**:
   - Cloud used generic `(Karaoke).json` instead of `(Lyrics Corrections).json`
   - Cloud used `(Karaoke).txt` instead of `(Lyrics Corrected).txt`
   - Reference lyrics files (`(Lyrics Genius).txt`, etc.) weren't downloaded

2. **Missing files in lyrics/ folder**:
   - `(With Vocals).mkv` wasn't copied to lyrics/ subfolder
   - `previews/` subfolder with preview ASS files wasn't created
   - Reference lyrics from multiple sources weren't included

**Root causes**:
- Lyrics worker uploaded reference files to GCS but didn't track them in `job.files`
- Video worker used generic naming pattern for all lyrics files
- Video worker didn't copy `(With Vocals).mkv` or previews to lyrics folder

**Solution**:

1. **Track all files in job.files** (lyrics_worker.py):
   ```python
   # Upload ALL reference lyrics and track them for distribution
   for ref_filename, source_key in reference_files:
       if os.path.exists(ref_path):
           url = storage.upload_file(ref_path, gcs_path)
           # Track in job.files so video_worker can download
           job_manager.update_file_url(job_id, 'lyrics', f'reference_{source_key}', url)
   ```

2. **Use proper filenames in distribution** (video_worker.py):
   ```python
   lyrics_file_mappings = [
       ('lrc', f"{base_name} (Karaoke).lrc"),
       ('corrections', f"{base_name} (Lyrics Corrections).json"),
       ('corrected_txt', f"{base_name} (Lyrics Corrected).txt"),
       ('uncorrected', f"{base_name} (Lyrics Uncorrected).txt"),
       ('reference_genius', f"{base_name} (Lyrics Genius).txt"),
       # ... etc
   ]
   ```

3. **Copy (With Vocals).mkv to lyrics folder**:
   ```python
   if videos.get('with_vocals'):
       dest = os.path.join(lyrics_dir, f"{base_name} (With Vocals).mkv")
       storage.download_file(videos['with_vocals'], dest)
   ```

4. **Create previews/ subfolder**:
   ```python
   previews_dir = os.path.join(lyrics_dir, "previews")
   os.makedirs(previews_dir, exist_ok=True)
   preview_files = storage.list_files(f"jobs/{job_id}/previews/")
   for blob_name in preview_files:
       if blob_name.endswith('.ass'):
           storage.download_file(blob_name, dest_path)
   ```

**Expected output structure** (matches local CLI):
```
NOMAD-XXXX - Artist - Title/
├── stems/
│   ├── Artist - Title (Vocals model_bs_roformer_...).flac
│   └── ...
└── lyrics/
    ├── Artist - Title (Karaoke).lrc
    ├── Artist - Title (Karaoke).ass
    ├── Artist - Title (Lyrics Corrections).json
    ├── Artist - Title (Lyrics Corrected).txt
    ├── Artist - Title (Lyrics Uncorrected).txt
    ├── Artist - Title (Lyrics Genius).txt
    ├── Artist - Title (With Vocals).mkv
    └── previews/
        └── preview_abc123 (Karaoke).ass
```

**Lesson**: When building distribution packages from cloud storage, the organization code must replicate the exact structure users expect from local execution. Track ALL intermediate files in `job.files` even if they're "optional" - users may rely on them.

## What We'd Do Differently

1. **Add Pydantic model fields test first** - Would have caught the silent field issue immediately

2. **Use emulator tests from day one** - Faster feedback than deploying to test

3. **Design for async human review upfront** - Avoided rearchitecting after discovering ReviewServer blocks

4. **Keep docs minimal and current** - Less documentation, but always accurate

5. **Check gitignore early for new directories** - Especially when adding frontend `lib/` directories
