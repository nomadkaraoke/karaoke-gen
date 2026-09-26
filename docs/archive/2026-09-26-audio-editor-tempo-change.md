# Audio Editor: Tempo Change + Published-Output Labeling

**Date:** 2026-09-26
**Branch:** `feat/sess-20260926-1410-audio-editor-tempo`

## Goal

> please brainstorm and add a way to adjust tempo to the audio editor - we'll also need to give
> consideration to how to make sure tempo-adjusted tracks are correctly labeled in the published
> outputs so nobody gets a surprise when they come to sing a song

## Design

### Tempo operation
- New audio-edit operation `tempo` with `params: {factor}` (0.5–1.5; 1.0 rejected as a no-op).
  Whole-track only — a region tempo change would be musically odd and there's no use case.
- `AudioEditService.change_tempo` uses FFmpeg **Rubber Band** (`rubberband=tempo=X:pitchq=quality:channels=together`),
  which is pitch-preserving and far cleaner than `atempo`. The production static ffmpeg
  (johnvansickle 7.0.2, `Dockerfile.base`) is built with `--enable-librubberband`; if a build
  lacks it, we log a warning and fall back to `atempo` (still pitch-preserving, lower quality).
- Tempo edits compound (90% then 90% = 81%); the cumulative factor is the product of all `tempo`
  entries in `audio_edit_stack`. Undo/redo/session-restore work unchanged since it's just another stack entry.
- Audio edit happens **before** separation + transcription, so lyrics are synced to the new tempo
  automatically — no timing rescaling needed.

### Editor UI
- **Tempo** toolbar button (always available, no selection needed) → dialog with slider (50–150%),
  presets (80–120%), resulting length, cumulative % when stacking, and a **live pitch-preserving
  browser preview** (`audio.playbackRate` + `preservesPitch`) before the server renders it.
- Persistent amber "Tempo: 90% of original" badge; the submit confirmation repeats that the
  outputs will be labeled.
- The label text shown in the UI is injected as a placeholder (`{label}` / `{example}`) and never
  translated, because it's what literally appears in the published outputs.

### Labeling published outputs
Every customer-visible output reads **`job.title`**: title/end screens (+ YouTube thumbnail),
CDG title screen, final filenames, CDG/TXT zips, Dropbox folder, GDrive + Divebar/kjbox mirror,
YouTube title/tags, download filenames, completion/reminder emails and push notifications.
`job.title` is already the *display* title (display-title overrides fold into it at creation, with
search terms held separately in `lyrics_title` / `audio_search_title`), so:

- At `/audio-edit/submit` (before workers are triggered) `_tempo_label_updates`:
  - sets `job.title = "<title> (90% Tempo)"` (idempotent — replaces any existing label),
  - pins the original into `job.lyrics_title` if unset, so lyrics search still finds the song,
  - stores `job.tempo_factor` (new field).
  - If a previously-labeled job is resubmitted at normal tempo, the label is removed.
- YouTube description: `render_youtube_description` prepends
  "Note: this karaoke version has been slowed down to 90% of the original song's tempo (same key)."
  whenever the title carries the label — covers direct, quota-queued and bulk-rewrite uploads.
- Title edits on completed tracks (`POST /api/jobs/{id}/edit`) re-apply the label, so it can't be
  accidentally dropped while the audio is still tempo-changed.
- Admin resets that restore normal-speed audio (`reset → awaiting_audio_edit`, reset-to-audio-search)
  strip the label and clear `tempo_factor`; reset-to-search also searches with the unlabeled title.

**Why the title, not just a config-level suffix:** besides being the one place every output reads,
YouTube server-mode upload deletes any existing channel video with the same title
(`youtube_upload_service.py` `replace_existing`). An unlabeled 90%-tempo upload of a song we'd
already published would have *replaced the normal-tempo video*.

## Label format
`"(NN% Tempo)"`, whole percent, rounded; a cumulative factor that rounds to 100% is unlabeled.
Backend `backend/services/tempo_label.py` and frontend `frontend/lib/tempo.ts` must stay in sync.

## Known limitations / follow-ups
- LRCLIB's first lookup matches on audio duration; a tempo change alters duration so it falls back
  to its plain search (works, slightly less precise).
- The title card for themes using `existing_image` renders no text at all (pre-existing), so the
  label appears everywhere except that screen for such themes.
- Portrait title card draws the title on one line without wrapping; very long titles + label could overflow (pre-existing).
- Possible pre-existing bug spotted: `audio_search.py` select-audio step writes
  `display_title`/`display_artist` onto the job but nothing reads those fields.
- Natural next feature: key/pitch shift (Rubber Band `pitch=`), which would need the same labeling treatment.

## Tests
- Backend: `test_audio_edit_service.py::TestChangeTempo`, `test_audio_edit_routes.py`
  (tempo apply validation + `TestSubmitTempoLabeling`), `test_tempo_label.py`,
  `test_admin_job_reset.py::TestResetClearsTempoLabel`, `test_edit_completed_track.py::TestEditPreservesTempoLabel`.
- Frontend: `AudioEditor.test.tsx` (tempo describe block), `lib/__tests__/tempo.test.ts`,
  E2E `e2e/regression/audio-editor-tempo.spec.ts`.
