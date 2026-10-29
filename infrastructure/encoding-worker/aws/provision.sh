#!/bin/bash
# One-time provisioning of the AWS encoding worker (EC2 user-data, first boot).
#
# AWS counterpart of infrastructure/packer/scripts/provision.sh: same static
# ffmpeg, fonts, Python 3.13 venv with the full karaoke-gen dependency tree
# (CPU-only torch), and the same systemd unit layout under /opt/encoding-worker
# (self_heal.running_on_worker_vm keys off /opt/encoding-worker/bootstrap.sh).
# The root EBS volume persists across stop/start, so this runs once.
#
# GCP auth: Workload Identity Federation (no keys). The credential config is a
# non-secret JSON pointing at the AWS IMDSv2 role creds; gcloud and the client
# libraries both use it to impersonate encoding-worker@nomadkaraoke.
set -euxo pipefail
exec > >(tee -a /var/log/encoding-worker-provision.log) 2>&1

WORKER_DIR=/opt/encoding-worker
BUCKET=gs://karaoke-gen-storage-nomadkaraoke
WIF_PROVIDER="${WIF_PROVIDER:?}"            # projects/<num>/locations/global/workloadIdentityPools/<pool>/providers/<provider>
WORKER_SA="${WORKER_SA:-encoding-worker@nomadkaraoke.iam.gserviceaccount.com}"
VM_NAME="${VM_NAME:-aws-encoding-worker-1}"

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q curl xz-utils unzip git build-essential libffi-dev libssl-dev \
    fonts-noto fonts-noto-cjk fontconfig apt-transport-https ca-certificates gnupg

# Google Cloud CLI (gcloud storage / secrets over WIF)
curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
    > /etc/apt/sources.list.d/google-cloud-sdk.list
apt-get update -q && apt-get install -y -q google-cloud-cli

# Static FFmpeg 7.x (same source as the GCE image)
curl -fsSL https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz -o /tmp/ff.tar.xz
tar -xf /tmp/ff.tar.xz -C /tmp && cp /tmp/ffmpeg-*-amd64-static/ff{mpeg,probe} /usr/local/bin/ && rm -rf /tmp/ff*

mkdir -p "${WORKER_DIR}"
gcloud iam workload-identity-pools create-cred-config "${WIF_PROVIDER}" \
    --service-account="${WORKER_SA}" --aws --enable-imdsv2 \
    --output-file="${WORKER_DIR}/gcp-wif.json"
gcloud auth login --cred-file="${WORKER_DIR}/gcp-wif.json" --quiet
gcloud config set project nomadkaraoke --quiet
export GOOGLE_APPLICATION_CREDENTIALS="${WORKER_DIR}/gcp-wif.json"

# Python 3.13 venv (uv-managed interpreter; --seed so pip exists for startup/ensure_latest_wheel)
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
UV_PYTHON_INSTALL_DIR=/opt/python uv venv --seed --python 3.13 "${WORKER_DIR}/venv"
VERSION=$(gcloud storage cat "${BUCKET}/encoding-worker/version.txt" | tr -d '[:space:]')
WHEEL="/tmp/karaoke_gen-${VERSION}-py3-none-any.whl"
gcloud storage cp "${BUCKET}/wheels/karaoke_gen-current.whl" "${WHEEL}"
"${WORKER_DIR}/venv/bin/pip" install --quiet fastapi uvicorn google-cloud-storage aiofiles aiohttp packaging
"${WORKER_DIR}/venv/bin/pip" install --quiet \
    --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple "${WHEEL}"
rm -f "${WHEEL}"

# Bootstrap (ExecStartPre): fetch the latest startup-aws.sh from GCS, else the baked copy.
cat > "${WORKER_DIR}/bootstrap.sh" <<'BOOT'
#!/bin/bash
set -e
if gcloud storage cp gs://karaoke-gen-storage-nomadkaraoke/encoding-worker/startup-aws.sh /opt/encoding-worker/startup-aws.sh.new; then
    mv /opt/encoding-worker/startup-aws.sh.new /opt/encoding-worker/startup-aws.sh
fi
exec bash /opt/encoding-worker/startup-aws.sh
BOOT
chmod +x "${WORKER_DIR}/bootstrap.sh"
gcloud storage cp "${BUCKET}/encoding-worker/startup-aws.sh" "${WORKER_DIR}/startup-aws.sh"
gcloud storage cp "${BUCKET}/encoding-worker/idle-watchdog.py" "${WORKER_DIR}/idle-watchdog.py"
echo "${VM_NAME}" > "${WORKER_DIR}/vm-name"

cat > /etc/systemd/system/encoding-worker.service <<UNIT
[Unit]
Description=Karaoke encoding worker (AWS)
After=network-online.target
Wants=network-online.target

[Service]
User=root
WorkingDirectory=${WORKER_DIR}
ExecStartPre=${WORKER_DIR}/bootstrap.sh
ExecStart=${WORKER_DIR}/venv/bin/uvicorn backend.services.gce_encoding.main:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=5
TimeoutStartSec=600
Environment="GOOGLE_CLOUD_PROJECT=nomadkaraoke"
EnvironmentFile=${WORKER_DIR}/env

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/encoding-worker-idle.service <<UNIT
[Unit]
Description=Stop this instance when the encoding worker is idle

[Service]
Type=oneshot
Environment="GOOGLE_APPLICATION_CREDENTIALS=${WORKER_DIR}/gcp-wif.json"
Environment="GOOGLE_CLOUD_PROJECT=nomadkaraoke"
ExecStart=${WORKER_DIR}/venv/bin/python ${WORKER_DIR}/idle-watchdog.py
UNIT

cat > /etc/systemd/system/encoding-worker-idle.timer <<UNIT
[Unit]
Description=Encoding worker idle check (every 2 min)

[Timer]
OnBootSec=10min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
UNIT

touch "${WORKER_DIR}/env"
systemctl daemon-reload
systemctl enable --now encoding-worker.service encoding-worker-idle.timer
echo "PROVISIONED $(date -u +%FT%TZ)"
