#!/bin/bash
# Deploy the Divebar Lookup API Cloud Function
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-nomadkaraoke}"
REGION="us-central1"
FUNCTION_NAME="divebar-lookup"
BUCKET_NAME="divebar-lookup-source-${PROJECT_ID}"

echo "Deploying Divebar Lookup Cloud Function..."
echo "  Project: ${PROJECT_ID}"
echo "  Region: ${REGION}"
echo "  Function: ${FUNCTION_NAME}"
echo ""

cd "${SCRIPT_DIR}"

echo "Creating source ZIP..."
rm -f /tmp/divebar-lookup-source.zip
zip -r /tmp/divebar-lookup-source.zip main.py requirements.txt

echo "Uploading to GCS..."
gsutil cp /tmp/divebar-lookup-source.zip "gs://${BUCKET_NAME}/divebar-lookup-source.zip"

echo ""
echo "Source uploaded. The Cloud Function will be updated on next Pulumi deploy."
