#!/bin/bash
# gen "local stack" for E2E: the REAL backend on the Firestore + GCS emulators, with
# fake workers (FAKE_WORKERS=1) that walk jobs through their real states, the
# /api/test/* router, captured email and fake Stripe checkout. Web Playwright
# (frontend/e2e/local-stack) and mobile Maestro flows run against it.
#
# Usage:
#   scripts/local-stack.sh up [--frontend]   start emulators + backend (+ Next dev server)
#   scripts/local-stack.sh down              stop everything
#   scripts/local-stack.sh status            show what's running
#   scripts/local-stack.sh logs [backend|frontend]
#   scripts/local-stack.sh serve             emulators + backend in the FOREGROUND (Playwright webServer, CI)
#   scripts/local-stack.sh env               print the backend env (for running uvicorn yourself)
#
# Ports (override via env): LOCAL_STACK_API_PORT (8010), LOCAL_STACK_WEB_PORT (3010).
# The backend binds 0.0.0.0 so the Android emulator (10.0.2.2) and iOS simulator
# can reach it; set LOCAL_STACK_API_URL to the address devices should use.
# See docs/TESTING.md § "Local stack".

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_DIR="$PROJECT_ROOT/.local-stack"
mkdir -p "$STATE_DIR"

API_PORT="${LOCAL_STACK_API_PORT:-8010}"
WEB_PORT="${LOCAL_STACK_WEB_PORT:-3010}"
API_URL="${LOCAL_STACK_API_URL:-http://127.0.0.1:$API_PORT}"
WEB_URL="${LOCAL_STACK_WEB_URL:-http://127.0.0.1:$WEB_PORT}"
ADMIN_TOKEN="${LOCAL_STACK_ADMIN_TOKEN:-local-stack-admin}"

backend_env() {
    cat <<EOF
FAKE_WORKERS=1
ENVIRONMENT=development
FIRESTORE_EMULATOR_HOST=127.0.0.1:8080
STORAGE_EMULATOR_HOST=http://127.0.0.1:4443
GOOGLE_CLOUD_PROJECT=test-project
GCS_BUCKET_NAME=test-bucket
FIRESTORE_COLLECTION=jobs
ADMIN_TOKENS=$ADMIN_TOKEN
FRONTEND_URL=$WEB_URL
LOCAL_STACK_API_URL=$API_URL
PORT=$API_PORT
ENABLE_CLOUD_TASKS=false
CREDIT_EVAL_ENABLED=false
REVIEW_AUDIO_PROXY=1
MAX_SIGNUPS_PER_IP=100000
BOARD_MAX_SIGNUPS_PER_IP=100000
LOGIN_CODES_PER_IP_PER_HOUR=100000
KJBOX_PARTNER_SECRET=${KJBOX_PARTNER_SECRET:-local-stack-secret}
DECIDE_GEN_PARTNER_SECRET=${DECIDE_GEN_PARTNER_SECRET:-local-stack-secret}
POSTMARK_SERVER_TOKEN=
STRIPE_SECRET_KEY=
EOF
}

export_backend_env() {
    while IFS= read -r line; do
        export "${line?}"
    done < <(backend_env)
}

is_running() {
    local pidfile="$STATE_DIR/$1.pid"
    [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null
}

wait_for() {
    local url="$1" name="$2" tries="${3:-90}"
    for _ in $(seq 1 "$tries"); do
        if curl -sf "$url" >/dev/null 2>&1; then
            echo "✅ $name ready ($url)"
            return 0
        fi
        sleep 1
    done
    echo "❌ $name did not become ready at $url"
    return 1
}

start_backend() {
    if is_running backend; then
        echo "⚠️  backend already running (pid $(cat "$STATE_DIR/backend.pid"))"
        return
    fi
    export_backend_env
    (cd "$PROJECT_ROOT" && poetry run python "$SCRIPT_DIR/seed-local-data.py")
    echo "🌐 Starting backend on :$API_PORT (FAKE_WORKERS=1)"
    (
        cd "$PROJECT_ROOT"
        nohup poetry run uvicorn backend.main:app --host 0.0.0.0 --port "$API_PORT" \
            > "$STATE_DIR/backend.log" 2>&1 &
        echo $! > "$STATE_DIR/backend.pid"
    )
    if ! wait_for "http://127.0.0.1:$API_PORT/api/test/status" backend 120; then
        tail -40 "$STATE_DIR/backend.log"
        exit 1
    fi
}

start_frontend() {
    if is_running frontend; then
        echo "⚠️  frontend already running (pid $(cat "$STATE_DIR/frontend.pid"))"
        return
    fi
    echo "🖥️  Starting Next dev server on :$WEB_PORT (BACKEND_URL=$API_URL)"
    (
        cd "$PROJECT_ROOT/frontend"
        BACKEND_URL="http://127.0.0.1:$API_PORT" nohup npx next dev -p "$WEB_PORT" -H 127.0.0.1 \
            > "$STATE_DIR/frontend.log" 2>&1 &
        echo $! > "$STATE_DIR/frontend.pid"
    )
    wait_for "$WEB_URL/" frontend 180
}

stop_pid() {
    local name="$1" pidfile="$STATE_DIR/$1.pid"
    if is_running "$name"; then
        local pid
        pid="$(cat "$pidfile")"
        # Kill the process group too (poetry/npx spawn children).
        pkill -TERM -P "$pid" 2>/dev/null || true
        kill "$pid" 2>/dev/null || true
        echo "🛑 stopped $name (pid $pid)"
    fi
    rm -f "$pidfile"
}

cmd="${1:-}"
shift || true
case "$cmd" in
    up)
        "$SCRIPT_DIR/start-emulators.sh"
        start_backend
        if [[ "${1:-}" == "--frontend" ]]; then
            start_frontend
        fi
        echo ""
        echo "Local stack up:"
        echo "  API      $API_URL   (docs: $API_URL/docs, test API: $API_URL/api/test/status)"
        is_running frontend && echo "  Web      $WEB_URL"
        echo "  Admin    X-Admin-Token: $ADMIN_TOKEN"
        echo "  Logs     scripts/local-stack.sh logs backend"
        ;;
    down)
        stop_pid frontend
        stop_pid backend
        "$SCRIPT_DIR/stop-emulators.sh" || true
        ;;
    serve)
        "$SCRIPT_DIR/start-emulators.sh"
        export_backend_env
        (cd "$PROJECT_ROOT" && poetry run python "$SCRIPT_DIR/seed-local-data.py")
        cd "$PROJECT_ROOT"
        echo "🌐 Backend on :$API_PORT (FAKE_WORKERS=1), logging to .local-stack/backend.log"
        exec poetry run uvicorn backend.main:app --host 0.0.0.0 --port "$API_PORT" \
            > "$STATE_DIR/backend.log" 2>&1
        ;;
    status)
        for name in backend frontend; do
            if is_running "$name"; then echo "$name: running (pid $(cat "$STATE_DIR/$name.pid"))"; else echo "$name: stopped"; fi
        done
        curl -sf "http://127.0.0.1:$API_PORT/api/test/status" && echo || true
        ;;
    logs)
        tail -f "$STATE_DIR/${1:-backend}.log"
        ;;
    env)
        backend_env
        ;;
    *)
        sed -n '2,20p' "$0"
        exit 1
        ;;
esac
