# Contributing to karaoke-gen

Thanks for your interest in contributing! This guide focuses on getting a
working **local development environment with no Google Cloud credentials or
paid API keys** — everything runs against local emulators.

## What runs locally (no credentials needed)

| Component | Local story |
|-----------|-------------|
| FastAPI backend | Runs directly via uvicorn |
| Firestore | Google's Firestore emulator |
| Cloud Storage | [fake-gcs-server](https://github.com/fsouza/fake-gcs-server) (Docker) |
| Background workers (audio, lyrics, screens, video…) | Run as local subprocesses / in-process tasks |
| Audio separation | Local CPU/GPU via `audio-separator` (set `MODEL_DIR`; models download on first use) |
| Lyrics transcription | Local Whisper (`poetry install --extras local-whisper`); AudioShake used only if you set a token |
| Login (magic links) | Printed to the backend console instead of emailed |
| Signed URLs (review audio, downloads, uploads) | Rewritten to plain emulator URLs |
| Frontend (Next.js) | `npm run dev`, pointed at the local backend |

Things that do NOT run locally: payments (Stripe), production email
(Postmark), YouTube/Drive/Dropbox distribution, and the `flacfetch`
URL-download service — **jobs from URLs won't process locally; use file
upload instead.**

## Prerequisites

- Python 3.12/3.13 + [Poetry](https://python-poetry.org/)
- Node.js 20+ (for the frontend)
- Docker (for the GCS emulator)
- Java 21+ and the Google Cloud SDK with the Firestore emulator:
  `gcloud components install cloud-firestore-emulator`
  (no login/account needed — the emulator runs entirely offline)
- ffmpeg (`brew install ffmpeg` / `apt install ffmpeg`)

## Backend quickstart

```bash
poetry install                              # heavy: torch etc, ~10 min first time
./scripts/run-backend-local.sh --with-emulators
```

This starts the Firestore emulator (port 8080), fake-gcs-server (port 4443),
creates the bucket, seeds a default theme (`scripts/seed-local-data.py`), and
runs the backend at <http://localhost:8000> (API docs at `/docs`).

Optional API keys go in `.env` (see `.env.example`) — none are required to boot.

### Try it

```bash
# Health
curl http://localhost:8000/api/health

# Create a job from a local audio file (admin token is "local-dev-token")
curl -X POST http://localhost:8000/api/jobs/upload \
  -H 'Authorization: Bearer local-dev-token' \
  -F "file=@/path/to/song.flac" -F "artist=Some Artist" -F "title=Some Song"

# Watch it process; when status hits awaiting_review, open the review UI
curl http://localhost:8000/api/jobs/<job_id> -H 'Authorization: Bearer local-dev-token'
```

For audio separation to run, set `MODEL_DIR` (e.g. `MODEL_DIR=./models`) —
separation models (~2 GB) are downloaded on first use. Without it the job
fails at the separation step with a clear error.

## Frontend quickstart

```bash
cd frontend
npm install
NEXT_PUBLIC_API_URL=http://localhost:8000 npx next dev
```

Then open <http://localhost:3000>. To log in, enter any email address — the
magic link is printed in the **backend** console (look for `EMAIL TO:`); open
that link in your browser.

### Review UI without running the full pipeline

If you're working on the lyrics review UI specifically, there's a fixtures
harness that serves real correction data through the review API shape without
processing any audio:

```bash
python scripts/review_test_fixtures.py   # serves on :8765
```

## Tests

```bash
make test          # everything (backend + frontend)
make test-backend  # backend only (unit + emulator tests)
```

Emulator-backed integration tests live in `backend/tests/emulator/` and use
the same two emulators as the local dev setup.

## Pull requests

- Keep PRs focused; include tests for behavior changes.
- Run `make test` before pushing — CI runs the same suite.
- Bump `tool.poetry.version` in `pyproject.toml` for code changes.
- CI must pass before merge; a maintainer will review from there.

See `docs/DEVELOPMENT.md` for deeper docs (architecture, deployment,
observability) — most of that file concerns the production GCP deployment,
which contributors don't need.
