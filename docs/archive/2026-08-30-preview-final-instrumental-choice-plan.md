# Preview modal — make final-instrumental choice unambiguous (2026-08-30)

## Problem (Andrew)
The finish/preview modal's single "Audio:" segmented control does two unrelated jobs —
(1) audition/preview playback (incl. "Original (with vocals)", never a valid final output)
and (2) the final-output selection (which instrumental gets baked into the video). Nothing
tells the user that clicking a pill is a permanent choice. The prose "switch to the clean
instrumental" has no obvious target. Also the escape-hatch checkbox wording is too wordy.

## Chosen design (Andrew picked "separate decision + preview rows", + fold escape hatch in)
- **Preview audio row** (audition only): `[ Original (with vocals) ] [ Instrumental ]`.
- **Decision radio group** "🎬 Your karaoke video will use:":
  - (•) Instrumental + backing vocals  ✓ recommended   (the auto-selection)
  - (○) Clean instrumental                              (backing case only, when a clean stem exists)
  - (○) Advanced mode (edit backing vocals or upload your own)   ← replaces the checkbox
- Selecting backing/clean both **selects** (final output) and **auditions** (plays that stem).
- Selecting Advanced → opts into the full instrumental review screen (CTA flips to
  "Proceed to Instrumental Review").

## Implementation
- `PreviewVideoSection.tsx`: revert the 3-pill split → always the 2-pill preview toggle.
  Replace `switchToInstrumentalAndSeek` handle with `auditionInstrumental(id, seekTime?)`.
  Drop `offerInstrumentalChoice` / `onInstrumentalChoiceChange` props. `audioLabel` → "Preview audio:".
- `ReviewChangesModal.tsx`: render the unified radio group (owns decision via existing
  `cleanOverride`/`onInstrumentalChoiceChange` + `reviewInstrumentalAnyway`/`onToggleReviewInstrumental`
  props). Waveform + helper text shown under the backing option. Escape-hatch checkbox removed.
- `LyricsAnalyzer.tsx`: no logic change (already wires all four props + submits clean vs auto).
- i18n: new `finalChoiceLabel`, `recommended`, `advancedMode`, `advancedModeClean`; reword
  `autoInstrumentalBacking`; remove `reviewInstrumentalAnyway`/`reviewInstrumentalCleanHatch`.
  Run `translate.py --target all` (33 locales).
- Tests: rewrite the choice tests in PreviewVideoSection.test + ReviewChangesModal.test.
