"""
Tests for IP geolocation service.
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

import sys
sys.modules.setdefault('google.cloud.firestore', MagicMock())
sys.modules.setdefault('google.cloud.firestore_v1', MagicMock())


class TestIsPrivateIp:
    def test_private_ipv4(self):
        from backend.services.ip_geolocation_service import _is_private_ip
        assert _is_private_ip("10.0.0.1") is True
        assert _is_private_ip("192.168.1.1") is True
        assert _is_private_ip("172.16.0.1") is True
        assert _is_private_ip("169.254.169.126") is True
        assert _is_private_ip("127.0.0.1") is True

    def test_public_ipv4(self):
        from backend.services.ip_geolocation_service import _is_private_ip
        assert _is_private_ip("8.8.8.8") is False
        assert _is_private_ip("1.2.3.4") is False

    def test_invalid_ip(self):
        from backend.services.ip_geolocation_service import _is_private_ip
        assert _is_private_ip("not-an-ip") is True
        assert _is_private_ip("") is True

    def test_ipv6(self):
        from backend.services.ip_geolocation_service import _is_private_ip
        assert _is_private_ip("::1") is True  # loopback
        assert _is_private_ip("2001:db8::1") is True  # documentation range
        assert _is_private_ip("2402:800:63ac::1") is False  # public


class TestParseIpApiResponse:
    def test_success_response(self):
        from backend.services.ip_geolocation_service import _parse_ip_api_response
        data = {
            "status": "success",
            "country": "United States",
            "countryCode": "US",
            "regionName": "South Carolina",
            "city": "Lancaster",
            "isp": "Comporium Inc",
            "org": "Comporium Inc",
            "as": "AS14615 Comporium Inc",
            "timezone": "America/New_York",
            "query": "8.8.8.8",
        }
        result = _parse_ip_api_response(data)
        assert result["status"] == "success"
        assert result["country_code"] == "US"
        assert result["city"] == "Lancaster"
        assert result["isp"] == "Comporium Inc"
        assert result["as_number"] == "AS14615"
        assert result["as_name"] == "Comporium Inc"

    def test_fail_response(self):
        from backend.services.ip_geolocation_service import _parse_ip_api_response
        result = _parse_ip_api_response({"status": "fail", "query": "bad"})
        assert result["status"] == "fail"


class TestLookupIp:
    @patch('backend.services.ip_geolocation_service.get_settings')
    @patch('backend.services.ip_geolocation_service.firestore')
    def test_private_ip_returns_immediately(self, mock_fs, mock_settings):
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_fs.Client.return_value = MagicMock()

        from backend.services.ip_geolocation_service import IpGeolocationService
        service = IpGeolocationService()
        result = service.lookup_ip("192.168.1.1")
        assert result["status"] == "private"

    @patch('backend.services.ip_geolocation_service.get_settings')
    @patch('backend.services.ip_geolocation_service.firestore')
    def test_cache_hit_returns_cached(self, mock_fs, mock_settings):
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        cached_data = {
            "status": "success",
            "ip": "8.8.8.8",
            "country_code": "US",
            "cached_at": datetime.utcnow().isoformat(),
        }
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = cached_data
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        from backend.services.ip_geolocation_service import IpGeolocationService
        service = IpGeolocationService()
        result = service.lookup_ip("8.8.8.8")
        assert result["status"] == "success"
        assert result["country_code"] == "US"

    @patch('backend.services.ip_geolocation_service.get_settings')
    @patch('backend.services.ip_geolocation_service.firestore')
    @patch('backend.services.ip_geolocation_service.httpx')
    def test_cache_miss_fetches_from_api(self, mock_httpx, mock_fs, mock_settings):
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        # Cache miss
        mock_doc = MagicMock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        # Mock httpx response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "status": "success",
            "country": "Vietnam",
            "countryCode": "VN",
            "regionName": "Ho Chi Minh",
            "city": "Ho Chi Minh City",
            "isp": "Viettel",
            "org": "Viettel Group",
            "as": "AS7552 Viettel Group",
            "timezone": "Asia/Ho_Chi_Minh",
            "query": "14.191.152.247",
        }
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_httpx.Client.return_value = mock_client

        from backend.services.ip_geolocation_service import IpGeolocationService
        service = IpGeolocationService()
        result = service.lookup_ip("14.191.152.247")

        assert result["status"] == "success"
        assert result["country_code"] == "VN"
        assert result["isp"] == "Viettel"
        # Should have cached the result
        mock_db.collection.return_value.document.return_value.set.assert_called_once()

    @patch('backend.services.ip_geolocation_service.get_settings')
    @patch('backend.services.ip_geolocation_service.firestore')
    def test_null_ip_returns_private(self, mock_fs, mock_settings):
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_fs.Client.return_value = MagicMock()

        from backend.services.ip_geolocation_service import IpGeolocationService
        service = IpGeolocationService()
        result = service.lookup_ip("")
        assert result["status"] == "private"
        result2 = service.lookup_ip(None)
        assert result2["status"] == "private"


class TestLookupBatch:
    @patch('backend.services.ip_geolocation_service.get_settings')
    @patch('backend.services.ip_geolocation_service.firestore')
    def test_all_cached(self, mock_fs, mock_settings):
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        cached_data = {
            "status": "success",
            "ip": "8.8.8.8",
            "country_code": "US",
            "cached_at": datetime.utcnow().isoformat(),
        }
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = cached_data
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        from backend.services.ip_geolocation_service import IpGeolocationService
        service = IpGeolocationService()
        results = service.lookup_ips_batch(["8.8.8.8"])
        assert "8.8.8.8" in results
        assert results["8.8.8.8"]["status"] == "success"

    @patch('backend.services.ip_geolocation_service.get_settings')
    @patch('backend.services.ip_geolocation_service.firestore')
    def test_private_ips_in_batch(self, mock_fs, mock_settings):
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_fs.Client.return_value = MagicMock()

        from backend.services.ip_geolocation_service import IpGeolocationService
        service = IpGeolocationService()
        results = service.lookup_ips_batch(["192.168.1.1", "10.0.0.1"])
        assert results["192.168.1.1"]["status"] == "private"
        assert results["10.0.0.1"]["status"] == "private"


class TestAdminEndpoints:
    @pytest.fixture
    def admin_client(self):
        from backend.main import app
        from backend.api.dependencies import require_admin
        from fastapi.testclient import TestClient

        mock_auth = MagicMock()
        mock_auth.is_admin = True
        app.dependency_overrides[require_admin] = lambda: mock_auth

        yield TestClient(app)
        app.dependency_overrides.clear()

    @patch('backend.api.routes.admin.get_ip_geolocation_service')
    def test_single_ip_endpoint(self, mock_get_svc, admin_client):
        mock_svc = MagicMock()
        mock_svc.lookup_ip.return_value = {
            "status": "success",
            "ip": "8.8.8.8",
            "country_code": "US",
        }
        mock_get_svc.return_value = mock_svc

        response = admin_client.get("/api/admin/abuse/ip-info/8.8.8.8")
        assert response.status_code == 200
        assert response.json()["country_code"] == "US"

    @patch('backend.api.routes.admin.get_ip_geolocation_service')
    def test_batch_endpoint(self, mock_get_svc, admin_client):
        mock_svc = MagicMock()
        mock_svc.lookup_ips_batch.return_value = {
            "8.8.8.8": {"status": "success", "country_code": "US"},
            "1.1.1.1": {"status": "success", "country_code": "AU"},
        }
        mock_get_svc.return_value = mock_svc

        response = admin_client.post(
            "/api/admin/abuse/ip-info/batch",
            json={"ips": ["8.8.8.8", "1.1.1.1"]},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["8.8.8.8"]["country_code"] == "US"
        assert data["1.1.1.1"]["country_code"] == "AU"
