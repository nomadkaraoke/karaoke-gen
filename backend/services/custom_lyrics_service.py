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

from google import genai
from google.genai import types

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
