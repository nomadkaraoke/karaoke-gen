#!/bin/bash
# AWS encoding worker startup (run by bootstrap.sh on every service start).
#
# AWS counterpart of ../startup.sh. Differences: no GCE metadata server (GCP
# auth is Workload Identity Federation via gcp-wif.json + the instance's IMDSv2
# role), `gcloud storage` instead of gsutil (gsutil can't use WIF credential
# files), and the VM name comes from /opt/encoding-worker/vm-name.
#
# CI uploads this to gs://karaoke-gen-storage-nomadkaraoke/encoding-worker/startup-aws.sh
set -e

BUCKET="gs://karaoke-gen-storage-nomadkaraoke"
WORKER_DIR="/opt/encoding-worker"
VENV="${WORKER_DIR}/venv"
TMP_DIR="/tmp/encoding-worker-startup"
export GOOGLE_APPLICATION_CREDENTIALS="${WORKER_DIR}/gcp-wif.json"
exec > >(tee -a /var/log/encoding-worker-startup.log) 2>&1

echo "=== AWS encoding worker startup: $(date) ==="
rm -rf "${TMP_DIR}" && mkdir -p "${TMP_DIR}"

echo "[1/5] Fetching API key from Secret Manager (WIF)..."
ENCODING_API_KEY=""
for attempt in $(seq 1 10); do
    ENCODING_API_KEY=$(gcloud secrets versions access latest --secret=encoding-worker-api-key --project=nomadkaraoke 2>/dev/null || echo "")
    [ -n "$ENCODING_API_KEY" ] && break
    echo "API key fetch failed (attempt ${attempt}/10), retrying in 5s..."
    sleep 5
done
if [ -z "$ENCODING_API_KEY" ]; then
    echo "FATAL: No API key from Secret Manager; exiting so systemd retries"
    exit 1
fi
VM_NAME=$(cat "${WORKER_DIR}/vm-name" 2>/dev/null || hostname)

cat > "${WORKER_DIR}/env" <<ENV
ENCODING_API_KEY=${ENCODING_API_KEY}
ENCODING_WORKER_VM_NAME=${VM_NAME}
ENCODING_WORKER_PROVIDER=aws
GOOGLE_APPLICATION_CREDENTIALS=${WORKER_DIR}/gcp-wif.json
LOCAL_ENCODED_OUTPUTS_DIR=/var/lib/encoding-worker/outputs
ENV
chmod 600 "${WORKER_DIR}/env"
mkdir -p /var/lib/encoding-worker/outputs

# Keep the idle watchdog + video-worker env helper current (best effort).
gcloud storage cp "${BUCKET}/encoding-worker/idle-watchdog.py" "${WORKER_DIR}/idle-watchdog.py" || true
gcloud storage cp "${BUCKET}/encoding-worker/video-worker-env.py" "${WORKER_DIR}/video-worker-env.py" || true

# Video worker on this box (/run-worker) runs as karaoke-backend@, the Cloud Run
# job's identity, via the same WIF provider. Its env mirrors the live job.
if [ ! -s "${WORKER_DIR}/gcp-wif-backend.json" ]; then
    WIF_PROVIDER=$(python3 -c "import json;print(json.load(open('${WORKER_DIR}/gcp-wif.json'))['audience'].split('//iam.googleapis.com/')[1])" 2>/dev/null) || WIF_PROVIDER=""
    if [ -n "${WIF_PROVIDER}" ]; then
        gcloud iam workload-identity-pools create-cred-config "${WIF_PROVIDER}" \
            --service-account=karaoke-backend@nomadkaraoke.iam.gserviceaccount.com --aws --enable-imdsv2 \
            --output-file="${WORKER_DIR}/gcp-wif-backend.json" || true
    fi
fi
if [ -f "${WORKER_DIR}/video-worker-env.py" ]; then
    "${VENV}/bin/python" "${WORKER_DIR}/video-worker-env.py" || true
fi

echo "[2/5] Downloading version manifest..."
EXPECTED_VERSION=$(gcloud storage cat "${BUCKET}/encoding-worker/version.txt" 2>/dev/null | tr -d '[:space:]' || echo "")
echo "Expected version: ${EXPECTED_VERSION:-unknown}"

echo "[3/5] Downloading current wheel..."
WHEEL_PATH="${TMP_DIR}/karaoke_gen-${EXPECTED_VERSION:-0.0.0}-py3-none-any.whl"
gcloud storage cp "${BUCKET}/wheels/karaoke_gen-current.whl" "${WHEEL_PATH}"

echo "[4/5] Installing wheel (code only; deps are baked by provision.sh)..."
"${VENV}/bin/pip" install --upgrade --quiet --force-reinstall --no-deps "${WHEEL_PATH}"

echo "[5/5] Verifying installed version..."
INSTALLED_VERSION=$("${VENV}/bin/pip" show karaoke-gen 2>/dev/null | grep "^Version:" | cut -d' ' -f2 | tr -d '[:space:]')
if [ -n "$EXPECTED_VERSION" ] && [ "${INSTALLED_VERSION}" != "${EXPECTED_VERSION}" ]; then
    echo "FATAL: version mismatch (expected ${EXPECTED_VERSION}, installed ${INSTALLED_VERSION})"
    exit 1
fi
rm -rf "${TMP_DIR}"
echo "=== Startup complete: karaoke-gen v${INSTALLED_VERSION} on ${VM_NAME} ==="
