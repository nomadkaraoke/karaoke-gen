# Audio Search Robustness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent the "No audio sources found" message from appearing when a search actually failed, by replacing scattered boolean/string state with an explicit state machine and adding automatic retries.

**Architecture:** Replace `isSearching` (boolean), `error` (string), and `isCreditError` (boolean) in `AudioSourceStep` with a single `searchStatus` discriminated union. Update `doSearch` to classify errors and auto-retry transient failures (up to 3 attempts with backoff). Update all rendering conditionals to key off `searchStatus.phase`.

**Tech Stack:** React (existing component), TypeScript

**Spec:** `docs/archive/2026-03-27-audio-search-robustness-design.md`

---

### Task 1: Add SearchStatus Type and Replace State Variables

**Files:**
- Modify: `frontend/components/job/steps/AudioSourceStep.tsx:110-114`

- [ ] **Step 1: Define the SearchStatus type**

Add immediately above the `AudioSourceStep` function (after the `PendingChoice` type around line 36):

```ts
type SearchStatus =
  | { phase: 'idle' }
  | { phase: 'searching'; attempt: number }
  | { phase: 'succeeded' }
  | { phase: 'failed'; reason: string; isCreditError: boolean; attempt: number }

const MAX_SEARCH_ATTEMPTS = 3
const RETRY_DELAYS = [0, 2000, 4000] // ms delay before each attempt
```

- [ ] **Step 2: Replace the three state variables**

Replace lines 110-114:

```ts
const [results, setResults] = useState<ExtendedAudioSearchResult[]>([])
const [isSearching, setIsSearching] = useState(true)
const [error, setError] = useState("")
const [pendingChoice, setPendingChoice] = useState<PendingChoice | null>(null)
const [isCreditError, setIsCreditError] = useState(false)
```

With:

```ts
const [results, setResults] = useState<ExtendedAudioSearchResult[]>([])
const [searchStatus, setSearchStatus] = useState<SearchStatus>({ phase: 'searching', attempt: 1 })
const [pendingChoice, setPendingChoice] = useState<PendingChoice | null>(null)
```

- [ ] **Step 3: Add derived booleans for convenience**

Add after the `searchStatus` state declaration (these keep the rest of the file readable and limit the blast radius):

```ts
// Derived from searchStatus for rendering convenience
const isSearching = searchStatus.phase === 'searching'
const error = searchStatus.phase === 'failed' ? searchStatus.reason : ''
const isCreditError = searchStatus.phase === 'failed' && searchStatus.isCreditError
```

- [ ] **Step 4: Verify the file compiles**

Run: `cd frontend && npx next build --no-lint 2>&1 | tail -30`

This should succeed because the derived booleans preserve the same interface that the rendering code expects. If there are type errors, fix them before proceeding.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/job/steps/AudioSourceStep.tsx
git commit -m "refactor: replace search booleans with SearchStatus state machine type"
```

---

### Task 2: Rewrite doSearch with State Machine and Retry Logic

**Files:**
- Modify: `frontend/components/job/steps/AudioSourceStep.tsx:140-165`

- [ ] **Step 1: Add a helper to classify whether an error is retryable**

Add immediately after the `RETRY_DELAYS` constant:

```ts
/** Returns true for transient errors that should be auto-retried */
function isRetryableError(err: unknown): boolean {
  // Network errors (TypeError: Failed to fetch, connection refused, CORS)
  if (!(err instanceof ApiError)) return true
  // Server errors (5xx) are retryable
  if (err.status >= 500) return true
  // Everything else (4xx) is not
  return false
}
```

- [ ] **Step 2: Replace the doSearch function**

Replace lines 140-165 (the entire `doSearch` useCallback) with:

```ts
// Search with automatic retry for transient failures
const doSearch = useCallback(async (attempt = 1): Promise<void> => {
  setSearchStatus({ phase: 'searching', attempt })

  // Delay before retry attempts
  if (attempt > 1) {
    await new Promise(r => setTimeout(r, RETRY_DELAYS[attempt - 1] ?? RETRY_DELAYS[RETRY_DELAYS.length - 1]))
  }

  try {
    const response = await api.searchStandalone(artist, title)
    onSearchCompleted(response.search_session_id)
    setResults(response.results as ExtendedAudioSearchResult[])
    setSearchStatus({ phase: 'succeeded' })
  } catch (err) {
    // Credit errors — never retry
    if (err instanceof ApiError && err.status === 402) {
      setSearchStatus({
        phase: 'failed',
        reason: "You're out of credits. Buy more to continue creating karaoke videos.",
        isCreditError: true,
        attempt,
      })
      return
    }

    // Retryable errors — try again if attempts remain
    if (isRetryableError(err) && attempt < MAX_SEARCH_ATTEMPTS) {
      return doSearch(attempt + 1)
    }

    // All retries exhausted or non-retryable error — show failure
    let reason: string
    if (err instanceof ApiError) {
      reason = err.message
    } else if (err instanceof DOMException && err.name === 'AbortError') {
      reason = "Search timed out. Please try again."
    } else {
      reason = "Search failed due to a network error. Please try again."
    }

    setSearchStatus({ phase: 'failed', reason, isCreditError: false, attempt })
  }
}, [artist, title, onSearchCompleted])
```

Note: the `finally { setIsSearching(false) }` is gone — the state machine handles it. Every path through `doSearch` now explicitly sets `searchStatus` to either `succeeded` or `failed`.

- [ ] **Step 3: Verify the file compiles**

Run: `cd frontend && npx next build --no-lint 2>&1 | tail -30`

- [ ] **Step 4: Commit**

```bash
git add frontend/components/job/steps/AudioSourceStep.tsx
git commit -m "feat: rewrite doSearch with state machine and auto-retry (up to 3 attempts)"
```

---

### Task 3: Update handleFuzzyAccept to Use State Machine

**Files:**
- Modify: `frontend/components/job/steps/AudioSourceStep.tsx:225-265` (the `handleFuzzyAccept` function)

The fuzzy accept has its own inline search call that bypasses `doSearch`. It needs to use the state machine too.

- [ ] **Step 1: Update handleFuzzyAccept state resets**

In the `handleFuzzyAccept` function, replace the state reset block (lines ~230-243):

```ts
setFuzzySuggestion(null)
setFuzzyAlternatives([])
setFuzzyDismissed(true)
setResults([])
setIsSearching(true)
setError("")
setCatalogResults([])
setCatalogDismissed(false)
setCommunityData(null)
setCommunityDismissed(false)
setShowOtherOptions(false)
searchTriggered.current = false
fuzzyTriggered.current = false
```

With:

```ts
setFuzzySuggestion(null)
setFuzzyAlternatives([])
setFuzzyDismissed(true)
setResults([])
setSearchStatus({ phase: 'searching', attempt: 1 })
setCatalogResults([])
setCatalogDismissed(false)
setCommunityData(null)
setCommunityDismissed(false)
setShowOtherOptions(false)
searchTriggered.current = false
fuzzyTriggered.current = false
```

- [ ] **Step 2: Update the inline search call's catch/finally**

In the same function, replace the `.catch` and `.finally` in the microtask (lines ~254-258):

```ts
.catch((err) => {
  if (err instanceof ApiError) setError(err.message)
  else setError("Search failed. Please try again.")
})
.finally(() => setIsSearching(false))
```

With:

```ts
.then(() => {
  setSearchStatus({ phase: 'succeeded' })
})
.catch((err) => {
  const reason = err instanceof ApiError
    ? err.message
    : "Search failed. Please try again."
  setSearchStatus({ phase: 'failed', reason, isCreditError: false, attempt: 1 })
})
```

Note: remove the `.finally(() => setIsSearching(false))` — the state machine handles it in both `.then` and `.catch`.

- [ ] **Step 3: Verify the file compiles**

Run: `cd frontend && npx next build --no-lint 2>&1 | tail -30`

- [ ] **Step 4: Commit**

```bash
git add frontend/components/job/steps/AudioSourceStep.tsx
git commit -m "refactor: update handleFuzzyAccept to use SearchStatus state machine"
```

---

### Task 4: Update the No-Results Guard and Retry Button

**Files:**
- Modify: `frontend/components/job/steps/AudioSourceStep.tsx:439-455` (error display), `529-531` (no-results)

- [ ] **Step 1: Update the no-results conditional**

The current line 529:

```tsx
{!isSearching && results.length === 0 && !error && (
  <NoResultsSection />
)}
```

Replace with:

```tsx
{searchStatus.phase === 'succeeded' && results.length === 0 && (
  <NoResultsSection />
)}
```

This is the **key safety change** — `NoResultsSection` now requires an explicit `succeeded` phase, not just the absence of an error string.

- [ ] **Step 2: Update the retry button in the error display**

In the error display section (around line 448), the retry button currently does:

```tsx
<button onClick={() => { searchTriggered.current = false; doSearch() }} ...>Retry</button>
```

This still works because `doSearch()` defaults to `attempt = 1`. No change needed here — the derived `error` and `isCreditError` booleans keep the error display working.

- [ ] **Step 3: Verify the file compiles**

Run: `cd frontend && npx next build --no-lint 2>&1 | tail -30`

- [ ] **Step 4: Commit**

```bash
git add frontend/components/job/steps/AudioSourceStep.tsx
git commit -m "fix: only show NoResultsSection when search explicitly succeeded with zero results"
```

---

### Task 5: Update Spinner to Show Retry Progress

**Files:**
- Modify: `frontend/components/job/steps/AudioSourceStep.tsx:458-467` (loading state)

- [ ] **Step 1: Update the spinner section**

Replace the loading state block (lines 458-467):

```tsx
{isSearching && (
  <div className="flex flex-col items-center justify-center py-12 gap-3">
    <Loader2 className="w-8 h-8 animate-spin" style={{ color: 'var(--brand-pink)' }} />
    <div className="text-center">
      <p className="text-sm font-medium" style={{ color: 'var(--text)' }}>Searching for audio sources...</p>
      <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
        This usually takes 15-30 seconds
      </p>
    </div>
  </div>
)}
```

With:

```tsx
{searchStatus.phase === 'searching' && (
  <div className="flex flex-col items-center justify-center py-12 gap-3">
    <Loader2 className="w-8 h-8 animate-spin" style={{ color: 'var(--brand-pink)' }} />
    <div className="text-center">
      <p className="text-sm font-medium" style={{ color: 'var(--text)' }}>Searching for audio sources...</p>
      <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
        {searchStatus.attempt > 1
          ? `Retry ${searchStatus.attempt - 1} of ${MAX_SEARCH_ATTEMPTS - 1} — this usually takes 15-30 seconds`
          : 'This usually takes 15-30 seconds'}
      </p>
    </div>
  </div>
)}
```

- [ ] **Step 2: Verify the file compiles**

Run: `cd frontend && npx next build --no-lint 2>&1 | tail -30`

- [ ] **Step 3: Commit**

```bash
git add frontend/components/job/steps/AudioSourceStep.tsx
git commit -m "feat: show retry progress in search spinner"
```

---

### Task 6: Update Remaining Conditionals to Use searchStatus.phase

**Files:**
- Modify: `frontend/components/job/steps/AudioSourceStep.tsx`

The derived `isSearching` boolean handles most rendering conditions, but a few spots should use `searchStatus.phase` directly for clarity and safety.

- [ ] **Step 1: Update the fallback section conditional**

Line 534:

```tsx
{!isSearching && (confidence.tier !== 3 || results.length === 0) && (
```

Replace with:

```tsx
{searchStatus.phase !== 'searching' && searchStatus.phase !== 'idle' && (confidence.tier !== 3 || results.length === 0) && (
```

This prevents the fallback section (upload/YouTube URL) from showing during the `idle` phase. In practice `idle` is never reached since we initialize to `searching`, but it's defensive.

- [ ] **Step 2: Verify no other references to the old state setters remain**

Search for any remaining direct calls to `setIsSearching`, `setError`, or `setIsCreditError`:

Run: `grep -n 'setIsSearching\|setError\|setIsCreditError' frontend/components/job/steps/AudioSourceStep.tsx`

If any remain, replace them with appropriate `setSearchStatus` calls. (Tasks 1-3 should have caught them all.)

- [ ] **Step 3: Verify the file compiles**

Run: `cd frontend && npx next build --no-lint 2>&1 | tail -30`

- [ ] **Step 4: Commit**

```bash
git add frontend/components/job/steps/AudioSourceStep.tsx
git commit -m "refactor: use searchStatus.phase in remaining conditionals"
```

---

### Task 7: Manual Smoke Test

**Files:** None (testing only)

- [ ] **Step 1: Start the dev server**

Run: `cd frontend && npx next dev`

- [ ] **Step 2: Test the happy path**

Navigate to the Create Karaoke Video flow, enter an artist and title that has results (e.g., "Queen - Bohemian Rhapsody"). Verify:
- Spinner appears with "Searching for audio sources..."
- Results appear after search completes
- No flash of "No audio sources found" before results load

- [ ] **Step 3: Test no-results path**

Enter a made-up song (e.g., "Asdfghjkl - Zxcvbnm"). Verify:
- Spinner appears
- "No audio sources found" appears (correctly this time — search succeeded with 0 results)
- Upload/YouTube fallback options are available

- [ ] **Step 4: Test error path (simulate network failure)**

In browser DevTools, go to Network tab, set throttling to "Offline" after clicking to the Choose Audio step. Verify:
- Spinner shows retry progress ("Retry 1 of 2", "Retry 2 of 2")
- After all retries, an error banner appears — NOT "No audio sources found"
- Retry button is present and works when back online

- [ ] **Step 5: Commit any fixes from smoke test**

If anything needed fixing:
```bash
git add frontend/components/job/steps/AudioSourceStep.tsx
git commit -m "fix: address issues found during smoke test"
```
