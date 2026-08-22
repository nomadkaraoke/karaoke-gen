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
        --input-gcs       gs://karaoke-gen-storage-nomadkaraoke/bench/inputs/ \\
        --encoding-config @bench/encoding_config.json \\
        --out-prefix      gs://karaoke-gen-storage-nomadkaraoke/bench/out \\
        [--repeats 3] [--types c4-highcpu-32,n2-highcpu-32] [--json ranking.json] \\
        [--include-running] [--no-stop]

The payload matches the real /encode contract (input_gcs_path + output_gcs_path +
encoding_config), i.e. the production FULL finalization path — not the lighter
/encode-preview. Pin one real job's inputs dir + its encoding_config in a stable
GCS location once and reuse it so the medians reflect production 4K finalization
time. `--encoding-config` takes inline JSON or `@path/to/file.json`.

SAFETY: VMs already RUNNING (likely serving production) are skipped by default and
never stopped; only VMs this run starts are benchmarked and stopped afterwards.

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


def _one_encode(ip: str, api_key: str, job_id: str, input_gcs: str,
                encoding_config: dict, out: str, poll_timeout: float = 1800.0) -> float:
    """Submit one full /encode and return client-side wall-time (seconds).

    Payload matches the real /encode contract (EncodeRequest:
    input_gcs_path + output_gcs_path + encoding_config) — the production
    finalization path we want to benchmark, NOT the lighter /encode-preview.
    """
    start = time.monotonic()
    _http("POST", f"http://{ip}:{PORT}/encode", api_key, body={
        "job_id": job_id,
        "input_gcs_path": input_gcs,
        "output_gcs_path": out,
        "encoding_config": encoding_config,
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
        initial_status = _vm_status(vm, zone)
        # SAFETY: never touch a VM that is already RUNNING — it may be the serving
        # primary/override, and injecting bench jobs (or stopping it) would disrupt
        # production and wipe its in-memory job registry. Only benchmark VMs we
        # start ourselves, and only stop those. `--include-running` overrides the
        # skip but STILL never stops a VM this run didn't start.
        started_by_us = False
        if initial_status == "RUNNING" and not args.include_running:
            print(f"  SKIP: {vm} is already RUNNING (likely serving); pass --include-running to bench it")
            continue
        try:
            if initial_status != "RUNNING":
                print("  starting VM...")
                _start(vm, zone)
                started_by_us = True
            if not ip:
                ip = _gcloud("compute", "instances", "describe", vm, f"--zone={zone}",
                             "--format=value(networkInterfaces[0].accessConfigs[0].natIP)")
            if not _wait_health(ip, api_key):
                print(f"  SKIP: {vm} never became healthy")
                continue
            samples: List[float] = []
            for i in range(args.repeats):
                job_id = f"bench-{mt}-{i}"
                out = f"{args.out_prefix}/{mt}-{i}/"
                secs = _one_encode(ip, api_key, job_id, args.input_gcs,
                                   args.encoding_config, out)
                print(f"  run {i + 1}/{args.repeats}: {secs:.1f}s")
                samples.append(secs)
            medians[mt] = round(statistics.median(samples), 1)
        except Exception as e:  # noqa: BLE001 — keep benchmarking the rest
            print(f"  ERROR benchmarking {mt}: {e}")
        finally:
            # Only stop a VM this invocation actually started (never a serving one).
            if started_by_us and not args.no_stop:
                print("  stopping VM...")
                _stop(vm, zone)
            elif not started_by_us:
                print("  leaving VM as found (was already running before this benchmark)")

    return medians


def _suggest_ranks(medians: Dict[str, float]) -> Dict[str, int]:
    """Map medians → SPEED_RANK ints (fastest = smallest), spaced by 10."""
    ordered = sorted(medians.items(), key=lambda kv: kv[1])
    return {mt: (idx + 1) * 10 for idx, (mt, _) in enumerate(ordered)}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input-gcs", dest="input_gcs", required=True,
                   help="gs:// path to the canonical /encode inputs dir")
    p.add_argument("--encoding-config", dest="encoding_config_raw", required=True,
                   help="encoding_config as inline JSON or @path/to/file.json")
    p.add_argument("--out-prefix", dest="out_prefix", required=True)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--types", default=None, help="comma-separated machine types to limit to")
    p.add_argument("--json", dest="json_out", default=None, help="write ranking JSON here")
    p.add_argument("--no-stop", action="store_true", help="leave VMs running after benchmarking")
    p.add_argument("--include-running", action="store_true",
                   help="also benchmark VMs already RUNNING (never stops them)")
    args = p.parse_args()

    # Resolve --encoding-config (inline JSON or @file) into a dict once.
    raw = args.encoding_config_raw
    try:
        if raw.startswith("@"):
            with open(raw[1:]) as f:
                args.encoding_config = json.load(f)
        else:
            args.encoding_config = json.loads(raw)
    except (OSError, ValueError) as e:
        print(f"ERROR: --encoding-config is not valid JSON / readable file: {e}")
        return 2
    if not isinstance(args.encoding_config, dict):
        print("ERROR: --encoding-config must be a JSON object")
        return 2

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
