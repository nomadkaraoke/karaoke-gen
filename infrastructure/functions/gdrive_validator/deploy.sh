#!/bin/bash
# Deploy the GDrive Validator Cloud Function
#
# This script:
# 1. Creates a ZIP of the function source
# 2. Uploads it to GCS
# 3. Deploys the function via gcloud
#
# Prerequisites:
# - gcloud CLI authenticated
# - Pulumi infrastructure already deployed (creates the bucket)
#
# Usage:
#   ./deploy.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-nomadkaraoke}"
REGION="us-central1"
FUNCTION_NAME="gdrive-validator"
BUCKET_NAME="gdrive-validator-source-${PROJECT_ID}"

echo "Deploying GDrive Validator Cloud Function..."
echo "  Project: ${PROJECT_ID}"
echo "  Region: ${REGION}"
echo "  Function: ${FUNCTION_NAME}"
echo ""

# Change to function directory
cd "${SCRIPT_DIR}"

# Create ZIP of function source
echo "Creating source ZIP..."
rm -f /tmp/gdrive-validator-source.zip
zip -r /tmp/gdrive-validator-source.zip main.py requirements.txt

# Upload to GCS
echo "Uploading to GCS..."
gsutil cp /tmp/gdrive-validator-source.zip "gs://${BUCKET_NAME}/gdrive-validator-source.zip"

# Deploy the function
echo "Deploying function..."
gcloud functions deploy "${FUNCTION_NAME}" \
  --gen2 \
  --region="${REGION}" \
  --runtime=python312 \
  --source="gs://${BUCKET_NAME}/gdrive-validator-source.zip" \
  --entry-point=validate_gdrive \
  --trigger-http \
  --memory=256MB \
  --timeout=300s \
  --min-instances=0 \
  --max-instances=1 \
  --service-account="gdrive-validator@${PROJECT_ID}.iam.gserviceaccount.com" \
  --set-env-vars="GDRIVE_FOLDER_ID=1laRKAyxo0v817SstfM5XkpbWiNKNAMSX,EMAIL_TO=gen@nomadkaraoke.com,EMAIL_FROM=gen@nomadkaraoke.com,NOTIFY_ON_SUCCESS=true" \
  --set-secrets="PUSHBULLET_API_KEY=pushbullet-api-key:latest,POSTMARK_SERVER_TOKEN=postmark-server-token:latest" \
  --project="${PROJECT_ID}"

echo ""
echo "Function deployed successfully!"
echo ""
echo "To test the function manually:"
echo "  gcloud functions call ${FUNCTION_NAME} --region=${REGION} --project=${PROJECT_ID}"
echo ""
echo "Function URL:"
gcloud functions describe "${FUNCTION_NAME}" --region="${REGION}" --project="${PROJECT_ID}" --format='value(serviceConfig.uri)'

