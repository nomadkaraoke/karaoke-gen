"""
Centralized configuration for Pulumi infrastructure.

This module provides shared constants and configuration used across all infrastructure modules.
"""

import pulumi
import pulumi_gcp as gcp

# GCP project info - lazily loaded to work with Pulumi runtime
_project = None


def _get_project():
    """Get GCP project info (lazy-loaded for Pulumi runtime compatibility)."""
    global _project
    if _project is None:
        _project = gcp.organizations.get_project()
    return _project


# Project ID and number are accessed as properties
# These will resolve correctly during Pulumi execution
PROJECT_ID = "nomadkaraoke"  # Hardcoded for now - matches existing behavior
PROJECT_NUMBER = None  # Set lazily when needed


def get_project_number() -> str:
    """Get the GCP project number."""
    return _get_project().number

# Region configuration
REGION = "us-central1"
ZONE = f"{REGION}-a"

# Encoding worker zone - uses us-central1-c due to c4d-highcpu-32 availability
# (us-central1-a and us-central1-b often lack capacity for high-end C4D instances)
ENCODING_WORKER_ZONE = f"{REGION}-c"

# Pulumi config accessor
config = pulumi.Config()


class MachineTypes:
    """Machine type configurations for GCE instances."""

    GITHUB_RUNNER = "e2-standard-4"  # 4 vCPU, 16GB RAM
    GITHUB_BUILD_RUNNER = "e2-standard-8"  # 8 vCPU, 32GB RAM - dedicated Docker build runner
    GITHUB_GPU_RUNNER = "n1-standard-4"  # 4 vCPU, 15GB RAM - GPU runners need N1 series
    ENCODING_WORKER = "c4d-highcpu-32"  # 32 vCPU, AMD EPYC 9B45 Turin - 4.92x faster than c4-standard-8
    FLACFETCH = "e2-small"  # 0.5 vCPU, 2GB RAM


class DiskSizes:
    """Disk size configurations in GB."""

    GITHUB_RUNNER = 200  # Large for Docker builds/caches
    ENCODING_WORKER = 100  # For temp video files during encoding
    FLACFETCH = 30  # For torrent storage


class QueueConfigs:
    """Cloud Tasks queue rate limiting configurations."""

    class Audio:
        MAX_DISPATCHES_PER_SECOND = 10
        MAX_CONCURRENT_DISPATCHES = 50
        MAX_RETRY_DURATION = "1800s"  # 30 min

    class Lyrics:
        MAX_DISPATCHES_PER_SECOND = 10
        MAX_CONCURRENT_DISPATCHES = 50
        MAX_RETRY_DURATION = "1200s"  # 20 min

    class Screens:
        MAX_DISPATCHES_PER_SECOND = 50
        MAX_CONCURRENT_DISPATCHES = 100
        MAX_RETRY_DURATION = "300s"  # 5 min

    class Render:
        MAX_DISPATCHES_PER_SECOND = 5
        MAX_CONCURRENT_DISPATCHES = 20
        MAX_RETRY_DURATION = "3600s"  # 60 min

    class Video:
        MAX_DISPATCHES_PER_SECOND = 3
        MAX_CONCURRENT_DISPATCHES = 10
        MAX_RETRY_DURATION = "7200s"  # 2 hours


# Number of self-hosted GitHub Action runners
# Reduced from 20 to 3 - runners auto-scale with start/stop management
NUM_GITHUB_RUNNERS = 3
NUM_GPU_RUNNERS = 3

# Runner labels
GENERAL_RUNNER_LABELS = "self-hosted,linux,x64,gcp,large-disk"
BUILD_RUNNER_LABELS = "self-hosted,linux,x64,gcp,large-disk,docker-build"
GPU_RUNNER_LABELS = "self-hosted,linux,x64,gcp,gpu"

# Google Drive folder ID for validator (public share folder)
GDRIVE_FOLDER_ID = "1laRKAyxo0v817SstfM5XkpbWiNKNAMSX"


class RunnerManagerConfig:
    """Configuration for GitHub runner auto-start/stop management."""
    # Auto-scales runners: starts on CI demand, stops after idle timeout

    FUNCTION_NAME = "github-runner-manager"
    FUNCTION_MEMORY = "512M"  # Increased from 256M due to memory usage
    FUNCTION_TIMEOUT = 300  # 5 minutes
    IDLE_TIMEOUT_HOURS = 1  # Shorter than Aquarius (3h) since we have more CI activity
    IDLE_CHECK_SCHEDULE = "*/15 * * * *"  # Every 15 minutes


class SecretNames:
    """Secret Manager secret names."""

    GITHUB_RUNNER_PAT = "github-runner-pat"
    GITHUB_WEBHOOK_SECRET = "github-webhook-secret"

# GitHub repository for runner registration
GITHUB_REPO_OWNER = "nomadkaraoke"
GITHUB_REPO_NAME = "karaoke-gen"
