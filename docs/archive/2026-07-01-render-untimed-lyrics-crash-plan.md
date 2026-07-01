# Prod Error Triage & Fix — 2026-07-01

Batch of Discord error alerts, grouped, root-caused, and fixed.

## Group A (PRIMARY — job-breaking, today): render/preview crash on untimed lyrics

### Symptoms (all job `231806a4`, "Nelly Furtado - Maneater")
- `Failed to generate outputs: unsupported format string passed to NoneType.__format__` (×4)
- `Job 231806a4: Failed to generate preview video: unsupported format string passed to NoneType.__format__` (×2)
- `Video render failed: Encoding job 231806a4 failed: '<' not supported between instances of 'NoneType' and 'float'` (×3, retried 3× then job failed)

### Root cause
The saved corrections JSON (`gs://.../jobs/231806a4/lyrics/corrections_updated.json`) had
**all 50 segments and all 364 words with `start_time`/`end_time` = `null`.**

The lyrics were Danish custom parody lyrics ("Vor's veninde er blevet gift") replacing the
original Maneater transcription. Segment IDs (`segment-N-<epoch>`) trace to the **LyricsSynchronizer's
"Edit Lyrics" sub-modal** (`LyricsSynchronizer.tsx:334 handleSaveEditedLyrics`), which creates
segments/words with `start_time: null, end_time: null` — an *expected intermediate state* that the
user must resolve by tap-syncing. The user saved + triggered preview/render **without synchronizing**,
so every timing was null.

The pipeline then crashes cryptically instead of rejecting cleanly. Both paths funnel through
`OutputGenerator.generate_outputs → SegmentResizer.resize_segments → ASS generation`, which has
**many** None-unsafe timing operations:
- `segment_resizer.py:540/550` — `f"time={segment.start_time:.2f}..."` → format crash (preview, newer Cloud Run wheel dies here first, at a *debug log line*).
- `segment_resizer.py:149` — `segment.end_time >= seg_start` (segment-level None not handled; only word-level is).
- `ass/lyrics_line.py:70/97` — `segment.start_time - previous_end_time >= threshold`.
- `ass/lyrics_screen.py:245/250` — `min/max(... .start_time/.end_time ...)`.
- `ass/section_detector.py:36` — `segments[0].start_time >= gap_threshold`.
- `_is_in_instrumental_section` — `segment.start_time < inst_end` → **exact `'<' NoneType vs float`** the GCE encoder hits (encoder runs its own pinned wheel and gets deeper before crashing).

Per-site None-guarding is whack-a-mole. The correct fix is a **validation gate** at the shared choke
points that rejects untimed lyrics with a clear, actionable message — untimed custom/replaced lyrics
*cannot* produce a correct karaoke video (auto-distribution would be meaningless against different
vocals), so blocking-with-guidance is the right product behavior.

### Fix
1. **Shared validator** `karaoke_gen/lyrics_transcriber/output/timing_validation.py` — new
   `validate_segment_timing(segments) -> None` raising `LyricsTimingError` (with count + first
   offending line) when any segment/word has `None` start/end. Called at the top of
   `OutputGenerator.generate_outputs()`. Covers preview (Cloud Run) + local render + encoder (once its
   wheel updates).
2. **Backend render gate** — call the validator in `render_video_worker.py` right after the
   `correction_result` is loaded (~line 363), *before* any render/encoder dispatch. This stops the
   encoder from ever being sent an untimed job **now**, without waiting for an encoder-wheel redeploy.
   Fail the job with a clear user-facing message.
3. **Preview endpoint** `review.py generate_preview_video` — map `LyricsTimingError` to a clean
   422 with a "synchronize your lyrics first" message (instead of the current cryptic 500).
4. **Defense-in-depth** — make `_log_input_segments`/`_log_output_segments` None-safe (a debug log
   must never crash a job); make `_sanitize_segment_timings` tolerate None segment timing.
5. **Frontend guard (prevention/UX)** — in the review UI, block "Generate preview" and
   "Complete/Submit" when untimed words exist, with a message pointing the user to synchronize.

## Group B (cheap robustness win): GDrive brand_code cleanup incomplete

- `Error cleaning up Google Drive for job 94ee69f3: GDrive brand_code search incomplete: 2/3 subfolder(s) failed`
- Underlying per-subfolder errors: `[SSL: UNEXPECTED_EOF_WHILE_READING]`, `[Errno 32] Broken pipe`
  — transient Drive API connection drops.
- `gdrive_service.py:462` correctly refuses to declare "clean" on partial results (prevents brand-code
  recycling) — behavior is right, but transient drops shouldn't reach that raise.

### Fix
Wrap the per-subfolder `files().list().execute()` (`gdrive_service.py:438`) in a small
retry-with-backoff for transient connection errors, so single SSL/broken-pipe blips self-heal.

## Group C (transient — document only, no code change)

- **`request_audit_error` + "Exception in ASGI application"** (same event): Firestore
  `The referenced transaction has expired or is no longer valid` on
  `/api/admin/rate-limits/blocklists/allowlisted-domains` (`email_validation_service.py:754`).
  Count 1, admin-only, transient (likely a cold/paused instance mid-transaction). Admin can retry.
  Not worth added complexity.
- **`Default STARTUP TCP probe failed ... DEADLINE_EXCEEDED`**: single slow cold start at 12:07:30;
  **retry succeeded** at 12:10. Pure Cloud Run infra noise, no code issue.

## Testing
- Unit: `validate_segment_timing` raises on None segment/word timing, passes on valid; `_log_input_segments`
  no longer crashes on None; gdrive retry recovers from transient then succeeds / raises after exhaustion.
- Frontend: Jest test that preview/complete is blocked when untimed words present.
- Regression fixture built from the real 231806a4 corrections shape (all-null timings).
