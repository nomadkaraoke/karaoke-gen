#!/usr/bin/env python3
"""Write the env for running the video worker on the AWS encoding box.

Mirrors the LIVE Cloud Run job ``video-encoding-job`` (plain env + secret refs
resolved from Secret Manager), so the box runs the video worker with exactly
the configuration Cloud Run does — no hand-copied list to drift. Authenticates
as karaoke-backend@ (the job's service account) via Workload Identity
Federation, and the env it writes points the video worker at the same identity.

On any failure it removes the env file: /run-worker then answers 503 and the
backend keeps using the Cloud Run job. Run by startup-aws.sh on every boot.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT = "nomadkaraoke"
JOB = "video-encoding-job"
REGION = os.environ.get("CPU_JOBS_REGION", "us-east4")
OUT = Path(os.environ.get("RUN_WORKER_ENV_FILE", "/opt/encoding-worker/video-worker-env.json"))
BACKEND_CREDS = "/opt/encoding-worker/gcp-wif-backend.json"

# Box-specific settings on top of the Cloud Run job's env.
BOX_ENV = {
    "GOOGLE_APPLICATION_CREDENTIALS": BACKEND_CREDS,
    # Take finals from the co-located encoder instead of GCS (local_encoded_outputs).
    "LOCAL_ENCODED_OUTPUTS_DIR": "/var/lib/encoding-worker/outputs",
    # Run-to-completion process like a Cloud Run job (synchronous log writes etc.).
    "CLOUD_RUN_JOB": JOB,
}


def build_env(job: dict, secret_value) -> dict:
    """Plain env + resolved secrets from a run_v2 Job dict, plus BOX_ENV."""
    containers = job["template"]["template"]["containers"]
    env: dict = {}
    for item in containers[0].get("env", []):
        name = item["name"]
        if "value" in item:
            env[name] = item["value"]
        elif "valueSource" in item:
            ref = item["valueSource"]["secretKeyRef"]
            secret = ref["secret"].rsplit("/", 1)[-1]
            env[name] = secret_value(secret, ref.get("version") or "latest")
    env.update(BOX_ENV)
    return env


def main() -> int:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = BACKEND_CREDS
    try:
        import google.auth
        import google.auth.transport.requests
        from google.cloud import secretmanager

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        session = google.auth.transport.requests.AuthorizedSession(creds)
        resp = session.get(f"https://run.googleapis.com/v2/projects/{PROJECT}/locations/{REGION}/jobs/{JOB}", timeout=30)
        resp.raise_for_status()
        sm = secretmanager.SecretManagerServiceClient(credentials=creds)

        def secret_value(secret: str, version: str) -> str:
            name = f"projects/{PROJECT}/secrets/{secret}/versions/{version}"
            return sm.access_secret_version(name=name).payload.data.decode("utf-8")

        env = build_env(resp.json(), secret_value)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        tmp = OUT.with_suffix(".tmp")
        tmp.write_text(json.dumps(env))
        os.chmod(tmp, 0o600)
        os.replace(tmp, OUT)
        print(f"video-worker env: {len(env)} vars from {JOB} ({REGION}) -> {OUT}")
        return 0
    except Exception as e:  # noqa: BLE001
        OUT.unlink(missing_ok=True)
        print(f"video-worker env NOT written (worker runs disabled, Cloud Run fallback): {e}", file=sys.stderr)
        return 0  # never block the encoder's startup


if __name__ == "__main__":
    sys.exit(main())
