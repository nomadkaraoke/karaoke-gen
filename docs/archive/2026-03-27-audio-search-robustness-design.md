# Audio Search Robustness — Design Spec

**Date:** 2026-03-27
**Status:** Approved
**Scope:** Frontend only — `AudioSourceStep.tsx`

## Problem

When an audio search fails silently (timeout, network error, edge case), the UI shows "No audio sources found" — implying the search succeeded with zero results. This misleads users into thinking the song isn't available when it actually is.

**Root cause:** The component uses `results.length === 0 && !error` to decide whether to show the "no results" message. But `results` initializes as `[]`, so any failure path that doesn't explicitly set the `error` string will render "No audio sources found."

## Design

### 1. Search State Machine

Replace `isSearching` (boolean), `error` (string), and `isCreditError` (boolean) with a single discriminated union:

```ts
type SearchStatus =
  | { phase: 'idle' }
  | { phase: 'searching'; attempt: number }
  | { phase: 'succeeded' }
  | { phase: 'failed'; reason: string; isCreditError: boolean; attempt: number }
```

Initial state: `{ phase: 'searching', attempt: 1 }` (search fires on mount).

**Rendering rules:**

| Condition | UI |
|---|---|
| `phase === 'searching'` | Spinner (with retry indicator if attempt > 1) |
| `phase === 'succeeded' && results.length > 0` | Tier-based result cards |
| `phase === 'succeeded' && results.length === 0` | NoResultsSection |
| `phase === 'failed' && isCreditError` | Credit error + Buy Credits button |
| `phase === 'failed' && !isCreditError` | Error banner + Retry button |

**Key guarantee:** `NoResultsSection` only renders when `phase === 'succeeded'` AND `results.length === 0`. No other path can reach it.

### 2. Retry Logic

Up to 3 automatic attempts (1 initial + 2 retries) with delays:

- Attempt 1: immediate
- Attempt 2: after 2s delay
- Attempt 3: after 4s delay

Only retry on transient failures:
- Network errors (TypeError from fetch, connection refused)
- Timeouts (AbortError from 45s timeout)
- Server errors (5xx status codes)

Do NOT retry on:
- 4xx errors (auth, credits, bad request)

Spinner shows retry progress: "Searching for audio sources... (retry 2 of 3)"

### 3. Error Classification

| Error Type | Detection | Message |
|---|---|---|
| Credit (402) | `ApiError` with status 402 | "You're out of credits..." + Buy Credits |
| Auth (401/403) | `ApiError` with status 401 or 403 | "Authentication required" |
| Server (5xx) | `ApiError` with status >= 500 | Auto-retry → "Our search service is temporarily unavailable. Please try again." |
| Timeout | `AbortError` or `err.name === 'AbortError'` | Auto-retry → "Search timed out. Please try again." |
| Network | Non-`ApiError` (TypeError, etc.) | Auto-retry → "Network error — check your connection and try again." |

All classified errors land in `{ phase: 'failed' }` — never a silent empty state.

### 4. Files Changed

Only `frontend/components/job/steps/AudioSourceStep.tsx`:

- Replace `isSearching`, `error`, `isCreditError` state variables with `searchStatus` state
- Rewrite `doSearch` to use state machine + retry with backoff
- Update all rendering conditionals to use `searchStatus.phase`
- Update spinner to show retry attempt count

No changes to: API client, backend, `NoResultsSection` component, result rendering, tier logic, fuzzy matching, catalog search.

### 5. Compatibility

- The `handleFuzzyAccept` function resets state and re-triggers search — will reset to `{ phase: 'searching', attempt: 1 }`
- Retry button in error banner resets to `{ phase: 'searching', attempt: 1 }` and calls `doSearch`
- External consumers (`onSearchCompleted`, `onSearchResultChosen`) are unaffected — they fire from the same success paths
