# Nomad Karaoke Error Monitor — Design Spec

> **Date:** 2026-04-12
> **Status:** Approved
> **Scope:** Error Monitor service for production error detection, normalization, dedup, alerting, and resolution tracking across all Nomad Karaoke services.
> **Reference:** Based on the Aquarius Error Bot system (`/Users/andrew/Projects/aquarius/docs/archive/error-bot-system-reference.md`)

---

## 1. System Overview

A Cloud Run Job that runs every 15 minutes, queries GCP Cloud Logging for errors across all production services, normalizes and deduplicates them, uses Gemini Flash to group related errors into incidents, sends alerts to Discord, tracks patterns in Firestore, and auto-resolves patterns that go silent.

### End-to-End Flow

```
Production Errors (Cloud Logging)
  |
  v
Error Monitor (Cloud Run Job, every 15 min)
  |-- Query Cloud Logging (ERROR severity, last 15 min)
  |   |-- cloud_run_revision: karaoke-backend, karaoke-decide, audio-separator
  |   |-- cloud_run_job: video-encoding-job, lyrics-transcription-job,
  |   |                  audio-separation-job, audio-download-job
  |   |-- cloud_function: gdrive-validator, runner_manager, backup_to_aws,
  |   |                   divebar_mirror, kn_data_sync, divebar_lookup,
  |   |                   encoding_worker_idle
  |   |-- gce_instance: encoding-worker-a, encoding-worker-b,
  |                     flacfetch-vm, divebar-sync-vm
  |-- Normalize messages (strip IDs, timestamps, emails, job IDs, GCS paths)
  |-- Group by pattern hash (SHA-256 of service::normalized_message)
  |-- Check Firestore for existing patterns
  |-- LLM dedup: check new patterns against existing (Gemini Flash)
  |-- If 2+ new patterns: LLM incident analysis (group by root cause)
  |-- Send Discord alerts (per-incident or per-pattern)
  |-- Auto-resolve stale patterns (frequency-aware thresholds)
  |
  v
Daily Digest (08:00 UTC, same job with DIGEST_MODE=true)
  |-- Dedup sweep of all active patterns
  |-- 24h summary: total errors, new patterns, frequency stats
  |-- Per-service breakdown
  |-- Send digest to Discord
```

### Monitored Resources

| Category | Resources |
|----------|-----------|
| **Cloud Run Services** | karaoke-backend, karaoke-decide, audio-separator |
| **Cloud Run Jobs** | video-encoding-job, lyrics-transcription-job, audio-separation-job, audio-download-job |
| **Cloud Functions** | gdrive-validator, runner_manager, backup_to_aws, divebar_mirror, kn_data_sync, divebar_lookup, encoding_worker_idle |
| **GCE Instances** | encoding-worker-a, encoding-worker-b, flacfetch-vm, divebar-sync-vm |

---

## 2. Log Collection

Four separate Cloud Logging queries, one per resource type, each scoped to:
- Severity: `>=ERROR`
- Time window: last 15 minutes (configurable via `LOOKBACK_MINUTES`)
- Max entries: 500 total across all queries

### Query Resource Types

| Resource Type | Filter Field | Values |
|---------------|-------------|--------|
| `cloud_run_revision` | `resource.labels.service_name` | karaoke-backend, karaoke-decide, audio-separator |
| `cloud_run_job` | `resource.labels.job_name` | video-encoding-job, lyrics-transcription-job, audio-separation-job, audio-download-job |
| `cloud_function` | `resource.labels.function_name` | gdrive-validator, runner_manager, backup_to_aws, divebar_mirror, kn_data_sync, divebar_lookup, encoding_worker_idle |
| `gce_instance` | `labels."compute.googleapis.com/resource_name"` | encoding-worker-a, encoding-worker-b, flacfetch-vm, divebar-sync-vm |

### Payload Handling

Three payload types (same as Aquarius):
- Structured JSON dict → extract message from `textPayload` or `jsonPayload.message`
- Plain text string → use directly
- HTTP request logs (no payload) → skip

---

## 3. Message Normalization

### Standard Normalizers (from Aquarius)

| Pattern | Placeholder |
|---------|-------------|
| UUIDs | `<ID>` |
| ISO timestamps | `<TS>` |
| Epoch timestamps | `<EPOCH>` |
| Hex/alphanumeric IDs (8+ chars) | `<ID>` |
| Email addresses | `<EMAIL>` |
| IP addresses | `<IP>` |
| URLs | domain only |
| Numbers (4+ digits) | `<NUM>` |

### NK-Specific Normalizers

| Pattern | Placeholder |
|---------|-------------|
| Job IDs (`/jobs/<id>`) | `/jobs/<ID>` |
| GCS paths (`gs://nomadkaraoke-*/**`) | `gs://<BUCKET>/<PATH>` |
| Firebase UIDs | `<UID>` |
| Audio file references (hashes, filenames) | `<FILE>` |
| Firestore document paths | `<DOC_PATH>` |

### Pattern Hash

`SHA-256(service::normalized_message)` — deterministic, collision-resistant.

---

## 4. LLM Analysis

### Model & Provider

- **Model:** Gemini 2.0 Flash via Vertex AI
- **Auth:** Application Default Credentials (service account)
- **Temperature:** 0.2 for incident analysis, 0.1 for dedup
- **Retry:** Exponential backoff, 3 attempts, 2s base delay
- **Fallback:** If LLM unavailable, group by service name (no root cause analysis)

### Incident Grouping

When 2+ new patterns are detected, the LLM analyzes them together. The system prompt provides NK-specific service dependency context:

```
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
```

### Output Format (JSON)

```json
{
  "incidents": [
    {
      "title": "max 60 chars",
      "root_cause": "one-liner",
      "severity": "P0|P1|P2|P3",
      "suggested_fix": "actionable, 1-2 sentences",
      "primary_service": "upstream root cause service",
      "pattern_indices": [1, 3]
    }
  ]
}
```

### Severity Levels

- **P0:** Service completely down, all jobs failing, payment processing broken
- **P1:** Major pipeline stage broken (audio separation, lyrics, encoding), auth failures
- **P2:** Degraded but functional — intermittent errors, slow responses, single-job failures
- **P3:** Minor — deprecation warnings, cleanup failures, spot VM preemptions

### Duplicate Detection

Identifies near-duplicate patterns that regex normalization missed (different phrasing of same error). Only merges new patterns into existing canonical ones to preserve alert history.

---

## 5. Firestore Data Model

### `error_patterns` Collection

| Field | Type | Description |
|-------|------|-------------|
| `pattern_id` | string | SHA-256(service::normalized_message) — document ID |
| `service` | string | Resource name (e.g., `karaoke-backend`, `video-encoding-job`) |
| `resource_type` | string | `cloud_run_service` / `cloud_run_job` / `cloud_function` / `gce_instance` |
| `normalized_message` | string | Regex-normalized error (max 200 chars) |
| `sample_message` | string | Raw error sample |
| `first_seen` | string (ISO) | First occurrence |
| `last_seen` | string (ISO) | Most recent occurrence |
| `total_count` | int | Cumulative count |
| `rolling_counts` | map | `{YYYY-MM-DDTHH:MM → count}` (7-day window) |
| `status` | string | `new` / `acknowledged` / `known` / `muted` / `fixed` / `auto_resolved` |
| `incident_id` | string? | Link to grouped incident |
| `severity` | string | `P0` / `P1` / `P2` / `P3` |
| `alerted_at` | string (ISO) | When Discord alert was sent |
| `notes` | string? | Free-text notes |
| `merged_into` | string? | Pattern ID this was merged into |
| `fixed_by` | map? | `{pr_display, pr_url, note, resolved_at}` |

### `error_incidents` Collection

| Field | Type | Description |
|-------|------|-------------|
| `incident_id` | string | `inc_YYYYMMDD_HHMM_<8-hex>` — document ID |
| `title` | string | Max 60 chars |
| `root_cause` | string? | LLM-identified |
| `severity` | string | `P0` / `P1` / `P2` / `P3` |
| `suggested_fix` | string? | Actionable next step |
| `primary_service` | string | Upstream root cause service |
| `pattern_ids` | list | Related pattern IDs |
| `used_llm` | bool | Whether LLM analysis was used |
| `created_at` | string (ISO) | Creation timestamp |
| `status` | string | `open` / `investigating` / `fixed` / `false_positive` |

### `discord_alerts` Collection

| Field | Type | Description |
|-------|------|-------------|
| Document ID | string | `alert_YYYYMMDD_HHMMSS_<8-hex>` |
| `timestamp` | string (ISO) | When sent |
| `alert_type` | string | `new_pattern` / `spike` / `incident` / `resolved_batch` / `daily_digest` |
| `content` | string | Message text (max 2000 chars) |
| `success` | bool | Whether webhook POST succeeded |
| + alert-specific metadata | varies | service, pattern, count, severity, etc. |

### Migration from `known-issues.yaml`

Existing entries in `known-issues.yaml` are migrated to `error_patterns` documents with status `known` or `muted`. The YAML file is then deprecated.

---

## 6. Discord Notifications

### Webhook

Reuses the existing `discord-alert-webhook` secret in Secret Manager.

### Alert Types

| Alert Type | Trigger | Emoji | Content |
|------------|---------|-------|---------|
| **New Pattern** | Unrecognized error | `🔴` | Service, resource type, normalized msg, sample, count, timestamps, `/prod-investigate` prompt |
| **Incident** | 2+ patterns grouped by LLM | `🚨`/`🔴`/`⚠️`/`🟡` (P0-P3) | Title, root cause, per-pattern details, suggested fix |
| **Auto-Resolved** | Pattern silent past threshold | `✅` | Pattern, hours silent, consolidated |
| **Spike** | Count > 5x rolling avg (min 5) | `⚠️` | Pattern, current vs. normal count |
| **Daily Digest** | 08:00 UTC | `📊` | 24h totals, per-service breakdown, frequency stats |

### Format Rules

- Compact inline formatting (no bullet lists)
- Show actual error message text, never summarize
- Include copy-paste `/prod-investigate` prompts
- Rate limited: max 10 messages per run
- 2000-char limit with truncation
- Timestamps in Eastern time (America/New_York, DST-aware)
- Honest when LLM fails: "LLM grouping unavailable"

---

## 7. Auto-Resolution

Frequency-aware thresholds based on historical occurrence rate:

```
threshold_hours = mean_interval_between_occurrences * AUTO_RESOLVE_MULTIPLIER
```

| Setting | Value |
|---------|-------|
| `AUTO_RESOLVE_MULTIPLIER` | 8 (99.97% confidence, Poisson) |
| `AUTO_RESOLVE_MIN_HOURS` | 6 |
| `AUTO_RESOLVE_MAX_HOURS` | 168 (1 week) |
| `AUTO_RESOLVE_FALLBACK_HOURS` | 48 (when <3 data points) |

High-frequency errors resolve faster; rare errors wait longer.

---

## 8. Known Issues / Ignore Patterns

Regex patterns for infrastructure noise that never triggers alerts:

- Cloud Run cold start / startup probe failures
- Spot VM preemption events (GitHub runners)
- GitHub runner startup/shutdown noise
- Encoding worker idle shutdown (expected)
- Cloud Scheduler retry transients
- Health check 404s from load balancers
- Ready condition changes during deploys

Defined in `known_issues.py`, easy to add to without code changes.

---

## 9. Infrastructure (Pulumi)

### New Resources (`infrastructure/modules/error_monitor.py`)

1. **Cloud Run Job** (`nomad-error-monitor`)
   - Image: existing karaoke-backend Docker image
   - Command: `["python", "-m", "backend.services.error_monitor.monitor"]`
   - Memory: 512Mi, CPU: 1, Timeout: 300s, Max retries: 0
   - Env vars: `GCP_PROJECT_ID`, `ENVIRONMENT`, `DISCORD_WEBHOOK_URL` (Secret Manager ref)

2. **Cloud Scheduler — Monitor** (`error-monitor-trigger`)
   - Schedule: `*/15 * * * *` (UTC)
   - Target: Cloud Run Jobs API `:run`
   - Auth: OAuth token via scheduler service account
   - Retry: 1 attempt, 60s max

3. **Cloud Scheduler — Daily Digest** (`error-monitor-daily-digest`)
   - Schedule: `0 8 * * *` (UTC)
   - Same job, with `DIGEST_MODE=true` environment override

4. **Service Account** (`error-monitor-scheduler`)
   - `roles/run.invoker` on the Cloud Run Job

### IAM for Error Monitor Job

The job runs under the existing backend service account (`karaoke-backend-sa`), which already has Firestore and Secret Manager access. Additional role needed:
- `roles/logging.viewer` (read Cloud Logging — not currently on the backend SA)

---

## 10. Slash Command Updates

All five `/prod-*` commands updated to use `error_patterns` as primary data source.

### `/prod-health`
Add error pattern summary (active count, new patterns in last hour) alongside existing health endpoint checks.

### `/prod-errors`
**Primary source becomes Firestore `error_patterns`** — fast, pre-deduplicated, pre-categorized. Cloud Logging becomes secondary, used only for stack traces when investigating a specific pattern.

### `/prod-investigate`
Query `error_patterns` first for pattern context (history, frequency, related incidents), then Cloud Logging for stack traces and detailed samples.

### `/prod-review`
Replace raw Cloud Logging categorization with pattern statuses: NEW, REGRESSIONS (previously fixed reoccurred), IN PROGRESS (acknowledged), KNOWN, VERIFIED FIXED. Remove `known-issues.yaml` dependency.

### `/prod-known-issue`
Write to Firestore `error_patterns` (status: `known`/`muted`) instead of YAML file.

### Helper Scripts

- `scripts/query-error-patterns.py` — CLI to query patterns from Firestore (used by slash commands)
- `scripts/resolve-error-pattern.py` — CLI to mark patterns as fixed with PR reference

---

## 11. Code Structure

```
backend/services/error_monitor/
├── __init__.py
├── monitor.py           # Entry point — query, normalize, group, analyze, alert
├── config.py            # Configuration constants, monitored services list
├── normalizer.py        # Message normalization (regex replacements)
├── known_issues.py      # Ignore pattern matching
├── llm_analysis.py      # Vertex AI Gemini incident grouping + dedup
├── discord.py           # Discord webhook client
└── firestore_adapter.py # CRUD for error_patterns, incidents, alerts
```

### Configuration (`config.py`)

| Setting | Value |
|---------|-------|
| `GCP_PROJECT` | `nomadkaraoke` |
| `LOOKBACK_MINUTES` | 15 |
| `MAX_LOG_ENTRIES` | 500 |
| `LLM_ANALYSIS_ENABLED` | true (env var) |
| `LLM_ANALYSIS_MODEL` | `gemini-2.0-flash` |
| `MIN_PATTERNS_FOR_ANALYSIS` | 2 |
| `SPIKE_MULTIPLIER` | 5.0 |
| `SPIKE_MIN_COUNT` | 5 |
| `MAX_DISCORD_MESSAGES_PER_RUN` | 10 |
| `MAX_ACTIVE_PATTERNS` | 500 |
| `ROLLING_WINDOW_DAYS` | 7 |
| `DISCORD_WEBHOOK_SECRET` | `discord-alert-webhook` |

---

## 12. Testing

### Unit Tests

```
tests/unit/services/error_monitor/
├── test_monitor.py              # Core grouping, windowing logic
├── test_normalizer.py           # Message normalization
├── test_llm_analysis.py         # LLM grouping, dedup
├── test_firestore_adapter.py    # CRUD, rolling counts, resolution
├── test_discord.py              # Message formatting
├── test_known_issues.py         # Ignore pattern matching
├── test_dedup.py                # Duplicate detection
└── test_monitor_orchestration.py # Full pipeline integration
```

### Integration Tests

- Test against Firestore emulator for pattern CRUD
- Test Cloud Logging query construction (mock the API client)
- Test Discord webhook formatting (mock HTTP)

---

## 13. Out of Scope (Future Work)

- **Auto-fixer (Nomad-fix):** Autonomous Claude Code agent that receives errors via Pub/Sub, creates fix PRs. Designed as a separate follow-up project.
- **Frontend admin dashboard:** Visual error monitoring UI. The slash commands serve this purpose for now.
- **Cross-repo error correlation:** Correlating errors between karaoke-gen and karaoke-decide Firestore. Both write to the same `error_patterns` collection, so patterns from both are visible together.
