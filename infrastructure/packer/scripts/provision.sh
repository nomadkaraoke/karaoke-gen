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

# Bake the FULL karaoke-gen dependency tree into the image (2026-08-15) so every
# fresh worker VM — including the broadened multi-instance-type fallback pool — is
# self-sufficient at boot instead of relying on a first-job ensure_latest_wheel()
# install of the whole tree (torch + CUDA, langchain, tenacity, ...). That lazy
# path repeatedly bit fresh fallback VMs (see #903 / "No module named 'tenacity'"):
# a partial/timed-out install left a broken venv and every retry hit the same VM.
#
# HOW: install the current published wheel WITH deps, but resolve torch from the
# PyTorch CPU index (primary) with PyPI as the fallback index (everything else).
# CPU-only torch avoids the multi-GB CUDA/nvidia wheels the encode/finalize path
# never needs, keeping the image small. The wheel's CODE is intentionally kept
# installed too — startup.sh still runs `pip install --no-deps <latest wheel>` at
# boot to fast-forward the code to the newest version (deps already satisfied), so
# there's no crash-loop from full resolution at boot (the #587 reason for --no-deps)
# AND no missing-dep failure on the first job.
source venv/bin/activate
pip install --upgrade pip

# Boot/serve packages first (guarantees the service can start even if the heavy
# install below is ever trimmed).
pip install fastapi uvicorn google-cloud-storage aiofiles aiohttp packaging

# Pull the current wheel from the fixed GCS path (same artifact the runtime
# fallback uses). gsutil ships in the debian-cloud base image and the Packer
# builder SA has GCS read on this bucket.
BUCKET="gs://karaoke-gen-storage-nomadkaraoke"
# pip requires a PEP 427-valid wheel filename; the GCS alias
# `karaoke_gen-current.whl` is NOT valid, so copy it to a properly-versioned
# name (mirrors startup.sh). Prefer version.txt for the version tag.
BAKE_VERSION=$(gsutil cat "${BUCKET}/encoding-worker/version.txt" 2>/dev/null | tr -d '[:space:]')
if [ -n "${BAKE_VERSION}" ]; then
    WHEEL_NAME="karaoke_gen-${BAKE_VERSION}-py3-none-any.whl"
else
    WHEEL_NAME="karaoke_gen-0.0.0-py3-none-any.whl"
fi
WHEEL_PATH="/tmp/${WHEEL_NAME}"
echo "Baking full dependency tree (CPU-only torch) from ${BUCKET}/wheels/karaoke_gen-current.whl → ${WHEEL_NAME} ..."
if ! gsutil cp "${BUCKET}/wheels/karaoke_gen-current.whl" "${WHEEL_PATH}"; then
    # Fall back to the latest properly-named versioned wheel.
    LATEST_WHEEL=$(gsutil ls "${BUCKET}/wheels/karaoke_gen-*.whl" 2>/dev/null | grep -v 'current' | sort -V | tail -1 || echo "")
    if [ -n "${LATEST_WHEEL}" ]; then
        WHEEL_NAME=$(basename "${LATEST_WHEEL}")
        WHEEL_PATH="/tmp/${WHEEL_NAME}"
        gsutil cp "${LATEST_WHEEL}" "${WHEEL_PATH}"
    else
        echo "FATAL: could not download a wheel to bake deps into the image"
        exit 1
    fi
fi

# --index-url = PyTorch CPU index (primary, so torch resolves to the CPU build),
# --extra-index-url = PyPI (everything else). Retry a couple of times to ride out
# transient index blips during the build.
for attempt in 1 2 3; do
    if pip install \
        --index-url https://download.pytorch.org/whl/cpu \
        --extra-index-url https://pypi.org/simple \
        "${WHEEL_PATH}"; then
        break
    fi
    echo "pip install of full dep tree failed (attempt ${attempt}/3); retrying..."
    sleep 15
    if [ "${attempt}" = "3" ]; then
        echo "FATAL: could not install the full karaoke-gen dependency tree"
        exit 1
    fi
done
rm -f "${WHEEL_PATH}"

# Verify the dependency TREE is complete. We import the heavy transitive
# libraries that constitute the encode/finalization chain — crucially tenacity,
# the dep that was silently missing on fresh fallback VMs (pulled via the
# generator→correction/langchain chain). We deliberately do NOT import the worker
# app module (backend.services.gce_encoding.main): it constructs GCP clients at
# import and needs GOOGLE_CLOUD_PROJECT + auth that only exist at runtime (systemd
# env), so importing it at BUILD time fails for environment reasons, not missing
# deps. The canary libs below prove the install is complete without that
# fragility. Fail the BUILD on any missing import so a half-baked image that
# claims to be self-sufficient can never ship.
echo "Verifying baked dependency imports..."
python - << 'PYVERIFY'
import importlib
import sys

# torch must be the CPU build. NOTE: torch.cuda.is_available() is False even for a
# CUDA build on this GPU-less builder, so it can't tell CPU from CUDA — check the
# build's compiled CUDA version instead (None only for a true CPU-only wheel).
import torch
assert torch.version.cuda is None, (
    f"baked torch is a CUDA build (torch.version.cuda={torch.version.cuda!r}) — "
    "expected CPU-only; check the --index-url resolves torch from the CPU index"
)

# Canary imports across the encode/generation dep tree. tenacity + the langchain
# chain are the exact modules whose absence broke fresh fallback VMs before.
modules = [
    "tenacity",
    "langchain",
    "langchain_core",
    "transformers",
    "librosa",
    "onnxruntime",
]
failed = []
for name in modules:
    try:
        importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001
        failed.append(f"{name}: {exc}")
if failed:
    print("FATAL: baked dependency import check failed:")
    for f in failed:
        print(f"  - {f}")
    sys.exit(1)
print("Baked dependency imports OK (CPU-only torch).")
PYVERIFY

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
echo "  - Full karaoke-gen dependency tree baked (CPU-only torch)"
echo "  - Systemd service: encoding-worker.service"
echo ""
echo "Immutable deployment pattern:"
echo "  - bootstrap.sh downloads startup.sh from GCS on every start"
echo "  - startup.sh is CI-managed, no image rebuild needed for logic changes"
echo "  - See infrastructure/encoding-worker/README.md"
