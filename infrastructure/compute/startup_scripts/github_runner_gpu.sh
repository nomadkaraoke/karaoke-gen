#!/bin/bash
# GitHub Actions GPU Runner startup script
# Designed to be IDEMPOTENT - safe to re-run on spot VM preemption/restart.
# This is a GPU-specific variant of github_runner.sh:
# - Adds NVIDIA driver + CUDA toolkit installation
# - Skips Docker, Java, Node.js (not needed for audio-separator CI)
# - Includes libsamplerate for audio processing

# Log to a file for debugging
exec > >(tee /var/log/github-runner-startup.log) 2>&1
echo "Starting GitHub Actions GPU runner setup at $(date)"

# ==================== Environment ====================
# Prevent interactive prompts during package installation (e.g., keyboard-configuration)
export DEBIAN_FRONTEND=noninteractive

# ==================== Network ====================
# Force apt to use IPv4 only — Cloud NAT doesn't support IPv6
echo 'Acquire::ForceIPv4 "true";' > /etc/apt/apt.conf.d/99force-ipv4

# ==================== Helper ====================
install_gpg_key() {
    local url="$1"
    local dest="$2"
    if [ -f "$dest" ]; then
        echo "GPG key already exists: $dest"
        return 0
    fi
    curl -fsSL "$url" | gpg --batch --dearmor -o "$dest"
}

# ==================== System packages ====================
apt-get update
apt-get install -y \
    curl \
    git \
    jq \
    unzip \
    build-essential \
    libssl-dev \
    libffi-dev \
    software-properties-common \
    apt-transport-https \
    ca-certificates \
    gnupg \
    lsb-release

# ==================== Kernel + NVIDIA drivers ====================
# Strategy:
#   The base debian-cloud/debian-12 image ships an older kernel (e.g., 6.1.0-43)
#   but Debian repos drop old kernel headers once a new point release lands.
#   If headers aren't available for the running kernel, we upgrade the kernel,
#   reboot ONCE (tracked via metadata flag), and then install headers + NVIDIA.
#
#   On subsequent stop/start cycles the kernel stays current and headers match,
#   so the script just ensures DKMS has built the nvidia module.

RUNNING_KERNEL="$(uname -r)"
REBOOT_FLAG="/var/lib/gpu-runner-kernel-upgraded"
echo "Running kernel: $RUNNING_KERNEL"

# Check if headers are available for the running kernel
if ! apt-cache show "linux-headers-$RUNNING_KERNEL" &>/dev/null; then
    if [ -f "$REBOOT_FLAG" ]; then
        echo "ERROR: Headers still unavailable after kernel upgrade + reboot. Giving up."
        echo "Running kernel: $RUNNING_KERNEL"
        apt-cache search linux-headers | grep cloud | tail -5
        exit 1
    fi

    echo "Headers for $RUNNING_KERNEL not in repos — upgrading kernel and rebooting..."

    # Upgrade kernel to latest available version (headers will match after reboot)
    apt-get install -y linux-image-cloud-amd64 linux-headers-cloud-amd64

    # Mark that we've upgraded so we don't loop forever
    touch "$REBOOT_FLAG"

    echo "Kernel upgraded. Rebooting to load new kernel..."
    reboot
    # Script exits here; startup script re-runs after reboot with the new kernel
    exit 0
fi

# If we got here after a reboot, clean up the flag
rm -f "$REBOOT_FLAG"

# 1. Install headers + DKMS for the running kernel
echo "Installing kernel headers for $RUNNING_KERNEL..."
apt-get install -y "linux-headers-$RUNNING_KERNEL" dkms

# 2. Clean up headers for kernels we're no longer running (prevents disk bloat)
for old_headers in /usr/src/linux-headers-*; do
    header_ver="$(basename "$old_headers" | sed 's/^linux-headers-//')"
    # Skip the running kernel's headers and the -common package
    if [ "$header_ver" = "$RUNNING_KERNEL" ] || echo "$header_ver" | grep -q "common"; then
        continue
    fi
    pkg="linux-headers-$header_ver"
    if dpkg -l "$pkg" 2>/dev/null | grep -q '^ii'; then
        echo "Removing stale kernel headers: $pkg"
        apt-get remove -y "$pkg" 2>/dev/null || true
    fi
done

# 3. Install NVIDIA packages if not yet present
if ! command -v nvidia-smi &>/dev/null; then
    echo "Installing NVIDIA drivers..."

    # Add NVIDIA CUDA repository for Debian 12
    install_gpg_key \
        "https://developer.download.nvidia.com/compute/cuda/repos/debian12/x86_64/3bf863cc.pub" \
        "/usr/share/keyrings/cuda-archive-keyring.gpg"
    echo "deb [signed-by=/usr/share/keyrings/cuda-archive-keyring.gpg] https://developer.download.nvidia.com/compute/cuda/repos/debian12/x86_64/ /" > /etc/apt/sources.list.d/cuda.list
    apt-get update

    # Install NVIDIA driver + CUDA toolkit
    # PyTorch bundles its own CUDA runtime, but we need the driver at minimum.
    # Installing cuda-toolkit gives us nvidia-smi and full CUDA support.
    apt-get install -y cuda-drivers cuda-toolkit-12-4
else
    echo "NVIDIA packages already installed"
fi

# 4. If the kernel module isn't loaded (e.g., kernel upgraded since last boot),
#    rebuild via DKMS and load it. This is the common failure mode: packages are
#    installed but the module was built against an older kernel.
if ! lsmod | grep -q '^nvidia '; then
    echo "NVIDIA kernel module not loaded for $RUNNING_KERNEL — rebuilding with DKMS..."
    echo "DKMS status before rebuild:"
    dkms status || true

    dkms autoinstall
    modprobe nvidia

    echo "DKMS status after rebuild:"
    dkms status || true
fi

# 5. Verify GPU is accessible
echo "Verifying GPU..."
for attempt in $(seq 1 10); do
    if nvidia-smi; then
        echo "GPU verified successfully on attempt $attempt"
        break
    fi
    if [ "$attempt" -eq 10 ]; then
        echo "ERROR: nvidia-smi failed after 10 attempts — GPU not available"
        echo "Diagnostic info:"
        dkms status || true
        ls /lib/modules/"$RUNNING_KERNEL"/updates/dkms/ 2>/dev/null || echo "No DKMS modules found for $RUNNING_KERNEL"
        exit 1
    fi
    echo "nvidia-smi attempt $attempt failed, retrying in 5s..."
    sleep 5
done

# ==================== FFmpeg + audio libraries ====================
if ! command -v ffmpeg &>/dev/null; then
    echo "Installing FFmpeg and audio libraries..."
    apt-get install -y ffmpeg libsamplerate0 libsamplerate-dev
else
    echo "FFmpeg already installed, skipping"
fi

# ==================== Python 3.13 from source ====================
# Compile Python 3.13 directly (pyenv is unreliable on Debian 12 with CUDA).
# setup-python@v5 doesn't have prebuilt Debian 12 binaries, so we must
# provide Python in the tool cache for it to find.
PYTHON_PREFIX="/opt/python-3.13"
if [ ! -f "$PYTHON_PREFIX/bin/python3.13" ]; then
    echo "Building Python 3.13 from source..."

    apt-get install -y make build-essential libssl-dev zlib1g-dev \
      libbz2-dev libreadline-dev libsqlite3-dev wget curl llvm \
      libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev

    cd /tmp
    curl -sSL https://www.python.org/ftp/python/3.13.0/Python-3.13.0.tgz -o Python-3.13.0.tgz
    tar xzf Python-3.13.0.tgz
    cd Python-3.13.0
    ./configure --prefix="$PYTHON_PREFIX" --enable-optimizations --with-ensurepip=install
    make -j$(nproc)
    make install
    rm -rf /tmp/Python-3.13.0*

    # Create convenience symlinks
    ln -sf "$PYTHON_PREFIX/bin/python3.13" /usr/local/bin/python3.13
    ln -sf "$PYTHON_PREFIX/bin/python3" /usr/local/bin/python3
    ln -sf "$PYTHON_PREFIX/bin/pip3" /usr/local/bin/pip3
else
    echo "Python 3.13 already installed at $PYTHON_PREFIX, skipping"
fi

# ==================== Poetry ====================
if ! command -v poetry &>/dev/null; then
    echo "Installing Poetry..."
    curl -sSL https://install.python-poetry.org | python3 -
    ln -sf /root/.local/bin/poetry /usr/local/bin/poetry
else
    echo "Poetry already installed, skipping"
fi

# ==================== Google Cloud SDK ====================
if ! command -v gcloud &>/dev/null; then
    echo "Installing Google Cloud SDK..."
    install_gpg_key \
        "https://packages.cloud.google.com/apt/doc/apt-key.gpg" \
        "/usr/share/keyrings/cloud.google.gpg"
    echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" > /etc/apt/sources.list.d/google-cloud-sdk.list
    apt-get update
    apt-get install -y google-cloud-cli
else
    echo "Google Cloud SDK already installed, skipping"
fi

# ==================== Create runner user ====================
echo "Ensuring runner user exists..."
useradd -m -s /bin/bash runner 2>/dev/null || true

# ==================== GitHub Actions Runner ====================
# This section ALWAYS runs — it handles version upgrades and re-registration
echo "Setting up GitHub Actions Runner..."
RUNNER_VERSION="2.332.0"
RUNNER_DIR="/home/runner/actions-runner"

mkdir -p $RUNNER_DIR

# Stop existing service if running (for upgrades/re-registration)
if [ -f "$RUNNER_DIR/svc.sh" ]; then
    echo "Stopping existing runner service..."
    cd $RUNNER_DIR
    ./svc.sh stop 2>/dev/null || true
    ./svc.sh uninstall 2>/dev/null || true
fi

# Check if runner needs download/upgrade
CURRENT_VERSION=""
if [ -f "$RUNNER_DIR/bin/Runner.Listener" ]; then
    CURRENT_VERSION=$("$RUNNER_DIR/bin/Runner.Listener" --version 2>/dev/null || echo "unknown")
fi

if [ "$CURRENT_VERSION" != "$RUNNER_VERSION" ]; then
    echo "Upgrading runner from ${CURRENT_VERSION:-none} to ${RUNNER_VERSION}..."
    cd $RUNNER_DIR
    curl -sO -L \
        https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz
    tar xzf actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz
    rm -f actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz
else
    echo "Runner already at version ${RUNNER_VERSION}"
fi

chown -R runner:runner $RUNNER_DIR

# Get GitHub PAT from Secret Manager
echo "Fetching GitHub PAT from Secret Manager..."
GITHUB_PAT=$(gcloud secrets versions access latest --secret=github-runner-pat)

if [ -z "$GITHUB_PAT" ]; then
    echo "ERROR: GitHub PAT not found in Secret Manager"
    exit 1
fi

# Get runner registration token using PAT (organization-level)
echo "Getting runner registration token..."
REGISTRATION_TOKEN=$(curl -s -X POST \
    -H "Authorization: token $GITHUB_PAT" \
    -H "Accept: application/vnd.github.v3+json" \
    https://api.github.com/orgs/nomadkaraoke/actions/runners/registration-token | jq -r .token)

if [ -z "$REGISTRATION_TOKEN" ] || [ "$REGISTRATION_TOKEN" = "null" ]; then
    echo "ERROR: Failed to get registration token"
    exit 1
fi

# Read runner labels from instance metadata (allows per-VM customization)
RUNNER_LABELS=$(curl -s -H "Metadata-Flavor: Google" \
    http://metadata.google.internal/computeMetadata/v1/instance/attributes/runner-labels)
RUNNER_LABELS=${RUNNER_LABELS:-"self-hosted,linux,x64,gcp,gpu"}

# Configure runner (non-interactive) - registered at organization level
echo "Configuring runner..."
cd $RUNNER_DIR
sudo -u runner ./config.sh \
    --url https://github.com/nomadkaraoke \
    --token "$REGISTRATION_TOKEN" \
    --name "gcp-gpu-runner-$(hostname)" \
    --labels "$RUNNER_LABELS" \
    --work "_work" \
    --unattended \
    --replace

# Install runner service (started AFTER all caches are warm — see end of script)
echo "Installing runner service..."
./svc.sh install runner

# ==================== Setup Python in tool cache ====================
# setup-python action looks for Python in RUNNER_TOOL_CACHE/_tool/Python
echo "Setting up Python 3.13 in tool cache for setup-python action..."
TOOL_CACHE="/home/runner/actions-runner/_work/_tool"
mkdir -p $TOOL_CACHE/Python/3.13.0/x64

# Copy compiled Python to tool cache (only if build succeeded and not already cached)
if [ -f "$PYTHON_PREFIX/bin/python3.13" ] && [ ! -f "$TOOL_CACHE/Python/3.13.0/x64.complete" ]; then
    cp -r "$PYTHON_PREFIX"/* $TOOL_CACHE/Python/3.13.0/x64/
    # Ensure python and python3 symlinks exist (setup-python expects them)
    cd $TOOL_CACHE/Python/3.13.0/x64/bin
    [ ! -f python ] && ln -s python3.13 python
    [ ! -f python3 ] && ln -s python3.13 python3
    touch $TOOL_CACHE/Python/3.13.0/x64.complete
    echo "Python 3.13 added to tool cache"
elif [ -f "$TOOL_CACHE/Python/3.13.0/x64.complete" ]; then
    echo "Python 3.13 already in tool cache"
else
    echo "ERROR: Python 3.13 build failed — setup-python will not find Python"
fi

# Fix permissions - must own entire _work directory, not just _tool
chown -R runner:runner /home/runner/actions-runner/_work

# ==================== Audio separator model pre-download ====================
# Pre-download ML models used by python-audio-separator integration tests.
# These models total ~14GB and would otherwise be downloaded on every CI run.
# Stored in /opt/audio-separator-models/ which persists across stop/start cycles
# (persistent disk, not ephemeral).
echo "Pre-downloading audio separator integration test models..."

MODEL_DIR="/opt/audio-separator-models"
mkdir -p "$MODEL_DIR"
chown runner:runner "$MODEL_DIR"

BASE_URL="https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models"
CONFIG_URL="https://raw.githubusercontent.com/TRvlvr/application_data/main/mdx_model_data/mdx_c_configs"
META_URL="https://raw.githubusercontent.com/TRvlvr/application_data/main"
DEMUCS_URL="https://dl.fbaipublicfiles.com/demucs/hybrid_transformer"
FALLBACK_URL="https://github.com/nomadkaraoke/python-audio-separator/releases/download/model-configs"

download_model() {
    local url="$1"
    local dest="$2"
    local fallback_url="${3:-}"
    if [ -f "$dest" ] && [ -s "$dest" ]; then
        return
    fi
    echo "  Downloading: $(basename $dest)"
    if curl -fSL --retry 3 --connect-timeout 30 -o "$dest" "$url" 2>/dev/null; then
        return
    fi
    if [ -n "$fallback_url" ]; then
        echo "  Primary URL failed, trying fallback..."
        curl -fSL --retry 3 --connect-timeout 30 -o "$dest" "$fallback_url" || true
    fi
}

# Metadata files
download_model "$META_URL/filelists/download_checks.json" "$MODEL_DIR/download_checks.json"
download_model "$META_URL/vr_model_data/model_data_new.json" "$MODEL_DIR/vr_model_data.json"
download_model "$META_URL/mdx_model_data/model_data_new.json" "$MODEL_DIR/mdx_model_data.json"

# === Core integration test models (8 models) ===
download_model "$BASE_URL/mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt" "$MODEL_DIR/mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"
download_model "$FALLBACK_URL/mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956_config.yaml" "$MODEL_DIR/mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956_config.yaml"
download_model "$BASE_URL/kuielab_b_vocals.onnx" "$MODEL_DIR/kuielab_b_vocals.onnx"
download_model "$BASE_URL/MGM_MAIN_v4.pth" "$MODEL_DIR/MGM_MAIN_v4.pth"
download_model "$BASE_URL/UVR-MDX-NET-Inst_HQ_4.onnx" "$MODEL_DIR/UVR-MDX-NET-Inst_HQ_4.onnx"
download_model "$BASE_URL/2_HP-UVR.pth" "$MODEL_DIR/2_HP-UVR.pth"
download_model "$BASE_URL/htdemucs_6s.yaml" "$MODEL_DIR/htdemucs_6s.yaml"
download_model "$DEMUCS_URL/5c90dfd2-34c22ccb.th" "$MODEL_DIR/5c90dfd2-34c22ccb.th"
download_model "$BASE_URL/model_bs_roformer_ep_937_sdr_10.5309.ckpt" "$MODEL_DIR/model_bs_roformer_ep_937_sdr_10.5309.ckpt"
download_model "$CONFIG_URL/model_bs_roformer_ep_937_sdr_10.5309.yaml" "$MODEL_DIR/model_bs_roformer_ep_937_sdr_10.5309.yaml"
download_model "$BASE_URL/model_bs_roformer_ep_317_sdr_12.9755.ckpt" "$MODEL_DIR/model_bs_roformer_ep_317_sdr_12.9755.ckpt"
download_model "$CONFIG_URL/model_bs_roformer_ep_317_sdr_12.9755.yaml" "$MODEL_DIR/model_bs_roformer_ep_317_sdr_12.9755.yaml"

# === Ensemble preset models (15 models, ~12GB) ===
# Model weights — hosted on nomadkaraoke releases (not available on TRvlvr)
download_model "$FALLBACK_URL/bs_roformer_instrumental_resurrection_unwa.ckpt" "$MODEL_DIR/bs_roformer_instrumental_resurrection_unwa.ckpt"
download_model "$FALLBACK_URL/bs_roformer_vocals_resurrection_unwa.ckpt" "$MODEL_DIR/bs_roformer_vocals_resurrection_unwa.ckpt"
download_model "$FALLBACK_URL/bs_roformer_vocals_revive_v2_unwa.ckpt" "$MODEL_DIR/bs_roformer_vocals_revive_v2_unwa.ckpt"
download_model "$FALLBACK_URL/bs_roformer_vocals_revive_v3e_unwa.ckpt" "$MODEL_DIR/bs_roformer_vocals_revive_v3e_unwa.ckpt"
download_model "$FALLBACK_URL/mel_band_roformer_instrumental_becruily.ckpt" "$MODEL_DIR/mel_band_roformer_instrumental_becruily.ckpt"
download_model "$FALLBACK_URL/mel_band_roformer_instrumental_fv7z_gabox.ckpt" "$MODEL_DIR/mel_band_roformer_instrumental_fv7z_gabox.ckpt"
download_model "$FALLBACK_URL/mel_band_roformer_instrumental_instv8_gabox.ckpt" "$MODEL_DIR/mel_band_roformer_instrumental_instv8_gabox.ckpt"
download_model "$FALLBACK_URL/mel_band_roformer_karaoke_becruily.ckpt" "$MODEL_DIR/mel_band_roformer_karaoke_becruily.ckpt"
download_model "$FALLBACK_URL/mel_band_roformer_karaoke_gabox_v2.ckpt" "$MODEL_DIR/mel_band_roformer_karaoke_gabox_v2.ckpt"
download_model "$FALLBACK_URL/mel_band_roformer_kim_ft2_bleedless_unwa.ckpt" "$MODEL_DIR/mel_band_roformer_kim_ft2_bleedless_unwa.ckpt"
download_model "$FALLBACK_URL/mel_band_roformer_vocals_becruily.ckpt" "$MODEL_DIR/mel_band_roformer_vocals_becruily.ckpt"
download_model "$FALLBACK_URL/mel_band_roformer_vocals_fv4_gabox.ckpt" "$MODEL_DIR/mel_band_roformer_vocals_fv4_gabox.ckpt"
download_model "$FALLBACK_URL/melband_roformer_big_beta6x.ckpt" "$MODEL_DIR/melband_roformer_big_beta6x.ckpt"
download_model "$FALLBACK_URL/melband_roformer_inst_v1e_plus.ckpt" "$MODEL_DIR/melband_roformer_inst_v1e_plus.ckpt"
download_model "$FALLBACK_URL/UVR-MDX-NET-Inst_HQ_5.onnx" "$MODEL_DIR/UVR-MDX-NET-Inst_HQ_5.onnx"
# YAML configs for ensemble models
download_model "$FALLBACK_URL/config_bs_roformer_instrumental_resurrection_unwa.yaml" "$MODEL_DIR/config_bs_roformer_instrumental_resurrection_unwa.yaml"
download_model "$FALLBACK_URL/config_bs_roformer_vocals_resurrection_unwa.yaml" "$MODEL_DIR/config_bs_roformer_vocals_resurrection_unwa.yaml"
download_model "$FALLBACK_URL/config_bs_roformer_vocals_revive_unwa.yaml" "$MODEL_DIR/config_bs_roformer_vocals_revive_unwa.yaml"
download_model "$FALLBACK_URL/config_mel_band_roformer_instrumental_becruily.yaml" "$MODEL_DIR/config_mel_band_roformer_instrumental_becruily.yaml"
download_model "$FALLBACK_URL/config_mel_band_roformer_instrumental_gabox.yaml" "$MODEL_DIR/config_mel_band_roformer_instrumental_gabox.yaml"
download_model "$FALLBACK_URL/config_mel_band_roformer_karaoke_becruily.yaml" "$MODEL_DIR/config_mel_band_roformer_karaoke_becruily.yaml"
download_model "$FALLBACK_URL/config_mel_band_roformer_karaoke_gabox.yaml" "$MODEL_DIR/config_mel_band_roformer_karaoke_gabox.yaml"
download_model "$FALLBACK_URL/config_mel_band_roformer_kim_ft_unwa.yaml" "$MODEL_DIR/config_mel_band_roformer_kim_ft_unwa.yaml"
download_model "$FALLBACK_URL/config_mel_band_roformer_vocals_becruily.yaml" "$MODEL_DIR/config_mel_band_roformer_vocals_becruily.yaml"
download_model "$FALLBACK_URL/config_mel_band_roformer_vocals_gabox.yaml" "$MODEL_DIR/config_mel_band_roformer_vocals_gabox.yaml"
download_model "$FALLBACK_URL/config_melbandroformer_big_beta6x.yaml" "$MODEL_DIR/config_melbandroformer_big_beta6x.yaml"
download_model "$FALLBACK_URL/config_melbandroformer_inst.yaml" "$MODEL_DIR/config_melbandroformer_inst.yaml"

# === Multi-stem test models ===
download_model "$BASE_URL/17_HP-Wind_Inst-UVR.pth" "$MODEL_DIR/17_HP-Wind_Inst-UVR.pth"
download_model "$FALLBACK_URL/MDX23C-DrumSep-aufr33-jarredou.ckpt" "$MODEL_DIR/MDX23C-DrumSep-aufr33-jarredou.ckpt"
download_model "$FALLBACK_URL/dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt" "$MODEL_DIR/dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt"
download_model "$FALLBACK_URL/config_dereverb_mel_band_roformer_anvuew.yaml" "$MODEL_DIR/config_dereverb_mel_band_roformer_anvuew.yaml"

chown -R runner:runner "$MODEL_DIR"
echo "Audio separator models pre-download complete ($(du -sh $MODEL_DIR | cut -f1))"

# ==================== Start runner service ====================
# Start AFTER all caches are warm so the runner only comes online
# when the VM is fully ready to accept CI jobs.
echo "Starting runner service..."
cd $RUNNER_DIR
./svc.sh start

echo "GitHub Actions GPU runner setup complete at $(date)"
echo "Runner registered at organization level (nomadkaraoke) with labels: $RUNNER_LABELS"
