# Edit Input Audio — Phase 3: Job Creation Flow Integration

**Date:** 2026-03-08
**Parent:** [Master Plan](2026-03-08-edit-input-audio-master-plan.md)
**Status:** Planning
**Depends on:** Phase 1

## Goal

Add an opt-in toggle during the guided job creation flow so users can indicate they want to edit the input audio before processing begins. This must not add friction for the majority of users who don't need it.

---

## 1. UX Design

### Where the Toggle Appears

After the user selects their audio source in **Step 2: Choose Audio** (in `AudioSourceStep.tsx`), a secondary question appears below their selection:

```
┌─────────────────────────────────────────────────────┐
│  ✓ Selected: "Bohemian Rhapsody - Live at Wembley"  │
│     YouTube · 6:03 · HD Audio                       │
│                                                      │
│  ┌─────────────────────────────────────────────────┐ │
│  │ ✂️ Want to trim or edit this audio first?        │ │
│  │                                                  │ │
│  │ If this is a live recording with talking/intro   │ │
│  │ you want to skip, or if you need to remove a     │ │
│  │ section, you can edit the audio after it's        │ │
│  │ downloaded — before lyrics are transcribed.       │ │
│  │                                                  │ │
│  │  ( ) No, use as-is  (default, selected)          │ │
│  │  ( ) Yes, I'll edit it first                     │ │
│  └─────────────────────────────────────────────────┘ │
│                                                      │
│  [Continue →]                                        │
└─────────────────────────────────────────────────────┘
```

### Design Principles

1. **Default is "No"** — The toggle defaults to "No, use as-is" so the existing flow is unaffected for most users.
2. **Collapsed by default** — The edit option panel is collapsed into a single line link ("Need to trim or edit this audio?") that expands on click. This keeps the UI clean.
3. **Only shown after selection** — The panel only appears after the user has selected/confirmed an audio source.
4. **Shown for all audio sources including file uploads** — Even if users upload their own files, they may not be comfortable with external audio editors. Our in-app editing makes basic operations (trim, cut, mute) accessible to everyone.

### Collapsed State

```
✓ Selected: "Bohemian Rhapsody - Live at Wembley"
  YouTube · 6:03 · HD Audio

  ✂️ Need to trim or edit this audio? ▸

  [Continue →]
```

Clicking the link expands the panel with the radio buttons.

---

## 2. Frontend Changes

### `AudioSourceStep.tsx`

Add state:
```typescript
const [wantsAudioEdit, setWantsAudioEdit] = useState(false)
const [showEditOption, setShowEditOption] = useState(false)
```

Pass `wantsAudioEdit` up to parent via callback:
```typescript
onAudioEditToggle?: (wants: boolean) => void
```

New sub-component: `AudioEditToggle.tsx`
```typescript
interface AudioEditToggleProps {
    value: boolean
    onChange: (wants: boolean) => void
}
```

### `GuidedJobFlow.tsx`

Add state tracking:
```typescript
const [wantsAudioEdit, setWantsAudioEdit] = useState(false)
```

Pass to job creation call:
```typescript
// In handleConfirm(), add to request body:
requires_audio_edit: wantsAudioEdit
```

### Success Screen Update

When `wantsAudioEdit` is true, update the timeline on the success screen:

```
What happens next:
1. Audio downloading (~1 min) — automatic
2. ✂️ You edit audio — trim, cut, or mute sections    ← NEW
3. Audio processing (~10 min) — automatic
4. You review — lyrics & instrumental
5. Video delivered (~10 min) — automatic
```

---

## 3. Backend Changes

### Job Creation Endpoints

Both `POST /api/jobs/create-from-search` and `POST /api/jobs/create-from-url` need to accept:

```python
class CreateFromSearchRequest(BaseModel):
    # ... existing fields ...
    requires_audio_edit: bool = False   # NEW
```

Store in job's `state_data`:
```python
if request.requires_audio_edit:
    state_data['requires_audio_edit'] = True
```

### File Upload Path

For `POST /api/jobs/upload` (direct file upload), add a query parameter or form field:
```python
requires_audio_edit: bool = Form(False)
```

---

## 4. Tenant Feature Control

Add to `TenantConfig.features`:
```python
audio_editing: bool = True  # Enable/disable audio editing for tenant
```

Default: `True` for all tenants. Can be disabled per-tenant if they don't want the extra step.

Frontend check:
```typescript
const { features } = useTenant()
// Only show the edit toggle if tenant allows it
{features.audio_editing && <AudioEditToggle ... />}
```

---

## 5. Job Card Status Display

When a job is in `awaiting_audio_edit` state, the `JobCard` component should show:

```
┌────────────────────────────────────────┐
│ Bohemian Rhapsody - Queen              │
│ Status: Edit Audio Required            │
│ Your audio is ready for editing.       │
│                                        │
│ [Edit Audio →]                         │
└────────────────────────────────────────┘
```

The "Edit Audio" button navigates to `/app/jobs#/{jobId}/audio-edit`.

Update `JobCard.tsx`:
```typescript
case 'awaiting_audio_edit':
    return (
        <ActionButton href={`/app/jobs#/${job.job_id}/audio-edit`}>
            Edit Audio
        </ActionButton>
    )
```

---

## Files Changed

| File | Type | Changes |
|------|------|---------|
| `frontend/components/job/steps/AudioSourceStep.tsx` | Edit | Add AudioEditToggle panel |
| `frontend/components/job/steps/AudioEditToggle.tsx` | New | Collapsed/expanded toggle component |
| `frontend/components/job/GuidedJobFlow.tsx` | Edit | Track wantsAudioEdit state, pass to API, update success screen |
| `frontend/components/job/JobCard.tsx` | Edit | Handle awaiting_audio_edit state with action button |
| `backend/api/routes/jobs.py` | Edit | Accept requires_audio_edit in create endpoints |
| `backend/api/routes/audio_search.py` | Edit | Accept requires_audio_edit in select endpoint |
| `backend/models/tenant.py` | Edit | Add audio_editing feature flag |

## Testing Strategy

| Test | Type | What |
|------|------|------|
| `AudioEditToggle.test.tsx` | Unit | Toggle renders, defaults to off, calls onChange |
| `GuidedJobFlow.test.tsx` | Unit | Toggle state flows through to job creation |
| `test_create_from_search_edit_flag.py` | Integration | API accepts and stores requires_audio_edit |
| `guided-flow-audio-edit.spec.ts` | E2E | Full flow: select audio → enable edit → create job → verify state |

## Resolved Questions

1. **Show for file uploads?** — **Yes.** Users may not be comfortable with external audio editors. Our in-app editing makes basic operations accessible to everyone.

2. **Admin override?** — **Yes.** Admins can reset a job to `awaiting_audio_edit` state via AdminJobActions. Useful when a client requests a trim after job creation.

3. **Remember preference?** — No — it should be a per-job decision.
