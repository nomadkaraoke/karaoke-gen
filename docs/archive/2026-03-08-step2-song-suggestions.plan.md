# Plan: Move Song DB Suggestions from Step 1 to Step 2

**Created:** 2026-03-08
**Branch:** feat/sess-20260308-0139-step2-song-suggestions
**Status:** Implemented

## Overview

The autocomplete on Step 1's title field is confusing — users don't know why they should wait for it or select a suggestion. Instead, we'll remove autocomplete from Step 1 (making it a simple text entry) and show song DB suggestions in Step 2 while the audio search is loading. This uses the ~15-30s wait time productively and presents suggestions with clear context about what they are and why.

## Requirements

- [x] Remove `AutocompleteInput` from Step 1 title field → plain `<Input>`
- [x] Keep community version check in Step 1 (non-blocking, useful)
- [x] Fire `api.searchCatalogTracks()` in parallel with `api.searchStandalone()` on Step 2 mount
- [x] Show catalog results in Step 2 as a "song name correction" panel
- [x] Clicking a suggestion updates artist/title (the search artist/title used for job creation)
- [x] Panel is dismissible, doesn't appear if no results
- [x] Catalog search failure is silent (doesn't affect audio search)
- [x] Works well on mobile

## Technical Approach

**No new API endpoints needed** — we already have `api.searchCatalogTracks()`.

**Data flow change:** In Step 2, `AudioSourceStep` gets two new props:
- `onArtistTitleCorrection(artist: string, title: string)` — called when user picks a suggestion
- The parent (`GuidedJobFlow`) updates `artist`/`title` state, which re-renders the "Searching for X - Y" header

**Key decision:** When the user corrects artist/title via catalog suggestion, this updates the *search* artist/title (used in `createJobFromSearch`). It does NOT restart the audio search — that would be confusing and waste time. The audio search already happened with the original terms.

## Implementation Steps

### Step 1: Simplify SongInfoStep (remove autocomplete)
1. [x] Replace `AutocompleteInput` with plain `<Input>` for the title field
2. [x] Remove `fetchTrackSuggestions` callback and `AutocompleteInput` import
3. [x] Keep all other functionality (community check, tips, form validation)

### Step 2: Add catalog search to AudioSourceStep
4. [x] Add `onArtistTitleCorrection` prop to `AudioSourceStepProps`
5. [x] Add state: `catalogResults`, `catalogLoading`, `catalogDismissed`
6. [x] Fire `api.searchCatalogTracks(title, artist, 5)` in parallel with `doSearch()` in the mount effect
7. [x] Build `SongSuggestionPanel` inline component showing results

### Step 3: Wire up GuidedJobFlow
8. [x] Add handler in `GuidedJobFlow` that updates `artist`/`title` when catalog suggestion selected
9. [x] Pass handler to `AudioSourceStep` as `onArtistTitleCorrection`

### Step 4: Testing
10. [ ] Run existing E2E tests to verify no regressions
11. [ ] Manual visual check in dev server

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `frontend/components/job/steps/SongInfoStep.tsx` | Modify | Remove AutocompleteInput, use plain Input for title |
| `frontend/components/job/steps/AudioSourceStep.tsx` | Modify | Add parallel catalog search + suggestion panel |
| `frontend/components/job/GuidedJobFlow.tsx` | Modify | Add artist/title correction handler, pass to Step 2 |

## SongSuggestionPanel Design

```
┌─────────────────────────────────────────────┐
│ 🔍 Found matching songs                     │
│ Click to use the official formatting:        │
│                                              │
│  ┌─ Fox Stevenson - Don't Care Crown ──── ┐ │
│  └──────────────────────────────────────── ┘ │
│  ┌─ Fox Stevenson - Don't Care Crown... ─ ┐ │
│  └──────────────────────────────────────── ┘ │
│                                    Dismiss ↗ │
└─────────────────────────────────────────────┘
```

- Appears between the "Searching for X - Y" header and the loading spinner
- Uses existing design language (card with border, muted colors)
- Each result is a clickable row showing `Artist - Track` with a subtle "Use" button
- When selected: row highlights briefly, artist/title update, panel auto-dismisses
- "Dismiss" link at bottom to hide permanently
- If already matches exactly, skip showing that result

## Rollback Plan

Revert the branch. No backend changes, no data migrations.
