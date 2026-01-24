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
| Auto-correction (agentic + heuristic) | Disabled (raw transcription goes to human review) |
| Human lyrics review | Working |
| Preview video generation | Working |
| Instrumental selection | Working |
| Multi-format encoding | Working |
| Token-based auth | Working |
| Magic link auth | Working |
| Payment flow (Stripe) | Working |
| Beta tester program | Working |
| Admin dashboard | Working |
| Rate limiting & abuse prevention | Working |
| CI/CD self-hosted runner | Working (GCP) |
| E2E happy path test | Working (~20-25 min full pipeline) |
| **White-label B2B portals** | Working (Vocal Star first tenant) |

## Known Issues

- CDG format generation requires additional style configuration

## Pending Work

(No pending work items)

## Recent Changes

- **Encoding Worker Immutable Deployment** (2026-01-22): Implemented robust deployment pattern for GCE encoding worker to eliminate version mismatch issues. Changes: (1) Fixed wheel path `karaoke_gen-current.whl` eliminates version sorting bugs, (2) Self-updating startup script downloaded from GCS on every service start (no Packer rebuild needed for logic changes), (3) Version manifest with strict CI verification (deployment fails on mismatch). Root cause: startup.sh was baked into Packer image, requiring image rebuild + VM recreation for fixes. New pattern allows CI to update deployment logic without infrastructure changes. See [infrastructure/encoding-worker/README.md](../infrastructure/encoding-worker/README.md) and [LESSONS-LEARNED.md](LESSONS-LEARNED.md#immutable-deployment-pattern-for-gce-workers).

- **Theme Validation & Default Removal** (2026-01-22): **BREAKING CHANGE in v0.109.0** - Removed all default style fallback logic to ensure cloud jobs always use complete themes. Style getter functions now raise `ValueError` if themes are incomplete instead of merging with defaults. Added `--list-themes` and `--validate-theme` CLI flags. All cloud jobs guaranteed to use complete nomad theme with no silent fallbacks. See [archive/2026-01-22-theme-validation-remove-defaults.md](archive/2026-01-22-theme-validation-remove-defaults.md).

- **Admin Job Reprocess** (2026-01-21, **improved 2026-01-24**): Added "Reprocess" button to admin job reset toolbar. Allows re-encoding and re-distributing a job with existing settings after deleting outputs. Resets job to `instrumental_selected` state and automatically triggers video worker. Clears video/encoding/distribution state while preserving instrumental selection and lyrics metadata. Use case: Fix distribution issues, re-upload to YouTube with different settings, or re-run encoding after infrastructure changes. **Improvements (2026-01-24)**: API response now includes `worker_triggered` and `worker_trigger_error` fields for transparency. Added new `/api/admin/jobs/{job_id}/trigger-worker` endpoint for manual worker triggering when auto-trigger fails. Frontend shows detailed result dialog after reset with status change, cleared data, and worker trigger status. Added "Trigger" button in admin toolbar for manual worker triggering. See [API.md](API.md#reset-job-state).

- **Countdown Audio Sync Fix** (2026-01-21, **completed 2026-01-24**): Fixed bug where songs starting within 3 seconds would have audio desync. PR #328 fixed the writer side: `render_video_worker` now updates `lyrics_metadata.has_countdown_padding`. PR #338 fixed the reader side: `video_worker_orchestrator` now reads the countdown state and passes it through to GCE encoding, which pads the instrumental audio to match. This was a "Fix Both Sides of Dual Code Paths" bug - see [LESSONS-LEARNED.md](LESSONS-LEARNED.md#fix-both-sides-of-dual-code-paths). Added comprehensive test suite (contract tests, pipeline integration, orchestrator countdown tests) to prevent regression. See [archive/2026-01-21-countdown-audio-sync-testing-strategy.md](archive/2026-01-21-countdown-audio-sync-testing-strategy.md).

- **Anchor Sequences Fix** (2026-01-21): Fixed anchor sequence calculation when auto-correction is disabled. Previously, disabling auto-correction (`SKIP_CORRECTION=true`) also stopped anchor/gap calculation, leaving reviewers without guidance on uncertain lyrics areas. Now anchor sequences are calculated independently, so reviewers always see highlighted matches against reference lyrics regardless of correction settings.

- **Auto-Correction Disabled** (2026-01-20): Disabled all auto-correction during lyrics transcription (both agentic AI via Gemini and heuristic rule-based handlers). Raw transcription now goes directly to human review without automatic fixes. This change was made because auto-correction was creating more work for reviewers by introducing errors that needed manual correction. Anchor sequences are still calculated to help reviewers identify uncertain areas. The correction feature can be re-enabled by setting `SKIP_CORRECTION=false` environment variable. See `backend/config.py` for configuration.

- **Worker Coordination Fix** (2026-01-20): Fixed bug where Cloud Run could shut down prematurely when one parallel worker completed while another was still running. Added `WorkerRegistry` to track active workers per job. Workers register at start and unregister in finally block. FastAPI lifespan shutdown handler now waits up to 10 minutes for active workers to complete before allowing container termination. Fixes job failures where lyrics worker was killed mid-processing after audio worker completed. See [ARCHITECTURE.md](ARCHITECTURE.md#worker-coordination) and [LESSONS-LEARNED.md](LESSONS-LEARNED.md#cloud-run-premature-shutdown-with-parallel-workers).

- **Admin Delete Outputs** (2026-01-10, **UX improved 2026-01-24**): Added admin button to delete all distributed outputs (YouTube, Dropbox, Google Drive) for a job while preserving the job record. Use case: fix quality issues by deleting outputs, resetting to `awaiting_review`, correcting lyrics, and re-generating. The brand code is freed for reuse when Dropbox folder is deleted. New endpoint: `POST /api/admin/jobs/{job_id}/delete-outputs`. UI: Delete Outputs button in admin job detail page, "Outputs Deleted" badge when flag is set. **UX improvements (2026-01-24)**: Download buttons (4K, 720p, CDG, TXT, etc.) are now hidden when outputs have been deleted, preventing broken links. Delete confirmation now shows a detailed result dialog with per-service status (YouTube video ID deleted, Dropbox folder path, Google Drive files) instead of a simple toast. See [API.md](API.md#delete-job-outputs).

- **Rate Limiting & Abuse Prevention** (2026-01-09): Added per-user rate limiting (5 jobs/day default), system-wide YouTube upload limits (10/day), and beta enrollment abuse prevention. Features: email normalization (Gmail alias detection), disposable domain blocklist (130+ domains), blocked email/IP lists, IP-based enrollment rate limiting (1 per 24h), and admin UI for managing blocklists and user overrides at `/admin/rate-limits`. Admins can grant users bypass permissions or custom limits. All limits configurable via environment variables. See [API.md](API.md#rate-limits).

- **Fast Preview Video Option** (2026-01-10): Added toggle in lyrics review UI to render preview videos with black background (~10s) instead of theme background image (~30-60s). Default is now black background for faster iteration during review. Users can enable theme background via checkbox when they want to verify the final look. See [API.md](API.md#generate-preview).

- **Frontend Consolidation** (2026-01-09): Consolidated three separate frontends into a single Next.js application. Previously: (1) Main frontend (Next.js + Tailwind), (2) Lyrics Review (React + Vite + MUI, ~14k lines), and (3) Instrumental Review (vanilla HTML/JS, ~1.7k lines) were deployed separately. Now all UIs are unified in the main frontend at `/app/jobs/{id}/review` and `/app/jobs/{id}/instrumental`. Benefits: single deployment to Cloudflare Pages, no external redirects during user workflows, unified design system (Radix/Tailwind), simpler authentication via job ownership model. See [archive/2026-01-09-frontend-consolidation-plan.md](archive/2026-01-09-frontend-consolidation-plan.md) and [tasks/](tasks/).

- **Admin Login Token for Made-For-You Orders** (2026-01-10): Added one-click admin login via email. When a made-for-you order is placed, the admin notification email now includes a direct link with an embedded login token (`?admin_token=TOKEN`). Clicking this link auto-authenticates the admin and opens the job directly - no manual login required. Tokens expire after 24 hours. Also added: BCC to `done@nomadkaraoke.com` on all job completion emails, fixed timezone display (EST→ET for DST correctness). See [API.md](API.md#verify-magic-link).

- **Made-For-You Audio Selection Flow** (2026-01-08, **fixed 2026-01-09**): Refactored made-for-you order processing to pause at audio selection like regular search jobs. New flow: customer places order → admin receives notification email → admin selects audio source in UI → job processes under admin ownership → on completion, ownership transfers to customer → customer receives delivery email. Added model fields (`made_for_you`, `customer_email`, `customer_notes`), professional email templates for order confirmation and admin notification, intermediate email suppression (no lyrics/instrumental reminders to customer during processing), and ownership transfer on job completion. Distribution defaults (Dropbox, YouTube, GDrive, brand prefix) are now properly applied from server settings. **Bug fix (2026-01-09)**: Fixed webhook handler that was not setting `made_for_you=True`, not using admin ownership, not storing `customer_email`/`customer_notes`, and not pausing at audio selection. Added 17 comprehensive unit tests for the webhook handler. See [API.md](API.md#made-for-you-order-flow) and [LESSONS-LEARNED.md](LESSONS-LEARNED.md#test-webhook-handlers-with-unit-tests-not-just-integration-tests).

- **White-Label B2B Portal Infrastructure** (2026-01-08): Added multi-tenant support for white-label karaoke portals. Vocal Star is the first tenant at `vocalstar.nomadkaraoke.com`. Features: subdomain-based tenant detection (frontend and backend), GCS-backed tenant config storage, per-tenant feature flags (audio search, file upload, YouTube, theme selection, etc.), dynamic branding (logo, colors, site title via CSS variables), locked themes per tenant, email domain restrictions for auth, and tenant-scoped jobs/users in Firestore. Backend: `TenantMiddleware` extracts tenant from X-Tenant-ID header, query param (dev only), or Host subdomain; `TenantService` loads config from GCS with caching. Frontend: Zustand store for tenant state, `TenantProvider` for initialization, `TenantLogo` component. Setup script: `scripts/setup-vocalstar-tenant.py`. See [ARCHITECTURE.md](ARCHITECTURE.md#multitenancy) and [API.md](API.md#tenant-config).

- **Encoding Worker Infrastructure Upgrade** (2026-01-08): Upgraded GCE encoding worker from c4-standard-8 (Intel) to c4d-highcpu-32 (AMD EPYC 9B45 Turin). Benchmarking showed **4.92x faster encoding** (135s vs 666s total). Key improvements: With Vocals 4K rendering 6x faster (54s vs 324s), 720p downscale 4.6x faster (11s vs 50s). Reduces total encoding time from ~11 min to ~2.3 min per job. See [archive/2026-01-08-performance-investigation.md](archive/2026-01-08-performance-investigation.md) for full benchmark methodology and results.

- **Theme Enforcement & Encoding Resilience** (2026-01-08): Added defense-in-depth theme enforcement - jobs without theme_id are now rejected at creation (JobManager) with a safety net at processing time (screens_worker). Fixed made-for-you webhook to apply default theme. Added retry logic with exponential backoff (3 retries, 2s→4s→8s) to GCE encoding service for transient connection failures during worker restarts. See [LESSONS-LEARNED.md](LESSONS-LEARNED.md#defense-in-depth-enforce-critical-requirements-at-multiple-layers).

- **Comprehensive Performance Optimization** (2026-01-08): Reduced lyrics processing from **16+ minutes to ~5 minutes** through multiple optimizations. Key changes: (1) NLTK cmudict preloading at startup saves 100-150s, (2) Langfuse callback handler preloading saves 200s, (3) Model warmup in `AgenticCorrector.from_model()` prevents parallel initialization race, (4) Parallel anchor sequence search (4 workers) reduces n-gram processing from 38s to ~10s. Added `/health/preload-status` endpoint for deployment verification. PR #236. See [archive/2026-01-08-performance-investigation.md](archive/2026-01-08-performance-investigation.md) and [LESSONS-LEARNED.md](LESSONS-LEARNED.md#preloading-heavy-resources-at-container-startup).

- **UI Simplification & Brand Accessibility** (2026-01-08): Simplified the "Create Karaoke Video" form by hiding advanced options (theme settings now admin-only, "Display As" behind toggle). Improved audio search dialog UX by hiding technical tracker names and showing user-friendly availability badges. Fixed brand pink color contrast for accessibility - swapped default/hover values so white text on pink buttons meets WCAG contrast ratio. See [BRAND-STYLE-GUIDE.md](BRAND-STYLE-GUIDE.md) v1.2.

- **SpaCy Preloading** (2026-01-08): Implemented SpaCy model preloading at container startup to eliminate 60+ second delay during agentic correction. The `en_core_web_sm` model is now loaded during FastAPI lifespan startup, and `PhraseAnalyzer`/`SyllablesMatchHandler` reuse the preloaded model. Added timing logs to verify performance. See [archive/2026-01-08-spacy-preload-plan.md](archive/2026-01-08-spacy-preload-plan.md).

- **Thread-Safe LangChain Model Initialization** (2026-01-08): Fixed race condition in `LangChainBridge` where parallel threads could all try to initialize the AI model simultaneously, causing 6+ minute delays. Added double-checked locking with `threading.Lock()`. PR #232. See [LESSONS-LEARNED.md](LESSONS-LEARNED.md#thread-safe-lazy-initialization-in-shared-components).

- **Brand Consistency & Style Guide** (2026-01-07): Implemented unified brand theming across all Nomad Karaoke frontends. Primary color changed from blue to brand pink (#ff7acc). Added comprehensive [BRAND-STYLE-GUIDE.md](BRAND-STYLE-GUIDE.md) for human and LLM reference. Includes: CSS variables for light/dark mode, MUI theme updates for lyrics transcriber, email template branding, and Playwright visual tests. See [BRAND-STYLE-GUIDE.md](BRAND-STYLE-GUIDE.md).

- **Audio Search Display Override** (2026-01-06): Added optional "Display As" fields to audio search, allowing users to search for audio by one artist/title (e.g., "Jeremy Kushnier") but display a different artist/title on title screens and filenames (e.g., "Footloose (Broadway Cast)"). Useful for Broadway casts, covers, remixes where tracker metadata differs from desired display. See [API.md](API.md#audio-search).

- **Infrastructure Modularization** (2026-01-06): Refactored `infrastructure/__main__.py` from 2,602 lines to 339 lines (87% reduction). Split into organized modules: `modules/` for core GCP resources, `compute/` for VMs and startup scripts, `config.py` for shared constants. Extracted embedded encoding worker Python app to `backend/services/gce_encoding/`. See `infrastructure/README.md` for new structure and `infrastructure/docs/PHASE5-PACKER-IMAGE-PLAN.md` for planned Packer image optimization.

- **Email Notification System** (2026-01-06): Added automated email notifications for job completion and user action reminders. Features: GCS-backed HTML email templates with fallback defaults, SendGrid with CC support, auto-completion emails on job finish, idle reminder emails via Cloud Tasks (5-min delay for blocking states), admin UI buttons to copy message or send email manually. Endpoints: `GET /api/admin/jobs/{id}/completion-message`, `POST /api/admin/jobs/{id}/send-completion-email`. Feature flag `ENABLE_AUTO_EMAILS` (default: false). See [API.md](API.md#email-notifications-admin) and [ARCHITECTURE.md](ARCHITECTURE.md#video-worker-orchestrator).

- **GCE Preview Encoding Unified with LocalPreviewEncodingService** (2026-01-06): GCE preview encoding now uses the same `LocalPreviewEncodingService` from the installed wheel, eliminating 100 lines of duplicated FFmpeg logic. Preview videos are now identical across local CLI, Cloud Run, and GCE worker environments. Benefits include: consistent encoding settings (480x270, 24fps, crf 28), proper FFmpeg filter path escaping for special characters, hardware acceleration (NVENC) when available, and custom font support. See [LESSONS-LEARNED.md](LESSONS-LEARNED.md#unify-preview-encoding-with-localpreviewencodingservice).

- **GCE Encoding Worker Python 3.13** (2026-01-06): Upgraded GCE encoding worker from Debian's Python 3.11 to Python 3.13 built from source. Uses dedicated virtual environment at `/opt/encoding-worker/venv`, removing the need for `--break-system-packages`. Aligns the encoding worker with CI/Cloud Run Python version.

- **GCE Encoding Unified with LocalEncodingService** (2026-01-06): GCE encoding worker now uses the same `LocalEncodingService` as the local CLI, eliminating duplicated encoding logic. Output files now have proper names (`Artist - Title (Final Karaoke Lossless 4k).mp4` instead of `output_4k_lossless.mp4`) and include title/end screen concatenation. Wheel deployed to GCS; worker installs at job start for hot updates without VM restart. Removed all fallback logic - single code path for consistent output across CLI, Cloud Run, and GCE. See [LESSONS-LEARNED.md](LESSONS-LEARNED.md#unify-encoding-logic-with-gcs-wheel-deployment).

- **Cloud Distribution Matches Local CLI** (2026-01-06): Fixed cloud distribution (Dropbox uploads) to match local CLI output structure. The `lyrics/` folder now includes: proper filenames (`(Lyrics Corrections).json` instead of `(Karaoke).json`), all reference lyrics files (`(Lyrics Genius).txt`, `(Lyrics Spotify).txt`, etc.), `(With Vocals).mkv`, and `previews/` subfolder with preview ASS files. See [LESSONS-LEARNED.md](LESSONS-LEARNED.md#cloud-distribution-must-match-local-cli-output-structure).

- **Agentic Correction Performance** (2026-01-05): Optimized agentic AI correction from ~5 minutes to ~55 seconds for 20 gaps (~5-6x speedup). Fixed anti-pattern of creating new model instance per gap (caused repeated 2s warm-up overhead). Now creates model once and processes gaps in parallel using ThreadPoolExecutor (default 5 workers). Configure via `AGENTIC_MAX_PARALLEL_GAPS` env var. See [LESSONS-LEARNED.md](LESSONS-LEARNED.md#reuse-llm-model-instances-across-operations).

- **Worker Logs Subcollection** (2026-01-04): Moved `worker_logs` from embedded array in job documents to Firestore subcollection (`jobs/{job_id}/logs`). Fixes job failures when logs exceed 1MB (job 501258e1 had 1.26MB of logs). New logs stored with 30-day TTL via Firestore TTL policy. Feature flag `USE_LOG_SUBCOLLECTION=true` (default). See [LESSONS-LEARNED.md](LESSONS-LEARNED.md#firestore-document-1mb-limit-with-embedded-arrays).

- **Admin Job Detail Enhancements** (2026-01-09): Enhanced admin job detail page with comprehensive job management features. Added: file downloads with signed GCS URLs grouped by category (stems, lyrics, outputs), inline editing of job fields (artist, title, theme, etc.), job reset actions to re-process from specific workflow checkpoints (pending, audio selection, review, instrumental selection), visual timeline with color-coded stages. New endpoints: `GET /api/admin/jobs/{job_id}/files`, `PATCH /api/admin/jobs/{job_id}`, `POST /api/admin/jobs/{job_id}/reset`. See [API.md](API.md#job-files-admin).

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
| [TESTING.md](TESTING.md) | Testing standards, CI requirements, Playwright |
| [API.md](API.md) | Backend endpoint reference |
| [BRAND-STYLE-GUIDE.md](BRAND-STYLE-GUIDE.md) | Brand colors, typography, UI patterns |
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
