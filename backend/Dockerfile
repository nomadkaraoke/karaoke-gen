# Fast application image - uses pre-built base with all dependencies
#
# The base image (karaoke-backend-base) contains:
# - Python 3.11, ffmpeg, sox, system deps
# - All pip dependencies from requirements-frozen.txt (NO code)
#
# This Dockerfile adds the actual application code on top.
# Build time: ~1-2 minutes (deps already cached in base)
# 
# If the base image doesn't exist yet, build it first:
#   gcloud builds submit --config=cloudbuild-base.yaml --project=nomadkaraoke

# Use pre-built base image with all dependencies
# Override with --build-arg BASE_IMAGE=karaoke-backend-base:local for local testing
ARG BASE_IMAGE=us-central1-docker.pkg.dev/nomadkaraoke/karaoke-repo/karaoke-backend-base:latest
FROM ${BASE_IMAGE}

# Set working directory (should already exist from base)
WORKDIR /app

# Copy backend code (this is the only thing that changes frequently)
COPY backend /app/backend

# Copy and reinstall karaoke_gen from local source
# This ensures new modules are included without rebuilding base image
# Note: lyrics_transcriber is now a subpackage of karaoke_gen
COPY karaoke_gen /app/karaoke_gen
COPY pyproject.toml README.md LICENSE /app/
# GPU images (MODEL_DIR set in base): use --no-deps to preserve pinned torch cu126
# and audio-separator[gpu]. Without this, pip upgrades torch to cu130 (breaks CUDA
# on Cloud Run L4 drivers) and reinstalls audio-separator[cpu] (breaks GPU ONNX).
# CPU images: normal install resolves all deps from pyproject.toml.
RUN if [ -n "$MODEL_DIR" ]; then \
      pip install --no-cache-dir --no-deps -e .; \
    else \
      pip install --no-cache-dir -e .; \
    fi

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

# Expose port
EXPOSE 8080

# Run uvicorn directly
# Note: Transmission daemon removed - torrent downloads now handled by
# dedicated flacfetch VM service (see docs/00-current-plan/TORRENT-SERVICE-PLAN.md)
# Using --loop asyncio (instead of uvloop) to support nest_asyncio for remote flacfetch calls
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080", "--loop", "asyncio"]
