#!/usr/bin/env python3
"""
Measure the real token usage and cost of the AI lyrics auto-correct feature
against production jobs.

For each job it loads the stored corrections (the same transcription +
reference lyrics the review UI sends to ``POST /{job_id}/auto-correct``),
rebuilds the exact prompts, calls each configured model once, and reports
input/output/thinking tokens and estimated USD per model — plus a per-track
and aggregate summary. This is the immediate ground-truth read that the
production usage logging (``auto-correct usage`` / ``auto-correct cost`` lines)
accumulates over time.

Usage:
    # Auto-pick the N most recent complete jobs that have corrections
    python scripts/measure_autocorrect_cost.py --limit 3

    # Measure specific jobs
    python scripts/measure_autocorrect_cost.py --jobs abc123 def456

    # Single-model instead of the multi-model compare config
    python scripts/measure_autocorrect_cost.py --limit 3 --models claude-fable-5

Requires:
    - ANTHROPIC_API_KEY in the environment (for claude-* models)
    - GCP creds for Vertex/Firestore/GCS:
      gcloud auth application-default login  (or GOOGLE_APPLICATION_CREDENTIALS)
    - GOOGLE_CLOUD_PROJECT=nomadkaraoke (set automatically below if unset)

NOTE: every run makes real, billed model calls. Gemini USD figures use the
ESTIMATED rates in backend/services/auto_correct/pricing.py — token counts are
exact; verify the Gemini rate before trusting its dollar value.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Optional

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "nomadkaraoke")

# Running "python scripts/foo.py" puts scripts/ on sys.path, not the repo root,
# so the backend.* imports below would fail. Add the repo root explicitly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.cloud import firestore, storage  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger("measure_autocorrect_cost")
logger.setLevel(logging.INFO)

PROJECT = "nomadkaraoke"
GCS_BUCKET = "karaoke-gen-storage-nomadkaraoke"
DEFAULT_MODELS = ["claude-fable-5", "gemini-3.1-pro-preview"]


# ---------------------------------------------------------------------------
# Job + corrections loading
# ---------------------------------------------------------------------------

def _corrections_path(job_data: dict, job_id: str) -> Optional[str]:
    """Mirror review.py: prefer the latest review edits, then the original."""
    lyrics = (job_data.get("file_urls") or {}).get("lyrics") or {}
    return (
        lyrics.get("corrections_updated")
        or lyrics.get("corrections")
        or f"jobs/{job_id}/lyrics/corrections.json"
    )


def fetch_recent_jobs(db: firestore.Client, limit: int) -> list[dict]:
    query = (
        db.collection("jobs")
        .where("status", "==", "complete")
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(limit * 4)
    )
    jobs = []
    for doc in query.stream():
        data = doc.to_dict()
        data["_job_id"] = doc.id
        lyrics = (data.get("file_urls") or {}).get("lyrics") or {}
        if lyrics.get("corrections") or lyrics.get("corrections_updated"):
            jobs.append(data)
        if len(jobs) >= limit:
            break
    return jobs


def fetch_job(db: firestore.Client, job_id: str) -> Optional[dict]:
    doc = db.collection("jobs").document(job_id).get()
    if not doc.exists:
        logger.warning("job %s not found in Firestore", job_id)
        return None
    data = doc.to_dict()
    data["_job_id"] = job_id
    return data


def download_corrections(
    gcs: storage.Client, path: str
) -> Optional[dict]:
    if path.startswith("gs://"):
        bucket_name, _, blob_path = path[5:].partition("/")
    else:
        bucket_name, blob_path = GCS_BUCKET, path
    try:
        blob = gcs.bucket(bucket_name).blob(blob_path)
        if not blob.exists():
            return None
        return json.loads(blob.download_as_bytes())
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to read %s: %s", path, exc)
        return None


def build_inputs(corrections: dict) -> Optional[tuple[list[dict], dict]]:
    """Return (segments, reference_lyrics) in the shape suggest() expects."""
    segments = corrections.get("corrected_segments") or corrections.get("segments") or []
    reference_lyrics = corrections.get("reference_lyrics") or {}
    if not segments:
        logger.warning("no segments in corrections")
        return None
    if not reference_lyrics:
        logger.warning("no reference_lyrics in corrections — auto-correct needs >=1 source")
        return None
    # The service requires segment + word ids; older jobs may lack them.
    for seg in segments:
        if not seg.get("id") or any(not w.get("id") for w in (seg.get("words") or [])):
            logger.warning("segments missing word/segment ids — skipping job")
            return None
    return segments, reference_lyrics


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

def measure_job(service, job: dict, gcs, models: list[str]) -> Optional[dict]:
    from backend.services.auto_correct.pricing import estimate_cost_usd
    from backend.services.auto_correct.prompts import (
        build_system_prompt,
        build_user_prompt,
    )
    from backend.services.auto_correct.settings import AutoCorrectSettings

    job_id = job["_job_id"]
    artist = job.get("artist")
    title = job.get("title")
    path = _corrections_path(job, job_id)
    corrections = download_corrections(gcs, path)
    if not corrections:
        logger.warning("[%s] no corrections at %s — skipping", job_id, path)
        return None
    built = build_inputs(corrections)
    if not built:
        return None
    segments, reference_lyrics = built

    settings = AutoCorrectSettings()
    system_prompt = build_system_prompt(settings)
    user_prompt = build_user_prompt(
        segments=segments,
        reference_lyrics=reference_lyrics,
        artist=artist,
        title=title,
    )

    word_count = sum(len(seg.get("words") or []) for seg in segments)
    logger.info(
        "[%s] %s - %s | %d segments, %d words, %d ref sources",
        job_id, artist or "?", title or "?", len(segments), word_count,
        len(reference_lyrics),
    )

    per_model = []
    for model in models:
        t0 = time.time()
        try:
            raw, usage = service._call_model(
                model, system_prompt, user_prompt, job_id=job_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] model %s failed: %s", job_id, model, exc)
            per_model.append({"model": model, "error": str(exc)})
            continue
        elapsed = time.time() - t0
        cost = estimate_cost_usd(model, usage)
        n_sugg = len(raw.get("suggestions") or []) if isinstance(raw, dict) else 0
        per_model.append({
            "model": model,
            "usage": usage,
            "cost": cost,
            "suggestions": n_sugg,
            "elapsed": elapsed,
        })
        if usage:
            logger.info(
                "  %-26s in=%-6d out=%-6d think=%-6s cost=%s  (%.1fs, %d suggestions)",
                model,
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
                "na" if usage.get("thinking_tokens") is None else usage["thinking_tokens"],
                "?" if cost is None else f"${cost:.4f}",
                elapsed, n_sugg,
            )
        else:
            logger.info("  %-26s usage unavailable", model)

    return {
        "job_id": job_id,
        "artist": artist,
        "title": title,
        "word_count": word_count,
        "per_model": per_model,
    }


def print_report(results: list[dict], models: list[str]) -> None:
    print("\n" + "=" * 78)
    print("AUTO-CORRECT COST MEASUREMENT")
    print("=" * 78)
    track_totals = []
    model_cost_sums = {m: 0.0 for m in models}
    model_cost_known = {m: False for m in models}

    for r in results:
        track_total = 0.0
        any_cost = False
        print(f"\n{r['artist'] or '?'} - {r['title'] or '?'}  "
              f"({r['job_id']}, {r['word_count']} words)")
        for pm in r["per_model"]:
            if "error" in pm:
                print(f"  {pm['model']:<26s} ERROR: {pm['error']}")
                continue
            u = pm["usage"] or {}
            cost = pm["cost"]
            if cost is not None:
                track_total += cost
                any_cost = True
                model_cost_sums[pm["model"]] += cost
                model_cost_known[pm["model"]] = True
            print(
                f"  {pm['model']:<26s} "
                f"in={u.get('input_tokens', 0):<6d} "
                f"out={u.get('output_tokens', 0):<6d} "
                f"think={('na' if u.get('thinking_tokens') is None else u['thinking_tokens'])!s:<6s} "
                f"cost={'?' if cost is None else f'${cost:.4f}':<9s} "
                f"({pm['suggestions']} suggestions)"
            )
        print(f"  {'TRACK TOTAL':<26s} {'':<26s}"
              f"{'$%.4f' % track_total if any_cost else 'unknown'}")
        if any_cost:
            track_totals.append(track_total)

    print("\n" + "-" * 78)
    print("AGGREGATE")
    print("-" * 78)
    for m in models:
        if model_cost_known[m]:
            avg = model_cost_sums[m] / len(results) if results else 0.0
            print(f"  {m:<26s} avg ${avg:.4f}/track")
    if track_totals:
        avg = sum(track_totals) / len(track_totals)
        hi = max(track_totals)
        print(f"\n  Avg total cost/track:  ${avg:.4f}")
        print(f"  Max total cost/track:  ${hi:.4f}")
        print(f"  Jobs measured:         {len(track_totals)}")
        print(f"\n  Projected at 30 jobs/day:  ${avg * 30:.2f}/day  (~${avg * 30 * 30:.0f}/mo)")
        print(f"  Projected at 100 jobs/day: ${avg * 100:.2f}/day  (~${avg * 100 * 30:.0f}/mo)")
    print("\nNOTE: Gemini USD uses ESTIMATED rates (see pricing.py). Token counts are exact.")
    print("=" * 78)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", nargs="+", help="Specific job IDs to measure")
    parser.add_argument("--limit", type=int, default=3,
                        help="Auto-pick N recent complete jobs (ignored if --jobs given)")
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS,
                        help=f"Models to measure (default: {' '.join(DEFAULT_MODELS)})")
    args = parser.parse_args()

    if any(m.startswith("claude") for m in args.models) and not os.environ.get("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY is not set — required for claude-* models")
        return 2

    from backend.services.auto_correct.service import AutoCorrectService

    db = firestore.Client(project=PROJECT)
    gcs = storage.Client(project=PROJECT)
    service = AutoCorrectService()

    if args.jobs:
        jobs = [j for j in (fetch_job(db, jid) for jid in args.jobs) if j]
    else:
        jobs = fetch_recent_jobs(db, args.limit)
        logger.info("auto-picked %d recent complete jobs with corrections", len(jobs))

    if not jobs:
        logger.error("no jobs to measure")
        return 1

    results = []
    for job in jobs:
        r = measure_job(service, job, gcs, args.models)
        if r:
            results.append(r)

    if not results:
        logger.error("no jobs produced measurable results")
        return 1

    print_report(results, args.models)
    return 0


if __name__ == "__main__":
    sys.exit(main())
