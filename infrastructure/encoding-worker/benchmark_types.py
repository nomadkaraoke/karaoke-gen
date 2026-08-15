#!/usr/bin/env python3
"""Benchmark encode wall-time per instance type to build the speed ranking.

The runtime + deploy candidate ordering is driven by
``backend/services/encoding_worker_preference.SPEED_RANK`` — a fastest-first
preference. That table is SEEDED from CPU architecture; this script replaces the
seed with MEASURED medians so the ranking reflects reality on our actual encode
workload (fast finalization is the whole reason c4d was chosen).

This is an OPERATOR tool, not part of CI: it starts real (billed) VMs, runs a
canonical encode on each, and stops them again. Run it occasionally and after
adding a new type.

Usage:
    python infrastructure/encoding-worker/benchmark_types.py \\
        --ass-gcs   gs://karaoke-gen-storage-nomadkaraoke/bench/canonical.ass \\
        --audio-gcs gs://karaoke-gen-storage-nomadkaraoke/bench/canonical.flac \\
        --out-prefix gs://karaoke-gen-storage-nomadkaraoke/bench/out \\
        [--repeats 3] [--types c4-highcpu-32,n2-highcpu-32] [--json ranking.json]

The canonical input should be a representative FULL 4K render (not the tiny CI
preview asset) so the medians reflect production finalization time. Pin one real
job's `.ass` + audio in a stable GCS location once and reuse it.

Requires: gcloud/gsutil authenticated with compute + secret access. The fallback
VM inventory (vm/zone/ip/machine_type) is read from the
``encoding-worker-fallback-vms`` secret; the c4d primary is added from Firestore.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

PROJECT = "nomadkaraoke"
PORT = 8080
DEFAULT_C4D_ZONE = "us-central1-c"


def _gcloud(*args: str, check: bool = True) -> str:
    r = subprocess.run(
        ["gcloud", *args, f"--project={PROJECT}"],
        capture_output=True, text=True,
    )
    if check and r.returncode != 0:
        raise RuntimeError(f"gcloud {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def _api_key() -> str:
    return _gcloud("secrets", "versions", "access", "latest",
                   "--secret=encoding-worker-api-key")


def _inventory(only_types: Optional[set]) -> List[Dict[str, Any]]:
    """Assemble the {vm,zone,ip,machine_type} list to benchmark.

    Primary c4d (from Firestore) + every fallback (from the secret). machine_type
    is required here — legacy entries without it are skipped with a warning.
    """
    workers: List[Dict[str, Any]] = []

    # Primary c4d from Firestore config.
    cfg_raw = _gcloud("firestore", "documents", "get",
                      "projects/nomadkaraoke/databases/(default)/documents/config/encoding-worker",
                      "--format=json", check=False)
    try:
        # Fall back to the python client if the CLI shape isn't available.
        from google.cloud import firestore  # type: ignore
        doc = firestore.Client(project=PROJECT).collection("config").document("encoding-worker").get()
        data = doc.to_dict() or {}
        if data.get("primary_vm"):
            workers.append({
                "vm": data["primary_vm"], "zone": DEFAULT_C4D_ZONE,
                "ip": data.get("primary_ip"), "machine_type": "c4d-highcpu-32",
            })
    except Exception as e:  # noqa: BLE001
        print(f"WARN: could not read primary from Firestore ({e}); benchmarking fallbacks only")

    secret = _gcloud("secrets", "versions", "access", "latest",
                     "--secret=encoding-worker-fallback-vms", check=False)
    try:
        for f in json.loads(secret or "[]"):
            mt = f.get("machine_type")
            if not mt:
                print(f"WARN: fallback {f.get('vm')} has no machine_type; skipping")
                continue
            workers.append({"vm": f["vm"], "zone": f["zone"], "ip": f.get("ip"), "machine_type": mt})
    except Exception as e:  # noqa: BLE001
        print(f"WARN: could not parse fallback secret ({e})")

    # De-dup by machine_type (one representative VM per type) and optional filter.
    seen, result = set(), []
    for w in workers:
        if only_types and w["machine_type"] not in only_types:
            continue
        if w["machine_type"] in seen:
            continue
        seen.add(w["machine_type"])
        result.append(w)
    return result


def _vm_status(vm: str, zone: str) -> str:
    return _gcloud("compute", "instances", "describe", vm, f"--zone={zone}",
                   "--format=value(status)", check=False)


def _start(vm: str, zone: str) -> None:
    _gcloud("compute", "instances", "start", vm, f"--zone={zone}", check=False)


def _stop(vm: str, zone: str) -> None:
    _gcloud("compute", "instances", "stop", vm, f"--zone={zone}", check=False)


def _http(method: str, url: str, api_key: str, body: Optional[dict] = None,
          timeout: float = 30.0) -> Dict[str, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-API-Key", api_key)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode() or "{}")


def _wait_health(ip: str, api_key: str, timeout: float = 240.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            _http("GET", f"http://{ip}:{PORT}/health", api_key, timeout=5.0)
            return True
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(5)
    return False


def _one_encode(ip: str, api_key: str, job_id: str, ass: str, audio: str,
                out: str, poll_timeout: float = 1800.0) -> float:
    """Submit one full /encode and return client-side wall-time (seconds)."""
    start = time.monotonic()
    _http("POST", f"http://{ip}:{PORT}/encode", api_key, body={
        "job_id": job_id,
        "ass_gcs_path": ass,
        "audio_gcs_path": audio,
        "output_gcs_path": out,
    })
    deadline = time.monotonic() + poll_timeout
    while time.monotonic() < deadline:
        time.sleep(5)
        try:
            st = _http("GET", f"http://{ip}:{PORT}/status/{job_id}", api_key, timeout=10.0)
        except (urllib.error.URLError, OSError, ValueError):
            continue
        status = st.get("status")
        if status == "complete":
            return time.monotonic() - start
        if status == "failed":
            raise RuntimeError(f"encode {job_id} failed: {st.get('error')}")
    raise TimeoutError(f"encode {job_id} did not complete within {poll_timeout}s")


def benchmark(args) -> Dict[str, float]:
    api_key = _api_key()
    only = set(args.types.split(",")) if args.types else None
    workers = _inventory(only)
    if not workers:
        print("No workers to benchmark (check --types / secret).")
        return {}

    print(f"Benchmarking {len(workers)} types × {args.repeats} repeats each:")
    for w in workers:
        print(f"  - {w['machine_type']:>16}  {w['vm']} ({w['zone']})")

    medians: Dict[str, float] = {}
    for w in workers:
        vm, zone, ip, mt = w["vm"], w["zone"], w.get("ip"), w["machine_type"]
        print(f"\n=== {mt} — {vm} ({zone}) ===")
        try:
            if _vm_status(vm, zone) != "RUNNING":
                print("  starting VM...")
                _start(vm, zone)
            if not ip:
                ip = _gcloud("compute", "instances", "describe", vm, f"--zone={zone}",
                             "--format=value(networkInterfaces[0].accessConfigs[0].natIP)")
            if not _wait_health(ip, api_key):
                print(f"  SKIP: {vm} never became healthy")
                continue
            samples: List[float] = []
            for i in range(args.repeats):
                job_id = f"bench-{mt}-{i}"
                out = f"{args.out_prefix}/{mt}-{i}.mp4"
                secs = _one_encode(ip, api_key, job_id, args.ass_gcs, args.audio_gcs, out)
                print(f"  run {i + 1}/{args.repeats}: {secs:.1f}s")
                samples.append(secs)
            medians[mt] = round(statistics.median(samples), 1)
        except Exception as e:  # noqa: BLE001 — keep benchmarking the rest
            print(f"  ERROR benchmarking {mt}: {e}")
        finally:
            if not args.no_stop:
                print("  stopping VM...")
                _stop(vm, zone)

    return medians


def _suggest_ranks(medians: Dict[str, float]) -> Dict[str, int]:
    """Map medians → SPEED_RANK ints (fastest = smallest), spaced by 10."""
    ordered = sorted(medians.items(), key=lambda kv: kv[1])
    return {mt: (idx + 1) * 10 for idx, (mt, _) in enumerate(ordered)}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ass-gcs", dest="ass_gcs", required=True)
    p.add_argument("--audio-gcs", dest="audio_gcs", required=True)
    p.add_argument("--out-prefix", dest="out_prefix", required=True)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--types", default=None, help="comma-separated machine types to limit to")
    p.add_argument("--json", dest="json_out", default=None, help="write ranking JSON here")
    p.add_argument("--no-stop", action="store_true", help="leave VMs running after benchmarking")
    args = p.parse_args()

    medians = benchmark(args)
    if not medians:
        return 1

    ranks = _suggest_ranks(medians)
    print("\n=== RESULTS (median encode seconds, fastest first) ===")
    for mt, secs in sorted(medians.items(), key=lambda kv: kv[1]):
        print(f"  {mt:>16}: {secs:>7.1f}s   → suggested SPEED_RANK {ranks[mt]}")

    payload = {"medians_seconds": medians, "suggested_speed_rank": ranks}
    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"\nWrote {args.json_out}")
    print("\nCopy suggested_speed_rank into "
          "backend/services/encoding_worker_preference.py::SPEED_RANK "
          "(keep c4d lowest/first unless measurements say otherwise).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
