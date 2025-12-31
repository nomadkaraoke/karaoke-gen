# Agentic AI Correction Timeout Implementation

**Date**: 2025-12-31
**PR**: #149
**Version**: 0.85.0

## Problem

Agentic AI lyrics correction was causing stuck jobs in production. Songs with many gaps (74+) could take 30+ minutes to process since each gap takes 10-30 seconds for LLM inference. This blocked the entire job pipeline.

Example from production logs:
```
Processing 22/74 gaps (~15s each) = ~18 minutes elapsed
Estimated total: 74 gaps × 15s = 18+ minutes
```

## Solution

Implemented a configurable 3-minute timeout (default) with two layers:

### Layer 1: Inner Deadline Check (Cooperative)
In `corrector.py`, check deadline before processing each gap:
```python
if deadline and time.time() > deadline:
    self.logger.warning("AGENTIC TIMEOUT: Deadline exceeded...")
    break  # Return uncorrected transcription for human review
```

### Layer 2: Outer asyncio.wait_for (Safety Net)
In `lyrics_worker.py`, wrap the entire transcription call:
```python
result = await asyncio.wait_for(
    asyncio.to_thread(lyrics_processor.transcribe_lyrics, ...),
    timeout=timeout_seconds + 60  # Extra buffer
)
```

## Behavior

| Scenario | What Happens |
|----------|--------------|
| Completes in <3 min | Normal flow, corrections applied |
| Gap loop exceeds deadline | Inner check breaks, returns uncorrected |
| LLM hangs indefinitely | Outer timeout fires (rare), job fails |
| Any timeout | Job proceeds to human review with raw transcription |

## Files Changed

- `backend/config.py` - Added `agentic_correction_timeout_seconds` config
- `backend/workers/lyrics_worker.py` - Deadline calculation, outer timeout wrapper
- `karaoke_gen/lyrics_processor.py` - Pass deadline to controller
- `lyrics_transcriber_temp/lyrics_transcriber/core/controller.py` - Accept deadline parameter
- `lyrics_transcriber_temp/lyrics_transcriber/correction/corrector.py` - Deadline check in gap loop

## Configuration

Environment variable: `AGENTIC_CORRECTION_TIMEOUT_SECONDS` (default: 180)

## Key Design Decisions

1. **Break, don't raise**: Inner timeout uses `break` to exit the gap loop gracefully, returning whatever we have (uncorrected). This is better than raising an exception because:
   - No retry logic needed
   - Job continues to human review
   - User can manually correct any issues

2. **Two-layer protection**: The inner check handles normal cases (many gaps). The outer timeout catches edge cases (single LLM call hanging). Both are needed for reliability.

3. **Configurable timeout**: Default 3 minutes balances correction quality vs. user experience. It can be adjusted via environment variable per deployment.
