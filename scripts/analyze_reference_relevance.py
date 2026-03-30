#!/usr/bin/env python3
"""
Empirical threshold analysis: measure per-source reference relevance scores
from production jobs that have corrections.json files.

Relevance score = (reference words appearing in anchor sequences) / (total reference words)

This helps determine appropriate thresholds for filtering low-quality reference
sources before running anchor sequence detection.

Usage:
    python scripts/analyze_reference_relevance.py
    python scripts/analyze_reference_relevance.py --limit 50
    python scripts/analyze_reference_relevance.py --output results.json

Requires:
    - google-cloud-firestore
    - google-cloud-storage
    - GOOGLE_APPLICATION_CREDENTIALS or gcloud auth application-default login
"""
import argparse
import io
import json
import logging
import sys
from collections import defaultdict
from typing import Optional

from google.cloud import firestore, storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT = "nomadkaraoke"
GCS_BUCKET = "karaoke-gen-storage-nomadkaraoke"


# ---------------------------------------------------------------------------
# Firestore helpers
# ---------------------------------------------------------------------------

def fetch_candidate_jobs(db: firestore.Client, limit: int) -> list[dict]:
    """
    Pull jobs that have a corrections file URL from Firestore.

    We over-fetch (up to 3x the limit) so we can bias toward jobs where at
    least one source has a low match score (mixed results).  We filter down
    after computing scores.
    """
    logger.info("Querying Firestore for completed jobs with correction data...")

    # Query jobs that are complete and have lyrics corrections URL stored
    query = (
        db.collection("jobs")
        .where("status", "==", "complete")
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(limit * 3)
    )

    jobs = []
    for doc in query.stream():
        data = doc.to_dict()
        data["_job_id"] = doc.id

        # Require a corrections URL
        corrections_url = _get_corrections_url(data)
        if corrections_url:
            data["_corrections_url"] = corrections_url
            jobs.append(data)

    logger.info(f"Found {len(jobs)} jobs with corrections URL")
    return jobs


def _get_corrections_url(job_data: dict) -> Optional[str]:
    """Extract the GCS path for the corrections.json from a Firestore job doc."""
    file_urls = job_data.get("file_urls", {})
    lyrics = file_urls.get("lyrics", {})
    corrections = lyrics.get("corrections")
    if corrections:
        return corrections
    return None


# ---------------------------------------------------------------------------
# GCS helpers
# ---------------------------------------------------------------------------

def download_corrections(gcs_client: storage.Client, gcs_path: str) -> Optional[dict]:
    """
    Download and parse a corrections.json from GCS.

    gcs_path may be:
      - A full gs:// URL:  gs://karaoke-gen-outputs/jobs/.../lyrics/corrections.json
      - A bare object path: jobs/.../lyrics/corrections.json
    """
    if gcs_path.startswith("gs://"):
        # Strip gs://<bucket>/
        parts = gcs_path[5:].split("/", 1)
        bucket_name = parts[0]
        blob_path = parts[1] if len(parts) > 1 else ""
    else:
        bucket_name = GCS_BUCKET
        blob_path = gcs_path

    try:
        bucket = gcs_client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        content = blob.download_as_bytes()
        return json.loads(content)
    except Exception as e:
        logger.warning(f"Failed to download {gcs_path}: {e}")
        return None


# ---------------------------------------------------------------------------
# Relevance computation
# ---------------------------------------------------------------------------

def compute_relevance_scores(corrections: dict) -> dict[str, dict]:
    """
    Compute per-source relevance scores from a corrections.json dict.

    Relevance = (# reference words that appear in at least one anchor) /
                (total words in that source's reference lyrics)

    Returns a dict keyed by source name, each value being:
        {
            "total_words": int,
            "anchored_words": int,
            "score": float,
        }
    """
    reference_lyrics: dict = corrections.get("reference_lyrics", {})
    anchor_sequences: list = corrections.get("anchor_sequences", [])

    # Build set of anchored word IDs per source
    anchored_ids: dict[str, set] = defaultdict(set)
    for anchor in anchor_sequences:
        ref_word_ids: dict = anchor.get("reference_word_ids", {})
        for source, word_ids in ref_word_ids.items():
            anchored_ids[source].update(word_ids)

    scores = {}
    for source, ref_data in reference_lyrics.items():
        if not ref_data:
            continue

        # Count total words in this source
        total_words = 0
        for segment in ref_data.get("segments", []):
            total_words += len(segment.get("words", []))

        if total_words == 0:
            scores[source] = {"total_words": 0, "anchored_words": 0, "score": 0.0}
            continue

        anchored_count = len(anchored_ids.get(source, set()))
        score = anchored_count / total_words

        scores[source] = {
            "total_words": total_words,
            "anchored_words": anchored_count,
            "score": round(score, 4),
        }

    return scores


def has_mixed_results(scores: dict[str, dict], low_threshold: float = 0.5) -> bool:
    """Return True if at least one source has a score below low_threshold."""
    if len(scores) < 2:
        return False
    score_values = [v["score"] for v in scores.values()]
    return min(score_values) < low_threshold


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def build_report(job_results: list[dict]) -> str:
    """Build a human-readable text report sorted by min source score."""
    lines = []
    lines.append("=" * 72)
    lines.append("REFERENCE LYRICS RELEVANCE ANALYSIS")
    lines.append(f"Jobs analyzed: {len(job_results)}")
    lines.append("=" * 72)
    lines.append("")

    # Sort: mixed-result jobs first (most interesting), then by min score asc
    def sort_key(r):
        scores = [v["score"] for v in r["scores"].values()] if r["scores"] else [1.0]
        return (0 if r["is_mixed"] else 1, min(scores))

    sorted_results = sorted(job_results, key=sort_key)

    for idx, result in enumerate(sorted_results, 1):
        job_id = result["job_id"]
        artist = result.get("artist", "Unknown")
        title = result.get("title", "Unknown")
        mixed_flag = " [MIXED]" if result["is_mixed"] else ""

        lines.append(f"{idx:3d}. {artist} - {title}  ({job_id}){mixed_flag}")

        scores = result["scores"]
        if not scores:
            lines.append("     (no reference sources found)")
        else:
            for source, info in sorted(scores.items(), key=lambda x: x[1]["score"]):
                bar_len = int(info["score"] * 20)
                bar = "#" * bar_len + "-" * (20 - bar_len)
                lines.append(
                    f"     {source:<12s}  [{bar}]  {info['score']:.1%}"
                    f"  ({info['anchored_words']}/{info['total_words']} words)"
                )
        lines.append("")

    # --- Aggregate statistics ---
    lines.append("=" * 72)
    lines.append("AGGREGATE STATISTICS")
    lines.append("=" * 72)

    all_source_scores: dict[str, list[float]] = defaultdict(list)
    for result in job_results:
        for source, info in result["scores"].items():
            all_source_scores[source].append(info["score"])

    for source in sorted(all_source_scores):
        vals = all_source_scores[source]
        avg = sum(vals) / len(vals) if vals else 0
        below_50 = sum(1 for v in vals if v < 0.5)
        below_30 = sum(1 for v in vals if v < 0.3)
        below_10 = sum(1 for v in vals if v < 0.1)
        lines.append(
            f"  {source:<12s}  n={len(vals):3d}  avg={avg:.1%}"
            f"  <50%: {below_50:3d}  <30%: {below_30:3d}  <10%: {below_10:3d}"
        )

    mixed_count = sum(1 for r in job_results if r["is_mixed"])
    lines.append("")
    lines.append(f"  Jobs with mixed results (at least 1 source <50%): {mixed_count}/{len(job_results)}")

    # Score distribution across ALL sources
    all_scores = [
        info["score"]
        for result in job_results
        for info in result["scores"].values()
    ]
    if all_scores:
        thresholds = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
        lines.append("")
        lines.append("  Score distribution (all sources):")
        for t in thresholds:
            count = sum(1 for s in all_scores if s < t)
            pct = count / len(all_scores) * 100
            lines.append(f"    < {t:.0%}: {count:4d} / {len(all_scores)}  ({pct:.1f}%)")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Analyze reference lyrics relevance scores from production jobs"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=30,
        help="Target number of jobs to analyze (default: 30)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Write raw JSON results to this file (optional)",
    )
    parser.add_argument(
        "--no-mixed-bias",
        action="store_true",
        help="Disable bias toward jobs with mixed results",
    )
    args = parser.parse_args()

    db = firestore.Client(project=PROJECT)
    gcs_client = storage.Client(project=PROJECT)

    # Step 1: Fetch candidate jobs from Firestore
    candidate_jobs = fetch_candidate_jobs(db, limit=args.limit)
    if not candidate_jobs:
        logger.error("No candidate jobs found. Check Firestore access.")
        sys.exit(1)

    # Step 2: Download corrections and compute scores
    job_results = []
    skipped = 0

    for job in candidate_jobs:
        job_id = job["_job_id"]
        corrections_url = job["_corrections_url"]
        artist = job.get("artist", job.get("state_data", {}).get("artist", "Unknown"))
        title = job.get("title", job.get("state_data", {}).get("title", "Unknown"))

        logger.info(f"Processing {job_id}: {artist} - {title}")

        corrections = download_corrections(gcs_client, corrections_url)
        if not corrections:
            skipped += 1
            continue

        scores = compute_relevance_scores(corrections)
        if not scores:
            skipped += 1
            continue

        is_mixed = has_mixed_results(scores)

        job_results.append({
            "job_id": job_id,
            "artist": artist,
            "title": title,
            "corrections_url": corrections_url,
            "scores": scores,
            "is_mixed": is_mixed,
        })

    if skipped:
        logger.info(f"Skipped {skipped} jobs (download failure or no reference sources)")

    if not job_results:
        logger.error("No results to report.")
        sys.exit(1)

    # Step 3: Optionally bias toward mixed-result jobs
    if not args.no_mixed_bias:
        mixed = [r for r in job_results if r["is_mixed"]]
        non_mixed = [r for r in job_results if not r["is_mixed"]]
        # Aim for at least half mixed, rest filled with non-mixed
        target_mixed = min(len(mixed), max(args.limit // 2, len(mixed)))
        target_non_mixed = min(len(non_mixed), args.limit - target_mixed)
        job_results = mixed[:target_mixed] + non_mixed[:target_non_mixed]
        logger.info(
            f"After mixed-bias selection: {len(job_results)} jobs "
            f"({target_mixed} mixed, {target_non_mixed} non-mixed)"
        )
    else:
        job_results = job_results[: args.limit]

    # Step 4: Print report
    report = build_report(job_results)
    print(report)

    # Step 5: Optionally write JSON
    if args.output:
        with open(args.output, "w") as f:
            json.dump(job_results, f, indent=2)
        logger.info(f"Raw results written to {args.output}")


if __name__ == "__main__":
    main()
