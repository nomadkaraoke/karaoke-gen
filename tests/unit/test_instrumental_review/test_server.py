"""
Unit tests for the InstrumentalReviewServer module.

Tests the local FastAPI server for instrumental review, including:
- Server initialization
- Request/Response models
- API endpoint logic
"""

import os
import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

from karaoke_gen.instrumental_review.server import (
    InstrumentalReviewServer,
    MuteRegionRequest,
    CreateCustomRequest,
    SelectionRequest,
)
from karaoke_gen.instrumental_review import (
    AnalysisResult,
    AudibleSegment,
    RecommendedSelection,
)


class TestRequestModels:
    """Test Pydantic request models."""
    
    def test_mute_region_request_valid(self):
        """Test creating a valid MuteRegionRequest."""
        region = MuteRegionRequest(start_seconds=1.0, end_seconds=2.0)
        assert region.start_seconds == 1.0
        assert region.end_seconds == 2.0
    
    def test_mute_region_request_negative_values(self):
        """Test MuteRegionRequest accepts negative values (validation elsewhere)."""
        region = MuteRegionRequest(start_seconds=-1.0, end_seconds=2.0)
        assert region.start_seconds == -1.0
    
    def test_create_custom_request_valid(self):
        """Test creating a valid CreateCustomRequest."""
        request = CreateCustomRequest(
            mute_regions=[
                MuteRegionRequest(start_seconds=1.0, end_seconds=2.0),
                MuteRegionRequest(start_seconds=5.0, end_seconds=6.0),
            ]
        )
        assert len(request.mute_regions) == 2
    
    def test_create_custom_request_empty(self):
        """Test CreateCustomRequest with empty mute_regions."""
        request = CreateCustomRequest(mute_regions=[])
        assert len(request.mute_regions) == 0
    
    def test_selection_request_valid(self):
        """Test creating a valid SelectionRequest."""
        request = SelectionRequest(selection="clean")
        assert request.selection == "clean"
    
    def test_selection_request_any_string(self):
        """Test SelectionRequest accepts any string (validation in endpoint)."""
        request = SelectionRequest(selection="invalid")
        assert request.selection == "invalid"


class TestServerInitialization:
    """Test InstrumentalReviewServer initialization."""
    
    @pytest.fixture
    def mock_analysis(self):
        """Create a mock analysis result."""
        return AnalysisResult(
            has_audible_content=True,
            total_duration_seconds=180.0,
            audible_segments=[
                AudibleSegment(
                    start_seconds=10.0,
                    end_seconds=15.0,
                    duration_seconds=5.0,
                    avg_amplitude_db=-30.0,
                    peak_amplitude_db=-25.0,
                )
            ],
            recommended_selection=RecommendedSelection.REVIEW_NEEDED,
            total_audible_duration_seconds=5.0,
            audible_percentage=2.78,
            silence_threshold_db=-40.0,
        )
    
    @pytest.fixture
    def temp_files(self, tmp_path):
        """Create temporary audio files for testing."""
        backing = tmp_path / "backing.flac"
        clean = tmp_path / "clean.flac"
        waveform = tmp_path / "waveform.png"
        
        backing.write_bytes(b"fake audio")
        clean.write_bytes(b"fake audio")
        waveform.write_bytes(b"fake png")
        
        return {
            "output_dir": str(tmp_path),
            "backing_vocals_path": str(backing),
            "clean_instrumental_path": str(clean),
            "waveform_path": str(waveform),
        }
    
    def test_server_initialization(self, mock_analysis, temp_files):
        """Test server initializes with all required parameters."""
        server = InstrumentalReviewServer(
            output_dir=temp_files["output_dir"],
            base_name="Artist - Title",
            analysis=mock_analysis,
            waveform_path=temp_files["waveform_path"],
            backing_vocals_path=temp_files["backing_vocals_path"],
            clean_instrumental_path=temp_files["clean_instrumental_path"],
        )
        
        assert server.output_dir == temp_files["output_dir"]
        assert server.base_name == "Artist - Title"
        assert server.analysis == mock_analysis
        assert server.backing_vocals_path == temp_files["backing_vocals_path"]
        assert server.clean_instrumental_path == temp_files["clean_instrumental_path"]
        assert server.waveform_path == temp_files["waveform_path"]
        assert server.with_backing_path is None
        assert server.custom_instrumental_path is None
        assert server.selection is None
    
    def test_server_initialization_with_optional_paths(self, mock_analysis, temp_files):
        """Test server initializes with optional with_backing_path."""
        with_backing = Path(temp_files["output_dir"]) / "with_backing.flac"
        with_backing.write_bytes(b"fake audio")
        
        server = InstrumentalReviewServer(
            output_dir=temp_files["output_dir"],
            base_name="Artist - Title",
            analysis=mock_analysis,
            waveform_path=temp_files["waveform_path"],
            backing_vocals_path=temp_files["backing_vocals_path"],
            clean_instrumental_path=temp_files["clean_instrumental_path"],
            with_backing_path=str(with_backing),
        )
        
        assert server.with_backing_path == str(with_backing)
    
    def test_server_initialization_with_original_audio(self, mock_analysis, temp_files):
        """Test server initializes with optional original_audio_path."""
        original_audio = Path(temp_files["output_dir"]) / "original.wav"
        original_audio.write_bytes(b"fake audio")
        
        server = InstrumentalReviewServer(
            output_dir=temp_files["output_dir"],
            base_name="Artist - Title",
            analysis=mock_analysis,
            waveform_path=temp_files["waveform_path"],
            backing_vocals_path=temp_files["backing_vocals_path"],
            clean_instrumental_path=temp_files["clean_instrumental_path"],
            original_audio_path=str(original_audio),
        )
        
        assert server.original_audio_path == str(original_audio)
    
    def test_get_selection_raises_without_selection(self, mock_analysis, temp_files):
        """Test get_selection raises when no selection made."""
        server = InstrumentalReviewServer(
            output_dir=temp_files["output_dir"],
            base_name="Artist - Title",
            analysis=mock_analysis,
            waveform_path=temp_files["waveform_path"],
            backing_vocals_path=temp_files["backing_vocals_path"],
            clean_instrumental_path=temp_files["clean_instrumental_path"],
        )
        
        with pytest.raises(ValueError, match="No selection has been made"):
            server.get_selection()
    
    def test_get_selection_returns_selection(self, mock_analysis, temp_files):
        """Test get_selection returns the selection after it's set."""
        server = InstrumentalReviewServer(
            output_dir=temp_files["output_dir"],
            base_name="Artist - Title",
            analysis=mock_analysis,
            waveform_path=temp_files["waveform_path"],
            backing_vocals_path=temp_files["backing_vocals_path"],
            clean_instrumental_path=temp_files["clean_instrumental_path"],
        )
        
        server.selection = "clean"
        assert server.get_selection() == "clean"
    
    def test_get_custom_instrumental_path_none_by_default(self, mock_analysis, temp_files):
        """Test custom instrumental path is None by default."""
        server = InstrumentalReviewServer(
            output_dir=temp_files["output_dir"],
            base_name="Artist - Title",
            analysis=mock_analysis,
            waveform_path=temp_files["waveform_path"],
            backing_vocals_path=temp_files["backing_vocals_path"],
            clean_instrumental_path=temp_files["clean_instrumental_path"],
        )
        
        assert server.get_custom_instrumental_path() is None
    
    def test_get_custom_instrumental_path_returns_path(self, mock_analysis, temp_files):
        """Test custom instrumental path is returned when set."""
        server = InstrumentalReviewServer(
            output_dir=temp_files["output_dir"],
            base_name="Artist - Title",
            analysis=mock_analysis,
            waveform_path=temp_files["waveform_path"],
            backing_vocals_path=temp_files["backing_vocals_path"],
            clean_instrumental_path=temp_files["clean_instrumental_path"],
        )
        
        server.custom_instrumental_path = "/path/to/custom.flac"
        assert server.get_custom_instrumental_path() == "/path/to/custom.flac"


class TestServerAppCreation:
    """Test FastAPI app creation and routes."""
    
    @pytest.fixture
    def mock_analysis(self):
        """Create a mock analysis result."""
        return AnalysisResult(
            has_audible_content=True,
            total_duration_seconds=180.0,
            audible_segments=[],
            recommended_selection=RecommendedSelection.CLEAN,
            total_audible_duration_seconds=0.0,
            audible_percentage=0.0,
            silence_threshold_db=-40.0,
        )
    
    @pytest.fixture
    def temp_files(self, tmp_path):
        """Create temporary files."""
        backing = tmp_path / "backing.flac"
        clean = tmp_path / "clean.flac"
        waveform = tmp_path / "waveform.png"
        
        backing.write_bytes(b"fake audio")
        clean.write_bytes(b"fake audio")
        waveform.write_bytes(b"fake png")
        
        return {
            "output_dir": str(tmp_path),
            "backing_vocals_path": str(backing),
            "clean_instrumental_path": str(clean),
            "waveform_path": str(waveform),
        }
    
    def test_create_app_returns_fastapi_app(self, mock_analysis, temp_files):
        """Test _create_app returns a FastAPI application."""
        from fastapi import FastAPI
        
        server = InstrumentalReviewServer(
            output_dir=temp_files["output_dir"],
            base_name="Artist - Title",
            analysis=mock_analysis,
            waveform_path=temp_files["waveform_path"],
            backing_vocals_path=temp_files["backing_vocals_path"],
            clean_instrumental_path=temp_files["clean_instrumental_path"],
        )
        
        app = server._create_app()
        assert isinstance(app, FastAPI)
    
    def test_app_has_cors_middleware(self, mock_analysis, temp_files):
        """Test app has CORS middleware configured."""
        server = InstrumentalReviewServer(
            output_dir=temp_files["output_dir"],
            base_name="Artist - Title",
            analysis=mock_analysis,
            waveform_path=temp_files["waveform_path"],
            backing_vocals_path=temp_files["backing_vocals_path"],
            clean_instrumental_path=temp_files["clean_instrumental_path"],
        )
        
        app = server._create_app()
        
        # Check middleware is configured (FastAPI stores middleware in user_middleware)
        middleware_classes = [m.cls.__name__ for m in app.user_middleware]
        assert "CORSMiddleware" in middleware_classes
    
    def test_server_serves_nextjs_frontend(self, mock_analysis, temp_files):
        """Test server serves the unified Next.js frontend."""
        from fastapi.testclient import TestClient

        server = InstrumentalReviewServer(
            output_dir=temp_files["output_dir"],
            base_name="Artist - Title",
            analysis=mock_analysis,
            waveform_path=temp_files["waveform_path"],
            backing_vocals_path=temp_files["backing_vocals_path"],
            clean_instrumental_path=temp_files["clean_instrumental_path"],
        )

        app = server._create_app()
        client = TestClient(app)

        # Root should redirect to /app/jobs/local/instrumental
        response = client.get("/", follow_redirects=False)
        assert response.status_code in (302, 307)  # Accept either redirect type
        assert response.headers["location"] == "/app/jobs/local/instrumental"

    def test_nextjs_frontend_route_returns_html(self, mock_analysis, temp_files):
        """Test the Next.js frontend route serves HTML."""
        from fastapi.testclient import TestClient

        server = InstrumentalReviewServer(
            output_dir=temp_files["output_dir"],
            base_name="Artist - Title",
            analysis=mock_analysis,
            waveform_path=temp_files["waveform_path"],
            backing_vocals_path=temp_files["backing_vocals_path"],
            clean_instrumental_path=temp_files["clean_instrumental_path"],
        )

        app = server._create_app()
        client = TestClient(app)

        # The instrumental route should return HTML
        response = client.get("/app/jobs/local/instrumental")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "<!DOCTYPE html>" in response.text


class TestServerStop:
    """Test server stop functionality."""
    
    @pytest.fixture
    def mock_analysis(self):
        """Create a mock analysis result."""
        return AnalysisResult(
            has_audible_content=False,
            total_duration_seconds=180.0,
            audible_segments=[],
            recommended_selection=RecommendedSelection.CLEAN,
            total_audible_duration_seconds=0.0,
            audible_percentage=0.0,
            silence_threshold_db=-40.0,
        )
    
    @pytest.fixture
    def temp_files(self, tmp_path):
        """Create temporary files."""
        backing = tmp_path / "backing.flac"
        clean = tmp_path / "clean.flac"
        waveform = tmp_path / "waveform.png"
        
        backing.write_bytes(b"fake audio")
        clean.write_bytes(b"fake audio")
        waveform.write_bytes(b"fake png")
        
        return {
            "output_dir": str(tmp_path),
            "backing_vocals_path": str(backing),
            "clean_instrumental_path": str(clean),
            "waveform_path": str(waveform),
        }
    
    def test_stop_sets_shutdown_event(self, mock_analysis, temp_files):
        """Test stop() sets the shutdown event."""
        server = InstrumentalReviewServer(
            output_dir=temp_files["output_dir"],
            base_name="Artist - Title",
            analysis=mock_analysis,
            waveform_path=temp_files["waveform_path"],
            backing_vocals_path=temp_files["backing_vocals_path"],
            clean_instrumental_path=temp_files["clean_instrumental_path"],
        )
        
        assert not server._shutdown_event.is_set()
        server.stop()
        assert server._shutdown_event.is_set()


class TestPortFinding:
    """Test automatic port finding for concurrent CLI instances."""
    
    def test_is_port_available_returns_true_for_free_port(self):
        """Test _is_port_available returns True for an available port."""
        import socket
        # Find a free port by binding to 0
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(('127.0.0.1', 0))
            free_port = sock.getsockname()[1]
        
        # The port should now be free
        assert InstrumentalReviewServer._is_port_available('127.0.0.1', free_port) is True
    
    def test_is_port_available_returns_false_for_used_port(self):
        """Test _is_port_available returns False when port is in use."""
        import socket
        # Bind to a port to make it unavailable
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('127.0.0.1', 0))
            used_port = sock.getsockname()[1]
            sock.listen(1)
            
            # The port should be unavailable while socket is bound
            assert InstrumentalReviewServer._is_port_available('127.0.0.1', used_port) is False
    
    def test_find_available_port_returns_preferred_if_free(self):
        """Test _find_available_port returns preferred port when available."""
        import socket
        # Find a free port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(('127.0.0.1', 0))
            free_port = sock.getsockname()[1]
        
        # Should return the same port
        result = InstrumentalReviewServer._find_available_port('127.0.0.1', free_port)
        assert result == free_port
    
    def test_find_available_port_tries_next_ports_when_preferred_busy(self):
        """Test _find_available_port tries subsequent ports when preferred is busy."""
        import socket
        # Bind to a port to make it unavailable
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('127.0.0.1', 0))
            used_port = sock.getsockname()[1]
            sock.listen(1)
            
            # Should return a different port (likely used_port + 1 if available)
            result = InstrumentalReviewServer._find_available_port('127.0.0.1', used_port)
            assert result != used_port
            assert result > used_port  # Should be one of the next ports
    
    def test_find_available_port_skips_multiple_busy_ports(self):
        """Test _find_available_port skips multiple busy ports."""
        import socket
        sockets = []
        try:
            # Bind to 3 consecutive ports
            base_port = 49000  # Use a high port range
            for offset in range(3):
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind(('127.0.0.1', base_port + offset))
                    sock.listen(1)
                    sockets.append(sock)
                except OSError:
                    sock.close()
                    # Port might already be in use, that's fine for this test
            
            if len(sockets) >= 2:
                # Should find a port beyond the bound ones
                result = InstrumentalReviewServer._find_available_port('127.0.0.1', base_port)
                assert result >= base_port + len(sockets)
        finally:
            for sock in sockets:
                sock.close()
    
    def test_find_available_port_falls_back_to_os_assigned(self):
        """Test _find_available_port falls back to OS-assigned port if needed."""
        # This should always work since we pass max_attempts=1 and use an invalid port
        # We can't easily test the fallback without binding to 100 consecutive ports,
        # but we can verify the method doesn't raise and returns a valid port
        result = InstrumentalReviewServer._find_available_port('127.0.0.1', 8765, max_attempts=100)
        assert 1024 <= result <= 65535


class TestServerAPIEndpoints:
    """Test FastAPI endpoint handlers using TestClient."""
    
    @pytest.fixture
    def mock_analysis(self):
        """Create a mock analysis result."""
        return AnalysisResult(
            has_audible_content=True,
            total_duration_seconds=180.0,
            audible_segments=[
                AudibleSegment(
                    start_seconds=10.0,
                    end_seconds=15.0,
                    duration_seconds=5.0,
                    avg_amplitude_db=-30.0,
                    peak_amplitude_db=-25.0,
                )
            ],
            recommended_selection=RecommendedSelection.REVIEW_NEEDED,
            total_audible_duration_seconds=5.0,
            audible_percentage=2.78,
            silence_threshold_db=-40.0,
        )
    
    @pytest.fixture
    def server_with_files(self, tmp_path, mock_analysis):
        """Create server with real temp files."""
        from pydub import AudioSegment
        from pydub.generators import Sine
        
        # Create a real audio file for testing
        audio = Sine(440).to_audio_segment(duration=1000)  # 1 second of 440Hz sine
        
        backing = tmp_path / "backing.flac"
        clean = tmp_path / "clean.flac"
        waveform = tmp_path / "waveform.png"
        
        audio.export(str(backing), format="flac")
        audio.export(str(clean), format="flac")
        waveform.write_bytes(b"\x89PNG\r\n\x1a\n")  # PNG magic bytes
        
        server = InstrumentalReviewServer(
            output_dir=str(tmp_path),
            base_name="Artist - Title",
            analysis=mock_analysis,
            waveform_path=str(waveform),
            backing_vocals_path=str(backing),
            clean_instrumental_path=str(clean),
        )
        
        return server
    
    @pytest.fixture
    def client(self, server_with_files):
        """Create TestClient for the server."""
        from fastapi.testclient import TestClient
        app = server_with_files._create_app()
        return TestClient(app)
    
    def test_root_redirects_to_instrumental(self, client):
        """Test root endpoint redirects to the instrumental review page."""
        response = client.get("/", follow_redirects=False)
        assert response.status_code in (302, 307)  # Accept either redirect type
        assert response.headers["location"] == "/app/jobs/local/instrumental"

    def test_instrumental_route_returns_html(self, client):
        """Test instrumental route returns HTML page."""
        response = client.get("/app/jobs/local/instrumental")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        # Next.js pre-renders the page with dynamic content loaded client-side
        assert "<!DOCTYPE html>" in response.text
    
    def test_get_analysis_returns_data(self, client, mock_analysis):
        """Test analysis endpoint returns correct data."""
        response = client.get("/api/jobs/local/instrumental-analysis")
        assert response.status_code == 200
        
        data = response.json()
        assert data["job_id"] == "local"
        assert data["status"] == "awaiting_instrumental_selection"
        assert data["analysis"]["has_audible_content"] == True
        assert data["analysis"]["total_duration_seconds"] == 180.0
        assert len(data["analysis"]["audible_segments"]) == 1
        assert data["audio_urls"]["backing_vocals"] is not None
        assert data["audio_urls"]["clean_instrumental"] is not None
    
    def test_get_analysis_with_original_audio(self, tmp_path, mock_analysis):
        """Test analysis endpoint includes has_original flag when original audio is provided."""
        from fastapi.testclient import TestClient
        from pydub import AudioSegment
        from pydub.generators import Sine
        
        # Create real audio files for testing
        audio = Sine(440).to_audio_segment(duration=1000)  # 1 second of 440Hz sine
        
        backing = tmp_path / "backing.flac"
        clean = tmp_path / "clean.flac"
        original = tmp_path / "original.wav"
        waveform = tmp_path / "waveform.png"
        
        audio.export(str(backing), format="flac")
        audio.export(str(clean), format="flac")
        audio.export(str(original), format="wav")
        waveform.write_bytes(b"\x89PNG\r\n\x1a\n")  # PNG magic bytes
        
        # Create server with original audio
        server = InstrumentalReviewServer(
            output_dir=str(tmp_path),
            base_name="Artist - Title",
            analysis=mock_analysis,
            waveform_path=str(waveform),
            backing_vocals_path=str(backing),
            clean_instrumental_path=str(clean),
            original_audio_path=str(original),
        )
        
        client = TestClient(server._create_app())
        response = client.get("/api/jobs/local/instrumental-analysis")
        assert response.status_code == 200
        
        data = response.json()
        assert data["has_original"] == True
        assert data["audio_urls"]["original"] == "/api/audio/original"
    
    def test_get_waveform_data_returns_amplitudes(self, client):
        """Test waveform data endpoint returns amplitudes."""
        response = client.get("/api/jobs/local/waveform-data?num_points=100")
        assert response.status_code == 200
        
        data = response.json()
        assert "amplitudes" in data
        assert "duration" in data
        assert isinstance(data["amplitudes"], list)
        assert len(data["amplitudes"]) == 100
    
    def test_get_waveform_data_validates_num_points(self, client):
        """Test waveform data validates num_points parameter."""
        # Test invalid num_points (0)
        response = client.get("/api/jobs/local/waveform-data?num_points=0")
        assert response.status_code == 400
        assert "num_points must be between" in response.json()["detail"]
        
        # Test invalid num_points (negative)
        response = client.get("/api/jobs/local/waveform-data?num_points=-10")
        assert response.status_code == 400
        
        # Test invalid num_points (too large)
        response = client.get("/api/jobs/local/waveform-data?num_points=100000")
        assert response.status_code == 400
    
    def test_stream_audio_returns_file(self, client):
        """Test audio streaming endpoint returns audio file."""
        response = client.get("/api/audio/backing_vocals")
        assert response.status_code == 200
        assert "audio/flac" in response.headers["content-type"]
    
    def test_stream_audio_invalid_type(self, client):
        """Test audio streaming with invalid stem type."""
        response = client.get("/api/audio/invalid_type")
        assert response.status_code == 404
    
    def test_get_waveform_image(self, client):
        """Test waveform image endpoint."""
        response = client.get("/api/waveform")
        assert response.status_code == 200
        assert "image/png" in response.headers["content-type"]
    
    def test_select_instrumental_clean(self, client, server_with_files):
        """Test selecting clean instrumental."""
        response = client.post(
            "/api/jobs/local/select-instrumental",
            json={"selection": "clean"}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert server_with_files.selection == "clean"
    
    def test_select_instrumental_with_backing(self, client, server_with_files):
        """Test selecting instrumental with backing vocals."""
        response = client.post(
            "/api/jobs/local/select-instrumental",
            json={"selection": "with_backing"}
        )
        assert response.status_code == 200
        assert server_with_files.selection == "with_backing"
    
    def test_select_instrumental_invalid(self, client):
        """Test selecting invalid option."""
        response = client.post(
            "/api/jobs/local/select-instrumental",
            json={"selection": "invalid"}
        )
        assert response.status_code == 400
        assert "Invalid selection" in response.json()["detail"]
    
    def test_create_custom_instrumental(self, client, server_with_files, tmp_path):
        """Test creating custom instrumental."""
        response = client.post(
            "/api/jobs/local/create-custom-instrumental",
            json={
                "mute_regions": [
                    {"start_seconds": 0.1, "end_seconds": 0.3},
                ]
            }
        )
        assert response.status_code == 200
        
        data = response.json()
        assert data["status"] == "success"
        assert data["custom_instrumental_url"] == "/api/audio/custom_instrumental"
        assert server_with_files.custom_instrumental_path is not None
    
    def test_create_custom_instrumental_empty_regions(self, client):
        """Test creating custom instrumental with no regions fails."""
        response = client.post(
            "/api/jobs/local/create-custom-instrumental",
            json={"mute_regions": []}
        )
        assert response.status_code == 400
        assert "No mute regions" in response.json()["detail"]
