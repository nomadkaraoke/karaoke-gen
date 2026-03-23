"""
Tests for anti-abuse investigation features: admin endpoints, session/job
fingerprint tracking, and user lookup methods.
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

import sys
sys.modules.setdefault('google.cloud.firestore', MagicMock())
sys.modules.setdefault('google.cloud.firestore_v1', MagicMock())

from backend.models.user import User, Session, MagicLinkToken


# =============================================================================
# Model Tests
# =============================================================================


class TestSessionFingerprint:
    """Session model stores device_fingerprint."""

    def test_session_has_device_fingerprint(self):
        session = Session(
            token="tok",
            user_email="test@example.com",
            expires_at=datetime.utcnow() + timedelta(days=30),
            device_fingerprint="fp-abc",
        )
        assert session.device_fingerprint == "fp-abc"

    def test_session_fingerprint_defaults_none(self):
        session = Session(
            token="tok",
            user_email="test@example.com",
            expires_at=datetime.utcnow() + timedelta(days=30),
        )
        assert session.device_fingerprint is None


class TestJobCreationIp:
    """Job model stores creation_ip."""

    def test_job_has_creation_ip(self):
        from backend.models.job import Job, JobStatus
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            creation_ip="1.2.3.4",
        )
        assert job.creation_ip == "1.2.3.4"

    def test_job_creation_ip_defaults_none(self):
        from backend.models.job import Job, JobStatus
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        assert job.creation_ip is None


# =============================================================================
# Internal/Test Email Filtering
# =============================================================================


class TestInternalOrTestEmail:
    """Tests for is_internal_or_test_email utility."""

    def test_nomadkaraoke_is_internal(self):
        from backend.utils.test_data import is_internal_or_test_email
        assert is_internal_or_test_email("admin@nomadkaraoke.com") is True

    def test_beveridge_is_internal(self):
        from backend.utils.test_data import is_internal_or_test_email
        assert is_internal_or_test_email("vocalstar@beveridge.uk") is True

    def test_testmail_is_test(self):
        from backend.utils.test_data import is_internal_or_test_email
        assert is_internal_or_test_email("test123@inbox.testmail.app") is True

    def test_regular_email_is_not_internal(self):
        from backend.utils.test_data import is_internal_or_test_email
        assert is_internal_or_test_email("user@gmail.com") is False

    def test_empty_email(self):
        from backend.utils.test_data import is_internal_or_test_email
        assert is_internal_or_test_email("") is False

    def test_is_internal_email_only(self):
        from backend.utils.test_data import is_internal_email
        assert is_internal_email("andrew@beveridge.uk") is True
        assert is_internal_email("user@gmail.com") is False


# =============================================================================
# UserService Investigation Methods
# =============================================================================


class TestFindUsersBySignupIp:

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_finds_matching_users(self, mock_fs, mock_settings):
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        user1 = User(email="a@test.com", signup_ip="1.2.3.4").model_dump(mode='json')
        user2 = User(email="b@test.com", signup_ip="1.2.3.4").model_dump(mode='json')
        mock_doc1 = MagicMock()
        mock_doc1.to_dict.return_value = user1
        mock_doc2 = MagicMock()
        mock_doc2.to_dict.return_value = user2
        mock_db.collection.return_value.where.return_value.order_by.return_value.limit.return_value.stream.return_value = [mock_doc1, mock_doc2]

        from backend.services.user_service import UserService
        service = UserService()
        results = service.find_users_by_signup_ip("1.2.3.4")

        assert len(results) == 2
        assert results[0].email == "a@test.com"

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_returns_empty_on_error(self, mock_fs, mock_settings):
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db
        mock_db.collection.return_value.where.side_effect = Exception("Firestore down")

        from backend.services.user_service import UserService
        service = UserService()
        results = service.find_users_by_signup_ip("1.2.3.4")
        assert results == []


class TestFindUsersByFingerprint:

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_finds_matching_users(self, mock_fs, mock_settings):
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        user1 = User(email="a@test.com", device_fingerprint="fp123").model_dump(mode='json')
        mock_doc1 = MagicMock()
        mock_doc1.to_dict.return_value = user1
        mock_db.collection.return_value.where.return_value.order_by.return_value.limit.return_value.stream.return_value = [mock_doc1]

        from backend.services.user_service import UserService
        service = UserService()
        results = service.find_users_by_fingerprint("fp123")

        assert len(results) == 1


class TestFindSuspiciousAccounts:

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_finds_free_high_usage_accounts(self, mock_fs, mock_settings):
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        free_abuser = User(email="free@test.com", total_jobs_created=5, total_spent=0).model_dump(mode='json')
        paying_user = User(email="paid@test.com", total_jobs_created=10, total_spent=500).model_dump(mode='json')
        mock_doc1 = MagicMock()
        mock_doc1.to_dict.return_value = free_abuser
        mock_doc2 = MagicMock()
        mock_doc2.to_dict.return_value = paying_user
        mock_db.collection.return_value.where.return_value.order_by.return_value.limit.return_value.stream.return_value = [mock_doc1, mock_doc2]

        from backend.services.user_service import UserService
        service = UserService()
        results = service.find_suspicious_accounts(min_jobs=2, max_spend=0)

        assert len(results) == 1
        assert results[0].email == "free@test.com"

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_excludes_internal_and_test_accounts(self, mock_fs, mock_settings):
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        real_user = User(email="abuser@gmail.com", total_jobs_created=5, total_spent=0).model_dump(mode='json')
        internal_user = User(email="admin@nomadkaraoke.com", total_jobs_created=10, total_spent=0).model_dump(mode='json')
        test_user = User(email="e2e@inbox.testmail.app", total_jobs_created=8, total_spent=0).model_dump(mode='json')
        personal_user = User(email="vocalstar@beveridge.uk", total_jobs_created=3, total_spent=0).model_dump(mode='json')

        docs = []
        for data in [real_user, internal_user, test_user, personal_user]:
            doc = MagicMock()
            doc.to_dict.return_value = data
            docs.append(doc)

        mock_db.collection.return_value.where.return_value.order_by.return_value.limit.return_value.stream.return_value = docs

        from backend.services.user_service import UserService
        service = UserService()
        results = service.find_suspicious_accounts(min_jobs=2, max_spend=0)

        assert len(results) == 1
        assert results[0].email == "abuser@gmail.com"


class TestFindRelatedAccounts:

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_finds_related_by_ip_and_fingerprint(self, mock_fs, mock_settings):
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        target = User(email="target@test.com", signup_ip="1.2.3.4", device_fingerprint="fp123")

        from backend.services.user_service import UserService
        service = UserService()

        with patch.object(service, 'get_user', return_value=target), \
             patch.object(service, 'find_users_by_signup_ip', return_value=[
                 target,
                 User(email="related@test.com", signup_ip="1.2.3.4"),
             ]), \
             patch.object(service, 'find_users_by_fingerprint', return_value=[
                 target,
                 User(email="device-buddy@test.com", device_fingerprint="fp123"),
             ]):
            result = service.find_related_accounts("target@test.com")

        assert result["user"].email == "target@test.com"
        # Should exclude the target user from related lists
        assert len(result["by_ip"]) == 1
        assert result["by_ip"][0].email == "related@test.com"
        assert len(result["by_fingerprint"]) == 1
        assert result["by_fingerprint"][0].email == "device-buddy@test.com"

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_returns_empty_for_unknown_user(self, mock_fs, mock_settings):
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        mock_doc = MagicMock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        mock_db.collection.return_value.where.return_value.limit.return_value.stream.return_value = []

        from backend.services.user_service import UserService
        service = UserService()
        result = service.find_related_accounts("ghost@test.com")

        assert result["user"] is None
        assert result["by_ip"] == []
        assert result["by_fingerprint"] == []


# =============================================================================
# Admin Endpoint Integration Tests
# =============================================================================


@pytest.fixture
def mock_user_svc():
    svc = MagicMock()
    svc.NEW_USER_FREE_CREDITS = 2
    return svc


@pytest.fixture
def admin_client(mock_user_svc):
    from backend.main import app
    from backend.services.user_service import get_user_service
    from backend.api.dependencies import require_admin

    mock_auth = MagicMock()
    mock_auth.is_admin = True
    mock_auth.user_email = "admin@nomadkaraoke.com"

    app.dependency_overrides[get_user_service] = lambda: mock_user_svc
    app.dependency_overrides[require_admin] = lambda: mock_auth

    yield TestClient(app)

    app.dependency_overrides.clear()


class TestAdminAbuseEndpoints:

    def test_suspicious_accounts_endpoint(self, admin_client, mock_user_svc):
        mock_user_svc.find_suspicious_accounts.return_value = [
            User(email="free@test.com", total_jobs_created=5, total_spent=0),
        ]

        response = admin_client.get("/api/admin/abuse/suspicious?min_jobs=2&max_spend=0")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["users"][0]["email"] == "free@test.com"

    def test_by_ip_endpoint(self, admin_client, mock_user_svc):
        mock_user_svc.find_users_by_signup_ip.return_value = [
            User(email="a@test.com", signup_ip="1.2.3.4"),
            User(email="b@test.com", signup_ip="1.2.3.4"),
        ]

        response = admin_client.get("/api/admin/abuse/by-ip/1.2.3.4")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2

    def test_by_fingerprint_endpoint(self, admin_client, mock_user_svc):
        mock_user_svc.find_users_by_fingerprint.return_value = [
            User(email="a@test.com", device_fingerprint="fp123"),
        ]

        response = admin_client.get("/api/admin/abuse/by-fingerprint/fp123")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1

    def test_related_endpoint(self, admin_client, mock_user_svc):
        target = User(email="target@test.com", signup_ip="1.2.3.4", device_fingerprint="fp123", total_jobs_created=3)
        mock_user_svc.find_related_accounts.return_value = {
            "user": target,
            "by_ip": [User(email="buddy@test.com", signup_ip="1.2.3.4")],
            "by_fingerprint": [],
        }

        response = admin_client.get("/api/admin/abuse/related/target@test.com")
        assert response.status_code == 200
        data = response.json()
        assert data["user"]["email"] == "target@test.com"
        assert len(data["related_by_ip"]) == 1
        assert len(data["related_by_fingerprint"]) == 0

    def test_related_endpoint_user_not_found(self, admin_client, mock_user_svc):
        mock_user_svc.find_related_accounts.return_value = {
            "user": None, "by_ip": [], "by_fingerprint": [],
        }

        response = admin_client.get("/api/admin/abuse/related/ghost@test.com")
        assert response.status_code == 404

    def test_correlations_endpoint(self, admin_client, mock_user_svc):
        user_a = User(email="a@test.com", signup_ip="1.2.3.4", device_fingerprint="fp123", total_jobs_created=2)
        user_b = User(email="b@test.com", signup_ip="1.2.3.4", device_fingerprint="fp123", total_jobs_created=3)

        mock_user_svc.find_account_correlations.return_value = {
            "fingerprint_clusters": [
                {"fingerprint": "fp123", "users": [user_a, user_b]},
            ],
            "ip_clusters": [
                {"ip": "1.2.3.4", "users": [user_a, user_b]},
            ],
            "user_agents": {"a@test.com": "Mozilla/5.0", "b@test.com": "Mozilla/5.0"},
        }

        response = admin_client.get("/api/admin/abuse/correlations")
        assert response.status_code == 200
        data = response.json()
        assert len(data["fingerprint_clusters"]) == 1
        assert data["fingerprint_clusters"][0]["fingerprint"] == "fp123"
        assert data["fingerprint_clusters"][0]["count"] == 2
        assert data["fingerprint_clusters"][0]["users"][0]["user_agent"] == "Mozilla/5.0"
        assert len(data["ip_clusters"]) == 1
        assert data["ip_clusters"][0]["ip"] == "1.2.3.4"
        assert data["ip_clusters"][0]["count"] == 2

    def test_correlations_endpoint_empty(self, admin_client, mock_user_svc):
        mock_user_svc.find_account_correlations.return_value = {
            "fingerprint_clusters": [],
            "ip_clusters": [],
            "user_agents": {},
        }

        response = admin_client.get("/api/admin/abuse/correlations")
        assert response.status_code == 200
        data = response.json()
        assert data["fingerprint_clusters"] == []
        assert data["ip_clusters"] == []


# =============================================================================
# Correlations Service Method Tests
# =============================================================================


class TestFindAccountCorrelations:

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_groups_by_fingerprint_and_ip(self, mock_fs, mock_settings):
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        user_a = User(email="a@gmail.com", signup_ip="1.2.3.4", device_fingerprint="fp123", total_jobs_created=2).model_dump(mode='json')
        user_b = User(email="b@gmail.com", signup_ip="1.2.3.4", device_fingerprint="fp123", total_jobs_created=3).model_dump(mode='json')
        user_c = User(email="c@gmail.com", signup_ip="5.6.7.8", device_fingerprint="fp999", total_jobs_created=1).model_dump(mode='json')

        docs = []
        for data in [user_a, user_b, user_c]:
            doc = MagicMock()
            doc.to_dict.return_value = data
            docs.append(doc)

        # Mock the users collection stream
        mock_db.collection.return_value.stream.return_value = docs

        # Mock session lookups (return empty for simplicity)
        mock_db.collection.return_value.where.return_value.order_by.return_value.limit.return_value.stream.return_value = []

        from backend.services.user_service import UserService
        service = UserService()
        result = service.find_account_correlations()

        # a and b share fp123 → 1 fingerprint cluster
        assert len(result["fingerprint_clusters"]) == 1
        assert result["fingerprint_clusters"][0]["fingerprint"] == "fp123"
        assert len(result["fingerprint_clusters"][0]["users"]) == 2

        # a and b share IP 1.2.3.4 → 1 IP cluster
        assert len(result["ip_clusters"]) == 1
        assert result["ip_clusters"][0]["ip"] == "1.2.3.4"
        assert len(result["ip_clusters"][0]["users"]) == 2

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_excludes_internal_accounts(self, mock_fs, mock_settings):
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        real_user = User(email="user@gmail.com", signup_ip="1.2.3.4", device_fingerprint="fp123").model_dump(mode='json')
        internal_user = User(email="admin@nomadkaraoke.com", signup_ip="1.2.3.4", device_fingerprint="fp123").model_dump(mode='json')

        docs = []
        for data in [real_user, internal_user]:
            doc = MagicMock()
            doc.to_dict.return_value = data
            docs.append(doc)

        mock_db.collection.return_value.stream.return_value = docs

        from backend.services.user_service import UserService
        service = UserService()
        result = service.find_account_correlations()

        # Only 1 non-internal user with fp123, so no cluster (need 2+)
        assert len(result["fingerprint_clusters"]) == 0
        assert len(result["ip_clusters"]) == 0

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_returns_empty_on_error(self, mock_fs, mock_settings):
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db
        mock_db.collection.return_value.stream.side_effect = Exception("Firestore down")

        from backend.services.user_service import UserService
        service = UserService()
        result = service.find_account_correlations()

        assert result["fingerprint_clusters"] == []
        assert result["ip_clusters"] == []
