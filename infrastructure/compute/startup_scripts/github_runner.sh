#!/bin/bash
# GitHub Actions Runner startup script
# Designed to be IDEMPOTENT - safe to re-run on spot VM preemption/restart.
# Software installation is skipped if already present; runner re-registration always runs.

# Log to a file for debugging
exec > >(tee /var/log/github-runner-startup.log) 2>&1
echo "Starting GitHub Actions runner setup at $(date)"

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

# ==================== Docker ====================
if ! command -v docker &>/dev/null; then
    echo "Installing Docker..."
    install_gpg_key \
        "https://download.docker.com/linux/debian/gpg" \
        "/usr/share/keyrings/docker-archive-keyring.gpg"
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
else
    echo "Docker already installed, skipping"
fi
systemctl enable docker
systemctl start docker

# ==================== Python 3.13 via pyenv ====================
if [ ! -f /opt/pyenv/versions/3.13.0/bin/python3.13 ]; then
    echo "Installing Python 3.13 via pyenv..."

    apt-get install -y make build-essential libssl-dev zlib1g-dev \
      libbz2-dev libreadline-dev libsqlite3-dev wget curl llvm \
      libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev

    if [ ! -d /opt/pyenv ]; then
        export PYENV_ROOT=/opt/pyenv
        curl https://pyenv.run | bash
    fi

    export PATH="/opt/pyenv/bin:$PATH"
    eval "$(/opt/pyenv/bin/pyenv init -)"

    /opt/pyenv/bin/pyenv install 3.13.0
    /opt/pyenv/bin/pyenv global 3.13.0

    ln -sf /opt/pyenv/shims/python3.13 /usr/local/bin/python3.13
    ln -sf /opt/pyenv/shims/python3 /usr/local/bin/python3
    ln -sf /opt/pyenv/shims/pip3 /usr/local/bin/pip3
else
    echo "Python 3.13 already installed, skipping"
    export PATH="/opt/pyenv/bin:$PATH"
    eval "$(/opt/pyenv/bin/pyenv init -)"
fi

# Add to system profile for future sessions
cat > /etc/profile.d/pyenv.sh << 'PYENV_PROFILE'
export PYENV_ROOT=/opt/pyenv
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"
PYENV_PROFILE

# ==================== Node.js 20 ====================
if ! command -v node &>/dev/null || ! node --version | grep -q "^v20"; then
    echo "Installing Node.js 20..."
    # Use NodeSource with retry and fallback
    if ! curl -fsSL --retry 3 --retry-delay 5 https://deb.nodesource.com/setup_20.x | bash -; then
        echo "WARNING: NodeSource setup failed, trying apt directly..."
        apt-get install -y nodejs || true
    else
        apt-get install -y nodejs
    fi
else
    echo "Node.js 20 already installed, skipping"
fi

# ==================== Java 21 (for Firestore emulator) ====================
if ! java -version 2>&1 | grep -q "21\."; then
    echo "Installing Java 21 via Temurin..."
    install_gpg_key \
        "https://packages.adoptium.net/artifactory/api/gpg/key/public" \
        "/usr/share/keyrings/adoptium.gpg"
    echo "deb [signed-by=/usr/share/keyrings/adoptium.gpg] https://packages.adoptium.net/artifactory/deb $(lsb_release -cs) main" > /etc/apt/sources.list.d/adoptium.list
    apt-get update
    apt-get install -y temurin-21-jdk
else
    echo "Java 21 already installed, skipping"
fi

# ==================== FFmpeg ====================
if ! command -v ffmpeg &>/dev/null; then
    echo "Installing FFmpeg..."
    apt-get install -y ffmpeg
else
    echo "FFmpeg already installed, skipping"
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
    apt-get install -y google-cloud-cli google-cloud-cli-firestore-emulator
else
    echo "Google Cloud SDK already installed, skipping"
fi

# ==================== Create runner user ====================
echo "Ensuring runner user exists..."
useradd -m -s /bin/bash runner 2>/dev/null || true
usermod -aG docker runner

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
RUNNER_LABELS=${RUNNER_LABELS:-"self-hosted,linux,x64,gcp,large-disk"}

# Configure runner (non-interactive) - registered at organization level
echo "Configuring runner..."
cd $RUNNER_DIR
sudo -u runner ./config.sh \
    --url https://github.com/nomadkaraoke \
    --token "$REGISTRATION_TOKEN" \
    --name "gcp-runner-$(hostname)" \
    --labels "$RUNNER_LABELS" \
    --work "_work" \
    --unattended \
    --replace

# Install and start runner as a service
echo "Installing runner service..."
./svc.sh install runner
./svc.sh start

echo "GitHub Actions runner setup complete at $(date)"
echo "Runner registered at organization level (nomadkaraoke) with labels: $RUNNER_LABELS"

# ==================== Setup Python in tool cache ====================
# setup-python action looks for Python in RUNNER_TOOL_CACHE/_tool/Python
echo "Setting up Python 3.13 in tool cache for setup-python action..."
TOOL_CACHE="/home/runner/actions-runner/_work/_tool"
mkdir -p $TOOL_CACHE/Python/3.13.0/x64

# Copy pyenv Python to tool cache (only if not already there)
if [ ! -f "$TOOL_CACHE/Python/3.13.0/x64.complete" ]; then
    cp -r /opt/pyenv/versions/3.13.0/* $TOOL_CACHE/Python/3.13.0/x64/
    touch $TOOL_CACHE/Python/3.13.0/x64.complete
    echo "Python 3.13 added to tool cache"
else
    echo "Python 3.13 already in tool cache"
fi

# Fix permissions - must own entire _work directory, not just _tool
chown -R runner:runner /home/runner/actions-runner/_work

# ==================== Docker cleanup cron ====================
# Prevent disk from filling up with old Docker images
# Run hourly instead of daily, with threshold-based cleanup
echo "Setting up Docker cleanup cron..."
cat > /etc/cron.hourly/docker-cleanup << 'CRON'
#!/bin/bash
# Docker disk space cleanup for GitHub Actions runners
# Runs hourly, with aggressive cleanup when disk usage exceeds threshold

# Log to syslog
exec 1> >(logger -t docker-cleanup) 2>&1

THRESHOLD=70  # Cleanup when disk usage exceeds this percentage

# Get current disk usage percentage (remove % sign)
USAGE=$(df / | tail -1 | awk '{print $5}' | tr -d '%')

echo "Current disk usage: ${USAGE}%"

if [ "$USAGE" -gt "$THRESHOLD" ]; then
    echo "Disk usage ${USAGE}% exceeds threshold ${THRESHOLD}%, running cleanup..."

    # Remove ALL unused images (not just old ones) - this is the key fix
    # The previous 168h filter let recent images accumulate
    docker system prune -af

    # Also clean builder cache which can grow large
    docker builder prune -af

    # Report new usage
    NEW_USAGE=$(df / | tail -1 | awk '{print $5}' | tr -d '%')
    echo "Cleanup complete. Disk usage: ${NEW_USAGE}%"
else
    echo "Disk usage ${USAGE}% is below threshold ${THRESHOLD}%, skipping cleanup"
fi
CRON
chmod +x /etc/cron.hourly/docker-cleanup

# Remove old daily cleanup if it exists (migrating to hourly)
rm -f /etc/cron.daily/docker-cleanup

# ==================== Docker pre-warm cron ====================
# Keep base images fresh by pulling hourly
echo "Setting up Docker pre-warm cron..."
cat > /etc/cron.hourly/docker-prewarm << 'CRON'
#!/bin/bash
# Pre-warm Docker images used by CI/CD
# This ensures runners always have the latest base images cached

# Log to syslog
exec 1> >(logger -t docker-prewarm) 2>&1

echo "Starting Docker image pre-warm..."

# Configure Docker auth (uses instance service account)
gcloud auth configure-docker us-central1-docker.pkg.dev --quiet 2>/dev/null

# Pull latest images (|| true to not fail if network issues)
docker pull us-central1-docker.pkg.dev/nomadkaraoke/karaoke-repo/karaoke-backend-base:latest || true
docker pull us-central1-docker.pkg.dev/nomadkaraoke/karaoke-repo/karaoke-backend:latest || true

echo "Docker image pre-warm complete"
CRON
chmod +x /etc/cron.hourly/docker-prewarm

# ==================== Pre-warm Docker images ====================
# Pre-fetch commonly used Docker images to speed up first CI runs
echo "Pre-warming Docker images for faster CI builds..."

# Configure Docker to authenticate with Artifact Registry
gcloud auth configure-docker us-central1-docker.pkg.dev --quiet

# Pull base images used by deploy-backend job (largest layers, most benefit from caching)
echo "Pulling karaoke-backend-base image..."
docker pull us-central1-docker.pkg.dev/nomadkaraoke/karaoke-repo/karaoke-backend-base:latest || true

echo "Pulling karaoke-backend image..."
docker pull us-central1-docker.pkg.dev/nomadkaraoke/karaoke-repo/karaoke-backend:latest || true

# Pull GCS emulator image used by backend-emulator-tests job
echo "Pulling fake-gcs-server image..."
docker pull fsouza/fake-gcs-server:latest || true

echo "Docker images pre-warmed"

echo "Setup complete!"
