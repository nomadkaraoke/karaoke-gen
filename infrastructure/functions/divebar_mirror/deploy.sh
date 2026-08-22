#!/bin/bash
# Deploy the Divebar Mirror Cloud Function
#
# NOTE: As of 2026-06, the function source is managed by Pulumi
# (modules/divebar_mirror.py zips this directory via pulumi.FileArchive and pins
# the function to the object generation). The normal way to ship a code change is:
#
#     cd infrastructure && pulumi up
#
# This script remains for ad-hoc manual uploads/testing only; a manual upload
# will be reset to the repo contents on the next `pulumi up`.

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
