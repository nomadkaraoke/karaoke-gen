#!/bin/bash
# Encoding Worker Startup Script
#
# This script is deployed to GCS and downloaded by bootstrap.sh on every service start.
# It handles:
# 1. Fetching API key from Secret Manager
# 2. Downloading the current wheel (fixed path, no version sorting!)
# 3. Installing the wheel
# 4. Verifying installed version matches expected version
# 5. Failing fast if version mismatch (don't start with wrong code)
#
# CI uploads this script to: gs://karaoke-gen-storage-nomadkaraoke/encoding-worker/startup.sh
# CI also uploads:
# - gs://karaoke-gen-storage-nomadkaraoke/wheels/karaoke_gen-current.whl (latest wheel)
# - gs://karaoke-gen-storage-nomadkaraoke/encoding-worker/version.txt (expected version)

set -e

BUCKET="gs://karaoke-gen-storage-nomadkaraoke"
WORKER_DIR="/opt/encoding-worker"
VENV="${WORKER_DIR}/venv"
TMP_DIR="/tmp/encoding-worker-startup"
LOG_FILE="/var/log/encoding-worker-startup.log"

exec > >(tee -a "$LOG_FILE") 2>&1

echo "=============================================="
echo "Encoding Worker Startup: $(date)"
echo "=============================================="

# Create temp directory for downloads
rm -rf "${TMP_DIR}"
mkdir -p "${TMP_DIR}"

# 1. Fetch API key from Secret Manager
echo "[1/5] Fetching API key from Secret Manager..."
ENCODING_API_KEY=$(gcloud secrets versions access latest --secret=encoding-worker-api-key 2>/dev/null || echo "")

if [ -z "$ENCODING_API_KEY" ]; then
    echo "WARNING: No API key found in Secret Manager"
    echo "Service will start but may reject requests"
fi

# Write environment file for systemd
echo "ENCODING_API_KEY=${ENCODING_API_KEY}" > "${WORKER_DIR}/env"
chmod 600 "${WORKER_DIR}/env"

# 2. Download expected version manifest
echo "[2/5] Downloading version manifest..."
if ! gsutil cp "${BUCKET}/encoding-worker/version.txt" "${TMP_DIR}/version.txt" 2>&1; then
    echo "WARNING: Could not download version.txt - skipping version verification"
    EXPECTED_VERSION=""
else
    EXPECTED_VERSION=$(cat "${TMP_DIR}/version.txt" | tr -d '[:space:]')
    echo "Expected version: ${EXPECTED_VERSION}"
fi

# 3. Download current wheel (FIXED PATH - no sorting!)
echo "[3/5] Downloading current wheel..."

# pip requires properly named wheel files (PEP 427)
# We need to rename the wheel to a valid name based on the version
if [ -n "$EXPECTED_VERSION" ]; then
    WHEEL_NAME="karaoke_gen-${EXPECTED_VERSION}-py3-none-any.whl"
else
    # Fallback name if no version.txt
    WHEEL_NAME="karaoke_gen-0.0.0-py3-none-any.whl"
fi
WHEEL_PATH="${TMP_DIR}/${WHEEL_NAME}"

if ! gsutil cp "${BUCKET}/wheels/karaoke_gen-current.whl" "${WHEEL_PATH}" 2>&1; then
    echo "ERROR: Failed to download karaoke_gen-current.whl"
    echo "Checking for any available wheel as fallback..."

    # Fallback: try to get the latest versioned wheel (with proper version sort)
    LATEST_WHEEL=$(gsutil ls "${BUCKET}/wheels/karaoke_gen-*.whl" 2>/dev/null | grep -v 'current' | sort -V | tail -1 || echo "")

    if [ -n "$LATEST_WHEEL" ]; then
        echo "Fallback: Using ${LATEST_WHEEL}"
        gsutil cp "${LATEST_WHEEL}" "${WHEEL_PATH}"
    else
        echo "FATAL: No wheel available in GCS"
        exit 1
    fi
fi

echo "Downloaded wheel as: ${WHEEL_NAME}"

# 4. Install the wheel
# Use --no-deps because the encoding worker venv has runtime deps pre-installed.
# The wheel only needs to update the karaoke-gen code itself.
# Full dependency resolution on the complex dep graph (langchain, CUDA, etc.)
# can hit pip's "resolution-too-deep" error and prevent the service from starting.
echo "[4/5] Installing wheel..."
"${VENV}/bin/pip" install --upgrade --quiet --force-reinstall --no-deps "${WHEEL_PATH}"

# 5. Verify installed version
echo "[5/5] Verifying installed version..."
INSTALLED_VERSION=$("${VENV}/bin/pip" show karaoke-gen 2>/dev/null | grep "^Version:" | cut -d' ' -f2 | tr -d '[:space:]')
echo "Installed version: ${INSTALLED_VERSION}"

if [ -n "$EXPECTED_VERSION" ]; then
    if [ "${INSTALLED_VERSION}" != "${EXPECTED_VERSION}" ]; then
        echo "=============================================="
        echo "FATAL: VERSION MISMATCH!"
        echo "  Expected: ${EXPECTED_VERSION}"
        echo "  Installed: ${INSTALLED_VERSION}"
        echo "=============================================="
        echo "Refusing to start with incorrect version."
        echo "This indicates a deployment issue - check CI logs."
        exit 1
    fi
    echo "Version verified successfully!"
else
    echo "WARNING: Version verification skipped (no version.txt)"
fi

# Cleanup
rm -rf "${TMP_DIR}"

echo "=============================================="
echo "Startup complete: karaoke-gen v${INSTALLED_VERSION}"
echo "=============================================="
