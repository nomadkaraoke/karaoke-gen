"""
Local FastAPI server for instrumental review.

This module provides a local HTTP server that serves the instrumental review
UI for local CLI usage. It provides the same API endpoints as the cloud backend
to enable UI reuse.

Similar pattern to LyricsTranscriber's ReviewServer.
"""

import logging
import os
import socket
import threading
import webbrowser
from typing import List, Optional

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import shutil
import tempfile
import uvicorn

from pydub import AudioSegment

from karaoke_gen.instrumental_review import (
    AnalysisResult,
    AudioAnalyzer,
    AudioEditor,
    MuteRegion,
    WaveformGenerator,
)

logger = logging.getLogger(__name__)


# Request/Response Models
class MuteRegionRequest(BaseModel):
    start_seconds: float
    end_seconds: float


class CreateCustomRequest(BaseModel):
    mute_regions: List[MuteRegionRequest]


class SelectionRequest(BaseModel):
    selection: str


class InstrumentalReviewServer:
    """
    Local FastAPI server for instrumental review UI.
    
    This server provides a web interface for reviewing and selecting
    instrumental tracks in the local CLI workflow. It serves the same
    API endpoints as the cloud backend to enable UI reuse.
    """
    
    def __init__(
        self,
        output_dir: str,
        base_name: str,
        analysis: AnalysisResult,
        waveform_path: str,
        backing_vocals_path: str,
        clean_instrumental_path: str,
        with_backing_path: Optional[str] = None,
        original_audio_path: Optional[str] = None,
    ):
        """
        Initialize the review server.
        
        Args:
            output_dir: Directory containing the audio files
            base_name: Base name for output files (e.g., "Artist - Title")
            analysis: Analysis result from AudioAnalyzer
            waveform_path: Path to the waveform image
            backing_vocals_path: Path to the backing vocals audio file
            clean_instrumental_path: Path to the clean instrumental audio file
            with_backing_path: Path to the instrumental with backing vocals
            original_audio_path: Path to the original audio file (with vocals)
        """
        self.output_dir = output_dir
        self.base_name = base_name
        self.analysis = analysis
        self.waveform_path = waveform_path
        self.backing_vocals_path = backing_vocals_path
        self.clean_instrumental_path = clean_instrumental_path
        self.with_backing_path = with_backing_path
        self.original_audio_path = original_audio_path
        self.custom_instrumental_path: Optional[str] = None
        self.uploaded_instrumental_path: Optional[str] = None
        self.selection: Optional[str] = None
        
        self._app: Optional[FastAPI] = None
        self._server_thread: Optional[threading.Thread] = None
        self._selection_event = threading.Event()
        self._shutdown_event = threading.Event()
    
    def _create_app(self) -> FastAPI:
        """Create and configure the FastAPI application."""
        app = FastAPI(title="Instrumental Review", docs_url=None, redoc_url=None)

        # Configure CORS
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Determine which frontend to use
        self._use_nextjs_frontend = self._setup_nextjs_frontend(app)

        # Register routes
        self._register_routes(app)

        return app

    def _setup_nextjs_frontend(self, app: FastAPI) -> bool:
        """Set up the unified Next.js frontend.

        Returns True if Next.js frontend is set up successfully.
        Raises FileNotFoundError if Next.js assets are not available.
        """
        import os
        from karaoke_gen.nextjs_frontend import get_nextjs_assets_dir, is_nextjs_frontend_available

        if not is_nextjs_frontend_available():
            raise FileNotFoundError(
                "Next.js frontend assets not found. Please ensure the frontend is built "
                "and copied to karaoke_gen/nextjs_frontend/out/"
            )

        frontend_dir = str(get_nextjs_assets_dir())
        logger.info(f"Using Next.js frontend from {frontend_dir}")

        # Mount static files for Next.js assets
        from fastapi.staticfiles import StaticFiles
        app.mount("/_next", StaticFiles(directory=os.path.join(frontend_dir, "_next")), name="nextjs_static")

        return True
    
    def _register_routes(self, app: FastAPI) -> None:
        """Register API routes."""

        @app.get("/")
        async def serve_frontend():
            """Redirect to Next.js instrumental review route."""
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url="/app/jobs/local/instrumental", status_code=302)

        # Local instrumental route - serve pre-rendered HTML for local mode
        @app.get("/app/jobs/local/instrumental")
        async def serve_local_instrumental():
            """Serve pre-rendered local instrumental page with patched chunk loading."""
            from karaoke_gen.nextjs_frontend import get_nextjs_assets_dir
            from fastapi.responses import HTMLResponse
            import glob
            frontend_dir = get_nextjs_assets_dir()
            if frontend_dir:
                import os
                local_instrumental_html = os.path.join(str(frontend_dir), "app", "jobs", "local", "instrumental", "index.html")
                if os.path.exists(local_instrumental_html):
                    # Read the HTML and inject the missing chunk script
                    # This works around a Turbopack static export issue
                    with open(local_instrumental_html, 'r', encoding='utf-8') as f:
                        html_content = f.read()

                    # Find the chunk containing module 78280 (JobRouterClient)
                    chunks_dir = os.path.join(str(frontend_dir), "_next", "static", "chunks")
                    for chunk_file in glob.glob(os.path.join(chunks_dir, "*.js")):
                        chunk_name = os.path.basename(chunk_file)
                        with open(chunk_file, 'r', encoding='utf-8') as cf:
                            chunk_content = cf.read(500)
                            if ",78280," in chunk_content:
                                script_tag = f'<script src="/_next/static/chunks/{chunk_name}" async=""></script>'
                                if chunk_name not in html_content:
                                    html_content = html_content.replace('</head>', f'{script_tag}</head>')
                                break

                    return HTMLResponse(content=html_content, media_type="text/html")
                # Fallback to jobs index
                jobs_html = os.path.join(str(frontend_dir), "app", "jobs", "index.html")
                if os.path.exists(jobs_html):
                    return FileResponse(jobs_html, media_type="text/html")
            raise HTTPException(status_code=404, detail="Instrumental page not found")

        # Job routes - serve the jobs page HTML for client-side routing
        @app.get("/app/jobs/{full_path:path}")
        async def serve_jobs_routes(full_path: str):
            """Serve jobs index.html for all /app/jobs/* routes (SPA routing)."""
            from karaoke_gen.nextjs_frontend import get_nextjs_assets_dir
            frontend_dir = get_nextjs_assets_dir()
            if frontend_dir:
                import os
                jobs_html = os.path.join(str(frontend_dir), "app", "jobs", "index.html")
                if os.path.exists(jobs_html):
                    return FileResponse(jobs_html, media_type="text/html")
            raise HTTPException(status_code=404, detail="Jobs page not found")

        # Other app routes - serve the app index.html
        @app.get("/app/{full_path:path}")
        async def serve_app_routes(full_path: str):
            """Serve app index.html for other /app/* routes."""
            from karaoke_gen.nextjs_frontend import get_nextjs_assets_dir
            frontend_dir = get_nextjs_assets_dir()
            if frontend_dir:
                import os
                app_html = os.path.join(str(frontend_dir), "app", "index.html")
                if os.path.exists(app_html):
                    return FileResponse(app_html, media_type="text/html")
                # Fallback to root index.html
                index_html = os.path.join(str(frontend_dir), "index.html")
                if os.path.exists(index_html):
                    return FileResponse(index_html, media_type="text/html")
            raise HTTPException(status_code=404, detail="Frontend not found")

        # Tenant config endpoint (returns default config for local mode)
        @app.get("/api/tenant/config")
        async def get_tenant_config():
            """Get tenant configuration for local mode."""
            return {
                "tenant": None,
                "is_default": True
            }

        # Mock job endpoint for local mode (required by unified frontend)
        @app.get("/api/jobs/local")
        async def get_local_job():
            """Get mock job data for local mode."""
            return {
                "job_id": "local",
                "status": "awaiting_instrumental_selection",
                "progress": 50,
                "created_at": None,
                "updated_at": None,
                "artist": self.base_name.split(" - ")[0] if " - " in self.base_name else "",
                "title": self.base_name.split(" - ")[1] if " - " in self.base_name else self.base_name,
                "user_email": "local@localhost",
                "audio_hash": "local",
            }
        
        @app.get("/api/jobs/local/instrumental-analysis")
        async def get_analysis():
            """Get analysis data for the instrumental review."""
            return {
                "job_id": "local",
                "artist": self.base_name.split(" - ")[0] if " - " in self.base_name else "",
                "title": self.base_name.split(" - ")[1] if " - " in self.base_name else self.base_name,
                "status": "awaiting_instrumental_selection",
                "analysis": {
                    "has_audible_content": self.analysis.has_audible_content,
                    "total_duration_seconds": self.analysis.total_duration_seconds,
                    "audible_segments": [
                        {
                            "start_seconds": seg.start_seconds,
                            "end_seconds": seg.end_seconds,
                            "duration_seconds": seg.duration_seconds,
                            "avg_amplitude_db": seg.avg_amplitude_db,
                            "peak_amplitude_db": seg.peak_amplitude_db,
                        }
                        for seg in self.analysis.audible_segments
                    ],
                    "recommended_selection": self.analysis.recommended_selection.value,
                    "total_audible_duration_seconds": self.analysis.total_audible_duration_seconds,
                    "audible_percentage": self.analysis.audible_percentage,
                    "silence_threshold_db": self.analysis.silence_threshold_db,
                },
                "audio_urls": {
                    "clean_instrumental": "/api/audio/clean_instrumental" if self.clean_instrumental_path else None,
                    "backing_vocals": "/api/audio/backing_vocals" if self.backing_vocals_path else None,
                    "with_backing": "/api/audio/with_backing" if self.with_backing_path else None,
                    "custom_instrumental": "/api/audio/custom_instrumental" if self.custom_instrumental_path else None,
                    "uploaded_instrumental": "/api/audio/uploaded_instrumental" if self.uploaded_instrumental_path else None,
                    "original": "/api/audio/original" if self.original_audio_path else None,
                },
                "waveform_url": "/api/waveform" if self.waveform_path else None,
                "has_custom_instrumental": self.custom_instrumental_path is not None,
                "has_uploaded_instrumental": self.uploaded_instrumental_path is not None,
                "has_original": self.original_audio_path is not None,
            }
        
        @app.get("/api/jobs/local/waveform-data")
        async def get_waveform_data(num_points: int = 600):
            """Get waveform amplitude data for client-side rendering."""
            # Validate num_points parameter
            if num_points <= 0 or num_points > 10000:
                raise HTTPException(
                    status_code=400, 
                    detail="num_points must be between 1 and 10000"
                )
            
            if not self.backing_vocals_path or not os.path.exists(self.backing_vocals_path):
                raise HTTPException(status_code=404, detail="Backing vocals file not found")
            
            try:
                generator = WaveformGenerator()
                amplitudes, duration = generator.generate_data_only(self.backing_vocals_path, num_points)
                return {"amplitudes": amplitudes, "duration": duration}
            except Exception as e:
                logger.exception(f"Error generating waveform data: {e}")
                raise HTTPException(status_code=500, detail=str(e)) from e
        
        def _get_audio_path(stem_type: str) -> Optional[str]:
            """Get the path for a given stem type."""
            path_map = {
                "clean_instrumental": self.clean_instrumental_path,
                "backing_vocals": self.backing_vocals_path,
                "with_backing": self.with_backing_path,
                "custom_instrumental": self.custom_instrumental_path,
                "uploaded_instrumental": self.uploaded_instrumental_path,
                "original": self.original_audio_path,
            }
            return path_map.get(stem_type)

        def _stream_audio_file(stem_type: str):
            """Stream an audio file by stem type."""
            audio_path = _get_audio_path(stem_type)
            if not audio_path or not os.path.exists(audio_path):
                raise HTTPException(status_code=404, detail=f"Audio file not found: {stem_type}")

            # Determine content type
            ext = os.path.splitext(audio_path)[1].lower()
            content_types = {
                ".flac": "audio/flac",
                ".mp3": "audio/mpeg",
                ".wav": "audio/wav",
            }
            content_type = content_types.get(ext, "application/octet-stream")

            return FileResponse(audio_path, media_type=content_type)

        @app.get("/api/audio/{stem_type}")
        async def stream_audio(stem_type: str):
            """Stream audio file (legacy route)."""
            return _stream_audio_file(stem_type)

        @app.get("/api/jobs/{job_id}/audio-stream/{stem_type}")
        async def stream_audio_cloud(job_id: str, stem_type: str):
            """Stream audio file (cloud-compatible route)."""
            return _stream_audio_file(stem_type)
        
        @app.get("/api/waveform")
        async def get_waveform_image():
            """Serve waveform image."""
            if not self.waveform_path or not os.path.exists(self.waveform_path):
                raise HTTPException(status_code=404, detail="Waveform image not found")
            return FileResponse(self.waveform_path, media_type="image/png")
        
        @app.post("/api/jobs/local/create-custom-instrumental")
        async def create_custom_instrumental(request: CreateCustomRequest):
            """Create a custom instrumental with muted regions."""
            if not request.mute_regions:
                raise HTTPException(status_code=400, detail="No mute regions provided")
            
            try:
                mute_regions = [
                    MuteRegion(
                        start_seconds=r.start_seconds,
                        end_seconds=r.end_seconds,
                    )
                    for r in request.mute_regions
                ]
                
                editor = AudioEditor()
                output_path = os.path.join(
                    self.output_dir,
                    f"{self.base_name} (Instrumental Custom).flac"
                )
                
                result = editor.create_custom_instrumental(
                    clean_instrumental_path=self.clean_instrumental_path,
                    backing_vocals_path=self.backing_vocals_path,
                    mute_regions=mute_regions,
                    output_path=output_path,
                )
                
                self.custom_instrumental_path = result.output_path
                
                return {
                    "status": "success",
                    "custom_instrumental_url": "/api/audio/custom_instrumental",
                    "statistics": {
                        "mute_regions_applied": len(result.mute_regions_applied),
                        "total_muted_duration_seconds": result.total_muted_duration_seconds,
                        "output_duration_seconds": result.output_duration_seconds,
                    },
                }
            except Exception as e:
                logger.exception(f"Error creating custom instrumental: {e}")
                raise HTTPException(status_code=500, detail=str(e)) from e
        
        @app.post("/api/jobs/local/upload-instrumental")
        async def upload_instrumental(file: UploadFile = File(...)):
            """Upload a custom instrumental audio file."""
            # Validate file type
            allowed_extensions = {".flac", ".mp3", ".wav", ".m4a", ".ogg"}
            ext = os.path.splitext(file.filename or "")[1].lower()
            if ext not in allowed_extensions:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Invalid file type. Allowed: {', '.join(allowed_extensions)}"
                )
            
            tmp_path = None
            file_moved = False
            try:
                # Save to temp file first to validate
                with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                    shutil.copyfileobj(file.file, tmp)
                    tmp_path = tmp.name
                
                # Load and check duration
                uploaded_audio = AudioSegment.from_file(tmp_path)
                uploaded_duration = len(uploaded_audio) / 1000.0  # ms to seconds
                
                expected_duration = self.analysis.total_duration_seconds
                duration_diff = abs(uploaded_duration - expected_duration)
                
                if duration_diff > 0.5:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Duration mismatch: uploaded file is {uploaded_duration:.2f}s, "
                               f"expected {expected_duration:.2f}s (±0.5s allowed)"
                    )
                
                # Move to final location
                output_path = os.path.join(
                    self.output_dir,
                    f"{self.base_name} (Instrumental Uploaded){ext}"
                )
                shutil.move(tmp_path, output_path)
                file_moved = True
                self.uploaded_instrumental_path = output_path
                
                return {
                    "status": "success",
                    "uploaded_instrumental_url": "/api/audio/uploaded_instrumental",
                    "duration_seconds": uploaded_duration,
                    "filename": file.filename,
                }
            except HTTPException:
                raise
            except Exception as e:
                logger.exception(f"Error uploading instrumental: {e}")
                raise HTTPException(status_code=500, detail=str(e)) from e
            finally:
                # Clean up temp file if it wasn't moved
                if tmp_path and not file_moved and os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass  # Best effort cleanup
        
        @app.post("/api/jobs/local/select-instrumental")
        async def select_instrumental(request: SelectionRequest):
            """Submit instrumental selection."""
            if request.selection not in ("clean", "with_backing", "custom", "uploaded", "original"):
                raise HTTPException(status_code=400, detail=f"Invalid selection: {request.selection}")
            
            self.selection = request.selection
            self._selection_event.set()
            
            return {"status": "success", "selection": request.selection}
    
    
    @staticmethod
    def _is_port_available(host: str, port: int) -> bool:
        """Check if a port is available for binding."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((host, port))
                return True
        except OSError:
            return False
    
    @staticmethod
    def _find_available_port(host: str, preferred_port: int, max_attempts: int = 100) -> int:
        """
        Find an available port, starting with the preferred port.
        
        Args:
            host: Host to bind to
            preferred_port: The preferred port to try first
            max_attempts: Maximum number of ports to try
            
        Returns:
            An available port number
            
        Raises:
            RuntimeError: If no available port could be found
        """
        # Try the preferred port first
        if InstrumentalReviewServer._is_port_available(host, preferred_port):
            return preferred_port
        
        # Try subsequent ports
        for offset in range(1, max_attempts):
            port = preferred_port + offset
            if port > 65535:
                break
            if InstrumentalReviewServer._is_port_available(host, port):
                return port
        
        # Last resort: let the OS assign a port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, 0))
            return sock.getsockname()[1]
    
    def start_and_open_browser(self, port: int = 8765) -> str:
        """
        Start server, open browser, and block until selection is submitted.
        
        Args:
            port: Preferred port to run the server on. If unavailable, will
                  automatically find an available port.
            
        Returns:
            The user's selection ("clean", "with_backing", or "custom")
        """
        self._app = self._create_app()
        
        # Find an available port (handles concurrent CLI instances)
        host = "127.0.0.1"
        actual_port = self._find_available_port(host, port)
        if actual_port != port:
            logger.info(f"Port {port} in use, using port {actual_port} instead")
        
        # Run uvicorn in a separate thread
        config = uvicorn.Config(
            self._app,
            host=host,
            port=actual_port,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        
        def run_server():
            server.run()
        
        self._server_thread = threading.Thread(target=run_server, daemon=True)
        self._server_thread.start()
        
        # Wait a moment for server to start
        import time
        time.sleep(0.5)
        
        url = f"http://localhost:{actual_port}/"
        logger.info(f"Instrumental review server started at {url}")
        
        # Open browser
        webbrowser.open(url)
        
        # Wait for selection
        logger.info("Waiting for instrumental selection...")
        self._selection_event.wait()
        
        # Give a moment for response to be sent
        time.sleep(0.5)
        
        return self.get_selection()
    
    def stop(self) -> None:
        """Stop the server."""
        self._shutdown_event.set()
    
    def get_selection(self) -> str:
        """Get the user's selection."""
        if self.selection is None:
            raise ValueError("No selection has been made")
        return self.selection
    
    def get_custom_instrumental_path(self) -> Optional[str]:
        """Get the path to the custom instrumental if one was created."""
        return self.custom_instrumental_path
    
    def get_uploaded_instrumental_path(self) -> Optional[str]:
        """Get the path to the uploaded instrumental if one was uploaded."""
        return self.uploaded_instrumental_path
