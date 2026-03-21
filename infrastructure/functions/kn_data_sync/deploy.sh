#!/bin/bash
# Deploy the KN Data Sync Cloud Function
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-nomadkaraoke}"
REGION="us-central1"
FUNCTION_NAME="kn-data-sync"
BUCKET_NAME="kn-data-sync-source-${PROJECT_ID}"

echo "Deploying KN Data Sync Cloud Function..."
echo "  Project: ${PROJECT_ID}"
echo "  Region: ${REGION}"
echo "  Function: ${FUNCTION_NAME}"
echo ""

cd "${SCRIPT_DIR}"

echo "Creating source ZIP..."
rm -f /tmp/kn-data-sync-source.zip
zip -r /tmp/kn-data-sync-source.zip main.py requirements.txt

echo "Uploading to GCS..."
gsutil cp /tmp/kn-data-sync-source.zip "gs://${BUCKET_NAME}/kn-data-sync-source.zip"

echo ""
echo "Source uploaded. The Cloud Function will be updated on next Pulumi deploy."
