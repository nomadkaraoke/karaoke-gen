# LLM-Powered Custom Lyrics Mode — Design

**Date:** 2026-04-28
**Status:** Approved, ready for implementation plan
**Author:** Andrew (with Claude Code, brainstormed in worktree `karaoke-gen-llm-custom-lyrics`)

## Problem

Clients regularly request karaoke jobs with custom lyrics — for weddings, birthdays, bar mitzvahs, parties. The current operator workflow is manual:

1. Submit a normal karaoke-gen job with the original artist/title
2. During lyrics review, fix transcription and segment-level timing
3. Open the existing **Replace Segment Lyrics** modal, copy the full transcribed lyrics out
4. Paste them into an external Claude session along with the client's customisation text/doc
5. Ask the LLM to return the same number of lines, with the client's customisations applied
6. Paste the result back into the Replace Segment Lyrics textarea, save

Step 4 is awkward. The operator has to context-switch into another tool, copy/paste lyrics, manage the prompt, and the LLM has no consistent guardrails (line-count strictness, structural preservation). It also leaks the workflow outside the karaoke-gen UI, which makes it harder to delegate or scale.

## Goal

Bring this LLM-assisted custom-lyrics workflow inside the lyrics review UI as a first-class mode in the existing **Edit All Lyrics** modal, with consistent prompting, file-upload support, line-count guardrails, and a human-in-the-loop preview before the change is committed to the job.

## Non-goals

- Auto-running re-sync after save (user does it manually; we just nudge with a toast)
- Persisting custom-lyrics inputs server-side (the endpoint is stateless)
- Building a side-by-side diff UI in v1 (preview-and-edit textarea is enough)
- Streaming Gemini tokens to the frontend (single synchronous call with spinner is fine)
- Two-pass LLM (generate + critique) — single pass is sufficient
- Supporting `.doc` (legacy Word) files

## High-level shape

A fifth mode card on the existing Edit All Lyrics picker, dispatching to a new `CustomLyricsMode` component. The mode has two phases:

1. **Input phase** — operator provides client custom lyrics (paste text *or* upload `.docx`/`.pdf`/`.txt`/`.md`, mutually exclusive) and optional free-form notes
2. **Preview phase** — Gemini's output lands in an editable textarea with line-count validation, identical to the existing Replace Segment Lyrics editor; Save uses the existing `onSave(newSegments, meta)` path, no new persistence layer

A new backend endpoint `POST /api/review/{job_id}/custom-lyrics/generate` is the LLM orchestrator: it parses the file, builds the prompt, calls Gemini 3.1 Pro via Vertex AI, validates the line count (with a single silent retry), and returns `{lines, warnings, model, line_count_mismatch}`. The endpoint persists nothing; existing review-complete flow handles persistence on Save.

## UX flow

```
ModeSelectionModal (existing)
  └─ Card: "Custom Lyrics" (new, AI-powered tag)
        ↓
CustomLyricsMode (new)
  ├─ Phase: input
  │    ├─ Tabs: [Paste text] [Upload file]
  │    │   - Paste text: <textarea> for client-provided custom lyrics
  │    │   - Upload file: drop-zone for .docx/.pdf/.txt/.md (≤5MB)
  │    ├─ Notes / instructions (optional) <textarea> — operator adds
  │    │  context like "wedding for Jane & John, replace generic 'baby'
  │    │  with names where it fits"
  │    └─ Button: "Generate Custom Lyrics" (disabled until text or file)
  │
  ├─ Phase: generating  (spinner + "Generating with AI… up to a minute")
  │
  └─ Phase: preview
       ├─ Editable <textarea> with one line per segment, pre-populated
       │  with Gemini's output
       ├─ Line-count validation banner (re-uses existing replaceSegments
       │  helper) — green when count matches, red when it doesn't
       ├─ Optional warning banner (line-count mismatch after silent retry)
       ├─ Buttons: [Regenerate] [Cancel] [Save]
       │   - Save disabled until line count matches existingSegments.length
       └─ On Save: build LyricsSegment[] (re-using replaceSegments helper),
          call onSave(newSegments, { operation: 'custom_lyrics_replace',
                                     details: { source: 'text'|'file',
                                                filename?, model } })
          Toast appears: "Manually sync each edited segment to ensure
                          word timings are good for customised lyrics"
```

## Frontend changes

### Files touched

| File | Change |
|---|---|
| `frontend/components/lyrics-review/modals/ModeSelectionModal.tsx` | Add 5th card "Custom Lyrics" with `Sparkles` icon and "AI-powered" tag. New `onSelectCustomLyrics` callback prop. Card is shown only when `hasExistingLyrics` (same gating as `replaceSegments`). |
| `frontend/components/lyrics-review/modals/ReplaceAllLyricsModal.tsx` | Extend `ModalMode` to `\| 'customLyrics'`. Add `customLyrics` branch that mounts `<CustomLyricsMode>`. Wire its `onSave` to the same path used by `replaceSegments`. |
| `frontend/components/lyrics-review/modals/CustomLyricsMode.tsx` | **New.** Encapsulates input form, generate-call state machine, and preview textarea. |
| `frontend/lib/api/customLyrics.ts` | **New.** Tiny client wrapper: `generateCustomLyrics(jobId, params, signal)` returning `{ lines, warnings, model, line_count_mismatch }`. Uses `fetch` with `multipart/form-data` and the admin token from the existing review-auth helper. |
| `frontend/lib/lyrics/segmentsFromLines.ts` | **New.** Extract the existing `text → LyricsSegment[]` builder and line-count validator from `ReplaceAllLyricsModal` into a shared helper. Both `replaceSegments` and `customLyrics` modes import from here. |
| `frontend/messages/en.json` | New keys under `lyricsReview.customLyrics.*`. All other 32 locales auto-generated by `python frontend/scripts/translate.py --target all`. |

### Internal state of `CustomLyricsMode`

```ts
type Phase = 'input' | 'generating' | 'preview'
type InputTab = 'text' | 'file'

interface State {
  phase: Phase
  inputTab: InputTab
  customText: string
  file: File | null
  notes: string
  generatedText: string         // populates the editable preview textarea
  generationWarnings: string[]
  lineCountMismatch: boolean
  error: string | null
  modelUsed: string | null
  abortController: AbortController | null
}
```

### Reuse of existing primitives

- `Dialog`, `Tabs`, `Textarea`, `Button`, `Label`, `ScrollArea`, `Alert`, `Badge` from `@/components/ui/*`
- `Sparkles` icon from `lucide-react` (consistent with the AI motif)
- `useTranslations('lyricsReview.customLyrics')` from next-intl
- The line-count validator and word-builder helpers extracted into `frontend/lib/lyrics/segmentsFromLines.ts`

### File picker UX

Native `<input type="file" accept=".docx,.pdf,.txt,.md">` wrapped in a labeled drop-zone. Client-side validation:

- Extension and mime check (reject otherwise with inline error)
- Size cap: 5 MB (reject otherwise with inline error)
- These checks happen pre-flight so we never spend an API request on a doomed upload

## Backend changes

### New module: `backend/services/custom_lyrics_service.py`

```python
@dataclass
class CustomLyricsResult:
    lines: list[str]
    warnings: list[str]
    model: str
    line_count_mismatch: bool
    duration_ms: int
    retry_count: int


class CustomLyricsService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.logger = logging.getLogger(__name__)

    async def generate(
        self,
        *,
        job_id: str,
        existing_lines: list[str],
        artist: str | None,
        title: str | None,
        custom_text: str | None,
        file_bytes: bytes | None,
        file_mime: str | None,
        file_name: str | None,
        notes: str | None,
    ) -> CustomLyricsResult:
        ...
```

Private helpers:

- `_extract_text_from_docx(bytes) -> str` — `python-docx` (new dep, pure Python, ~1MB)
- `_decode_text(bytes) -> str` — for `.txt` / `.md`
- `_build_user_prompt(...)` — templating
- `_call_gemini(prompt, pdf_bytes?) -> dict` — mirrors `credit_evaluation_service._call_gemini`

### Gemini call

Mirrors the existing pattern in `backend/services/credit_evaluation_service.py:_call_gemini`:

```python
client = genai.Client(
    vertexai=True,
    project=self.settings.google_cloud_project,
    location="global",
)

contents: list[Part] = [Part.from_text(user_prompt)]
if pdf_bytes:
    contents.append(Part.from_bytes(mime_type="application/pdf", data=pdf_bytes))

response = await asyncio.to_thread(
    client.models.generate_content,
    model=self.settings.custom_lyrics_model,  # "gemini-3.1-pro-preview"
    contents=contents,
    config=GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=0.4,
        response_mime_type="application/json",
        response_schema={
            "type": "object",
            "properties": {
                "lines": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["lines"],
        },
        thinking_config=ThinkingConfig(thinking_budget=-1),  # dynamic thinking
    ),
)
```

Structured output (`response_schema`) eliminates brittle string-parsing.

### Retry logic

If `len(parsed.lines) != len(existing_lines)`:

1. **Retry once** with an augmented prompt: *"On your previous attempt you returned X lines. The answer must contain exactly N lines. Try again."*
2. After retry, return whatever we have plus `line_count_mismatch=True` and a warning. The frontend will show this in the editable preview with Save disabled until manually fixed.

### New route: extension to `backend/api/routes/review.py`

```python
@router.post("/{job_id}/custom-lyrics/generate")
async def generate_custom_lyrics(
    job_id: str,
    existing_lines: str = Form(...),         # JSON-encoded list[str]
    custom_text: str | None = Form(None),
    notes: str | None = Form(None),
    file: UploadFile | None = File(None),
    artist: str | None = Form(None),
    title: str | None = Form(None),
    _auth: None = Depends(require_review_auth),
    service: CustomLyricsService = Depends(get_custom_lyrics_service),
) -> CustomLyricsResponse:
    ...
```

Validation:

- At least one of `custom_text` (non-empty after strip) or `file` must be present → 400 otherwise
- `existing_lines` parses to non-empty `list[str]` → 400 otherwise
- File mime ∈ {`docx`, `pdf`, `txt`, `md`} → 400 otherwise
- File size ≤ 5MB → 400 otherwise

Response Pydantic:

```python
class CustomLyricsResponse(BaseModel):
    lines: list[str]
    warnings: list[str]
    model: str
    line_count_mismatch: bool
```

Auth: existing `require_review_auth` dependency (admin-token-gated like the rest of the review router).

### Configuration additions to `backend/config.py`

```python
custom_lyrics_model: str = "gemini-3.1-pro-preview"
custom_lyrics_max_file_mb: int = 5
custom_lyrics_max_input_lines: int = 500     # safety upper bound
```

### Why no persistence layer

The endpoint is a pure LLM-orchestration helper. Saving still happens through the existing `POST /api/review/{job_id}/complete` flow that the editable preview's Save button drives (same path Replace Segment Lyrics uses today). This keeps the blast radius small and means we don't need new Firestore collections, GCS schemas, or worker hooks.

## The prompt

### System prompt (module constant)

```
You are a professional karaoke lyricist. Your task is to produce custom
karaoke lyrics for a song, preserving the original line structure exactly
while applying customisations the client has requested.

RULES (non-negotiable):
1. The output MUST contain exactly the same number of lines as the input
   transcribed lyrics.
2. Each output line corresponds positionally to the same input line
   (line 1 → line 1, etc.). Do not reorder.
3. Try to preserve the syllable count of the original line where possible
   so the lyrics remain singable.
4. Preserve the line's role (verse/chorus/repeat). If the original repeats
   a chorus three times, the customised version should also repeat its
   corresponding chorus three times.
5. Apply the client's customisations as faithfully as possible. If the
   client gave explicit "replace X with Y" instructions, follow them. If
   they gave a free-form theme or names without explicit mapping, use your
   best judgement to weave them in where they fit naturally.
6. Where the client gave no clear customisation for a line, keep the
   original lyric unchanged.
7. Output JSON only, matching the schema:
   {"lines": ["line1", "line2", ...]}
   No commentary, no surrounding prose.
```

### User prompt (constructed per request)

```
SONG: {artist} - {title}

ORIGINAL TRANSCRIBED LYRICS ({N} lines, in singing order):
1. <line 1>
2. <line 2>
...
N. <line N>

CLIENT CUSTOM LYRICS / INSTRUCTIONS:
{either: pasted text, OR extracted text from .docx/.txt/.md, OR "(see attached PDF)" with the PDF as a Part}

ADDITIONAL NOTES FROM OPERATOR:
{notes if provided, else "(none)"}

Produce the customised lyrics in JSON format with exactly {N} lines.
```

Numbered prefixes help the model count; structured-output schema enforces shape.

## Error handling

| Failure | Where caught | What user sees |
|---|---|---|
| File > 5MB or wrong mime | Frontend pre-flight | Inline form error; no request sent |
| `existing_lines` empty | Backend 400 | Toast: "No transcribed lyrics yet; complete transcription first" |
| `python-docx` raises on corrupt file | Backend 400 | Toast: "Could not read .docx file. Please re-export and try again." |
| Gemini API down / 5xx / timeout | Backend 502 (logged) | Toast: "AI service unavailable. Try again in a moment." Modal stays in input phase. |
| Gemini returns empty/non-JSON despite schema | Backend 502 | Same as above |
| Line count mismatch after retry | Backend 200 with `line_count_mismatch=true` | Frontend lands in preview phase with warning banner; Save disabled until line count matches manually or after Regenerate |
| User clicks Regenerate during a generation | Frontend cancels prior `AbortController`, fires new request | No double-fire |
| User closes modal mid-generation | Cleanup via `AbortController` on unmount | No orphaned spinner; no pending callbacks |
| Backend partial failure mid-file-parse | Service raises typed errors; route maps to HTTP codes | Specific error toasts |

### Logging / observability

The service emits structured logs on every call:

```python
self.logger.info(
    "custom_lyrics_generated",
    extra={
        "job_id": job_id,
        "model": result.model,
        "input_size_bytes": ...,
        "input_lines": len(existing_lines),
        "output_lines": len(result.lines),
        "had_pdf": ...,
        "had_docx": ...,
        "had_text": ...,
        "had_notes": ...,
        "duration_ms": ...,
        "retry_count": ...,
        "line_count_mismatch": ...,
    },
)
```

So we can spot quality issues (high mismatch rate, slow calls) from production logs.

### PII / data handling

- Lyrics aren't PII
- Client custom-lyrics inputs (text or file) are held only in request memory and never persisted by this endpoint
- Standard Vertex AI data-residency / privacy posture inherited from the existing Gemini integrations in this project

## Testing strategy

Per `docs/TESTING.md` conventions:

### Backend unit tests — `backend/tests/services/test_custom_lyrics_service.py`

- Happy path with text-only input → returns N lines
- Happy path with `.docx` file (real fixture in `tests/fixtures/custom_lyrics/`)
- Happy path with `.pdf` file passed through (mock Gemini, assert `Part.from_bytes` called with `application/pdf`)
- Happy path with `.txt` and `.md`
- Notes field passed through into prompt
- Retry path: first call wrong count, second call correct count → returns correct, retry_count=1
- Retry exhausted: both calls wrong count → returns result with `line_count_mismatch=True` plus warning
- Gemini exception → service raises typed `CustomLyricsServiceError`
- Corrupt `.docx` → service raises typed error
- File too large at service layer → service raises typed error
- All `genai.Client` calls mocked via `unittest.mock.patch("backend.services.custom_lyrics_service.genai.Client")` returning a MagicMock yielding a canned `GenerateContentResponse` (same approach as `test_credit_evaluation_service.py`)

### Backend route tests — `backend/tests/api/routes/test_review_custom_lyrics.py`

- 401 without admin token
- 400 when neither text nor file provided
- 400 when `existing_lines` is empty / not a JSON array
- 400 with unsupported mime type
- 413/400 when file exceeds size cap
- 200 with valid multipart payload (service mocked)
- 200 with `line_count_mismatch=true` propagation
- Confirms `Depends(require_review_auth)` is wired

### Frontend unit tests — `frontend/components/lyrics-review/modals/__tests__/CustomLyricsMode.test.tsx`

Jest + React Testing Library + MSW for the API mock:

- Renders input phase by default
- Generate button disabled until text or file present
- File picker rejects oversize file with inline error
- Tab toggle between text and file resets the inactive input
- On successful generate: lands in preview, populates textarea, Save enabled when count matches
- Line count mismatch path: shows warning banner, Save disabled
- Regenerate re-fires API call with same inputs
- Toast appears after Save with the exact wording: "Manually sync each edited segment to ensure word timings are good for customised lyrics"
- AbortController fires on unmount mid-generation

### Frontend production E2E — `frontend/e2e/production/custom-lyrics-mode.spec.ts`

- Loads a real production job in review mode (uses existing pattern from `frontend/e2e/production/`)
- Opens Edit All Lyrics → selects Custom Lyrics card
- Pastes simple custom-lyrics text, clicks Generate, waits for response, asserts preview populated
- Clicks Save, asserts toast and that segments updated in underlying state
- Skipped in CI by default; runs on-demand with `KARAOKE_ADMIN_TOKEN` set, per project's prod-test pattern

### No emulator tests needed

The service is stateless and doesn't touch Firestore.

## Dependencies

- **Backend new dep:** `python-docx` (~1 MB pure-Python, BSD licence) — added to `pyproject.toml` under main dependencies
- **No new frontend deps** — the file picker uses native browser APIs; existing `lucide-react` provides `Sparkles`

## Internationalization

All user-facing strings go to `frontend/messages/en.json` under `lyricsReview.customLyrics.*`. After implementation:

```bash
python frontend/scripts/translate.py --messages-dir frontend/messages --target all
```

This auto-translates to all 32 non-English locales using the same Gemini pipeline. CI will fail the PR if any locale is out of date.

## Rollout plan

1. Implement and ship behind no flag — the mode card simply appears for all admins on the review page once merged
2. Bump `pyproject.toml` patch version
3. Backend deploys via existing Cloud Run CI/CD
4. Frontend deploys via existing Cloudflare Pages CI/CD
5. Translation pre-commit hook generates 32 locale files at commit time
6. Validate in production with the `frontend/e2e/production/custom-lyrics-mode.spec.ts` E2E
7. Manual smoke test: real wedding-themed test case end to end

## Out of scope (potential follow-ups)

- Streaming Gemini tokens to the frontend for live progress (Approach 2 from brainstorm)
- Two-pass generate-and-critique (Approach 3 from brainstorm)
- Side-by-side diff view with per-line accept/reject and regenerate-with-feedback
- Saving common custom-lyrics templates per client account
- Auto-running re-sync after Save
- Supporting `.doc` (legacy Word)

## Open questions

None remaining as of approval.
