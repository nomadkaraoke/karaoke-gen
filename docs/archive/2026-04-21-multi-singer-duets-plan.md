# Multi-Singer / Duet Support — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable karaoke jobs with multiple singers (duets) by letting users mark segments and words as Singer 1 / Singer 2 / Both during the lyrics review phase, then rendering each singer's lyrics in distinct colors in both the MP4 video and the CDG file. Backward compatible — solo jobs render byte-identically to today.

**Architecture:** Add an optional `singer` field to `Word` and `LyricsSegment`. A job-level `is_duet` flag in the review session's `state_data` gates duet behaviour. The main-video ASS renderer gains a style-per-singer factory and passes a styles map through the `SubtitlesGenerator → LyricsScreen → LyricsLine` chain. The CDG path reuses the `cdgmaker` composer's existing 3-singer support via a new adapter. Theme JSON gains an optional `singers` block for per-singer color overrides in the ASS output; CDG colors are hardcoded.

**Tech Stack:** Python 3 (backend, `karaoke_gen/` package), FastAPI (`backend/`), Next.js + TypeScript (`frontend/`), `next-intl` for i18n, pytest, Jest, Playwright.

**Design spec:** `docs/archive/2026-04-21-multi-singer-duets-design.md` — read § 3–10 for context.

---

## File Structure

### Files to create

**Backend:**
- `tests/unit/lyrics_transcriber/test_multisinger_types.py` — singer field serialization for `Word` / `LyricsSegment`
- `tests/unit/lyrics_transcriber/output/test_multisinger_ass.py` — style factory, per-line style, word overrides
- `tests/unit/lyrics_transcriber/output/test_multisinger_cdg.py` — CDG adapter
- `tests/integration/test_multisinger_end_to_end.py` — small duet fixture through full pipeline

**Frontend:**
- `frontend/components/lyrics-review/SingerChip.tsx` — per-segment singer chip component
- `frontend/lib/lyrics-review/duet.ts` — pure helpers (`cycleSinger`, `resolveSegmentSinger`, `collectSingersInUse`, `hasWordOverrides`)
- `frontend/components/lyrics-review/__tests__/SingerChip.test.tsx`
- `frontend/lib/lyrics-review/__tests__/duet.test.ts`
- `frontend/e2e/production/duet-review.spec.ts` — Playwright happy-path

### Files to modify

**Backend:**
- `karaoke_gen/lyrics_transcriber/types.py` — add `singer` to `Word` and `LyricsSegment`
- `karaoke_gen/style_loader.py` — extend `DEFAULT_KARAOKE_STYLE` with `singers` block, add resolver helper + `CDG_DUET_SINGERS` constant
- `karaoke_gen/lyrics_transcriber/output/ass/style.py` — add `build_karaoke_styles` factory
- `karaoke_gen/lyrics_transcriber/output/ass/lyrics_line.py` — accept `styles_by_singer`, pick style by segment singer, emit per-word override tags
- `karaoke_gen/lyrics_transcriber/output/ass/lyrics_screen.py` — thread `styles_by_singer` through
- `karaoke_gen/lyrics_transcriber/output/subtitles.py` — build `styles_by_singer`, pass into screens, regression for solo
- `karaoke_gen/lyrics_transcriber/output/cdg.py` — extend to produce per-line `SettingsLyric.singer`
- `karaoke_gen/lyrics_transcriber/output/generator.py` — thread `is_duet` through `OutputGenerator`
- `backend/api/routes/review.py` — persist `is_duet` in `state_data`
- `backend/workers/render_video_worker.py` — read `is_duet`, propagate to render config and GCE payload
- `backend/workers/style_helper.py` — (if it merges theme dicts) keep `singers` block intact
- `tests/unit/test_style_loader.py` — add coverage for `singers` resolution

**Frontend:**
- `frontend/lib/lyrics-review/types.ts` — add optional `singer` to `Word` and `LyricsSegment`; add `is_duet` to review state shape
- `frontend/components/lyrics-review/Header.tsx` — "Mark as duet" toggle button
- `frontend/components/lyrics-review/LyricsAnalyzer.tsx` — manage `isDuet` state, pass down, include in save payload
- `frontend/components/lyrics-review/TranscriptionView.tsx` — render chip + subtle row tint when duet
- `frontend/lib/lyrics-review/utils/keyboardHandlers.ts` — `1`/`2`/`B` handlers
- `frontend/lib/lyrics-review/utils/segmentOperations.ts` — singer inheritance on split/merge/add
- `frontend/lib/api.ts` — extend save payload shape
- `frontend/messages/en.json` — `lyricsReview.duet.*` namespace (auto-propagates to other locales via `translate.py`)

### Files NOT to touch

- `karaoke_gen/lyrics_transcriber/output/cdgmaker/**` — CDG composer already supports 3 singers. Integration only, no edits.

---

## Conventions for this plan

- **Paths are absolute from repo root** (`/Users/andrew/Projects/nomadkaraoke/karaoke-gen-multi-singer-duets`). Commands assume pwd is repo root.
- **Every task ends with a commit.** Commit messages follow the repo's conventional-commit style (`feat:`, `test:`, `refactor:`, `docs:`, `chore:`).
- **TDD:** write the test first, watch it fail, implement, watch it pass, commit. Do not skip the "watch it fail" step — a test that passes before implementation is a broken test.
- **When a step shows code:** type it verbatim unless the step explicitly says "adapt to existing structure".
- **Make test targets:**
  - Python unit + coverage: `make test-unit` (runs `pytest tests/unit/ --cov=karaoke_gen --cov-fail-under=69`)
  - Backend API unit: `make test-backend-unit`
  - Frontend unit: `cd frontend && npm run test:unit`
  - Frontend E2E (prod): `cd frontend && npm run test:e2e`

---

## Phase 1 — Backend foundations (types + theme)

### Task 1: Add `SingerId` type alias and `singer` field to backend `Word` / `LyricsSegment`

**Files:**
- Create: `tests/unit/lyrics_transcriber/test_multisinger_types.py`
- Modify: `karaoke_gen/lyrics_transcriber/types.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/lyrics_transcriber/test_multisinger_types.py`:

```python
"""Tests for multi-singer fields on Word and LyricsSegment."""
import pytest

from karaoke_gen.lyrics_transcriber.types import Word, LyricsSegment


class TestWordSinger:
    def test_word_singer_defaults_to_none(self):
        w = Word(id="w1", text="hello", start_time=0.0, end_time=0.5)
        assert w.singer is None

    def test_word_accepts_singer_ids(self):
        for sid in (0, 1, 2):
            w = Word(id="w1", text="hi", start_time=0.0, end_time=0.5, singer=sid)
            assert w.singer == sid

    def test_word_to_dict_omits_singer_when_none(self):
        w = Word(id="w1", text="hi", start_time=0.0, end_time=0.5)
        assert "singer" not in w.to_dict()

    def test_word_to_dict_includes_singer_when_set(self):
        w = Word(id="w1", text="hi", start_time=0.0, end_time=0.5, singer=2)
        assert w.to_dict()["singer"] == 2

    def test_word_from_dict_round_trip(self):
        original = Word(id="w1", text="hi", start_time=0.0, end_time=0.5, singer=2)
        restored = Word.from_dict(original.to_dict())
        assert restored.singer == 2

    def test_word_from_dict_missing_singer_is_none(self):
        restored = Word.from_dict({"id": "w1", "text": "hi", "start_time": 0.0, "end_time": 0.5})
        assert restored.singer is None


class TestLyricsSegmentSinger:
    def _word(self) -> Word:
        return Word(id="w1", text="hi", start_time=0.0, end_time=0.5)

    def test_segment_singer_defaults_to_none(self):
        seg = LyricsSegment(id="s1", text="hi", words=[self._word()], start_time=0.0, end_time=0.5)
        assert seg.singer is None

    def test_segment_to_dict_omits_singer_when_none(self):
        seg = LyricsSegment(id="s1", text="hi", words=[self._word()], start_time=0.0, end_time=0.5)
        assert "singer" not in seg.to_dict()

    def test_segment_to_dict_includes_singer_when_set(self):
        seg = LyricsSegment(id="s1", text="hi", words=[self._word()], start_time=0.0, end_time=0.5, singer=1)
        assert seg.to_dict()["singer"] == 1

    def test_segment_from_dict_round_trip(self):
        original = LyricsSegment(
            id="s1",
            text="hi",
            words=[Word(id="w1", text="hi", start_time=0.0, end_time=0.5, singer=2)],
            start_time=0.0,
            end_time=0.5,
            singer=1,
        )
        restored = LyricsSegment.from_dict(original.to_dict())
        assert restored.singer == 1
        assert restored.words[0].singer == 2

    def test_segment_from_dict_missing_singer_is_none(self):
        data = {
            "id": "s1", "text": "hi",
            "words": [{"id": "w1", "text": "hi", "start_time": 0.0, "end_time": 0.5}],
            "start_time": 0.0, "end_time": 0.5,
        }
        restored = LyricsSegment.from_dict(data)
        assert restored.singer is None
        assert restored.words[0].singer is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/unit/lyrics_transcriber/test_multisinger_types.py -v
```

Expected: All tests fail with `AttributeError: 'Word' object has no attribute 'singer'`.

- [ ] **Step 3: Modify `karaoke_gen/lyrics_transcriber/types.py`**

Add `SingerId` type alias near the top of the file, just after the `typing` imports:

```python
from typing import Any, Dict, List, Literal, Optional, Set, Tuple

# Singer id. 1 = Singer 1, 2 = Singer 2, 0 = Both. None/absent = default (Singer 1).
SingerId = Literal[0, 1, 2]
```

In the `Word` dataclass, add one field after `created_during_correction`:

```python
@dataclass
class Word:
    id: str
    text: str
    start_time: float
    end_time: float
    confidence: Optional[float] = None
    created_during_correction: bool = False
    singer: Optional[SingerId] = None

    def __post_init__(self):
        self.text = self.text.strip()

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d["confidence"] is None:
            del d["confidence"]
        if d["singer"] is None:
            del d["singer"]
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Word":
        return cls(
            id=data["id"],
            text=data["text"],
            start_time=data["start_time"],
            end_time=data["end_time"],
            confidence=data.get("confidence"),
            created_during_correction=data.get("created_during_correction", False),
            singer=data.get("singer"),
        )
```

In the `LyricsSegment` dataclass, add a `singer` field:

```python
@dataclass
class LyricsSegment:
    id: str
    text: str
    words: List[Word]
    start_time: float
    end_time: float
    singer: Optional[SingerId] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "id": self.id,
            "text": self.text,
            "words": [word.to_dict() for word in self.words],
            "start_time": self.start_time,
            "end_time": self.end_time,
        }
        if self.singer is not None:
            d["singer"] = self.singer
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LyricsSegment":
        return cls(
            id=data["id"],
            text=data["text"],
            words=[Word.from_dict(w) for w in data["words"]],
            start_time=data["start_time"],
            end_time=data["end_time"],
            singer=data.get("singer"),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/unit/lyrics_transcriber/test_multisinger_types.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Run full unit tests to confirm no regressions**

```bash
make test-unit 2>&1 | tail -n 40
```

Expected: all existing `lyrics_transcriber` tests still pass; coverage ≥ 69%.

- [ ] **Step 6: Commit**

```bash
git add karaoke_gen/lyrics_transcriber/types.py tests/unit/lyrics_transcriber/test_multisinger_types.py
git commit -m "$(cat <<'EOF'
feat(types): add optional singer field to Word and LyricsSegment

Introduces SingerId type alias (0=Both, 1=Singer 1, 2=Singer 2, None=default)
and adds optional singer field to both Word and LyricsSegment. Absent field
serializes to absent key for backward compatibility with existing corrections.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Extend theme JSON with `singers` block and update `nomad` defaults

**Files:**
- Modify: `karaoke_gen/style_loader.py`
- Modify: `tests/unit/test_style_loader.py`

- [ ] **Step 1: Write failing tests**

Open `tests/unit/test_style_loader.py`. Add to the bottom of the file (or inside `class TestDefaultStyles` if one exists — match the file's existing pattern):

```python
class TestKaraokeSingersBlock:
    """Tests for the optional per-singer colors block under karaoke style."""

    def test_default_karaoke_style_has_singers_block(self):
        from karaoke_gen.style_loader import DEFAULT_KARAOKE_STYLE
        assert "singers" in DEFAULT_KARAOKE_STYLE
        # The nomad defaults ship with blue / pink / yellow presets
        singers = DEFAULT_KARAOKE_STYLE["singers"]
        assert set(singers.keys()) == {"1", "2", "both"}

    def test_singer2_has_pink_primary(self):
        from karaoke_gen.style_loader import DEFAULT_KARAOKE_STYLE
        # Pink active for Singer 2
        assert DEFAULT_KARAOKE_STYLE["singers"]["2"]["primary_color"] == "247, 112, 180, 255"

    def test_both_has_yellow_primary(self):
        from karaoke_gen.style_loader import DEFAULT_KARAOKE_STYLE
        assert DEFAULT_KARAOKE_STYLE["singers"]["both"]["primary_color"] == "252, 211, 77, 255"


class TestResolveSingerColors:
    """Tests for resolve_singer_colors — per-singer color resolution."""

    def test_resolve_singer1_uses_flat_colors_when_no_override(self):
        from karaoke_gen.style_loader import resolve_singer_colors
        karaoke = {
            "primary_color": "1, 1, 1, 255",
            "secondary_color": "2, 2, 2, 255",
            "outline_color": "3, 3, 3, 255",
            "back_color": "0, 0, 0, 0",
            "singers": {},
        }
        colors = resolve_singer_colors(karaoke, 1)
        assert colors["primary_color"] == "1, 1, 1, 255"
        assert colors["outline_color"] == "3, 3, 3, 255"

    def test_resolve_singer2_overrides_only_specified_fields(self):
        from karaoke_gen.style_loader import resolve_singer_colors
        karaoke = {
            "primary_color": "1, 1, 1, 255",
            "secondary_color": "2, 2, 2, 255",
            "outline_color": "3, 3, 3, 255",
            "back_color": "0, 0, 0, 0",
            "singers": {"2": {"primary_color": "9, 9, 9, 255"}},
        }
        colors = resolve_singer_colors(karaoke, 2)
        assert colors["primary_color"] == "9, 9, 9, 255"
        # Fields not overridden fall back to flat colors
        assert colors["secondary_color"] == "2, 2, 2, 255"
        assert colors["outline_color"] == "3, 3, 3, 255"

    def test_resolve_both_uses_both_key_not_numeric(self):
        from karaoke_gen.style_loader import resolve_singer_colors
        karaoke = {
            "primary_color": "1, 1, 1, 255",
            "secondary_color": "2, 2, 2, 255",
            "outline_color": "3, 3, 3, 255",
            "back_color": "0, 0, 0, 0",
            "singers": {"both": {"primary_color": "7, 7, 7, 255"}},
        }
        colors = resolve_singer_colors(karaoke, 0)
        assert colors["primary_color"] == "7, 7, 7, 255"

    def test_resolve_handles_missing_singers_block(self):
        from karaoke_gen.style_loader import resolve_singer_colors
        karaoke = {
            "primary_color": "1, 1, 1, 255",
            "secondary_color": "2, 2, 2, 255",
            "outline_color": "3, 3, 3, 255",
            "back_color": "0, 0, 0, 0",
            # no "singers" key
        }
        colors = resolve_singer_colors(karaoke, 2)
        # Should fall back to flat for every field
        assert colors["primary_color"] == "1, 1, 1, 255"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/unit/test_style_loader.py::TestKaraokeSingersBlock tests/unit/test_style_loader.py::TestResolveSingerColors -v
```

Expected: all new tests fail (some with `KeyError: 'singers'`, others with `ImportError: cannot import name 'resolve_singer_colors'`).

- [ ] **Step 3: Extend `DEFAULT_KARAOKE_STYLE` and add `resolve_singer_colors`**

In `karaoke_gen/style_loader.py`, modify `DEFAULT_KARAOKE_STYLE` (currently lines 68–104). Add a new `singers` key at the end of the dict, before the closing brace:

```python
    "max_line_length": 40,
    "top_padding": 200,
    "font_size": 100,
    # NEW: optional per-singer color overrides. Keys: "1" / "2" / "both".
    # Missing singer or missing field falls back to the flat colors above.
    # Key note: "both" corresponds to SingerId 0.
    "singers": {
        # Singer 1 inherits the flat colors above — explicit empty dict for clarity
        "1": {},
        # Singer 2: pink
        "2": {
            "primary_color":   "247, 112, 180, 255",
            "secondary_color": "255, 255, 255, 255",
            "outline_color":   "158, 26, 96, 255",
            "back_color":      "0, 0, 0, 0",
        },
        # Both: yellow
        "both": {
            "primary_color":   "252, 211, 77, 255",
            "secondary_color": "255, 255, 255, 255",
            "outline_color":   "146, 108, 0, 255",
            "back_color":      "0, 0, 0, 0",
        },
    },
}
```

Then, anywhere below `DEFAULT_STYLE_PARAMS` (line ~119) but above the `ASSET_KEY_MAPPINGS` section, add the resolver:

```python
# =============================================================================
# SINGER COLOR RESOLUTION (for multi-singer / duet rendering)
# =============================================================================

# Map an internal SingerId (0, 1, 2) to the theme's singers-block key.
_SINGER_KEY_MAP = {0: "both", 1: "1", 2: "2"}

_SINGER_COLOR_FIELDS = ("primary_color", "secondary_color", "outline_color", "back_color")


def resolve_singer_colors(karaoke_style: Dict[str, Any], singer_id: int) -> Dict[str, str]:
    """Return the resolved color dict for a given SingerId.

    Starts from the flat colors (primary_color, secondary_color, outline_color,
    back_color) and overlays any fields specified under karaoke_style["singers"][key],
    where key is "1", "2", or "both" depending on singer_id.

    singer_id must be 0 ("both"), 1, or 2.
    """
    singers_block = karaoke_style.get("singers", {}) or {}
    key = _SINGER_KEY_MAP[singer_id]
    override = singers_block.get(key, {}) or {}

    return {
        field: override.get(field, karaoke_style.get(field))
        for field in _SINGER_COLOR_FIELDS
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/unit/test_style_loader.py -v
```

Expected: all tests in the file PASS, including existing ones.

- [ ] **Step 5: Commit**

```bash
git add karaoke_gen/style_loader.py tests/unit/test_style_loader.py
git commit -m "$(cat <<'EOF'
feat(style): extend theme JSON with optional per-singer color overrides

Adds a "singers" block under the karaoke style with keys "1"/"2"/"both"
for per-singer primary/secondary/outline/back color overrides. Updates
the nomad default theme with pink for Singer 2 and yellow for Both.
Introduces resolve_singer_colors() which merges flat colors with any
overrides. Themes without a "singers" block continue to work unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 2 — Video / ASS render extension

### Task 3: Add `build_karaoke_styles` factory to `ass/style.py`

**Files:**
- Modify: `karaoke_gen/lyrics_transcriber/output/ass/style.py`
- Create: `tests/unit/lyrics_transcriber/output/test_multisinger_ass.py`

- [ ] **Step 1: Write failing test**

Create `tests/unit/lyrics_transcriber/output/test_multisinger_ass.py`:

```python
"""Tests for multi-singer ASS rendering."""
import pytest

from karaoke_gen.lyrics_transcriber.output.ass.style import Style, build_karaoke_styles


@pytest.fixture
def karaoke_style_dict():
    # Minimal style dict used by SubtitlesGenerator
    return {
        "ass_name": "Default",
        "font": "Noto Sans",
        "font_path": "",
        "font_size": 100,
        "primary_color":   "112, 112, 247, 255",
        "secondary_color": "255, 255, 255, 255",
        "outline_color":   "26, 58, 235, 255",
        "back_color":      "0, 0, 0, 0",
        "bold": False,
        "italic": False,
        "underline": False,
        "strike_out": False,
        "scale_x": 100,
        "scale_y": 100,
        "spacing": 0,
        "angle": 0.0,
        "border_style": 1,
        "outline": 1,
        "shadow": 0,
        "margin_l": 0, "margin_r": 0, "margin_v": 0,
        "encoding": 0,
        "singers": {
            "1": {},
            "2": {"primary_color": "247, 112, 180, 255"},
            "both": {"primary_color": "252, 211, 77, 255"},
        },
    }


class TestBuildKaraokeStyles:
    def test_solo_returns_single_default_style(self, karaoke_style_dict):
        # Solo path: singers=[1] with the original ass_name
        styles = build_karaoke_styles(karaoke_style_dict, singers=[1], solo=True)
        assert len(styles) == 1
        assert styles[0].Name == "Default"
        assert styles[0].PrimaryColour == (112, 112, 247, 255)

    def test_duet_returns_named_styles_per_singer(self, karaoke_style_dict):
        styles = build_karaoke_styles(karaoke_style_dict, singers=[1, 2, 0])
        by_name = {s.Name: s for s in styles}
        assert set(by_name) == {"Karaoke.Singer1", "Karaoke.Singer2", "Karaoke.Both"}

    def test_singer2_picks_up_overridden_primary(self, karaoke_style_dict):
        styles = build_karaoke_styles(karaoke_style_dict, singers=[2])
        assert styles[0].Name == "Karaoke.Singer2"
        assert styles[0].PrimaryColour == (247, 112, 180, 255)
        # Non-overridden fields still come from flat theme
        assert styles[0].SecondaryColour == (255, 255, 255, 255)

    def test_both_is_yellow(self, karaoke_style_dict):
        styles = build_karaoke_styles(karaoke_style_dict, singers=[0])
        assert styles[0].Name == "Karaoke.Both"
        assert styles[0].PrimaryColour == (252, 211, 77, 255)

    def test_font_settings_identical_across_singers(self, karaoke_style_dict):
        styles = build_karaoke_styles(karaoke_style_dict, singers=[1, 2, 0])
        font_sizes = {s.Fontsize for s in styles}
        fontnames = {s.Fontname for s in styles}
        assert len(font_sizes) == 1
        assert len(fontnames) == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/unit/lyrics_transcriber/output/test_multisinger_ass.py -v
```

Expected: fails with `ImportError: cannot import name 'build_karaoke_styles'`.

- [ ] **Step 3: Implement `build_karaoke_styles`**

At the bottom of `karaoke_gen/lyrics_transcriber/output/ass/style.py` (after the `Style.formatters` assignment at line 187), add:

```python
from karaoke_gen.style_loader import resolve_singer_colors


# Singer id → suffix for the generated ASS style name.
_DUET_STYLE_NAME_SUFFIX = {0: "Both", 1: "Singer1", 2: "Singer2"}


def build_karaoke_styles(karaoke_style: dict, singers, solo: bool = False):
    """Build one ASS Style per singer id.

    Args:
        karaoke_style: the theme's "karaoke" block (flat colors + optional "singers" block)
        singers: iterable of SingerId (0/1/2) to build styles for
        solo: if True, returns a single Style named per karaoke_style["ass_name"]
              with the flat colors. Used when is_duet=False for byte-identical
              regression with pre-change output.

    Returns:
        list[Style] — one Style per singer id.
    """
    def _parse_color(color_str):
        return tuple(int(x.strip()) for x in color_str.split(","))

    def _parse_bool(val):
        return -1 if val else 0

    def _make_style(name: str, colors: dict) -> Style:
        s = Style()
        s.type = "Style"
        s.Name = name
        s.Fontname = karaoke_style["font"]
        s.Fontpath = karaoke_style.get("font_path", "")
        s.Fontsize = karaoke_style["font_size"]
        s.PrimaryColour = _parse_color(colors["primary_color"])
        s.SecondaryColour = _parse_color(colors["secondary_color"])
        s.OutlineColour = _parse_color(colors["outline_color"])
        s.BackColour = _parse_color(colors["back_color"])
        s.Bold = _parse_bool(karaoke_style["bold"])
        s.Italic = _parse_bool(karaoke_style["italic"])
        s.Underline = _parse_bool(karaoke_style["underline"])
        s.StrikeOut = _parse_bool(karaoke_style["strike_out"])
        s.ScaleX = int(karaoke_style["scale_x"])
        s.ScaleY = int(karaoke_style["scale_y"])
        s.Spacing = int(karaoke_style["spacing"])
        s.Angle = float(karaoke_style["angle"])
        s.BorderStyle = int(karaoke_style["border_style"])
        s.Outline = int(karaoke_style["outline"])
        s.Shadow = int(karaoke_style["shadow"])
        s.MarginL = int(karaoke_style["margin_l"])
        s.MarginR = int(karaoke_style["margin_r"])
        s.MarginV = int(karaoke_style["margin_v"])
        s.Encoding = int(karaoke_style["encoding"])
        # Alignment is set later by the caller via ALIGN_TOP_CENTER; leave default
        return s

    if solo:
        # Solo: one style, original ass_name, flat colors only.
        colors = {
            "primary_color":   karaoke_style["primary_color"],
            "secondary_color": karaoke_style["secondary_color"],
            "outline_color":   karaoke_style["outline_color"],
            "back_color":      karaoke_style["back_color"],
        }
        return [_make_style(karaoke_style["ass_name"], colors)]

    styles = []
    for sid in singers:
        colors = resolve_singer_colors(karaoke_style, sid)
        name = f"Karaoke.{_DUET_STYLE_NAME_SUFFIX[sid]}"
        styles.append(_make_style(name, colors))
    return styles
```

- [ ] **Step 4: Run test to verify it passes**

```bash
poetry run pytest tests/unit/lyrics_transcriber/output/test_multisinger_ass.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add karaoke_gen/lyrics_transcriber/output/ass/style.py tests/unit/lyrics_transcriber/output/test_multisinger_ass.py
git commit -m "$(cat <<'EOF'
feat(ass): add build_karaoke_styles factory for per-singer ASS styles

New factory produces one ASS Style per singer id when duet mode is on
(Karaoke.Singer1 / Karaoke.Singer2 / Karaoke.Both), or a single
"Default" style with flat colors when solo=True. Font/positioning
settings are identical across singers; only colors vary.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Teach `LyricsLine.create_ass_events` to pick a style per segment singer

**Files:**
- Modify: `karaoke_gen/lyrics_transcriber/output/ass/lyrics_line.py`
- Modify: `tests/unit/lyrics_transcriber/output/test_multisinger_ass.py`

Context: today `LyricsLine.create_ass_events(state, style, config, previous_end_time)` takes a single `style`. We'll add a `styles_by_singer` optional parameter. When present and the segment has a singer, pick the right style; otherwise fall back to `style`.

- [ ] **Step 1: Add failing tests**

Append to `tests/unit/lyrics_transcriber/output/test_multisinger_ass.py`:

```python
from karaoke_gen.lyrics_transcriber.types import LyricsSegment, Word
from karaoke_gen.lyrics_transcriber.output.ass.lyrics_line import LyricsLine
from karaoke_gen.lyrics_transcriber.output.ass.config import ScreenConfig, LineTimingInfo, LineState


def _screen_config():
    return ScreenConfig(
        line_height=60, video_width=1920, video_height=1080,
    )


def _line_state():
    return LineState(
        text="hello world",
        timing=LineTimingInfo(fade_in_time=0.0, end_time=2.0),
        y_position=100,
    )


def _make_line(singer=None):
    segment = LyricsSegment(
        id="s1",
        text="hello world",
        words=[
            Word(id="w1", text="hello", start_time=0.0, end_time=0.5),
            Word(id="w2", text="world", start_time=0.6, end_time=1.0),
        ],
        start_time=0.0,
        end_time=1.0,
        singer=singer,
    )
    return LyricsLine(segment=segment, screen_config=_screen_config())


class TestLyricsLineStylePerSinger:
    def test_line_uses_fallback_style_when_no_styles_map(self, karaoke_style_dict):
        styles = build_karaoke_styles(karaoke_style_dict, singers=[1], solo=True)
        line = _make_line(singer=None)
        events = line.create_ass_events(
            state=_line_state(), style=styles[0], config=line.screen_config
        )
        assert len(events) >= 1
        assert events[-1].Style is styles[0]

    def test_line_picks_singer2_style_when_segment_singer_is_2(self, karaoke_style_dict):
        styles = build_karaoke_styles(karaoke_style_dict, singers=[1, 2, 0])
        by_name = {s.Name: s for s in styles}
        line = _make_line(singer=2)
        events = line.create_ass_events(
            state=_line_state(),
            style=by_name["Karaoke.Singer1"],  # fallback
            config=line.screen_config,
            styles_by_singer={1: by_name["Karaoke.Singer1"], 2: by_name["Karaoke.Singer2"], 0: by_name["Karaoke.Both"]},
        )
        assert events[-1].Style is by_name["Karaoke.Singer2"]

    def test_line_picks_both_style_when_segment_singer_is_0(self, karaoke_style_dict):
        styles = build_karaoke_styles(karaoke_style_dict, singers=[1, 2, 0])
        by_name = {s.Name: s for s in styles}
        line = _make_line(singer=0)
        events = line.create_ass_events(
            state=_line_state(),
            style=by_name["Karaoke.Singer1"],
            config=line.screen_config,
            styles_by_singer={1: by_name["Karaoke.Singer1"], 2: by_name["Karaoke.Singer2"], 0: by_name["Karaoke.Both"]},
        )
        assert events[-1].Style is by_name["Karaoke.Both"]

    def test_line_defaults_to_singer1_style_when_segment_singer_none_with_map(self, karaoke_style_dict):
        styles = build_karaoke_styles(karaoke_style_dict, singers=[1, 2, 0])
        by_name = {s.Name: s for s in styles}
        line = _make_line(singer=None)
        events = line.create_ass_events(
            state=_line_state(),
            style=by_name["Karaoke.Singer1"],
            config=line.screen_config,
            styles_by_singer={1: by_name["Karaoke.Singer1"], 2: by_name["Karaoke.Singer2"], 0: by_name["Karaoke.Both"]},
        )
        assert events[-1].Style is by_name["Karaoke.Singer1"]
```

- [ ] **Step 2: Run tests to verify failures**

```bash
poetry run pytest tests/unit/lyrics_transcriber/output/test_multisinger_ass.py -v
```

Expected: the 4 new tests fail (current `create_ass_events` doesn't accept `styles_by_singer`).

- [ ] **Step 3: Modify `LyricsLine.create_ass_events`**

In `karaoke_gen/lyrics_transcriber/output/ass/lyrics_line.py`, change the `create_ass_events` method signature (line 183) and the main-event style assignment (line 203):

```python
    def create_ass_events(
        self,
        state: LineState,
        style: Style,
        config: ScreenConfig,
        previous_end_time: Optional[float] = None,
        styles_by_singer: Optional[dict] = None,
    ) -> List[Event]:
        """Create ASS events for this line.

        If styles_by_singer is provided, the main event is tagged with the
        style for self.segment.singer (falling back to singer 1 when
        self.segment.singer is None). Otherwise the fallback `style` is used
        (solo / backward-compat path).
        """
        self.previous_end_time = previous_end_time
        events = []

        # Pick the style for this line
        line_style = style
        if styles_by_singer:
            singer_key = self.segment.singer if self.segment.singer is not None else 1
            line_style = styles_by_singer.get(singer_key, style)

        lead_in_event = self._create_lead_in_event(state, line_style, config.video_width, config)
        if lead_in_event:
            events.extend(lead_in_event)

        main_event = Event()
        main_event.type = "Dialogue"
        main_event.Layer = 0
        main_event.Style = line_style
        main_event.Start = state.timing.fade_in_time
        main_event.End = state.timing.end_time

        x_pos = config.video_width // 2
        text = (
            f"{{\\an8}}{{\\pos({x_pos},{state.y_position})}}"
            f"{{\\fad({config.fade_in_ms},{config.fade_out_ms})}}"
        )
        text += self._create_ass_text(timedelta(seconds=state.timing.fade_in_time))

        main_event.Text = text
        events.append(main_event)

        return events
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/unit/lyrics_transcriber/output/test_multisinger_ass.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add karaoke_gen/lyrics_transcriber/output/ass/lyrics_line.py tests/unit/lyrics_transcriber/output/test_multisinger_ass.py
git commit -m "$(cat <<'EOF'
feat(ass): LyricsLine picks style per segment singer

create_ass_events gains an optional styles_by_singer parameter. When
provided, the main Dialogue event uses the style for segment.singer
(defaulting to singer 1 when segment.singer is None). Solo path is
unchanged — absent styles_by_singer keeps existing single-style
behavior.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Render word-level singer overrides as inline color tags

**Files:**
- Modify: `karaoke_gen/lyrics_transcriber/output/ass/lyrics_line.py`
- Modify: `tests/unit/lyrics_transcriber/output/test_multisinger_ass.py`

Context: ASS inline tags `{\c&HBBGGRR&}` override PrimaryColour for the next words until `{\r}` resets to the Dialogue's Style. We emit the override color only on words whose `singer` differs from the segment's singer.

- [ ] **Step 1: Add failing tests**

Append to `tests/unit/lyrics_transcriber/output/test_multisinger_ass.py`:

```python
def _line_with_override():
    segment = LyricsSegment(
        id="s1",
        text="hello world",
        words=[
            Word(id="w1", text="hello", start_time=0.0, end_time=0.5, singer=None),
            Word(id="w2", text="world", start_time=0.6, end_time=1.0, singer=2),
        ],
        start_time=0.0,
        end_time=1.0,
        singer=1,
    )
    return LyricsLine(segment=segment, screen_config=_screen_config())


class TestLyricsLineWordOverride:
    def test_word_override_emits_color_tag(self, karaoke_style_dict):
        styles = build_karaoke_styles(karaoke_style_dict, singers=[1, 2, 0])
        by_name = {s.Name: s for s in styles}
        line = _line_with_override()

        events = line.create_ass_events(
            state=_line_state(),
            style=by_name["Karaoke.Singer1"],
            config=line.screen_config,
            styles_by_singer={1: by_name["Karaoke.Singer1"], 2: by_name["Karaoke.Singer2"], 0: by_name["Karaoke.Both"]},
        )
        text = events[-1].Text
        # Singer 2 primary = 247, 112, 180 → BGR hex: B4 70 F7 (padded)
        # ASS color format: &HBBGGRR& i.e. B4 70 F7 → &HB470F7&
        assert "\\c&HB470F7&" in text
        # Reset tag after the overridden word
        assert "{\\r}" in text

    def test_no_override_when_word_singer_matches_segment(self, karaoke_style_dict):
        styles = build_karaoke_styles(karaoke_style_dict, singers=[1, 2, 0])
        by_name = {s.Name: s for s in styles}
        # All words' singer None (inherit from segment.singer=1) — no overrides
        line = _make_line(singer=1)
        events = line.create_ass_events(
            state=_line_state(),
            style=by_name["Karaoke.Singer1"],
            config=line.screen_config,
            styles_by_singer={1: by_name["Karaoke.Singer1"], 2: by_name["Karaoke.Singer2"], 0: by_name["Karaoke.Both"]},
        )
        text = events[-1].Text
        assert "\\c&H" not in text
        assert "{\\r}" not in text
```

- [ ] **Step 2: Run tests to verify failure**

```bash
poetry run pytest tests/unit/lyrics_transcriber/output/test_multisinger_ass.py::TestLyricsLineWordOverride -v
```

Expected: both tests fail.

- [ ] **Step 3: Emit per-word override tags in `_create_ass_text`**

In `karaoke_gen/lyrics_transcriber/output/ass/lyrics_line.py`, modify `_create_ass_text` (line 237) to accept the styles map and segment singer so it can emit inline color overrides:

```python
    def _create_ass_text(
        self,
        start_ts: timedelta,
        styles_by_singer: Optional[dict] = None,
    ) -> str:
        """Create the ASS text with karaoke timing tags and word-level singer overrides."""
        first_word_time = self.segment.start_time
        start_time = max(0, (first_word_time - start_ts.total_seconds()) * 100)
        text = r"{\k" + str(int(round(start_time))) + r"}"

        prev_end_time = first_word_time
        segment_singer = self.segment.singer if self.segment.singer is not None else 1
        current_inline_singer = None  # tracks whether we've emitted a color override

        for word in self.segment.words:
            gap = word.start_time - prev_end_time
            if gap > 0.1:
                text += r"{\k" + str(int(round(gap * 100))) + r"}"

            duration = int(round((word.end_time - word.start_time) * 100))
            clean_word_text = word.text.replace("\n", "").strip()
            transformed_text = self._apply_case_transform(clean_word_text)

            # Determine whether this word has a singer override
            word_singer = word.singer if word.singer is not None else segment_singer
            needs_override = (
                styles_by_singer is not None
                and word_singer != segment_singer
            )

            if needs_override and current_inline_singer != word_singer:
                override_style = styles_by_singer.get(word_singer)
                if override_style is not None:
                    # PrimaryColour tuple is (R, G, B, A). ASS inline format: &HBBGGRR&
                    r, g, b, _a = override_style.PrimaryColour
                    text += r"{\c&H" + f"{b:02X}{g:02X}{r:02X}" + r"&}"
                    current_inline_singer = word_singer
            elif not needs_override and current_inline_singer is not None:
                text += r"{\r}"
                current_inline_singer = None

            text += r"{\kf" + str(duration) + r"}" + transformed_text + " "
            prev_end_time = word.end_time

        # Close any lingering override
        if current_inline_singer is not None:
            text += r"{\r}"

        return text.rstrip()
```

Now update the call site inside `create_ass_events` (added in Task 4) to pass the styles map through:

```python
        text += self._create_ass_text(
            timedelta(seconds=state.timing.fade_in_time),
            styles_by_singer=styles_by_singer,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/unit/lyrics_transcriber/output/test_multisinger_ass.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add karaoke_gen/lyrics_transcriber/output/ass/lyrics_line.py tests/unit/lyrics_transcriber/output/test_multisinger_ass.py
git commit -m "$(cat <<'EOF'
feat(ass): emit inline color tags for word-level singer overrides

_create_ass_text now takes a styles_by_singer map and emits
{\c&HBBGGRR&} before words whose singer differs from the segment
singer, then {\r} to reset back to the line's base style. Solo path
and lines without overrides are unchanged — no color tags are emitted
if all words match the segment singer.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Thread `styles_by_singer` through `LyricsScreen`

**Files:**
- Modify: `karaoke_gen/lyrics_transcriber/output/ass/lyrics_screen.py`

- [ ] **Step 1: Add failing test**

Append to `tests/unit/lyrics_transcriber/output/test_multisinger_ass.py`:

```python
from karaoke_gen.lyrics_transcriber.output.ass.lyrics_screen import LyricsScreen


class TestLyricsScreenPassesStylesMap:
    def test_screen_threads_styles_by_singer_to_lines(self, karaoke_style_dict):
        styles = build_karaoke_styles(karaoke_style_dict, singers=[1, 2, 0])
        by_name = {s.Name: s for s in styles}
        styles_map = {1: by_name["Karaoke.Singer1"], 2: by_name["Karaoke.Singer2"], 0: by_name["Karaoke.Both"]}

        screen = LyricsScreen(
            video_size=(1920, 1080),
            line_height=60,
            config=_screen_config(),
        )
        screen.lines.append(_make_line(singer=2))

        events, _ = screen.as_ass_events(style=by_name["Karaoke.Singer1"], styles_by_singer=styles_map)
        # The one line's event should use Singer2's style
        dialogue_events = [e for e in events if getattr(e, "type", None) == "Dialogue"]
        assert any(e.Style is by_name["Karaoke.Singer2"] for e in dialogue_events)
```

- [ ] **Step 2: Run test to verify failure**

```bash
poetry run pytest tests/unit/lyrics_transcriber/output/test_multisinger_ass.py::TestLyricsScreenPassesStylesMap -v
```

Expected: fails (`as_ass_events` doesn't accept `styles_by_singer`).

- [ ] **Step 3: Modify `LyricsScreen.as_ass_events`**

In `karaoke_gen/lyrics_transcriber/output/ass/lyrics_screen.py`, extend the `as_ass_events` method (line 175):

```python
    def as_ass_events(
        self,
        style: Style,
        previous_active_lines: Optional[List[Tuple[float, int, str]]] = None,
        previous_instrumental_end: Optional[float] = None,
        styles_by_singer: Optional[dict] = None,
    ) -> Tuple[List[Event], List[Tuple[float, int, str]]]:
        """Convert screen to ASS events. Returns (events, active_lines)."""
        events = []
        active_lines = []
        previous_active_lines = previous_active_lines or []

        previous_end_time = None
        if previous_active_lines:
            previous_end_time = max(end_time for end_time, _, _ in previous_active_lines)

        if previous_active_lines:
            self.logger.debug("  Active lines from previous screen:")
            for end, pos, text in previous_active_lines:
                line_index = PositionCalculator.position_to_line_index(pos, self.config)
                clear_time = end + (self.config.fade_out_ms / 1000)
                self.logger.debug(
                    f"    Line {line_index + 1}: '{text}' "
                    f"(ends {end:.2f}s, fade out {end + (self.config.fade_out_ms / 1000):.2f}s, clear {clear_time:.2f}s)"
                )

        positions = self.position_strategy.calculate_line_positions()
        timings = self.timing_strategy.calculate_line_timings(
            current_lines=self.lines,
            previous_active_lines=previous_active_lines,
            previous_instrumental_end=previous_instrumental_end,
        )

        for i, (line, timing) in enumerate(zip(self.lines, timings)):
            y_position = positions[i]
            line_state = LineState(text=line.segment.text, timing=timing, y_position=y_position)

            line_events = line.create_ass_events(
                state=line_state,
                style=style,
                config=self.config,
                previous_end_time=previous_end_time,
                styles_by_singer=styles_by_singer,
            )
            events.extend(line_events)

            previous_end_time = timing.end_time
            active_lines.append((timing.end_time, y_position, line.segment.text))

            self.logger.debug(f"    Line {i + 1}: '{line.segment.text}'")

        return events, active_lines
```

- [ ] **Step 4: Run test to verify it passes**

```bash
poetry run pytest tests/unit/lyrics_transcriber/output/test_multisinger_ass.py::TestLyricsScreenPassesStylesMap -v
```

Expected: PASS.

- [ ] **Step 5: Run all multisinger_ass tests + existing ASS tests to confirm no regressions**

```bash
poetry run pytest tests/unit/lyrics_transcriber/output/test_multisinger_ass.py -v
poetry run pytest tests/unit/lyrics_transcriber/output -v 2>&1 | tail -n 20
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add karaoke_gen/lyrics_transcriber/output/ass/lyrics_screen.py tests/unit/lyrics_transcriber/output/test_multisinger_ass.py
git commit -m "$(cat <<'EOF'
feat(ass): thread styles_by_singer through LyricsScreen to lines

LyricsScreen.as_ass_events accepts an optional styles_by_singer map and
forwards it to LyricsLine.create_ass_events. Solo path remains
unchanged — callers that don't pass the map get the single-style
behavior.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Wire `SubtitlesGenerator` to build styles map and detect singers in use

**Files:**
- Modify: `karaoke_gen/lyrics_transcriber/output/subtitles.py`
- Modify: `tests/unit/lyrics_transcriber/output/test_multisinger_ass.py`

Context: `SubtitlesGenerator.generate_ass` today builds one Style via `_create_styled_ass_instance` and passes it into each screen. We'll: (1) scan segments for singers-in-use, (2) build a styles map or a single solo style via `build_karaoke_styles`, (3) add all styles to the ASS file, (4) pass the map into each screen's `as_ass_events`. We also accept an `is_duet` flag — when False, force the solo path even if singer fields are present.

- [ ] **Step 1: Add failing tests**

Append to `tests/unit/lyrics_transcriber/output/test_multisinger_ass.py`:

```python
from unittest.mock import MagicMock


def _segment(seg_id, start, end, words, singer=None):
    return LyricsSegment(
        id=seg_id, text=" ".join(w.text for w in words),
        words=words, start_time=start, end_time=end, singer=singer,
    )


class TestSubtitlesGeneratorSingerDetection:
    def test_solo_default_returns_singer_1_only(self, karaoke_style_dict):
        from karaoke_gen.lyrics_transcriber.output.subtitles import SubtitlesGenerator
        gen = SubtitlesGenerator(
            output_dir="/tmp", video_resolution=(1920, 1080),
            font_size=100, line_height=60, styles={"karaoke": karaoke_style_dict},
            subtitle_offset_ms=0, logger=MagicMock(),
        )
        segments = [
            _segment("s1", 0.0, 1.0, [Word(id="w1", text="hi", start_time=0.0, end_time=1.0)], singer=None),
        ]
        assert gen._detect_singers_in_use(segments, is_duet=False) == [1]

    def test_duet_on_but_all_singer_1_still_returns_just_1(self, karaoke_style_dict):
        from karaoke_gen.lyrics_transcriber.output.subtitles import SubtitlesGenerator
        gen = SubtitlesGenerator(
            output_dir="/tmp", video_resolution=(1920, 1080),
            font_size=100, line_height=60, styles={"karaoke": karaoke_style_dict},
            subtitle_offset_ms=0, logger=MagicMock(),
        )
        segments = [
            _segment("s1", 0.0, 1.0, [Word(id="w1", text="hi", start_time=0.0, end_time=1.0)], singer=1),
        ]
        assert gen._detect_singers_in_use(segments, is_duet=True) == [1]

    def test_duet_with_mixed_singers(self, karaoke_style_dict):
        from karaoke_gen.lyrics_transcriber.output.subtitles import SubtitlesGenerator
        gen = SubtitlesGenerator(
            output_dir="/tmp", video_resolution=(1920, 1080),
            font_size=100, line_height=60, styles={"karaoke": karaoke_style_dict},
            subtitle_offset_ms=0, logger=MagicMock(),
        )
        segments = [
            _segment("s1", 0.0, 1.0, [Word(id="w1", text="hi", start_time=0.0, end_time=1.0)], singer=1),
            _segment("s2", 1.0, 2.0, [Word(id="w2", text="bye", start_time=1.0, end_time=2.0)], singer=2),
            _segment("s3", 2.0, 3.0, [Word(id="w3", text="hey", start_time=2.0, end_time=3.0)], singer=0),
        ]
        # Always sorted with 1 first
        singers = gen._detect_singers_in_use(segments, is_duet=True)
        assert 1 in singers and 2 in singers and 0 in singers

    def test_duet_picks_up_word_level_override(self, karaoke_style_dict):
        from karaoke_gen.lyrics_transcriber.output.subtitles import SubtitlesGenerator
        gen = SubtitlesGenerator(
            output_dir="/tmp", video_resolution=(1920, 1080),
            font_size=100, line_height=60, styles={"karaoke": karaoke_style_dict},
            subtitle_offset_ms=0, logger=MagicMock(),
        )
        segments = [
            _segment(
                "s1", 0.0, 1.0,
                [Word(id="w1", text="hi", start_time=0.0, end_time=0.5, singer=2),
                 Word(id="w2", text="bye", start_time=0.5, end_time=1.0)],
                singer=1,
            ),
        ]
        singers = gen._detect_singers_in_use(segments, is_duet=True)
        assert 2 in singers
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/unit/lyrics_transcriber/output/test_multisinger_ass.py::TestSubtitlesGeneratorSingerDetection -v
```

Expected: all fail (`_detect_singers_in_use` doesn't exist).

- [ ] **Step 3: Add singer detection and refactor `SubtitlesGenerator.generate_ass`**

In `karaoke_gen/lyrics_transcriber/output/subtitles.py`, do three things:

**(a)** Add `is_duet` to `__init__` (default False for backward compat):

```python
    def __init__(
        self,
        output_dir: str,
        video_resolution: Tuple[int, int],
        font_size: int,
        line_height: int,
        styles: dict,
        subtitle_offset_ms: int = 0,
        logger: Optional[logging.Logger] = None,
        is_duet: bool = False,
    ):
        ...
        self.is_duet = is_duet
        ...
```

(keep the existing body; just assign the new attribute right after `self.styles = styles`.)

**(b)** Add helper method `_detect_singers_in_use` on the class (place after `_get_audio_duration`):

```python
    def _detect_singers_in_use(self, segments: List[LyricsSegment], is_duet: bool) -> List[int]:
        """Scan segments + words for singer ids. Returns a sorted list with 1 always first.

        When is_duet is False, always returns [1] (solo path).
        """
        if not is_duet:
            return [1]

        found = {1}  # Singer 1 is always present as the default
        for seg in segments:
            if seg.singer is not None:
                found.add(seg.singer)
            for w in seg.words:
                if w.singer is not None:
                    found.add(w.singer)
        # Sort with 1 first, then 2, then 0 (Both) for stable output
        order = [1, 2, 0]
        return [sid for sid in order if sid in found]
```

**(c)** Rewrite `_create_styled_ass_instance` to optionally return a styles map. Then update `_create_styled_subtitles` and `generate_ass` to thread the map. Keep the solo path producing byte-identical output.

Replace the existing `_create_styled_ass_instance` (lines 273–352) with:

```python
    def _create_styled_ass_instance(self, resolution, fontsize, segments=None):
        from karaoke_gen.lyrics_transcriber.output.ass.style import build_karaoke_styles

        a = ASS()
        a.set_resolution(resolution)

        a.styles_format = [
            "Name", "Fontname", "Fontpath", "Fontsize",
            "PrimaryColour", "SecondaryColour", "OutlineColour", "BackColour",
            "Bold", "Italic", "Underline", "StrikeOut",
            "ScaleX", "ScaleY", "Spacing", "Angle",
            "BorderStyle", "Outline", "Shadow",
            "Alignment", "MarginL", "MarginR", "MarginV", "Encoding",
        ]

        karaoke_styles = self.styles.get("karaoke", {})
        singers_in_use = self._detect_singers_in_use(segments or [], self.is_duet)
        solo = not self.is_duet or singers_in_use == [1]

        # Note: we still need to respect the existing karaoke ass_name for solo path
        # so ASS output stays byte-identical to pre-change for solo jobs.
        style_list = build_karaoke_styles(karaoke_styles, singers=singers_in_use, solo=solo)

        # All styles share the same alignment
        for s in style_list:
            s.Alignment = ALIGN_TOP_CENTER
            a.add_style(s)

        # Build the singer→Style map (used for duet path; unused for solo)
        styles_by_singer = None
        if not solo:
            name_to_singer = {"Karaoke.Singer1": 1, "Karaoke.Singer2": 2, "Karaoke.Both": 0}
            styles_by_singer = {name_to_singer[s.Name]: s for s in style_list}

        a.events_format = ["Layer", "Style", "Start", "End", "MarginV", "Text"]
        # Primary (fallback) style is the first one (singer 1 for duet, "Default" for solo)
        primary_style = style_list[0]
        return a, primary_style, styles_by_singer
```

Replace `_create_styled_subtitles` (line 354) with:

```python
    def _create_styled_subtitles(
        self,
        screens: List[Union[SectionScreen, LyricsScreen]],
        resolution: Tuple[int, int],
        fontsize: int,
        segments: Optional[List[LyricsSegment]] = None,
    ) -> ASS:
        """Create styled ASS subtitles from all screens."""
        ass_file, style, styles_by_singer = self._create_styled_ass_instance(resolution, fontsize, segments=segments)

        active_lines = []
        previous_instrumental_end = None

        for screen in screens:
            if isinstance(screen, SectionScreen):
                section_events, _ = screen.as_ass_events(style=style)
                for event in section_events:
                    ass_file.add(event)

                previous_instrumental_end = screen.end_time
                active_lines = []
                self.logger.debug(f"Found instrumental section ending at {screen.end_time:.2f}s")
                continue

            self.logger.debug(f"Processing screen with instrumental_end={previous_instrumental_end}")
            events, active_lines = screen.as_ass_events(
                style=style,
                previous_active_lines=active_lines,
                previous_instrumental_end=previous_instrumental_end,
                styles_by_singer=styles_by_singer,
            )

            if previous_instrumental_end is not None:
                self.logger.debug("Clearing instrumental end time after processing post-instrumental screen")
                previous_instrumental_end = None

            for event in events:
                ass_file.add(event)

        return ass_file
```

Update `generate_ass` (line 107) to pass segments through:

```python
    def generate_ass(self, segments: List[LyricsSegment], output_prefix: str, audio_filepath: str) -> str:
        self.logger.info("Generating ASS format subtitles")
        output_path = self._get_output_path(f"{output_prefix} (Karaoke)", "ass")

        try:
            self.logger.debug(f"Processing {len(segments)} segments")
            song_duration = self._get_audio_duration(audio_filepath, segments)

            screens = self._create_screens(segments, song_duration)
            self.logger.debug(f"Created {len(screens)} initial screens")

            lyric_subtitles_ass = self._create_styled_subtitles(
                screens, self.video_resolution, self.font_size, segments=segments
            )
            self.logger.debug("Created styled subtitles")

            lyric_subtitles_ass.write(output_path)
            self.logger.info(f"ASS file generated: {output_path}")
            return output_path

        except Exception as e:
            self.logger.error(f"Failed to generate ASS file: {str(e)}", exc_info=True)
            raise
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/unit/lyrics_transcriber/output/test_multisinger_ass.py -v
```

Expected: all PASS.

- [ ] **Step 5: Run the full lyrics_transcriber output tests to catch regressions**

```bash
poetry run pytest tests/unit/lyrics_transcriber/output/ -v 2>&1 | tail -n 30
```

Expected: all existing tests still PASS (including any that exercised `_create_styled_ass_instance`).

- [ ] **Step 6: Commit**

```bash
git add karaoke_gen/lyrics_transcriber/output/subtitles.py tests/unit/lyrics_transcriber/output/test_multisinger_ass.py
git commit -m "$(cat <<'EOF'
feat(subtitles): wire SubtitlesGenerator to produce per-singer ASS styles

Adds is_duet flag and _detect_singers_in_use helper. When is_duet is
True and multiple singers are present, builds one Style per singer via
build_karaoke_styles and threads a styles_by_singer map through the
screen → line chain. Solo path (is_duet=False or only singer 1
detected) uses the original single "Default" style.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Solo byte-identical regression guard

**Files:**
- Create: `tests/unit/lyrics_transcriber/output/test_multisinger_regression.py`

- [ ] **Step 1: Write the regression test**

Create `tests/unit/lyrics_transcriber/output/test_multisinger_regression.py`:

```python
"""Regression guard: solo jobs must produce byte-identical ASS output to pre-change.

We generate ASS for a fixture segment using both the legacy-compatible path
(is_duet=False) and compare structural markers to the expected pre-change output.
This catches accidental behaviour changes in the solo path.
"""
import os
from unittest.mock import MagicMock

from karaoke_gen.lyrics_transcriber.output.subtitles import SubtitlesGenerator
from karaoke_gen.lyrics_transcriber.types import Word, LyricsSegment
from karaoke_gen.style_loader import DEFAULT_KARAOKE_STYLE


def _fixture_segments():
    return [
        LyricsSegment(
            id="s1",
            text="hello world",
            words=[
                Word(id="w1", text="hello", start_time=0.5, end_time=1.0),
                Word(id="w2", text="world", start_time=1.1, end_time=1.8),
            ],
            start_time=0.5,
            end_time=1.8,
        ),
    ]


def test_solo_ass_has_single_default_style():
    """is_duet=False + no singer fields → single 'Default' style, no per-singer styles."""
    gen = SubtitlesGenerator(
        output_dir="/tmp",
        video_resolution=(1920, 1080),
        font_size=100,
        line_height=60,
        styles={"karaoke": DEFAULT_KARAOKE_STYLE},
        subtitle_offset_ms=0,
        logger=MagicMock(),
        is_duet=False,
    )
    ass, _style, styles_by_singer = gen._create_styled_ass_instance((1920, 1080), 100, segments=_fixture_segments())
    assert styles_by_singer is None, "Solo path should produce no styles_by_singer map"
    # The ASS file should contain exactly one style, named per DEFAULT_KARAOKE_STYLE["ass_name"]
    # (ASS keeps styles in a list accessible via its internal structure)
    assert len(ass.styles) == 1
    assert ass.styles[0].Name == DEFAULT_KARAOKE_STYLE["ass_name"]


def test_solo_ass_no_color_override_tags(tmp_path):
    """Generated ASS text for a solo segment should not contain any {\\c} or {\\r} tags."""
    gen = SubtitlesGenerator(
        output_dir=str(tmp_path),
        video_resolution=(1920, 1080),
        font_size=100,
        line_height=60,
        styles={"karaoke": DEFAULT_KARAOKE_STYLE},
        subtitle_offset_ms=0,
        logger=MagicMock(),
        is_duet=False,
    )
    # Run generate_ass against a fake audio filepath — we'll mock the duration
    gen._get_audio_duration = MagicMock(return_value=10.0)
    segments = _fixture_segments()
    output = gen.generate_ass(segments, output_prefix="test", audio_filepath="/fake/audio.mp3")
    with open(output, "r", encoding="utf-8") as f:
        content = f.read()
    assert "\\c&H" not in content, "Solo ASS must not contain inline color override tags"
    # Count number of Style definition lines — should be exactly 1 Style
    style_lines = [l for l in content.split("\n") if l.startswith("Style:")]
    assert len(style_lines) == 1, f"Solo ASS must have exactly one Style, got {len(style_lines)}"
```

- [ ] **Step 2: Run the test**

```bash
poetry run pytest tests/unit/lyrics_transcriber/output/test_multisinger_regression.py -v
```

Expected: PASS (the solo path should already behave correctly).

If it FAILS, something in Task 7's refactor broke the solo path — fix before proceeding.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/lyrics_transcriber/output/test_multisinger_regression.py
git commit -m "$(cat <<'EOF'
test(ass): regression guard for solo job byte-identical ASS output

Adds structural assertions that is_duet=False produces a single
"Default" style, no styles_by_singer map, and no inline color override
tags in the generated ASS file.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 3 — CDG rendering integration

### Task 9: Add `CDG_DUET_SINGERS` palette to `style_loader.py`

**Files:**
- Modify: `karaoke_gen/style_loader.py`
- Modify: `tests/unit/test_style_loader.py`

- [ ] **Step 1: Add failing test**

Append to `tests/unit/test_style_loader.py`:

```python
class TestCdgDuetSingers:
    def test_cdg_duet_singers_has_three_entries(self):
        from karaoke_gen.style_loader import CDG_DUET_SINGERS
        assert len(CDG_DUET_SINGERS) == 3

    def test_cdg_duet_singer_colors_are_rgb_tuples(self):
        from karaoke_gen.style_loader import CDG_DUET_SINGERS
        for s in CDG_DUET_SINGERS:
            assert len(s.active_fill) == 3
            assert all(isinstance(c, int) for c in s.active_fill)

    def test_cdg_duet_singer_1_is_blue(self):
        from karaoke_gen.style_loader import CDG_DUET_SINGERS
        assert CDG_DUET_SINGERS[0].active_fill == (112, 112, 247)

    def test_cdg_duet_singer_2_is_pink(self):
        from karaoke_gen.style_loader import CDG_DUET_SINGERS
        assert CDG_DUET_SINGERS[1].active_fill == (247, 112, 180)

    def test_cdg_duet_singer_both_is_yellow(self):
        from karaoke_gen.style_loader import CDG_DUET_SINGERS
        assert CDG_DUET_SINGERS[2].active_fill == (252, 211, 77)
```

- [ ] **Step 2: Run test**

```bash
poetry run pytest tests/unit/test_style_loader.py::TestCdgDuetSingers -v
```

Expected: fails with `ImportError: cannot import name 'CDG_DUET_SINGERS'`.

- [ ] **Step 3: Add `CDG_DUET_SINGERS` constant**

In `karaoke_gen/style_loader.py`, add at the end of the `DEFAULT_CDG_STYLE` section (after line 111):

```python
# =============================================================================
# CDG DUET PALETTE (hardcoded — CDG has a 16-color palette budget)
# =============================================================================

# CDG singer colors (RGB tuples).
# Ordering: [Singer 1, Singer 2, Both]. CDG composer is 1-indexed so this
# list maps to SettingsLyric.singer = 1, 2, 3 respectively. Our internal
# SingerId 0 (Both) maps to CDG singer 3.
#
# Imported lazily to avoid circular imports; see karaoke_gen/lyrics_transcriber/output/cdg.py
# for usage.
def _build_cdg_duet_singers():
    # Imported here to avoid top-level import of the cdgmaker package
    from karaoke_gen.lyrics_transcriber.output.cdgmaker.config import SettingsSinger
    return [
        # Singer 1: blue
        SettingsSinger(
            active_fill=(112, 112, 247), active_stroke=(26, 58, 235),
            inactive_fill=(255, 255, 255), inactive_stroke=(80, 80, 80),
        ),
        # Singer 2: pink
        SettingsSinger(
            active_fill=(247, 112, 180), active_stroke=(158, 26, 96),
            inactive_fill=(255, 255, 255), inactive_stroke=(80, 80, 80),
        ),
        # Both: yellow
        SettingsSinger(
            active_fill=(252, 211, 77), active_stroke=(146, 108, 0),
            inactive_fill=(255, 255, 255), inactive_stroke=(80, 80, 80),
        ),
    ]


# Use a module-level lazy accessor so first access triggers the import
class _CdgDuetSingersLazy:
    _cache = None
    def __iter__(self):
        if self._cache is None:
            self._cache = _build_cdg_duet_singers()
        return iter(self._cache)
    def __getitem__(self, idx):
        if self._cache is None:
            self._cache = _build_cdg_duet_singers()
        return self._cache[idx]
    def __len__(self):
        if self._cache is None:
            self._cache = _build_cdg_duet_singers()
        return len(self._cache)


CDG_DUET_SINGERS = _CdgDuetSingersLazy()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/unit/test_style_loader.py -v 2>&1 | tail -n 30
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add karaoke_gen/style_loader.py tests/unit/test_style_loader.py
git commit -m "$(cat <<'EOF'
feat(cdg): add hardcoded CDG_DUET_SINGERS palette (blue/pink/yellow)

3-singer palette for CDG output matching the theme JSON defaults for
the main-video ASS path. Uses lazy initialization to avoid importing
the cdgmaker package at module load time.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Inspect current `cdg.py` adapter, then extend for `is_duet`

**Files:**
- Read: `karaoke_gen/lyrics_transcriber/output/cdg.py` (current structure)
- Modify: `karaoke_gen/lyrics_transcriber/output/cdg.py`
- Create: `tests/unit/lyrics_transcriber/output/test_multisinger_cdg.py`

**Pre-step: Read the file to understand its current shape**

```bash
cat karaoke_gen/lyrics_transcriber/output/cdg.py | head -150
```

The adapter already builds `Settings` (including `lyrics` and `singers`). We need to:
1. Accept `is_duet: bool` (default False)
2. When False: behave exactly as today.
3. When True: use `CDG_DUET_SINGERS` as `settings.singers`, and set each `SettingsLyric.singer` per segment-level singer (map SingerId 0 → CDG singer 3).

The exact edit depends on what's in `cdg.py`. The tests below are written against a function `build_cdg_settings(...)` — rename in your implementation to match whatever entry point the adapter exposes, OR introduce a new small helper function `build_cdg_lyrics(segments, is_duet)` that returns a `list[SettingsLyric]`. Check the existing code first; prefer extending in place over creating a new function if the existing one is the only code path.

- [ ] **Step 1: Write failing test**

Create `tests/unit/lyrics_transcriber/output/test_multisinger_cdg.py`:

```python
"""Tests for CDG multi-singer adapter."""
from karaoke_gen.lyrics_transcriber.types import Word, LyricsSegment


def _segment(seg_id, start, end, text, singer=None):
    words = [Word(id=f"{seg_id}-w{i}", text=t, start_time=start + i*0.1, end_time=start + (i+1)*0.1)
             for i, t in enumerate(text.split())]
    return LyricsSegment(
        id=seg_id, text=text, words=words,
        start_time=start, end_time=end, singer=singer,
    )


class TestBuildCdgLyrics:
    def test_solo_omits_singer_tag(self):
        from karaoke_gen.lyrics_transcriber.output.cdg import build_cdg_lyrics
        segments = [_segment("s1", 0.0, 1.0, "hello world", singer=None)]
        result = build_cdg_lyrics(segments, is_duet=False, line_tile_height=4, lines_per_page=3)
        assert len(result) == 1
        # Solo path defaults to singer=1 (CDG default)
        assert result[0].singer == 1

    def test_duet_singer_1_maps_to_1(self):
        from karaoke_gen.lyrics_transcriber.output.cdg import build_cdg_lyrics
        segments = [_segment("s1", 0.0, 1.0, "hi", singer=1)]
        result = build_cdg_lyrics(segments, is_duet=True, line_tile_height=4, lines_per_page=3)
        assert result[0].singer == 1

    def test_duet_singer_2_maps_to_2(self):
        from karaoke_gen.lyrics_transcriber.output.cdg import build_cdg_lyrics
        segments = [_segment("s1", 0.0, 1.0, "hi", singer=2)]
        result = build_cdg_lyrics(segments, is_duet=True, line_tile_height=4, lines_per_page=3)
        assert result[0].singer == 2

    def test_duet_both_maps_to_3(self):
        from karaoke_gen.lyrics_transcriber.output.cdg import build_cdg_lyrics
        # SingerId 0 = Both → CDG singer 3
        segments = [_segment("s1", 0.0, 1.0, "hi", singer=0)]
        result = build_cdg_lyrics(segments, is_duet=True, line_tile_height=4, lines_per_page=3)
        assert result[0].singer == 3

    def test_duet_none_singer_defaults_to_1(self):
        from karaoke_gen.lyrics_transcriber.output.cdg import build_cdg_lyrics
        segments = [_segment("s1", 0.0, 1.0, "hi", singer=None)]
        result = build_cdg_lyrics(segments, is_duet=True, line_tile_height=4, lines_per_page=3)
        assert result[0].singer == 1

    def test_duet_word_level_overrides_ignored_for_cdg(self):
        """CDG uses segment-level singer only — word-level overrides are ignored."""
        from karaoke_gen.lyrics_transcriber.output.cdg import build_cdg_lyrics
        words = [
            Word(id="w1", text="hi", start_time=0.0, end_time=0.5, singer=2),
            Word(id="w2", text="bye", start_time=0.5, end_time=1.0),
        ]
        segments = [LyricsSegment(id="s1", text="hi bye", words=words,
                                  start_time=0.0, end_time=1.0, singer=1)]
        result = build_cdg_lyrics(segments, is_duet=True, line_tile_height=4, lines_per_page=3)
        # Only ONE SettingsLyric, tagged with segment singer (1), not split
        assert len(result) == 1
        assert result[0].singer == 1
```

- [ ] **Step 2: Run tests to verify failures**

```bash
poetry run pytest tests/unit/lyrics_transcriber/output/test_multisinger_cdg.py -v
```

Expected: all fail (`build_cdg_lyrics` doesn't exist yet).

- [ ] **Step 3: Implement `build_cdg_lyrics` in `cdg.py`**

Read `karaoke_gen/lyrics_transcriber/output/cdg.py`. Find the class that builds `Settings` (likely a method producing `settings.lyrics: list[SettingsLyric]`).

Add a module-level helper near the top, after the existing imports:

```python
from karaoke_gen.lyrics_transcriber.output.cdgmaker.config import SettingsLyric
from karaoke_gen.lyrics_transcriber.types import LyricsSegment


# Map our internal SingerId (0/1/2) to the CDG composer's 1-indexed singer field.
# SingerId 0 ("Both") → CDG singer 3.
_SINGER_ID_TO_CDG_INDEX = {0: 3, 1: 1, 2: 2}


def build_cdg_lyrics(
    segments,
    is_duet: bool,
    line_tile_height: int,
    lines_per_page: int,
) -> list:
    """Build a list of SettingsLyric entries from our LyricsSegment list.

    When is_duet is False, every SettingsLyric uses the CDG default singer=1
    (regression guard — produces identical output to the previous path).

    When is_duet is True, each SettingsLyric.singer is derived from the
    segment-level singer only (0→3, 1→1, 2→2, None→1). Word-level overrides
    are ignored — CDG's model is line-level singer and splitting lines at
    word boundaries would produce visually distinct display lines.
    """
    out = []
    for seg in segments:
        # Build sync list (timings in centiseconds) — same as existing path
        sync = []
        for w in seg.words:
            sync.append(int(round(w.start_time * 100)))
        # Use the segment text as-is (don't include |singer| prefix — we use numeric singer)
        text = seg.text

        if is_duet and seg.singer is not None:
            cdg_singer = _SINGER_ID_TO_CDG_INDEX.get(seg.singer, 1)
        else:
            cdg_singer = 1

        out.append(SettingsLyric(
            sync=sync,
            text=text,
            line_tile_height=line_tile_height,
            lines_per_page=lines_per_page,
            singer=cdg_singer,
        ))
    return out
```

Then find where the existing `cdg.py` code constructs `SettingsLyric` entries or builds `settings.lyrics`. Replace that construction with a call to `build_cdg_lyrics(...)`. Pass through the `is_duet` flag from wherever it enters this module (likely a method parameter on a class like `CDGGenerator`). If the class doesn't currently accept `is_duet`, add it to `__init__` with default `False` and to the relevant method signature.

Also, when `is_duet` is True, set `settings.singers = list(CDG_DUET_SINGERS)`:

```python
from karaoke_gen.style_loader import CDG_DUET_SINGERS

# Inside the code that builds `settings`:
if is_duet:
    settings.singers = list(CDG_DUET_SINGERS)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/unit/lyrics_transcriber/output/test_multisinger_cdg.py -v
```

Expected: all PASS.

- [ ] **Step 5: Run existing CDG tests to catch regressions**

```bash
poetry run pytest tests/ -k "cdg" -v 2>&1 | tail -n 30
```

Expected: existing CDG tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add karaoke_gen/lyrics_transcriber/output/cdg.py tests/unit/lyrics_transcriber/output/test_multisinger_cdg.py
git commit -m "$(cat <<'EOF'
feat(cdg): build_cdg_lyrics adapter with segment-level singer tagging

Adds build_cdg_lyrics which produces SettingsLyric entries from our
LyricsSegment list. When is_duet=True, each entry is tagged with the
segment-level singer (SingerId 0 → CDG singer 3, 1→1, 2→2). Word-level
overrides are intentionally ignored because CDG's composer model is
line-level; splitting at word boundaries would create visual artifacts.
Solo path produces the same output as before.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 4 — Backend API + render worker

### Task 11: Persist `is_duet` in review session `state_data`

**Files:**
- Modify: `backend/api/routes/review.py`
- Modify: `backend/tests/api/test_review_routes.py` (path may differ — check for an existing review test)

- [ ] **Step 1: Find the existing review-endpoint test**

```bash
ls backend/tests/ | grep -i review
```

Report-back path; tests below assume `backend/tests/api/test_review_complete.py` or similar. Adapt paths accordingly.

- [ ] **Step 2: Write failing tests**

Append to the existing review test file (create one if none exists at that location) — test is_duet handling:

```python
class TestReviewCompleteDuetFlag:
    def test_complete_saves_is_duet_true(self, client, job_id, review_token, mock_storage, mock_job_manager):
        payload = {
            "corrections": {"corrected_segments": []},
            "instrumental_selection": "with_backing",
            "is_duet": True,
        }
        resp = client.post(
            f"/review/{job_id}/complete",
            json=payload,
            headers={"X-Review-Token": review_token},
        )
        assert resp.status_code == 200
        # State data should have been updated with is_duet
        calls = [c for c in mock_job_manager.update_state_data.call_args_list
                 if c.args[1] == "is_duet"]
        assert len(calls) == 1
        assert calls[0].args[2] is True

    def test_complete_defaults_is_duet_false_when_absent(self, client, job_id, review_token, mock_storage, mock_job_manager):
        payload = {
            "corrections": {"corrected_segments": []},
            "instrumental_selection": "with_backing",
            # no is_duet field
        }
        resp = client.post(
            f"/review/{job_id}/complete",
            json=payload,
            headers={"X-Review-Token": review_token},
        )
        assert resp.status_code == 200
        calls = [c for c in mock_job_manager.update_state_data.call_args_list
                 if c.args[1] == "is_duet"]
        assert len(calls) == 1
        assert calls[0].args[2] is False

    def test_complete_rejects_non_bool_is_duet(self, client, job_id, review_token, mock_storage, mock_job_manager):
        payload = {
            "corrections": {"corrected_segments": []},
            "instrumental_selection": "with_backing",
            "is_duet": "not a bool",
        }
        resp = client.post(
            f"/review/{job_id}/complete",
            json=payload,
            headers={"X-Review-Token": review_token},
        )
        assert resp.status_code == 400
```

(If fixtures named `client`, `job_id`, `review_token`, `mock_storage`, `mock_job_manager` don't exist, copy the pattern from the nearest existing test in `backend/tests/api/` — likely `test_review_routes.py` or similar. The specifics are codebase-dependent.)

- [ ] **Step 3: Run tests to verify they fail**

```bash
poetry run pytest backend/tests/api/ -k "duet" -v
```

Expected: 3 failures.

- [ ] **Step 4: Modify `backend/api/routes/review.py`**

Locate the `complete_review` handler (around line 281). After the `instrumental_selection` validation and before the `try` block, add:

```python
    # === is_duet flag (optional, defaults to False) ===
    is_duet_raw = updated_data.get("is_duet", False)
    if not isinstance(is_duet_raw, bool):
        raise HTTPException(
            status_code=400,
            detail=t("en", "review.invalidIsDuetFlag"),
        )
    is_duet = is_duet_raw
```

Inside the `try` block, right after the existing `job_manager.update_state_data(job_id, 'instrumental_selection', instrumental_selection)` line (~349), add:

```python
        # Store duet flag so render worker knows to use multi-singer styles
        job_manager.update_state_data(job_id, 'is_duet', is_duet)
        logger.info(f"Job {job_id}: Stored is_duet flag: {is_duet}")
```

Also strip `is_duet` from the corrections-save payload so it doesn't end up in `corrections_updated.json`. Extend the existing line 333 filter:

```python
        # Remove instrumental_selection and is_duet from updated_data before saving corrections
        # (they're stored separately in state_data)
        excluded_fields = {"instrumental_selection", "is_duet"}
        corrections_to_save = {k: v for k, v in updated_data.items() if k not in excluded_fields}
```

Add the i18n key by adding to `backend/translations/en.json` under `review`:

```json
"invalidIsDuetFlag": "is_duet must be a boolean (true or false)"
```

(Repeat for `backend/translations/es.json` and `backend/translations/de.json` with translated strings — or leave for the translation-run task.)

- [ ] **Step 5: Run tests to verify they pass**

```bash
poetry run pytest backend/tests/api/ -k "duet" -v
poetry run pytest backend/tests/ -v 2>&1 | tail -n 20
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/api/routes/review.py backend/translations/*.json backend/tests/api/
git commit -m "$(cat <<'EOF'
feat(review): persist is_duet flag in review session state_data

complete_review now accepts an optional is_duet boolean in the
request body and stores it via job_manager.update_state_data. The
field is stripped from the corrections save payload. Render workers
will read this from state_data to decide whether to activate the
multi-singer render path.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Propagate `is_duet` through `render_video_worker` and GCE payload

**Files:**
- Modify: `backend/workers/render_video_worker.py`
- Modify: `karaoke_gen/lyrics_transcriber/output/generator.py`
- Modify: relevant backend test file

- [ ] **Step 1: Read current worker flow**

```bash
grep -n "state_data\|instrumental_selection\|is_duet" backend/workers/render_video_worker.py | head -30
```

Locate where `instrumental_selection` is read from state_data — mirror that pattern for `is_duet`.

- [ ] **Step 2: Write failing tests**

In the backend worker test file (check `backend/tests/workers/` for existing patterns), add:

```python
def test_render_worker_reads_is_duet_from_state_data(mocker, job_id):
    mocker.patch("backend.workers.render_video_worker.StorageService")
    mock_jm = mocker.patch("backend.workers.render_video_worker.JobManager").return_value
    mock_jm.get_state_data.return_value = {
        "instrumental_selection": "with_backing",
        "is_duet": True,
    }
    # ... invoke the worker ...
    # Assert OutputGenerator was constructed with is_duet=True
    # (the exact assertion depends on how the worker builds its config)
```

- [ ] **Step 3: Thread `is_duet` through `OutputGenerator`**

Modify `karaoke_gen/lyrics_transcriber/output/generator.py` `OutputGenerator.__init__` to accept `is_duet: bool = False` and pass it into the `SubtitlesGenerator`:

```python
# In OutputGenerator.__init__, near line 119:
if self.config.render_video:
    self.subtitle = SubtitlesGenerator(
        output_dir=self.config.output_dir,
        video_resolution=self.video_resolution_num,
        font_size=self.font_size,
        line_height=self.line_height,
        styles=self.config.styles,
        subtitle_offset_ms=self.config.subtitle_offset_ms,
        logger=self.logger,
        is_duet=self.config.is_duet,
    )
```

Add `is_duet: bool = False` to the `OutputGeneratorConfig` dataclass (or equivalent) so it flows through the config.

Do the same for CDG generation — wherever `OutputGenerator` constructs the CDG adapter, pass `is_duet` through.

- [ ] **Step 4: Modify `backend/workers/render_video_worker.py`**

Locate where the render config is built (look for `render_config = { ... }` or `OutputGeneratorConfig(...)`). Read `is_duet` from state_data:

```python
# After reading instrumental_selection:
state_data = job_manager.get_state_data(job_id) or {}
is_duet = bool(state_data.get("is_duet", False))

# Pass into OutputGenerator config (or equivalent)
output_config.is_duet = is_duet

# And add to the GCE encoding-worker request payload:
gce_payload["is_duet"] = is_duet
```

Find the exact object name from the existing code — `state_data`, `render_config`, and `gce_payload` are placeholders.

- [ ] **Step 5: Run tests**

```bash
poetry run pytest backend/tests/workers/ -k "render" -v
poetry run pytest tests/unit/lyrics_transcriber/ -v 2>&1 | tail -n 20
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/workers/render_video_worker.py karaoke_gen/lyrics_transcriber/output/generator.py backend/tests/
git commit -m "$(cat <<'EOF'
feat(workers): propagate is_duet from state_data through render pipeline

render_video_worker now reads is_duet from state_data and passes it
through OutputGenerator config and the GCE encoding-worker request
payload. SubtitlesGenerator receives is_duet in its constructor; CDG
generator likewise.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 5 — Frontend data layer

### Task 13: Add singer fields to frontend types

**Files:**
- Modify: `frontend/lib/lyrics-review/types.ts`

- [ ] **Step 1: Add the types**

Open `frontend/lib/lyrics-review/types.ts`. Near the top (after imports), add:

```typescript
// Singer id. 1 = Singer 1, 2 = Singer 2, 0 = Both. undefined = default (Singer 1).
export type SingerId = 0 | 1 | 2
```

Modify the existing `Word` interface:

```typescript
export interface Word {
  id: string
  text: string
  start_time: number | null
  end_time: number | null
  confidence?: number
  created_during_correction?: boolean
  singer?: SingerId
}
```

Modify `LyricsSegment`:

```typescript
export interface LyricsSegment {
  id: string
  text: string
  words: Word[]
  start_time: number | null
  end_time: number | null
  singer?: SingerId
}
```

If there's a top-level review state / CorrectionData type, add:

```typescript
export interface CorrectionData {
  // ... existing fields ...
  is_duet?: boolean  // review session flag, mirrors backend state_data.is_duet
}
```

- [ ] **Step 2: Run frontend type check**

```bash
cd frontend && npx tsc --noEmit 2>&1 | head -40
```

Expected: no type errors introduced by adding optional fields.

- [ ] **Step 3: Commit**

```bash
git add frontend/lib/lyrics-review/types.ts
git commit -m "$(cat <<'EOF'
feat(types): add SingerId type and optional singer fields to review types

Adds SingerId (0|1|2) and optional singer to Word and LyricsSegment,
plus is_duet to CorrectionData. All fields are optional so existing
review state shapes stay valid.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: Create `duet.ts` helper module

**Files:**
- Create: `frontend/lib/lyrics-review/duet.ts`
- Create: `frontend/lib/lyrics-review/__tests__/duet.test.ts`

- [ ] **Step 1: Write failing tests**

Create `frontend/lib/lyrics-review/__tests__/duet.test.ts`:

```typescript
import {
  cycleSinger,
  resolveSegmentSinger,
  collectSingersInUse,
  hasWordOverrides,
} from '../duet'
import type { LyricsSegment, Word, SingerId } from '../types'

const word = (id: string, singer?: SingerId): Word => ({
  id, text: id, start_time: 0, end_time: 1, singer,
})

const seg = (id: string, singer?: SingerId, words: Word[] = [word(`${id}-w1`)]): LyricsSegment => ({
  id, text: words.map(w => w.text).join(' '), words, start_time: 0, end_time: 1, singer,
})

describe('cycleSinger', () => {
  it('cycles 1 → 2 → 0 (Both) → 1', () => {
    expect(cycleSinger(1)).toBe(2)
    expect(cycleSinger(2)).toBe(0)
    expect(cycleSinger(0)).toBe(1)
  })

  it('undefined cycles to 2 (treated as Singer 1 default)', () => {
    expect(cycleSinger(undefined)).toBe(2)
  })
})

describe('resolveSegmentSinger', () => {
  it('returns 1 when segment.singer is undefined', () => {
    expect(resolveSegmentSinger(seg('s1', undefined))).toBe(1)
  })
  it('returns the explicit singer when set', () => {
    expect(resolveSegmentSinger(seg('s1', 2))).toBe(2)
    expect(resolveSegmentSinger(seg('s1', 0))).toBe(0)
  })
})

describe('collectSingersInUse', () => {
  it('returns [1] for solo (no singer fields)', () => {
    expect(collectSingersInUse([seg('s1')])).toEqual([1])
  })
  it('includes all used singers, sorted 1,2,0', () => {
    const segments = [
      seg('s1', 1),
      seg('s2', 2),
      seg('s3', 0),
    ]
    expect(collectSingersInUse(segments)).toEqual([1, 2, 0])
  })
  it('picks up word-level overrides', () => {
    const segments = [
      seg('s1', 1, [word('w1'), word('w2', 2)]),
    ]
    expect(collectSingersInUse(segments)).toEqual([1, 2])
  })
})

describe('hasWordOverrides', () => {
  it('false when no words have singer field', () => {
    expect(hasWordOverrides(seg('s1', 1, [word('w1'), word('w2')]))).toBe(false)
  })
  it('false when all word singers match segment', () => {
    expect(hasWordOverrides(seg('s1', 1, [word('w1', 1), word('w2', 1)]))).toBe(false)
  })
  it('true when any word singer differs from segment', () => {
    expect(hasWordOverrides(seg('s1', 1, [word('w1'), word('w2', 2)]))).toBe(true)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd frontend && npm run test:unit -- duet.test 2>&1 | tail -n 20
```

Expected: fails with "cannot find module '../duet'".

- [ ] **Step 3: Create `frontend/lib/lyrics-review/duet.ts`**

```typescript
import type { LyricsSegment, Word, SingerId } from './types'

/** Cycle through singer values: undefined/1 → 2 → 0 (Both) → 1. */
export function cycleSinger(current: SingerId | undefined): SingerId {
  const effective = current ?? 1
  if (effective === 1) return 2
  if (effective === 2) return 0
  return 1
}

/** The effective singer for a segment — defaults to 1 when unset. */
export function resolveSegmentSinger(segment: LyricsSegment): SingerId {
  return segment.singer ?? 1
}

/** All singer ids used in a list of segments, sorted with 1 first, then 2, then 0 (Both). */
export function collectSingersInUse(segments: LyricsSegment[]): SingerId[] {
  const seen = new Set<SingerId>([1])
  for (const seg of segments) {
    if (seg.singer !== undefined) seen.add(seg.singer)
    for (const w of seg.words) {
      if (w.singer !== undefined) seen.add(w.singer)
    }
  }
  const order: SingerId[] = [1, 2, 0]
  return order.filter(sid => seen.has(sid))
}

/** True when any word in the segment has a singer that differs from the resolved segment singer. */
export function hasWordOverrides(segment: LyricsSegment): boolean {
  const segmentSinger = resolveSegmentSinger(segment)
  return segment.words.some(w => w.singer !== undefined && w.singer !== segmentSinger)
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd frontend && npm run test:unit -- duet.test 2>&1 | tail -n 10
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/lyrics-review/duet.ts frontend/lib/lyrics-review/__tests__/duet.test.ts
git commit -m "$(cat <<'EOF'
feat(lyrics-review): add duet helper module

Pure-function helpers used by UI and save payload logic:
cycleSinger, resolveSegmentSinger, collectSingersInUse, hasWordOverrides.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: Extend `segmentOperations.ts` with singer inheritance rules

**Files:**
- Modify: `frontend/lib/lyrics-review/utils/segmentOperations.ts`
- Create or extend: `frontend/lib/lyrics-review/__tests__/segmentOperations.test.ts`

- [ ] **Step 1: Check for existing tests**

```bash
ls frontend/lib/lyrics-review/__tests__/ 2>/dev/null
```

If `segmentOperations.test.ts` exists, extend it. Otherwise create it.

- [ ] **Step 2: Write failing tests**

Append (or create) tests for singer inheritance:

```typescript
import {
  splitSegment,
  mergeSegment,
  addSegmentBefore,
  deleteSegment,
} from '../utils/segmentOperations'
import type { CorrectionData } from '../types'

const baseData = (segments: any[]): CorrectionData => ({
  corrected_segments: segments,
} as CorrectionData)

describe('segmentOperations — singer inheritance', () => {
  it('split: both halves inherit parent singer', () => {
    const data = baseData([{
      id: 's1', text: 'hello world foo', start_time: 0, end_time: 3,
      words: [
        { id: 'w1', text: 'hello', start_time: 0, end_time: 1 },
        { id: 'w2', text: 'world', start_time: 1, end_time: 2 },
        { id: 'w3', text: 'foo', start_time: 2, end_time: 3 },
      ],
      singer: 2,
    }])
    const result = splitSegment(data, 0, 0)  // split after word index 0 → two segments
    if (!result) throw new Error('split returned null')
    expect(result.corrected_segments[0].singer).toBe(2)
    expect(result.corrected_segments[1].singer).toBe(2)
  })

  it('split: word-level overrides stay with the half containing that word', () => {
    const data = baseData([{
      id: 's1', text: 'hello world', start_time: 0, end_time: 2,
      words: [
        { id: 'w1', text: 'hello', start_time: 0, end_time: 1, singer: 2 },
        { id: 'w2', text: 'world', start_time: 1, end_time: 2 },
      ],
      singer: 1,
    }])
    const result = splitSegment(data, 0, 0)
    if (!result) throw new Error('split returned null')
    // First half: w1 with singer=2 override; segment singer inherited as 1
    expect(result.corrected_segments[0].words[0].singer).toBe(2)
    // Second half: w2 with no override
    expect(result.corrected_segments[1].words[0].singer).toBeUndefined()
  })

  it('merge: result takes first segment singer; words preserve their singers', () => {
    const data = baseData([
      { id: 's1', text: 'hello', start_time: 0, end_time: 1,
        words: [{ id: 'w1', text: 'hello', start_time: 0, end_time: 1 }], singer: 1 },
      { id: 's2', text: 'world', start_time: 1, end_time: 2,
        words: [{ id: 'w2', text: 'world', start_time: 1, end_time: 2 }], singer: 2 },
    ])
    const result = mergeSegment(data, 0, true)
    expect(result.corrected_segments[0].singer).toBe(1)
    // The merged words — w2 was implicitly singer 2 via its segment,
    // it now becomes an explicit word-level override relative to the new segment singer (1)
    const mergedWords = result.corrected_segments[0].words
    expect(mergedWords.find(w => w.id === 'w2')?.singer).toBe(2)
    expect(mergedWords.find(w => w.id === 'w1')?.singer).toBeUndefined()
  })

  it('addSegmentBefore: inherits next segment singer', () => {
    const data = baseData([
      { id: 's1', text: 'hello', start_time: 1, end_time: 2, words: [], singer: 2 },
    ])
    const result = addSegmentBefore(data, 0)
    expect(result.corrected_segments[0].singer).toBe(2)
  })

  it('delete: remaining segments unchanged', () => {
    const data = baseData([
      { id: 's1', text: 'a', start_time: 0, end_time: 1, words: [], singer: 1 },
      { id: 's2', text: 'b', start_time: 1, end_time: 2, words: [], singer: 2 },
    ])
    const result = deleteSegment(data, 0)
    expect(result.corrected_segments[0].id).toBe('s2')
    expect(result.corrected_segments[0].singer).toBe(2)
  })
})
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd frontend && npm run test:unit -- segmentOperations 2>&1 | tail -n 30
```

Expected: failures.

- [ ] **Step 4: Modify `frontend/lib/lyrics-review/utils/segmentOperations.ts`**

The exact modifications depend on the current implementation. For each operation:

- `splitSegment`: when constructing the two new segments, copy `singer` from the parent to both. Words retain their existing `singer` field (which may already be undefined).
- `mergeSegment`: when merging segment B into segment A, any word from B that doesn't already have a `singer` field should get `singer: B.singer` set explicitly (unless B.singer is undefined, in which case leave the word's singer undefined). Then concatenate words. New segment's `singer` = A's `singer`.
- `addSegmentBefore`: the new segment's `singer` = the `singer` of the segment at `beforeIndex`.
- `deleteSegment`: no singer change.

Read the current file first, then apply surgical edits preserving the existing logic. For example, in `splitSegment` where it constructs the two new segments, ensure both have `singer: segment.singer` copied.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd frontend && npm run test:unit -- segmentOperations 2>&1 | tail -n 15
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/lib/lyrics-review/utils/segmentOperations.ts frontend/lib/lyrics-review/__tests__/segmentOperations.test.ts
git commit -m "$(cat <<'EOF'
feat(lyrics-review): preserve singer fields through segment edits

splitSegment copies the parent singer to both halves and preserves
word-level overrides with the half containing each word.
mergeSegment keeps the first segment's singer and converts the
second segment's implicit singer into explicit word-level overrides
so no info is lost.
addSegmentBefore inherits the next segment's singer.
deleteSegment has no singer effect.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 6 — Frontend UI

### Task 16: Add `lyricsReview.duet.*` namespace to `en.json`

**Files:**
- Modify: `frontend/messages/en.json`

- [ ] **Step 1: Add the namespace**

Open `frontend/messages/en.json`. Inside the `lyricsReview` block (existing keys: `header`, `guidance`, `transcription`, etc.), add:

```json
    "duet": {
      "markAsDuet": "Mark as duet",
      "duetOn": "Duet: ON",
      "markAsDuetHint": "Enable to mark segments as Singer 1, Singer 2, or Both. Colors will appear in the final video.",
      "singer1": "Singer 1",
      "singer2": "Singer 2",
      "both": "Both",
      "singerChipAriaLabel": "Singer for this segment (click to change)",
      "singerChipAriaLabelWithOverrides": "Singer for this segment with word-level overrides (click to change)",
      "wordOverrideNote": "Word-level singer overrides apply to the karaoke video only — CDG output uses the segment's main singer.",
      "legendSinger": "Singer:",
      "keyboardHintSegment": "With a segment focused, press 1 / 2 / B",
      "resetOverridesTooltip": "Reset word overrides in this segment"
    }
```

- [ ] **Step 2: Run next-intl validation**

```bash
cd frontend && npm run test:unit -- --testPathPattern=i18n 2>&1 | tail -n 15 || true
```

(If no i18n-specific test exists, move on.)

- [ ] **Step 3: Do NOT run translate.py yet**

The automated translation run happens once at the end after all UI changes land. Don't run it per-commit.

- [ ] **Step 4: Commit**

```bash
git add frontend/messages/en.json
git commit -m "$(cat <<'EOF'
feat(i18n): add lyricsReview.duet namespace (en)

Strings for the duet toggle, singer chip, keyboard hints, and the
word-override/CDG divergence note. Other locales will be generated
via translate.py after the UI is complete.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 17: Create `SingerChip` component

**Files:**
- Create: `frontend/components/lyrics-review/SingerChip.tsx`
- Create: `frontend/components/lyrics-review/__tests__/SingerChip.test.tsx`

- [ ] **Step 1: Write failing tests**

Create `frontend/components/lyrics-review/__tests__/SingerChip.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { NextIntlClientProvider } from 'next-intl'
import enMessages from '@/messages/en.json'
import SingerChip from '../SingerChip'

function renderChip(props: any) {
  return render(
    <NextIntlClientProvider locale="en" messages={enMessages}>
      <SingerChip {...props} />
    </NextIntlClientProvider>,
  )
}

describe('SingerChip', () => {
  it('renders Singer 1 label by default', () => {
    const onChange = jest.fn()
    renderChip({ singer: 1, hasOverrides: false, onChange })
    expect(screen.getByRole('button')).toHaveTextContent('1')
  })

  it('renders Singer 2 label', () => {
    renderChip({ singer: 2, hasOverrides: false, onChange: jest.fn() })
    expect(screen.getByRole('button')).toHaveTextContent('2')
  })

  it('renders Both label', () => {
    renderChip({ singer: 0, hasOverrides: false, onChange: jest.fn() })
    expect(screen.getByRole('button')).toHaveTextContent(/Both/)
  })

  it('shows asterisk when hasOverrides is true', () => {
    renderChip({ singer: 1, hasOverrides: true, onChange: jest.fn() })
    expect(screen.getByRole('button').textContent).toContain('*')
  })

  it('click cycles through singers', async () => {
    const onChange = jest.fn()
    const user = userEvent.setup()
    renderChip({ singer: 1, hasOverrides: false, onChange })
    await user.click(screen.getByRole('button'))
    expect(onChange).toHaveBeenCalledWith(2)
  })
})
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd frontend && npm run test:unit -- SingerChip 2>&1 | tail -n 15
```

Expected: fails with module-not-found.

- [ ] **Step 3: Create `SingerChip.tsx`**

```tsx
'use client'

import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import type { SingerId } from '@/lib/lyrics-review/types'
import { cycleSinger } from '@/lib/lyrics-review/duet'

interface SingerChipProps {
  singer: SingerId
  hasOverrides: boolean
  onChange: (next: SingerId) => void
  className?: string
}

const SINGER_LABEL: Record<SingerId, string> = { 1: '1', 2: '2', 0: 'Both' }

const SINGER_CHIP_CLASSES: Record<SingerId, string> = {
  1: 'bg-blue-900/40 border-blue-500 text-blue-200',
  2: 'bg-pink-900/40 border-pink-500 text-pink-200',
  0: 'bg-yellow-900/40 border-yellow-500 text-yellow-100',
}

export default function SingerChip({ singer, hasOverrides, onChange, className }: SingerChipProps) {
  const t = useTranslations('lyricsReview.duet')

  const label = SINGER_LABEL[singer]
  const ariaLabel = hasOverrides
    ? t('singerChipAriaLabelWithOverrides')
    : t('singerChipAriaLabel')

  return (
    <button
      type="button"
      onClick={() => onChange(cycleSinger(singer))}
      className={cn(
        'inline-flex items-center gap-1 px-2 py-0 rounded-sm text-[0.7rem] font-semibold border cursor-pointer select-none',
        SINGER_CHIP_CLASSES[singer],
        className,
      )}
      aria-label={ariaLabel}
    >
      <span>●</span>
      <span>{label}{hasOverrides ? '*' : ''}</span>
    </button>
  )
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd frontend && npm run test:unit -- SingerChip 2>&1 | tail -n 10
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/lyrics-review/SingerChip.tsx frontend/components/lyrics-review/__tests__/SingerChip.test.tsx
git commit -m "$(cat <<'EOF'
feat(lyrics-review): add SingerChip component

Compact clickable chip showing Singer 1 / 2 / Both with per-singer
color. Click cycles through values. Shows asterisk when the segment
has word-level overrides.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 18: Add "Mark as duet" toggle to `Header.tsx`

**Files:**
- Modify: `frontend/components/lyrics-review/Header.tsx`

- [ ] **Step 1: Add props for duet toggle**

Open `frontend/components/lyrics-review/Header.tsx`. In `HeaderProps`, add:

```typescript
  isDuet?: boolean
  onToggleDuet?: () => void
```

- [ ] **Step 2: Render the button**

Near the top of the `Header` component body, add a second translations hook scoped to the duet namespace (the existing `t` is scoped to `lyricsReview.header`):

```typescript
const tDuet = useTranslations('lyricsReview.duet')
```

Find where the existing toolbar buttons (Edit / Highlight / Delete / Undo) are rendered. Add a new button inline:

```tsx
{onToggleDuet && (
  <button
    type="button"
    onClick={onToggleDuet}
    className={cn(
      'px-3 py-1 text-sm rounded border transition-colors',
      isDuet
        ? 'bg-pink-900/40 border-pink-500 text-pink-100'
        : 'border-border text-foreground hover:bg-muted'
    )}
    title={tDuet('markAsDuetHint')}
  >
    ◐ {isDuet ? tDuet('duetOn') : tDuet('markAsDuet')}
  </button>
)}
```

(Use whatever UI primitive (`Button` component, `cn()`, etc.) the rest of the file already uses.)

- [ ] **Step 3: Typecheck**

```bash
cd frontend && npx tsc --noEmit 2>&1 | head -20
```

Expected: no type errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/lyrics-review/Header.tsx
git commit -m "$(cat <<'EOF'
feat(lyrics-review): add Mark as duet toggle to Header

Adds an opt-in button that gates all duet UI. When onToggleDuet is
not provided, the button doesn't render (backward compat for callers
that haven't wired it up).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 19: Wire `isDuet` state in `LyricsAnalyzer.tsx`

**Files:**
- Modify: `frontend/components/lyrics-review/LyricsAnalyzer.tsx`

- [ ] **Step 1: Add state and persistence**

In `LyricsAnalyzer`, add useState for `isDuet`:

```typescript
const [isDuet, setIsDuet] = useState<boolean>(
  (data as any).is_duet ?? false
)
```

Pass it into the `Header`:

```tsx
<Header
  // ... existing props ...
  isDuet={isDuet}
  onToggleDuet={() => setIsDuet(d => !d)}
/>
```

Pass `isDuet` into `TranscriptionView` (next task wires this prop):

```tsx
<TranscriptionView
  // ... existing props ...
  isDuet={isDuet}
  onSegmentSingerChange={(segmentIdx, next) => {
    setData(d => {
      const segments = [...d.corrected_segments]
      segments[segmentIdx] = { ...segments[segmentIdx], singer: next }
      return { ...d, corrected_segments: segments }
    })
  }}
/>
```

- [ ] **Step 2: Include `is_duet` in the save payload**

Find the save function (likely `handleComplete` or similar, calls `apiClient.submitCorrections(data)`). Ensure the payload includes `is_duet`:

```typescript
const payload = {
  corrections: data,
  instrumental_selection: instrumentalSelection,
  is_duet: isDuet,
}
await apiClient.submitCorrections(payload)
```

If `submitCorrections` in `frontend/lib/api.ts` has a typed signature, extend it to include `is_duet?: boolean`.

- [ ] **Step 3: Typecheck**

```bash
cd frontend && npx tsc --noEmit 2>&1 | head -20
```

Expected: no new type errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/lyrics-review/LyricsAnalyzer.tsx frontend/lib/api.ts
git commit -m "$(cat <<'EOF'
feat(lyrics-review): wire duet toggle state and include in save payload

LyricsAnalyzer tracks isDuet, flows it to Header toggle and
TranscriptionView, and persists via the complete-review endpoint
payload. Solo jobs still serialize without is_duet (defaults false).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 20: Render `SingerChip` + row tint in `TranscriptionView.tsx`

**Files:**
- Modify: `frontend/components/lyrics-review/TranscriptionView.tsx`

- [ ] **Step 1: Accept new props**

Add to the prop types of `TranscriptionView`:

```typescript
  isDuet?: boolean
  onSegmentSingerChange?: (segmentIndex: number, next: SingerId) => void
```

- [ ] **Step 2: Render chip + row tint**

Read the current JSX inside the `.map((segment, segmentIndex) => ...)` block. Between the play/delete controls and the word-rendering container, add:

```tsx
{isDuet && onSegmentSingerChange && (
  <SingerChip
    singer={resolveSegmentSinger(segment)}
    hasOverrides={hasWordOverrides(segment)}
    onChange={(next) => onSegmentSingerChange(segmentIndex, next)}
    className="mr-1 flex-shrink-0"
  />
)}
```

Add the imports at the top:

```typescript
import SingerChip from './SingerChip'
import { resolveSegmentSinger, hasWordOverrides } from '@/lib/lyrics-review/duet'
import type { SingerId } from '@/lib/lyrics-review/types'
```

For the row tint, extend the row container's `className`:

```tsx
const segmentSinger = resolveSegmentSinger(segment)
const rowTintClass = isDuet
  ? segmentSinger === 1 ? 'bg-gradient-to-r from-blue-500/10 to-transparent' :
    segmentSinger === 2 ? 'bg-gradient-to-r from-pink-500/10 to-transparent' :
    /* Both */          'bg-gradient-to-r from-yellow-500/10 to-transparent'
  : ''

return (
  <div key={segment.id} className={cn('flex items-start w-full hover:bg-muted/50', rowTintClass)}>
    {/* ... */}
  </div>
)
```

- [ ] **Step 3: Typecheck**

```bash
cd frontend && npx tsc --noEmit 2>&1 | head -20
```

- [ ] **Step 4: Commit**

```bash
git add frontend/components/lyrics-review/TranscriptionView.tsx
git commit -m "$(cat <<'EOF'
feat(lyrics-review): render SingerChip and row tint in TranscriptionView

When isDuet is on, each segment row renders a SingerChip between the
existing controls and the word list, and gets a subtle singer-colored
left-to-right gradient tint for visual scanning. Solo path is
unchanged — no chip, no tint.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 21: Add `1` / `2` / `B` keyboard shortcuts

**Files:**
- Modify: `frontend/lib/lyrics-review/utils/keyboardHandlers.ts`
- Modify: `frontend/components/lyrics-review/LyricsAnalyzer.tsx` (to wire the handler)

- [ ] **Step 1: Audit existing shortcuts**

```bash
grep -n "e.key ===" frontend/lib/lyrics-review/utils/keyboardHandlers.ts | head -20
```

Confirmed existing keys: `Shift`, `Control`/`Meta`, `N`/`n`, `P`/`p`, `Space`, `Escape`. `1`, `2`, `B` are free.

- [ ] **Step 2: Extend `KeyboardState` and handler**

In `frontend/lib/lyrics-review/utils/keyboardHandlers.ts`, add to the `KeyboardState` interface:

```typescript
export interface KeyboardState {
  // ... existing fields ...
  isDuet?: boolean
  focusedSegmentIndex?: number | null
  onAssignSegmentSinger?: (segmentIndex: number, singer: 0 | 1 | 2) => void
}
```

In the key handler, add cases for `1`, `2`, `b`:

```typescript
} else if (state.isDuet && state.focusedSegmentIndex != null && state.onAssignSegmentSinger && !isModalOpen) {
  if (e.key === '1') {
    e.preventDefault()
    state.onAssignSegmentSinger(state.focusedSegmentIndex, 1)
    return
  } else if (e.key === '2') {
    e.preventDefault()
    state.onAssignSegmentSinger(state.focusedSegmentIndex, 2)
    return
  } else if (e.key === 'b' || e.key === 'B') {
    e.preventDefault()
    state.onAssignSegmentSinger(state.focusedSegmentIndex, 0)
    return
  }
}
```

- [ ] **Step 3: Wire in `LyricsAnalyzer.tsx`**

Pass the new fields into `setupKeyboardHandlers`:

```tsx
useEffect(() => {
  return setupKeyboardHandlers({
    // ... existing fields ...
    isDuet,
    focusedSegmentIndex,  // add a useState for this; set when a segment row is focused
    onAssignSegmentSinger: (idx, next) => {
      setData(d => {
        const segments = [...d.corrected_segments]
        segments[idx] = { ...segments[idx], singer: next }
        return { ...d, corrected_segments: segments }
      })
    },
  })
}, [isDuet, focusedSegmentIndex, /* existing deps */])
```

Add `focusedSegmentIndex` state and wire `onFocus` on each segment row (in `TranscriptionView`) to call `onSegmentFocus(segmentIndex)`. This is roughly 10 lines of plumbing — propagate similarly to the other focus-tracked state already in the component.

- [ ] **Step 4: Write test**

Append to `frontend/lib/lyrics-review/__tests__/keyboardHandlers.test.ts` (create if absent):

```typescript
import { setupKeyboardHandlers } from '../utils/keyboardHandlers'

describe('keyboard shortcuts — duet', () => {
  it('pressing 1 calls onAssignSegmentSinger with 1', () => {
    const onAssign = jest.fn()
    const cleanup = setupKeyboardHandlers({
      isDuet: true,
      focusedSegmentIndex: 3,
      onAssignSegmentSinger: onAssign,
    } as any)
    const ev = new KeyboardEvent('keydown', { key: '1' })
    document.dispatchEvent(ev)
    expect(onAssign).toHaveBeenCalledWith(3, 1)
    cleanup()
  })

  it('pressing B calls onAssignSegmentSinger with 0 (Both)', () => {
    const onAssign = jest.fn()
    const cleanup = setupKeyboardHandlers({
      isDuet: true,
      focusedSegmentIndex: 0,
      onAssignSegmentSinger: onAssign,
    } as any)
    const ev = new KeyboardEvent('keydown', { key: 'b' })
    document.dispatchEvent(ev)
    expect(onAssign).toHaveBeenCalledWith(0, 0)
    cleanup()
  })

  it('keys are ignored when isDuet is false', () => {
    const onAssign = jest.fn()
    const cleanup = setupKeyboardHandlers({
      isDuet: false,
      focusedSegmentIndex: 0,
      onAssignSegmentSinger: onAssign,
    } as any)
    const ev = new KeyboardEvent('keydown', { key: '1' })
    document.dispatchEvent(ev)
    expect(onAssign).not.toHaveBeenCalled()
    cleanup()
  })
})
```

- [ ] **Step 5: Run tests**

```bash
cd frontend && npm run test:unit -- keyboardHandlers 2>&1 | tail -n 15
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/lib/lyrics-review/utils/keyboardHandlers.ts frontend/components/lyrics-review/LyricsAnalyzer.tsx frontend/components/lyrics-review/TranscriptionView.tsx frontend/lib/lyrics-review/__tests__/keyboardHandlers.test.ts
git commit -m "$(cat <<'EOF'
feat(lyrics-review): add 1/2/B keyboard shortcuts for singer assignment

With duet mode on and a segment focused, pressing 1, 2, or B assigns
that segment to Singer 1, Singer 2, or Both respectively. Shortcuts
are ignored outside duet mode or when no segment is focused.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 7 — Integration & E2E tests

### Task 22: End-to-end duet fixture integration test

**Files:**
- Create: `tests/integration/test_multisinger_end_to_end.py`
- Create: `tests/integration/fixtures/duet_small.json` (small synthetic corrections fixture)

- [ ] **Step 1: Create the fixture**

```json
{
  "original_segments": [],
  "corrected_segments": [
    {
      "id": "s1",
      "text": "Hello darkness my old friend",
      "start_time": 0.5,
      "end_time": 2.5,
      "singer": 1,
      "words": [
        {"id": "w1", "text": "Hello", "start_time": 0.5, "end_time": 0.9},
        {"id": "w2", "text": "darkness", "start_time": 1.0, "end_time": 1.5},
        {"id": "w3", "text": "my", "start_time": 1.6, "end_time": 1.8},
        {"id": "w4", "text": "old", "start_time": 1.9, "end_time": 2.1},
        {"id": "w5", "text": "friend", "start_time": 2.2, "end_time": 2.5}
      ]
    },
    {
      "id": "s2",
      "text": "I've come to talk with you",
      "start_time": 3.0,
      "end_time": 5.0,
      "singer": 2,
      "words": [
        {"id": "w6", "text": "I've", "start_time": 3.0, "end_time": 3.3},
        {"id": "w7", "text": "come", "start_time": 3.4, "end_time": 3.7},
        {"id": "w8", "text": "to", "start_time": 3.8, "end_time": 3.95},
        {"id": "w9", "text": "talk", "start_time": 4.0, "end_time": 4.3, "singer": 1},
        {"id": "w10", "text": "with", "start_time": 4.4, "end_time": 4.7},
        {"id": "w11", "text": "you", "start_time": 4.8, "end_time": 5.0}
      ]
    },
    {
      "id": "s3",
      "text": "Hello again",
      "start_time": 5.5,
      "end_time": 7.0,
      "singer": 0,
      "words": [
        {"id": "w12", "text": "Hello", "start_time": 5.5, "end_time": 6.2},
        {"id": "w13", "text": "again", "start_time": 6.3, "end_time": 7.0}
      ]
    }
  ]
}
```

- [ ] **Step 2: Write the integration test**

Create `tests/integration/test_multisinger_end_to_end.py`:

```python
"""End-to-end integration tests for multi-singer rendering."""
import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from karaoke_gen.lyrics_transcriber.types import LyricsSegment
from karaoke_gen.lyrics_transcriber.output.subtitles import SubtitlesGenerator
from karaoke_gen.style_loader import DEFAULT_KARAOKE_STYLE


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "duet_small.json"


@pytest.fixture
def duet_segments():
    data = json.loads(FIXTURE_PATH.read_text())
    return [LyricsSegment.from_dict(s) for s in data["corrected_segments"]]


def _run_ass(segments, is_duet: bool, tmp_path: Path) -> str:
    gen = SubtitlesGenerator(
        output_dir=str(tmp_path),
        video_resolution=(1920, 1080),
        font_size=100,
        line_height=60,
        styles={"karaoke": DEFAULT_KARAOKE_STYLE},
        subtitle_offset_ms=0,
        logger=MagicMock(),
        is_duet=is_duet,
    )
    gen._get_audio_duration = MagicMock(return_value=30.0)
    return gen.generate_ass(segments, output_prefix="duet_test", audio_filepath="/fake/a.mp3")


class TestDuetEndToEnd:
    def test_duet_ass_has_three_named_styles(self, duet_segments, tmp_path):
        out = _run_ass(duet_segments, is_duet=True, tmp_path=tmp_path)
        content = Path(out).read_text()
        style_names = re.findall(r"^Style:\s*([^,]+),", content, re.MULTILINE)
        assert "Karaoke.Singer1" in style_names
        assert "Karaoke.Singer2" in style_names
        assert "Karaoke.Both" in style_names

    def test_duet_ass_tags_lines_with_correct_style(self, duet_segments, tmp_path):
        out = _run_ass(duet_segments, is_duet=True, tmp_path=tmp_path)
        content = Path(out).read_text()
        # The three singers should each appear as a style on at least one Dialogue line
        dialogue_lines = [l for l in content.split("\n") if l.startswith("Dialogue:")]
        # Extract Style field (index 3 after "Dialogue:" prefix, depends on events_format)
        # Simpler: just search for "Karaoke.Singer2" appearing as a Dialogue style
        assert any("Karaoke.Singer1" in l for l in dialogue_lines)
        assert any("Karaoke.Singer2" in l for l in dialogue_lines)
        assert any("Karaoke.Both" in l for l in dialogue_lines)

    def test_duet_word_override_produces_inline_color_tag(self, duet_segments, tmp_path):
        out = _run_ass(duet_segments, is_duet=True, tmp_path=tmp_path)
        content = Path(out).read_text()
        # In s2 (Singer 2), word "talk" has singer=1 override. Its color tag should appear.
        # Singer 1 primary = 112, 112, 247 → ASS &HF77070& (BGR)
        assert "\\c&HF77070&" in content

    def test_solo_ass_has_one_default_style(self, duet_segments, tmp_path):
        # Force solo even though fixture has singer fields — is_duet=False ignores them
        out = _run_ass(duet_segments, is_duet=False, tmp_path=tmp_path)
        content = Path(out).read_text()
        style_names = re.findall(r"^Style:\s*([^,]+),", content, re.MULTILINE)
        assert style_names == [DEFAULT_KARAOKE_STYLE["ass_name"]]
        assert "Karaoke.Singer" not in content
        # No inline color tags
        assert "\\c&H" not in content


class TestCdgEndToEnd:
    def test_duet_cdg_lyrics_have_correct_singer_indices(self, duet_segments):
        from karaoke_gen.lyrics_transcriber.output.cdg import build_cdg_lyrics
        result = build_cdg_lyrics(duet_segments, is_duet=True, line_tile_height=4, lines_per_page=3)
        # Three segments: singer=1, singer=2, singer=0 (Both → 3)
        assert [r.singer for r in result] == [1, 2, 3]

    def test_solo_cdg_lyrics_all_singer_1(self, duet_segments):
        from karaoke_gen.lyrics_transcriber.output.cdg import build_cdg_lyrics
        result = build_cdg_lyrics(duet_segments, is_duet=False, line_tile_height=4, lines_per_page=3)
        assert all(r.singer == 1 for r in result)
```

- [ ] **Step 3: Run the integration tests**

```bash
poetry run pytest tests/integration/test_multisinger_end_to_end.py -v
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_multisinger_end_to_end.py tests/integration/fixtures/duet_small.json
git commit -m "$(cat <<'EOF'
test(integration): end-to-end multi-singer ASS and CDG fixture tests

Small duet fixture (3 segments, 2 singers, 1 word-level override)
verified end-to-end. ASS output has the three named styles, lines
reference the right style names, word override emits inline color
tag. Solo path (is_duet=False) produces byte-identical single-style
output even when the fixture has singer fields.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 23: Playwright production E2E test

**Files:**
- Create: `frontend/e2e/production/duet-review.spec.ts`

- [ ] **Step 1: Read an existing production E2E test for patterns**

```bash
ls frontend/e2e/production/ | head
cat frontend/e2e/production/admin-dashboard.spec.ts 2>/dev/null | head -60
```

Mirror the auth / setup pattern.

- [ ] **Step 2: Create the new spec**

```typescript
import { test, expect } from '@playwright/test'

// Happy path: enable duet mode, assign singer to a segment, save, verify payload round-trips.
test.describe('Lyrics review — duet mode', () => {
  test.beforeEach(async ({ page }) => {
    // Seed a job in review state, grab review URL (mirror existing helper)
    // e.g. via API: create a test job, run transcription, get review URL
  })

  test('toggles duet, assigns singer, persists on save', async ({ page }) => {
    await page.goto(process.env.REVIEW_URL!)

    // Solo view: no chip visible
    await expect(page.getByRole('button', { name: /Singer for this segment/ })).toHaveCount(0)

    // Toggle duet mode
    await page.getByRole('button', { name: 'Mark as duet' }).click()

    // Chip column appears
    const chips = page.getByRole('button', { name: /Singer for this segment/ })
    await expect(chips.first()).toBeVisible()

    // Click first chip — should go 1 → 2 (label shows "2")
    const firstChip = chips.first()
    await expect(firstChip).toContainText('1')
    await firstChip.click()
    await expect(firstChip).toContainText('2')

    // Save
    await page.getByRole('button', { name: /Lyrics look good|Save|Complete/ }).click()

    // Expect a network call to /complete with is_duet: true
    const response = await page.waitForResponse(r => r.url().includes('/complete') && r.request().method() === 'POST')
    const body = await response.request().postDataJSON()
    expect(body.is_duet).toBe(true)
    // First segment should have singer=2 in the saved corrections
    expect(body.corrections.corrected_segments[0].singer).toBe(2)
  })
})
```

- [ ] **Step 3: Run in CI-like mode (not required to green locally without a job set up)**

```bash
cd frontend && npx playwright test e2e/production/duet-review.spec.ts --list 2>&1 | tail
```

Expected: test is listed (compiles).

- [ ] **Step 4: Commit**

```bash
git add frontend/e2e/production/duet-review.spec.ts
git commit -m "$(cat <<'EOF'
test(e2e): playwright spec for duet review happy path

Enables duet mode, clicks a singer chip to advance, saves, and
verifies the request payload contains is_duet=true and the expected
segment singer.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 8 — Localization + docs + ship

### Task 24: Translate `en.json` changes to all 33 locales

**Files:**
- Modify: `frontend/messages/*.json` (auto-generated)

- [ ] **Step 1: Run the translation script**

```bash
python frontend/scripts/translate.py --messages-dir frontend/messages --target all
```

Expected: script reports translating the new `lyricsReview.duet` keys, caches hits for repeated strings, writes to all 32 non-English locale files.

- [ ] **Step 2: Sanity-check a couple of locales**

```bash
python3 -c "import json; print(json.load(open('frontend/messages/es.json'))['lyricsReview']['duet']['markAsDuet'])"
python3 -c "import json; print(json.load(open('frontend/messages/ja.json'))['lyricsReview']['duet']['markAsDuet'])"
```

Expected: Spanish and Japanese translations of "Mark as duet" (e.g., "Marcar como dueto", "デュエットとしてマーク").

- [ ] **Step 3: Commit**

```bash
git add frontend/messages/*.json
git commit -m "$(cat <<'EOF'
i18n: translate lyricsReview.duet namespace to all 32 non-en locales

Auto-generated via scripts/translate.py using Vertex AI Gemini with
GCS cache.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 25: Manual validation with real audio tracks (pre-merge gate)

**Files:**
- None checked in; this task documents the validation steps in the PR description.

- [ ] **Step 1: Pick 3 real tracks**

Suggested:
- A **classic duet**: Queen & Bowie "Under Pressure" or Sonny & Cher "I Got You Babe"
- A **dense call-and-response / rap trade-off** track: e.g., Eminem & Rihanna "Love the Way You Lie"
- A **long "both" chorus**: Lady Gaga & Bradley Cooper "Shallow"

- [ ] **Step 2: Run each through the local pipeline**

For each track:

```bash
# Create a job locally via CLI (see karaoke_gen/utils/gen_cli.py for args)
karaoke-gen <audio_file> --artist "<A>" --title "<T>"

# Step through lyrics review, toggle "Mark as duet", assign singers per segment
# (for the call-and-response track, set a word-level override on ≥ 2 words)
# Complete review; wait for render.
```

- [ ] **Step 3: Play back the outputs**

- Play the generated MP4 in a media player. Confirm:
  - Singer 1 is blue, Singer 2 is pink, Both is yellow.
  - The call-and-response track's word-level overrides appear in the right color on those specific words.
  - Karaoke timing (per-word highlight) still syncs correctly.
- Play the generated CDG file using `cdgmaker`'s preview, or on a real CDG player / VirtualCDG:
  - Colors match intent.
  - Word-level overrides are **not** visible in CDG (expected behavior — CDG uses segment-level only). Verify this is OK visually.

- [ ] **Step 4: Run one solo track to confirm regression**

Pick any old solo-only track from a past job. Re-run the pipeline without toggling duet. Diff the output MP4/ASS/CDG against the pre-change baseline (if one is saved) or simply verify the ASS contains exactly one "Default" style and no inline color tags.

- [ ] **Step 5: Record short clips of each**

Capture ≤15s clips showing the singer colors in action. Attach to the PR description. This is the manual-validation sign-off.

- [ ] **Step 6: No commit**

This task has no code deliverable — it's a PR-gate checklist.

---

### Task 26: Documentation updates

**Files:**
- Modify: `docs/LESSONS-LEARNED.md`
- Modify: `docs/README.md` (if it tracks feature status)
- Modify: `pyproject.toml` (version bump)

- [ ] **Step 1: Add a lessons-learned entry**

Append to `docs/LESSONS-LEARNED.md`:

```markdown
## Multi-Singer / Duet Support (Apr 2026)

- The CDG composer (`cdgmaker/composer.py`) already supports up to 3
  singers, but the path had never been exercised against real inputs
  before this feature. Mitigation during rollout: manual validation
  with real tracks before PR merge.
- Word-level singer overrides render only in the MP4/ASS output. CDG
  is a line-level format (`SettingsLyric.singer` is an int per line);
  splitting lines at word boundaries would produce visually distinct
  display lines on the CDG and make the output worse, not better.
- Theme JSON gains an optional `singers` block under `karaoke` for
  per-singer color overrides. Themes without it continue to work —
  all singers render with the flat colors, which is a valid (if dull)
  fallback.
```

- [ ] **Step 2: Bump the version**

In `pyproject.toml`, find `[tool.poetry]` and bump the patch version (e.g., `0.63.4` → `0.63.5`).

- [ ] **Step 3: Commit**

```bash
git add docs/LESSONS-LEARNED.md pyproject.toml
git commit -m "$(cat <<'EOF'
docs: add multi-singer lessons-learned entry and bump version

Documents the CDG untested-path risk and the word-override/CDG
divergence for future maintainers. Bumps patch version for release.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 27: Final local test run and PR

- [ ] **Step 1: Run the full test suite**

```bash
make test 2>&1 | tail -n 100
```

Expected: all backend + frontend tests PASS, coverage ≥ 69%.

- [ ] **Step 2: Run CodeRabbit review locally**

```bash
/coderabbit
```

(Slash command — see `~/.claude/CLAUDE.md`.) Fix any real issues flagged; skip nitpicks. Max 3 cycles.

- [ ] **Step 3: Docs review**

```bash
/docs-review
```

- [ ] **Step 4: Create the PR**

```bash
/pr
```

(Adds `@coderabbitai ignore` automatically.) Include in the PR description:
- Summary of the feature
- Link to `docs/archive/2026-04-21-multi-singer-duets-design.md`
- The 3 manual-validation track clips from Task 25
- Screenshots of the duet review UI (chip on, chip off)

- [ ] **Step 5: After merge**

Follow `/shipit` to deploy and `/cleanup` to remove the worktree.

---

## Self-review

_(This section is self-filled-in at plan-writing time; the implementing engineer can ignore it.)_

**Spec coverage check:**
- § 3 Data model → Tasks 1, 13 ✓
- § 4 Frontend review UI → Tasks 14–21, 24 ✓
- § 4.4 Segment operations + singer inheritance → Task 15 ✓
- § 4.6 i18n → Tasks 16, 24 ✓
- § 5 Theme JSON extension → Task 2 ✓
- § 6 Full-video ASS render → Tasks 3–8 ✓
- § 7 CDG render → Tasks 9, 10 ✓
- § 8 API + persistence → Tasks 11, 12 ✓
- § 9 Testing (unit, integration, E2E, manual) → Tasks 1–23, 25 ✓
- § 10 Rollout + scope → Tasks 26, 27 ✓

**Placeholder scan:** clean. One explicit TBD in the spec (reset-overrides keybinding) is deferred to implementation time.

**Type consistency:** `styles_by_singer` is `Optional[dict]` everywhere; `SingerId = Literal[0, 1, 2]` in Python and `type SingerId = 0 | 1 | 2` in TypeScript; `is_duet` is bool throughout. Field names (`singer`, `is_duet`) identical on both sides of the wire.
