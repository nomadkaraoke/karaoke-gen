#!/bin/bash
# Image-time provisioning for ephemeral GitHub Actions runner images.
# Builds one of three variants:
#   general — Docker, Python 3.13, Node 20, Java 21, FFmpeg, gcloud, Poetry, runner binary
#   build   — Same as general + pre-pulled Docker base images for deploy-backend
#   gpu     — NVIDIA driver/CUDA, Python 3.13, FFmpeg+libsamplerate, gcloud, Poetry,
#             runner binary, audio-separator models (~14GB)
#
# Runs on a fresh Debian 12 VM as root. Idempotent (safe to re-run).
# Signals completion by writing /opt/runner-image-ready.

set -euo pipefail

# Redirect ALL output to BOTH the on-VM log file AND the GCE serial console.
# The serial console is readable via `gcloud compute instances
# get-serial-port-output` with no SSH/IAP dependency, which is the only
# diagnostic channel that has worked reliably for us when IAP tunnel issues
# break SSH. Without this, a script crash mid-provision is invisible until
# someone can get a working SSH session, which can take many retries.
mkdir -p /var/log
# /dev/ttyS0 is the GCE serial console (read via `gcloud compute instances
# get-serial-port-output --port=1`). Use a single tee with multiple targets
# so a failure on one (e.g. ttyS0 unwritable in some test env) doesn't break
# the other; tee will print an error and continue with the remaining files.
exec > >(tee /var/log/runner-image-provision.log /dev/ttyS0 2>/dev/null) 2>&1

# ERR trap: print the failed command and line number to stdout (which is
# also serial-console-routed) so any non-zero exit leaves a clear marker.
on_err() {
    local rc=$?
    local line=${BASH_LINENO[0]:-?}
    local cmd="${BASH_COMMAND:-?}"
    echo
    echo "########################################################################"
    echo "### runner-image-provision FAILED"
    echo "###   exit=$rc  line=$line  cmd=$cmd"
    echo "###   variant=${VARIANT:-?}  ts=$(date -u +%FT%TZ)"
    echo "########################################################################"
    # Drop a marker on disk so the GHA workflow can `gcloud compute scp`
    # this back if SSH happens to work — but the serial console output
    # above is the primary diagnostic.
    printf '{"variant":"%s","rc":%d,"line":%s,"cmd":"%s","ts":"%s"}\n' \
        "${VARIANT:-unknown}" "$rc" "$line" "${cmd//\"/\\\"}" "$(date -u +%FT%TZ)" \
        > /opt/runner-image-failed 2>/dev/null || true
}
trap on_err ERR

# Phase checkpoint helper — prominent on serial console.
ck() {
    echo
    echo "================================================================"
    echo "### runner-image: $*  ($(date -u +%FT%TZ))"
    echo "================================================================"
}

# Resolve the variant in priority order:
#   1. positional arg (interactive runs / tests)
#   2. RUNNER_IMAGE_VARIANT env var
#   3. GCE instance metadata key `image-variant` (workflow sets this)
VARIANT="${1:-${RUNNER_IMAGE_VARIANT:-}}"
if [[ -z "$VARIANT" ]]; then
    VARIANT=$(curl -sf -H "Metadata-Flavor: Google" \
        "http://metadata.google.internal/computeMetadata/v1/instance/attributes/image-variant" \
        2>/dev/null || true)
fi

if [[ -z "$VARIANT" ]]; then
    echo "ERROR: variant must be provided as \$1, \$RUNNER_IMAGE_VARIANT, or instance metadata 'image-variant'" >&2
    echo "Valid variants: general | build | gpu" >&2
    exit 2
fi

case "$VARIANT" in
    general|build|gpu) ;;
    *) echo "ERROR: unknown variant '$VARIANT'" >&2; exit 2 ;;
esac

ck "starting variant=$VARIANT"

export DEBIAN_FRONTEND=noninteractive

# ==================== Pacify apt-daily race ====================
# Fresh Debian VMs auto-start `apt-daily.service` / `apt-daily-upgrade.service`
# in the background. These hold /var/lib/apt/lists/lock and /var/lib/dpkg/lock,
# and any concurrent `apt-get` (especially the GHA runner's own
# installdependencies.sh later in this script) fails with:
#   E: Could not get lock /var/lib/apt/lists/lock. It is held by process N (apt-get)
# Disable the timers + services up front, then wait for any in-flight apt run
# to release its locks. Without this we hit a race ~30% of the time.
ck "phase: pacify apt-daily"
systemctl stop apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
systemctl disable apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
systemctl mask apt-daily.service apt-daily-upgrade.service 2>/dev/null || true
# Wait for any apt-get already in flight to release its lock (up to 2 min).
for i in $(seq 1 60); do
    if ! fuser /var/lib/apt/lists/lock /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock 2>/dev/null; then
        break
    fi
    echo "  apt locked by another process, waiting 2s..."
    sleep 2
done

# ==================== Network ====================
ck "phase: network (apt IPv4)"
# Cloud NAT doesn't support IPv6 — force apt to IPv4 to avoid hangs.
echo 'Acquire::ForceIPv4 "true";' > /etc/apt/apt.conf.d/99force-ipv4

# ==================== Helpers ====================
install_gpg_key() {
    local url="$1" dest="$2"
    [[ -f "$dest" ]] && return 0
    curl -fsSL "$url" | gpg --batch --dearmor -o "$dest"
}

retry() {
    local n=0 max="${RETRIES:-3}" delay="${RETRY_DELAY:-5}"
    until "$@"; do
        n=$((n+1))
        if (( n >= max )); then return 1; fi
        echo "Retry $n/$max after failure: $*"
        sleep "$delay"
    done
}

# ==================== Base packages (all variants) ====================
ck "phase: apt base packages"
apt-get update
apt-get install -y \
    curl git jq unzip \
    build-essential libssl-dev libffi-dev \
    software-properties-common apt-transport-https ca-certificates gnupg lsb-release

# ==================== Variant: GPU — kernel + NVIDIA + CUDA ====================
if [[ "$VARIANT" == "gpu" ]]; then
    ck "phase: gpu kernel + nvidia + cuda"
    RUNNING_KERNEL="$(uname -r)"
    REBOOT_FLAG="/var/lib/gpu-image-kernel-upgraded"
    echo "Running kernel: $RUNNING_KERNEL"

    if ! apt-cache show "linux-headers-$RUNNING_KERNEL" &>/dev/null; then
        if [[ -f "$REBOOT_FLAG" ]]; then
            echo "ERROR: kernel headers still unavailable after upgrade+reboot" >&2
            exit 1
        fi
        echo "Headers for $RUNNING_KERNEL unavailable — upgrading kernel and rebooting"
        apt-get install -y linux-image-cloud-amd64 linux-headers-cloud-amd64
        touch "$REBOOT_FLAG"
        reboot
        exit 0
    fi
    rm -f "$REBOOT_FLAG"

    echo "Installing kernel headers for $RUNNING_KERNEL"
    apt-get install -y "linux-headers-$RUNNING_KERNEL" dkms

    # Strip stale kernel headers
    for old in /usr/src/linux-headers-*; do
        ver="$(basename "$old" | sed 's/^linux-headers-//')"
        [[ "$ver" == "$RUNNING_KERNEL" || "$ver" == *common* ]] && continue
        pkg="linux-headers-$ver"
        if dpkg -l "$pkg" 2>/dev/null | grep -q '^ii'; then
            apt-get remove -y "$pkg" 2>/dev/null || true
        fi
    done

    if ! command -v nvidia-smi &>/dev/null; then
        echo "Installing NVIDIA driver + CUDA 12.4"
        install_gpg_key \
            "https://developer.download.nvidia.com/compute/cuda/repos/debian12/x86_64/3bf863cc.pub" \
            "/usr/share/keyrings/cuda-archive-keyring.gpg"
        echo "deb [signed-by=/usr/share/keyrings/cuda-archive-keyring.gpg] https://developer.download.nvidia.com/compute/cuda/repos/debian12/x86_64/ /" \
            > /etc/apt/sources.list.d/cuda.list
        apt-get update
        apt-get install -y cuda-drivers cuda-toolkit-12-4
    fi

    # ============== Boot-time NVIDIA module load / DKMS rebuild ==============
    # The NVIDIA kernel module installed above only loads cleanly when (a) the
    # baked .ko matches the running kernel and (b) udev / something else
    # actually modprobes it. Neither is guaranteed on ephemeral VMs:
    #
    #   1. `apt-get install cuda-drivers` can pull in a newer kernel image
    #      (linux-image-cloud-amd64 is a meta-package). The build VM keeps
    #      running the kernel it booted with, so DKMS builds nvidia.ko for
    #      *that* kernel — but a fresh VM from this image boots whichever
    #      kernel GRUB picks (usually the newest installed), leading to a
    #      module-not-found mismatch. Confirmed on gha-runner-gpu-20260517:
    #      image baked nvidia.ko for 6.1.0-47, fresh VMs boot 6.1.0-48.
    #   2. There's no udev rule or modules-load.d entry that asks the kernel
    #      to load nvidia at boot, so even when the .ko exists for the running
    #      kernel nothing triggers a modprobe — nvidia-persistenced just fails
    #      with "Failed to query NVIDIA devices" because /dev/nvidia* never
    #      gets created.
    #
    # Fix: bake a systemd unit that runs at every boot, before
    # nvidia-persistenced and the GCE startup-script. It checks for the
    # running-kernel .ko, runs `dkms autoinstall` if missing (installing
    # matching headers first), then modprobes. Legacy long-lived GPU runners
    # had this logic in their startup-script; ephemeral VMs need it baked
    # into the image.
    ck "phase: gpu boot-time modprobe systemd unit"

    install -m 0755 /dev/stdin /usr/local/sbin/gha-gpu-modprobe <<'GPU_MODPROBE_SCRIPT'
#!/bin/bash
# Build (if needed) and load NVIDIA kernel modules.
# Idempotent — fast no-op when nvidia is already loaded.
set -eu
export DEBIAN_FRONTEND=noninteractive

if lsmod | grep -q '^nvidia '; then
    echo "[gha-gpu-modprobe] nvidia already loaded"
    exit 0
fi

KR="$(uname -r)"
DKMS_KO="/lib/modules/${KR}/updates/dkms/nvidia.ko"

if [[ ! -f "$DKMS_KO" ]]; then
    echo "[gha-gpu-modprobe] nvidia.ko missing for ${KR}; rebuilding via DKMS"
    if ! dpkg -s "linux-headers-${KR}" >/dev/null 2>&1; then
        echo "[gha-gpu-modprobe] installing linux-headers-${KR}"
        apt-get update -qq
        apt-get install -y --no-upgrade "linux-headers-${KR}"
    fi
    dkms autoinstall -k "${KR}"
fi

echo "[gha-gpu-modprobe] modprobing nvidia kernel modules"
modprobe nvidia
modprobe nvidia_uvm
modprobe nvidia_drm

echo "[gha-gpu-modprobe] verifying with nvidia-smi"
nvidia-smi >/dev/null
echo "[gha-gpu-modprobe] OK"
GPU_MODPROBE_SCRIPT

    install -m 0644 /dev/stdin /etc/systemd/system/gha-gpu-modprobe.service <<'GPU_MODPROBE_UNIT'
[Unit]
Description=Build (if needed) and load NVIDIA kernel modules at boot
DefaultDependencies=yes
After=network-online.target
Wants=network-online.target
Before=nvidia-persistenced.service google-startup-scripts.service
ConditionPathExists=/usr/local/sbin/gha-gpu-modprobe

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/gha-gpu-modprobe
StandardOutput=journal+console
StandardError=journal+console
TimeoutStartSec=600

[Install]
WantedBy=multi-user.target
GPU_MODPROBE_UNIT

    # systemctl enable creates the symlink in /etc/systemd/system/multi-user.target.wants/.
    # Falling back to a direct symlink keeps this idempotent if systemd's daemon
    # bus isn't reachable mid-image-build (rare, but observed once on a slow
    # build VM).
    systemctl enable gha-gpu-modprobe.service 2>/dev/null \
        || ln -sf /etc/systemd/system/gha-gpu-modprobe.service \
                  /etc/systemd/system/multi-user.target.wants/gha-gpu-modprobe.service
fi

# ==================== Docker (general + build only) ====================
if [[ "$VARIANT" != "gpu" ]]; then
    ck "phase: docker"
    if ! command -v docker &>/dev/null; then
        echo "Installing Docker"
        install_gpg_key \
            "https://download.docker.com/linux/debian/gpg" \
            "/usr/share/keyrings/docker-archive-keyring.gpg"
        echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" \
            > /etc/apt/sources.list.d/docker.list
        apt-get update
        apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    fi
    systemctl enable docker
fi

# ==================== Python 3.13 ====================
# Use pyenv for ALL variants. Compiling Python 3.13 from source by hand
# proved fragile (3.13.0 hit an install-time ensurepip bug; 3.13.2 without
# --enable-optimizations produced a broken _socket extension that crashed
# any urllib-using script with `SystemError: error return without exception
# set`). pyenv has been hardened against these issues over many releases.
ck "phase: python 3.13"
if [[ ! -f /opt/pyenv/versions/3.13.0/bin/python3.13 ]]; then
    echo "Installing Python 3.13 via pyenv"
    apt-get install -y make zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev \
        wget llvm libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev liblzma-dev
    [[ -d /opt/pyenv ]] || { export PYENV_ROOT=/opt/pyenv; curl https://pyenv.run | bash; }
    export PATH="/opt/pyenv/bin:$PATH"
    eval "$(/opt/pyenv/bin/pyenv init -)"
    /opt/pyenv/bin/pyenv install 3.13.0
    /opt/pyenv/bin/pyenv global 3.13.0
    ln -sf /opt/pyenv/shims/python3.13 /usr/local/bin/python3.13
    ln -sf /opt/pyenv/shims/python3 /usr/local/bin/python3
    ln -sf /opt/pyenv/shims/pip3 /usr/local/bin/pip3
fi
PYTHON_PREFIX="/opt/pyenv/versions/3.13.0"
cat > /etc/profile.d/pyenv.sh <<'EOF'
export PYENV_ROOT=/opt/pyenv
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"
EOF

# ==================== Node.js 20 + Java 21 (general/build only) ====================
if [[ "$VARIANT" != "gpu" ]]; then
    ck "phase: node 20 + java 21"
    if ! command -v node &>/dev/null || ! node --version | grep -q "^v20"; then
        echo "Installing Node.js 20"
        retry curl -fsSL --retry 3 --retry-delay 5 https://deb.nodesource.com/setup_20.x -o /tmp/nodesource.sh
        bash /tmp/nodesource.sh
        apt-get install -y nodejs
        rm -f /tmp/nodesource.sh
    fi

    if ! java -version 2>&1 | grep -q '"21\.'; then
        echo "Installing Java 21 (Temurin)"
        install_gpg_key \
            "https://packages.adoptium.net/artifactory/api/gpg/key/public" \
            "/usr/share/keyrings/adoptium.gpg"
        echo "deb [signed-by=/usr/share/keyrings/adoptium.gpg] https://packages.adoptium.net/artifactory/deb $(lsb_release -cs) main" \
            > /etc/apt/sources.list.d/adoptium.list
        apt-get update
        apt-get install -y temurin-21-jdk
    fi
fi

# ==================== FFmpeg (all variants) ====================
ck "phase: ffmpeg"
if [[ "$VARIANT" == "gpu" ]]; then
    apt-get install -y ffmpeg libsamplerate0 libsamplerate-dev
else
    apt-get install -y ffmpeg
fi

# ==================== Poetry + gcloud (all variants) ====================
ck "phase: poetry + gcloud"
if ! command -v poetry &>/dev/null; then
    echo "Installing Poetry"
    # Install to /opt/poetry on every variant so the runner user can access
    # poetry's binaries — /root/.local is mode 700 and shimming through there
    # works only for jobs that run as root (we now have a runner user).
    export POETRY_HOME="/opt/poetry"
    curl -sSL https://install.python-poetry.org | python3 -
    ln -sf /opt/poetry/bin/poetry /usr/local/bin/poetry
fi

if ! command -v gcloud &>/dev/null; then
    echo "Installing Google Cloud SDK"
    install_gpg_key \
        "https://packages.cloud.google.com/apt/doc/apt-key.gpg" \
        "/usr/share/keyrings/cloud.google.gpg"
    echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
        > /etc/apt/sources.list.d/google-cloud-sdk.list
    apt-get update
    if [[ "$VARIANT" != "gpu" ]]; then
        apt-get install -y google-cloud-cli google-cloud-cli-firestore-emulator
    else
        apt-get install -y google-cloud-cli
    fi
fi

# ==================== Runner user ====================
ck "phase: runner user"
useradd -m -s /bin/bash runner 2>/dev/null || true
if [[ "$VARIANT" != "gpu" ]]; then
    usermod -aG docker runner
fi

# Many CI workflows install OS packages with `sudo apt-get …`. The legacy
# GHA runner VMs had passwordless sudo for the runner user; without it,
# steps like `sudo apt-get install -y google-cloud-cli-firestore-emulator`
# in backend-emulator-tests fail with "a terminal is required to read the
# password". Match the legacy behaviour.
mkdir -p /etc/sudoers.d
echo "runner ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/runner
chmod 0440 /etc/sudoers.d/runner
visudo -cf /etc/sudoers.d/runner

# ==================== GitHub Actions runner binary ====================
# Keep current with actions/runner releases. GitHub deprecates older runner
# versions and returns HTTP 403 ("deprecated and cannot receive messages") on
# the broker poll — and ephemeral/JIT runners run with disableUpdate, so they
# CANNOT self-update. A stale pin silently breaks every ephemeral runner: it
# registers, gets rejected, and self-halts, leaving CI jobs queued forever.
# When bumping, check https://github.com/actions/runner/releases/latest.
ck "phase: github actions runner binary"
RUNNER_VERSION="2.336.0"
RUNNER_DIR="/home/runner/actions-runner"
mkdir -p "$RUNNER_DIR"

CURRENT_VERSION=""
if [[ -f "$RUNNER_DIR/bin/Runner.Listener" ]]; then
    CURRENT_VERSION="$("$RUNNER_DIR/bin/Runner.Listener" --version 2>/dev/null || echo unknown)"
fi
if [[ "$CURRENT_VERSION" != "$RUNNER_VERSION" ]]; then
    echo "Downloading runner v$RUNNER_VERSION"
    cd "$RUNNER_DIR"
    curl -sLO "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
    tar xzf "actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
    rm -f "actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
fi
chown -R runner:runner "$RUNNER_DIR"

# Pre-install runner dependencies (avoids first-run apt cost).
# installdependencies.sh is idempotent.
if [[ -x "$RUNNER_DIR/bin/installdependencies.sh" ]]; then
    "$RUNNER_DIR/bin/installdependencies.sh"
fi

# Convenience symlink so external tooling (runbooks, ad-hoc SSH) can refer to
# the runner installation by a stable path. The startup script in ephemeral.py
# cds to $RUNNER_DIR directly and doesn't rely on this.
ln -sfn "$RUNNER_DIR" /opt/actions-runner

# ==================== Docker base-image pre-pull (build variant only) ====================
if [[ "$VARIANT" == "build" ]]; then
    ck "phase: docker base-image pre-pull"
    echo "Pre-pulling Docker base images for deploy-backend"
    systemctl start docker
    # Image-build VM uses the gha-runner-image-builder@ SA which has artifactregistry.reader.
    gcloud auth configure-docker us-central1-docker.pkg.dev --quiet || true
    gcloud auth configure-docker us-east4-docker.pkg.dev --quiet || true
    docker pull us-central1-docker.pkg.dev/nomadkaraoke/karaoke-repo/karaoke-backend-base:latest || true
    docker pull us-central1-docker.pkg.dev/nomadkaraoke/karaoke-repo/karaoke-backend:latest || true
    docker pull fsouza/fake-gcs-server:latest || true
fi

# ==================== Audio-separator models (GPU only) ====================
# ~14GB of model weights baked into the image so jobs don't re-download.
if [[ "$VARIANT" == "gpu" ]]; then
    ck "phase: audio-separator models (~14GB)"
    MODEL_DIR="/opt/audio-separator-models"
    mkdir -p "$MODEL_DIR"
    BASE_URL="https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models"
    CONFIG_URL="https://raw.githubusercontent.com/TRvlvr/application_data/main/mdx_model_data/mdx_c_configs"
    META_URL="https://raw.githubusercontent.com/TRvlvr/application_data/main"
    DEMUCS_URL="https://dl.fbaipublicfiles.com/demucs/hybrid_transformer"
    FALLBACK_URL="https://github.com/nomadkaraoke/python-audio-separator/releases/download/model-configs"

    dl() {
        local url="$1" dest="$2" fb="${3:-}"
        [[ -s "$dest" ]] && return 0
        echo "  → $(basename "$dest")"
        if curl -fSL --retry 3 --connect-timeout 30 -o "$dest" "$url" 2>/dev/null; then return 0; fi
        if [[ -n "$fb" ]]; then
            curl -fSL --retry 3 --connect-timeout 30 -o "$dest" "$fb" || return 1
        fi
    }

    # Metadata
    dl "$META_URL/filelists/download_checks.json" "$MODEL_DIR/download_checks.json"
    dl "$META_URL/vr_model_data/model_data_new.json" "$MODEL_DIR/vr_model_data.json"
    dl "$META_URL/mdx_model_data/model_data_new.json" "$MODEL_DIR/mdx_model_data.json"

    # Core integration test models (8)
    dl "$BASE_URL/mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt" "$MODEL_DIR/mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"
    dl "$FALLBACK_URL/mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956_config.yaml" "$MODEL_DIR/mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956_config.yaml"
    dl "$BASE_URL/kuielab_b_vocals.onnx" "$MODEL_DIR/kuielab_b_vocals.onnx"
    dl "$BASE_URL/MGM_MAIN_v4.pth" "$MODEL_DIR/MGM_MAIN_v4.pth"
    dl "$BASE_URL/UVR-MDX-NET-Inst_HQ_4.onnx" "$MODEL_DIR/UVR-MDX-NET-Inst_HQ_4.onnx"
    dl "$BASE_URL/2_HP-UVR.pth" "$MODEL_DIR/2_HP-UVR.pth"
    dl "$BASE_URL/htdemucs_6s.yaml" "$MODEL_DIR/htdemucs_6s.yaml"
    dl "$DEMUCS_URL/5c90dfd2-34c22ccb.th" "$MODEL_DIR/5c90dfd2-34c22ccb.th"
    dl "$BASE_URL/model_bs_roformer_ep_937_sdr_10.5309.ckpt" "$MODEL_DIR/model_bs_roformer_ep_937_sdr_10.5309.ckpt"
    dl "$CONFIG_URL/model_bs_roformer_ep_937_sdr_10.5309.yaml" "$MODEL_DIR/model_bs_roformer_ep_937_sdr_10.5309.yaml"
    dl "$BASE_URL/model_bs_roformer_ep_317_sdr_12.9755.ckpt" "$MODEL_DIR/model_bs_roformer_ep_317_sdr_12.9755.ckpt"
    dl "$CONFIG_URL/model_bs_roformer_ep_317_sdr_12.9755.yaml" "$MODEL_DIR/model_bs_roformer_ep_317_sdr_12.9755.yaml"

    # Ensemble preset models (15, ~12GB)
    for m in \
        bs_roformer_instrumental_resurrection_unwa.ckpt \
        bs_roformer_vocals_resurrection_unwa.ckpt \
        bs_roformer_vocals_revive_v2_unwa.ckpt \
        bs_roformer_vocals_revive_v3e_unwa.ckpt \
        mel_band_roformer_instrumental_becruily.ckpt \
        mel_band_roformer_instrumental_fv7z_gabox.ckpt \
        mel_band_roformer_instrumental_instv8_gabox.ckpt \
        mel_band_roformer_karaoke_becruily.ckpt \
        mel_band_roformer_karaoke_gabox_v2.ckpt \
        mel_band_roformer_kim_ft2_bleedless_unwa.ckpt \
        mel_band_roformer_vocals_becruily.ckpt \
        mel_band_roformer_vocals_fv4_gabox.ckpt \
        melband_roformer_big_beta6x.ckpt \
        melband_roformer_inst_v1e_plus.ckpt \
        UVR-MDX-NET-Inst_HQ_5.onnx; do
        dl "$FALLBACK_URL/$m" "$MODEL_DIR/$m"
    done
    for c in \
        config_bs_roformer_instrumental_resurrection_unwa.yaml \
        config_bs_roformer_vocals_resurrection_unwa.yaml \
        config_bs_roformer_vocals_revive_unwa.yaml \
        config_mel_band_roformer_instrumental_becruily.yaml \
        config_mel_band_roformer_instrumental_gabox.yaml \
        config_mel_band_roformer_karaoke_becruily.yaml \
        config_mel_band_roformer_karaoke_gabox.yaml \
        config_mel_band_roformer_kim_ft_unwa.yaml \
        config_mel_band_roformer_vocals_becruily.yaml \
        config_mel_band_roformer_vocals_gabox.yaml \
        config_melbandroformer_big_beta6x.yaml \
        config_melbandroformer_inst.yaml; do
        dl "$FALLBACK_URL/$c" "$MODEL_DIR/$c"
    done

    # Multi-stem test models
    dl "$BASE_URL/17_HP-Wind_Inst-UVR.pth" "$MODEL_DIR/17_HP-Wind_Inst-UVR.pth"
    dl "$FALLBACK_URL/MDX23C-DrumSep-aufr33-jarredou.ckpt" "$MODEL_DIR/MDX23C-DrumSep-aufr33-jarredou.ckpt"
    dl "$FALLBACK_URL/dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt" "$MODEL_DIR/dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt"
    dl "$FALLBACK_URL/config_dereverb_mel_band_roformer_anvuew.yaml" "$MODEL_DIR/config_dereverb_mel_band_roformer_anvuew.yaml"

    chown -R runner:runner "$MODEL_DIR"
    echo "Audio-separator models: $(du -sh "$MODEL_DIR" | cut -f1)"
fi

# ==================== Tool cache for setup-python action ====================
ck "phase: tool cache for setup-python"
# setup-python@v5 looks for prebuilt Python in $RUNNER_TOOL_CACHE/Python/<ver>/x64
TOOL_CACHE="/home/runner/actions-runner/_work/_tool"
mkdir -p "$TOOL_CACHE/Python/3.13.0/x64"
if [[ -d "$PYTHON_PREFIX" && ! -f "$TOOL_CACHE/Python/3.13.0/x64.complete" ]]; then
    cp -r "$PYTHON_PREFIX"/* "$TOOL_CACHE/Python/3.13.0/x64/"
    (cd "$TOOL_CACHE/Python/3.13.0/x64/bin"; [[ -f python ]] || ln -s python3.13 python; [[ -f python3 ]] || ln -s python3.13 python3)
    touch "$TOOL_CACHE/Python/3.13.0/x64.complete"
fi
chown -R runner:runner /home/runner/actions-runner/_work

# ==================== Docker disk-pressure cleanup cron ====================
# Carried over from the legacy startup script. Useful even on ephemeral VMs
# because deploy-backend can fill the disk during long Docker builds.
if [[ "$VARIANT" != "gpu" ]]; then
    cat > /etc/cron.hourly/docker-cleanup <<'EOF'
#!/bin/bash
exec 1> >(logger -t docker-cleanup) 2>&1
THRESHOLD=70
USAGE=$(df / | tail -1 | awk '{print $5}' | tr -d '%')
if (( USAGE > THRESHOLD )); then
    docker system prune -af
    docker builder prune -af
fi
EOF
    chmod +x /etc/cron.hourly/docker-cleanup
fi

# ==================== Cleanup before imaging ====================
ck "phase: cleanup before imaging"
apt-get clean
rm -rf /var/lib/apt/lists/*
journalctl --vacuum-time=1s 2>/dev/null || true

# Remove SSH host keys — regenerated on first boot of cloned VMs
rm -f /etc/ssh/ssh_host_*

# Mark image ready (the workflow polls for this file via gcloud ssh, but
# the primary completion signal is now the "READY" line on the serial
# console below).
date -u +%FT%TZ > /opt/runner-image-ready
echo "$VARIANT" > /opt/runner-image-variant

ck "READY: variant=$VARIANT  ts=$(date -u +%FT%TZ)"
