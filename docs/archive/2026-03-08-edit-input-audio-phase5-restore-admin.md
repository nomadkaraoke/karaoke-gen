# Edit Input Audio — Phase 5: Session Restore & Admin Dashboard

**Date:** 2026-03-08
**Parent:** [Master Plan](2026-03-08-edit-input-audio-master-plan.md)
**Status:** Planning
**Depends on:** Phases 2, 4

## Goal

Wire up auto-save during audio editing, provide a restore dialog when users return to an in-progress edit, and give admins the ability to view/replay audio edit sessions in the dashboard.

---

## 1. Auto-Save Hook

### `useAudioEditAutoSave`

New hook following the exact pattern of `useReviewSessionAutoSave`:

```typescript
// frontend/hooks/use-audio-edit-autosave.ts

function useAudioEditAutoSave({
    jobId: string,
    editStack: EditEntry[],
    originalDuration: number,
    currentDuration: number,
    isReadOnly: boolean,
    apiClient: ReviewApiClient,
}) {
    const lastBackupStackSize = useRef(0)

    const computeSummary = useCallback((): AudioEditSessionSummary => {
        const breakdown: Record<string, number> = {}
        for (const entry of editStack) {
            breakdown[entry.operation] = (breakdown[entry.operation] || 0) + 1
        }
        return {
            total_operations: editStack.length,
            operations_breakdown: breakdown,
            duration_change_seconds: currentDuration - originalDuration,
            net_duration_seconds: currentDuration,
        }
    }, [editStack, originalDuration, currentDuration])

    const saveSession = useCallback(async (trigger: string) => {
        if (editStack.length === 0) return
        if (editStack.length === lastBackupStackSize.current) return

        await apiClient.saveAudioEditSession(jobId, {
            edit_data: { entries: editStack, started_at: ... },
            edit_count: editStack.length,
            trigger,
            summary: computeSummary(),
        })

        lastBackupStackSize.current = editStack.length
    }, [editStack, ...])

    // Save triggers
    // 1. Every 3 edits (same cadence as lyrics review)
    useEffect(() => {
        if (editStack.length > 0 && editStack.length % 3 === 0) {
            saveSession("auto")
        }
    }, [editStack.length, saveSession])

    // 2. On page visibility change
    useEffect(() => {
        const handler = () => {
            if (document.hidden) saveSession("auto")
        }
        document.addEventListener("visibilitychange", handler)
        return () => document.removeEventListener("visibilitychange", handler)
    }, [saveSession])

    return { saveSession }
}
```

### Save Triggers

| Trigger | Condition | `trigger` field |
|---------|-----------|-----------------|
| Every N edits | Every 3 edits | `"auto"` |
| Page hide | `visibilitychange` when tab loses focus | `"auto"` |
| Before submit | Just before submitting edited audio | `"submit"` |
| Manual | User clicks "Save Progress" button | `"manual"` |

---

## 2. Session Restore Dialog

### When It Appears

When the `AudioEditor` component mounts:
1. Call `GET /api/review/{job_id}/audio-edit-sessions` to check for saved sessions
2. If sessions exist → show `AudioEditRestoreDialog`
3. If no sessions → proceed to fresh editor

### Dialog Design

Same split-pane layout as the lyrics review `SessionRestoreDialog`:

```
┌─────────────────────────────────────────────────────┐
│  Saved Audio Edit Sessions                     [×]   │
├──────────────────────┬──────────────────────────────┤
│ Session List         │ Edit Preview                 │
│                      │                              │
│ ▸ Mar 8, 2:15 PM    │ Operations applied:           │
│   3 edits · auto     │                              │
│   [Most recent]      │ 1. Trim start → 0:15         │
│                      │    Removed 15s intro          │
│ ▸ Mar 8, 1:45 PM    │                              │
│   1 edit · auto      │ 2. Cut 0:32-0:45             │
│                      │    Removed 13s                │
│                      │                              │
│                      │ 3. Mute 1:45-1:48            │
│                      │    Silenced 3.2s              │
│                      │                              │
│                      │ Duration: 4:05 → 3:34 (-31s)  │
│                      │                              │
├──────────────────────┴──────────────────────────────┤
│            [Start Fresh]         [Restore Selected]  │
└─────────────────────────────────────────────────────┘
```

### Restore Behavior

When user clicks "Restore Selected":
1. Load full session data: `GET /api/review/{job_id}/audio-edit-sessions/{session_id}`
2. Replay edit entries:
   - For each entry in `edit_data.entries`, call `POST /audio-edit/apply` sequentially
   - Show progress indicator: "Restoring edit 2 of 3..."
   - Each apply returns the updated waveform and audio
3. After all edits replayed, editor is in the same state as when the session was saved
4. User can continue editing from where they left off

### Alternative: Server-side Replay

Instead of replaying edits client-side, the backend could:
1. Accept a session_id in a new endpoint: `POST /audio-edit/restore-session`
2. Backend replays all operations server-side
3. Returns final state (waveform, playback URL, edit stack)

This is faster and more reliable. The frontend just needs to load the final state.

**Recommendation:** Server-side replay for MVP. Client-side replay only needed if we want to show animated step-by-step restoration.

---

## 3. "Save Progress" Button

Add to the `AudioEditorToolbar`:

```
[⟲ Undo] [⟳ Redo] | [✂️ Trim] [✂ Cut] [🔇 Mute] [📎 Join] | [💾 Save] | [Submit & Continue →]
```

- "Save" button triggers `saveSession("manual")`
- Shows toast notification: "Progress saved" / "No new changes"
- Disabled when no edits have been made

---

## 4. Admin Dashboard

### Audio Edit Reviews Page

New admin page: `/admin/audio-edits`

Follows the same pattern as `/admin/edit-reviews` for lyrics.

### List View

```
┌──────────────────────────────────────────────────────────┐
│ Audio Edit Reviews                        [Search 🔍]    │
├──────────────────────────────────────────────────────────┤
│ Job ID    │ Artist        │ Title       │ Edits │ Status │
│──────────────────────────────────────────────────────────│
│ abc12345  │ Queen         │ Bohemian... │ 3     │ ✓ Done │
│ def67890  │ Taylor Swift  │ Shake It... │ 1     │ ⏳ In Progress │
└──────────────────────────────────────────────────────────┘
```

### Detail View

Click a job to see its audio edit history:

```
┌──────────────────────────────────────────────────────────┐
│ Audio Edit: Queen - Bohemian Rhapsody (abc12345)         │
├──────────────────────────────────────────────────────────┤
│                                                          │
│ Original Audio                                           │
│ [▶ Play] Duration: 4:05                                 │
│ ██████████████████████████████████████████████ (waveform)│
│                                                          │
│ Edited Audio                                             │
│ [▶ Play] Duration: 3:34                                 │
│ ████████████████████████████████████████ (waveform)      │
│                                                          │
│ Edit Log:                                                │
│ ─────────                                                │
│ 1. [12:01:30] trim_start → removed 0:00-0:15 (15s)     │
│ 2. [12:03:15] cut → removed 0:17-0:30 (13s)            │
│ 3. [12:05:42] mute → silenced 1:30-1:33 (3.2s)         │
│                                                          │
│ Sessions: 2 auto-saves, 1 submit                        │
│ User: andrew@nomadkaraoke.com                            │
│ Submitted: Mar 8, 2026 12:06 PM                         │
└──────────────────────────────────────────────────────────┘
```

### Admin API Endpoints

Add to `backend/api/routes/admin.py`:

#### `GET /api/admin/audio-edit-reviews`

List jobs that have audio edit sessions.

**Query params:**
- `q`: Search by artist, title, job_id
- `exclude_test`: Filter test emails
- `limit`: Max results (default 50)

**Implementation:**
- Query jobs where `state_data.requires_audio_edit == true`
- Return summary with job metadata + edit count

#### `GET /api/admin/audio-edit-reviews/{job_id}`

Get full audio edit review for a job.

**Response:**
```json
{
    "job_id": "abc123",
    "artist": "Queen",
    "title": "Bohemian Rhapsody",
    "user_email": "andrew@...",
    "original_audio_url": "https://...",    // Signed URL
    "edited_audio_url": "https://...",       // Signed URL
    "original_waveform": { "amplitudes": [...], "duration": 245.3 },
    "edited_waveform": { "amplitudes": [...], "duration": 212.8 },
    "edit_sessions": [
        {
            "session_id": "...",
            "trigger": "submit",
            "edit_count": 3,
            "summary": { ... },
            "edit_data": { "entries": [...] }
        }
    ]
}
```

---

## 5. Admin Navigation

Add "Audio Edits" link to admin sidebar, alongside existing "Edit Reviews" link:

```typescript
// In admin layout
{ href: "/admin/audio-edits", label: "Audio Edits", icon: Scissors }
```

---

## Files Changed

| File | Type | Changes |
|------|------|---------|
| `frontend/hooks/use-audio-edit-autosave.ts` | New | Auto-save hook |
| `frontend/components/audio-editor/AudioEditRestoreDialog.tsx` | New | Session restore modal |
| `frontend/components/audio-editor/AudioEditor.tsx` | Edit | Wire up auto-save + restore |
| `frontend/components/audio-editor/AudioEditorToolbar.tsx` | Edit | Add Save button |
| `frontend/app/admin/audio-edits/page.tsx` | New | Admin list view |
| `frontend/app/admin/audio-edits/[jobId]/page.tsx` | New | Admin detail view |
| `frontend/lib/api.ts` | Edit | Add audio edit session API methods + admin endpoints |
| `backend/api/routes/admin.py` | Edit | Add audio edit review endpoints |

## Testing Strategy

| Test | Type | What |
|------|------|------|
| `use-audio-edit-autosave.test.ts` | Unit | Save triggers, dirty tracking, dedup |
| `AudioEditRestoreDialog.test.tsx` | Unit | Session list, selection, restore action |
| `admin-audio-edits.test.tsx` | Unit | Admin list and detail views render |
| `audio-edit-session-restore.spec.ts` | E2E | Full flow: edit → close → reopen → restore → continue |

## Resolved Questions

1. **Cross-job session restore?** — **No.** Audio edits are position-specific and unlikely to transfer across jobs.

2. **Admin replay animation?** — **No for MVP.** Show the edit log as a list + before/after waveforms and audio playback.

3. **Session pruning?** — Cap at 20 sessions per job. Auto-prune oldest auto-saves when limit is reached.
