#!/usr/bin/env python3
"""
Karaoke-Gen Log Timing Analyzer

Parses karaoke-gen log output (both local CLI and cloud job logs) and extracts
timing information for each stage of the processing pipeline. Identifies both
automated processing time and human interaction wait times.

Supports two log formats:
- Local CLI: "2025-12-30 16:22:28.657 - INFO - logger - message"
- Cloud Job: "2025-12-30T20:52:21.293 - INFO - backend.service - message"

Usage:
    python analyze_log_timing.py <log_file>
    python analyze_log_timing.py <log_file> --json       # Output as JSON
    python analyze_log_timing.py <log_file> --verbose    # Show all events
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


@dataclass
class Stage:
    """Represents a processing stage with timing information."""
    name: str
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    is_human_wait: bool = False
    sub_stages: list = field(default_factory=list)

    @property
    def duration(self) -> Optional[timedelta]:
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        return None

    @property
    def duration_seconds(self) -> Optional[float]:
        d = self.duration
        return d.total_seconds() if d else None

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'duration_seconds': self.duration_seconds,
            'is_human_wait': self.is_human_wait,
            'sub_stages': [s.to_dict() for s in self.sub_stages]
        }


# Timestamp patterns for both formats
# Local: 2025-12-30 16:22:28.657
# Cloud: 2025-12-30T20:52:21.293
TIMESTAMP_PATTERN_LOCAL = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})')
TIMESTAMP_PATTERN_CLOUD = re.compile(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3})')

# Stage detection patterns
STAGE_PATTERNS = {
    # Initial setup
    'flacfetch_search': (r'Using remote flacfetch API at:', r'Found \d+ results'),
    'human_track_select': (r'Select \(1-\d+\)', r'Downloading:.*from'),
    'audio_download': (r'\[RemoteFlacFetcher\] Downloading:', r'Audio downloaded from'),

    # Parallel processing phase
    'parallel_start': (r'=== Starting Parallel Processing ===', None),

    # Lyrics track
    'lyrics_fetch_lrclib': (r'lrclib - Searching LRCLIB for', r'Successfully fetched lyrics from lrclib'),
    'lyrics_fetch_genius': (r'genius - Trying RapidAPI for', r'Successfully fetched lyrics from genius'),
    'lyrics_fetch_spotify': (r'spotify - Trying RapidAPI for', r'Successfully fetched lyrics from spotify'),
    'lyrics_transcription': (r'audioshake - Starting transcription for', r'audioshake - All targets completed'),
    'lyrics_correction': (r'corrector - Starting correction process', r'Correction process completed'),

    # Audio track
    'audio_separation_stage1': (r'Stage 1: Submitting audio separation job', r'Stage 1 completed'),
    'audio_separation_stage2': (r'Stage 2: Processing clean vocals', r'Stage 2 completed'),
    'audio_normalization': (r'Normalizing clean instrumental', r'Audio normalization process completed'),

    # Human review
    'human_lyrics_review': (r'Opening review UI:', r'Human review completed'),

    # Post-review rendering
    'video_render_main': (r'Rendering karaoke video with synchronized lyrics', r'Video rendered successfully:'),
    'title_screen_gen': (r'Creating title video', r'Creating end screen video'),
    'end_screen_gen': (r'Creating end screen video', r'Audio separation was already completed'),

    # Human instrumental selection
    'human_instrumental_select': (r'Starting instrumental review UI', r'User selected:'),

    # KaraokeFinalise phase
    'human_finalise_confirm': (r'Confirm features enabled log messages above match', r'Finding input file ending in'),
    'cdg_package': (r'Creating CDG package', r'CDG package created successfully'),
    'txt_package': (r'Creating TXT package', r'TXT package created successfully'),

    # Video encoding (the big one)
    'encode_step1_remux': (r'\[Step 1/6\] Remuxing video', r'\[Step 2/6\]'),
    'encode_step2_mp4_convert': (r'\[Step 2/6\] Converting karaoke video', r'\[Step 3/6\]'),
    'encode_step3_lossless_4k': (r'\[Step 3/6\] Encoding lossless 4K', r'\[Step 4/6\]'),
    'encode_step4_lossy_4k': (r'\[Step 4/6\] Encoding lossy 4K', r'\[Step 5/6\]'),
    'encode_step5_mkv_flac': (r'\[Step 5/6\] Creating MKV', r'\[Step 6/6\]'),
    'encode_step6_720p': (r'\[Step 6/6\] Encoding 720p', r'Video encoding completed'),

    # Human video check
    'human_video_check': (r'Please check them! Proceed\?', r'Video encoding completed successfully'),

    # Modal file downloads (often overlooked)
    'modal_download_stage1': (r'📥 Downloading 8 output files', r'🎉 Successfully downloaded 8 files'),
    'modal_download_stage2': (r'📥 Downloading 2 output files', r'🎉 Successfully downloaded 2 files'),

    # Distribution
    'youtube_upload': (r'Uploading final MKV to YouTube', r'Uploaded video to YouTube:'),
    'discord_notification': (r'Posting Discord notification', r'Message posted to Discord'),
    'rclone_copy': (r'Copying to cloud destination', r'Command completed successfully'),
}

# Cloud-specific stage patterns (for backend workers)
CLOUD_STAGE_PATTERNS = {
    # Job lifecycle
    'job_created': (r'transitioned to searching_audio', None),
    'audio_search': (r'transitioned to searching_audio', r'transitioned to awaiting_audio_selection'),
    'human_audio_select': (r'transitioned to awaiting_audio_selection', r'transitioned to downloading_audio'),
    'flacfetch_download': (r'transitioned to downloading_audio', r'transitioned to downloading'),

    # Audio worker (cloud) - overall
    'cloud_audio_worker': (r'=== AUDIO WORKER STARTED ===', r'Audio separation complete!'),

    # Audio worker stages
    'cloud_audio_gcs_download': (r'Downloading audio file\.\.\.', r'Audio downloaded:'),
    'cloud_modal_stage1': (r'Stage 1: Submitting audio separation job', r'Stage 1 completed'),
    'cloud_modal_stage1_download': (r'📥 Downloading 8 output files', r'🎉 Successfully downloaded 8 files'),
    'cloud_modal_stage2': (r'Stage 2: Processing clean vocals', r'Stage 2 completed'),
    'cloud_modal_stage2_download': (r'📥 Downloading 2 output files', r'🎉 Successfully downloaded 2 files'),
    'cloud_audio_normalize': (r'Normalizing clean instrumental', r'Audio normalization process completed'),
    'cloud_stems_upload': (r'Uploading separation results to GCS', r'All stems uploaded successfully'),

    # Lyrics worker (cloud) - overall
    'cloud_lyrics_worker': (r'=== LYRICS WORKER STARTED ===', r'=== LYRICS WORKER COMPLETE ==='),

    # Lyrics worker stages
    'cloud_lyrics_gcs_download': (r'Downloading audio file from GCS', r'Audio downloaded:.*lyrics'),
    'cloud_style_download': (r'Loading style configuration for lyrics', r'Using custom style params:'),
    'cloud_lyrics_fetch_lrclib': (r'Searching LRCLIB for', r'Successfully fetched lyrics from lrclib'),
    'cloud_lyrics_fetch_genius': (r'Trying RapidAPI for.*genius|Trying RapidAPI for', r'Successfully fetched lyrics from genius'),
    'cloud_lyrics_fetch_spotify': (r'Successfully fetched lyrics from genius', r'Successfully fetched lyrics from spotify'),
    'cloud_audioshake_transcribe': (r'Uploading.*to AudioShake', r'Getting task result for task'),
    'cloud_lyrics_correction': (r'Starting correction process', r'Correction process completed'),
    'cloud_lyrics_upload': (r'Uploading lyrics results to GCS', r'Successfully uploaded lyrics'),

    # Screens worker (cloud)
    'cloud_screens_worker': (r'=== SCREENS WORKER STARTED ===', r'=== SCREENS WORKER COMPLETE ==='),
    'cloud_title_screen': (r'Creating title screen', r'Title screen created'),
    'cloud_end_screen': (r'Creating end screen', r'End screen created'),

    # Render worker (cloud)
    'cloud_render_worker': (r'=== RENDER WORKER STARTED ===', r'=== RENDER WORKER COMPLETE ==='),
    'cloud_video_render': (r'Rendering karaoke video|Starting video render', r'Video rendered successfully|Video generation complete'),
    'cloud_render_upload': (r'Uploading rendered video', r'Successfully uploaded.*video'),

    # Video worker (cloud)
    'cloud_video_worker': (r'=== VIDEO WORKER STARTED ===', r'=== VIDEO WORKER COMPLETE ==='),
    'cloud_video_download': (r'Downloading files for video encoding', r'All files downloaded'),
    'cloud_video_encode': (r'Starting video encoding', r'Video encoding completed'),
}

# Human wait stages
HUMAN_STAGES = {
    'human_track_select',
    'human_lyrics_review',
    'human_instrumental_select',
    'human_finalise_confirm',
    'human_video_check',
    'human_audio_select',  # Cloud
}


def parse_timestamp(line: str) -> Optional[datetime]:
    """Extract timestamp from log line. Handles both local and cloud formats."""
    # Try local format first: 2025-12-30 16:22:28.657
    match = TIMESTAMP_PATTERN_LOCAL.match(line)
    if match:
        return datetime.strptime(match.group(1), '%Y-%m-%d %H:%M:%S.%f')

    # Try cloud format: 2025-12-30T20:52:21.293
    match = TIMESTAMP_PATTERN_CLOUD.match(line)
    if match:
        return datetime.strptime(match.group(1), '%Y-%m-%dT%H:%M:%S.%f')

    return None


def detect_log_type(lines: list) -> str:
    """Detect whether log is from local CLI or cloud backend."""
    for line in lines[:50]:  # Check first 50 lines
        if TIMESTAMP_PATTERN_CLOUD.match(line):
            if 'backend.' in line or 'WORKER_START' in line or 'Firestore' in line:
                return 'cloud'
        if TIMESTAMP_PATTERN_LOCAL.match(line):
            if 'karaoke_gen' in line or 'nomadauto' in line.lower():
                return 'local'
    # Default based on timestamp format
    for line in lines[:10]:
        if TIMESTAMP_PATTERN_CLOUD.match(line):
            return 'cloud'
        if TIMESTAMP_PATTERN_LOCAL.match(line):
            return 'local'
    return 'unknown'


def find_pattern_match(line: str, pattern: str) -> bool:
    """Check if line matches pattern."""
    return bool(re.search(pattern, line))


def analyze_log(log_path: Path, verbose: bool = False) -> dict:
    """
    Analyze a karaoke-gen log file and extract timing information.

    Returns:
        Dictionary with stage timings and summary statistics.
    """
    with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
        lines = f.readlines()

    # Detect log type
    log_type = detect_log_type(lines)
    if verbose:
        print(f"Detected log type: {log_type}")

    # Select appropriate patterns based on log type
    if log_type == 'cloud':
        patterns_to_use = {**CLOUD_STAGE_PATTERNS}
    else:
        patterns_to_use = {**STAGE_PATTERNS}

    # Track stage states
    stages: dict[str, Stage] = {}
    active_stages: set[str] = set()

    first_timestamp = None
    last_timestamp = None

    for line in lines:
        ts = parse_timestamp(line)
        if ts:
            if first_timestamp is None:
                first_timestamp = ts
            last_timestamp = ts

        # Check each stage pattern
        for stage_name, (start_pattern, end_pattern) in patterns_to_use.items():
            # Check for stage start
            if start_pattern and find_pattern_match(line, start_pattern):
                if stage_name not in stages:
                    stages[stage_name] = Stage(
                        name=stage_name,
                        is_human_wait=stage_name in HUMAN_STAGES
                    )
                if ts and not stages[stage_name].start_time:
                    stages[stage_name].start_time = ts
                    active_stages.add(stage_name)
                    if verbose:
                        print(f"[START] {stage_name} at {ts}")

            # Check for stage end
            if end_pattern and find_pattern_match(line, end_pattern):
                if stage_name in stages and stage_name in active_stages:
                    if ts:
                        stages[stage_name].end_time = ts
                        active_stages.discard(stage_name)
                        if verbose:
                            print(f"[END] {stage_name} at {ts} ({stages[stage_name].duration})")

    # Calculate summary statistics
    total_duration = (last_timestamp - first_timestamp) if (first_timestamp and last_timestamp) else None

    human_time = timedelta()
    processing_time = timedelta()

    for stage in stages.values():
        if stage.duration:
            if stage.is_human_wait:
                human_time += stage.duration
            else:
                processing_time += stage.duration

    # Group stages by category (different categories for local vs cloud)
    if log_type == 'cloud':
        categories = {
            'job_setup': ['job_created', 'audio_search', 'flacfetch_download', 'cloud_tasks_setup'],
            'audio_worker': ['cloud_audio_worker', 'cloud_audio_gcs_download', 'cloud_modal_api',
                            'cloud_modal_stage1_download', 'cloud_modal_stage2_download', 'cloud_stems_upload'],
            'lyrics_worker': ['cloud_lyrics_worker', 'cloud_lyrics_gcs_download', 'cloud_style_download',
                             'cloud_audioshake_upload', 'cloud_lyrics_correction', 'cloud_lyrics_upload'],
            'screens_worker': ['cloud_screens_worker', 'cloud_title_screen', 'cloud_end_screen'],
            'render_worker': ['cloud_render_worker', 'cloud_video_render', 'cloud_render_upload'],
            'video_worker': ['cloud_video_worker', 'cloud_video_download', 'cloud_video_encode'],
            'human_interaction': ['human_audio_select'],
        }
    else:
        categories = {
            'setup': ['flacfetch_search', 'audio_download'],
            'lyrics_processing': ['lyrics_fetch_lrclib', 'lyrics_fetch_genius', 'lyrics_fetch_spotify',
                                 'lyrics_transcription', 'lyrics_correction'],
            'audio_separation': ['audio_separation_stage1', 'audio_separation_stage2', 'audio_normalization',
                                'modal_download_stage1', 'modal_download_stage2'],
            'video_rendering': ['video_render_main', 'title_screen_gen', 'end_screen_gen'],
            'packaging': ['cdg_package', 'txt_package'],
            'video_encoding': ['encode_step1_remux', 'encode_step2_mp4_convert', 'encode_step3_lossless_4k',
                              'encode_step4_lossy_4k', 'encode_step5_mkv_flac', 'encode_step6_720p'],
            'distribution': ['youtube_upload', 'discord_notification', 'rclone_copy'],
            'human_interaction': ['human_track_select', 'human_lyrics_review', 'human_instrumental_select',
                                 'human_finalise_confirm', 'human_video_check'],
        }

    category_totals = {}
    for cat_name, stage_names in categories.items():
        total = timedelta()
        for sn in stage_names:
            if sn in stages and stages[sn].duration:
                total += stages[sn].duration
        category_totals[cat_name] = total.total_seconds()

    return {
        'log_file': str(log_path),
        'log_type': log_type,
        'first_timestamp': first_timestamp.isoformat() if first_timestamp else None,
        'last_timestamp': last_timestamp.isoformat() if last_timestamp else None,
        'total_wall_clock_seconds': total_duration.total_seconds() if total_duration else None,
        'total_human_wait_seconds': human_time.total_seconds(),
        'total_processing_seconds': processing_time.total_seconds(),
        'automated_processing_seconds': (total_duration.total_seconds() - human_time.total_seconds()) if total_duration else None,
        'category_totals': category_totals,
        'stages': {name: stage.to_dict() for name, stage in stages.items()},
    }


def format_duration(seconds: Optional[float]) -> str:
    """Format seconds as human-readable duration."""
    if seconds is None:
        return "N/A"
    minutes, secs = divmod(int(seconds), 60)
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def print_report(analysis: dict) -> None:
    """Print a human-readable timing report."""
    print("=" * 70)
    print("KARAOKE-GEN LOG TIMING ANALYSIS")
    print("=" * 70)
    log_type = analysis.get('log_type', 'unknown')
    print(f"\nLog file: {analysis['log_file']}")
    print(f"Log type: {log_type.upper()}")
    print(f"Start: {analysis['first_timestamp']}")
    print(f"End:   {analysis['last_timestamp']}")

    print("\n" + "-" * 70)
    print("SUMMARY")
    print("-" * 70)
    print(f"Total wall-clock time:     {format_duration(analysis['total_wall_clock_seconds'])}")
    print(f"Human interaction time:    {format_duration(analysis['total_human_wait_seconds'])}")
    print(f"Automated processing time: {format_duration(analysis['automated_processing_seconds'])}")

    print("\n" + "-" * 70)
    print("CATEGORY BREAKDOWN")
    print("-" * 70)
    for cat_name, seconds in analysis['category_totals'].items():
        if seconds > 0:
            label = cat_name.replace('_', ' ').title()
            print(f"  {label:25} {format_duration(seconds):>10}")

    print("\n" + "-" * 70)
    print("DETAILED STAGE TIMINGS")
    print("-" * 70)

    # Sort stages by start time
    sorted_stages = sorted(
        [(name, data) for name, data in analysis['stages'].items() if data['start_time']],
        key=lambda x: x[1]['start_time']
    )

    for name, data in sorted_stages:
        duration = format_duration(data['duration_seconds'])
        human_marker = " [HUMAN]" if data['is_human_wait'] else ""
        label = name.replace('_', ' ').title()
        print(f"  {label:35} {duration:>10}{human_marker}")

    print("\n" + "=" * 70)

    # Performance insights
    print("\nPERFORMANCE INSIGHTS")
    print("-" * 70)

    # Find slowest automated stages
    automated_stages = [
        (name, data['duration_seconds'])
        for name, data in analysis['stages'].items()
        if data['duration_seconds'] and not data['is_human_wait']
    ]
    automated_stages.sort(key=lambda x: x[1], reverse=True)

    print("\nSlowest automated stages:")
    for name, duration in automated_stages[:5]:
        label = name.replace('_', ' ').title()
        print(f"  {label:35} {format_duration(duration):>10}")

    # Calculate encoding total
    encoding_total = sum(
        analysis['stages'].get(f'encode_step{i}_{suffix}', {}).get('duration_seconds', 0) or 0
        for i, suffix in [(1, 'remux'), (2, 'mp4_convert'), (3, 'lossless_4k'),
                          (4, 'lossy_4k'), (5, 'mkv_flac'), (6, '720p')]
    )
    print(f"\nTotal video encoding time: {format_duration(encoding_total)}")

    # Parallelizable encoding stages (steps 4, 5, 6)
    parallel_steps = ['encode_step4_lossy_4k', 'encode_step5_mkv_flac', 'encode_step6_720p']
    parallel_times = [
        analysis['stages'].get(step, {}).get('duration_seconds', 0) or 0
        for step in parallel_steps
    ]
    current_sequential = sum(parallel_times)
    potential_parallel = max(parallel_times) if parallel_times else 0

    if current_sequential > 0:
        print(f"\nEncoding optimization potential:")
        print(f"  Steps 4-6 currently run sequentially: {format_duration(current_sequential)}")
        print(f"  If parallelized, could take:          {format_duration(potential_parallel)}")
        print(f"  Potential time savings:               {format_duration(current_sequential - potential_parallel)}")


def main():
    parser = argparse.ArgumentParser(
        description='Analyze karaoke-gen log files for timing information'
    )
    parser.add_argument('log_file', type=Path, help='Path to log file')
    parser.add_argument('--json', action='store_true', help='Output as JSON')
    parser.add_argument('--verbose', '-v', action='store_true', help='Show all detected events')

    args = parser.parse_args()

    if not args.log_file.exists():
        print(f"Error: Log file not found: {args.log_file}", file=sys.stderr)
        sys.exit(1)

    analysis = analyze_log(args.log_file, verbose=args.verbose)

    if args.json:
        print(json.dumps(analysis, indent=2))
    else:
        print_report(analysis)


if __name__ == '__main__':
    main()
