#!/usr/bin/env bash
# Deploy the KaraokeHunt interceptor Worker to Cloudflare (idempotent).
#
# NOT Pulumi-managed: the karaokehunt.com zone lives outside IaC, so per the
# workspace rule this script IS the record of what was changed, alongside
# docs/ARCHITECTURE.md § KaraokeHunt interceptor.
#
# Requires:
#   - CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID (workspace .envrc)
#   - gcloud auth able to read Secret Manager `karaokehunt-forwarder-secret`
#
# What it does:
#   1. Uploads worker script `karaokehunt-interceptor` (module syntax) with the
#      FORWARDER_SECRET binding read from Secret Manager.
#   2. Ensures the route create.karaokehunt.com/* -> karaokehunt-interceptor.
#   3. Ensures the create.karaokehunt.com DNS record is proxied (orange cloud)
#      so the route applies. The record's origin IP is never contacted while
#      the Worker route exists.
set -euo pipefail

ZONE_ID="02440ab623269c428be9e65a16bee280" # karaokehunt.com
SCRIPT_NAME="karaokehunt-interceptor"
ROUTE_PATTERN="create.karaokehunt.com/*"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

: "${CLOUDFLARE_API_TOKEN:?source the workspace .envrc first}"
: "${CLOUDFLARE_ACCOUNT_ID:?source the workspace .envrc first}"

SECRET_VALUE="$(gcloud secrets versions access latest \
  --secret=karaokehunt-forwarder-secret --project=nomadkaraoke)"

api() {
  local method="$1" path="$2"; shift 2
  curl -sS -X "$method" "https://api.cloudflare.com/client/v4${path}" \
    -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" "$@"
}

echo "1/3 Uploading worker script ${SCRIPT_NAME}..."
METADATA=$(python3 - "$SECRET_VALUE" <<'EOF'
import json, sys
print(json.dumps({
    "main_module": "worker.js",
    "compatibility_date": "2026-09-01",
    "bindings": [
        {"type": "secret_text", "name": "FORWARDER_SECRET", "text": sys.argv[1]}
    ],
}))
EOF
)
api PUT "/accounts/${CLOUDFLARE_ACCOUNT_ID}/workers/scripts/${SCRIPT_NAME}" \
  -F "metadata=${METADATA};type=application/json" \
  -F "worker.js=@${HERE}/worker.js;type=application/javascript+module" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['success'], d['errors']; print('   uploaded')"

echo "2/3 Ensuring route ${ROUTE_PATTERN}..."
EXISTING_ROUTE=$(api GET "/zones/${ZONE_ID}/workers/routes" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); m=[r for r in d['result'] if r['pattern']=='${ROUTE_PATTERN}']; print(m[0]['id'] if m else '')")
if [ -z "$EXISTING_ROUTE" ]; then
  api POST "/zones/${ZONE_ID}/workers/routes" \
    -H "Content-Type: application/json" \
    --data "{\"pattern\":\"${ROUTE_PATTERN}\",\"script\":\"${SCRIPT_NAME}\"}" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['success'], d['errors']; print('   route created')"
else
  api PUT "/zones/${ZONE_ID}/workers/routes/${EXISTING_ROUTE}" \
    -H "Content-Type: application/json" \
    --data "{\"pattern\":\"${ROUTE_PATTERN}\",\"script\":\"${SCRIPT_NAME}\"}" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['success'], d['errors']; print('   route updated')"
fi

echo "3/3 Ensuring create.karaokehunt.com is proxied..."
RECORD_ID=$(api GET "/zones/${ZONE_ID}/dns_records?name=create.karaokehunt.com" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); r=d['result']; print(r[0]['id'] if r else '')")
if [ -z "$RECORD_ID" ]; then
  echo "   ERROR: no DNS record for create.karaokehunt.com — create one first" >&2
  exit 1
fi
api PATCH "/zones/${ZONE_ID}/dns_records/${RECORD_ID}" \
  -H "Content-Type: application/json" \
  --data '{"proxied": true}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['success'], d['errors']; print('   proxied')"

echo "Done. Smoke test:"
echo "  curl -s https://create.karaokehunt.com/ (expect {\"status\":\"ok\",...})"
