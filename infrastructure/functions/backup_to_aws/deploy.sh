#!/bin/bash
# Deploy the backup-to-aws Cloud Function.
#
# Pulumi manages the function resource (env vars, IAM, scheduler) but does not
# package or upload the function source. This script does that.
#
# Run this:
#   - any time you change main.py / *_export.py / s3_upload.py / gcs_sync.py /
#     discord_alert.py / requirements.txt
#   - after pulumi up if Pulumi reports no diff but the function is on stale code
#
# Prerequisites:
#   - gcloud CLI authenticated as a principal with cloudfunctions.functions.update
#     and storage.objects.create on backup-to-aws-source-${PROJECT_ID}
#   - Pulumi infrastructure deployed first (creates the bucket and SA)
#
# Usage:
#   ./deploy.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-nomadkaraoke}"
REGION="us-central1"
FUNCTION_NAME="backup-to-aws"
BUCKET_NAME="backup-to-aws-source-${PROJECT_ID}"

echo "Deploying backup-to-aws Cloud Function..."
echo "  Project:  ${PROJECT_ID}"
echo "  Region:   ${REGION}"
echo "  Function: ${FUNCTION_NAME}"
echo ""

cd "${SCRIPT_DIR}"

ZIP_PATH=/tmp/backup-to-aws-source.zip
echo "Creating source ZIP..."
rm -f "${ZIP_PATH}"
zip -r "${ZIP_PATH}" \
  main.py \
  requirements.txt \
  firestore_export.py \
  bigquery_export.py \
  gcs_sync.py \
  secrets_export.py \
  s3_upload.py \
  discord_alert.py

echo "Uploading to GCS..."
gcloud storage cp "${ZIP_PATH}" "gs://${BUCKET_NAME}/backup-to-aws-source.zip"

echo "Triggering function rebuild..."
gcloud functions deploy "${FUNCTION_NAME}" \
  --gen2 \
  --region="${REGION}" \
  --source="gs://${BUCKET_NAME}/backup-to-aws-source.zip" \
  --project="${PROJECT_ID}"

echo ""
echo "Function deployed."
echo ""
echo "Verify env vars (should include BACKUP_ENCRYPTION_PUBKEY):"
echo "  gcloud functions describe ${FUNCTION_NAME} --gen2 --region=${REGION} --project=${PROJECT_ID} --format='value(serviceConfig.environmentVariables)'"
echo ""
echo "Trigger a one-off run (skips the daily schedule):"
echo "  curl -X POST -H \"Authorization: Bearer \$(gcloud auth print-identity-token)\" \\"
echo "    https://${REGION}-${PROJECT_ID}.cloudfunctions.net/${FUNCTION_NAME}"
