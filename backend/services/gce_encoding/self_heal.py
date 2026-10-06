"""Self-healing for an encoding worker that booted into a broken state.

Incident 2026-10-06 (job ebfe4344): the primary VM booted before its network was
up, so ``bootstrap.sh`` could not download the CI-managed ``startup.sh`` from GCS
and fell back to the Packer-baked ``startup-fallback.sh``. That fallback wrote an
env file with no ``ENCODING_API_KEY`` and no ``GCE_METADATA_MTLS_MODE=none``, so
google-auth talked mTLS to the metadata server and every job failed with
``CERTIFICATE_VERIFY_FAILED``. The worker still answered /health, and the backend
deliberately keeps retrying the primary on infra errors, so customer renders
looped for an hour until someone ran ``systemctl restart encoding-worker``.

This module performs that restart automatically. Exiting the process makes
systemd (``Restart=always``) re-run ``ExecStartPre=bootstrap.sh``, which, now that
the network is up, downloads the real ``startup.sh`` and rewrites the env file.

Only active on an encoding-worker VM running under systemd (``INVOCATION_ID`` is
set by systemd for every service process, and the Packer image installs
``/opt/encoding-worker/bootstrap.sh``), so local runs, CI and tests never exit.
"""

import logging
import os
import threading
import time
from typing import Callable, Optional

from backend.services.encoding_errors import is_worker_infra_error

logger = logging.getLogger(__name__)

# Credential probe: a few attempts so a single metadata-server blip doesn't
# restart a healthy worker.
PROBE_ATTEMPTS = 3
PROBE_RETRY_SECONDS = 5.0

# After an infra-failed job, wait before checking/restarting so the backend's
# status poll (every ~10s) sees the "failed" status first and parks the job for
# retry rather than hitting a vanished worker.
RESTART_DELAY_SECONDS = 30.0

_restart_lock = threading.Lock()
_restart_pending = False


BOOTSTRAP_PATH = "/opt/encoding-worker/bootstrap.sh"


def running_on_worker_vm() -> bool:
    """True only for the encoding-worker systemd service on a worker VM."""
    return bool(os.environ.get("INVOCATION_ID")) and os.path.exists(BOOTSTRAP_PATH)


def _refresh_default_credentials() -> None:
    import google.auth
    import google.auth.transport.requests

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(google.auth.transport.requests.Request())


def probe_credentials(
    attempts: int = PROBE_ATTEMPTS,
    retry_seconds: float = PROBE_RETRY_SECONDS,
    refresh: Callable[[], None] = _refresh_default_credentials,
) -> Optional[str]:
    """Try to fetch a service-account token. Returns None if healthy, else the last error."""
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            refresh()
            return None
        except Exception as e:  # noqa: BLE001 — any failure means "can't authenticate"
            last_error = str(e) or repr(e)
            logger.warning(
                "Credential probe failed (attempt %d/%d): %s", attempt, attempts, last_error
            )
            if attempt < attempts:
                time.sleep(retry_seconds)
    return last_error


def boot_health_problem(probe: Callable[[], Optional[str]] = probe_credentials) -> Optional[str]:
    """Describe why this worker booted broken, or None if it is fine to serve.

    Returns None outside systemd so local/dev runs are unaffected.
    """
    if not running_on_worker_vm():
        return None
    problems = []
    if not os.environ.get("ENCODING_API_KEY"):
        problems.append("ENCODING_API_KEY is not set (startup fell back without the real env)")
    probe_error = probe()
    if probe_error:
        problems.append(f"cannot obtain GCP credentials: {probe_error}")
    return "; ".join(problems) or None


def schedule_restart_if_unhealthy(
    error_text: str,
    has_active_jobs: Callable[[], bool],
    *,
    delay_seconds: float = RESTART_DELAY_SECONDS,
    probe: Callable[[], Optional[str]] = probe_credentials,
    exit_process: Callable[[int], None] = os._exit,
) -> bool:
    """After a job failed with a VM infra/auth error, restart the worker if it is still broken.

    Runs in a daemon thread: waits ``delay_seconds``, re-probes credentials, and if
    they still fail, waits for in-flight jobs to drain and exits so systemd
    re-bootstraps the service. Returns True if a check was scheduled.
    """
    global _restart_pending
    if not running_on_worker_vm() or not is_worker_infra_error(error_text):
        return False
    with _restart_lock:
        if _restart_pending:
            return False
        _restart_pending = True

    def _run() -> None:
        global _restart_pending
        try:
            while True:
                time.sleep(delay_seconds)
                probe_error = probe()
                if not probe_error:
                    logger.info("Credentials healthy again after infra failure; not restarting")
                    return
                if has_active_jobs():
                    logger.warning(
                        "Credentials still failing but jobs are in flight; "
                        "re-checking before restarting: %s", probe_error,
                    )
                    continue
                logger.critical(
                    "Encoding worker cannot authenticate to GCP (%s); exiting so systemd "
                    "re-runs the bootstrap", probe_error,
                )
                exit_process(1)
                return
        finally:
            with _restart_lock:
                _restart_pending = False

    threading.Thread(target=_run, name="encoding-worker-self-heal", daemon=True).start()
    return True
