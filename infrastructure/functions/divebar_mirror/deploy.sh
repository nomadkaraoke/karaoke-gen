#!/bin/bash
# Deploy the Divebar Mirror Cloud Function
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
FUNCTION_NAME="divebar-mirror"
BUCKET_NAME="divebar-mirror-source-${PROJECT_ID}"

echo "Deploying Divebar Mirror Cloud Function..."
echo "  Project: ${PROJECT_ID}"
echo "  Region: ${REGION}"
echo "  Function: ${FUNCTION_NAME}"
echo ""

# Change to function directory
cd "${SCRIPT_DIR}"

# Create ZIP of function source
echo "Creating source ZIP..."
rm -f /tmp/divebar-mirror-source.zip
zip -r /tmp/divebar-mirror-source.zip \
    main.py \
    drive_client.py \
    filename_parser.py \
    index_builder.py \
    requirements.txt

# Upload to GCS
echo "Uploading to GCS..."
gsutil cp /tmp/divebar-mirror-source.zip "gs://${BUCKET_NAME}/divebar-mirror-source.zip"

echo ""
echo "Source uploaded. The Cloud Function will be updated on next Pulumi deploy."
echo ""
echo "To test locally:"
echo "  python main.py"
echo ""
echo "To trigger the deployed function:"
echo "  gcloud functions call ${FUNCTION_NAME} --region=${REGION} --project=${PROJECT_ID} --data='{}'"
