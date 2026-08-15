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
