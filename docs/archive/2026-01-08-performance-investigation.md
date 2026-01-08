# Performance Investigation: Karaoke Generation Pipeline

**Date**: 2026-01-08
**Status**: Phase 2 Complete - Encoding Infrastructure Upgraded
**Goal**: Reduce total karaoke generation time from ~30 minutes to ~10 minutes

## Executive Summary

We achieved a **~50% reduction in lyrics processing time** (from ~13 min to ~6.5 min) through preloading optimizations. However, the overall pipeline still takes ~25-30 minutes. Further optimization is needed to reach the 10-minute target.

**Phase 2 Focus**: Encoding infrastructure optimization. Benchmarking confirms the current GCE encoding worker (c4-standard-8) is **1.75x slower** than an M3 Max MacBook Pro, validating the need for infrastructure upgrades.

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

## Phase 2: Encoding Infrastructure Benchmarking

### Motivation

The GCE encoding worker (final encoding stage) currently takes ~9-11 minutes. User hypothesis: the current c4-standard-8 instance is underperforming compared to modern hardware (e.g., M3 MacBook Pro).

**Important constraint**: GPU encoding (NVENC) is NOT viable because:
1. We deliberately use `libx264` for quality
2. `libass` subtitle rendering is CPU-only and cannot use GPU
3. The heaviest operations (4K ASS overlay) require CPU

### Benchmark Methodology

Created `scripts/benchmark_encoding.py` and `scripts/benchmark_encoding_gce.sh` to measure actual encoding operations using the same test data (job `fddad04d` - "piri - dog"):

**Test files**:
- `karaoke.ass` - ASS subtitle file
- `vocals.flac` / `instrumental_clean.flac` - Audio stems
- `title.mov` / `end.mov` - Title/end screen videos
- `background.png` - Nomad theme 4K background
- `font.ttf` - AvenirNext-Bold font

**Benchmark stages** (matching actual production code paths):
1. Preview Video (480x270 with ASS overlay) - `LocalPreviewEncodingService`
2. With Vocals Video (4K with ASS overlay) - `VideoGenerator.generate_video`
3. Remux with Instrumental - `LocalEncodingService.remux_with_instrumental`
4. Convert Title/End MOV to MP4 - `LocalEncodingService.convert_mov_to_mp4`
5. Lossless 4K Concat - `LocalEncodingService.encode_lossless_mp4`
6. Lossy 4K (AAC audio) - `LocalEncodingService.encode_lossy_mp4`
7. MKV (FLAC audio) - `LocalEncodingService.encode_lossless_mkv`
8. 720p Downscale - `LocalEncodingService.encode_720p`

### Benchmark Results: Current Infrastructure

#### System Specifications

| Spec | M3 Max MacBook Pro | GCE c4-standard-8 |
|------|-------------------|-------------------|
| CPU | Apple M3 Max | Intel Xeon Platinum 8581C @ 2.30GHz |
| Cores | 14 (10P + 4E) | 8 vCPUs (4 physical + HT) |
| Memory | 36 GB | 29 GB |
| FFmpeg | 8.0.1 (Homebrew) | 7.0.2-static (John Van Sickle) |
| Architecture | ARM64 (Apple Silicon) | x86_64 |

#### Detailed Results

| Stage | M3 Max | GCE c4-standard-8 | Ratio |
|-------|--------|-------------------|-------|
| Preview (480x270 ASS) | 50.12s | 110.36s | **2.2x slower** |
| **With Vocals (4K ASS)** | 200.71s | 324.16s | **1.6x slower** |
| Remux with Instrumental | 0.21s | 0.24s | ~same |
| Convert Title MOV→MP4 | 1.87s | 3.58s | 1.9x slower |
| Convert End MOV→MP4 | 1.72s | 3.54s | 2.1x slower |
| **Lossless 4K Concat** | 90.95s | 164.86s | **1.8x slower** |
| Lossy 4K (AAC) | 7.03s | 8.85s | 1.3x slower |
| MKV (FLAC) | 0.54s | 0.40s | ~same |
| **720p Downscale** | 27.74s | 50.20s | **1.8x slower** |
| **TOTAL** | **380.91s (6.3 min)** | **666.19s (11.1 min)** | **1.75x slower** |

#### Key Findings

1. **Overall**: GCE c4-standard-8 is **1.75x slower** than M3 Max MacBook Pro

2. **Heaviest operations** (CPU-bound libx264 + libass):
   - With Vocals (4K ASS): 324s GCE vs 201s Mac (61% of GCE total)
   - Lossless 4K Concat: 165s GCE vs 91s Mac (25% of GCE total)
   - These two operations account for **73%** of total GCE encoding time

3. **Root causes**:
   - Apple M3 Max has significantly better single-threaded performance
   - More CPU cores (14 vs 8)
   - Better memory bandwidth and cache architecture
   - libass subtitle rendering is particularly demanding

### Optimization Opportunities

Since GPU is NOT an option (libass limitation), focus on:

1. **More powerful CPU instance types**:
   - More cores for parallel FFmpeg operations
   - Higher clock speeds for single-threaded libass rendering
   - Better memory bandwidth

2. **FFmpeg optimizations**:
   - Explicit thread count tuning
   - Preset adjustments where quality loss is acceptable
   - Parallel output format encoding

3. **Alternative machine families**:
   - c3/c3d (Intel Sapphire Rapids, higher clocks)
   - c4a (AMD EPYC Genoa, high core count)
   - n2/n2d (balanced performance/cost)
   - t2a (Arm-based Ampere Altra, potentially better perf/watt)

### Next Steps: Infrastructure Testing

After researching latest GCP compute options (C4, C4A, C4D, C3, C3D series), selected 5 candidates focusing on:
- High single-thread performance (critical for libass subtitle rendering)
- Good multi-thread scaling (for libx264 encoding)
- Latest generation CPUs with highest clock speeds

#### Selected Candidates

| # | Instance Type | vCPUs | CPU | Clock Speed | Memory | Hypothesis |
|---|--------------|-------|-----|-------------|--------|------------|
| **Baseline** | c4-standard-8 | 8 | Intel Emerald Rapids | 2.3/3.9 GHz | 30 GB | Current production |
| 1 | **c4d-highcpu-16** | 16 | AMD EPYC Turin (5th gen) | 4.1 GHz turbo | 30 GB | **Newest, fastest CPU** |
| 2 | **c4-highcpu-16** | 16 | Intel Granite Rapids (6th gen) | 3.9/4.2 GHz | 32 GB | 2x cores, same vendor |
| 3 | **c4-highcpu-32** | 32 | Intel Granite Rapids (6th gen) | 3.9/4.2 GHz | 64 GB | 4x cores, max parallelism |
| 4 | **c3d-highcpu-30** | 30 | AMD EPYC Genoa (4th gen) | 3.3 GHz | 59 GB | AMD high core count |
| 5 | **c4a-highcpu-16** | 16 | Google Axion (ARM Neoverse V2) | - | 32 GB | ARM architecture test |

**Rationale**:
- **C4D**: Latest AMD Turin with 4.1GHz max boost - likely best single-thread perf
- **C4 16/32**: Intel Granite Rapids scaling test - same arch as current, more cores
- **C3D**: AMD Genoa known for excellent multi-thread - test FFmpeg scaling
- **C4A**: ARM Axion to test if Apple Silicon-like performance translates to GCP ARM

**Benchmark scripts**:
- `scripts/benchmark_encoding_gce.sh` - Shell script for GCE VMs
- `scripts/benchmark_candidates.sh` - Orchestrates multi-VM testing

#### Candidate Test Results

| Instance Type | CPU | Total Time | vs Baseline | With Vocals (4K) | Lossless Concat | 720p | Status |
|--------------|-----|------------|-------------|------------------|-----------------|------|--------|
| c4-standard-8 (baseline) | Intel Xeon 8581C | 666.19s | 1.00x | 324.16s | 164.86s | 50.20s | ✅ Complete |
| **c4d-highcpu-32** | **AMD EPYC 9B45 (Turin)** | **135.27s** | **4.92x** | **53.89s** | **33.97s** | **10.84s** | ✅ **WINNER** |
| c4d-highcpu-16 | AMD EPYC 9B45 (Turin) | 220.00s | 3.03x | 93.73s | 57.99s | 19.94s | ✅ Complete |
| c4a-highcpu-16 | Google Axion (ARM) | 248.07s | 2.69x | 114.13s | 59.54s | 19.26s | ✅ Complete |
| c4-highcpu-16 | Intel Xeon 8581C | 308.89s | 2.16x | 135.38s | 81.10s | 24.93s | ✅ Complete |
| M3 Max MacBook (reference) | Apple M3 Max | 380.91s | 1.75x | 200.71s | 90.95s | 27.74s | ✅ Complete |
| c4-highcpu-32 | Intel Xeon 8581C | - | - | - | - | - | ❌ Quota limit |
| c3d-highcpu-30 | AMD EPYC Genoa | - | - | - | - | - | ❌ Quota limit |

#### Key Findings

1. **c4d-highcpu-32 (AMD EPYC 9B45 Turin) is the clear winner**:
   - **4.92x faster** than baseline (135s vs 666s)
   - **6.01x faster** on the heaviest operation (With Vocals 4K ASS: 54s vs 324s)
   - Even faster than M3 Max MacBook Pro by 2.8x!
   - Doubling cores 16→32 gave 1.63x additional speedup (220s → 135s)

2. **Scaling analysis**:
   - c4d-highcpu-16: 220s (3.03x faster)
   - c4d-highcpu-32: 135s (4.92x faster)
   - Near-linear scaling for this workload - FFmpeg efficiently uses all 32 cores

3. **AMD dominates Intel for this workload**:
   - c4d-highcpu-16 (AMD): 220s
   - c4-highcpu-16 (Intel): 309s
   - AMD is 1.4x faster at same core count

4. **ARM (Axion) competitive but not optimal**:
   - c4a-highcpu-16: 248s (2.69x faster)
   - Good performance but AMD x86 still wins

#### Recommendation

**Deploy c4d-highcpu-32 as the new encoding worker**:
- Reduces encoding time from ~11 min to ~2.3 min (4.92x faster)
- Overall job time reduction: ~9 min saved per job
- Cost is justified by GCP free credits and dramatically improved UX

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
