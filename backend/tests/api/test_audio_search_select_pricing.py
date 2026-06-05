"""
Tests for duration-based credit delta-charge on audio-search /select.

Covers:
- result with duration=1500s (25 min → 3 credits), credits_charged=1 → deducts 2 MORE,
  credits_charged becomes 3, count_as_job_creation=False, duration_estimate_source=search_metadata
- result with duration=4000s (over 60 min) → 422, no deduction, no download
- insufficient credits for top-up → 402
- result with no duration → owed 0, no deduction, selection proceeds
- acknowledged_credits mismatch → 409
"""
from __future__ import annotations

from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.job import Job, JobStatus
from backend.services.auth_service import UserType, AuthResult

NOW = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Auth fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _ensure_auth_overrides():
    """Ensure auth dependency overrides are set for every test in this file."""
    from backend.api.dependencies import require_auth, require_admin
    from backend.main import app

    async def mock_require_auth():
        return AuthResult(
            is_valid=True,
            user_type=UserType.STRIPE,
            remaining_uses=5,
            message="Regular test user",
            is_admin=False,
            user_email="buyer@example.com",
        )

    app.dependency_overrides[require_auth] = mock_require_auth
    app.dependency_overrides[require_admin] = mock_require_auth
    yield
    app.dependency_overrides.pop(require_auth, None)
    app.dependency_overrides.pop(require_admin, None)


# ---------------------------------------------------------------------------
# Helpers: build mock jobs
# ---------------------------------------------------------------------------


def _awaiting_selection_job(
    job_id: str = "sel-job-1",
    credits_charged: int = 1,
    user_email: str = "buyer@example.com",
    search_results: list | None = None,
) -> Job:
    """A job in AWAITING_AUDIO_SELECTION with audio_search_results in state_data."""
    if search_results is None:
        search_results = [
            {
                "title": "Waterloo",
                "artist": "ABBA",
                "provider": "RED",
                "quality": "FLAC",
                "source_id": "abc123",
                "target_file": "Waterloo.flac",
                "url": "",
                "duration": 300,
            }
        ]
    return Job(
        job_id=job_id,
        status=JobStatus.AWAITING_AUDIO_SELECTION,
        created_at=NOW,
        updated_at=NOW,
        user_email=user_email,
        state_data={
            "credits_charged": credits_charged,
            "audio_search_results": search_results,
        },
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_job_manager():
    jm = MagicMock()
    jm.get_job.return_value = _awaiting_selection_job()
    jm.update_job.return_value = None
    jm.transition_to_state.return_value = True
    return jm


@pytest.fixture
def mock_user_service():
    us = MagicMock()
    us.deduct_credits.return_value = (True, 3, "ok")
    return us


@pytest.fixture
def mock_worker_service():
    ws = MagicMock()
    ws.trigger_audio_download_worker = AsyncMock(return_value=True)
    return ws


@pytest.fixture
def patched_app(mock_job_manager, mock_user_service, mock_worker_service):
    """Patch module-level singletons in audio_search route and yield TestClient."""
    from fastapi.testclient import TestClient
    from backend.main import app

    with (
        patch("backend.api.routes.audio_search.job_manager", mock_job_manager),
        patch(
            "backend.api.routes.audio_search.get_user_service",
            return_value=mock_user_service,
        ),
        patch(
            "backend.api.routes.audio_search.get_worker_service",
            return_value=mock_worker_service,
        ),
        patch("backend.api.routes.audio_search.get_audio_search_service", return_value=MagicMock()),
    ):
        yield TestClient(app), mock_job_manager, mock_user_service, mock_worker_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _post_select(client, job_id="sel-job-1", *, selection_index=0, **extra):
    body = {"selection_index": selection_index}
    body.update(extra)
    return client.post(f"/api/audio-search/{job_id}/select", json=body)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSelectPricingDeltaCharge:
    """
    Duration 1500s (25 min) → ceil(1500/600) = 3 credits.
    credits_charged=1 at search time → owed=2 MORE credits.
    """

    def _job_1500s(self) -> Job:
        return _awaiting_selection_job(
            credits_charged=1,
            search_results=[
                {
                    "title": "Long Song",
                    "artist": "Artist",
                    "provider": "RED",
                    "quality": "FLAC",
                    "source_id": "abc999",
                    "target_file": "long.flac",
                    "url": "",
                    "duration": 1500,
                }
            ],
        )

    def test_returns_200(self, patched_app):
        client, jm, us, ws = patched_app
        jm.get_job.return_value = self._job_1500s()

        resp = _post_select(client)

        assert resp.status_code == 200, resp.text

    def test_deducts_delta_2_credits(self, patched_app):
        """owed = duration_to_credits(1500) - credits_charged = 3 - 1 = 2."""
        client, jm, us, ws = patched_app
        jm.get_job.return_value = self._job_1500s()

        _post_select(client)

        us.deduct_credits.assert_called_once()
        args, kwargs = us.deduct_credits.call_args
        amount = kwargs.get("amount", args[2] if len(args) > 2 else None)
        assert amount == 2, f"Expected delta deduction of 2, got {amount}"

    def test_count_as_job_creation_false(self, patched_app):
        """Job was already counted at create_job; must NOT double-count."""
        client, jm, us, ws = patched_app
        jm.get_job.return_value = self._job_1500s()

        _post_select(client)

        us.deduct_credits.assert_called_once()
        args, kwargs = us.deduct_credits.call_args
        flag = kwargs.get("count_as_job_creation", args[4] if len(args) > 4 else True)
        assert flag is False, "must pass count_as_job_creation=False"

    def test_credits_charged_updated_to_3(self, patched_app):
        """After deduction, job_manager.update_job must store credits_charged=3."""
        client, jm, us, ws = patched_app
        jm.get_job.return_value = self._job_1500s()

        _post_select(client)

        # Collect all update_job calls and look for credits_charged update
        all_payloads: dict = {}
        for call in jm.update_job.call_args_list:
            args, kwargs = call
            payload = args[1] if len(args) >= 2 else kwargs.get("updates", {})
            if isinstance(payload, dict):
                all_payloads.update(payload)

        assert all_payloads.get("state_data.credits_charged") == 3, (
            f"Expected state_data.credits_charged=3 in update_job payload, got: {all_payloads}"
        )

    def test_duration_estimate_stored(self, patched_app):
        """duration_estimate_seconds and duration_estimate_source must be persisted."""
        client, jm, us, ws = patched_app
        jm.get_job.return_value = self._job_1500s()

        _post_select(client)

        all_payloads: dict = {}
        for call in jm.update_job.call_args_list:
            args, kwargs = call
            payload = args[1] if len(args) >= 2 else kwargs.get("updates", {})
            if isinstance(payload, dict):
                all_payloads.update(payload)

        assert all_payloads.get("state_data.duration_estimate_seconds") == 1500, (
            f"Expected duration_estimate_seconds=1500 in update_job, got: {all_payloads}"
        )
        assert all_payloads.get("state_data.duration_estimate_source") == "search_metadata", (
            f"Expected duration_estimate_source='search_metadata' in update_job, got: {all_payloads}"
        )

    def test_download_worker_triggered_after_deduction(self, patched_app):
        """Download must be triggered AFTER the credit deduction succeeds."""
        client, jm, us, ws = patched_app
        jm.get_job.return_value = self._job_1500s()

        _post_select(client)

        ws.trigger_audio_download_worker.assert_called_once_with("sel-job-1")


class TestSelectPricingOverLimit:
    """Duration 4000s > 3600s → is_blocked=True → 422 before download."""

    def _job_4000s(self) -> Job:
        return _awaiting_selection_job(
            credits_charged=1,
            search_results=[
                {
                    "title": "Mashup",
                    "artist": "DJ",
                    "provider": "RED",
                    "quality": "FLAC",
                    "source_id": "big999",
                    "target_file": "mashup.flac",
                    "url": "",
                    "duration": 4000,
                }
            ],
        )

    def test_returns_422(self, patched_app):
        client, jm, us, ws = patched_app
        jm.get_job.return_value = self._job_4000s()

        resp = _post_select(client)

        assert resp.status_code == 422, resp.text

    def test_no_deduction(self, patched_app):
        """Over-limit: no credits deducted."""
        client, jm, us, ws = patched_app
        jm.get_job.return_value = self._job_4000s()

        _post_select(client)

        us.deduct_credits.assert_not_called()

    def test_no_download_triggered(self, patched_app):
        """Over-limit: download worker must NOT be triggered."""
        client, jm, us, ws = patched_app
        jm.get_job.return_value = self._job_4000s()

        _post_select(client)

        ws.trigger_audio_download_worker.assert_not_called()


class TestSelectPricingInsufficientCredits:
    """Top-up deduction fails → 402 Insufficient Credits."""

    def _job_1500s(self) -> Job:
        return _awaiting_selection_job(
            credits_charged=1,
            search_results=[
                {
                    "title": "Long Song",
                    "artist": "Artist",
                    "provider": "RED",
                    "quality": "FLAC",
                    "source_id": "abc999",
                    "target_file": "long.flac",
                    "url": "",
                    "duration": 1500,
                }
            ],
        )

    def test_returns_402_when_deduction_fails(self, patched_app):
        client, jm, us, ws = patched_app
        jm.get_job.return_value = self._job_1500s()
        us.deduct_credits.return_value = (False, 0, "Insufficient credits")

        resp = _post_select(client)

        assert resp.status_code == 402, resp.text

    def test_no_download_on_insufficient_credits(self, patched_app):
        """If deduction fails, download worker must NOT be triggered."""
        client, jm, us, ws = patched_app
        jm.get_job.return_value = self._job_1500s()
        us.deduct_credits.return_value = (False, 0, "Insufficient credits")

        _post_select(client)

        ws.trigger_audio_download_worker.assert_not_called()


class TestSelectPricingNoDuration:
    """Result has duration=None → owed=0, no deduction, selection proceeds normally."""

    def _job_no_duration(self) -> Job:
        return _awaiting_selection_job(
            credits_charged=1,
            search_results=[
                {
                    "title": "Unknown Length",
                    "artist": "Band",
                    "provider": "RED",
                    "quality": "FLAC",
                    "source_id": "no-dur-001",
                    "target_file": "unknown.flac",
                    "url": "",
                    # No 'duration' key — simulates missing duration
                }
            ],
        )

    def test_returns_200(self, patched_app):
        client, jm, us, ws = patched_app
        jm.get_job.return_value = self._job_no_duration()

        resp = _post_select(client)

        assert resp.status_code == 200, resp.text

    def test_no_deduction_when_no_duration(self, patched_app):
        """duration=None → owed=0 → no deduction call."""
        client, jm, us, ws = patched_app
        jm.get_job.return_value = self._job_no_duration()

        _post_select(client)

        us.deduct_credits.assert_not_called()

    def test_download_still_triggered(self, patched_app):
        """Even without duration, the download worker must fire."""
        client, jm, us, ws = patched_app
        jm.get_job.return_value = self._job_no_duration()

        _post_select(client)

        ws.trigger_audio_download_worker.assert_called_once()


class TestSelectPricingAcknowledgedCreditsMismatch:
    """
    acknowledged_credits sent but doesn't match server's target_total → 409.
    """

    def _job_1500s(self) -> Job:
        return _awaiting_selection_job(
            credits_charged=1,
            search_results=[
                {
                    "title": "Long Song",
                    "artist": "Artist",
                    "provider": "RED",
                    "quality": "FLAC",
                    "source_id": "abc999",
                    "target_file": "long.flac",
                    "url": "",
                    "duration": 1500,
                }
            ],
        )

    def test_mismatch_returns_409(self, patched_app):
        """target_total=3 (1500s), client sends acknowledged_credits=2 → 409."""
        client, jm, us, ws = patched_app
        jm.get_job.return_value = self._job_1500s()

        resp = _post_select(client, acknowledged_credits=2)

        assert resp.status_code == 409, resp.text

    def test_no_deduction_on_mismatch(self, patched_app):
        client, jm, us, ws = patched_app
        jm.get_job.return_value = self._job_1500s()

        _post_select(client, acknowledged_credits=2)

        us.deduct_credits.assert_not_called()

    def test_no_download_on_mismatch(self, patched_app):
        client, jm, us, ws = patched_app
        jm.get_job.return_value = self._job_1500s()

        _post_select(client, acknowledged_credits=2)

        ws.trigger_audio_download_worker.assert_not_called()

    def test_correct_acknowledged_credits_succeeds(self, patched_app):
        """target_total=3, acknowledged_credits=3 → 200."""
        client, jm, us, ws = patched_app
        jm.get_job.return_value = self._job_1500s()

        resp = _post_select(client, acknowledged_credits=3)

        assert resp.status_code == 200, resp.text
