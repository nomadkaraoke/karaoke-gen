#!/usr/bin/env bash
#
# rollback.sh — fast, codified rollback for the karaoke-gen production backend.
#
# WHY THIS EXISTS (incident hardening item R2):
#   The deploy pipeline auto-deploys on merge to main and has NO automatic
#   rollback. During an outage we should not be composing `gcloud` from memory.
#   This script is the single, documented, parameterized rollback path for:
#     - the `karaoke-backend` Cloud Run SERVICE, and
#     - the three Cloud Run JOBS that share the backend image:
#         * video-encoding-job       (CPU, us-central1)
#         * lyrics-transcription-job (CPU, us-central1)
#         * audio-separation-job     (GPU, us-east4 — uses the GPU image)
#
# TWO DIFFERENT MECHANISMS (intentional — see docs/TROUBLESHOOTING.md "Fast rollback"):
#   * SERVICE  → shift 100% traffic to a previous, already-built revision
#                (`gcloud run services update-traffic --to-revisions=<rev>=100`).
#                This is instant and needs no rebuild — the safest fast path.
#   * JOBS     → Cloud Run Jobs cannot traffic-split, so we re-pin them to a
#                previous image tag (`gcloud run jobs update --image ...:<tag>`).
#                The next job invocation then runs the pinned (old) image.
#
# USAGE:
#   scripts/rollback.sh --list
#       Show recent service revisions + recent backend image tags so you can
#       pick a known-good target. Run this first during an incident.
#
#   scripts/rollback.sh --to-revision <REVISION> [--service-only] [--dry-run]
#       Roll the SERVICE back to an existing revision (fast, no rebuild).
#
#   scripts/rollback.sh --image-version <vX.Y.Z|SHA> [--jobs-only] [--dry-run]
#       Re-pin the three JOBS to a previous image tag.
#
#   scripts/rollback.sh --to-revision <REV> --image-version <vX.Y.Z> [--dry-run]
#       Roll the service AND re-pin the jobs in one shot (typical full rollback).
#
# FLAGS:
#   --to-revision <REV>      Target service revision (e.g. karaoke-backend-00123-abc).
#   --image-version <TAG>    Target image tag for the jobs (e.g. v0.222.2 or a git SHA).
#   --service-only           Only roll the service (ignore jobs).
#   --jobs-only              Only re-pin the jobs (ignore the service).
#   --dry-run                Print the gcloud commands WITHOUT executing them.
#   --list                   List recent revisions + image tags, then exit.
#   -h | --help              Show this help.
#
# SAFETY:
#   * Nothing runs until you pass an explicit target (--to-revision / --image-version).
#   * --dry-run prints every gcloud command verbatim so you can eyeball it first.
#   * The script echoes exactly what it will do and (when interactive) asks for
#     confirmation before mutating anything.
#
set -euo pipefail

# --------------------------------------------------------------------------
# Constants — mirror .github/workflows/ci.yml deploy-backend (see ~ci.yml:1809,
# :1911, :1939). Keep these in sync if the CI registry/region/names change.
# --------------------------------------------------------------------------
PROJECT_ID="nomadkaraoke"
SERVICE_NAME="karaoke-backend"
SERVICE_REGION="us-central1"

# CPU (service + CPU jobs) image repository.
CPU_IMAGE_REPO="us-central1-docker.pkg.dev/${PROJECT_ID}/karaoke-repo/karaoke-backend"
# GPU (audio separation) image repository, co-located with the L4 GPU in us-east4.
GPU_IMAGE_REPO="us-east4-docker.pkg.dev/${PROJECT_ID}/karaoke-backend-gpu/karaoke-backend"

# job_name:region:repo — audio-separation-job uses the GPU repo/region.
JOBS=(
  "video-encoding-job:us-central1:${CPU_IMAGE_REPO}"
  "lyrics-transcription-job:us-central1:${CPU_IMAGE_REPO}"
  "audio-separation-job:us-east4:${GPU_IMAGE_REPO}"
)

# --------------------------------------------------------------------------
# Arg parsing
# --------------------------------------------------------------------------
TO_REVISION=""
IMAGE_VERSION=""
SERVICE_ONLY=false
JOBS_ONLY=false
DRY_RUN=false
DO_LIST=false

usage() {
  # Print the leading comment block (everything between the shebang and `set -e`)
  # as help text so this stays the single source of truth.
  sed -n '3,60p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --to-revision)    TO_REVISION="${2:?--to-revision requires a revision name}"; shift 2 ;;
    --image-version)  IMAGE_VERSION="${2:?--image-version requires a tag}"; shift 2 ;;
    --service-only)   SERVICE_ONLY=true; shift ;;
    --jobs-only)      JOBS_ONLY=true; shift ;;
    --dry-run)        DRY_RUN=true; shift ;;
    --list)           DO_LIST=true; shift ;;
    -h|--help)        usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; echo "Run '$0 --help' for usage." >&2; exit 2 ;;
  esac
done

# run_cmd: echo the command, then run it unless --dry-run is set.
run_cmd() {
  echo "+ $*"
  if [[ "$DRY_RUN" == "true" ]]; then
    echo "  (dry-run — not executed)"
    return 0
  fi
  "$@"
}

confirm() {
  # Skip confirmation for dry-runs and for non-interactive shells (e.g. CI).
  if [[ "$DRY_RUN" == "true" ]] || [[ ! -t 0 ]]; then
    return 0
  fi
  read -r -p "Proceed with the above rollback? [y/N] " reply
  case "$reply" in
    y|Y|yes|YES) return 0 ;;
    *) echo "Aborted."; exit 1 ;;
  esac
}

# --------------------------------------------------------------------------
# --list: show the operator what targets are available.
# --------------------------------------------------------------------------
if [[ "$DO_LIST" == "true" ]]; then
  echo "=== Recent '${SERVICE_NAME}' service revisions (newest first) ==="
  gcloud run revisions list \
    --service="${SERVICE_NAME}" \
    --region="${SERVICE_REGION}" \
    --project="${PROJECT_ID}" \
    --format='table(metadata.name, status.conditions[0].lastTransitionTime.date("%Y-%m-%d %H:%M"), spec.containers[0].image.basename())' \
    --limit=15 || echo "(could not list revisions — check gcloud auth/project)"

  echo
  echo "=== Current traffic split ==="
  gcloud run services describe "${SERVICE_NAME}" \
    --region="${SERVICE_REGION}" \
    --project="${PROJECT_ID}" \
    --format='table(status.traffic[].revisionName, status.traffic[].percent, status.traffic[].tag)' \
    || echo "(could not describe service)"

  echo
  echo "=== Recent CPU backend image tags (for --image-version) ==="
  gcloud artifacts docker tags list "${CPU_IMAGE_REPO}" \
    --project="${PROJECT_ID}" \
    --format='value(tag)' --limit=20 2>/dev/null \
    | grep -E '^v[0-9]' | sort -V | tail -15 || echo "(could not list image tags)"
  exit 0
fi

# --------------------------------------------------------------------------
# Validate inputs.
# --------------------------------------------------------------------------
if [[ -z "$TO_REVISION" && -z "$IMAGE_VERSION" ]]; then
  echo "ERROR: you must pass an explicit target." >&2
  echo "  - Roll the SERVICE:  --to-revision <REVISION>" >&2
  echo "  - Re-pin the JOBS:   --image-version <vX.Y.Z|SHA>" >&2
  echo "  - Not sure what's available?  $0 --list" >&2
  echo "Run '$0 --help' for full usage." >&2
  exit 2
fi

if [[ "$SERVICE_ONLY" == "true" && "$JOBS_ONLY" == "true" ]]; then
  echo "ERROR: --service-only and --jobs-only are mutually exclusive." >&2
  exit 2
fi

# Decide what we're doing.
ROLL_SERVICE=false
ROLL_JOBS=false
[[ "$JOBS_ONLY" != "true" && -n "$TO_REVISION" ]] && ROLL_SERVICE=true
[[ "$SERVICE_ONLY" != "true" && -n "$IMAGE_VERSION" ]] && ROLL_JOBS=true

if [[ "$ROLL_SERVICE" != "true" && "$ROLL_JOBS" != "true" ]]; then
  echo "ERROR: the given flags don't select anything to roll back." >&2
  echo "  (e.g. --service-only needs --to-revision; --jobs-only needs --image-version)" >&2
  exit 2
fi

# --------------------------------------------------------------------------
# Show the plan before doing anything.
# --------------------------------------------------------------------------
echo "=============================================================="
echo " karaoke-gen production ROLLBACK"
echo "   project:  ${PROJECT_ID}"
echo "   dry-run:  ${DRY_RUN}"
if [[ "$ROLL_SERVICE" == "true" ]]; then
  echo "   SERVICE:  ${SERVICE_NAME} (${SERVICE_REGION})"
  echo "             → shift 100% traffic to revision '${TO_REVISION}'"
fi
if [[ "$ROLL_JOBS" == "true" ]]; then
  echo "   JOBS:     re-pin to image tag '${IMAGE_VERSION}'"
  for entry in "${JOBS[@]}"; do
    IFS=':' read -r job_name job_region job_repo <<< "$entry"
    echo "             → ${job_name} (${job_region}) = ${job_repo}:${IMAGE_VERSION}"
  done
fi
echo "=============================================================="
confirm

# --------------------------------------------------------------------------
# Roll the SERVICE (traffic shift — instant, no rebuild).
# --------------------------------------------------------------------------
if [[ "$ROLL_SERVICE" == "true" ]]; then
  echo
  echo ">>> Rolling service '${SERVICE_NAME}' traffic to '${TO_REVISION}'..."
  run_cmd gcloud run services update-traffic "${SERVICE_NAME}" \
    --region="${SERVICE_REGION}" \
    --project="${PROJECT_ID}" \
    --to-revisions="${TO_REVISION}=100"
fi

# --------------------------------------------------------------------------
# Re-pin the JOBS (image update — next invocation runs the pinned image).
# --------------------------------------------------------------------------
if [[ "$ROLL_JOBS" == "true" ]]; then
  echo
  echo ">>> Re-pinning Cloud Run Jobs to '${IMAGE_VERSION}'..."
  for entry in "${JOBS[@]}"; do
    IFS=':' read -r job_name job_region job_repo <<< "$entry"
    echo "--- ${job_name} (${job_region}) ---"
    run_cmd gcloud run jobs update "${job_name}" \
      --image="${job_repo}:${IMAGE_VERSION}" \
      --region="${job_region}" \
      --project="${PROJECT_ID}" \
      --quiet
  done
fi

echo
if [[ "$DRY_RUN" == "true" ]]; then
  echo "✅ Dry-run complete — no changes were made."
else
  echo "✅ Rollback complete."
  echo "   Verify the service: curl -s https://api.nomadkaraoke.com/api/health/detailed | jq .version"
  echo "   Jobs pick up the pinned image on their NEXT invocation (in-flight runs are unaffected)."
fi
