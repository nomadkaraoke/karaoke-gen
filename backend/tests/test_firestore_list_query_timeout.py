"""Job-list queries must fail fast instead of hanging for the 300s gRPC default.

Regression: `GET /api/jobs?fields=summary` logged "Error listing jobs summary:
Timeout of 300.0s exceeded" — a stalled RunQuery stream held a threadpool
thread for 5 minutes because Query.stream()'s default retry/timeout is 300s.
"""
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def service_and_query():
    with patch("backend.services.firestore_service.firestore"):
        from backend.services.firestore_service import FirestoreService
        svc = FirestoreService()
    query = MagicMock()
    # Every chained builder call returns the same query mock.
    query.where.return_value = query
    query.order_by.return_value = query
    query.limit.return_value = query
    query.select.return_value = query
    query.stream.return_value = iter([])
    svc.db = MagicMock()
    svc.db.collection.return_value = query
    return svc, query


def _assert_bounded(query):
    from backend.services.firestore_service import LIST_QUERY_TIMEOUT_SECONDS

    assert LIST_QUERY_TIMEOUT_SECONDS <= 30
    query.stream.assert_called_once()
    kwargs = query.stream.call_args.kwargs
    assert kwargs["timeout"] == LIST_QUERY_TIMEOUT_SECONDS
    retry = kwargs["retry"]
    # google.api_core Retry exposes its overall deadline as .timeout
    assert retry is not None
    assert retry.timeout <= LIST_QUERY_TIMEOUT_SECONDS


def test_list_jobs_summary_stream_has_bounded_timeout(service_and_query):
    svc, query = service_and_query
    assert svc.list_jobs_summary(user_email="a@b.com", limit=10) == []
    _assert_bounded(query)


def test_list_jobs_stream_has_bounded_timeout(service_and_query):
    svc, query = service_and_query
    assert svc.list_jobs(user_email="a@b.com", limit=10) == []
    _assert_bounded(query)


def test_list_jobs_by_batch_id_stream_has_bounded_timeout(service_and_query):
    svc, query = service_and_query
    assert svc.list_jobs_by_batch_id("batch-1") == []
    _assert_bounded(query)
