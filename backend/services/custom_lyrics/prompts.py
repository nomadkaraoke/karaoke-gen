"""System and user prompt builders for custom-lyrics generation."""
from __future__ import annotations

from typing import Optional

from backend.services.custom_lyrics.settings import GenerationSettings, params_for
from backend.services.custom_lyrics.validator import LineValidation


_BASE_SYSTEM = """You are a professional karaoke lyricist. Your task is to produce custom karaoke lyrics for a song, applying customisations the client has requested while making the result singable.

RULES:
1. Each output line corresponds positionally to the same input line (line 1 -> line 1, etc.). Do not reorder.
2. Preserve each line's role (verse/chorus/repeat). If the original repeats a chorus three times, the customised version should also repeat its corresponding chorus three times.
3. Apply the client's customisations as faithfully as possible. If the client gave explicit "replace X with Y" instructions, follow them. If they gave a free-form theme or names without explicit mapping, use your best judgement to weave them in where they fit naturally.
4. Where the client gave no clear customisation for a line, keep the original lyric unchanged.
5. Output JSON only, matching the schema. No commentary, no surrounding prose."""


_RULE_FIXED_COUNT = "6. The output MUST contain exactly the same number of lines as the input transcribed lyrics."
_RULE_FLEXIBLE_COUNT = "6. You may output any number of lines that flows naturally; do not pad to match the input length."
_RULE_NO_REWORD = "7. Do NOT paraphrase or reword the client's text. Use it verbatim wherever it appears in the input."
_RULE_USE_ALL = "8. All client-provided content MUST appear in the output. Do not skip any of the client's words."


def build_system_prompt(settings: GenerationSettings) -> str:
    parts = [_BASE_SYSTEM]
    parts.append(_RULE_FIXED_COUNT if settings.fixed_line_count else _RULE_FLEXIBLE_COUNT)
    if not settings.allow_reword:
        parts.append(_RULE_NO_REWORD)
    if not settings.allow_omit:
        parts.append(_RULE_USE_ALL)
    parts.append("")
    parts.append(f"GUIDANCE: {params_for(settings.strictness).prompt_phrase}")
    return "\n".join(parts)


def _format_target_count(counts: list[int]) -> str:
    """Render the 4-method target as a single ≈ approximate value (median for stability)."""
    if not counts:
        return "≈0 syl"
    sorted_c = sorted(counts)
    mid = sorted_c[len(sorted_c) // 2]
    return f"≈{mid} syl"


def build_initial_user_prompt(
    *,
    artist: Optional[str],
    title: Optional[str],
    target_lines: list[str],
    target_syllables: list[list[int]],
    time_budgets: list[float],
    observed_rates: list[float],
    custom_text_block: str,
    notes: Optional[str],
    settings: GenerationSettings,
) -> str:
    n = len(target_lines)
    annotated = []
    for i, line in enumerate(target_lines):
        meta = (
            f"[{_format_target_count(target_syllables[i])}, "
            f"{time_budgets[i]:.1f}s @ {observed_rates[i]:.1f} syl/s]"
        )
        annotated.append(f"{i + 1}. {meta} {line}")
    numbered = "\n".join(annotated)
    notes_block = notes.strip() if notes else "(none)"
    custom_block = custom_text_block if custom_text_block else "(see attached PDF)"
    line_count_clause = (
        f"exactly {n} lines" if settings.fixed_line_count else "an appropriate number of lines"
    )
    return (
        f"SONG: {artist or '(unknown artist)'} - {title or '(unknown title)'}\n\n"
        f"ORIGINAL TRANSCRIBED LYRICS ({n} lines, with per-line syllable target and time budget):\n"
        f"{numbered}\n\n"
        f"CLIENT CUSTOM LYRICS / INSTRUCTIONS:\n{custom_block}\n\n"
        f"ADDITIONAL NOTES FROM OPERATOR:\n{notes_block}\n\n"
        f"Produce the customised lyrics in JSON format with {line_count_clause}, "
        f"honouring the per-line syllable targets and time budgets where possible."
    )


def build_repair_user_prompt(
    *,
    previous_output: list[str],
    violations: list[LineValidation],
    target_lines: list[str],
    target_syllables: list[list[int]],
    time_budgets: list[float],
    observed_rates: list[float],
    settings: GenerationSettings,
) -> str:
    if not violations:
        return "no violations to fix; return previous output unchanged"

    n = len(previous_output)
    violating_indices = {v.line_index for v in violations}
    keep_indices = sorted(i + 1 for i in range(n) if i not in violating_indices)
    keep_clause = (
        ", ".join(str(i) for i in keep_indices) if keep_indices else "(none)"
    )

    fix_blocks: list[str] = []
    for v in violations:
        i = v.line_index
        target_meta = _format_target_count(target_syllables[i])
        delta_sign = "+" if (v.candidate_syllables and v.target_syllables and
                              max(v.candidate_syllables) > max(v.target_syllables)) else "-"
        actual = max(v.candidate_syllables) if v.candidate_syllables else 0
        fix_blocks.append(
            f"- Line {i + 1}: target {target_meta}, time budget {time_budgets[i]:.1f}s, "
            f"sung at ~{observed_rates[i]:.1f} syl/s.\n"
            f"  Original transcribed line (for inspiration on how the budget can be filled): "
            f"\"{target_lines[i]}\"\n"
            f"  You wrote: \"{v.candidate_text}\" "
            f"({actual} syl, {delta_sign}{v.min_delta} {'over' if delta_sign == '+' else 'under'}).\n"
            f"  Trim or paraphrase to fit while preserving the client's intent."
        )

    return (
        f"Your previous output had {len(violations)} lines that exceeded the syllable budget too far.\n"
        f"Please fix ONLY those lines. Keep every other line exactly the same as before.\n\n"
        f"Lines to fix:\n\n"
        f"{chr(10).join(fix_blocks)}\n\n"
        f"Lines to keep unchanged: {keep_clause}\n\n"
        f"Return JSON with all {n} lines, in original order, with the listed lines fixed "
        f"and all other lines unchanged."
    )
