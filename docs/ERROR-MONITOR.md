# Error Monitor — Operational Reference

Production error monitoring service that runs every 15 minutes, queries Cloud Logging across all Nomad Karaoke services, normalizes and deduplicates errors, uses Gemini Flash for incident grouping, sends Discord alerts, and auto-resolves silent patterns.

**Based on:** The Aquarius Error Bot system (`/Users/andrew/Projects/aquarius/docs/archive/error-bot-system-reference.md`)

---

## How It Works

```
Cloud Logging (all services, every 15 min)
  → Normalize messages (strip IDs, timestamps, emails, GCS paths)
  → Group by pattern hash (SHA-256 of service::normalized_message)
  → Check Firestore for existing patterns (new vs known)
  → LLM dedup: merge near-duplicates (Gemini Flash)
  → LLM incident analysis: group by root cause (if 2+ new patterns)
  → Discord alerts (new patterns, spikes, auto-resolved, daily digest)
  → Auto-resolve patterns silent past frequency-aware threshold
```

**Daily Digest:** 08:00 UTC — 24h summary with per-service breakdown.

## Monitored Resources

All resources log as `cloud_run_revision` (Gen2 Cloud Functions run on Cloud Run):

| Category | Resources |
|----------|-----------|
| **Cloud Run Services** | karaoke-backend, karaoke-decide, audio-separator |
| **Gen2 Cloud Functions** | gdrive-validator, github-runner-manager, backup-to-aws, divebar-mirror, kn-data-sync, divebar-lookup, encoding-worker-idle-shutdown |
| **Cloud Run Jobs** | video-encoding-job, lyrics-transcription-job, audio-separation-job, audio-download-job |
| **GCE Instances** | encoding-worker-a, encoding-worker-b, flacfetch-vm, divebar-sync-vm |

**Important:** Gen2 Cloud Functions appear as `cloud_run_revision` in Cloud Logging, NOT `cloud_function`. Their service names use hyphens (e.g., `encoding-worker-idle-shutdown`), not underscores.

## Infrastructure

| Resource | Name | Schedule |
|----------|------|----------|
| Cloud Run Job | `nomad-error-monitor` | — |
| Cloud Scheduler | `error-monitor-trigger` | `*/15 * * * *` (UTC) |
| Cloud Scheduler | `error-monitor-daily-digest` | `0 8 * * *` (UTC) |
| Service Account | `error-monitor-scheduler@nomadkaraoke.iam.gserviceaccount.com` | — |

Job runs as `karaoke-backend@nomadkaraoke.iam.gserviceaccount.com` (shared backend SA with `roles/logging.viewer`).

**Pulumi module:** `infrastructure/modules/error_monitor.py`

## Firestore Collections

| Collection | Purpose | Key Fields |
|------------|---------|------------|
| `error_patterns` | One doc per normalized error | pattern_id, service, status, total_count, rolling_counts, severity |
| `error_incidents` | Groups of related patterns | incident_id, title, root_cause, severity, pattern_ids |
| `discord_alerts` | Audit trail of Discord messages | alert_type, content, success, timestamp |

**Pattern statuses:** `new` → `acknowledged` → `known` / `muted` / `fixed` / `auto_resolved`

## Code Structure

```
backend/services/error_monitor/
├── __init__.py
├── config.py            # Constants, monitored services, env var overrides
├── normalizer.py        # Regex normalization + SHA-256 pattern hashing
├── known_issues.py      # 8 ignore patterns for infrastructure noise
├── firestore_adapter.py # CRUD for patterns, incidents, alerts
├── discord.py           # Webhook client + 5 alert formatters
├── llm_analysis.py      # Gemini Flash incident grouping + dedup
└── monitor.py           # Orchestrator entry point

scripts/
├── query-error-patterns.py   # CLI: query patterns from Firestore
└── resolve-error-pattern.py  # CLI: mark patterns as fixed
```

**Entry point:** `python -m backend.services.error_monitor.monitor`

**Tests:** `tests/unit/services/error_monitor/` (310 tests across 7 files)

## Slash Commands

All `/prod-*` commands use `error_patterns` Firestore as primary data source:

| Command | What It Does |
|---------|-------------|
| `/prod-health` | Quick health check + error pattern summary |
| `/prod-errors` | Summarize patterns by priority (P0-P3) |
| `/prod-investigate <pattern>` | Deep dive — Firestore context + Cloud Logging stack traces |
| `/prod-review` | Full review — categorize as NEW/REGRESSION/KNOWN/FIXED |
| `/prod-known-issue` | Manage patterns: mark as known/muted, resolve with PR |

## Common Operations

### Manual trigger

```bash
gcloud run jobs execute nomad-error-monitor --region=us-central1 --project=nomadkaraoke --wait
```

### Check recent patterns

```bash
python scripts/query-error-patterns.py                    # All active
python scripts/query-error-patterns.py --status new       # New only
python scripts/query-error-patterns.py --service karaoke-backend --hours 24
python scripts/query-error-patterns.py --json             # JSON for scripting
```

### Resolve a pattern

```bash
python scripts/resolve-error-pattern.py <pattern_id_prefix> --pr 42 --note "Fixed timeout"
```

### Check job execution logs

```bash
gcloud run jobs executions list --job=nomad-error-monitor --region=us-central1 --project=nomadkaraoke --limit=5

# Logs for specific execution
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="nomad-error-monitor" AND labels."run.googleapis.com/execution_name"="<EXECUTION_NAME>"' --project=nomadkaraoke --limit=50 --format=json --freshness=1h
```

### Discord webhook test

```bash
# Get webhook URL
gcloud secrets versions access latest --secret=discord-alert-webhook --project=nomadkaraoke

# Test post
curl -X POST "<webhook_url>" -H "Content-Type: application/json" -d '{"content": "Test from error monitor"}'
```

## Configuration

All in `backend/services/error_monitor/config.py`:

| Setting | Value | Override |
|---------|-------|---------|
| `GCP_PROJECT` | `nomadkaraoke` | `GCP_PROJECT_ID` env var |
| `LOOKBACK_MINUTES` | 15 | hardcoded |
| `MAX_LOG_ENTRIES` | 500 | hardcoded |
| `LLM_ANALYSIS_MODEL` | `gemini-2.0-flash` | `LLM_ANALYSIS_MODEL` env var |
| `LLM_ANALYSIS_ENABLED` | true | `LLM_ANALYSIS_ENABLED` env var |
| `AUTO_RESOLVE_MULTIPLIER` | 8 | hardcoded (99.97% Poisson confidence) |
| `AUTO_RESOLVE_MIN/MAX_HOURS` | 6 / 168 | hardcoded |
| `MAX_DISCORD_MESSAGES_PER_RUN` | 10 | hardcoded |
| `DISCORD_WEBHOOK_SECRET` | `discord-alert-webhook` | Secret Manager |

## Troubleshooting

**Monitor finds 0 errors but you know there are errors:**
- Check if the service name matches exactly (Gen2 functions use hyphens, not underscores)
- Verify the 15-min lookback window covers when errors occurred
- Check `known_issues.py` — the error might be in the ignore list

**Discord alerts not sending:**
- Verify `discord-alert-webhook` secret exists and contains a valid URL
- Check `DISCORD_WEBHOOK_URL` env var on the Cloud Run Job
- Look for error logs in the job execution

**Patterns not auto-resolving:**
- Only `new` and `acknowledged` patterns are candidates
- Threshold is frequency-aware: high-frequency errors resolve faster (min 6h), rare errors wait longer (max 168h / 1 week)
- Fallback: 48h when <3 data points

## Design Documents

- **Design spec:** `docs/archive/2026-04-12-error-monitor-design.md`
- **Implementation plan:** `docs/archive/2026-04-12-error-monitor-plan.md`

## Future Work

- **Auto-fixer (Nomad-fix):** Autonomous Claude Code agent that receives errors via Pub/Sub, creates fix PRs. Follow-up project.
- **Frontend admin dashboard:** Visual error monitoring UI.
