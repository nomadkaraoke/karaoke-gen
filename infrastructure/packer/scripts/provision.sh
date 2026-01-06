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
# At runtime, the startup script only needs to:
# 1. Fetch API key from Secret Manager
# 2. Download latest karaoke-gen wheel
# 3. Start the systemd service

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
pip install fastapi uvicorn google-cloud-storage aiofiles aiohttp

# Create startup helper script (runs via ExecStartPre before service starts)
# This fetches API key and installs latest wheel at boot time
cat > /opt/encoding-worker/startup.sh << 'STARTUP'
#!/bin/bash
set -e

echo "=== Encoding worker startup at $(date) ==="

# Fetch API key from Secret Manager
echo "Fetching API key from Secret Manager..."
ENCODING_API_KEY=$(gcloud secrets versions access latest --secret=encoding-worker-api-key 2>/dev/null || echo "")

if [ -z "$ENCODING_API_KEY" ]; then
    echo "WARNING: No API key found in Secret Manager"
fi

# Write environment file for systemd
echo "ENCODING_API_KEY=${ENCODING_API_KEY}" > /opt/encoding-worker/env

# Download and install latest karaoke-gen wheel from GCS
# This enables hot code updates without rebuilding the image
echo "Installing latest karaoke-gen wheel from GCS..."
gsutil cp "gs://karaoke-gen-storage-nomadkaraoke/wheels/karaoke_gen-*.whl" /tmp/ 2>/dev/null || true
WHEEL_FILE=$(ls -t /tmp/karaoke_gen-*.whl 2>/dev/null | head -1)
if [ -n "$WHEEL_FILE" ]; then
    echo "Installing wheel: $WHEEL_FILE"
    /opt/encoding-worker/venv/bin/pip install --upgrade --quiet "$WHEEL_FILE" || echo "WARNING: Failed to install wheel"
else
    echo "No wheel found in GCS"
fi

echo "=== Startup script complete ==="
STARTUP

chmod +x /opt/encoding-worker/startup.sh

# Create systemd service
# ExecStartPre runs the startup script to fetch API key and install wheel
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
ExecStartPre=/opt/encoding-worker/startup.sh
ExecStart=/opt/encoding-worker/venv/bin/uvicorn backend.services.gce_encoding.main:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=10
TimeoutStartSec=300
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
