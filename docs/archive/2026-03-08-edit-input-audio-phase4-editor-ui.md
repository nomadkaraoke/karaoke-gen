# Edit Input Audio — Phase 4: Audio Editor UI

**Date:** 2026-03-08
**Parent:** [Master Plan](2026-03-08-edit-input-audio-master-plan.md)
**Status:** Planning
**Depends on:** Phase 1

## Goal

Build the audio editor UI that lets users trim, cut, mute, and join audio before processing. The UI is modeled on the existing instrumental review UI (waveform viewer, tabs, playback) but adds editing controls and an undo/redo system.

---

## 1. Page Routing

### Hash-based Route

New route: `/app/jobs#/{jobId}/audio-edit`

Add to the existing hash-based router in `frontend/app/app/jobs/[[...slug]]/client.tsx`:

```typescript
function getRouteType(hash: string): RouteType {
    if (hash.includes('/audio-edit')) return 'audio-edit'
    if (hash.includes('/instrumental')) return 'instrumental'
    if (hash.includes('/review')) return 'review'
    return 'unknown'
}

function getExpectedStates(routeType: RouteType): string[] {
    switch (routeType) {
        case 'audio-edit':
            return ['awaiting_audio_edit', 'in_audio_edit']
        // ... existing cases
    }
}
```

### Review Token Auth

The audio edit page uses the same review token auth as lyrics/instrumental review. The token is generated when entering `awaiting_audio_edit` state (Phase 1).

---

## 2. Component Architecture

```
AudioEditor (main container)
├── AudioEditorToolbar
│   ├── UndoRedoControls
│   ├── EditModeSelector (trim/cut/mute)
│   ├── JoinUploadButton
│   └── SubmitButton
├── WaveformEditor (extended WaveformViewer)
│   ├── Canvas (amplitude visualization)
│   ├── TimeRuler
│   ├── SelectionOverlay (for cut/mute regions)
│   ├── TrimHandles (draggable start/end markers)
│   └── Playhead
├── AudioTabSwitcher
│   ├── "Original" tab
│   └── "Edited" tab
├── PlaybackControls
│   ├── Play/Pause
│   ├── Current time / Total duration
│   └── PlaybackSpeed (0.5x, 1x, 1.5x, 2x)
├── EditHistoryPanel (collapsible sidebar)
│   └── List of applied edits with undo points
└── AudioEditorGuidance (help/tips panel)
```

---

## 3. WaveformEditor Component

Extends the existing `WaveformViewer.tsx` pattern but adds interactive editing.

### Canvas Rendering

Same as instrumental review:
- Dark background (#0d1117)
- Blue amplitude bars (#60a5fa)
- Time ruler at bottom
- White playhead line with dot
- 3 zoom levels (1x, 2x, 4x)

### New Interactive Elements

**Trim Handles** (for trim_start / trim_end):
- Draggable vertical lines at the start and end of the waveform
- Drag start handle right → marks region for trimming from start
- Drag end handle left → marks region for trimming from end
- Trimmed region shown with red-tinted overlay and crosshatch pattern
- Snaps to 0.1 second intervals

**Selection Overlay** (for cut / mute):
- Click and drag to select a region
- Selected region highlighted with yellow border and semi-transparent fill
- Context menu or toolbar buttons to apply "Cut" or "Mute" to selection
- Muted regions shown with very dim bars (like instrumental review)
- Cut regions shown with red-tinted overlay

**Interaction Modes:**
| Mode | Click Behavior | Drag Behavior |
|------|---------------|---------------|
| Play | Seek to position | — |
| Trim | — | Drag trim handles |
| Select | Seek to position | Create selection for cut/mute |

### Waveform Data Updates

After each edit operation:
1. Frontend calls `POST /api/review/{job_id}/audio-edit/apply`
2. Backend returns new `waveform_data.amplitudes` for the edited audio
3. Canvas re-renders with new data
4. Duration display updates

### Tab Switching (Original vs Edited)

Two tabs above the waveform:
- **Original**: Shows original audio waveform, plays original audio
- **Edited**: Shows edited audio waveform (after latest edit), plays edited audio

Tab behavior:
- Switching tabs loads the corresponding waveform data and audio URL
- Maintains playback position proportionally (if audio duration changed, map position)
- Shows loading spinner during tab switch (same as instrumental review)
- "Original" tab always shows the unmodified waveform
- "Edited" tab shows the waveform after the most recent edit

---

## 4. Editing Operations UI

### Trim Start

1. User drags the start trim handle to the right
2. Trimmed region turns red with "Remove" label
3. User clicks "Apply Trim" button (or double-clicks the handle)
4. API call to `POST /audio-edit/apply` with `{ operation: "trim_start", params: { end_seconds: X } }`
5. Waveform updates to show trimmed audio
6. "Edited" tab auto-selected

### Trim End

Same as trim start but from the right side.

### Cut Region

1. User switches to "Select" mode
2. Drags to select a region on the waveform
3. Selection highlighted with yellow border
4. User clicks "Cut" in toolbar (or keyboard shortcut: Delete/Backspace)
5. Confirmation tooltip: "Remove 3.2s of audio from 1:23 to 1:26?"
6. API call to `POST /audio-edit/apply` with `{ operation: "cut", params: { start_seconds, end_seconds } }`
7. Waveform updates — region removed, audio joined

### Mute Region

1. Same selection as cut
2. User clicks "Mute" in toolbar (or keyboard shortcut: M)
3. API call with `{ operation: "mute", params: { start_seconds, end_seconds } }`
4. Waveform shows muted region with dim bars
5. Audio duration unchanged

### Join Audio

1. User clicks "Join" button in toolbar
2. File picker opens (accepts FLAC, WAV, MP3, M4A, OGG)
3. File uploaded via `POST /audio-edit/upload`
4. Small dialog appears: "Add to [Start] or [End]?"
5. API call with `{ operation: "join_start" or "join_end", params: { upload_id } }`
6. Waveform expands to include joined audio

**Note:** Join is limited to start/end only (no insert at arbitrary middle position). This is a niche feature that will rarely be used. Users who need to insert in the middle can work around it by cutting and re-joining.

---

## 5. Undo/Redo System

### Frontend State

```typescript
interface EditState {
    editStack: EditEntry[]      // Applied edits (from backend)
    redoStack: EditEntry[]      // Undone edits available for redo
    canUndo: boolean
    canRedo: boolean
}

interface EditEntry {
    editId: string
    operation: string
    params: Record<string, unknown>
    durationBefore: number
    durationAfter: number
    timestamp: string
}
```

### Controls

- Undo button (⟲) — calls `POST /audio-edit/undo`
- Redo button (⟳) — calls `POST /audio-edit/redo`
- Keyboard shortcuts: Ctrl/Cmd+Z (undo), Ctrl/Cmd+Shift+Z (redo)
- Buttons disabled when stack is empty

### Backend Coordination

Each undo/redo returns the same response as apply — updated waveform data and playback URL for the now-current version. The backend maintains the edit/redo stacks in `state_data`.

---

## 6. Playback Controls

### Audio Element

Single hidden `<audio>` element (same pattern as instrumental review):
- Dynamically updates `src` when switching original/edited tabs
- Maintains position across tab switches

### Controls

```
  [⏮] [▶/⏸] [⏭]    0:45 / 3:32    Speed: [1x ▾]
```

- Previous: jump 5 seconds back
- Play/Pause: toggle playback
- Next: jump 5 seconds forward
- Time display: current / total
- Speed selector: 0.5x, 1x, 1.5x, 2x

### Click-to-Seek

Click anywhere on the waveform to seek to that position (same as instrumental review).

---

## 7. Edit History Panel

Collapsible sidebar showing the list of applied edits:

```
Edit History
─────────────────
3. Mute 1:45-1:48
   (silenced 3.2s)
2. Cut 0:32-0:45
   (removed 13s)
1. Trim start → 0:15
   (removed 15s intro)
─────────────────
Original: 4:05
Current:  3:34 (-31s)
```

Each entry shows:
- Operation type and parameters
- Duration impact
- Click to preview the state at that point (optional, stretch goal)

---

## 8. Guidance Panel

A collapsible help panel (same pattern as `InstrumentalGuidancePanel`):

```
┌───────────────────────────────────────────────┐
│ ✂️ Audio Editor Tips                     [×]  │
│                                               │
│ • Trim: Drag the handles at the start/end     │
│   to remove intros or outros                  │
│ • Cut: Select a region and press Delete to    │
│   remove it from the middle                   │
│ • Mute: Select a region and press M to        │
│   silence it without changing the duration     │
│ • Join: Upload additional audio to add to     │
│   the start or end                            │
│ • Undo/Redo: Ctrl+Z / Ctrl+Shift+Z           │
│ • Click anywhere on the waveform to listen    │
│                                               │
│ When you're done, click "Submit" to continue  │
│ with processing.                              │
└───────────────────────────────────────────────┘
```

Dismissible via localStorage (same pattern as instrumental guidance).

---

## 9. Submit Flow

1. User clicks "Submit & Continue" button
2. Confirmation dialog:
   ```
   Submit Edited Audio?

   Original duration: 4:05
   Edited duration: 3:34
   Edits applied: 3

   The edited audio will be used for separation and
   lyrics transcription. The original is preserved.

   [Cancel]  [Submit]
   ```
3. API call to `POST /api/review/{job_id}/audio-edit/submit`
4. Success screen with countdown (same pattern as instrumental review):
   ```
   ✓ Audio edit complete!

   Processing will continue with your edited audio.
   You'll receive an email when lyrics are ready for review.

   Redirecting in 3...
   ```
5. Redirect to `/app` after 3 seconds

### Submit with No Edits

If the user opens the editor but doesn't make any changes:
- "Submit" button text changes to "Continue Without Editing"
- No confirmation dialog needed
- Submits immediately

---

## 10. Mobile Responsiveness

The editor should work on tablet/desktop. Mobile phone support is a stretch goal due to the precision needed for waveform interactions.

- **Desktop (>1024px)**: Full layout with sidebar edit history
- **Tablet (768-1024px)**: Edit history collapsed by default, narrower waveform
- **Mobile (<768px)**: Simplified layout, larger touch targets for trim handles, edit history in a bottom sheet

---

## Files

| File | Type | Description |
|------|------|-------------|
| `frontend/components/audio-editor/AudioEditor.tsx` | New | Main container component |
| `frontend/components/audio-editor/WaveformEditor.tsx` | New | Interactive waveform with trim handles and selection |
| `frontend/components/audio-editor/AudioEditorToolbar.tsx` | New | Undo/redo, mode selector, join, submit |
| `frontend/components/audio-editor/PlaybackControls.tsx` | New | Play/pause, seek, speed |
| `frontend/components/audio-editor/AudioTabSwitcher.tsx` | New | Original vs Edited tabs |
| `frontend/components/audio-editor/EditHistoryPanel.tsx` | New | List of applied edits |
| `frontend/components/audio-editor/AudioEditorGuidance.tsx` | New | Help/tips panel |
| `frontend/components/audio-editor/TrimHandle.tsx` | New | Draggable trim markers |
| `frontend/components/audio-editor/SelectionOverlay.tsx` | New | Cut/mute selection region |
| `frontend/components/audio-editor/index.ts` | New | Exports |
| `frontend/app/app/jobs/[[...slug]]/client.tsx` | Edit | Add audio-edit route |
| `frontend/lib/api.ts` | Edit | Add audio edit API methods |

## Testing Strategy

| Test | Type | What |
|------|------|------|
| `AudioEditor.test.tsx` | Unit | Renders, loads audio info, displays waveform |
| `WaveformEditor.test.tsx` | Unit | Canvas rendering, click-to-seek, selection interactions |
| `AudioEditorToolbar.test.tsx` | Unit | Mode switching, undo/redo state |
| `PlaybackControls.test.tsx` | Unit | Play/pause, speed changes |
| `audio-editor.spec.ts` | E2E | Full flow: open editor → trim → submit → verify job continues |

## Resolved Questions

1. **Client-side preview before apply?** — **No.** All processing is server-side only. This ensures the preview is perfectly representative of the final result. FFmpeg operations should complete in a few seconds.

2. **Keyboard shortcuts scope** — Waveform-focused to avoid conflicts with other page elements.

3. **Max audio duration for editing?** — No limit for MVP. Add one later if performance issues arise.

4. **Zoom-to-selection?** — Defer to post-MVP. Nice UX but adds complexity.
