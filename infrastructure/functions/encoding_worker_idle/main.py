"""
Encoding Worker Idle Shutdown Cloud Function.

Triggered by Cloud Scheduler every 5 minutes. Checks both encoding worker
VMs (a/b) and stops any that are idle.

Idle criteria (all must be true to stop a VM):
- No active encoding jobs (from /health endpoint active_jobs)
- No recent user activity (from Firestore last_activity_at > 15 min)
  - Only applies to primary VM; secondary always stops if no active jobs
- No deploy in progress (from Firestore deploy_in_progress flag)

Environment variables:
- GCP_PROJECT: GCP project ID (default: nomadkaraoke)
- GCP_ZONE: Zone where VMs are located (default: us-central1-c)
- IDLE_TIMEOUT_MINUTES: Minutes of inactivity before shutdown (default: 15)
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta

import functions_framework
import requests
from google.cloud import compute_v1, firestore

logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("GCP_PROJECT", "nomadkaraoke")
ZONE = os.environ.get("GCP_ZONE", "us-central1-c")
IDLE_TIMEOUT_MINUTES = int(os.environ.get("IDLE_TIMEOUT_MINUTES", "15"))
DEPLOY_STALE_TIMEOUT_MINUTES = 20
ENCODING_WORKER_PORT = 8080

_compute_client = None
_firestore_client = None


def get_compute_client():
    global _compute_client
    if _compute_client is None:
        _compute_client = compute_v1.InstancesClient()
    return _compute_client


def get_firestore_client():
    global _firestore_client
    if _firestore_client is None:
        _firestore_client = firestore.Client(project=PROJECT_ID)
    return _firestore_client


def get_vm_status(vm_name):
    client = get_compute_client()
    try:
        instance = client.get(project=PROJECT_ID, zone=ZONE, instance=vm_name)
        return instance.status
    except Exception as e:
        logger.error(f"Failed to get status for {vm_name}: {e}")
        return "UNKNOWN"


def get_vm_ip(vm_name):
    client = get_compute_client()
    try:
        instance = client.get(project=PROJECT_ID, zone=ZONE, instance=vm_name)
        for iface in instance.network_interfaces:
            for access in iface.access_configs:
                if access.nat_i_p:
                    return access.nat_i_p
    except Exception:
        pass
    return None


def check_active_jobs(vm_ip):
    try:
        resp = requests.get(f"http://{vm_ip}:{ENCODING_WORKER_PORT}/health", timeout=5)
        if resp.status_code == 200:
            return resp.json().get("active_jobs", 0)
    except Exception:
        pass
    return 0


def stop_vm(vm_name):
    client = get_compute_client()
    logger.info(f"Stopping idle VM: {vm_name}")
    client.stop(project=PROJECT_ID, zone=ZONE, instance=vm_name)


@functions_framework.http
def idle_shutdown(request):
    now = datetime.now(timezone.utc)
    idle_cutoff = now - timedelta(minutes=IDLE_TIMEOUT_MINUTES)
    stale_cutoff = now - timedelta(minutes=DEPLOY_STALE_TIMEOUT_MINUTES)

    db = get_firestore_client()
    doc = db.collection("config").document("encoding-worker").get()
    if not doc.exists:
        logger.warning("Encoding worker config not found in Firestore")
        return json.dumps({"status": "no_config"}), 200

    config = doc.to_dict()

    # Check deploy_in_progress
    deploy_in_progress = config.get("deploy_in_progress", False)
    if deploy_in_progress:
        deploy_since = config.get("deploy_in_progress_since")
        if deploy_since:
            deploy_time = datetime.fromisoformat(deploy_since)
            if deploy_time < stale_cutoff:
                logger.warning(
                    f"Clearing stale deploy_in_progress flag (started {deploy_since})"
                )
                db.collection("config").document("encoding-worker").update({
                    "deploy_in_progress": False,
                    "deploy_in_progress_since": None,
                })
                deploy_in_progress = False
            else:
                logger.info("Deploy in progress, skipping idle check")
                return json.dumps({"status": "deploy_in_progress"}), 200

    # Parse last_activity_at
    last_activity = config.get("last_activity_at")
    has_recent_activity = False
    if last_activity:
        activity_time = datetime.fromisoformat(last_activity)
        has_recent_activity = activity_time > idle_cutoff

    # Check each VM individually
    primary_vm = config.get("primary_vm")
    results = {}
    for vm_name in [primary_vm, config.get("secondary_vm")]:
        if not vm_name:
            continue

        status = get_vm_status(vm_name)
        if status != "RUNNING":
            results[vm_name] = f"already_{status.lower()}"
            continue

        vm_ip = get_vm_ip(vm_name)
        if vm_ip:
            active_jobs = check_active_jobs(vm_ip)
            if active_jobs > 0:
                results[vm_name] = f"active_jobs={active_jobs}"
                logger.info(f"{vm_name} has {active_jobs} active jobs, keeping alive")
                continue

        # Recent activity keeps PRIMARY alive only (not secondary)
        if vm_name == primary_vm and has_recent_activity:
            results[vm_name] = "active_session"
            logger.info(f"{vm_name} (primary) kept alive due to recent activity")
            continue

        stop_vm(vm_name)
        results[vm_name] = "stopped"

    return json.dumps({"status": "checked", "results": results}), 200
