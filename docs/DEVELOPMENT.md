# Development Guide

## Quick Start

### Prerequisites

- Python 3.12
- Node.js 20+
- Docker (for GCS emulator)
- Google Cloud SDK with Firestore emulator

```bash
# Install Firestore emulator
gcloud components install cloud-firestore-emulator
```

### Backend Setup

```bash
# Create and activate venv
python3.12 -m venv venv
source venv/bin/activate

# Install dependencies
poetry install

# Copy environment template
cp .env.example .env
# Edit .env with required API keys
```

### Frontend Setup

```bash
cd frontend
npm install
cp .env.example .env.local
# Edit .env.local with KARAOKE_ACCESS_TOKEN
```

## Running Locally

### Backend with Emulators

```bash
# Start emulators and backend
./scripts/run-backend-local.sh --with-emulators

# Backend runs at http://localhost:8000
# API docs at http://localhost:8000/docs
```

### Frontend

```bash
cd frontend
npm run dev
# Runs at http://localhost:3000
```

## Testing

**See [TESTING.md](TESTING.md) for complete testing guidance.**

Quick reference (run before committing):

```bash
make test 2>&1 | tail -n 500
```

This runs ALL tests (backend + frontend) and installs dependencies automatically.

For faster iteration, run subsets:
- `make test-backend` - Backend only (~2 min)
- `make test-frontend` - Frontend only (~3 min)

TESTING.md covers: test types, CI requirements, Playwright usage, mocking guidelines, and coverage enforcement.

## Deployment

### Automatic (CI/CD)

Push to `main` triggers GitHub Actions:
1. Run tests
2. Build Docker image
3. Deploy to Cloud Run
4. Deploy frontend to Cloudflare Pages

### Infrastructure Changes (Pulumi)

**All infrastructure changes must go through PRs.** The CI pipeline automatically deploys Pulumi changes when PRs merge to main.

```bash
# Preview changes locally (requires GCP auth)
cd infrastructure
pulumi preview --stack nomadkaraoke/karaoke-gen-infrastructure/prod

# DO NOT run `pulumi up` locally - let CI handle deployment
# CI uses --skip-preview due to limited service account permissions
```

**What's managed by Pulumi:**
- Firestore database and indexes
- GCS buckets
- Cloud Run service configuration
- Cloud Tasks queues
- Secret Manager secrets
- Service accounts and IAM bindings
- Workload Identity Federation
- GCE instances (GitHub runners, encoding worker)

**Workflow:**
1. Make changes in the appropriate module under `infrastructure/`
   - `modules/` - Core GCP resources (database, storage, secrets, IAM, etc.)
   - `compute/` - VM definitions and startup scripts
   - `config.py` - Shared configuration constants
   - `__main__.py` - Entry point that wires modules together
2. Run `pulumi preview` locally to verify
3. Create PR and merge
4. CI deploys automatically

See `docs/LESSONS-LEARNED.md` for Pulumi CI gotchas.

### Manual Frontend Deploy

```bash
cd frontend
npm run build
npx wrangler pages deploy out
```

## Environment Variables

### Backend (.env)

```bash
# Required
AUDIOSHAKE_API_TOKEN=...
GENIUS_ACCESS_TOKEN=...
AUDIO_SEPARATOR_API_URL=...  # Modal API endpoint

# GCP (auto-configured in Cloud Run)
GOOGLE_CLOUD_PROJECT=...
GCS_BUCKET_NAME=...

# Auth
ADMIN_TOKENS=token1,token2
```

### Frontend (.env.local)

```bash
NEXT_PUBLIC_API_URL=http://localhost:8000

# For E2E tests (see frontend/.env.local.example)
KARAOKE_ACCESS_TOKEN=...     # Skip email enrollment
TESTMAIL_API_KEY=...         # testmail.app API key
TESTMAIL_NAMESPACE=...       # testmail.app namespace
```

## Key Scripts

| Script | Purpose |
|--------|---------|
| `make test` | Run all backend tests |
| `./scripts/run-backend-local.sh` | Start backend with emulators |
| `./scripts/start-emulators.sh` | Start emulators only |
| `./scripts/stop-emulators.sh` | Stop emulators |
| `./scripts/debug-job.sh <job_id>` | Debug a cloud job (status, logs, files) |
| `./scripts/get_job.py <job_id>` | Fetch job data from Firestore |
| `./scripts/fetch_job_logs.py <job_id>` | Fetch cloud job logs to local file |
| `./scripts/analyze_log_timing.py <log>` | Profile karaoke-gen performance from logs |
| `./scripts/benchmark_ffmpeg.py` | Isolated FFmpeg encoding benchmark |
| `./scripts/compare_local_vs_remote.py` | Compare local vs cloud karaoke-gen performance |

## Project Structure

```
karaoke-gen/
├── backend/
│   ├── api/routes/       # FastAPI endpoints
│   ├── models/           # Pydantic models
│   ├── services/         # Business logic
│   ├── workers/          # Background workers
│   └── tests/            # Backend tests
├── frontend/
│   ├── src/
│   │   ├── app/          # Next.js pages
│   │   ├── components/   # React components
│   │   └── lib/          # Utilities
│   └── tests/            # Frontend tests
├── infrastructure/       # Pulumi IaC
├── karaoke_gen/          # CLI package
└── scripts/              # Dev scripts
```

## Observability & Debugging

The backend exports OpenTelemetry traces to Google Cloud Trace. Use these for investigating production issues.

### Accessing Cloud Trace

**Cloud Console (easiest for browsing):**
```
https://console.cloud.google.com/traces/list?project=nomadkaraoke
```

**CLI queries:**
```bash
# List recent traces (requires `gcloud alpha` for trace commands)
# Easier to use the Console UI for trace browsing

# Find traces by job_id in logs, then correlate with Cloud Trace
gcloud logging read 'resource.type="cloud_run_revision" textPayload=~"JOB_ID"' \
  --project=nomadkaraoke --limit=50
```

### Key Traced Spans

| Span Name | What It Measures | Key Attributes |
|-----------|------------------|----------------|
| `lyrics_corrector.run` | Full lyrics correction pipeline | `word_count`, `reference_count` |
| `lyrics_corrector.find_anchors_and_gaps` | Anchor sequence matching | `anchor_count`, `gap_count` |
| `lyrics_corrector.process_corrections` | Agentic correction processing | `gap_count`, `total_proposals` |
| `anchor_search.find_anchors` | N-gram parallel processing | `transcription.text_length`, `timeout_seconds` |
| `agentic.propose_for_gap` | AI gap classification | `gap_id`, `word_count`, `gap_category` |
| `add-lyrics` | Adding reference lyrics | `job_id`, `source` |
| `generate-preview-video` | Video preview generation | `job_id`, `use_gce` |

### Investigating Slow Operations

1. **Find the trace**: Go to Cloud Trace console, filter by time range around the failure
2. **Look at waterfall**: Identify which span took longest
3. **Check attributes**: Each span has metadata (word counts, durations, etc.)
4. **Correlate with logs**: Use timestamp and job_id to find related logs

### Example Investigation Workflow

```bash
# 1. Find job failures in logs
gcloud logging read 'resource.type="cloud_run_revision" severity>=ERROR' \
  --project=nomadkaraoke --limit=20 --format="table(timestamp,textPayload)"

# 2. Get timeline for specific job
./scripts/debug-job.sh <job_id>

# 3. Check anchor search progress (time-based logging every 30s)
gcloud logging read 'resource.type="cloud_run_revision" "ANCHOR SEARCH: Progress"' \
  --project=nomadkaraoke --limit=20

# 4. Open Cloud Trace to see detailed timing breakdown
# Filter traces by time window when the job ran
```

### Debugging Scripts

| Script | Purpose |
|--------|---------|
| `./scripts/debug-job.sh <job_id>` | Show job status, timeline, logs, GCS files |
| `./scripts/get_job.py <job_id>` | Fetch raw job data from Firestore |
| `./scripts/fetch_job_logs.py <job_id>` | Download all logs for a job to local file |

### Adding New Traces

When adding new operations that may need investigation:

```python
from lyrics_transcriber.utils.tracing import create_span, add_span_attribute

with create_span("operation_name", {"initial": "attributes"}) as span:
    # ... do work ...
    if span:
        span.set_attribute("result_count", len(results))
```

For backend code:
```python
from backend.services.tracing import create_span

with create_span("operation_name", {"job_id": job_id}) as span:
    # ... do work ...
```

## Troubleshooting

### Emulators Won't Start

```bash
# Check if already running
lsof -i :8080  # Firestore
lsof -i :4443  # GCS

# Stop them
./scripts/stop-emulators.sh
```

### Docker Issues

```bash
# Ensure Docker is running
docker info

# For GCS emulator
docker pull fsouza/fake-gcs-server
```

### Test Failures

See [TESTING.md](TESTING.md#debugging-failed-tests) for debugging test failures, coverage issues, and CI troubleshooting.
