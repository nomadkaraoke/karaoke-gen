#!/usr/bin/env python
"""Audit completed jobs for sung stretches with no transcribed lyrics (missing lyrics).

Calibration tool for the vocal-gaps detector (backend/services/auto_approval/vocal_gaps.py).
The analysis runs SERVER-SIDE via ``POST /api/internal/jobs/{id}/vocal-gaps`` — Cloud
Run downloads each job's lead-vocal stem — so nothing bulky passes through this machine.

    export KARAOKE_ADMIN_TOKEN=$(gcloud secrets versions access latest --secret=admin-tokens \\
        --project=nomadkaraoke | cut -d',' -f1)
    python scripts/audit_vocal_gaps.py --days 30 --limit 300            # analyze + store
    python scripts/audit_vocal_gaps.py --report-only --days 30          # summarize stored results
    python scripts/audit_vocal_gaps.py --job 5710831e --dry-run         # one job, don't store

Output: distribution of each job's longest unlyricked vocal run, and the suspect gaps
(with/without reference lines in the gap) to hand-check before choosing a gate threshold.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

API = os.environ.get("KARAOKE_API_URL", "https://api.nomadkaraoke.com")


def _jobs(days: int, limit: int):
    from google.cloud import firestore

    db = firestore.Client(project="nomadkaraoke")
    since = datetime.now(timezone.utc) - timedelta(days=days)
    # Range + order on created_at only (no composite index needed); status filtered here
    query = (db.collection("jobs").where(filter=firestore.FieldFilter("created_at", ">=", since))
             .order_by("created_at", direction=firestore.Query.DESCENDING))
    jobs = []
    for d in query.stream():
        data = d.to_dict()
        if data.get("status") == "complete":
            jobs.append((d.id, data))
            if len(jobs) >= limit:
                break
    return jobs


def _analyze(job_id: str, token: str, dry_run: bool) -> dict:
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            r = requests.post(f"{API}/api/internal/jobs/{job_id}/vocal-gaps",
                              params={"dry_run": str(dry_run).lower()},
                              headers={"X-Admin-Token": token}, timeout=180)
            if r.status_code == 200:
                return r.json()
            err = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code < 500:  # auth/not-found won't fix itself
                break
        except requests.RequestException as e:
            err = str(e)
        if attempt < attempts:
            time.sleep(5 * attempt)
    return {"job_id": job_id, "status": "request_failed", "error": err}


def _report(rows):
    checked = [r for r in rows if r.get("vocal_gaps")]
    print(f"\n{len(rows)} jobs, {len(checked)} analyzed")
    statuses = {}
    for r in rows:
        statuses[r.get("status")] = statuses.get(r.get("status"), 0) + 1
    print("statuses:", statuses)

    runs = sorted(max((g["longest_run_s"] for g in r["vocal_gaps"]["gaps"]), default=0.0) for r in checked)
    if runs:
        print("longest unlyricked vocal run per job (s): "
              + ", ".join(f"p{p}={runs[min(len(runs) - 1, int(len(runs) * p / 100))]:.1f}" for p in (50, 75, 90, 95, 99))
              + f", max={runs[-1]:.1f}")
        for threshold in (2, 3, 4, 6, 8, 10):
            print(f"  jobs with a run >= {threshold:>2}s: {sum(r >= threshold for r in runs)}")

    print("\nSuspect gaps (hand-check these):")
    for r in sorted(checked, key=lambda r: -r["vocal_gaps"]["max_suspect_run_s"]):
        for g in r["vocal_gaps"]["gaps"]:
            if not g["suspect"]:
                continue
            refs = g.get("reference_lines") or {}
            ref_note = f"{sum(len(v) for v in refs.values())} ref line(s) {list(refs)}" if refs else "no ref lines"
            title = f"{r.get('artist', '?')} - {r.get('title', '?')}"
            print(f"  {r['job_id']}  {g['start']:7.2f}-{g['end']:7.2f}s  run {g['longest_run_s']:5.2f}s  "
                  f"active {g['active_fraction']:.0%}  {ref_note}  | {title}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--job", action="append", help="specific job id(s)")
    ap.add_argument("--dry-run", action="store_true", help="analyze without storing on the job")
    ap.add_argument("--report-only", action="store_true", help="summarize already-stored results")
    ap.add_argument("--workers", type=int, default=4, help="concurrent requests (Cloud Run does the work)")
    ap.add_argument("--json", help="write raw results to this file")
    args = ap.parse_args()

    if args.job:
        from google.cloud import firestore
        db = firestore.Client(project="nomadkaraoke")
        jobs = [(j, (db.collection("jobs").document(j).get().to_dict() or {})) for j in args.job]
    else:
        jobs = _jobs(args.days, args.limit)
    meta = {j: {"artist": d.get("artist"), "title": d.get("title")} for j, d in jobs}

    if args.report_only:
        rows = [{"job_id": j, "status": "stored" if (d.get("state_data") or {}).get("vocal_gaps") else "missing",
                 "vocal_gaps": (d.get("state_data") or {}).get("vocal_gaps"), **meta[j]} for j, d in jobs]
    else:
        token = os.environ.get("KARAOKE_ADMIN_TOKEN")
        if not token:
            print("KARAOKE_ADMIN_TOKEN not set (see module docstring)", file=sys.stderr)
            return 2
        print(f"Analyzing {len(jobs)} jobs via {API} ({args.workers} at a time)...")
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            results = list(pool.map(lambda jd: _analyze(jd[0], token, args.dry_run), jobs))
        rows = [{**r, **meta.get(r.get("job_id"), {})} for r in results]

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1, default=str)
    _report(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
