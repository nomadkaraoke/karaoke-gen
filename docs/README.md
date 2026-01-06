# Karaoke-Gen Documentation

> **Status**: Production-ready. First full E2E flow completed 2025-12-28.

## What Works

- **Web App**: https://gen.nomadkaraoke.com
  - `/` - Landing page with pricing and beta enrollment
  - `/app` - Main app (upload audio, review lyrics, download videos)
  - `/admin` - Admin dashboard (user management, job monitoring, beta program)
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
| Admin dashboard | Working |
| CI/CD self-hosted runner | Working (GCP) |
| E2E happy path test | Working (38 min full pipeline) |

## Known Issues

- CDG format generation requires additional style configuration

## Recent Changes

- **Email Notification System** (2026-01-06): Added automated email notifications for job completion and user action reminders. Features: GCS-backed HTML email templates with fallback defaults, SendGrid with CC support, auto-completion emails on job finish, idle reminder emails via Cloud Tasks (5-min delay for blocking states), admin UI buttons to copy message or send email manually. Endpoints: `GET /api/admin/jobs/{id}/completion-message`, `POST /api/admin/jobs/{id}/send-completion-email`. Feature flag `ENABLE_AUTO_EMAILS` (default: false). See [API.md](API.md#email-notifications-admin) and [ARCHITECTURE.md](ARCHITECTURE.md#video-worker-orchestrator).

- **GCE Encoding Worker Python 3.13** (2026-01-06): Upgraded GCE encoding worker from Debian's Python 3.11 to Python 3.13 built from source. Uses dedicated virtual environment at `/opt/encoding-worker/venv`, removing the need for `--break-system-packages`. Aligns the encoding worker with CI/Cloud Run Python version.

- **GCE Encoding Unified with LocalEncodingService** (2026-01-06): GCE encoding worker now uses the same `LocalEncodingService` as the local CLI, eliminating duplicated encoding logic. Output files now have proper names (`Artist - Title (Final Karaoke Lossless 4k).mp4` instead of `output_4k_lossless.mp4`) and include title/end screen concatenation. Wheel deployed to GCS; worker installs at job start for hot updates without VM restart. Removed all fallback logic - single code path for consistent output across CLI, Cloud Run, and GCE. See [LESSONS-LEARNED.md](LESSONS-LEARNED.md#unify-encoding-logic-with-gcs-wheel-deployment).

- **Cloud Distribution Matches Local CLI** (2026-01-06): Fixed cloud distribution (Dropbox uploads) to match local CLI output structure. The `lyrics/` folder now includes: proper filenames (`(Lyrics Corrections).json` instead of `(Karaoke).json`), all reference lyrics files (`(Lyrics Genius).txt`, `(Lyrics Spotify).txt`, etc.), `(With Vocals).mkv`, and `previews/` subfolder with preview ASS files. See [LESSONS-LEARNED.md](LESSONS-LEARNED.md#cloud-distribution-must-match-local-cli-output-structure).

- **Agentic Correction Performance** (2026-01-05): Optimized agentic AI correction from ~5 minutes to ~55 seconds for 20 gaps (~5-6x speedup). Fixed anti-pattern of creating new model instance per gap (caused repeated 2s warm-up overhead). Now creates model once and processes gaps in parallel using ThreadPoolExecutor (default 5 workers). Configure via `AGENTIC_MAX_PARALLEL_GAPS` env var. See [LESSONS-LEARNED.md](LESSONS-LEARNED.md#reuse-llm-model-instances-across-operations).

- **Worker Logs Subcollection** (2026-01-04): Moved `worker_logs` from embedded array in job documents to Firestore subcollection (`jobs/{job_id}/logs`). Fixes job failures when logs exceed 1MB (job 501258e1 had 1.26MB of logs). New logs stored with 30-day TTL via Firestore TTL policy. Feature flag `USE_LOG_SUBCOLLECTION=true` (default). See [LESSONS-LEARNED.md](LESSONS-LEARNED.md#firestore-document-1mb-limit-with-embedded-arrays).

- **GCE Encoding Response Fixes** (2026-01-04): Fixed multiple response format mismatches with GCE encoding worker. The worker returns `output_files` as a list of paths, not a dict with format keys - added conversion logic. Also added defensive type checking for status responses that could be lists. See [LESSONS-LEARNED.md](LESSONS-LEARNED.md#external-service-response-format-mismatches).

- **Video Worker Orchestrator** (2026-01-04): Major refactor to unify video generation pipeline. Created VideoWorkerOrchestrator that coordinates all stages (packaging, encoding, distribution, notifications) regardless of encoding backend (GCE or local). Fixes issue where GCE encoding path bypassed YouTube upload, Discord notifications, and CDG/TXT packaging. Feature flag `USE_NEW_ORCHESTRATOR` (default: true) enables rollback. 139 new tests across 6 new service modules. See [ARCHITECTURE.md](ARCHITECTURE.md#video-worker-orchestrator) and [archive/2026-01-04-video-worker-orchestrator-refactor.md](archive/2026-01-04-video-worker-orchestrator-refactor.md).

- **Full Unicode Font Support** (2026-01-03): Fixed rendering of musical symbols (♪) and added comprehensive international font support to Docker base image. Installed Noto fonts covering Latin, CJK (Chinese/Japanese/Korean), Arabic, Hebrew, Thai, and other scripts. Changed default karaoke font from Arial to Noto Sans. See [LESSONS-LEARNED.md](LESSONS-LEARNED.md#fonts-in-docker-for-video-rendering).

- **Flacfetch Cache API Integration** (2026-01-03): Enhanced audio search cache management to also clear flacfetch's GCS cache. When admins clear a job's cache, both Firestore (job state) and flacfetch (GCS search results) are cleared simultaneously. Added "Clear All Cache" button and cache stats display to admin UI. New endpoints: `DELETE /api/admin/cache`, `GET /api/admin/cache/stats`. See [API.md](API.md#audio-search-management-admin).

- **Audio Search Cache Management** (2026-01-03): Added admin UI at `/admin/searches` to view and manage cached audio search results. Admins can clear stale caches (e.g., when flacfetch is updated with new providers) and allow users to re-search. Fixes issue where jobs showed only YouTube results when lossless sources should be available. See [API.md](API.md#audio-search-management-admin) and [LESSONS-LEARNED.md](LESSONS-LEARNED.md#firestore-cache-has-no-automatic-invalidation).

- **User Database Separation** (2026-01-03): Migrated karaoke-gen users from shared `users` collection to dedicated `gen_users` collection. This separates karaoke-gen user data from karaoke-decide user data (both apps share the same GCP project/Firestore instance). Migration script in `scripts/migrate_users_to_gen_users.py`. See [ARCHITECTURE.md](ARCHITECTURE.md#firestore-collections) for collection details.

- **Admin Dashboard** (2026-01-03): Added comprehensive admin dashboard at `/admin` with: stats overview (users, jobs, credits), user management (search, sort, add credits, toggle role, enable/disable), job browser (filter by status/user, view details, delete), and beta program monitoring. Query-param based routing for static export compatibility. See [API.md](API.md#admin-endpoints) for backend endpoints.

- **GCE Encoding Worker** (2026-01-03): Added optional high-performance encoding on dedicated GCE VM (C4-standard-8 with Intel Granite Rapids 3.9 GHz). Uses `USE_GCE_ENCODING=true` env var. Falls back to Cloud Run if unavailable. See [ARCHITECTURE.md](ARCHITECTURE.md).

- **Runner Disk Space Auto-Cleanup** (2026-01-03): Fixed CI failures caused by self-hosted runners filling up with Docker images. Changed cleanup from daily/7-day-old to hourly/threshold-based (70%). Added pre-job disk checks to all self-hosted CI jobs. See [LESSONS-LEARNED.md](LESSONS-LEARNED.md#docker-disk-space-management-on-self-hosted-runners)

- **E2E Happy Path Test** (2026-01-02): Added comprehensive E2E test that validates the complete karaoke generation journey using ONLY browser UI interactions (no API shortcuts). Test covers all 12 steps from landing page through cleanup. Runs daily via GitHub Actions and takes ~38 minutes. See [E2E-HAPPY-PATH-TEST-SPEC.md](E2E-HAPPY-PATH-TEST-SPEC.md)

- **LyricsTranscriber Cache Persistence** (2026-01-02): Added GCS-backed cache for LyricsTranscriber to avoid redundant API calls. AudioShake transcription and lyrics provider responses (Genius, Spotify, LRCLib, Musixmatch) are now cached to `gs://karaoke-gen-storage/lyrics-transcriber-cache/`. Cache is synced before/after each job, persisting across Cloud Run instances. Significantly reduces API costs and speeds up repeated processing of same songs.

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
