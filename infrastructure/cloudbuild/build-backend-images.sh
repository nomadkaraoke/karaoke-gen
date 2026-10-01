#!/usr/bin/env bash
# Builds + pushes the backend container images. Runs INSIDE Cloud Build
# (gcr.io/cloud-builders/docker, us-central1) — invoked by
# infrastructure/cloudbuild/backend-images.yaml, which the deploy-backend job in
# .github/workflows/ci.yml submits.
#
# Why Cloud Build and not the GitHub-hosted runner: the app images are built
# FROM large bases (CPU ~4.2 GB, GPU ~13 GB compressed) that live in
# us-central1 Artifact Registry. Pulling them to a GitHub-hosted runner is GCP
# internet egress (~$0.12/GB → ~$2/deploy); pulling them to Cloud Build in
# us-central1 is same-region (free), and e2-standard-2 build-minutes are in the
# 2,500 min/month free tier. See docs/EPHEMERAL-GHA-RUNNERS.md.
#
# Usage: build-backend-images.sh cpu|gpu
# Env (from Cloud Build substitutions): SHA, VERSION, RUN_ID,
#   BUILD_BASE, BASE_HASH (cpu) / BUILD_GPU_BASE, GPU_BASE_HASH (gpu)
set -euo pipefail

TARGET="${1:?usage: build-backend-images.sh cpu|gpu}"

REG="us-central1-docker.pkg.dev/nomadkaraoke/karaoke-repo"
# us-east4 repo: GPU app image (audio-separation job) + CPU app copy for the
# latency-critical us-east4 CPU jobs. Only the changed app layers cross regions.
EAST="us-east4-docker.pkg.dev/nomadkaraoke/karaoke-backend-gpu"
BUILDER="nk-${TARGET}"

# Cloud Build pre-seeds ~/.docker/config.json with a static access token. A
# GPU base rebuild can run long enough to outlive it, so log in with a fresh
# metadata-server token before every build.
refresh_auth() {
  local token
  token=$(curl -sf -H 'Metadata-Flavor: Google' \
    'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token' \
    | sed -E 's/.*"access_token" *: *"([^"]+)".*/\1/')
  if [ -z "$token" ]; then
    echo "ERROR: could not fetch access token from metadata server" >&2
    exit 1
  fi
  for host in us-central1-docker.pkg.dev us-east4-docker.pkg.dev; do
    echo "$token" | docker login -u oauth2accesstoken --password-stdin "https://${host}" >/dev/null
  done
}

# Each target gets its own docker-container builder: the cpu and gpu steps run
# in parallel and must not race creating/bootstrapping a shared one.
docker buildx create --name "$BUILDER" --driver docker-container >/dev/null
docker buildx inspect "$BUILDER" --bootstrap >/dev/null

build() {
  refresh_auth
  docker buildx build --builder "$BUILDER" --push --provenance=false --progress=plain "$@" .
}

case "$TARGET" in
  cpu)
    if [ "${BUILD_BASE:-false}" = "true" ]; then
      echo "=== Building CPU base image (karaoke-backend-base) ==="
      build -f backend/Dockerfile.base \
        -t "$REG/karaoke-backend-base:latest" \
        -t "$REG/karaoke-backend-base:$SHA" \
        --build-arg "BUILD_DATE=$RUN_ID" \
        --build-arg "BUILD_ID=$SHA" \
        --build-arg "BASE_CONTENT_HASH=$BASE_HASH" \
        --cache-from "type=registry,ref=$REG/karaoke-backend-base:cache" \
        --cache-to "type=registry,ref=$REG/karaoke-backend-base:cache,mode=max,ignore-error=true"
    fi
    echo "=== Building CPU app image (karaoke-backend + us-east4 karaoke-backend-cpu) ==="
    build -f backend/Dockerfile \
      -t "$REG/karaoke-backend:latest" \
      -t "$REG/karaoke-backend:$SHA" \
      -t "$REG/karaoke-backend:v$VERSION" \
      -t "$EAST/karaoke-backend-cpu:latest" \
      -t "$EAST/karaoke-backend-cpu:$SHA" \
      -t "$EAST/karaoke-backend-cpu:v$VERSION" \
      --cache-from "type=registry,ref=$REG/karaoke-backend:cache" \
      --cache-to "type=registry,ref=$REG/karaoke-backend:cache,mode=max,ignore-error=true"
    ;;
  gpu)
    if [ "${BUILD_GPU_BASE:-false}" = "true" ]; then
      echo "=== Building GPU base image (karaoke-backend-gpu-base) ==="
      build -f backend/Dockerfile.gpu-base \
        -t "$REG/karaoke-backend-gpu-base:latest" \
        -t "$REG/karaoke-backend-gpu-base:$SHA" \
        --build-arg "BUILD_DATE=$RUN_ID" \
        --build-arg "BUILD_ID=$SHA" \
        --build-arg "BASE_CONTENT_HASH=$GPU_BASE_HASH" \
        --cache-from "type=registry,ref=$REG/karaoke-backend-gpu-base:cache" \
        --cache-to "type=registry,ref=$REG/karaoke-backend-gpu-base:cache,mode=max,ignore-error=true"
    fi
    # Base + cache in us-central1 (same region as Cloud Build); final image
    # → us-east4, same region as the audio-separation job that pulls it.
    echo "=== Building GPU app image (us-east4 karaoke-backend) ==="
    build -f backend/Dockerfile \
      -t "$EAST/karaoke-backend:latest" \
      -t "$EAST/karaoke-backend:$SHA" \
      -t "$EAST/karaoke-backend:v$VERSION" \
      --build-arg "BASE_IMAGE=$REG/karaoke-backend-gpu-base:latest" \
      --cache-from "type=registry,ref=$REG/karaoke-backend-gpu:cache" \
      --cache-to "type=registry,ref=$REG/karaoke-backend-gpu:cache,mode=max,ignore-error=true"
    ;;
  *)
    echo "unknown target: $TARGET (expected cpu|gpu)" >&2
    exit 2
    ;;
esac

echo "=== $TARGET images pushed ==="
