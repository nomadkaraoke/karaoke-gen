# Divebar Index Filename-Parsing Fix — Plan

_Worktree: `karaoke-gen-divebar-index-parsing` · branch `feat/sess-20260628-0511-divebar-index-parsing`_
_Picks up: HANDOFF-divebar-mirror-parsing.md (kjbox side shipped v0.41.1; this is the upstream index fix)_

## Problem (refined by live data, not just the handoff examples)

The Divebar Drive→BigQuery indexer (`infrastructure/functions/divebar_mirror/filename_parser.py`,
Cloud Function `divebar-mirror`) mis-parses community-brand filenames. kjbox cross-references
mirror files on `(normalized artist, normalized title)` + brand, so bad parses → mirror files
never surface in the rotation selector even though the file is in GCS.

### Measured state (BigQuery `karaoke_decide.divebar_catalog`, 50,046 rows)

| Signal | Count | Meaning |
|---|---|---|
| null `artist` | 164 (0.3%) | rare |
| null `title` | 0 | — |
| **null `brand_code`** | **18,437 (37%)** | biggest visible gap |
| `artist` is 2–6 all-caps/digits | 1,908 | brand-code-as-artist mis-split (e.g. `CKK`, `EBK`) |
| `artist` starts with `(` | 3,051 | `(SDK) Artist` prefix (Sandell) |
| `title` contains ` - ` | 5,947 | unstripped ` - Brand` suffix / disc-id-in-title |
| `title` ends in `[..]`/`(..)` | 2,583 | `[DJ Sauly Karaoke]`, `[Karaoke]`, etc. |

Current xref coverage: 90,719 matches, 17,261 KN songs, **23,318 / 50,046 divebar files matched (47%)**.

> Reframe vs handoff: `artist`/`title` are rarely *null*, but **mis-splits + polluted titles**
> are common and silently defeat the EXACT xref (`LOWER(TRIM(kn.Title)) = db.title_normalized`).
> Every polluted title misses the join. That + missing `brand_code` is the real lever.

### Root cause: parser has only 3 rigid patterns and never uses the folder brand to clean

`parse_filename(name, brand_folder)`:
1. `^CODE-### - Artist - Title` (disc-id prefix) — works.
2. `... (BrandTag)$` — strips ANY trailing paren (so it also eats `(KARAOKE)`, `(Live)`,
   `(Banda Remix)` → clobbers `brand_name`), and never sets `brand_code`.
3. `Artist - Title` fallback split on ` - `.

It ignores `[...]` brackets, ` - Brand` dash-suffixes, disc-id *suffixes*, `(CODE) ` prefixes,
and `CODE - ` (no-number) prefixes. The folder name (reliable brand) is passed in but only used as
a fallback `brand_name` — **never to strip the brand token before splitting artist/title**.
`_KARAOKE_SUFFIXES` is applied only to the normalized fields, never the display `title`.

Note: `build_rows` writes `brand` = folder (always, reliable) and only consumes `brand_code` from
the parser (not `brand_name`) — so Pattern-2's `brand_name` clobbering is harmless. The fields that
matter from the parser are **artist, title, brand_code, disc_id**.

## Real-world conventions (gold fixtures, observed live)

| Convention | Example filename | Brand (folder) | Correct artist / title |
|---|---|---|---|
| disc-id prefix (OK today) | `FATBIRD-163 - Incubus - I Miss You.zip` | Fatbird | Incubus / I Miss You |
| code prefix, no number | `CKK -  Buoys, The - Timothy.zip` | Cereal Killer Karaoke | Buoys, The / Timothy |
| `(CODE)` paren prefix | `(SDK) (G)I-DLE - Queencard.cdg` | Sandell Karaoke | (G)I-DLE / Queencard |
| `(Brand)` paren suffix (OK today) | `10 Years - Fix Me (BellySings).mp4` | BellySings | 10 Years / Fix Me |
| `[Brand]` bracket suffix | `3rd Bass - Green Eggs And Swine [DJ Sauly Karaoke].zip` | DJ Sauly Collection | 3rd Bass / Green Eggs And Swine |
| ` - Brand` dash suffix | `100 Gecs - Last Train To Awesometown - Rock Solid Karaoke.zip` | Rock Solid Karaoke | 100 Gecs / Last Train To Awesometown |
| ` - CODE` dash suffix | `Akon - Right Now (Na Na Na) - EBK.avi` | ForteKaraoke Discord | Akon / Right Now (Na Na Na) |
| disc-id *suffix* | `Against Me - Wagon Wheel - SOKC-0306 - (KARAOKE).zip` | Steve-O Karaoke Cult | Against Me / Wagon Wheel |
| disc-id suffix no dash | `Angelcorpse - Wolflust - ATG0014.zip` | ATG Karaoke | Angelcorpse / Wolflust |
| `[Karaoke]` generic tag | `Bill Murray - Star Wars [Karaoke].mp4` | Mobile Karaoke Unit | Bill Murray / Star Wars |
| mixed-case brand prefix | `Lopenash-002 - Dexter's Laboratory - Breathe in The Sunshine.mp4` | Lopenash | Dexter's Laboratory / Breathe in The Sunshine |
| clean (only brand_code missing) | `A Tribe Called Quest - Lyrics To Go.avi` | WIZZYOKE | A Tribe Called Quest / Lyrics To Go |

**Guard cases (must NOT over-strip):** `(G)I-DLE`, `R.E.M.`, artist `311`; titles `(Live)`,
`(Banda Remix)`, `(Single Version 2 WBV)`, `(99X Live)`, `[Acoustic]` must survive.

## Design — folder-brand-aware parser

Core principle (per handoff): **the top-level folder reliably names the brand; use it to recognise
and strip the brand token wherever it sits, then split the remainder into artist/title.**

`parse_filename(name, brand_folder)` rewrite, in order:

1. **Strip generic karaoke tags** from the display name: trailing `(karaoke|instrumental|backing
   track|no vocals|kj version|with vocals|demo)` in either `()` or `[]` (reuse `_KARAOKE_SUFFIXES`,
   now applied to display too).
2. **Build brand matchers from `brand_folder`**: normalized full name, and name with trailing
   descriptors removed (`karaoke`, `collection`, `cdg files`, `discord`, `videos`, `mp4`,
   `zipped mp3+g`, etc.); plus an acronym of significant words (Cereal Killer Karaoke→CKK,
   Steve-O Karaoke Cult→SOKC, ATG Karaoke→ATG).
3. **Strip the brand token from whichever position it occupies — each gated so we only remove a
   *known* brand, never song text:**
   - disc-id **prefix** `^CODE[-\s]?### - ` → `brand_code`, `disc_id`. (existing)
   - disc-id **suffix** ` - CODE-### ` / ` CODE#### ` at end, gated on `CODE` ≈ folder acronym/known → `brand_code`,`disc_id`.
   - `[Brand]`/`(Brand)` **bracket/paren suffix** where content ≈ folder brand → strip; `brand_code` from folder map.
   - ` - Brand` **dash suffix** where the last ` - ` segment ≈ folder brand → strip.
   - `(CODE) ` **paren prefix** (≤6 chars, alnum) with content after → strip; `brand_code`=CODE.
   - `CODE - ` / `Folder-### - ` **prefix** where token ≈ folder acronym/name → strip; `brand_code`.
4. **Split remainder** on ` - ` → artist = parts[0], title = join(parts[1:]); strip any leftover
   generic karaoke tag from title.
5. **brand_code resolution order:** captured-during-strip → small curated **folder→code map** →
   null. `brand` always = folder (unchanged in `build_rows`).

### Brand registry decision (diverges from handoff)

Handoff suggested importing kjbox `version_priority.py` `COMMUNITY_BRANDS`. **Rejected** because:
(a) cross-repo import isn't viable inside a GCP Cloud Function, (b) that registry is **incomplete**
vs the 63 real Divebar folder brands (no Cereal Killer/DJ Sauly/Rock Solid/Sandell), and (c) it
**conflicts** — registry maps `SDK`→SNDL but Sandell's folder uses `(SDK)`. Instead: derive
`brand_code` from the in-filename token / folder, and keep a **small curated `folder→KN-code` map**
seeded from the actual KN community codes that intersect Divebar folders (FBK, BELLY, DJS, C11K,
PLAY, REEKIES, HALJAM, NOMAD, FAKEY, CC, …). kjbox's read-time `canonical_brand_for_match` already
folds codes/names, so the index doesn't need canonical output — just resolvable.

## Refined design from full 63-brand data (read-only export)

Exported all 50,046 rows + per-brand samples + KN community codes to scratchpad. Key refinement:
**artist is wrong-but-rarely-null; the measurable gaps are polluted titles + null brand_code.**
Brands needing a fix fall into these convention buckets (✅ already-correct buckets only need the
brand_code map):

| Bucket | Brands (examples) | Fix |
|---|---|---|
| disc-id PREFIX `CODE-### - A - T` | Nomad, Imperfekt, Play, Gnome, Fakey, C11K, Reekies, Fatbird, … | works today — **preserve** |
| disc-id PREFIX space-sep `CODE-#### Artist - T` | Potos (`RABZ-0001 The Seatbelts - …`) | allow space (not just ` - `) after disc-id |
| disc-id PREFIX mixed-case/digit-lead `Tok-### - …` | Lopenash (`Lopenash-002 -`), 7uLpA (`7uLpA-0003 -`) | match when prefix token ≈ folder name |
| leading track-no after disc-id `CODE-### - NN - A - T` | Mix Tape (`MIX001 - 02 - …`) | drop leading pure-numeric segment |
| disc-id SUFFIX `A - T - CODE-####` | It Might Be (IMBK), Steve-O (SOKC), ATG (ATG####) | **strip trailing disc-id (unconditional), set brand_code/disc_id** |
| `(CODE)` paren PREFIX | Sandell (`(SDK) A - T`) | strip leading `(short-alnum) ` |
| `CODE - ` PREFIX no number | Cereal Killer (`CKK - A - T`), Nerdy (`TNSK -`), Crossfire (`Crossfire - A - T`) | strip when token ≈ folder acronym/name |
| `[Brand]` bracket SUFFIX | DJ Sauly (`… [DJ Sauly Karaoke]`) | strip bracket suffix gated on folder-name match |
| `(Brand)` paren SUFFIX | BellySings (`… (BellySings)`), Popeoke (`(popeoke)`/`[popeoke]`) | already strips paren; add bracket + folder-gate |
| ` - Brand` dash SUFFIX | Rock Solid (`… - Rock Solid Karaoke`), Forte (`… - EBK`) | strip last ` - ` segment gated on folder-name match (Forte EBK via per-folder token) |
| generic karaoke tag `[Karaoke]`/`(Karaoke HD)` | Mobile, Funbox, Matt Joy, Steve-O | strip generic tag from **display** title (currently only normalized), tolerate trailing words |
| clean `Artist - Title`, only brand_code missing | Funbox, WIZZYOKE, Brilliant Trash, Monster Tracks, KaddaOK, April's Choice | **curated folder→code map only** |

### Parser algorithm (folder-brand-aware), ordered

1. strip extension; `format = detect_format`.
2. strip generic karaoke tags from display name (both `()`/`[]`, token optionally followed by words).
3. brand-token strip, first match wins, each sets `brand_code`/`disc_id` and removes the token:
   (a) disc-id PREFIX `^CODE[-\s]?NUM` followed by ` - ` **or** whitespace; CODE all-caps **or** ≈ folder token (covers Lopenash/7uLpA/Potos);
   (b) disc-id SUFFIX `[-\s]\s*CODE-?NUM$` — **unconditional** (trailing `ABCD-0123` is a strong signal);
   (c) `[Brand]`/`(Brand)` SUFFIX gated on `norm(content) ≈ norm(folder)`;
   (d) ` - Brand` dash SUFFIX gated on `norm(last segment) ≈ norm(folder)` (or per-folder token);
   (e) `(CODE)` paren PREFIX (≤6 alnum) with content after;
   (f) `CODE - ` PREFIX (no number) gated on `CODE ≈ folder acronym/name`.
4. split remainder on ` - ` → artist=parts[0], title=join(rest); drop a leading pure-numeric segment;
   1 part → title only.
5. strip any leftover generic karaoke tag from title.
6. `brand_code` = captured → **curated folder→code map** → null. `brand` always = folder (in `build_rows`).

**Guards (must survive):** `(G)I-DLE`, `R.E.M.`, artist `311`, titles `(Live)`, `(Banda Remix)`,
`(Single Version 2 WBV)`, `(99X Live)`, `[Acoustic]`. Folder-gating + "disc-id has digits" +
"prefix code ≈ folder" are what prevent eating these.

### Curated folder→KN-code map (minimal, high-confidence only)

Aligned to actual KN community `Brand` codes (so the secondary brand_match xref can also fire);
brands with no confident KN code are left null (artist/title fix already surfaces them via the
primary 0.95 exact xref):

```
Funbox Karaoke→FBK · Sandell Karaoke→SDK · BellySings Karaoke→BELLY · DJ Sauly Collection→DJS
PlayKaraoke→PLAY · Reekies Karaoke→REEKIES · Cereal Killer Karaoke→CKK · WIZZYOKE→WIZZY
Brilliant Trash Karaoke→BTRASH · Lopenash→LOPE · Monster Tracks→MTRAX · Popeoke→POPE
KaddaOK→KADDA · Potos Power Plant Productions→PPPP · April's Choice Karaoke→APRIL
The Nerdy Singer Karaoke→TNS · Mobile Karaoke Unit→MKU · ATG Karaoke - Zipped MP3+G→ATG
It Might Be Karaoke→IMBK
```
(Map *overrides* a prefix-derived code where they differ, e.g. Play `PLK`→`PLAY`, Reekies
`REEK`→`REEKIES`; `disc_id` keeps the literal prefix.) Skipped (no confident code): Rock Solid,
Steve-O (uses SOKC, not a KN code — set from disc-id suffix anyway), ForteKaraoke (EBK≠KN), Gnome,
7uLpA, Crossfire, Monster… handled where a code exists.

## Work breakdown

**Phase 1 — Parser rewrite (TDD, primary deliverable).** New `tests/unit/test_divebar_filename_parser.py`
with all gold fixtures + guard cases (add function dir to `sys.path`; parser deps are stdlib only).
Rewrite `filename_parser.py` to the design above. Keep `normalize_for_search`, `detect_format`,
`should_index_file` behaviour. Keep existing `test_divebar_index_builder.py` green.

**Phase 2 — brand_code coverage.** Curated `folder→code` map + disc-id-suffix / leading-token
capture. Target: cut null `brand_code` well below 37% on the offline harness.

**Phase 3 — Deploy enablement (infra papercut).** The module points the function at a *static*
source object (`divebar-mirror-source.zip`) with no content hash, so overwriting it won't make
`pulumi up` redeploy. Switch `modules/divebar_mirror.py` to `pulumi.FileArchive` + `BucketObject`
(the proven pattern in `modules/runner_manager.py`) so new code deploys on `pulumi up`. (Optional
but needed for the fix to ship cleanly; Andrew runs the apply.)

**Phase 4 — Offline validation harness + prod verify.**
- Export distinct `(filename, brand)` from BQ (read-only) → run NEW parser locally over all 50k →
  report before/after: % null brand_code, % artist-looks-like-code, % title-has-dash, % title-ends-bracket,
  plus a spot-check sample. **Proves improvement with zero prod writes** (fits read-only ADC).
- After Andrew deploys + triggers `divebar-mirror-daily` then `divebar-xref-rebuild-daily`
  (MERGE preserves `gcs_path`; no Drive→GCS re-sync needed): re-measure xref `db_files` matched
  (baseline 23,318/50,046) and the live `incubus admiration` repro.

## Constraints / notes

- **My ADC is read-only** (workspace memory `feedback_claude_readonly_adc`): I can read BQ and run
  the offline harness, but **cannot** `pulumi up`, upload source, or run scheduler jobs. Andrew
  deploys + re-indexes; I validate offline first and via BQ reads after.
- Re-index re-parses ALL files (full Drive listing → MERGE by `file_id`), so existing rows are
  corrected in place; `gcs_path` is preserved.
- Version bump `pyproject.toml`; this is infra/function code (no i18n, no frontend).
- Tests: `make test` (or targeted `pytest tests/unit/test_divebar_filename_parser.py`).

## Results (offline harness over all 50,046 live rows)

| metric | before | after | delta |
|---|---|---|---|
| null brand_code | 18,437 (37%) | 1,619 (3%) | **−16,818** |
| title contains ` - ` | 5,947 | 2,523 | −3,424 (residual mostly legit multi-part titles) |
| artist looks like a code | 1,908 | 1,400 | −508 (residual are real bands: ACDC, KISS, ABBA, U2…) |
| null artist | 164 | 179 | +15 (medleys / single-field — acceptable) |
| rows with changed artist/title | — | 13,348 (26.7%) | — |

Per-brand `brand_code` null% collapsed to ~0% for every broken brand (Sandell, BellySings,
DJ Sauly, Cereal Killer, Steve-O, WIZZYOKE, Lopenash, ForteKaraoke, ATG, Mobile, 7uLpA, …);
Rock Solid stays null by design (no confident KN code).

**Xref lift (replaying the live `_rebuild_xref` EXACT join):** Divebar files that match a KN
song **20,802 → 23,394 (+2,592, +12.5%)** — i.e. that many more mirror files now surface in kjbox.

### BUNDLED: the xref SQL normalization asymmetry (fixed in this PR)

`_rebuild_xref` joined `LOWER(TRIM(kn.Artist)) = db.artist_normalized`, but `db.artist_normalized`
is produced by `normalize_for_search` (strips diacritics, a leading "the ", and all punctuation)
while the KN side got only `LOWER(TRIM(...))` — so most rows could never match. **Fix:** a new
`_norm_sql(col)` helper routes **both** sides through one identical BigQuery expression
replicating `normalize_for_search` (NFD + drop `\p{Mn}`, lower/trim, strip trailing karaoke tag,
strip leading "the ", drop non-`\p{L}\p{N}_\s`, collapse whitespace). Applied to the exact join,
the brand_match join, and the NOT EXISTS de-dup.

**Validated read-only against live BigQuery** (the real `_norm_sql`): the SQL normalization
reproduces the Python `normalize_for_search` baseline exactly (32,496 on current data), and the
**full new union already matches 33,449 distinct Divebar files on current (old-parse) data vs
production's 23,318 — +10,131 from symmetry alone**, before any re-index. After the parser
re-index the exact portion lifts toward ~37,910, compounding further. Residual misses after the parser fix are tiny and documented: `(WTF Karaone)` typo
(~6), `SOKC-O104` letter-O typo (~21), `GGZ007-02,2` comma in disc-id (~16), `RVZ-033.d` (~5).

## Status: IMPLEMENTED

- `filename_parser.py` rewritten folder-brand-aware; `tests/unit/test_divebar_filename_parser.py`
  added (48 cases, green); existing `test_divebar_index_builder.py` still green.
- `divebar_lookup/main.py` `_rebuild_xref` rewritten to normalize both sides via `_norm_sql`;
  3 guard tests added to `test_divebar_lookup` (`test_main.py`). All 64 divebar tests green.
- `modules/divebar_mirror.py` switched to `pulumi.FileArchive` + `BucketObject` with
  generation-pinning so `pulumi up` actually redeploys new function code; `deploy.sh` note updated.
- Version bumped 0.188.3 → 0.188.4.
- **Deploy (Andrew, read-only ADC blocks me):** `cd infrastructure && pulumi up` (redeploys both
  `divebar-mirror` and `divebar-lookup`), then trigger scheduler jobs `divebar-mirror-daily`
  (re-parse → MERGE, preserves `gcs_path`) then `divebar-xref-rebuild-daily`. Then re-measure xref
  `db_files` and the live `incubus admiration` repro. (Order matters: re-index before xref rebuild.)

## Open questions for Andrew

1. **Include Phase 3** (FileArchive deploy fix) in this PR, or keep PR parser-only and handle deploy
   mechanics separately?
2. **brand_code map scope** — minimal (only folders that clearly map to a KN community code) vs
   broader best-effort? Minimal is safer (no mismaps); recommend minimal.
3. Confirm **you'll run the `pulumi up` + scheduler re-index** after merge (I can't with read-only ADC).
