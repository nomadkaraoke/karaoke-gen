# Missing lyrics: sung sections the transcription dropped ("vocals but no lyrics")

## Trigger
Job `5710831e` (Omer Adam – שני משוגעים, KaraokeHunt batch, reviewed by Andrew):
the transcription has **no words from 20.66s to 32.40s** while the lead-vocal stem is
active the whole time. Both references (Genius, LRCLIB) put three pre-chorus lines there:

- שמרנו רגעים בלב, שתינו ת'נוף
- ואת היית יפה כמו פרח שאסור היה לקטוף
- רציתי רק לקטוף אותך, רציתי לקטוף

`SectionDetector` (gap ≥ 10s between segments, no audio check) then rendered
"♪ INSTRUMENTAL (6 seconds) ♪" over clearly sung vocals. Not caused by the RTL work
(PR #1094): shipped and re-rendered ASS have identical sections.

## Why nothing caught it
- **Scorer** said REVIEW only because anchor coverage was 98.8% (< AUTO bar). Anchor
  coverage = fraction of *transcribed* words matching the reference; skipped reference
  lines don't lower it. Naive reference-side coverage is noisy for repeated choruses
  (anchors map each transcribed chorus to one reference occurrence) — 18–23 "missing"
  lines reported vs 3 real.
- **timing_check G3** (`max_unclaimed_run_s`, longest lead-vocal activity not covered by
  any word) DOES fire on this job (`unclaimed-vocal`, 5.84s run; 23% of activity
  unclaimed) — but it is shadow-only and only computed when lyrics would otherwise be
  AUTO, so it never ran here. Known false-positive class: ad-libs / vocal samples
  deliberately absent from lyrics.
- **Review UI** shows nothing for a gap; a reviewer who doesn't read the language can't spot it.

## Plan
1. **Signal**: compute untranscribed-vocal runs for every job once stems exist (reuse
   `timing_check._rms_activity`; output a list of runs `{start, end, active_fraction}`
   merged across short breaths, plus the reference lines that fall between the
   neighbouring anchors). Store in `state_data` / corrections metadata.
2. **Audit / calibration** (run on GCP, not the Mac): run the signal over completed jobs
   (KaraokeHunt batch + recent prod) → distribution of merged-run lengths, manual check
   of top hits → threshold that separates dropped lines from ad-libs (likely: merged run
   ≥ ~6s AND reference lines unaccounted for between the neighbouring anchors).
3. **Gate**: scorer REVIEW with an explicit reason ("~11s of vocals at 0:20 with no
   lyrics; reference has 3 lines there"); compute regardless of tier.
4. **Section detection**: don't label a gap INSTRUMENTAL when vocal activity covers most
   of it (carrier: corrections metadata, already downloaded by both render paths).
5. **Review UI**: mark the gap ("vocals detected, no lyrics") and offer the reference
   lines between the neighbouring anchors for one-click insert → Tap To Sync.
6. Later: auto-fill (re-transcribe the chunk / align reference lines).

## Fix for this job
Insert the three lines in review (Tap To Sync) and re-render after PR #1094 deploys.

## Phase 1 built (shadow signal + backfill)
- `backend/services/auto_approval/vocal_gaps.py`: every transcription gap ≥3s →
  lead-vocal active fraction, longest breath-bridged (≤0.5s) vocal run, and the reference
  lines between the reference positions of the words either side (section headers
  dropped, ±1-word anchor drift at line boundaries snapped, reversed/huge spans → none).
  `suspect` = longest run ≥3s (NOT active fraction: a dropped line followed by an
  instrumental in the same gap dilutes the fraction), after a 1.5s held-note allowance
  for a run touching the gap start. Silent/empty stems → error, never a clean pass.
  Stored results are keyed by `input_key` (word timings + stem + version) so resets /
  edits recompute.
- On 5710831e: one suspect gap 20.66–32.40s (96% active, 11.76s run) with exactly the 3
  missing Genius lines; the three real instrumentals have ≤0.16s runs.
- Executor computes it once per job when stems exist (any verdict), stores
  `state_data.vocal_gaps`, summary in `processing_metadata.auto_approval.vocal_gaps`.
- Backfill: `POST /api/internal/jobs/{id}/vocal-gaps` + `scripts/audit_vocal_gaps.py`
  (server-side; nothing bulky through the Mac).

### Upstream oddity found
Anchor `reference_word_ids` can be shifted one word at "[Section]" header boundaries
(Genius "[קדם-פזמון]": the anchor for "כמו איזה שני משוגעים בחוף" maps its last word to the
next line's first word). Worth a separate look — it probably mis-highlights reference words
in the review UI too.

## Known overlap
`timing_check` G3 (`max_unclaimed_run_s`) is a whole-song version of the same idea. Once the
audit shows which formulation separates dropped lines from ad-libs/held notes best, fold
them into one detector (one stem download/decode; currently AUTO jobs decode twice).

## Next
Deploy → run the audit (needs `gcloud auth login` for the admin token) → hand-check
suspects → pick the gate threshold → Phase 2: scorer gate + SectionDetector + review UI.

## Audit results (2026-10-01, v0.256.0, 262 completed jobs, last 60 days)
Server-side via `scripts/audit_vocal_gaps.py` (results stored on each job).

- Longest unlyricked vocal run per job: p50 0.7s, p75 2.4s, p90 5.1s, p95 7.4s, p99 22.4s.
- Jobs with a run ≥3s: 47 (18%); ≥6s: 21; ≥10s: 8 — far too many for an audio-only gate.
- Cross-check against **synced** reference lyrics (LRCLIB line timestamps): of 57 suspect
  gaps on jobs with a synced reference, only **15** have a reference line timestamped
  inside the gap (~25% precision at 3s; ~60% at ≥6s). The rest are vocal chops / samples /
  ad-libs / synth bleed (DnB-heavy: Etherwood, London Elektricity, Danny Byrd, …).
- Reference lines between the neighbouring anchors appeared on only 3 suspect gaps
  (5710831e, XTC, Etherwood) — high precision when present, low recall (repeated choruses
  break the anchor-span method).

### Phase 2 decision: evidence-based gate
- **REVIEW gate** (explicit reason) when a vocal run ≥3s AND reference lyrics place
  lines inside the gap — synced reference timestamps inside the gap, or reference lines
  between the neighbouring anchors.
- Audio-only runs: record + soft note in review, no gate (precision too low).
- `SectionDetector`: don't label a gap INSTRUMENTAL when the evidence rule fires.
- Review UI: marker at the gap + the reference lines (from either method) offered for insert.

## Phase 2b built: review UI marker + insert
- `GET /api/review/{job_id}/correction-data` → `vocal_gaps` (typed as `VocalGapsResult` in
  `frontend/lib/lyrics-review/types.ts`). Only **evidenced** gaps are shown; audio-only
  suspects stay hidden (precision too low). Nothing shows when `vocal_gaps` is null/empty.
- Open gaps are derived from the reviewer's CURRENT segments
  (`lib/lyrics-review/utils/missingLyrics.ts`): a gap is open while no timed word starts in
  `[start, end)`. `vocal_gaps` is never mutated, so inserting/typing lines hides the marker
  and undo brings it back. Taken from the loaded data so restored sessions still show it.
- `MissingLyricsCallout` (above Synced Lyrics): "Possible missing lyrics at m:ss–m:ss", the
  expected lines (synced reference lines preferred, else anchor-bounded reference lines,
  with the source name), Play, and **Insert these lines**. `TranscriptionView` draws a
  dashed amber marker row at the gap's chronological position (all three view modes);
  clicking it scrolls to the callout.
- Insert (`hooks/useMissingLyrics.ts`): one segment per line, words split on whitespace,
  provisional timings spread evenly per word across `[start+0.1, end-0.1]`, inserted before
  the first segment starting after the gap midpoint, singer inherited from the neighbour.
  Goes through `updateDataWithHistory` (one undo step) and logs one `segment_add` edit-log
  entry per line (`details.origin = "missing_lyrics_gap"`).
- Re-sync guidance: `MissingLyricsResyncHint` lists the inserted lines (while they exist)
  with a **Sync timing** button each, opening the existing Edit modal (Tap To Sync). No
  new timing system.
- Read-only/replay: marker + lines shown, insert disabled.
- Tests: Jest (`missingLyrics.test.ts`, `useMissingLyrics.test.tsx`,
  `MissingLyricsCallout.test.tsx`, `TranscriptionView.missingLyrics.test.tsx`) + Playwright
  regression (`lyrics-review.spec.ts` › "Possible Missing Lyrics": insert, order, undo, Sync
  timing → Edit modal).
- Found while building: sonner's `<Toaster />` isn't mounted anywhere (root layout mounts
  the shadcn `ui/toaster`), so `toast` from `sonner` in the review UI is silently invisible.
  The hint is therefore inline rather than a toast. Worth a separate fix.

### Phase 2b review fixes (code review)
- **Edge tolerance:** words starting within 0.05s of a gap edge are its neighbours (backend
  rounds gap bounds to 2dp and `end` = next word's start, e.g. 19.996 vs 20.0).
- **Stale-analysis guard:** a gap is only shown if, in the segments AS LOADED, it holds no
  words and the neighbouring words sit within 1s of both edges (or it touches the song
  start / has no words after it). The frontend can't recompute `input_key`, so this is the
  proxy for "the analysis matches this transcription".
- **Insert position by word times:** a gap inside one segment (words 5–9s + a word at 22s)
  splits that segment — earlier words keep the id, later words get a fresh id — with the
  lines inserted between (logged as `segment_split` + `segment_add`).
- **No fabricated timing:** lines take word timings from the synced reference source
  (`reference_lyrics[source].segments`, matched by text inside the gap, all words timed);
  otherwise words are inserted UNTIMED (segment bounds = the gap, so Play/Edit open there)
  and the existing "N lyric word(s) have no timing yet" submit guard forces a Tap To Sync.
- **Pending state:** untimed words between the gap's neighbouring timed words (inserted
  here, typed via Edit/Add Lyrics, or Replace All) → no Insert (no duplicates); the callout
  shows a "not timed yet" note + Sync timing instead. Timed words inside close the gap.
- Re-sync hint appends across inserts; a line leaves it once re-synced or deleted.
- Duet singer: following segment's, then preceding (as `addSegmentBefore`).
- Display timing offset applied to the callout range, Play time and markers.
- Read-only tooltip moved to a focusable wrapper (disabled buttons get no hover).
- Caveat: the submit guard reports via a sonner toast, and sonner's `<Toaster />` isn't
  mounted, so a blocked submit is currently silent (pre-existing; fix separately).
