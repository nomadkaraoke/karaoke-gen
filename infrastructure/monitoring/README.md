# Monitoring Infrastructure

This directory contains Cloud Monitoring dashboard and alerting configurations for the karaoke-gen backend.

---

## 🎯 Quick Start: Debug a Failed Job

**Bookmark this URL:**
```
https://console.cloud.google.com/logs/query;query=resource.type%3D%22cloud_run_revision%22%0Aresource.labels.service_name%3D%22karaoke-backend%22;duration=PT6H?project=nomadkaraoke
```

### How to investigate a job:

1. **Open the bookmarked URL** (Logs Explorer)

2. **Add your job ID to the query.** In the query box, add a third line with your job ID:
   ```
   resource.type="cloud_run_revision"
   resource.labels.service_name="karaoke-backend"
   "[job:YOUR_JOB_ID"
   ```
   (Replace `YOUR_JOB_ID` with the actual ID, e.g. `"[job:8f03f0ac"`)

3. **Click "Run query"** or press `Shift+Enter`

4. **Enable "Stream logs"** (toggle at top right) for real-time updates

### What you'll see:
- `WORKER_START` - When each worker (audio/lyrics/screens/video) begins
- `WORKER_END status=success/error` - When it finishes with duration
- All log messages from that job's processing
- Any error tracebacks

### Example queries:

**All logs for job `8f03f0ac`:**
```
resource.type="cloud_run_revision"
resource.labels.service_name="karaoke-backend"
"[job:8f03f0ac"
```

**Only errors for a job:**
```
resource.type="cloud_run_revision"
resource.labels.service_name="karaoke-backend"
"[job:8f03f0ac"
severity>=ERROR
```

**Only worker start/end events:**
```
resource.type="cloud_run_revision"
resource.labels.service_name="karaoke-backend"
"[job:8f03f0ac"
("WORKER_START" OR "WORKER_END")
```

---

## Dashboard

The `dashboard.json` file defines a Cloud Monitoring dashboard with the following panels:

### Overview Metrics
- **Jobs Created (Last Hour)**: Count of jobs created
- **Cloud Run Request Count**: Request rate to the backend
- **Cloud Run Error Rate**: Percentage of non-2xx responses

### Performance Metrics
- **Request Latency (p95)**: 95th percentile response time
- **Cloud Run Instance Count**: Active container instances
- **CPU Utilization**: CPU usage across instances
- **Memory Utilization**: Memory usage across instances

### Cloud Tasks
- **Queue Depth**: Number of tasks waiting in each queue
- **Execution Rate**: Task processing rate per queue

### Logs
- **Worker Logs**: Searchable by `job_id` 
- **Error Logs**: All ERROR-level logs from the backend

## Importing the Dashboard

To import the dashboard into Cloud Monitoring:

```bash
# Using gcloud CLI
gcloud monitoring dashboards create --config-from-file=infrastructure/monitoring/dashboard.json --project=nomadkaraoke
```

Or manually:
1. Go to Cloud Console → Monitoring → Dashboards
2. Click "Create Dashboard"
3. Click "JSON Editor" and paste contents of `dashboard.json`

## Alert Policies

Alert policies are defined in `infrastructure/__main__.py` as Pulumi resources:

### High Error Rate
- **Condition**: >10% of requests return errors for 5+ minutes
- **Severity**: Warning
- **Auto-close**: 1 hour after resolution

### Queue Backlog
- **Condition**: >50 tasks in any queue for 10+ minutes  
- **Severity**: Warning
- **Auto-close**: 30 minutes after resolution

### High Memory Usage
- **Condition**: >85% memory utilization for 5+ minutes
- **Severity**: Warning
- **Auto-close**: 30 minutes after resolution

### Service Unavailable
- **Condition**: No healthy Cloud Run instances for 5+ minutes
- **Severity**: Critical
- **Auto-close**: 10 minutes after resolution

## Notification Channels

Notification channels are managed as Pulumi IaC in
[`infrastructure/modules/monitoring.py`](../modules/monitoring.py) — see
`create_notification_channels()` and the `notification_channels=...` arg
attached to every alert policy.

**Current channel:** one email channel (`alerts-email`) delivering to the
project owner. Cloud Monitoring puts new email channels in PENDING state and
sends a one-time confirmation link to the address; the channel stays inactive
until the link is clicked.

If alerts seem to be firing but not delivering, check:

```bash
gcloud alpha monitoring channels list --project=nomadkaraoke \
  --format='table(displayName,type,verificationStatus,enabled)'
```

`verificationStatus=VERIFIED` is the active state.

### Adding a second channel (e.g. Discord)

Discord supports Slack-compatible webhooks: append `/slack` to a Discord
webhook URL and Discord parses Slack-formatted payloads. To add a Discord
channel alongside email:

1. Create a webhook in Discord (Server settings → Integrations → Webhooks).
2. Store the URL in Secret Manager (the project already has
   `discord-alert-webhook` for the error-monitor service).
3. In `monitoring.py`, add a second entry to `create_notification_channels()`:

   ```python
   channels["discord"] = gcp.monitoring.NotificationChannel(
       "alerts-discord",
       display_name="Alerts (Discord)",
       type="slack",
       sensitive_labels=gcp.monitoring.NotificationChannelSensitiveLabelsArgs(
           auth_token=discord_webhook_url + "/slack",
       ),
       enabled=True,
   )
   ```

4. `pulumi up`. All alert policies pick up the new channel automatically
   because they iterate `channels.values()`.

## Log-Based Metrics

The structured logging in `backend/services/structured_logging.py` outputs metrics as JSON log entries. You can create log-based metrics to track:

1. Go to Cloud Console → Logging → Log-based metrics
2. Create new metric with filter:

```
resource.type="cloud_run_revision"
resource.labels.service_name="karaoke-backend"
jsonPayload.metric_name="jobs_total"
```

3. Set extraction field to `jsonPayload.metric_value`

Repeat for other metrics:
- `worker_invocations_total`
- `job_stage_duration_seconds`
- `external_api_calls_total`
- `external_api_duration_seconds`
- `gcs_operations_total`

## Useful Log Queries

### Find all logs for a specific job:
```
resource.type="cloud_run_revision"
resource.labels.service_name="karaoke-backend"
jsonPayload.job_id="YOUR_JOB_ID"
```

### Find worker errors:
```
resource.type="cloud_run_revision"
resource.labels.service_name="karaoke-backend"
textPayload:"WORKER_END" AND textPayload:"status=error"
```

### Find slow workers (>5 min):
```
resource.type="cloud_run_revision"
resource.labels.service_name="karaoke-backend"
textPayload:"WORKER_END" AND textPayload:"duration=" 
```

Then add duration filter in the UI.

## Cloud Trace Integration

Structured logs include trace correlation fields:
- `logging.googleapis.com/trace`: Links to Cloud Trace
- `logging.googleapis.com/spanId`: Current span ID

To view correlated traces:
1. Find a log entry in Cloud Logging
2. Click the "View in Trace" link in the log entry
3. See the full distributed trace across services

