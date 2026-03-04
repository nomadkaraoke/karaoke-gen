#!/bin/bash
set -e

# Log to a file for debugging
exec > >(tee /var/log/github-runner-startup.log) 2>&1
echo "Starting GitHub Actions runner setup at $(date)"

# System updates and core dependencies
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
echo "Installing Docker..."
curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --batch --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Configure Docker for the runner user
systemctl enable docker
systemctl start docker

# ==================== Python 3.13 via pyenv ====================
# setup-python action on self-hosted runners needs Python in the tool cache
echo "Installing Python 3.13 via pyenv..."

# Install pyenv dependencies
apt-get install -y make build-essential libssl-dev zlib1g-dev \
  libbz2-dev libreadline-dev libsqlite3-dev wget curl llvm \
  libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev

# Install pyenv system-wide
export PYENV_ROOT=/opt/pyenv
curl https://pyenv.run | bash

# Add to system profile for future sessions
cat > /etc/profile.d/pyenv.sh << 'PYENV_PROFILE'
export PYENV_ROOT=/opt/pyenv
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"
PYENV_PROFILE

# Source pyenv for current session
export PATH="/opt/pyenv/bin:$PATH"
eval "$(/opt/pyenv/bin/pyenv init -)"

# Install Python 3.13
/opt/pyenv/bin/pyenv install 3.13.0
/opt/pyenv/bin/pyenv global 3.13.0

# Create symlinks for system-wide access
ln -sf /opt/pyenv/shims/python3.13 /usr/local/bin/python3.13
ln -sf /opt/pyenv/shims/python3 /usr/local/bin/python3
ln -sf /opt/pyenv/shims/pip3 /usr/local/bin/pip3

# ==================== Node.js 20 ====================
echo "Installing Node.js 20..."
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y nodejs

# ==================== Java 21 (for Firestore emulator) ====================
echo "Installing Java 21 via Temurin..."
# Use Temurin (Adoptium) - more reliable than Debian repos
curl -fsSL https://packages.adoptium.net/artifactory/api/gpg/key/public | gpg --batch --dearmor -o /usr/share/keyrings/adoptium.gpg
echo "deb [signed-by=/usr/share/keyrings/adoptium.gpg] https://packages.adoptium.net/artifactory/deb $(lsb_release -cs) main" > /etc/apt/sources.list.d/adoptium.list
apt-get update
apt-get install -y temurin-21-jdk

# ==================== FFmpeg ====================
echo "Installing FFmpeg..."
apt-get install -y ffmpeg

# ==================== Poetry ====================
echo "Installing Poetry..."
curl -sSL https://install.python-poetry.org | python3 -
ln -sf /root/.local/bin/poetry /usr/local/bin/poetry

# ==================== Google Cloud SDK ====================
echo "Installing Google Cloud SDK..."
curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --batch --dearmor -o /usr/share/keyrings/cloud.google.gpg
echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" > /etc/apt/sources.list.d/google-cloud-sdk.list
apt-get update
apt-get install -y google-cloud-cli google-cloud-cli-firestore-emulator

# ==================== Create runner user ====================
echo "Creating runner user..."
useradd -m -s /bin/bash runner || true
usermod -aG docker runner

# ==================== GitHub Actions Runner ====================
echo "Setting up GitHub Actions Runner..."
RUNNER_VERSION="2.321.0"
RUNNER_DIR="/home/runner/actions-runner"

mkdir -p $RUNNER_DIR
cd $RUNNER_DIR

# Download runner
curl -o actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz -L \
    https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz
tar xzf actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz
rm actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz

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

# Copy pyenv Python to tool cache
cp -r /opt/pyenv/versions/3.13.0/* $TOOL_CACHE/Python/3.13.0/x64/

# Create marker file that setup-python looks for
touch $TOOL_CACHE/Python/3.13.0/x64.complete

# Fix permissions - must own entire _work directory, not just _tool
chown -R runner:runner /home/runner/actions-runner/_work

echo "Python 3.13 added to tool cache"

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
