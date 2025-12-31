# Lessons Learned

Key insights for future AI agents working on this codebase.

## Architecture Decisions

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

## Deployment Notes

### Cloud Run Timeouts

Default 5-minute timeout may not be enough for video encoding. Consider:
- Cloud Run Jobs for long-running tasks
- Breaking encoding into smaller chunks
- Increasing timeout for specific operations

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

**Solution**: Use `langchain-google-genai` (REST-based) instead:
```python
# Before (gRPC, can hang)
from langchain_google_vertexai import ChatVertexAI
model = ChatVertexAI(model=model_name, project=project)

# After (REST, reliable)
from langchain_google_genai import ChatGoogleGenerativeAI
model = ChatGoogleGenerativeAI(model=model_name, google_api_key=api_key)
```

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

## What We'd Do Differently

1. **Add Pydantic model fields test first** - Would have caught the silent field issue immediately

2. **Use emulator tests from day one** - Faster feedback than deploying to test

3. **Design for async human review upfront** - Avoided rearchitecting after discovering ReviewServer blocks

4. **Keep docs minimal and current** - Less documentation, but always accurate

5. **Check gitignore early for new directories** - Especially when adding frontend `lib/` directories
