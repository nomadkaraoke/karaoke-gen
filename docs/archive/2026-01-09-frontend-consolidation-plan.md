# Frontend Consolidation Plan

**Date**: 2026-01-09
**Branch**: `feat/sess-20260109-1931-consolidate-frontends`
**Approach**: Big bang migration

## Goal

Consolidate three separate frontends into a single Next.js application:

| Current | Lines | Location | After |
|---------|-------|----------|-------|
| Main karaoke-gen | ~10k | `frontend/` | Keep, extend |
| Lyrics Review | ~14,300 | `lyrics_transcriber_temp/.../frontend/` | Migrate to `frontend/` |
| Instrumental Review | ~1,700 | `karaoke_gen/instrumental_review/static/` | Migrate to `frontend/` |

**Benefits**:
- Single deployment (Cloudflare Pages only)
- Seamless UX (no external redirects)
- Unified codebase and build system
- Consistent design system (Radix + Tailwind)

## Current Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ gen.nomadkaraoke.com (Cloudflare Pages)                     │
│ └── Next.js main frontend                                   │
│     ├── /app → Job dashboard                                │
│     ├── /admin → Admin panel                                │
│     └── Opens external URLs for review...                   │
└─────────────────────────────────────────────────────────────┘
        │                               │
        ▼                               ▼
┌───────────────────────┐   ┌───────────────────────┐
│ GitHub Pages          │   │ Backend (Cloud Run)   │
│ Lyrics Review (Vite)  │   │ Instrumental (static) │
│ MUI + Emotion         │   │ Vanilla HTML/JS       │
└───────────────────────┘   └───────────────────────┘
```

## Target Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ gen.nomadkaraoke.com (Cloudflare Pages)                     │
│ └── Next.js unified frontend                                │
│     ├── /app → Job dashboard                                │
│     ├── /app/jobs/[id]/review → Lyrics review (embedded)    │
│     ├── /app/jobs/[id]/instrumental → Instrumental select   │
│     └── /admin → Admin panel                                │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
                ┌───────────────────────┐
                │ Backend (Cloud Run)   │
                │ API only (no static)  │
                └───────────────────────┘
```

## Migration Phases

### Phase 1: Setup & Routing Structure

**Tasks**:
1. Create new Next.js routes:
   - `frontend/app/app/jobs/[id]/review/page.tsx`
   - `frontend/app/app/jobs/[id]/instrumental/page.tsx`

2. Update JobCard navigation to use internal routes instead of external URLs

3. Add temporary wrapper components that will host migrated content

**Files to create**:
```
frontend/app/app/jobs/[id]/
├── review/
│   └── page.tsx           # Lyrics review route
└── instrumental/
    └── page.tsx           # Instrumental selection route
```

### Phase 2: Migrate Lyrics Review Components

**Source**: `lyrics_transcriber_temp/lyrics_transcriber/frontend/src/`

**Components to migrate** (in dependency order):

#### Tier 1: Core utilities (keep as-is, move location)
- `types.ts` → `frontend/lib/lyrics-review/types.ts`
- `validation.ts` → `frontend/lib/lyrics-review/validation.ts`
- `api.ts` → integrate into `frontend/lib/api.ts`

#### Tier 2: Shared utilities
- `components/shared/utils/*.ts` → `frontend/lib/lyrics-review/utils/`
- `components/shared/types.ts` → merge with types.ts
- `components/shared/constants.ts` → `frontend/lib/lyrics-review/constants.ts`

#### Tier 3: Base components (MUI → Radix/Tailwind)
- `AudioPlayer.tsx` - Audio playback with waveform
- `Word.tsx` - Individual word display
- `HighlightedText.tsx` - Text highlighting
- `SourceSelector.tsx` - Source selection dropdown

#### Tier 4: View components
- `TranscriptionView.tsx` - Main transcription display
- `ReferenceView.tsx` - Reference lyrics panel
- `DurationTimelineView.tsx` - Timeline visualization
- `EditWordList.tsx` - Word list editor
- `TimelineEditor.tsx` - Timeline editing

#### Tier 5: Modal components
- `FindReplaceModal.tsx`
- `ReviewChangesModal.tsx`
- `SegmentDetailsModal.tsx`
- `ReplaceAllLyricsModal.tsx`
- `TimingOffsetModal.tsx`
- `AddLyricsModal.tsx`
- `ModeSelectionModal.tsx`
- `CorrectionAnnotationModal.tsx`
- `AIFeedbackModal.tsx`

#### Tier 6: Lyrics Synchronizer
- `LyricsSynchronizer/index.tsx`
- `LyricsSynchronizer/TimelineCanvas.tsx`
- `LyricsSynchronizer/SyncControls.tsx`
- `LyricsSynchronizer/UpcomingWordsBar.tsx`

#### Tier 7: Main container
- `LyricsAnalyzer.tsx` - Main orchestrator component
- `AppHeader.tsx` - Header (merge with existing)
- `Header.tsx` - Review header

#### Tier 8: Metrics/Dashboard
- `CorrectionMetrics.tsx`
- `AgenticCorrectionMetrics.tsx`
- `MetricsDashboard.tsx`
- `PreviewVideoSection.tsx`

**Target structure**:
```
frontend/
├── components/
│   └── lyrics-review/
│       ├── LyricsAnalyzer.tsx      # Main container
│       ├── AudioPlayer.tsx
│       ├── TranscriptionView.tsx
│       ├── ReferenceView.tsx
│       ├── TimelineEditor.tsx
│       ├── modals/
│       │   ├── FindReplaceModal.tsx
│       │   ├── ReviewChangesModal.tsx
│       │   └── ...
│       ├── synchronizer/
│       │   ├── index.tsx
│       │   ├── TimelineCanvas.tsx
│       │   └── ...
│       └── shared/
│           ├── Word.tsx
│           └── ...
└── lib/
    └── lyrics-review/
        ├── types.ts
        ├── validation.ts
        ├── constants.ts
        └── utils/
```

### Phase 3: Migrate Instrumental Review

**Source**: `karaoke_gen/instrumental_review/static/index.html`

**Conversion approach**: Extract from single HTML file to React components

**Components to create**:
```
frontend/components/instrumental-review/
├── InstrumentalSelector.tsx    # Main container
├── WaveformViewer.tsx          # Audio waveform display
├── StemPlayer.tsx              # Individual stem playback
├── MuteRegionEditor.tsx        # Custom mute region UI
├── InstrumentalOptions.tsx     # Selection options
└── AudioUpload.tsx             # Custom instrumental upload
```

**Key features to preserve**:
- Waveform visualization
- A/B comparison between instrumentals
- Custom mute region editing
- Custom instrumental upload
- Keyboard shortcuts

### Phase 4: API Client Consolidation

**Current state**: 3 separate API clients
- `frontend/lib/api.ts` - Main app
- `lyrics_transcriber_temp/.../api.ts` - Lyrics review (LiveApiClient)
- Vanilla fetch in `index.html` - Instrumental

**Target**: Single unified API client in `frontend/lib/api.ts`

**New endpoints to add**:
```typescript
// Lyrics review
getCorrectionData(jobId: string, token: string): Promise<CorrectionData>
submitCorrections(jobId: string, token: string, corrections: Corrections): Promise<void>
getMetadata(jobId: string): Promise<Metadata>
generatePreview(jobId: string, corrections: Corrections): Promise<PreviewResponse>

// Instrumental review
getInstrumentalAnalysis(jobId: string, token: string): Promise<Analysis>
getWaveformData(jobId: string, numPoints: number): Promise<WaveformData>
selectInstrumental(jobId: string, token: string, selection: Selection): Promise<void>
uploadCustomInstrumental(jobId: string, token: string, file: File): Promise<void>
createCustomInstrumental(jobId: string, token: string, muteRegions: MuteRegion[]): Promise<void>
getAudioStream(jobId: string, stemType: string): string  // Returns URL
```

### Phase 5: Design System Migration

**MUI → Radix/Tailwind component mapping**:

| MUI Component | Radix/Tailwind Equivalent |
|---------------|---------------------------|
| `Button` | `components/ui/button.tsx` |
| `Dialog` | `components/ui/dialog.tsx` |
| `TextField` | `components/ui/input.tsx` |
| `Select` | `components/ui/select.tsx` |
| `Slider` | `components/ui/slider.tsx` |
| `Tabs` | `components/ui/tabs.tsx` |
| `IconButton` | Button with icon variant |
| `Tooltip` | `components/ui/tooltip.tsx` |
| `CircularProgress` | `components/ui/spinner.tsx` |
| `Snackbar` | `sonner` toast |
| `Box` | `<div>` with Tailwind |
| `Typography` | Tailwind text classes |
| `Paper` | `Card` component |
| `Accordion` | `components/ui/accordion.tsx` |

**Theme mapping** (MUI → Tailwind CSS variables):
- `theme.palette.primary` → `--primary`
- `theme.palette.secondary` → `--secondary`
- `theme.palette.background` → `--background`
- `theme.spacing()` → Tailwind spacing scale

### Phase 6: Navigation & State Updates

**JobCard.tsx changes**:
```typescript
// Before (external URLs)
const reviewUrl = `${reviewUiUrl}/?baseApiUrl=${encodedApiUrl}&reviewToken=${token}&audioHash=${hash}`
window.open(reviewUrl, '_blank')

// After (internal routes)
router.push(`/app/jobs/${job.job_id}/review`)
```

**Token handling**:
- Review tokens currently passed via URL params
- Move to: Server-side session or secure cookie
- Or: Keep in URL but validate on server

### Phase 7: Testing Strategy

**Update existing E2E tests**:
- `frontend/e2e/lyrics-review-only.spec.ts` - Update URLs
- `frontend/e2e/instrumental-selection-only.spec.ts` - Update URLs
- Happy path tests should work unchanged (test user flow, not URLs)

**New tests to add**:
- Route parameter validation
- Token validation
- Component-level tests for migrated components

### Phase 8: Cleanup

**Delete**:
- `lyrics_transcriber_temp/lyrics_transcriber/frontend/` (entire directory)
- `karaoke_gen/instrumental_review/static/index.html`
- GitHub Pages deployment workflow (if exists)

**Update**:
- Remove `NEXT_PUBLIC_REVIEW_UI_URL` env var
- Update backend to stop serving instrumental static files
- Update documentation

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Large scope | Detailed component inventory; test each phase |
| MUI→Tailwind bugs | Visual regression tests; side-by-side comparison |
| Broken functionality | E2E tests cover critical paths |
| Performance regression | Lighthouse CI; bundle size monitoring |

## Estimated Component Count

| Category | Count | Complexity |
|----------|-------|------------|
| Core types/utils | 8 | Low (copy) |
| Base components | 4 | Medium |
| View components | 5 | High |
| Modals | 9 | Medium |
| Synchronizer | 4 | High |
| Metrics | 4 | Medium |
| Instrumental | 5 (new) | Medium |
| **Total** | **39** | |

## Success Criteria

1. All existing functionality works through new internal routes
2. No external frontend deployments required
3. E2E tests pass
4. Visual parity with current UI (or better)
5. No performance regression
6. Single `npm run build` produces complete frontend

## Decisions Made

### Authentication: Job Ownership Model

**Decision**: Use job ownership for access control (no URL tokens needed)

**Implementation**:
- User must be logged in to access `/app/jobs/[id]/review` or `/app/jobs/[id]/instrumental`
- Backend validates: `job.user_email === current_user.email` OR `current_user.role === 'admin'`
- Simplifies URLs: `/app/jobs/abc123/review` instead of `?reviewToken=xyz&audioHash=...`
- Email notification links go to `/app/jobs/{id}/review` - user logs in if needed

**Backend changes needed**:
- Update `/api/review/{job_id}/*` endpoints to check job ownership instead of token
- Update `/api/jobs/{job_id}/instrumental-*` endpoints similarly
- Keep token-based auth as fallback for backward compatibility (deprecate later)

### Audio Streaming

**Decision**: Keep as-is - stream directly from backend API
- Frontend calls `api.nomadkaraoke.com/api/jobs/{id}/audio-stream/{stem}`
- No need to proxy through Next.js

### Preview Video

**Decision**: Keep as-is - generated on-demand by backend
- Frontend POSTs corrections, backend returns preview URL

## Next Steps

1. [x] Resolve authentication question → Job ownership
2. [ ] Create route structure (Phase 1)
3. [ ] Migrate core types/utils (Phase 2, Tier 1-2)
4. [ ] Migrate base components (Tier 3)
5. [ ] Migrate view components (Tier 4)
6. [ ] Migrate modals (Tier 5)
7. [ ] Migrate synchronizer (Tier 6)
8. [ ] Migrate main container (Tier 7)
9. [ ] Convert instrumental review (Phase 3)
10. [ ] Update API client (Phase 4)
11. [ ] Update navigation (Phase 6)
12. [ ] Update tests (Phase 7)
13. [ ] Cleanup old code (Phase 8)
