"""
Unit tests for the admin stats overview endpoint.

Covers the exclude_test=True path (the admin dashboard default), which streams
users and jobs with Firestore field masks. The field masks are load-bearing:
without them the endpoint streams full job documents (~100MB collection-wide)
and takes 15-20s, blocking the event loop for every other request.
"""
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.admin import (
    router,
    _compute_admin_stats_overview,
    _OVERVIEW_USER_FIELDS,
    _OVERVIEW_JOB_FIELDS,
)
from backend.api.dependencies import require_admin
from backend.services.user_service import get_user_service, USERS_COLLECTION


app = FastAPI()
app.include_router(router, prefix="/api")


def get_mock_admin():
    from backend.api.dependencies import AuthResult, UserType

    return AuthResult(
        is_valid=True,
        user_type=UserType.ADMIN,
        remaining_uses=999,
        message="Admin authenticated",
        user_email="admin@example.com",
        is_admin=True,
    )


app.dependency_overrides[require_admin] = get_mock_admin


def make_docs(dicts):
    docs = []
    for d in dicts:
        m = Mock()
        m.to_dict.return_value = d
        docs.append(m)
    return docs


def make_db(user_dicts, job_dicts):
    """Fake Firestore db supporting .collection(...).select(...).limit(...).stream()."""
    users_coll = Mock()
    users_coll.select.return_value.limit.return_value.stream.return_value = make_docs(user_dicts)
    jobs_coll = Mock()
    jobs_coll.select.return_value.limit.return_value.stream.return_value = make_docs(job_dicts)

    db = Mock()
    db.collection.side_effect = lambda name: users_coll if name == USERS_COLLECTION else jobs_coll
    return db, users_coll, jobs_coll


@pytest.fixture
def now():
    return datetime.utcnow()


@pytest.fixture
def sample_data(now):
    users = [
        {"email": "real1@example.com", "last_login_at": now - timedelta(days=1),
         "credit_transactions": [{"amount": 5, "created_at": now - timedelta(days=2)}]},
        {"email": "real2@example.com", "last_login_at": now - timedelta(days=20),
         "credit_transactions": [{"amount": -1, "created_at": now - timedelta(days=2)},
                                 {"amount": 3, "created_at": now - timedelta(days=45)}]},
        # Test user — must be excluded from all counts
        {"email": "abc@inbox.testmail.app", "last_login_at": now - timedelta(days=1),
         "credit_transactions": [{"amount": 100, "created_at": now - timedelta(days=1)}]},
    ]
    jobs = [
        {"user_email": "real1@example.com", "created_at": now - timedelta(days=1), "status": "complete"},
        {"user_email": "real1@example.com", "created_at": now - timedelta(days=10), "status": "failed"},
        {"user_email": "real2@example.com", "created_at": now - timedelta(days=40), "status": "transcribing"},
        {"user_email": "real2@example.com", "created_at": now - timedelta(days=1), "status": "awaiting_review"},
        # Test-user job — must be excluded
        {"user_email": "abc@inbox.testmail.app", "created_at": now - timedelta(days=1), "status": "complete"},
    ]
    return users, jobs


def test_compute_overview_counts_and_filters_test_data(sample_data):
    users, jobs = sample_data
    db, _, _ = make_db(users, jobs)

    result = _compute_admin_stats_overview(db, exclude_test=True)

    assert result.total_users == 2
    assert result.active_users_7d == 1
    assert result.active_users_30d == 2
    assert result.total_jobs == 4
    assert result.jobs_last_7d == 2
    assert result.jobs_last_30d == 3
    assert result.jobs_by_status.complete == 1
    assert result.jobs_by_status.failed == 1
    assert result.jobs_by_status.processing == 1  # transcribing
    assert result.jobs_by_status.awaiting_review == 1
    # 5 (recent) — excludes the 3 older than 30d, the negative txn, and the test user's 100
    assert result.total_credits_issued_30d == 5


def test_compute_overview_uses_field_masks(sample_data):
    """The .select() field masks are a performance guarantee — full job docs are huge."""
    users, jobs = sample_data
    db, users_coll, jobs_coll = make_db(users, jobs)

    _compute_admin_stats_overview(db, exclude_test=True)

    users_coll.select.assert_called_once_with(_OVERVIEW_USER_FIELDS)
    jobs_coll.select.assert_called_once_with(_OVERVIEW_JOB_FIELDS)


def test_overview_endpoint_returns_stats(sample_data):
    users, jobs = sample_data
    db, _, _ = make_db(users, jobs)
    mock_service = Mock()
    mock_service.db = db
    app.dependency_overrides[get_user_service] = lambda: mock_service
    try:
        client = TestClient(app)
        resp = client.get("/api/admin/stats/overview?exclude_test=true")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_users"] == 2
        assert body["total_jobs"] == 4
        assert body["jobs_by_status"]["failed"] == 1
    finally:
        app.dependency_overrides.pop(get_user_service, None)


def test_overview_handles_empty_collections():
    db, _, _ = make_db([], [])
    result = _compute_admin_stats_overview(db, exclude_test=True)
    assert result.total_users == 0
    assert result.total_jobs == 0
    assert result.total_credits_issued_30d == 0
