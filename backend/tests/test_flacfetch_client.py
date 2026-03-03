"""
Tests for the flacfetch remote HTTP API client.

This module tests FlacfetchClient which handles communication with the
dedicated flacfetch VM for torrent/audio downloads.

These tests use mocked HTTP responses to verify:
- Request formatting (headers, payloads)
- Response parsing
- Error handling
- State management (singleton pattern)
"""
import pytest
from unittest.mock import AsyncMock, Mock, patch, MagicMock
import asyncio

import httpx

from backend.services.flacfetch_client import (
    FlacfetchClient,
    FlacfetchServiceError,
    _format_httpx_error,
    get_flacfetch_client,
    reset_flacfetch_client,
)


class TestFlacfetchClientInit:
    """Test FlacfetchClient initialization."""
    
    def test_init_with_required_params(self):
        """Test client initializes with base_url and api_key."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key-123",
        )
        
        assert client.base_url == "http://localhost:8080"
        assert client.api_key == "test-key-123"
        assert client.timeout == 60  # Default timeout
    
    def test_init_strips_trailing_slash(self):
        """Test base_url trailing slash is stripped."""
        client = FlacfetchClient(
            base_url="http://localhost:8080/",
            api_key="test-key",
        )
        
        assert client.base_url == "http://localhost:8080"
    
    def test_init_with_custom_timeout(self):
        """Test custom timeout is set."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key",
            timeout=120,
        )
        
        assert client.timeout == 120
    
    def test_headers_include_api_key(self):
        """Test _headers() includes X-API-Key."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="secret-key-456",
        )
        
        headers = client._headers()
        
        assert headers["X-API-Key"] == "secret-key-456"
        assert headers["Content-Type"] == "application/json"


class TestFlacfetchClientHealthCheck:
    """Test health_check() method."""
    
    @pytest.mark.asyncio
    async def test_health_check_success(self):
        """Test successful health check."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key",
        )
        
        mock_response = Mock()
        mock_response.json.return_value = {
            "status": "healthy",
            "transmission": {"available": True, "version": "4.0.5"},
            "disk": {"free_gb": 15.5},
        }
        mock_response.raise_for_status = Mock()
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client
            
            result = await client.health_check()
        
        assert result["status"] == "healthy"
        assert result["transmission"]["available"] is True
    
    @pytest.mark.asyncio
    async def test_health_check_degraded_status(self):
        """Test health check with degraded status still succeeds."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key",
        )
        
        mock_response = Mock()
        mock_response.json.return_value = {"status": "degraded"}
        mock_response.raise_for_status = Mock()
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client
            
            result = await client.health_check()
        
        assert result["status"] == "degraded"
    
    @pytest.mark.asyncio
    async def test_health_check_unhealthy_raises_error(self):
        """Test health check with unhealthy status raises error."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key",
        )
        
        mock_response = Mock()
        mock_response.json.return_value = {"status": "unhealthy", "error": "Transmission down"}
        mock_response.raise_for_status = Mock()
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client
            
            with pytest.raises(FlacfetchServiceError) as exc_info:
                await client.health_check()
        
        assert "unhealthy" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_health_check_network_error(self):
        """Test health check handles network errors."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key",
        )
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.RequestError("Connection refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client
            
            with pytest.raises(FlacfetchServiceError) as exc_info:
                await client.health_check()
        
        assert "Cannot reach" in str(exc_info.value)


class TestFlacfetchClientSearch:
    """Test search() method."""
    
    @pytest.mark.asyncio
    async def test_search_success(self):
        """Test successful search returns results."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key",
        )
        
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "search_id": "search_abc123",
            "results": [
                {
                    "title": "Waterloo",
                    "artist": "ABBA",
                    "provider": "RED",
                    "quality": "FLAC 16bit CD",
                    "seeders": 50,
                    "index": 0,
                }
            ],
            "results_count": 1,
        }
        mock_response.raise_for_status = Mock()
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client
            
            result = await client.search("ABBA", "Waterloo")
        
        assert result["search_id"] == "search_abc123"
        assert len(result["results"]) == 1
        assert result["results"][0]["title"] == "Waterloo"
        
        # Verify request was made correctly
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args.kwargs["json"] == {"artist": "ABBA", "title": "Waterloo"}
    
    @pytest.mark.asyncio
    async def test_search_no_results_returns_empty(self):
        """Test search with no results returns empty list."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key",
        )
        
        mock_response = Mock()
        mock_response.status_code = 404
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client
            
            result = await client.search("Unknown", "Artist")
        
        assert result["results"] == []
        assert result["search_id"] is None


class TestFlacfetchClientDownload:
    """Test download() method."""
    
    @pytest.mark.asyncio
    async def test_download_starts_successfully(self):
        """Test download() returns download_id."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key",
        )
        
        mock_response = Mock()
        mock_response.json.return_value = {
            "download_id": "dl_xyz789",
            "status": "queued",
        }
        mock_response.raise_for_status = Mock()
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client
            
            download_id = await client.download(
                search_id="search_abc123",
                result_index=0,
            )
        
        assert download_id == "dl_xyz789"
    
    @pytest.mark.asyncio
    async def test_download_with_gcs_path(self):
        """Test download() includes GCS upload parameters."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key",
        )
        
        mock_response = Mock()
        mock_response.json.return_value = {"download_id": "dl_xyz789"}
        mock_response.raise_for_status = Mock()
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client
            
            await client.download(
                search_id="search_abc123",
                result_index=0,
                gcs_path="uploads/job123/audio/",
            )
        
        # Verify GCS params were included
        call_args = mock_client.post.call_args
        payload = call_args.kwargs["json"]
        assert payload["upload_to_gcs"] is True
        assert payload["gcs_path"] == "uploads/job123/audio/"
    
    @pytest.mark.asyncio
    async def test_download_with_custom_filename(self):
        """Test download() with custom output filename."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key",
        )
        
        mock_response = Mock()
        mock_response.json.return_value = {"download_id": "dl_xyz789"}
        mock_response.raise_for_status = Mock()
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client
            
            await client.download(
                search_id="search_abc123",
                result_index=0,
                output_filename="ABBA - Waterloo",
            )
        
        call_args = mock_client.post.call_args
        payload = call_args.kwargs["json"]
        assert payload["output_filename"] == "ABBA - Waterloo"


class TestFlacfetchClientGetDownloadStatus:
    """Test get_download_status() method."""
    
    @pytest.mark.asyncio
    async def test_get_download_status_success(self):
        """Test getting download status."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key",
        )
        
        mock_response = Mock()
        mock_response.json.return_value = {
            "download_id": "dl_xyz789",
            "status": "downloading",
            "progress": 45.5,
            "download_speed_kbps": 1250.0,
            "peers": 5,
        }
        mock_response.raise_for_status = Mock()
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client
            
            status = await client.get_download_status("dl_xyz789")
        
        assert status["status"] == "downloading"
        assert status["progress"] == 45.5
    
    @pytest.mark.asyncio
    async def test_get_download_status_not_found(self):
        """Test getting status for non-existent download."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key",
        )
        
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.raise_for_status = Mock(
            side_effect=httpx.HTTPStatusError("Not found", request=Mock(), response=mock_response)
        )
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client
            
            with pytest.raises(FlacfetchServiceError) as exc_info:
                await client.get_download_status("nonexistent")
        
        assert "not found" in str(exc_info.value).lower()


class TestFlacfetchClientWaitForDownload:
    """Test wait_for_download() method."""
    
    @pytest.mark.asyncio
    async def test_wait_for_download_completes(self):
        """Test waiting for download to complete."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key",
        )
        
        # Mock get_download_status to return complete status
        async def mock_get_status(download_id):
            return {
                "download_id": download_id,
                "status": "complete",
                "gcs_path": "gs://bucket/uploads/test.flac",
            }
        
        client.get_download_status = mock_get_status
        
        result = await client.wait_for_download("dl_xyz789", timeout=10)
        
        assert result["status"] == "complete"
        assert result["gcs_path"] == "gs://bucket/uploads/test.flac"
    
    @pytest.mark.asyncio
    async def test_wait_for_download_seeding_status_completes(self):
        """Test that 'seeding' status is treated as complete."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key",
        )
        
        async def mock_get_status(download_id):
            return {
                "download_id": download_id,
                "status": "seeding",
                "output_path": "/downloads/test.flac",
            }
        
        client.get_download_status = mock_get_status
        
        result = await client.wait_for_download("dl_xyz789", timeout=10)
        
        assert result["status"] == "seeding"
    
    @pytest.mark.asyncio
    async def test_wait_for_download_failed(self):
        """Test wait raises error when download fails."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key",
        )
        
        async def mock_get_status(download_id):
            return {
                "download_id": download_id,
                "status": "failed",
                "error": "Torrent download failed: no peers",
            }
        
        client.get_download_status = mock_get_status
        
        with pytest.raises(FlacfetchServiceError) as exc_info:
            await client.wait_for_download("dl_xyz789", timeout=10)
        
        assert "failed" in str(exc_info.value).lower()
        assert "no peers" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_wait_for_download_cancelled(self):
        """Test wait raises error when download is cancelled."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key",
        )
        
        async def mock_get_status(download_id):
            return {
                "download_id": download_id,
                "status": "cancelled",
            }
        
        client.get_download_status = mock_get_status
        
        with pytest.raises(FlacfetchServiceError) as exc_info:
            await client.wait_for_download("dl_xyz789", timeout=10)
        
        assert "cancelled" in str(exc_info.value).lower()
    
    @pytest.mark.asyncio
    async def test_wait_for_download_timeout(self):
        """Test wait raises error on timeout."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key",
        )
        
        call_count = 0
        async def mock_get_status(download_id):
            nonlocal call_count
            call_count += 1
            return {
                "download_id": download_id,
                "status": "downloading",
                "progress": 10.0 * call_count,
            }
        
        client.get_download_status = mock_get_status
        
        with pytest.raises(FlacfetchServiceError) as exc_info:
            # Very short timeout with short poll interval
            await client.wait_for_download("dl_xyz789", timeout=0.2, poll_interval=0.1)
        
        assert "timed out" in str(exc_info.value).lower()
    
    @pytest.mark.asyncio
    async def test_wait_for_download_calls_progress_callback(self):
        """Test progress callback is called during wait."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key",
        )
        
        call_count = 0
        async def mock_get_status(download_id):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                return {"status": "complete", "gcs_path": "gs://test"}
            return {"status": "downloading", "progress": 33.0 * call_count}
        
        client.get_download_status = mock_get_status
        
        progress_updates = []
        def progress_callback(status):
            progress_updates.append(status)
        
        await client.wait_for_download(
            "dl_xyz789",
            timeout=10,
            poll_interval=0.01,
            progress_callback=progress_callback,
        )
        
        # Should have received progress updates
        assert len(progress_updates) >= 2


class TestGetFlacfetchClient:
    """Test get_flacfetch_client() singleton factory."""
    
    def test_returns_none_when_not_configured(self):
        """Test returns None when FLACFETCH_API_URL not set."""
        reset_flacfetch_client()
        
        with patch("backend.config.get_settings") as mock_settings:
            mock_settings.return_value = Mock(
                flacfetch_api_url=None,
                flacfetch_api_key=None,
            )
            
            client = get_flacfetch_client()
        
        assert client is None
    
    def test_returns_none_when_key_missing(self):
        """Test returns None when API key not set."""
        reset_flacfetch_client()
        
        with patch("backend.config.get_settings") as mock_settings:
            mock_settings.return_value = Mock(
                flacfetch_api_url="http://localhost:8080",
                flacfetch_api_key=None,
            )
            
            client = get_flacfetch_client()
        
        assert client is None
    
    def test_returns_client_when_configured(self):
        """Test returns FlacfetchClient when both URL and key are set."""
        reset_flacfetch_client()
        
        with patch("backend.config.get_settings") as mock_settings:
            mock_settings.return_value = Mock(
                flacfetch_api_url="http://10.0.0.5:8080",
                flacfetch_api_key="secret-key",
            )
            
            client = get_flacfetch_client()
        
        assert client is not None
        assert isinstance(client, FlacfetchClient)
        assert client.base_url == "http://10.0.0.5:8080"
        assert client.api_key == "secret-key"
    
    def test_returns_same_instance(self):
        """Test singleton returns same instance."""
        reset_flacfetch_client()
        
        with patch("backend.config.get_settings") as mock_settings:
            mock_settings.return_value = Mock(
                flacfetch_api_url="http://localhost:8080",
                flacfetch_api_key="test-key",
            )
            
            client1 = get_flacfetch_client()
            client2 = get_flacfetch_client()
        
        assert client1 is client2
    
    def test_reset_clears_singleton(self):
        """Test reset_flacfetch_client() clears the singleton."""
        reset_flacfetch_client()
        
        with patch("backend.config.get_settings") as mock_settings:
            mock_settings.return_value = Mock(
                flacfetch_api_url="http://localhost:8080",
                flacfetch_api_key="test-key",
            )
            
            client1 = get_flacfetch_client()
            reset_flacfetch_client()
            
            # Change the settings
            mock_settings.return_value = Mock(
                flacfetch_api_url="http://other:8080",
                flacfetch_api_key="other-key",
            )
            
            client2 = get_flacfetch_client()
        
        assert client1 is not client2
        assert client2.base_url == "http://other:8080"


class TestFlacfetchRetryLogic:
    """Test retry logic for flacfetch download methods."""

    @pytest.mark.asyncio
    async def test_download_by_id_retries_on_request_error(self):
        """Test download_by_id retries on network errors (RequestError)."""
        # Mock config with retry settings
        mock_settings = Mock()
        mock_settings.flacfetch_retry_max_attempts = 3
        mock_settings.flacfetch_retry_min_wait = 0.01  # Fast retries for testing
        mock_settings.flacfetch_retry_max_wait = 0.02

        with patch("backend.services.flacfetch_client.get_settings", return_value=mock_settings):
            client = FlacfetchClient(
                base_url="http://test:8080",
                api_key="test-key",
            )

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client_cls.return_value.__aenter__.return_value = mock_client

                # Fail twice with network error, then succeed
                mock_response = Mock()
                mock_response.json.return_value = {"download_id": "test123"}
                mock_response.raise_for_status = Mock()

                mock_client.post.side_effect = [
                    httpx.RequestError("Connection timeout"),
                    httpx.RequestError("Connection refused"),
                    mock_response,  # Success on third attempt
                ]

                result = await client.download_by_id("YouTube", "abc123")

                assert result == "test123"
                assert mock_client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_download_by_id_no_retry_on_4xx(self):
        """Test download_by_id does NOT retry on 4xx client errors."""
        mock_settings = Mock()
        mock_settings.flacfetch_retry_max_attempts = 3
        mock_settings.flacfetch_retry_min_wait = 0.01
        mock_settings.flacfetch_retry_max_wait = 0.02

        with patch("backend.services.flacfetch_client.get_settings", return_value=mock_settings):
            client = FlacfetchClient(
                base_url="http://test:8080",
                api_key="test-key",
            )

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client_cls.return_value.__aenter__.return_value = mock_client

                # 404 error
                mock_response = Mock()
                mock_response.status_code = 404
                mock_response.text = "Not found"

                def raise_status_error():
                    raise httpx.HTTPStatusError(
                        "Not found",
                        request=Mock(),
                        response=mock_response
                    )

                mock_response.raise_for_status.side_effect = raise_status_error
                mock_client.post.return_value = mock_response

                with pytest.raises(FlacfetchServiceError, match="404"):
                    await client.download_by_id("YouTube", "abc123")

                # Should only attempt once (no retries for 4xx)
                assert mock_client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_download_by_id_retries_on_5xx(self):
        """Test download_by_id retries on 5xx server errors."""
        mock_settings = Mock()
        mock_settings.flacfetch_retry_max_attempts = 3
        mock_settings.flacfetch_retry_min_wait = 0.01
        mock_settings.flacfetch_retry_max_wait = 0.02

        with patch("backend.services.flacfetch_client.get_settings", return_value=mock_settings):
            client = FlacfetchClient(
                base_url="http://test:8080",
                api_key="test-key",
            )

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client_cls.return_value.__aenter__.return_value = mock_client

                # 503 error twice, then success
                mock_503_response = Mock()
                mock_503_response.status_code = 503
                mock_503_response.text = "Service Unavailable"

                def raise_503():
                    raise httpx.HTTPStatusError(
                        "Service Unavailable",
                        request=Mock(),
                        response=mock_503_response
                    )

                mock_success_response = Mock()
                mock_success_response.json.return_value = {"download_id": "test456"}
                mock_success_response.raise_for_status = Mock()

                mock_503_response_1 = Mock()
                mock_503_response_1.status_code = 503
                mock_503_response_1.text = "Service Unavailable"
                mock_503_response_1.raise_for_status.side_effect = raise_503

                mock_503_response_2 = Mock()
                mock_503_response_2.status_code = 503
                mock_503_response_2.text = "Service Unavailable"
                mock_503_response_2.raise_for_status.side_effect = raise_503

                mock_client.post.side_effect = [
                    mock_503_response_1,
                    mock_503_response_2,
                    mock_success_response,
                ]

                result = await client.download_by_id("YouTube", "abc123")

                assert result == "test456"
                assert mock_client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_download_by_id_fails_after_max_retries(self):
        """Test download_by_id fails with clear message after all retries exhausted."""
        mock_settings = Mock()
        mock_settings.flacfetch_retry_max_attempts = 3
        mock_settings.flacfetch_retry_min_wait = 0.01
        mock_settings.flacfetch_retry_max_wait = 0.02

        with patch("backend.services.flacfetch_client.get_settings", return_value=mock_settings):
            client = FlacfetchClient(
                base_url="http://test:8080",
                api_key="test-key",
            )

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client_cls.return_value.__aenter__.return_value = mock_client

                # All attempts fail
                mock_client.post.side_effect = httpx.RequestError("Connection timeout")

                with pytest.raises(FlacfetchServiceError, match="request failed"):
                    await client.download_by_id("YouTube", "abc123")

                # Should have attempted max times
                assert mock_client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_download_retries_same_as_download_by_id(self):
        """Test download() method has same retry behavior as download_by_id()."""
        mock_settings = Mock()
        mock_settings.flacfetch_retry_max_attempts = 3
        mock_settings.flacfetch_retry_min_wait = 0.01
        mock_settings.flacfetch_retry_max_wait = 0.02

        with patch("backend.services.flacfetch_client.get_settings", return_value=mock_settings):
            client = FlacfetchClient(
                base_url="http://test:8080",
                api_key="test-key",
            )

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client_cls.return_value.__aenter__.return_value = mock_client

                # Fail twice, then succeed
                mock_response = Mock()
                mock_response.json.return_value = {"download_id": "test789"}
                mock_response.raise_for_status = Mock()

                mock_client.post.side_effect = [
                    httpx.RequestError("Network error"),
                    httpx.RequestError("Network error"),
                    mock_response,
                ]

                result = await client.download(
                    search_id="search123",
                    result_index=0,
                )

                assert result == "test789"
                assert mock_client.post.call_count == 3


class TestFormatHttpxError:
    """Test _format_httpx_error helper for descriptive error messages."""

    def test_formats_error_with_message(self):
        """Test formatting when str(error) has content."""
        error = httpx.ConnectError("Connection refused")
        result = _format_httpx_error(error)
        assert result == "ConnectError: Connection refused"

    def test_formats_error_with_empty_message(self):
        """Test formatting when str(error) is empty -- the exact case from job c6b1b00f."""
        request = httpx.Request("GET", "http://10.128.0.41:8080/download/dl_abc/status")
        error = httpx.ConnectError("", request=request)
        result = _format_httpx_error(error)
        # Should include class name and URL context, NOT be empty
        assert "ConnectError" in result
        assert "GET" in result
        assert "10.128.0.41" in result

    def test_formats_error_with_empty_message_no_request(self):
        """Test formatting when both str and request are unavailable."""
        # Use base RequestError which doesn't require request in constructor
        error = httpx.RequestError("")
        result = _format_httpx_error(error)
        # Should at minimum return the class name
        assert result == "RequestError"

    def test_formats_timeout_error(self):
        """Test formatting of timeout errors."""
        error = httpx.ReadTimeout("timed out")
        result = _format_httpx_error(error)
        assert result == "ReadTimeout: timed out"

    def test_formats_non_httpx_error(self):
        """Test formatting of a generic exception."""
        error = ValueError("bad value")
        result = _format_httpx_error(error)
        assert result == "ValueError: bad value"


class TestWaitForDownloadRetryTolerance:
    """Test that wait_for_download tolerates transient poll failures."""

    @pytest.mark.asyncio
    async def test_tolerates_single_poll_failure(self):
        """Test that a single transient error doesn't fail the download."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key",
        )

        call_count = 0
        async def mock_get_status(download_id):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise httpx.ConnectError("transient failure")
            if call_count >= 3:
                return {"status": "complete", "gcs_path": "gs://bucket/test.flac"}
            return {"status": "downloading", "progress": 50.0}

        client.get_download_status = mock_get_status

        result = await client.wait_for_download(
            "dl_test", timeout=10, poll_interval=0.01
        )
        assert result["status"] == "complete"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_tolerates_two_consecutive_failures(self):
        """Test that two consecutive errors don't fail with default threshold of 3."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key",
        )
        mock_request = httpx.Request("GET", "http://localhost:8080/download/dl_test/status")

        call_count = 0
        async def mock_get_status(download_id):
            nonlocal call_count
            call_count += 1
            if call_count in (2, 3):
                raise httpx.ConnectError("", request=mock_request)
            if call_count >= 4:
                return {"status": "complete", "gcs_path": "gs://bucket/test.flac"}
            return {"status": "downloading", "progress": 25.0}

        client.get_download_status = mock_get_status

        result = await client.wait_for_download(
            "dl_test", timeout=10, poll_interval=0.01
        )
        assert result["status"] == "complete"

    @pytest.mark.asyncio
    async def test_fails_after_max_consecutive_errors(self):
        """Test that 3 consecutive errors (default) fails with descriptive message."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key",
        )

        call_count = 0
        async def mock_get_status(download_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"status": "downloading", "progress": 10.0}
            # All subsequent polls fail
            raise httpx.ConnectError("connection reset")

        client.get_download_status = mock_get_status

        with pytest.raises(FlacfetchServiceError) as exc_info:
            await client.wait_for_download(
                "dl_test", timeout=10, poll_interval=0.01
            )

        error_msg = str(exc_info.value)
        assert "3 consecutive" in error_msg
        assert "ConnectError" in error_msg

    @pytest.mark.asyncio
    async def test_resets_error_count_on_success(self):
        """Test that a successful poll resets the consecutive error counter."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key",
        )
        mock_request = httpx.Request("GET", "http://localhost:8080/download/dl_test/status")

        call_count = 0
        async def mock_get_status(download_id):
            nonlocal call_count
            call_count += 1
            # Fail twice, succeed, fail twice, succeed, complete
            if call_count in (1, 2):
                raise httpx.ConnectError("", request=mock_request)
            if call_count == 3:
                return {"status": "downloading", "progress": 25.0}
            if call_count in (4, 5):
                raise httpx.ConnectError("", request=mock_request)
            if call_count == 6:
                return {"status": "downloading", "progress": 75.0}
            return {"status": "complete", "gcs_path": "gs://bucket/test.flac"}

        client.get_download_status = mock_get_status

        result = await client.wait_for_download(
            "dl_test", timeout=10, poll_interval=0.01
        )
        assert result["status"] == "complete"
        assert call_count == 7  # 2 fail + 1 ok + 2 fail + 1 ok + 1 complete

    @pytest.mark.asyncio
    async def test_custom_max_consecutive_errors(self):
        """Test configuring a different consecutive error threshold."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key",
        )

        async def mock_get_status(download_id):
            raise httpx.ReadError("read failed")

        client.get_download_status = mock_get_status

        with pytest.raises(FlacfetchServiceError) as exc_info:
            await client.wait_for_download(
                "dl_test", timeout=10, poll_interval=0.01,
                max_consecutive_errors=5,
            )

        assert "5 consecutive" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_error_message_includes_error_type_for_empty_str(self):
        """Test the exact scenario from job c6b1b00f: empty httpx error string."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key",
        )
        mock_request = httpx.Request("GET", "http://10.128.0.41:8080/download/dl_e93031543e96/status")

        async def mock_get_status(download_id):
            # Simulate the exact error from job c6b1b00f -- empty string
            raise httpx.ConnectError("", request=mock_request)

        client.get_download_status = mock_get_status

        with pytest.raises(FlacfetchServiceError) as exc_info:
            await client.wait_for_download(
                "dl_test", timeout=10, poll_interval=0.01,
            )

        error_msg = str(exc_info.value)
        # Must contain useful info, NOT just "Status request failed: "
        assert "ConnectError" in error_msg
        assert "consecutive" in error_msg

    @pytest.mark.asyncio
    async def test_application_errors_still_fail_immediately(self):
        """Test that FlacfetchServiceError (download not found, etc.) still fails immediately."""
        client = FlacfetchClient(
            base_url="http://localhost:8080",
            api_key="test-key",
        )

        async def mock_get_status(download_id):
            raise FlacfetchServiceError("Download not found: dl_test")

        client.get_download_status = mock_get_status

        with pytest.raises(FlacfetchServiceError, match="Download not found"):
            await client.wait_for_download(
                "dl_test", timeout=10, poll_interval=0.01,
            )

