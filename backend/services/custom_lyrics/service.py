"""Custom lyrics service: validate-and-repair loop orchestrator."""
from __future__ import annotations

import io
import json
import logging
import time
from typing import Any, Optional

from google import genai
from google.genai import types

from backend.config import get_settings
from backend.services.custom_lyrics.prompts import (
    build_initial_user_prompt,
    build_repair_user_prompt,
    build_system_prompt,
)
from backend.services.custom_lyrics.result import CustomLyricsResult, StopReason
from backend.services.custom_lyrics.settings import (
    GenerationSettings,
    StrictnessLevel,
    params_for,
)
from backend.services.custom_lyrics.timing import redistribute_timing_proportional
from backend.services.custom_lyrics.validator import (
    LineValidation,
    Severity,
    validate,
)
from karaoke_gen.lyrics_transcriber.utils.syllable_counter import SyllableCounter


logger = logging.getLogger(__name__)


SUPPORTED_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/pdf": "pdf",
    "text/plain": "txt",
    "text/markdown": "md",
}


def _format_worst_lines(validations: list[LineValidation], n: int = 3) -> list[dict]:
    """Top-N failing lines for log breadcrumbs. Cuts text at 80 chars."""
    failing = sorted(
        (v for v in validations if not v.passes),
        key=lambda v: v.min_delta,
        reverse=True,
    )[:n]
    return [
        {
            "line_index": v.line_index,
            "min_delta": v.min_delta,
            "severity": v.severity.value,
            "target": v.target_text[:80],
            "candidate": v.candidate_text[:80],
            "target_syllables": v.target_syllables,
            "candidate_syllables": v.candidate_syllables,
        }
        for v in failing
    ]


class CustomLyricsServiceError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class CustomLyricsService:
    def __init__(self, counter: Optional[SyllableCounter] = None) -> None:
        self.settings = get_settings()
        self._counter = counter or SyllableCounter()

    # ---- public ----

    def generate(
        self,
        *,
        job_id: str,
        target_lines: list[str],
        target_segments: list[Any],
        artist: Optional[str],
        title: Optional[str],
        custom_text: Optional[str],
        file_bytes: Optional[bytes],
        file_mime: Optional[str],
        file_name: Optional[str],
        notes: Optional[str],
        settings: GenerationSettings,
    ) -> CustomLyricsResult:
        start = time.monotonic()
        n = len(target_lines)
        if n == 0:
            raise CustomLyricsServiceError(
                "target_lines must not be empty", status_code=400
            )
        if n > self.settings.custom_lyrics_max_input_lines:
            raise CustomLyricsServiceError(
                f"target_lines exceeds max ({self.settings.custom_lyrics_max_input_lines})",
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

        # Pre-compute per-line metadata (used for prompts AND for validator)
        target_syllables = [self._counter.count_per_line(line) for line in target_lines]
        time_budgets = [self._segment_duration(seg) for seg in target_segments]
        observed_rates = [
            (sum(s) / 4 / dur) if dur > 0 else 0.0
            for s, dur in zip(target_syllables, time_budgets)
        ]

        params = params_for(settings.strictness)
        sys_prompt = build_system_prompt(settings)
        initial_user_prompt = build_initial_user_prompt(
            artist=artist, title=title,
            target_lines=target_lines,
            target_syllables=target_syllables,
            time_budgets=time_budgets,
            observed_rates=observed_rates,
            custom_text_block=custom_text_block,
            notes=notes,
            settings=settings,
        )

        logger.info(
            "custom_lyrics_initial_call_start",
            extra={
                "job_id": job_id,
                "model": self.settings.custom_lyrics_model,
                "input_lines": n,
                "settings": settings.to_dict(),
                "tolerance": params.tolerance,
                "max_iterations": params.max_iterations,
                "system_prompt_chars": len(sys_prompt),
                "user_prompt_chars": len(initial_user_prompt),
                "pdf_bytes": len(pdf_bytes) if pdf_bytes else 0,
                "input_lines_total_syllables": sum(sum(s) / 4 for s in target_syllables),
            },
        )

        try:
            candidate_lines = self._call_gemini(
                system_prompt=sys_prompt,
                user_prompt=initial_user_prompt,
                pdf_bytes=pdf_bytes,
                settings=settings,
            )
        except Exception:
            logger.exception(
                "custom_lyrics_initial_call_failed",
                extra={"job_id": job_id, "stage": "initial"},
            )
            raise

        logger.info(
            "custom_lyrics_initial_call_complete",
            extra={
                "job_id": job_id,
                "candidate_lines": len(candidate_lines),
                "expected_lines": n,
                "line_count_match": len(candidate_lines) == n,
            },
        )

        # Verbatim path: skip the repair loop entirely
        if settings.strictness is StrictnessLevel.VERBATIM:
            metadata = self._validate_for_metadata_only(
                candidate_lines, target_lines, target_segments,
            )
            return self._finalise(
                lines=candidate_lines,
                metadata=metadata,
                iterations_used=0,
                stop_reason=StopReason.VERBATIM_SKIP,
                settings=settings,
                target_lines=target_lines,
                target_segments=target_segments,
                start=start,
                job_id=job_id,
            )

        # Validate-and-repair loop
        validations = self._validate_with_length_handling(
            candidate_lines, target_lines, target_segments,
            tolerance=params.tolerance, fixed=settings.fixed_line_count,
        )
        best = (candidate_lines, validations)
        prev_score = self._score(validations)
        iteration = 0
        violation_count = sum(1 for v in validations if not v.passes)
        stop_reason = StopReason.SUCCESS if violation_count == 0 else StopReason.MAX_ITERS_REACHED

        logger.info(
            "custom_lyrics_initial_validation",
            extra={
                "job_id": job_id,
                "violations": prev_score[0],
                "total_min_delta": prev_score[1],
                "worst_lines": _format_worst_lines(validations),
            },
        )

        while iteration < params.max_iterations:
            violations = [v for v in validations if not v.passes]
            if not violations:
                stop_reason = StopReason.SUCCESS
                break

            repair_prompt = build_repair_user_prompt(
                previous_output=candidate_lines,
                violations=violations,
                target_lines=target_lines,
                target_syllables=target_syllables,
                time_budgets=time_budgets,
                observed_rates=observed_rates,
                settings=settings,
            )
            iter_num = iteration + 1
            logger.info(
                "custom_lyrics_repair_call_start",
                extra={
                    "job_id": job_id,
                    "iteration": iter_num,
                    "max_iterations": params.max_iterations,
                    "violations_to_repair": len(violations),
                    "repair_prompt_chars": len(repair_prompt),
                },
            )
            try:
                candidate_lines = self._call_gemini(
                    system_prompt=sys_prompt,
                    user_prompt=repair_prompt,
                    pdf_bytes=pdf_bytes,
                    settings=settings,
                )
            except Exception:
                logger.exception(
                    "custom_lyrics_repair_call_failed",
                    extra={"job_id": job_id, "iteration": iter_num, "stage": "repair"},
                )
                raise
            validations = self._validate_with_length_handling(
                candidate_lines, target_lines, target_segments,
                tolerance=params.tolerance, fixed=settings.fixed_line_count,
            )

            new_score = self._score(validations)
            improved = new_score < prev_score
            if improved:
                best = (candidate_lines, validations)

            logger.info(
                "custom_lyrics_repair_iter_complete",
                extra={
                    "job_id": job_id,
                    "iteration": iter_num,
                    "violations_before": prev_score[0],
                    "violations_after": new_score[0],
                    "delta_total_before": prev_score[1],
                    "delta_total_after": new_score[1],
                    "improved": improved,
                    "candidate_line_count": len(candidate_lines),
                    "worst_lines": _format_worst_lines(validations),
                },
            )

            # Plateau when score did not strictly improve
            if new_score >= prev_score:
                stop_reason = StopReason.PLATEAU
                iteration += 1
                break
            prev_score = new_score
            iteration += 1
            if new_score[0] == 0:
                stop_reason = StopReason.SUCCESS
                break

        return self._finalise(
            lines=best[0],
            metadata=best[1],
            iterations_used=iteration,
            stop_reason=stop_reason,
            settings=settings,
            target_lines=target_lines,
            target_segments=target_segments,
            start=start,
            job_id=job_id,
        )

    # ---- helpers ----

    @staticmethod
    def _segment_duration(seg: Any) -> float:
        if isinstance(seg, dict):
            start = seg.get("start_time")
            end = seg.get("end_time")
        else:
            start = getattr(seg, "start_time", None)
            end = getattr(seg, "end_time", None)
        if start is None or end is None:
            return 0.0
        try:
            return max(0.0, float(end) - float(start))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _score(validations: list[LineValidation]) -> tuple[int, int]:
        """Lower is better. Sort by (violation_count, total_min_delta)."""
        violations = sum(1 for v in validations if not v.passes)
        total = sum(v.min_delta for v in validations)
        return (violations, total)

    def _validate_for_metadata_only(
        self,
        candidate_lines: list[str],
        target_lines: list[str],
        target_segments: list[Any],
    ) -> list[LineValidation]:
        """Validator run with infinite tolerance (everything passes); used for Verbatim."""
        if len(candidate_lines) != len(target_lines):
            return self._validate_with_length_handling(
                candidate_lines, target_lines, target_segments,
                tolerance=10**9, fixed=False,
            )
        return validate(
            candidate_lines, target_lines, target_segments,
            self._counter, tolerance=10**9,
        )

    def _validate_with_length_handling(
        self,
        candidate_lines: list[str],
        target_lines: list[str],
        target_segments: list[Any],
        *,
        tolerance: int,
        fixed: bool,
    ) -> list[LineValidation]:
        """Handle length mismatches without raising:
           - If fixed=True and lengths differ, all positions count as violations
             beyond the overlap.
           - If fixed=False, only validate the overlap (variable line count is OK).
        """
        if len(candidate_lines) == len(target_lines):
            return validate(
                candidate_lines, target_lines, target_segments,
                self._counter, tolerance=tolerance,
            )
        overlap = min(len(candidate_lines), len(target_lines))
        partial = validate(
            candidate_lines[:overlap], target_lines[:overlap],
            target_segments[:overlap], self._counter, tolerance=tolerance,
        )
        if fixed and len(candidate_lines) != len(target_lines):
            extra = abs(len(candidate_lines) - len(target_lines))
            for i in range(overlap, overlap + extra):
                idx = i
                tgt_text = target_lines[idx] if idx < len(target_lines) else ""
                cand_text = candidate_lines[idx] if idx < len(candidate_lines) else ""
                partial.append(LineValidation(
                    line_index=idx,
                    target_text=tgt_text,
                    candidate_text=cand_text,
                    target_syllables=[0, 0, 0, 0],
                    candidate_syllables=[0, 0, 0, 0],
                    min_delta=99,
                    passes=False,
                    severity=Severity.MAJOR,
                    time_budget_seconds=0.0,
                ))
        return partial

    def _finalise(
        self,
        *,
        lines: list[str],
        metadata: list[LineValidation],
        iterations_used: int,
        stop_reason: StopReason,
        settings: GenerationSettings,
        target_lines: list[str],
        target_segments: list[Any],
        start: float,
        job_id: str,
    ) -> CustomLyricsResult:
        n = len(target_lines)
        m = len(lines)
        line_count_mismatch = (m != n)

        new_timing: Optional[list[tuple[float, float]]] = None
        if line_count_mismatch and not settings.fixed_line_count and target_segments:
            window_start = self._segment_start(target_segments[0])
            window_end = self._segment_end(target_segments[-1])
            new_timing = redistribute_timing_proportional(
                new_lines=lines,
                total_window=(window_start, window_end),
                counter=self._counter,
            )

        warnings: list[str] = []
        if line_count_mismatch and settings.fixed_line_count:
            stop_reason = StopReason.LINE_COUNT_MISMATCH
            warnings.append(
                f"AI returned {m} lines but {n} were expected. "
                f"Manually adjust the textarea or click Regenerate."
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        result = CustomLyricsResult(
            lines=lines,
            line_metadata=metadata,
            iterations_used=iterations_used,
            stop_reason=stop_reason,
            settings_applied=settings,
            model=self.settings.custom_lyrics_model,
            duration_ms=duration_ms,
            new_segment_timing=new_timing,
            line_count_mismatch=line_count_mismatch,
            warnings=warnings,
        )

        logger.info(
            "custom_lyrics_generated",
            extra={
                "job_id": job_id,
                "model": result.model,
                "input_lines": n,
                "output_lines": m,
                "iterations_used": iterations_used,
                "stop_reason": stop_reason.value,
                "settings": settings.to_dict(),
                "final_violation_count": sum(1 for v in metadata if not v.passes),
                "line_count_mismatch": line_count_mismatch,
                "duration_ms": duration_ms,
            },
        )
        return result

    @staticmethod
    def _segment_start(seg: Any) -> float:
        v = seg["start_time"] if isinstance(seg, dict) else seg.start_time
        return float(v or 0.0)

    @staticmethod
    def _segment_end(seg: Any) -> float:
        v = seg["end_time"] if isinstance(seg, dict) else seg.end_time
        return float(v or 0.0)

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
            self._validate_file(file_bytes=file_bytes, file_mime=file_mime, file_name=file_name)
            kind = SUPPORTED_MIMES[file_mime]
            if kind == "pdf":
                pdf_bytes = file_bytes
            elif kind == "docx":
                text_chunks.append(self._extract_docx_text(file_bytes))
            elif kind in ("txt", "md"):
                text_chunks.append(file_bytes.decode("utf-8", errors="replace"))
            else:
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
        try:
            import docx as docx_mod
        except ImportError as exc:
            raise CustomLyricsServiceError(
                "python-docx is not installed", status_code=500
            ) from exc

        try:
            doc = docx_mod.Document(io.BytesIO(file_bytes))
        except Exception as exc:
            raise CustomLyricsServiceError(
                f"could not parse .docx: {exc}", status_code=400
            ) from exc

        paragraphs = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
        return "\n".join(paragraphs)

    def _call_gemini(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        pdf_bytes: Optional[bytes],
        settings: GenerationSettings,
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
                system_instruction=system_prompt,
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
    global _service_instance
    if _service_instance is None:
        _service_instance = CustomLyricsService()
    return _service_instance
