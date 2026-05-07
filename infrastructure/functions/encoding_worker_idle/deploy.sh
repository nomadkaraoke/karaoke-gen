#!/bin/bash
# Deploy the encoding-worker-idle-shutdown Cloud Function.
#
# Pulumi manages the function resource (env vars, IAM, scheduler) but does not
# package or upload the function source. This script does that.
#
# Run this:
#   - any time you change main.py / requirements.txt
#   - after pulumi up if Pulumi reports no diff but the function is on stale code
#
# Prerequisites:
#   - gcloud CLI authenticated as a principal with cloudfunctions.functions.update
#     and storage.objects.create on encoding-worker-idle-source-${PROJECT_ID}
#   - Pulumi infrastructure deployed first (creates the bucket, SA, and the
#     ENCODING_WORKER_FALLBACK_VMS secret env var binding)
#
# Usage:
#   ./deploy.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-nomadkaraoke}"
REGION="us-central1"
FUNCTION_NAME="encoding-worker-idle-shutdown"
BUCKET_NAME="encoding-worker-idle-source-${PROJECT_ID}"

echo "Deploying encoding-worker-idle-shutdown Cloud Function..."
echo "  Project:  ${PROJECT_ID}"
echo "  Region:   ${REGION}"
echo "  Function: ${FUNCTION_NAME}"
echo ""

cd "${SCRIPT_DIR}"

ZIP_PATH=/tmp/encoding-worker-idle-source.zip
echo "Creating source ZIP..."
rm -f "${ZIP_PATH}"
zip -r "${ZIP_PATH}" main.py requirements.txt

echo "Uploading to GCS..."
gcloud storage cp "${ZIP_PATH}" "gs://${BUCKET_NAME}/encoding-worker-idle-source.zip"

echo "Triggering function rebuild..."
gcloud functions deploy "${FUNCTION_NAME}" \
  --gen2 \
  --region="${REGION}" \
  --source="gs://${BUCKET_NAME}/encoding-worker-idle-source.zip" \
  --project="${PROJECT_ID}"

echo ""
echo "Function deployed."
echo ""
echo "Verify ENCODING_WORKER_FALLBACK_VMS env var is wired:"
echo "  gcloud functions describe ${FUNCTION_NAME} --gen2 --region=${REGION} --project=${PROJECT_ID} \\"
echo "    --format='value(serviceConfig.secretEnvironmentVariables)'"
echo ""
echo "Trigger a one-off run (skips the 5-min schedule):"
echo "  curl -X POST -H \"Authorization: Bearer \$(gcloud auth print-identity-token)\" \\"
echo "    https://${REGION}-${PROJECT_ID}.cloudfunctions.net/${FUNCTION_NAME}"
