# RTL (Hebrew/Arabic) rendering — investigation + fix options

**Trigger:** KaraokeHunt outreach reply from the requester (2026-09-30) on job `5710831e`
(Omer Adam – שני משוגעים, https://www.youtube.com/watch?v=AWYG9lXFywA):
"I noticed Hebrew/right to left languages don't format correctly, had that issue before, is that an easy fix?"

## Findings (measured, not eyeballed)

Method: extracted 30fps frames of `lossy_720p_mp4` (34–52s), classified pixels as unsung (white) vs
sung (highlight #7070F7), tracked highlight x-extent per glyph cluster over time; compared the static
line against Chrome-rendered references (correct RTL / LTR word order / no bidi) via column-profile
correlation. Then reproduced with local libass 0.17.4 + fribidi (`ffmpeg -vf subtitles=`) and A/B'd fixes.
Scratch tooling: `/tmp/heb/analyze.py`, `/tmp/heb/lab/` (worth turning into a regression test).

| # | Symptom | Evidence / root cause |
|---|---------|------------|
| 1 | Title card: `?` tofu boxes | Theme font (AvenirNext-Bold) has no Hebrew glyphs; `_get_font_path_for_text` only falls back for CJK. Glyph *order* on the card is already RTL (Pillow raqm works in prod). |
| 2 | **Whole line laid out LTR** — first sung word on the LEFT, highlight sweeps left→right across the line like English | Prod frame correlates with "LTR word order, letters correct" r=0.46–0.77 vs correct-RTL r≈0. Highlight at t=40.5s starts x=242 (line left edge) and grows to x=1040. Cause: style `Encoding=0` → libass forces LTR paragraph direction ("VSFilter compat"); the `{\kf}` tags between words split the text so each word is its own RTL run, laid out LTR. Reproduced locally identically. **Fix verified: `Encoding=-1`** (libass auto base direction) → layout correlates with correct-RTL r=0.78, first word lights at the right edge, progression right→left. |
| 3 | Within each word, `\kf` fill still wipes left→right | Upstream libass limitation ([libass#406](https://github.com/libass/libass/issues/406)), remains after the Encoding fix. **Verified workaround for Hebrew:** split each word's `\kf` across its letters → highlight grows monotonically from the right edge leftward (x 939→373 over 5 words), layout still correct-RTL (r=0.77). Not safe for Arabic (tags break joining) → Arabic: `\k` per word, or clip-animated sweep. |
| 4 | Lead-in square on the left of the line | `_create_lead_in_event` anchors at `text_left`, moves in from x=0. After fix #2 the first word is on the right → must anchor at `text_right` and move in from the right for RTL lines. |
| 5 | (latent) Hebrew measured with glyph-less font | `lyrics_line._get_font` measures with theme font → notdef widths; affects line fit + lead-in x. |
| 6 | (unverified) CDG + portrait | PIL + theme font → likely tofu; CDG wipe direction also LTR. |

Prod caveat: the encoding worker uses johnvansickle static ffmpeg (Packer `provision.sh`); prod output
matched local libass 0.17.4 exactly, but verify Encoding=-1 on the worker before claiming done.

## What shipped (v0.255.0)

The per-letter `\kf` idea was superseded by the libass#406 thread: since libass 0.15,
`{\frz180\frx180\fry180}` makes `\kf` fill right→left without splitting words (so Arabic
joins). Verified on the prod static ffmpeg in Docker.

1. `build_karaoke_styles` forces `Encoding=-1` (libass auto paragraph direction).
2. RTL lines (`ass/text_direction.is_rtl_text`, first strong bidi char) get
   `RTL_KARAOKE_FILL_TAGS`, re-emitted after every duet `{\r}` reset.
3. Lead-in mirrored for RTL (enters from the right, stops outside the right edge).
4. Text measurement mirrors libass: per-run fallback fonts, metric-derived size scale.
5. Glyph-coverage font fallback (`utils/font_fallback.py`) for title/end cards and
   portrait headers; CJK path now also falls back generically when Noto CJK is absent.
6. Karaoke `\k`/`\kf` computed from one absolute centisecond timeline (small gaps and
   rounding no longer make the highlight run early) — found by the new render tests.
7. Pixel-level render tests + Linux runner (see docs/TESTING.md). CI installs Noto fonts.

Not done: CDG output (`cdgmaker/composer.py`) still draws with PIL + theme font and wipes
LTR — separate follow-up.

## Testing
- Render tests: `tests/unit/lyrics_transcriber/output/test_ass_render_highlight.py` (120 cases
  pass on Debian ffmpeg and prod static ffmpeg; 32 fail on the pre-fix code; mutation checks
  for "Encoding only" and "no re-emit after \r" each fail the expected cases).
- Title cards: `tests/unit/test_font_fallback_render.py`. Units: `test_rtl_ass_output.py`,
  `test_font_fallback.py`.
- After deploy: re-render job `5710831e`, check title + sweep, update YouTube, reply to the requester.
