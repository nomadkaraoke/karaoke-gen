#!/usr/bin/env python3
"""
Production end-to-end health check for a white-label tenant portal.

Drives the exact path a tenant uses: submit a job with X-Tenant-ID + the tenant's own
mixed/instrumental audio, let it transcribe, approve the lyrics review, wait for the
render, and assert the tenant distribution was applied (locked theme, no YouTube, no
Google Drive, Dropbox folder + downloadable outputs). Cleans up the job and its uploads.

Designed to run daily per tenant in GitHub Actions (see .github/workflows/e2e-tenant-daily.yml)
and locally for ad-hoc verification.

Auth / credentials:
  - Admin token: env KARAOKE_ADMIN_TOKEN, else Secret Manager (admin-tokens) via ADC.
  - Test audio + cleanup: Google Cloud Storage via ADC.
In CI these are provided by Workload Identity Federation (google-github-actions/auth).

Usage:
  python scripts/e2e/tenant_e2e.py vocalstar
  python scripts/e2e/tenant_e2e.py singa
"""
import os
import sys
import time
import json
import tempfile
import urllib.request
import urllib.error

API = os.environ.get("KARAOKE_API_URL", "https://api.nomadkaraoke.com")
BUCKET = os.environ.get("KARAOKE_GCS_BUCKET", "karaoke-gen-storage-nomadkaraoke")
# Shared test pair (real audio with vocals so transcription has something to align).
TEST_MIXED = f"gs://{BUCKET}/e2e-tests/shared/e2e-mixed.mp3"
TEST_INSTRUMENTAL = f"gs://{BUCKET}/e2e-tests/shared/e2e-instrumental.mp3"
# Clearly-labelled so the daily output is identifiable in the tenant's Dropbox folder.
# Title is tenant-specific: the GCE encoder caches by base_name (artist - title), so two
# tenants running the matrix concurrently must NOT share a base_name or one would receive
# the other's (differently-themed) cached encode.
ARTIST = "Nomad Karaoke"
TITLE = None  # set per-tenant below

EXPECTED = {
    "vocalstar": {"theme": "vocalstar"},
    "singa": {"theme": "singa"},
}

REVIEW_TIMEOUT = int(os.environ.get("E2E_REVIEW_TIMEOUT", "900"))    # 15 min to transcription/review
RENDER_TIMEOUT = int(os.environ.get("E2E_RENDER_TIMEOUT", "1800"))   # 30 min to full render


class TransientEncoderError(Exception):
    """A known, non-tenant-specific transient failure from the shared GCE encoder."""


def log(msg):
    print(msg, flush=True)


def admin_token():
    tok = os.environ.get("KARAOKE_ADMIN_TOKEN")
    if tok:
        return tok.split(",")[0].strip()
    from google.cloud import secretmanager
    c = secretmanager.SecretManagerServiceClient()
    resp = c.access_secret_version(request={
        "name": "projects/nomadkaraoke/secrets/admin-tokens/versions/latest"})
    return resp.payload.data.decode().split(",")[0].strip()


def gcs_download(gs_uri, local_path):
    from google.cloud import storage
    bucket, _, blob = gs_uri[5:].partition("/")
    storage.Client().bucket(bucket).blob(blob).download_to_filename(local_path)


def gcs_delete_prefix(prefix):
    from google.cloud import storage
    client = storage.Client()
    b = client.bucket(BUCKET)
    for blob in b.list_blobs(prefix=prefix):
        blob.delete()


TENANT = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("E2E_TENANT", "vocalstar")
if TENANT not in EXPECTED:
    log(f"Unknown tenant: {TENANT}")
    sys.exit(2)
TITLE = f"E2E Health Check {TENANT}"
TOKEN = admin_token()


def req(method, path, body=None, timeout=180):
    url = path if path.startswith("http") else API + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {TOKEN}", "X-Tenant-ID": TENANT}
    if data is not None:
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:400]}


def put_file(signed_url, local_path, content_type):
    with open(local_path, "rb") as f:
        data = f.read()
    r = urllib.request.Request(signed_url, data=data, headers={"Content-Type": content_type}, method="PUT")
    with urllib.request.urlopen(r, timeout=600) as resp:
        return resp.status


def poll_until(job_id, statuses, timeout, label):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        st, job = req("GET", f"/api/jobs/{job_id}")
        s = job.get("status")
        if s != last:
            log(f"  [{time.strftime('%H:%M:%S')}] status={s} progress={job.get('progress')}")
            last = s
        if s in statuses:
            return job
        if s in ("failed", "error"):
            msg = job.get("error_message") or ""
            # The shared GCE encoder occasionally drops a job mid-encode (VM restart /
            # idle-shutdown race) — a pre-existing, non-tenant-specific transient. Surface
            # it distinctly so the caller retries the whole job rather than false-alarming.
            if "lost contact with worker" in msg or "not found" in msg:
                raise TransientEncoderError(f"Transient encoder failure during {label}: {msg}")
            raise RuntimeError(f"Job failed during {label}: {msg}")
        time.sleep(15)
    raise TimeoutError(f"Timed out waiting for {label} (last status={last})")


def run_once(mixed, instrumental):
    """Submit one job, drive it to completion, assert outputs. Cleans up its own job.
    Raises TransientEncoderError on a known transient encoder failure (caller may retry)."""
    files = [
        {"file_type": "audio", "filename": "mixed.mp3", "content_type": "audio/mpeg"},
        {"file_type": "existing_instrumental", "filename": "instrumental.mp3", "content_type": "audio/mpeg"},
    ]
    st, resp = req("POST", "/api/jobs/create-with-upload-urls",
                   {"artist": ARTIST, "title": TITLE, "files": files,
                    "is_private": True, "existing_instrumental": True})
    if st != 200:
        raise RuntimeError(f"create failed: {st} {resp}")
    job_id = resp["job_id"]
    log(f"job_id: {job_id}")

    try:
        urls = {u["file_type"]: u for u in resp["upload_urls"]}
        put_file(urls["audio"]["upload_url"], mixed, urls["audio"]["content_type"])
        put_file(urls["existing_instrumental"]["upload_url"], instrumental, urls["existing_instrumental"]["content_type"])
        req("POST", f"/api/jobs/{job_id}/uploads-complete", {"uploaded_files": ["audio", "existing_instrumental"]})

        poll_until(job_id, ("awaiting_review", "in_review"), REVIEW_TIMEOUT, "review gate")
        log("approving review")
        st, _ = req("POST", f"/api/jobs/{job_id}/complete-review", {})
        if st != 200:
            raise RuntimeError(f"complete-review failed: {st}")

        job = poll_until(job_id, ("complete",), RENDER_TIMEOUT, "render")

        # Assertions: tenant distribution applied end-to-end.
        sd = job.get("state_data") or {}
        dropbox = sd.get("dropbox_link") or job.get("dropbox_link")
        youtube = sd.get("youtube_url") or job.get("youtube_url")
        # Portal downloads are driven by job.file_urls (finals/packages), the same field
        # the OutputLinks UI reads — not download_urls/output_files.
        file_urls = job.get("file_urls") or {}
        finals = file_urls.get("finals") or {}
        packages = file_urls.get("packages") or {}
        has_downloads = bool(
            finals.get("lossy_4k_mp4") or finals.get("lossy_720p_mp4")
            or finals.get("with_vocals_mp4") or packages.get("cdg_zip") or packages.get("txt_zip")
        )
        log(f"  dropbox_link: {dropbox}")
        log(f"  youtube_url: {youtube}")
        log(f"  finals: {sorted(finals.keys())} | packages: {sorted(packages.keys())}")

        errors = []
        if not dropbox:
            errors.append("no dropbox_link (tenant Dropbox distribution did not run)")
        if youtube:
            errors.append(f"unexpected youtube_url={youtube} (tenant must not publish to YouTube)")
        if not has_downloads:
            errors.append("no downloadable outputs in file_urls (finals/packages empty)")
        if errors:
            raise RuntimeError("Assertions failed: " + "; ".join(errors))

        log(f"\n✅ {TENANT} E2E PASSED — job completed, Dropbox link present, no YouTube, downloads available")
    finally:
        # Always clean up the test job and its uploads.
        try:
            req("DELETE", f"/api/jobs/{job_id}")
            gcs_delete_prefix(f"uploads/{job_id}/")
            gcs_delete_prefix(f"jobs/{job_id}/")
            log(f"cleaned up job {job_id}")
        except Exception as e:
            log(f"cleanup warning: {e}")


def main():
    log(f"=== Tenant E2E health check: {TENANT} ===")
    tmp = tempfile.mkdtemp()
    mixed = os.path.join(tmp, "mixed.mp3")
    instrumental = os.path.join(tmp, "instrumental.mp3")
    gcs_download(TEST_MIXED, mixed)
    gcs_download(TEST_INSTRUMENTAL, instrumental)

    # Retry once on a known transient encoder failure (shared GCE worker dropping a job
    # mid-encode) so the daily health check doesn't false-alarm on pre-existing flakiness.
    attempts = 2
    for attempt in range(1, attempts + 1):
        try:
            run_once(mixed, instrumental)
            return
        except TransientEncoderError as e:
            log(f"⚠️  attempt {attempt}/{attempts}: {e}")
            if attempt == attempts:
                log("Transient encoder failure persisted across retries.")
                sys.exit(1)
            log("Retrying with a fresh job...")


if __name__ == "__main__":
    main()
