"""
Blue-Green Encoding Worker Manager.

Manages the lifecycle and role assignment of two GCE encoding worker VMs
(blue/green) via a Firestore config document as the single source of truth.

The config document at `config/encoding-worker` tracks which VM is primary
(actively serving encoding requests) and which is secondary (idle or being
deployed to). This enables zero-downtime deployments by swapping roles
after deploying new code to the secondary.

Usage:
    from google.cloud import firestore
    from google.cloud.compute_v1 import InstancesClient

    db = firestore.Client()
    compute = InstancesClient()
    manager = EncodingWorkerManager(db, compute)

    # Get current config
    config = manager.get_config()
    print(f"Primary: {config.primary_vm} at {config.primary_url}")

    # Ensure primary VM is running before dispatching work
    result = manager.ensure_primary_running()
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Any, Optional

import aiohttp

from google.cloud import firestore

from backend.services.encoding_errors import (
    EncodingWorkerCapacityError,
    EncodingWorkerStartError,
    classify_gce_error,
)

logger = logging.getLogger(__name__)

# How long to wait for a compute.instances.start operation to settle. The GCE
# operation itself is fast (1-2s) — we just need to know whether it succeeded
# or returned an immediate error like ZONE_RESOURCE_POOL_EXHAUSTED.
START_OPERATION_TIMEOUT_SECONDS = 30.0

CONFIG_COLLECTION = "config"
CONFIG_DOCUMENT = "encoding-worker"
ENCODING_WORKER_PORT = 8080


@dataclass
class EncodingWorkerConfig:
    """Represents the Firestore config/encoding-worker document."""

    primary_vm: str
    primary_ip: str
    primary_version: str
    primary_deployed_at: Optional[str]
    secondary_vm: str
    secondary_ip: str
    secondary_version: Optional[str]
    secondary_deployed_at: Optional[str]
    last_swap_at: Optional[str]
    last_activity_at: Optional[str]
    deploy_in_progress: bool
    deploy_in_progress_since: Optional[str]
    # Optional capacity-fallback override: when ensure_any_running falls back
    # to a non-primary VM (e.g. due to ZONE_RESOURCE_POOL_EXHAUSTED), these
    # fields point at the active fallback so requests target the right VM.
    # Cleared when the primary becomes healthy again.
    active_override_vm: Optional[str] = None
    active_override_ip: Optional[str] = None
    active_override_zone: Optional[str] = None
    active_override_set_at: Optional[str] = None

    @property
    def primary_url(self) -> str:
        return f"http://{self.primary_ip}:{ENCODING_WORKER_PORT}"

    @property
    def secondary_url(self) -> str:
        return f"http://{self.secondary_ip}:{ENCODING_WORKER_PORT}"

    @property
    def active_url(self) -> str:
        """URL the encoding service should target right now.

        Returns the capacity-fallback override if set, otherwise the primary
        URL. The override mechanism lets us route around a zone that's
        temporarily out of c4d-highcpu-32 capacity without rewriting the
        blue-green primary/secondary tracking.
        """
        if self.active_override_ip:
            return f"http://{self.active_override_ip}:{ENCODING_WORKER_PORT}"
        return self.primary_url


@dataclass
class EncodingWorkerCandidate:
    """A VM that ensure_any_running may try to start.

    Carries everything needed to talk to the worker: VM name + zone (so the
    GCE compute client targets the right zone) and external IP (so successful
    starts can be persisted as the active URL override).
    """

    vm_name: str
    zone: str
    ip: str

    @property
    def url(self) -> str:
        return f"http://{self.ip}:{ENCODING_WORKER_PORT}"


class EncodingWorkerManager:
    """Manages blue-green encoding worker VMs via Firestore config and Compute API."""

    def __init__(self, db, compute_client=None, project_id="nomadkaraoke", zone="us-central1-c"):
        self._db = db
        self._compute = compute_client
        self._project_id = project_id
        self._zone = zone

    def _doc_ref(self):
        """Return a reference to the config/encoding-worker document."""
        return self._db.collection(CONFIG_COLLECTION).document(CONFIG_DOCUMENT)

    # ------------------------------------------------------------------
    # Firestore config operations (Task 1)
    # ------------------------------------------------------------------

    def get_config(self) -> EncodingWorkerConfig:
        """Read config/encoding-worker from Firestore and return as dataclass."""
        snapshot = self._doc_ref().get()
        if not snapshot.exists:
            raise ValueError(
                f"Encoding worker config document not found at "
                f"{CONFIG_COLLECTION}/{CONFIG_DOCUMENT}"
            )
        data = snapshot.to_dict()
        return EncodingWorkerConfig(
            primary_vm=data["primary_vm"],
            primary_ip=data["primary_ip"],
            primary_version=data["primary_version"],
            primary_deployed_at=data.get("primary_deployed_at"),
            secondary_vm=data["secondary_vm"],
            secondary_ip=data["secondary_ip"],
            secondary_version=data.get("secondary_version"),
            secondary_deployed_at=data.get("secondary_deployed_at"),
            last_swap_at=data.get("last_swap_at"),
            last_activity_at=data.get("last_activity_at"),
            deploy_in_progress=data.get("deploy_in_progress", False),
            deploy_in_progress_since=data.get("deploy_in_progress_since"),
            active_override_vm=data.get("active_override_vm"),
            active_override_ip=data.get("active_override_ip"),
            active_override_zone=data.get("active_override_zone"),
            active_override_set_at=data.get("active_override_set_at"),
        )

    def update_activity(self) -> None:
        """Update last_activity_at timestamp to record recent usage."""
        now = datetime.now(UTC).isoformat()
        self._doc_ref().update({"last_activity_at": now})
        logger.info("Updated encoding worker last_activity_at to %s", now)

    def swap_roles(
        self,
        new_primary_vm: str,
        new_primary_ip: str,
        new_primary_version: str,
        old_primary_vm: str,
        old_primary_ip: str,
        old_primary_version: str,
    ) -> None:
        """Atomically swap primary and secondary roles using a Firestore transaction.

        Args:
            new_primary_vm: VM name that will become the new primary.
            new_primary_ip: Internal IP of the new primary.
            new_primary_version: Version deployed on the new primary.
            old_primary_vm: Current primary VM name (becomes secondary).
            old_primary_ip: Internal IP of the old primary.
            old_primary_version: Version on the old primary.
        """
        transaction = self._db.transaction()
        doc_ref = self._doc_ref()
        now = datetime.now(UTC).isoformat()

        @firestore.transactional
        def _swap_in_transaction(txn, ref):
            txn.update(ref, {
                "primary_vm": new_primary_vm,
                "primary_ip": new_primary_ip,
                "primary_version": new_primary_version,
                "primary_deployed_at": now,
                "secondary_vm": old_primary_vm,
                "secondary_ip": old_primary_ip,
                "secondary_version": old_primary_version,
                "secondary_deployed_at": None,
                "last_swap_at": now,
                "deploy_in_progress": False,
                "deploy_in_progress_since": None,
            })

        _swap_in_transaction(transaction, doc_ref)
        logger.info(
            "Swapped encoding worker roles: %s -> primary, %s -> secondary",
            new_primary_vm, old_primary_vm,
        )

    # ------------------------------------------------------------------
    # VM lifecycle operations (Task 2)
    # ------------------------------------------------------------------

    def get_vm_status(self, vm_name: str, zone: Optional[str] = None) -> str:
        """Get the current status of a GCE VM (e.g. RUNNING, TERMINATED, STAGING).

        Pass `zone` for fallback VMs that live in alternate zones.
        """
        instance = self._compute.get(
            project=self._project_id,
            zone=zone or self._zone,
            instance=vm_name,
        )
        return instance.status

    def start_vm(self, vm_name: str, zone: Optional[str] = None) -> None:
        """Start a VM if it is not already running or starting.

        Waits for the start operation to complete and raises a typed error if
        GCE rejects the request (e.g. ZONE_RESOURCE_POOL_EXHAUSTED). Caller
        should still poll get_vm_status / wait_for_worker_ready before
        dispatching work — operation success only means GCE accepted the start,
        not that the VM has finished booting.

        Args:
            vm_name: VM to start.
            zone: Override the manager's default zone. Required when starting
                capacity-fallback VMs in alternate zones.

        Raises:
            EncodingWorkerCapacityError: zone is out of capacity for the
                machine type. Retry from a different zone or after a wait.
            EncodingWorkerStartError: any other start failure.
        """
        target_zone = zone or self._zone
        status = self.get_vm_status(vm_name, zone=target_zone)
        if status in ("RUNNING", "STAGING"):
            logger.info("VM %s is already %s, skipping start", vm_name, status)
            return
        logger.info("Starting VM %s in zone %s (current status: %s)", vm_name, target_zone, status)
        operation = self._compute.start(
            project=self._project_id,
            zone=target_zone,
            instance=vm_name,
        )
        self._wait_for_compute_operation(operation, vm_name=vm_name, zone=target_zone)

    def stop_vm(self, vm_name: str, zone: Optional[str] = None) -> None:
        """Stop a VM if it is not already stopped or stopping."""
        target_zone = zone or self._zone
        status = self.get_vm_status(vm_name, zone=target_zone)
        if status in ("TERMINATED", "STOPPING"):
            logger.info("VM %s is already %s, skipping stop", vm_name, status)
            return
        logger.info("Stopping VM %s in zone %s (current status: %s)", vm_name, target_zone, status)
        self._compute.stop(
            project=self._project_id,
            zone=target_zone,
            instance=vm_name,
        )

    def ensure_primary_running(self) -> dict:
        """Ensure the primary encoding worker VM is running.

        Reads config, starts primary VM if stopped, and updates activity
        timestamp. Waits for the start operation to settle so that capacity
        errors surface immediately instead of being hidden behind a 120s
        readiness wait.

        Returns:
            dict with keys:
                - started (bool): True if VM was started, False if already running
                - vm_name (str): Name of the primary VM
                - primary_url (str): URL of the primary encoding worker

        Raises:
            EncodingWorkerCapacityError: zone is exhausted for the machine type.
            EncodingWorkerStartError: any other start failure.
        """
        config = self.get_config()
        vm_name = config.primary_vm

        status = self.get_vm_status(vm_name)
        started = False

        if status not in ("RUNNING", "STAGING"):
            logger.info("Primary VM %s is %s, starting it", vm_name, status)
            operation = self._compute.start(
                project=self._project_id,
                zone=self._zone,
                instance=vm_name,
            )
            self._wait_for_compute_operation(operation, vm_name=vm_name, zone=self._zone)
            started = True
        else:
            logger.info("Primary VM %s is already %s", vm_name, status)

        self.update_activity()

        return {
            "started": started,
            "vm_name": vm_name,
            "zone": self._zone,
            "primary_url": config.primary_url,
        }

    def ensure_any_running(self, candidates: list) -> dict:
        """Start the first candidate VM that GCE accepts; raise if all are exhausted.

        Iterates `candidates` in order, attempting to start each one. A
        capacity error (ZONE_RESOURCE_POOL_EXHAUSTED, etc.) on candidate N
        triggers a try of candidate N+1. Other start failures abort the
        whole call.

        On fallback success (a non-first candidate started), persists the
        fallback's URL as `active_override_*` in Firestore so subsequent
        encoding requests target the right VM. The first candidate (the
        primary) succeeding clears any stale override.

        Args:
            candidates: List of EncodingWorkerCandidate, ordered by preference.
                Typically [primary, secondary, *fallbacks_in_alt_zones].

        Returns:
            dict {started, vm_name, zone, primary_url, fell_back}
                fell_back is True iff the chosen VM is not the first candidate.

        Raises:
            EncodingWorkerCapacityError: all candidates rejected with
                capacity errors.
            EncodingWorkerStartError: any non-capacity start failure.
            ValueError: empty candidate list.
        """
        if not candidates:
            raise ValueError("ensure_any_running requires at least one candidate")

        # Track the last error so we can raise something representative if
        # every candidate fails. Capacity errors are preferred (they're more
        # actionable for the user-facing message) but generic start errors
        # also trigger fallback — observed failure modes include 503
        # SERVICE_UNAVAILABLE from the GCE backend, which behaves like
        # capacity exhaustion (transient, retry on a different VM/zone helps).
        last_capacity_error: Optional[EncodingWorkerCapacityError] = None
        last_start_error: Optional[EncodingWorkerStartError] = None
        for index, candidate in enumerate(candidates):
            try:
                status = self.get_vm_status(candidate.vm_name, zone=candidate.zone)
                started = False
                if status not in ("RUNNING", "STAGING"):
                    logger.info(
                        "Trying candidate %d/%d: VM %s in %s (status %s)",
                        index + 1, len(candidates), candidate.vm_name,
                        candidate.zone, status,
                    )
                    self.start_vm(candidate.vm_name, zone=candidate.zone)
                    started = True

                self.update_activity()
                fell_back = index > 0
                if fell_back:
                    self._set_active_override(candidate)
                else:
                    self._clear_active_override()

                return {
                    "started": started,
                    "vm_name": candidate.vm_name,
                    "zone": candidate.zone,
                    "primary_url": candidate.url,
                    "fell_back": fell_back,
                }
            except EncodingWorkerCapacityError as cap_err:
                logger.warning(
                    "Candidate %s in %s exhausted (%s), trying next candidate",
                    candidate.vm_name, candidate.zone, cap_err.code,
                )
                last_capacity_error = cap_err
                last_start_error = cap_err
                continue
            except EncodingWorkerStartError as start_err:
                # Other start failures (e.g. 503 SERVICE_UNAVAILABLE from the
                # GCE backend) are also transient — try the next candidate
                # rather than giving up. If all candidates fail with these
                # generic errors and no candidate hit a capacity error, we
                # surface the last generic error.
                logger.warning(
                    "Candidate %s in %s start failed (%s), trying next candidate",
                    candidate.vm_name, candidate.zone, start_err.code or "no-code",
                )
                last_start_error = start_err
                continue

        # Every candidate failed. Prefer raising the capacity error if any
        # candidate hit one (more actionable for the caller / user message).
        if last_capacity_error is not None:
            raise last_capacity_error
        assert last_start_error is not None
        raise last_start_error

    def _set_active_override(self, candidate) -> None:
        """Record a fallback VM as the active worker URL in Firestore."""
        now = datetime.now(UTC).isoformat()
        self._doc_ref().update({
            "active_override_vm": candidate.vm_name,
            "active_override_ip": candidate.ip,
            "active_override_zone": candidate.zone,
            "active_override_set_at": now,
        })
        logger.info(
            "Set active_override to %s in %s (capacity fallback)",
            candidate.vm_name, candidate.zone,
        )

    def _clear_active_override(self) -> None:
        """Clear active_override fields (primary is healthy again).

        Idempotent: safe to call when no override is currently set.
        """
        snapshot = self._doc_ref().get()
        if not snapshot.exists:
            return
        data = snapshot.to_dict() or {}
        if not data.get("active_override_vm"):
            return  # nothing to clear
        self._doc_ref().update({
            "active_override_vm": None,
            "active_override_ip": None,
            "active_override_zone": None,
            "active_override_set_at": None,
        })
        logger.info("Cleared active_override (primary is healthy again)")

    # ------------------------------------------------------------------
    # Compute operation helpers
    # ------------------------------------------------------------------

    def _wait_for_compute_operation(
        self,
        operation: Any,
        *,
        vm_name: str,
        zone: str,
        timeout: float = START_OPERATION_TIMEOUT_SECONDS,
    ) -> None:
        """Block on a compute v1 ExtendedOperation; raise typed exceptions on error.

        google.cloud.compute_v1 returns an ExtendedOperation. Calling .result()
        blocks until done; successful operations return None, failures raise.
        Some failure modes set .error_code/.error_message on the operation
        without raising (depending on SDK version), so we check both paths.
        """
        result_exc: Optional[BaseException] = None
        try:
            if hasattr(operation, "result"):
                operation.result(timeout=timeout)
        except Exception as e:  # noqa: BLE001 — we re-raise as a typed error below
            result_exc = e

        code = (getattr(operation, "error_code", "") or "")
        message = (getattr(operation, "error_message", "") or "")

        if not code and result_exc is None:
            return  # Success.

        if not code and result_exc is not None:
            # Operation raised but did not set error_code (e.g. timeout, transport
            # failure). Fall back to the raised exception text.
            raise EncodingWorkerStartError(
                f"VM {vm_name} start operation failed in {zone}: {result_exc}",
                vm_name=vm_name,
                zone=zone,
                code="",
            ) from result_exc

        # error_code populated — classify it.
        raise classify_gce_error(code, message, vm_name=vm_name, zone=zone) from result_exc

    # ------------------------------------------------------------------
    # Task 3: cold-start readiness gate
    # ------------------------------------------------------------------

    async def wait_for_worker_ready(
        self,
        vm_name: str,
        health_url: str,
        *,
        zone: Optional[str] = None,
        vm_timeout: float = 120.0,
        health_timeout: float = 180.0,
        poll_interval: float = 5.0,
    ) -> None:
        """Block until the VM is RUNNING and the worker /health endpoint returns 200.

        Two-phase wait, used after ensure_primary_running() reports started=True
        (i.e., the VM was actually TERMINATED and is cold-booting):

          Phase 1 — poll get_vm_status until RUNNING (max vm_timeout seconds)
          Phase 2 — poll GET health_url until 200 (max health_timeout seconds)

        Connection errors and non-200 responses during phase 2 are treated as
        "not ready, keep polling". A TimeoutError is raised if either phase
        exceeds its budget.

        Args:
            vm_name: Name of the GCE VM to poll.
            health_url: Full URL of the worker /health endpoint
                (e.g. "http://10.0.0.1:8080/health").
            zone: Override the manager's default zone. Required when waiting on
                capacity-fallback VMs that live in alternate zones — without
                this override the status poll hits the wrong zone and 404s
                ("instance not found"), the readiness wait gives up, and the
                next request hits a VM that hasn't finished booting.
            vm_timeout: Max seconds to wait for VM RUNNING.
            health_timeout: Max seconds to wait for /health 200.
            poll_interval: Seconds between polls in either phase.

        Raises:
            TimeoutError: If VM never reaches RUNNING, or /health never returns 200.
        """
        # Phase 1: VM status
        loop = asyncio.get_event_loop()
        deadline = loop.time() + vm_timeout
        last_status = None
        last_log = 0.0
        while loop.time() < deadline:
            last_status = self.get_vm_status(vm_name, zone=zone)
            if last_status == "RUNNING":
                logger.info("VM %s reached RUNNING", vm_name)
                break
            now = loop.time()
            if now - last_log >= 30.0:
                logger.info("Waiting for VM %s (status=%s)", vm_name, last_status)
                last_log = now
            await asyncio.sleep(poll_interval)
        else:
            raise TimeoutError(
                f"VM {vm_name} did not reach RUNNING within {vm_timeout}s "
                f"(last status: {last_status})"
            )

        # Phase 2: worker /health
        deadline = loop.time() + health_timeout
        last_log = 0.0
        last_detail = "no response"
        async with aiohttp.ClientSession() as session:
            while loop.time() < deadline:
                try:
                    async with session.get(health_url, timeout=5.0) as resp:
                        if resp.status == 200:
                            logger.info("Worker at %s is healthy", health_url)
                            return
                        last_detail = f"HTTP {resp.status}"
                except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                    last_detail = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                now = loop.time()
                if now - last_log >= 30.0:
                    logger.info("Waiting for worker /health at %s (last: %s)", health_url, last_detail)
                    last_log = now
                await asyncio.sleep(poll_interval)

        raise TimeoutError(
            f"Worker at {health_url} did not become healthy within {health_timeout}s "
            f"(last: {last_detail})"
        )


