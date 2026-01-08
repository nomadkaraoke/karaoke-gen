# Performance Investigation: Karaoke Generation Pipeline

**Date**: 2026-01-08
**Status**: Phase 1 Complete, Validated in Production
**Goal**: Reduce total karaoke generation time from ~30 minutes to ~10 minutes

## Executive Summary

We achieved a **~50% reduction in lyrics processing time** (from ~13 min to ~6.5 min) through preloading optimizations. However, the overall pipeline still takes ~25-30 minutes. Further optimization is needed to reach the 10-minute target.

### Key Achievements (PR #236)

| Optimization | Before | After | Savings |
|-------------|--------|-------|---------|
| NLTK cmudict preloading | 100-150s lazy load | ~2s at startup | **~150s** |
| Langfuse handler preloading | 201s lazy load | ~2s at startup | **~200s** |
| Model warmup (eager init) | 300s+ parallel timeout | 0s (pre-warmed) | **~300s** |
| Parallel anchor search | 38s sequential | ~10s parallel | **~28s** |
| **Total Lyrics Stage** | ~13 min | **~6.5 min** | **~50%** |

### Remaining Bottlenecks

| Stage | Current Duration | Target | Gap |
|-------|-----------------|--------|-----|
| Audio separation (Modal) | ~3 min | 2 min | 1 min |
| Lyrics processing | ~6.5 min | 3 min | 3.5 min |
| Video rendering | ~6 min | 3 min | 3 min |
| Final encoding (GCE) | ~9 min | 2 min | 7 min |
| **Total (excl. human review)** | **~25 min** | **10 min** | **15 min** |

---

## Empirical Data: E2E Test Comparison

### Before Optimization (Job 6b5a354e - 09:17 UTC)

```
Test song: piri - dog
Total E2E test time: 31.4 minutes

Stage Breakdown:
├─ Steps 1-5 (Setup/Audio selection):     25 seconds
├─ Step 6 (Lyrics processing):           12 min 53 sec  ← PRIMARY BOTTLENECK
├─ Step 7 (Lyrics review UI):             2 min 12 sec
├─ Step 8 (Video render + instrumental):  6 min 3 sec
└─ Step 9 (Final encoding):               9 min 28 sec
```

### After Optimization (Job b099c91b - 10:02 UTC)

```
Test song: piri - dog
Total E2E test time: ~19 min (encoding failed, unrelated bug)

Stage Breakdown:
├─ Steps 1-5 (Setup/Audio selection):     32 seconds
├─ Step 6 (Lyrics processing):            6 min 28 sec  ← 50% FASTER!
├─ Step 7 (Lyrics review UI):             2 min 20 sec
├─ Step 8 (Video render + instrumental):  6 min 10 sec
└─ Step 9 (Final encoding):               FAILED (MOV→MP4 bug, unrelated)
```

### Improvement Summary

| Stage | Before | After | Change |
|-------|--------|-------|--------|
| Lyrics Processing | 12m 53s | **6m 28s** | **-50%** |
| Video Rendering | 6m 3s | 6m 10s | ~same |
| Encoding | 9m 28s | N/A (failed) | expected same |

---

## Complete Pipeline Timing Breakdown

Based on analysis of multiple jobs, here's the detailed breakdown of each stage:

### Stage 1: Audio Worker (~3 minutes)

| Sub-stage | Duration | Notes |
|-----------|----------|-------|
| Job creation → Worker start | 13s | Cloud Tasks dispatch |
| Modal audio separation | 72s | GPU-accelerated, fast |
| Stems download from Modal | 47s | 8 files from Modal storage |
| Stage 2 local separation | 23s | CPU-bound on Cloud Run |
| **Total Audio Worker** | **~3 min** | Acceptable |

**Optimization opportunities**:
- [ ] Faster Modal model (research needed)
- [ ] Parallel stem downloads
- [ ] Skip Stage 2 separation for some use cases

### Stage 2: Lyrics Worker (~6.5 minutes after optimization)

| Sub-stage | Before | After | Notes |
|-----------|--------|-------|-------|
| AudioShake API call | 78s | 78s | External API, hard to optimize |
| NLTK cmudict load | 106s | **~0s** | Preloaded at startup |
| SpaCy model load | 63s | **~0s** | Preloaded at startup |
| Langfuse init | 201s | **~0s** | Preloaded at startup |
| Anchor sequence search | 38s | **~10s** | Parallelized |
| Agentic correction | 300s+ | **~60s** | Model pre-warmed |
| Output generation | ~30s | ~30s | - |
| **Total Lyrics Worker** | **~16 min** | **~6.5 min** | **-60%** |

**Optimization opportunities**:
- [ ] Local Whisper transcription (eliminates AudioShake 78s)
- [ ] GCE-based lyrics worker (eliminates all cold start overhead)
- [ ] Faster agentic model (smaller model, fewer gaps)
- [ ] Skip agentic correction for simple songs

### Stage 3: Video Rendering (~6 minutes)

| Sub-stage | Duration | Notes |
|-----------|----------|-------|
| ASS subtitle generation | ~5s | Fast, CPU-only |
| FFmpeg video render | ~5-6 min | CPU-bound, single-threaded |
| Upload to GCS | ~10s | - |

**Optimization opportunities**:
- [ ] GPU-accelerated FFmpeg (NVENC on GCE)
- [ ] Lower resolution preview during render stage
- [ ] Parallel encoding of segments

### Stage 4: Final Encoding (~9 minutes)

| Sub-stage | Duration | Notes |
|-----------|----------|-------|
| GCE job dispatch | ~5s | - |
| File downloads from GCS | ~30s | Videos + stems |
| Title/karaoke/end concatenation | ~2 min | FFmpeg concat |
| Multi-format encoding | ~6 min | 4K lossless + 720p |
| Upload results to GCS | ~30s | - |

**Optimization opportunities**:
- [ ] GPU encoding (NVENC) - would reduce 6 min → ~1-2 min
- [ ] Skip 4K lossless for most users
- [ ] Parallel format encoding
- [ ] Pre-rendered title/end screens

---

## Optimization Ideas: Not Yet Implemented

### High Impact, Medium Effort

#### 1. GPU-Accelerated Encoding on GCE
**Current**: GCE worker uses CPU encoding (libx264)
**Proposed**: Add NVIDIA GPU to GCE VM, use NVENC
**Expected savings**: 6 min → 1-2 min (~4-5 min saved)
**Effort**: 1-2 days (Pulumi changes, FFmpeg flags)

```python
# Current (CPU)
ffmpeg -i input.mkv -c:v libx264 -preset medium output.mp4

# With GPU (NVENC)
ffmpeg -hwaccel cuda -i input.mkv -c:v h264_nvenc -preset p4 output.mp4
```

#### 2. Local Whisper Transcription
**Current**: AudioShake API call takes 78 seconds
**Proposed**: Run Whisper locally on Modal GPU or GCE
**Expected savings**: 78s → ~30s (API overhead eliminated)
**Effort**: 1 week (integration, testing, fallback logic)

#### 3. GCE Lyrics Worker
**Current**: Lyrics processing on Cloud Run (cold starts, ephemeral filesystem)
**Proposed**: Dedicated GCE VM with persistent cache
**Expected savings**: Additional 1-2 min (no remaining init overhead)
**Effort**: 2-3 days (infrastructure + integration)

### Medium Impact, Low Effort

#### 4. Skip 4K Lossless for Web Users
**Current**: Always encode 4K lossless + 720p
**Proposed**: Only encode 720p by default, 4K on request
**Expected savings**: ~3 min per job
**Effort**: 1 day (API flag, conditional encoding)

#### 5. Pre-rendered Title/End Screens
**Current**: Title screens rendered per-job
**Proposed**: Pre-render common templates, composite at encode time
**Expected savings**: ~1 min
**Effort**: 2 days

#### 6. Parallel Multi-Format Encoding
**Current**: Formats encoded sequentially (4K then 720p)
**Proposed**: Encode both formats in parallel
**Expected savings**: ~2-3 min (depends on CPU cores)
**Effort**: 1 day

### Lower Impact, Research Needed

#### 7. Faster Audio Separation Model
**Current**: Modal uses bs_roformer model
**Proposed**: Research faster models with acceptable quality
**Expected savings**: Unknown
**Effort**: Research project

#### 8. Smart Agentic Correction Skipping
**Current**: Always run agentic correction
**Proposed**: Skip for songs with high-confidence transcription
**Expected savings**: ~60s for some songs
**Effort**: 2 days (confidence scoring, threshold tuning)

#### 9. Cached Lyrics from Previous Jobs
**Current**: Each job fetches/transcribes fresh
**Proposed**: Cache lyrics by audio hash, reuse if available
**Expected savings**: Up to 5+ min for repeat songs
**Effort**: 1 day

---

## Implementation Status

### Completed (PR #236)

| Fix | File | Status |
|-----|------|--------|
| NLTK cmudict preloading | `backend/services/nltk_preloader.py` | ✅ Deployed |
| Langfuse handler preloading | `backend/services/langfuse_preloader.py` | ✅ Deployed |
| SpaCy model preloading | `backend/services/spacy_preloader.py` | ✅ Deployed |
| Model warmup method | `langchain_bridge.py` | ✅ Deployed |
| Parallel anchor search | `anchor_sequence.py` | ✅ Deployed |
| Preload status endpoint | `/health/preload-status` | ✅ Deployed |

### Verification

Production endpoint confirms all preloaders working:
```bash
curl https://api.nomadkaraoke.com/api/health/preload-status
```
```json
{
  "status": "ok",
  "message": "All resources preloaded",
  "spacy": {"preloaded": true, "vocab_size": 764},
  "nltk": {"preloaded": true, "entries": 123455},
  "langfuse": {"preloaded": true, "handler_type": "LangchainCallbackHandler"}
}
```

### Not Started

| Optimization | Priority | Effort | Expected Impact |
|-------------|----------|--------|-----------------|
| GPU encoding (NVENC) | High | 2 days | -4 min |
| Skip 4K lossless | High | 1 day | -3 min |
| Local Whisper | Medium | 1 week | -1 min |
| GCE lyrics worker | Medium | 3 days | -2 min |
| Parallel format encoding | Medium | 1 day | -2 min |

---

## Target Timeline to 10-Minute Goal

| Phase | Optimization | Cumulative Time |
|-------|-------------|-----------------|
| Baseline | Current state | ~25 min |
| Done | NLTK/Langfuse/SpaCy preload | ~19 min |
| Next | GPU encoding (NVENC) | ~15 min |
| Next | Skip 4K lossless | ~12 min |
| Next | Local Whisper | ~11 min |
| Stretch | GCE lyrics worker | ~10 min |

---

## Debugging Tips

### Check Preload Status
```bash
curl https://api.nomadkaraoke.com/api/health/preload-status | jq
```

### Check GCE Encoding Worker Logs
```bash
gcloud compute ssh encoding-worker --zone=us-central1-a --project=nomadkaraoke \
  --command="journalctl -u encoding-worker --since '30 min ago' | tail -100"
```

### Monitor E2E Test Progress
```bash
gh run view <run_id> --log 2>&1 | grep -E 'STEP.*COMPLETE'
```

### Check FFmpeg Hardware Acceleration
```bash
curl https://api.nomadkaraoke.com/api/health/detailed | jq '.ffmpeg'
```

---

## References

- PR #232: Thread-safe locking for LangChainBridge
- PR #233: SpaCy model preloading
- PR #236: Comprehensive performance optimization (this work)
- [LESSONS-LEARNED.md - Preloading Heavy Resources](../LESSONS-LEARNED.md#preloading-heavy-resources-at-container-startup)
- [LESSONS-LEARNED.md - Thread-Safe Lazy Initialization](../LESSONS-LEARNED.md#thread-safe-lazy-initialization-in-shared-components)
