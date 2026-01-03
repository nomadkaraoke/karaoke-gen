#!/usr/bin/env python3
"""
Isolated FFmpeg encoding benchmark.

Creates a test video and runs the same encoding commands used by karaoke-gen
to measure raw encoding performance. Can be run locally and compared with
cloud results.

Usage:
    # Run locally
    python scripts/benchmark_ffmpeg.py

    # Compare with cloud (run from Cloud Run shell)
    python scripts/benchmark_ffmpeg.py --cloud

Output:
    Timing results for each encoding step
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path


def run_timed_command(description: str, command: str, timeout: int = 600) -> dict:
    """Run a command and return timing info."""
    print(f"\n{'='*60}")
    print(f"BENCHMARK: {description}")
    print(f"{'='*60}")
    print(f"Command: {command[:100]}...")

    start = time.time()
    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout
    )
    elapsed = time.time() - start

    success = result.returncode == 0
    print(f"Result: {'SUCCESS' if success else 'FAILED'}")
    print(f"Time: {elapsed:.2f}s")

    if not success:
        print(f"Error: {result.stderr[:500]}")

    return {
        "description": description,
        "success": success,
        "elapsed_seconds": elapsed,
        "command": command,
        "stderr": result.stderr[:1000] if not success else None,
    }


def get_video_info(file_path: str) -> dict:
    """Get video file info using ffprobe."""
    cmd = f'ffprobe -v quiet -print_format json -show_format -show_streams "{file_path}"'
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        return json.loads(result.stdout)
    return {}


def create_test_video(output_path: str, duration: int = 60) -> dict:
    """Create a test video similar to karaoke output (4K, ~1 minute)."""
    # Generate a 4K test pattern video with audio
    # This simulates the karaoke video before final encoding
    command = (
        f'ffmpeg -y -f lavfi -i "testsrc2=size=3840x2160:rate=30:duration={duration}" '
        f'-f lavfi -i "sine=frequency=440:duration={duration}" '
        f'-c:v libx264 -preset ultrafast -crf 18 -pix_fmt yuv420p '
        f'-c:a pcm_s16le -ar 48000 '
        f'"{output_path}"'
    )
    return run_timed_command(f"Create test video ({duration}s, 4K)", command)


def benchmark_lossless_4k_encode(input_file: str, output_file: str) -> dict:
    """
    Benchmark Step 3: Lossless 4K MP4 encoding.
    This is the most CPU-intensive step in karaoke-gen.
    """
    command = (
        f'ffmpeg -y -hide_banner -nostats -loglevel warning '
        f'-i "{input_file}" '
        f'-c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p '
        f'-c:a pcm_s16le -ar 48000 '
        f'"{output_file}"'
    )
    return run_timed_command("Encode lossless 4K MP4 (Step 3 equivalent)", command)


def benchmark_720p_encode(input_file: str, output_file: str) -> dict:
    """
    Benchmark Step 6: 720p encoding with scaling.
    This is the second most CPU-intensive step.
    """
    command = (
        f'ffmpeg -y -hide_banner -nostats -loglevel warning '
        f'-i "{input_file}" '
        f'-c:v libx264 -vf "scale=1280:720" -b:v 2000k -preset medium -tune animation '
        f'-c:a aac -ar 48000 -b:a 128k -pix_fmt yuv420p '
        f'"{output_file}"'
    )
    return run_timed_command("Encode 720p MP4 (Step 6 equivalent)", command)


def benchmark_lossy_4k_encode(input_file: str, output_file: str) -> dict:
    """
    Benchmark Step 4: Lossy 4K with AAC audio.
    Mostly stream copy, should be fast.
    """
    command = (
        f'ffmpeg -y -hide_banner -nostats -loglevel warning '
        f'-i "{input_file}" '
        f'-c:v copy -c:a aac -ar 48000 -b:a 320k '
        f'"{output_file}"'
    )
    return run_timed_command("Encode lossy 4K MP4 (Step 4 equivalent)", command)


def benchmark_mkv_flac_encode(input_file: str, output_file: str) -> dict:
    """
    Benchmark Step 5: MKV with FLAC audio.
    Video copy, audio re-encode to FLAC.
    """
    command = (
        f'ffmpeg -y -hide_banner -nostats -loglevel warning '
        f'-i "{input_file}" '
        f'-c:v copy -c:a flac '
        f'"{output_file}"'
    )
    return run_timed_command("Encode MKV with FLAC (Step 5 equivalent)", command)


def benchmark_title_screen_generation(output_file: str) -> dict:
    """
    Benchmark title screen generation (5 second video).
    This simulates the screens worker.
    """
    # Create a title screen similar to what karaoke-gen creates
    command = (
        f'ffmpeg -y -hide_banner -nostats -loglevel warning '
        f'-f lavfi -i "color=black:s=3840x2160:d=5:r=30" '
        f'-f lavfi -i "anullsrc=r=48000:cl=stereo:d=5" '
        f'-vf "drawtext=text=\'Artist - Title\':fontsize=120:fontcolor=white:'
        f'x=(w-text_w)/2:y=(h-text_h)/2" '
        f'-c:v libx264 -preset medium -pix_fmt yuv420p '
        f'-c:a pcm_s16le '
        f'"{output_file}"'
    )
    return run_timed_command("Generate title screen (5s, 4K)", command)


def get_system_info() -> dict:
    """Get system information for context."""
    info = {
        "timestamp": datetime.now().isoformat(),
        "python_version": sys.version,
    }

    # Get FFmpeg version
    result = subprocess.run("ffmpeg -version", shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        info["ffmpeg_version"] = result.stdout.split('\n')[0]

    # Get CPU info
    if sys.platform == "darwin":
        result = subprocess.run("sysctl -n machdep.cpu.brand_string", shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            info["cpu"] = result.stdout.strip()
        result = subprocess.run("sysctl -n hw.ncpu", shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            info["cpu_cores"] = int(result.stdout.strip())
    else:
        result = subprocess.run("nproc", shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            info["cpu_cores"] = int(result.stdout.strip())
        result = subprocess.run("cat /proc/cpuinfo | grep 'model name' | head -1", shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            info["cpu"] = result.stdout.split(':')[-1].strip() if ':' in result.stdout else "Unknown"

    return info


def main():
    parser = argparse.ArgumentParser(description="FFmpeg encoding benchmark")
    parser.add_argument("--duration", type=int, default=60, help="Test video duration in seconds")
    parser.add_argument("--cloud", action="store_true", help="Running on cloud (skip some checks)")
    parser.add_argument("--output-dir", type=Path, help="Output directory for results")
    parser.add_argument("--existing-video", type=Path, help="Use existing video instead of creating test")

    args = parser.parse_args()

    # Setup output directory
    if args.output_dir:
        output_dir = args.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
    else:
        output_dir = Path(tempfile.mkdtemp(prefix="ffmpeg_benchmark_"))

    print("="*70)
    print("FFMPEG ENCODING BENCHMARK")
    print("="*70)
    print(f"Output directory: {output_dir}")
    print(f"Test duration: {args.duration}s")
    print(f"Environment: {'Cloud' if args.cloud else 'Local'}")

    # Get system info
    sys_info = get_system_info()
    print(f"\nSystem Info:")
    for k, v in sys_info.items():
        print(f"  {k}: {v}")

    results = {
        "system_info": sys_info,
        "environment": "cloud" if args.cloud else "local",
        "benchmarks": [],
    }

    # Create or use test video
    if args.existing_video and args.existing_video.exists():
        test_video = str(args.existing_video)
        print(f"\nUsing existing video: {test_video}")
    else:
        test_video = str(output_dir / "test_input.mp4")
        result = create_test_video(test_video, args.duration)
        results["benchmarks"].append(result)

        if not result["success"]:
            print("ERROR: Failed to create test video")
            sys.exit(1)

    # Get video info
    video_info = get_video_info(test_video)
    if video_info:
        duration = float(video_info.get("format", {}).get("duration", args.duration))
        print(f"\nTest video duration: {duration:.1f}s")
        results["video_duration"] = duration

    # Run benchmarks
    benchmarks = [
        ("title_screen", benchmark_title_screen_generation, output_dir / "title_screen.mp4"),
        ("lossless_4k", benchmark_lossless_4k_encode, output_dir / "output_lossless_4k.mp4"),
        ("lossy_4k", benchmark_lossy_4k_encode, output_dir / "output_lossy_4k.mp4"),
        ("mkv_flac", benchmark_mkv_flac_encode, output_dir / "output_lossless_4k.mkv"),
        ("720p", benchmark_720p_encode, output_dir / "output_720p.mp4"),
    ]

    for name, func, output_path in benchmarks:
        if name == "title_screen":
            result = func(str(output_path))
        else:
            result = func(test_video, str(output_path))
        results["benchmarks"].append(result)

    # Summary
    print("\n" + "="*70)
    print("BENCHMARK SUMMARY")
    print("="*70)
    print(f"\n{'Step':<40} {'Time':>12} {'Status':>10}")
    print("-"*64)

    total_time = 0
    for bench in results["benchmarks"]:
        status = "OK" if bench["success"] else "FAILED"
        time_str = f"{bench['elapsed_seconds']:.2f}s"
        print(f"{bench['description']:<40} {time_str:>12} {status:>10}")
        if bench["success"]:
            total_time += bench["elapsed_seconds"]

    print("-"*64)
    print(f"{'Total encoding time':<40} {total_time:.2f}s")

    # Save results
    results_file = output_dir / "benchmark_results.json"
    with open(results_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {results_file}")

    # Cleanup temp files (keep results)
    if not args.output_dir:
        print(f"\nTemp files in: {output_dir}")
        print("Delete manually when done reviewing")

    return results


if __name__ == "__main__":
    main()
