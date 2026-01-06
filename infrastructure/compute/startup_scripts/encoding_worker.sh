#!/bin/bash
# Encoding worker startup script (for Packer-built custom image)
#
# Most dependencies are pre-installed in the custom GCE image:
# - Python 3.13 at /opt/python313
# - FFmpeg at /usr/local/bin/ffmpeg
# - Virtual environment at /opt/encoding-worker/venv
# - Systemd service: encoding-worker.service
#
# This startup script just starts the service.
# The service's ExecStartPre (/opt/encoding-worker/startup.sh) handles:
# - Fetching API key from Secret Manager
# - Installing latest karaoke-gen wheel from GCS
#
# See infrastructure/packer/README.md for image build instructions.

set -e
exec > >(tee /var/log/encoding-worker-startup.log) 2>&1

echo "=== Encoding worker VM startup at $(date) ==="

# Start Docker (in case it's not running)
systemctl start docker || echo "Docker already running or not available"

# Start the encoding worker service
# This triggers ExecStartPre which fetches API key and installs wheel
echo "Starting encoding-worker service..."
systemctl start encoding-worker

echo "=== Encoding worker started at $(date) ==="
echo "Service listening on port 8080"
