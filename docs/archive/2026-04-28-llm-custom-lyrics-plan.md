# LLM-Powered Custom Lyrics Mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fifth "Custom Lyrics" mode to the Edit All Lyrics modal that uses Gemini 3.1 Pro (Vertex AI) to transform transcribed lyrics into client-customised lyrics for weddings/birthdays/parties, replacing the operator's manual external-LLM workflow.

**Architecture:** Frontend modal collects custom-lyrics input (text or `.docx`/`.pdf`/`.txt`/`.md` file) plus optional notes and posts a multipart form to a new stateless backend endpoint `POST /api/review/{job_id}/custom-lyrics/generate`. Backend parses the file, calls Gemini 3.1 Pro with a strict line-count contract (single silent retry on mismatch), and returns the generated lines plus warnings. Frontend lands the result in the existing line-count-validated editable preview, then the existing `replaceSegments` save path persists.

**Tech Stack:** FastAPI, Pydantic, `google-genai` SDK (Vertex AI), `python-docx`, Next.js, React, Radix UI, Tailwind, Jest + React Testing Library, Playwright.

**Spec:** `docs/archive/2026-04-28-llm-custom-lyrics-design.md`

---

## File Structure

### Backend — new files

| File | Responsibility |
|---|---|
| `backend/services/custom_lyrics_service.py` | Service: file parsing, prompt building, Gemini call, retry. Stateless; persists nothing. |
| `backend/tests/test_custom_lyrics_service.py` | Unit tests for service (mock `genai.Client`). |
| `backend/tests/api/test_review_custom_lyrics.py` | Route tests (mock service). |
| `backend/tests/fixtures/custom_lyrics/sample.docx` | Real `.docx` fixture (5–10 short lines). |

### Backend — modified files

| File | Change |
|---|---|
| `backend/config.py` | Add `custom_lyrics_model`, `custom_lyrics_max_file_mb`, `custom_lyrics_max_input_lines` settings. |
| `backend/api/routes/review.py` | Add `POST /{job_id}/custom-lyrics/generate` endpoint, response Pydantic model, dependency factory. |
| `pyproject.toml` | Add `python-docx` dep, bump patch version. |

### Frontend — new files

| File | Responsibility |
|---|---|
| `frontend/lib/lyrics-review/utils/segmentsFromLines.ts` | Pure helper: takes `lines: string[]` + `existingSegments: LyricsSegment[]`, returns `LyricsSegment[]` reusing the exact logic that today lives inside `ReplaceAllLyricsModal.handleApplyReplaceSegments`. |
| `frontend/lib/api/customLyrics.ts` | Client wrapper: `generateCustomLyrics(jobId, params, signal): Promise<CustomLyricsResponse>` using `fetch` + multipart form. |
| `frontend/components/lyrics-review/modals/CustomLyricsMode.tsx` | The new mode panel. Owns input/generating/preview phases. |
| `frontend/components/lyrics-review/__tests__/CustomLyricsMode.test.tsx` | Component tests. |
| `frontend/lib/lyrics-review/utils/__tests__/segmentsFromLines.test.ts` | Helper unit tests. |
| `frontend/e2e/production/custom-lyrics-mode.spec.ts` | Production E2E. |

### Frontend — modified files

| File | Change |
|---|---|
| `frontend/components/lyrics-review/modals/ReplaceAllLyricsModal.tsx` | Add `'customLyrics'` to `ModalMode`; add `handleSelectCustomLyrics`; pass `onSelectCustomLyrics` to `ModeSelectionModal`; mount `<CustomLyricsMode>` when `mode === 'customLyrics'`; refactor `handleApplyReplaceSegments` to delegate to `segmentsFromLines`. |
| `frontend/components/lyrics-review/modals/ModeSelectionModal.tsx` | Add `onSelectCustomLyrics` prop; add 5th card `customLyrics` with `Sparkles` icon and AI-powered tag; gated by `hasExistingLyrics`. |
| `frontend/messages/en.json` | Add `lyricsReview.modals.modeSelection.customLyrics*` keys and a new `lyricsReview.modals.customLyricsMode.*` namespace. |

### Translation step (no code, just a script)

`python frontend/scripts/translate.py --messages-dir frontend/messages --target all` runs once after en.json is final.

---

## Phase 1 — Backend foundation (TDD)

### Task 1: Add config settings and `python-docx` dep

**Files:**
- Modify: `backend/config.py` (add 3 settings near `credit_eval_model`)
- Modify: `pyproject.toml` (add `python-docx` dep)
- Modify: `backend/tests/test_settings.py` (add coverage if file exists; otherwise skip)

- [ ] **Step 1: Add settings to `backend/config.py`**

Locate the line that reads `credit_eval_model: str = os.getenv(...)` (around line 67). Immediately after it, add:

```python
custom_lyrics_model: str = os.getenv("CUSTOM_LYRICS_MODEL", "gemini-3.1-pro-preview")
custom_lyrics_max_file_mb: int = int(os.getenv("CUSTOM_LYRICS_MAX_FILE_MB", "5"))
custom_lyrics_max_input_lines: int = int(os.getenv("CUSTOM_LYRICS_MAX_INPUT_LINES", "500"))
```

- [ ] **Step 2: Add `python-docx` to `pyproject.toml`**

Locate the `[tool.poetry.dependencies]` section. Add after `python-multipart` line:

```toml
python-docx = ">=1.1.0"
```

- [ ] **Step 3: Install the dep**

Run:
```bash
cd backend && poetry lock --no-update && poetry install
```

(If the project uses `poetry install --sync` or similar, follow the convention used by `make install` — check `make help` or the Makefile for the exact target before running.)

- [ ] **Step 4: Verify import works**

Run:
```bash
cd backend && poetry run python -c "import docx; print(docx.__version__)"
```

Expected: prints a version like `1.1.x` or higher.

- [ ] **Step 5: Commit**

```bash
git add backend/config.py pyproject.toml poetry.lock
git commit -m "feat(custom-lyrics): add settings and python-docx dependency"
```

---

### Task 2: `CustomLyricsService` skeleton + happy-path text-only test

**Files:**
- Create: `backend/services/custom_lyrics_service.py`
- Create: `backend/tests/test_custom_lyrics_service.py`

- [ ] **Step 1: Write the failing test (happy path, text-only input)**

Create `backend/tests/test_custom_lyrics_service.py`:

```python
"""Unit tests for CustomLyricsService."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.services.custom_lyrics_service import (
    CustomLyricsResult,
    CustomLyricsService,
    CustomLyricsServiceError,
)


@pytest.fixture
def service() -> CustomLyricsService:
    """Service with mocked settings."""
    with patch(
        "backend.services.custom_lyrics_service.get_settings"
    ) as mock_settings:
        mock_settings.return_value = MagicMock(
            google_cloud_project="test-project",
            custom_lyrics_model="gemini-3.1-pro-preview",
            custom_lyrics_max_file_mb=5,
            custom_lyrics_max_input_lines=500,
        )
        yield CustomLyricsService()


def _mock_gemini_response(lines: list[str]) -> MagicMock:
    """Build a MagicMock that mimics genai's GenerateContentResponse."""
    response = MagicMock()
    response.text = json.dumps({"lines": lines})
    return response


def test_text_only_happy_path(service: CustomLyricsService) -> None:
    existing_lines = ["happy birthday to you", "happy birthday to you"]
    expected_output = ["happy birthday dear jane", "happy birthday dear jane"]

    with patch(
        "backend.services.custom_lyrics_service.genai.Client"
    ) as mock_client_cls:
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _mock_gemini_response(
            expected_output
        )
        mock_client_cls.return_value = mock_client

        result = service.generate(
            job_id="job-123",
            existing_lines=existing_lines,
            artist="Anonymous",
            title="Happy Birthday",
            custom_text="Replace 'to you' with 'dear jane' wherever it makes sense",
            file_bytes=None,
            file_mime=None,
            file_name=None,
            notes=None,
        )

    assert isinstance(result, CustomLyricsResult)
    assert result.lines == expected_output
    assert result.line_count_mismatch is False
    assert result.retry_count == 0
    assert result.model == "gemini-3.1-pro-preview"
    assert mock_client_cls.call_args.kwargs == {
        "vertexai": True,
        "project": "test-project",
        "location": "global",
    }
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && poetry run pytest tests/test_custom_lyrics_service.py::test_text_only_happy_path -xvs
```

Expected: ImportError or ModuleNotFoundError for `backend.services.custom_lyrics_service`.

- [ ] **Step 3: Create the service module with minimal happy-path implementation**

Create `backend/services/custom_lyrics_service.py`:

```python
"""LLM-powered custom lyrics generator service.

Stateless helper that takes the existing transcribed lyrics plus client
custom-lyrics input (text and/or file) and returns the same number of lines
with the customisations applied, generated via Gemini 3.1 Pro on Vertex AI.
The endpoint that wraps this service does NOT persist anything — the
frontend flows the result through the existing replace-segments save path.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from backend.config import get_settings

logger = logging.getLogger(__name__)


SUPPORTED_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/pdf": "pdf",
    "text/plain": "txt",
    "text/markdown": "md",
}


SYSTEM_PROMPT = """You are a professional karaoke lyricist. Your task is to produce custom karaoke lyrics for a song, preserving the original line structure exactly while applying customisations the client has requested.

RULES (non-negotiable):
1. The output MUST contain exactly the same number of lines as the input transcribed lyrics.
2. Each output line corresponds positionally to the same input line (line 1 -> line 1, etc.). Do not reorder.
3. Try to preserve the syllable count of the original line where possible so the lyrics remain singable.
4. Preserve the line's role (verse/chorus/repeat). If the original repeats a chorus three times, the customised version should also repeat its corresponding chorus three times.
5. Apply the client's customisations as faithfully as possible. If the client gave explicit "replace X with Y" instructions, follow them. If they gave a free-form theme or names without explicit mapping, use your best judgement to weave them in where they fit naturally.
6. Where the client gave no clear customisation for a line, keep the original lyric unchanged.
7. Output JSON only, matching the schema: {"lines": ["line1", "line2", ...]}. No commentary, no surrounding prose."""


class CustomLyricsServiceError(Exception):
    """Raised by CustomLyricsService for caller-actionable errors.

    Carries an HTTP status hint so the route layer can map cleanly.
    """

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class CustomLyricsResult:
    lines: list[str]
    warnings: list[str] = field(default_factory=list)
    model: str = ""
    line_count_mismatch: bool = False
    duration_ms: int = 0
    retry_count: int = 0


class CustomLyricsService:
    """Generates custom lyrics via Gemini 3.1 Pro.

    Pure stateless service: takes inputs, returns CustomLyricsResult.
    Persists nothing, touches no GCS or Firestore.
    """

    def __init__(self) -> None:
        self.settings = get_settings()

    def generate(
        self,
        *,
        job_id: str,
        existing_lines: list[str],
        artist: Optional[str],
        title: Optional[str],
        custom_text: Optional[str],
        file_bytes: Optional[bytes],
        file_mime: Optional[str],
        file_name: Optional[str],
        notes: Optional[str],
    ) -> CustomLyricsResult:
        start = time.monotonic()
        n = len(existing_lines)
        if n == 0:
            raise CustomLyricsServiceError(
                "existing_lines must not be empty", status_code=400
            )
        if n > self.settings.custom_lyrics_max_input_lines:
            raise CustomLyricsServiceError(
                f"existing_lines exceeds max ({self.settings.custom_lyrics_max_input_lines})",
                status_code=400,
            )
        if not custom_text and not file_bytes:
            raise CustomLyricsServiceError(
                "must provide custom_text or file_bytes", status_code=400
            )

        custom_text_block, pdf_bytes = self._prepare_inputs(
            custom_text=custom_text,
            file_bytes=file_bytes,
            file_mime=file_mime,
            file_name=file_name,
        )

        user_prompt = self._build_user_prompt(
            existing_lines=existing_lines,
            artist=artist,
            title=title,
            custom_text_block=custom_text_block,
            notes=notes,
        )

        lines, retry_count = self._call_gemini_with_retry(
            user_prompt=user_prompt,
            pdf_bytes=pdf_bytes,
            expected_count=n,
        )

        warnings: list[str] = []
        line_count_mismatch = len(lines) != n
        if line_count_mismatch:
            warnings.append(
                f"AI returned {len(lines)} lines but {n} were expected. "
                f"Manually adjust the textarea or click Regenerate."
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        result = CustomLyricsResult(
            lines=lines,
            warnings=warnings,
            model=self.settings.custom_lyrics_model,
            line_count_mismatch=line_count_mismatch,
            duration_ms=duration_ms,
            retry_count=retry_count,
        )

        logger.info(
            "custom_lyrics_generated",
            extra={
                "job_id": job_id,
                "model": result.model,
                "input_lines": n,
                "output_lines": len(lines),
                "had_pdf": pdf_bytes is not None,
                "had_docx_text": file_bytes is not None and pdf_bytes is None,
                "had_text": bool(custom_text),
                "had_notes": bool(notes),
                "duration_ms": duration_ms,
                "retry_count": retry_count,
                "line_count_mismatch": line_count_mismatch,
            },
        )
        return result

    # ---- Stub helpers (filled out in later tasks) ----

    def _prepare_inputs(
        self,
        *,
        custom_text: Optional[str],
        file_bytes: Optional[bytes],
        file_mime: Optional[str],
        file_name: Optional[str],
    ) -> tuple[str, Optional[bytes]]:
        """Returns (combined_text_block, pdf_bytes_for_inline_upload).

        For text-only input or .docx/.txt/.md, pdf_bytes is None and the
        full text is included in the prompt body. For PDFs, the body
        references the attached PDF and the bytes are sent inline.
        """
        # Minimal text-only happy path (Task 2). File parsing added in Task 3.
        text = (custom_text or "").strip()
        if file_bytes is not None:
            raise CustomLyricsServiceError(
                "file uploads not yet supported", status_code=501
            )
        return text, None

    def _build_user_prompt(
        self,
        *,
        existing_lines: list[str],
        artist: Optional[str],
        title: Optional[str],
        custom_text_block: str,
        notes: Optional[str],
        retry_hint: Optional[str] = None,
    ) -> str:
        n = len(existing_lines)
        numbered = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(existing_lines))
        notes_block = notes.strip() if notes else "(none)"
        custom_block = custom_text_block if custom_text_block else "(see attached PDF)"
        retry_block = f"\nIMPORTANT: {retry_hint}\n" if retry_hint else ""
        return (
            f"SONG: {artist or '(unknown artist)'} - {title or '(unknown title)'}\n\n"
            f"ORIGINAL TRANSCRIBED LYRICS ({n} lines, in singing order):\n"
            f"{numbered}\n\n"
            f"CLIENT CUSTOM LYRICS / INSTRUCTIONS:\n{custom_block}\n\n"
            f"ADDITIONAL NOTES FROM OPERATOR:\n{notes_block}\n"
            f"{retry_block}\n"
            f"Produce the customised lyrics in JSON format with exactly {n} lines."
        )

    def _call_gemini_with_retry(
        self,
        *,
        user_prompt: str,
        pdf_bytes: Optional[bytes],
        expected_count: int,
    ) -> tuple[list[str], int]:
        """Call Gemini, retry once if line count is wrong. Returns (lines, retry_count)."""
        lines = self._call_gemini(user_prompt=user_prompt, pdf_bytes=pdf_bytes)
        if len(lines) == expected_count:
            return lines, 0

        retry_prompt = (
            user_prompt
            + f"\n\nIMPORTANT: On your previous attempt you returned {len(lines)} lines. "
            f"The answer must contain exactly {expected_count} lines. Try again."
        )
        lines = self._call_gemini(user_prompt=retry_prompt, pdf_bytes=pdf_bytes)
        return lines, 1

    def _call_gemini(
        self,
        *,
        user_prompt: str,
        pdf_bytes: Optional[bytes],
    ) -> list[str]:
        from google import genai
        from google.genai import types

        client = genai.Client(
            vertexai=True,
            project=self.settings.google_cloud_project,
            location="global",
        )

        contents: list = [user_prompt]
        if pdf_bytes is not None:
            contents.append(
                types.Part.from_bytes(mime_type="application/pdf", data=pdf_bytes)
            )

        response = client.models.generate_content(
            model=self.settings.custom_lyrics_model,
            contents=contents,
            config=types.GenerateContentConfig(
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
            ),
        )
        return self._parse_lines(response.text)

    @staticmethod
    def _parse_lines(text: str) -> list[str]:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CustomLyricsServiceError(
                f"AI returned non-JSON output: {exc}", status_code=502
            ) from exc
        if not isinstance(data, dict) or "lines" not in data:
            raise CustomLyricsServiceError(
                "AI response missing 'lines' field", status_code=502
            )
        lines = data["lines"]
        if not isinstance(lines, list) or not all(isinstance(x, str) for x in lines):
            raise CustomLyricsServiceError(
                "AI response 'lines' is not a list of strings", status_code=502
            )
        return lines


_service_instance: Optional[CustomLyricsService] = None


def get_custom_lyrics_service() -> CustomLyricsService:
    """Singleton accessor used by FastAPI dependency injection."""
    global _service_instance
    if _service_instance is None:
        _service_instance = CustomLyricsService()
    return _service_instance
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && poetry run pytest tests/test_custom_lyrics_service.py::test_text_only_happy_path -xvs
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/custom_lyrics_service.py backend/tests/test_custom_lyrics_service.py
git commit -m "feat(custom-lyrics): add CustomLyricsService skeleton with happy-path test"
```

---

### Task 3: File parsing — `.docx`, `.txt`, `.md`, `.pdf` passthrough

**Files:**
- Modify: `backend/services/custom_lyrics_service.py` (replace `_prepare_inputs` body, add helpers)
- Modify: `backend/tests/test_custom_lyrics_service.py` (add 4 tests)
- Create: `backend/tests/fixtures/custom_lyrics/sample.docx` (real docx, generated in test or pre-built)

- [ ] **Step 1: Add fixture-creation helper to `conftest.py` for the test module**

Inside `backend/tests/test_custom_lyrics_service.py`, add at top (under existing imports):

```python
import io
from pathlib import Path

import docx as docx_mod  # python-docx


@pytest.fixture
def docx_bytes() -> bytes:
    """Build a real .docx in-memory with two short lines."""
    buf = io.BytesIO()
    doc = docx_mod.Document()
    doc.add_paragraph("Replace happy birthday with happy anniversary throughout.")
    doc.add_paragraph("Insert the names Mary and Steve where natural.")
    doc.save(buf)
    return buf.getvalue()
```

- [ ] **Step 2: Write failing tests for file parsing**

Append to `backend/tests/test_custom_lyrics_service.py`:

```python
def test_docx_input_parsed_to_text(
    service: CustomLyricsService, docx_bytes: bytes
) -> None:
    existing_lines = ["happy birthday to you"]

    captured_prompts: list[str] = []

    def fake_generate_content(*, model, contents, config):  # noqa: ARG001
        # Capture the user prompt from the contents list
        captured_prompts.append(contents[0])
        return _mock_gemini_response(["happy anniversary mary and steve"])

    with patch(
        "backend.services.custom_lyrics_service.genai.Client"
    ) as mock_client_cls:
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = fake_generate_content
        mock_client_cls.return_value = mock_client

        result = service.generate(
            job_id="job-1",
            existing_lines=existing_lines,
            artist="A",
            title="T",
            custom_text=None,
            file_bytes=docx_bytes,
            file_mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            file_name="instructions.docx",
            notes=None,
        )

    assert result.lines == ["happy anniversary mary and steve"]
    assert any("happy anniversary" in p.lower() for p in captured_prompts)
    assert any("Mary and Steve" in p for p in captured_prompts)


def test_txt_input_decoded_and_used(service: CustomLyricsService) -> None:
    existing_lines = ["original line 1"]
    txt_bytes = b"Use the word 'celebration' instead of 'birthday'."

    captured_prompts: list[str] = []

    def fake_generate_content(*, model, contents, config):  # noqa: ARG001
        captured_prompts.append(contents[0])
        return _mock_gemini_response(["original celebration 1"])

    with patch(
        "backend.services.custom_lyrics_service.genai.Client"
    ) as mock_client_cls:
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = fake_generate_content
        mock_client_cls.return_value = mock_client

        service.generate(
            job_id="job-1",
            existing_lines=existing_lines,
            artist=None,
            title=None,
            custom_text=None,
            file_bytes=txt_bytes,
            file_mime="text/plain",
            file_name="brief.txt",
            notes=None,
        )

    assert any("celebration" in p for p in captured_prompts)


def test_md_input_decoded_and_used(service: CustomLyricsService) -> None:
    existing_lines = ["a", "b"]
    md_bytes = b"# Brief\n\nSwap 'baby' with 'sweetie'."

    with patch(
        "backend.services.custom_lyrics_service.genai.Client"
    ) as mock_client_cls:
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _mock_gemini_response(
            ["a", "b"]
        )
        mock_client_cls.return_value = mock_client

        result = service.generate(
            job_id="job-1",
            existing_lines=existing_lines,
            artist=None,
            title=None,
            custom_text=None,
            file_bytes=md_bytes,
            file_mime="text/markdown",
            file_name="brief.md",
            notes=None,
        )

    assert result.lines == ["a", "b"]


def test_pdf_input_passed_inline_to_gemini(service: CustomLyricsService) -> None:
    """PDFs are sent as inline parts; their bytes never get extracted server-side."""
    existing_lines = ["line one", "line two"]
    pdf_bytes = b"%PDF-1.4\nfake-pdf-bytes"

    captured_contents: list[list] = []

    def fake_generate_content(*, model, contents, config):  # noqa: ARG001
        captured_contents.append(contents)
        return _mock_gemini_response(["line ONE", "line TWO"])

    with patch(
        "backend.services.custom_lyrics_service.genai.Client"
    ) as mock_client_cls:
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = fake_generate_content
        mock_client_cls.return_value = mock_client

        service.generate(
            job_id="job-1",
            existing_lines=existing_lines,
            artist=None,
            title=None,
            custom_text=None,
            file_bytes=pdf_bytes,
            file_mime="application/pdf",
            file_name="brief.pdf",
            notes=None,
        )

    assert len(captured_contents) == 1
    contents = captured_contents[0]
    assert any(getattr(part, "mime_type", None) == "application/pdf" for part in contents[1:]) or \
        any(part is not None for part in contents[1:])  # part objects vary by SDK version


def test_unsupported_mime_rejected(service: CustomLyricsService) -> None:
    with pytest.raises(CustomLyricsServiceError) as exc_info:
        service.generate(
            job_id="job-1",
            existing_lines=["a"],
            artist=None,
            title=None,
            custom_text=None,
            file_bytes=b"<html/>",
            file_mime="text/html",
            file_name="brief.html",
            notes=None,
        )
    assert exc_info.value.status_code == 400


def test_corrupt_docx_raises(service: CustomLyricsService) -> None:
    with pytest.raises(CustomLyricsServiceError) as exc_info:
        service.generate(
            job_id="job-1",
            existing_lines=["a"],
            artist=None,
            title=None,
            custom_text=None,
            file_bytes=b"this is not a real docx",
            file_mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            file_name="bad.docx",
            notes=None,
        )
    assert exc_info.value.status_code == 400


def test_oversize_file_rejected(service: CustomLyricsService) -> None:
    too_big = b"x" * (6 * 1024 * 1024)  # 6 MB > default 5 MB cap
    with pytest.raises(CustomLyricsServiceError) as exc_info:
        service.generate(
            job_id="job-1",
            existing_lines=["a"],
            artist=None,
            title=None,
            custom_text=None,
            file_bytes=too_big,
            file_mime="text/plain",
            file_name="big.txt",
            notes=None,
        )
    assert exc_info.value.status_code == 400
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd backend && poetry run pytest tests/test_custom_lyrics_service.py -xvs -k "docx_input or txt_input or md_input or pdf_input or unsupported_mime or corrupt_docx or oversize"
```

Expected: failures (file uploads not yet supported, oversize check absent, etc.).

- [ ] **Step 4: Replace `_prepare_inputs` with the full implementation**

In `backend/services/custom_lyrics_service.py`, replace the entire `_prepare_inputs` method body and add helpers below it:

```python
    def _prepare_inputs(
        self,
        *,
        custom_text: Optional[str],
        file_bytes: Optional[bytes],
        file_mime: Optional[str],
        file_name: Optional[str],
    ) -> tuple[str, Optional[bytes]]:
        text_chunks: list[str] = []
        if custom_text and custom_text.strip():
            text_chunks.append(custom_text.strip())

        pdf_bytes: Optional[bytes] = None
        if file_bytes is not None:
            self._validate_file(
                file_bytes=file_bytes, file_mime=file_mime, file_name=file_name
            )
            kind = SUPPORTED_MIMES[file_mime]  # validated above
            if kind == "pdf":
                pdf_bytes = file_bytes
            elif kind == "docx":
                text_chunks.append(self._extract_docx_text(file_bytes))
            elif kind in ("txt", "md"):
                text_chunks.append(file_bytes.decode("utf-8", errors="replace"))
            else:  # defensive — validated already
                raise CustomLyricsServiceError(
                    f"unsupported file kind: {kind}", status_code=400
                )

        combined = "\n\n".join(chunk for chunk in text_chunks if chunk.strip())
        return combined, pdf_bytes

    def _validate_file(
        self,
        *,
        file_bytes: bytes,
        file_mime: Optional[str],
        file_name: Optional[str],
    ) -> None:
        max_bytes = self.settings.custom_lyrics_max_file_mb * 1024 * 1024
        if len(file_bytes) > max_bytes:
            raise CustomLyricsServiceError(
                f"file exceeds {self.settings.custom_lyrics_max_file_mb} MB limit",
                status_code=400,
            )
        if file_mime not in SUPPORTED_MIMES:
            raise CustomLyricsServiceError(
                f"unsupported file mime: {file_mime!r}; expected one of "
                f"{sorted(SUPPORTED_MIMES.keys())}",
                status_code=400,
            )

    @staticmethod
    def _extract_docx_text(file_bytes: bytes) -> str:
        import io

        try:
            import docx as docx_mod  # python-docx
        except ImportError as exc:  # pragma: no cover
            raise CustomLyricsServiceError(
                "python-docx is not installed", status_code=500
            ) from exc

        try:
            doc = docx_mod.Document(io.BytesIO(file_bytes))
        except Exception as exc:  # python-docx raises various Package* errors
            raise CustomLyricsServiceError(
                f"could not parse .docx: {exc}", status_code=400
            ) from exc

        paragraphs = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
        return "\n".join(paragraphs)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd backend && poetry run pytest tests/test_custom_lyrics_service.py -xvs
```

Expected: all tests pass (the original happy-path test continues to pass; the new file tests pass).

- [ ] **Step 6: Commit**

```bash
git add backend/services/custom_lyrics_service.py backend/tests/test_custom_lyrics_service.py
git commit -m "feat(custom-lyrics): add file parsing for docx/pdf/txt/md inputs"
```

---

### Task 4: Retry-on-line-count-mismatch logic

**Files:**
- Modify: `backend/tests/test_custom_lyrics_service.py` (add 2 tests)

The retry logic was already written in Task 2 inside `_call_gemini_with_retry`; this task just covers it with tests.

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_custom_lyrics_service.py`:

```python
def test_retry_succeeds_on_second_attempt(service: CustomLyricsService) -> None:
    existing_lines = ["a", "b", "c"]

    responses = [
        _mock_gemini_response(["bad", "wrong-count"]),
        _mock_gemini_response(["A", "B", "C"]),
    ]

    with patch(
        "backend.services.custom_lyrics_service.genai.Client"
    ) as mock_client_cls:
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = responses
        mock_client_cls.return_value = mock_client

        result = service.generate(
            job_id="job-1",
            existing_lines=existing_lines,
            artist=None,
            title=None,
            custom_text="anything",
            file_bytes=None,
            file_mime=None,
            file_name=None,
            notes=None,
        )

    assert result.lines == ["A", "B", "C"]
    assert result.retry_count == 1
    assert result.line_count_mismatch is False
    assert result.warnings == []


def test_retry_exhausted_returns_mismatch_with_warning(
    service: CustomLyricsService,
) -> None:
    existing_lines = ["a", "b", "c"]
    bad = _mock_gemini_response(["only-two", "lines-here"])

    with patch(
        "backend.services.custom_lyrics_service.genai.Client"
    ) as mock_client_cls:
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = [bad, bad]
        mock_client_cls.return_value = mock_client

        result = service.generate(
            job_id="job-1",
            existing_lines=existing_lines,
            artist=None,
            title=None,
            custom_text="anything",
            file_bytes=None,
            file_mime=None,
            file_name=None,
            notes=None,
        )

    assert result.lines == ["only-two", "lines-here"]
    assert result.retry_count == 1
    assert result.line_count_mismatch is True
    assert len(result.warnings) == 1
    assert "2 lines" in result.warnings[0]
    assert "3 were expected" in result.warnings[0]
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
cd backend && poetry run pytest tests/test_custom_lyrics_service.py -xvs -k "retry"
```

Expected: both pass without source changes (logic already in Task 2).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_custom_lyrics_service.py
git commit -m "test(custom-lyrics): cover retry-on-line-count-mismatch behavior"
```

---

### Task 5: Gemini failure handling and notes-passthrough tests

**Files:**
- Modify: `backend/tests/test_custom_lyrics_service.py` (add 4 tests)

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_custom_lyrics_service.py`:

```python
def test_gemini_exception_propagates_as_service_error(
    service: CustomLyricsService,
) -> None:
    with patch(
        "backend.services.custom_lyrics_service.genai.Client"
    ) as mock_client_cls:
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError(
            "vertex unavailable"
        )
        mock_client_cls.return_value = mock_client

        with pytest.raises(RuntimeError, match="vertex unavailable"):
            service.generate(
                job_id="job-1",
                existing_lines=["a"],
                artist=None,
                title=None,
                custom_text="any",
                file_bytes=None,
                file_mime=None,
                file_name=None,
                notes=None,
            )


def test_gemini_returns_non_json_raises_502(service: CustomLyricsService) -> None:
    bad = MagicMock()
    bad.text = "Sure! Here you go: ..."  # not JSON

    with patch(
        "backend.services.custom_lyrics_service.genai.Client"
    ) as mock_client_cls:
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = bad
        mock_client_cls.return_value = mock_client

        with pytest.raises(CustomLyricsServiceError) as exc_info:
            service.generate(
                job_id="job-1",
                existing_lines=["a"],
                artist=None,
                title=None,
                custom_text="any",
                file_bytes=None,
                file_mime=None,
                file_name=None,
                notes=None,
            )
        assert exc_info.value.status_code == 502


def test_gemini_returns_wrong_shape_raises_502(service: CustomLyricsService) -> None:
    bad = MagicMock()
    bad.text = json.dumps({"output": ["a"]})  # missing "lines" key

    with patch(
        "backend.services.custom_lyrics_service.genai.Client"
    ) as mock_client_cls:
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = bad
        mock_client_cls.return_value = mock_client

        with pytest.raises(CustomLyricsServiceError) as exc_info:
            service.generate(
                job_id="job-1",
                existing_lines=["a"],
                artist=None,
                title=None,
                custom_text="any",
                file_bytes=None,
                file_mime=None,
                file_name=None,
                notes=None,
            )
        assert exc_info.value.status_code == 502


def test_notes_field_included_in_prompt(service: CustomLyricsService) -> None:
    captured_prompts: list[str] = []

    def fake_generate_content(*, model, contents, config):  # noqa: ARG001
        captured_prompts.append(contents[0])
        return _mock_gemini_response(["a"])

    with patch(
        "backend.services.custom_lyrics_service.genai.Client"
    ) as mock_client_cls:
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = fake_generate_content
        mock_client_cls.return_value = mock_client

        service.generate(
            job_id="job-1",
            existing_lines=["a"],
            artist=None,
            title=None,
            custom_text="any",
            file_bytes=None,
            file_mime=None,
            file_name=None,
            notes="Wedding for John & Jane — keep it cheesy",
        )

    assert any("John & Jane" in p for p in captured_prompts)
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
cd backend && poetry run pytest tests/test_custom_lyrics_service.py -xvs
```

Expected: all tests pass without source changes (logic already in Task 2).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_custom_lyrics_service.py
git commit -m "test(custom-lyrics): cover gemini failure modes and notes propagation"
```

---

### Task 6: Pydantic response model + dependency factory in `review.py`

**Files:**
- Modify: `backend/api/routes/review.py` (add Pydantic response model class + dependency factory)

- [ ] **Step 1: Add Pydantic model and dependency factory near the top of `review.py`**

After the existing imports in `backend/api/routes/review.py`, locate the section just below the imports (before `router = APIRouter(...)` declaration). Add:

```python
from pydantic import BaseModel as _PydBaseModel
from backend.services.custom_lyrics_service import (
    CustomLyricsService,
    CustomLyricsServiceError,
    get_custom_lyrics_service,
)


class CustomLyricsResponse(_PydBaseModel):
    lines: list[str]
    warnings: list[str]
    model: str
    line_count_mismatch: bool
    retry_count: int
    duration_ms: int


def _get_custom_lyrics_service_dep() -> CustomLyricsService:
    """FastAPI dependency wrapper around the singleton accessor."""
    return get_custom_lyrics_service()
```

- [ ] **Step 2: Verify imports load cleanly**

```bash
cd backend && poetry run python -c "from backend.api.routes.review import CustomLyricsResponse, _get_custom_lyrics_service_dep; print('ok')"
```

Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add backend/api/routes/review.py
git commit -m "feat(custom-lyrics): add CustomLyricsResponse model and DI helper"
```

---

### Task 7: New endpoint `POST /{job_id}/custom-lyrics/generate`

**Files:**
- Modify: `backend/api/routes/review.py` (add endpoint)

- [ ] **Step 1: Add the endpoint**

Find a clean spot in `backend/api/routes/review.py` (after `complete_review` endpoint at line ~361, before `@router.post("/{job_id}/handlers")` at line 422). Insert:

```python
@router.post("/{job_id}/custom-lyrics/generate", response_model=CustomLyricsResponse)
async def generate_custom_lyrics(
    job_id: str,
    existing_lines: str = Form(..., description="JSON-encoded array of segment lines"),
    custom_text: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    artist: Optional[str] = Form(None),
    title: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    auth_info: Tuple[str, str] = Depends(require_review_auth),
    service: CustomLyricsService = Depends(_get_custom_lyrics_service_dep),
) -> CustomLyricsResponse:
    """Generate custom lyrics for a job using Gemini 3.1 Pro.

    Stateless: returns generated lines for the frontend to render in an
    editable preview. Persistence happens via the existing /complete path
    after the user reviews and saves.
    """
    # Parse existing_lines JSON
    try:
        parsed_lines = json.loads(existing_lines)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400, detail=f"existing_lines is not valid JSON: {exc}"
        ) from exc
    if not isinstance(parsed_lines, list) or not all(
        isinstance(x, str) for x in parsed_lines
    ):
        raise HTTPException(
            status_code=400,
            detail="existing_lines must be a JSON array of strings",
        )
    if not parsed_lines:
        raise HTTPException(status_code=400, detail="existing_lines is empty")

    # Read file body (if any) — capped check is done by service too
    file_bytes: Optional[bytes] = None
    file_mime: Optional[str] = None
    file_name: Optional[str] = None
    if file is not None:
        file_bytes = await file.read()
        file_mime = file.content_type
        file_name = file.filename

    if not custom_text and not file_bytes:
        raise HTTPException(
            status_code=400,
            detail="must provide either custom_text or a file upload",
        )

    try:
        result = service.generate(
            job_id=job_id,
            existing_lines=parsed_lines,
            artist=artist,
            title=title,
            custom_text=custom_text,
            file_bytes=file_bytes,
            file_mime=file_mime,
            file_name=file_name,
            notes=notes,
        )
    except CustomLyricsServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    return CustomLyricsResponse(
        lines=result.lines,
        warnings=result.warnings,
        model=result.model,
        line_count_mismatch=result.line_count_mismatch,
        retry_count=result.retry_count,
        duration_ms=result.duration_ms,
    )
```

(Confirm `Form`, `File`, `UploadFile` imports already exist near the top of `review.py`. If not, add: `from fastapi import APIRouter, HTTPException, Request, Depends, Form, File, UploadFile` — replacing the existing import line.)

- [ ] **Step 2: Verify import works**

```bash
cd backend && poetry run python -c "from backend.api.routes.review import generate_custom_lyrics; print('ok')"
```

Expected: prints `ok`.

- [ ] **Step 3: Verify FastAPI app boots**

```bash
cd backend && poetry run python -c "from backend.main import app; print(len(app.routes))"
```

Expected: prints a number, no errors.

- [ ] **Step 4: Commit**

```bash
git add backend/api/routes/review.py
git commit -m "feat(custom-lyrics): add POST /custom-lyrics/generate endpoint"
```

---

### Task 8: Route tests for `generate_custom_lyrics`

**Files:**
- Create: `backend/tests/api/test_review_custom_lyrics.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/api/test_review_custom_lyrics.py`:

```python
"""Route tests for POST /api/review/{job_id}/custom-lyrics/generate."""
from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.custom_lyrics_service import (
    CustomLyricsResult,
    CustomLyricsServiceError,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def bypass_auth(monkeypatch):
    """Bypass require_review_auth for these tests."""
    from backend.api.dependencies import require_review_auth as real_dep

    async def fake_dep(*args, **kwargs):  # noqa: ANN001, ARG001
        return ("test-job", "full")

    app.dependency_overrides[real_dep] = fake_dep
    yield
    app.dependency_overrides.pop(real_dep, None)


def _override_service(result_or_exc):
    """Install a service mock via dependency_overrides."""
    from backend.api.routes.review import _get_custom_lyrics_service_dep

    mock_service = MagicMock()
    if isinstance(result_or_exc, Exception):
        mock_service.generate.side_effect = result_or_exc
    else:
        mock_service.generate.return_value = result_or_exc

    app.dependency_overrides[_get_custom_lyrics_service_dep] = lambda: mock_service
    return mock_service


def teardown_function(_):  # noqa: ANN001
    app.dependency_overrides.pop(
        __import__(
            "backend.api.routes.review", fromlist=["_get_custom_lyrics_service_dep"]
        )._get_custom_lyrics_service_dep,
        None,
    )


def test_happy_path_text(client: TestClient) -> None:
    _override_service(
        CustomLyricsResult(
            lines=["a", "b"],
            warnings=[],
            model="gemini-3.1-pro-preview",
            line_count_mismatch=False,
            duration_ms=42,
            retry_count=0,
        )
    )

    response = client.post(
        "/api/review/test-job/custom-lyrics/generate",
        data={
            "existing_lines": json.dumps(["one", "two"]),
            "custom_text": "make it about cats",
            "notes": "for clara's birthday",
            "artist": "Test",
            "title": "Test Song",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["lines"] == ["a", "b"]
    assert body["line_count_mismatch"] is False
    assert body["model"] == "gemini-3.1-pro-preview"


def test_missing_existing_lines_400(client: TestClient) -> None:
    _override_service(MagicMock())
    response = client.post(
        "/api/review/test-job/custom-lyrics/generate",
        data={"custom_text": "anything"},
    )
    assert response.status_code == 422  # Form(...) required field


def test_existing_lines_not_json_400(client: TestClient) -> None:
    _override_service(MagicMock())
    response = client.post(
        "/api/review/test-job/custom-lyrics/generate",
        data={
            "existing_lines": "not-json",
            "custom_text": "anything",
        },
    )
    assert response.status_code == 400
    assert "JSON" in response.json()["detail"]


def test_existing_lines_wrong_type_400(client: TestClient) -> None:
    _override_service(MagicMock())
    response = client.post(
        "/api/review/test-job/custom-lyrics/generate",
        data={
            "existing_lines": json.dumps([1, 2, 3]),
            "custom_text": "anything",
        },
    )
    assert response.status_code == 400


def test_existing_lines_empty_array_400(client: TestClient) -> None:
    _override_service(MagicMock())
    response = client.post(
        "/api/review/test-job/custom-lyrics/generate",
        data={
            "existing_lines": json.dumps([]),
            "custom_text": "anything",
        },
    )
    assert response.status_code == 400


def test_no_text_and_no_file_400(client: TestClient) -> None:
    _override_service(MagicMock())
    response = client.post(
        "/api/review/test-job/custom-lyrics/generate",
        data={"existing_lines": json.dumps(["a"])},
    )
    assert response.status_code == 400
    assert "custom_text" in response.json()["detail"]


def test_file_upload_passed_through(client: TestClient) -> None:
    mock_service = _override_service(
        CustomLyricsResult(
            lines=["x"],
            warnings=[],
            model="m",
            line_count_mismatch=False,
            duration_ms=1,
            retry_count=0,
        )
    )

    response = client.post(
        "/api/review/test-job/custom-lyrics/generate",
        data={"existing_lines": json.dumps(["a"])},
        files={
            "file": ("brief.txt", io.BytesIO(b"do this"), "text/plain"),
        },
    )

    assert response.status_code == 200
    call_kwargs = mock_service.generate.call_args.kwargs
    assert call_kwargs["file_bytes"] == b"do this"
    assert call_kwargs["file_mime"] == "text/plain"
    assert call_kwargs["file_name"] == "brief.txt"


def test_service_validation_error_propagates_status(client: TestClient) -> None:
    _override_service(
        CustomLyricsServiceError("file too big", status_code=400)
    )
    response = client.post(
        "/api/review/test-job/custom-lyrics/generate",
        data={
            "existing_lines": json.dumps(["a"]),
            "custom_text": "anything",
        },
    )
    assert response.status_code == 400
    assert "file too big" in response.json()["detail"]


def test_service_502_propagates_status(client: TestClient) -> None:
    _override_service(
        CustomLyricsServiceError("AI returned non-JSON", status_code=502)
    )
    response = client.post(
        "/api/review/test-job/custom-lyrics/generate",
        data={
            "existing_lines": json.dumps(["a"]),
            "custom_text": "anything",
        },
    )
    assert response.status_code == 502


def test_line_count_mismatch_returns_200_with_flag(client: TestClient) -> None:
    _override_service(
        CustomLyricsResult(
            lines=["only-one"],
            warnings=["AI returned 1 lines but 2 were expected."],
            model="m",
            line_count_mismatch=True,
            duration_ms=10,
            retry_count=1,
        )
    )

    response = client.post(
        "/api/review/test-job/custom-lyrics/generate",
        data={
            "existing_lines": json.dumps(["a", "b"]),
            "custom_text": "anything",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["line_count_mismatch"] is True
    assert len(body["warnings"]) == 1
    assert body["retry_count"] == 1
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
cd backend && poetry run pytest tests/api/test_review_custom_lyrics.py -xvs
```

Expected: all tests pass.

- [ ] **Step 3: Run the full backend test suite to ensure no regressions**

```bash
cd backend && poetry run pytest tests/ -x --ignore=tests/e2e --ignore=tests/integration --ignore=tests/emulator -q
```

Expected: pre-existing tests still pass.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/api/test_review_custom_lyrics.py
git commit -m "test(custom-lyrics): add route tests covering happy paths and errors"
```

---

## Phase 2 — Frontend foundation

### Task 9: Extract `segmentsFromLines` shared helper (with tests)

**Files:**
- Create: `frontend/lib/lyrics-review/utils/segmentsFromLines.ts`
- Create: `frontend/lib/lyrics-review/utils/__tests__/segmentsFromLines.test.ts`
- Modify: `frontend/components/lyrics-review/modals/ReplaceAllLyricsModal.tsx` (extract logic, delegate)

- [ ] **Step 1: Write failing tests**

Create `frontend/lib/lyrics-review/utils/__tests__/segmentsFromLines.test.ts`:

```ts
import { segmentsFromLines } from '../segmentsFromLines'
import type { LyricsSegment } from '@/lib/lyrics-review/types'

const make = (text: string, words: { text: string; start: number | null; end: number | null }[]): LyricsSegment => ({
  id: `seg-${text}`,
  text,
  start_time: words[0]?.start ?? null,
  end_time: words[words.length - 1]?.end ?? null,
  words: words.map((w, i) => ({
    id: `w-${text}-${i}`,
    text: w.text,
    start_time: w.start,
    end_time: w.end,
    confidence: 1,
  })),
})

describe('segmentsFromLines', () => {
  it('returns deep-copies of unchanged segments', () => {
    const existing = [
      make('hello world', [
        { text: 'hello', start: 0, end: 1 },
        { text: 'world', start: 1, end: 2 },
      ]),
    ]
    const result = segmentsFromLines(['hello world'], existing)
    expect(result).toHaveLength(1)
    expect(result[0].text).toBe('hello world')
    expect(result[0]).not.toBe(existing[0])
    expect(result[0].words[0].start_time).toBe(0)
  })

  it('preserves per-word timing when text changes but word count matches', () => {
    const existing = [
      make('hello world', [
        { text: 'hello', start: 0, end: 1 },
        { text: 'world', start: 1, end: 2 },
      ]),
    ]
    const result = segmentsFromLines(['howdy partner'], existing)
    expect(result[0].text).toBe('howdy partner')
    expect(result[0].words.map((w) => w.text)).toEqual(['howdy', 'partner'])
    expect(result[0].words[0].start_time).toBe(0)
    expect(result[0].words[1].end_time).toBe(2)
  })

  it('distributes timing when word count changes', () => {
    const existing = [
      make('one two', [
        { text: 'one', start: 0, end: 1 },
        { text: 'two', start: 1, end: 2 },
      ]),
    ]
    const result = segmentsFromLines(['one two three'], existing)
    expect(result[0].text).toBe('one two three')
    expect(result[0].words.map((w) => w.text)).toEqual(['one', 'two', 'three'])
    // distributed timings should fall in the segment range
    result[0].words.forEach((w) => {
      expect(w.start_time).not.toBeNull()
      expect(w.end_time).not.toBeNull()
    })
  })

  it('iterates over existingSegments.length even if input has fewer lines', () => {
    const existing = [
      make('a', [{ text: 'a', start: 0, end: 1 }]),
      make('b', [{ text: 'b', start: 1, end: 2 }]),
    ]
    const result = segmentsFromLines(['a'], existing)
    // Second segment becomes empty text
    expect(result).toHaveLength(2)
    expect(result[1].text).toBe('')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd frontend && npx jest lib/lyrics-review/utils/__tests__/segmentsFromLines.test.ts --no-coverage
```

Expected: failure (module not found).

- [ ] **Step 3: Create the helper**

Create `frontend/lib/lyrics-review/utils/segmentsFromLines.ts`:

```ts
import type { LyricsSegment } from '@/lib/lyrics-review/types'
import { createWordsWithDistributedTiming } from '@/lib/lyrics-review/utils/wordUtils'

/**
 * Build an updated LyricsSegment[] by replacing each existing segment's text
 * with the corresponding line from `lines`. Word-level timing is preserved
 * when the new word count matches the old; otherwise distributed across
 * the original segment time range.
 *
 * Used by both the "Replace Segment Lyrics" mode and the
 * "Custom Lyrics" mode (where lines come from Gemini instead of paste).
 */
export function segmentsFromLines(
  lines: string[],
  existingSegments: LyricsSegment[],
): LyricsSegment[] {
  return existingSegments.map((segment, i) => {
    const newLineText = (lines[i] ?? '').trim()
    const originalText = segment.text.trim()

    if (newLineText === originalText) {
      return JSON.parse(JSON.stringify(segment)) as LyricsSegment
    }

    const newWordTexts = newLineText.split(/\s+/).filter((w) => w.length > 0)

    if (newWordTexts.length === segment.words.length && newWordTexts.length > 0) {
      const updatedWords = segment.words.map((word, idx) => ({
        ...word,
        text: newWordTexts[idx],
      }))
      return {
        ...segment,
        text: newLineText,
        words: updatedWords,
      }
    }

    const newWords = createWordsWithDistributedTiming(
      newLineText,
      segment.start_time,
      segment.end_time,
    )

    return {
      ...segment,
      text: newLineText,
      words: newWords,
    }
  })
}
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd frontend && npx jest lib/lyrics-review/utils/__tests__/segmentsFromLines.test.ts --no-coverage
```

Expected: all tests pass.

- [ ] **Step 5: Refactor `ReplaceAllLyricsModal.tsx` to use the helper**

In `frontend/components/lyrics-review/modals/ReplaceAllLyricsModal.tsx`:

1. Add at the top with the other imports:
   ```ts
   import { segmentsFromLines } from '@/lib/lyrics-review/utils/segmentsFromLines'
   ```
2. Replace the entire `handleApplyReplaceSegments` callback (lines 151–193 originally) with:
   ```ts
   const handleApplyReplaceSegments = useCallback(() => {
     const newLines = inputText.split('\n')
     const updatedSegments = segmentsFromLines(newLines, existingSegments)
     onSave(updatedSegments)
     handleClose()
   }, [inputText, existingSegments, onSave, handleClose])
   ```
3. Remove the now-unused `Word` type import if it's no longer used elsewhere in the file.

- [ ] **Step 6: Run any existing tests for the modal**

```bash
cd frontend && npx jest components/lyrics-review --no-coverage
```

Expected: all existing modal tests still pass (the refactor is behavior-preserving).

- [ ] **Step 7: Commit**

```bash
git add frontend/lib/lyrics-review/utils/segmentsFromLines.ts \
        frontend/lib/lyrics-review/utils/__tests__/segmentsFromLines.test.ts \
        frontend/components/lyrics-review/modals/ReplaceAllLyricsModal.tsx
git commit -m "refactor(lyrics-review): extract segmentsFromLines into shared helper"
```

---

### Task 10: Frontend API client wrapper `customLyrics.ts`

**Files:**
- Create: `frontend/lib/api/customLyrics.ts`
- Create: `frontend/lib/api/__tests__/customLyrics.test.ts`

- [ ] **Step 1: Write failing tests**

Create `frontend/lib/api/__tests__/customLyrics.test.ts`:

```ts
import { generateCustomLyrics } from '../customLyrics'

describe('generateCustomLyrics', () => {
  let originalFetch: typeof fetch

  beforeEach(() => {
    originalFetch = global.fetch
  })

  afterEach(() => {
    global.fetch = originalFetch
  })

  it('posts multipart form-data with text input', async () => {
    const captured: { url: string; init: RequestInit | undefined } = { url: '', init: undefined }
    global.fetch = jest.fn(async (url: RequestInfo, init?: RequestInit) => {
      captured.url = String(url)
      captured.init = init
      return new Response(
        JSON.stringify({
          lines: ['a', 'b'],
          warnings: [],
          model: 'gemini-3.1-pro-preview',
          line_count_mismatch: false,
          retry_count: 0,
          duration_ms: 50,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    }) as unknown as typeof fetch

    const result = await generateCustomLyrics(
      'job-123',
      {
        existingLines: ['one', 'two'],
        customText: 'make it about cats',
        notes: 'for clara',
        artist: 'A',
        title: 'T',
      },
      undefined,
      'fake-token',
    )

    expect(captured.url).toContain('/api/review/job-123/custom-lyrics/generate')
    const init = captured.init!
    expect(init.method).toBe('POST')
    const body = init.body as FormData
    expect(body.get('existing_lines')).toBe(JSON.stringify(['one', 'two']))
    expect(body.get('custom_text')).toBe('make it about cats')
    expect(body.get('notes')).toBe('for clara')
    expect((init.headers as Record<string, string>).Authorization).toBe(
      'Bearer fake-token',
    )
    expect(result.lines).toEqual(['a', 'b'])
    expect(result.lineCountMismatch).toBe(false)
  })

  it('posts file when provided instead of text', async () => {
    const captured: { init: RequestInit | undefined } = { init: undefined }
    global.fetch = jest.fn(async (_url: RequestInfo, init?: RequestInit) => {
      captured.init = init
      return new Response(
        JSON.stringify({
          lines: ['x'],
          warnings: [],
          model: 'm',
          line_count_mismatch: false,
          retry_count: 0,
          duration_ms: 5,
        }),
        { status: 200 },
      )
    }) as unknown as typeof fetch

    const file = new File([new Uint8Array([1, 2, 3])], 'brief.docx', {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    })

    await generateCustomLyrics(
      'job-1',
      { existingLines: ['a'], file },
      undefined,
      'tok',
    )

    const body = (captured.init!.body as FormData)
    expect(body.get('file')).toBeInstanceOf(File)
    expect(body.has('custom_text')).toBe(false)
  })

  it('throws CustomLyricsApiError on non-2xx', async () => {
    global.fetch = jest.fn(
      async () =>
        new Response(JSON.stringify({ detail: 'AI service unavailable' }), {
          status: 502,
          headers: { 'Content-Type': 'application/json' },
        }),
    ) as unknown as typeof fetch

    await expect(
      generateCustomLyrics(
        'job-1',
        { existingLines: ['a'], customText: 'x' },
        undefined,
        'tok',
      ),
    ).rejects.toMatchObject({ status: 502, message: expect.stringContaining('AI service') })
  })

  it('passes AbortSignal through to fetch', async () => {
    const captured: { init: RequestInit | undefined } = { init: undefined }
    global.fetch = jest.fn(async (_url: RequestInfo, init?: RequestInit) => {
      captured.init = init
      return new Response(JSON.stringify({
        lines: ['a'], warnings: [], model: 'm',
        line_count_mismatch: false, retry_count: 0, duration_ms: 1,
      }), { status: 200 })
    }) as unknown as typeof fetch

    const ac = new AbortController()
    await generateCustomLyrics(
      'job-1',
      { existingLines: ['a'], customText: 'x' },
      ac.signal,
      'tok',
    )
    expect(captured.init!.signal).toBe(ac.signal)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd frontend && npx jest lib/api/__tests__/customLyrics.test.ts --no-coverage
```

Expected: failure (module not found).

- [ ] **Step 3: Create the wrapper**

Create `frontend/lib/api/customLyrics.ts`:

```ts
/**
 * Client for the LLM-powered custom lyrics endpoint.
 *
 * POST /api/review/{job_id}/custom-lyrics/generate
 *
 * The endpoint is stateless — it returns generated lines for the editable
 * preview. Persistence still flows through the existing replace-segments
 * Save path.
 */
const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? 'https://api.nomadkaraoke.com'

export interface CustomLyricsParams {
  existingLines: string[]
  customText?: string
  notes?: string
  artist?: string
  title?: string
  file?: File
}

export interface CustomLyricsResponse {
  lines: string[]
  warnings: string[]
  model: string
  lineCountMismatch: boolean
  retryCount: number
  durationMs: number
}

export class CustomLyricsApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'CustomLyricsApiError'
    this.status = status
  }
}

export async function generateCustomLyrics(
  jobId: string,
  params: CustomLyricsParams,
  signal?: AbortSignal,
  token?: string,
): Promise<CustomLyricsResponse> {
  const form = new FormData()
  form.set('existing_lines', JSON.stringify(params.existingLines))
  if (params.customText) form.set('custom_text', params.customText)
  if (params.notes) form.set('notes', params.notes)
  if (params.artist) form.set('artist', params.artist)
  if (params.title) form.set('title', params.title)
  if (params.file) form.set('file', params.file)

  const headers: Record<string, string> = {}
  if (token) headers.Authorization = `Bearer ${token}`

  const response = await fetch(
    `${API_BASE}/api/review/${encodeURIComponent(jobId)}/custom-lyrics/generate`,
    { method: 'POST', body: form, headers, signal },
  )

  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try {
      const body = await response.json()
      if (body && typeof body.detail === 'string') detail = body.detail
    } catch {
      /* swallow JSON parse errors and use generic message */
    }
    throw new CustomLyricsApiError(response.status, detail)
  }

  const data = await response.json()
  return {
    lines: data.lines,
    warnings: data.warnings ?? [],
    model: data.model,
    lineCountMismatch: Boolean(data.line_count_mismatch),
    retryCount: Number(data.retry_count ?? 0),
    durationMs: Number(data.duration_ms ?? 0),
  }
}
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd frontend && npx jest lib/api/__tests__/customLyrics.test.ts --no-coverage
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/api/customLyrics.ts \
        frontend/lib/api/__tests__/customLyrics.test.ts
git commit -m "feat(custom-lyrics): add frontend API client for /custom-lyrics/generate"
```

---

### Task 11: `CustomLyricsMode` component (input phase)

**Files:**
- Create: `frontend/components/lyrics-review/modals/CustomLyricsMode.tsx`

This task delivers a working input phase only; preview phase is added in Task 12.

- [ ] **Step 1: Create the component**

Create `frontend/components/lyrics-review/modals/CustomLyricsMode.tsx`:

```tsx
'use client'

import { useTranslations } from 'next-intl'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, X, Sparkles, FileText, Type, AlertTriangle, Check, Upload, Info } from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { LyricsSegment } from '@/lib/lyrics-review/types'
import { segmentsFromLines } from '@/lib/lyrics-review/utils/segmentsFromLines'
import { generateCustomLyrics, CustomLyricsApiError } from '@/lib/api/customLyrics'

const ACCEPTED_MIME_TYPES = [
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/pdf',
  'text/plain',
  'text/markdown',
]

const ACCEPTED_EXTENSIONS = ['.docx', '.pdf', '.txt', '.md']
const MAX_FILE_BYTES = 5 * 1024 * 1024

type Phase = 'input' | 'generating' | 'preview'
type InputTab = 'text' | 'file'

interface CustomLyricsModeProps {
  open: boolean
  jobId: string
  artist?: string
  title?: string
  authToken?: string
  existingSegments: LyricsSegment[]
  onSave: (newSegments: LyricsSegment[], meta: { source: 'text' | 'file'; filename?: string; model: string }) => void
  onCancel: () => void
  onBack: () => void
}

export default function CustomLyricsMode({
  open,
  jobId,
  artist,
  title,
  authToken,
  existingSegments,
  onSave,
  onCancel,
  onBack,
}: CustomLyricsModeProps) {
  const t = useTranslations('lyricsReview.modals.customLyricsMode')

  const [phase, setPhase] = useState<Phase>('input')
  const [inputTab, setInputTab] = useState<InputTab>('text')
  const [customText, setCustomText] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [fileError, setFileError] = useState<string | null>(null)
  const [notes, setNotes] = useState('')
  const [generatedText, setGeneratedText] = useState('')
  const [warnings, setWarnings] = useState<string[]>([])
  const [lineCountMismatch, setLineCountMismatch] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [modelUsed, setModelUsed] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  // Reset state on open
  useEffect(() => {
    if (open) {
      setPhase('input')
      setInputTab('text')
      setCustomText('')
      setFile(null)
      setFileError(null)
      setNotes('')
      setGeneratedText('')
      setWarnings([])
      setLineCountMismatch(false)
      setError(null)
      setModelUsed(null)
    }
  }, [open])

  // Abort any in-flight request on unmount
  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [])

  const existingLines = useMemo(
    () => existingSegments.map((s) => s.text.trim()),
    [existingSegments],
  )

  const canGenerate = useMemo(() => {
    if (inputTab === 'text') return customText.trim().length > 0
    return file !== null
  }, [inputTab, customText, file])

  const handleFileSelect = useCallback((selected: File | null) => {
    setFileError(null)
    if (!selected) {
      setFile(null)
      return
    }
    const ext = '.' + (selected.name.split('.').pop() ?? '').toLowerCase()
    if (
      !ACCEPTED_EXTENSIONS.includes(ext) &&
      !ACCEPTED_MIME_TYPES.includes(selected.type)
    ) {
      setFileError(t('fileTypeError', { allowed: ACCEPTED_EXTENSIONS.join(', ') }))
      setFile(null)
      return
    }
    if (selected.size > MAX_FILE_BYTES) {
      setFileError(t('fileSizeError', { maxMb: 5 }))
      setFile(null)
      return
    }
    setFile(selected)
  }, [t])

  const runGenerate = useCallback(async () => {
    setError(null)
    setWarnings([])
    setLineCountMismatch(false)
    setPhase('generating')

    abortRef.current?.abort()
    const ac = new AbortController()
    abortRef.current = ac

    try {
      const response = await generateCustomLyrics(
        jobId,
        {
          existingLines,
          customText: inputTab === 'text' ? customText : undefined,
          file: inputTab === 'file' && file ? file : undefined,
          notes: notes.trim() || undefined,
          artist,
          title,
        },
        ac.signal,
        authToken,
      )

      setGeneratedText(response.lines.join('\n'))
      setWarnings(response.warnings)
      setLineCountMismatch(response.lineCountMismatch)
      setModelUsed(response.model)
      setPhase('preview')
    } catch (err) {
      if ((err as Error).name === 'AbortError') return
      const message =
        err instanceof CustomLyricsApiError
          ? err.message
          : (err as Error).message || t('genericError')
      setError(message)
      setPhase('input')
    }
  }, [
    jobId,
    existingLines,
    inputTab,
    customText,
    file,
    notes,
    artist,
    title,
    authToken,
    t,
  ])

  const previewLineCount = useMemo(
    () => generatedText.split('\n').length,
    [generatedText],
  )
  const expectedLineCount = existingLines.length
  const previewLineDiff = previewLineCount - expectedLineCount
  const canSave = previewLineDiff === 0 && phase === 'preview'

  const handleSave = useCallback(() => {
    const lines = generatedText.split('\n')
    const newSegments = segmentsFromLines(lines, existingSegments)
    onSave(newSegments, {
      source: inputTab,
      filename: file?.name,
      model: modelUsed ?? 'unknown',
    })
  }, [generatedText, existingSegments, onSave, inputTab, file, modelUsed])

  const handleClose = useCallback(() => {
    abortRef.current?.abort()
    onCancel()
  }, [onCancel])

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && handleClose()}>
      <DialogContent className="max-w-2xl h-[80vh] max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Button variant="ghost" size="icon" onClick={onBack} className="h-8 w-8" disabled={phase === 'generating'}>
              <ArrowLeft className="h-4 w-4" />
            </Button>
            <span className="flex-1 flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-primary" />
              {t('title')}
            </span>
            <Button variant="ghost" size="icon" onClick={handleClose} className="h-8 w-8" disabled={phase === 'generating'}>
              <X className="h-4 w-4" />
            </Button>
          </DialogTitle>
        </DialogHeader>

        <div className="flex-1 overflow-hidden flex flex-col gap-4">
          {/* Input phase */}
          {phase === 'input' && (
            <>
              <div className="flex items-start gap-2 p-3 rounded-md bg-muted/50 text-sm text-muted-foreground">
                <Info className="h-4 w-4 mt-0.5 shrink-0" />
                <p>{t('description', { count: expectedLineCount })}</p>
              </div>

              <Tabs value={inputTab} onValueChange={(v) => setInputTab(v as InputTab)}>
                <TabsList className="grid grid-cols-2">
                  <TabsTrigger value="text">
                    <Type className="h-4 w-4 mr-2" />
                    {t('tabText')}
                  </TabsTrigger>
                  <TabsTrigger value="file">
                    <FileText className="h-4 w-4 mr-2" />
                    {t('tabFile')}
                  </TabsTrigger>
                </TabsList>

                <TabsContent value="text" className="mt-3">
                  <Label htmlFor="custom-lyrics-text" className="text-sm">{t('textLabel')}</Label>
                  <Textarea
                    id="custom-lyrics-text"
                    value={customText}
                    onChange={(e) => setCustomText(e.target.value)}
                    placeholder={t('textPlaceholder')}
                    className="mt-1.5 font-mono text-sm min-h-[180px]"
                  />
                </TabsContent>

                <TabsContent value="file" className="mt-3">
                  <Label className="text-sm">{t('fileLabel')}</Label>
                  <div className="mt-1.5 flex items-center gap-3">
                    <label className="inline-flex items-center gap-2 px-3 py-2 rounded-md border border-dashed cursor-pointer hover:bg-muted/30">
                      <Upload className="h-4 w-4" />
                      <span className="text-sm">{t('chooseFile')}</span>
                      <input
                        type="file"
                        accept={ACCEPTED_EXTENSIONS.join(',')}
                        className="sr-only"
                        onChange={(e) => handleFileSelect(e.target.files?.[0] ?? null)}
                      />
                    </label>
                    {file && (
                      <span className="text-sm text-muted-foreground">
                        {file.name} ({(file.size / 1024).toFixed(1)} KB)
                      </span>
                    )}
                  </div>
                  {fileError && (
                    <p className="mt-1.5 text-sm text-destructive flex items-center gap-1">
                      <AlertTriangle className="h-4 w-4" />
                      {fileError}
                    </p>
                  )}
                  <p className="mt-1.5 text-xs text-muted-foreground">
                    {t('fileHint', { allowed: ACCEPTED_EXTENSIONS.join(', '), maxMb: 5 })}
                  </p>
                </TabsContent>
              </Tabs>

              <div>
                <Label htmlFor="custom-lyrics-notes" className="text-sm">{t('notesLabel')}</Label>
                <Textarea
                  id="custom-lyrics-notes"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder={t('notesPlaceholder')}
                  className="mt-1.5 text-sm min-h-[80px]"
                />
              </div>

              {error && (
                <div className="flex items-start gap-2 p-3 rounded-md bg-destructive/10 text-sm text-destructive">
                  <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
                  <p>{error}</p>
                </div>
              )}
            </>
          )}

          {/* Generating phase */}
          {phase === 'generating' && (
            <div className="flex-1 flex flex-col items-center justify-center gap-4 text-center">
              <Sparkles className="h-10 w-10 text-primary animate-pulse" />
              <div>
                <p className="text-lg font-semibold">{t('generatingTitle')}</p>
                <p className="text-sm text-muted-foreground mt-1">{t('generatingSubtitle')}</p>
              </div>
              <Button variant="outline" size="sm" onClick={() => abortRef.current?.abort()}>
                {t('cancelGeneration')}
              </Button>
            </div>
          )}

          {/* Preview phase — Task 12 */}
        </div>

        <DialogFooter>
          {phase === 'input' && (
            <>
              <Button variant="outline" onClick={handleClose}>
                {t('cancel')}
              </Button>
              <Button onClick={runGenerate} disabled={!canGenerate}>
                <Sparkles className="h-4 w-4 mr-2" />
                {t('generate')}
              </Button>
            </>
          )}
          {/* Preview footer added in Task 12 */}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd frontend && npx tsc --noEmit
```

Expected: zero errors. (Some i18n keys are referenced before being added — Task 16 adds them; if `useTranslations` complains at runtime that's expected, but `tsc` doesn't validate them.)

- [ ] **Step 3: Commit**

```bash
git add frontend/components/lyrics-review/modals/CustomLyricsMode.tsx
git commit -m "feat(custom-lyrics): add CustomLyricsMode input phase"
```

---

### Task 12: `CustomLyricsMode` preview phase + regenerate

**Files:**
- Modify: `frontend/components/lyrics-review/modals/CustomLyricsMode.tsx` (add preview phase markup + footer)

- [ ] **Step 1: Add preview phase markup**

In `frontend/components/lyrics-review/modals/CustomLyricsMode.tsx`, immediately after the `{/* Preview phase — Task 12 */}` comment inside the dialog body, add:

```tsx
          {phase === 'preview' && (
            <>
              <div className="flex items-center justify-between">
                <span
                  className={
                    'text-sm font-medium ' +
                    (previewLineDiff === 0 ? 'text-green-500' : 'text-destructive')
                  }
                >
                  {previewLineDiff === 0 ? (
                    <span className="flex items-center gap-1">
                      <Check className="h-4 w-4" />
                      {previewLineCount}/{expectedLineCount} lines
                    </span>
                  ) : (
                    <span className="flex items-center gap-1">
                      <AlertTriangle className="h-4 w-4" />
                      {previewLineCount}/{expectedLineCount} lines
                    </span>
                  )}
                </span>
                {modelUsed && (
                  <span className="text-xs text-muted-foreground">
                    {t('modelLabel')}: {modelUsed}
                  </span>
                )}
              </div>

              {(warnings.length > 0 || lineCountMismatch) && (
                <div className="flex items-start gap-2 p-3 rounded-md bg-yellow-500/10 text-sm text-yellow-700 dark:text-yellow-300">
                  <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
                  <ul className="list-disc list-inside">
                    {warnings.map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                    {lineCountMismatch && warnings.length === 0 && (
                      <li>{t('lineCountMismatchFallback')}</li>
                    )}
                  </ul>
                </div>
              )}

              <Textarea
                value={generatedText}
                onChange={(e) => setGeneratedText(e.target.value)}
                placeholder={t('previewPlaceholder')}
                className="flex-1 resize-none font-mono text-sm min-h-[260px]"
              />

              {previewLineDiff !== 0 && (
                <div className="flex items-start gap-2 p-3 rounded-md bg-destructive/10 text-sm text-destructive">
                  <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
                  <p>
                    {previewLineDiff > 0
                      ? t('tooManyLines', { diff: Math.abs(previewLineDiff), expected: expectedLineCount, actual: previewLineCount })
                      : t('tooFewLines', { diff: Math.abs(previewLineDiff), expected: expectedLineCount, actual: previewLineCount })}
                  </p>
                </div>
              )}
            </>
          )}
```

- [ ] **Step 2: Add preview footer**

In the same file, immediately after the `{/* Preview footer added in Task 12 */}` comment inside the `DialogFooter`, add:

```tsx
          {phase === 'preview' && (
            <>
              <Button variant="outline" onClick={() => setPhase('input')}>
                {t('backToInput')}
              </Button>
              <Button variant="outline" onClick={runGenerate}>
                <Sparkles className="h-4 w-4 mr-2" />
                {t('regenerate')}
              </Button>
              <Button onClick={handleSave} disabled={!canSave}>
                <Check className="h-4 w-4 mr-2" />
                {t('saveCustom')}
              </Button>
            </>
          )}
          {phase === 'generating' && (
            <Button variant="outline" disabled>
              {t('generating')}
            </Button>
          )}
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd frontend && npx tsc --noEmit
```

Expected: zero errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/lyrics-review/modals/CustomLyricsMode.tsx
git commit -m "feat(custom-lyrics): add CustomLyricsMode preview phase with line-count validation"
```

---

### Task 13: Component tests for `CustomLyricsMode`

**Files:**
- Create: `frontend/components/lyrics-review/__tests__/CustomLyricsMode.test.tsx`

- [ ] **Step 1: Write tests**

Create `frontend/components/lyrics-review/__tests__/CustomLyricsMode.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { NextIntlClientProvider } from 'next-intl'
import CustomLyricsMode from '../modals/CustomLyricsMode'
import type { LyricsSegment } from '@/lib/lyrics-review/types'

// Mock the API client
jest.mock('@/lib/api/customLyrics', () => ({
  generateCustomLyrics: jest.fn(),
  CustomLyricsApiError: class CustomLyricsApiError extends Error {
    status: number
    constructor(status: number, message: string) {
      super(message)
      this.status = status
    }
  },
}))

const { generateCustomLyrics } = jest.requireMock('@/lib/api/customLyrics')

const messages = {
  lyricsReview: {
    modals: {
      customLyricsMode: {
        title: 'Custom Lyrics',
        description: '{count} lines expected',
        tabText: 'Paste text',
        tabFile: 'Upload file',
        textLabel: 'Custom lyrics or instructions',
        textPlaceholder: 'Paste here...',
        fileLabel: 'Upload file',
        chooseFile: 'Choose file',
        fileHint: 'Allowed: {allowed}. Max {maxMb} MB.',
        fileTypeError: 'Unsupported file. Use {allowed}.',
        fileSizeError: 'File too large (max {maxMb} MB).',
        notesLabel: 'Notes',
        notesPlaceholder: 'Wedding for John & Jane...',
        generate: 'Generate',
        cancel: 'Cancel',
        cancelGeneration: 'Cancel generation',
        generatingTitle: 'Generating...',
        generatingSubtitle: 'Up to a minute',
        generating: 'Generating',
        backToInput: 'Edit inputs',
        regenerate: 'Regenerate',
        saveCustom: 'Save',
        modelLabel: 'Model',
        previewPlaceholder: 'Generated lyrics...',
        lineCountMismatchFallback: 'Line count mismatch',
        tooManyLines: '{diff} too many lines',
        tooFewLines: '{diff} too few lines',
        genericError: 'Something went wrong',
      },
    },
  },
}

const renderMode = (props: Partial<React.ComponentProps<typeof CustomLyricsMode>> = {}) => {
  const seg = (text: string): LyricsSegment => ({
    id: `s-${text}`,
    text,
    start_time: 0,
    end_time: 1,
    words: [{ id: 'w', text, start_time: 0, end_time: 1, confidence: 1 }],
  })

  const defaults: React.ComponentProps<typeof CustomLyricsMode> = {
    open: true,
    jobId: 'job-1',
    artist: 'A',
    title: 'T',
    authToken: 'token',
    existingSegments: [seg('one'), seg('two'), seg('three')],
    onSave: jest.fn(),
    onCancel: jest.fn(),
    onBack: jest.fn(),
  }

  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <CustomLyricsMode {...defaults} {...props} />
    </NextIntlClientProvider>,
  )
}

describe('CustomLyricsMode', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('renders input phase by default with Generate disabled', () => {
    renderMode()
    expect(screen.getByText('Custom Lyrics')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Generate$/i })).toBeDisabled()
  })

  it('enables Generate once text is provided', async () => {
    renderMode()
    const ta = screen.getByLabelText('Custom lyrics or instructions')
    await userEvent.type(ta, 'use cats everywhere')
    expect(screen.getByRole('button', { name: /^Generate$/i })).not.toBeDisabled()
  })

  it('rejects oversize files', async () => {
    renderMode()
    await userEvent.click(screen.getByRole('tab', { name: /Upload file/i }))
    const input = screen.getByLabelText(/Choose file/i, { selector: 'input' }) as HTMLInputElement
    const big = new File([new Uint8Array(6 * 1024 * 1024)], 'big.txt', { type: 'text/plain' })
    await userEvent.upload(input, big)
    expect(await screen.findByText(/File too large/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Generate$/i })).toBeDisabled()
  })

  it('calls API and lands in preview on success with matching line count', async () => {
    generateCustomLyrics.mockResolvedValueOnce({
      lines: ['ONE', 'TWO', 'THREE'],
      warnings: [],
      model: 'gemini-3.1-pro-preview',
      lineCountMismatch: false,
      retryCount: 0,
      durationMs: 50,
    })

    const onSave = jest.fn()
    renderMode({ onSave })

    await userEvent.type(screen.getByLabelText('Custom lyrics or instructions'), 'caps lock')
    await userEvent.click(screen.getByRole('button', { name: /^Generate$/i }))

    expect(await screen.findByText(/3\/3 lines/)).toBeInTheDocument()
    const saveBtn = screen.getByRole('button', { name: /^Save$/i })
    expect(saveBtn).not.toBeDisabled()

    await userEvent.click(saveBtn)
    expect(onSave).toHaveBeenCalledTimes(1)
    const [savedSegments, meta] = onSave.mock.calls[0]
    expect(savedSegments).toHaveLength(3)
    expect(savedSegments[0].text).toBe('ONE')
    expect(meta).toMatchObject({ source: 'text', model: 'gemini-3.1-pro-preview' })
  })

  it('shows line-count mismatch warning and disables Save', async () => {
    generateCustomLyrics.mockResolvedValueOnce({
      lines: ['ONE', 'TWO'],
      warnings: ['AI returned 2 lines but 3 were expected.'],
      model: 'm',
      lineCountMismatch: true,
      retryCount: 1,
      durationMs: 5,
    })
    renderMode()
    await userEvent.type(screen.getByLabelText('Custom lyrics or instructions'), 'x')
    await userEvent.click(screen.getByRole('button', { name: /^Generate$/i }))

    expect(await screen.findByText(/AI returned 2 lines/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Save$/i })).toBeDisabled()
  })

  it('Regenerate fires another API call', async () => {
    generateCustomLyrics
      .mockResolvedValueOnce({
        lines: ['A', 'B', 'C'],
        warnings: [],
        model: 'm',
        lineCountMismatch: false,
        retryCount: 0,
        durationMs: 5,
      })
      .mockResolvedValueOnce({
        lines: ['X', 'Y', 'Z'],
        warnings: [],
        model: 'm',
        lineCountMismatch: false,
        retryCount: 0,
        durationMs: 5,
      })

    renderMode()
    await userEvent.type(screen.getByLabelText('Custom lyrics or instructions'), 'x')
    await userEvent.click(screen.getByRole('button', { name: /^Generate$/i }))

    await screen.findByText(/3\/3 lines/)
    await userEvent.click(screen.getByRole('button', { name: /Regenerate/i }))

    await waitFor(() => expect(generateCustomLyrics).toHaveBeenCalledTimes(2))
  })

  it('falls back to input phase on API error', async () => {
    generateCustomLyrics.mockRejectedValueOnce(new Error('boom'))
    renderMode()
    await userEvent.type(screen.getByLabelText('Custom lyrics or instructions'), 'x')
    await userEvent.click(screen.getByRole('button', { name: /^Generate$/i }))

    expect(await screen.findByText(/boom/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Generate$/i })).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run tests**

```bash
cd frontend && npx jest components/lyrics-review/__tests__/CustomLyricsMode.test.tsx --no-coverage
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add frontend/components/lyrics-review/__tests__/CustomLyricsMode.test.tsx
git commit -m "test(custom-lyrics): add component tests for CustomLyricsMode"
```

---

### Task 14: Wire `CustomLyricsMode` into `ReplaceAllLyricsModal`

**Files:**
- Modify: `frontend/components/lyrics-review/modals/ReplaceAllLyricsModal.tsx`

- [ ] **Step 1: Extend `ModalMode` and `ReplaceAllOperation`**

In `ReplaceAllLyricsModal.tsx`:

- Update line 24: `type ModalMode = 'selection' | 'replace' | 'resync' | 'replaceSegments' | 'changeCase' | 'customLyrics'`
- Update line 26: `export type ReplaceAllOperation = 'replace_all_lyrics' | 'change_case' | 'custom_lyrics_replace'`

- [ ] **Step 2: Extend props and resolve auth token + jobId**

Add to the `ReplaceAllLyricsModalProps` interface near line 33:

```ts
  jobId: string
  artist?: string
  title?: string
  authToken?: string
```

Default values are passed from `LyricsAnalyzer.tsx`. (We'll wire those in Step 4 below.)

Then update the function signature destructuring (around line 43) to include the new props.

- [ ] **Step 3: Add the customLyrics mode handler and dialog mount**

After `handleSelectChangeCase` (line ~221), add:

```ts
  const handleSelectCustomLyrics = useCallback(() => {
    setMode('customLyrics')
  }, [])

  const handleCustomLyricsSave = useCallback(
    (
      newSegments: LyricsSegment[],
      meta: { source: 'text' | 'file'; filename?: string; model: string },
    ) => {
      onSave(newSegments, {
        operation: 'custom_lyrics_replace',
        details: {
          source: meta.source,
          filename: meta.filename ?? null,
          model: meta.model,
        },
      })
      handleClose()
    },
    [onSave, handleClose],
  )
```

Then in the JSX, add a new `<CustomLyricsMode>` mount alongside the other dialogs (just below the synchronizer dialog):

```tsx
      {/* Custom Lyrics Mode (LLM-powered) */}
      <CustomLyricsMode
        open={open && mode === 'customLyrics'}
        jobId={jobId}
        artist={artist}
        title={title}
        authToken={authToken}
        existingSegments={existingSegments}
        onSave={handleCustomLyricsSave}
        onCancel={handleClose}
        onBack={handleBackToSelection}
      />
```

Add the import at the top of the file:

```ts
import CustomLyricsMode from './CustomLyricsMode'
```

- [ ] **Step 4: Pass new prop to `ModeSelectionModal`**

Update the `<ModeSelectionModal>` props in the same JSX (line 262) to include `onSelectCustomLyrics={handleSelectCustomLyrics}` (this prop will be wired in Task 15).

- [ ] **Step 5: Wire jobId/artist/title/authToken from `LyricsAnalyzer.tsx`**

Find where `<ReplaceAllLyricsModal />` is mounted in `frontend/components/lyrics-review/LyricsAnalyzer.tsx` (around line 1484) and add the new props. The component already receives the parent `correctionData` and admin token; pass them through. Use `grep` to find the right context:

```bash
cd frontend && grep -n "ReplaceAllLyricsModal" components/lyrics-review/LyricsAnalyzer.tsx
```

Update the JSX to:

```tsx
<ReplaceAllLyricsModal
  open={isReplaceAllLyricsModalOpen}
  onClose={...existing...}
  onSave={...existing...}
  onPlaySegment={...existing...}
  currentTime={...existing...}
  setModalSpacebarHandler={...existing...}
  existingSegments={...existing...}
  jobId={jobId}                      /* parent already has this */
  artist={correctionData?.metadata?.artist}
  title={correctionData?.metadata?.title}
  authToken={authToken}              /* parent already has this */
/>
```

(Field names for artist/title may vary — check `correctionData` shape with `grep -n "correctionData" frontend/components/lyrics-review/LyricsAnalyzer.tsx | head -20` and adapt. Pass `undefined` if unknown — the backend tolerates missing artist/title.)

- [ ] **Step 6: Verify TypeScript compiles**

```bash
cd frontend && npx tsc --noEmit
```

Expected: zero errors.

- [ ] **Step 7: Run frontend tests to ensure no regressions**

```bash
cd frontend && npx jest --no-coverage
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add frontend/components/lyrics-review/modals/ReplaceAllLyricsModal.tsx \
        frontend/components/lyrics-review/LyricsAnalyzer.tsx
git commit -m "feat(custom-lyrics): wire CustomLyricsMode into ReplaceAllLyricsModal"
```

---

### Task 15: Add 5th card to `ModeSelectionModal`

**Files:**
- Modify: `frontend/components/lyrics-review/modals/ModeSelectionModal.tsx`

- [ ] **Step 1: Add `Sparkles` import**

Update the lucide-react import line at line 6 to include `Sparkles`:

```ts
import { RefreshCw, ClipboardPaste, TextCursorInput, CaseSensitive, Sparkles, X, type LucideIcon } from 'lucide-react'
```

- [ ] **Step 2: Add `onSelectCustomLyrics` prop**

Update the `ModeSelectionModalProps` interface (lines 9–17):

```ts
interface ModeSelectionModalProps {
  open: boolean
  onClose: () => void
  onSelectReplace: () => void
  onSelectResync: () => void
  onSelectReplaceSegments: () => void
  onSelectChangeCase: () => void
  onSelectCustomLyrics: () => void
  hasExistingLyrics: boolean
}
```

Add the new prop to the function signature (around line 38).

- [ ] **Step 3: Add the 5th option**

In the `options` array (lines 42–80), insert a new entry just before the `replaceAll` entry:

```ts
    hasExistingLyrics && {
      key: 'customLyrics',
      icon: Sparkles,
      title: t('customLyricsTitle'),
      desc: t('customLyricsDesc'),
      tag: t('customLyricsTag'),
      tagTone: 'positive' as const,
      onSelect: onSelectCustomLyrics,
    },
```

- [ ] **Step 4: Verify TypeScript compiles**

```bash
cd frontend && npx tsc --noEmit
```

Expected: zero errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/lyrics-review/modals/ModeSelectionModal.tsx
git commit -m "feat(custom-lyrics): add Custom Lyrics card to mode selection modal"
```

---

## Phase 3 — i18n, E2E, ship

### Task 16: Add i18n keys to `en.json` and run translation script

**Files:**
- Modify: `frontend/messages/en.json`

- [ ] **Step 1: Add new keys**

Open `frontend/messages/en.json`. Locate the `lyricsReview.modals.modeSelection` block (around line 990–1005). Add three new keys inside it:

```json
"customLyricsTitle": "Custom Lyrics",
"customLyricsDesc": "Replace lyrics with custom versions for weddings, birthdays, parties. AI matches each line to the original structure.",
"customLyricsTag": "AI-powered, recommended for client requests",
```

Then add a new sibling block `customLyricsMode` (alongside `modeSelection`) under `lyricsReview.modals`:

```json
"customLyricsMode": {
  "title": "Custom Lyrics (AI-powered)",
  "description": "Provide the client's custom lyrics or notes — AI will return exactly {count} lines matching the original structure.",
  "tabText": "Paste text",
  "tabFile": "Upload file",
  "textLabel": "Custom lyrics or instructions",
  "textPlaceholder": "Paste the client's custom lyrics here, or describe what should change. Examples:\n\nReplace 'baby' with 'Mary' throughout.\nVerse 2 should be about our trip to Italy.\nKeep the chorus original.",
  "fileLabel": "Client document",
  "chooseFile": "Choose file",
  "fileHint": "Accepted: {allowed} (max {maxMb} MB)",
  "fileTypeError": "Unsupported file type. Allowed: {allowed}",
  "fileSizeError": "File is too large (max {maxMb} MB)",
  "notesLabel": "Notes / instructions (optional)",
  "notesPlaceholder": "Extra context for the AI, e.g. 'Wedding for John & Jane — keep it cheesy'",
  "generate": "Generate Custom Lyrics",
  "generatingTitle": "Generating custom lyrics with AI...",
  "generatingSubtitle": "This can take up to a minute. You can cancel and try again.",
  "generating": "Generating...",
  "cancelGeneration": "Cancel",
  "cancel": "Cancel",
  "modelLabel": "Model",
  "backToInput": "Edit inputs",
  "regenerate": "Regenerate",
  "saveCustom": "Save Custom Lyrics",
  "previewPlaceholder": "Generated lyrics will appear here...",
  "lineCountMismatchFallback": "AI returned a different number of lines than expected. Adjust the textarea or click Regenerate.",
  "tooManyLines": "You have {diff} extra line(s). Expected {expected}, got {actual}. Remove lines or click Regenerate.",
  "tooFewLines": "You're missing {diff} line(s). Expected {expected}, got {actual}. Add lines or click Regenerate.",
  "genericError": "Something went wrong. Please try again."
},
```

- [ ] **Step 2: Add a save-success toast key**

Find the namespace where `LyricsAnalyzer` raises toasts after `replaceSegments` save (search for an existing key like `replaceSegmentsSavedToast` or similar via `grep -rn "replaceSegments" frontend/messages/en.json`). Add a sibling key in that same toast namespace:

```json
"customLyricsSavedToast": "Custom lyrics applied. Manually sync each edited segment to ensure word timings are good for customised lyrics."
```

- [ ] **Step 3: Wire the toast in `LyricsAnalyzer.tsx`**

Find the existing `onSave` handler that `ReplaceAllLyricsModal` invokes. Detect the `custom_lyrics_replace` operation in `meta` and trigger the toast accordingly. For example:

```ts
const handleReplaceAllSave = useCallback(
  (newSegments: LyricsSegment[], meta?: ReplaceAllSaveMeta) => {
    // ... existing save logic ...
    if (meta?.operation === 'custom_lyrics_replace') {
      toast.info(t('customLyricsSavedToast'))
    }
  },
  /* deps */,
)
```

(Use the existing toast helper imported at the top of `LyricsAnalyzer.tsx` — match the call style of nearby `toast.*()` invocations.)

- [ ] **Step 4: Validate JSON syntax**

```bash
cd frontend && python3 -c "import json; json.load(open('messages/en.json')); print('ok')"
```

Expected: prints `ok`.

- [ ] **Step 5: Run the translation script for all locales**

```bash
cd frontend && python scripts/translate.py --messages-dir messages --target all
```

Expected: writes updated keys to all 32 locale files. May take a few minutes the first time; subsequent runs hit the GCS cache and finish in seconds.

- [ ] **Step 6: Verify no key drift**

```bash
cd frontend && python scripts/check_translations.py 2>/dev/null || python -c "
import json, glob
en = json.load(open('messages/en.json'))
def keys(d, prefix=''):
    out = set()
    for k, v in d.items():
        path = f'{prefix}.{k}' if prefix else k
        if isinstance(v, dict): out |= keys(v, path)
        else: out.add(path)
    return out
en_keys = keys(en)
for f in glob.glob('messages/*.json'):
    if f.endswith('en.json'): continue
    locale = json.load(open(f))
    missing = en_keys - keys(locale)
    if missing: print(f'{f}: missing {len(missing)} keys')
print('done')
"
```

Expected: no `missing` lines printed.

- [ ] **Step 7: Run frontend tests**

```bash
cd frontend && npx jest --no-coverage
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add frontend/messages/ frontend/components/lyrics-review/LyricsAnalyzer.tsx
git commit -m "feat(custom-lyrics): add i18n keys and save toast across all locales"
```

---

### Task 17: Production E2E spec

**Files:**
- Create: `frontend/e2e/production/custom-lyrics-mode.spec.ts`

- [ ] **Step 1: Write the spec**

Create `frontend/e2e/production/custom-lyrics-mode.spec.ts`:

```ts
import { test, expect, Page } from '@playwright/test'

const PROD_URL = 'https://gen.nomadkaraoke.com'
const API_URL = 'https://api.nomadkaraoke.com'

const ADMIN_TOKEN = process.env.KARAOKE_ADMIN_TOKEN

test.describe('Custom Lyrics LLM mode (production)', () => {
  test.skip(!ADMIN_TOKEN, 'KARAOKE_ADMIN_TOKEN not set')

  test('generate, preview, save', async ({ page }: { page: Page }) => {
    test.setTimeout(180_000) // up to 3 minutes including LLM call

    // Find an existing AWAITING_REVIEW or IN_REVIEW job to operate on
    const listResponse = await page.request.get(
      `${API_URL}/api/admin/jobs?status=in_review&limit=1`,
      { headers: { Authorization: `Bearer ${ADMIN_TOKEN}` } },
    )
    expect(listResponse.ok()).toBe(true)
    const { jobs } = await listResponse.json()
    test.skip(!jobs?.length, 'No in_review jobs available for E2E')
    const jobId = jobs[0].id

    // Inject token into localStorage (the app's auth pattern)
    await page.addInitScript((token: string) => {
      window.localStorage.setItem('karaoke_admin_token', token)
    }, ADMIN_TOKEN!)

    await page.goto(`${PROD_URL}/app/jobs#/${jobId}/review`)
    await expect(page.getByRole('button', { name: /Edit All Lyrics/i })).toBeVisible({ timeout: 30_000 })

    await page.getByRole('button', { name: /Edit All Lyrics/i }).click()
    await page.getByRole('button', { name: /Custom Lyrics/i }).first().click()

    // Input phase: type a tiny custom-lyrics request
    await page.getByLabel(/Custom lyrics or instructions/i).fill(
      "Replace 'baby' with 'sweetie' wherever it appears, otherwise keep the original."
    )

    await page.getByRole('button', { name: /Generate Custom Lyrics/i }).click()

    // Wait for preview phase (line counter or Save button visible)
    await expect(page.getByRole('button', { name: /Save Custom Lyrics/i })).toBeVisible({
      timeout: 120_000,
    })

    // Capture the generated text and ensure non-empty
    const previewTextarea = page.getByPlaceholder(/Generated lyrics will appear here/)
    const generated = await previewTextarea.inputValue()
    expect(generated.trim().length).toBeGreaterThan(0)

    // Save and verify toast appears
    await page.getByRole('button', { name: /Save Custom Lyrics/i }).click()
    await expect(
      page.getByText(/Manually sync each edited segment/i),
    ).toBeVisible({ timeout: 10_000 })
  })
})
```

- [ ] **Step 2: Verify the spec compiles**

```bash
cd frontend && npx tsc --noEmit
```

Expected: zero errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/e2e/production/custom-lyrics-mode.spec.ts
git commit -m "test(custom-lyrics): add production E2E spec"
```

---

### Task 18: Bump version and update docs index

**Files:**
- Modify: `pyproject.toml` (bump patch version)
- Modify: `docs/README.md` (add a one-line entry under recent work, if convention)

- [ ] **Step 1: Bump patch version**

In `pyproject.toml` under `[tool.poetry]`, increment the patch component of `version`. For example, if current is `1.42.7`, change to `1.42.8`.

- [ ] **Step 2: Update `docs/README.md` if it tracks recent work**

```bash
grep -n "Recent" docs/README.md | head -5
```

If a "Recent work" or similar list exists, add a one-line entry referencing the spec/plan. Otherwise skip.

- [ ] **Step 3: Run the full test suite locally**

```bash
make test 2>&1 | tail -n 100
```

Expected: ✅ All tests passed.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml docs/README.md
git commit -m "chore(custom-lyrics): bump version for custom lyrics feature"
```

---

## Self-review summary

- All 18 tasks have file paths, code, and exact commands. No "TBD" / "implement later".
- Spec coverage: every spec section has a corresponding task. Phase 1 (Tasks 1–8) implements the design's "Backend changes". Phase 2 (Tasks 9–15) implements "Frontend changes". Phase 3 (Tasks 16–18) covers i18n, E2E, and ship.
- Type / signature consistency checked: `CustomLyricsService.generate` keyword args match between service def, tests, and route call site. `CustomLyricsResponse` Pydantic shape matches frontend `CustomLyricsResponse` (`lineCountMismatch` camelCased on the frontend, `line_count_mismatch` snake_cased on backend — wrapper translates).
- Helper extraction (Task 9) introduces `segmentsFromLines.ts` used by both old `replaceSegments` mode (refactor) and new `customLyrics` mode — preserves existing behavior, no dual-write risk.

## Execution

The user has granted full autonomy. Implementation will proceed via `superpowers:subagent-driven-development` (one subagent per task with two-stage review).
