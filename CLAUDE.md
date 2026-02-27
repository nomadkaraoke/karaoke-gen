# Karaoke-Gen AI Assistant Guidelines

## Project Overview

**Karaoke video generation platform** - CLI tool and web service that creates professional karaoke videos with synchronized lyrics.

- **Production**: <https://gen.nomadkaraoke.com> (frontend), <https://api.nomadkaraoke.com> (backend)
- **CLI**: `karaoke-gen` (local), `karaoke-gen-remote` (cloud)
- **Repo**: <https://github.com/nomadkaraoke/karaoke-gen>

## Quick Reference

| What | Where |
|------|-------|
| Product vision & goals | `docs/PRODUCT-VISION.md` |
| Current status | `docs/README.md` |
| Architecture | `docs/ARCHITECTURE.md` |
| Dev setup & testing | `docs/DEVELOPMENT.md` |
| **Testing & code quality** | `docs/TESTING.md` |
| API reference | `docs/API.md` |
| Past learnings | `docs/LESSONS-LEARNED.md` |
| **Product communication** | `docs/PRODUCT-COMMUNICATION-GUIDE.md` |
| Brand style guide | `docs/BRAND-STYLE-GUIDE.md` |

## Tech Stack

- **Backend**: FastAPI on Cloud Run, Firestore, GCS, Secret Manager
- **Frontend**: Next.js on Cloudflare Pages
- **Processing**: Modal (GPU audio separation), AudioShake/Whisper (transcription)
- **Infra**: Pulumi IaC, GitHub Actions CI/CD

## Essential Rules

### Git Workflow
- **Never commit directly to `main`** - use `/new-worktree <description>` to start
- **Follow global workflow** - see `~/.claude/CLAUDE.md` for command sequence
- Create PR with summary, changes, testing info
- Merge only after CI passes

### Testing (Required)
**Before planning any implementation work**, read `docs/TESTING.md` and ensure your plan includes a thorough testing strategy that follows the guidance there. This includes:
- Which test types are needed (unit, integration, E2E)
- Where tests should be placed
- What mocking approach to use
- Coverage expectations
- **Production E2E tests for user-facing features** - see below

**Critical:** If your plan includes "Manual Testing" steps, convert them to automated Playwright E2E tests. Production E2E tests (in `frontend/e2e/production/`) are valuable even if only run once after deployment - they codify expected behavior and verify the deployed code works. See `docs/TESTING.md` for details.

**Before committing**, run all tests:
```bash
make test 2>&1 | tail -n 500
```

This single command:
- Installs dependencies automatically (backend + frontend)
- Runs all backend tests (unit, integration, emulator)
- Runs all frontend tests (Jest unit + Playwright E2E)
- Takes ~5-10 minutes

**All tests must pass.** Don't dismiss failures as "pre-existing" - investigate and fix them.

### Testing in Production (for Agents)

When asked to "test it yourself in prod" or verify a fix in production:

```bash
# Get admin token
export KARAOKE_ADMIN_TOKEN=$(gcloud secrets versions access latest --secret=admin-tokens --project=nomadkaraoke | cut -d',' -f1)

# Option 1: Use the debug template directly
cd frontend && node e2e/helpers/debug-prod-template.mjs

# Option 2: Copy template for customization (gitignored)
cp frontend/e2e/helpers/debug-prod-template.mjs frontend/test-my-issue.local.mjs
# Edit the script, then run it
node test-my-issue.local.mjs

# Option 3: Run existing production E2E tests
KARAOKE_ADMIN_TOKEN=$KARAOKE_ADMIN_TOKEN npx playwright test e2e/production/admin-dashboard.spec.ts
```

Files matching `test-*.local.*` and `debug-*.local.*` are gitignored - you can hardcode tokens in them safely.

See `docs/TESTING.md` § "Ad-Hoc Production Debugging" for full details.

### Version Bumping
- Bump `tool.poetry.version` in `pyproject.toml` for code changes
- Skip for docs-only changes

### Infrastructure
- **All GCP changes via Pulumi PRs** - changes in `infrastructure/` deploy automatically on merge to main
- Never modify GCP resources directly via console or `gcloud` CLI
- `gcloud` CLI for reading/debugging only (e.g., checking logs, SSH to VMs)
- Local `pulumi preview` works but `pulumi up` requires broader permissions than CI
- Stop and notify user on auth issues

## API Authentication (for Agents)

When you need to call backend APIs programmatically:

### Internal Worker Endpoints
Use the `X-Admin-Token` header with the token from Secret Manager:
```bash
# Trigger video worker
curl -X POST "https://api.nomadkaraoke.com/api/internal/workers/video" \
  -H "X-Admin-Token: $(gcloud secrets versions access latest --secret=admin-tokens --project=nomadkaraoke)" \
  -H "Content-Type: application/json" \
  -d '{"job_id": "YOUR_JOB_ID"}'

# Other internal endpoints follow the same pattern
```

### Firestore Direct Access
```bash
# Set the correct project
export GOOGLE_CLOUD_PROJECT=nomadkaraoke

# Use Python with google-cloud-firestore
python3 << 'EOF'
import os
os.environ['GOOGLE_CLOUD_PROJECT'] = 'nomadkaraoke'
from google.cloud import firestore
db = firestore.Client(project='nomadkaraoke')
# Query/update documents...
EOF
```

### GCE Encoding Worker
```bash
# Check health (includes wheel_version)
gcloud compute ssh encoding-worker --zone=us-central1-c --project=nomadkaraoke \
  --command="curl -s http://localhost:8080/health"

# Restart service (pick up new wheel after a deploy)
gcloud compute ssh encoding-worker --zone=us-central1-c --project=nomadkaraoke \
  --command="sudo systemctl restart encoding-worker"
```

**Job stuck at `encoding` status?** This happens when a Cloud Run deployment kills the poller mid-encoding. The GCE worker finishes but nobody receives the result. Recovery steps:

```bash
# 1. Check if the job is flagged by health monitoring
curl -s "https://api.nomadkaraoke.com/api/health/job-consistency" \
  -H "X-Admin-Token: $(gcloud secrets versions access latest --secret=admin-tokens --project=nomadkaraoke)" \
  | python3 -m json.tool | grep -A3 "encoding_stuck"

# 2. Check GCE worker status for the specific job
WORKER_URL=$(gcloud secrets versions access latest --secret=encoding-worker-url --project=nomadkaraoke)
WORKER_KEY=$(gcloud secrets versions access latest --secret=encoding-worker-api-key --project=nomadkaraoke)
curl -s "$WORKER_URL/status/YOUR_JOB_ID" -H "X-API-Key: $WORKER_KEY" | python3 -m json.tool

# 3. Re-trigger the video worker — it will rejoin the GCE poll if still running,
#    or pick up the cached result if already complete
curl -X POST "https://api.nomadkaraoke.com/api/internal/workers/video" \
  -H "X-Admin-Token: $(gcloud secrets versions access latest --secret=admin-tokens --project=nomadkaraoke)" \
  -H "Content-Type: application/json" \
  -d '{"job_id": "YOUR_JOB_ID"}'
```

Note: **SSH restart is not needed** for stuck encoding jobs since PR #413 (v0.119.6). The `/encode` endpoint is now idempotent — re-triggering the video worker is sufficient.

### What Doesn't Work
- `gcloud auth print-identity-token` - Wrong token type for internal endpoints
- Cloud Tasks without proper service account - Gets 401 from Cloud Run

## Documentation Maintenance

### Structure
```text
docs/
├── README.md          # Current status + navigation (UPDATE THIS)
├── ARCHITECTURE.md    # System design
├── DEVELOPMENT.md     # Dev setup, testing, deployment
├── API.md             # Backend API reference
├── LESSONS-LEARNED.md # Key insights for future agents
└── archive/           # Historical docs (YYYY-MM-DD-topic.md)
```

### Rules
1. **Before merging PRs**: Run `/docs-review` to check if docs need updating
2. **Periodically**: Run `/docs-maintain` to verify docs are organized
3. **For significant work**: Create `docs/archive/YYYY-MM-DD-topic.md`
4. **Add learnings**: Update `docs/LESSONS-LEARNED.md` with insights
5. **Keep status current**: Update `docs/README.md` when project state changes

### What Goes Where
- **README.md**: Current status, known issues, quick links
- **ARCHITECTURE.md**: System design, data flow, tech decisions
- **DEVELOPMENT.md**: Setup, testing, deployment, CI/CD
- **API.md**: Endpoints, request/response formats
- **LESSONS-LEARNED.md**: What worked, what didn't, gotchas
- **archive/**: Completed work, session summaries, old plans

## Related Projects

### flacfetch
Location: `/Users/andrew/Projects/flacfetch`

Workflow: Make changes on `main`, bump version, push, wait for CI, then `poetry update flacfetch` in karaoke-gen.

## Pre-PR Checklist

Follow global workflow (`~/.claude/CLAUDE.md`):

- [ ] Tests pass: `/test` (or `make test` / `npm run test:all`)
- [ ] Version bumped (if code changed)
- [ ] Docs checked: `/docs-review`
- [ ] Code reviewed: `/review` (CodeRabbit CLI)
- [ ] PR created: `/pr` (adds @coderabbitai ignore)
