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
from typing import Optional

import aiohttp

from google.cloud import firestore

logger = logging.getLogger(__name__)

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

    @property
    def primary_url(self) -> str:
        return f"http://{self.primary_ip}:{ENCODING_WORKER_PORT}"

    @property
    def secondary_url(self) -> str:
        return f"http://{self.secondary_ip}:{ENCODING_WORKER_PORT}"


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

    def get_vm_status(self, vm_name: str) -> str:
        """Get the current status of a GCE VM (e.g. RUNNING, TERMINATED, STAGING)."""
        instance = self._compute.get(
            project=self._project_id,
            zone=self._zone,
            instance=vm_name,
        )
        return instance.status

    def start_vm(self, vm_name: str) -> None:
        """Start a VM if it is not already running or starting.

        Fire-and-forget: returns immediately. Caller should poll get_vm_status
        if they need to wait for the VM to be fully RUNNING.
        """
        status = self.get_vm_status(vm_name)
        if status in ("RUNNING", "STAGING"):
            logger.info("VM %s is already %s, skipping start", vm_name, status)
            return
        logger.info("Starting VM %s (current status: %s)", vm_name, status)
        self._compute.start(
            project=self._project_id,
            zone=self._zone,
            instance=vm_name,
        )

    def stop_vm(self, vm_name: str) -> None:
        """Stop a VM if it is not already stopped or stopping."""
        status = self.get_vm_status(vm_name)
        if status in ("TERMINATED", "STOPPING"):
            logger.info("VM %s is already %s, skipping stop", vm_name, status)
            return
        logger.info("Stopping VM %s (current status: %s)", vm_name, status)
        self._compute.stop(
            project=self._project_id,
            zone=self._zone,
            instance=vm_name,
        )

    def ensure_primary_running(self) -> dict:
        """Ensure the primary encoding worker VM is running.

        Reads config, starts primary VM if stopped, and updates activity timestamp.

        Returns:
            dict with keys:
                - started (bool): True if VM was started, False if already running
                - vm_name (str): Name of the primary VM
                - primary_url (str): URL of the primary encoding worker
        """
        config = self.get_config()
        vm_name = config.primary_vm

        status = self.get_vm_status(vm_name)
        started = False

        if status not in ("RUNNING", "STAGING"):
            logger.info("Primary VM %s is %s, starting it", vm_name, status)
            self._compute.start(
                project=self._project_id,
                zone=self._zone,
                instance=vm_name,
            )
            started = True
        else:
            logger.info("Primary VM %s is already %s", vm_name, status)

        self.update_activity()

        return {
            "started": started,
            "vm_name": vm_name,
            "primary_url": config.primary_url,
        }

    # ------------------------------------------------------------------
    # Task 3: cold-start readiness gate
    # ------------------------------------------------------------------

    async def wait_for_worker_ready(
        self,
        vm_name: str,
        health_url: str,
        *,
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
            last_status = self.get_vm_status(vm_name)
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


