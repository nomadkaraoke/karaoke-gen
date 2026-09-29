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
gcloud storage cp /tmp/kn-data-sync-source.zip "gs://${BUCKET_NAME}/kn-data-sync-source.zip"

# Pulumi only names the bucket/object (no generation pin), so a new upload is not
# a diff to it and `pulumi up` never rebuilds the function: deploy it directly.
# Env vars, secrets, SA and limits stay as Pulumi set them.
echo "Deploying function..."
gcloud functions deploy "${FUNCTION_NAME}" --gen2 --region="${REGION}" --project="${PROJECT_ID}" \
  --source="gs://${BUCKET_NAME}/kn-data-sync-source.zip" \
  --runtime=python312 --entry-point=sync_kn_data --quiet

echo ""
echo "Deployed. Test run: gcloud scheduler jobs run kn-data-sync-full-daily --location ${REGION} --project ${PROJECT_ID}"
