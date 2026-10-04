#!/usr/bin/env python3
"""
Review what the storage-retention process deleted (docs/STORAGE-RETENTION.md).

  python scripts/storage_retention_report.py                # list all runs (summaries)
  python scripts/storage_retention_report.py RUN_ID          # one run: totals by category + per job
  python scripts/storage_retention_report.py --find jobs/abc/finals/x.mp4   # which run deleted it
  python scripts/storage_retention_report.py --find abc123   # everything deleted for a job id

RUN_ID is the log name without extension, e.g. 20261004T103000Z-job_purge.
Uses Application Default Credentials (read-only access is enough).
"""
import argparse
import json
import sys
from collections import Counter, defaultdict

from google.cloud import storage

BUCKET = "karaoke-gen-storage-nomadkaraoke"
PREFIX = "storage-retention/deletion-logs/"


def _bucket():
    return storage.Client(project="nomadkaraoke").bucket(BUCKET)


def _gib(n):
    return f"{n / 2**30:.2f} GiB"


def list_runs(bucket):
    for blob in sorted(bucket.list_blobs(prefix=PREFIX), key=lambda b: b.name):
        if not blob.name.endswith(".summary.json"):
            continue
        s = json.loads(blob.download_as_text())
        print(f"{s['run_id']:<40} jobs={s['jobs']:<5} objects={s['objects_deleted']:<6} "
              f"{_gib(s['bytes_deleted']):>11} failures={s['failures']}")


def _lines(bucket, run_id):
    blob = bucket.blob(f"{PREFIX}{run_id}.jsonl")
    for line in blob.download_as_text().splitlines():
        if line.strip():
            yield json.loads(line)


def show_run(bucket, run_id):
    by_cat, n_cat, by_job = Counter(), Counter(), defaultdict(Counter)
    failures = []
    for e in _lines(bucket, run_id):
        if e["result"] == "deleted":
            by_cat[e["category"]] += e["size_bytes"]
            n_cat[e["category"]] += 1
            by_job[e["job_id"]][e["category"]] += e["size_bytes"]
        else:
            failures.append(e)
    print(f"Run {run_id}: {len(by_job)} jobs, {sum(n_cat.values())} objects, {_gib(sum(by_cat.values()))}")
    for cat, size in by_cat.most_common():
        print(f"  {cat:<18} {n_cat[cat]:>6} objects {_gib(size):>11}")
    print("Per job:")
    for job_id, cats in sorted(by_job.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"  {job_id:<16} {_gib(sum(cats.values())):>11}  " + ", ".join(f"{c}={_gib(v)}" for c, v in cats.items()))
    if failures:
        print(f"Failures ({len(failures)}):")
        for e in failures:
            print(f"  {e['path']}: {e.get('error')}")


def find(bucket, needle):
    for blob in sorted(bucket.list_blobs(prefix=PREFIX), key=lambda b: b.name):
        if not blob.name.endswith(".jsonl"):
            continue
        run_id = blob.name[len(PREFIX):-len(".jsonl")]
        for e in _lines(bucket, run_id):
            if needle == e["job_id"] or needle in e["path"]:
                # Only offer a restore for objects actually deleted at a known
                # generation (a failed delete may have a newer live version).
                restore = ""
                if e["result"] == "deleted" and e.get("generation"):
                    restore = (f"  restore: gcloud storage cp 'gs://{BUCKET}/{e['path']}#{e['generation']}' "
                               f"'gs://{BUCKET}/{e['path']}'")
                print(f"{run_id}  {e['result']:<7} {e['path']}  gen={e.get('generation')}  {e['size_bytes']}B{restore}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_id", nargs="?")
    parser.add_argument("--find")
    args = parser.parse_args()
    bucket = _bucket()
    if args.find:
        find(bucket, args.find)
    elif args.run_id:
        show_run(bucket, args.run_id)
    else:
        list_runs(bucket)


if __name__ == "__main__":
    sys.exit(main())
