# Plan: Add Retry Logic for Flacfetch Download Failures

**Created:** 2026-02-10
**Branch:** feat/sess-20260210-1158-investigate-job-749141f8
**Status:** Draft

## Overview

Job 749141f8 failed with error: "Audio download failed: Remote download by ID failed: Download by ID request failed:"

Investigation revealed **multiple related issues** when flacfetch network calls fail:

1. **No retry logic** - A single network hiccup causes immediate job failure
2. **Missing source info** - When download fails, source_id/source_name aren't saved to job
3. **Broken retry button** - Can't retry because no source info was persisted
4. **Silent UI failures** - Frontend doesn't show retry error messages to user

### Root Cause

Audio search flow saves selection info to `state_data`, but doesn't save download parameters (source_id, source_name, etc.) to the job document until AFTER download succeeds. If the flacfetch call fails (network error), the job fails with no way to retry.

### Solution

This plan fixes all four issues:
- Add retry logic to flacfetch client (network resilience)
- Persist download info before attempting download (enables retry)
- Update retry endpoint to handle audio search jobs
- Show error messages in frontend when retry fails

## Requirements

### Functional Requirements - Flacfetch Retry Logic
- [x] Retry `download_by_id()` on `httpx.RequestError` (network failures)
- [x] Use exponential backoff spread over ~5 minutes
- [x] Configurable max retries via environment variable
- [x] Log each retry attempt with context
- [x] Do NOT retry on HTTP 4xx errors (client errors - immediate failure)
- [x] Do retry on HTTP 5xx errors (server errors - transient)

### Functional Requirements - Persist Download Info
- [x] Save source_id, source_name, target_file, download_url to job document
- [x] Save BEFORE attempting download (not after)
- [x] Store in top-level fields for easy retry access
- [x] Preserve existing state_data fields (selected_audio_index, etc.)

### Functional Requirements - Retry Endpoint
- [x] Add case for audio search jobs in `/jobs/{id}/retry` endpoint
- [x] Check for saved download params (source_id, source_name)
- [x] Re-download using saved params
- [x] Clear error state before retrying
- [x] Trigger appropriate workers after successful download

### Functional Requirements - Frontend Error Handling
- [x] Show toast notification when retry fails
- [x] Display actual error message from API
- [x] Don't just console.error - make it visible to user

### Non-Functional Requirements
- [x] Minimal code changes (use existing patterns)
- [x] Clear error messages that include retry context
- [x] Backwards compatible (no breaking changes)
- [x] Should not significantly increase total timeout duration

## Technical Approach

### Architecture Decisions

**Option 1: Tenacity library (Recommended)**
- ✅ Industry standard, well-tested
- ✅ Declarative retry configuration
- ✅ Built-in exponential backoff
- ✅ Already used in project (check `poetry.lock`)
- ❌ New dependency if not already present

**Option 2: Manual retry loop**
- ✅ No new dependencies
- ✅ Full control over logic
- ❌ More code to maintain
- ❌ Easy to get wrong (edge cases)

**Decision:** Use **tenacity** if already in dependencies, otherwise implement manual retry loop.

### Retry Strategy

```python
# Retry on network errors only (RequestError)
# Do NOT retry on:
#   - HTTP 4xx (client errors like 400, 401, 404)
#   - Already completed downloads
# Do retry on:
#   - httpx.RequestError (connection timeout, refused, DNS failure)
#   - HTTP 5xx (server errors - service temporarily down)

Max retries: 8 (total 9 attempts)
Backoff: Exponential with cap, multiplier=2, min=10s, max=60s
  - Attempt 1: immediate
  - Attempt 2: wait 10s
  - Attempt 3: wait 20s
  - Attempt 4: wait 40s
  - Attempt 5: wait 60s (capped)
  - Attempt 6: wait 60s (capped)
  - Attempt 7: wait 60s (capped)
  - Attempt 8: wait 60s (capped)
  - Attempt 9: wait 60s (capped)
Total max time: ~370s (6+ minutes) of backoff + request timeouts

This gives the flacfetch service time to recover from restarts,
network issues, or temporary VM problems.
```

### Configuration

Add to `backend/config.py`:
```python
# Flacfetch client retry settings
flacfetch_retry_max_attempts: int = int(os.getenv("FLACFETCH_RETRY_MAX_ATTEMPTS", "9"))  # Total attempts (1 initial + 8 retries)
flacfetch_retry_min_wait: float = float(os.getenv("FLACFETCH_RETRY_MIN_WAIT", "10.0"))  # seconds
flacfetch_retry_max_wait: float = float(os.getenv("FLACFETCH_RETRY_MAX_WAIT", "60.0"))  # seconds
```

### Error Message Improvements

Change from:
```
Download by ID request failed:
```

To:
```
Download by ID request failed after 4 attempts: [ConnectTimeout] Connection to http://10.0.0.5:8080 timed out
```

## Implementation Steps

### Phase 1: Check Dependencies
1. [ ] Check if `tenacity` is in `pyproject.toml` dependencies
2. [ ] If not present, add `tenacity = "^8.2.0"` to dependencies
3. [ ] Run `poetry lock` if dependencies changed

### Phase 2: Add Configuration
4. [ ] Add retry configuration to `backend/config.py`
   - `flacfetch_retry_max_attempts = 9`
   - `flacfetch_retry_min_wait = 10.0`
   - `flacfetch_retry_max_wait = 60.0`

### Phase 3: Implement Flacfetch Retry Logic
5. [ ] Import tenacity in `backend/services/flacfetch_client.py`
6. [ ] Create retry decorator with:
   - Retry on `httpx.RequestError`
   - Retry on `httpx.HTTPStatusError` with 5xx status
   - Stop on `httpx.HTTPStatusError` with 4xx status
   - Exponential backoff with cap at 60s
   - Before-sleep logging callback
7. [ ] Apply decorator to `download_by_id()` method
8. [ ] Improve error messages to include:
   - Number of attempts made
   - Original exception type and message

### Phase 4: Persist Download Info Before Download
9. [ ] Update `audio_search.py:_download_and_start_processing()`
10. [ ] After getting source info from selected result (line 307-311)
11. [ ] **Save to job document BEFORE download attempt:**
    ```python
    job_manager.update_job(job_id, {
        'audio_source_type': 'audio_search',
        'source_name': source_name,
        'source_id': source_id,
        'target_file': target_file,
        'download_url': download_url,
    })
    ```
12. [ ] Then proceed with download (existing code)
13. [ ] Add to Job model if needed (check if fields exist)

### Phase 5: Update Retry Endpoint for Audio Search
14. [ ] Update `backend/api/routes/jobs.py:retry_job()`
15. [ ] Add new case before "no input audio" error (line ~1088)
16. [ ] Check if job has saved download params:
    ```python
    elif job.audio_source_type == 'audio_search' and job.source_id:
        # Has audio search download info - retry download
    ```
17. [ ] Re-call audio search download logic with saved params
18. [ ] Or refactor download into reusable function

### Phase 6: Frontend Error Handling
19. [ ] Update `frontend/components/job/JobActions.tsx`
20. [ ] Add toast notification library (check if exists)
21. [ ] In `handleRetry()` catch block, show toast:
    ```typescript
    catch (error) {
      const message = error.response?.data?.detail || "Failed to retry job"
      toast.error(message)
      console.error("Failed to retry job:", error)
    }
    ```

### Phase 7: Testing - Flacfetch Retry
22. [ ] Add unit tests to `backend/tests/test_flacfetch_client.py`
23. [ ] Test retry on RequestError (mock fails N times then succeeds)
24. [ ] Test no retry on 4xx errors
25. [ ] Test retry on 5xx errors
26. [ ] Test max retries exhausted
27. [ ] Verify logging output includes attempt numbers

### Phase 8: Testing - End-to-End
28. [ ] Test audio search job with flacfetch down
29. [ ] Verify source info is saved even when download fails
30. [ ] Verify retry button works after source info saved
31. [ ] Test retry succeeds when flacfetch comes back up
32. [ ] Verify error toast appears when retry fails

### Phase 9: Documentation
33. [ ] Update `backend/services/flacfetch_client.py` docstring
34. [ ] Add to `docs/LESSONS-LEARNED.md`:
    - "Always add retries for external service calls"
    - "Save operation parameters before attempting operations"
    - "Persist enough state to enable retry from any failure point"
35. [ ] Update `docs/ARCHITECTURE.md` if needed
36. [ ] Document the new job fields in API.md

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `backend/config.py` | Modify | Add retry configuration variables |
| `backend/services/flacfetch_client.py` | Modify | Add retry logic to `download_by_id()` |
| `backend/api/routes/audio_search.py` | Modify | Save download info before attempting download |
| `backend/api/routes/jobs.py` | Modify | Add audio search case to retry endpoint |
| `backend/models/job.py` | Modify (maybe) | Add fields: source_name, source_id, target_file, download_url |
| `frontend/components/job/JobActions.tsx` | Modify | Show error toast when retry fails |
| `backend/tests/test_flacfetch_client.py` | Modify | Add retry tests |
| `backend/tests/test_audio_search.py` | Modify | Test download info persistence |
| `backend/tests/test_jobs.py` | Modify | Test retry with audio search jobs |
| `pyproject.toml` | Modify (maybe) | Add tenacity if not present |
| `docs/LESSONS-LEARNED.md` | Modify | Document patterns |
| `docs/API.md` | Modify | Document new job fields |

## Testing Strategy

### Unit Tests (`backend/tests/test_flacfetch_client.py`)

1. **Test retry on RequestError**
   ```python
   @pytest.mark.asyncio
   async def test_download_by_id_retries_on_network_error():
       """Should retry 3 times on connection errors."""
       client = FlacfetchClient(base_url="http://test", api_key="key")

       with patch("httpx.AsyncClient.post") as mock_post:
           # Fail 2 times, then succeed
           mock_post.side_effect = [
               httpx.RequestError("Connection timeout"),
               httpx.RequestError("Connection refused"),
               Mock(json=lambda: {"download_id": "test123"}, status_code=200),
           ]

           result = await client.download_by_id("YouTube", "abc123")

           assert result == "test123"
           assert mock_post.call_count == 3
   ```

2. **Test no retry on 4xx errors**
   ```python
   @pytest.mark.asyncio
   async def test_download_by_id_no_retry_on_client_error():
       """Should NOT retry on 400/401/404 errors."""
       client = FlacfetchClient(base_url="http://test", api_key="key")

       with patch("httpx.AsyncClient.post") as mock_post:
           response = Mock(status_code=404, text="Not found")
           response.raise_for_status.side_effect = httpx.HTTPStatusError(
               "Not found", request=Mock(), response=response
           )
           mock_post.return_value = response

           with pytest.raises(FlacfetchServiceError, match="404"):
               await client.download_by_id("YouTube", "abc123")

           assert mock_post.call_count == 1  # No retries
   ```

3. **Test retry on 5xx errors**
   ```python
   @pytest.mark.asyncio
   async def test_download_by_id_retries_on_server_error():
       """Should retry on 500/503 errors."""
       # Similar to test 1 but with HTTP 503 errors
   ```

4. **Test max retries exhausted**
   ```python
   @pytest.mark.asyncio
   async def test_download_by_id_fails_after_max_retries():
       """Should fail with clear message after max retries."""
       # All attempts fail, verify error message includes attempt count
   ```

### Integration Tests

5. **Test with actual flacfetch service** (if available in test environment)
   - Verify retry doesn't break normal operation
   - Test with deliberate network issues

### Manual Testing

6. **Simulate network failure**
   ```bash
   # Temporarily stop flacfetch VM
   gcloud compute instances stop flacfetch-service --zone=us-central1-a

   # Submit a test job
   # Observe logs showing retry attempts

   # Restart VM during retries
   gcloud compute instances start flacfetch-service --zone=us-central1-a

   # Job should succeed
   ```

7. **Test with intentional delays**
   - Add sleep in flacfetch API handler
   - Verify retries don't cause issues

## Open Questions

- [x] ~~Is tenacity already in dependencies?~~ (Will check during implementation)
- [x] ~~What's the appropriate total timeout budget?~~ - 9 attempts over ~6 minutes
- [x] ~~Should we fix the Retry button UX in this PR?~~ - YES, it's the same root cause
- [x] ~~Why did job 749141f8 fail before saving audio source info?~~ - Download info saved AFTER download, not before
- [ ] Should we also add retries to `wait_for_download()`?
  - Probably NO - that already has its own polling/timeout logic
- [ ] Should we add circuit breaker pattern?
  - Probably NO for now - keep it simple
  - Could add in future if we see cascading failures
- [ ] Should retry logic be applied to other flacfetch methods?
  - `start_download()` - probably YES (same pattern)
  - `search()` - maybe YES (search can also have network issues)
  - `get_download_status()` - probably NO (polling already handles this)
  - **Decision**: Apply to `start_download()` too, skip `search()` for now
- [ ] Which toast library should frontend use?
  - Check if already exists (react-hot-toast, sonner, etc.)
  - If not, add lightweight library
- [ ] Should we save download params to state_data or top-level fields?
  - Top-level fields more visible and easier to query
  - **Decision**: Top-level fields (source_id, source_name, etc.)

## Rollback Plan

If this causes issues:

1. **Quick rollback**: Set env var `FLACFETCH_RETRY_MAX_ATTEMPTS=1` in Cloud Run
   - Disables retries without code change
   - Can deploy in ~2 minutes

2. **Full rollback**: Revert the PR
   - Git revert the merge commit
   - Deploy previous version

3. **Monitoring**: Watch for:
   - Increased request duration to flacfetch
   - Jobs timing out at higher layer (Cloud Tasks, Cloud Run)
   - Duplicate download attempts causing issues

## Success Criteria

- [ ] Jobs with transient flacfetch failures succeed instead of failing
- [ ] Error messages are more informative (show attempts and root cause)
- [ ] No increase in false positives (jobs that should fail, now hang)
- [ ] All tests pass
- [ ] No impact on successful jobs (retry overhead is minimal)

## Related Issues

### Job 749141f8 - The Incident

**What happened:**
- User searched for audio via audio search
- Selected "Elizabeth Moen - Songbird"
- Backend tried to download via flacfetch
- Flacfetch call failed with network error (connection timeout/refused)
- Job immediately failed
- No source info saved → Retry button broken
- User sees "Retry" button but clicking does nothing (silent 400 error)

**How this fix helps:**
1. ✅ Flacfetch retry logic → Download would have succeeded after retry
2. ✅ Persist download info → Even if all retries fail, user can retry later
3. ✅ Retry endpoint fix → Retry button will actually work
4. ✅ Frontend toast → User sees helpful error if retry still fails

**Note:** Job 749141f8 cannot be fixed retroactively (no source info saved). But future jobs with same issue will be fixable via retry button.

### Future Work (Not in This PR)

Similar pattern should be applied to other external service calls:
- AudioShake API (lyrics transcription)
- Modal API (audio separation)
- YouTube direct API calls (not via flacfetch)
- Email sending (SendGrid)

## References

- [Tenacity documentation](https://tenacity.readthedocs.io/)
- [LESSONS-LEARNED.md](docs/LESSONS-LEARNED.md) - External service response format mismatches
- [flacfetch README](../../flacfetch/README.md) - Understanding the service
