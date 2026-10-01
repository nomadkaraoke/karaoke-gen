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
  instrumental in the same gap dilutes the fraction).
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

## Next
Deploy → run the audit (needs `gcloud auth login` for the admin token) → hand-check
suspects → pick the gate threshold → Phase 2: scorer gate + SectionDetector + review UI.
