"""Typed exceptions for encoding worker lifecycle failures.

These let callers distinguish between transient capacity issues (retry will
likely help) and unexpected start failures (something is actually broken).
"""

from typing import FrozenSet


CAPACITY_ERROR_CODES: FrozenSet[str] = frozenset({
    "ZONE_RESOURCE_POOL_EXHAUSTED",
    "ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS",
    "STOCKOUT",
    "QUOTA_EXCEEDED",
})


# Sentinel code stamped on an EncodingWorkerInfraError so log filters and the
# park/retry path can tell a runtime worker-infra failure apart from a real
# GCE VM-start failure.
WORKER_INFRA_FAILURE_CODE = "worker_infra_failure"


# Substrings (matched case-insensitively) that identify a worker-side *infrastructure*
# failure — the VM booted and accepted the job but then could not talk to GCP to do
# real work. The canonical case (job 6452888e, 2026-08-31) was a fallback VM that
# could not reach the metadata server to fetch its default service-account token:
#   "Failed to retrieve https://metadata.google.internal/.../service-accounts/default/
#    ... Compute Engine Metadata server unavailable ... SSLCertVerificationError ..."
# These are properties of the *VM*, not the encode job — the same work will succeed on
# a healthy worker, so they must be retried/re-dispatched rather than failing the job.
# Kept deliberately specific so a genuine encode error (bad codec, corrupt input) never
# matches. Compared lowercase.
WORKER_INFRA_ERROR_MARKERS: FrozenSet[str] = frozenset({
    "metadata.google.internal",
    "metadata server unavailable",
    "compute engine metadata",
    "service-accounts/default",
    "sslcertverificationerror",
    "certificate_verify_failed",
    "could not automatically determine credentials",
    "defaultcredentialserror",
})


def is_worker_infra_error(message: str) -> bool:
    """True if a worker-reported error string looks like a VM-level infra/auth
    failure (metadata server unreachable, SSL/cert, missing credentials) rather
    than a genuine problem with the encode job itself."""
    if not message:
        return False
    lowered = message.lower()
    return any(marker in lowered for marker in WORKER_INFRA_ERROR_MARKERS)


class EncodingWorkerStartError(Exception):
    """Raised when an attempt to start an encoding worker VM fails.

    Carries the GCE error code so callers can react to specific failure modes.
    """

    def __init__(
        self,
        message: str,
        *,
        vm_name: str = "",
        zone: str = "",
        code: str = "",
    ) -> None:
        super().__init__(message)
        self.vm_name = vm_name
        self.zone = zone
        self.code = code


class EncodingWorkerCapacityError(EncodingWorkerStartError):
    """A capacity-related GCE failure (e.g. ZONE_RESOURCE_POOL_EXHAUSTED).

    The zone temporarily cannot allocate the requested machine type. Callers
    should treat this as recoverable — retrying after a wait, or trying an
    alternate zone, is likely to succeed.
    """


class EncodingWorkerInfraError(EncodingWorkerStartError):
    """A worker VM accepted the job but then hit a VM-level infrastructure/auth
    failure mid-run (e.g. it could not reach the GCE metadata server to fetch its
    service-account token — job 6452888e, 2026-08-31).

    Subclasses ``EncodingWorkerStartError`` on purpose: the failure is a property
    of the *worker*, not the encode job, so it should flow through the same
    recoverable path as a VM-start failure (the render worker parks the job for
    auto-retry; the final-encode Cloud Run Job retries). Before raising this,
    callers demote the offending VM so the retry lands on a healthy worker rather
    than looping on the broken one.
    """


# Marker the encoding worker writes into a job's status when it was interrupted
# by a worker-process restart (OOM, deploy, crash). Kept in sync with
# gce_encoding/persistence.py `_RESTART_FAIL_CODE`.
ENCODING_RESTART_FAILURE_CODE = "encoding_worker_restart"


class EncodingJobNotFoundError(RuntimeError):
    """The encoding worker returned HTTP 404 for a job status poll.

    Subclasses RuntimeError so existing `except RuntimeError` handlers and
    `"not found" in str(e)` checks keep working, while giving callers a precise
    type to branch on (e.g. distinguishing a lost job from other RuntimeErrors)
    without parsing the message text. Keep the message containing "not found".
    """


class EncodingJobLostError(Exception):
    """The encoding worker lost a job mid-run (restart wiped its in-memory state).

    Distinct from a transient poll blip: the ffmpeg process and temp work dir
    are gone, so the *same* job will never complete. The only recovery is to
    resubmit the work as a fresh job. Deliberately NOT a subclass of
    RuntimeError so it escapes the poll-failure tolerance in
    `wait_for_completion` instead of being swallowed as a retryable blip.
    """

    def __init__(self, message: str, *, job_id: str = "") -> None:
        super().__init__(message)
        self.job_id = job_id


def classify_gce_error(code: str, message: str, *, vm_name: str, zone: str) -> EncodingWorkerStartError:
    """Wrap a GCE error code/message in the appropriate typed exception."""
    if code in CAPACITY_ERROR_CODES:
        return EncodingWorkerCapacityError(
            f"VM {vm_name} could not be started in {zone}: {code} — {message}",
            vm_name=vm_name,
            zone=zone,
            code=code,
        )
    return EncodingWorkerStartError(
        f"VM {vm_name} start failed in {zone}: {code or 'unknown error'} — {message or 'no message'}",
        vm_name=vm_name,
        zone=zone,
        code=code,
    )
