#!/bin/bash
# Run the backend locally in REPLAY mode against REAL production data (read-only),
# so you can re-open the lyrics-review + instrumental-selection UIs for COMPLETED
# jobs with full audio/backing-vocals playback.
#
# Uses your own gcloud ADC (must be `gcloud auth application-default login` as an
# account that can READ Firestore `jobs` + the GCS bucket). No writes are made.
# REVIEW_AUDIO_PROXY=1 makes the backend stream review audio bytes instead of GCS
# signed URLs (local user ADC can't sign) — nothing customer-facing changes.
#
# Usage:
#   ./scripts/run-replay-local.sh
# then in another terminal:  (cd frontend && npm run dev)
#
# See docs/REPLAY.md for the full walkthrough + the job list.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-nomadkaraoke}"
export GCS_BUCKET_NAME="${GCS_BUCKET_NAME:-karaoke-gen-storage-nomadkaraoke}"
export FIRESTORE_COLLECTION="${FIRESTORE_COLLECTION:-jobs}"   # REAL prod jobs (not jobs-dev)
export ENVIRONMENT="development"
export EDGE_AUTH_MODE="off"
export REVIEW_AUDIO_PROXY="1"                                 # stream bytes, no signing
export ADMIN_TOKENS="${ADMIN_TOKENS:-replay-local-token}"     # matches the frontend token below
export PORT="${PORT:-8000}"

cat <<EOF

🎬  Replay backend starting on http://127.0.0.1:$PORT  (REAL prod data, read-only)

Next steps:
  1. In another terminal:   cd frontend && npm run dev        # http://localhost:3000
  2. Open http://localhost:3000 in your browser, then in DevTools console run:
         localStorage.setItem('karaoke_access_token', '$ADMIN_TOKENS')
     (then reload — you'll be signed in as admin@nomadkaraoke.com)
  3. Open a job in replay (LYRICS review):
         http://localhost:3000/en/app/jobs?baseApiUrl=http://127.0.0.1:$PORT&replay=1#/<JOB_ID>/review
     …or the INSTRUMENTAL screen:
         http://localhost:3000/en/app/jobs?baseApiUrl=http://127.0.0.1:$PORT&replay=1#/<JOB_ID>/instrumental

  Job list + walkthrough:   docs/REPLAY.md

EOF

exec poetry run uvicorn backend.main:app --host 127.0.0.1 --port "$PORT"
