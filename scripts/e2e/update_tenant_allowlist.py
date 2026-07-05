#!/usr/bin/env python3
"""Idempotently add the E2E email domain to a tenant's allowed_email_domains in
the live GCS config (tenants/{id}/config.json).

The setup-{tenant}-tenant.py scripts are the source of truth, but the live GCS
config must also be updated for the change to take effect (tenant config is
loaded from GCS, not re-derived from the setup scripts).

Usage: python scripts/e2e/update_tenant_allowlist.py vocalstar singa

Requires a write-capable credential (Application Default Credentials of an
account with storage.objects.create/update on the bucket). If ADC is pinned to
a read-only service account via GOOGLE_APPLICATION_CREDENTIALS, run with that
override cleared, e.g.:

    env -u GOOGLE_APPLICATION_CREDENTIALS python scripts/e2e/update_tenant_allowlist.py vocalstar singa
"""
import json
import sys

from google.cloud import storage

BUCKET = "karaoke-gen-storage-nomadkaraoke"
E2E_DOMAIN = "inbox.testmail.app"


def update(tenant_id: str) -> None:
    client = storage.Client(project="nomadkaraoke")
    blob = client.bucket(BUCKET).blob(f"tenants/{tenant_id}/config.json")
    cfg = json.loads(blob.download_as_text())
    auth = cfg.setdefault("auth", {})
    domains = auth.setdefault("allowed_email_domains", [])
    if E2E_DOMAIN in domains:
        print(f"{tenant_id}: already allowlisted ({domains})")
        return
    domains.append(E2E_DOMAIN)
    blob.upload_from_string(json.dumps(cfg, indent=2), content_type="application/json")
    print(f"{tenant_id}: added {E2E_DOMAIN} -> {domains}")


if __name__ == "__main__":
    for t in sys.argv[1:] or ["vocalstar", "singa"]:
        update(t)
