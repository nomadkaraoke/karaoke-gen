"""
FastAPI application entry point for karaoke generation backend.

(Trivial comment touch to trigger backend CI on ephemeral runners after
 the 2026-05-17 dispatcher e2 fix; safe to remove on next backend edit.)
"""
import logging
import threading
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings, validate_production_config
from backend.api.routes import health, jobs, internal, file_upload, review, auth, audio_search, themes, users, admin, tenant, tenant_admin, tenant_bulk, rate_limits, push, catalog, encoding_worker, client_errors, client_events, bulk, parse_titles
from backend.services.tracing import setup_tracing, instrument_app, get_current_trace_id
from backend.services.structured_logging import setup_structured_logging
from backend.services.spacy_preloader import preload_spacy_model
from backend.services.nltk_preloader import preload_all_nltk_resources
from backend.services.langfuse_preloader import preload_langfuse_handler
from backend.middleware.audit_logging import AuditLoggingMiddleware
from backend.middleware.tenant import TenantMiddleware
from backend.middleware.edge_auth import EdgeAuthMiddleware
from backend.workers.registry import worker_registry


from backend.version import VERSION


# Configure structured logging (JSON in Cloud Run, human-readable locally)
# This must happen before any logging calls
setup_structured_logging()
logger = logging.getLogger(__name__)

# Initialize OpenTelemetry tracing (must happen before app creation)
tracing_enabled = setup_tracing(
    service_name="karaoke-backend",
    service_version=VERSION,
    enable_in_dev=False,  # Set to True to enable tracing locally
)


def validate_credentials_on_startup():
    """Validate OAuth credentials on startup and send alerts if needed."""
    try:
        from backend.services.credential_manager import get_credential_manager, CredentialStatus
        
        manager = get_credential_manager()
        results = manager.check_all_credentials()
        
        invalid_services = [
            result for result in results.values()
            if result.status in (CredentialStatus.INVALID, CredentialStatus.EXPIRED)
        ]
        
        if invalid_services:
            logger.warning(f"Some OAuth credentials need attention:")
            for result in invalid_services:
                logger.warning(f"  - {result.service}: {result.message}")
            
            # Try to send Discord alert
            discord_url = settings.get_secret("discord-alert-webhook") if hasattr(settings, 'get_secret') else None
            if discord_url:
                manager.send_credential_alert(invalid_services, discord_url)
                logger.info("Sent credential alert to Discord")
        else:
            logger.info("All OAuth credentials validated successfully")
            
    except Exception as e:
        logger.error(f"Failed to validate credentials on startup: {e}")


def _run_background_warmup():
    """Warm caches that used to block startup (runs in a daemon thread).

    Historically these preloads ran inline in lifespan startup, which meant
    Cloud Run held every routed request for the full ~7.5s they take (spaCy
    ~1s, NLTK ~2.5s, Langfuse ~3s, credential checks ~1.5s) on top of import
    time. None of them are needed to serve HTTP: they exist to make the FIRST
    lyrics-processing job fast (see docs/archive/2026-01-08-performance-
    investigation.md). Running them in a background thread right after boot
    keeps that warm-cache property (they finish within seconds, long before
    any real job runs) without gating readiness. Workers that race the warmup
    simply fall back to the preloaders' lazy paths.
    """
    warmup_start = time.time()

    # 1. SpaCy model (60+ second delay without preload)
    try:
        preload_spacy_model("en_core_web_sm")
    except Exception as e:
        logger.warning(f"SpaCy preload failed (will load lazily): {e}")

    # 2. NLTK cmudict (50-100+ second delay without preload)
    try:
        preload_all_nltk_resources()
    except Exception as e:
        logger.warning(f"NLTK preload failed (will load lazily): {e}")

    # 3. Langfuse callback handler (200+ second delay without preload)
    try:
        preload_langfuse_handler()
    except Exception as e:
        logger.warning(f"Langfuse preload failed (will initialize lazily): {e}")

    # Validate OAuth credentials (alerting only, nothing depends on it)
    try:
        validate_credentials_on_startup()
    except Exception as e:
        logger.error(f"Credential validation failed: {e}")

    logger.info(f"Background warmup complete in {time.time() - warmup_start:.2f}s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup and shutdown."""
    # Startup
    logger.info("Starting karaoke generation backend")
    logger.info(f"Environment: {settings.environment}")
    logger.info(f"GCS Bucket: {settings.gcs_bucket_name}")
    logger.info(f"Tracing enabled: {tracing_enabled}")

    # Fail fast if required production config is missing, rather than silently
    # running on dev defaults (wrong GCP project / GCS bucket / localhost worker
    # URL). No-op outside production. (Fallback audit 2026-06-09, Theme 7.)
    validate_production_config()

    # NLP model / credential warmup runs in the background so it doesn't gate
    # readiness — Cloud Run holds all routed requests until lifespan startup
    # returns, and these preloads were ~7.5s of the ~16s cold start.
    threading.Thread(
        target=_run_background_warmup, name="startup-warmup", daemon=True
    ).start()

    yield

    # Shutdown - best-effort parking of any still-registered workers.
    #
    # Reality check (incident 2026-09-13, job 41e06b90): Cloud Run services
    # SIGKILL instances ~10 seconds after SIGTERM — NOT the 600s this comment
    # previously claimed — and uvicorn only runs this lifespan-shutdown code
    # AFTER all BackgroundTasks have completed ("Waiting for background tasks
    # to complete"). A long-running worker task therefore keeps uvicorn stuck
    # in that wait until SIGKILL, and this hook never executes at all. It can
    # NOT be relied on to protect in-flight renders; that protection comes
    # from running them as Cloud Run Jobs instead (USE_CLOUD_RUN_JOBS_FOR_RENDER,
    # mirroring USE_CLOUD_RUN_JOBS_FOR_VIDEO from incident 2026-03-08).
    #
    # This hook is kept as a cheap safety net for the legacy flag-off path and
    # for the narrow case where workers finish right as SIGTERM lands. The wait
    # is capped well inside the 10s kill window so the parking pass below still
    # gets a chance to write Firestore state.
    logger.info("Shutdown requested, checking for active workers...")
    if worker_registry.has_active_workers():
        active = worker_registry.get_active_workers()
        logger.info(f"Active workers found: {active}")
        logger.info("Waiting for workers to complete (timeout: 5s)...")
        completed = await worker_registry.wait_for_completion(timeout=5)
        if not completed:
            logger.error(
                "Shutdown timeout - some workers may not have completed cleanly. "
                f"Remaining workers: {worker_registry.get_active_workers()}"
            )

        # Park any still-active render jobs so the auto-retry scheduler can
        # recover them. Safe to call unconditionally — it's a no-op if nothing
        # render-related is in flight.
        try:
            from backend.workers.render_video_worker import park_active_render_jobs_for_shutdown
            parked = park_active_render_jobs_for_shutdown()
            if parked:
                logger.warning(
                    f"Parked {parked} in-flight render job(s) for auto-retry "
                    f"due to shutdown"
                )
        except Exception as exc:
            logger.error(f"Render-job shutdown park failed: {exc!r}")
    else:
        logger.info("No active workers, proceeding with shutdown")

    logger.info("Shutting down karaoke generation backend")


# Create FastAPI app
app = FastAPI(
    title="Karaoke Generator API",
    description="Backend API for web-based karaoke video generation",
    version=VERSION,
    lifespan=lifespan
)

# Instrument FastAPI with OpenTelemetry (adds automatic spans for all requests)
if tracing_enabled:
    instrument_app(app)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add audit logging middleware (captures all requests with request_id for correlation)
app.add_middleware(AuditLoggingMiddleware)

# Add tenant detection middleware (extracts tenant from subdomain/headers)
app.add_middleware(TenantMiddleware)

# Edge origin lock (added last = outermost, runs first). Rejects direct-to-origin
# requests that bypassed the Cloudflare edge, before tenant/audit middleware do
# any work. No-op unless EDGE_AUTH_MODE is warn/enforce. See
# backend/middleware/edge_auth.py.
app.add_middleware(EdgeAuthMiddleware)

# Include routers
app.include_router(health.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(file_upload.router, prefix="/api")  # File upload endpoint
app.include_router(internal.router, prefix="/api")  # Internal worker endpoints
app.include_router(encoding_worker.router, prefix="/api")  # Encoding worker lifecycle
app.include_router(review.router, prefix="/api")  # Review UI compatibility endpoints
app.include_router(auth.router, prefix="/api")  # OAuth credential management
app.include_router(audio_search.router, prefix="/api")  # Audio search (artist+title mode)
app.include_router(themes.router, prefix="/api")  # Theme selection for styles
app.include_router(users.router, prefix="/api")  # User auth, credits, and Stripe webhooks
from backend.api.routes import referrals
app.include_router(referrals.router, prefix="/api")  # Referral links and payouts
app.include_router(admin.router, prefix="/api")  # Admin dashboard and management
app.include_router(rate_limits.router, prefix="/api")  # Rate limits admin management
app.include_router(push.router, prefix="/api")  # Push notification subscription management
app.include_router(catalog.router, prefix="/api")  # Catalog proxy for song/artist autocomplete
app.include_router(parse_titles.router, prefix="/api")  # kjbox karaoke-filename parser
app.include_router(bulk.router, prefix="/api")  # Bulk Mode: multi-job submission
from backend.api.routes import requests_board
app.include_router(requests_board.router, prefix="/api")  # Public song-request voting board
app.include_router(client_errors.router, prefix="/api")  # Frontend crash reports
app.include_router(client_events.router, prefix="/api")  # Frontend degradation telemetry (banner/unavailable/waveform events)
from backend.api.routes import karaokehunt
app.include_router(karaokehunt.router, prefix="/api")  # Retired KaraokeHunt app request interceptor
app.include_router(tenant.router)  # Tenant/white-label configuration (no /api prefix, router has it)
app.include_router(tenant_admin.router)  # Admin tenant provisioning (router has /api prefix)
app.include_router(tenant_bulk.router)  # Tenant bulk-upload filename analysis (router has /api prefix)


# Exception handlers
from fastapi import Request
from fastapi.responses import JSONResponse
from backend.exceptions import InsufficientCreditsError
from backend.i18n import t, get_locale_from_request


@app.exception_handler(InsufficientCreditsError)
async def insufficient_credits_exception_handler(request: Request, exc: InsufficientCreditsError):
    """Handle insufficient credits errors with 402 Payment Required status."""
    locale = get_locale_from_request(request)
    return JSONResponse(
        status_code=402,
        content={
            "detail": t(locale, "errors.insufficientCredits"),
            "credits_available": exc.credits_available,
            "credits_required": exc.credits_required,
            "buy_url": t(locale, "errors.insufficientCreditsBuyUrl"),
        },
    )


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "karaoke-gen-backend",
        "version": VERSION,
        "status": "running"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)

