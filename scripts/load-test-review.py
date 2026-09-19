#!/usr/bin/env python3
"""Concurrent review-page load test (the "10 tabs" regression guard).

Reproduces the 2026-09-18 incident scenario: N simultaneous lyrics-review tab
loads, each firing the page's real GET trio (correction-data, waveform-data,
audio/vocals with a Range header). Before v0.230.0 this melted the backend
(9/30 requests 500'd, Cloud Run "no available instance"); after, all requests
succeed with warm tabs completing in a few seconds.

Auth: per-job review tokens read from Firestore via ADC (read-only SA works) —
no admin secret needed. Requests go through curl (browser-ish UA) because the
Cloudflare WAF blocks python-urllib.

Usage:
    python scripts/load-test-review.py                # 10 tabs vs prod
    python scripts/load-test-review.py --tabs 20
    python scripts/load-test-review.py --max-tab-seconds 45   # cold-cache run

Exit code 0 = pass (no failed requests, all tabs under --max-tab-seconds);
1 = regression. Prints a per-request table either way.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import time

API_DEFAULT = "https://api.nomadkaraoke.com"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128 Safari/537.36"
)


def fetch_review_jobs(limit: int) -> list[dict]:
    """In-review jobs + review tokens from Firestore (ADC)."""
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "nomadkaraoke")
    from google.cloud import firestore  # type: ignore[import]

    db = firestore.Client(project="nomadkaraoke")
    query = db.collection("jobs").where(
        filter=firestore.FieldFilter("status", "in", ["in_review", "awaiting_review"])
    ).limit(limit * 2)
    jobs = []
    for doc in query.stream():
        data = doc.to_dict()
        token = data.get("review_token") or (data.get("state_data") or {}).get("review_token")
        if token:
            jobs.append({"job_id": doc.id, "token": token})
        if len(jobs) >= limit:
            break
    return jobs


def curl(url: str, extra_args: list[str] | None = None, timeout: int = 90) -> dict:
    """One GET via curl; returns {status, seconds, bytes}."""
    cmd = [
        "curl", "-sS", "-o", "/dev/null", "-A", UA, "--max-time", str(timeout),
        "-w", "%{http_code} %{time_total} %{size_download}",
        *(extra_args or []),
        url,
    ]
    started = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 and not proc.stdout.strip():
        return {"status": 0, "seconds": time.monotonic() - started, "bytes": 0}
    status, seconds, size = proc.stdout.strip().split()[:3]
    return {"status": int(status), "seconds": float(seconds), "bytes": int(size)}


def tab_requests(api: str, job: dict) -> list[tuple[str, str, list[str]]]:
    base = f"{api}/api/review/{job['job_id']}"
    tok = f"review_token={job['token']}"
    return [
        ("correction-data", f"{base}/correction-data?{tok}", []),
        ("waveform-data", f"{base}/waveform-data?{tok}", []),
        ("vocals", f"{base}/audio/vocals?{tok}", ["-r", "0-131071"]),
    ]


def simulate_tab(api: str, job: dict) -> dict:
    started = time.monotonic()
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(curl, url, extra): name
            for name, url, extra in tab_requests(api, job)
        }
        for fut in concurrent.futures.as_completed(futures):
            results[futures[fut]] = fut.result()
    return {"job_id": job["job_id"], "seconds": time.monotonic() - started, "requests": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tabs", type=int, default=10)
    parser.add_argument("--api", default=API_DEFAULT)
    parser.add_argument(
        "--max-tab-seconds", type=float, default=20.0,
        help="Fail if any simulated tab takes longer than this (default 20; "
        "use ~45 for a cold-cache run where waveforms compute for the first time)",
    )
    parser.add_argument(
        "--jobs-file", default=None,
        help="JSON file of [{job_id, token}] to skip the Firestore lookup",
    )
    args = parser.parse_args()
    if args.tabs < 1:
        parser.error("--tabs must be >= 1")

    if args.jobs_file:
        jobs = json.load(open(args.jobs_file))[: args.tabs]
    else:
        print(f"Fetching up to {args.tabs} in-review jobs from Firestore…")
        jobs = fetch_review_jobs(args.tabs)
    if len(jobs) < args.tabs:
        # A partial run would "PASS" without reproducing the requested burst.
        print(
            f"Only {len(jobs)} in-review jobs available but --tabs {args.tabs} requested. "
            "Aborting — re-run with a smaller --tabs to test at reduced scale."
        )
        return 1
    print(f"Simulating {len(jobs)} concurrent review tabs against {args.api}\n")

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        tabs = list(pool.map(lambda j: simulate_tab(args.api, j), jobs))

    failed_requests = 0
    slow_tabs = 0
    for tab in sorted(tabs, key=lambda t: t["seconds"]):
        parts = []
        for name in ("correction-data", "waveform-data", "vocals"):
            r = tab["requests"][name]
            ok = 200 <= r["status"] < 300
            failed_requests += 0 if ok else 1
            parts.append(f"{name}={r['status']}({r['seconds']:.1f}s)")
        slow = tab["seconds"] > args.max_tab_seconds
        slow_tabs += 1 if slow else 0
        flag = " ← SLOW" if slow else ""
        print(f"  {tab['job_id']}  tab={tab['seconds']:5.1f}s  {'  '.join(parts)}{flag}")

    total = len(jobs) * 3
    print(f"\n{total - failed_requests}/{total} requests OK; "
          f"{slow_tabs} tab(s) over {args.max_tab_seconds}s")
    if failed_requests or slow_tabs:
        print("RESULT: FAIL — concurrent review-load regression "
              "(see docs/archive/2026-09-18-concurrent-review-reliability-investigation-and-plan.md)")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
