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
    # Capacity-fallback machine family, deliberately DIFFERENT from ENCODING_WORKER.
    # c4d-highcpu-32 suffered a region-wide ZONE_RESOURCE_POOL_EXHAUSTED stockout across
    # us-central1-a/-b/-c simultaneously (2026-08-12) which took out every same-family lane.
    # n2-highcpu-32 (Intel Cascade/Ice Lake) draws from a much deeper, independent pool, so a
    # c4d shortage cannot exhaust it. n2 does NOT support hyperdisk-balanced → uses pd-balanced.
    ENCODING_WORKER_ALT = "n2-highcpu-32"  # 32 vCPU, Intel - deep-capacity fallback pool
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
    """Configuration for the ephemeral GHA runner dispatcher Cloud Function."""

    FUNCTION_NAME = "github-runner-manager"
    FUNCTION_MEMORY = "512M"  # Increased from 256M due to memory usage
    FUNCTION_TIMEOUT = 300  # 5 minutes
    # Scheduler cadence for the orphan-cleanup pass (reconciles ephemeral VMs
    # against org-runner registrations, deletes stragglers).
    IDLE_CHECK_SCHEDULE = "*/15 * * * *"  # Every 15 minutes


class SecretNames:
    """Secret Manager secret names."""

    GITHUB_RUNNER_PAT = "github-runner-pat"
    GITHUB_WEBHOOK_SECRET = "github-webhook-secret"
    # Shared secret Cloudflare injects as a header on proxied requests; the
    # backend edge-auth middleware rejects requests to public routes that lack
    # it (blocks direct-to-origin bypass of the Cloudflare edge). See
    # modules/edge_security.py and backend/middleware/edge_auth.py.
    EDGE_ORIGIN_SECRET = "edge-origin-secret"


class CloudflareConfig:
    """
    Configuration for the Cloudflare edge (WAF / rate limiting / origin lock).

    Non-secret values (zone/account ids, hostnames) are read from Pulumi config
    so they are version-controlled per stack without hardcoding account details.
    The API token is a Pulumi *secret* config under the provider key
    ``cloudflare:apiToken`` (set with ``pulumi config set --secret``).

    Set the ids once known:
        pulumi config set edge:cloudflareZoneId    <zone-id-for-nomadkaraoke.com>
        pulumi config set edge:cloudflareAccountId <account-id>
        pulumi config set --secret cloudflare:apiToken <token>   # Zone WAF:Edit + DNS:Edit
    """

    # Production backend host (already a Cloud Run domain mapping today).
    PROD_API_HOST = "api.nomadkaraoke.com"
    # Throwaway staging host used to validate the full edge stack against the
    # SAME Cloud Run service before touching the prod `api` record. Torn down
    # after cutover. See docs/archive/2026-07-20-edge-security-hardening-plan.md.
    STAGING_API_HOST = "api-edge-test.nomadkaraoke.com"
    # Cloud Run domain mappings both target this service origin.
    DOMAIN_MAPPING_ROUTE = "karaoke-backend"
    ORIGIN_CNAME_TARGET = "ghs.googlehosted.com"

    # SSL/TLS NOTE (validated on staging 2026-07-20): the zone SSL mode is
    # "full" (not strict). In "full" mode Cloudflare encrypts to the origin but
    # does NOT validate the origin cert, so the Cloud Run domain-mapping managed
    # cert's provisioning/renewal status is irrelevant through the proxy — the
    # edge works even while that cert shows "pending". This sidesteps the 525
    # "dragon" (which only bites in "full (strict)") WITHOUT needing a run.app
    # Origin Rule. Keep the zone on "full" (never flip to strict without first
    # provisioning/renewing the managed cert, which cannot issue behind CF).

    # Header name Cloudflare injects and the backend checks (value = secret).
    ORIGIN_AUTH_HEADER = "X-Edge-Auth"

    # Rate limit: block a client IP that exceeds this many requests/period.
    # Free plan constraints (enforced by the Cloudflare API): period must be 10s,
    # and mitigation_timeout must equal the period (10s). A single flood control
    # rule — the WAF path block (below) is the primary defense against the
    # scanner class; this catches volumetric abuse. ~50 req / 10s = 5 req/s per
    # IP, comfortably above legit page-load bursts to a control-plane API.
    RATE_LIMIT_REQUESTS = 50
    RATE_LIMIT_PERIOD_SECONDS = 10
    RATE_LIMIT_MITIGATION_SECONDS = 10

    # Paths that must never be rate-limited / header-gated at the edge:
    # scheduler cron hits (OIDC-authed) to /api/internal/*.
    INTERNAL_PATH_PREFIX = "/api/internal/"

    @staticmethod
    def managed_waf_enabled():
        """
        Whether to deploy the Cloudflare Managed (OWASP-style) Ruleset.

        The nomadkaraoke.com zone is on the **Free** plan, where managed
        rulesets are NOT available (Pro+ only) — deploying one errors. So this
        defaults to False. The custom exploit-path rules + rate limiting +
        origin lock + bot mitigation (all Free-tier) already cover the
        path-scanning threat this was built for. If the zone is upgraded to
        Pro, set ``edge:managedWafEnabled true`` to turn on the OWASP ruleset.
        """
        return pulumi.Config("edge").get_bool("managedWafEnabled") or False

    # Cloudflare zone id for nomadkaraoke.com (not sensitive). Overridable via
    # Pulumi config `edge:cloudflareZoneId`; defaults to the known zone so the
    # edge module activates as soon as a WAF/DNS-scoped `cloudflare:apiToken`
    # is set. NOTE: the zone is on the **Free** plan (managed WAF ruleset is
    # Pro+; see managed_waf_enabled).
    DEFAULT_ZONE_ID = "807f07f458f9cd38251f3b7948d55172"

    @staticmethod
    def enabled():
        """
        Master activation switch for the Cloudflare edge module
        (``edge:enabled`` bool, default False).

        MUST stay False until a WAF/DNS-scoped ``cloudflare:apiToken`` is set —
        otherwise `pulumi up` would try to create Cloudflare resources with an
        unauthorized token and fail, blocking all infra deploys. Flip to True
        only after the token + config are in place (see the cutover runbook).
        """
        return pulumi.Config("edge").get_bool("enabled") or False

    @staticmethod
    def zone_id():
        """Cloudflare zone id for nomadkaraoke.com."""
        return pulumi.Config("edge").get("cloudflareZoneId") or CloudflareConfig.DEFAULT_ZONE_ID

    @staticmethod
    def account_id():
        """Cloudflare account id (from Pulumi config)."""
        return pulumi.Config("edge").get("cloudflareAccountId")

    @staticmethod
    def rollout_stage():
        """
        Which hosts the edge WAF/rate-limit/header rules apply to (from Pulumi
        config ``edge:rolloutStage``):
          - "staging" : rules scoped to the staging host only.
          - "prod"    : rules apply to the prod host too (staging kept for soak).
        Defaults to "staging" so a first apply is non-disruptive to prod.

        NOTE: this does NOT flip the prod DNS proxy — that's a SEPARATE switch
        (``proxy_prod_api``), so production edge rules can be provisioned and
        verified while the API is still DNS-only, then the proxy flipped in an
        isolated second apply (and rolled back without tearing down the rules).
        """
        stage = pulumi.Config("edge").get("rolloutStage") or "staging"
        return "prod" if stage in ("prod", "cutover") else "staging"

    @staticmethod
    def proxy_staging_api():
        """
        Whether the staging ``api-edge-test`` record is proxied (``edge:proxyStaging``
        bool, default False).

        THE SSL DRAGON: a Cloud Run domain mapping provisions its Google-managed
        cert via ACME, which needs the hostname to resolve DIRECTLY to
        ``ghs.googlehosted.com``. While the record is proxied (orange-cloud) DNS
        resolves to Cloudflare, so ACME can't validate and the cert never issues.
        Sequence: create grey (proxied=False) → wait for cert → set this True.
        """
        return pulumi.Config("edge").get_bool("proxyStaging") or False

    @staticmethod
    def proxy_prod_api():
        """
        Whether the prod ``api`` DNS record is proxied through Cloudflare
        (``edge:proxyProdApi`` bool, default False).

        This is the ONE prod-affecting, instantly-reversible flip. Sequence:
          1. rolloutStage=prod, apply  → prod edge rules live; api still DNS-only.
          2. proxyProdApi=true, apply   → api proxied (only this record changes).
        Rollback = proxyProdApi=false, apply (edge rules stay provisioned).
        """
        return pulumi.Config("edge").get_bool("proxyProdApi") or False

# GitHub repository for runner registration
GITHUB_REPO_OWNER = "nomadkaraoke"
GITHUB_REPO_NAME = "karaoke-gen"


class EncodingWorkerConfig:
    """Configuration for blue-green encoding worker VMs."""
    VM_NAMES = ["encoding-worker-a", "encoding-worker-b"]
    IP_NAMES = ["encoding-worker-ip-a", "encoding-worker-ip-b"]
    IDLE_CHECK_SCHEDULE = "*/5 * * * *"  # Every 5 minutes
    IDLE_TIMEOUT_MINUTES = 15
    FUNCTION_NAME = "encoding-worker-idle-shutdown"
    FUNCTION_MEMORY = "512M"  # Increased from 256M — OOM with gRPC/Firestore/Compute client libs
    FUNCTION_TIMEOUT = 120  # 2 minutes

    # Capacity-resilience fallback fleet. Each VM is provisioned STOPPED in an
    # alternate zone / machine family and is started on demand only when the
    # primary zone rejects a start with ZONE_RESOURCE_POOL_EXHAUSTED. Cost when
    # stopped is just the boot disk (~$10/mo each).
    #
    # Machine-family diversity is DELIBERATE. The primary pair and the two c4d
    # fallbacks are all c4d-highcpu-32, so a region-wide c4d stockout (observed
    # 2026-08-12 across us-central1-a/-b/-c at once) exhausts every lane
    # simultaneously and forces slow local encoding (→ 524 on preview, parked
    # renders). The n2-highcpu-32 fallbacks draw from an independent, much deeper
    # pool so a c4d shortage cannot take them out. n2 does not support
    # hyperdisk-balanced, hence pd-balanced boot disks on those entries.
    #
    # Each entry: name/IP suffix, zone suffix ({REGION}-{zone_suffix}),
    # machine_type, boot disk_type. ORDER MATTERS — IPs and VMs are zipped by
    # position, and it defines candidate priority. Keep the c4d a/b entries
    # first and byte-identical so Pulumi does not recreate the existing VMs.
    FALLBACKS = [
        {"suffix": "a", "zone_suffix": "a",
         "machine_type": MachineTypes.ENCODING_WORKER, "disk_type": "hyperdisk-balanced"},
        {"suffix": "b", "zone_suffix": "b",
         "machine_type": MachineTypes.ENCODING_WORKER, "disk_type": "hyperdisk-balanced"},
        {"suffix": "n2c", "zone_suffix": "c",
         "machine_type": MachineTypes.ENCODING_WORKER_ALT, "disk_type": "pd-balanced"},
        {"suffix": "n2f", "zone_suffix": "f",
         "machine_type": MachineTypes.ENCODING_WORKER_ALT, "disk_type": "pd-balanced"},
    ]

    # Derived name lists (kept for readability / any external reference).
    FALLBACK_VM_NAMES = [f"encoding-worker-fallback-{fb['suffix']}" for fb in FALLBACKS]
    FALLBACK_IP_NAMES = [f"encoding-worker-fallback-ip-{fb['suffix']}" for fb in FALLBACKS]

    @staticmethod
    def fallback_vm_name(suffix: str) -> str:
        return f"encoding-worker-fallback-{suffix}"

    @staticmethod
    def fallback_ip_name(suffix: str) -> str:
        return f"encoding-worker-fallback-ip-{suffix}"


class ErrorMonitorConfig:
    """Configuration for the error monitor Cloud Run Job."""
    JOB_NAME = "nomad-error-monitor"
    MEMORY = "512Mi"
    CPU = "1"
    TIMEOUT = "300s"
    MAX_RETRIES = 0
    MONITOR_SCHEDULE = "*/15 * * * *"  # Every 15 minutes UTC
    DIGEST_SCHEDULE = "0 8 * * *"  # 08:00 UTC daily
    SCHEDULER_SA_NAME = "error-monitor-scheduler"
