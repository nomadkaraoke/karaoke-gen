# Proactive AI Auto-Correction + UI De-AI-ification

**Date:** 2026-06-11
**Branch:** `feat/sess-20260611-1514-autocorrect-cost-logging`
**Context:** Measured cost ~$0.13/track (multi-model). Cheap enough to run on every job.

## Goal (user's words)

Run multi-model auto-correction automatically on every karaoke job so suggestions
are ready to review/approve/reject by the time anyone reaches the lyrics review UI.
Must fail gracefully — never block the karaoke job if AI APIs are flaky / out of
credits. Remove all "AI"/"model" mentions from the review UI; button becomes just
"Auto-correct"; drop the user-configurable model selection and always use multi-model.
Keep the button for the case where references arrive later (user pastes reference
lyrics during review, then clicks it).

## Architecture decisions (validated against the codebase)

1. **Where proactive runs: the `lyrics-transcription-job` Cloud Run Job**, after
   `mark_lyrics_complete()` (so downstream screens/render are already triggered and
   are NOT blocked), awaited (a fire-and-forget task would die when the job
   entrypoint returns). The job is long-lived, so an extra ~15-30s is fine and the
   process stays alive to finish the cache write.
2. **Cache alignment = the whole point.** `AutoCorrectService` already writes a
   per-job GCS cache keyed on (models, settings, word-id+text list, ref texts,
   artist, title). If the proactive run uses the SAME inputs + settings + models the
   UI will send, the UI's on-load call is a **cache hit** (instant, no second spend).
   - Proactive settings = `AutoCorrectSettings(compare_models=True)` (all other
     fields default). Backend defaults == frontend `DEFAULT_AUTO_CORRECT_SETTINGS`
     once we flip `compare_models` to `true`. Both paths build settings server-side
     and hash the same dict.
   - `models` is derived server-side identically in both paths via `_models_for()`.
   - Proactive reads `corrections.json` (corrected_segments + reference_lyrics) — the
     exact data the review-data endpoint serves on first load (before any edits).
3. **Env/secret wiring (REQUIRED for it to actually work).** The lyrics job currently
   has neither `ANTHROPIC_API_KEY` nor `AUTO_CORRECT_*`. It uses the SAME SA as the
   API service (`karaoke-backend@…`), which already reads `anthropic-api-key`
   (project-level secret access) — so adding the secret to the job is low-risk.
   Add via `--update-env-vars`/`--update-secrets` in the ci.yml job-update step
   (merges, doesn't wipe the existing 12 vars).
4. **Feature flag:** `auto_correct_proactive_enabled` (config), default **false** in
   code, set `AUTO_CORRECT_PROACTIVE_ENABLED=true` on the lyrics job via ci.yml.
5. **UI auto-run on load:** on review load, if references exist, auto-invoke
   auto-correct (cache hit → instant). Keep the button for manual re-run after the
   user pastes references. Always send `compare_models: true`.

## Changes

### Backend
- `backend/config.py`: add `auto_correct_proactive_enabled`.
- `backend/workers/lyrics_worker.py`: after `mark_lyrics_complete`, await a guarded
  helper `_run_proactive_auto_correct(job_id, ...)` — loads corrections.json, calls
  `suggest(..., settings=AutoCorrectSettings(compare_models=True))`, `asyncio.wait_for`
  timeout, swallow ALL exceptions (log only). Never affects job status.
- Tests: worker test that it's gated by the flag, swallows errors, and skips when no
  references.

### Infra
- `.github/workflows/ci.yml`: lyrics-transcription-job update step gains
  `--update-env-vars "AUTO_CORRECT_MODEL=claude-fable-5,AUTO_CORRECT_COMPARE_MODELS=claude-fable-5;gemini-3.1-pro-preview,AUTO_CORRECT_PROACTIVE_ENABLED=true"`
  and `--update-secrets "ANTHROPIC_API_KEY=anthropic-api-key:latest"`.

### Frontend (`frontend/`, next-intl, 33 locales)
- Always multi-model: `DEFAULT_AUTO_CORRECT_SETTINGS.compare_models = true`; remove
  the compare/model toggle from `AutoCorrectModal.tsx`.
- Strip model attribution: remove model-name badge + consensus "X/Y models" badge in
  `AutoCorrectPanel.tsx`; reword the conflict badge to not mention models (keep the
  conflict-resolution behavior).
- Rename button to "Auto-correct"; remove "AI"/"model" from all related strings.
- Auto-run on review load when references exist (cache hit → instant); keep button.
- i18n: edit `messages/en.json`, then `python frontend/scripts/translate.py
  --messages-dir frontend/messages --target all` for all 33 locales.
- Update `e2e/production/auto-correct-suggestions.spec.ts` button selector.

## Failure modes (all non-blocking by design)
- Anthropic down / no credits → claude call raises, caught; Gemini result still cached
  (multi-model degrades to whatever answered). UI on-load call same behaviour.
- Both providers fail → proactive logs warning, job proceeds normally; UI on-load call
  returns 502 and the panel shows nothing (or the manual button can retry later).
- Proactive task times out → `wait_for` cancels it, job proceeds.
- No references at lyrics-complete time → proactive skipped; user pastes refs in UI →
  clicks button → on-demand call.
