#!/bin/bash
# Stop GCP emulators
# This script stops Firestore and GCS emulators

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
EMULATOR_LOG_DIR="$PROJECT_ROOT/.emulator-logs"

echo "🛑 Stopping GCP emulators..."
echo ""

# Stop Firestore emulator
if [ -f "$EMULATOR_LOG_DIR/firestore.pid" ]; then
    FIRESTORE_PID=$(cat "$EMULATOR_LOG_DIR/firestore.pid")
    if ps -p $FIRESTORE_PID > /dev/null 2>&1; then
        echo "📦 Stopping Firestore emulator (PID: $FIRESTORE_PID)..."
        # Kill the process and its children (Java emulator subprocess)
        pkill -P $FIRESTORE_PID 2>/dev/null || true
        kill $FIRESTORE_PID 2>/dev/null || true
    fi
    rm -f "$EMULATOR_LOG_DIR/firestore.pid"
fi
# Always clean up by port as a fallback (catches orphaned Java processes)
REMAINING=$(lsof -ti:8080 2>/dev/null || true)
if [ -n "$REMAINING" ]; then
    echo "📦 Cleaning up remaining processes on port 8080..."
    kill $REMAINING 2>/dev/null || true
    echo "   ✅ Firestore emulator stopped"
else
    echo "⚠️  Firestore emulator not running"
fi

# Stop GCS emulator
if docker ps -q -f name=test-gcs-emulator > /dev/null 2>&1; then
    echo "📦 Stopping GCS emulator (Docker container)..."
    docker stop test-gcs-emulator >/dev/null 2>&1 || true
    docker rm test-gcs-emulator >/dev/null 2>&1 || true
    echo "   ✅ GCS emulator stopped"
else
    echo "⚠️  GCS emulator not running"
fi

echo ""
echo "🎉 Emulators stopped successfully!"

