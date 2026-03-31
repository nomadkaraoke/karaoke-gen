#!/bin/bash
# Divebar File Sync VM startup script
#
# Downloads karaoke files from Google Drive to GCS using the BigQuery index.
# Auto-shuts down after sync completes. Designed to be started daily by
# Cloud Scheduler or manually for initial bulk sync.
#
# Logs: journalctl -u divebar-sync -f
#       OR: /var/log/divebar-sync.log

set -euo pipefail

LOG_FILE="/var/log/divebar-sync.log"
REPO_DIR="/opt/nomad/divebar-sync"
SCRIPT="infrastructure/scripts/divebar_file_sync.py"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG_FILE"
}

log "=== Divebar file sync starting ==="

# Install dependencies (idempotent)
if ! command -v pip3 &>/dev/null; then
    log "Installing Python3 and pip..."
    apt-get update -qq && apt-get install -y -qq python3-pip python3-venv git
fi

# Clone or update the repo
if [ -d "$REPO_DIR" ]; then
    log "Updating repo..."
    cd "$REPO_DIR" && git pull origin main --quiet
else
    log "Cloning repo..."
    git clone --depth 1 https://github.com/nomadkaraoke/karaoke-gen.git "$REPO_DIR"
fi

cd "$REPO_DIR"

log "Installing Python dependencies..."
# Pin google-auth<2.28 to avoid MTLS metadata server issue on GCE
# See: https://github.com/googleapis/google-auth-library-python/issues/1474
# Use --break-system-packages since Debian 12 enforces PEP 668
pip3 install -q --break-system-packages 'google-auth<2.28' google-api-python-client google-cloud-storage google-cloud-bigquery requests

# Force metadata server to use HTTP (not HTTPS) — required for GCE VMs
# See: https://github.com/googleapis/google-auth-library-python/issues/1474
export GCE_METADATA_HOST="169.254.169.254"
export GOOGLE_CLOUD_PROJECT="nomadkaraoke"

# Run the sync
log "Starting file sync (workers=4)..."
python3 "$SCRIPT" --workers 4 2>&1 | tee -a "$LOG_FILE"

log "=== Divebar file sync script exited ==="

log "Shutting down VM after sync completion..."
shutdown -h now
