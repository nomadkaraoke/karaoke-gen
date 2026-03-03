#!/bin/bash
# Start GCP emulators for local testing
# This script starts Firestore and GCS emulators in the background

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
EMULATOR_LOG_DIR="$PROJECT_ROOT/.emulator-logs"

# Create log directory
mkdir -p "$EMULATOR_LOG_DIR"

echo "🚀 Starting GCP emulators for local testing..."
echo ""

# Check if emulators are already running
if lsof -Pi :8080 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "⚠️  Firestore emulator already running on port 8080"
else
    echo "📦 Starting Firestore emulator on port 8080..."
    gcloud beta emulators firestore start \
        --host-port=127.0.0.1:8080 \
        --quiet \
        > "$EMULATOR_LOG_DIR/firestore.log" 2>&1 &
    
    FIRESTORE_PID=$!
    echo $FIRESTORE_PID > "$EMULATOR_LOG_DIR/firestore.pid"
    echo "   ✅ Firestore emulator started (PID: $FIRESTORE_PID)"
fi

if lsof -Pi :4443 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "⚠️  GCS emulator already running on port 4443"
else
    echo "📦 Starting GCS emulator (fake-gcs-server) on port 4443..."
    
    # Check if Docker is running
    if ! docker info > /dev/null 2>&1; then
        echo "❌ Docker is not running. Please start Docker and try again."
        exit 1
    fi
    
    # Stop any existing container
    docker rm -f test-gcs-emulator >/dev/null 2>&1 || true
    
    # Start fake-gcs-server
    docker run -d \
        --name test-gcs-emulator \
        -p 4443:4443 \
        fsouza/fake-gcs-server:latest \
        -scheme http \
        -port 4443 \
        -external-url http://localhost:4443 \
        > "$EMULATOR_LOG_DIR/gcs.log" 2>&1
    
    GCS_CONTAINER=$(docker ps -q -f name=test-gcs-emulator)
    echo "   ✅ GCS emulator started (Container: $GCS_CONTAINER)"
fi

echo ""
echo "⏳ Waiting for emulators to be ready..."

# Wait for Firestore emulator with retries (up to 30 seconds)
FIRESTORE_READY=false
for i in {1..30}; do
    if curl -s http://127.0.0.1:8080 > /dev/null 2>&1; then
        FIRESTORE_READY=true
        echo "✅ Firestore emulator is ready (after ${i}s)"
        break
    fi
    sleep 1
done

if [ "$FIRESTORE_READY" = false ]; then
    echo "❌ Firestore emulator failed to start after 30s"
    echo "Checking emulator logs:"
    tail -20 "$EMULATOR_LOG_DIR/firestore.log" 2>&1 || echo "No logs found"
    exit 1
fi

# Wait for GCS emulator with retries (up to 10 seconds)
GCS_READY=false
for i in {1..10}; do
    if curl -s http://127.0.0.1:4443/storage/v1/b > /dev/null 2>&1; then
        GCS_READY=true
        echo "✅ GCS emulator is ready (after ${i}s)"
        break
    fi
    sleep 1
done

if [ "$GCS_READY" = false ]; then
    echo "❌ GCS emulator failed to start after 10s"
    docker logs test-gcs-emulator 2>&1 | tail -20 || echo "No logs found"
    exit 1
fi

echo ""
echo "🎉 Emulators started successfully!"
echo ""
echo "Environment variables to use:"
echo "  export FIRESTORE_EMULATOR_HOST=127.0.0.1:8080"
echo "  export STORAGE_EMULATOR_HOST=http://127.0.0.1:4443"
echo "  export GOOGLE_CLOUD_PROJECT=test-project"
echo ""
echo "To stop emulators, run: ./scripts/stop-emulators.sh"

