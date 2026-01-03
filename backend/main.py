"""
FastAPI application entry point for karaoke generation backend.
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings
from backend.api.routes import health, jobs, internal, file_upload, review, auth, audio_search, themes, users, admin
from backend.services.tracing import setup_tracing, instrument_app, get_current_trace_id
from backend.services.structured_logging import setup_structured_logging
from backend.middleware.audit_logging import AuditLoggingMiddleware


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


async def validate_credentials_on_startup():
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup and shutdown."""
    # Startup
    logger.info("Starting karaoke generation backend")
    logger.info(f"Environment: {settings.environment}")
    logger.info(f"GCS Bucket: {settings.gcs_bucket_name}")
    logger.info(f"Tracing enabled: {tracing_enabled}")
    
    # Validate OAuth credentials (non-blocking)
    try:
        await validate_credentials_on_startup()
    except Exception as e:
        logger.error(f"Credential validation failed: {e}")
    
    yield
    
    # Shutdown
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
    allow_origins=["*"],  # Configure this properly for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add audit logging middleware (captures all requests with request_id for correlation)
app.add_middleware(AuditLoggingMiddleware)

# Include routers
app.include_router(health.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(file_upload.router, prefix="/api")  # File upload endpoint
app.include_router(internal.router, prefix="/api")  # Internal worker endpoints
app.include_router(review.router, prefix="/api")  # Review UI compatibility endpoints
app.include_router(auth.router, prefix="/api")  # OAuth credential management
app.include_router(audio_search.router, prefix="/api")  # Audio search (artist+title mode)
app.include_router(themes.router, prefix="/api")  # Theme selection for styles
app.include_router(users.router, prefix="/api")  # User auth, credits, and Stripe webhooks
app.include_router(admin.router, prefix="/api")  # Admin dashboard and management


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

