# Inline "use clean instrumental" override on the lyrics-review complete modal

**Date:** 2026-08-30
**Worktree:** `karaoke-gen-clean-instrumental-option`
**Spec (Andrew, verbatim):**
> this is overselling what it means to choose and encouraging that step: [screenshot of the
> ReviewChangesModal auto-instrumental box]. lets condense more and easier choice into this view
> by adding the clean instrumental as an additional audio option here (when the default
> instrumental is the auto-selected instrumental+BV) giving a single click button to choose clean
> instrumental instead, overriding the auto-selection logic because eg. they hear too much lead
> vocal bleed or noise in the backing vocals when they click the pink regions above etc. no need
> to even show that for tracks where clean is already the default choice. in both cases, the
> wording of "Let me choose the instrumental myself" can become more specific instead and rarely
> used, eg. "Let me keep the BV but mute specific sections, or upload a custom instrumental"

## Context

The confident auto-instrumental box lives in `ReviewChangesModal` (Workstream C / per-screen skip,
#957). When the backing verdict is confident, the instrumental review screen is skipped and the
modal shows an info note + a "Let me choose the instrumental myself" checkbox (the escape hatch to
the full instrumental screen). The escape hatch is heavyweight and over-encouraged.

The common override — "the backing vocals sound bad, just give me the clean instrumental" — should
be a **single click inline**, not a trip to the full review screen.

## Behaviour

- Backend already accepts an explicit `instrumental_selection="clean"` at
  `POST /api/review/{id}/complete` (valid_selections always includes `clean`). **No backend change.**
- The clean instrumental becomes a **third pill in the "Audio:" toggle** on the preview
  (`PreviewVideoSection`) — preview + one-click choose in a single control. When both the clean and
  with-backing stems exist and are meaningfully different (verdict = `with_backing` + clean stem
  present), the single "Instrumental" pill splits into **Instrumental + backing vocals** /
  **Clean instrumental**. Clicking a variant both auditions it and selects it (the last activated
  variant is what gets submitted). The backing waveform seek auditions the with-backing stem.
- When verdict = `clean` (or only one stem exists): the toggle keeps the single "Instrumental"
  pill — nothing to choose.
- Submit: the reviewer's last choice drives `clean` vs `auto` (auto resolves to the with_backing
  verdict server-side). The confident-box note reflects the current choice.
- Escape-hatch checkbox reworded to be specific + rare:
  - with_backing: "Let me keep the backing vocals but mute specific sections, or upload a custom instrumental"
  - clean: "Let me review the instrumental myself, or upload a custom instrumental"

## Files

- `frontend/messages/en.json` — reword `autoInstrumentalBacking`/`autoInstrumentalClean`/
  `reviewInstrumentalAnyway`; add `autoInstrumentalCleanChosen`, `reviewInstrumentalCleanHatch`
  (reviewChanges) + `audioInstrumentalBacking`, `audioInstrumentalClean` (previewVideo). Then
  `translate.py --target all` (all 33 locales).
- `frontend/components/lyrics-review/PreviewVideoSection.tsx` — 3-way audio toggle; new props
  `offerInstrumentalChoice`, `onInstrumentalChoiceChange`; state = `isInstrumental` + `selectedId`.
- `frontend/components/lyrics-review/modals/ReviewChangesModal.tsx` — passes the choice through to
  the toggle + dynamic message + per-case hatch text; props `cleanOverride`,
  `onInstrumentalChoiceChange`.
- `frontend/components/lyrics-review/LyricsAnalyzer.tsx` — `useCleanOverride` state fed by the
  reported choice; submit `clean` vs `auto`; toast reflects choice.
- Tests: `__tests__/PreviewVideoSection.test.tsx` (3-way toggle), `__tests__/ReviewChangesModal.test.tsx` (note/hatch).
