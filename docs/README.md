# Karaoke-Gen Documentation

> **Status**: Production-ready. First full E2E flow completed 2025-12-28.

## What Works

- **Web App**: https://gen.nomadkaraoke.com
  - `/` - Landing page with pricing and beta enrollment
  - `/app` - Main app (upload audio, review lyrics, download videos)
- **Backend API**: https://api.nomadkaraoke.com - FastAPI on Cloud Run
- **CLI Tools**: `karaoke-gen` (local), `karaoke-gen-remote` (cloud)

## Current State

| Component | Status |
|-----------|--------|
| Audio upload & separation | Working |
| Lyrics transcription | Working |
| Agentic AI correction | Working (Gemini 2 Flash via Vertex AI) |
| Human lyrics review | Working |
| Preview video generation | Working |
| Instrumental selection | Working |
| Multi-format encoding | Working |
| Token-based auth | Working |
| Magic link auth | Working |
| Payment flow (Stripe) | Working |
| Beta tester program | Working |
| CI/CD self-hosted runner | Working (GCP) |

## Known Issues

- CDG format generation requires additional style configuration

## Recent Changes

- **Worker Timeout Fixes** (2026-01-01): Fixed 3 timeout issues blocking job completion: (1) Lyrics transcription timeout increased to 20 min (PR #153), (2) Cloud Tasks dispatch_deadline added for audio worker - default 10 min was killing Modal API calls (PR #154), (3) Enabled Cloud Run Jobs for video encoding - 1-hour timeout vs 30-min Cloud Run service limit (PR #155). Full E2E pipeline now verified working. See [archive/2026-01-01-worker-timeout-fixes.md](archive/2026-01-01-worker-timeout-fixes.md)

- **Agentic Correction Timeout** (2025-12-31): Added 3-minute configurable timeout for agentic AI lyrics correction. Prevents stuck jobs when songs have many gaps (74+ gaps could take 30+ minutes). On timeout, skips correction and proceeds to human review with raw transcription. See [archive/2025-12-31-agentic-timeout-implementation.md](archive/2025-12-31-agentic-timeout-implementation.md)
- **Job Failure Fixes** (2025-12-31): Fixed 4 critical issues causing job failures: (1) user_email not set on jobs - users couldn't see their jobs, (2) YouTube URL race condition - lyrics worker started before audio downloaded, (3) Audio search cache not persisting across Cloud Run instances, (4) Jobs stuck in downloading state - added 10min transcription timeout. See [archive/2024-12-31-job-failure-investigation.md](archive/2024-12-31-job-failure-investigation.md)
- **Vertex AI Auth Fix** (2025-12-31): Fixed `ChatGoogleGenerativeAI` to use Vertex AI backend with service account auth on Cloud Run. PR #145 broke cloud deployments by requiring `GOOGLE_API_KEY`. Solution: pass `project` parameter to trigger Vertex AI backend with ADC. See [archive/2025-12-31-vertex-ai-auth-fix.md](archive/2025-12-31-vertex-ai-auth-fix.md)
- **LangChain Provider Migration** (2025-12-30): Migrated from `langchain-google-vertexai` (gRPC) to `langchain-google-genai` (REST) to fix silent hangs during model initialization. Added initialization and warm-up timeouts using ThreadPoolExecutor for fail-fast behavior. See [archive/2025-12-30-langchain-vertexai-to-genai-migration.md](archive/2025-12-30-langchain-vertexai-to-genai-migration.md)
- **LangFuse Prompt Management** (2025-12-30): Replaced hardcoded LLM prompts with LangFuse-managed prompts for dynamic iteration without redeploying. Added migration script, LangFuse Vertex AI service account for GCP credits. See [archive/2025-12-30-langfuse-prompt-management.md](archive/2025-12-30-langfuse-prompt-management.md)
- **Gemini 3 Flash Agentic Correction Fix** (2025-12-30): Fixed agentic correction hanging due to Gemini 3 requiring `global` location instead of regional endpoints, and multimodal response format handling. Added local test script for faster iteration. See [archive/2025-12-30-gemini3-agentic-correction-fix.md](archive/2025-12-30-gemini3-agentic-correction-fix.md)
- **Cold Start UX** (2025-12-30): Frontend now shows friendly "Warming up the generator..." messages when backend is slow to respond (cold start after deployment or inactivity). Progressive messages appear after 5s and 15s to reassure users during 30s+ cold starts.
- **Cloud Output Structure Fix** (2025-12-30): Fixed remote CLI (`karaoke-gen-remote`) to produce identical output as local CLI: YouTube uploads now work, Dropbox includes stems/ and lyrics/ subfolders, instrumental filenames use actual model names. See [archive/2025-12-30-cloud-output-structure-fix.md](archive/2025-12-30-cloud-output-structure-fix.md)
- **Auth Session Persistence Fix** (2025-12-30): Fixed two bugs causing 401 errors despite valid sessions: timezone-aware datetime comparisons in backend, and stale token caching in frontend. Firestore indexes now tracked in Pulumi. See [archive/2025-12-30-auth-session-persistence-fix.md](archive/2025-12-30-auth-session-persistence-fix.md)
- **E2E Test Suite Consolidated** (2025-12-29): Tests reorganized into `production/` (real API) and `regression/` (mocked API, CI-safe). See [archive/2025-12-29-e2e-test-consolidation-plan.md](archive/2025-12-29-e2e-test-consolidation-plan.md)

## Quick Links

| Doc | Purpose |
|-----|---------|
| [PRODUCT-VISION.md](PRODUCT-VISION.md) | Product goals, audience, business model |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design, data flow, integrations |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Local setup, testing, deployment |
| [API.md](API.md) | Backend endpoint reference |
| [LESSONS-LEARNED.md](LESSONS-LEARNED.md) | Key insights for future agents |
| [STRIPE-SETUP.md](STRIPE-SETUP.md) | Payment integration setup |
| [MOBILE-UX-BEST-PRACTICES.md](MOBILE-UX-BEST-PRACTICES.md) | Mobile/responsive design guidelines |
| [archive/](archive/) | Historical documentation |

## Workflow Summary

```
Upload Audio → Parallel Processing → Human Review → Video Generation → Download
                   │                      │
           (Modal + AudioShake)    (LyricsTranscriber UI)
```

## For AI Agents

Start with `CLAUDE.md` in the repo root for essential rules, then reference these docs as needed. Keep this README current when project state changes.
