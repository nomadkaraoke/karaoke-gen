#!/usr/bin/env bash
# Download the shared E2E test audio pair (mixed + instrumental) used by the
# tenant frontend E2E. Gitignored — not committed (12MB). Requires gcloud auth
# (WIF in CI, ADC locally) with read access to the storage bucket.
set -euo pipefail
DEST="${1:-frontend/e2e/fixtures}"
SRC="gs://karaoke-gen-storage-nomadkaraoke/e2e-tests/shared/short"
mkdir -p "$DEST"
gcloud storage cp "$SRC/e2e-mixed.mp3" "$DEST/e2e-mixed.mp3"
gcloud storage cp "$SRC/e2e-instrumental.mp3" "$DEST/e2e-instrumental.mp3"
echo "Fetched test audio to $DEST"
