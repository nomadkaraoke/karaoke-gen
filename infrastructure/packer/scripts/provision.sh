#!/bin/bash
# Provision encoding worker image with all dependencies pre-installed
#
# This script runs during Packer image build. After it completes:
# - Python 3.13 is installed at /opt/python313
# - FFmpeg 7.x is installed at /usr/local/bin/ffmpeg
# - All fonts (including CJK) are installed
# - Virtual environment exists at /opt/encoding-worker/venv
# - Systemd service is configured (but not started - no API key yet)
#
# IMPORTANT: This image uses the "immutable deployment" pattern:
# - bootstrap.sh is baked into the image (minimal, rarely changes)
# - bootstrap.sh downloads startup.sh from GCS on every service start
# - startup.sh contains all logic and is CI-managed
# - This allows logic updates without rebuilding the image
#
# See infrastructure/encoding-worker/README.md for details.

set -e

echo "=== Starting encoding worker image provisioning ==="
echo "Python version: ${PYTHON_VERSION}"

# Install system packages
echo "Installing system packages..."
apt-get update
apt-get install -y \
    docker.io \
    curl \
    git \
    xz-utils \
    build-essential \
    libssl-dev \
    zlib1g-dev \
    libbz2-dev \
    libreadline-dev \
    libsqlite3-dev \
    libncursesw5-dev \
    tk-dev \
    libxml2-dev \
    libxmlsec1-dev \
    libffi-dev \
    liblzma-dev

# Install fonts for ASS subtitle rendering (libass uses fontconfig)
# - fonts-noto: Noto Sans (default karaoke font) + musical symbols
# - fonts-noto-cjk: Chinese, Japanese, Korean character support
# - fontconfig: Font configuration system
echo "Installing fonts..."
apt-get install -y fonts-noto fonts-noto-cjk fontconfig
fc-cache -fv

# Enable Docker (will start on boot)
systemctl enable docker

# Install optimized static FFmpeg build (John Van Sickle)
# This is faster than Debian's package and has more codecs enabled
echo "Installing FFmpeg..."
curl -L https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz -o /tmp/ffmpeg.tar.xz
tar -xf /tmp/ffmpeg.tar.xz -C /tmp
cp /tmp/ffmpeg-*-amd64-static/ffmpeg /usr/local/bin/
cp /tmp/ffmpeg-*-amd64-static/ffprobe /usr/local/bin/
chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe
rm -rf /tmp/ffmpeg*
echo "FFmpeg version:"
ffmpeg -version | head -1

# Build and install Python from source
# This is the slow part (~7 min) - pre-baking it saves startup time
echo "Building Python ${PYTHON_VERSION} from source..."
cd /tmp
curl -L "https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tar.xz" -o python.tar.xz
tar -xf python.tar.xz
cd "Python-${PYTHON_VERSION}"

# Configure with optimizations
# --enable-optimizations: Profile-guided optimization (slower build, faster runtime)
# --with-lto: Link-time optimization
./configure --prefix=/opt/python313 --enable-optimizations --with-lto
make -j$(nproc)
make install

# Clean up Python build artifacts
rm -rf /tmp/python.tar.xz /tmp/Python-${PYTHON_VERSION}

echo "Python ${PYTHON_VERSION} installed:"
/opt/python313/bin/python3.13 --version

# Create working directory and venv
mkdir -p /opt/encoding-worker
cd /opt/encoding-worker

echo "Creating Python virtual environment..."
/opt/python313/bin/python3.13 -m venv venv

# Install base Python dependencies (wheel will add more at runtime)
source venv/bin/activate
pip install --upgrade pip
pip install fastapi uvicorn google-cloud-storage aiofiles aiohttp packaging

# Create bootstrap script (runs via ExecStartPre before service starts)
# This minimal script downloads the REAL startup script from GCS.
# All actual logic lives in the GCS-hosted startup.sh, managed by CI.
#
# Why this pattern?
# - startup.sh can be updated without rebuilding the Packer image
# - All code changes go through CI -> GCS -> service restart
# - No version sorting bugs (fixed wheel path)
# - Strict version verification before starting
cat > /opt/encoding-worker/bootstrap.sh << 'BOOTSTRAP'
#!/bin/bash
# Encoding Worker Bootstrap Script
#
# This is the ONLY script baked into the Packer image.
# It downloads and executes the real startup script from GCS.

set -e

BUCKET="gs://karaoke-gen-storage-nomadkaraoke"
WORKER_DIR="/opt/encoding-worker"
LOG_FILE="/var/log/encoding-worker-bootstrap.log"

exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== Bootstrap: $(date) ==="
echo "Downloading latest startup script from GCS..."

# Download the CI-managed startup script
if ! gsutil cp "${BUCKET}/encoding-worker/startup.sh" "${WORKER_DIR}/startup-latest.sh" 2>&1; then
    echo "ERROR: Failed to download startup.sh from GCS"
    echo "Falling back to local startup.sh if available..."
    if [ -f "${WORKER_DIR}/startup-fallback.sh" ]; then
        cp "${WORKER_DIR}/startup-fallback.sh" "${WORKER_DIR}/startup-latest.sh"
    else
        echo "FATAL: No fallback startup script available"
        exit 1
    fi
fi

chmod +x "${WORKER_DIR}/startup-latest.sh"

echo "=== Bootstrap: Executing downloaded startup script ==="
exec "${WORKER_DIR}/startup-latest.sh"
BOOTSTRAP

chmod +x /opt/encoding-worker/bootstrap.sh

# Create fallback startup script (used if GCS is unreachable)
# This is a simplified version that at least tries to start the service
cat > /opt/encoding-worker/startup-fallback.sh << 'FALLBACK'
#!/bin/bash
set -e

echo "=== FALLBACK startup at $(date) ==="
echo "WARNING: Using fallback startup - GCS was unreachable"

WORKER_DIR="/opt/encoding-worker"
BUCKET="gs://karaoke-gen-storage-nomadkaraoke"

# Fetch API key
ENCODING_API_KEY=$(gcloud secrets versions access latest --secret=encoding-worker-api-key 2>/dev/null || echo "")
echo "ENCODING_API_KEY=${ENCODING_API_KEY}" > "${WORKER_DIR}/env"
chmod 600 "${WORKER_DIR}/env"

# Try to install wheel from fixed path
echo "Attempting to download wheel..."
if gsutil cp "${BUCKET}/wheels/karaoke_gen-current.whl" /tmp/karaoke_gen.whl 2>/dev/null; then
    "${WORKER_DIR}/venv/bin/pip" install --upgrade --quiet /tmp/karaoke_gen.whl || true
fi

echo "=== Fallback startup complete ==="
FALLBACK

chmod +x /opt/encoding-worker/startup-fallback.sh

# Create systemd service
# ExecStartPre runs bootstrap.sh which downloads and runs the real startup.sh from GCS
# ExecStart runs uvicorn with the encoding worker from the installed wheel
cat > /etc/systemd/system/encoding-worker.service << 'SYSTEMD'
[Unit]
Description=Encoding Worker HTTP API Service
After=network.target docker.service
Wants=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/encoding-worker
# bootstrap.sh downloads startup.sh from GCS, then runs it
ExecStartPre=/opt/encoding-worker/bootstrap.sh
ExecStart=/opt/encoding-worker/venv/bin/uvicorn backend.services.gce_encoding.main:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=10
# Increased timeout to allow for GCS downloads
TimeoutStartSec=600
Environment="GOOGLE_CLOUD_PROJECT=nomadkaraoke"
EnvironmentFile=/opt/encoding-worker/env

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=encoding-worker

[Install]
WantedBy=multi-user.target
SYSTEMD

# Create empty env file (will be populated at runtime)
touch /opt/encoding-worker/env

# Reload systemd and enable service (will start on boot)
systemctl daemon-reload
systemctl enable encoding-worker

echo "=== Provisioning complete ==="
echo "Image includes:"
echo "  - Python ${PYTHON_VERSION} at /opt/python313"
echo "  - FFmpeg at /usr/local/bin/ffmpeg"
echo "  - Noto fonts (including CJK)"
echo "  - Virtual environment at /opt/encoding-worker/venv"
echo "  - Systemd service: encoding-worker.service"
echo ""
echo "Immutable deployment pattern:"
echo "  - bootstrap.sh downloads startup.sh from GCS on every start"
echo "  - startup.sh is CI-managed, no image rebuild needed for logic changes"
echo "  - See infrastructure/encoding-worker/README.md"
