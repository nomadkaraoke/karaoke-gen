# Testing & Code Quality Guide

This document defines testing standards, code quality principles, and CI requirements for Nomad Karaoke Gen.

## Critical: All Tests Must Pass

**All tests must pass before merging.** If tests fail:

1. **Fix them** - Don't assume failures are "pre-existing issues"
2. **Investigate** - Understand why the test is failing
3. **Don't dismiss** - If a test fails, either the code or test needs fixing

CI blocks merging if tests fail. If you encounter failures locally, fix them as part of your work.

### Running Tests

```bash
# Run ALL tests (backend + frontend) - installs deps automatically
make test

# Run subsets for faster iteration
make test-backend   # Backend only (~2 min)
make test-frontend  # Frontend only (~3 min)
```

Dependencies are installed automatically when needed.

## Critical: Automate Manual Testing Steps

**If your plan includes "Manual Testing" steps, convert them to automated Playwright E2E tests.**

When you write a plan with steps like "manually verify the dialog opens" or "test that the button appears for complete jobs", these are perfect candidates for automated E2E tests. Don't leave verification as a manual step that may never happen.

### Why Automate?

1. **Codifies expected behavior** - The test documents exactly what should happen
2. **Repeatable verification** - Can re-run after future changes to catch regressions
3. **Actually gets executed** - Manual steps are often skipped; automated tests run reliably
4. **Proves the feature works** - Passing test = working feature in production

### Production E2E Tests Are Valuable

It's acceptable (and preferred) to write E2E tests that only run against production:

```typescript
// frontend/e2e/production/admin-delete-outputs.spec.ts
test('Admin can delete job outputs', async ({ page }) => {
  // This test runs against real production after deployment
  // It verifies the feature actually works end-to-end
  await page.goto(`${PROD_URL}/admin/jobs/${testJobId}`);
  await page.click('[data-testid="delete-outputs-button"]');
  await expect(page.getByText('Confirm deletion')).toBeVisible();
  // ... complete the flow
});
```

**Even if the test is only run once** after deployment, it's still valuable because:
- It codifies the expected behavior completely
- It proves the deployed code works
- It can be re-run if issues are suspected
- It serves as documentation for the feature

### Workflow for Production-Only E2E Tests

1. **Implement the feature** with unit tests and regression E2E tests (mocked API)
2. **Write production E2E test** that exercises the real flow
3. **Merge and deploy** to production
4. **Run the production E2E test** to verify: `npm run test:e2e:prod -- admin-delete-outputs.spec.ts`
5. **Commit passed** - Feature is verified working in production

### Example: Converting Manual Steps to E2E

**Before (manual testing in plan):**
```
Manual testing:
- Create/complete a test job with YouTube/Dropbox upload
- Navigate to admin job detail
- Click "Delete All Outputs" button
- Verify dialog shows, confirm deletion
- Verify "Outputs Deleted" badge appears
```

**After (automated production E2E):**
```typescript
test('Delete outputs flow', async ({ page }) => {
  // Use existing complete job or create one via API
  const jobId = process.env.E2E_COMPLETE_JOB_ID;

  await page.goto(`${PROD_URL}/admin/jobs/${jobId}`);
  await page.click('[data-testid="delete-outputs-button"]');
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.click('[data-testid="confirm-delete"]');
  await expect(page.getByText('Outputs Deleted')).toBeVisible();
});
```

### When to Use Each Test Type

| Scenario | Test Type | Location |
|----------|-----------|----------|
| UI component behavior | Jest unit test | `components/__tests__/` |
| UI flow with mocked API | Playwright regression | `e2e/regression/` |
| Full flow against real backend | Playwright production | `e2e/production/` |
| API endpoint logic | pytest unit test | `backend/tests/` |
| Database operations | Emulator test | `backend/tests/emulator/` |

**Default to production E2E** for any feature that involves user-facing workflows. The goal is automated verification of deployed code, not checkbox completion of manual steps.

## Core Principles

### SOLID Principles

All code should follow SOLID principles:

1. **Single Responsibility** - Each class/function does one thing well
2. **Open/Closed** - Open for extension, closed for modification
3. **Liskov Substitution** - Subtypes must be substitutable for base types
4. **Interface Segregation** - Many specific interfaces over one general interface
5. **Dependency Inversion** - Depend on abstractions, not concretions

### Code Quality Standards

- **Maintainability** - Code should be easy to understand and modify
- **Testability** - Code should be designed for easy testing (dependency injection, pure functions)
- **No magic numbers** - Use named constants
- **Explicit over implicit** - Clear is better than clever
- **DRY but not prematurely** - Don't repeat yourself, but don't abstract too early

## Test Types

### Unit Tests

**Locations:**
- `tests/unit/` - CLI package (`karaoke_gen/`) unit tests
- `backend/tests/` - Backend API unit tests (excludes `emulator/`)
- `frontend/components/__tests__/` - React component unit tests

**Characteristics:**
- Fast (< 1ms per test typical)
- No external dependencies (mocked)
- No network, database, or filesystem access
- Run on every commit

**Coverage targets:**
- `karaoke_gen/` package: 69%+
- `backend/` package: No explicit threshold (but coverage collected)
- `frontend/` components: No explicit threshold (but coverage collected)

```python
# Good unit test example (backend/tests/test_job_manager.py)
def test_create_job_applies_default_theme(mock_firestore, mock_gcs):
    job_manager = JobManager()
    job = job_manager.create_job(
        artist="Test Artist",
        title="Test Song",
        user_email="test@example.com"
    )
    assert job.theme_id == "nomad"  # Default theme applied
```

**Critical: Mocks Must Match Real API Contracts**

When mocking methods, ensure the mock's `return_value` matches what the real method returns. A common bug pattern:

```python
# ❌ BAD: Assumes method returns boolean
mock_service.update_job.return_value = True

# Code under test:
success = job_manager.update_job(job_id, updates)
if not success:  # BUG: if real method returns None, this is always True!
    raise HTTPException(500)
```

The fix:

```python
# ✅ GOOD: Check real method signature first
# def update_job(self, job_id: str, updates: Dict) -> None:
mock_service.update_job.return_value = None  # Match real API
```

**Before writing a mock**, check the actual method's return type. Void methods should mock `return_value = None`. Methods that raise on error (rather than returning False) should have mocks that raise exceptions for error cases. See [LESSONS-LEARNED.md](LESSONS-LEARNED.md#mocks-must-match-real-return-values-feb-2026) for a production bug caused by this.

**Critical: Test the Contract Between Caller and Callee**

When function A calls function B, it's not enough to test B with hand-crafted ideal inputs. You must also verify A actually produces those inputs. This is a "contract test."

```python
# ❌ BAD: Tests _store_metadata in isolation with hand-crafted input
# If transcribe_lyrics() never includes "lyrics_dir", this test still passes
# but production silently loses all metadata
def test_stores_audioshake_ids(tmp_path):
    _store_lyrics_processing_metadata(
        transcription_result={"lyrics_dir": str(tmp_path)},  # Too helpful!
        ...
    )
    assert call_args["audioshake_task_id"] == "task-abc"  # ✅ passes, but useless

# ✅ GOOD: Also test that the caller produces the expected input
def test_transcribe_lyrics_returns_lyrics_dir(tmp_path):
    result = processor.transcribe_lyrics(audio, artist, title, tmp_path)
    assert "lyrics_dir" in result  # Catches the actual bug
```

**When to write contract tests:**
- Function A builds a dict/object and passes it to function B
- Function B reads specific keys/fields from that dict/object
- The dict/object shape is implicit (no schema, no TypedDict, no dataclass)

**Especially dangerous:** Non-fatal code paths (try/except that swallows errors). If the function is designed to never crash, a test that only checks "didn't raise" provides zero signal. Assert on the *positive outcome* (data was stored, value was returned) not just the absence of exceptions.

See [LESSONS-LEARNED.md](LESSONS-LEARNED.md#test-the-contract-not-just-the-function-mar-2026) for the production bug this pattern caused.

### Integration Tests (`tests/integration/`)

**Purpose:** Test CLI workflows and component interactions.

**Characteristics:**
- May mock high-level components
- Tests full command pipelines
- Uses `@pytest.mark.integration` marker
- Often async (`@pytest.mark.asyncio`)

**Example scenarios:**
- CLI `--edit-lyrics` flow end-to-end
- Job creation → processing pipeline
- Audio separation → transcription coordination

### Emulator Tests (`backend/tests/emulator/`)

**Purpose:** Test real Firestore/GCS behavior without production access.

**Characteristics:**
- Requires GCP emulators running
- Tests actual database operations
- Verifies subcollections, TTL policies, etc.
- Auto-skips if emulators unavailable

**Setup:**
```bash
# Start emulators
make emulators-start

# Run emulator tests
poetry run pytest backend/tests/emulator/ -v

# Stop emulators
make emulators-stop
```

### E2E Tests (Frontend)

**Locations:**
- `frontend/e2e/regression/` - Mocked API tests (CI-safe, fast)
- `frontend/e2e/production/` - Real backend tests (comprehensive, slower)
- `frontend/e2e/manual/` - Tests requiring manual setup (not in CI)

**Tool:** Playwright

**Regression tests** run on every PR:
- Mock API responses via fixtures
- Test UI behavior in isolation
- Playwright auto-starts dev server via `webServer` config
- ~3 min total runtime

**Production tests** run on-demand or daily:
- Hit real `https://gen.nomadkaraoke.com` and `https://api.nomadkaraoke.com`
- Full user journeys (20-40 min for happy path)
- Require authentication tokens

**Manual tests** are NOT included in CI:
- Require separate dev servers (e.g., LyricsTranscriber frontend on port 5173)
- See `frontend/e2e/manual/README.md` for setup instructions

## Playwright Usage

### Installation

```bash
cd frontend
npm install -D @playwright/test
npx playwright install
```

### Running E2E Tests

```bash
# Regression tests (mocked API, default)
npm run test:e2e

# Production tests (real backend)
npm run test:e2e:prod

# With UI mode for debugging
npm run test:e2e:ui

# Specific test file
npx playwright test e2e/regression/smoke.spec.ts

# Production test with environment
E2E_TEST_TOKEN=xxx npm run test:e2e:prod
```

### Investigating Frontend Issues

When debugging frontend issues, use Playwright to:

1. **Reproduce the issue** - Write a test that fails
2. **Debug visually** - Use `--ui` mode or `--headed`
3. **Capture evidence** - Screenshots, traces, console logs
4. **Verify the fix** - Test passes after fix

```bash
# Debug mode with browser visible
npm run test:e2e:headed

# Generate trace for failed tests
npx playwright test --trace on
```

### Writing E2E Tests

**Regression test example (mocked API):**
```typescript
// frontend/e2e/regression/smoke.spec.ts
import { test, expect } from '@playwright/test';
import { setupApiFixtures, clearAuthToken } from '../fixtures/test-helper';

test.describe('Smoke Tests', () => {
  test.beforeEach(async ({ page }) => {
    await clearAuthToken(page);
  });

  test('landing page loads and shows hero', async ({ page }) => {
    await setupApiFixtures(page, { mocks: [] });
    await page.goto('/');
    await expect(page.locator('h1')).toContainText('Karaoke');
  });
});
```

**Production test example (real backend):**
```typescript
// frontend/e2e/production/lyrics-review-only.spec.ts
import { test, expect } from '@playwright/test';

const PROD_URL = 'https://gen.nomadkaraoke.com';

test.describe('Lyrics Review', () => {
  test('Complete lyrics review via UI', async ({ page }) => {
    test.setTimeout(900_000); // 15 min - preview generation can be slow

    const jobId = process.env.E2E_JOB_ID;
    await page.goto(`${PROD_URL}/review/${jobId}`);
    // ... test implementation
  });
});
```

## CI Pipeline

### Required Checks (Block PR Merge)

All PRs must pass these checks (path-filtered for efficiency):

| Check | Description | Threshold |
|-------|-------------|-----------|
| `backend-lint` | Ruff linter | No errors |
| `backend-unit-tests` | Backend tests (excludes emulator) | Must pass |
| `backend-emulator-tests` | GCP emulator integration | Must pass |
| `package-unit-tests` | CLI package tests | 69% coverage |
| `package-integration-tests` | CLI integration tests | Must pass |
| `frontend-lint` | ESLint (Rules of Hooks) | No errors |
| `frontend-unit-tests` | Jest component tests | Must pass |
| `frontend-e2e-smoke` | Playwright smoke tests | Must pass |
| `frontend-build` | Next.js build | Must pass |

### On-Demand Checks

| Check | Trigger | Description |
|-------|---------|-------------|
| `frontend-e2e-full` | `e2e` PR label | Full regression E2E suite |
| `e2e-happy-path` | Daily 6 AM UTC | Complete production happy path |

### Path Filtering

CI intelligently runs only relevant tests based on changed files:

| Changed Path | Tests Run |
|--------------|-----------|
| `frontend/` | Frontend lint, unit, E2E |
| `backend/` | Backend lint, unit, emulator |
| `karaoke_gen/` | Package unit, integration |
| `tests/` | Package tests |
| `infrastructure/` | Infrastructure validation |

### Known Testing Gaps

**Infrastructure dependencies** (Modal, AudioShake, Cloud Tasks, Firestore) are mocked in tests. This means IAM permission errors or API changes won't be caught until production. Mitigations:

1. **Emulator tests** - Use `backend/tests/emulator/` for Firestore/GCS integration
2. **Manual verification** after infrastructure changes (Pulumi updates)
3. **Health endpoints** - `/api/health` and `/api/health/detailed` validate connectivity
4. **Daily E2E** - `e2e-happy-path.yml` runs full production flow

## Production E2E Testing

For comprehensive production testing:

```bash
cd frontend

# Run full happy path (requires tokens)
E2E_TEST_TOKEN=<token> npx playwright test e2e/production/happy-path-real-user.spec.ts \
  --config=playwright.production.config.ts

# Run focused tests (faster iteration)
E2E_JOB_ID=<id> E2E_REVIEW_TOKEN=<token> npx playwright test e2e/production/lyrics-review-only.spec.ts \
  --config=playwright.production.config.ts

# Admin dashboard tests
KARAOKE_ADMIN_TOKEN=<admin-token> npx playwright test e2e/production/admin-dashboard.spec.ts \
  --config=playwright.production.config.ts
```

### Environment Variables

| Variable | Purpose | Required For |
|----------|---------|--------------|
| `E2E_TEST_TOKEN` | User authentication | Most production tests |
| `KARAOKE_ADMIN_TOKEN` | Admin authentication | Admin dashboard tests |
| `E2E_JOB_ID` | Existing job to test | Focused tests |
| `E2E_REVIEW_TOKEN` | Review page access | Lyrics review tests |
| `TESTMAIL_API_KEY` | TestMail.app API | Magic link auth tests |
| `TESTMAIL_NAMESPACE` | TestMail.app namespace | Magic link auth tests |

### Getting Test Tokens

**Option 1: From browser**
1. Login to https://gen.nomadkaraoke.com
2. Open DevTools → Application → Local Storage
3. Copy the `karaoke_access_token` value

**Option 2: From backend logs**
When the daily E2E runs, tokens are logged for debugging.

The production tests cover:
- Magic link authentication flow
- Audio search and selection
- YouTube URL processing
- File upload workflow
- Lyrics review and correction
- Preview video generation
- Instrumental selection
- Final encoding and download
- Admin dashboard functionality

## Ad-Hoc Production Debugging (for Agents)

When asked to "test it yourself in prod" or debug a specific production issue, use the debug script template:

### Quick Start

```bash
# Copy the template (the .local suffix is gitignored)
cp frontend/e2e/helpers/debug-prod-template.mjs frontend/test-my-issue.local.mjs

# Edit the script to customize what you're testing
# You can hardcode tokens in .local files - they won't be committed

# Run it
cd frontend && node test-my-issue.local.mjs
```

### With Environment Variables (Preferred)

```bash
# Get admin token from Secret Manager
export KARAOKE_ADMIN_TOKEN=$(gcloud secrets versions access latest --secret=admin-tokens --project=nomadkaraoke | cut -d',' -f1)

# Run the template directly
cd frontend && node e2e/helpers/debug-prod-template.mjs

# Or with a specific job
DEBUG_JOB_ID=abc123 node e2e/helpers/debug-prod-template.mjs
```

### What the Template Provides

- Playwright browser automation against production
- Auth token injection (via localStorage)
- Console error capture
- HTTP error capture (4xx, 5xx responses)
- Screenshot capability
- Customizable debug steps

### Gitignore Patterns

Files matching these patterns are gitignored in `frontend/`:
- `test-*.local.*` - Ad-hoc test scripts
- `debug-*.local.*` - Debug scripts

**Never commit tokens.** Use env vars or `.local` files only.

### When to Use What

| Scenario | Approach |
|----------|----------|
| Quick one-off debugging | Copy template → `.local` file |
| Reusable production test | Add to `e2e/production/` with env var auth |
| Regression test (mocked) | Add to `e2e/regression/` |

### Example: Debug a Specific Job's Review Page

```javascript
// frontend/test-review-debug.local.mjs
import { chromium } from 'playwright';

const TOKEN = 'your-token-here'; // OK in .local files
const JOB_ID = '26391a79';

async function debug() {
  const browser = await chromium.launch({ headless: false }); // See the browser
  const page = await browser.newContext().then(c => c.newPage());

  await page.addInitScript(t => localStorage.setItem('karaoke_access_token', t), TOKEN);
  await page.goto(`https://gen.nomadkaraoke.com/app/jobs#/${JOB_ID}/review`);

  // Debug interactively - the browser stays open
  await page.pause(); // Opens Playwright inspector

  await browser.close();
}

debug();
```

## Running Tests Locally

```bash
# All backend/package tests
make test

# Specific suites
make test-unit       # Package unit tests (69% coverage)
make test-backend    # Backend tests only
make test-e2e        # E2E with emulators

# With coverage report
poetry run pytest tests/unit --cov=karaoke_gen --cov-report=html
open htmlcov/index.html

# Frontend tests
cd frontend
npm run test:unit    # Jest watch mode
npm run test:ci      # Jest with coverage
npm run test:e2e     # Playwright regression
npm run test:all     # Unit + E2E combined
```

## Test File Organization

```
karaoke-gen/
├── tests/
│   ├── unit/                        # CLI package unit tests
│   │   ├── conftest.py              # Shared fixtures
│   │   ├── test_audio.py            # Audio separation tests
│   │   ├── test_karaoke_finalise/   # Finalize module tests
│   │   └── ...
│   ├── integration/                 # CLI integration tests
│   │   ├── test_cli_edit_lyrics.py  # Edit lyrics workflow
│   │   └── test_cli_backend_integration.py
│   └── conftest.py                  # Root fixtures
├── backend/
│   └── tests/
│       ├── conftest.py              # FastAPI TestClient, mocks
│       ├── test_job_manager.py      # Job management tests
│       ├── test_dependencies.py     # DI tests
│       ├── test_made_for_you_webhook.py  # Webhook tests
│       └── emulator/                # GCP emulator tests
│           ├── conftest.py          # Emulator setup
│           ├── test_emulator_integration.py
│           └── test_worker_logs_direct.py
└── frontend/
    ├── components/__tests__/        # Jest component tests
    │   ├── JobCard.test.tsx
    │   ├── ThemeSelector.test.tsx
    │   └── ...
    ├── e2e/
    │   ├── fixtures/                # Shared test helpers
    │   │   ├── test-helper.ts       # API mocking utilities
    │   │   └── mock-responses.ts    # Canned API responses
    │   ├── regression/              # Mocked API E2E (CI)
    │   │   ├── smoke.spec.ts
    │   │   ├── authentication.spec.ts
    │   │   ├── karaoke-generation.spec.ts
    │   │   └── ...
    │   ├── production/              # Real backend E2E
    │   │   ├── happy-path-real-user.spec.ts
    │   │   ├── lyrics-review-only.spec.ts
    │   │   └── admin-dashboard.spec.ts
    │   └── manual/                  # Manual setup required (NOT in CI)
    │       ├── README.md            # Setup instructions
    │       └── lyrics-review-mobile.spec.ts  # LyricsTranscriber frontend
    ├── jest.config.js
    ├── jest.setup.js
    ├── playwright.config.ts         # Default (regression)
    └── playwright.production.config.ts
```

## Writing Good Tests

### Do

- Test behavior, not implementation
- Use descriptive test names: `test_create_job_applies_default_theme_when_none_specified`
- One assertion per test (when practical)
- Use fixtures for common setup
- Test edge cases and error conditions
- Mock external services (Modal, AudioShake, Genius, etc.)

### Don't

- Test private methods directly
- Write tests that depend on execution order
- Use sleep/wait without timeouts
- Test framework code (FastAPI, React, Next.js)
- Skip tests without a reason

## Coverage Enforcement

Coverage is enforced in CI for the CLI package:

```bash
# Package unit tests require 69% coverage
poetry run pytest tests/unit/ --cov=karaoke_gen --cov-fail-under=69
```

To check coverage locally:

```bash
# CLI package
poetry run pytest tests/unit --cov=karaoke_gen --cov-report=term-missing

# Backend (no threshold, but useful to see)
poetry run pytest backend/tests --cov=backend --cov-report=term-missing

# Frontend (via Jest)
cd frontend && npm run test:ci  # Includes coverage
```

## Mocking Guidelines

### When to Mock

- External APIs (Modal, AudioShake, Genius, Spotify lyrics, YouTube)
- GCS/Firestore in unit tests
- FFmpeg in unit tests
- Time-dependent operations
- Network requests

### When NOT to Mock

- Your own code (test the real thing)
- Simple data transformations
- In emulator tests (use real Firestore/GCS emulators)
- In production E2E tests (use real backend)

### Mock Example

```python
from unittest.mock import Mock, patch

def test_fetch_audio_handles_modal_timeout():
    with patch('karaoke_gen.audio.modal_api.submit_job') as mock_submit:
        mock_submit.side_effect = TimeoutError("Modal API timeout")

        with pytest.raises(AudioSeparationError):
            separate_audio("test.mp3")
```

### Frontend Mock Example

```typescript
// Using Playwright route interception
test('shows error on API failure', async ({ page }) => {
  await page.route('**/api/jobs', route => {
    route.fulfill({ status: 500, body: 'Server Error' });
  });

  await page.goto('/app');
  await expect(page.getByText('Error loading jobs')).toBeVisible();
});
```

## Debugging Failed Tests

### Local Debugging

```bash
# Run single test with verbose output
poetry run pytest tests/unit/test_audio.py::test_separate_audio -vvs

# Drop into debugger on failure
poetry run pytest --pdb

# Show local variables on failure
poetry run pytest -l

# Frontend: Debug specific test
cd frontend && npx playwright test smoke.spec.ts --debug
```

### CI Debugging

1. Check the GitHub Actions logs
2. Look for the specific assertion that failed
3. Reproduce locally with the same command
4. If flaky, check for timing/ordering issues
5. Download test artifacts (screenshots, videos) from failed E2E runs

## Adding Tests for New Features

When adding a new feature:

1. **Write tests first** (TDD) or immediately after
2. **Unit tests** for business logic in `karaoke_gen/` or `backend/`
3. **Backend tests** for new API endpoints
4. **Frontend unit tests** for new components
5. **E2E regression tests** for UI flows with mocked API (add to `e2e/regression/`)
6. **Production E2E test** for full workflow verification (add to `e2e/production/`)
7. **Update this doc** if new patterns emerge

**Remember:** If your verification plan includes manual steps, write a production E2E test instead. See [Critical: Automate Manual Testing Steps](#critical-automate-manual-testing-steps).

## Quick Reference

| Task | Command |
|------|---------|
| **All tests (before commit)** | `make test` |
| Backend only | `make test-backend` |
| Frontend only | `make test-frontend` |
| Package unit | `make test-unit` |
| Emulator tests | `make test-e2e` |
| Frontend E2E (production) | `cd frontend && npm run test:e2e:prod` |
| Coverage HTML | `poetry run pytest tests/unit --cov=karaoke_gen --cov-report=html` |
| Install deps | `make install` |
| Start emulators | `make emulators-start` |
| Stop emulators | `make emulators-stop` |
