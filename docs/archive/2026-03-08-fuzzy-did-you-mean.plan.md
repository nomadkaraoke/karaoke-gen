# Plan: Fuzzy "Did you mean?" correction for typos

**Created:** 2026-03-08
**Branch:** feat/sess-20260308-0139-step2-song-suggestions
**Status:** Draft

## Overview

When a user types a misspelled song title (e.g. "ruises" instead of "Bruises"), the audio search returns poor results (Tier 3 / no lossless) and the catalog search returns nothing. The user has no signal they made a typo. We add a second-pass fuzzy search that triggers when results are poor: fetch the artist's discography from the catalog, fuzzy-match against the user's title, and show a "Did you mean X?" banner. Clicking "Yes" corrects the title and restarts the audio search.

## Technical Approach

1. **Trigger condition:** Audio search completes with Tier 3 or no results AND catalog search returned 0 results
2. **Fuzzy search:** Call `api.searchCatalogTracks(artist, artist, 20)` — search the artist's name as the query with artist filter to get their top tracks. This is a broader search that doesn't require matching the title.
3. **Client-side fuzzy match:** Compare each returned track name against the user's title using simple Levenshtein-like similarity (normalized). Pick the best match above a threshold (e.g. 0.6 similarity).
4. **"Did you mean?" banner:** Prominent amber banner with the suggested correction. "Yes, search again" button restarts audio search with corrected artist/title.
5. **Restart flow:** Reset search state, update artist/title via parent callback, re-trigger `doSearch()`.

## Fuzzy Matching Algorithm

Simple approach — no external dependency needed:
- Lowercase both strings
- Calculate longest common subsequence (LCS) ratio: `2 * LCS_length / (len(a) + len(b))`
- Also check if user's title is a substring of the track name or vice versa
- Threshold: 0.6 for LCS ratio, or substring match with length >= 3
- Pick the highest-scoring match

## Files to Modify

| File | Action | Description |
|------|--------|-------------|
| `frontend/components/job/steps/AudioSourceStep.tsx` | Modify | Add fuzzy search trigger, "Did you mean?" banner, restart logic |

## Implementation Steps

1. Add `fuzzyMatch()` utility function (inline in AudioSourceStep)
2. Add state for fuzzy suggestion (`fuzzySuggestion`, `fuzzyDismissed`)
3. Add `useEffect` that triggers fuzzy search when audio search completes with poor results + no catalog results
4. Add `DidYouMeanBanner` component
5. Add restart handler that clears state and re-runs search with corrected name
