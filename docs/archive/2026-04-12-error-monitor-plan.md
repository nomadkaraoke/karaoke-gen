# Nomad Karaoke Error Monitor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production error monitoring service that queries Cloud Logging every 15 minutes, normalizes/deduplicates errors, uses Gemini Flash for incident grouping, sends Discord alerts, and tracks patterns in Firestore.

**Architecture:** Cloud Run Job sharing the karaoke-backend Docker image with command override. Firestore for pattern/incident persistence. Gemini 2.0 Flash via Vertex AI for LLM dedup and incident grouping. Discord webhook for alerts. Cloud Scheduler for 15-min trigger and daily digest.

**Tech Stack:** Python 3.12, google-cloud-logging, google-cloud-firestore, google-generativeai (Vertex AI), requests, Pulumi (GCP infrastructure)

**Design Spec:** `docs/archive/2026-04-12-error-monitor-design.md`

**Aquarius Reference:** `/Users/andrew/Projects/aquarius/docs/archive/error-bot-system-reference.md`

---

## File Structure

### New Files

```
backend/services/error_monitor/
├── __init__.py              # Package init, version
├── config.py                # All configuration constants
├── normalizer.py            # Message normalization (regex replacements)
├── known_issues.py          # Ignore pattern matching
├── firestore_adapter.py     # CRUD for error_patterns, incidents, alerts
├── discord.py               # Discord webhook client for alerts
├── llm_analysis.py          # Vertex AI Gemini incident grouping + dedup
└── monitor.py               # Entry point — orchestrates full pipeline

scripts/
├── query-error-patterns.py  # CLI to query patterns from Firestore
└── resolve-error-pattern.py # CLI to mark patterns as fixed

infrastructure/modules/
└── error_monitor.py         # Pulumi: Cloud Run Job + Cloud Schedulers

tests/unit/services/error_monitor/
├── __init__.py
├── test_config.py
├── test_normalizer.py
├── test_known_issues.py
├── test_firestore_adapter.py
├── test_discord.py
├── test_llm_analysis.py
└── test_monitor.py
```

### Modified Files

```
infrastructure/__main__.py       # Wire in error_monitor module
infrastructure/config.py         # Add ErrorMonitorConfig class
infrastructure/modules/iam/backend_sa.py  # Add roles/logging.viewer
pyproject.toml                   # Add google-cloud-logging dependency
```

### Slash Commands (workspace level, updated)

```
/Users/andrew/Projects/nomadkaraoke/.claude/commands/
├── prod-errors.md          # Update to use error_patterns as primary source
├── prod-health.md          # Add error pattern summary
├── prod-investigate.md     # Query error_patterns first
├── prod-review.md          # Replace Cloud Logging categorization with pattern statuses
└── prod-known-issue.md     # Write to Firestore instead of YAML
```

---

## Task 1: Configuration & Dependencies

**Files:**
- Create: `backend/services/error_monitor/__init__.py`
- Create: `backend/services/error_monitor/config.py`
- Create: `tests/unit/services/error_monitor/__init__.py`
- Create: `tests/unit/services/error_monitor/test_config.py`
- Modify: `pyproject.toml` (add google-cloud-logging)

- [ ] **Step 1: Add google-cloud-logging dependency**

In `pyproject.toml`, add to `[tool.poetry.dependencies]`:

```
google-cloud-logging = ">=3.9.0"
```

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-error-bot && poetry lock --no-update`

- [ ] **Step 2: Create package init**

Create `backend/services/error_monitor/__init__.py`:

```python
"""Production error monitoring service for Nomad Karaoke."""
```

Create `tests/unit/services/error_monitor/__init__.py`:

```python
```

- [ ] **Step 3: Write config tests**

Create `tests/unit/services/error_monitor/test_config.py`:

```python
"""Tests for error monitor configuration."""

import os
from unittest.mock import patch

from backend.services.error_monitor.config import (
    MONITORED_CLOUD_RUN_SERVICES,
    MONITORED_CLOUD_RUN_JOBS,
    MONITORED_CLOUD_FUNCTIONS,
    MONITORED_GCE_INSTANCES,
    GCP_PROJECT,
    LOOKBACK_MINUTES,
    MAX_LOG_ENTRIES,
    SPIKE_MULTIPLIER,
    SPIKE_MIN_COUNT,
    AUTO_RESOLVE_MULTIPLIER,
    AUTO_RESOLVE_MIN_HOURS,
    AUTO_RESOLVE_MAX_HOURS,
    AUTO_RESOLVE_FALLBACK_HOURS,
    MAX_DISCORD_MESSAGES_PER_RUN,
    MAX_ACTIVE_PATTERNS,
    ROLLING_WINDOW_DAYS,
    get_llm_enabled,
    get_discord_webhook_secret_name,
)


class TestMonitoredServices:
    def test_cloud_run_services_are_defined(self):
        assert "karaoke-backend" in MONITORED_CLOUD_RUN_SERVICES
        assert "karaoke-decide" in MONITORED_CLOUD_RUN_SERVICES
        assert "audio-separator" in MONITORED_CLOUD_RUN_SERVICES
        assert len(MONITORED_CLOUD_RUN_SERVICES) == 3

    def test_cloud_run_jobs_are_defined(self):
        assert "video-encoding-job" in MONITORED_CLOUD_RUN_JOBS
        assert "lyrics-transcription-job" in MONITORED_CLOUD_RUN_JOBS
        assert "audio-separation-job" in MONITORED_CLOUD_RUN_JOBS
        assert "audio-download-job" in MONITORED_CLOUD_RUN_JOBS
        assert len(MONITORED_CLOUD_RUN_JOBS) == 4

    def test_cloud_functions_are_defined(self):
        assert "gdrive-validator" in MONITORED_CLOUD_FUNCTIONS
        assert "runner_manager" in MONITORED_CLOUD_FUNCTIONS
        assert len(MONITORED_CLOUD_FUNCTIONS) >= 7

    def test_gce_instances_are_defined(self):
        assert "encoding-worker-a" in MONITORED_GCE_INSTANCES
        assert "flacfetch-vm" in MONITORED_GCE_INSTANCES
        assert len(MONITORED_GCE_INSTANCES) >= 4


class TestDefaults:
    def test_gcp_project(self):
        assert GCP_PROJECT == "nomadkaraoke"

    def test_lookback_minutes(self):
        assert LOOKBACK_MINUTES == 15

    def test_max_log_entries(self):
        assert MAX_LOG_ENTRIES == 500

    def test_spike_settings(self):
        assert SPIKE_MULTIPLIER == 5.0
        assert SPIKE_MIN_COUNT == 5

    def test_auto_resolve_settings(self):
        assert AUTO_RESOLVE_MULTIPLIER == 8
        assert AUTO_RESOLVE_MIN_HOURS == 6
        assert AUTO_RESOLVE_MAX_HOURS == 168
        assert AUTO_RESOLVE_FALLBACK_HOURS == 48

    def test_discord_limits(self):
        assert MAX_DISCORD_MESSAGES_PER_RUN == 10

    def test_rolling_window(self):
        assert ROLLING_WINDOW_DAYS == 7


class TestEnvOverrides:
    @patch.dict(os.environ, {"LLM_ANALYSIS_ENABLED": "false"})
    def test_llm_disabled_via_env(self):
        assert get_llm_enabled() is False

    @patch.dict(os.environ, {"LLM_ANALYSIS_ENABLED": "true"})
    def test_llm_enabled_via_env(self):
        assert get_llm_enabled() is True

    def test_llm_enabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove the key if present
            os.environ.pop("LLM_ANALYSIS_ENABLED", None)
            assert get_llm_enabled() is True
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-error-bot && python -m pytest tests/unit/services/error_monitor/test_config.py -v`

Expected: ImportError — `backend.services.error_monitor.config` does not exist yet.

- [ ] **Step 5: Implement config module**

Create `backend/services/error_monitor/config.py`:

```python
"""Configuration for the error monitor service."""

import os

# GCP
GCP_PROJECT = os.environ.get("GCP_PROJECT_ID", "nomadkaraoke")
GCP_REGION = os.environ.get("GCP_REGION", "us-central1")

# Log collection
LOOKBACK_MINUTES = 15
MAX_LOG_ENTRIES = 500

# Monitored resources
MONITORED_CLOUD_RUN_SERVICES = [
    "karaoke-backend",
    "karaoke-decide",
    "audio-separator",
]

MONITORED_CLOUD_RUN_JOBS = [
    "video-encoding-job",
    "lyrics-transcription-job",
    "audio-separation-job",
    "audio-download-job",
]

MONITORED_CLOUD_FUNCTIONS = [
    "gdrive-validator",
    "runner_manager",
    "backup_to_aws",
    "divebar_mirror",
    "kn_data_sync",
    "divebar_lookup",
    "encoding_worker_idle",
]

MONITORED_GCE_INSTANCES = [
    "encoding-worker-a",
    "encoding-worker-b",
    "flacfetch-vm",
    "divebar-sync-vm",
]

# Service dependency map (for LLM system prompt)
SERVICE_DEPENDENCY_MAP = """
karaoke-backend (API) → orchestrates all workers
  ├── audio-download-job → flacfetch-vm (downloads audio)
  ├── audio-separation-job / audio-separator (GPU stem separation)
  ├── lyrics-transcription-job (AudioShake + AI correction)
  ├── video-encoding-job → encoding-worker-a/b (FFmpeg rendering)
  └── youtube-upload (distribution)

karaoke-decide (independent) → BigQuery, Spotify, Last.fm

Cloud Functions: gdrive-validator, backup_to_aws, divebar_mirror,
                 kn_data_sync, divebar_lookup, runner_manager,
                 encoding_worker_idle
""".strip()

# LLM analysis
LLM_ANALYSIS_MODEL = os.environ.get("LLM_ANALYSIS_MODEL", "gemini-2.0-flash")
LLM_VERTEX_LOCATION = os.environ.get("LLM_VERTEX_LOCATION", "us-central1")
MIN_PATTERNS_FOR_ANALYSIS = int(os.environ.get("MIN_PATTERNS_FOR_ANALYSIS", "2"))

# Spike detection
SPIKE_MULTIPLIER = 5.0
SPIKE_MIN_COUNT = 5

# Auto-resolution
AUTO_RESOLVE_MULTIPLIER = 8
AUTO_RESOLVE_MIN_HOURS = 6
AUTO_RESOLVE_MAX_HOURS = 168  # 1 week
AUTO_RESOLVE_FALLBACK_HOURS = 48

# Discord
MAX_DISCORD_MESSAGES_PER_RUN = 10
DISCORD_MAX_MESSAGE_LENGTH = 2000

# Pattern tracking
MAX_ACTIVE_PATTERNS = 500
ROLLING_WINDOW_DAYS = 7
MAX_NORMALIZED_MESSAGE_LENGTH = 200


def get_llm_enabled() -> bool:
    """Check if LLM analysis is enabled (default: True)."""
    return os.environ.get("LLM_ANALYSIS_ENABLED", "true").lower() == "true"


def get_discord_webhook_secret_name() -> str:
    """Get the Secret Manager secret name for the Discord webhook."""
    return os.environ.get("DISCORD_WEBHOOK_SECRET", "discord-alert-webhook")


def get_digest_mode() -> bool:
    """Check if running in daily digest mode."""
    return os.environ.get("DIGEST_MODE", "false").lower() == "true"
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-error-bot && python -m pytest tests/unit/services/error_monitor/test_config.py -v`

Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/services/error_monitor/__init__.py backend/services/error_monitor/config.py tests/unit/services/error_monitor/__init__.py tests/unit/services/error_monitor/test_config.py pyproject.toml
git commit -m "feat(error-monitor): add config module and dependencies"
```

---

## Task 2: Message Normalizer

**Files:**
- Create: `backend/services/error_monitor/normalizer.py`
- Create: `tests/unit/services/error_monitor/test_normalizer.py`

- [ ] **Step 1: Write normalizer tests**

Create `tests/unit/services/error_monitor/test_normalizer.py`:

```python
"""Tests for error message normalization."""

from backend.services.error_monitor.normalizer import normalize_message, compute_pattern_hash


class TestNormalizeMessage:
    def test_replaces_uuids(self):
        msg = "Failed to process job 550e8400-e29b-41d4-a716-446655440000"
        assert "<ID>" in normalize_message(msg)
        assert "550e8400" not in normalize_message(msg)

    def test_replaces_iso_timestamps(self):
        msg = "Error at 2026-04-10T14:30:00.123Z in handler"
        assert "<TS>" in normalize_message(msg)
        assert "2026-04-10" not in normalize_message(msg)

    def test_replaces_epoch_timestamps(self):
        msg = "Timeout after 1712345678.123 seconds elapsed"
        result = normalize_message(msg)
        assert "<EPOCH>" in result

    def test_replaces_emails(self):
        msg = "Auth failed for user@example.com"
        assert "<EMAIL>" in normalize_message(msg)
        assert "user@example.com" not in normalize_message(msg)

    def test_replaces_ip_addresses(self):
        msg = "Connection refused from 192.168.1.100"
        assert "<IP>" in normalize_message(msg)

    def test_replaces_hex_ids(self):
        msg = "Document abc123def456 not found"
        assert "<ID>" in normalize_message(msg)

    def test_replaces_large_numbers(self):
        msg = "Request took 12345 ms"
        assert "<NUM>" in normalize_message(msg)

    def test_preserves_small_numbers(self):
        msg = "Error code 404"
        result = normalize_message(msg)
        assert "404" in result

    def test_replaces_job_ids_in_paths(self):
        msg = "Failed /jobs/abc123def456ghi789 processing"
        result = normalize_message(msg)
        assert "/jobs/<ID>" in result

    def test_replaces_gcs_paths(self):
        msg = "File not found: gs://nomadkaraoke-prod/audio/abc123/vocals.wav"
        result = normalize_message(msg)
        assert "gs://<BUCKET>/<PATH>" in result

    def test_replaces_firebase_uids(self):
        msg = "User XLsws9aOZ5hJA0hJHQ7ymw5x4pw2 not found"
        result = normalize_message(msg)
        assert "<UID>" in result

    def test_replaces_firestore_doc_paths(self):
        msg = "Error reading jobs/abc123def/state_data/current"
        result = normalize_message(msg)
        assert "<DOC_PATH>" in result

    def test_truncates_long_messages(self):
        msg = "x" * 300
        result = normalize_message(msg)
        assert len(result) <= 200

    def test_strips_urls_to_domain(self):
        msg = "Failed to reach https://api.audioshake.ai/v1/transcribe?key=abc123"
        result = normalize_message(msg)
        assert "api.audioshake.ai" in result
        assert "key=abc123" not in result

    def test_deterministic(self):
        msg = "Error processing job 550e8400-e29b-41d4-a716-446655440000 at 2026-04-10T14:30:00Z"
        assert normalize_message(msg) == normalize_message(msg)

    def test_empty_message(self):
        assert normalize_message("") == ""

    def test_no_replacements_needed(self):
        msg = "Simple error message"
        assert normalize_message(msg) == "Simple error message"


class TestComputePatternHash:
    def test_returns_hex_string(self):
        result = compute_pattern_hash("karaoke-backend", "Some error")
        assert len(result) == 64  # SHA-256 hex
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        h1 = compute_pattern_hash("karaoke-backend", "Error X")
        h2 = compute_pattern_hash("karaoke-backend", "Error X")
        assert h1 == h2

    def test_different_services_different_hash(self):
        h1 = compute_pattern_hash("karaoke-backend", "Error X")
        h2 = compute_pattern_hash("karaoke-decide", "Error X")
        assert h1 != h2

    def test_different_messages_different_hash(self):
        h1 = compute_pattern_hash("karaoke-backend", "Error X")
        h2 = compute_pattern_hash("karaoke-backend", "Error Y")
        assert h1 != h2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-error-bot && python -m pytest tests/unit/services/error_monitor/test_normalizer.py -v`

Expected: ImportError.

- [ ] **Step 3: Implement normalizer**

Create `backend/services/error_monitor/normalizer.py`:

```python
"""Error message normalization for pattern deduplication.

Replaces variable parts (IDs, timestamps, emails, etc.) with placeholders
so the same logical error produces the same pattern hash.
"""

import hashlib
import re

from backend.services.error_monitor.config import MAX_NORMALIZED_MESSAGE_LENGTH

# Order matters — more specific patterns first to prevent partial matches.
_NORMALIZERS: list[tuple[re.Pattern, str]] = [
    # GCS paths (before general URLs)
    (re.compile(r"gs://[a-zA-Z0-9._-]+/[^\s]+"), "gs://<BUCKET>/<PATH>"),
    # Firestore document paths (collections/docId/subcollections/docId)
    (re.compile(r"(?<!\w)(?:[a-z_]+/[a-zA-Z0-9_-]{8,}){2,}(?!\w)"), "<DOC_PATH>"),
    # UUIDs
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I), "<ID>"),
    # ISO timestamps (with optional fractional seconds and timezone)
    (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<TS>"),
    # Epoch timestamps (10+ digit float)
    (re.compile(r"\b\d{10,13}\.\d+\b"), "<EPOCH>"),
    # Email addresses
    (re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), "<EMAIL>"),
    # IP addresses
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "<IP>"),
    # URLs — strip to domain only
    (re.compile(r"https?://([a-zA-Z0-9.-]+)[^\s]*"), r"\1"),
    # Job IDs in paths (before general hex IDs)
    (re.compile(r"/jobs/[a-zA-Z0-9_-]{8,}"), "/jobs/<ID>"),
    # Firebase UIDs (20-28 alphanumeric chars, typically mixed case)
    (re.compile(r"\b[a-zA-Z0-9]{20,28}\b"), "<UID>"),
    # Hex/alphanumeric IDs (8+ chars with at least one digit and one letter)
    (re.compile(r"\b(?=[a-f0-9]*[a-f])(?=[a-f0-9]*[0-9])[a-f0-9]{8,}\b", re.I), "<ID>"),
    # Large numbers (4+ digits)
    (re.compile(r"\b\d{4,}\b"), "<NUM>"),
]


def normalize_message(message: str) -> str:
    """Normalize an error message by replacing variable parts with placeholders.

    Returns the normalized message, truncated to MAX_NORMALIZED_MESSAGE_LENGTH.
    """
    if not message:
        return ""

    result = message
    for pattern, replacement in _NORMALIZERS:
        result = pattern.sub(replacement, result)

    if len(result) > MAX_NORMALIZED_MESSAGE_LENGTH:
        result = result[:MAX_NORMALIZED_MESSAGE_LENGTH]

    return result


def compute_pattern_hash(service: str, normalized_message: str) -> str:
    """Compute a deterministic hash for a service::message pattern.

    Returns a 64-char lowercase hex SHA-256 digest.
    """
    key = f"{service}::{normalized_message}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-error-bot && python -m pytest tests/unit/services/error_monitor/test_normalizer.py -v`

Expected: All tests PASS. If any regex tests fail, adjust the regex patterns and re-run until all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/services/error_monitor/normalizer.py tests/unit/services/error_monitor/test_normalizer.py
git commit -m "feat(error-monitor): add message normalizer with NK-specific patterns"
```

---

## Task 3: Known Issues / Ignore Patterns

**Files:**
- Create: `backend/services/error_monitor/known_issues.py`
- Create: `tests/unit/services/error_monitor/test_known_issues.py`

- [ ] **Step 1: Write known issues tests**

Create `tests/unit/services/error_monitor/test_known_issues.py`:

```python
"""Tests for known issue / ignore pattern matching."""

from backend.services.error_monitor.known_issues import should_ignore, IgnoreReason


class TestShouldIgnore:
    def test_ignores_cloud_run_startup_probe(self):
        result = should_ignore(
            service="karaoke-backend",
            message="Startup probe failed: HTTP probe failed with statuscode: 503",
        )
        assert result is not None
        assert isinstance(result, IgnoreReason)
        assert "startup" in result.reason.lower()

    def test_ignores_ready_condition_change(self):
        result = should_ignore(
            service="karaoke-backend",
            message="Ready condition status changed to True",
        )
        assert result is not None

    def test_ignores_spot_vm_preemption(self):
        result = should_ignore(
            service="github-runner-1",
            message="Instance was preempted",
        )
        assert result is not None

    def test_ignores_encoding_worker_idle_shutdown(self):
        result = should_ignore(
            service="encoding_worker_idle",
            message="Stopping idle encoding worker encoding-worker-a",
        )
        assert result is not None

    def test_ignores_health_check_404(self):
        result = should_ignore(
            service="karaoke-backend",
            message='GET /health returned 404 "Not Found"',
        )
        assert result is not None

    def test_does_not_ignore_real_errors(self):
        result = should_ignore(
            service="karaoke-backend",
            message="Firestore transaction failed: DEADLINE_EXCEEDED",
        )
        assert result is None

    def test_does_not_ignore_unknown_service(self):
        result = should_ignore(
            service="karaoke-backend",
            message="NullPointerException in processJob",
        )
        assert result is None

    def test_case_insensitive_matching(self):
        result = should_ignore(
            service="karaoke-backend",
            message="STARTUP PROBE FAILED: connection refused",
        )
        assert result is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-error-bot && python -m pytest tests/unit/services/error_monitor/test_known_issues.py -v`

Expected: ImportError.

- [ ] **Step 3: Implement known issues module**

Create `backend/services/error_monitor/known_issues.py`:

```python
"""Ignore patterns for infrastructure noise that should never trigger alerts."""

import re
from dataclasses import dataclass


@dataclass
class IgnoreReason:
    """Why a log entry was ignored."""
    pattern_name: str
    reason: str


# Each entry: (pattern_name, compiled_regex, reason)
# Regex is matched against the full message, case-insensitive.
_IGNORE_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    (
        "startup_probe",
        re.compile(r"startup probe failed", re.I),
        "Startup probe failure during Cloud Run cold start (transient)",
    ),
    (
        "ready_condition",
        re.compile(r"ready condition status changed", re.I),
        "Ready condition change during deploy (expected)",
    ),
    (
        "spot_preemption",
        re.compile(r"instance was preempted", re.I),
        "Spot VM preemption (expected for GitHub runners)",
    ),
    (
        "idle_shutdown",
        re.compile(r"stopping idle encoding worker", re.I),
        "Encoding worker idle shutdown (expected behavior)",
    ),
    (
        "health_check_404",
        re.compile(r"(?:GET|HEAD)\s+/health.*(?:404|not found)", re.I),
        "Health check 404 from load balancer (transient during deploys)",
    ),
    (
        "scheduler_retry",
        re.compile(r"cloud scheduler.*retry", re.I),
        "Cloud Scheduler retry noise (transient)",
    ),
    (
        "runner_startup",
        re.compile(r"github.runner.*(?:starting|stopping|idle)", re.I),
        "GitHub runner lifecycle event (expected)",
    ),
    (
        "container_shutdown",
        re.compile(r"container called exit\(0\)", re.I),
        "Clean container shutdown (not an error)",
    ),
]


def should_ignore(service: str, message: str) -> IgnoreReason | None:
    """Check if a log entry should be ignored.

    Returns an IgnoreReason if the message matches a known ignore pattern,
    or None if it should be processed.
    """
    for pattern_name, regex, reason in _IGNORE_PATTERNS:
        if regex.search(message):
            return IgnoreReason(pattern_name=pattern_name, reason=reason)
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-error-bot && python -m pytest tests/unit/services/error_monitor/test_known_issues.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/error_monitor/known_issues.py tests/unit/services/error_monitor/test_known_issues.py
git commit -m "feat(error-monitor): add ignore patterns for infrastructure noise"
```

---

## Task 4: Firestore Adapter

**Files:**
- Create: `backend/services/error_monitor/firestore_adapter.py`
- Create: `tests/unit/services/error_monitor/test_firestore_adapter.py`

- [ ] **Step 1: Write Firestore adapter tests**

Create `tests/unit/services/error_monitor/test_firestore_adapter.py`:

```python
"""Tests for Firestore adapter (error_patterns, incidents, alerts)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest

from backend.services.error_monitor.firestore_adapter import (
    ErrorPatternsAdapter,
    PatternData,
)


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def adapter(mock_db):
    return ErrorPatternsAdapter(db=mock_db)


class TestGetPattern:
    def test_returns_none_for_missing_pattern(self, adapter, mock_db):
        mock_db.collection.return_value.document.return_value.get.return_value.exists = False
        result = adapter.get_pattern("nonexistent_hash")
        assert result is None

    def test_returns_pattern_data_for_existing(self, adapter, mock_db):
        doc_mock = MagicMock()
        doc_mock.exists = True
        doc_mock.to_dict.return_value = {
            "pattern_id": "abc123",
            "service": "karaoke-backend",
            "status": "new",
            "total_count": 5,
        }
        mock_db.collection.return_value.document.return_value.get.return_value = doc_mock
        result = adapter.get_pattern("abc123")
        assert result is not None
        assert result["pattern_id"] == "abc123"


class TestUpsertPattern:
    def test_creates_new_pattern(self, adapter, mock_db):
        doc_mock = MagicMock()
        doc_mock.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = doc_mock

        adapter.upsert_pattern(PatternData(
            pattern_id="hash1",
            service="karaoke-backend",
            resource_type="cloud_run_service",
            normalized_message="Error in <ID>",
            sample_message="Error in abc123",
            count=3,
            timestamp=datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc),
        ))

        mock_db.collection.return_value.document.return_value.set.assert_called_once()
        call_args = mock_db.collection.return_value.document.return_value.set.call_args
        data = call_args[0][0]
        assert data["pattern_id"] == "hash1"
        assert data["status"] == "new"
        assert data["total_count"] == 3

    def test_updates_existing_pattern_counts(self, adapter, mock_db):
        doc_mock = MagicMock()
        doc_mock.exists = True
        doc_mock.to_dict.return_value = {
            "pattern_id": "hash1",
            "service": "karaoke-backend",
            "status": "acknowledged",
            "total_count": 10,
            "rolling_counts": {},
        }
        mock_db.collection.return_value.document.return_value.get.return_value = doc_mock

        adapter.upsert_pattern(PatternData(
            pattern_id="hash1",
            service="karaoke-backend",
            resource_type="cloud_run_service",
            normalized_message="Error in <ID>",
            sample_message="Error in xyz789",
            count=5,
            timestamp=datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc),
        ))

        mock_db.collection.return_value.document.return_value.update.assert_called_once()

    def test_reactivates_auto_resolved_pattern(self, adapter, mock_db):
        doc_mock = MagicMock()
        doc_mock.exists = True
        doc_mock.to_dict.return_value = {
            "pattern_id": "hash1",
            "status": "auto_resolved",
            "total_count": 10,
            "rolling_counts": {},
        }
        mock_db.collection.return_value.document.return_value.get.return_value = doc_mock

        result = adapter.upsert_pattern(PatternData(
            pattern_id="hash1",
            service="karaoke-backend",
            resource_type="cloud_run_service",
            normalized_message="Error in <ID>",
            sample_message="Error again",
            count=1,
            timestamp=datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc),
        ))

        assert result.is_new is True  # Treat as new since it was resolved


class TestGetActivePatterns:
    def test_returns_active_patterns(self, adapter, mock_db):
        doc1 = MagicMock()
        doc1.to_dict.return_value = {"pattern_id": "a", "status": "new"}
        doc2 = MagicMock()
        doc2.to_dict.return_value = {"pattern_id": "b", "status": "acknowledged"}

        mock_query = MagicMock()
        mock_query.stream.return_value = [doc1, doc2]
        mock_db.collection.return_value.where.return_value.where.return_value = mock_query

        result = adapter.get_active_patterns()
        assert len(result) == 2


class TestAutoResolve:
    def test_computes_threshold_from_rolling_counts(self, adapter):
        rolling_counts = {
            "2026-04-12T09:00": 2,
            "2026-04-12T09:15": 3,
            "2026-04-12T09:30": 1,
            "2026-04-12T09:45": 4,
        }
        threshold = adapter._compute_resolve_threshold(rolling_counts)
        # Mean interval = ~15 min = 0.25 hours
        # threshold = 0.25 * 8 = 2 hours, clamped to min 6
        assert threshold >= 6
        assert threshold <= 168


class TestCreateIncident:
    def test_creates_incident_document(self, adapter, mock_db):
        adapter.create_incident(
            title="Audio separation failures",
            root_cause="GPU OOM on large files",
            severity="P1",
            suggested_fix="Increase GPU memory limit",
            primary_service="audio-separator",
            pattern_ids=["hash1", "hash2"],
            used_llm=True,
        )

        mock_db.collection.return_value.document.return_value.set.assert_called_once()
        call_args = mock_db.collection.return_value.document.return_value.set.call_args
        data = call_args[0][0]
        assert data["title"] == "Audio separation failures"
        assert data["severity"] == "P1"
        assert len(data["pattern_ids"]) == 2
        assert data["status"] == "open"


class TestMergePattern:
    def test_marks_source_as_muted(self, adapter, mock_db):
        adapter.merge_pattern("source_hash", "target_hash", "Same error, different phrasing")
        mock_db.collection.return_value.document.return_value.update.assert_called_once()
        call_args = mock_db.collection.return_value.document.return_value.update.call_args
        data = call_args[0][0]
        assert data["status"] == "muted"
        assert data["merged_into"] == "target_hash"
        assert "Same error" in data["merged_reason"]


class TestUpdatePatternAlerted:
    def test_sets_alerted_timestamp(self, adapter, mock_db):
        adapter.update_pattern_alerted("hash1")
        mock_db.collection.return_value.document.return_value.update.assert_called_once()
        call_args = mock_db.collection.return_value.document.return_value.update.call_args
        data = call_args[0][0]
        assert "alerted_at" in data
        assert data["alerted_at"] is not None


class TestLogDiscordAlert:
    def test_logs_alert_to_firestore(self, adapter, mock_db):
        adapter.log_discord_alert(
            alert_type="new_pattern",
            content="🔴 New error in karaoke-backend...",
            success=True,
            metadata={"service": "karaoke-backend", "pattern_id": "hash1"},
        )

        mock_db.collection.return_value.document.return_value.set.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-error-bot && python -m pytest tests/unit/services/error_monitor/test_firestore_adapter.py -v`

Expected: ImportError.

- [ ] **Step 3: Implement Firestore adapter**

Create `backend/services/error_monitor/firestore_adapter.py`:

```python
"""Firestore adapter for error_patterns, error_incidents, and discord_alerts."""

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from google.cloud import firestore

from backend.services.error_monitor.config import (
    AUTO_RESOLVE_FALLBACK_HOURS,
    AUTO_RESOLVE_MAX_HOURS,
    AUTO_RESOLVE_MIN_HOURS,
    AUTO_RESOLVE_MULTIPLIER,
    ROLLING_WINDOW_DAYS,
)

logger = logging.getLogger(__name__)

PATTERNS_COLLECTION = "error_patterns"
INCIDENTS_COLLECTION = "error_incidents"
ALERTS_COLLECTION = "discord_alerts"

# Statuses that count as "active" (not resolved/fixed)
ACTIVE_STATUSES = ["new", "acknowledged", "known"]

# Statuses that should be treated as "new" when seen again
REACTIVATE_STATUSES = ["auto_resolved", "fixed"]


@dataclass
class PatternData:
    """Input data for upserting a pattern."""
    pattern_id: str
    service: str
    resource_type: str
    normalized_message: str
    sample_message: str
    count: int
    timestamp: datetime


@dataclass
class UpsertResult:
    """Result of upserting a pattern."""
    pattern_id: str
    is_new: bool
    previous_status: str | None = None


class ErrorPatternsAdapter:
    """Firestore CRUD for error patterns, incidents, and alerts."""

    def __init__(self, db: firestore.Client | None = None):
        self.db = db or firestore.Client(
            project=os.environ.get("GCP_PROJECT_ID", "nomadkaraoke")
        )

    def get_pattern(self, pattern_id: str) -> dict | None:
        """Get a single pattern by ID. Returns None if not found."""
        doc = self.db.collection(PATTERNS_COLLECTION).document(pattern_id).get()
        if doc.exists:
            return doc.to_dict()
        return None

    def upsert_pattern(self, data: PatternData) -> UpsertResult:
        """Create or update an error pattern.

        Returns UpsertResult indicating whether this is a new pattern.
        A pattern is considered "new" if it didn't exist before, or if
        it was previously auto_resolved/fixed and has reappeared.
        """
        doc_ref = self.db.collection(PATTERNS_COLLECTION).document(data.pattern_id)
        doc = doc_ref.get()
        ts_iso = data.timestamp.isoformat()
        window_key = data.timestamp.strftime("%Y-%m-%dT%H:%M")

        if not doc.exists:
            doc_ref.set({
                "pattern_id": data.pattern_id,
                "service": data.service,
                "resource_type": data.resource_type,
                "normalized_message": data.normalized_message,
                "sample_message": data.sample_message,
                "first_seen": ts_iso,
                "last_seen": ts_iso,
                "total_count": data.count,
                "rolling_counts": {window_key: data.count},
                "status": "new",
                "severity": "P3",  # Default, upgraded by LLM analysis
                "alerted_at": None,
                "incident_id": None,
                "notes": None,
                "merged_into": None,
                "fixed_by": None,
            })
            return UpsertResult(pattern_id=data.pattern_id, is_new=True)

        existing = doc.to_dict()
        previous_status = existing.get("status", "new")
        is_reactivation = previous_status in REACTIVATE_STATUSES

        rolling_counts = existing.get("rolling_counts", {})
        rolling_counts[window_key] = rolling_counts.get(window_key, 0) + data.count
        self._prune_rolling_counts(rolling_counts)

        update_data = {
            "last_seen": ts_iso,
            "total_count": existing.get("total_count", 0) + data.count,
            "rolling_counts": rolling_counts,
            "sample_message": data.sample_message,  # Keep latest sample
        }

        if is_reactivation:
            update_data["status"] = "new"
            update_data["alerted_at"] = None  # Will be re-alerted

        doc_ref.update(update_data)
        return UpsertResult(
            pattern_id=data.pattern_id,
            is_new=is_reactivation,
            previous_status=previous_status,
        )

    def get_active_patterns(self) -> list[dict]:
        """Get all patterns with active status (new, acknowledged, known)."""
        results = []
        for status in ACTIVE_STATUSES:
            docs = (
                self.db.collection(PATTERNS_COLLECTION)
                .where("status", "in", ACTIVE_STATUSES)
                .stream()
            )
            results = [doc.to_dict() for doc in docs]
            break  # Only need one query with "in"
        return results

    def get_patterns_for_auto_resolve(self) -> list[dict]:
        """Get active patterns that might be eligible for auto-resolution."""
        return [
            p for p in self.get_active_patterns()
            if p.get("status") in ("new", "acknowledged")
        ]

    def auto_resolve_pattern(self, pattern_id: str, hours_silent: float) -> None:
        """Mark a pattern as auto-resolved."""
        self.db.collection(PATTERNS_COLLECTION).document(pattern_id).update({
            "status": "auto_resolved",
            "notes": f"Auto-resolved after {hours_silent:.1f} hours of silence",
        })
        logger.info(f"Auto-resolved pattern {pattern_id} after {hours_silent:.1f}h")

    def resolve_pattern(
        self, pattern_id: str, pr_url: str | None = None, note: str = ""
    ) -> None:
        """Manually resolve a pattern (mark as fixed)."""
        fixed_by = {
            "resolved_at": datetime.now(timezone.utc).isoformat(),
            "note": note,
        }
        if pr_url:
            fixed_by["pr_url"] = pr_url
            # Extract "org/repo#N" display from URL
            parts = pr_url.rstrip("/").split("/")
            if len(parts) >= 2:
                fixed_by["pr_display"] = f"{parts[-3]}/{parts[-2]}#{parts[-1]}"

        self.db.collection(PATTERNS_COLLECTION).document(pattern_id).update({
            "status": "fixed",
            "fixed_by": fixed_by,
        })

    def _compute_resolve_threshold(self, rolling_counts: dict) -> float:
        """Compute auto-resolve threshold in hours from rolling counts.

        Uses frequency-aware thresholds: threshold = mean_interval * multiplier.
        Clamped between MIN and MAX hours.
        """
        if len(rolling_counts) < 3:
            return AUTO_RESOLVE_FALLBACK_HOURS

        # Parse timestamps and sort
        timestamps = sorted(rolling_counts.keys())
        if len(timestamps) < 2:
            return AUTO_RESOLVE_FALLBACK_HOURS

        # Compute mean interval between occurrences in hours
        intervals = []
        for i in range(1, len(timestamps)):
            try:
                t1 = datetime.fromisoformat(timestamps[i - 1])
                t2 = datetime.fromisoformat(timestamps[i])
                hours = (t2 - t1).total_seconds() / 3600
                if hours > 0:
                    intervals.append(hours)
            except (ValueError, TypeError):
                continue

        if not intervals:
            return AUTO_RESOLVE_FALLBACK_HOURS

        mean_interval = sum(intervals) / len(intervals)
        threshold = mean_interval * AUTO_RESOLVE_MULTIPLIER
        return max(AUTO_RESOLVE_MIN_HOURS, min(AUTO_RESOLVE_MAX_HOURS, threshold))

    def check_auto_resolve(self, pattern: dict) -> float | None:
        """Check if a pattern should be auto-resolved.

        Returns hours_silent if it should be resolved, None otherwise.
        """
        last_seen = pattern.get("last_seen")
        if not last_seen:
            return None

        try:
            last_seen_dt = datetime.fromisoformat(last_seen)
        except (ValueError, TypeError):
            return None

        if last_seen_dt.tzinfo is None:
            last_seen_dt = last_seen_dt.replace(tzinfo=timezone.utc)

        hours_silent = (
            datetime.now(timezone.utc) - last_seen_dt
        ).total_seconds() / 3600

        threshold = self._compute_resolve_threshold(
            pattern.get("rolling_counts", {})
        )

        if hours_silent >= threshold:
            return hours_silent
        return None

    def _prune_rolling_counts(self, rolling_counts: dict) -> None:
        """Remove rolling count entries older than ROLLING_WINDOW_DAYS."""
        from datetime import timedelta
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=ROLLING_WINDOW_DAYS)
        cutoff_str = cutoff_dt.strftime("%Y-%m-%dT%H:%M")
        keys_to_remove = [k for k in rolling_counts if k < cutoff_str]
        for k in keys_to_remove:
            del rolling_counts[k]

    def create_incident(
        self,
        title: str,
        root_cause: str | None,
        severity: str,
        suggested_fix: str | None,
        primary_service: str,
        pattern_ids: list[str],
        used_llm: bool,
    ) -> str:
        """Create an incident grouping related patterns. Returns incident_id."""
        now = datetime.now(timezone.utc)
        incident_id = f"inc_{now.strftime('%Y%m%d_%H%M')}_{uuid.uuid4().hex[:8]}"

        self.db.collection(INCIDENTS_COLLECTION).document(incident_id).set({
            "incident_id": incident_id,
            "title": title[:60],
            "root_cause": root_cause,
            "severity": severity,
            "suggested_fix": suggested_fix,
            "primary_service": primary_service,
            "pattern_ids": pattern_ids,
            "pattern_count": len(pattern_ids),
            "used_llm": used_llm,
            "created_at": now.isoformat(),
            "status": "open",
        })

        # Link patterns to incident
        for pid in pattern_ids:
            self.db.collection(PATTERNS_COLLECTION).document(pid).update({
                "incident_id": incident_id,
                "severity": severity,
            })

        return incident_id

    def log_discord_alert(
        self,
        alert_type: str,
        content: str,
        success: bool,
        metadata: dict | None = None,
    ) -> None:
        """Log a Discord alert to Firestore for audit trail."""
        now = datetime.now(timezone.utc)
        alert_id = f"alert_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        data = {
            "timestamp": now.isoformat(),
            "alert_type": alert_type,
            "content": content[:2000],
            "success": success,
        }
        if metadata:
            data.update(metadata)

        self.db.collection(ALERTS_COLLECTION).document(alert_id).set(data)

    def update_pattern_alerted(self, pattern_id: str) -> None:
        """Mark a pattern as having been alerted."""
        self.db.collection(PATTERNS_COLLECTION).document(pattern_id).update({
            "alerted_at": datetime.now(timezone.utc).isoformat(),
        })

    def merge_pattern(
        self, source_id: str, target_id: str, reason: str
    ) -> None:
        """Merge a duplicate pattern into a canonical one."""
        now = datetime.now(timezone.utc).isoformat()
        self.db.collection(PATTERNS_COLLECTION).document(source_id).update({
            "status": "muted",
            "merged_into": target_id,
            "merged_at": now,
            "merged_reason": reason,
        })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-error-bot && python -m pytest tests/unit/services/error_monitor/test_firestore_adapter.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/error_monitor/firestore_adapter.py tests/unit/services/error_monitor/test_firestore_adapter.py
git commit -m "feat(error-monitor): add Firestore adapter for patterns, incidents, alerts"
```

---

## Task 5: Discord Alert Client

**Files:**
- Create: `backend/services/error_monitor/discord.py`
- Create: `tests/unit/services/error_monitor/test_discord.py`

- [ ] **Step 1: Write Discord client tests**

Create `tests/unit/services/error_monitor/test_discord.py`:

```python
"""Tests for Discord alert formatting and sending."""

from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from backend.services.error_monitor.discord import (
    ErrorMonitorDiscord,
    format_new_pattern_alert,
    format_incident_alert,
    format_auto_resolved_alert,
    format_spike_alert,
    format_daily_digest,
)


@pytest.fixture
def discord_client():
    return ErrorMonitorDiscord(webhook_url="https://discord.com/api/webhooks/123/abc")


class TestFormatNewPatternAlert:
    def test_includes_service_and_message(self):
        msg = format_new_pattern_alert(
            service="karaoke-backend",
            resource_type="cloud_run_service",
            normalized_message="Firestore DEADLINE_EXCEEDED in /jobs/<ID>",
            sample_message="Firestore DEADLINE_EXCEEDED in /jobs/abc123",
            count=3,
            first_seen="2026-04-12T10:00:00Z",
        )
        assert "karaoke-backend" in msg
        assert "Firestore DEADLINE_EXCEEDED" in msg
        assert "🔴" in msg

    def test_includes_investigate_prompt(self):
        msg = format_new_pattern_alert(
            service="karaoke-backend",
            resource_type="cloud_run_service",
            normalized_message="Error X",
            sample_message="Error X detail",
            count=1,
            first_seen="2026-04-12T10:00:00Z",
        )
        assert "/prod-investigate" in msg

    def test_truncates_to_discord_limit(self):
        long_message = "x" * 3000
        msg = format_new_pattern_alert(
            service="karaoke-backend",
            resource_type="cloud_run_service",
            normalized_message=long_message,
            sample_message=long_message,
            count=1,
            first_seen="2026-04-12T10:00:00Z",
        )
        assert len(msg) <= 2000


class TestFormatIncidentAlert:
    def test_p0_uses_siren_emoji(self):
        msg = format_incident_alert(
            title="Backend completely down",
            severity="P0",
            root_cause="Firestore unavailable",
            suggested_fix="Check Firestore status page",
            primary_service="karaoke-backend",
            patterns=[
                {"service": "karaoke-backend", "normalized_message": "Error A", "count": 10},
                {"service": "video-encoding-job", "normalized_message": "Error B", "count": 5},
            ],
        )
        assert "🚨" in msg

    def test_p3_uses_yellow_emoji(self):
        msg = format_incident_alert(
            title="Minor cleanup issue",
            severity="P3",
            root_cause="Stale temp files",
            suggested_fix="Add cleanup cron",
            primary_service="backup_to_aws",
            patterns=[
                {"service": "backup_to_aws", "normalized_message": "Cleanup failed", "count": 1},
            ],
        )
        assert "🟡" in msg


class TestFormatAutoResolvedAlert:
    def test_consolidated_format(self):
        msg = format_auto_resolved_alert(
            resolved_patterns=[
                {"service": "karaoke-backend", "normalized_message": "Error A", "hours_silent": 24.5},
                {"service": "karaoke-decide", "normalized_message": "Error B", "hours_silent": 48.0},
            ],
        )
        assert "✅" in msg
        assert "karaoke-backend" in msg
        assert "karaoke-decide" in msg


class TestFormatSpikeAlert:
    def test_includes_current_vs_normal(self):
        msg = format_spike_alert(
            service="karaoke-backend",
            normalized_message="Timeout on /api/jobs",
            current_count=50,
            rolling_average=5.0,
        )
        assert "⚠️" in msg
        assert "50" in msg
        assert "5" in msg


class TestFormatDailyDigest:
    def test_includes_totals(self):
        msg = format_daily_digest(
            total_errors=150,
            new_patterns=3,
            resolved_patterns=2,
            active_patterns=12,
            per_service_counts={"karaoke-backend": 80, "karaoke-decide": 40, "audio-separator": 30},
        )
        assert "📊" in msg
        assert "150" in msg
        assert "karaoke-backend" in msg


import pytest


class TestSendMessage:
    @patch("backend.services.error_monitor.discord.requests.post")
    def test_sends_webhook_message(self, mock_post, discord_client):
        mock_post.return_value.status_code = 204
        mock_post.return_value.ok = True
        result = discord_client.send_message("Test message")
        assert result is True
        mock_post.assert_called_once()

    @patch("backend.services.error_monitor.discord.requests.post")
    def test_returns_false_on_failure(self, mock_post, discord_client):
        mock_post.return_value.status_code = 429
        mock_post.return_value.ok = False
        mock_post.return_value.text = "Rate limited"
        result = discord_client.send_message("Test message")
        assert result is False

    def test_tracks_message_count(self, discord_client):
        assert discord_client.messages_sent == 0

    @patch("backend.services.error_monitor.discord.requests.post")
    def test_respects_rate_limit(self, mock_post, discord_client):
        mock_post.return_value.ok = True
        discord_client.messages_sent = 10  # At limit
        result = discord_client.send_message("Should not send")
        assert result is False
        mock_post.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-error-bot && python -m pytest tests/unit/services/error_monitor/test_discord.py -v`

Expected: ImportError.

- [ ] **Step 3: Implement Discord client**

Create `backend/services/error_monitor/discord.py`:

```python
"""Discord webhook client for error monitor alerts."""

import logging
from datetime import datetime, timezone

import requests

from backend.services.error_monitor.config import (
    DISCORD_MAX_MESSAGE_LENGTH,
    MAX_DISCORD_MESSAGES_PER_RUN,
)

logger = logging.getLogger(__name__)

SEVERITY_EMOJI = {
    "P0": "🚨",
    "P1": "🔴",
    "P2": "⚠️",
    "P3": "🟡",
}


def _now_eastern() -> str:
    """Current time formatted in Eastern time for Discord messages."""
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/New_York"))
    return now.strftime("%I:%M %p ET")


def _truncate(msg: str) -> str:
    """Truncate message to Discord's limit."""
    if len(msg) <= DISCORD_MAX_MESSAGE_LENGTH:
        return msg
    return msg[: DISCORD_MAX_MESSAGE_LENGTH - 3] + "..."


def format_new_pattern_alert(
    service: str,
    resource_type: str,
    normalized_message: str,
    sample_message: str,
    count: int,
    first_seen: str,
) -> str:
    """Format a new error pattern alert."""
    resource_label = resource_type.replace("cloud_run_", "").replace("_", " ")
    msg = (
        f"🔴 **New error pattern** in `{service}` ({resource_label})\n"
        f"**Pattern:** `{normalized_message[:120]}`\n"
        f"**Sample:** `{sample_message[:200]}`\n"
        f"**Count:** {count} | **First seen:** {first_seen[:16]} | {_now_eastern()}\n"
        f"_Investigate:_ `/prod-investigate '{service} {normalized_message[:60]}'`"
    )
    return _truncate(msg)


def format_incident_alert(
    title: str,
    severity: str,
    root_cause: str | None,
    suggested_fix: str | None,
    primary_service: str,
    patterns: list[dict],
) -> str:
    """Format an incident alert (2+ patterns grouped by root cause)."""
    emoji = SEVERITY_EMOJI.get(severity, "⚠️")
    lines = [
        f"{emoji} **[{severity}] {title}**",
        f"**Root cause:** {root_cause or 'Unknown'} (primary: `{primary_service}`)",
    ]
    if suggested_fix:
        lines.append(f"**Fix:** {suggested_fix}")

    for p in patterns[:5]:  # Max 5 patterns shown
        lines.append(
            f"  → `{p['service']}`: `{p['normalized_message'][:80]}` ({p.get('count', '?')}x)"
        )

    if len(patterns) > 5:
        lines.append(f"  _...and {len(patterns) - 5} more patterns_")

    lines.append(f"_{_now_eastern()}_")
    return _truncate("\n".join(lines))


def format_auto_resolved_alert(resolved_patterns: list[dict]) -> str:
    """Format a consolidated auto-resolved alert."""
    lines = [f"✅ **{len(resolved_patterns)} pattern(s) auto-resolved**"]
    for p in resolved_patterns[:8]:
        hours = p.get("hours_silent", 0)
        lines.append(
            f"  → `{p['service']}`: `{p['normalized_message'][:60]}` (silent {hours:.0f}h)"
        )
    if len(resolved_patterns) > 8:
        lines.append(f"  _...and {len(resolved_patterns) - 8} more_")
    lines.append(f"_{_now_eastern()}_")
    return _truncate("\n".join(lines))


def format_spike_alert(
    service: str,
    normalized_message: str,
    current_count: int,
    rolling_average: float,
) -> str:
    """Format a spike detection alert."""
    msg = (
        f"⚠️ **Error spike** in `{service}`\n"
        f"**Pattern:** `{normalized_message[:120]}`\n"
        f"**Current:** {current_count} (normal avg: {rolling_average:.1f}) — "
        f"{current_count / rolling_average:.0f}x increase\n"
        f"_{_now_eastern()}_"
    )
    return _truncate(msg)


def format_daily_digest(
    total_errors: int,
    new_patterns: int,
    resolved_patterns: int,
    active_patterns: int,
    per_service_counts: dict[str, int],
) -> str:
    """Format the daily digest summary."""
    lines = [
        f"📊 **Daily Error Digest** — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        f"**24h total:** {total_errors} errors | **Active patterns:** {active_patterns} | "
        f"**New:** {new_patterns} | **Resolved:** {resolved_patterns}",
        "",
        "**By service:**",
    ]
    for service, count in sorted(per_service_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  `{service}`: {count}")
    lines.append(f"\n_{_now_eastern()}_")
    return _truncate("\n".join(lines))


class ErrorMonitorDiscord:
    """Discord webhook client with rate limiting and audit logging."""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
        self.messages_sent = 0

    def send_message(self, content: str) -> bool:
        """Send a message to Discord. Returns True on success."""
        if self.messages_sent >= MAX_DISCORD_MESSAGES_PER_RUN:
            logger.warning("Discord rate limit reached (%d messages), skipping", self.messages_sent)
            return False

        try:
            response = requests.post(
                self.webhook_url,
                json={"content": content},
                timeout=30,
            )
            self.messages_sent += 1
            if response.ok:
                return True
            else:
                logger.error(
                    "Discord webhook failed: %d %s", response.status_code, response.text[:200]
                )
                return False
        except requests.RequestException as e:
            logger.error("Discord webhook error: %s", e)
            self.messages_sent += 1
            return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-error-bot && python -m pytest tests/unit/services/error_monitor/test_discord.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/error_monitor/discord.py tests/unit/services/error_monitor/test_discord.py
git commit -m "feat(error-monitor): add Discord alert client with formatting"
```

---

## Task 6: LLM Analysis (Gemini)

**Files:**
- Create: `backend/services/error_monitor/llm_analysis.py`
- Create: `tests/unit/services/error_monitor/test_llm_analysis.py`

- [ ] **Step 1: Write LLM analysis tests**

Create `tests/unit/services/error_monitor/test_llm_analysis.py`:

```python
"""Tests for LLM incident analysis and dedup."""

import json
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from backend.services.error_monitor.llm_analysis import (
    analyze_patterns,
    find_duplicate_patterns,
    IncidentAnalysis,
    Incident,
    DuplicateGroup,
    _parse_llm_response,
)


class TestParseResponse:
    def test_parses_valid_json(self):
        response = json.dumps({
            "incidents": [
                {
                    "title": "Audio separation failures",
                    "root_cause": "GPU OOM",
                    "severity": "P1",
                    "suggested_fix": "Increase memory",
                    "primary_service": "audio-separator",
                    "pattern_indices": [0, 1],
                }
            ]
        })
        result = _parse_llm_response(response)
        assert result is not None
        assert len(result.incidents) == 1
        assert result.incidents[0].title == "Audio separation failures"
        assert result.incidents[0].severity == "P1"

    def test_returns_none_for_invalid_json(self):
        assert _parse_llm_response("not json") is None

    def test_returns_none_for_missing_incidents(self):
        assert _parse_llm_response('{"other": "data"}') is None

    def test_handles_markdown_code_block(self):
        response = '```json\n{"incidents": [{"title": "Test", "root_cause": null, "severity": "P3", "suggested_fix": null, "primary_service": "backend", "pattern_indices": [0]}]}\n```'
        result = _parse_llm_response(response)
        assert result is not None
        assert len(result.incidents) == 1


class TestAnalyzePatterns:
    @patch("backend.services.error_monitor.llm_analysis._call_llm")
    def test_groups_patterns_into_incidents(self, mock_llm):
        mock_llm.return_value = json.dumps({
            "incidents": [
                {
                    "title": "Firestore connectivity",
                    "root_cause": "Firestore overloaded",
                    "severity": "P1",
                    "suggested_fix": "Check Firestore dashboard",
                    "primary_service": "karaoke-backend",
                    "pattern_indices": [0, 1],
                }
            ]
        })

        patterns = [
            {"service": "karaoke-backend", "normalized_message": "Firestore DEADLINE_EXCEEDED"},
            {"service": "video-encoding-job", "normalized_message": "Firestore unavailable"},
        ]

        result = analyze_patterns(patterns)
        assert result is not None
        assert len(result.incidents) == 1
        assert result.incidents[0].pattern_indices == [0, 1]

    @patch("backend.services.error_monitor.llm_analysis._call_llm")
    def test_falls_back_on_llm_failure(self, mock_llm):
        mock_llm.side_effect = Exception("LLM unavailable")
        patterns = [
            {"service": "karaoke-backend", "normalized_message": "Error A"},
            {"service": "karaoke-decide", "normalized_message": "Error B"},
        ]
        result = analyze_patterns(patterns)
        # Fallback: group by service
        assert result is not None
        assert len(result.incidents) == 2  # One per service

    @patch("backend.services.error_monitor.llm_analysis._call_llm")
    def test_skips_analysis_for_single_pattern(self, mock_llm):
        patterns = [
            {"service": "karaoke-backend", "normalized_message": "Error A"},
        ]
        result = analyze_patterns(patterns)
        assert result is None  # Not enough patterns
        mock_llm.assert_not_called()


class TestFindDuplicates:
    @patch("backend.services.error_monitor.llm_analysis._call_llm")
    def test_identifies_near_duplicates(self, mock_llm):
        mock_llm.return_value = json.dumps({
            "duplicates": [
                {
                    "canonical_index": 0,
                    "duplicate_indices": [1],
                    "reason": "Same Firestore timeout, different phrasing",
                }
            ]
        })
        new_patterns = [
            {"pattern_id": "new1", "normalized_message": "Firestore timeout on read"},
        ]
        existing_patterns = [
            {"pattern_id": "existing1", "normalized_message": "Firestore read timed out"},
        ]
        result = find_duplicate_patterns(new_patterns, existing_patterns)
        assert len(result) == 1
        assert result[0].canonical_index == 0

    @patch("backend.services.error_monitor.llm_analysis._call_llm")
    def test_returns_empty_on_no_duplicates(self, mock_llm):
        mock_llm.return_value = json.dumps({"duplicates": []})
        result = find_duplicate_patterns(
            [{"pattern_id": "a", "normalized_message": "Error X"}],
            [{"pattern_id": "b", "normalized_message": "Completely different"}],
        )
        assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-error-bot && python -m pytest tests/unit/services/error_monitor/test_llm_analysis.py -v`

Expected: ImportError.

- [ ] **Step 3: Implement LLM analysis module**

Create `backend/services/error_monitor/llm_analysis.py`:

```python
"""LLM-powered incident analysis and duplicate detection using Gemini Flash."""

import json
import logging
import re
import time
from dataclasses import dataclass, field

import google.generativeai as genai

from backend.services.error_monitor.config import (
    GCP_PROJECT,
    LLM_ANALYSIS_MODEL,
    LLM_VERTEX_LOCATION,
    MIN_PATTERNS_FOR_ANALYSIS,
    SERVICE_DEPENDENCY_MAP,
)

logger = logging.getLogger(__name__)

# Retry settings
MAX_RETRIES = 3
BASE_DELAY = 2.0


@dataclass
class Incident:
    title: str
    root_cause: str | None
    severity: str
    suggested_fix: str | None
    primary_service: str
    pattern_indices: list[int]


@dataclass
class IncidentAnalysis:
    incidents: list[Incident]
    used_llm: bool = True


@dataclass
class DuplicateGroup:
    canonical_index: int
    duplicate_indices: list[int]
    reason: str


def _call_llm(
    system_prompt: str, user_prompt: str, temperature: float = 0.2
) -> str:
    """Call Gemini Flash via Vertex AI. Returns raw response text."""
    model = genai.GenerativeModel(
        model_name=LLM_ANALYSIS_MODEL,
        system_instruction=system_prompt,
        generation_config=genai.GenerationConfig(temperature=temperature),
    )

    for attempt in range(MAX_RETRIES):
        try:
            response = model.generate_content(user_prompt)
            return response.text
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                delay = BASE_DELAY * (2**attempt)
                logger.warning(
                    "LLM call attempt %d failed: %s, retrying in %.1fs",
                    attempt + 1, e, delay,
                )
                time.sleep(delay)
            else:
                raise


def _parse_llm_response(text: str) -> IncidentAnalysis | None:
    """Parse LLM JSON response into IncidentAnalysis."""
    # Strip markdown code blocks if present
    cleaned = re.sub(r"```(?:json)?\s*", "", text).strip()
    cleaned = cleaned.rstrip("`")

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.error("Failed to parse LLM response as JSON: %s", text[:200])
        return None

    if "incidents" not in data:
        logger.error("LLM response missing 'incidents' key")
        return None

    incidents = []
    for inc in data["incidents"]:
        incidents.append(Incident(
            title=inc.get("title", "Unknown"),
            root_cause=inc.get("root_cause"),
            severity=inc.get("severity", "P3"),
            suggested_fix=inc.get("suggested_fix"),
            primary_service=inc.get("primary_service", "unknown"),
            pattern_indices=inc.get("pattern_indices", []),
        ))

    return IncidentAnalysis(incidents=incidents, used_llm=True)


def _fallback_group_by_service(patterns: list[dict]) -> IncidentAnalysis:
    """Fallback grouping when LLM is unavailable: one incident per service."""
    by_service: dict[str, list[int]] = {}
    for i, p in enumerate(patterns):
        svc = p.get("service", "unknown")
        by_service.setdefault(svc, []).append(i)

    incidents = []
    for service, indices in by_service.items():
        incidents.append(Incident(
            title=f"Errors in {service}",
            root_cause=None,
            severity="P2",
            suggested_fix=None,
            primary_service=service,
            pattern_indices=indices,
        ))

    return IncidentAnalysis(incidents=incidents, used_llm=False)


def analyze_patterns(patterns: list[dict]) -> IncidentAnalysis | None:
    """Analyze 2+ new patterns to identify root causes and group into incidents.

    Returns None if fewer than MIN_PATTERNS_FOR_ANALYSIS patterns.
    Falls back to service-based grouping if LLM fails.
    """
    if len(patterns) < MIN_PATTERNS_FOR_ANALYSIS:
        return None

    system_prompt = f"""You are a production error analyst for Nomad Karaoke, a karaoke video generation platform.

Service dependency map:
{SERVICE_DEPENDENCY_MAP}

Analyze the error patterns below. Group related errors that likely share a root cause into incidents.

Respond with JSON only:
{{
  "incidents": [
    {{
      "title": "max 60 chars",
      "root_cause": "one-liner explanation",
      "severity": "P0|P1|P2|P3",
      "suggested_fix": "actionable, 1-2 sentences",
      "primary_service": "upstream root cause service name",
      "pattern_indices": [0, 2]
    }}
  ]
}}

Severity:
- P0: Service completely down, all jobs failing, payment processing broken
- P1: Major pipeline stage broken (audio separation, lyrics, encoding), auth failures
- P2: Degraded but functional — intermittent errors, slow responses
- P3: Minor — deprecation warnings, cleanup failures, spot VM preemptions

Every pattern must appear in exactly one incident's pattern_indices."""

    patterns_text = "\n".join(
        f"[{i}] service={p['service']}, message={p.get('normalized_message', '')}, count={p.get('count', 1)}"
        for i, p in enumerate(patterns)
    )

    try:
        response = _call_llm(system_prompt, patterns_text)
        result = _parse_llm_response(response)
        if result:
            return result
        logger.warning("Could not parse LLM response, falling back to service grouping")
    except Exception as e:
        logger.error("LLM analysis failed: %s, falling back to service grouping", e)

    return _fallback_group_by_service(patterns)


def find_duplicate_patterns(
    new_patterns: list[dict],
    existing_patterns: list[dict],
) -> list[DuplicateGroup]:
    """Identify near-duplicate patterns that regex normalization missed.

    Compares new patterns against existing canonical patterns.
    Returns groups where new patterns should be merged into existing ones.
    """
    if not new_patterns or not existing_patterns:
        return []

    system_prompt = """You are checking for near-duplicate error patterns. Two patterns are duplicates if they describe the same error with slightly different phrasing.

Only merge a NEW pattern into an EXISTING one — never merge two existing patterns.

Respond with JSON only:
{
  "duplicates": [
    {
      "canonical_index": 0,
      "duplicate_indices": [1],
      "reason": "why they're the same error"
    }
  ]
}

If no duplicates, return {"duplicates": []}."""

    text = "EXISTING patterns (canonical):\n"
    for i, p in enumerate(existing_patterns):
        text += f"  [{i}] {p.get('service', '?')}: {p.get('normalized_message', '')}\n"
    text += "\nNEW patterns (check for duplicates):\n"
    for i, p in enumerate(new_patterns):
        text += f"  [{i}] {p.get('service', '?')}: {p.get('normalized_message', '')}\n"

    try:
        response = _call_llm(system_prompt, text, temperature=0.1)
        cleaned = re.sub(r"```(?:json)?\s*", "", response).strip().rstrip("`")
        data = json.loads(cleaned)
        groups = []
        for d in data.get("duplicates", []):
            groups.append(DuplicateGroup(
                canonical_index=d["canonical_index"],
                duplicate_indices=d["duplicate_indices"],
                reason=d["reason"],
            ))
        return groups
    except Exception as e:
        logger.error("Duplicate detection failed: %s", e)
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-error-bot && python -m pytest tests/unit/services/error_monitor/test_llm_analysis.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/error_monitor/llm_analysis.py tests/unit/services/error_monitor/test_llm_analysis.py
git commit -m "feat(error-monitor): add LLM incident analysis and dedup via Gemini"
```

---

## Task 7: Monitor Orchestrator (Main Entry Point)

**Files:**
- Create: `backend/services/error_monitor/monitor.py`
- Create: `tests/unit/services/error_monitor/test_monitor.py`

- [ ] **Step 1: Write monitor orchestrator tests**

Create `tests/unit/services/error_monitor/test_monitor.py`:

```python
"""Tests for the error monitor orchestrator."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest

from backend.services.error_monitor.monitor import (
    ErrorMonitor,
    _build_log_filter,
    _extract_message,
    _extract_service_name,
    _classify_resource_type,
)


class TestBuildLogFilter:
    def test_cloud_run_service_filter(self):
        f = _build_log_filter(
            resource_type="cloud_run_revision",
            resource_names=["karaoke-backend", "karaoke-decide"],
            lookback_minutes=15,
        )
        assert 'resource.type="cloud_run_revision"' in f
        assert "severity>=ERROR" in f
        assert "karaoke-backend" in f

    def test_cloud_run_job_filter(self):
        f = _build_log_filter(
            resource_type="cloud_run_job",
            resource_names=["video-encoding-job"],
            lookback_minutes=15,
        )
        assert 'resource.type="cloud_run_job"' in f
        assert "video-encoding-job" in f

    def test_gce_instance_filter(self):
        f = _build_log_filter(
            resource_type="gce_instance",
            resource_names=["encoding-worker-a"],
            lookback_minutes=15,
        )
        assert 'resource.type="gce_instance"' in f


class TestExtractMessage:
    def test_extracts_text_payload(self):
        entry = MagicMock()
        entry.payload = "Simple error text"
        entry.payload_type = "text_payload"
        assert _extract_message(entry) == "Simple error text"

    def test_extracts_json_message(self):
        entry = MagicMock()
        entry.payload = {"message": "JSON error message"}
        entry.payload_type = "json_payload"
        assert _extract_message(entry) == "JSON error message"

    def test_extracts_json_textpayload_field(self):
        entry = MagicMock()
        entry.payload = {"textPayload": "Text from JSON"}
        entry.payload_type = "json_payload"
        assert _extract_message(entry) == "Text from JSON"

    def test_returns_none_for_empty(self):
        entry = MagicMock()
        entry.payload = None
        entry.payload_type = "proto_payload"
        assert _extract_message(entry) is None


class TestClassifyResourceType:
    def test_cloud_run_service(self):
        assert _classify_resource_type("cloud_run_revision") == "cloud_run_service"

    def test_cloud_run_job(self):
        assert _classify_resource_type("cloud_run_job") == "cloud_run_job"

    def test_cloud_function(self):
        assert _classify_resource_type("cloud_function") == "cloud_function"

    def test_gce_instance(self):
        assert _classify_resource_type("gce_instance") == "gce_instance"


class TestExtractServiceName:
    def test_cloud_run_service(self):
        entry = MagicMock()
        entry.resource.labels = {"service_name": "karaoke-backend"}
        assert _extract_service_name(entry, "cloud_run_revision") == "karaoke-backend"

    def test_cloud_run_job(self):
        entry = MagicMock()
        entry.resource.labels = {"job_name": "video-encoding-job"}
        assert _extract_service_name(entry, "cloud_run_job") == "video-encoding-job"

    def test_cloud_function(self):
        entry = MagicMock()
        entry.resource.labels = {"function_name": "backup_to_aws"}
        assert _extract_service_name(entry, "cloud_function") == "backup_to_aws"

    def test_returns_unknown_for_missing_labels(self):
        entry = MagicMock()
        entry.resource.labels = {}
        assert _extract_service_name(entry, "cloud_run_revision") == "unknown"


class TestMonitorPipeline:
    @pytest.fixture
    def monitor(self):
        m = ErrorMonitor.__new__(ErrorMonitor)
        m.logging_client = MagicMock()
        m.firestore_adapter = MagicMock()
        m.discord = MagicMock()
        m.discord.messages_sent = 0
        m.logger = MagicMock()
        return m

    def test_groups_entries_by_pattern(self, monitor):
        entries = [
            {"service": "karaoke-backend", "normalized": "Error A", "raw": "Error A detail", "resource_type": "cloud_run_service", "timestamp": datetime.now(timezone.utc)},
            {"service": "karaoke-backend", "normalized": "Error A", "raw": "Error A detail 2", "resource_type": "cloud_run_service", "timestamp": datetime.now(timezone.utc)},
            {"service": "karaoke-decide", "normalized": "Error B", "raw": "Error B detail", "resource_type": "cloud_run_service", "timestamp": datetime.now(timezone.utc)},
        ]
        groups = monitor._group_by_pattern(entries)
        assert len(groups) == 2  # Two distinct patterns

    def test_detects_spike(self, monitor):
        pattern = {
            "rolling_counts": {
                "2026-04-12T09:00": 2,
                "2026-04-12T09:15": 3,
                "2026-04-12T09:30": 2,
                "2026-04-12T09:45": 1,
            }
        }
        # Normal avg ~2, current count 15 → spike (15/2 = 7.5x > 5x)
        assert monitor._is_spike(pattern, current_count=15) is True
        # Normal avg ~2, current count 5 → not spike (5/2 = 2.5x < 5x)
        assert monitor._is_spike(pattern, current_count=5) is False

    def test_rolling_average(self, monitor):
        assert monitor._rolling_average({"a": 2, "b": 4, "c": 6}) == 4.0
        assert monitor._rolling_average({}) == 0.0

    @patch("backend.services.error_monitor.monitor.get_digest_mode", return_value=False)
    @patch("backend.services.error_monitor.monitor.get_llm_enabled", return_value=False)
    def test_run_cycle_no_errors(self, mock_llm, mock_digest, monitor):
        """End-to-end: no errors found, still checks auto-resolve."""
        monitor._collect_logs = MagicMock(return_value=[])
        monitor._check_auto_resolve = MagicMock()
        monitor._run_monitor_cycle()
        monitor._check_auto_resolve.assert_called_once()

    @patch("backend.services.error_monitor.monitor.get_digest_mode", return_value=False)
    @patch("backend.services.error_monitor.monitor.get_llm_enabled", return_value=False)
    def test_run_cycle_with_new_pattern(self, mock_llm, mock_digest, monitor):
        """End-to-end: one new error triggers individual alert."""
        from backend.services.error_monitor.firestore_adapter import UpsertResult
        monitor._collect_logs = MagicMock(return_value=[
            {"service": "karaoke-backend", "resource_type": "cloud_run_service",
             "raw": "Firestore timeout", "normalized": "Firestore timeout",
             "timestamp": datetime.now(timezone.utc)},
        ])
        monitor.firestore_adapter.upsert_pattern.return_value = UpsertResult(
            pattern_id="hash1", is_new=True
        )
        monitor.firestore_adapter.get_pattern.return_value = None
        monitor._check_auto_resolve = MagicMock()

        monitor._run_monitor_cycle()

        monitor.discord.send_message.assert_called_once()  # One alert sent
        monitor.firestore_adapter.update_pattern_alerted.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-error-bot && python -m pytest tests/unit/services/error_monitor/test_monitor.py -v`

Expected: ImportError.

- [ ] **Step 3: Implement monitor orchestrator**

Create `backend/services/error_monitor/monitor.py`:

```python
"""Error monitor orchestrator — main entry point.

Queries Cloud Logging, normalizes, deduplicates, analyzes, and alerts.
Run as: python -m backend.services.error_monitor.monitor
"""

import logging
import os
import sys
from datetime import datetime, timezone, timedelta

from google.cloud import logging as cloud_logging
from google.cloud import secretmanager

from backend.services.error_monitor.config import (
    GCP_PROJECT,
    LOOKBACK_MINUTES,
    MAX_LOG_ENTRIES,
    MONITORED_CLOUD_FUNCTIONS,
    MONITORED_CLOUD_RUN_JOBS,
    MONITORED_CLOUD_RUN_SERVICES,
    MONITORED_GCE_INSTANCES,
    SPIKE_MIN_COUNT,
    SPIKE_MULTIPLIER,
    get_digest_mode,
    get_discord_webhook_secret_name,
    get_llm_enabled,
)
from backend.services.error_monitor.discord import (
    ErrorMonitorDiscord,
    format_auto_resolved_alert,
    format_daily_digest,
    format_incident_alert,
    format_new_pattern_alert,
    format_spike_alert,
)
from backend.services.error_monitor.firestore_adapter import (
    ErrorPatternsAdapter,
    PatternData,
)
from backend.services.error_monitor.known_issues import should_ignore
from backend.services.error_monitor.llm_analysis import analyze_patterns, find_duplicate_patterns
from backend.services.error_monitor.normalizer import compute_pattern_hash, normalize_message

logger = logging.getLogger(__name__)

# Maps Cloud Logging resource types to our filter fields
RESOURCE_TYPE_FILTERS = {
    "cloud_run_revision": ("resource.labels.service_name", MONITORED_CLOUD_RUN_SERVICES),
    "cloud_run_job": ("resource.labels.job_name", MONITORED_CLOUD_RUN_JOBS),
    "cloud_function": ("resource.labels.function_name", MONITORED_CLOUD_FUNCTIONS),
    "gce_instance": ('labels."compute.googleapis.com/resource_name"', MONITORED_GCE_INSTANCES),
}

RESOURCE_TYPE_MAP = {
    "cloud_run_revision": "cloud_run_service",
    "cloud_run_job": "cloud_run_job",
    "cloud_function": "cloud_function",
    "gce_instance": "gce_instance",
}


def _build_log_filter(
    resource_type: str, resource_names: list[str], lookback_minutes: int
) -> str:
    """Build a Cloud Logging filter string."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    filter_field, _ = RESOURCE_TYPE_FILTERS.get(resource_type, ("", []))
    names_filter = " OR ".join(f'{filter_field}="{name}"' for name in resource_names)

    return (
        f'resource.type="{resource_type}" '
        f"severity>=ERROR "
        f'timestamp>="{cutoff_str}" '
        f"({names_filter})"
    )


def _extract_message(entry) -> str | None:
    """Extract the error message from a Cloud Logging entry."""
    payload = entry.payload
    if payload is None:
        return None

    if isinstance(payload, str):
        return payload if payload.strip() else None

    if isinstance(payload, dict):
        # Try common fields
        for key in ("message", "textPayload", "error", "msg"):
            if key in payload and payload[key]:
                return str(payload[key])
        # Fallback to string representation
        return str(payload)[:500]

    return None


def _extract_service_name(entry, resource_type: str) -> str:
    """Extract the service/resource name from a log entry."""
    labels = entry.resource.labels or {}
    if resource_type == "cloud_run_revision":
        return labels.get("service_name", "unknown")
    elif resource_type == "cloud_run_job":
        return labels.get("job_name", "unknown")
    elif resource_type == "cloud_function":
        return labels.get("function_name", "unknown")
    elif resource_type == "gce_instance":
        # Try instance name from labels
        entry_labels = getattr(entry, "labels", {}) or {}
        return entry_labels.get("compute.googleapis.com/resource_name", labels.get("instance_id", "unknown"))
    return "unknown"


def _classify_resource_type(log_resource_type: str) -> str:
    """Map Cloud Logging resource type to our resource type."""
    return RESOURCE_TYPE_MAP.get(log_resource_type, "unknown")


class ErrorMonitor:
    """Main error monitor orchestrator."""

    def __init__(self):
        self.logging_client = cloud_logging.Client(project=GCP_PROJECT)
        self.firestore_adapter = ErrorPatternsAdapter()
        self.logger = logger

        # Get Discord webhook URL from Secret Manager
        webhook_url = self._get_discord_webhook()
        self.discord = ErrorMonitorDiscord(webhook_url=webhook_url)

    def _get_discord_webhook(self) -> str:
        """Retrieve Discord webhook URL from Secret Manager."""
        # Check env var first (injected by Cloud Run Job)
        url = os.environ.get("DISCORD_WEBHOOK_URL")
        if url:
            return url.strip()

        # Fall back to Secret Manager
        client = secretmanager.SecretManagerServiceClient()
        secret_name = get_discord_webhook_secret_name()
        name = f"projects/{GCP_PROJECT}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(name=name)
        return response.payload.data.decode("utf-8").strip()

    def run(self) -> None:
        """Run the full error monitor pipeline."""
        if get_digest_mode():
            self._run_daily_digest()
            return

        self._run_monitor_cycle()

    def _run_monitor_cycle(self) -> None:
        """Standard 15-minute monitor cycle."""
        logger.info("Starting error monitor cycle")

        # 1. Collect logs
        entries = self._collect_logs()
        logger.info("Collected %d log entries", len(entries))

        if not entries:
            # Still check for auto-resolution even with no new errors
            self._check_auto_resolve()
            logger.info("No errors found, cycle complete")
            return

        # 2. Group by pattern
        groups = self._group_by_pattern(entries)
        logger.info("Grouped into %d patterns", len(groups))

        # 3. Upsert patterns to Firestore, identify new ones
        new_patterns = []
        spike_patterns = []
        for pattern_hash, group in groups.items():
            result = self.firestore_adapter.upsert_pattern(PatternData(
                pattern_id=pattern_hash,
                service=group["service"],
                resource_type=group["resource_type"],
                normalized_message=group["normalized"],
                sample_message=group["sample"],
                count=group["count"],
                timestamp=group["latest_timestamp"],
            ))

            if result.is_new:
                new_patterns.append({
                    "pattern_id": pattern_hash,
                    "service": group["service"],
                    "resource_type": group["resource_type"],
                    "normalized_message": group["normalized"],
                    "sample_message": group["sample"],
                    "count": group["count"],
                    "first_seen": group["earliest_timestamp"].isoformat(),
                })

            # Check for spike on existing patterns
            existing = self.firestore_adapter.get_pattern(pattern_hash)
            if existing and self._is_spike(existing, group["count"]):
                spike_patterns.append({
                    "service": group["service"],
                    "normalized_message": group["normalized"],
                    "current_count": group["count"],
                    "rolling_average": self._rolling_average(existing.get("rolling_counts", {})),
                })

        # 4. LLM dedup new patterns against existing
        if new_patterns and get_llm_enabled():
            existing = self.firestore_adapter.get_active_patterns()
            duplicates = find_duplicate_patterns(new_patterns, existing)
            for dup in duplicates:
                for dup_idx in dup.duplicate_indices:
                    if dup_idx < len(new_patterns):
                        merged = new_patterns[dup_idx]
                        canonical = existing[dup.canonical_index] if dup.canonical_index < len(existing) else None
                        if canonical:
                            self.firestore_adapter.merge_pattern(
                                merged["pattern_id"],
                                canonical["pattern_id"],
                                dup.reason,
                            )
                            logger.info(
                                "Merged duplicate %s into %s: %s",
                                merged["pattern_id"][:8],
                                canonical["pattern_id"][:8],
                                dup.reason,
                            )

            # Remove merged patterns from new list
            merged_ids = set()
            for dup in duplicates:
                for idx in dup.duplicate_indices:
                    if idx < len(new_patterns):
                        merged_ids.add(new_patterns[idx]["pattern_id"])
            new_patterns = [p for p in new_patterns if p["pattern_id"] not in merged_ids]

        # 5. LLM incident analysis for new patterns
        if len(new_patterns) >= 2 and get_llm_enabled():
            analysis = analyze_patterns(new_patterns)
            if analysis:
                for incident in analysis.incidents:
                    pattern_ids = [
                        new_patterns[i]["pattern_id"]
                        for i in incident.pattern_indices
                        if i < len(new_patterns)
                    ]
                    incident_id = self.firestore_adapter.create_incident(
                        title=incident.title,
                        root_cause=incident.root_cause,
                        severity=incident.severity,
                        suggested_fix=incident.suggested_fix,
                        primary_service=incident.primary_service,
                        pattern_ids=pattern_ids,
                        used_llm=analysis.used_llm,
                    )

                    # Send incident alert
                    incident_patterns = [
                        new_patterns[i] for i in incident.pattern_indices
                        if i < len(new_patterns)
                    ]
                    msg = format_incident_alert(
                        title=incident.title,
                        severity=incident.severity,
                        root_cause=incident.root_cause,
                        suggested_fix=incident.suggested_fix,
                        primary_service=incident.primary_service,
                        patterns=incident_patterns,
                    )
                    success = self.discord.send_message(msg)
                    self.firestore_adapter.log_discord_alert(
                        alert_type="incident",
                        content=msg,
                        success=success,
                        metadata={"incident_id": incident_id, "severity": incident.severity},
                    )
                    for pid in pattern_ids:
                        self.firestore_adapter.update_pattern_alerted(pid)
        else:
            # Send individual alerts for new patterns
            for pattern in new_patterns:
                msg = format_new_pattern_alert(
                    service=pattern["service"],
                    resource_type=pattern["resource_type"],
                    normalized_message=pattern["normalized_message"],
                    sample_message=pattern["sample_message"],
                    count=pattern["count"],
                    first_seen=pattern["first_seen"],
                )
                success = self.discord.send_message(msg)
                self.firestore_adapter.log_discord_alert(
                    alert_type="new_pattern",
                    content=msg,
                    success=success,
                    metadata={"service": pattern["service"], "pattern_id": pattern["pattern_id"]},
                )
                self.firestore_adapter.update_pattern_alerted(pattern["pattern_id"])

        # 6. Send spike alerts
        for spike in spike_patterns:
            msg = format_spike_alert(
                service=spike["service"],
                normalized_message=spike["normalized_message"],
                current_count=spike["current_count"],
                rolling_average=spike["rolling_average"],
            )
            success = self.discord.send_message(msg)
            self.firestore_adapter.log_discord_alert(
                alert_type="spike",
                content=msg,
                success=success,
                metadata={"service": spike["service"]},
            )

        # 7. Check for auto-resolution
        self._check_auto_resolve()

        logger.info(
            "Cycle complete: %d entries, %d patterns, %d new, %d spikes",
            len(entries), len(groups), len(new_patterns), len(spike_patterns),
        )

    def _collect_logs(self) -> list[dict]:
        """Query Cloud Logging for errors across all resource types."""
        all_entries = []

        for resource_type, (filter_field, names) in RESOURCE_TYPE_FILTERS.items():
            if not names:
                continue

            log_filter = _build_log_filter(resource_type, names, LOOKBACK_MINUTES)
            try:
                entries = list(
                    self.logging_client.list_entries(
                        filter_=log_filter,
                        max_results=MAX_LOG_ENTRIES // len(RESOURCE_TYPE_FILTERS),
                        project_ids=[GCP_PROJECT],
                    )
                )
            except Exception as e:
                logger.error("Failed to query %s logs: %s", resource_type, e)
                continue

            for entry in entries:
                message = _extract_message(entry)
                if not message:
                    continue

                service = _extract_service_name(entry, resource_type)
                our_resource_type = _classify_resource_type(resource_type)

                # Check ignore patterns
                if should_ignore(service, message):
                    continue

                normalized = normalize_message(message)
                all_entries.append({
                    "service": service,
                    "resource_type": our_resource_type,
                    "raw": message,
                    "normalized": normalized,
                    "timestamp": entry.timestamp or datetime.now(timezone.utc),
                })

        return all_entries[:MAX_LOG_ENTRIES]

    def _group_by_pattern(self, entries: list[dict]) -> dict:
        """Group log entries by normalized pattern hash."""
        groups = {}
        for entry in entries:
            pattern_hash = compute_pattern_hash(entry["service"], entry["normalized"])
            if pattern_hash not in groups:
                groups[pattern_hash] = {
                    "service": entry["service"],
                    "resource_type": entry["resource_type"],
                    "normalized": entry["normalized"],
                    "sample": entry["raw"],
                    "count": 0,
                    "earliest_timestamp": entry["timestamp"],
                    "latest_timestamp": entry["timestamp"],
                }
            groups[pattern_hash]["count"] += 1
            if entry["timestamp"] < groups[pattern_hash]["earliest_timestamp"]:
                groups[pattern_hash]["earliest_timestamp"] = entry["timestamp"]
            if entry["timestamp"] > groups[pattern_hash]["latest_timestamp"]:
                groups[pattern_hash]["latest_timestamp"] = entry["timestamp"]

        return groups

    def _is_spike(self, pattern: dict, current_count: int) -> bool:
        """Check if current count represents a spike vs. rolling average."""
        if current_count < SPIKE_MIN_COUNT:
            return False
        avg = self._rolling_average(pattern.get("rolling_counts", {}))
        if avg <= 0:
            return False
        return current_count / avg >= SPIKE_MULTIPLIER

    def _rolling_average(self, rolling_counts: dict) -> float:
        """Compute rolling average from rolling_counts map."""
        if not rolling_counts:
            return 0.0
        values = list(rolling_counts.values())
        return sum(values) / len(values)

    def _check_auto_resolve(self) -> None:
        """Check all active patterns for auto-resolution."""
        patterns = self.firestore_adapter.get_patterns_for_auto_resolve()
        resolved = []

        for pattern in patterns:
            hours_silent = self.firestore_adapter.check_auto_resolve(pattern)
            if hours_silent is not None:
                self.firestore_adapter.auto_resolve_pattern(
                    pattern["pattern_id"], hours_silent
                )
                resolved.append({
                    "service": pattern.get("service", "unknown"),
                    "normalized_message": pattern.get("normalized_message", ""),
                    "hours_silent": hours_silent,
                })

        if resolved:
            msg = format_auto_resolved_alert(resolved)
            success = self.discord.send_message(msg)
            self.firestore_adapter.log_discord_alert(
                alert_type="resolved_batch",
                content=msg,
                success=success,
                metadata={"count": len(resolved)},
            )

    def _run_daily_digest(self) -> None:
        """Generate and send the daily digest."""
        logger.info("Running daily digest")

        # Dedup sweep
        if get_llm_enabled():
            active = self.firestore_adapter.get_active_patterns()
            if len(active) >= 2:
                duplicates = find_duplicate_patterns(active[:50], active[:50])
                for dup in duplicates:
                    for idx in dup.duplicate_indices:
                        if idx < len(active):
                            canonical = active[dup.canonical_index] if dup.canonical_index < len(active) else None
                            if canonical:
                                self.firestore_adapter.merge_pattern(
                                    active[idx]["pattern_id"],
                                    canonical["pattern_id"],
                                    dup.reason,
                                )

        # Gather stats
        active = self.firestore_adapter.get_active_patterns()
        per_service: dict[str, int] = {}
        total_24h = 0
        new_count = 0
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

        for p in active:
            svc = p.get("service", "unknown")
            # Sum rolling counts from last 24h
            for ts_key, count in p.get("rolling_counts", {}).items():
                try:
                    if datetime.fromisoformat(ts_key) >= cutoff:
                        per_service[svc] = per_service.get(svc, 0) + count
                        total_24h += count
                except (ValueError, TypeError):
                    pass
            if p.get("status") == "new":
                first_seen = p.get("first_seen", "")
                try:
                    if datetime.fromisoformat(first_seen) >= cutoff:
                        new_count += 1
                except (ValueError, TypeError):
                    pass

        # Count resolved in last 24h from discord_alerts
        cutoff_iso = cutoff.isoformat()
        resolved_docs = (
            self.db.collection("discord_alerts")
            .where("alert_type", "==", "resolved_batch")
            .where("timestamp", ">=", cutoff_iso)
            .stream()
        )
        resolved_count = sum(doc.to_dict().get("count", 0) for doc in resolved_docs)

        msg = format_daily_digest(
            total_errors=total_24h,
            new_patterns=new_count,
            resolved_patterns=resolved_count,
            active_patterns=len(active),
            per_service_counts=per_service,
        )
        success = self.discord.send_message(msg)
        self.firestore_adapter.log_discord_alert(
            alert_type="daily_digest",
            content=msg,
            success=success,
            metadata={"total_24h": total_24h, "active": len(active)},
        )
        logger.info("Daily digest sent: %d errors, %d active patterns", total_24h, len(active))


def main():
    """Entry point for Cloud Run Job."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("Error monitor starting (project=%s, digest=%s)", GCP_PROJECT, get_digest_mode())

    try:
        monitor = ErrorMonitor()
        monitor.run()
    except Exception as e:
        logger.exception("Error monitor failed: %s", e)
        sys.exit(1)

    logger.info("Error monitor completed successfully")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-error-bot && python -m pytest tests/unit/services/error_monitor/test_monitor.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/error_monitor/monitor.py tests/unit/services/error_monitor/test_monitor.py
git commit -m "feat(error-monitor): add monitor orchestrator (main entry point)"
```

---

## Task 8: Pulumi Infrastructure

**Files:**
- Create: `infrastructure/modules/error_monitor.py`
- Modify: `infrastructure/__main__.py`
- Modify: `infrastructure/config.py`
- Modify: `infrastructure/modules/iam/backend_sa.py`

- [ ] **Step 1: Add ErrorMonitorConfig to infrastructure config**

Read `infrastructure/config.py` and add a new config class following existing patterns (like `RunnerManagerConfig`).

Add to `infrastructure/config.py`:

```python
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
```

- [ ] **Step 2: Add logging.viewer role to backend SA**

Read `infrastructure/modules/iam/backend_sa.py` and add:

```python
    "logging_viewer": "roles/logging.viewer",  # Error monitor: read Cloud Logging
```

to the existing roles dict/list for the backend service account.

- [ ] **Step 3: Create error monitor Pulumi module**

Create `infrastructure/modules/error_monitor.py`:

```python
"""Pulumi module for error monitor infrastructure.

Creates:
- Cloud Run Job (nomad-error-monitor) — shares karaoke-backend image
- Cloud Scheduler (error-monitor-trigger) — every 15 minutes
- Cloud Scheduler (error-monitor-daily-digest) — 08:00 UTC daily
- Service Account (error-monitor-scheduler) — for scheduler auth
"""

import pulumi
import pulumi_gcp as gcp

from infrastructure.config import (
    PROJECT_ID,
    REGION,
    ErrorMonitorConfig as Config,
)


def create_error_monitor(
    backend_image: pulumi.Input[str],
    backend_sa_email: pulumi.Input[str],
    depends_on: list | None = None,
):
    """Create error monitor infrastructure.

    Args:
        backend_image: Docker image URL for karaoke-backend
        backend_sa_email: Backend service account email (for job execution)
        depends_on: Resources that must be created first
    """

    # 1. Service Account for Cloud Scheduler
    scheduler_sa = gcp.serviceaccount.Account(
        "error-monitor-scheduler-sa",
        account_id=Config.SCHEDULER_SA_NAME,
        display_name="Error Monitor Scheduler",
        project=PROJECT_ID,
    )

    # 2. Cloud Run Job
    job = gcp.cloudrunv2.Job(
        "error-monitor-job",
        name=Config.JOB_NAME,
        location=REGION,
        template=gcp.cloudrunv2.JobTemplateArgs(
            template=gcp.cloudrunv2.JobTemplateTemplateArgs(
                service_account=backend_sa_email,
                max_retries=Config.MAX_RETRIES,
                timeout=Config.TIMEOUT,
                containers=[
                    gcp.cloudrunv2.JobTemplateTemplateContainerArgs(
                        image=backend_image,
                        args=["python", "-m", "backend.services.error_monitor.monitor"],
                        resources=gcp.cloudrunv2.JobTemplateTemplateContainerResourcesArgs(
                            limits={
                                "memory": Config.MEMORY,
                                "cpu": Config.CPU,
                            },
                        ),
                        envs=[
                            gcp.cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GCP_PROJECT_ID",
                                value=PROJECT_ID,
                            ),
                            gcp.cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="ENVIRONMENT",
                                value="production",
                            ),
                            gcp.cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="DISCORD_WEBHOOK_URL",
                                value_source=gcp.cloudrunv2.JobTemplateTemplateContainerEnvValueSourceArgs(
                                    secret_key_ref=gcp.cloudrunv2.JobTemplateTemplateContainerEnvValueSourceSecretKeyRefArgs(
                                        secret=f"projects/{PROJECT_ID}/secrets/discord-alert-webhook",
                                        version="latest",
                                    ),
                                ),
                            ),
                        ],
                    ),
                ],
            ),
        ),
        opts=pulumi.ResourceOptions(depends_on=depends_on or []),
    )

    # 3. Grant scheduler SA permission to invoke the job
    gcp.cloudrunv2.JobIamMember(
        "error-monitor-scheduler-invoker",
        name=job.name,
        location=REGION,
        role="roles/run.invoker",
        member=pulumi.Output.concat("serviceAccount:", scheduler_sa.email),
    )

    # 4. Cloud Scheduler — Monitor (every 15 min)
    job_uri = pulumi.Output.concat(
        "https://", REGION, "-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/",
        PROJECT_ID, "/jobs/", Config.JOB_NAME, ":run",
    )

    gcp.cloudscheduler.Job(
        "error-monitor-trigger",
        name="error-monitor-trigger",
        description="Trigger error monitor every 15 minutes",
        region=REGION,
        schedule=Config.MONITOR_SCHEDULE,
        time_zone="UTC",
        http_target=gcp.cloudscheduler.JobHttpTargetArgs(
            uri=job_uri,
            http_method="POST",
            oauth_token=gcp.cloudscheduler.JobHttpTargetOauthTokenArgs(
                service_account_email=scheduler_sa.email,
            ),
        ),
        retry_config=gcp.cloudscheduler.JobRetryConfigArgs(
            retry_count=1,
            max_retry_duration="60s",
        ),
    )

    # 5. Cloud Scheduler — Daily Digest (08:00 UTC)
    gcp.cloudscheduler.Job(
        "error-monitor-daily-digest",
        name="error-monitor-daily-digest",
        description="Trigger daily error digest at 08:00 UTC",
        region=REGION,
        schedule=Config.DIGEST_SCHEDULE,
        time_zone="UTC",
        http_target=gcp.cloudscheduler.JobHttpTargetArgs(
            uri=job_uri,
            http_method="POST",
            body=pulumi.Output.from_input(
                '{"overrides":{"containerOverrides":[{"env":[{"name":"DIGEST_MODE","value":"true"}]}]}}'
            ).apply(lambda s: s.encode("utf-8")),
            headers={"Content-Type": "application/json"},
            oauth_token=gcp.cloudscheduler.JobHttpTargetOauthTokenArgs(
                service_account_email=scheduler_sa.email,
            ),
        ),
        retry_config=gcp.cloudscheduler.JobRetryConfigArgs(
            retry_count=1,
            max_retry_duration="60s",
        ),
    )

    return {
        "job": job,
        "scheduler_sa": scheduler_sa,
    }
```

- [ ] **Step 4: Wire into infrastructure/__main__.py**

Read `infrastructure/__main__.py` and add the error monitor module call. Find where other modules are invoked and add:

```python
from infrastructure.modules.error_monitor import create_error_monitor

# ... after backend image and SA are defined ...
error_monitor = create_error_monitor(
    backend_image=backend_image_url,  # Use the actual variable name from __main__.py
    backend_sa_email=backend_sa.email,  # Use the actual variable name
)
```

The exact variable names depend on what's already in `__main__.py` — read it first and match the existing patterns.

- [ ] **Step 5: Commit**

```bash
git add infrastructure/modules/error_monitor.py infrastructure/__main__.py infrastructure/config.py infrastructure/modules/iam/backend_sa.py
git commit -m "feat(error-monitor): add Pulumi infrastructure (Cloud Run Job + Schedulers)"
```

---

## Task 9: Helper Scripts

**Files:**
- Create: `scripts/query-error-patterns.py`
- Create: `scripts/resolve-error-pattern.py`

- [ ] **Step 1: Create query-error-patterns.py**

Create `scripts/query-error-patterns.py`:

```python
#!/usr/bin/env python3
"""CLI to query error patterns from Firestore.

Usage:
    python scripts/query-error-patterns.py                    # All active
    python scripts/query-error-patterns.py --status new       # Only new
    python scripts/query-error-patterns.py --service karaoke-backend
    python scripts/query-error-patterns.py --hours 24         # Last 24h
    python scripts/query-error-patterns.py --json             # JSON output
"""

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta

from google.cloud import firestore


def main():
    parser = argparse.ArgumentParser(description="Query error patterns from Firestore")
    parser.add_argument("--status", help="Filter by status (new, acknowledged, known, muted, fixed, auto_resolved)")
    parser.add_argument("--service", help="Filter by service name")
    parser.add_argument("--hours", type=int, help="Only patterns seen in last N hours")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--project", default="nomadkaraoke", help="GCP project ID")
    args = parser.parse_args()

    db = firestore.Client(project=args.project)
    query = db.collection("error_patterns")

    if args.status:
        query = query.where("status", "==", args.status)
    else:
        # Default: active patterns only
        query = query.where("status", "in", ["new", "acknowledged", "known"])

    if args.service:
        query = query.where("service", "==", args.service)

    patterns = [doc.to_dict() for doc in query.stream()]

    # Filter by time if requested
    if args.hours:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat()
        patterns = [p for p in patterns if p.get("last_seen", "") >= cutoff]

    # Sort by last_seen descending
    patterns.sort(key=lambda p: p.get("last_seen", ""), reverse=True)

    if args.json:
        print(json.dumps(patterns, indent=2, default=str))
        return

    # Table output
    if not patterns:
        print("No matching patterns found.")
        return

    print(f"{'Status':<14} {'Service':<25} {'Count':>6} {'Last Seen':<20} Message")
    print("-" * 120)
    for p in patterns:
        status = p.get("status", "?")
        service = p.get("service", "?")[:24]
        count = p.get("total_count", 0)
        last_seen = p.get("last_seen", "?")[:19]
        message = p.get("normalized_message", "?")[:50]
        print(f"{status:<14} {service:<25} {count:>6} {last_seen:<20} {message}")

    print(f"\nTotal: {len(patterns)} pattern(s)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Create resolve-error-pattern.py**

Create `scripts/resolve-error-pattern.py`:

```python
#!/usr/bin/env python3
"""CLI to resolve error patterns in Firestore.

Usage:
    python scripts/resolve-error-pattern.py <pattern_id_prefix> --pr 42 --note "Fixed timeout"
    python scripts/resolve-error-pattern.py <pattern_id_prefix> --note "Added retry logic"
"""

import argparse
import sys
from datetime import datetime, timezone

from google.cloud import firestore


def find_pattern(db, prefix: str) -> dict | None:
    """Find a pattern by ID prefix."""
    docs = list(db.collection("error_patterns").stream())
    matches = [
        doc.to_dict() for doc in docs
        if doc.id.startswith(prefix)
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"Ambiguous prefix '{prefix}', matches {len(matches)} patterns:")
        for m in matches[:5]:
            print(f"  {m['pattern_id'][:16]}  {m.get('service', '?')}  {m.get('normalized_message', '?')[:60]}")
        return None
    return None


def main():
    parser = argparse.ArgumentParser(description="Resolve an error pattern")
    parser.add_argument("pattern_prefix", help="Pattern ID or prefix (at least 8 chars)")
    parser.add_argument("--pr", type=int, help="PR number that fixes this")
    parser.add_argument("--note", required=True, help="Description of the fix")
    parser.add_argument("--repo", default="nomadkaraoke/karaoke-gen", help="GitHub repo for PR URL")
    parser.add_argument("--project", default="nomadkaraoke", help="GCP project ID")
    args = parser.parse_args()

    db = firestore.Client(project=args.project)
    pattern = find_pattern(db, args.pattern_prefix)

    if not pattern:
        print(f"Pattern not found for prefix: {args.pattern_prefix}")
        sys.exit(1)

    pattern_id = pattern["pattern_id"]
    print(f"Resolving: {pattern.get('service', '?')} — {pattern.get('normalized_message', '?')[:80]}")

    fixed_by = {
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "note": args.note,
    }
    if args.pr:
        fixed_by["pr_url"] = f"https://github.com/{args.repo}/pull/{args.pr}"
        fixed_by["pr_display"] = f"{args.repo}#{args.pr}"

    db.collection("error_patterns").document(pattern_id).update({
        "status": "fixed",
        "fixed_by": fixed_by,
    })

    print(f"Resolved pattern {pattern_id[:16]}...")
    if args.pr:
        print(f"  PR: {fixed_by['pr_url']}")
    print(f"  Note: {args.note}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Make scripts executable**

Run:
```bash
chmod +x scripts/query-error-patterns.py scripts/resolve-error-pattern.py
```

- [ ] **Step 4: Commit**

```bash
git add scripts/query-error-patterns.py scripts/resolve-error-pattern.py
git commit -m "feat(error-monitor): add CLI scripts for querying and resolving patterns"
```

---

## Task 10: Update Slash Commands

**Files:**
- Modify: `/Users/andrew/Projects/nomadkaraoke/.claude/commands/prod-errors.md`
- Modify: `/Users/andrew/Projects/nomadkaraoke/.claude/commands/prod-health.md`
- Modify: `/Users/andrew/Projects/nomadkaraoke/.claude/commands/prod-investigate.md`
- Modify: `/Users/andrew/Projects/nomadkaraoke/.claude/commands/prod-review.md`
- Modify: `/Users/andrew/Projects/nomadkaraoke/.claude/commands/prod-known-issue.md`

**Note:** These files live in the workspace root, not the worktree. They are shared Claude Code commands, not repo code. Read each file, understand its current structure, and update to use `error_patterns` Firestore as the primary data source.

- [ ] **Step 1: Read all five current slash commands**

Read each command file to understand current structure before editing.

- [ ] **Step 2: Update /prod-errors**

Key changes:
- Primary source: `python scripts/query-error-patterns.py --json` (from karaoke-gen repo)
- Fallback: Cloud Logging direct query (if error_patterns is empty — first-run scenario)
- Group output by pattern status (new → acknowledged → known) instead of raw log categorization
- Include pattern_id prefix in output for easy `/prod-investigate` follow-up

- [ ] **Step 3: Update /prod-health**

Key changes:
- Add a section after health endpoint checks: "Error Patterns: X active (Y new in last hour)"
- Query: `python scripts/query-error-patterns.py --hours 1 --status new --json | python -c "import sys,json; print(len(json.load(sys.stdin)))"`

- [ ] **Step 4: Update /prod-investigate**

Key changes:
- First query `error_patterns` for the pattern matching the search term
- Show pattern history: first_seen, last_seen, total_count, status, rolling frequency
- Then query Cloud Logging for stack traces and detailed samples
- Show related incident if pattern has an incident_id

- [ ] **Step 5: Update /prod-review**

Key changes:
- Replace `known-issues.yaml` loading with `error_patterns` query
- Categorize: NEW (status=new), REGRESSIONS (status=new AND fixed_by is not null), IN PROGRESS (acknowledged), KNOWN (known/muted), FIXED (fixed in last 24h)
- Remove references to `known-issues.yaml`

- [ ] **Step 6: Update /prod-known-issue**

Key changes:
- Write to Firestore `error_patterns` (status: known/muted) instead of YAML
- Use `scripts/query-error-patterns.py --status known` to list
- Add/update via Python Firestore update (not YAML file editing)

- [ ] **Step 7: Commit slash command changes**

Note: These are in the workspace root, committed separately from the worktree.

```bash
cd /Users/andrew/Projects/nomadkaraoke
git add .claude/commands/prod-errors.md .claude/commands/prod-health.md .claude/commands/prod-investigate.md .claude/commands/prod-review.md .claude/commands/prod-known-issue.md
git commit -m "feat(error-monitor): update prod slash commands to use error_patterns Firestore"
```

---

## Task 11: Run Full Test Suite and Verify

- [ ] **Step 1: Run all error monitor tests**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-error-bot
python -m pytest tests/unit/services/error_monitor/ -v
```

Expected: All tests PASS.

- [ ] **Step 2: Run the full project test suite**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-error-bot
make test 2>&1 | tail -n 100
```

Expected: No regressions. All existing tests still pass.

- [ ] **Step 3: Verify Pulumi preview**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-error-bot/infrastructure
pulumi preview 2>&1 | tail -n 50
```

Expected: Shows new resources to create (Cloud Run Job, 2 Cloud Schedulers, Service Account, IAM binding). No unexpected changes to existing resources.

- [ ] **Step 4: Commit any final fixes**

If any tests failed or Pulumi preview showed issues, fix and commit.

---

## Task 12: Migrate known-issues.yaml

- [ ] **Step 1: Read current known-issues.yaml**

Read `/Users/andrew/Projects/nomadkaraoke/.claude/known-issues.yaml` to understand existing entries.

- [ ] **Step 2: Write migration script**

Create a one-time migration script (can be deleted after running):

```python
#!/usr/bin/env python3
"""One-time migration: known-issues.yaml → Firestore error_patterns."""

import hashlib
import yaml
from google.cloud import firestore
from datetime import datetime, timezone


def main():
    with open("/Users/andrew/Projects/nomadkaraoke/.claude/known-issues.yaml") as f:
        data = yaml.safe_load(f)

    db = firestore.Client(project="nomadkaraoke")
    now = datetime.now(timezone.utc).isoformat()

    for issue in data.get("issues", []):
        pattern_id = hashlib.sha256(
            f"{issue.get('service', 'unknown')}::{issue.get('pattern', '')}".encode()
        ).hexdigest()

        status = "known" if issue.get("status") in ("known-acceptable", "investigating", "backlog") else "muted"
        if issue.get("status") == "fixed":
            status = "fixed"

        doc = {
            "pattern_id": pattern_id,
            "service": issue.get("service", "unknown"),
            "resource_type": "cloud_run_service",  # Most are backend
            "normalized_message": issue.get("pattern", "")[:200],
            "sample_message": issue.get("description", ""),
            "first_seen": issue.get("fixed_date", now),
            "last_seen": issue.get("fixed_date", now),
            "total_count": 0,
            "rolling_counts": {},
            "status": status,
            "severity": "P3",
            "notes": issue.get("reason", issue.get("description", "")),
            "alerted_at": None,
            "incident_id": None,
            "merged_into": None,
            "fixed_by": {"note": issue.get("fixed_by", ""), "resolved_at": issue.get("fixed_date", "")} if issue.get("status") == "fixed" else None,
        }

        db.collection("error_patterns").document(pattern_id).set(doc, merge=True)
        print(f"Migrated: {issue.get('id', '?')} → {status}")

    print(f"\nMigrated {len(data.get('issues', []))} issues to Firestore")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run migration**

```bash
python scripts/migrate-known-issues.py
```

- [ ] **Step 4: Verify migration**

```bash
python scripts/query-error-patterns.py --status known
python scripts/query-error-patterns.py --status fixed
```

- [ ] **Step 5: Clean up**

Delete the migration script (one-time use):
```bash
rm scripts/migrate-known-issues.py
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore(error-monitor): migrate known-issues.yaml to Firestore"
```
