# Countdown Padding Feature

This document explains how the countdown padding feature works across the karaoke-gen system, including both the standalone CLI tool and the cloud backend.

## Overview

When a song's vocals start within the first 3 seconds, karaoke singers don't have enough time to prepare. The countdown padding feature addresses this by:

1. Adding 3 seconds of silence to the start of the audio
2. Shifting all lyrics timestamps forward by 3 seconds
3. Adding a "3... 2... 1..." countdown segment (displayed at 0.1s - 2.9s)

This gives singers a visual countdown before the lyrics begin.

## Technical Details

### CountdownProcessor (lyrics-transcriber)

The core countdown logic lives in the `lyrics-transcriber` library:

**File:** `lyrics_transcriber/output/countdown_processor.py`

**Constants:**
- `COUNTDOWN_THRESHOLD_SECONDS = 3.0` - Trigger countdown if first word starts within this time
- `COUNTDOWN_PADDING_SECONDS = 3.0` - Amount of silence to add
- `COUNTDOWN_START_TIME = 0.1` - When countdown text starts displaying
- `COUNTDOWN_END_TIME = 2.9` - When countdown text ends
- `COUNTDOWN_TEXT = "3... 2... 1..."` - The countdown text displayed

**Key Methods:**
- `process(correction_result, audio_filepath)` - Main entry point. Returns `(modified_result, padded_audio_path, padding_added, padding_seconds)`
- `_needs_countdown(correction_result)` - Checks if first word starts within 3 seconds
- `_create_padded_audio(audio_filepath)` - Uses ffmpeg to prepend silence
- `_add_countdown_to_result(correction_result)` - Shifts timestamps and adds countdown segment
- `has_countdown(correction_result)` - Detects if countdown already exists (for loading saved corrections)

**FFmpeg Command Used:**
```bash
ffmpeg -y -hide_banner -loglevel error \
  -f lavfi -t 3.0 -i anullsrc=channel_layout=stereo:sample_rate=44100 \
  -i input_audio.flac \
  -filter_complex "[0:a][1:a]concat=n=2:v=0:a=1[out]" \
  -map "[out]" \
  -c:a flac \
  output_padded.flac
```

This prepends 3 seconds of stereo silence (44.1kHz) and uses FLAC to preserve audio quality.

## Workflow: Standalone CLI (karaoke-gen)

When using the karaoke-gen CLI tool locally:

```
┌─────────────────────────────────────────────────────────────────────┐
│                    STANDALONE CLI WORKFLOW                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  1. LyricsTranscriber.process()                                     │
│     └─> add_countdown=True (default)                                │
│     └─> CountdownProcessor adds countdown + pads vocals audio       │
│     └─> Returns: padded_audio_filepath, countdown_padding_added     │
│                                                                     │
│  2. Audio Separation                                                │
│     └─> Creates instrumental files (original timing)                │
│                                                                     │
│  3. Apply Padding to Instrumentals                                  │
│     └─> AudioProcessor.apply_countdown_padding_to_instrumentals()   │
│     └─> Pads all instrumental variants to match vocals              │
│     └─> Creates files with "(Padded)" suffix                        │
│                                                                     │
│  4. Final Video Generation                                          │
│     └─> Uses padded vocals + padded instrumental                    │
│     └─> Everything is synchronized                                  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**Key Files:**
- `karaoke_gen/lyrics_processor.py` - Calls LyricsTranscriber, captures padding info
- `karaoke_gen/audio_processor.py` - `apply_countdown_padding_to_instrumentals()` and `pad_audio_file()`
- `karaoke_gen/karaoke_gen.py` - Orchestrates the workflow, applies padding after separation

**Instrumental File Naming:**
- Original: `Artist - Song (Instrumental model.ckpt).flac`
- Padded: `Artist - Song (Instrumental model.ckpt) (Padded).flac`

## Workflow: Cloud Backend

The cloud backend has a two-phase workflow with human review in between. This requires special handling to avoid sync issues.

```
┌─────────────────────────────────────────────────────────────────────┐
│                    CLOUD BACKEND WORKFLOW                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  PHASE 1: Lyrics Worker (lyrics_worker.py)                          │
│  ─────────────────────────────────────────                          │
│     └─> LyricsTranscriber.process()                                 │
│     └─> add_countdown=FALSE  <── Deferred to Phase 2                │
│     └─> Corrections saved WITHOUT countdown                         │
│     └─> Original audio timing preserved                             │
│                                                                     │
│  HUMAN REVIEW (Review UI)                                           │
│  ────────────────────────                                           │
│     └─> User reviews/edits lyrics                                   │
│     └─> Audio playback uses original (un-padded) audio              │
│     └─> Timestamps match audio perfectly                            │
│                                                                     │
│  PHASE 2: Render Video Worker (render_video_worker.py)              │
│  ─────────────────────────────────────────────────────              │
│     └─> CountdownProcessor.process() called HERE                    │
│     └─> Adds countdown segment + shifts timestamps                  │
│     └─> Creates padded audio for video rendering                    │
│     └─> Final video has countdown + synced audio                    │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**Why Defer Countdown to Phase 2?**

Previously, countdown was added during Phase 1 (lyrics transcription). This caused a problem:

1. Lyrics corrections were saved with countdown segment + shifted timestamps
2. The padded audio file was created in a temp directory that was deleted
3. Review UI served the original (un-padded) audio
4. **Result:** 3-second sync mismatch between lyrics and audio in review UI

By deferring countdown to Phase 2:
- Review UI shows original timing (audio and lyrics are perfectly synced)
- Users can accurately review and edit lyrics
- Countdown is added just before final video rendering

**Key Files:**
- `karaoke_gen/lyrics_processor.py:405` - Sets `add_countdown=not is_serverless`
- `backend/workers/render_video_worker.py:188-200` - Adds countdown using `CountdownProcessor.process()`

**Serverless Detection:**
```python
is_serverless = (
    os.getenv("MODAL_TASK_ID") is not None or
    os.getenv("MODAL_FUNCTION_NAME") is not None or
    os.path.exists("/.modal")
)
```

## Finalization Step (karaoke_finalise)

The `KaraokeFinalise` class handles creating the final karaoke video with instrumental audio.

**File:** `karaoke_gen/karaoke_finalise/karaoke_finalise.py`

It includes a safety net in `remux_with_instrumental()`:
- If `countdown_padding_seconds` is set but instrumental doesn't have "(Padded)" in the name
- It automatically pads the instrumental before remuxing
- This ensures vocals and instrumental stay synchronized

## Detecting Existing Countdown

When loading saved corrections (e.g., from a JSON file), you can detect if countdown was applied:

```python
countdown_processor = CountdownProcessor(cache_dir=temp_dir)
has_countdown = countdown_processor.has_countdown(correction_result)

# Or check for countdown text in first segment
first_segment = correction_result.corrected_segments[0]
is_countdown = first_segment.text == "3... 2... 1..."
```

You can also detect from LRC files:
```python
# LyricsProcessor._detect_countdown_padding_from_lrc()
# Checks for "3... 2... 1..." text or first timestamp >= 2.5s
```

## Configuration

### Disabling Countdown

To disable countdown for a specific transcription:

```python
from karaoke_gen.lyrics_transcriber.core.config import OutputConfig

output_config = OutputConfig(
    # ... other options
    add_countdown=False,  # Disable countdown
)
```

### Padding Amount

The padding amount is currently fixed at 3.0 seconds. To change this, you would need to modify the constants in `CountdownProcessor`.

## Troubleshooting

### Issue: Countdown appears but audio is out of sync

**Cause:** Instrumental audio wasn't padded to match vocals.

**Solution:**
- Check that `countdown_padding_added` was captured from transcription
- Verify `apply_countdown_padding_to_instrumentals()` was called
- Look for "(Padded)" in instrumental filenames

### Issue: Review UI shows 3-second offset

**Cause:** Countdown was added during transcription instead of rendering phase.

**Solution:**
- This was fixed in v0.76.1
- Ensure `add_countdown=False` for serverless mode
- Countdown should only be added in `render_video_worker.py`

### Issue: No countdown even though song starts immediately

**Cause:** First word timestamp might be exactly at or after 3.0 seconds.

**Check:**
- Look at the first segment's first word start_time
- Countdown triggers only if `first_word_start < 3.0`

## Testing

### Unit Tests

Countdown padding tests are in `tests/unit/test_countdown_padding.py`:

```bash
python -m pytest tests/unit/test_countdown_padding.py -v
```

Tests cover:
- `pad_audio_file()` success, failure, and timeout
- `apply_countdown_padding_to_instrumentals()` for clean and combined instrumentals
- Integration with `LyricsProcessor` and `KaraokeGen`
- Custom instrumental padding
- Idempotency (skipping already-padded files)

## Data Flow Summary

```
┌─────────────────────────────────────────────────────────────────────┐
│                         DATA FLOW                                   │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Input: Song with vocals starting at 1.5s                           │
│                                                                     │
│  After Countdown Processing:                                        │
│  ─────────────────────────────                                      │
│  • Audio: 3s silence + original audio                               │
│  • First segment: "3... 2... 1..." at 0.1s-2.9s                     │
│  • Original first word: moved from 1.5s → 4.5s                      │
│  • All subsequent timestamps: shifted +3.0s                         │
│                                                                     │
│  Result: Singer sees countdown, has time to prepare                 │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## Version History

- **v0.76.1** - Fixed cloud backend sync issue by deferring countdown to render phase
- **v0.70.x** - Initial countdown padding implementation integrated from lyrics-transcriber
