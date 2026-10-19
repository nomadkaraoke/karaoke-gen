#!/usr/bin/env bash
# Upload the requests-host Worker script (idempotent). See worker.js for what it does.
#
# Only the script is deployed here; its route (requests.nomadkaraoke.com/*) and the proxied
# requests CNAME are declared in the nomad-cloudflare Pulumi stack (../pages_workers.py,
# ../dns.py). First-time order: run this, then `pulumi up`.
#
# Requires CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID (workspace .envrc).
set -euo pipefail

SCRIPT_NAME="requests-host"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

: "${CLOUDFLARE_API_TOKEN:?source the workspace .envrc first}"
: "${CLOUDFLARE_ACCOUNT_ID:?source the workspace .envrc first}"

echo "Uploading worker script ${SCRIPT_NAME}..."
curl -sS -X PUT "https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/workers/scripts/${SCRIPT_NAME}" \
  -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
  -F 'metadata={"main_module":"worker.js","compatibility_date":"2026-09-01"};type=application/json' \
  -F "worker.js=@${HERE}/worker.js;type=application/javascript+module" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['success'], d['errors']; print('   uploaded')"

echo "Smoke test (once the route exists):"
echo "  curl -sI https://requests.nomadkaraoke.com/   # expect 200, no location header"
