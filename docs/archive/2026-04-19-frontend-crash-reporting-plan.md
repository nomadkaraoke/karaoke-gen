# Frontend Crash Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give us long-term peace of mind that frontend errors never reach users silently — every client-side crash is captured, displayed to the user with useful debug info, reported to the backend, and alerted to our existing Discord error channel.

**Architecture:** Reuse the existing error-monitor infrastructure (`backend/services/error_monitor/*`, `error_patterns` Firestore collection, Cloud Run Job → Discord webhook). Add one new backend endpoint `POST /api/client-errors` that normalizes the incoming error and upserts into the same `error_patterns` collection with `service="frontend"`. On the frontend, add global `window` error listeners, a Next.js App Router `error.tsx` boundary, a component-level React `ErrorBoundary` wrapping the two review wrappers, and a shared `CrashReport` UI component that shows the error + "Copy debug info" + "Send crash report" buttons and auto-sends on mount.

**Tech Stack:** FastAPI + Pydantic (backend endpoint), Firestore (existing adapter), Next.js 15 App Router + React 19 (frontend), next-intl for strings, Jest + Testing Library for FE tests, pytest for BE tests.

---

## File Structure

### Backend — new files
- `backend/api/routes/client_errors.py` — `POST /api/client-errors` endpoint
- `backend/services/error_monitor/frontend_ingestion.py` — pure function that converts an inbound report to a `PatternData` and upserts via the existing adapter; also handles per-IP rate limiting via in-memory sliding window
- `backend/tests/api/test_client_errors.py` — endpoint tests
- `backend/tests/unit/services/error_monitor/test_frontend_ingestion.py` — pure-function tests

### Backend — modified files
- `backend/main.py` — register `client_errors.router`

### Frontend — new files
- `frontend/lib/crash-reporter.ts` — `reportClientError({ error, context })`, context collection (UA, URL sanitized, user email if authed, viewport, locale, build sha, release), fire-and-forget `fetch` with `keepalive: true`
- `frontend/lib/client-error-setup.ts` — installs `window.onerror` + `window.onunhandledrejection` listeners once; idempotent
- `frontend/components/CrashReport.tsx` — fatal-error card UI (error name/message, collapsed stack, debug info, copy + send buttons); receives `error`, `digest?`, `onReset?` props
- `frontend/components/CrashReportBoundary.tsx` — React error boundary that renders `CrashReport` in place; catches errors below it and reports them
- `frontend/components/ClientErrorInit.tsx` — client component that calls `client-error-setup` once on mount; zero UI
- `frontend/app/[locale]/error.tsx` — Next.js locale-level error boundary that renders `CrashReport`
- `frontend/app/[locale]/global-error.tsx` — Next.js root error boundary (the only one that must render its own `<html>`/`<body>`)
- `frontend/__tests__/crash-reporter.test.ts` — unit tests for reporter
- `frontend/__tests__/CrashReport.test.tsx` — component tests
- `frontend/__tests__/CrashReportBoundary.test.tsx` — boundary behaviour tests

### Frontend — modified files
- `frontend/app/[locale]/layout.tsx` — mount `<ClientErrorInit />` once
- `frontend/app/[locale]/app/jobs/[[...slug]]/client.tsx` — wrap `LyricsReviewWrapper` and `InstrumentalReviewWrapper` in `CrashReportBoundary`
- `frontend/lib/api.ts` — add `reportClientError` helper (or expose via fetch in `crash-reporter.ts`; pick one, not both)
- `frontend/messages/en.json` — add `crashReport.*` strings (then run `translate.py --target all`)

### Config
- No new env vars required. Reuses existing `DISCORD_ERROR_WEBHOOK_URL` (already set for the error monitor).

---

## Design Decisions

**Dedup model:** Same as backend. `normalize_message(stack || message)` → `compute_pattern_hash("frontend", normalized)`. 1000 users hitting the same bug = 1 pattern with `total_count=1000`. No new collection.

**`service` value:** `"frontend"` so existing monitor/digest treats it as a distinct service in Discord output.

**`resource_type` value:** `"browser"`. We keep it coarse; the pathname (sanitized) goes into `sample_message` for context.

**PII:** strip query strings from `url`; user email is optional and only included if authenticated (so logged-out crashes still report but anonymously). Do not send cookies, localStorage contents, or form values.

**Rate limiting:** per-IP sliding window — max 60 reports / 60 s. Exceeding returns `429` without storing. Dedup via pattern hash means a viral bug still produces usable data at this limit.

**Auth:** endpoint is unauthenticated (logged-out users' crashes still matter). CORS allows `https://gen.nomadkaraoke.com` (already configured).

**Transport:** `fetch(..., { keepalive: true, method: 'POST' })` so reports survive navigation. Fire-and-forget — we never block the UI on the report.

**Auto-send policy:** `CrashReport` auto-sends once on mount (to catch the bug in the wild without user action), then shows a "Report sent ✓" indicator. User can tap "Send again" to resend with notes.

**Sourcemaps:** deferred to a follow-up PR. This plan stores minified stacks + build SHA; we can resolve later via the artifact on Cloudflare. Noted as a follow-up in Task 14.

---

## Tasks

### Task 1: Backend — frontend ingestion pure function

**Files:**
- Create: `backend/services/error_monitor/frontend_ingestion.py`
- Create: `backend/tests/unit/services/error_monitor/test_frontend_ingestion.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/unit/services/error_monitor/test_frontend_ingestion.py`:

```python
"""Unit tests for frontend error ingestion."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from backend.services.error_monitor.frontend_ingestion import (
    FrontendErrorReport,
    build_pattern_data,
    sanitize_url,
)


def test_sanitize_url_strips_query_and_fragment():
    assert sanitize_url("https://gen.nomadkaraoke.com/en/app/jobs/?token=abc#/xyz/review") == "https://gen.nomadkaraoke.com/en/app/jobs/"


def test_sanitize_url_keeps_path_and_preserves_hash_route_prefix():
    # For our hash-routed pages we want #/ retained as a coarse indicator, but
    # the job id inside it is sensitive-ish — drop the fragment entirely.
    assert sanitize_url("https://gen.nomadkaraoke.com/en/app/jobs/#/abcdef/review") == "https://gen.nomadkaraoke.com/en/app/jobs/"


def test_sanitize_url_handles_bad_input():
    assert sanitize_url("") == ""
    assert sanitize_url("not a url") == "not a url"[:512]


def test_build_pattern_data_uses_stack_when_present():
    report = FrontendErrorReport(
        message="TypeError: Cannot read properties of null",
        stack="TypeError: Cannot read properties of null\n  at foo (app.js:1:2)\n  at bar (app.js:3:4)",
        url="https://gen.nomadkaraoke.com/en/app/jobs/",
        user_agent="Mozilla/5.0 (Android 14; Mobile; rv:138.0) Gecko/138.0 Firefox/138.0",
        release="abc1234",
        user_email=None,
        viewport={"w": 412, "h": 915},
        locale="en",
        extra=None,
    )

    data = build_pattern_data(report, now=datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc))

    assert data.service == "frontend"
    assert data.resource_type == "browser"
    # normalized_message is what was hashed — stack-based so same message from
    # different URLs/ids collapses.
    assert "TypeError" in data.normalized_message
    assert data.sample_message.startswith("TypeError: Cannot read properties of null")
    assert data.count == 1
    # pattern_id is deterministic for same stack
    data2 = build_pattern_data(report, now=datetime(2026, 4, 20, 9, 0, tzinfo=timezone.utc))
    assert data.pattern_id == data2.pattern_id


def test_build_pattern_data_falls_back_to_message_when_no_stack():
    report = FrontendErrorReport(
        message="Network request failed",
        stack=None,
        url="https://gen.nomadkaraoke.com/en/app/",
        user_agent="ua",
        release="abc",
        user_email="x@y.com",
        viewport=None,
        locale="en",
        extra=None,
    )
    data = build_pattern_data(report, now=datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc))
    assert "Network request failed" in data.normalized_message


def test_different_errors_produce_different_pattern_ids():
    a = FrontendErrorReport(
        message="A", stack="A\n  at foo", url="", user_agent="", release="", user_email=None,
        viewport=None, locale="en", extra=None,
    )
    b = FrontendErrorReport(
        message="B", stack="B\n  at bar", url="", user_agent="", release="", user_email=None,
        viewport=None, locale="en", extra=None,
    )
    assert build_pattern_data(a, now=datetime.now(tz=timezone.utc)).pattern_id != build_pattern_data(b, now=datetime.now(tz=timezone.utc)).pattern_id
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest backend/tests/unit/services/error_monitor/test_frontend_ingestion.py -v
```
Expected: FAIL with `ModuleNotFoundError: backend.services.error_monitor.frontend_ingestion`.

- [ ] **Step 3: Write implementation**

Create `backend/services/error_monitor/frontend_ingestion.py`:

```python
"""Frontend error ingestion helpers.

Converts an inbound browser crash report into a ``PatternData`` suitable for
the shared ``ErrorPatternsAdapter``. The adapter + existing error-monitor
Cloud Run Job handle all alerting / Discord plumbing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from backend.services.error_monitor.firestore_adapter import PatternData
from backend.services.error_monitor.normalizer import (
    compute_pattern_hash,
    normalize_message,
)

MAX_SAMPLE_MESSAGE_CHARS = 4000
MAX_URL_CHARS = 512


@dataclass
class FrontendErrorReport:
    """In-memory representation of an inbound crash report."""

    message: str
    stack: str | None
    url: str
    user_agent: str
    release: str
    user_email: str | None
    viewport: dict | None
    locale: str
    extra: dict | None


def sanitize_url(url: str) -> str:
    """Strip query and fragment from a URL; cap length; tolerate junk."""
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        if not parts.scheme:
            return url[:MAX_URL_CHARS]
        cleaned = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        return cleaned[:MAX_URL_CHARS]
    except ValueError:
        return url[:MAX_URL_CHARS]


def _stack_for_hashing(report: FrontendErrorReport) -> str:
    """Pick the most stable signal we have for pattern dedup.

    Prefer the stack (stable across invocations) over the message (sometimes
    has interpolated values). Falls back to message if no stack.
    """
    if report.stack:
        return report.stack
    return report.message


def build_pattern_data(
    report: FrontendErrorReport, now: datetime | None = None
) -> PatternData:
    """Convert an inbound report to a PatternData ready for upsert."""
    if now is None:
        now = datetime.now(tz=timezone.utc)

    raw = _stack_for_hashing(report)
    normalized = normalize_message(raw)
    pattern_id = compute_pattern_hash("frontend", normalized)

    # sample_message is human-readable context. Keep the error message plus a
    # trimmed stack + sanitized URL so the Discord alert is self-contained.
    sample_parts: list[str] = []
    if report.message:
        sample_parts.append(report.message.strip())
    if report.stack and report.stack.strip() != (report.message or "").strip():
        sample_parts.append(report.stack.strip())
    clean_url = sanitize_url(report.url)
    if clean_url:
        sample_parts.append(f"URL: {clean_url}")
    if report.user_agent:
        sample_parts.append(f"UA: {report.user_agent[:200]}")
    if report.release:
        sample_parts.append(f"Build: {report.release}")
    sample_message = "\n".join(sample_parts)[:MAX_SAMPLE_MESSAGE_CHARS]

    return PatternData(
        pattern_id=pattern_id,
        service="frontend",
        resource_type="browser",
        normalized_message=normalized,
        sample_message=sample_message,
        count=1,
        timestamp=now,
    )


class RateLimiter:
    """In-memory sliding-window limiter. One instance per process is enough —
    this runs inside Cloud Run which scales to multiple instances, so the
    effective limit is (per_ip_per_minute * num_instances). That's fine for our
    threat model (non-malicious browsers reporting their own crashes).
    """

    def __init__(self, max_per_minute: int = 60) -> None:
        self._max = max_per_minute
        self._hits: dict[str, list[float]] = {}

    def allow(self, ip: str, now_ts: float) -> bool:
        cutoff = now_ts - 60.0
        hits = [t for t in self._hits.get(ip, []) if t >= cutoff]
        if len(hits) >= self._max:
            self._hits[ip] = hits
            return False
        hits.append(now_ts)
        self._hits[ip] = hits
        # simple cleanup: if the map gets huge, drop stale keys
        if len(self._hits) > 10_000:
            self._hits = {
                k: [t for t in v if t >= cutoff]
                for k, v in self._hits.items()
                if any(t >= cutoff for t in v)
            }
        return True
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest backend/tests/unit/services/error_monitor/test_frontend_ingestion.py -v
```
Expected: all tests pass.

- [ ] **Step 5: Commit**

```
git add backend/services/error_monitor/frontend_ingestion.py backend/tests/unit/services/error_monitor/test_frontend_ingestion.py
git commit -m "feat(errors): add frontend error ingestion helper reusing pattern adapter"
```

---

### Task 2: Backend — `POST /api/client-errors` endpoint

**Files:**
- Create: `backend/api/routes/client_errors.py`
- Create: `backend/tests/api/test_client_errors.py`
- Modify: `backend/main.py:167` (add `client_errors.router` next to other routers)

- [ ] **Step 1: Write failing tests**

Create `backend/tests/api/test_client_errors.py`:

```python
"""API tests for POST /api/client-errors."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.client_errors import router as client_errors_router


@pytest.fixture
def client(monkeypatch):
    # reset the module-level limiter between tests
    import backend.api.routes.client_errors as mod
    mod._limiter = mod.RateLimiter(max_per_minute=60)

    fake_adapter = MagicMock()
    fake_adapter.upsert_pattern.return_value = MagicMock(
        pattern_id="deadbeef", is_new=True, previous_status=None
    )
    monkeypatch.setattr(mod, "_get_adapter", lambda: fake_adapter)

    app = FastAPI()
    app.include_router(client_errors_router, prefix="/api")
    client = TestClient(app)
    client.fake_adapter = fake_adapter  # type: ignore[attr-defined]
    return client


def _payload(**overrides):
    base = {
        "message": "TypeError: Cannot read properties of null",
        "stack": "TypeError: Cannot read properties of null\n  at x (app.js:1:2)",
        "url": "https://gen.nomadkaraoke.com/en/app/jobs/#/abc/review",
        "user_agent": "Mozilla/5.0 (Android 14; Mobile; rv:138.0) Gecko/138.0 Firefox/138.0",
        "release": "abc1234",
        "user_email": None,
        "viewport": {"w": 412, "h": 915},
        "locale": "en",
        "source": "window.onerror",
    }
    base.update(overrides)
    return base


def test_accepts_valid_report_and_upserts(client):
    resp = client.post("/api/client-errors", json=_payload())
    assert resp.status_code == 202
    assert resp.json()["pattern_id"] == "deadbeef"
    client.fake_adapter.upsert_pattern.assert_called_once()
    (pd,), _ = client.fake_adapter.upsert_pattern.call_args
    assert pd.service == "frontend"
    assert pd.resource_type == "browser"


def test_rejects_missing_message(client):
    resp = client.post("/api/client-errors", json=_payload(message=""))
    assert resp.status_code == 422


def test_caps_oversized_payload(client):
    huge = "x" * 200_000
    resp = client.post("/api/client-errors", json=_payload(stack=huge))
    # server accepts but truncates — sample_message must be <= MAX
    assert resp.status_code == 202
    (pd,), _ = client.fake_adapter.upsert_pattern.call_args
    from backend.services.error_monitor.frontend_ingestion import (
        MAX_SAMPLE_MESSAGE_CHARS,
    )
    assert len(pd.sample_message) <= MAX_SAMPLE_MESSAGE_CHARS


def test_rate_limit_returns_429(client, monkeypatch):
    import backend.api.routes.client_errors as mod
    mod._limiter = mod.RateLimiter(max_per_minute=2)
    assert client.post("/api/client-errors", json=_payload()).status_code == 202
    assert client.post("/api/client-errors", json=_payload()).status_code == 202
    r3 = client.post("/api/client-errors", json=_payload())
    assert r3.status_code == 429


def test_strips_sensitive_query_string(client):
    resp = client.post(
        "/api/client-errors",
        json=_payload(url="https://gen.nomadkaraoke.com/en/app/jobs/?token=SECRET#/abc/review"),
    )
    assert resp.status_code == 202
    (pd,), _ = client.fake_adapter.upsert_pattern.call_args
    assert "SECRET" not in pd.sample_message
    assert "token=" not in pd.sample_message
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest backend/tests/api/test_client_errors.py -v
```
Expected: FAIL with import error for `backend.api.routes.client_errors`.

- [ ] **Step 3: Write endpoint implementation**

Create `backend/api/routes/client_errors.py`:

```python
"""POST /api/client-errors — ingest frontend crash reports.

Unauthenticated (logged-out users' crashes still matter). Deduped and alerted
via the existing error-monitor pipeline — see
``backend/services/error_monitor``.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field, field_validator

from backend.services.error_monitor.firestore_adapter import ErrorPatternsAdapter
from backend.services.error_monitor.frontend_ingestion import (
    FrontendErrorReport,
    RateLimiter,
    build_pattern_data,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/client-errors", tags=["client-errors"])

_limiter = RateLimiter(max_per_minute=60)
_adapter_singleton: ErrorPatternsAdapter | None = None


def _get_adapter() -> ErrorPatternsAdapter:
    global _adapter_singleton
    if _adapter_singleton is None:
        _adapter_singleton = ErrorPatternsAdapter()
    return _adapter_singleton


class ClientErrorPayload(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    stack: Optional[str] = Field(None, max_length=100_000)
    url: str = Field("", max_length=2048)
    user_agent: str = Field("", max_length=1024)
    release: str = Field("", max_length=64)
    user_email: Optional[str] = Field(None, max_length=320)
    viewport: Optional[dict] = None
    locale: str = Field("en", max_length=10)
    source: str = Field("unknown", max_length=64)
    # optional free-form extra for debugging. Capped server-side.
    extra: Optional[dict] = None

    @field_validator("message", "url", "user_agent", "locale", "source")
    @classmethod
    def strip(cls, v: str) -> str:
        return (v or "").strip()


class ClientErrorResponse(BaseModel):
    pattern_id: str
    is_new: bool


@router.post("", response_model=ClientErrorResponse, status_code=202)
def report_client_error(payload: ClientErrorPayload, request: Request) -> ClientErrorResponse:
    client_ip = request.client.host if request.client else "unknown"
    if not _limiter.allow(client_ip, time.monotonic()):
        raise HTTPException(status_code=429, detail="too many reports")

    report = FrontendErrorReport(
        message=payload.message,
        stack=payload.stack,
        url=payload.url,
        user_agent=payload.user_agent,
        release=payload.release,
        user_email=payload.user_email,
        viewport=payload.viewport,
        locale=payload.locale,
        extra=payload.extra,
    )
    pattern_data = build_pattern_data(report)

    try:
        result = _get_adapter().upsert_pattern(pattern_data)
    except Exception:  # pragma: no cover - Firestore transient errors
        logger.exception("Failed to upsert frontend error pattern")
        raise HTTPException(status_code=503, detail="storage unavailable")

    logger.info(
        "frontend_crash_reported pattern_id=%s is_new=%s source=%s",
        result.pattern_id,
        result.is_new,
        payload.source,
    )
    return ClientErrorResponse(pattern_id=result.pattern_id, is_new=result.is_new)
```

- [ ] **Step 4: Wire the router in `main.py`**

Edit `backend/main.py` around line 167 (next to the other `include_router` calls):

```python
from backend.api.routes import client_errors  # existing imports block
...
app.include_router(client_errors.router, prefix="/api")  # Frontend crash reports
```

- [ ] **Step 5: Run tests to verify they pass**

```
pytest backend/tests/api/test_client_errors.py backend/tests/unit/services/error_monitor/test_frontend_ingestion.py -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```
git add backend/api/routes/client_errors.py backend/tests/api/test_client_errors.py backend/main.py
git commit -m "feat(api): POST /api/client-errors endpoint for frontend crash reports"
```

---

### Task 3: Frontend — crash reporter module

**Files:**
- Create: `frontend/lib/crash-reporter.ts`
- Create: `frontend/__tests__/crash-reporter.test.ts`

- [ ] **Step 1: Write failing tests**

Create `frontend/__tests__/crash-reporter.test.ts`:

```typescript
/**
 * @jest-environment jsdom
 */
import { collectContext, reportClientError, __resetForTest } from '@/lib/crash-reporter'

describe('crash-reporter.collectContext', () => {
  beforeEach(() => {
    __resetForTest()
  })

  it('sanitizes url by dropping query + fragment', () => {
    const ctx = collectContext({
      href: 'https://gen.nomadkaraoke.com/en/app/jobs/?token=abc#/x/review',
      userAgent: 'Mozilla/5.0 Firefox/138.0',
    })
    expect(ctx.url).toBe('https://gen.nomadkaraoke.com/en/app/jobs/')
  })

  it('captures viewport dimensions', () => {
    const ctx = collectContext({
      href: 'https://x.example/',
      userAgent: 'ua',
      innerWidth: 412,
      innerHeight: 915,
    })
    expect(ctx.viewport).toEqual({ w: 412, h: 915 })
  })
})

describe('crash-reporter.reportClientError', () => {
  const fetchMock = jest.fn()

  beforeEach(() => {
    __resetForTest()
    fetchMock.mockReset()
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ pattern_id: 'p', is_new: true }) })
    ;(global as any).fetch = fetchMock
  })

  it('POSTs sanitized payload to /api/client-errors', async () => {
    await reportClientError({
      error: new TypeError('boom'),
      source: 'test',
      context: { href: 'https://gen.nomadkaraoke.com/en/app/', userAgent: 'ua' },
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toMatch(/\/api\/client-errors$/)
    expect(init.method).toBe('POST')
    expect(init.keepalive).toBe(true)
    const body = JSON.parse(init.body)
    expect(body.message).toBe('TypeError: boom')
    expect(body.stack).toContain('TypeError')
    expect(body.source).toBe('test')
    expect(body.url).toBe('https://gen.nomadkaraoke.com/en/app/')
  })

  it('dedupes identical errors within the window', async () => {
    const err = new Error('same')
    await reportClientError({ error: err, source: 'a', context: { href: '', userAgent: '' } })
    await reportClientError({ error: err, source: 'a', context: { href: '', userAgent: '' } })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('does not throw on fetch failure', async () => {
    fetchMock.mockRejectedValueOnce(new Error('offline'))
    await expect(
      reportClientError({
        error: new Error('x'),
        source: 'test',
        context: { href: '', userAgent: '' },
      })
    ).resolves.toBeUndefined()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```
cd frontend && npx jest __tests__/crash-reporter.test.ts
```
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the module**

Create `frontend/lib/crash-reporter.ts`:

```typescript
/**
 * Frontend crash reporter.
 *
 * - Call `reportClientError(...)` from React error boundaries and global listeners.
 * - Dedupes identical errors within a short window to avoid spamming the API.
 * - Never throws; any failure to report is swallowed and logged to the console.
 */

import { API_BASE_URL } from '@/lib/api-config'

export interface ClientErrorContext {
  href: string
  userAgent: string
  innerWidth?: number
  innerHeight?: number
  locale?: string
  release?: string
  userEmail?: string | null
}

export interface CollectedContext {
  url: string
  userAgent: string
  locale: string
  release: string
  viewport: { w: number; h: number } | undefined
  userEmail: string | null
}

export interface ReportArgs {
  error: unknown
  /** Where the error was caught. e.g. 'window.onerror', 'error.tsx', 'CrashReportBoundary' */
  source: string
  context: ClientErrorContext
  extra?: Record<string, unknown>
}

const DEDUP_WINDOW_MS = 5_000
const recentSignatures = new Map<string, number>()

export function __resetForTest() {
  recentSignatures.clear()
}

function sanitizeUrl(href: string): string {
  if (!href) return ''
  try {
    const u = new URL(href)
    u.search = ''
    u.hash = ''
    return u.toString()
  } catch {
    return href.slice(0, 512)
  }
}

export function collectContext(ctx: ClientErrorContext): CollectedContext {
  const viewport =
    typeof ctx.innerWidth === 'number' && typeof ctx.innerHeight === 'number'
      ? { w: ctx.innerWidth, h: ctx.innerHeight }
      : undefined
  return {
    url: sanitizeUrl(ctx.href),
    userAgent: ctx.userAgent || '',
    locale: ctx.locale || 'en',
    release: ctx.release || (process.env.NEXT_PUBLIC_BUILD_SHA as string) || '',
    viewport,
    userEmail: ctx.userEmail ?? null,
  }
}

function normalizeError(e: unknown): { message: string; stack: string | null } {
  if (e instanceof Error) {
    return {
      message: `${e.name}: ${e.message}`,
      stack: e.stack ?? null,
    }
  }
  if (typeof e === 'string') {
    return { message: e, stack: null }
  }
  try {
    return { message: JSON.stringify(e).slice(0, 4000), stack: null }
  } catch {
    return { message: String(e).slice(0, 4000), stack: null }
  }
}

function signatureFor(message: string, stack: string | null, source: string): string {
  return `${source}|${stack?.slice(0, 500) ?? message.slice(0, 500)}`
}

export async function reportClientError(args: ReportArgs): Promise<void> {
  try {
    const { message, stack } = normalizeError(args.error)
    const sig = signatureFor(message, stack, args.source)
    const now = Date.now()
    const last = recentSignatures.get(sig)
    if (last && now - last < DEDUP_WINDOW_MS) return
    recentSignatures.set(sig, now)
    // simple cleanup
    if (recentSignatures.size > 200) {
      for (const [k, t] of recentSignatures) {
        if (now - t > DEDUP_WINDOW_MS * 4) recentSignatures.delete(k)
      }
    }

    const ctx = collectContext(args.context)
    const body = {
      message,
      stack,
      url: ctx.url,
      user_agent: ctx.userAgent,
      release: ctx.release,
      user_email: ctx.userEmail,
      viewport: ctx.viewport ?? null,
      locale: ctx.locale,
      source: args.source,
      extra: args.extra ?? null,
    }

    await fetch(`${API_BASE_URL}/api/client-errors`, {
      method: 'POST',
      keepalive: true,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).catch(() => {
      /* swallow — never surface reporter errors to callers */
    })
  } catch (err) {
    if (typeof console !== 'undefined') {
      console.warn('[crash-reporter] failed:', err)
    }
  }
}
```

Confirm `API_BASE_URL` exists in `frontend/lib/api-config.ts`; if not, import it from wherever the existing `api.ts` pulls its base URL. Do **not** add a second config source.

- [ ] **Step 4: Run tests to verify they pass**

```
cd frontend && npx jest __tests__/crash-reporter.test.ts
```
Expected: all pass.

- [ ] **Step 5: Commit**

```
git add frontend/lib/crash-reporter.ts frontend/__tests__/crash-reporter.test.ts
git commit -m "feat(frontend): add crash-reporter module with dedup + sanitization"
```

---

### Task 4: Frontend — window error listeners

**Files:**
- Create: `frontend/lib/client-error-setup.ts`
- Create: `frontend/components/ClientErrorInit.tsx`
- Modify: `frontend/app/[locale]/layout.tsx` (mount `<ClientErrorInit />` once)

- [ ] **Step 1: Write the setup module**

Create `frontend/lib/client-error-setup.ts`:

```typescript
'use client'

import { reportClientError } from '@/lib/crash-reporter'

let installed = false

function buildContext(userEmail: string | null, locale: string) {
  return {
    href: typeof window !== 'undefined' ? window.location.href : '',
    userAgent: typeof navigator !== 'undefined' ? navigator.userAgent : '',
    innerWidth: typeof window !== 'undefined' ? window.innerWidth : undefined,
    innerHeight: typeof window !== 'undefined' ? window.innerHeight : undefined,
    locale,
    userEmail,
  }
}

export function installGlobalErrorHandlers(getUserEmail: () => string | null, locale: string) {
  if (installed) return
  if (typeof window === 'undefined') return
  installed = true

  window.addEventListener('error', (event) => {
    const err = event.error ?? new Error(event.message || 'Unknown window error')
    void reportClientError({
      error: err,
      source: 'window.onerror',
      context: buildContext(getUserEmail(), locale),
      extra: {
        filename: event.filename,
        lineno: event.lineno,
        colno: event.colno,
      },
    })
  })

  window.addEventListener('unhandledrejection', (event) => {
    const err = event.reason instanceof Error ? event.reason : new Error(String(event.reason))
    void reportClientError({
      error: err,
      source: 'unhandledrejection',
      context: buildContext(getUserEmail(), locale),
    })
  })
}
```

- [ ] **Step 2: Create the mount component**

Create `frontend/components/ClientErrorInit.tsx`:

```tsx
'use client'

import { useEffect } from 'react'
import { useAuth } from '@/lib/auth'
import { installGlobalErrorHandlers } from '@/lib/client-error-setup'

interface Props {
  locale: string
}

export default function ClientErrorInit({ locale }: Props) {
  const user = useAuth((s) => s.user)
  useEffect(() => {
    installGlobalErrorHandlers(() => user?.email ?? null, locale)
  }, [locale, user])
  return null
}
```

- [ ] **Step 3: Mount in the locale layout**

Edit `frontend/app/[locale]/layout.tsx`. Add at the top of the imports:

```tsx
import ClientErrorInit from '@/components/ClientErrorInit'
```

And inside the root JSX tree of the layout (anywhere inside `<body>` / inside the NextIntlClientProvider), add:

```tsx
<ClientErrorInit locale={locale} />
```

(If the layout is async and reads `locale` via `params`, keep the existing await pattern; just pass `locale` as a prop.)

- [ ] **Step 4: Verify dev build mounts without errors**

```
cd frontend && npx next dev --port 3100 &
NEXT_PID=$!
sleep 6
curl -s http://localhost:3100/en/ -o /dev/null -w "%{http_code}\n"
kill $NEXT_PID
```
Expected: `200`.

- [ ] **Step 5: Commit**

```
git add frontend/lib/client-error-setup.ts frontend/components/ClientErrorInit.tsx frontend/app/[locale]/layout.tsx
git commit -m "feat(frontend): install global error + unhandled-rejection listeners"
```

---

### Task 5: Frontend — CrashReport UI component

**Files:**
- Create: `frontend/components/CrashReport.tsx`
- Create: `frontend/__tests__/CrashReport.test.tsx`
- Modify: `frontend/messages/en.json` (new `crashReport` namespace)

- [ ] **Step 1: Add English strings**

Append to `frontend/messages/en.json` under a new top-level `crashReport` key:

```json
"crashReport": {
  "title": "Something went wrong",
  "subtitle": "The app hit an unexpected error. You can copy the details or go back to the dashboard.",
  "reportSent": "Crash report sent — we've been alerted.",
  "reportSending": "Sending crash report…",
  "reportFailed": "We couldn't send the crash report automatically. Please copy the details and message support.",
  "copyDebug": "Copy debug info",
  "copied": "Copied!",
  "sendReport": "Send crash report again",
  "retry": "Try again",
  "backToDashboard": "Back to dashboard",
  "toggleStack": "Show technical details",
  "hideStack": "Hide technical details"
}
```

- [ ] **Step 2: Write failing tests**

Create `frontend/__tests__/CrashReport.test.tsx`:

```tsx
/**
 * @jest-environment jsdom
 */
import React from 'react'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import messages from '@/messages/en.json'
import CrashReport from '@/components/CrashReport'

jest.mock('@/lib/crash-reporter', () => ({
  reportClientError: jest.fn().mockResolvedValue(undefined),
}))
import { reportClientError } from '@/lib/crash-reporter'

function wrap(ui: React.ReactElement) {
  return (
    <NextIntlClientProvider locale="en" messages={messages as any}>
      {ui}
    </NextIntlClientProvider>
  )
}

describe('CrashReport', () => {
  beforeEach(() => {
    ;(reportClientError as jest.Mock).mockClear()
    ;(reportClientError as jest.Mock).mockResolvedValue(undefined)
  })

  it('renders title and auto-sends on mount', async () => {
    const err = new Error('boom')
    render(wrap(<CrashReport error={err} source="test" />))
    expect(screen.getByText(/Something went wrong/i)).toBeInTheDocument()
    await waitFor(() => expect(reportClientError).toHaveBeenCalled())
    const [[args]] = (reportClientError as jest.Mock).mock.calls
    expect(args.source).toBe('test')
  })

  it('shows a success indicator after successful send', async () => {
    render(wrap(<CrashReport error={new Error('x')} source="test" />))
    expect(await screen.findByText(/Crash report sent/i)).toBeInTheDocument()
  })

  it('copies debug info to clipboard on click', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    render(wrap(<CrashReport error={new Error('copy me')} source="test" />))
    fireEvent.click(screen.getByRole('button', { name: /Copy debug info/i }))
    await waitFor(() => expect(writeText).toHaveBeenCalled())
    expect(writeText.mock.calls[0][0]).toContain('copy me')
  })

  it('calls onReset when Try again is clicked', () => {
    const reset = jest.fn()
    render(wrap(<CrashReport error={new Error('x')} source="test" onReset={reset} />))
    fireEvent.click(screen.getByRole('button', { name: /Try again/i }))
    expect(reset).toHaveBeenCalled()
  })
})
```

- [ ] **Step 3: Run test to verify it fails**

```
cd frontend && npx jest __tests__/CrashReport.test.tsx
```
Expected: FAIL — module not found.

- [ ] **Step 4: Implement the component**

Create `frontend/components/CrashReport.tsx`:

```tsx
'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { AlertCircle, Check, Clipboard, RefreshCw, Send } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { useAuth } from '@/lib/auth'
import { reportClientError } from '@/lib/crash-reporter'

type SendState = 'sending' | 'sent' | 'failed' | 'idle'

interface Props {
  error: unknown
  source: string
  /** Optional Next.js error digest (present for errors in server components) */
  digest?: string
  /** Optional reset handler (present for Next.js error.tsx boundaries) */
  onReset?: () => void
  /** Optional "back" URL for the in-boundary version */
  backHref?: string
}

function errorText(error: unknown): { name: string; message: string; stack: string } {
  if (error instanceof Error) {
    return { name: error.name, message: error.message, stack: error.stack ?? '' }
  }
  if (typeof error === 'string') return { name: 'Error', message: error, stack: '' }
  try {
    return { name: 'Error', message: JSON.stringify(error), stack: '' }
  } catch {
    return { name: 'Error', message: String(error), stack: '' }
  }
}

export default function CrashReport({ error, source, digest, onReset, backHref }: Props) {
  const t = useTranslations('crashReport')
  const user = useAuth((s) => s.user)
  const [state, setState] = useState<SendState>('idle')
  const [copied, setCopied] = useState(false)
  const [showStack, setShowStack] = useState(false)

  const info = useMemo(() => errorText(error), [error])

  const debugText = useMemo(() => {
    const lines = [
      `${info.name}: ${info.message}`,
      digest ? `Digest: ${digest}` : null,
      `Source: ${source}`,
      `URL: ${typeof window !== 'undefined' ? window.location.href : ''}`,
      `UA: ${typeof navigator !== 'undefined' ? navigator.userAgent : ''}`,
      `Build: ${process.env.NEXT_PUBLIC_BUILD_SHA || '(dev)'}`,
      `When: ${new Date().toISOString()}`,
      '',
      info.stack || '(no stack)',
    ].filter(Boolean) as string[]
    return lines.join('\n')
  }, [info, source, digest])

  async function send() {
    setState('sending')
    try {
      await reportClientError({
        error,
        source,
        context: {
          href: typeof window !== 'undefined' ? window.location.href : '',
          userAgent: typeof navigator !== 'undefined' ? navigator.userAgent : '',
          innerWidth: typeof window !== 'undefined' ? window.innerWidth : undefined,
          innerHeight: typeof window !== 'undefined' ? window.innerHeight : undefined,
          userEmail: user?.email ?? null,
        },
        extra: { digest, interactive: state === 'idle' ? 'auto' : 'manual' },
      })
      setState('sent')
    } catch {
      setState('failed')
    }
  }

  useEffect(() => {
    // auto-send once on mount
    void send()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function copy() {
    try {
      await navigator.clipboard.writeText(debugText)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      /* ignore */
    }
  }

  return (
    <div className="flex min-h-[60vh] items-center justify-center p-4">
      <div className="w-full max-w-xl rounded-xl border bg-card p-6 shadow-sm">
        <div className="flex items-start gap-3">
          <AlertCircle className="mt-0.5 h-6 w-6 shrink-0 text-destructive" />
          <div className="flex-1 space-y-1">
            <h1 className="text-lg font-semibold">{t('title')}</h1>
            <p className="text-sm text-muted-foreground">{t('subtitle')}</p>
          </div>
        </div>

        <div
          className="mt-4 rounded-md border bg-muted/40 p-3 text-xs font-mono break-words"
          data-testid="crash-report-message"
        >
          <strong>{info.name}:</strong> {info.message}
        </div>

        <div className="mt-3">
          <button
            type="button"
            className="text-xs text-muted-foreground underline"
            onClick={() => setShowStack((v) => !v)}
          >
            {showStack ? t('hideStack') : t('toggleStack')}
          </button>
          {showStack && (
            <pre className="mt-2 max-h-64 overflow-auto rounded-md border bg-muted/40 p-3 text-[10px] leading-tight">
              {debugText}
            </pre>
          )}
        </div>

        <div
          className="mt-4 text-xs"
          role="status"
          data-testid="crash-report-status"
        >
          {state === 'sending' && <span>{t('reportSending')}</span>}
          {state === 'sent' && (
            <span className="inline-flex items-center gap-1 text-emerald-600">
              <Check className="h-3.5 w-3.5" />
              {t('reportSent')}
            </span>
          )}
          {state === 'failed' && (
            <span className="text-destructive">{t('reportFailed')}</span>
          )}
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={copy} className="min-h-[40px]">
            <Clipboard className="mr-1.5 h-4 w-4" />
            {copied ? t('copied') : t('copyDebug')}
          </Button>
          <Button variant="outline" size="sm" onClick={send} disabled={state === 'sending'} className="min-h-[40px]">
            <Send className="mr-1.5 h-4 w-4" />
            {t('sendReport')}
          </Button>
          {onReset && (
            <Button size="sm" onClick={onReset} className="min-h-[40px]">
              <RefreshCw className="mr-1.5 h-4 w-4" />
              {t('retry')}
            </Button>
          )}
          {backHref && (
            <Button variant="ghost" size="sm" asChild className="min-h-[40px]">
              <a href={backHref}>{t('backToDashboard')}</a>
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 5: Run tests to verify they pass**

```
cd frontend && npx jest __tests__/CrashReport.test.tsx
```
Expected: all pass.

- [ ] **Step 6: Translate all locales**

```
python scripts/translate.py --messages-dir frontend/messages --target all
```

This uses the GCS cache — only new `crashReport.*` strings hit Gemini.

- [ ] **Step 7: Commit**

```
git add frontend/components/CrashReport.tsx frontend/__tests__/CrashReport.test.tsx frontend/messages
git commit -m "feat(frontend): CrashReport UI component with auto-send + copy debug info"
```

---

### Task 6: Frontend — Next.js error boundary files

**Files:**
- Create: `frontend/app/[locale]/error.tsx`
- Create: `frontend/app/[locale]/global-error.tsx`

- [ ] **Step 1: Create `error.tsx`**

Create `frontend/app/[locale]/error.tsx`:

```tsx
'use client'

import { useEffect } from 'react'
import CrashReport from '@/components/CrashReport'

interface Props {
  error: Error & { digest?: string }
  reset: () => void
}

export default function LocaleError({ error, reset }: Props) {
  useEffect(() => {
    // The CrashReport component also auto-sends on mount; this log is just for
    // local dev visibility.
    if (typeof console !== 'undefined') {
      console.error('[error.tsx]', error)
    }
  }, [error])

  return <CrashReport error={error} source="error.tsx" digest={error.digest} onReset={reset} />
}
```

- [ ] **Step 2: Create `global-error.tsx`**

Create `frontend/app/[locale]/global-error.tsx`. This file is Next.js's last-resort boundary and **must** render its own `<html>` / `<body>` — translations may not be available here, so we use plain English.

```tsx
'use client'

import { useEffect } from 'react'
import { reportClientError } from '@/lib/crash-reporter'

interface Props {
  error: Error & { digest?: string }
  reset: () => void
}

export default function GlobalError({ error, reset }: Props) {
  useEffect(() => {
    void reportClientError({
      error,
      source: 'global-error.tsx',
      context: {
        href: typeof window !== 'undefined' ? window.location.href : '',
        userAgent: typeof navigator !== 'undefined' ? navigator.userAgent : '',
        innerWidth: typeof window !== 'undefined' ? window.innerWidth : undefined,
        innerHeight: typeof window !== 'undefined' ? window.innerHeight : undefined,
        userEmail: null,
      },
      extra: { digest: error.digest },
    })
  }, [error])

  return (
    <html lang="en">
      <body>
        <div style={{ minHeight: '60vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16, fontFamily: 'system-ui, sans-serif' }}>
          <div style={{ maxWidth: 560 }}>
            <h1 style={{ fontSize: 20, marginBottom: 8 }}>Something went wrong</h1>
            <p style={{ color: '#666', fontSize: 14 }}>
              The app hit an unexpected error. We've been notified automatically.
            </p>
            <pre style={{ marginTop: 12, padding: 12, background: '#f4f4f4', border: '1px solid #e5e5e5', borderRadius: 6, fontSize: 11, overflow: 'auto' }}>
              {error.name}: {error.message}
              {error.digest ? `\nDigest: ${error.digest}` : ''}
            </pre>
            <button onClick={reset} style={{ marginTop: 12, padding: '8px 16px', border: '1px solid #ddd', borderRadius: 6, background: '#fff', cursor: 'pointer' }}>
              Try again
            </button>
          </div>
        </div>
      </body>
    </html>
  )
}
```

- [ ] **Step 3: Verify build**

```
cd frontend && npx next build
```
Expected: build succeeds; both new files are listed under `/[locale]` in the output.

- [ ] **Step 4: Commit**

```
git add frontend/app/[locale]/error.tsx frontend/app/[locale]/global-error.tsx
git commit -m "feat(frontend): Next.js error boundaries render CrashReport with auto-send"
```

---

### Task 7: Frontend — Component-level CrashReportBoundary

**Files:**
- Create: `frontend/components/CrashReportBoundary.tsx`
- Create: `frontend/__tests__/CrashReportBoundary.test.tsx`
- Modify: `frontend/app/[locale]/app/jobs/[[...slug]]/client.tsx` (wrap `LyricsReviewWrapper` + `InstrumentalReviewWrapper`)

- [ ] **Step 1: Write failing tests**

Create `frontend/__tests__/CrashReportBoundary.test.tsx`:

```tsx
/**
 * @jest-environment jsdom
 */
import React from 'react'
import { render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import messages from '@/messages/en.json'
import CrashReportBoundary from '@/components/CrashReportBoundary'

jest.mock('@/lib/crash-reporter', () => ({
  reportClientError: jest.fn().mockResolvedValue(undefined),
}))

function Bomb(): React.ReactElement {
  throw new Error('KABOOM')
}

function wrap(ui: React.ReactElement) {
  return (
    <NextIntlClientProvider locale="en" messages={messages as any}>
      {ui}
    </NextIntlClientProvider>
  )
}

describe('CrashReportBoundary', () => {
  const origError = console.error
  beforeAll(() => {
    // Silence React's expected error noise in tests
    console.error = jest.fn()
  })
  afterAll(() => {
    console.error = origError
  })

  it('catches a child error and renders CrashReport', () => {
    render(wrap(
      <CrashReportBoundary source="test">
        <Bomb />
      </CrashReportBoundary>
    ))
    expect(screen.getByText(/Something went wrong/i)).toBeInTheDocument()
    expect(screen.getByTestId('crash-report-message').textContent).toContain('KABOOM')
  })

  it('renders children normally when no error', () => {
    render(wrap(
      <CrashReportBoundary source="test">
        <div>hello</div>
      </CrashReportBoundary>
    ))
    expect(screen.getByText('hello')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```
cd frontend && npx jest __tests__/CrashReportBoundary.test.tsx
```
Expected: FAIL.

- [ ] **Step 3: Implement the boundary**

Create `frontend/components/CrashReportBoundary.tsx`:

```tsx
'use client'

import React from 'react'
import CrashReport from './CrashReport'

interface Props {
  children: React.ReactNode
  source: string
  backHref?: string
}

interface State {
  error: Error | null
  key: number
}

export default class CrashReportBoundary extends React.Component<Props, State> {
  state: State = { error: null, key: 0 }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    if (typeof console !== 'undefined') {
      console.error('[CrashReportBoundary]', error, info.componentStack)
    }
  }

  reset = () => {
    this.setState((s) => ({ error: null, key: s.key + 1 }))
  }

  render() {
    if (this.state.error) {
      return (
        <CrashReport
          error={this.state.error}
          source={this.props.source}
          onReset={this.reset}
          backHref={this.props.backHref}
        />
      )
    }
    return <React.Fragment key={this.state.key}>{this.props.children}</React.Fragment>
  }
}
```

- [ ] **Step 4: Wrap review wrappers in `client.tsx`**

Edit `frontend/app/[locale]/app/jobs/[[...slug]]/client.tsx`.

Add import at top:

```tsx
import CrashReportBoundary from '@/components/CrashReportBoundary'
```

Find where `LyricsReviewWrapper` and `InstrumentalReviewWrapper` are rendered from `JobRouterClient` (look for the `accessState.status === "authorized"` / `"local_mode"` branches). Wrap each render site:

```tsx
{accessState.routeType === 'review' && (
  <CrashReportBoundary source="lyrics-review" backHref="/app">
    <LyricsReviewWrapper job={accessState.job} isLocalMode={...} />
  </CrashReportBoundary>
)}
{accessState.routeType === 'instrumental' && (
  <CrashReportBoundary source="instrumental-review" backHref="/app">
    <InstrumentalReviewWrapper job={accessState.job} isLocalMode={...} />
  </CrashReportBoundary>
)}
```

(Keep the existing conditional structure — just wrap, don't rewrite it.)

- [ ] **Step 5: Run tests to verify they pass**

```
cd frontend && npx jest __tests__/CrashReportBoundary.test.tsx
```
Expected: pass.

- [ ] **Step 6: Commit**

```
git add frontend/components/CrashReportBoundary.tsx frontend/__tests__/CrashReportBoundary.test.tsx frontend/app/[locale]/app/jobs/[[...slug]]/client.tsx
git commit -m "feat(frontend): wrap review wrappers in CrashReportBoundary for in-place error UI"
```

---

### Task 8: Version-aware cache busting

Users who crash on a stale bundle need to pick up the fix automatically. Three concerns:

1. **ChunkLoadError after deploy** — old HTML points to chunk URLs that no longer exist.
2. **Already-open tabs** — a user who crashed is still running pre-fix JS until they reload.
3. **Ambient staleness** — a tab left open for hours gets further behind each deploy.

**Files:**
- Create: `frontend/scripts/write-version.mjs`
- Create: `frontend/public/version.json` (gitignored — build emits it)
- Modify: `frontend/.gitignore` (add `public/version.json`)
- Modify: `frontend/package.json` (prebuild hook)
- Modify: `frontend/next.config.ts` (`env` block wires `NEXT_PUBLIC_BUILD_SHA`)
- Create: `frontend/lib/version-check.ts`
- Create: `frontend/__tests__/version-check.test.ts`
- Modify: `frontend/lib/client-error-setup.ts` (ChunkLoadError auto-reload + ambient poll)
- Modify: `frontend/components/CrashReport.tsx` (stale banner + Update now CTA)
- Modify: `frontend/messages/en.json` (new `crashReport.updateAvailable`, `crashReport.updateNow`)

- [ ] **Step 1: Build-time version emission**

Create `frontend/scripts/write-version.mjs`:

```javascript
#!/usr/bin/env node
/**
 * Emit public/version.json with build SHA + timestamp.
 *
 * Runs as `prebuild` before `next build`. The JSON file is served as a static
 * asset; the frontend fetches it at runtime to detect stale bundles.
 *
 * SHA sources (first match wins):
 *   1. CF_PAGES_COMMIT_SHA    (Cloudflare Pages)
 *   2. GITHUB_SHA             (GitHub Actions)
 *   3. git rev-parse HEAD     (local dev)
 *   4. "dev"                  (fallback)
 */
import { execSync } from 'node:child_process'
import { writeFileSync, mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

function gitSha() {
  try {
    return execSync('git rev-parse HEAD', { stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim()
  } catch {
    return null
  }
}

const sha = process.env.CF_PAGES_COMMIT_SHA || process.env.GITHUB_SHA || gitSha() || 'dev'
const payload = {
  build_sha: sha,
  built_at: new Date().toISOString(),
}

const outPath = resolve(__dirname, '..', 'public', 'version.json')
mkdirSync(dirname(outPath), { recursive: true })
writeFileSync(outPath, JSON.stringify(payload, null, 2) + '\n', 'utf8')
console.log(`[write-version] wrote ${outPath}: ${JSON.stringify(payload)}`)
```

Mark it executable (optional but tidy):

```
chmod +x frontend/scripts/write-version.mjs
```

- [ ] **Step 2: Wire the prebuild hook**

Edit `frontend/package.json`. Add to the `scripts` block (preserve existing scripts):

```json
"scripts": {
  "prebuild": "node scripts/write-version.mjs",
  ...
}
```

If a `prebuild` script already exists, chain with `&&`.

- [ ] **Step 3: Gitignore the generated file**

Append to `frontend/.gitignore`:

```
# Generated at build time by scripts/write-version.mjs
public/version.json
```

- [ ] **Step 4: Expose `NEXT_PUBLIC_BUILD_SHA` to the runtime bundle**

Edit `frontend/next.config.ts`. Inside the `nextConfig` object, add or extend the `env` property:

```ts
env: {
  NEXT_PUBLIC_BUILD_SHA:
    process.env.CF_PAGES_COMMIT_SHA ||
    process.env.GITHUB_SHA ||
    process.env.NEXT_PUBLIC_BUILD_SHA ||
    'dev',
},
```

Keep all existing `nextConfig` properties intact; only add this one.

- [ ] **Step 5: Verify the build emits version.json**

```
cd frontend && npm run build
cat public/version.json
```

Expected: JSON with a real `build_sha` (40-char git SHA in CI, or local HEAD).

- [ ] **Step 6: Write failing tests for version-check**

Create `frontend/__tests__/version-check.test.ts`:

```typescript
/**
 * @jest-environment jsdom
 */
import { isStale, hardReload, __resetForTest, __setBuildShaForTest } from '@/lib/version-check'

describe('version-check.isStale', () => {
  const fetchMock = jest.fn()
  beforeEach(() => {
    __resetForTest()
    __setBuildShaForTest('aaa')
    fetchMock.mockReset()
    ;(global as any).fetch = fetchMock
  })

  it('returns stale=false when SHAs match', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ build_sha: 'aaa' }) })
    const r = await isStale()
    expect(r.stale).toBe(false)
    expect(r.latestSha).toBe('aaa')
  })

  it('returns stale=true when SHAs differ', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ build_sha: 'bbb' }) })
    const r = await isStale()
    expect(r.stale).toBe(true)
    expect(r.latestSha).toBe('bbb')
  })

  it('fetches with cache: no-store', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ build_sha: 'aaa' }) })
    await isStale()
    const [, init] = fetchMock.mock.calls[0]
    expect(init.cache).toBe('no-store')
  })

  it('treats fetch failures as not-stale (fail-safe)', async () => {
    fetchMock.mockRejectedValue(new Error('offline'))
    const r = await isStale()
    expect(r.stale).toBe(false)
  })

  it('treats "dev" build_sha as not-stale (local dev)', async () => {
    __setBuildShaForTest('dev')
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ build_sha: 'bbb' }) })
    const r = await isStale()
    expect(r.stale).toBe(false)
  })
})

describe('version-check.hardReload circuit breaker', () => {
  const assignMock = jest.fn()
  let originalLocation: Location

  beforeEach(() => {
    __resetForTest()
    assignMock.mockReset()
    originalLocation = window.location
    // @ts-expect-error — overwriting readonly for test
    delete window.location
    // @ts-expect-error — mock location
    window.location = {
      pathname: '/en/app/jobs/',
      assign: assignMock,
      reload: jest.fn(),
    }
  })

  afterEach(() => {
    // @ts-expect-error — restore
    window.location = originalLocation
  })

  it('navigates with _v cache-buster on first call', () => {
    hardReload('bbb')
    expect(assignMock).toHaveBeenCalledTimes(1)
    expect(assignMock.mock.calls[0][0]).toContain('_v=bbb')
    expect(assignMock.mock.calls[0][0]).toContain('/en/app/jobs/')
  })

  it('refuses to reload twice within 60s (circuit breaker)', () => {
    hardReload('bbb')
    hardReload('ccc')
    expect(assignMock).toHaveBeenCalledTimes(1)
  })
})
```

- [ ] **Step 7: Run tests to verify they fail**

```
cd frontend && npx jest __tests__/version-check.test.ts
```
Expected: FAIL — module not found.

- [ ] **Step 8: Implement `version-check.ts`**

Create `frontend/lib/version-check.ts`:

```typescript
'use client'

/**
 * Version-awareness for cache-busting.
 *
 * - isStale()    — compares the compiled NEXT_PUBLIC_BUILD_SHA against /version.json.
 * - hardReload() — unregisters SWs, clears caches, navigates to url?_v=<sha>.
 *                  Includes a 60s circuit breaker to prevent reload loops.
 * - handleChunkLoadError() — pattern-matches common chunk errors and reloads.
 */

const STORAGE_KEY = 'karaoke_last_hard_reload'
const CIRCUIT_BREAKER_MS = 60_000

let _buildShaOverride: string | null = null

export function __resetForTest() {
  _buildShaOverride = null
  if (typeof sessionStorage !== 'undefined') {
    sessionStorage.removeItem(STORAGE_KEY)
  }
}

export function __setBuildShaForTest(sha: string) {
  _buildShaOverride = sha
}

function currentBuildSha(): string {
  if (_buildShaOverride !== null) return _buildShaOverride
  return (process.env.NEXT_PUBLIC_BUILD_SHA as string) || 'dev'
}

export interface StaleResult {
  stale: boolean
  latestSha: string
  currentSha: string
}

export async function isStale(): Promise<StaleResult> {
  const current = currentBuildSha()
  // 'dev' means local — never surface a stale warning in dev
  if (current === 'dev') {
    return { stale: false, latestSha: current, currentSha: current }
  }
  try {
    const res = await fetch('/version.json', { cache: 'no-store' })
    if (!res.ok) throw new Error(`status ${res.status}`)
    const data = (await res.json()) as { build_sha?: string }
    const latest = data?.build_sha ?? current
    return { stale: latest !== current, latestSha: latest, currentSha: current }
  } catch {
    // Fail-safe: if we can't check, don't claim stale (would spam users).
    return { stale: false, latestSha: current, currentSha: current }
  }
}

function withinCircuitBreaker(): boolean {
  if (typeof sessionStorage === 'undefined') return false
  const last = sessionStorage.getItem(STORAGE_KEY)
  if (!last) return false
  const ts = Number(last)
  if (!Number.isFinite(ts)) return false
  return Date.now() - ts < CIRCUIT_BREAKER_MS
}

function markReload() {
  try {
    sessionStorage.setItem(STORAGE_KEY, String(Date.now()))
  } catch {
    /* private mode — ok */
  }
}

export function hardReload(latestSha?: string): boolean {
  if (typeof window === 'undefined') return false
  if (withinCircuitBreaker()) {
    if (typeof console !== 'undefined') {
      console.warn('[version-check] hardReload suppressed by circuit breaker')
    }
    return false
  }
  markReload()

  // Best-effort cache-busting before reload. These are fire-and-forget —
  // if they're slow, the navigation still proceeds.
  try {
    if ('serviceWorker' in navigator) {
      void navigator.serviceWorker
        .getRegistrations()
        .then((regs) => regs.forEach((r) => r.unregister()))
        .catch(() => {})
    }
  } catch {
    /* ignore */
  }
  try {
    if (typeof caches !== 'undefined') {
      void caches
        .keys()
        .then((keys) => keys.forEach((k) => caches.delete(k)))
        .catch(() => {})
    }
  } catch {
    /* ignore */
  }

  const sep = window.location.pathname.includes('?') ? '&' : '?'
  const buster = latestSha && latestSha !== 'dev' ? latestSha : String(Date.now())
  window.location.assign(`${window.location.pathname}${sep}_v=${encodeURIComponent(buster)}`)
  return true
}

/**
 * Returns true if `err` looks like a chunk-load error from a post-deploy
 * mismatch. Next.js surfaces these as `ChunkLoadError` or messages mentioning
 * "Loading chunk" / "dynamically imported module".
 */
export function isChunkLoadError(err: unknown): boolean {
  if (!err) return false
  const e = err as { name?: string; message?: string }
  if (e.name === 'ChunkLoadError') return true
  const msg = typeof e.message === 'string' ? e.message : ''
  return (
    /ChunkLoadError/i.test(msg) ||
    /Loading chunk [\w-]+ failed/i.test(msg) ||
    /Failed to fetch dynamically imported module/i.test(msg) ||
    /Importing a module script failed/i.test(msg)
  )
}

/**
 * Ambient poll every 10 minutes. Only installs one poller per tab.
 */
let pollHandle: ReturnType<typeof setInterval> | null = null

export function startAmbientVersionPoll(
  onStale: (r: StaleResult) => void,
  intervalMs = 10 * 60 * 1000,
) {
  if (typeof window === 'undefined') return () => {}
  if (pollHandle !== null) return () => {}
  const tick = async () => {
    const r = await isStale()
    if (r.stale) onStale(r)
  }
  pollHandle = setInterval(() => void tick(), intervalMs)
  return () => {
    if (pollHandle !== null) {
      clearInterval(pollHandle)
      pollHandle = null
    }
  }
}
```

- [ ] **Step 9: Run tests to verify they pass**

```
cd frontend && npx jest __tests__/version-check.test.ts
```
Expected: all pass.

- [ ] **Step 10: Wire ChunkLoadError auto-reload into the global handlers**

Edit `frontend/lib/client-error-setup.ts`. Inside both the `error` and `unhandledrejection` listeners, check for chunk errors *before* reporting, and auto-reload:

Add import:

```ts
import { hardReload, isChunkLoadError, startAmbientVersionPoll, isStale } from '@/lib/version-check'
```

Replace the body of `installGlobalErrorHandlers` with:

```ts
export function installGlobalErrorHandlers(getUserEmail: () => string | null, locale: string) {
  if (installed) return
  if (typeof window === 'undefined') return
  installed = true

  const maybeReloadForChunkError = async (err: unknown) => {
    if (!isChunkLoadError(err)) return false
    const staleResult = await isStale().catch(() => null)
    // If the bundle is stale OR we can't verify, still reload — ChunkLoadError
    // by itself is strong signal of post-deploy mismatch.
    const triggered = hardReload(staleResult?.latestSha)
    return triggered
  }

  window.addEventListener('error', (event) => {
    const err = event.error ?? new Error(event.message || 'Unknown window error')
    void (async () => {
      const reloaded = await maybeReloadForChunkError(err)
      if (reloaded) return
      void reportClientError({
        error: err,
        source: 'window.onerror',
        context: buildContext(getUserEmail(), locale),
        extra: {
          filename: event.filename,
          lineno: event.lineno,
          colno: event.colno,
        },
      })
    })()
  })

  window.addEventListener('unhandledrejection', (event) => {
    const err = event.reason instanceof Error ? event.reason : new Error(String(event.reason))
    void (async () => {
      const reloaded = await maybeReloadForChunkError(err)
      if (reloaded) return
      void reportClientError({
        error: err,
        source: 'unhandledrejection',
        context: buildContext(getUserEmail(), locale),
      })
    })()
  })

  // Ambient poll: every 10 min, if stale, quietly stash info for UI to pick up.
  // We do NOT auto-reload here — that's reserved for real crashes.
  startAmbientVersionPoll((r) => {
    try {
      sessionStorage.setItem('karaoke_latest_sha', r.latestSha)
      window.dispatchEvent(new CustomEvent('karaoke:stale-version', { detail: r }))
    } catch {
      /* ignore */
    }
  })
}
```

- [ ] **Step 11: Add stale-version banner + "Update now" CTA to CrashReport**

Edit `frontend/components/CrashReport.tsx`:

Add imports near the top:

```tsx
import { useEffect, useState, useMemo } from 'react'
import { hardReload, isStale } from '@/lib/version-check'
```

Add new strings in the message (handled in step 12). In the component body, after existing `useState` calls, add:

```tsx
const [staleInfo, setStaleInfo] = useState<{ latestSha: string } | null>(null)
useEffect(() => {
  let cancelled = false
  void isStale().then((r) => {
    if (!cancelled && r.stale) setStaleInfo({ latestSha: r.latestSha })
  })
  return () => { cancelled = true }
}, [])
```

Inside the `reportClientError` call's `extra` object, add `latest_sha: staleInfo?.latestSha ?? null` so Discord shows whether the user was current:

```tsx
extra: { digest, interactive: state === 'idle' ? 'auto' : 'manual', latest_sha: staleInfo?.latestSha ?? null },
```

In the JSX, **above** the error message card (inserted right after the title `<div>`), add the banner:

```tsx
{staleInfo && (
  <div className="mt-4 rounded-md border border-amber-400/60 bg-amber-400/10 p-3 text-sm" data-testid="crash-report-stale-banner">
    <strong className="font-medium">{t('updateAvailable')}</strong>
    <p className="mt-1 text-xs text-muted-foreground">{t('updateAvailableSubtitle')}</p>
    <Button
      size="sm"
      className="mt-2 min-h-[40px]"
      onClick={() => hardReload(staleInfo.latestSha)}
    >
      {t('updateNow')}
    </Button>
  </div>
)}
```

When `staleInfo` is set, the "Try again" button in the existing button row should be de-emphasised (change its variant from default to `outline`). Adjust the existing `onReset` button render to:

```tsx
{onReset && (
  <Button
    size="sm"
    variant={staleInfo ? 'outline' : 'default'}
    onClick={onReset}
    className="min-h-[40px]"
  >
    <RefreshCw className="mr-1.5 h-4 w-4" />
    {t('retry')}
  </Button>
)}
```

- [ ] **Step 12: Add new i18n strings**

Append to the `crashReport` namespace in `frontend/messages/en.json`:

```json
"updateAvailable": "A newer version is available — it may fix this issue.",
"updateAvailableSubtitle": "We'll refresh the page and load the latest code.",
"updateNow": "Update now"
```

Then run:

```
python scripts/translate.py --messages-dir frontend/messages --target all
```

- [ ] **Step 13: Add a test for the stale-banner path**

Append to `frontend/__tests__/CrashReport.test.tsx`:

```tsx
import { isStale, hardReload } from '@/lib/version-check'

jest.mock('@/lib/version-check', () => ({
  isStale: jest.fn(),
  hardReload: jest.fn(),
}))

describe('CrashReport stale-version banner', () => {
  beforeEach(() => {
    ;(isStale as jest.Mock).mockReset()
    ;(hardReload as jest.Mock).mockReset()
  })

  it('renders banner + Update now button when stale', async () => {
    ;(isStale as jest.Mock).mockResolvedValue({ stale: true, latestSha: 'bbb', currentSha: 'aaa' })
    render(wrap(<CrashReport error={new Error('x')} source="test" />))
    expect(await screen.findByTestId('crash-report-stale-banner')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Update now/i }))
    expect(hardReload).toHaveBeenCalledWith('bbb')
  })

  it('does not render banner when not stale', async () => {
    ;(isStale as jest.Mock).mockResolvedValue({ stale: false, latestSha: 'aaa', currentSha: 'aaa' })
    render(wrap(<CrashReport error={new Error('x')} source="test" />))
    // Let the microtask flush
    await new Promise((r) => setTimeout(r, 0))
    expect(screen.queryByTestId('crash-report-stale-banner')).not.toBeInTheDocument()
  })
})
```

Note: if the existing CrashReport test file imports `reportClientError` at the top-level and also mocks it, keep that mock intact; the new mock for `version-check` is additional.

- [ ] **Step 14: Run all new/affected tests**

```
cd frontend && npx jest __tests__/version-check.test.ts __tests__/CrashReport.test.tsx
```
Expected: all pass.

- [ ] **Step 15: Commit**

```
git add frontend/scripts/write-version.mjs frontend/.gitignore frontend/package.json frontend/next.config.ts frontend/lib/version-check.ts frontend/__tests__/version-check.test.ts frontend/lib/client-error-setup.ts frontend/components/CrashReport.tsx frontend/__tests__/CrashReport.test.tsx frontend/messages
git commit -m "feat(frontend): version-aware cache busting — ChunkLoadError auto-reload + stale-version banner"
```

---

### Task 9: End-to-end Playwright verification

**Files:**
- Create: `frontend/e2e/crash-reporting.spec.ts`

- [ ] **Step 1: Write the e2e test**

```typescript
import { test, expect } from '@playwright/test'

test('client crash renders CrashReport and POSTs /api/client-errors', async ({ page }) => {
  const requests: { url: string; body: any }[] = []
  await page.route('**/api/client-errors', async (route) => {
    const req = route.request()
    requests.push({ url: req.url(), body: JSON.parse(req.postData() || '{}') })
    await route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({ pattern_id: 'test', is_new: true }),
    })
  })

  // Navigate to dev server (or preview) home.
  await page.goto('/en/')

  // Trigger a synthetic error via the page context.
  await page.evaluate(() => {
    setTimeout(() => {
      throw new Error('synthetic-crash-test')
    }, 0)
  })

  // Wait for the POST to land.
  await expect.poll(() => requests.length, { timeout: 5000 }).toBeGreaterThan(0)

  const body = requests[0].body
  expect(body.message).toContain('synthetic-crash-test')
  expect(body.source).toBe('window.onerror')
})

test('review page error boundary catches throw and renders CrashReport in place', async ({ page }) => {
  const requests: { url: string; body: any }[] = []
  await page.route('**/api/client-errors', async (route) => {
    const req = route.request()
    requests.push({ url: req.url(), body: JSON.parse(req.postData() || '{}') })
    await route.fulfill({ status: 202, contentType: 'application/json', body: '{"pattern_id":"t","is_new":true}' })
  })

  // Navigate somewhere that renders CrashReportBoundary; exact test route TBD at
  // implementation time (could be a /dev/_crash-test page added temporarily).
  // Placeholder — adapt to whatever test harness route is most convenient.
  await page.goto('/en/')
  // If there is no test harness route yet, the unit tests in Tasks 5 and 7
  // already cover the boundary behaviour; this e2e test can be skipped.
  test.skip()
})
```

- [ ] **Step 2: Run tests**

```
cd frontend && npx playwright test crash-reporting.spec.ts
```
Expected: first test passes; second is skipped (documented as TODO).

- [ ] **Step 3: Commit**

```
git add frontend/e2e/crash-reporting.spec.ts
git commit -m "test(e2e): verify crash reporter posts to /api/client-errors on window error"
```

---

### Task 10: Synthetic crash endpoint for verification (admin only)

Purpose: a way to intentionally trigger a crash in prod to verify Discord alerts fire, without having to wait for a real user crash.

**Files:**
- Create: `frontend/app/[locale]/admin/_crash-test/page.tsx`

- [ ] **Step 1: Create the page**

```tsx
'use client'

import { useState } from 'react'
import { Button } from '@/components/ui/button'

export default function CrashTestPage() {
  const [mode, setMode] = useState<'sync' | 'async' | 'render' | null>(null)
  if (mode === 'render') {
    throw new Error('synthetic render crash')
  }
  return (
    <div className="p-6 space-y-3 max-w-md">
      <h1 className="text-lg font-semibold">Crash test (admin only)</h1>
      <p className="text-sm text-muted-foreground">Fires intentional errors to exercise the crash-reporting pipeline end-to-end.</p>
      <Button onClick={() => { setTimeout(() => { throw new Error('synthetic async crash') }, 0) }}>Async throw</Button>
      <Button onClick={() => { void Promise.reject(new Error('synthetic rejection')) }}>Unhandled rejection</Button>
      <Button variant="destructive" onClick={() => setMode('render')}>Render-time throw</Button>
    </div>
  )
}
```

Note: this is under `/admin/` which is excluded from i18n per CLAUDE.md, so no translation needed.

- [ ] **Step 2: Add admin-only gate**

Reuse the existing admin gate pattern from `frontend/app/[locale]/admin/layout.tsx` (or whatever the admin shell is). If that already wraps the route, nothing more is needed. Otherwise, add `useAuth` check and redirect non-admin users to `/app`.

- [ ] **Step 3: Commit**

```
git add frontend/app/[locale]/admin/_crash-test
git commit -m "chore(frontend): admin crash-test page to exercise reporting end-to-end"
```

---

### Task 11: Integration test — alert fires in Discord

Out of scope for automated tests — requires the live Discord webhook. Do this as a manual post-deploy check in Task 14.

---

### Task 12: Run full test suite

- [ ] **Step 1: Run all tests**

```
make test 2>&1 | tail -n 500
```
Expected: all pass.

- [ ] **Step 2: If failures exist, fix root causes before proceeding**

Do not skip or mark as expected. The `make test` output should show 0 failing tests before shipping.

- [ ] **Step 3: Commit any fixes**

Only if needed. Otherwise proceed.

---

### Task 13: Version bump + PR

- [ ] **Step 1: Bump version**

Edit `pyproject.toml`, bump `tool.poetry.version` patch component.

- [ ] **Step 2: Run `/docs-review`**

Follow the `/docs-review` workflow. Update `docs/ARCHITECTURE.md` if it documents the error-monitor pipeline — add a line noting that `service="frontend"` patterns come from `POST /api/client-errors`. Update `docs/ERROR-MONITOR.md` (or the archive design doc equivalent) similarly.

- [ ] **Step 3: Run `/coderabbit`**

Follow the `/coderabbit` workflow, up to 3 cycles.

- [ ] **Step 4: Create PR**

Follow the `/pr` workflow. Title: `feat: frontend crash reporting → Discord alerts`.

---

### Task 14: Post-deploy verification

- [ ] **Step 1: Wait for Cloudflare + Cloud Run deployment**

- [ ] **Step 2: Visit `https://gen.nomadkaraoke.com/en/admin/_crash-test` as admin**

Trigger each of: async throw, unhandled rejection, render-time throw.

- [ ] **Step 3: Verify Discord alert arrives**

Expected: an alert in the error channel with `service=frontend`, pattern_id, and the synthetic error message. May take up to ~5 minutes depending on the monitor Cloud Run Job schedule.

- [ ] **Step 4: Verify Firestore**

```
python3 -c "
import os; os.environ['GOOGLE_CLOUD_PROJECT']='nomadkaraoke'
from google.cloud import firestore
db = firestore.Client(project='nomadkaraoke')
q = db.collection('error_patterns').where(filter=firestore.FieldFilter('service','==','frontend')).limit(5)
for d in q.stream(): print(d.id, d.to_dict().get('sample_message','')[:120])
"
```
Expected: at least 3 synthetic patterns.

- [ ] **Step 5: If alert did not fire, investigate**

Check: (a) Cloud Run Job schedule (`infrastructure/modules/error_monitor.py`), (b) `discord_alerts` audit collection, (c) normalizer behaviour on the synthetic messages, (d) rate limiter false positives.

---

### Task 15 (follow-up, separate PR): Sourcemap upload

Out of scope for this PR but worth planning. Cloudflare static export produces minified bundles. Options:
- Upload sourcemaps to GCS as part of the frontend deploy workflow, keyed by build SHA. Provide a small CLI script `scripts/resolve-frontend-stack.py` that takes `(pattern_id)` and resolves the stack via the sourcemap.
- Or: add a Next.js build flag to emit sourcemaps alongside the bundle on Cloudflare Pages, but restrict access via a Cloudflare rule so they're only fetchable from the backend. Avoid public exposure.

Do this in a follow-up. Current plan ships useful-but-minified stacks.

---

## Self-Review Checklist

- [x] Spec coverage: all four user asks (capture, in-UI debug, backend ingest, Discord alert) have tasks
- [x] No TBDs except Task 14 (explicitly follow-up) and the skipped e2e test (explicitly noted)
- [x] Type consistency: `FrontendErrorReport`, `PatternData`, `ClientErrorPayload`, `ReportArgs`, `CollectedContext` all referenced consistently
- [x] Dedup mechanism in two places (client + server via pattern hash) — by design, not a bug
- [x] No hardcoded URLs — API base comes from existing `api-config.ts`
- [x] All new user-facing strings are i18n-routed via `next-intl`; `global-error.tsx` intentionally uses English (translations unavailable in that last-resort boundary)
