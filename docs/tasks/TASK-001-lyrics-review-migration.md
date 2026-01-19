# Task: Migrate Lyrics Review Components to Main Frontend

**Task ID**: TASK-001
**Status**: Ready for execution
**Estimated scope**: ~14,000 lines, 39 components
**Approach**: Big bang migration from MUI to Radix/Tailwind

## Context

We're consolidating three separate frontends into a single Next.js application. This task focuses on migrating the Lyrics Review frontend (currently React + Vite + MUI) into the main karaoke-gen frontend (Next.js + Radix/Tailwind).

### Current State
- **Source**: `lyrics_transcriber_temp/lyrics_transcriber/frontend/src/`
- **Target**: `frontend/components/lyrics-review/` and `frontend/lib/lyrics-review/`
- **Placeholder exists at**: `frontend/app/app/jobs/[[...slug]]/client.tsx` (search for `LyricsReviewPlaceholder`)

### Goal
Replace `LyricsReviewPlaceholder` with the actual migrated `LyricsAnalyzer` component, fully converted from MUI to Radix/Tailwind.

## Design System Mapping

The main frontend uses **shadcn/ui** (Radix primitives + Tailwind). Available components are in `frontend/components/ui/`.

### MUI → shadcn/Tailwind Mapping

| MUI Component | shadcn Equivalent | Notes |
|---------------|-------------------|-------|
| `Button` | `components/ui/button.tsx` | Use `variant` prop |
| `IconButton` | `Button` with `variant="ghost" size="icon"` | |
| `Dialog` | `components/ui/dialog.tsx` | |
| `TextField` | `components/ui/input.tsx` | |
| `Select` | `components/ui/select.tsx` | |
| `Slider` | `components/ui/slider.tsx` | |
| `Tabs`, `Tab` | `components/ui/tabs.tsx` | |
| `Tooltip` | `components/ui/tooltip.tsx` | |
| `CircularProgress` | `components/ui/spinner.tsx` | |
| `Snackbar` | `sonner` toast (already configured) | Use `toast()` from sonner |
| `Box` | `<div>` with Tailwind classes | |
| `Typography` | Tailwind text classes | `text-sm`, `font-semibold`, etc. |
| `Paper` | `components/ui/card.tsx` | |
| `Accordion` | `components/ui/accordion.tsx` | |
| `Checkbox` | `components/ui/checkbox.tsx` | |
| `Switch` | `components/ui/switch.tsx` | |
| `Menu`, `MenuItem` | `components/ui/dropdown-menu.tsx` | |
| `Popover` | `components/ui/popover.tsx` | |
| `LinearProgress` | `components/ui/progress.tsx` | |

### Theme/Styling Mapping

**MUI Theme → Tailwind CSS Variables:**
```
theme.palette.primary.main    → text-primary, bg-primary
theme.palette.secondary.main  → text-secondary, bg-secondary
theme.palette.error.main      → text-destructive, bg-destructive
theme.palette.background.paper → bg-card
theme.palette.text.primary    → text-foreground
theme.palette.text.secondary  → text-muted-foreground
theme.spacing(2)              → Tailwind spacing (p-2, m-2, gap-2)
```

**MUI sx prop → Tailwind classes:**
```jsx
// Before (MUI)
<Box sx={{ display: 'flex', gap: 2, p: 1, bgcolor: 'background.paper' }}>

// After (Tailwind)
<div className="flex gap-2 p-1 bg-card">
```

**Emotion styled → Tailwind:**
```jsx
// Before (Emotion)
const StyledButton = styled(Button)`
  background-color: #1976d2;
  &:hover { background-color: #1565c0; }
`;

// After (Tailwind)
<Button className="bg-blue-600 hover:bg-blue-700">
```

## Migration Order (Dependency-Based)

Migrate in this order to minimize broken imports:

### Tier 1: Core Types & Utilities (No UI - copy with minimal changes)

| File | Source | Target | Notes |
|------|--------|--------|-------|
| `types.ts` | `src/types.ts` | `lib/lyrics-review/types.ts` | Copy as-is |
| `validation.ts` | `src/validation.ts` | `lib/lyrics-review/validation.ts` | Copy as-is |
| `shared/types.ts` | `src/components/shared/types.ts` | Merge into `types.ts` | |
| `shared/constants.ts` | `src/components/shared/constants.ts` | `lib/lyrics-review/constants.ts` | |

### Tier 2: Utility Functions (No UI)

| File | Source | Target |
|------|--------|--------|
| `localStorage.ts` | `src/components/shared/utils/localStorage.ts` | `lib/lyrics-review/utils/localStorage.ts` |
| `keyboardHandlers.ts` | `src/components/shared/utils/keyboardHandlers.ts` | `lib/lyrics-review/utils/keyboardHandlers.ts` |
| `segmentOperations.ts` | `src/components/shared/utils/segmentOperations.ts` | `lib/lyrics-review/utils/segmentOperations.ts` |
| `timingUtils.ts` | `src/components/shared/utils/timingUtils.ts` | `lib/lyrics-review/utils/timingUtils.ts` |
| `wordUtils.ts` | `src/components/shared/utils/wordUtils.ts` | `lib/lyrics-review/utils/wordUtils.ts` |
| `referenceLineCalculator.ts` | `src/components/shared/utils/referenceLineCalculator.ts` | `lib/lyrics-review/utils/referenceLineCalculator.ts` |

### Tier 3: Base Components (Simple UI)

| Component | Source | Target | Complexity |
|-----------|--------|--------|------------|
| `Word.tsx` | `src/components/shared/components/Word.tsx` | `components/lyrics-review/shared/Word.tsx` | Low |
| `HighlightedText.tsx` | `src/components/shared/components/HighlightedText.tsx` | `components/lyrics-review/shared/HighlightedText.tsx` | Low |
| `SourceSelector.tsx` | `src/components/shared/components/SourceSelector.tsx` | `components/lyrics-review/shared/SourceSelector.tsx` | Medium |
| `AudioPlayer.tsx` | `src/components/AudioPlayer.tsx` | `components/lyrics-review/AudioPlayer.tsx` | Medium |

### Tier 4: View Components (Complex UI)

| Component | Source | Target | Complexity |
|-----------|--------|--------|------------|
| `TranscriptionView.tsx` | `src/components/TranscriptionView.tsx` | `components/lyrics-review/TranscriptionView.tsx` | High |
| `ReferenceView.tsx` | `src/components/ReferenceView.tsx` | `components/lyrics-review/ReferenceView.tsx` | Medium |
| `DurationTimelineView.tsx` | `src/components/DurationTimelineView.tsx` | `components/lyrics-review/DurationTimelineView.tsx` | Medium |
| `EditWordList.tsx` | `src/components/EditWordList.tsx` | `components/lyrics-review/EditWordList.tsx` | High |
| `TimelineEditor.tsx` | `src/components/TimelineEditor.tsx` | `components/lyrics-review/TimelineEditor.tsx` | High |

### Tier 5: Modal Components

| Component | Source | Target | Complexity |
|-----------|--------|--------|------------|
| `FindReplaceModal.tsx` | `src/components/FindReplaceModal.tsx` | `components/lyrics-review/modals/FindReplaceModal.tsx` | Medium |
| `ReviewChangesModal.tsx` | `src/components/ReviewChangesModal.tsx` | `components/lyrics-review/modals/ReviewChangesModal.tsx` | Medium |
| `SegmentDetailsModal.tsx` | `src/components/SegmentDetailsModal.tsx` | `components/lyrics-review/modals/SegmentDetailsModal.tsx` | Medium |
| `ReplaceAllLyricsModal.tsx` | `src/components/ReplaceAllLyricsModal.tsx` | `components/lyrics-review/modals/ReplaceAllLyricsModal.tsx` | Medium |
| `TimingOffsetModal.tsx` | `src/components/TimingOffsetModal.tsx` | `components/lyrics-review/modals/TimingOffsetModal.tsx` | Low |
| `AddLyricsModal.tsx` | `src/components/AddLyricsModal.tsx` | `components/lyrics-review/modals/AddLyricsModal.tsx` | Medium |
| `ModeSelectionModal.tsx` | `src/components/ModeSelectionModal.tsx` | `components/lyrics-review/modals/ModeSelectionModal.tsx` | Low |
| `CorrectionAnnotationModal.tsx` | `src/components/CorrectionAnnotationModal.tsx` | `components/lyrics-review/modals/CorrectionAnnotationModal.tsx` | Medium |
| `AIFeedbackModal.tsx` | `src/components/AIFeedbackModal.tsx` | `components/lyrics-review/modals/AIFeedbackModal.tsx` | Medium |

### Tier 6: Lyrics Synchronizer (Complex)

| Component | Source | Target | Complexity |
|-----------|--------|--------|------------|
| `index.tsx` | `src/components/LyricsSynchronizer/index.tsx` | `components/lyrics-review/synchronizer/index.tsx` | High |
| `TimelineCanvas.tsx` | `src/components/LyricsSynchronizer/TimelineCanvas.tsx` | `components/lyrics-review/synchronizer/TimelineCanvas.tsx` | High |
| `SyncControls.tsx` | `src/components/LyricsSynchronizer/SyncControls.tsx` | `components/lyrics-review/synchronizer/SyncControls.tsx` | Medium |
| `UpcomingWordsBar.tsx` | `src/components/LyricsSynchronizer/UpcomingWordsBar.tsx` | `components/lyrics-review/synchronizer/UpcomingWordsBar.tsx` | Low |

### Tier 7: Main Container & Supporting

| Component | Source | Target | Complexity |
|-----------|--------|--------|------------|
| `LyricsAnalyzer.tsx` | `src/components/LyricsAnalyzer.tsx` | `components/lyrics-review/LyricsAnalyzer.tsx` | Very High |
| `Header.tsx` | `src/components/Header.tsx` | `components/lyrics-review/Header.tsx` | Medium |
| `EditActionBar.tsx` | `src/components/EditActionBar.tsx` | `components/lyrics-review/EditActionBar.tsx` | Medium |
| `CorrectedWordWithActions.tsx` | `src/components/CorrectedWordWithActions.tsx` | `components/lyrics-review/CorrectedWordWithActions.tsx` | Medium |
| `PreviewVideoSection.tsx` | `src/components/PreviewVideoSection.tsx` | `components/lyrics-review/PreviewVideoSection.tsx` | Medium |

### Tier 8: Metrics & Dashboard

| Component | Source | Target | Complexity |
|-----------|--------|--------|------------|
| `CorrectionMetrics.tsx` | `src/components/CorrectionMetrics.tsx` | `components/lyrics-review/CorrectionMetrics.tsx` | Low |
| `AgenticCorrectionMetrics.tsx` | `src/components/AgenticCorrectionMetrics.tsx` | `components/lyrics-review/AgenticCorrectionMetrics.tsx` | Low |
| `MetricsDashboard.tsx` | `src/components/MetricsDashboard.tsx` | `components/lyrics-review/MetricsDashboard.tsx` | Medium |

## API Integration

The current Lyrics Review frontend has its own API client at `src/api.ts` (LiveApiClient). This needs to be integrated into the main frontend's API client.

### Current API Endpoints Used

```typescript
// From lyrics_transcriber_temp/.../api.ts
GET  {baseApiUrl}/correction-data     → Get lyrics correction data
POST {baseApiUrl}/corrections         → Submit corrections
GET  {baseApiUrl}/metadata            → Get job metadata
POST {baseApiUrl}/generate-preview    → Generate preview video
GET  {baseApiUrl}/preview-status      → Check preview generation status
```

### Integration Approach

1. Add these endpoints to `frontend/lib/api.ts`
2. Update components to use the main API client instead of LiveApiClient
3. Remove token-based auth (now using job ownership model)

**Add to `frontend/lib/api.ts`:**
```typescript
// Lyrics Review endpoints
async getLyricsReviewData(jobId: string): Promise<CorrectionData> {
  const response = await fetch(`${API_BASE_URL}/api/review/${jobId}/correction-data`, {
    headers: getAuthHeaders()
  });
  return handleResponse(response);
},

async submitLyricsCorrections(jobId: string, corrections: CorrectionData): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/review/${jobId}/corrections`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(corrections),
  });
  return handleResponse(response);
},

async generatePreview(jobId: string, corrections: CorrectionData): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE_URL}/api/review/${jobId}/generate-preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(corrections),
  });
  return handleResponse(response);
},

async getPreviewStatus(jobId: string): Promise<{ status: string; preview_url?: string }> {
  const response = await fetch(`${API_BASE_URL}/api/review/${jobId}/preview-status`, {
    headers: getAuthHeaders()
  });
  return handleResponse(response);
},
```

## Target Directory Structure

```
frontend/
├── components/
│   └── lyrics-review/
│       ├── LyricsAnalyzer.tsx        # Main container
│       ├── Header.tsx
│       ├── AudioPlayer.tsx
│       ├── TranscriptionView.tsx
│       ├── ReferenceView.tsx
│       ├── DurationTimelineView.tsx
│       ├── EditWordList.tsx
│       ├── TimelineEditor.tsx
│       ├── EditActionBar.tsx
│       ├── CorrectedWordWithActions.tsx
│       ├── PreviewVideoSection.tsx
│       ├── CorrectionMetrics.tsx
│       ├── AgenticCorrectionMetrics.tsx
│       ├── MetricsDashboard.tsx
│       ├── modals/
│       │   ├── FindReplaceModal.tsx
│       │   ├── ReviewChangesModal.tsx
│       │   ├── SegmentDetailsModal.tsx
│       │   ├── ReplaceAllLyricsModal.tsx
│       │   ├── TimingOffsetModal.tsx
│       │   ├── AddLyricsModal.tsx
│       │   ├── ModeSelectionModal.tsx
│       │   ├── CorrectionAnnotationModal.tsx
│       │   └── AIFeedbackModal.tsx
│       ├── synchronizer/
│       │   ├── index.tsx
│       │   ├── TimelineCanvas.tsx
│       │   ├── SyncControls.tsx
│       │   └── UpcomingWordsBar.tsx
│       └── shared/
│           ├── Word.tsx
│           ├── HighlightedText.tsx
│           └── SourceSelector.tsx
└── lib/
    └── lyrics-review/
        ├── types.ts
        ├── validation.ts
        ├── constants.ts
        └── utils/
            ├── localStorage.ts
            ├── keyboardHandlers.ts
            ├── segmentOperations.ts
            ├── timingUtils.ts
            ├── wordUtils.ts
            └── referenceLineCalculator.ts
```

## Acceptance Criteria

1. **Build passes**: `cd frontend && npm run build` succeeds
2. **No MUI imports**: No `@mui/*` or `@emotion/*` imports in migrated code
3. **Placeholder replaced**: `LyricsReviewPlaceholder` in `client.tsx` replaced with actual `LyricsAnalyzer`
4. **Visual parity**: UI looks similar to current (doesn't need to be pixel-perfect)
5. **Functionality works**:
   - Can load correction data for a job
   - Can edit lyrics (add, delete, modify words)
   - Can adjust timing
   - Can submit corrections
   - Preview video generation works
6. **Tests pass**: Any existing tests continue to pass

## Execution Notes

### Suggested Sub-Tasks

Given the scope, consider breaking this into multiple agent sessions:

1. **Session A**: Tier 1-2 (types, utilities) - Quick, no UI work
2. **Session B**: Tier 3-4 (base + view components) - Medium complexity
3. **Session C**: Tier 5 (modals) - Repetitive, medium complexity
4. **Session D**: Tier 6 (synchronizer) - Complex, self-contained
5. **Session E**: Tier 7-8 + integration - Main container, final assembly

### Common Patterns to Watch For

1. **MUI `sx` prop**: Convert to Tailwind classes
2. **Emotion `styled`**: Convert to Tailwind classes or component variants
3. **MUI theme access**: Replace `theme.palette.*` with Tailwind colors
4. **MUI icons**: Already using Lucide icons (same as main frontend)
5. **MUI `useTheme`**: Remove, use Tailwind dark mode classes

### Testing During Migration

After each tier, verify:
```bash
cd frontend
npm run build  # Should pass
npm run dev    # Should start without errors
```

## Related Files

- **Plan document**: `docs/archive/2026-01-09-frontend-consolidation-plan.md`
- **Brand style guide**: `docs/BRAND-STYLE-GUIDE.md`
- **Testing guide**: `docs/TESTING.md`

## Questions to Resolve During Execution

1. **Audio waveform**: Current uses a custom canvas implementation. Keep or find Tailwind-compatible library?
2. **Complex canvas components**: TimelineCanvas, TimelineEditor - may need to keep as-is with minimal styling changes
3. **Keyboard shortcuts**: Current uses custom hook - ensure it works in Next.js context
