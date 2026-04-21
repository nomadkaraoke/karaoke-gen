# Multi-Singer / Duet Support — Design Spec

**Date:** 2026-04-21
**Status:** Design approved, awaiting implementation plan
**Branch:** `feat/sess-20260421-1248-multi-singer-duets`

## 1. Summary

Enable karaoke tracks with multiple vocalists (duets) by letting the user mark segments — and individual words — as sung by Singer 1, Singer 2, or Both during the lyrics review phase. The final MP4 video and CDG file render each singer's lyrics in a distinct color.

Backward compatible: solo jobs ignore all duet logic and render byte-identically to today.

## 2. Scope decisions

| Decision | Choice |
|---|---|
| Number of voices | 3: Singer 1, Singer 2, Both |
| Labels | Fixed ("Singer 1" / "Singer 2" / "Both") — no user-editable names |
| Granularity | Segment-level default with optional per-word override |
| Default assignment | Everything starts as Singer 1; user marks exceptions |
| Opt-in | A per-job "Mark as duet" toggle in the review UI |
| CDG colors | Hardcoded defaults (palette budget is tight) |
| Video/ASS colors | Configurable per theme JSON, with sensible defaults in the `nomad` theme |
| "Both" render | Distinct third color in both CDG and ASS |
| UI interaction | Inline chip per segment (cycles 1→2→Both→1), plus `1`/`2`/`B` keyboard shortcut on focused segment |
| Word-level override | Shift-select words, press `1`/`2`/`B`; segment chip shows `● 1*` when overrides exist |

**Non-goals:** >3 voices, user-editable names, per-job custom color pickers, split-word rendering, singer assignment in plain-text/LRC outputs, singer labels on rendered video, CLI flags for singer assignment.

## 3. Data model

A new literal type captures singer id:

```python
# Singer id. 1 = Singer 1, 2 = Singer 2, 0 = Both. None/absent = default (Singer 1).
SingerId = Literal[0, 1, 2]
```

**Shared types** — both `frontend/lib/lyrics-review/types.ts` and `karaoke_gen/lyrics_transcriber/types.py` gain:

- `LyricsSegment.singer: Optional[SingerId]` — the segment's default singer
- `Word.singer: Optional[SingerId]` — word-level override; absent means inherit from the segment

**Job-level flag** — persisted alongside the existing `instrumental_selection` in the review session's `state_data`, and propagated to the render worker:

```python
state_data = {
    "instrumental_selection": "with_backing",
    "is_duet": True,  # NEW — only true when the user toggled it on
}
```

**On-disk shape** (`jobs/{job_id}/lyrics/corrections_updated.json`) — `singer` fields appear only when set. Absent keys are valid and mean default. Every existing stored correction stays valid unchanged; no migration.

## 4. Frontend review UI

All changes live in `frontend/components/lyrics-review/` and its existing interaction patterns.

### 4.1 Duet toggle

A new button **"Mark as duet"** in the top toolbar next to Edit / Highlight / Delete / Undo. When off, no singer UI renders and saved payloads omit all singer fields. When on, the chip column appears and `is_duet=true` is persisted to the review session.

### 4.2 Per-segment chip

Rendered between the play (▷) icon and the first word on each segment row:

- `● 1` (blue), `● 2` (pink), `● Both` (yellow)
- Click cycles `1 → 2 → Both → 1`
- The row gets a subtle left-edge gradient in the singer color for quick visual scanning
- When any word in the segment has an override, the chip shows `● 1*` (asterisk)

### 4.3 Keyboard

Matches the existing keyboard-handler pattern in `utils/keyboardHandlers.ts`.

- With a segment focused: `1` / `2` / `B` sets the segment's singer
- With words shift-selected: `1` / `2` / `B` sets word-level overrides
- An affordance to clear all per-word overrides in a segment (exact key TBD at implementation time — could be a context menu item on the chip or a dedicated shortcut)

**Shortcut collisions:** existing review hotkeys must be audited before claiming `1`, `2`, `B`. If any collide, remap or require a modifier (e.g. `Shift+1`).

### 4.4 Interaction with existing segment operations

| Operation | Singer behaviour |
|---|---|
| Split segment | Both halves inherit the parent segment's `singer`; word-level overrides stay with whichever half the word ends up in. |
| Merge segments with different singers | New segment takes the first segment's `singer`; words from both originals keep whatever they had, becoming word-level overrides relative to the new segment singer. No info is lost. |
| Delete segment | No impact. |
| Add new segment | Inherits the surrounding segment's singer (fits call-and-response flow). |

### 4.5 Preview Video

The existing "Preview Video" button needs no API changes — the renderer automatically picks up singer info from the corrections data, so the preview will already show the duet colors.

### 4.6 i18n

The lyrics review is under `frontend/app/[locale]/app/jobs/...` — fully localized via `next-intl`, NOT English-only. Every new user-visible string must:

- Be added to `frontend/messages/en.json` (suggested namespace: `lyricsReview.duet.*`)
- Be accessed via `useTranslations('lyricsReview.duet')` / `t('...')`
- Be auto-translated to all 33 locales via `python frontend/scripts/translate.py --messages-dir frontend/messages --target all` before PR (CI fails PRs with missing locale keys; a pre-commit hook handles this when enabled)

Unit snapshot tests that assert on text must use message keys or the mocked translator, not string literals.

## 5. Theme JSON extension

Today `DEFAULT_KARAOKE_STYLE` in `karaoke_gen/style_loader.py` has flat color keys (`primary_color`, `secondary_color`, `outline_color`, `back_color`). We extend with an optional per-singer block:

```jsonc
"karaoke": {
  // existing flat colors remain — used as Singer 1 defaults and for solo jobs
  "primary_color":   "112, 112, 247, 255",
  "secondary_color": "255, 255, 255, 255",
  "outline_color":   "26, 58, 235, 255",
  "back_color":      "0, 0, 0, 0",

  // NEW: optional per-singer overrides
  "singers": {
    "1":    { /* optional — inherits the flat colors above */ },
    "2": {
      "primary_color":   "247, 112, 180, 255",   // pink active
      "secondary_color": "255, 255, 255, 255",
      "outline_color":   "158, 26, 96, 255",
      "back_color":      "0, 0, 0, 0"
    },
    "both": {
      "primary_color":   "252, 211, 77, 255",    // yellow active
      "secondary_color": "255, 255, 255, 255",
      "outline_color":   "146, 108, 0, 255",
      "back_color":      "0, 0, 0, 0"
    }
  }
}
```

**Resolution rule** (in `style_loader.py`): for each singer id, start from the flat colors, then overlay any fields in `singers[id]`. Any singer with no entry inherits Singer 1's flat colors. Solo jobs never hit this code path.

**Default `nomad` theme** ships with a populated `singers` block (blue / pink / yellow as above). Existing custom user themes stay valid — they just render duet voices with identical flat colors until they add per-singer entries.

## 6. Full-video ASS render

Today `SubtitlesGenerator` (in `karaoke_gen/lyrics_transcriber/output/subtitles.py`) + the `ass/` module produce a single ASS `Style` named "Default" and every line uses it. We extend to N styles and per-line style assignment.

### 6.1 Style factory — `ass/style.py`

```python
def build_karaoke_styles(karaoke_style: dict, singers: list[SingerId]) -> list[Style]:
    """
    Returns one Style per requested singer id, named:
        Karaoke.Singer1, Karaoke.Singer2, Karaoke.Both
    Each style resolves its colors via karaoke_style + karaoke_style.get('singers', {}).get(id),
    falling back to the flat colors. Font / size / positioning are identical across singers.
    """
```

Solo jobs request a single style, named "Default" (same as today), with the same colors — produces byte-identical ASS to pre-change output.

### 6.2 Per-line style assignment — `ass/lyrics_line.py`

`LyricsLine` currently writes its `Dialogue` event with a hardcoded `Style: Default`. Change it to accept a `style_name` parameter, derived from the segment's singer.

**Word-level overrides** render as inline color tags on just the overridden words:
```
...{\c&HBBGGRR&}overridden word{\r}...
```
`{\r}` resets back to the line's base style, so the karaoke-timing `{\k}` tags stay intact and the line stays as one `Dialogue` event.

### 6.3 Wiring — `SubtitlesGenerator.generate_styled_subtitles`

1. Scan all corrected segments + word overrides → compute `singers_in_use`
2. If `singers_in_use == [1]` (or empty): behave exactly as today (single "Default" style)
3. Otherwise: build N styles via the factory, emit to ASS header, tag each line with the right `Karaoke.Singer{id}` style name

### 6.4 What we deliberately don't touch

- `cdgmaker/` — the CDG composer's internal ASS generation (lines 1970–1981 of `composer.py`) already handles multi-singer and stays independent. Main-video ASS and CDG-internal ASS evolve separately, as today.
- No per-singer font / size / shadow variations. Only color varies per singer.

## 7. CDG render

The CDG composer (`karaoke_gen/lyrics_transcriber/output/cdgmaker/composer.py`) already supports up to 3 singers via `config.singers: list[SettingsSinger]` and `SettingsLyric.singer: int`. Lines 231, 242, 1970–1981, and 2082 implement it. This path **appears untested against real inputs** — a pre-merge manual validation gate (§ 9.4) mitigates this.

### 7.1 Hardcoded CDG palette

New constant in `style_loader.py`:

```python
CDG_DUET_SINGERS = [
    # Singer 1: blue
    SettingsSinger(active_fill=(112,112,247), active_stroke=(26,58,235),
                   inactive_fill=(255,255,255), inactive_stroke=(80,80,80)),
    # Singer 2: pink
    SettingsSinger(active_fill=(247,112,180), active_stroke=(158,26,96),
                   inactive_fill=(255,255,255), inactive_stroke=(80,80,80)),
    # Both: yellow
    SettingsSinger(active_fill=(252,211,77),  active_stroke=(146,108,0),
                   inactive_fill=(255,255,255), inactive_stroke=(80,80,80)),
]
```

Solo jobs still get the current single-singer palette — no change.

### 7.2 Corrections → SettingsLyric adapter

New helper in `karaoke_gen/lyrics_transcriber/output/cdg.py`:

```python
def build_cdg_lyrics(segments, is_duet) -> list[SettingsLyric]:
    """
    If not is_duet: return exactly what we emit today (regression guard).
    Otherwise: walk segments, emit one SettingsLyric per line with
    singer=1/2/3 derived from the SEGMENT-level singer only.
    (Internal 'Both' = 0 maps to CDG singer 3 — CDG composer is 1-indexed.)
    """
```

The composer's text-based `"singer|text"` parsing path is bypassed — we feed the numeric `SettingsLyric.singer` field directly, which is supported out of the box (`config.py:96`).

**CDG ignores word-level overrides — by design.** The CDG composer's model is line-level singer (`SettingsLyric.singer: int`; `composer.py:1013` packs `fill = singer << 2 | 0` per line). Splitting a line at word-override boundaries would render the line as multiple visually distinct display lines on the CDG output, which is worse than just showing the segment's primary singer color. Word-level overrides therefore affect the MP4/ASS render only.

The frontend should communicate this: when duet mode is on AND any word-level overrides exist, surface a small note near the duet toggle ("Word-level singer overrides apply to the karaoke video only — CDG output uses the segment's main singer."). This is the only behaviour difference between the two output formats.

### 7.3 Palette budget check

3 singers × 4 colors = 12 slots + background + reserved = 16. Within the CDG composer's asserted limit. The composer's own `check_valid_config` validates this at load time.

## 8. API + persistence

### 8.1 Review save endpoint

`POST /api/review/{job_id}/complete` in `backend/api/routes/review.py` accepts `updated_data` containing corrected segments. The only change is extending the Pydantic schema for `LyricsSegment` and `Word` with an optional `singer: Optional[int]` field (validator: must be 0 / 1 / 2 if present). No new endpoint.

The `is_duet` flag is persisted alongside `instrumental_selection` in the review session's `state_data` (Firestore). Solo payloads serialize unchanged.

### 8.2 Render worker

`backend/workers/render_video_worker.py` reads `corrections_updated.json` — singer fields flow through naturally. Pass `is_duet` to the `OutputGenerator` config so it can short-circuit duet logic when false. Add `is_duet` to the GCE encoding-worker request payload for the same reason.

### 8.3 Dashboard summary projection (gotcha)

**If** we ever surface `is_duet` in the job dashboard summary, both allowlists must be updated (memory note — missing projection fields cause subtle dashboard bugs):

- `SUMMARY_FIELD_PATHS` in `backend/services/firestore_service.py`
- `_SUMMARY_STATE_DATA_KEYS` in `backend/api/routes/jobs.py`

Add a regression test asserting both contain the key when surfaced. Not required for MVP.

### 8.4 CLI parity

The local `karaoke-gen` CLI reads/writes the same corrections JSON shape; singer fields flow through for free. No CLI flags for MVP — editing happens in the review UI.

### 8.5 Migration / fixtures

Test fixtures with pre-existing corrections JSON remain valid (absent singer fields = solo). No mass-migration script.

## 9. Testing strategy

Per `docs/TESTING.md` — unit, integration, E2E, and manual.

### 9.1 Backend unit (pytest)

- `test_style_loader.py`: per-singer color resolution (flat-only, partial, full override, unknown id)
- `test_style_validation.py`: extended `singers` schema validates; legacy themes load unchanged
- New `test_multisinger_ass.py`:
  - `build_karaoke_styles` produces correct styles
  - `lyrics_line` tags the right style name
  - Word overrides emit `{\c&H...&}...{\r}`
  - **Solo job renders byte-identical ASS** to pre-change (strong regression guard)
- New `test_multisinger_cdg.py`:
  - `build_cdg_lyrics` adapter produces correct `SettingsLyric` entries
  - Segment-splitting on word-override boundaries
  - `CDG_DUET_SINGERS` passes `check_valid_config`
- `test_review_endpoint.py`: singer fields round-trip; solo payload unchanged; invalid singer values rejected

### 9.2 Frontend unit (Jest)

- `lyrics-review/__tests__/duet.test.tsx`:
  - Chip renders correct label / color
  - Click cycles `1 → 2 → Both → 1`
  - Keyboard `1` / `2` / `B` on focused segment
  - `R` resets per-word overrides
  - `*` appears when any word in the segment has an override
- Existing split / merge / delete / add tests extended to assert singer-inheritance rules from § 4.4
- Save payload includes `is_duet` + singer fields only when duet mode is on; solo jobs serialize byte-identically
- Snapshot guard: `is_duet=false` produces unchanged review UI markup (backward compat)
- i18n: every new string resolves via `t(...)`, no hardcoded literals

### 9.3 Integration — end-to-end fixtures

- Synthetic 30 s duet fixture (4 segments, 2 singers, 1 word-level override) runs through the full pipeline:
  - ASS header has expected styles; lines reference the right style names
  - CDG file decodes round-trip and shows expected palette indices on expected frames
- Solo fixture runs through and produces byte-identical outputs to pre-change baseline

### 9.4 E2E — Playwright

Production E2E test in `frontend/e2e/production/`: load a review job → toggle duet → assign singers → save → verify payload round-trips. Does not need a full render run.

### 9.5 Manual validation — pre-merge gate

**Called out explicitly because the CDG multi-singer path has never been exercised against real inputs.** Before the PR can merge:

- Pick 3 real songs covering tricky cases:
  - One classic duet (e.g., "Under Pressure")
  - One with dense call-and-response (rap trade-offs)
  - One with long "both" choruses
- Run each through the full pipeline locally, play back MP4 and CDG on a real CD+G viewer (or `cdgmaker`'s preview)
- Visual-check: colors match intent, timing still syncs, "Both" sections look right, solo songs render unchanged
- Record a short clip of each in the PR description

### 9.6 Explicitly not tested in automation

- Pixel-level visual fidelity of rendered video frames (covered structurally by ASS-level tests)
- Third-party CDG player palette rendering on real hardware (covered by manual validation)

## 10. Rollout, scope boundaries, non-goals

### 10.1 Rollout

- **No feature flag.** The "Mark as duet" toggle is the gate — existing jobs ignore duet logic until a user opts in. Silent-regression risk on solo jobs is mitigated by byte-identical ASS/CDG regression tests.
- **No database migration.** Singer fields are purely additive and optional.
- **No breaking API changes.** `updated_data` schema gains optional fields.
- **Version bump:** `tool.poetry.version` in `pyproject.toml`.

### 10.2 Suggested implementation order (to be detailed in the implementation plan)

1. Shared types + theme JSON extension (foundation, no user-visible change)
2. CDG adapter + `CDG_DUET_SINGERS` palette (backend-only)
3. ASS style factory + `lyrics_line` style-per-line wiring (backend-only)
4. Review endpoint schema + `is_duet` in state
5. Frontend review UI (chip, keyboard, duet toggle, i18n strings) + translation run
6. End-to-end integration tests
7. Manual validation with real tracks → PR

### 10.3 Known risks / watch items

- **CDG composer multi-singer path untested with real inputs.** Mitigation: manual validation gate in § 9.5.
- **Custom user themes without `singers` block** render all three voices identically. Acceptable — users opt in by adding singer colors to their theme.
- **Keyboard shortcut collisions** with existing review hotkeys (`1`, `2`, `B`, `R`). Audit required at implementation time; remap or add modifier if needed.

## 11. Out of scope (deferred)

- More than 3 voices (trios, choirs, ensembles). Revisit if real demand lands.
- User-editable singer names.
- Per-job custom color pickers in the review UI.
- Split-word rendering (word simultaneously showing two colors).
- Singer assignment flowing to plain-text / LRC exports.
- Singer labels / credits on rendered video (existing `extra_text` already covers this use case).
- CLI flags for singer assignment.
