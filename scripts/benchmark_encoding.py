#!/usr/bin/env python3
"""
Encoding Performance Benchmark Script

This script benchmarks the actual encoding operations used in karaoke generation
by importing and using the real code paths from LyricsTranscriber and karaoke-gen.

Usage:
    # Download test files and run benchmark locally
    python scripts/benchmark_encoding.py --download --run

    # Run benchmark on already-downloaded files
    python scripts/benchmark_encoding.py --run

Test data source:
    Job fddad04d (piri - dog) from GCS

Benchmark stages (using actual code):
    1. Preview video (480x270 with ASS) - LocalPreviewEncodingService
    2. Full "With Vocals" video (4K with ASS) - LyricsTranscriber VideoGenerator
    3. Remux with instrumental - LocalEncodingService
    4. Lossless 4K concat (title + karaoke + end) - LocalEncodingService
    5. Lossy 4K (AAC audio) - LocalEncodingService
    6. MKV (FLAC audio) - LocalEncodingService
    7. 720p downscale - LocalEncodingService
"""

import argparse
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Test job ID (piri - dog)
TEST_JOB_ID = "fddad04d"
GCS_BUCKET = "karaoke-gen-storage-nomadkaraoke"
GCS_JOB_PATH = f"gs://{GCS_BUCKET}/jobs/{TEST_JOB_ID}"
GCS_THEME_PATH = f"gs://{GCS_BUCKET}/themes/nomad/assets"

# Local benchmark directory
BENCHMARK_DIR = Path("benchmark_data")

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


@dataclass
class BenchmarkResult:
    """Result of a single benchmark operation."""
    name: str
    duration_seconds: float
    success: bool
    output_size_mb: float = 0.0
    error: Optional[str] = None


@dataclass
class SystemInfo:
    """System information for the benchmark."""
    platform: str
    platform_version: str
    cpu_model: str
    cpu_count: int
    memory_gb: float
    ffmpeg_version: str
    hostname: str


def get_system_info() -> SystemInfo:
    """Collect system information."""
    cpu_model = "Unknown"
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True
            )
            cpu_model = result.stdout.strip()
        except Exception:
            pass
    elif platform.system() == "Linux":
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        cpu_model = line.split(":")[1].strip()
                        break
        except Exception:
            pass

    memory_gb = 0.0
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True
            )
            memory_gb = int(result.stdout.strip()) / (1024**3)
        except Exception:
            pass
    elif platform.system() == "Linux":
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        mem_kb = int(line.split()[1])
                        memory_gb = mem_kb / (1024**2)
                        break
        except Exception:
            pass

    ffmpeg_version = "Unknown"
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, text=True
        )
        ffmpeg_version = result.stdout.split("\n")[0]
    except Exception:
        pass

    hostname = platform.node()

    return SystemInfo(
        platform=platform.system(),
        platform_version=platform.release(),
        cpu_model=cpu_model,
        cpu_count=os.cpu_count() or 0,
        memory_gb=round(memory_gb, 1),
        ffmpeg_version=ffmpeg_version,
        hostname=hostname,
    )


def download_test_files(benchmark_dir: Path) -> bool:
    """Download test files from GCS."""
    print(f"\n{'='*60}")
    print("Downloading test files from GCS...")
    print(f"{'='*60}")

    benchmark_dir.mkdir(parents=True, exist_ok=True)

    files_to_download = [
        # Lyrics/subtitles
        (f"{GCS_JOB_PATH}/lyrics/karaoke.ass", benchmark_dir / "karaoke.ass"),
        # Screens
        (f"{GCS_JOB_PATH}/screens/title.mov", benchmark_dir / "title.mov"),
        (f"{GCS_JOB_PATH}/screens/end.mov", benchmark_dir / "end.mov"),
        # Audio
        (f"{GCS_JOB_PATH}/stems/instrumental_clean.flac", benchmark_dir / "instrumental_clean.flac"),
        (f"{GCS_JOB_PATH}/stems/vocals_clean.flac", benchmark_dir / "vocals.flac"),
        # Theme assets
        (f"{GCS_THEME_PATH}/karaoke-background-image-nomad-4k.png", benchmark_dir / "background.png"),
        (f"{GCS_THEME_PATH}/AvenirNext-Bold.ttf", benchmark_dir / "font.ttf"),
    ]

    for gcs_path, local_path in files_to_download:
        if local_path.exists():
            print(f"  [cached] {local_path.name}")
            continue

        print(f"  Downloading {local_path.name}...")
        try:
            result = subprocess.run(
                ["gsutil", "-q", "cp", gcs_path, str(local_path)],
                capture_output=True, text=True, timeout=300
            )
            if result.returncode != 0:
                print(f"    ERROR: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            print(f"    ERROR: Download timed out")
            return False

    print("  All files downloaded successfully.")
    return True


def time_operation(func, name: str) -> BenchmarkResult:
    """Time a function and return a BenchmarkResult."""
    print(f"\n  Running: {name}")
    start_time = time.perf_counter()

    try:
        result = func()
        duration = time.perf_counter() - start_time

        # Get output size if result is a path
        output_size_mb = 0.0
        if isinstance(result, (str, Path)):
            result_path = Path(result)
            if result_path.exists():
                output_size_mb = result_path.stat().st_size / (1024 * 1024)

        print(f"    Duration: {duration:.2f}s, Output: {output_size_mb:.1f}MB")
        return BenchmarkResult(
            name=name,
            duration_seconds=duration,
            success=True,
            output_size_mb=output_size_mb,
        )
    except Exception as e:
        duration = time.perf_counter() - start_time
        print(f"    FAILED: {e}")
        return BenchmarkResult(
            name=name,
            duration_seconds=duration,
            success=False,
            error=str(e),
        )


def run_benchmarks(benchmark_dir: Path, output_dir: Path) -> List[BenchmarkResult]:
    """Run all encoding benchmarks using actual code paths."""
    from backend.services.local_encoding_service import LocalEncodingService, EncodingConfig
    from backend.services.local_preview_encoding_service import (
        LocalPreviewEncodingService, PreviewEncodingConfig
    )
    from karaoke_gen.lyrics_transcriber.output.video import VideoGenerator

    results = []
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = output_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Input files
    ass_file = str(benchmark_dir / "karaoke.ass")
    vocals_audio = str(benchmark_dir / "vocals.flac")
    instrumental_audio = str(benchmark_dir / "instrumental_clean.flac")
    title_mov = str(benchmark_dir / "title.mov")
    end_mov = str(benchmark_dir / "end.mov")
    background_image = str(benchmark_dir / "background.png")
    font_file = str(benchmark_dir / "font.ttf")

    # Verify input files exist
    required_files = [ass_file, vocals_audio, instrumental_audio, title_mov, end_mov, background_image]
    for f in required_files:
        if not Path(f).exists():
            print(f"ERROR: Missing required file: {f}")
            return results

    print(f"\n{'='*60}")
    print("Running Encoding Benchmarks (using actual code paths)")
    print(f"{'='*60}")

    # ============================================================
    # Stage 1: Preview video (480x270 with ASS overlay)
    # Uses: LocalPreviewEncodingService
    # ============================================================
    preview_output = str(output_dir / "preview.mp4")
    preview_service = LocalPreviewEncodingService(logger=logger)
    preview_config = PreviewEncodingConfig(
        ass_path=ass_file,
        audio_path=vocals_audio,
        output_path=preview_output,
        background_image_path=background_image,
        background_color="black",
        font_path=font_file,
    )

    def run_preview():
        result = preview_service.encode_preview(preview_config)
        if not result.success:
            raise RuntimeError(result.error)
        return result.output_path

    results.append(time_operation(run_preview, "Stage 1: Preview Video (480x270 ASS)"))

    # ============================================================
    # Stage 2: Full "With Vocals" video (4K with ASS overlay)
    # Uses: LyricsTranscriber VideoGenerator._build_ffmpeg_command
    # This is the HEAVIEST operation - full 4K render with libass
    # ============================================================
    with_vocals_output = str(output_dir / "with_vocals.mkv")

    # Create VideoGenerator with realistic styles config
    styles = {
        "karaoke": {
            "background_image": background_image,
            "background_color": "black",
            "font_path": font_file,
        }
    }
    video_gen = VideoGenerator(
        output_dir=str(output_dir),
        cache_dir=str(cache_dir),
        video_resolution=(3840, 2160),  # 4K
        styles=styles,
        logger=logger,
    )

    def run_with_vocals():
        # Use the actual generate_video method
        return video_gen.generate_video(ass_file, vocals_audio, "benchmark")

    results.append(time_operation(run_with_vocals, "Stage 2: With Vocals Video (4K ASS) - HEAVIEST"))

    # Get the actual output path from VideoGenerator
    with_vocals_actual = str(output_dir / "benchmark (With Vocals).mkv")
    if not Path(with_vocals_actual).exists():
        print(f"    ERROR: With Vocals video not generated at {with_vocals_actual}")
        # Try to find it
        for f in output_dir.glob("*.mkv"):
            print(f"    Found: {f}")
            with_vocals_actual = str(f)
            break

    # ============================================================
    # Stage 3: Remux with instrumental audio
    # Uses: LocalEncodingService.remux_with_instrumental
    # ============================================================
    karaoke_mp4 = str(output_dir / "karaoke.mp4")
    encoding_service = LocalEncodingService(logger=logger)

    def run_remux():
        success = encoding_service.remux_with_instrumental(
            with_vocals_actual,
            instrumental_audio,
            karaoke_mp4,
        )
        if not success:
            raise RuntimeError("Remux failed")
        return karaoke_mp4

    results.append(time_operation(run_remux, "Stage 3: Remux with Instrumental"))

    # ============================================================
    # Stage 4: Convert title/end MOV to MP4 (needed for concat)
    # Uses: LocalEncodingService.convert_mov_to_mp4
    # ============================================================
    title_mp4 = str(output_dir / "title.mp4")
    end_mp4 = str(output_dir / "end.mp4")

    def run_title_convert():
        success = encoding_service.convert_mov_to_mp4(title_mov, title_mp4)
        if not success:
            raise RuntimeError("Title convert failed")
        return title_mp4

    def run_end_convert():
        success = encoding_service.convert_mov_to_mp4(end_mov, end_mp4)
        if not success:
            raise RuntimeError("End convert failed")
        return end_mp4

    results.append(time_operation(run_title_convert, "Stage 4a: Convert Title MOV to MP4"))
    results.append(time_operation(run_end_convert, "Stage 4b: Convert End MOV to MP4"))

    # ============================================================
    # Stage 5: Lossless 4K concatenation (title + karaoke + end)
    # Uses: LocalEncodingService.encode_lossless_mp4
    # ============================================================
    lossless_4k = str(output_dir / "final_lossless_4k.mp4")

    def run_lossless_concat():
        success = encoding_service.encode_lossless_mp4(
            title_mp4,
            karaoke_mp4,
            lossless_4k,
            end_mp4,
        )
        if not success:
            raise RuntimeError("Lossless concat failed")
        return lossless_4k

    results.append(time_operation(run_lossless_concat, "Stage 5: Lossless 4K Concat (title+karaoke+end)"))

    # ============================================================
    # Stage 6: Lossy 4K with AAC audio
    # Uses: LocalEncodingService.encode_lossy_mp4
    # ============================================================
    lossy_4k = str(output_dir / "final_lossy_4k.mp4")

    def run_lossy():
        success = encoding_service.encode_lossy_mp4(lossless_4k, lossy_4k)
        if not success:
            raise RuntimeError("Lossy 4K failed")
        return lossy_4k

    if Path(lossless_4k).exists():
        results.append(time_operation(run_lossy, "Stage 6: Lossy 4K (AAC audio)"))

    # ============================================================
    # Stage 7: MKV with FLAC audio
    # Uses: LocalEncodingService.encode_lossless_mkv
    # ============================================================
    lossless_mkv = str(output_dir / "final_lossless_4k.mkv")

    def run_mkv():
        success = encoding_service.encode_lossless_mkv(lossless_4k, lossless_mkv)
        if not success:
            raise RuntimeError("MKV failed")
        return lossless_mkv

    if Path(lossless_4k).exists():
        results.append(time_operation(run_mkv, "Stage 7: MKV (FLAC audio)"))

    # ============================================================
    # Stage 8: 720p downscale
    # Uses: LocalEncodingService.encode_720p
    # ============================================================
    lossy_720p = str(output_dir / "final_lossy_720p.mp4")

    def run_720p():
        success = encoding_service.encode_720p(lossless_4k, lossy_720p)
        if not success:
            raise RuntimeError("720p failed")
        return lossy_720p

    if Path(lossless_4k).exists():
        results.append(time_operation(run_720p, "Stage 8: 720p Downscale"))

    return results


def print_results(system_info: SystemInfo, results: List[BenchmarkResult]):
    """Print benchmark results."""
    print(f"\n{'='*60}")
    print("BENCHMARK RESULTS")
    print(f"{'='*60}")

    print(f"\nSystem Information:")
    print(f"  Hostname:     {system_info.hostname}")
    print(f"  Platform:     {system_info.platform} {system_info.platform_version}")
    print(f"  CPU:          {system_info.cpu_model}")
    print(f"  CPU Count:    {system_info.cpu_count}")
    print(f"  Memory:       {system_info.memory_gb} GB")
    print(f"  FFmpeg:       {system_info.ffmpeg_version}")

    print(f"\nResults:")
    print(f"{'Operation':<55} {'Duration':>10} {'Size':>10} {'Status':>10}")
    print("-" * 85)

    total_time = 0.0
    for r in results:
        status = "OK" if r.success else "FAILED"
        duration_str = f"{r.duration_seconds:.2f}s"
        size_str = f"{r.output_size_mb:.1f}MB" if r.output_size_mb > 0 else "-"
        print(f"{r.name:<55} {duration_str:>10} {size_str:>10} {status:>10}")
        if r.success:
            total_time += r.duration_seconds
        if not r.success and r.error:
            print(f"    Error: {r.error[:80]}")

    print("-" * 85)
    print(f"{'TOTAL (successful operations)':<55} {total_time:.2f}s")

    # Calculate key metrics
    with_vocals = next((r for r in results if "With Vocals" in r.name), None)
    lossless_concat = next((r for r in results if "Lossless 4K Concat" in r.name), None)
    downscale_720p = next((r for r in results if "720p Downscale" in r.name), None)

    print(f"\nKey Metrics:")
    if with_vocals and with_vocals.success:
        print(f"  With Vocals (4K ASS render):   {with_vocals.duration_seconds:.2f}s  <- HEAVIEST OPERATION")
    if lossless_concat and lossless_concat.success:
        print(f"  Lossless 4K concatenation:     {lossless_concat.duration_seconds:.2f}s")
    if downscale_720p and downscale_720p.success:
        print(f"  720p downscale:                {downscale_720p.duration_seconds:.2f}s")


def save_results(system_info: SystemInfo, results: List[BenchmarkResult], output_path: Path):
    """Save results to JSON file."""
    data = {
        "system": {
            "hostname": system_info.hostname,
            "platform": system_info.platform,
            "platform_version": system_info.platform_version,
            "cpu_model": system_info.cpu_model,
            "cpu_count": system_info.cpu_count,
            "memory_gb": system_info.memory_gb,
            "ffmpeg_version": system_info.ffmpeg_version,
        },
        "results": [
            {
                "name": r.name,
                "duration_seconds": r.duration_seconds,
                "success": r.success,
                "output_size_mb": r.output_size_mb,
                "error": r.error,
            }
            for r in results
        ],
        "total_time": sum(r.duration_seconds for r in results if r.success),
    }

    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\nResults saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Encoding Performance Benchmark")
    parser.add_argument("--download", action="store_true", help="Download test files from GCS")
    parser.add_argument("--run", action="store_true", help="Run benchmarks locally")
    parser.add_argument("--output", type=str, default="benchmark_results.json", help="Output JSON file")
    parser.add_argument("--data-dir", type=str, default="benchmark_data", help="Directory for test data")
    parser.add_argument("--output-dir", type=str, default=None, help="Directory for output files (default: temp)")
    args = parser.parse_args()

    benchmark_dir = Path(args.data_dir)

    if not any([args.download, args.run]):
        parser.print_help()
        print("\nExample usage:")
        print("  python scripts/benchmark_encoding.py --download --run")
        sys.exit(1)

    if args.download:
        if not download_test_files(benchmark_dir):
            print("ERROR: Failed to download test files")
            sys.exit(1)

    if args.run:
        # Check for required files
        required = ["karaoke.ass", "title.mov", "end.mov", "instrumental_clean.flac", "vocals.flac", "background.png"]
        missing = [f for f in required if not (benchmark_dir / f).exists()]
        if missing:
            print(f"ERROR: Missing files: {missing}")
            print("Run with --download first")
            sys.exit(1)

        system_info = get_system_info()

        # Use temp directory or specified output directory
        if args.output_dir:
            output_dir = Path(args.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            results = run_benchmarks(benchmark_dir, output_dir)
            print_results(system_info, results)
            save_results(system_info, results, Path(args.output))
        else:
            with tempfile.TemporaryDirectory() as tmpdir:
                output_dir = Path(tmpdir)
                results = run_benchmarks(benchmark_dir, output_dir)
                print_results(system_info, results)
                save_results(system_info, results, Path(args.output))


if __name__ == "__main__":
    main()
