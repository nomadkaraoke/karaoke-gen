# Makefile for karaoke-gen project
# Run `make help` to see available commands

.PHONY: help install install-backend install-frontend build-frontend test test-unit test-backend test-e2e test-frontend test-all lint clean emulators-start emulators-stop

# Default target
help:
	@echo "Available commands:"
	@echo "  make test           - Run ALL tests (backend + frontend, installs deps automatically)"
	@echo "  make test-backend   - Run backend tests only (unit + emulator)"
	@echo "  make test-frontend  - Run frontend tests only (unit + E2E)"
	@echo "  make test-unit      - Run unit tests only (karaoke_gen package)"
	@echo "  make test-e2e       - Run emulator tests with auto-start/stop"
	@echo "  make install        - Install all dependencies (backend + frontend)"
	@echo "  make build-frontend - Build frontend and copy to Python package (for local testing)"
	@echo "  make lint           - Run linter checks"
	@echo "  make emulators-start - Start GCP emulators for local development"
	@echo "  make emulators-stop  - Stop GCP emulators"
	@echo ""
	@echo "Before committing, run: make test"

# Install dependencies (only if needed)
install-backend:
	@if [ ! -d "$$(poetry env info --path 2>/dev/null)" ] || ! poetry run python -c "import fastapi" 2>/dev/null; then \
		echo "=== Installing backend dependencies ==="; \
		poetry install; \
	fi

install-frontend:
	@if [ ! -d "frontend/node_modules" ]; then \
		echo "=== Installing frontend dependencies ==="; \
		cd frontend && npm install; \
	fi

install: install-backend install-frontend

# Build frontend and copy to Python package (for local CLI testing)
build-frontend: install-frontend
	@echo "=== Building frontend ==="
	cd frontend && npm run build
	@echo "=== Copying build to Python package ==="
	cp -r frontend/out/* karaoke_gen/nextjs_frontend/out/
	@echo "✅ Frontend built and ready for local testing"

# Run unit tests for karaoke_gen package
test-unit: install-backend
	@echo "=== Running karaoke_gen unit tests ==="
	poetry run pytest tests/unit/ -v --cov=karaoke_gen --cov-report=term-missing --cov-fail-under=69

# Run backend unit tests (excludes emulator tests)
test-backend-unit: install-backend
	@echo "=== Running backend unit tests ==="
	poetry run pytest backend/tests/ --ignore=backend/tests/emulator -v

# Run E2E integration tests with emulators
test-e2e: install-backend
	@echo "=== Running E2E integration tests with emulators ==="
	@./scripts/start-emulators.sh || (echo "Failed to start emulators" && exit 1)
	@export FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 && \
	export STORAGE_EMULATOR_HOST=http://127.0.0.1:4443 && \
	export GOOGLE_CLOUD_PROJECT=test-project && \
	export GCS_BUCKET_NAME=test-bucket && \
	export ADMIN_TOKENS=test-admin-token && \
	export ENVIRONMENT=test && \
	poetry run pytest backend/tests/emulator/ -v; \
	TEST_RESULT=$$?; \
	./scripts/stop-emulators.sh; \
	exit $$TEST_RESULT

# Run all backend tests (unit + emulator)
test-backend: test-unit test-backend-unit test-e2e
	@echo ""
	@echo "✅ Backend tests passed!"

# Run frontend tests (unit + E2E)
test-frontend: install-frontend
	@echo "=== Running frontend tests ==="
	cd frontend && npm run test:all

# Run ALL tests (backend + frontend) - use this before committing!
test: test-backend test-frontend
	@echo ""
	@echo "✅ All tests passed! Ready to commit."

# Alias for test
test-all: test

# Lint checks
lint:
	@echo "=== Running lint checks ==="
	poetry run ruff check karaoke_gen/ backend/
	poetry run ruff format --check karaoke_gen/ backend/

# Start emulators for local development
emulators-start:
	@echo "=== Starting GCP emulators ==="
	./scripts/start-emulators.sh

# Stop emulators
emulators-stop:
	@echo "=== Stopping GCP emulators ==="
	./scripts/stop-emulators.sh

# Clean up temporary files
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".coverage" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf htmlcov/ .coverage coverage.xml 2>/dev/null || true
	@echo "Cleaned up temporary files"

