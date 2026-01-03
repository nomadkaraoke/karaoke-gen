#!/usr/bin/env python3
"""
Fetch cloud job logs from Firestore and save to a local file.

This script extracts worker_logs and timeline entries from a cloud job
and formats them into a log file compatible with analyze_log_timing.py.

Usage:
    python scripts/fetch_job_logs.py <job_id> [output_file]
    python scripts/fetch_job_logs.py 7ccfcd64
    python scripts/fetch_job_logs.py 7ccfcd64 ./output/job-7ccfcd64.log

Requires:
    - google-cloud-firestore
    - GOOGLE_APPLICATION_CREDENTIALS or gcloud auth application-default login
"""
import sys
import json
from datetime import datetime
from pathlib import Path

from google.cloud import firestore


def get_job(job_id: str) -> dict | None:
    """Fetch job document from Firestore."""
    db = firestore.Client(project='nomadkaraoke')
    doc = db.collection('jobs').document(job_id).get()

    if doc.exists:
        return doc.to_dict()
    return None


def format_worker_log(log: dict) -> str:
    """Format a worker_log entry as a log line."""
    timestamp = log.get('timestamp', '')
    # Handle both string and datetime timestamps
    if hasattr(timestamp, 'isoformat'):
        timestamp = timestamp.isoformat()

    worker = log.get('worker', 'unknown')
    level = log.get('level', 'INFO')
    message = log.get('message', '')

    # Format: TIMESTAMP [WORKER] LEVEL: MESSAGE
    return f"{timestamp} [{worker}] {level}: {message}"


def format_timeline_entry(entry: dict) -> str:
    """Format a timeline entry as a log line."""
    timestamp = entry.get('timestamp', '')
    # Handle both string and datetime timestamps
    if hasattr(timestamp, 'isoformat'):
        timestamp = timestamp.isoformat()

    status = entry.get('status', 'unknown')
    message = entry.get('message', '')
    progress = entry.get('progress', '')

    # Format: TIMESTAMP [timeline] STATUS: MESSAGE (progress%)
    progress_str = f" ({progress}%)" if progress else ""
    return f"{timestamp} [timeline] {status}: {message}{progress_str}"


def fetch_job_logs(job_id: str, output_path: str | None = None) -> str:
    """
    Fetch job logs from Firestore and save to a file.

    Args:
        job_id: The job ID to fetch logs for
        output_path: Optional path to save logs. If None, uses ./output/job-{job_id}.log

    Returns:
        Path to the saved log file
    """
    data = get_job(job_id)

    if data is None:
        print(f"Error: Job {job_id} not found", file=sys.stderr)
        sys.exit(1)

    # Collect all log entries with timestamps
    log_entries = []

    # Extract worker_logs
    worker_logs = data.get('worker_logs', [])
    for log in worker_logs:
        timestamp = log.get('timestamp', '')
        if hasattr(timestamp, 'isoformat'):
            timestamp = timestamp.isoformat()
        log_entries.append({
            'timestamp': timestamp,
            'line': format_worker_log(log)
        })

    # Extract timeline entries
    timeline = data.get('timeline', [])
    for entry in timeline:
        timestamp = entry.get('timestamp', '')
        if hasattr(timestamp, 'isoformat'):
            timestamp = timestamp.isoformat()
        log_entries.append({
            'timestamp': timestamp,
            'line': format_timeline_entry(entry)
        })

    # Sort all entries by timestamp
    log_entries.sort(key=lambda x: x['timestamp'])

    # Generate header with job metadata
    header_lines = [
        f"# Cloud Job Logs for {job_id}",
        f"# Fetched at: {datetime.now().isoformat()}",
        f"# Status: {data.get('status', 'unknown')}",
        f"# Artist: {data.get('artist', 'unknown')}",
        f"# Title: {data.get('title', 'unknown')}",
        f"# Created: {data.get('created_at', 'unknown')}",
        f"# Worker logs: {len(worker_logs)}",
        f"# Timeline entries: {len(timeline)}",
        "#" + "=" * 79,
        ""
    ]

    # Combine header and log lines
    all_lines = header_lines + [entry['line'] for entry in log_entries]
    content = '\n'.join(all_lines)

    # Determine output path
    if output_path is None:
        output_dir = Path('./output')
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"job-{job_id}.log"
    else:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write to file
    output_path.write_text(content)

    return str(output_path)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nExamples:")
        print("  python scripts/fetch_job_logs.py 7ccfcd64")
        print("  python scripts/fetch_job_logs.py 7ccfcd64 ./logs/my-job.log")
        print("\nThe output file can be analyzed with:")
        print("  python scripts/analyze_log_timing.py ./output/job-7ccfcd64.log")
        sys.exit(1)

    job_id = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"Fetching logs for job {job_id}...", file=sys.stderr)

    saved_path = fetch_job_logs(job_id, output_path)

    print(f"Logs saved to: {saved_path}", file=sys.stderr)
    print(f"\nTo analyze timing, run:", file=sys.stderr)
    print(f"  python scripts/analyze_log_timing.py {saved_path}", file=sys.stderr)

    # Print the path to stdout for scripting
    print(saved_path)


if __name__ == "__main__":
    main()
