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

### Backend Tests

```bash
# Run all tests (required before commit)
make test 2>&1 | tail -n 500

# This runs in order:
# 1. Unit tests with coverage
# 2. Backend unit tests
# 3. E2E integration tests with emulators
```

**Important**: Set 10 minute timeout. Tests can take a while with emulators.

### Frontend Tests

```bash
cd frontend
npm run test:all 2>&1 | tail -n 200

# Individual commands:
npm run test:unit   # Jest unit tests
npm run test:e2e    # Playwright E2E tests
```

### Test Architecture

```
┌─────────────────────────────────┐
│   Unit Tests (Mocked)           │  Fast, isolated
│   ~62 tests, <1s                │
├─────────────────────────────────┤
│   Emulator Integration Tests    │  Local Firestore/GCS
│   ~11 tests, ~2s                │
├─────────────────────────────────┤
│   E2E Tests                     │  Full API + Frontend
│   Requires deployed backend     │
└─────────────────────────────────┘
```

## Deployment

### Automatic (CI/CD)

Push to `main` triggers GitHub Actions:
1. Run tests
2. Build Docker image
3. Deploy to Cloud Run
4. Deploy frontend to Cloudflare Pages

### Manual Backend Deploy

```bash
# Preview infrastructure changes
cd infrastructure
pulumi preview

# Apply changes
pulumi up
```

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

## Troubleshooting

### Emulators Won't Start

```bash
# Check if already running
lsof -i :8080  # Firestore
lsof -i :4443  # GCS

# Stop them
./scripts/stop-emulators.sh
```

### Tests Fail with Auth Errors

Ensure `ADMIN_TOKENS` is set in environment or `.env` file.

### Docker Issues

```bash
# Ensure Docker is running
docker info

# For GCS emulator
docker pull fsouza/fake-gcs-server
```

### Coverage Below Target

Run `./scripts/run-tests.sh --coverage` and check `htmlcov/index.html` to see uncovered lines.
