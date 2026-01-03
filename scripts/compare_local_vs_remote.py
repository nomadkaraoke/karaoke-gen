#!/usr/bin/env python3
"""
Performance comparison script: Local vs Remote karaoke generation.

Runs the same audio file through both local and remote pipelines in
non-interactive mode, capturing detailed timing logs for analysis.

Usage:
    python scripts/compare_local_vs_remote.py <audio_file> [--local-only] [--remote-only]

    # Run both tests
    python scripts/compare_local_vs_remote.py input/minutemen/Search.flac

    # Run only local test
    python scripts/compare_local_vs_remote.py input/minutemen/Search.flac --local-only

    # Run only remote test (useful if local already done)
    python scripts/compare_local_vs_remote.py input/minutemen/Search.flac --remote-only

Output:
    - output/perf-test-{timestamp}/local/  - Local CLI output + logs
    - output/perf-test-{timestamp}/remote/ - Remote CLI output + logs
    - output/perf-test-{timestamp}/comparison.txt - Side-by-side timing analysis
"""

import argparse
import os
import subprocess
import sys
import time
import json
import re
from datetime import datetime
from pathlib import Path


def run_command(cmd: list[str], log_file: Path, cwd: Path) -> tuple[int, float]:
    """
    Run a command and capture output to a log file.

    Returns:
        Tuple of (return_code, elapsed_seconds)
    """
    print(f"\n{'='*60}")
    print(f"Running: {' '.join(cmd)}")
    print(f"Logging to: {log_file}")
    print(f"{'='*60}\n")

    start_time = time.time()

    with open(log_file, 'w') as f:
        # Write command header
        f.write(f"# Command: {' '.join(cmd)}\n")
        f.write(f"# Started: {datetime.now().isoformat()}\n")
        f.write(f"# CWD: {cwd}\n")
        f.write("#" + "=" * 79 + "\n\n")
        f.flush()

        # Run with real-time output to both console and file
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            text=True,
            bufsize=1,
        )

        for line in process.stdout:
            # Add timestamp to each line for log analysis
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            timestamped_line = f"{timestamp} - {line}"

            # Write to file
            f.write(timestamped_line if not line.startswith("20") else line)
            f.flush()

            # Echo to console (without extra timestamp)
            print(line, end='')

        process.wait()

    elapsed = time.time() - start_time

    # Append timing summary to log
    with open(log_file, 'a') as f:
        f.write(f"\n# Completed: {datetime.now().isoformat()}\n")
        f.write(f"# Return code: {process.returncode}\n")
        f.write(f"# Total elapsed: {elapsed:.2f} seconds ({elapsed/60:.1f} minutes)\n")

    return process.returncode, elapsed


def extract_job_id_from_log(log_file: Path) -> str | None:
    """Extract the job ID from remote CLI log output."""
    content = log_file.read_text()

    # Look for job ID in various formats
    patterns = [
        r'Job ID: ([a-f0-9-]+)',
        r'job_id["\']?\s*[:=]\s*["\']?([a-f0-9-]+)',
        r'Created job: ([a-f0-9-]+)',
        r'/jobs/([a-f0-9-]+)',
    ]

    for pattern in patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            return match.group(1)

    return None


def run_local_test(audio_file: Path, output_dir: Path, artist: str, title: str) -> tuple[int, float, Path]:
    """
    Run local karaoke-gen CLI test.

    Returns:
        Tuple of (return_code, elapsed_seconds, log_file_path)
    """
    print("\n" + "=" * 70)
    print("RUNNING LOCAL TEST (karaoke-gen)")
    print("=" * 70)

    log_file = output_dir / "local_cli.log"

    cmd = [
        "poetry", "run", "karaoke-gen",
        str(audio_file.absolute()),
        artist,
        title,
        "--output_dir", str(output_dir.absolute()),
        "--log_level", "DEBUG",
        "--skip_transcription_review",  # Non-interactive: skip lyrics review
        "--skip_instrumental_review",    # Non-interactive: skip instrumental selection
        "-y",  # Auto-confirm prompts
    ]

    # Get project root for cwd
    project_root = Path(__file__).parent.parent

    return_code, elapsed = run_command(cmd, log_file, project_root)

    return return_code, elapsed, log_file


def run_remote_test(audio_file: Path, output_dir: Path, artist: str, title: str) -> tuple[int, float, Path, str | None]:
    """
    Run remote karaoke-gen-remote CLI test.

    Returns:
        Tuple of (return_code, elapsed_seconds, log_file_path, job_id)
    """
    print("\n" + "=" * 70)
    print("RUNNING REMOTE TEST (karaoke-gen-remote)")
    print("=" * 70)

    log_file = output_dir / "remote_cli.log"

    cmd = [
        "poetry", "run", "karaoke-gen-remote",
        str(audio_file.absolute()),
        artist,
        title,
        "--output_dir", str(output_dir.absolute()),
        "--log_level", "DEBUG",
        "--skip_transcription_review",  # Non-interactive: skip lyrics review
        "--skip_instrumental_review",    # Non-interactive: skip instrumental selection
        "-y",  # Auto-confirm prompts
    ]

    # Get project root for cwd
    project_root = Path(__file__).parent.parent

    return_code, elapsed = run_command(cmd, log_file, project_root)

    # Try to extract job ID for fetching cloud logs
    job_id = extract_job_id_from_log(log_file)

    return return_code, elapsed, log_file, job_id


def analyze_logs(local_log: Path | None, remote_log: Path | None, output_dir: Path) -> None:
    """Run timing analysis on the logs and generate comparison."""
    project_root = Path(__file__).parent.parent
    analyzer = project_root / "scripts" / "analyze_log_timing.py"

    results = {}

    if local_log and local_log.exists():
        print("\n" + "=" * 70)
        print("ANALYZING LOCAL LOG")
        print("=" * 70)

        # Run analyzer with JSON output
        result = subprocess.run(
            ["python", str(analyzer), str(local_log), "--json"],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            try:
                results['local'] = json.loads(result.stdout)
            except json.JSONDecodeError:
                print(f"Warning: Could not parse local log analysis")

        # Also print human-readable output
        subprocess.run(
            ["python", str(analyzer), str(local_log)],
            cwd=project_root,
        )

    if remote_log and remote_log.exists():
        print("\n" + "=" * 70)
        print("ANALYZING REMOTE LOG")
        print("=" * 70)

        # Run analyzer with JSON output
        result = subprocess.run(
            ["python", str(analyzer), str(remote_log), "--json"],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            try:
                results['remote'] = json.loads(result.stdout)
            except json.JSONDecodeError:
                print(f"Warning: Could not parse remote log analysis")

        # Also print human-readable output
        subprocess.run(
            ["python", str(analyzer), str(remote_log)],
            cwd=project_root,
        )

    # Generate comparison summary
    if results:
        comparison_file = output_dir / "comparison.json"
        comparison_file.write_text(json.dumps(results, indent=2))
        print(f"\nComparison data saved to: {comparison_file}")

        # Print comparison summary
        if 'local' in results and 'remote' in results:
            print("\n" + "=" * 70)
            print("PERFORMANCE COMPARISON SUMMARY")
            print("=" * 70)

            local_total = results['local'].get('total_wall_clock_seconds', 0)
            remote_total = results['remote'].get('total_wall_clock_seconds', 0)
            local_auto = results['local'].get('automated_processing_seconds', 0)
            remote_auto = results['remote'].get('automated_processing_seconds', 0)

            print(f"\n{'Metric':<35} {'Local':>12} {'Remote':>12} {'Diff':>12}")
            print("-" * 73)

            if local_total and remote_total:
                diff = remote_total - local_total
                print(f"{'Total wall-clock time':35} {local_total:>10.1f}s {remote_total:>10.1f}s {diff:>+10.1f}s")

            if local_auto and remote_auto:
                diff = remote_auto - local_auto
                print(f"{'Automated processing':35} {local_auto:>10.1f}s {remote_auto:>10.1f}s {diff:>+10.1f}s")

            # Compare category totals
            print("\nCategory breakdown:")
            local_cats = results['local'].get('category_totals', {})
            remote_cats = results['remote'].get('category_totals', {})
            all_cats = set(local_cats.keys()) | set(remote_cats.keys())

            for cat in sorted(all_cats):
                local_time = local_cats.get(cat, 0)
                remote_time = remote_cats.get(cat, 0)
                if local_time > 0 or remote_time > 0:
                    diff = remote_time - local_time
                    cat_label = cat.replace('_', ' ').title()
                    print(f"  {cat_label:<33} {local_time:>10.1f}s {remote_time:>10.1f}s {diff:>+10.1f}s")


def main():
    parser = argparse.ArgumentParser(
        description="Compare local vs remote karaoke generation performance"
    )
    parser.add_argument("audio_file", type=Path, help="Path to audio file to process")
    parser.add_argument("--artist", default="Minutemen", help="Artist name (default: Minutemen)")
    parser.add_argument("--title", default="Search", help="Track title (default: Search)")
    parser.add_argument("--local-only", action="store_true", help="Only run local test")
    parser.add_argument("--remote-only", action="store_true", help="Only run remote test")
    parser.add_argument("--output-dir", type=Path, help="Custom output directory")

    args = parser.parse_args()

    # Validate audio file
    if not args.audio_file.exists():
        print(f"Error: Audio file not found: {args.audio_file}")
        sys.exit(1)

    # Create timestamped output directory
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if args.output_dir:
        output_base = args.output_dir
    else:
        output_base = Path(__file__).parent.parent / "output" / f"perf-test-{timestamp}"

    output_base.mkdir(parents=True, exist_ok=True)

    local_output = output_base / "local"
    remote_output = output_base / "remote"

    print(f"\n{'='*70}")
    print(f"KARAOKE GENERATION PERFORMANCE TEST")
    print(f"{'='*70}")
    print(f"Audio file: {args.audio_file}")
    print(f"Artist: {args.artist}")
    print(f"Title: {args.title}")
    print(f"Output directory: {output_base}")
    print(f"{'='*70}")

    local_log = None
    remote_log = None

    # Run local test
    if not args.remote_only:
        local_output.mkdir(parents=True, exist_ok=True)
        return_code, elapsed, local_log = run_local_test(
            args.audio_file, local_output, args.artist, args.title
        )
        print(f"\nLocal test completed in {elapsed:.1f}s ({elapsed/60:.1f} minutes)")
        print(f"Return code: {return_code}")

    # Run remote test
    if not args.local_only:
        remote_output.mkdir(parents=True, exist_ok=True)
        return_code, elapsed, remote_log, job_id = run_remote_test(
            args.audio_file, remote_output, args.artist, args.title
        )
        print(f"\nRemote test completed in {elapsed:.1f}s ({elapsed/60:.1f} minutes)")
        print(f"Return code: {return_code}")

        if job_id:
            print(f"Job ID: {job_id}")
            print(f"\nTo fetch detailed cloud logs:")
            print(f"  python scripts/fetch_job_logs.py {job_id}")

    # Analyze logs
    analyze_logs(local_log, remote_log, output_base)

    print(f"\n{'='*70}")
    print(f"TEST COMPLETE")
    print(f"{'='*70}")
    print(f"Output directory: {output_base}")
    if local_log:
        print(f"Local log: {local_log}")
    if remote_log:
        print(f"Remote log: {remote_log}")


if __name__ == "__main__":
    main()
