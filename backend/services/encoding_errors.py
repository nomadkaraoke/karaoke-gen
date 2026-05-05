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
