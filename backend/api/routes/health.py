"""
Health check routes.
"""
import os
import logging
import subprocess
import platform
from fastapi import APIRouter, Depends
from typing import Dict, Any, Optional, Tuple

from backend.version import VERSION, COMMIT_SHA, PR_NUMBER, PR_TITLE, STARTUP_TIME
from backend.api.dependencies import optional_auth
from backend.services.auth_service import UserType
from backend.services.flacfetch_client import get_flacfetch_client
from backend.services.email_service import get_email_service
from backend.services.stripe_service import get_stripe_service
from backend.services.encoding_service import get_encoding_service
from backend.services.spacy_preloader import get_preloaded_model, is_model_preloaded
from backend.services.nltk_preloader import get_preloaded_cmudict, is_cmudict_preloaded
from backend.services.langfuse_preloader import get_preloaded_langfuse_handler, is_langfuse_preloaded, is_langfuse_configured
from backend.services.job_health_service import check_job_consistency_detailed
from backend.services.job_manager import JobManager
from backend.models.job import JobStatus

router = APIRouter()
logger = logging.getLogger(__name__)


def get_system_info() -> Dict[str, Any]:
    """Get system information for performance analysis."""
    info = {
        "platform": platform.system(),
        "platform_release": platform.release(),
        "python_version": platform.python_version(),
    }

    # Get CPU info
    try:
        cpu_count = os.cpu_count() or 0
        info["cpu_count"] = cpu_count

        # Try to get more detailed CPU info on Linux
        if platform.system() == "Linux":
            try:
                with open("/proc/cpuinfo", "r") as f:
                    for line in f:
                        if "model name" in line:
                            info["cpu_model"] = line.split(":")[1].strip()
                            break
            except Exception:
                pass
    except Exception as e:
        info["cpu_error"] = str(e)

    # Get memory info
    try:
        if platform.system() == "Linux":
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        mem_kb = int(line.split()[1])
                        info["memory_gb"] = round(mem_kb / (1024 * 1024), 1)
                        break
    except Exception as e:
        info["memory_error"] = str(e)

    return info


def get_ffmpeg_info() -> Dict[str, Any]:
    """Get FFmpeg version and configuration for debugging encoding performance."""
    try:
        # Get FFmpeg version
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            lines = result.stdout.split("\n")
            version_line = lines[0] if lines else "unknown"

            # Check for hardware acceleration support
            hw_accel = {}
            config_result = subprocess.run(
                ["ffmpeg", "-hwaccels"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if config_result.returncode == 0:
                hw_lines = config_result.stdout.strip().split("\n")
                hw_accel["available"] = [h.strip() for h in hw_lines[1:] if h.strip()]

            # Check encoder availability
            encoders = {}
            encoder_result = subprocess.run(
                ["ffmpeg", "-encoders"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if encoder_result.returncode == 0:
                encoder_output = encoder_result.stdout
                # Check for specific encoders we care about
                encoders["libx264"] = "libx264" in encoder_output
                encoders["h264_nvenc"] = "h264_nvenc" in encoder_output
                encoders["h264_vaapi"] = "h264_vaapi" in encoder_output
                encoders["hevc_nvenc"] = "hevc_nvenc" in encoder_output

            return {
                "available": True,
                "version": version_line,
                "hw_acceleration": hw_accel,
                "encoders": encoders,
            }
        else:
            return {
                "available": False,
                "error": result.stderr[:500] if result.stderr else "Unknown error",
            }
    except subprocess.TimeoutExpired:
        return {"available": False, "error": "Timeout getting FFmpeg info"}
    except FileNotFoundError:
        return {"available": False, "error": "FFmpeg not found"}
    except Exception as e:
        return {"available": False, "error": str(e)}


def check_transmission_status() -> Dict[str, Any]:
    """Check if Transmission daemon is available and responsive."""
    try:
        import transmission_rpc
        host = os.environ.get("TRANSMISSION_HOST", "localhost")
        port = int(os.environ.get("TRANSMISSION_PORT", "9091"))
        
        client = transmission_rpc.Client(host=host, port=port, timeout=5)
        stats = client.session_stats()
        
        # Get detailed torrent info
        torrents = client.get_torrents()
        torrent_details = []
        for t in torrents:
            torrent_info = {
                "name": t.name,
                "progress": round(t.progress, 1),
                "status": str(t.status),
                "peers": t.peers_connected if hasattr(t, 'peers_connected') else 0,
                "download_speed": round(t.rate_download / 1024, 1) if hasattr(t, 'rate_download') else 0,  # KB/s
            }
            # Check if stalled (downloading but no progress and no peers)
            if t.status.downloading and t.progress < 100 and torrent_info['peers'] == 0:
                torrent_info['stalled'] = True
            torrent_details.append(torrent_info)
        
        return {
            "available": True,
            "host": host,
            "port": port,
            "download_dir": stats.download_dir if hasattr(stats, 'download_dir') else None,
            "active_torrent_count": stats.active_torrent_count if hasattr(stats, 'active_torrent_count') else 0,
            "torrents": torrent_details,
        }
    except ImportError as e:
        return {
            "available": False,
            "error": f"transmission_rpc not installed: {e}",
        }
    except Exception as e:
        return {
            "available": False,
            "host": os.environ.get("TRANSMISSION_HOST", "localhost"),
            "port": int(os.environ.get("TRANSMISSION_PORT", "9091")),
            "error": str(e),
        }


@router.get("/health")
async def health_check() -> Dict[str, str]:
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "karaoke-gen-backend"
    }


async def check_flacfetch_service_status() -> Dict[str, Any]:
    """Check if remote flacfetch service is available and healthy."""
    client = get_flacfetch_client()
    
    if not client:
        return {
            "configured": False,
            "message": "Remote flacfetch service not configured (FLACFETCH_API_URL not set)",
        }
    
    try:
        health = await client.health_check()
        return {
            "configured": True,
            "available": True,
            "status": health.get("status"),
            "version": health.get("version"),
            "transmission": health.get("transmission", {}),
            "disk": health.get("disk", {}),
            "providers": health.get("providers", {}),
        }
    except Exception as e:
        return {
            "configured": True,
            "available": False,
            "error": str(e),
        }


async def check_encoding_worker_status() -> Dict[str, Any]:
    """Check if GCE encoding worker is available and healthy."""
    encoding_service = get_encoding_service()

    if not encoding_service.is_configured:
        return {
            "configured": False,
            "enabled": encoding_service.settings.use_gce_encoding,
            "message": "GCE encoding worker not configured (ENCODING_WORKER_URL or API key not set)",
        }

    try:
        health = await encoding_service.health_check()
        return {
            "configured": True,
            "enabled": encoding_service.is_enabled,
            "available": health.get("status") == "ok",
            "status": health.get("status"),
            "active_jobs": health.get("active_jobs", 0),
            "queue_length": health.get("queue_length", 0),
            "ffmpeg_version": health.get("ffmpeg_version"),
            "wheel_version": health.get("wheel_version"),
        }
    except Exception as e:
        return {
            "configured": True,
            "enabled": encoding_service.is_enabled,
            "available": False,
            "error": str(e),
        }


@router.get("/health/encoding-worker")
async def encoding_worker_health() -> Dict[str, Any]:
    """
    Lightweight endpoint to check encoding worker status.

    Returns minimal info for frontend footer display.
    No authentication required.
    """
    status = await check_encoding_worker_status()

    # Return simplified response for frontend
    if not status.get("configured"):
        return {
            "available": False,
            "status": "not_configured",
        }

    if not status.get("available"):
        return {
            "available": False,
            "status": "offline",
            "error": status.get("error"),
        }

    return {
        "available": True,
        "status": "ok",
        "version": status.get("wheel_version"),
        "active_jobs": status.get("active_jobs", 0),
        "queue_length": status.get("queue_length", 0),
    }


@router.get("/health/flacfetch")
async def flacfetch_health() -> Dict[str, Any]:
    """
    Lightweight endpoint to check flacfetch service status.

    Returns minimal info for frontend footer display.
    No authentication required.
    """
    status = await check_flacfetch_service_status()

    # Return simplified response for frontend
    if not status.get("configured"):
        return {
            "available": False,
            "status": "not_configured",
        }

    if not status.get("available"):
        return {
            "available": False,
            "status": "offline",
            "error": status.get("error"),
        }

    return {
        "available": True,
        "status": "ok",
        "version": status.get("version"),
    }


def check_audio_separator_status() -> Dict[str, Any]:
    """Check audio separator availability without calling the remote GPU service."""
    from backend.config import get_settings

    settings = get_settings()
    if not settings.audio_separator_api_url:
        return {
            "available": False,
            "status": "not_configured",
        }

    try:
        from importlib.metadata import version
        pkg_version = version("audio-separator")
    except Exception:
        pkg_version = None

    return {
        "available": True,
        "status": "ok",
        "version": pkg_version,
    }


@router.get("/health/audio-separator")
async def audio_separator_health() -> Dict[str, Any]:
    """
    Return the installed audio-separator package version.

    Does NOT call the remote GPU service — that would cold-start an expensive
    Cloud Run GPU instance (~$0.07 per wake). Instead reports the locally
    installed package version, which matches what the audio worker uses.

    No authentication required.
    """
    from backend.config import get_settings

    settings = get_settings()
    if not settings.audio_separator_api_url:
        return {
            "available": False,
            "status": "not_configured",
        }

    try:
        from importlib.metadata import version
        pkg_version = version("audio-separator")
    except Exception:
        pkg_version = None

    return {
        "available": True,
        "status": "ok",
        "version": pkg_version,
    }


def _get_encoding_worker_manager():
    """Get the EncodingWorkerManager instance. Separated for testability."""
    try:
        encoding_service = get_encoding_service()
        if hasattr(encoding_service, '_worker_manager') and encoding_service._worker_manager:
            return encoding_service._worker_manager
    except Exception:
        pass
    return None


@router.get("/health/system-status")
async def system_status(
    auth: Optional[Tuple] = Depends(optional_auth),
) -> Dict[str, Any]:
    """
    Aggregated system status for all services.

    Returns basic info for everyone. Admin users get additional
    blue-green deployment details for the encoder.
    """
    is_admin = auth is not None and len(auth) >= 2 and auth[1] == UserType.ADMIN

    # Fetch all health statuses concurrently
    import asyncio
    encoding_task = check_encoding_worker_status()
    flacfetch_task = check_flacfetch_service_status()
    encoding_status, flacfetch_status = await asyncio.gather(
        encoding_task, flacfetch_task
    )
    separator_status = check_audio_separator_status()

    # Build frontend service info (from env vars baked at build time — returned as-is)
    frontend_svc = {
        "status": "ok",
        "version": VERSION,  # Frontend and backend share the same version from pyproject.toml
    }

    # Build backend service info
    backend_svc = {
        "status": "ok",
        "version": VERSION,
        "deployed_at": STARTUP_TIME,
    }
    if COMMIT_SHA:
        backend_svc["commit_sha"] = COMMIT_SHA
    if PR_NUMBER:
        backend_svc["pr_number"] = PR_NUMBER
    if PR_TITLE:
        backend_svc["pr_title"] = PR_TITLE

    # Build encoder service info
    encoder_svc = {
        "status": "ok" if encoding_status.get("available") else "offline",
        "version": encoding_status.get("wheel_version"),
        "active_jobs": encoding_status.get("active_jobs", 0),
    }

    # Build flacfetch service info
    flacfetch_svc = {
        "status": "ok" if flacfetch_status.get("available") else "offline",
        "version": flacfetch_status.get("version"),
    }

    # Build separator service info
    separator_svc = {
        "status": "ok" if separator_status.get("available") else "offline",
        "version": separator_status.get("version"),
    }

    # Admin-only: blue-green encoder details from Firestore
    if is_admin:
        manager = _get_encoding_worker_manager()
        if manager:
            try:
                config = manager.get_config()
                encoder_svc["admin_details"] = {
                    "primary_vm": config.primary_vm,
                    "primary_ip": config.primary_ip,
                    "primary_version": config.primary_version,
                    "primary_deployed_at": config.primary_deployed_at,
                    "secondary_vm": config.secondary_vm,
                    "secondary_ip": config.secondary_ip,
                    "secondary_version": config.secondary_version,
                    "secondary_deployed_at": config.secondary_deployed_at,
                    "last_swap_at": config.last_swap_at,
                    "deploy_in_progress": config.deploy_in_progress,
                    "active_jobs": encoding_status.get("active_jobs", 0),
                    "queue_length": encoding_status.get("queue_length", 0),
                }
            except Exception as e:
                logger.warning(f"Failed to get blue-green config: {e}")

        # Add error details for offline services
        if not flacfetch_status.get("available"):
            flacfetch_svc["admin_details"] = {"error": flacfetch_status.get("error")}
        if not separator_status.get("available"):
            separator_svc["admin_details"] = {"error": separator_status.get("error")}
        if not encoding_status.get("available") and "admin_details" not in encoder_svc:
            encoder_svc["admin_details"] = {"error": encoding_status.get("error")}

    return {
        "services": {
            "frontend": frontend_svc,
            "backend": backend_svc,
            "encoder": encoder_svc,
            "flacfetch": flacfetch_svc,
            "separator": separator_svc,
        }
    }


@router.get("/health/detailed")
async def detailed_health_check() -> Dict[str, Any]:
    """
    Detailed health check including dependencies.

    Use this to debug issues with Transmission, flacfetch service, etc.
    """
    transmission_status = check_transmission_status()
    flacfetch_status = await check_flacfetch_service_status()
    encoding_status = await check_encoding_worker_status()

    # Check email service
    email_service = get_email_service()
    email_status = {
        "configured": email_service.is_configured(),
        "provider": type(email_service.provider).__name__,
    }

    # Check Stripe service
    stripe_service = get_stripe_service()
    stripe_status = {
        "configured": stripe_service.is_configured(),
    }

    # Log for debugging
    if not transmission_status.get("available"):
        logger.warning(f"Local Transmission not available: {transmission_status.get('error')}")
    else:
        logger.info(f"Local Transmission available at {transmission_status.get('host')}:{transmission_status.get('port')}")

    if flacfetch_status.get("configured") and not flacfetch_status.get("available"):
        logger.warning(f"Remote flacfetch service not available: {flacfetch_status.get('error')}")
    elif flacfetch_status.get("available"):
        logger.info(f"Remote flacfetch service healthy: {flacfetch_status.get('status')}")

    if not email_status["configured"]:
        logger.warning("Email service not configured - magic links will not work")

    if not stripe_status["configured"]:
        logger.warning("Stripe service not configured - payments will not work")

    # Get system and FFmpeg info for performance analysis
    system_info = get_system_info()
    ffmpeg_info = get_ffmpeg_info()

    return {
        "status": "healthy",
        "service": "karaoke-gen-backend",
        "version": VERSION,
        "system": system_info,
        "ffmpeg": ffmpeg_info,
        "dependencies": {
            "transmission_local": transmission_status,
            "flacfetch_remote": flacfetch_status,
            "encoding_worker": encoding_status,
        },
        "services": {
            "email": email_status,
            "stripe": stripe_status,
        }
    }


@router.get("/health/preload-status")
async def preload_status() -> Dict[str, Any]:
    """
    Check status of preloaded resources for performance optimization.

    Use this endpoint to verify that NLTK, SpaCy, and Langfuse resources
    were successfully preloaded at container startup. If any show as
    not preloaded, check Cloud Run startup logs for errors.

    Expected state after successful deployment:
    - spacy.preloaded: true
    - nltk.preloaded: true
    - langfuse.preloaded: true (if configured) or configured: false
    """
    # SpaCy status
    spacy_model = get_preloaded_model("en_core_web_sm")
    spacy_status = {
        "preloaded": is_model_preloaded("en_core_web_sm"),
        "model": "en_core_web_sm",
    }
    if spacy_model:
        spacy_status["vocab_size"] = len(spacy_model.vocab)

    # NLTK status
    cmudict = get_preloaded_cmudict()
    nltk_status = {
        "preloaded": is_cmudict_preloaded(),
        "resource": "cmudict",
    }
    if cmudict:
        nltk_status["entries"] = len(cmudict)

    # Langfuse status
    langfuse_handler = get_preloaded_langfuse_handler()
    langfuse_status = {
        "configured": is_langfuse_configured(),
        "preloaded": is_langfuse_preloaded(),
    }
    if langfuse_handler:
        langfuse_status["handler_type"] = type(langfuse_handler).__name__

    # Overall status
    all_preloaded = (
        spacy_status["preloaded"]
        and nltk_status["preloaded"]
        and (langfuse_status["preloaded"] or not langfuse_status["configured"])
    )

    return {
        "status": "ok" if all_preloaded else "degraded",
        "message": "All resources preloaded" if all_preloaded else "Some resources not preloaded - check startup logs",
        "spacy": spacy_status,
        "nltk": nltk_status,
        "langfuse": langfuse_status,
        "performance_impact": {
            "spacy_preload": "Saves ~60s on first lyrics correction",
            "nltk_preload": "Saves ~100-150s on SyllablesMatchHandler init",
            "langfuse_preload": "Saves ~200s on AgenticCorrector init",
        }
    }


@router.get("/health/job-consistency")
async def job_consistency_check(
    status: Optional[str] = None,
    limit: int = 50
) -> Dict[str, Any]:
    """
    Check job consistency across the system.

    This endpoint identifies jobs with state inconsistencies that may indicate
    bugs in the state machine or trigger logic. Use this to find stuck jobs
    or jobs in invalid states.

    Args:
        status: Optional filter by job status (e.g., "pending", "downloading")
        limit: Maximum number of jobs to check (default 50)

    Returns:
        Dictionary with:
        - total_checked: Number of jobs checked
        - inconsistent_count: Number of jobs with issues
        - inconsistent_jobs: List of job details with their issues
        - summary: Summary of issue types found
    """
    job_manager = JobManager()

    # Query jobs to check
    query_params = {}
    if status:
        try:
            job_status = JobStatus(status)
            query_params['status'] = job_status
        except ValueError:
            return {
                "error": f"Invalid status: {status}",
                "valid_statuses": [s.value for s in JobStatus]
            }

    # Get jobs from Firestore
    jobs = job_manager.list_jobs(limit=limit, **query_params)

    # Check each job for consistency
    inconsistent_jobs = []
    issue_summary = {}

    for job in jobs:
        result = check_job_consistency_detailed(job)
        if not result['is_healthy']:
            inconsistent_jobs.append(result)
            # Count issue types
            for issue in result['issues']:
                issue_type = issue.split(':')[0] if ':' in issue else issue
                issue_summary[issue_type] = issue_summary.get(issue_type, 0) + 1

    return {
        "total_checked": len(jobs),
        "inconsistent_count": len(inconsistent_jobs),
        "inconsistent_jobs": inconsistent_jobs,
        "summary": issue_summary,
        "status_filter": status,
        "limit": limit,
    }


@router.get("/readiness")
async def readiness_check() -> Dict[str, str]:
    """Readiness check endpoint for Cloud Run."""
    return {
        "status": "ready",
        "service": "karaoke-gen-backend"
    }

