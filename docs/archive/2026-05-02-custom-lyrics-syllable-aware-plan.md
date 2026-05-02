# Syllable-Aware Custom Lyrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the LLM custom-lyrics generator syllable-aware via a deterministic validate-and-repair loop, expose 4 operator controls (3 toggles + 5-position strictness slider), support variable line count, and ship a first-class eval harness.

**Architecture:** Extract a shared `SyllableCounter` utility from the existing `SyllablesMatchHandler`. Build a pure validator that uses it. Wrap the existing single-call Gemini service in a validate-and-repair loop with plateau detection and best-iteration tracking. Plumb 4 operator settings end-to-end. Add a flat-proportional save path for variable line count. Ship an eval harness at `backend/eval/custom_lyrics/` with a 5-fixture corpus, LLM-call cache, baseline diffing, and a pre/post baseline pair committed in the PR proving v1-ships criteria are met.

**Tech Stack:** Python 3.11 (FastAPI backend, Vertex AI Gemini 3.1 Pro), Next.js (frontend), pytest, Jest + Playwright. Existing libs: spacy + spacy-syllables, pyphen, NLTK cmudict, syllables. No new dependencies.

**Spec:** `docs/archive/2026-05-02-custom-lyrics-syllable-aware-design.md`

---

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `karaoke_gen/lyrics_transcriber/utils/__init__.py` | Package marker |
| `karaoke_gen/lyrics_transcriber/utils/syllable_counter.py` | `SyllableCounter` class — 4-method counts + any-method-agrees check |
| `backend/services/custom_lyrics/__init__.py` | Package marker (existing service moves here) |
| `backend/services/custom_lyrics/service.py` | `CustomLyricsService` orchestrator (split from monolithic file) |
| `backend/services/custom_lyrics/settings.py` | `GenerationSettings` dataclass + `StrictnessLevel` enum + strictness→params map |
| `backend/services/custom_lyrics/validator.py` | `LineValidation` dataclass + `validate()` pure function |
| `backend/services/custom_lyrics/prompts.py` | System prompt fragments + `build_initial_prompt()` + `build_repair_prompt()` |
| `backend/services/custom_lyrics/result.py` | `CustomLyricsResult` + `LineMetadata` + `SectionInfo` dataclasses |
| `backend/services/custom_lyrics/timing.py` | `redistribute_timing_proportional()` for variable line count |
| `backend/tests/services/custom_lyrics/test_syllable_counter.py` | Unit tests for shared utility |
| `backend/tests/services/custom_lyrics/test_validator.py` | Unit tests for validator |
| `backend/tests/services/custom_lyrics/test_prompts.py` | Snapshot tests for prompt builders |
| `backend/tests/services/custom_lyrics/test_settings.py` | Strictness→params mapping tests |
| `backend/tests/services/custom_lyrics/test_timing.py` | Variable line count timing tests |
| `frontend/components/lyrics-review/modals/CustomLyricsSettings.tsx` | Settings panel sub-component (toggles + slider) |
| `frontend/components/lyrics-review/modals/CustomLyricsPreview.tsx` | Per-line annotated preview sub-component |
| `frontend/components/lyrics-review/modals/__tests__/CustomLyricsSettings.test.tsx` | Jest tests for settings panel |
| `frontend/components/lyrics-review/modals/__tests__/CustomLyricsPreview.test.tsx` | Jest tests for preview |
| `backend/eval/__init__.py` | Package marker |
| `backend/eval/custom_lyrics/__init__.py` | Package marker |
| `backend/eval/custom_lyrics/run.py` | CLI entrypoint (`python -m backend.eval.custom_lyrics.run`) |
| `backend/eval/custom_lyrics/runner.py` | Orchestration: load fixture → call service → score → write report |
| `backend/eval/custom_lyrics/scorer.py` | Pure metric functions over (output, fixture) |
| `backend/eval/custom_lyrics/report.py` | Markdown report generation |
| `backend/eval/custom_lyrics/cache.py` | LLM-call disk cache by `hash(prompt + settings + model)` |
| `backend/eval/custom_lyrics/fixtures/year-5-stars-shake-it-off/metadata.json` | Fixture metadata |
| `backend/eval/custom_lyrics/fixtures/year-5-stars-shake-it-off/original_lyrics.txt` | Pulled from GCS via bootstrap script |
| `backend/eval/custom_lyrics/fixtures/year-5-stars-shake-it-off/original_segments.json` | Pulled from GCS via bootstrap script |
| `backend/eval/custom_lyrics/fixtures/year-5-stars-shake-it-off/client_input.txt` | Copied from `~/Projects/nomadkaraoke/year-5-stars-client-custom-lyrics.txt` |
| `backend/eval/custom_lyrics/fixtures/{4 handcrafted}/...` | Handcrafted fixtures |
| `backend/eval/custom_lyrics/_bootstrap_year5.py` | One-shot script to pull Year 5 data from GCS |
| `backend/eval/custom_lyrics/baseline-pre.json` | Eval baseline against current code (pre-validator) |
| `backend/eval/custom_lyrics/baseline-post.json` | Eval baseline against new code (post-validator) |

### Modified files

| Path | Modification |
|---|---|
| `karaoke_gen/lyrics_transcriber/correction/handlers/syllables_match.py` | Refactor to delegate `_count_syllables*` to `SyllableCounter` |
| `backend/services/custom_lyrics_service.py` | Becomes a re-export shim from `backend.services.custom_lyrics.service` for backward compat, then deleted in final task once all imports updated |
| `backend/api/routes/review.py` | Update `generate_custom_lyrics` endpoint: accept `settings_json` form field; return new response shape |
| `backend/config.py` | Add `custom_lyrics_max_iterations`, `custom_lyrics_default_strictness`, `custom_lyrics_max_output_lines_multiplier` |
| `backend/tests/test_custom_lyrics_service.py` | Extend with new loop / settings / variable-line-count cases |
| `backend/tests/api/test_review_custom_lyrics.py` | Extend with settings_json parsing, new response shape |
| `frontend/components/lyrics-review/modals/CustomLyricsMode.tsx` | Add settings + preview sub-components; plumb settings through |
| `frontend/lib/api/customLyrics.ts` | Update request/response types |
| `frontend/lib/lyrics-review/utils/segmentsFromLines.ts` | Accept optional `redistributedTiming` param for variable line count |
| `frontend/messages/en.json` | New keys under `lyricsReview.modals.customLyricsMode.settings.*` and `.preview.*` |
| `frontend/components/lyrics-review/modals/__tests__/CustomLyricsMode.test.tsx` | Extend Jest tests |
| `frontend/e2e/production/custom-lyrics-mode.spec.ts` | Extend Playwright tests for settings + preview |
| `pyproject.toml` | Bump patch version |
| `Makefile` | Add `eval-custom-lyrics` target |
| `docs/LESSONS-LEARNED.md` | Add post-implementation lesson about validate-and-repair pattern |

---

## Phase 1 — Shared SyllableCounter utility (refactor)

This phase is a behavior-preserving extraction. Existing `SyllablesMatchHandler` tests must continue to pass.

### Task 1: Create `SyllableCounter` package skeleton

**Files:**
- Create: `karaoke_gen/lyrics_transcriber/utils/__init__.py`
- Create: `karaoke_gen/lyrics_transcriber/utils/syllable_counter.py`
- Create: `backend/tests/services/custom_lyrics/__init__.py`
- Create: `backend/tests/services/custom_lyrics/test_syllable_counter.py`

- [ ] **Step 1: Create the `__init__.py` files**

```bash
mkdir -p karaoke_gen/lyrics_transcriber/utils
mkdir -p backend/tests/services/custom_lyrics
touch karaoke_gen/lyrics_transcriber/utils/__init__.py
touch backend/tests/services/custom_lyrics/__init__.py
```

- [ ] **Step 2: Write the failing test for `SyllableCounter` instantiation**

Create `backend/tests/services/custom_lyrics/test_syllable_counter.py`:

```python
"""Tests for the shared SyllableCounter utility."""
from __future__ import annotations

import pytest

from karaoke_gen.lyrics_transcriber.utils.syllable_counter import SyllableCounter


@pytest.fixture(scope="module")
def counter() -> SyllableCounter:
    return SyllableCounter()


def test_instantiates_without_error(counter: SyllableCounter) -> None:
    assert counter is not None


def test_count_per_word_returns_four_method_counts(counter: SyllableCounter) -> None:
    counts = counter.count_per_word(["hello"])
    assert isinstance(counts, list)
    assert len(counts) == 4
    assert all(isinstance(c, int) and c > 0 for c in counts)


def test_count_per_word_empty_input(counter: SyllableCounter) -> None:
    counts = counter.count_per_word([])
    assert counts == [0, 0, 0, 0]


def test_count_per_line_tokenises_then_counts(counter: SyllableCounter) -> None:
    line_counts = counter.count_per_line("hello world")
    word_counts = counter.count_per_word(["hello", "world"])
    assert line_counts == word_counts


def test_count_per_line_handles_punctuation(counter: SyllableCounter) -> None:
    counts = counter.count_per_line("Hello, world!")
    assert all(c >= 2 for c in counts)
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
poetry run pytest backend/tests/services/custom_lyrics/test_syllable_counter.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'karaoke_gen.lyrics_transcriber.utils.syllable_counter'`.

- [ ] **Step 4: Implement minimal `SyllableCounter`**

Create `karaoke_gen/lyrics_transcriber/utils/syllable_counter.py`. Copy the four counting methods verbatim from `karaoke_gen/lyrics_transcriber/correction/handlers/syllables_match.py` (lines 98–145), wrap in a class:

```python
"""Shared 4-method syllable counter.

Extracted from SyllablesMatchHandler so multiple components can reuse it
without duplicating the heavy spacy / NLTK initialisation.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

import nltk
import pyphen
import spacy
import syllables
from nltk.corpus import cmudict
from spacy_syllables import SpacySyllables

try:
    from backend.services.spacy_preloader import get_preloaded_model
    from backend.services.nltk_preloader import get_preloaded_cmudict

    _HAS_PRELOADER = True
except ImportError:
    _HAS_PRELOADER = False


_TOKEN_RE = re.compile(r"[A-Za-z']+")


class SyllableCounter:
    """Counts syllables using 4 independent methods. Returns lists, never single ints."""

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or logging.getLogger(__name__)
        init_start = time.time()

        _ = SpacySyllables  # silence unused-import warning

        if _HAS_PRELOADER:
            preloaded = get_preloaded_model("en_core_web_sm")
            if preloaded is not None:
                self.nlp = preloaded
                if "syllables" not in self.nlp.pipe_names:
                    self.nlp.add_pipe("syllables", after="tagger")
                self._init_nltk_resources()
                self.logger.info(
                    "Initialised SyllableCounter in %.2fs (preloaded)",
                    time.time() - init_start,
                )
                return

        try:
            self.nlp = spacy.load("en_core_web_sm")
        except OSError as exc:
            raise OSError(
                "spacy model 'en_core_web_sm' not installed. "
                "Run: python -m spacy download en_core_web_sm"
            ) from exc

        if "syllables" not in self.nlp.pipe_names:
            self.nlp.add_pipe("syllables", after="tagger")

        self._init_nltk_resources()
        self.logger.info(
            "Initialised SyllableCounter in %.2fs (lazy)",
            time.time() - init_start,
        )

    def _init_nltk_resources(self) -> None:
        self.dic = pyphen.Pyphen(lang="en_US")

        if _HAS_PRELOADER:
            preloaded = get_preloaded_cmudict()
            if preloaded is not None:
                self.cmudict = preloaded
                return

        try:
            self.cmudict = cmudict.dict()
        except LookupError:
            nltk.download("cmudict")
            self.cmudict = cmudict.dict()

    @staticmethod
    def _tokenise(line: str) -> list[str]:
        return _TOKEN_RE.findall(line)

    def _count_spacy(self, words: list[str]) -> int:
        if not words:
            return 0
        text = " ".join(words)
        doc = self.nlp(text)
        return sum(token._.syllables_count or 1 for token in doc)

    def _count_pyphen(self, words: list[str]) -> int:
        total = 0
        for word in words:
            hyphenated = self.dic.inserted(word)
            total += len(hyphenated.split("-")) if hyphenated else 1
        return total

    def _count_nltk(self, words: list[str]) -> int:
        total = 0
        for word in words:
            w = word.lower()
            if w in self.cmudict:
                total += len([ph for ph in self.cmudict[w][0] if ph[-1].isdigit()])
            else:
                total += 1
        return total

    def _count_lib(self, words: list[str]) -> int:
        return sum(syllables.estimate(word) for word in words)

    def count_per_word(self, words: list[str]) -> list[int]:
        if not words:
            return [0, 0, 0, 0]
        return [
            self._count_spacy(words),
            self._count_pyphen(words),
            self._count_nltk(words),
            self._count_lib(words),
        ]

    def count_per_line(self, line: str) -> list[int]:
        return self.count_per_word(self._tokenise(line))

    @staticmethod
    def any_method_within(
        candidate_counts: list[int],
        target_counts: list[int],
        tolerance: int,
    ) -> bool:
        """True iff some pair of counters across candidate × target agrees within tolerance."""
        if not candidate_counts or not target_counts:
            return False
        return any(
            abs(c - t) <= tolerance
            for c in candidate_counts
            for t in target_counts
        )

    @staticmethod
    def min_delta(
        candidate_counts: list[int],
        target_counts: list[int],
    ) -> int:
        """Minimum |c - t| across all 4×4 method pairs."""
        if not candidate_counts or not target_counts:
            return 0
        return min(
            abs(c - t)
            for c in candidate_counts
            for t in target_counts
        )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
poetry run pytest backend/tests/services/custom_lyrics/test_syllable_counter.py -v
```

Expected: PASS for all 5 tests.

- [ ] **Step 6: Commit**

```bash
git add karaoke_gen/lyrics_transcriber/utils/ backend/tests/services/custom_lyrics/__init__.py backend/tests/services/custom_lyrics/test_syllable_counter.py
git commit -m "feat(syllable-counter): extract shared 4-method syllable counter"
```

---

### Task 2: Add `any_method_within` and `min_delta` static helpers tests

**Files:**
- Modify: `backend/tests/services/custom_lyrics/test_syllable_counter.py`

- [ ] **Step 1: Add tests for the static helpers**

Append to `backend/tests/services/custom_lyrics/test_syllable_counter.py`:

```python
def test_any_method_within_all_match() -> None:
    assert SyllableCounter.any_method_within([5, 5, 5, 5], [5, 5, 5, 5], tolerance=0) is True


def test_any_method_within_one_pair_close() -> None:
    # spacy(candidate)=10, syllables(target)=8 → delta=2; passes at tol=2
    assert SyllableCounter.any_method_within([10, 11, 12, 13], [6, 7, 7, 8], tolerance=2) is True


def test_any_method_within_no_pair_close() -> None:
    assert SyllableCounter.any_method_within([10, 10, 10, 10], [5, 5, 5, 5], tolerance=2) is False


def test_any_method_within_empty_inputs_returns_false() -> None:
    assert SyllableCounter.any_method_within([], [5, 5, 5, 5], tolerance=10) is False
    assert SyllableCounter.any_method_within([5, 5, 5, 5], [], tolerance=10) is False


def test_min_delta() -> None:
    assert SyllableCounter.min_delta([10, 11, 12, 13], [6, 7, 7, 8]) == 2
    assert SyllableCounter.min_delta([5, 5, 5, 5], [5, 5, 5, 5]) == 0
    assert SyllableCounter.min_delta([], [5, 5, 5, 5]) == 0
```

- [ ] **Step 2: Run tests to verify all pass**

```bash
poetry run pytest backend/tests/services/custom_lyrics/test_syllable_counter.py -v
```

Expected: PASS for all 10 tests (5 new + 5 from Task 1).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/services/custom_lyrics/test_syllable_counter.py
git commit -m "test(syllable-counter): cover any_method_within and min_delta edge cases"
```

---

### Task 3: Refactor `SyllablesMatchHandler` to delegate to `SyllableCounter`

**Files:**
- Modify: `karaoke_gen/lyrics_transcriber/correction/handlers/syllables_match.py`

- [ ] **Step 1: Verify existing handler tests pass before changing anything**

```bash
poetry run pytest karaoke_gen/lyrics_transcriber/tests/correction/handlers/test_syllables_match*.py -v 2>&1 | tail -20
```

(If no such tests exist, find them: `find karaoke_gen -name "*syllables_match*" -name "test_*"`. Run all matches.)

Expected: PASS. Note baseline pass count.

- [ ] **Step 2: Replace the handler's `_count_syllables*` methods with delegation**

In `karaoke_gen/lyrics_transcriber/correction/handlers/syllables_match.py`:

1. Remove the four `_count_syllables_spacy`, `_count_syllables_pyphen`, `_count_syllables_nltk`, `_count_syllables_lib` methods (lines 98–132).
2. Replace `_count_syllables` (line 134) to delegate:

```python
    def _count_syllables(self, words: list[str]) -> list[int]:
        """Count syllables using multiple methods (delegates to shared SyllableCounter)."""
        counts = self._counter.count_per_word(words)
        text = " ".join(words)
        self.logger.debug(
            f"Syllable counts for '{text}': spacy={counts[0]}, pyphen={counts[1]}, "
            f"nltk={counts[2]}, syllables={counts[3]}"
        )
        return counts
```

3. Replace `__init__` to construct a `SyllableCounter` (preserving the existing init logic for backward compat with any external callers that might inspect `self.nlp` etc., assign them from the counter):

```python
    def __init__(self, logger: Optional[logging.Logger] = None):
        super().__init__(logger)
        self.logger = logger or logging.getLogger(__name__)
        self._counter = SyllableCounter(logger=self.logger)
        # Preserve attributes that external code may still inspect
        self.nlp = self._counter.nlp
        self.dic = self._counter.dic
        self.cmudict = self._counter.cmudict
```

4. Replace the imports at the top:

```python
from typing import List, Tuple, Dict, Any, Optional
import logging

from karaoke_gen.lyrics_transcriber.types import GapSequence, WordCorrection
from karaoke_gen.lyrics_transcriber.correction.handlers.base import GapCorrectionHandler
from karaoke_gen.lyrics_transcriber.correction.handlers.word_operations import WordOperations
from karaoke_gen.lyrics_transcriber.utils.syllable_counter import SyllableCounter
```

(Drop the now-unused `spacy`, `pyphen`, `nltk`, `cmudict`, `syllables`, `spacy_syllables` and `time` imports. Keep `_HAS_PRELOADER` block deleted; handled inside `SyllableCounter`.)

- [ ] **Step 3: Re-run handler tests**

```bash
poetry run pytest karaoke_gen/lyrics_transcriber/tests/correction/handlers/test_syllables_match*.py -v 2>&1 | tail -20
```

Expected: same pass count as in Step 1; no regressions.

- [ ] **Step 4: Commit**

```bash
git add karaoke_gen/lyrics_transcriber/correction/handlers/syllables_match.py
git commit -m "refactor(syllables-match): delegate counting to shared SyllableCounter"
```

---

## Phase 2 — Settings model and validator

### Task 4: Create the `custom_lyrics` package skeleton

**Files:**
- Create: `backend/services/custom_lyrics/__init__.py`

- [ ] **Step 1: Create the package directory**

```bash
mkdir -p backend/services/custom_lyrics
```

- [ ] **Step 2: Create `__init__.py` with re-exports**

`backend/services/custom_lyrics/__init__.py`:

```python
"""Custom lyrics generation package.

Stateless helpers that take transcribed lyrics + client custom-lyrics input
and return syllable-aware customised lyrics via Gemini 3.1 Pro.
"""
```

- [ ] **Step 3: Commit**

```bash
git add backend/services/custom_lyrics/__init__.py
git commit -m "feat(custom-lyrics): create custom_lyrics package skeleton"
```

---

### Task 5: Implement `GenerationSettings` and `StrictnessLevel`

**Files:**
- Create: `backend/services/custom_lyrics/settings.py`
- Create: `backend/tests/services/custom_lyrics/test_settings.py`

- [ ] **Step 1: Write failing tests**

`backend/tests/services/custom_lyrics/test_settings.py`:

```python
"""Tests for GenerationSettings and strictness mapping."""
from __future__ import annotations

import pytest

from backend.services.custom_lyrics.settings import (
    GenerationSettings,
    StrictnessLevel,
    StrictnessParams,
    params_for,
    settings_from_dict,
)


def test_default_settings() -> None:
    s = GenerationSettings()
    assert s.allow_reword is True
    assert s.allow_omit is True
    assert s.fixed_line_count is True
    assert s.strictness is StrictnessLevel.BALANCED


def test_params_for_each_level() -> None:
    assert params_for(StrictnessLevel.VERBATIM) == StrictnessParams(
        tolerance=10**6, max_iterations=0, prompt_phrase=mock_phrase("verbatim"),
    ) or params_for(StrictnessLevel.VERBATIM).max_iterations == 0
    assert params_for(StrictnessLevel.LOOSE).tolerance == 4
    assert params_for(StrictnessLevel.LOOSE).max_iterations == 1
    assert params_for(StrictnessLevel.BALANCED).tolerance == 2
    assert params_for(StrictnessLevel.BALANCED).max_iterations == 2
    assert params_for(StrictnessLevel.TIGHT).tolerance == 1
    assert params_for(StrictnessLevel.TIGHT).max_iterations == 3
    assert params_for(StrictnessLevel.STRICT).tolerance == 0
    assert params_for(StrictnessLevel.STRICT).max_iterations == 4


def mock_phrase(level: str) -> str:
    return params_for(StrictnessLevel(level)).prompt_phrase


def test_params_for_has_non_empty_phrase_at_each_level() -> None:
    for lvl in StrictnessLevel:
        assert params_for(lvl).prompt_phrase, f"Empty phrase for {lvl}"


def test_settings_from_dict_defaults_when_empty() -> None:
    s = settings_from_dict({})
    assert s == GenerationSettings()


def test_settings_from_dict_partial() -> None:
    s = settings_from_dict({"strictness": "tight"})
    assert s.strictness is StrictnessLevel.TIGHT
    assert s.allow_reword is True  # default preserved


def test_settings_from_dict_invalid_strictness_raises() -> None:
    with pytest.raises(ValueError):
        settings_from_dict({"strictness": "extreme"})


def test_settings_from_dict_full() -> None:
    s = settings_from_dict({
        "allow_reword": False,
        "allow_omit": False,
        "fixed_line_count": False,
        "strictness": "verbatim",
    })
    assert s.allow_reword is False
    assert s.allow_omit is False
    assert s.fixed_line_count is False
    assert s.strictness is StrictnessLevel.VERBATIM
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest backend/tests/services/custom_lyrics/test_settings.py -v
```

Expected: FAIL with `ModuleNotFoundError: backend.services.custom_lyrics.settings`.

- [ ] **Step 3: Implement `settings.py`**

`backend/services/custom_lyrics/settings.py`:

```python
"""Operator-facing generation settings and strictness→params mapping."""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from enum import Enum
from typing import Any


class StrictnessLevel(str, Enum):
    VERBATIM = "verbatim"
    LOOSE = "loose"
    BALANCED = "balanced"
    TIGHT = "tight"
    STRICT = "strict"


@dataclass(frozen=True)
class StrictnessParams:
    tolerance: int
    max_iterations: int
    prompt_phrase: str


_STRICTNESS_TABLE: dict[StrictnessLevel, StrictnessParams] = {
    StrictnessLevel.VERBATIM: StrictnessParams(
        tolerance=10**6,
        max_iterations=0,
        prompt_phrase="Use the client's text as-is. Rhythm matching is not a goal.",
    ),
    StrictnessLevel.LOOSE: StrictnessParams(
        tolerance=4,
        max_iterations=1,
        prompt_phrase="Aim to roughly match the original syllable count where convenient.",
    ),
    StrictnessLevel.BALANCED: StrictnessParams(
        tolerance=2,
        max_iterations=2,
        prompt_phrase="Match each line's syllable count within 2 where possible.",
    ),
    StrictnessLevel.TIGHT: StrictnessParams(
        tolerance=1,
        max_iterations=3,
        prompt_phrase="Closely match each line's syllable count. Aim for ±1 syllable.",
    ),
    StrictnessLevel.STRICT: StrictnessParams(
        tolerance=0,
        max_iterations=4,
        prompt_phrase="Match each line's syllable count exactly. Rhythm precision is the priority.",
    ),
}


def params_for(level: StrictnessLevel) -> StrictnessParams:
    return _STRICTNESS_TABLE[level]


@dataclass
class GenerationSettings:
    allow_reword: bool = True
    allow_omit: bool = True
    fixed_line_count: bool = True
    strictness: StrictnessLevel = StrictnessLevel.BALANCED

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["strictness"] = self.strictness.value
        return d


def settings_from_dict(data: dict[str, Any]) -> GenerationSettings:
    """Construct GenerationSettings from a partial dict (used by the API layer)."""
    kwargs: dict[str, Any] = {}
    if "allow_reword" in data:
        kwargs["allow_reword"] = bool(data["allow_reword"])
    if "allow_omit" in data:
        kwargs["allow_omit"] = bool(data["allow_omit"])
    if "fixed_line_count" in data:
        kwargs["fixed_line_count"] = bool(data["fixed_line_count"])
    if "strictness" in data:
        kwargs["strictness"] = StrictnessLevel(data["strictness"])  # raises ValueError if invalid
    return GenerationSettings(**kwargs)
```

- [ ] **Step 4: Run tests**

```bash
poetry run pytest backend/tests/services/custom_lyrics/test_settings.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/custom_lyrics/settings.py backend/tests/services/custom_lyrics/test_settings.py
git commit -m "feat(custom-lyrics): GenerationSettings + StrictnessLevel mapping"
```

---

### Task 6: Implement validator (pure function over LLM output)

**Files:**
- Create: `backend/services/custom_lyrics/validator.py`
- Create: `backend/tests/services/custom_lyrics/test_validator.py`

- [ ] **Step 1: Write failing tests**

`backend/tests/services/custom_lyrics/test_validator.py`:

```python
"""Tests for the custom-lyrics validator."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.services.custom_lyrics.validator import (
    LineValidation,
    Severity,
    validate,
)


@pytest.fixture
def stub_counter() -> MagicMock:
    """Returns a SyllableCounter-shaped stub returning fixed counts per call."""
    c = MagicMock()
    c.count_per_line = MagicMock(side_effect=lambda line: _stub_counts(line))
    c.any_method_within = MagicMock(
        side_effect=lambda cand, tgt, tol: min(abs(a - b) for a in cand for b in tgt) <= tol
    )
    c.min_delta = MagicMock(
        side_effect=lambda cand, tgt: min(abs(a - b) for a in cand for b in tgt)
    )
    return c


def _stub_counts(line: str) -> list[int]:
    """Stub: every word = 1 syllable; punctuation ignored. Returns [n, n, n, n]."""
    n = len([w for w in line.split() if any(ch.isalpha() for ch in w)])
    return [n, n, n, n]


def test_validate_all_pass(stub_counter: MagicMock) -> None:
    candidates = ["one two three", "four five"]
    targets = ["aa bb cc", "dd ee"]
    segments = [_segment(0.0, 1.0), _segment(1.0, 2.0)]
    result = validate(candidates, targets, segments, stub_counter, tolerance=0)
    assert len(result) == 2
    assert all(r.passes for r in result)
    assert all(r.severity is Severity.OK for r in result)


def test_validate_one_fails(stub_counter: MagicMock) -> None:
    candidates = ["one two three four five six seven eight"]
    targets = ["aa bb cc"]
    segments = [_segment(0.0, 1.0)]
    result = validate(candidates, targets, segments, stub_counter, tolerance=0)
    assert len(result) == 1
    assert result[0].passes is False
    assert result[0].min_delta == 5
    assert result[0].severity is Severity.MAJOR


def test_validate_severity_minor(stub_counter: MagicMock) -> None:
    candidates = ["a b c d"]      # 4 words
    targets = ["x y z"]           # 3 words
    segments = [_segment(0.0, 1.0)]
    # tolerance=0 → delta=1 → fails; minor severity (delta <= tolerance + 2)
    result = validate(candidates, targets, segments, stub_counter, tolerance=0)
    assert result[0].passes is False
    assert result[0].severity is Severity.MINOR


def test_validate_length_mismatch_raises(stub_counter: MagicMock) -> None:
    with pytest.raises(ValueError, match="length mismatch"):
        validate(["one"], ["aa", "bb"], [_segment(0.0, 1.0)], stub_counter, tolerance=0)


def test_validate_includes_time_budget(stub_counter: MagicMock) -> None:
    segments = [_segment(2.0, 5.5)]
    result = validate(["x"], ["y"], segments, stub_counter, tolerance=0)
    assert result[0].time_budget_seconds == pytest.approx(3.5)


def _segment(start: float, end: float) -> dict:
    return {"start_time": start, "end_time": end}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest backend/tests/services/custom_lyrics/test_validator.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `validator.py`**

`backend/services/custom_lyrics/validator.py`:

```python
"""Pure validator over LLM-generated candidate lines vs. target lines."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol


class Severity(str, Enum):
    OK = "ok"
    MINOR = "minor"
    MAJOR = "major"


@dataclass
class LineValidation:
    line_index: int
    target_text: str
    candidate_text: str
    target_syllables: list[int]
    candidate_syllables: list[int]
    min_delta: int
    passes: bool
    severity: Severity
    time_budget_seconds: float


class _CounterProtocol(Protocol):
    def count_per_line(self, line: str) -> list[int]: ...
    def any_method_within(
        self, candidate_counts: list[int], target_counts: list[int], tolerance: int
    ) -> bool: ...
    def min_delta(
        self, candidate_counts: list[int], target_counts: list[int]
    ) -> int: ...


def _segment_duration(seg: Any) -> float:
    """Accepts dict or object with start_time/end_time; returns end - start (>= 0)."""
    start = seg["start_time"] if isinstance(seg, dict) else seg.start_time
    end = seg["end_time"] if isinstance(seg, dict) else seg.end_time
    if start is None or end is None:
        return 0.0
    return max(0.0, float(end) - float(start))


def validate(
    candidate_lines: list[str],
    target_lines: list[str],
    target_segments: list[Any],
    counter: _CounterProtocol,
    tolerance: int,
) -> list[LineValidation]:
    """Score each candidate line against its target. Pure; no I/O."""
    if not (len(candidate_lines) == len(target_lines) == len(target_segments)):
        raise ValueError(
            f"length mismatch: candidates={len(candidate_lines)} targets={len(target_lines)} "
            f"segments={len(target_segments)}"
        )

    out: list[LineValidation] = []
    for i, (cand, tgt, seg) in enumerate(zip(candidate_lines, target_lines, target_segments)):
        target_counts = counter.count_per_line(tgt)
        candidate_counts = counter.count_per_line(cand)
        delta = counter.min_delta(candidate_counts, target_counts)
        passes = counter.any_method_within(candidate_counts, target_counts, tolerance)
        if passes:
            severity = Severity.OK
        elif delta <= tolerance + 2:
            severity = Severity.MINOR
        else:
            severity = Severity.MAJOR
        out.append(
            LineValidation(
                line_index=i,
                target_text=tgt,
                candidate_text=cand,
                target_syllables=target_counts,
                candidate_syllables=candidate_counts,
                min_delta=delta,
                passes=passes,
                severity=severity,
                time_budget_seconds=_segment_duration(seg),
            )
        )
    return out
```

- [ ] **Step 4: Run tests**

```bash
poetry run pytest backend/tests/services/custom_lyrics/test_validator.py -v
```

Expected: PASS for all 5 tests.

- [ ] **Step 5: Commit**

```bash
git add backend/services/custom_lyrics/validator.py backend/tests/services/custom_lyrics/test_validator.py
git commit -m "feat(custom-lyrics): pure validator over candidate vs. target lines"
```

---

## Phase 3 — Prompt builders and timing helpers

### Task 7: Implement prompt builders

**Files:**
- Create: `backend/services/custom_lyrics/prompts.py`
- Create: `backend/tests/services/custom_lyrics/test_prompts.py`

- [ ] **Step 1: Write failing tests**

`backend/tests/services/custom_lyrics/test_prompts.py`:

```python
"""Snapshot tests for prompt construction across settings."""
from __future__ import annotations

from backend.services.custom_lyrics.prompts import (
    build_initial_user_prompt,
    build_repair_user_prompt,
    build_system_prompt,
)
from backend.services.custom_lyrics.settings import GenerationSettings, StrictnessLevel
from backend.services.custom_lyrics.validator import LineValidation, Severity


def test_system_prompt_balanced_default() -> None:
    s = GenerationSettings()
    out = build_system_prompt(s)
    assert "professional karaoke lyricist" in out
    assert "Match each line's syllable count within 2" in out
    # default toggles: no extra restrictive rules added
    assert "Do NOT paraphrase" not in out
    assert "MUST appear in the output" not in out


def test_system_prompt_no_reword_no_omit() -> None:
    s = GenerationSettings(allow_reword=False, allow_omit=False)
    out = build_system_prompt(s)
    assert "Do NOT paraphrase" in out
    assert "MUST appear in the output" in out


def test_system_prompt_strictness_phrase() -> None:
    out = build_system_prompt(GenerationSettings(strictness=StrictnessLevel.STRICT))
    assert "Rhythm precision is the priority" in out


def test_system_prompt_fixed_line_count_rule() -> None:
    out = build_system_prompt(GenerationSettings(fixed_line_count=True))
    assert "exactly the same number of lines" in out


def test_system_prompt_flexible_line_count() -> None:
    out = build_system_prompt(GenerationSettings(fixed_line_count=False))
    assert "any number of lines" in out
    assert "exactly the same number" not in out


def test_initial_prompt_includes_per_line_metadata() -> None:
    target_lines = ["alpha beta", "gamma"]
    target_syllables = [[3, 3, 3, 3], [2, 2, 2, 2]]
    time_budgets = [1.5, 0.7]
    rates = [2.0, 2.86]
    out = build_initial_user_prompt(
        artist="Some Artist",
        title="Some Title",
        target_lines=target_lines,
        target_syllables=target_syllables,
        time_budgets=time_budgets,
        observed_rates=rates,
        custom_text_block="my custom lyric content",
        notes=None,
        settings=GenerationSettings(),
    )
    assert "Some Artist" in out
    assert "Some Title" in out
    assert "≈3 syl" in out
    assert "1.5s" in out
    assert "alpha beta" in out
    assert "my custom lyric content" in out


def test_repair_prompt_lists_violations_only() -> None:
    violations = [
        LineValidation(
            line_index=0,
            target_text="alpha",
            candidate_text="alpha beta gamma delta",
            target_syllables=[2, 2, 2, 2],
            candidate_syllables=[6, 6, 6, 6],
            min_delta=4,
            passes=False,
            severity=Severity.MAJOR,
            time_budget_seconds=0.8,
        )
    ]
    previous_output = ["alpha beta gamma delta", "fine line"]
    out = build_repair_user_prompt(
        previous_output=previous_output,
        violations=violations,
        target_lines=["alpha", "fine"],
        target_syllables=[[2, 2, 2, 2], [1, 1, 1, 1]],
        time_budgets=[0.8, 0.4],
        observed_rates=[2.5, 2.5],
        settings=GenerationSettings(),
    )
    assert "Line 1" in out
    assert "alpha" in out
    assert "alpha beta gamma delta" in out
    assert "+4 over" in out
    assert "Lines to keep unchanged: 2" in out


def test_repair_prompt_no_violations_returns_empty_signal() -> None:
    out = build_repair_user_prompt(
        previous_output=["fine"],
        violations=[],
        target_lines=["fine"],
        target_syllables=[[1, 1, 1, 1]],
        time_budgets=[0.4],
        observed_rates=[2.5],
        settings=GenerationSettings(),
    )
    # Caller should never invoke with empty violations; we still return a sensible string
    assert "no violations" in out.lower() or out == ""
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest backend/tests/services/custom_lyrics/test_prompts.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `prompts.py`**

`backend/services/custom_lyrics/prompts.py`:

```python
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
```

- [ ] **Step 4: Run tests**

```bash
poetry run pytest backend/tests/services/custom_lyrics/test_prompts.py -v
```

Expected: PASS for all 8 tests.

- [ ] **Step 5: Commit**

```bash
git add backend/services/custom_lyrics/prompts.py backend/tests/services/custom_lyrics/test_prompts.py
git commit -m "feat(custom-lyrics): system + initial + repair prompt builders"
```

---

### Task 8: Implement timing redistributor for variable line count

**Files:**
- Create: `backend/services/custom_lyrics/timing.py`
- Create: `backend/tests/services/custom_lyrics/test_timing.py`

- [ ] **Step 1: Write failing tests**

`backend/tests/services/custom_lyrics/test_timing.py`:

```python
"""Tests for proportional timing redistribution (variable line count)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.services.custom_lyrics.timing import redistribute_timing_proportional


@pytest.fixture
def stub_counter() -> MagicMock:
    c = MagicMock()
    c.count_per_line = MagicMock(
        side_effect=lambda line: [len(line.split())] * 4
    )
    return c


def test_redistribute_two_equal_lines(stub_counter: MagicMock) -> None:
    new_lines = ["one two", "three four"]
    result = redistribute_timing_proportional(
        new_lines=new_lines, total_window=(0.0, 4.0), counter=stub_counter,
    )
    assert len(result) == 2
    # Equal syllable counts → equal slices
    assert result[0] == pytest.approx((0.0, 2.0))
    assert result[1] == pytest.approx((2.0, 4.0))


def test_redistribute_proportional_to_syllables(stub_counter: MagicMock) -> None:
    new_lines = ["a", "b c d"]  # 1 vs 3 syllables
    result = redistribute_timing_proportional(
        new_lines=new_lines, total_window=(0.0, 4.0), counter=stub_counter,
    )
    # 1/(1+3) = 0.25 → first slice 1.0s, second slice 3.0s
    assert result[0] == pytest.approx((0.0, 1.0))
    assert result[1] == pytest.approx((1.0, 4.0))


def test_redistribute_zero_syllable_lines_falls_back_to_even(stub_counter: MagicMock) -> None:
    stub_counter.count_per_line = MagicMock(return_value=[0, 0, 0, 0])
    new_lines = ["", ""]
    result = redistribute_timing_proportional(
        new_lines=new_lines, total_window=(0.0, 2.0), counter=stub_counter,
    )
    assert result[0] == pytest.approx((0.0, 1.0))
    assert result[1] == pytest.approx((1.0, 2.0))


def test_redistribute_single_line_spans_window(stub_counter: MagicMock) -> None:
    result = redistribute_timing_proportional(
        new_lines=["only"], total_window=(2.0, 5.0), counter=stub_counter,
    )
    assert result == [(2.0, 5.0)]


def test_redistribute_empty_lines_raises(stub_counter: MagicMock) -> None:
    with pytest.raises(ValueError, match="empty"):
        redistribute_timing_proportional(
            new_lines=[], total_window=(0.0, 1.0), counter=stub_counter,
        )


def test_redistribute_invalid_window_raises(stub_counter: MagicMock) -> None:
    with pytest.raises(ValueError, match="window"):
        redistribute_timing_proportional(
            new_lines=["a"], total_window=(5.0, 2.0), counter=stub_counter,
        )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest backend/tests/services/custom_lyrics/test_timing.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `timing.py`**

`backend/services/custom_lyrics/timing.py`:

```python
"""Proportional timing redistribution for variable line count."""
from __future__ import annotations

from typing import Protocol


class _CounterProtocol(Protocol):
    def count_per_line(self, line: str) -> list[int]: ...


def _median_count(counts: list[int]) -> int:
    if not counts:
        return 0
    sorted_c = sorted(counts)
    return sorted_c[len(sorted_c) // 2]


def redistribute_timing_proportional(
    *,
    new_lines: list[str],
    total_window: tuple[float, float],
    counter: _CounterProtocol,
) -> list[tuple[float, float]]:
    """Distribute `total_window` across `new_lines` proportional to per-line syllable count.

    Falls back to even distribution if all lines have 0 syllables.
    """
    if not new_lines:
        raise ValueError("new_lines must not be empty")
    start, end = total_window
    if end <= start:
        raise ValueError(f"window invalid: ({start}, {end})")

    syllables = [_median_count(counter.count_per_line(line)) for line in new_lines]
    total_syl = sum(syllables)

    if total_syl == 0:
        # Even split fallback
        per_line = (end - start) / len(new_lines)
        return [
            (start + i * per_line, start + (i + 1) * per_line)
            for i in range(len(new_lines))
        ]

    out: list[tuple[float, float]] = []
    cursor = start
    duration = end - start
    for i, syl in enumerate(syllables):
        if i == len(syllables) - 1:
            out.append((cursor, end))  # last slice goes to exact end (avoid float drift)
        else:
            slice_dur = duration * syl / total_syl
            out.append((cursor, cursor + slice_dur))
            cursor += slice_dur
    return out
```

- [ ] **Step 4: Run tests**

```bash
poetry run pytest backend/tests/services/custom_lyrics/test_timing.py -v
```

Expected: PASS for all 6 tests.

- [ ] **Step 5: Commit**

```bash
git add backend/services/custom_lyrics/timing.py backend/tests/services/custom_lyrics/test_timing.py
git commit -m "feat(custom-lyrics): proportional timing redistribution for variable line count"
```

---

## Phase 4 — Service orchestrator

### Task 9: Define result dataclasses

**Files:**
- Create: `backend/services/custom_lyrics/result.py`

- [ ] **Step 1: Create `result.py`**

`backend/services/custom_lyrics/result.py`:

```python
"""Result types for the custom-lyrics service."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from backend.services.custom_lyrics.settings import GenerationSettings
from backend.services.custom_lyrics.validator import LineValidation


class StopReason(str, Enum):
    SUCCESS = "success"
    PLATEAU = "plateau"
    MAX_ITERS_REACHED = "max_iters_reached"
    LINE_COUNT_MISMATCH = "line_count_mismatch"
    VERBATIM_SKIP = "verbatim_skip"


@dataclass
class CustomLyricsResult:
    lines: list[str]
    line_metadata: list[LineValidation]
    iterations_used: int
    stop_reason: StopReason
    settings_applied: GenerationSettings
    model: str
    duration_ms: int
    new_segment_timing: Optional[list[tuple[float, float]]] = None
    line_count_mismatch: bool = False
    warnings: list[str] = field(default_factory=list)
```

- [ ] **Step 2: Commit**

```bash
git add backend/services/custom_lyrics/result.py
git commit -m "feat(custom-lyrics): result dataclasses + StopReason enum"
```

---

### Task 10: Implement service orchestrator (validate-and-repair loop)

**Files:**
- Create: `backend/services/custom_lyrics/service.py`
- Modify: `backend/services/custom_lyrics_service.py` (becomes a re-export shim)

The new `service.py` is the spiritual replacement for the old monolithic `custom_lyrics_service.py`. We'll keep the old path importable as a re-export shim so the API route's existing import doesn't break mid-refactor.

- [ ] **Step 1: Skim existing `backend/tests/test_custom_lyrics_service.py` to understand conventions**

```bash
poetry run pytest backend/tests/test_custom_lyrics_service.py -v --collect-only 2>&1 | head -50
```

Note the existing test class structure and mocking patterns. We will *extend* it later (Task 13), not replace.

- [ ] **Step 2: Write failing service tests**

Create `backend/tests/services/custom_lyrics/test_service.py`:

```python
"""Tests for the validate-and-repair loop orchestrator."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.services.custom_lyrics.result import StopReason
from backend.services.custom_lyrics.service import CustomLyricsService
from backend.services.custom_lyrics.settings import GenerationSettings, StrictnessLevel


def _segment(start: float, end: float, text: str = "x"):
    """Minimal LyricsSegment-shaped dict for tests."""
    return {
        "id": f"seg-{start}",
        "text": text,
        "start_time": start,
        "end_time": end,
        "words": [],
    }


@pytest.fixture
def stub_counter() -> MagicMock:
    c = MagicMock()
    c.count_per_line = MagicMock(
        side_effect=lambda line: [len([w for w in line.split() if w])] * 4
    )
    c.any_method_within = MagicMock(
        side_effect=lambda cand, tgt, tol: min(abs(a - b) for a in cand for b in tgt) <= tol
    )
    c.min_delta = MagicMock(
        side_effect=lambda cand, tgt: min(abs(a - b) for a in cand for b in tgt)
    )
    return c


@pytest.fixture
def service(stub_counter: MagicMock) -> CustomLyricsService:
    s = CustomLyricsService(counter=stub_counter)
    return s


def _patch_gemini(returns: list[list[str]]):
    """Helper: patch service._call_gemini to return the next list each call."""
    iterator = iter(returns)
    return patch.object(
        CustomLyricsService,
        "_call_gemini",
        side_effect=lambda *args, **kwargs: next(iterator),
    )


def test_success_first_iteration(service: CustomLyricsService) -> None:
    target_segments = [_segment(0.0, 1.0, "aa"), _segment(1.0, 2.0, "bb")]
    target_lines = ["aa", "bb"]
    with _patch_gemini([["xx", "yy"]]):
        result = service.generate(
            job_id="j1",
            target_lines=target_lines,
            target_segments=target_segments,
            artist=None,
            title=None,
            custom_text="custom",
            file_bytes=None,
            file_mime=None,
            file_name=None,
            notes=None,
            settings=GenerationSettings(),
        )
    assert result.lines == ["xx", "yy"]
    assert result.iterations_used == 0
    assert result.stop_reason is StopReason.SUCCESS
    assert all(v.passes for v in result.line_metadata)


def test_repairs_one_line(service: CustomLyricsService) -> None:
    target_segments = [_segment(0.0, 1.0, "aa"), _segment(1.0, 2.0, "bb")]
    target_lines = ["aa", "bb"]
    # iteration 0: line 1 has too many words; iteration 1: fixed
    with _patch_gemini([
        ["xx yy zz qq", "yy"],  # 4 vs 1 → fail at tol=2
        ["xx", "yy"],
    ]):
        result = service.generate(
            job_id="j1",
            target_lines=target_lines,
            target_segments=target_segments,
            artist=None, title=None, custom_text="c",
            file_bytes=None, file_mime=None, file_name=None,
            notes=None,
            settings=GenerationSettings(strictness=StrictnessLevel.BALANCED),
        )
    assert result.lines == ["xx", "yy"]
    assert result.iterations_used == 1
    assert result.stop_reason is StopReason.SUCCESS


def test_plateau_detection(service: CustomLyricsService) -> None:
    target_segments = [_segment(0.0, 1.0, "aa")]
    target_lines = ["aa"]
    # Same bad output every iteration → plateau
    with _patch_gemini([
        ["xx yy zz qq pp"],
        ["xx yy zz qq pp"],
        ["xx yy zz qq pp"],
    ]):
        result = service.generate(
            job_id="j1",
            target_lines=target_lines,
            target_segments=target_segments,
            artist=None, title=None, custom_text="c",
            file_bytes=None, file_mime=None, file_name=None,
            notes=None,
            settings=GenerationSettings(strictness=StrictnessLevel.BALANCED),
        )
    assert result.stop_reason is StopReason.PLATEAU
    assert result.iterations_used == 1  # one repair attempt before plateau


def test_max_iters_reached(service: CustomLyricsService) -> None:
    target_segments = [_segment(0.0, 1.0, "aa")]
    target_lines = ["aa"]
    # Improving by 1 each iteration but never passing
    with _patch_gemini([
        ["a b c d e f g h"],   # 8 → delta 7
        ["a b c d e f g"],     # 7 → delta 6
        ["a b c d e f"],       # 6 → delta 5
        ["a b c d e"],         # 5 → delta 4
        ["a b c d"],           # 4 → delta 3
    ]):
        result = service.generate(
            job_id="j1",
            target_lines=target_lines,
            target_segments=target_segments,
            artist=None, title=None, custom_text="c",
            file_bytes=None, file_mime=None, file_name=None,
            notes=None,
            settings=GenerationSettings(strictness=StrictnessLevel.STRICT),  # max_iter=4
        )
    assert result.stop_reason is StopReason.MAX_ITERS_REACHED
    assert result.iterations_used == 4


def test_verbatim_skips_loop(service: CustomLyricsService) -> None:
    target_segments = [_segment(0.0, 1.0, "aa"), _segment(1.0, 2.0, "bb")]
    with _patch_gemini([["wildly wrong syllables here", "second"]]):
        result = service.generate(
            job_id="j1",
            target_lines=["aa", "bb"],
            target_segments=target_segments,
            artist=None, title=None, custom_text="c",
            file_bytes=None, file_mime=None, file_name=None,
            notes=None,
            settings=GenerationSettings(strictness=StrictnessLevel.VERBATIM),
        )
    assert result.iterations_used == 0
    assert result.stop_reason is StopReason.VERBATIM_SKIP
    # metadata still populated
    assert len(result.line_metadata) == 2


def test_best_iteration_tracking(service: CustomLyricsService) -> None:
    """If iteration 2 regresses, return iteration 1's result."""
    target_segments = [_segment(0.0, 1.0, "aa"), _segment(1.0, 2.0, "bb")]
    with _patch_gemini([
        ["a b c d", "yy"],     # iter 0: 1 violation, delta=3
        ["a", "yy"],           # iter 1: 0 violations
        ["a b c d e f", "yy"], # iter 2: 1 violation worse than before
    ]):
        result = service.generate(
            job_id="j1",
            target_lines=["aa", "bb"],
            target_segments=target_segments,
            artist=None, title=None, custom_text="c",
            file_bytes=None, file_mime=None, file_name=None,
            notes=None,
            settings=GenerationSettings(strictness=StrictnessLevel.BALANCED),
        )
    assert result.lines == ["a", "yy"]


def test_variable_line_count_returns_new_timing(service: CustomLyricsService) -> None:
    target_segments = [_segment(0.0, 1.0, "aa"), _segment(1.0, 2.0, "bb"), _segment(2.0, 3.0, "cc")]
    with _patch_gemini([["alpha beta gamma", "delta"]]):
        result = service.generate(
            job_id="j1",
            target_lines=["aa", "bb", "cc"],
            target_segments=target_segments,
            artist=None, title=None, custom_text="c",
            file_bytes=None, file_mime=None, file_name=None,
            notes=None,
            settings=GenerationSettings(fixed_line_count=False),
        )
    assert len(result.lines) == 2
    assert result.new_segment_timing is not None
    assert len(result.new_segment_timing) == 2
    assert result.line_count_mismatch is True


def test_existing_lines_empty_raises(service: CustomLyricsService) -> None:
    from backend.services.custom_lyrics.service import CustomLyricsServiceError
    with pytest.raises(CustomLyricsServiceError, match="must not be empty"):
        service.generate(
            job_id="j1",
            target_lines=[],
            target_segments=[],
            artist=None, title=None, custom_text="c",
            file_bytes=None, file_mime=None, file_name=None,
            notes=None,
            settings=GenerationSettings(),
        )
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
poetry run pytest backend/tests/services/custom_lyrics/test_service.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Implement `service.py`**

`backend/services/custom_lyrics/service.py`:

```python
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

        candidate_lines = self._call_gemini(
            system_prompt=sys_prompt,
            user_prompt=initial_user_prompt,
            pdf_bytes=pdf_bytes,
            settings=settings,
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
        prev_violation_count = sum(1 for v in validations if not v.passes)
        iteration = 0
        stop_reason = StopReason.SUCCESS if prev_violation_count == 0 else StopReason.MAX_ITERS_REACHED

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
            candidate_lines = self._call_gemini(
                system_prompt=sys_prompt,
                user_prompt=repair_prompt,
                pdf_bytes=pdf_bytes,
                settings=settings,
            )
            validations = self._validate_with_length_handling(
                candidate_lines, target_lines, target_segments,
                tolerance=params.tolerance, fixed=settings.fixed_line_count,
            )

            if self._score(validations) < self._score(best[1]):
                best = (candidate_lines, validations)

            new_violation_count = sum(1 for v in validations if not v.passes)
            if new_violation_count >= prev_violation_count:
                stop_reason = StopReason.PLATEAU
                iteration += 1
                break
            prev_violation_count = new_violation_count
            iteration += 1
            if new_violation_count == 0:
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
        start = seg["start_time"] if isinstance(seg, dict) else seg.start_time
        end = seg["end_time"] if isinstance(seg, dict) else seg.end_time
        if start is None or end is None:
            return 0.0
        return max(0.0, float(end) - float(start))

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
            # Length mismatch: align by truncation/padding so the validator runs
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
            # Append synthetic MAJOR violations for the missing/extra positions
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
```

- [ ] **Step 5: Make `backend/services/custom_lyrics_service.py` a re-export shim**

Replace the contents of `backend/services/custom_lyrics_service.py` with:

```python
"""Backward-compat shim. Real implementation lives in backend.services.custom_lyrics."""
from backend.services.custom_lyrics.service import (
    CustomLyricsService,
    CustomLyricsServiceError,
    get_custom_lyrics_service,
)
from backend.services.custom_lyrics.result import CustomLyricsResult, StopReason
from backend.services.custom_lyrics.settings import GenerationSettings, StrictnessLevel

__all__ = [
    "CustomLyricsService",
    "CustomLyricsServiceError",
    "CustomLyricsResult",
    "GenerationSettings",
    "StopReason",
    "StrictnessLevel",
    "get_custom_lyrics_service",
]
```

- [ ] **Step 6: Run new service tests**

```bash
poetry run pytest backend/tests/services/custom_lyrics/test_service.py -v
```

Expected: PASS for all 8 tests.

- [ ] **Step 7: Verify existing service tests still pass**

```bash
poetry run pytest backend/tests/test_custom_lyrics_service.py -v
```

Expected: most existing tests pass; some may fail because the old service had a different signature (`existing_lines` vs `target_lines`). That's expected — we'll fix the existing test file in Task 13.

- [ ] **Step 8: Commit**

```bash
git add backend/services/custom_lyrics/result.py backend/services/custom_lyrics/service.py backend/services/custom_lyrics_service.py backend/tests/services/custom_lyrics/test_service.py
git commit -m "feat(custom-lyrics): validate-and-repair loop service orchestrator"
```

---

### Task 11: Add config settings

**Files:**
- Modify: `backend/config.py`

- [ ] **Step 1: Find the existing custom-lyrics config block**

```bash
grep -n "custom_lyrics" backend/config.py
```

Note the line numbers; new fields go alongside the existing `custom_lyrics_*` fields.

- [ ] **Step 2: Add three new fields**

In `backend/config.py`, in the `Settings` class near other `custom_lyrics_*` fields, add:

```python
    custom_lyrics_max_iterations: int = 4
    custom_lyrics_default_strictness: str = "balanced"
    custom_lyrics_max_output_lines_multiplier: float = 2.0
```

- [ ] **Step 3: Verify imports / usage**

```bash
poetry run python -c "from backend.config import get_settings; s = get_settings(); print(s.custom_lyrics_max_iterations, s.custom_lyrics_default_strictness, s.custom_lyrics_max_output_lines_multiplier)"
```

Expected: `4 balanced 2.0`.

- [ ] **Step 4: Commit**

```bash
git add backend/config.py
git commit -m "feat(custom-lyrics): config knobs for max_iters / default_strictness / max_output_multiplier"
```

---

## Phase 5 — API surface

### Task 12: Update `generate_custom_lyrics` endpoint

**Files:**
- Modify: `backend/api/routes/review.py`

- [ ] **Step 1: Locate current endpoint**

```bash
grep -n "generate_custom_lyrics\|custom-lyrics/generate" backend/api/routes/review.py
```

Note the function and the Pydantic response model name (likely `CustomLyricsResponse`).

- [ ] **Step 2: Add new Pydantic models for the response**

At the top of the relevant section in `backend/api/routes/review.py`, add (or modify the existing `CustomLyricsResponse`):

```python
class LineMetadataResponse(BaseModel):
    line_index: int
    target_text: str
    candidate_text: str
    target_syllables: list[int]
    candidate_syllables: list[int]
    min_delta: int
    passes: bool
    severity: Literal["ok", "minor", "major"]
    time_budget_seconds: float


class CustomLyricsResponse(BaseModel):
    lines: list[str]
    line_metadata: list[LineMetadataResponse]
    iterations_used: int
    stop_reason: Literal["success", "plateau", "max_iters_reached", "line_count_mismatch", "verbatim_skip"]
    settings_applied: dict
    new_segment_timing: list[tuple[float, float]] | None = None
    line_count_mismatch: bool
    warnings: list[str]
    model: str
```

Ensure `Literal` is imported from `typing`.

- [ ] **Step 3: Update the endpoint to accept `settings_json`**

Replace the body of `generate_custom_lyrics`:

```python
@router.post("/{job_id}/custom-lyrics/generate")
async def generate_custom_lyrics(
    job_id: str,
    existing_lines: str = Form(...),
    custom_text: str | None = Form(None),
    notes: str | None = Form(None),
    file: UploadFile | None = File(None),
    artist: str | None = Form(None),
    title: str | None = Form(None),
    settings_json: str | None = Form(None),
    _auth: None = Depends(require_review_auth),
    service: CustomLyricsService = Depends(get_custom_lyrics_service),
) -> CustomLyricsResponse:
    from backend.services.custom_lyrics.settings import settings_from_dict, GenerationSettings

    # Parse existing_lines
    try:
        target_lines = json.loads(existing_lines)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="existing_lines must be JSON array")
    if not isinstance(target_lines, list) or not all(isinstance(x, str) for x in target_lines):
        raise HTTPException(status_code=400, detail="existing_lines must be a list of strings")

    # Parse settings
    if settings_json:
        try:
            settings_dict = json.loads(settings_json)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="settings_json must be valid JSON")
        try:
            settings = settings_from_dict(settings_dict)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"invalid settings: {e}")
    else:
        settings = GenerationSettings()

    # Load segments from job (we need timing data for the validator/timing helpers)
    segments = await _load_target_segments(job_id, target_lines)

    file_bytes: bytes | None = None
    file_mime: str | None = None
    file_name: str | None = None
    if file is not None:
        file_bytes = await file.read()
        file_mime = file.content_type
        file_name = file.filename

    try:
        result = service.generate(
            job_id=job_id,
            target_lines=target_lines,
            target_segments=segments,
            artist=artist,
            title=title,
            custom_text=custom_text,
            file_bytes=file_bytes,
            file_mime=file_mime,
            file_name=file_name,
            notes=notes,
            settings=settings,
        )
    except CustomLyricsServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))

    return CustomLyricsResponse(
        lines=result.lines,
        line_metadata=[
            LineMetadataResponse(
                line_index=v.line_index,
                target_text=v.target_text,
                candidate_text=v.candidate_text,
                target_syllables=v.target_syllables,
                candidate_syllables=v.candidate_syllables,
                min_delta=v.min_delta,
                passes=v.passes,
                severity=v.severity.value,
                time_budget_seconds=v.time_budget_seconds,
            )
            for v in result.line_metadata
        ],
        iterations_used=result.iterations_used,
        stop_reason=result.stop_reason.value,
        settings_applied=result.settings_applied.to_dict(),
        new_segment_timing=result.new_segment_timing,
        line_count_mismatch=result.line_count_mismatch,
        warnings=result.warnings,
        model=result.model,
    )
```

- [ ] **Step 4: Implement `_load_target_segments` helper**

If it doesn't exist, add a private helper near the route:

```python
async def _load_target_segments(job_id: str, target_lines: list[str]) -> list[dict]:
    """Load segments from the job's corrected.json. Falls back to synthesized timing if unavailable.

    Returns a list of LyricsSegment-shaped dicts (start_time, end_time at minimum).
    """
    from backend.services.firestore_service import get_firestore_service
    from backend.services.gcs_service import get_gcs_service

    fs = get_firestore_service()
    gcs = get_gcs_service()

    # Try to load corrected.json from GCS for accurate timing
    try:
        job = await fs.get_job(job_id)
        if job and job.corrected_json_gcs_path:
            data = await gcs.download_json(job.corrected_json_gcs_path)
            segments = data.get("corrected_segments") or data.get("segments") or []
            if len(segments) == len(target_lines):
                return segments
    except Exception:  # noqa: BLE001 - fall through to synthesized timing
        pass

    # Synthesized fallback: 2 seconds per line
    return [
        {"start_time": float(i * 2.0), "end_time": float((i + 1) * 2.0), "text": line, "words": []}
        for i, line in enumerate(target_lines)
    ]
```

(Adjust the firestore/GCS service call signatures to match the project's actual conventions; the broad-except fallback is intentional so that callers without timing don't 500.)

- [ ] **Step 5: Smoke-test imports**

```bash
poetry run python -c "from backend.api.routes import review; print(review.generate_custom_lyrics.__name__)"
```

Expected: `generate_custom_lyrics`. No import errors.

- [ ] **Step 6: Commit**

```bash
git add backend/api/routes/review.py
git commit -m "feat(custom-lyrics): API accepts settings_json + returns rich line_metadata"
```

---

### Task 13: Extend existing service tests for new signatures

**Files:**
- Modify: `backend/tests/test_custom_lyrics_service.py`

- [ ] **Step 1: Audit existing tests**

```bash
poetry run pytest backend/tests/test_custom_lyrics_service.py -v 2>&1 | tail -40
```

Note which tests now fail. Likely all of them, because:
- The service method signature changed (`existing_lines` → `target_lines`, added `target_segments`, added `settings`).
- The result type changed.

- [ ] **Step 2: Update each failing test to pass new args**

For each failing test, update the call site to use the new signature. Example pattern:

```python
# Before:
result = service.generate(
    job_id="j1",
    existing_lines=["aa", "bb"],
    artist=None, title=None, custom_text="x",
    file_bytes=None, file_mime=None, file_name=None,
    notes=None,
)

# After:
from backend.services.custom_lyrics.settings import GenerationSettings
result = service.generate(
    job_id="j1",
    target_lines=["aa", "bb"],
    target_segments=[
        {"start_time": 0.0, "end_time": 1.0, "text": "aa", "words": []},
        {"start_time": 1.0, "end_time": 2.0, "text": "bb", "words": []},
    ],
    artist=None, title=None, custom_text="x",
    file_bytes=None, file_mime=None, file_name=None,
    notes=None,
    settings=GenerationSettings(),
)
```

For tests that asserted on the old result shape (e.g. `result.line_count_mismatch`, `result.lines`), keep them — those fields are preserved on the new result.

For tests that asserted on the old retry behavior (silent retry on line-count mismatch), either:
- Migrate the assertion to use the new `iterations_used` / `stop_reason` fields, OR
- Delete the test if the new repair loop subsumes it (the new test file `test_service.py` covers this territory).

- [ ] **Step 3: Run all backend custom-lyrics tests**

```bash
poetry run pytest backend/tests/test_custom_lyrics_service.py backend/tests/services/custom_lyrics/ -v
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_custom_lyrics_service.py
git commit -m "test(custom-lyrics): update existing service tests to new signature"
```

---

### Task 14: Extend existing route tests

**Files:**
- Modify: `backend/tests/api/test_review_custom_lyrics.py`

- [ ] **Step 1: Audit existing route tests**

```bash
poetry run pytest backend/tests/api/test_review_custom_lyrics.py -v 2>&1 | tail -30
```

- [ ] **Step 2: Update tests + add new ones**

For each failing test, update the multipart payload assertions. Add three new tests:

```python
def test_endpoint_accepts_settings_json(client, auth_headers, mock_service):
    """Posting valid settings_json plumbs settings through to the service."""
    response = client.post(
        f"/api/review/job-1/custom-lyrics/generate",
        headers=auth_headers,
        data={
            "existing_lines": json.dumps(["aa", "bb"]),
            "custom_text": "x",
            "settings_json": json.dumps({
                "allow_reword": False,
                "strictness": "tight",
            }),
        },
    )
    assert response.status_code == 200
    # Assert mock_service.generate was called with allow_reword=False, strictness=TIGHT
    call_kwargs = mock_service.generate.call_args.kwargs
    settings = call_kwargs["settings"]
    assert settings.allow_reword is False
    assert settings.strictness.value == "tight"


def test_endpoint_rejects_invalid_settings_json(client, auth_headers):
    response = client.post(
        f"/api/review/job-1/custom-lyrics/generate",
        headers=auth_headers,
        data={
            "existing_lines": json.dumps(["aa"]),
            "custom_text": "x",
            "settings_json": "not json",
        },
    )
    assert response.status_code == 400
    assert "settings_json" in response.json()["detail"]


def test_endpoint_rejects_invalid_strictness(client, auth_headers):
    response = client.post(
        f"/api/review/job-1/custom-lyrics/generate",
        headers=auth_headers,
        data={
            "existing_lines": json.dumps(["aa"]),
            "custom_text": "x",
            "settings_json": json.dumps({"strictness": "extreme"}),
        },
    )
    assert response.status_code == 400


def test_endpoint_returns_line_metadata(client, auth_headers, mock_service):
    """Response includes per-line metadata."""
    # mock_service.generate already returns a result with line_metadata populated
    response = client.post(
        f"/api/review/job-1/custom-lyrics/generate",
        headers=auth_headers,
        data={"existing_lines": json.dumps(["aa", "bb"]), "custom_text": "x"},
    )
    body = response.json()
    assert "line_metadata" in body
    assert len(body["line_metadata"]) == 2
    for entry in body["line_metadata"]:
        assert "min_delta" in entry
        assert "severity" in entry
```

(Update `mock_service` fixture so its `generate` returns a `CustomLyricsResult` with realistic `line_metadata`.)

- [ ] **Step 3: Run tests**

```bash
poetry run pytest backend/tests/api/test_review_custom_lyrics.py -v
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/api/test_review_custom_lyrics.py
git commit -m "test(custom-lyrics): route tests for settings_json + line_metadata response"
```

---

## Phase 6 — Frontend

### Task 15: Update API client types

**Files:**
- Modify: `frontend/lib/api/customLyrics.ts`

- [ ] **Step 1: Read existing client wrapper**

```bash
cat frontend/lib/api/customLyrics.ts
```

- [ ] **Step 2: Add new types and update request shape**

At the top of `frontend/lib/api/customLyrics.ts` (after existing imports), add:

```typescript
export type StrictnessLevel = 'verbatim' | 'loose' | 'balanced' | 'tight' | 'strict'
export type StopReason =
  | 'success'
  | 'plateau'
  | 'max_iters_reached'
  | 'line_count_mismatch'
  | 'verbatim_skip'
export type LineSeverity = 'ok' | 'minor' | 'major'

export interface GenerationSettings {
  allow_reword: boolean
  allow_omit: boolean
  fixed_line_count: boolean
  strictness: StrictnessLevel
}

export const DEFAULT_GENERATION_SETTINGS: GenerationSettings = {
  allow_reword: true,
  allow_omit: true,
  fixed_line_count: true,
  strictness: 'balanced',
}

export interface LineMetadata {
  line_index: number
  target_text: string
  candidate_text: string
  target_syllables: number[]
  candidate_syllables: number[]
  min_delta: number
  passes: boolean
  severity: LineSeverity
  time_budget_seconds: number
}

export interface CustomLyricsResponse {
  lines: string[]
  line_metadata: LineMetadata[]
  iterations_used: number
  stop_reason: StopReason
  settings_applied: GenerationSettings
  new_segment_timing: Array<[number, number]> | null
  line_count_mismatch: boolean
  warnings: string[]
  model: string
}
```

- [ ] **Step 3: Update `generateCustomLyrics` to send settings**

Find the existing function signature and add `settings: GenerationSettings` to its params type. In the FormData construction, add:

```typescript
formData.append('settings_json', JSON.stringify(params.settings))
```

Update the response parsing to use the new shape.

- [ ] **Step 4: Run frontend type check**

```bash
cd frontend && npx tsc --noEmit 2>&1 | tail -20
```

Expected: type errors **only** in `CustomLyricsMode.tsx` (we'll fix in Task 18). No errors elsewhere.

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/api/customLyrics.ts
git commit -m "feat(custom-lyrics): frontend API types for settings + line_metadata"
```

---

### Task 16: Build the `CustomLyricsSettings` panel component

**Files:**
- Create: `frontend/components/lyrics-review/modals/CustomLyricsSettings.tsx`
- Create: `frontend/components/lyrics-review/modals/__tests__/CustomLyricsSettings.test.tsx`

- [ ] **Step 1: Add new i18n keys to `frontend/messages/en.json`**

Find `lyricsReview.modals.customLyricsMode` block; add keys:

```json
{
  "lyricsReview": {
    "modals": {
      "customLyricsMode": {
        "settings": {
          "title": "Generation settings",
          "allowReword": "Allow rewording client lyrics",
          "allowRewordHint": "Let the AI paraphrase or shorten client text to fit the song.",
          "allowOmit": "Allow omitting client lyrics",
          "allowOmitHint": "Let the AI drop client material that doesn't fit the song's structure.",
          "fixedLineCount": "Maintain original segment count",
          "fixedLineCountHint": "Keep the same number of lines as the original song.",
          "strictness": "Singability strictness",
          "strictnessVerbatim": "Verbatim",
          "strictnessLoose": "Loose",
          "strictnessBalanced": "Balanced",
          "strictnessTight": "Tight",
          "strictnessStrict": "Strict",
          "strictnessHintVerbatim": "Use the client's text as-is. Rhythm matching is not a goal.",
          "strictnessHintLoose": "Roughly match syllable count where convenient.",
          "strictnessHintBalanced": "Match each line's syllable count within ±2.",
          "strictnessHintTight": "Closely match syllable count, ±1.",
          "strictnessHintStrict": "Exact syllable match. Rhythm precision is the priority.",
          "contradictionWarning": "Rewording is disabled; the AI may be unable to match syllable counts. Enable rewording or use Verbatim strictness."
        }
      }
    }
  }
}
```

(Merge into the existing structure; don't replace.)

- [ ] **Step 2: Write failing component test**

`frontend/components/lyrics-review/modals/__tests__/CustomLyricsSettings.test.tsx`:

```tsx
import { render, screen, fireEvent } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import messages from '@/messages/en.json'
import CustomLyricsSettings from '../CustomLyricsSettings'
import { DEFAULT_GENERATION_SETTINGS, GenerationSettings } from '@/lib/api/customLyrics'

function renderWith(settings: GenerationSettings, onChange = jest.fn()) {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <CustomLyricsSettings settings={settings} onChange={onChange} />
    </NextIntlClientProvider>,
  )
}

describe('CustomLyricsSettings', () => {
  it('renders all 3 toggles and slider', () => {
    renderWith(DEFAULT_GENERATION_SETTINGS)
    expect(screen.getByText(/Allow rewording client lyrics/i)).toBeInTheDocument()
    expect(screen.getByText(/Allow omitting client lyrics/i)).toBeInTheDocument()
    expect(screen.getByText(/Maintain original segment count/i)).toBeInTheDocument()
    expect(screen.getByText(/Singability strictness/i)).toBeInTheDocument()
  })

  it('toggling a switch fires onChange with updated settings', () => {
    const onChange = jest.fn()
    renderWith(DEFAULT_GENERATION_SETTINGS, onChange)
    const rewordSwitch = screen.getByRole('switch', { name: /Allow rewording/i })
    fireEvent.click(rewordSwitch)
    expect(onChange).toHaveBeenCalledWith({
      ...DEFAULT_GENERATION_SETTINGS,
      allow_reword: false,
    })
  })

  it('shows contradiction warning when reword=OFF and strictness=Strict', () => {
    renderWith({ ...DEFAULT_GENERATION_SETTINGS, allow_reword: false, strictness: 'strict' })
    expect(
      screen.getByText(/AI may be unable to match syllable counts/i),
    ).toBeInTheDocument()
  })

  it('does not show warning at safe combinations', () => {
    renderWith(DEFAULT_GENERATION_SETTINGS)
    expect(
      screen.queryByText(/AI may be unable to match syllable counts/i),
    ).not.toBeInTheDocument()
  })

  it('clicking a strictness label updates the slider', () => {
    const onChange = jest.fn()
    renderWith(DEFAULT_GENERATION_SETTINGS, onChange)
    fireEvent.click(screen.getByText('Tight'))
    expect(onChange).toHaveBeenCalledWith({
      ...DEFAULT_GENERATION_SETTINGS,
      strictness: 'tight',
    })
  })
})
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd frontend && npx jest CustomLyricsSettings.test --watchAll=false
```

Expected: FAIL with "Cannot find module '../CustomLyricsSettings'".

- [ ] **Step 4: Implement the component**

`frontend/components/lyrics-review/modals/CustomLyricsSettings.tsx`:

```tsx
'use client'

import { useTranslations } from 'next-intl'
import { useMemo } from 'react'
import { AlertTriangle, ChevronDown } from 'lucide-react'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import {
  GenerationSettings,
  StrictnessLevel,
} from '@/lib/api/customLyrics'

const STRICTNESS_ORDER: StrictnessLevel[] = [
  'verbatim',
  'loose',
  'balanced',
  'tight',
  'strict',
]

interface Props {
  settings: GenerationSettings
  onChange: (next: GenerationSettings) => void
  disabled?: boolean
}

export default function CustomLyricsSettings({ settings, onChange, disabled = false }: Props) {
  const t = useTranslations('lyricsReview.modals.customLyricsMode.settings')

  const showContradiction = useMemo(
    () => !settings.allow_reword && (settings.strictness === 'strict' || settings.strictness === 'tight'),
    [settings.allow_reword, settings.strictness],
  )

  const set = <K extends keyof GenerationSettings>(key: K, value: GenerationSettings[K]) => {
    onChange({ ...settings, [key]: value })
  }

  const strictnessHint = (level: StrictnessLevel) =>
    t(`strictnessHint${level.charAt(0).toUpperCase() + level.slice(1)}` as const)

  const strictnessLabel = (level: StrictnessLevel) =>
    t(`strictness${level.charAt(0).toUpperCase() + level.slice(1)}` as const)

  return (
    <Collapsible className="border rounded-md">
      <CollapsibleTrigger className="flex w-full items-center justify-between p-3 text-sm font-medium">
        {t('title')}
        <ChevronDown className="h-4 w-4 transition-transform [&[data-state=open]]:rotate-180" />
      </CollapsibleTrigger>
      <CollapsibleContent className="px-3 pb-3 space-y-3">
        <ToggleRow
          name="allow_reword"
          label={t('allowReword')}
          hint={t('allowRewordHint')}
          checked={settings.allow_reword}
          onChange={(v) => set('allow_reword', v)}
          disabled={disabled}
        />
        <ToggleRow
          name="allow_omit"
          label={t('allowOmit')}
          hint={t('allowOmitHint')}
          checked={settings.allow_omit}
          onChange={(v) => set('allow_omit', v)}
          disabled={disabled}
        />
        <ToggleRow
          name="fixed_line_count"
          label={t('fixedLineCount')}
          hint={t('fixedLineCountHint')}
          checked={settings.fixed_line_count}
          onChange={(v) => set('fixed_line_count', v)}
          disabled={disabled}
        />

        <div className="pt-2">
          <Label className="text-sm font-medium">{t('strictness')}</Label>
          <div className="mt-2 grid grid-cols-5 gap-1">
            {STRICTNESS_ORDER.map((level) => (
              <button
                key={level}
                type="button"
                disabled={disabled}
                onClick={() => set('strictness', level)}
                className={
                  'rounded-md text-xs py-1.5 transition-colors ' +
                  (settings.strictness === level
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-muted hover:bg-muted/70')
                }
              >
                {strictnessLabel(level)}
              </button>
            ))}
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            {strictnessHint(settings.strictness)}
          </p>
        </div>

        {showContradiction && (
          <div className="flex items-start gap-2 p-2 rounded-md bg-yellow-500/10 text-xs text-yellow-700 dark:text-yellow-300">
            <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
            <span>{t('contradictionWarning')}</span>
          </div>
        )}
      </CollapsibleContent>
    </Collapsible>
  )
}

interface ToggleRowProps {
  name: string
  label: string
  hint: string
  checked: boolean
  onChange: (value: boolean) => void
  disabled?: boolean
}

function ToggleRow({ name, label, hint, checked, onChange, disabled }: ToggleRowProps) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="flex-1">
        <Label htmlFor={name} className="text-sm font-medium">
          {label}
        </Label>
        <p className="text-xs text-muted-foreground mt-0.5">{hint}</p>
      </div>
      <Switch
        id={name}
        checked={checked}
        onCheckedChange={onChange}
        disabled={disabled}
        aria-label={label}
      />
    </div>
  )
}
```

- [ ] **Step 5: Run tests**

```bash
cd frontend && npx jest CustomLyricsSettings.test --watchAll=false
```

Expected: PASS for all 5 tests.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/lyrics-review/modals/CustomLyricsSettings.tsx frontend/components/lyrics-review/modals/__tests__/CustomLyricsSettings.test.tsx frontend/messages/en.json
git commit -m "feat(custom-lyrics): settings panel component (toggles + strictness slider)"
```

---

### Task 17: Build the `CustomLyricsPreview` component

**Files:**
- Create: `frontend/components/lyrics-review/modals/CustomLyricsPreview.tsx`
- Create: `frontend/components/lyrics-review/modals/__tests__/CustomLyricsPreview.test.tsx`

- [ ] **Step 1: Add preview-related i18n keys to `frontend/messages/en.json`**

Under `lyricsReview.modals.customLyricsMode`, add a `preview` block:

```json
{
  "preview": {
    "iterationsBadge": "AI iterations: {n}",
    "linesPassingBadge": "{passing}/{total} lines passing",
    "severityOk": "OK",
    "severityMinor": "Minor",
    "severityMajor": "Major",
    "stopReasonSuccess": "All lines fit the syllable budget.",
    "stopReasonPlateau": "AI couldn't improve further; some lines still over budget.",
    "stopReasonMaxIters": "Max repair attempts reached; some lines still over budget.",
    "stopReasonLineCountMismatch": "Output line count doesn't match — fix manually before saving.",
    "stopReasonVerbatimSkip": "Verbatim mode: syllable matching not enforced.",
    "variableLineCountBanner": "Output has {actual} lines vs {expected} original. Segments will be re-timed proportionally — sync each segment manually after save.",
    "lineNumberLabel": "Line {n}",
    "syllableSummary": "target {target} / actual {actual}",
    "syllableDelta": "Δ{delta}"
  }
}
```

- [ ] **Step 2: Write failing component test**

`frontend/components/lyrics-review/modals/__tests__/CustomLyricsPreview.test.tsx`:

```tsx
import { render, screen, fireEvent } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import messages from '@/messages/en.json'
import CustomLyricsPreview from '../CustomLyricsPreview'
import type { LineMetadata } from '@/lib/api/customLyrics'

const baseMeta: LineMetadata = {
  line_index: 0,
  target_text: 'aa bb',
  candidate_text: 'xx yy',
  target_syllables: [2, 2, 2, 2],
  candidate_syllables: [2, 2, 2, 2],
  min_delta: 0,
  passes: true,
  severity: 'ok',
  time_budget_seconds: 1.0,
}

function renderWith(props: Partial<React.ComponentProps<typeof CustomLyricsPreview>> = {}) {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <CustomLyricsPreview
        lines={['xx yy', 'aa bb']}
        lineMetadata={[
          { ...baseMeta, line_index: 0, candidate_text: 'xx yy' },
          { ...baseMeta, line_index: 1, candidate_text: 'aa bb', target_text: 'cc dd' },
        ]}
        iterationsUsed={0}
        stopReason="success"
        expectedLineCount={2}
        onLineEdit={jest.fn()}
        {...props}
      />
    </NextIntlClientProvider>,
  )
}

describe('CustomLyricsPreview', () => {
  it('renders one row per line', () => {
    renderWith()
    expect(screen.getAllByRole('textbox')).toHaveLength(2)
  })

  it('shows iterations badge', () => {
    renderWith({ iterationsUsed: 3 })
    expect(screen.getByText(/AI iterations: 3/i)).toBeInTheDocument()
  })

  it('shows passing summary', () => {
    renderWith({
      lineMetadata: [
        { ...baseMeta, line_index: 0, severity: 'ok', passes: true },
        { ...baseMeta, line_index: 1, severity: 'major', passes: false, min_delta: 5 },
      ],
    })
    expect(screen.getByText(/1\/2 lines passing/i)).toBeInTheDocument()
  })

  it('shows variable-line-count banner when actual ≠ expected', () => {
    renderWith({ expectedLineCount: 3, lines: ['x', 'y'] })
    expect(screen.getByText(/Output has 2 lines vs 3 original/i)).toBeInTheDocument()
  })

  it('editing a line fires onLineEdit', () => {
    const onLineEdit = jest.fn()
    renderWith({ onLineEdit })
    const inputs = screen.getAllByRole('textbox')
    fireEvent.change(inputs[0], { target: { value: 'new text' } })
    expect(onLineEdit).toHaveBeenCalledWith(0, 'new text')
  })

  it('shows stop-reason message for plateau', () => {
    renderWith({ stopReason: 'plateau' })
    expect(screen.getByText(/AI couldn't improve further/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd frontend && npx jest CustomLyricsPreview.test --watchAll=false
```

Expected: FAIL with "Cannot find module".

- [ ] **Step 4: Implement the preview component**

`frontend/components/lyrics-review/modals/CustomLyricsPreview.tsx`:

```tsx
'use client'

import { useTranslations } from 'next-intl'
import { useMemo } from 'react'
import { Check, AlertTriangle, Info } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import type { LineMetadata, LineSeverity, StopReason } from '@/lib/api/customLyrics'

interface Props {
  lines: string[]
  lineMetadata: LineMetadata[]
  iterationsUsed: number
  stopReason: StopReason
  expectedLineCount: number
  onLineEdit: (index: number, text: string) => void
}

export default function CustomLyricsPreview({
  lines,
  lineMetadata,
  iterationsUsed,
  stopReason,
  expectedLineCount,
  onLineEdit,
}: Props) {
  const t = useTranslations('lyricsReview.modals.customLyricsMode.preview')

  const passing = useMemo(
    () => lineMetadata.filter((m) => m.passes).length,
    [lineMetadata],
  )

  const stopMessage = useMemo(() => {
    switch (stopReason) {
      case 'success': return t('stopReasonSuccess')
      case 'plateau': return t('stopReasonPlateau')
      case 'max_iters_reached': return t('stopReasonMaxIters')
      case 'line_count_mismatch': return t('stopReasonLineCountMismatch')
      case 'verbatim_skip': return t('stopReasonVerbatimSkip')
    }
  }, [stopReason, t])

  const showVariableBanner = lines.length !== expectedLineCount

  return (
    <div className="flex flex-col gap-3 flex-1 overflow-hidden">
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        <span>{t('iterationsBadge', { n: iterationsUsed })}</span>
        <span>•</span>
        <span>{t('linesPassingBadge', { passing, total: lineMetadata.length })}</span>
      </div>

      {stopMessage && (
        <div className="flex items-start gap-2 p-2 rounded-md bg-muted/50 text-xs">
          <Info className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <span>{stopMessage}</span>
        </div>
      )}

      {showVariableBanner && (
        <div className="flex items-start gap-2 p-2 rounded-md bg-blue-500/10 text-xs text-blue-700 dark:text-blue-300">
          <Info className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <span>
            {t('variableLineCountBanner', { actual: lines.length, expected: expectedLineCount })}
          </span>
        </div>
      )}

      <div className="flex-1 overflow-y-auto space-y-1 pr-1">
        {lines.map((line, idx) => {
          const meta = lineMetadata[idx]
          return (
            <LineRow
              key={idx}
              index={idx}
              text={line}
              meta={meta}
              onEdit={(value) => onLineEdit(idx, value)}
            />
          )
        })}
      </div>
    </div>
  )
}

interface LineRowProps {
  index: number
  text: string
  meta: LineMetadata | undefined
  onEdit: (value: string) => void
}

function LineRow({ index, text, meta, onEdit }: LineRowProps) {
  const severity: LineSeverity = meta?.severity ?? 'ok'
  const colorClass = severityClass(severity)
  const summary = meta
    ? `target ${median(meta.target_syllables)} / actual ${median(meta.candidate_syllables)} · Δ${meta.min_delta}`
    : ''

  return (
    <div className="flex items-center gap-2">
      <span className="w-8 text-xs text-muted-foreground tabular-nums shrink-0">
        {index + 1}
      </span>
      <Input
        value={text}
        onChange={(e) => onEdit(e.target.value)}
        className={'flex-1 font-mono text-sm h-8 ' + colorClass}
      />
      <span className="text-xs text-muted-foreground w-44 shrink-0 text-right">{summary}</span>
      <SeverityBadge severity={severity} />
    </div>
  )
}

function median(arr: number[]): number {
  if (!arr.length) return 0
  const sorted = [...arr].sort((a, b) => a - b)
  return sorted[Math.floor(sorted.length / 2)]
}

function severityClass(s: LineSeverity): string {
  if (s === 'major') return 'border-destructive'
  if (s === 'minor') return 'border-yellow-500'
  return ''
}

function SeverityBadge({ severity }: { severity: LineSeverity }) {
  if (severity === 'ok') return <Check className="h-4 w-4 text-green-500 shrink-0" />
  if (severity === 'minor') return <AlertTriangle className="h-4 w-4 text-yellow-500 shrink-0" />
  return <AlertTriangle className="h-4 w-4 text-destructive shrink-0" />
}
```

- [ ] **Step 5: Run tests**

```bash
cd frontend && npx jest CustomLyricsPreview.test --watchAll=false
```

Expected: PASS for all 6 tests.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/lyrics-review/modals/CustomLyricsPreview.tsx frontend/components/lyrics-review/modals/__tests__/CustomLyricsPreview.test.tsx frontend/messages/en.json
git commit -m "feat(custom-lyrics): preview component with per-line syllable annotations"
```

---

### Task 18: Wire settings + preview into `CustomLyricsMode`

**Files:**
- Modify: `frontend/components/lyrics-review/modals/CustomLyricsMode.tsx`
- Modify: `frontend/lib/lyrics-review/utils/segmentsFromLines.ts`
- Modify: `frontend/components/lyrics-review/modals/__tests__/CustomLyricsMode.test.tsx`

- [ ] **Step 1: Extend `segmentsFromLines` to accept timing redistribution**

Read the existing helper:

```bash
cat frontend/lib/lyrics-review/utils/segmentsFromLines.ts
```

Modify the signature to accept an optional `redistributedTiming?: Array<[number, number]>`. When present, use those (start, end) pairs for the new segments instead of inheriting from the original at the same index. Add unit tests in `__tests__/segmentsFromLines.test.ts` to cover both code paths.

```typescript
// segmentsFromLines.ts (additions to existing file)
export interface SegmentsFromLinesOptions {
  redistributedTiming?: Array<[number, number]>
}

export function segmentsFromLines(
  lines: string[],
  existingSegments: LyricsSegment[],
  options: SegmentsFromLinesOptions = {},
): LyricsSegment[] {
  const { redistributedTiming } = options
  return lines.map((line, idx) => {
    const [start, end] = redistributedTiming?.[idx] ?? [
      existingSegments[idx]?.start_time ?? null,
      existingSegments[idx]?.end_time ?? null,
    ]
    // ... existing word-building logic, using start/end above
    return /* existing-shape segment with updated start_time, end_time */
  })
}
```

(Adapt the inner body to whatever the existing implementation does — the change is purely the timing source.)

- [ ] **Step 2: Update `CustomLyricsMode.tsx` to render new components**

In `frontend/components/lyrics-review/modals/CustomLyricsMode.tsx`:

1. Add settings state:

```tsx
import CustomLyricsSettings from './CustomLyricsSettings'
import CustomLyricsPreview from './CustomLyricsPreview'
import {
  GenerationSettings,
  DEFAULT_GENERATION_SETTINGS,
  LineMetadata,
  StopReason,
} from '@/lib/api/customLyrics'

// in component body:
const [settings, setSettings] = useState<GenerationSettings>(DEFAULT_GENERATION_SETTINGS)
const [lineMetadata, setLineMetadata] = useState<LineMetadata[]>([])
const [iterationsUsed, setIterationsUsed] = useState(0)
const [stopReason, setStopReason] = useState<StopReason>('success')
const [newSegmentTiming, setNewSegmentTiming] = useState<Array<[number, number]> | null>(null)
```

2. Render `<CustomLyricsSettings settings={settings} onChange={setSettings} disabled={phase === 'generating'} />` below the existing inputs in the input phase.

3. Pass `settings` into the `generateCustomLyrics` API call.

4. On successful response, populate `lineMetadata`, `iterationsUsed`, `stopReason`, `newSegmentTiming`. Also `setGeneratedText(response.lines.join('\n'))` for backward compat.

5. In the preview phase, replace the plain `<Textarea>` with `<CustomLyricsPreview>`. Wire its `onLineEdit` handler to update the local lines array (re-derive `generatedText` from it). Also re-validate locally after edit (we keep the previous severity until the user re-generates; that's good enough — operator's manual edits are trusted).

6. Update `handleSave` to use `redistributedTiming` if set:

```tsx
const handleSave = useCallback(() => {
  const lines = generatedText.split('\n')
  const newSegments = segmentsFromLines(lines, existingSegments, {
    redistributedTiming: newSegmentTiming ?? undefined,
  })
  onSave(newSegments, {
    source: inputTab,
    filename: file?.name,
    model: modelUsed ?? 'unknown',
  })
}, [generatedText, existingSegments, onSave, inputTab, file, modelUsed, newSegmentTiming])
```

7. Update `canSave` logic. With `fixed_line_count=true` (default), Save is gated on count match (existing). With `fixed_line_count=false`, Save is allowed if there's at least one non-empty line and ≤ `2N` lines (project's safety bound).

```tsx
const canSave = useMemo(() => {
  if (phase !== 'preview') return false
  if (settings.fixed_line_count) return previewLineDiff === 0
  return previewLineCount > 0 && previewLineCount <= expectedLineCount * 2
}, [phase, settings.fixed_line_count, previewLineDiff, previewLineCount, expectedLineCount])
```

- [ ] **Step 3: Update existing `CustomLyricsMode.test.tsx`**

Existing test for the plain-textarea preview is now obsolete; replace with tests asserting `<CustomLyricsPreview>` is rendered. Existing tests for settings panel rendering are not needed (covered in `CustomLyricsSettings.test.tsx`).

- [ ] **Step 4: Run frontend tests**

```bash
cd frontend && npx jest CustomLyrics --watchAll=false
```

Expected: all PASS.

- [ ] **Step 5: Run frontend type check**

```bash
cd frontend && npx tsc --noEmit 2>&1 | tail -10
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/lyrics-review/modals/CustomLyricsMode.tsx frontend/components/lyrics-review/modals/__tests__/CustomLyricsMode.test.tsx frontend/lib/lyrics-review/utils/segmentsFromLines.ts frontend/lib/lyrics-review/utils/__tests__/segmentsFromLines.test.ts
git commit -m "feat(custom-lyrics): wire settings + preview into CustomLyricsMode"
```

---

### Task 19: Run i18n translation pipeline

**Files:**
- Modify: `frontend/messages/{ar,bn,...all 32 non-en}.json`

- [ ] **Step 1: Run translation script**

```bash
cd frontend && python scripts/translate.py --messages-dir ./messages --target all
```

Expected: all 32 non-English locale files updated with translated keys for the new `settings.*` and `preview.*` blocks. The GCS cache will speed up unchanged keys.

- [ ] **Step 2: Verify CI parity check passes**

```bash
cd frontend && python scripts/translate.py --messages-dir ./messages --target all --dry-run
```

Expected: "All locales up to date" (or equivalent).

- [ ] **Step 3: Commit translations**

```bash
git add frontend/messages/
git commit -m "i18n(custom-lyrics): translate new settings + preview keys to all 32 locales"
```

---

### Task 20: Update production E2E

**Files:**
- Modify: `frontend/e2e/production/custom-lyrics-mode.spec.ts`

- [ ] **Step 1: Read existing E2E spec**

```bash
cat frontend/e2e/production/custom-lyrics-mode.spec.ts
```

- [ ] **Step 2: Add E2E coverage for new UI**

Add three test cases (skipped without `KARAOKE_ADMIN_TOKEN`):
1. `'opens generation settings panel and toggles a switch'` — opens collapsible, clicks `Allow rewording client lyrics` switch, verifies state.
2. `'sets strictness to Tight and generates'` — clicks Tight button, runs Generate, asserts preview shows iterations badge ≥ 1.
3. `'shows variable line count banner when fixed_line_count=OFF'` — toggles off `Maintain original segment count`, runs Generate, asserts banner appears.

```typescript
test('opens generation settings panel and toggles allow_reword', async ({ page }) => {
  // (skip if !KARAOKE_ADMIN_TOKEN)
  await openCustomLyricsModal(page)
  await page.getByText('Generation settings').click()
  const switchEl = page.getByRole('switch', { name: /Allow rewording/ })
  await expect(switchEl).toHaveAttribute('aria-checked', 'true')
  await switchEl.click()
  await expect(switchEl).toHaveAttribute('aria-checked', 'false')
})
```

(Adapt to project's existing test scaffolding helpers.)

- [ ] **Step 3: Smoke-run locally**

```bash
KARAOKE_ADMIN_TOKEN=$(gcloud secrets versions access latest --secret=admin-tokens --project=nomadkaraoke | cut -d',' -f1) \
  cd frontend && npx playwright test e2e/production/custom-lyrics-mode.spec.ts --headed
```

Expected: all new tests pass against production.

- [ ] **Step 4: Commit**

```bash
git add frontend/e2e/production/custom-lyrics-mode.spec.ts
git commit -m "test(custom-lyrics): production E2E for settings + preview"
```

---

## Phase 7 — Eval harness

### Task 21: Create eval package + cache module

**Files:**
- Create: `backend/eval/__init__.py`
- Create: `backend/eval/custom_lyrics/__init__.py`
- Create: `backend/eval/custom_lyrics/cache.py`
- Create: `backend/eval/custom_lyrics/tests/__init__.py`
- Create: `backend/eval/custom_lyrics/tests/test_cache.py`

- [ ] **Step 1: Create skeleton**

```bash
mkdir -p backend/eval/custom_lyrics/tests backend/eval/custom_lyrics/fixtures backend/eval/custom_lyrics/cache backend/eval/custom_lyrics/results
touch backend/eval/__init__.py backend/eval/custom_lyrics/__init__.py backend/eval/custom_lyrics/tests/__init__.py
echo "*" > backend/eval/custom_lyrics/cache/.gitignore  # don't commit cache contents
echo "!.gitignore" >> backend/eval/custom_lyrics/cache/.gitignore
echo "*" > backend/eval/custom_lyrics/results/.gitignore
echo "!.gitignore" >> backend/eval/custom_lyrics/results/.gitignore
```

- [ ] **Step 2: Write failing test for cache**

`backend/eval/custom_lyrics/tests/test_cache.py`:

```python
"""Tests for the LLM-call disk cache."""
from __future__ import annotations

import tempfile
from pathlib import Path

from backend.eval.custom_lyrics.cache import LlmCallCache


def test_miss_returns_none() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        c = LlmCallCache(Path(tmp))
        assert c.get("system", "user", model="m1", settings_dict={"x": 1}) is None


def test_set_then_get_hits() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        c = LlmCallCache(Path(tmp))
        c.set("system", "user", model="m1", settings_dict={"x": 1}, response_lines=["a", "b"])
        assert c.get("system", "user", model="m1", settings_dict={"x": 1}) == ["a", "b"]


def test_different_settings_different_keys() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        c = LlmCallCache(Path(tmp))
        c.set("system", "user", model="m1", settings_dict={"x": 1}, response_lines=["a"])
        assert c.get("system", "user", model="m1", settings_dict={"x": 2}) is None


def test_replay_only_raises_on_miss() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        c = LlmCallCache(Path(tmp), replay_only=True)
        try:
            c.get("system", "user", model="m1", settings_dict={"x": 1})
        except RuntimeError as e:
            assert "cache miss" in str(e).lower()
        else:
            raise AssertionError("expected RuntimeError")
```

- [ ] **Step 3: Implement `cache.py`**

`backend/eval/custom_lyrics/cache.py`:

```python
"""Disk-backed cache for LLM responses, keyed by (system, user, model, settings)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional


class LlmCallCache:
    def __init__(self, cache_dir: Path, replay_only: bool = False) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.replay_only = replay_only

    def _key(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str,
        settings_dict: dict[str, Any],
    ) -> str:
        h = hashlib.sha256()
        h.update(system_prompt.encode())
        h.update(b"\x00")
        h.update(user_prompt.encode())
        h.update(b"\x00")
        h.update(model.encode())
        h.update(b"\x00")
        h.update(json.dumps(settings_dict, sort_keys=True).encode())
        return h.hexdigest()

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def get(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str,
        settings_dict: dict[str, Any],
    ) -> Optional[list[str]]:
        path = self._path(self._key(system_prompt, user_prompt, model=model, settings_dict=settings_dict))
        if not path.exists():
            if self.replay_only:
                raise RuntimeError(f"cache miss in replay-only mode: {path.name}")
            return None
        return json.loads(path.read_text())["lines"]

    def set(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str,
        settings_dict: dict[str, Any],
        response_lines: list[str],
    ) -> None:
        if self.replay_only:
            return
        path = self._path(self._key(system_prompt, user_prompt, model=model, settings_dict=settings_dict))
        path.write_text(json.dumps({"lines": response_lines}, indent=2))
```

- [ ] **Step 4: Run tests**

```bash
poetry run pytest backend/eval/custom_lyrics/tests/test_cache.py -v
```

Expected: PASS for all 4 tests.

- [ ] **Step 5: Commit**

```bash
git add backend/eval/__init__.py backend/eval/custom_lyrics/__init__.py backend/eval/custom_lyrics/cache.py backend/eval/custom_lyrics/cache/.gitignore backend/eval/custom_lyrics/results/.gitignore backend/eval/custom_lyrics/tests/__init__.py backend/eval/custom_lyrics/tests/test_cache.py
git commit -m "feat(eval): LLM-call disk cache for record-and-replay"
```

---

### Task 22: Implement scorer

**Files:**
- Create: `backend/eval/custom_lyrics/scorer.py`
- Create: `backend/eval/custom_lyrics/tests/test_scorer.py`

- [ ] **Step 1: Write failing scorer tests**

`backend/eval/custom_lyrics/tests/test_scorer.py`:

```python
"""Tests for eval scorer."""
from __future__ import annotations

from backend.eval.custom_lyrics.scorer import (
    PerLineScore,
    FixtureScore,
    score_per_line,
    aggregate_fixture,
)
from backend.services.custom_lyrics.validator import LineValidation, Severity


def _line_val(idx: int, target: list[int], cand: list[int], passes: bool, severity: Severity, delta: int) -> LineValidation:
    return LineValidation(
        line_index=idx,
        target_text=f"t{idx}",
        candidate_text=f"c{idx}",
        target_syllables=target,
        candidate_syllables=cand,
        min_delta=delta,
        passes=passes,
        severity=severity,
        time_budget_seconds=1.0,
    )


def test_score_per_line_pass_fail_thresholds() -> None:
    val = _line_val(0, [5, 5, 5, 5], [6, 6, 6, 6], passes=False, severity=Severity.MINOR, delta=1)
    s = score_per_line(val)
    assert s.pass_at_0 is False
    assert s.pass_at_1 is True
    assert s.pass_at_2 is True
    assert s.pass_at_4 is True
    assert s.min_delta == 1


def test_score_per_line_major_violation() -> None:
    val = _line_val(0, [5, 5, 5, 5], [12, 12, 12, 12], passes=False, severity=Severity.MAJOR, delta=7)
    s = score_per_line(val)
    assert s.pass_at_0 is False
    assert s.pass_at_4 is False
    assert s.severity == "major"


def test_aggregate_fixture_basic() -> None:
    metadata = [
        _line_val(0, [5]*4, [5]*4, passes=True, severity=Severity.OK, delta=0),
        _line_val(1, [5]*4, [6]*4, passes=False, severity=Severity.MINOR, delta=1),
        _line_val(2, [5]*4, [9]*4, passes=False, severity=Severity.MAJOR, delta=4),
    ]
    fixture = aggregate_fixture(
        fixture_id="x",
        settings_name="default",
        metadata=metadata,
        iterations_used=2,
        stop_reason="max_iters_reached",
        duration_ms=1500,
        gemini_calls=3,
        line_count_match=True,
    )
    assert fixture.pct_pass_at_2 == pytest.approx(2 / 3)
    assert fixture.pct_pass_at_0 == pytest.approx(1 / 3)
    assert fixture.mean_delta == pytest.approx((0 + 1 + 4) / 3)
    assert fixture.max_delta == 4
```

(Add `import pytest` to the imports.)

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest backend/eval/custom_lyrics/tests/test_scorer.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `scorer.py`**

`backend/eval/custom_lyrics/scorer.py`:

```python
"""Pure scoring functions for custom-lyrics eval."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from backend.services.custom_lyrics.validator import LineValidation


@dataclass
class PerLineScore:
    line_index: int
    min_delta: int
    pass_at_0: bool
    pass_at_1: bool
    pass_at_2: bool
    pass_at_4: bool
    severity: str


@dataclass
class FixtureScore:
    fixture_id: str
    settings_name: str
    line_count: int
    pct_pass_at_2: float
    pct_pass_at_1: float
    pct_pass_at_0: float
    pct_pass_at_4: float
    mean_delta: float
    median_delta: float
    max_delta: int
    iterations_used: int
    stop_reason: str
    duration_ms: int
    gemini_calls: int
    line_count_match: bool
    severity_breakdown: dict[str, int]


def score_per_line(val: LineValidation) -> PerLineScore:
    return PerLineScore(
        line_index=val.line_index,
        min_delta=val.min_delta,
        pass_at_0=val.min_delta <= 0,
        pass_at_1=val.min_delta <= 1,
        pass_at_2=val.min_delta <= 2,
        pass_at_4=val.min_delta <= 4,
        severity=val.severity.value,
    )


def aggregate_fixture(
    *,
    fixture_id: str,
    settings_name: str,
    metadata: list[LineValidation],
    iterations_used: int,
    stop_reason: str,
    duration_ms: int,
    gemini_calls: int,
    line_count_match: bool,
) -> FixtureScore:
    n = len(metadata)
    if n == 0:
        return FixtureScore(
            fixture_id=fixture_id, settings_name=settings_name,
            line_count=0,
            pct_pass_at_2=0, pct_pass_at_1=0, pct_pass_at_0=0, pct_pass_at_4=0,
            mean_delta=0, median_delta=0, max_delta=0,
            iterations_used=iterations_used, stop_reason=stop_reason,
            duration_ms=duration_ms, gemini_calls=gemini_calls,
            line_count_match=line_count_match,
            severity_breakdown={"ok": 0, "minor": 0, "major": 0},
        )
    deltas = sorted(v.min_delta for v in metadata)
    severity_breakdown = {"ok": 0, "minor": 0, "major": 0}
    for v in metadata:
        severity_breakdown[v.severity.value] += 1
    return FixtureScore(
        fixture_id=fixture_id,
        settings_name=settings_name,
        line_count=n,
        pct_pass_at_2=sum(1 for d in deltas if d <= 2) / n,
        pct_pass_at_1=sum(1 for d in deltas if d <= 1) / n,
        pct_pass_at_0=sum(1 for d in deltas if d <= 0) / n,
        pct_pass_at_4=sum(1 for d in deltas if d <= 4) / n,
        mean_delta=sum(deltas) / n,
        median_delta=deltas[n // 2],
        max_delta=deltas[-1],
        iterations_used=iterations_used,
        stop_reason=stop_reason,
        duration_ms=duration_ms,
        gemini_calls=gemini_calls,
        line_count_match=line_count_match,
        severity_breakdown=severity_breakdown,
    )


def aggregate_corpus(per_fixture: Iterable[FixtureScore]) -> dict:
    """Macro-averaged corpus aggregate."""
    fixtures = list(per_fixture)
    if not fixtures:
        return {}
    return {
        "fixture_count": len(fixtures),
        "macro_pct_pass_at_2": sum(f.pct_pass_at_2 for f in fixtures) / len(fixtures),
        "macro_pct_pass_at_1": sum(f.pct_pass_at_1 for f in fixtures) / len(fixtures),
        "macro_pct_pass_at_0": sum(f.pct_pass_at_0 for f in fixtures) / len(fixtures),
        "macro_pct_pass_at_4": sum(f.pct_pass_at_4 for f in fixtures) / len(fixtures),
        "macro_mean_delta": sum(f.mean_delta for f in fixtures) / len(fixtures),
        "total_gemini_calls": sum(f.gemini_calls for f in fixtures),
    }
```

- [ ] **Step 4: Run tests**

```bash
poetry run pytest backend/eval/custom_lyrics/tests/test_scorer.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/eval/custom_lyrics/scorer.py backend/eval/custom_lyrics/tests/test_scorer.py
git commit -m "feat(eval): per-line + per-fixture + corpus scorers"
```

---

### Task 23: Implement runner + report

**Files:**
- Create: `backend/eval/custom_lyrics/runner.py`
- Create: `backend/eval/custom_lyrics/report.py`
- Create: `backend/eval/custom_lyrics/run.py`

- [ ] **Step 1: Implement runner**

`backend/eval/custom_lyrics/runner.py`:

```python
"""Eval runner: load fixture → call service (cached) → score → return result."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

from backend.eval.custom_lyrics.cache import LlmCallCache
from backend.eval.custom_lyrics.scorer import FixtureScore, aggregate_fixture
from backend.services.custom_lyrics.service import CustomLyricsService
from backend.services.custom_lyrics.settings import (
    GenerationSettings,
    settings_from_dict,
)


@dataclass
class FixtureRunResult:
    fixture_id: str
    settings_name: str
    score: FixtureScore
    output_lines: list[str]
    line_metadata: list[dict[str, Any]]
    fixture_dir: Path


def load_fixture(fixture_dir: Path) -> dict:
    metadata = json.loads((fixture_dir / "metadata.json").read_text())
    metadata["original_lyrics"] = (fixture_dir / "original_lyrics.txt").read_text().strip().splitlines()
    metadata["original_segments"] = json.loads((fixture_dir / "original_segments.json").read_text())
    metadata["client_input"] = (fixture_dir / "client_input.txt").read_text()
    notes_path = fixture_dir / "notes.txt"
    metadata["notes"] = notes_path.read_text() if notes_path.exists() else None
    return metadata


def run_fixture(
    fixture_dir: Path,
    settings_name: str,
    settings_dict: dict[str, Any],
    *,
    cache: LlmCallCache,
    service: CustomLyricsService,
) -> FixtureRunResult:
    fixture = load_fixture(fixture_dir)
    settings = settings_from_dict(settings_dict)
    gemini_call_count = 0

    real_call = service._call_gemini

    def cached_call(*, system_prompt: str, user_prompt: str, pdf_bytes, settings: GenerationSettings):
        nonlocal gemini_call_count
        cached = cache.get(
            system_prompt, user_prompt,
            model=service.settings.custom_lyrics_model,
            settings_dict=settings.to_dict(),
        )
        if cached is not None:
            return cached
        gemini_call_count += 1
        result = real_call(
            system_prompt=system_prompt, user_prompt=user_prompt,
            pdf_bytes=pdf_bytes, settings=settings,
        )
        cache.set(
            system_prompt, user_prompt,
            model=service.settings.custom_lyrics_model,
            settings_dict=settings.to_dict(),
            response_lines=result,
        )
        return result

    with patch.object(service, "_call_gemini", side_effect=cached_call):
        result = service.generate(
            job_id=fixture["id"],
            target_lines=fixture["original_lyrics"],
            target_segments=fixture["original_segments"],
            artist=fixture.get("artist"),
            title=fixture.get("title"),
            custom_text=fixture["client_input"],
            file_bytes=None, file_mime=None, file_name=None,
            notes=fixture.get("notes"),
            settings=settings,
        )

    score = aggregate_fixture(
        fixture_id=fixture["id"],
        settings_name=settings_name,
        metadata=result.line_metadata,
        iterations_used=result.iterations_used,
        stop_reason=result.stop_reason.value,
        duration_ms=result.duration_ms,
        gemini_calls=gemini_call_count,
        line_count_match=not result.line_count_mismatch,
    )

    return FixtureRunResult(
        fixture_id=fixture["id"],
        settings_name=settings_name,
        score=score,
        output_lines=result.lines,
        line_metadata=[{
            "line_index": v.line_index,
            "target_text": v.target_text,
            "candidate_text": v.candidate_text,
            "min_delta": v.min_delta,
            "severity": v.severity.value,
        } for v in result.line_metadata],
        fixture_dir=fixture_dir,
    )
```

- [ ] **Step 2: Implement report**

`backend/eval/custom_lyrics/report.py`:

```python
"""Markdown report generation for eval runs."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.eval.custom_lyrics.runner import FixtureRunResult
from backend.eval.custom_lyrics.scorer import aggregate_corpus


def write_run_reports(
    results: list[FixtureRunResult],
    out_dir: Path,
    baseline: Optional[dict] = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "per_fixture").mkdir(exist_ok=True)

    summary_lines = ["# Custom Lyrics Eval Run", ""]
    summary_lines.append(f"_Generated: {datetime.now(timezone.utc).isoformat()}_")
    summary_lines.append("")
    summary_lines.append("## Per-fixture summary")
    summary_lines.append("")
    summary_lines.append("| Fixture | Settings | pass@±2 | Δ | mean_Δ | iters | stop |")
    summary_lines.append("|---------|----------|---------|---|--------|-------|------|")
    for r in results:
        s = r.score
        delta_str = ""
        if baseline:
            prev = baseline.get(r.fixture_id, {}).get(r.settings_name, {})
            if "pct_pass_at_2" in prev:
                d = s.pct_pass_at_2 - prev["pct_pass_at_2"]
                delta_str = f"{d:+.2f}"
        summary_lines.append(
            f"| {r.fixture_id} | {r.settings_name} | {s.pct_pass_at_2:.2f} | "
            f"{delta_str} | {s.mean_delta:.2f} | {s.iterations_used} | {s.stop_reason} |"
        )

    summary_lines.append("")
    corpus = aggregate_corpus([r.score for r in results])
    summary_lines.append("## Corpus aggregate")
    summary_lines.append("")
    summary_lines.append(f"- Macro pass@±2: {corpus.get('macro_pct_pass_at_2', 0):.2f}")
    summary_lines.append(f"- Macro pass@±1: {corpus.get('macro_pct_pass_at_1', 0):.2f}")
    summary_lines.append(f"- Macro pass@±0: {corpus.get('macro_pct_pass_at_0', 0):.2f}")
    summary_lines.append(f"- Total Gemini calls: {corpus.get('total_gemini_calls', 0)}")

    (out_dir / "summary.md").write_text("\n".join(summary_lines))

    for r in results:
        per_dir = out_dir / "per_fixture" / f"{r.fixture_id}__{r.settings_name}"
        per_dir.mkdir(parents=True, exist_ok=True)
        (per_dir / "output.json").write_text(json.dumps({
            "fixture_id": r.fixture_id,
            "settings_name": r.settings_name,
            "score": r.score.__dict__,
            "lines": r.output_lines,
            "line_metadata": r.line_metadata,
        }, indent=2))
        _write_per_fixture_md(per_dir, r)


def _write_per_fixture_md(out_dir: Path, r: FixtureRunResult) -> None:
    lines = [
        f"# {r.fixture_id} — {r.settings_name}",
        "",
        f"Stop reason: **{r.score.stop_reason}** after {r.score.iterations_used} iterations.",
        f"Pass@±2: **{r.score.pct_pass_at_2:.0%}** ({sum(1 for v in r.line_metadata if v['min_delta']<=2)}/{r.score.line_count})",
        "",
        "| # | Original target | Candidate | Δ | Severity |",
        "|---|------------------|-----------|---|----------|",
    ]
    for v in r.line_metadata:
        lines.append(
            f"| {v['line_index']+1} | {_escape(v['target_text'])} | "
            f"{_escape(v['candidate_text'])} | {v['min_delta']} | {v['severity']} |"
        )
    lines += [
        "",
        "## Your rating",
        "",
        "Add your qualitative notes here:",
        "- [ ] Singable end-to-end",
        "- [ ] Names placed naturally",
        "- [ ] Stress patterns reasonable",
        "- Notes:",
        "",
    ]
    (out_dir / "output.md").write_text("\n".join(lines))


def _escape(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def write_baseline(results: list[FixtureRunResult], path: Path) -> None:
    out: dict = {}
    for r in results:
        bucket = out.setdefault(r.fixture_id, {})
        bucket[r.settings_name] = {
            "pct_pass_at_2": r.score.pct_pass_at_2,
            "pct_pass_at_0": r.score.pct_pass_at_0,
            "mean_delta": r.score.mean_delta,
            "iterations_used": r.score.iterations_used,
            "captured_at": datetime.now(timezone.utc).date().isoformat(),
        }
    path.write_text(json.dumps(out, indent=2))
```

- [ ] **Step 3: Implement CLI entrypoint**

`backend/eval/custom_lyrics/run.py`:

```python
"""CLI: python -m backend.eval.custom_lyrics.run"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend.eval.custom_lyrics.cache import LlmCallCache
from backend.eval.custom_lyrics.report import write_baseline, write_run_reports
from backend.eval.custom_lyrics.runner import run_fixture
from backend.services.custom_lyrics.service import CustomLyricsService

EVAL_ROOT = Path(__file__).parent
FIXTURES_DIR = EVAL_ROOT / "fixtures"
RESULTS_DIR = EVAL_ROOT / "results"
CACHE_DIR = EVAL_ROOT / "cache"
BASELINE_PATH = EVAL_ROOT / "baseline.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", default="all", help="fixture id, or comma-separated, or 'all'")
    parser.add_argument("--settings", default=None, help="settings name from metadata.settings_to_test")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--replay-only", action="store_true")
    parser.add_argument("--save-as-baseline", default=None,
                        help="path to write baseline JSON (e.g., baseline-post.json)")
    args = parser.parse_args()

    cache = LlmCallCache(
        CACHE_DIR,
        replay_only=args.replay_only,
    ) if not args.no_cache else _NullCache()

    fixture_dirs = _select_fixtures(args.fixtures)
    service = CustomLyricsService()

    results = []
    for fdir in fixture_dirs:
        meta = json.loads((fdir / "metadata.json").read_text())
        candidate_settings = meta.get("settings_to_test", [{
            "name": "default",
            "allow_reword": True,
            "allow_omit": True,
            "fixed_line_count": True,
            "strictness": "balanced",
        }])
        for s in candidate_settings:
            if args.settings and s["name"] != args.settings:
                continue
            settings_dict = {k: v for k, v in s.items() if k != "name"}
            print(f"Running {fdir.name} / {s['name']}...", file=sys.stderr)
            results.append(run_fixture(
                fdir, s["name"], settings_dict,
                cache=cache, service=service,
            ))

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    out_dir = RESULTS_DIR / ts
    baseline = json.loads(BASELINE_PATH.read_text()) if BASELINE_PATH.exists() else None
    write_run_reports(results, out_dir, baseline=baseline)

    if args.save_as_baseline:
        write_baseline(results, EVAL_ROOT / args.save_as_baseline)
        print(f"Baseline saved to {args.save_as_baseline}", file=sys.stderr)

    print(f"\nReport: {out_dir / 'summary.md'}", file=sys.stderr)
    return 0


def _select_fixtures(spec: str) -> list[Path]:
    if spec == "all":
        return sorted(p for p in FIXTURES_DIR.iterdir() if p.is_dir() and (p / "metadata.json").exists())
    ids = [s.strip() for s in spec.split(",")]
    return [FIXTURES_DIR / i for i in ids]


class _NullCache:
    def get(self, *args, **kwargs): return None
    def set(self, *args, **kwargs): pass


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Add `eval-custom-lyrics` Make target**

In `Makefile`, add:

```makefile
.PHONY: eval-custom-lyrics
eval-custom-lyrics:
	cd backend && poetry run python -m backend.eval.custom_lyrics.run
```

- [ ] **Step 5: Smoke-import**

```bash
poetry run python -c "from backend.eval.custom_lyrics import run, runner, report, scorer, cache; print('ok')"
```

Expected: `ok`. No import errors.

- [ ] **Step 6: Commit**

```bash
git add backend/eval/custom_lyrics/runner.py backend/eval/custom_lyrics/report.py backend/eval/custom_lyrics/run.py Makefile
git commit -m "feat(eval): runner + markdown report + CLI entrypoint"
```

---

### Task 24: Bootstrap Year 5 fixture from GCS

**Files:**
- Create: `backend/eval/custom_lyrics/_bootstrap_year5.py`
- Create: `backend/eval/custom_lyrics/fixtures/year-5-stars-shake-it-off/metadata.json`
- Create: `backend/eval/custom_lyrics/fixtures/year-5-stars-shake-it-off/original_lyrics.txt`
- Create: `backend/eval/custom_lyrics/fixtures/year-5-stars-shake-it-off/original_segments.json`
- Create: `backend/eval/custom_lyrics/fixtures/year-5-stars-shake-it-off/client_input.txt`

- [ ] **Step 1: Write the bootstrap script**

`backend/eval/custom_lyrics/_bootstrap_year5.py`:

```python
"""One-shot script to populate the year-5-stars fixture from a real production job.

Run once:
    poetry run python -m backend.eval.custom_lyrics._bootstrap_year5
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from google.cloud import firestore, storage


JOB_ID = "2cb49a45"
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "year-5-stars-shake-it-off"
CLIENT_INPUT_SRC = Path("/Users/andrew/Projects/nomadkaraoke/year-5-stars-client-custom-lyrics.txt")


def main() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    fs = firestore.Client(project="nomadkaraoke")
    job_doc = fs.collection("jobs").document(JOB_ID).get()
    if not job_doc.exists:
        raise SystemExit(f"Job {JOB_ID} not found")
    job = job_doc.to_dict()

    corrected_path = job.get("corrected_json_gcs_path") or job.get("lyrics", {}).get("corrected_path")
    if not corrected_path:
        raise SystemExit(f"Job {JOB_ID} has no corrected.json GCS path")

    storage_client = storage.Client(project="nomadkaraoke")
    bucket_name, *blob_parts = corrected_path.replace("gs://", "").split("/", 1)
    blob = storage_client.bucket(bucket_name).blob(blob_parts[0])
    corrected = json.loads(blob.download_as_bytes())

    segments = corrected.get("corrected_segments") or corrected.get("segments") or []
    if not segments:
        raise SystemExit("No segments found in corrected.json")

    (FIXTURE_DIR / "original_segments.json").write_text(json.dumps(segments, indent=2))
    (FIXTURE_DIR / "original_lyrics.txt").write_text(
        "\n".join(seg.get("text", "").strip() for seg in segments) + "\n"
    )

    if CLIENT_INPUT_SRC.exists():
        shutil.copy(CLIENT_INPUT_SRC, FIXTURE_DIR / "client_input.txt")
    else:
        print(f"WARNING: {CLIENT_INPUT_SRC} not found; populate client_input.txt manually")

    metadata = {
        "id": "year-5-stars-shake-it-off",
        "artist": job.get("artist") or "Taylor Swift",
        "title": job.get("title") or "Shake It Off",
        "source_job_id": JOB_ID,
        "difficulty": "hard",
        "input_style": "long substantive lines, name-heavy, mismatched syllable budget",
        "settings_to_test": [
            {"name": "default", "allow_reword": True, "allow_omit": True, "fixed_line_count": True, "strictness": "balanced"},
            {"name": "verbatim", "allow_reword": True, "allow_omit": True, "fixed_line_count": True, "strictness": "verbatim"},
            {"name": "strict", "allow_reword": True, "allow_omit": True, "fixed_line_count": True, "strictness": "strict"},
        ],
    }
    (FIXTURE_DIR / "metadata.json").write_text(json.dumps(metadata, indent=2))

    print(f"Fixture populated at {FIXTURE_DIR}")
    print(f"  - {len(segments)} segments")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the bootstrap**

```bash
GOOGLE_CLOUD_PROJECT=nomadkaraoke poetry run python -m backend.eval.custom_lyrics._bootstrap_year5
```

Expected: prints "Fixture populated at ...". Four files appear in the fixture directory.

If GCS auth fails: ensure `gcloud auth application-default login` has been run.

- [ ] **Step 3: Verify fixture loads**

```bash
poetry run python -c "
from pathlib import Path
from backend.eval.custom_lyrics.runner import load_fixture
m = load_fixture(Path('backend/eval/custom_lyrics/fixtures/year-5-stars-shake-it-off'))
print(f'Lines: {len(m[\"original_lyrics\"])}')
print(f'Segments: {len(m[\"original_segments\"])}')
print(f'Client chars: {len(m[\"client_input\"])}')
"
```

Expected: matching counts (~70 lines, ~70 segments) and >0 client chars.

- [ ] **Step 4: Commit (fixture data + bootstrap script)**

```bash
git add backend/eval/custom_lyrics/_bootstrap_year5.py backend/eval/custom_lyrics/fixtures/year-5-stars-shake-it-off/
git commit -m "feat(eval): year-5-stars fixture bootstrapped from production job 2cb49a45"
```

---

### Task 25: Add 4 handcrafted fixtures

**Files:**
- Create: `backend/eval/custom_lyrics/fixtures/twinkle-name-swap/{metadata.json,original_lyrics.txt,original_segments.json,client_input.txt}`
- Create: `backend/eval/custom_lyrics/fixtures/happy-birthday-explicit-replace/{...}`
- Create: `backend/eval/custom_lyrics/fixtures/clementine-name-heavy/{...}`
- Create: `backend/eval/custom_lyrics/fixtures/row-row-line-surplus/{...}`

Source songs are public domain. Avoid copyrighted material.

For each fixture, follow the template:

- [ ] **Step 1: Build the fixture template**

For each of the 4 fixture directories, the structure is:

`metadata.json`:

```json
{
  "id": "<fixture-id>",
  "artist": "<traditional / public domain>",
  "title": "<song title>",
  "difficulty": "easy|medium|hard",
  "input_style": "<description of client input style>",
  "settings_to_test": [
    {"name": "default", "allow_reword": true, "allow_omit": true, "fixed_line_count": true, "strictness": "balanced"},
    {"name": "strict", "allow_reword": true, "allow_omit": true, "fixed_line_count": true, "strictness": "strict"}
  ]
}
```

`original_lyrics.txt`: one line per segment.

`original_segments.json`: synthetic timing — `[{"start_time": i * avg_dur, "end_time": (i+1) * avg_dur, "text": line, "words": []} for i, line in enumerate(lines)]` where `avg_dur` matches typical singing pace (e.g., 2.0s per line for a slow song; 1.2s for faster).

`client_input.txt`: handcrafted client input matching the `input_style`.

- [ ] **Step 2: Build `twinkle-name-swap` (easy)**

Source: "Twinkle Twinkle Little Star" (traditional, public domain). 8 short lines; client input asks "replace 'star' with 'Sam' throughout".

- [ ] **Step 3: Build `happy-birthday-explicit-replace` (easy)**

Source: "Happy Birthday" melody with public-domain alternate lyrics. 4 lines. Client input gives an explicit "replace [name] with Alex" instruction.

- [ ] **Step 4: Build `clementine-name-heavy` (hard)**

Source: "Oh My Darling Clementine" (traditional). ~20 lines. Client input is name-heavy ("celebrate the Smith family of seven, John, Mary, Bobby, Sue, ...") — many names to fit, mimicking Year-5-Stars complexity but on a public-domain song.

- [ ] **Step 5: Build `row-row-line-surplus` (medium)**

Source: "Row Row Row Your Boat" (traditional). 4 short lines. Client input is way more substantive than the song's structure can hold (e.g., a 20-line poem) — tests the line-surplus case.

- [ ] **Step 6: Commit fixtures**

```bash
git add backend/eval/custom_lyrics/fixtures/twinkle-name-swap/ backend/eval/custom_lyrics/fixtures/happy-birthday-explicit-replace/ backend/eval/custom_lyrics/fixtures/clementine-name-heavy/ backend/eval/custom_lyrics/fixtures/row-row-line-surplus/
git commit -m "feat(eval): 4 handcrafted fixtures (public-domain songs, varied difficulty)"
```

---

## Phase 8 — Capture baselines and validate v1-ships criteria

### Task 26: Capture `baseline-pre.json` against the OLD code

The "before" baseline is from the unmodified, pre-validator service. Easiest way: temporarily patch the new service to bypass the loop entirely, capture results, save baseline, revert.

- [ ] **Step 1: Stash current branch state**

```bash
git status
git stash push -u -m "wip: pre-baseline patch"
```

- [ ] **Step 2: Check out the pre-change code (the parent of the first refactor commit)**

```bash
git log --oneline | head -10
# Identify the commit hash just before "feat(syllable-counter): extract..."
git checkout <hash-before-task-1>
```

- [ ] **Step 3: Run eval against the legacy service path with default settings**

Because the legacy code uses `existing_lines`, the runner won't work directly. Easiest path:
- Build a tiny adapter script that mimics the legacy service interface, runs each fixture with default settings (single Gemini call, no validator), and captures outputs
- Score those outputs through the new scorer (which is on the new branch, so we'll need to manually score against the validator)

Practical alternative — capture pre-baseline by **patching the new service to act like the old one for one run**:

Skip to Step 4.

- [ ] **Step 4: Return to the new branch and capture pre-baseline via a one-shot config**

```bash
git checkout feat/sess-20260502-1444-custom-lyrics-syllables
git stash pop
```

Add a temporary `--legacy-mode` flag to `run.py` that forces every fixture's settings to `{"strictness": "verbatim"}` AND patches `_call_gemini` to wrap with a "skip the system prompt rules" version. Or — more honestly — just run the full corpus with `strictness=verbatim` settings, since Verbatim skips the validator/repair loop entirely. The single Gemini call with the original (v1-style) system-prompt-equivalent gives us a "what would the AI have produced without our enforcement" output. Score those outputs through the new scorer.

```bash
poetry run python -m backend.eval.custom_lyrics.run --settings verbatim --no-cache --save-as-baseline baseline-pre.json
```

This captures the unenforced output and scores it.

(The Verbatim path's prompt is closest to the v1 prompt: no per-line target metadata, no repair, no syllable-budget rules. It's not a perfect reproduction of pre-change behavior, but it's a fair lower-bound baseline.)

- [ ] **Step 5: Inspect the pre-baseline**

```bash
cat backend/eval/custom_lyrics/baseline-pre.json
```

Expected: `pct_pass_at_2` for `year-5-stars-shake-it-off` somewhere in the 0.20–0.50 range based on the empirical observation.

- [ ] **Step 6: Commit pre-baseline**

```bash
git add backend/eval/custom_lyrics/baseline-pre.json
git commit -m "feat(eval): capture baseline-pre.json (verbatim/unenforced output)"
```

---

### Task 27: Capture `baseline-post.json` and verify v1-ships criteria

- [ ] **Step 1: Run eval with default Balanced settings on full corpus**

```bash
poetry run python -m backend.eval.custom_lyrics.run --settings default --save-as-baseline baseline-post.json
```

Expected: significantly improved scores. Specifically check:
- `year-5-stars-shake-it-off` `pct_pass_at_2` ≥ 0.75
- Macro `pct_pass_at_2` across corpus ≥ 0.70

- [ ] **Step 2: Inspect baseline-post**

```bash
cat backend/eval/custom_lyrics/baseline-post.json | python -m json.tool
cat backend/eval/custom_lyrics/results/$(ls backend/eval/custom_lyrics/results | tail -1)/summary.md
```

- [ ] **Step 3: If criteria are NOT met, escalate**

If pass@±2 on Year 5 is < 75% or macro is < 70%:
- Capture the violation patterns from the per-fixture markdown reports
- Document the failure in this plan's task notes
- Triage:
  - If specific lines consistently fail → tune repair prompt for those classes
  - If LLM ignores per-line targets → strengthen system prompt rules
  - If multiple iterations don't help → consider chunking (out of scope; document as follow-up)

Iterate prompt fragments in `backend/services/custom_lyrics/prompts.py`, re-run with `--no-cache`, until criteria are met OR a fallback is documented.

- [ ] **Step 4: Once criteria are met, commit baseline-post and the final prompt state**

```bash
git add backend/eval/custom_lyrics/baseline-post.json backend/services/custom_lyrics/prompts.py
git commit -m "feat(eval): capture baseline-post.json meeting v1-ships criteria"
```

---

### Task 28: Capture human review notes for Year 5

- [ ] **Step 1: Open the per-fixture report for Year 5 default settings**

```bash
ls backend/eval/custom_lyrics/results/
# pick the latest
open backend/eval/custom_lyrics/results/<latest>/per_fixture/year-5-stars-shake-it-off__default/output.md
```

- [ ] **Step 2: Sing through (mentally or with the original audio)**

Operator (Andrew) reviews each line, marks the qualitative checklist, adds notes for any lines that fail subjective singability even though they pass syllable check.

- [ ] **Step 3: Save notes**

```bash
mkdir -p backend/eval/custom_lyrics/fixtures/year-5-stars-shake-it-off/human_review
cp backend/eval/custom_lyrics/results/<latest>/per_fixture/year-5-stars-shake-it-off__default/output.md \
   backend/eval/custom_lyrics/fixtures/year-5-stars-shake-it-off/human_review/$(date +%Y-%m-%d).md
# Edit the file to fill in the checklist and notes
```

- [ ] **Step 4: Commit**

```bash
git add backend/eval/custom_lyrics/fixtures/year-5-stars-shake-it-off/human_review/
git commit -m "eval(year-5-stars): human review notes for default-settings output"
```

---

## Phase 9 — Wire-up, version bump, docs, ship

### Task 29: Run full backend test suite

- [ ] **Step 1: Run pytest**

```bash
poetry run pytest backend/tests/ backend/eval/custom_lyrics/tests/ 2>&1 | tail -50
```

Expected: all tests pass.

- [ ] **Step 2: If any tests fail, investigate**

For unrelated test failures, check whether they are pre-existing on `main`:

```bash
git stash
git checkout main
poetry run pytest <failing_test> -v
git checkout -
git stash pop
```

If pre-existing on main → document and continue. If new → fix.

- [ ] **Step 3: No commit needed (no code changes); proceed to Task 30**

---

### Task 30: Run frontend test suite

- [ ] **Step 1: Run Jest**

```bash
cd frontend && npm test -- --watchAll=false 2>&1 | tail -30
```

Expected: all tests pass.

- [ ] **Step 2: Run TypeScript type check**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Run i18n parity check**

```bash
cd frontend && python scripts/translate.py --messages-dir ./messages --target all --dry-run
```

Expected: all locales up to date.

---

### Task 31: Bump version

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Find current version**

```bash
grep '^version' pyproject.toml
```

- [ ] **Step 2: Bump patch**

Edit `pyproject.toml`. Increment the patch version (e.g., `0.123.4` → `0.123.5`).

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: bump version for syllable-aware custom lyrics"
```

---

### Task 32: Update LESSONS-LEARNED

**Files:**
- Modify: `docs/LESSONS-LEARNED.md`

- [ ] **Step 1: Append a new lesson**

Add a section describing the validate-and-repair pattern: what it is, why we needed it (empirical: prompt-only enforcement was unreliable), how to apply it elsewhere (LLM workflows where output has measurable correctness), and the eval-harness-with-record-and-replay pattern. ~150 words.

- [ ] **Step 2: Commit**

```bash
git add docs/LESSONS-LEARNED.md
git commit -m "docs: lesson on validate-and-repair LLM pattern"
```

---

### Task 33: Run /test-review, /docs-review, /coderabbit, then /pr

Per the project's pre-PR checklist (see `~/.claude/CLAUDE.md`):

- [ ] **Step 1: `/test-review`** — verify test coverage is adequate (target ≥70%, key paths covered)
- [ ] **Step 2: `/docs-review`** — verify docs reflect new behavior; update `docs/README.md` if status warrants
- [ ] **Step 3: `/coderabbit`** — local review, fix issues (max 3 cycles), skip pure nitpicks
- [ ] **Step 4: `/pr`** — opens PR with `@coderabbitai ignore` baked in
- [ ] **Step 5: Wait for CI to pass, then merge**

PR description should reference:
- Spec: `docs/archive/2026-05-02-custom-lyrics-syllable-aware-design.md`
- Plan: `docs/archive/2026-05-02-custom-lyrics-syllable-aware-plan.md`
- Eval baselines: `backend/eval/custom_lyrics/baseline-pre.json` vs `backend/eval/custom_lyrics/baseline-post.json` (call out specific delta numbers in the PR body)

---

### Task 34: Post-deploy production smoke test

- [ ] **Step 1: Wait for backend deploy**

Cloud Run CI/CD takes ~5–10 minutes after merge. Watch deploy logs.

- [ ] **Step 2: Re-run Year 5 in production**

Use the existing job ID `2cb49a45`. Open the lyrics review modal, select Custom Lyrics mode, paste the same client input, click Generate with Balanced settings.

Expected: output significantly improved over the pre-change version. Specifically:
- Lines that previously had +3 to +5 syllables overflow are now within ±2
- The structurally salient lines (chorus repeats) still match (no regression)
- `iterations_used` badge shows ≥1 (loop was active)

- [ ] **Step 3: Sing through the result**

Manual check: does it actually fit the rhythm now? Capture qualitative impressions in a short followup note in `docs/archive/2026-05-02-custom-lyrics-post-deploy-notes.md`.

- [ ] **Step 4: Test other strictness levels in prod**

- Verbatim: should pass through more of the client's literal text, with no syllable badges flagged.
- Strict: should attempt up to 4 iterations; should produce ≤1 violations on Year 5.

- [ ] **Step 5: Commit any post-deploy notes**

```bash
git add docs/archive/2026-05-02-custom-lyrics-post-deploy-notes.md
git commit -m "docs: post-deploy notes for syllable-aware custom lyrics"
git push
```

---

## Self-review checklist

Before declaring this plan complete, walk through:

- [ ] Every spec section has at least one task that implements it (Phases 1–8 cover §SyllableCounter, §Settings, §Validate-and-repair, §Variable line count, §API, §Frontend, §Eval harness, §Falsifiability, §Rollout)
- [ ] No "TBD" / "TODO" / "fill in later" placeholders in any task body
- [ ] Function/method/class names are consistent across tasks (`SyllableCounter`, `GenerationSettings`, `validate`, `LineValidation`, `CustomLyricsService.generate`, `CustomLyricsResult`, `redistribute_timing_proportional`, `LlmCallCache`, `aggregate_fixture`)
- [ ] Each task ends with a commit
- [ ] Test code is shown verbatim (not "tests for this go here")
- [ ] Implementation code is shown verbatim
- [ ] Variable line count timing path is wired end-to-end (backend `redistribute_timing_proportional` → API `new_segment_timing` → frontend `segmentsFromLines({ redistributedTiming })`)
- [ ] i18n step (Task 19) covers all new keys

---

## Post-ship follow-ups (out of scope for v1)

These are documented in the spec's "Out of scope" section. Each gets its own plan when prioritised:

- Pre-flight feasibility check ("your input has X syllables; song fits Y")
- Stress / rhyme-aware validation
- Section-aware time redistribution for variable-line-count case
- Web UI for human review of eval outputs
- Wiring eval harness into PR CI with diff comments
- Per-line LLM repair fallback when whole-song repair plateaus
- Chunked generation for very long songs
- Saving custom-lyrics templates per client
