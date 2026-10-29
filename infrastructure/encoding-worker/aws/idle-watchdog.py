#!/usr/bin/env python3
"""Idle self-stop for the AWS encoding worker (systemd timer, every 2 min).

The GCE workers are stopped by the encoding-worker-idle Cloud Function; the AWS
worker isn't a GCE VM, so it stops itself with the same rules. This runs as its
own systemd unit (not inside uvicorn), so a wedged worker process still gets
stopped:

- busy (``/health`` active_jobs > 0)                → keep running
- idle and not the routed worker                    → stop
- idle, routed, last_activity_at older than 5 min   → stop
- health or Firestore unreadable for 15 min         → stop (never run forever)

"Stop" = ``shutdown -h now``; the instance's shutdown behaviour is ``stop``, so
the EBS root (venv, caches) survives and the backend restarts it on demand.
CI uploads this to gs://karaoke-gen-storage-nomadkaraoke/encoding-worker/idle-watchdog.py.
"""

from __future__ import annotations

import json
import subprocess
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

IDLE_TIMEOUT = timedelta(minutes=5)
UNKNOWN_GRACE = timedelta(minutes=15)
STATE_FILE = Path("/var/lib/encoding-worker-idle/unknown-since")
VM_NAME_FILE = Path("/opt/encoding-worker/vm-name")


def decide(
    *,
    now: datetime,
    active_jobs: Optional[int],
    config: Optional[dict],
    vm_name: str,
    unknown_since: Optional[datetime],
) -> tuple[str, str]:
    """Return ("stop" | "keep" | "unknown", reason). Pure, for tests.

    ``active_jobs``/``config`` are None when /health or Firestore couldn't be
    read. "unknown" asks the caller to start (or keep) the grace clock.
    """
    if active_jobs is not None and active_jobs > 0:
        return "keep", f"{active_jobs} active job(s)"
    if active_jobs is None or config is None:
        what = "health" if active_jobs is None else "Firestore config"
        if unknown_since and now - unknown_since >= UNKNOWN_GRACE:
            return "stop", f"{what} unreadable since {unknown_since.isoformat()}"
        return "unknown", f"{what} unreadable"
    if config.get("active_override_vm") != vm_name:
        return "stop", f"idle and not the routed worker (routed: {config.get('active_override_vm')})"
    last = config.get("last_activity_at")
    try:
        last_dt = datetime.fromisoformat(last) if last else None
    except (TypeError, ValueError):
        last_dt = None
    if last_dt is not None and last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    if last_dt is None or now - last_dt >= IDLE_TIMEOUT:
        return "stop", f"idle since {last or 'never'}"
    return "keep", f"routed, last activity {last}"


def _active_jobs() -> Optional[int]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=10) as r:
            return int(json.load(r).get("active_jobs", 0))
    except Exception:  # noqa: BLE001
        return None


def _config() -> Optional[dict]:
    try:
        from google.cloud import firestore

        snap = firestore.Client(project="nomadkaraoke").collection("config").document("encoding-worker").get()
        return snap.to_dict() if snap.exists else None
    except Exception:  # noqa: BLE001
        return None


def main() -> None:
    now = datetime.now(timezone.utc)
    vm_name = VM_NAME_FILE.read_text().strip() if VM_NAME_FILE.exists() else ""
    try:
        unknown_since = datetime.fromisoformat(STATE_FILE.read_text().strip())
    except (OSError, ValueError):
        unknown_since = None

    action, reason = decide(
        now=now, active_jobs=_active_jobs(), config=_config(), vm_name=vm_name, unknown_since=unknown_since
    )
    print(f"idle-watchdog: {action}: {reason}")
    if action == "unknown":
        if unknown_since is None:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(now.isoformat())
        return
    STATE_FILE.unlink(missing_ok=True)
    if action == "stop":
        subprocess.run(["shutdown", "-h", "now"], check=False)


if __name__ == "__main__":
    main()
