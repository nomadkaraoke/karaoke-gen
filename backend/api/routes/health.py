"""
Health check routes.
"""
import os
import logging
import subprocess
import platform
from fastapi import APIRouter
from typing import Dict, Any, Optional

from backend.version import VERSION
from backend.services.flacfetch_client import get_flacfetch_client
from backend.services.email_service import get_email_service
from backend.services.stripe_service import get_stripe_service

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


@router.get("/health/detailed")
async def detailed_health_check() -> Dict[str, Any]:
    """
    Detailed health check including dependencies.

    Use this to debug issues with Transmission, flacfetch service, etc.
    """
    transmission_status = check_transmission_status()
    flacfetch_status = await check_flacfetch_service_status()

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
        },
        "services": {
            "email": email_status,
            "stripe": stripe_status,
        }
    }


@router.get("/readiness")
async def readiness_check() -> Dict[str, str]:
    """Readiness check endpoint for Cloud Run."""
    return {
        "status": "ready",
        "service": "karaoke-gen-backend"
    }

