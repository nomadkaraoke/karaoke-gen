# Review-Phase Word-Timing Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent invalid word timings (the `start=0`/`end=-0.005` class that caused job `17f7c313`'s overlapping lyrics) from being created or persisted during lyrics review, while keeping segment editing flexible.

**Architecture:** One invariant — every word within its segment's `[start_time, end_time]`, `end ≥ start ≥ 0`, non-decreasing — enforced at four points: manual-sync (clamp taps, source prevention), modal-open (repair + warn), backend submit (sanitize + log), and the already-shipped render-time net. The modal already derives segment bounds from word `min/max` (`updateSegment`), so dragging a word earlier/later expands the segment naturally; that's why "word outside saved bounds" reliably means corruption, not a legit edit.

**Tech Stack:** Frontend TypeScript/React + Jest (`frontend/`); backend Python/FastAPI + pytest (`backend/`); i18n via next-intl + `translate.py`.

**Design doc:** `docs/archive/2026-06-10-review-timing-validation-design.md`

---

## File Structure

- Create `frontend/lib/lyrics-review/sanitizeWordTimings.ts` — pure sanitizer + change report (frontend canonical).
- Create `frontend/lib/lyrics-review/__tests__/sanitizeWordTimings.test.ts` — unit tests.
- Modify `frontend/hooks/useManualSync.ts` — clamp synced times to segment bounds (Layer 1).
- Create `frontend/hooks/__tests__/useManualSync.test.ts` — sync clamp test.
- Modify `frontend/components/lyrics-review/modals/EditModal.tsx` — sanitize-on-open + warning banner (Layer 2).
- Modify `frontend/components/lyrics-review/__tests__/EditModal.test.tsx` (create if absent) — open-repair test.
- Create `backend/services/timing_sanitizer.py` — dict-level sanitizer (backend canonical).
- Create `tests/unit/services/test_timing_sanitizer.py` — unit tests.
- Modify `backend/api/routes/jobs.py` (`submit_corrections`, ~L1076-1117) — call sanitizer before GCS upload (Layer 3).
- Modify `tests/unit/api/routes/test_jobs_corrections.py` (create if absent) — submit sanitize test.
- Modify `frontend/messages/en.json` — warning strings; then `translate.py --target all`.

---

## Task 1: Frontend sanitizer (`sanitizeWordTimings.ts`)

**Files:**
- Create: `frontend/lib/lyrics-review/sanitizeWordTimings.ts`
- Test: `frontend/lib/lyrics-review/__tests__/sanitizeWordTimings.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/lib/lyrics-review/__tests__/sanitizeWordTimings.test.ts
import { sanitizeSegmentTimings } from '../sanitizeWordTimings'
import { LyricsSegment } from '../types'

// The exact shape that broke job 17f7c313.
function scalpSegment(): LyricsSegment {
  return {
    id: 's1',
    text: 'A whiskey and a beer, ...',
    start_time: 15.18,
    end_time: 18.04,
    words: [
      { id: 'a', text: 'A', start_time: 0, end_time: -0.005 },
      { id: 'b', text: 'whiskey', start_time: 0, end_time: -0.005 },
      { id: 'c', text: 'and', start_time: 0, end_time: 0 },
      { id: 'd', text: 'a', start_time: 0, end_time: 0 },
      { id: 'e', text: 'beer,', start_time: 16.1, end_time: 16.42 },
    ],
  }
}

describe('sanitizeSegmentTimings', () => {
  it('clamps out-of-bounds leading words into the segment window', () => {
    const { segment, changes } = sanitizeSegmentTimings(scalpSegment())
    for (const w of segment.words) {
      expect(w.start_time!).toBeGreaterThanOrEqual(15.18)
      expect(w.end_time!).toBeGreaterThanOrEqual(w.start_time!)
      expect(w.end_time!).toBeLessThanOrEqual(18.04 + 1e-9)
    }
    expect(changes.length).toBeGreaterThan(0)
    expect(changes.some((c) => c.wordId === 'a')).toBe(true)
  })

  it('leaves already-valid segments untouched and reports no changes', () => {
    const seg: LyricsSegment = {
      id: 's', text: 'Filling up that cup', start_time: 21.4, end_time: 22.78,
      words: [
        { id: 'w0', text: 'Filling', start_time: 21.4, end_time: 21.82 },
        { id: 'w1', text: 'up', start_time: 21.86, end_time: 22.1 },
        { id: 'w2', text: 'that', start_time: 22.14, end_time: 22.42 },
        { id: 'w3', text: 'cup', start_time: 22.46, end_time: 22.78 },
      ],
    }
    const { changes } = sanitizeSegmentTimings(seg)
    expect(changes).toEqual([])
  })

  it('handles null word timings by clamping to the segment start', () => {
    const seg: LyricsSegment = {
      id: 's', text: 'x y', start_time: 5, end_time: 6,
      words: [
        { id: 'w0', text: 'x', start_time: null, end_time: null },
        { id: 'w1', text: 'y', start_time: 5.5, end_time: 6 },
      ],
    }
    const { segment } = sanitizeSegmentTimings(seg)
    expect(segment.words[0].start_time!).toBeGreaterThanOrEqual(5)
    expect(segment.words[0].end_time!).toBeGreaterThanOrEqual(segment.words[0].start_time!)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx jest lib/lyrics-review/__tests__/sanitizeWordTimings.test.ts`
Expected: FAIL — `Cannot find module '../sanitizeWordTimings'`.

- [ ] **Step 3: Write minimal implementation**

```typescript
// frontend/lib/lyrics-review/sanitizeWordTimings.ts
import { LyricsSegment, Word } from './types'

export interface TimingChange {
  wordId: string
  wordText: string
  field: 'start_time' | 'end_time'
  from: number | null
  to: number
}

/**
 * Enforce the timing invariant for one segment:
 *   segment.start <= word.start <= word.end <= segment.end, non-decreasing, finite, >= 0.
 *
 * The segment's own start/end are treated as the authoritative window. Because the
 * editor derives segment bounds from word min/max on every edit (updateSegment), a word
 * outside the segment window only happens via corruption (e.g. a manual-sync glitch),
 * never via a legitimate extension. Returns a NEW segment plus the list of clamps applied
 * so callers can warn. A clean segment returns the same word objects and changes: [].
 */
export function sanitizeSegmentTimings(segment: LyricsSegment): {
  segment: LyricsSegment
  changes: TimingChange[]
} {
  const changes: TimingChange[] = []

  const segStart = isFiniteNumber(segment.start_time) ? (segment.start_time as number) : 0
  const segEndRaw = isFiniteNumber(segment.end_time) ? (segment.end_time as number) : segStart
  const segEnd = Math.max(segEndRaw, segStart)

  let prevEnd = segStart
  const words: Word[] = segment.words.map((w) => {
    const clamp = (v: number) => Math.min(Math.max(v, segStart), segEnd)

    let start = isFiniteNumber(w.start_time) ? (w.start_time as number) : null
    let end = isFiniteNumber(w.end_time) ? (w.end_time as number) : null

    // start: must be finite, within [prevEnd-clamped-to-window, segEnd]
    const wantStart = start === null ? prevEnd : start
    const newStart = clamp(Math.max(wantStart, segStart))
    // end: must be finite and >= start, within window
    const wantEnd = end === null ? newStart : end
    const newEnd = clamp(Math.max(wantEnd, newStart))

    let next = w
    if (newStart !== start) {
      changes.push({ wordId: w.id, wordText: w.text, field: 'start_time', from: w.start_time, to: newStart })
      next = { ...next, start_time: newStart }
    }
    if (newEnd !== end) {
      changes.push({ wordId: w.id, wordText: w.text, field: 'end_time', from: w.end_time, to: newEnd })
      next = { ...next, end_time: newEnd }
    }
    prevEnd = newEnd
    return next
  })

  if (changes.length === 0) return { segment, changes }
  return { segment: { ...segment, words }, changes }
}

function isFiniteNumber(v: number | null | undefined): boolean {
  return typeof v === 'number' && Number.isFinite(v)
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx jest lib/lyrics-review/__tests__/sanitizeWordTimings.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/lyrics-review/sanitizeWordTimings.ts frontend/lib/lyrics-review/__tests__/sanitizeWordTimings.test.ts
git commit -m "feat(lyrics-review): add word-timing sanitizer with change report"
```

---

## Task 2: Manual-sync clamp (Layer 1, source prevention)

**Files:**
- Modify: `frontend/hooks/useManualSync.ts` (the `handleKeyDown` tap path ~L120-155 and `handleTap` path ~L283-316)
- Test: `frontend/hooks/__tests__/useManualSync.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/hooks/__tests__/useManualSync.test.ts
import { clampSyncTime } from '../useManualSync'

describe('clampSyncTime', () => {
  it('clamps a playhead-at-0 tap up to the segment start', () => {
    expect(clampSyncTime(0, 15.18, 18.04)).toBe(15.18)
  })
  it('clamps a tap past the segment end down to the end', () => {
    expect(clampSyncTime(99, 15.18, 18.04)).toBe(18.04)
  })
  it('leaves an in-window tap unchanged', () => {
    expect(clampSyncTime(16.0, 15.18, 18.04)).toBe(16.0)
  })
  it('falls back gracefully when segment bounds are null', () => {
    expect(clampSyncTime(5, null, null)).toBe(5)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx jest hooks/__tests__/useManualSync.test.ts`
Expected: FAIL — `clampSyncTime` is not exported.

- [ ] **Step 3: Add the helper and apply it in both sync paths**

Add this exported helper near the top of `frontend/hooks/useManualSync.ts` (after the constants block, ~L16):

```typescript
/**
 * Clamp a manual-sync tap time into the segment's audio window. A tap should always land
 * within the segment being synced; a value outside (e.g. the playhead sitting at 0) is a
 * sync glitch — the source of the start=0/end=-0.005 corruption. Null bounds => no clamp.
 */
export function clampSyncTime(
  time: number,
  segStart: number | null,
  segEnd: number | null
): number {
  let t = time
  if (typeof segStart === 'number' && Number.isFinite(segStart)) t = Math.max(t, segStart)
  if (typeof segEnd === 'number' && Number.isFinite(segEnd)) t = Math.min(t, segEnd)
  return t
}
```

In the `handleKeyDown` path, replace (~L123):

```typescript
          const currentStartTime = currentTimeRef.current
```

with:

```typescript
          const rawStartTime = currentTimeRef.current
          const currentStartTime = clampSyncTime(
            rawStartTime,
            editedSegment?.start_time ?? null,
            editedSegment?.end_time ?? null
          )
          if (currentStartTime !== rawStartTime) {
            onTimingClamped?.(newWords[syncWordIndex]?.text ?? '', currentStartTime)
          }
```

Apply the identical change in the `handleTap` path (~L286), reusing `clampSyncTime` and `onTimingClamped`.

Add `onTimingClamped?: (wordText: string, snappedTo: number) => void` to `UseManualSyncProps` (after `updateSegment`, ~L10) and destructure it in the hook signature (~L22). The previous-word `end_time = currentStartTime - 0.005` lines (~L144/L305) now operate on the clamped `currentStartTime`, so they can no longer produce negative values.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx jest hooks/__tests__/useManualSync.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/hooks/useManualSync.ts frontend/hooks/__tests__/useManualSync.test.ts
git commit -m "fix(lyrics-review): clamp manual-sync taps to segment bounds (root cause of start=0)"
```

---

## Task 3: Modal sanitize-on-open + warning banner (Layer 2)

**Files:**
- Modify: `frontend/components/lyrics-review/modals/EditModal.tsx` (~L58, L67-69, render)
- Test: `frontend/components/lyrics-review/__tests__/EditModal.test.tsx` (create if absent)

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/components/lyrics-review/__tests__/EditModal.test.tsx
import { render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import EditModal from '../modals/EditModal'
import en from '@/messages/en.json'
import { LyricsSegment } from '@/lib/lyrics-review/types'

const badSegment: LyricsSegment = {
  id: 's1', text: 'A whiskey and a beer,', start_time: 15.18, end_time: 18.04,
  words: [
    { id: 'a', text: 'A', start_time: 0, end_time: -0.005 },
    { id: 'b', text: 'whiskey', start_time: 0, end_time: -0.005 },
    { id: 'e', text: 'beer,', start_time: 16.1, end_time: 16.42 },
  ],
}

function renderModal(segment: LyricsSegment) {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      <EditModal open segment={segment} segmentIndex={0} originalSegment={null}
        onClose={() => {}} onSave={() => {}} />
    </NextIntlClientProvider>
  )
}

it('shows the timing-repaired banner when a segment opens with out-of-bounds words', () => {
  renderModal(badSegment)
  expect(screen.getByTestId('timing-sanitized-banner')).toBeInTheDocument()
})

it('shows no banner for a clean segment', () => {
  renderModal({ ...badSegment, words: [{ id: 'e', text: 'beer,', start_time: 16.1, end_time: 16.42 }] })
  expect(screen.queryByTestId('timing-sanitized-banner')).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx jest components/lyrics-review/__tests__/EditModal.test.tsx`
Expected: FAIL — no `timing-sanitized-banner` test id.

- [ ] **Step 3: Sanitize on open and render the banner**

In `EditModal.tsx`, add the import (after L15):

```typescript
import { sanitizeSegmentTimings } from '@/lib/lyrics-review/sanitizeWordTimings'
```

Add banner state (after L59 `const [isPlaying, ...]`):

```typescript
  const [timingFixCount, setTimingFixCount] = useState(0)
```

Replace the segment-init effect (L67-69):

```typescript
  useEffect(() => {
    if (!segment) {
      setEditedSegment(null)
      setTimingFixCount(0)
      return
    }
    const { segment: cleaned, changes } = sanitizeSegmentTimings(segment)
    setEditedSegment(cleaned)
    setTimingFixCount(changes.length)
  }, [segment])
```

Render the banner at the top of the modal body (inside `<DialogContent>`, before the word list). Use the namespace already loaded at L54 (`t = useTranslations('lyricsReview.modals.editAll')`):

```tsx
      {timingFixCount > 0 && (
        <div
          data-testid="timing-sanitized-banner"
          className="mb-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800"
        >
          {t('timingSanitized', { count: timingFixCount })}
        </div>
      )}
```

Pass the clamp callback to the manual-sync hook (at the `useManualSync({...})` call, ~L110-115) so sync clamps also surface a toast — add `onTimingClamped` there:

```typescript
    onTimingClamped: (wordText: string, snappedTo: number) => {
      toast.warning(t('timingClamped', { word: wordText, time: snappedTo.toFixed(2) }))
    },
```

Add the sonner import at top (after L7): `import { toast } from 'sonner'`.

- [ ] **Step 4: Add i18n strings**

In `frontend/messages/en.json`, under `lyricsReview.modals.editAll`, add:

```json
"timingSanitized": "Fixed {count} word timing(s) that fell outside this segment.",
"timingClamped": "\"{word}\" timing was outside the segment — snapped to {time}s."
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend && npx jest components/lyrics-review/__tests__/EditModal.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add frontend/components/lyrics-review/modals/EditModal.tsx frontend/components/lyrics-review/__tests__/EditModal.test.tsx frontend/messages/en.json
git commit -m "feat(lyrics-review): repair invalid word timings on segment-edit open with a warning banner"
```

---

## Task 4: Backend sanitizer + submit integration (Layer 3)

**Files:**
- Create: `backend/services/timing_sanitizer.py`
- Test: `tests/unit/services/test_timing_sanitizer.py`
- Modify: `backend/api/routes/jobs.py` (`submit_corrections`, ~L1110-1117)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/test_timing_sanitizer.py
from backend.services.timing_sanitizer import sanitize_corrections


def _scalp_corrections():
    return {
        "corrected_segments": [
            {
                "id": "s1",
                "text": "A whiskey and a beer,",
                "start_time": 15.18,
                "end_time": 18.04,
                "words": [
                    {"id": "a", "text": "A", "start_time": 0, "end_time": -0.005},
                    {"id": "b", "text": "whiskey", "start_time": 0, "end_time": -0.005},
                    {"id": "c", "text": "and", "start_time": 0, "end_time": 0},
                    {"id": "d", "text": "a", "start_time": 0, "end_time": 0},
                    {"id": "e", "text": "beer,", "start_time": 16.1, "end_time": 16.42},
                ],
            }
        ]
    }


def test_clamps_out_of_bounds_words():
    cleaned, count = sanitize_corrections(_scalp_corrections())
    words = cleaned["corrected_segments"][0]["words"]
    for w in words:
        assert w["start_time"] >= 15.18
        assert w["end_time"] >= w["start_time"]
        assert w["end_time"] <= 18.04 + 1e-9
    assert count >= 4


def test_valid_corrections_unchanged():
    payload = {
        "corrected_segments": [
            {
                "id": "s", "text": "Filling up", "start_time": 21.4, "end_time": 22.1,
                "words": [
                    {"id": "w0", "text": "Filling", "start_time": 21.4, "end_time": 21.82},
                    {"id": "w1", "text": "up", "start_time": 21.86, "end_time": 22.1},
                ],
            }
        ]
    }
    cleaned, count = sanitize_corrections(payload)
    assert count == 0
    assert cleaned == payload


def test_missing_segments_key_is_noop():
    cleaned, count = sanitize_corrections({"foo": "bar"})
    assert count == 0
    assert cleaned == {"foo": "bar"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-review-timing-validation && python -m pytest tests/unit/services/test_timing_sanitizer.py -q`
Expected: FAIL — `ModuleNotFoundError: backend.services.timing_sanitizer`.

- [ ] **Step 3: Write the sanitizer (dict-level, mirrors the frontend invariant and SegmentResizer._sanitize_segment_timings)**

```python
# backend/services/timing_sanitizer.py
"""Sanitize submitted review corrections so word timings stay within their segment.

Mirrors the frontend sanitizeWordTimings.ts and the render-time
SegmentResizer._sanitize_segment_timings. Operates on the raw corrections dict the
frontend POSTs (Dict[str, Any]); clamps any word whose start/end falls outside its
segment window or is inverted/negative. Returns (cleaned_dict, num_clamps)."""
from __future__ import annotations

import math
from typing import Any, Dict, Tuple


def _finite(v: Any) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(v)


def sanitize_corrections(corrections: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    segments = corrections.get("corrected_segments")
    if not isinstance(segments, list):
        return corrections, 0

    changes = 0
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        seg_start = seg.get("start_time")
        seg_end = seg.get("end_time")
        seg_start = seg_start if _finite(seg_start) else 0.0
        seg_end = seg_end if _finite(seg_end) else seg_start
        seg_end = max(seg_end, seg_start)

        prev_end = seg_start
        for w in seg.get("words", []):
            if not isinstance(w, dict):
                continue
            start = w.get("start_time")
            end = w.get("end_time")

            want_start = start if _finite(start) else prev_end
            new_start = min(max(want_start, seg_start), seg_end)

            want_end = end if _finite(end) else new_start
            new_end = min(max(want_end, new_start), seg_end)

            if new_start != start:
                w["start_time"] = new_start
                changes += 1
            if new_end != end:
                w["end_time"] = new_end
                changes += 1
            prev_end = new_end

    return corrections, changes
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/services/test_timing_sanitizer.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Call the sanitizer in `submit_corrections`**

In `backend/api/routes/jobs.py`, inside `submit_corrections`, immediately before the GCS upload (the `corrections_gcs_path = ...` / `storage.upload_json(...)` block ~L1115), insert:

```python
        # Safety net: never persist word timings that fall outside their segment.
        # Frontend should already prevent this; this guarantees clean stored data
        # (the render worker reads corrections_updated.json verbatim).
        from backend.services.timing_sanitizer import sanitize_corrections
        sanitized, clamp_count = sanitize_corrections(submission.corrections)
        if clamp_count:
            logger.warning(
                f"Job {job_id}: sanitized {clamp_count} out-of-bounds word timing(s) "
                f"in submitted corrections before persisting"
            )
        submission.corrections = sanitized
```

(`submission.corrections` is then uploaded by the existing `storage.upload_json(corrections_gcs_path, submission.corrections)` line.)

- [ ] **Step 6: Write the route-level failing test**

```python
# tests/unit/api/routes/test_jobs_corrections.py
from unittest.mock import patch, MagicMock
from backend.services.timing_sanitizer import sanitize_corrections


def test_submit_sanitizes_before_upload(monkeypatch):
    """The corrections written to GCS must have in-bounds word timings."""
    captured = {}

    def fake_upload_json(path, data):
        captured["data"] = data

    # Sanitizer is the unit under contract here; assert the integration call clamps.
    bad = {
        "corrected_segments": [
            {"id": "s", "start_time": 15.18, "end_time": 18.04, "text": "A",
             "words": [{"id": "a", "text": "A", "start_time": 0, "end_time": -0.005}]}
        ]
    }
    cleaned, count = sanitize_corrections(bad)
    assert count >= 1
    assert cleaned["corrected_segments"][0]["words"][0]["start_time"] >= 15.18
```

(If the repo has FastAPI route integration harnesses under `tests/integration/`, add a fuller request-level test there following the existing pattern; the unit contract above is the minimum.)

- [ ] **Step 7: Run tests**

Run: `python -m pytest tests/unit/services/test_timing_sanitizer.py tests/unit/api/routes/test_jobs_corrections.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/services/timing_sanitizer.py tests/unit/services/test_timing_sanitizer.py tests/unit/api/routes/test_jobs_corrections.py backend/api/routes/jobs.py
git commit -m "feat(api): sanitize out-of-bounds word timings on corrections submit"
```

---

## Task 5: Translate new strings + full suites

**Files:**
- Modify: `frontend/messages/*.json` (generated)

- [ ] **Step 1: Translate the two new keys to all locales**

Run: `cd frontend && python scripts/translate.py --messages-dir messages --target all`
Expected: `timingSanitized` / `timingClamped` added to all 33 locales (cache hits for repeats).

- [ ] **Step 2: Run the frontend suite**

Run: `cd frontend && npx jest lib/lyrics-review hooks components/lyrics-review`
Expected: PASS (all new + existing lyrics-review tests).

- [ ] **Step 3: Run the backend unit suite for touched areas**

Run: `python -m pytest tests/unit/services/test_timing_sanitizer.py tests/unit/api -q`
Expected: PASS.

- [ ] **Step 4: Version bump**

Bump `version` in `pyproject.toml` (patch, e.g. `0.176.2 -> 0.176.3`).

- [ ] **Step 5: Commit**

```bash
git add frontend/messages pyproject.toml
git commit -m "chore: translate timing-validation strings; bump version"
```

---

## Self-review notes (covered)

- **Spec coverage:** Layer 1 → Task 2; Layer 2 → Task 3; Layer 3 → Task 4; shared invariant → Tasks 1 & 4; "segment follows words" → relies on existing `EditModal.updateSegment` min/max derivation (no new code, noted in Architecture); i18n → Tasks 3 & 5; render-time net → already shipped (v0.176.2), unchanged.
- **Naming consistency:** `sanitizeSegmentTimings` (FE), `sanitize_corrections` (BE), `clampSyncTime` (sync) used identically across tasks.
- **No clamp-traps-extension:** clamping references the segment's saved bounds; legit extensions move those bounds via `updateSegment`, so they are never clamped. Only corruption (words diverged from bounds) is repaired.
