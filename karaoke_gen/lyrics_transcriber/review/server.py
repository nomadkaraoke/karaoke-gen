import logging
import socket
from fastapi import FastAPI, Body, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any, List, Optional
from karaoke_gen.lyrics_transcriber.types import CorrectionResult, WordCorrection, LyricsSegment, LyricsData, LyricsMetadata, Word
import time
import os
import urllib.parse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import hashlib
from karaoke_gen.lyrics_transcriber.core.config import OutputConfig
import uvicorn
import webbrowser
from threading import Thread
from karaoke_gen.lyrics_transcriber.output.generator import OutputGenerator
import json
from karaoke_gen.lyrics_transcriber.correction.corrector import LyricsCorrector
from karaoke_gen.lyrics_transcriber.types import TranscriptionResult, TranscriptionData
from karaoke_gen.lyrics_transcriber.lyrics.user_input_provider import UserInputProvider
from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations
import uuid

try:
    # Optional: used to introspect local models for /api/v1/models
    from karaoke_gen.lyrics_transcriber.correction.agentic.providers.health import (
        is_ollama_available,
        get_ollama_models,
    )
except Exception:
    def is_ollama_available() -> bool:  # type: ignore
        return False

    def get_ollama_models():  # type: ignore
        return []

try:
    from karaoke_gen.lyrics_transcriber.correction.agentic.observability.metrics import MetricsAggregator
except Exception:
    MetricsAggregator = None  # type: ignore

try:
    from karaoke_gen.lyrics_transcriber.correction.agentic.observability.langfuse_integration import (
        setup_langfuse,
        record_metrics as lf_record,
    )
except Exception:
    setup_langfuse = lambda *args, **kwargs: None  # type: ignore
    lf_record = lambda *args, **kwargs: None  # type: ignore

try:
    from karaoke_gen.lyrics_transcriber.correction.agentic.feedback.store import FeedbackStore
except Exception:
    FeedbackStore = None  # type: ignore

try:
    from karaoke_gen.lyrics_transcriber.correction.feedback.store import FeedbackStore as NewFeedbackStore
    from karaoke_gen.lyrics_transcriber.correction.feedback.schemas import CorrectionAnnotation
except Exception:
    NewFeedbackStore = None  # type: ignore
    CorrectionAnnotation = None  # type: ignore


class ReviewServer:
    """Handles the review process through a web interface."""

    def __init__(
        self,
        correction_result: CorrectionResult,
        output_config: OutputConfig,
        audio_filepath: str,
        logger: logging.Logger,
        # Instrumental review data (optional - for combined review flow)
        instrumental_options: Optional[List[Dict[str, Any]]] = None,
        backing_vocals_analysis: Optional[Dict[str, Any]] = None,
        clean_instrumental_path: Optional[str] = None,
        with_backing_path: Optional[str] = None,
        backing_vocals_path: Optional[str] = None,
    ):
        """Initialize the review server.

        Args:
            correction_result: The lyrics correction result to review
            output_config: Output configuration
            audio_filepath: Path to the main audio file (vocals)
            logger: Logger instance
            instrumental_options: List of instrumental options for selection
                Each option: {"id": str, "label": str, "audio_path": str}
            backing_vocals_analysis: Analysis result from AudioAnalyzer
            clean_instrumental_path: Path to clean instrumental audio file
            with_backing_path: Path to instrumental with backing vocals
            backing_vocals_path: Path to backing vocals audio file
        """
        self.correction_result = correction_result
        self.output_config = output_config
        self.audio_filepath = audio_filepath
        self.logger = logger or logging.getLogger(__name__)
        self.review_completed = False
        self.corrections_saved = False  # Flag for intermediate save (before instrumental review)
        self.pending_corrections: Optional[Dict[str, Any]] = None  # Store corrections until final submission

        # Instrumental review data
        self.instrumental_options = instrumental_options or []
        self.backing_vocals_analysis = backing_vocals_analysis
        self.clean_instrumental_path = clean_instrumental_path
        self.with_backing_path = with_backing_path
        self.backing_vocals_path = backing_vocals_path
        self.instrumental_selection: Optional[str] = None

        # Create FastAPI instance and configure
        self.app = FastAPI()
        self._configure_cors()
        self._register_routes()
        self._mount_frontend()
        self._register_spa_routes()
        # Initialize optional SQLite store for sessions/feedback (legacy)
        try:
            default_db = os.path.join(self.output_config.cache_dir, "agentic_feedback.sqlite3")
            self._store = FeedbackStore(default_db) if FeedbackStore else None
        except Exception:
            self._store = None
        
        # Initialize new annotation store
        try:
            self._annotation_store = NewFeedbackStore(storage_dir=self.output_config.cache_dir) if NewFeedbackStore else None
        except Exception:
            self._annotation_store = None
        # Metrics aggregator
        self._metrics = MetricsAggregator() if MetricsAggregator else None
        # LangFuse (optional)
        try:
            self._langfuse = setup_langfuse("agentic-corrector")
        except Exception:
            self._langfuse = None

    def _configure_cors(self) -> None:
        """Configure CORS middleware."""
        # Allow localhost development ports and the hosted review UI
        allowed_origins = (
            [f"http://localhost:{port}" for port in range(3000, 5174)]
            + [f"http://127.0.0.1:{port}" for port in range(3000, 5174)]
            + ["https://gen.nomadkaraoke.com"]
        )
        
        # Also allow custom review UI URL if set
        custom_ui = os.environ.get("LYRICS_REVIEW_UI_URL", "")
        if custom_ui and custom_ui.lower() != "local" and custom_ui not in allowed_origins:
            allowed_origins.append(custom_ui)
        
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        @self.app.exception_handler(HTTPException)
        async def _http_exception_handler(request, exc: HTTPException):
            return JSONResponse(status_code=exc.status_code, content={"error": "HTTPException", "message": exc.detail, "details": {}})

        @self.app.exception_handler(Exception)
        async def _unhandled_exception_handler(request, exc: Exception):
            return JSONResponse(status_code=500, content={"error": "InternalServerError", "message": str(exc), "details": {}})

    def _mount_frontend(self) -> None:
        """Mount the unified Next.js frontend static files."""
        from karaoke_gen.nextjs_frontend import get_nextjs_assets_dir, is_nextjs_frontend_available

        if not is_nextjs_frontend_available():
            raise FileNotFoundError(
                "Next.js frontend assets not found. Please ensure the frontend is built "
                "and copied to karaoke_gen/nextjs_frontend/out/"
            )

        frontend_dir = str(get_nextjs_assets_dir())
        self.logger.info(f"Using Next.js frontend from {frontend_dir}")
        self._frontend_dir = frontend_dir  # Store for use in route handlers

        # Mount Next.js static assets directory
        nextjs_static = os.path.join(frontend_dir, "_next")
        if os.path.exists(nextjs_static):
            self.app.mount("/_next", StaticFiles(directory=nextjs_static), name="nextjs_static")

        # Mount the entire frontend directory for static files, but use html=False
        # so it doesn't serve index.html automatically for directories
        self.app.mount("/static", StaticFiles(directory=frontend_dir), name="frontend_static")

    def _register_spa_routes(self) -> None:
        """Register SPA fallback routes for Next.js client-side routing."""
        frontend_dir = self._frontend_dir

        # Root route - serve index.html
        @self.app.get("/")
        async def serve_root():
            index_html = os.path.join(frontend_dir, "index.html")
            if os.path.exists(index_html):
                return FileResponse(index_html, media_type="text/html")
            raise HTTPException(status_code=404, detail="Frontend not found")

        # Local review route - serve pre-rendered HTML for local mode
        @self.app.get("/app/jobs/local/review")
        async def serve_local_review():
            """Serve pre-rendered local review page with patched chunk loading."""
            from fastapi.responses import HTMLResponse
            local_review_html = os.path.join(frontend_dir, "app", "jobs", "local", "review", "index.html")
            if os.path.exists(local_review_html):
                # Read the HTML and inject the missing chunk script
                # This works around a Turbopack static export issue where the RSC stream
                # references module chunks that don't contain the actual code
                with open(local_review_html, 'r', encoding='utf-8') as f:
                    html_content = f.read()

                # Find the missing chunk that contains JobRouterClient (module 78280)
                # The chunk name is determined at build time, so we need to find it dynamically
                # We look for ",78280," which is the Turbopack module ID pattern
                import glob
                chunks_dir = os.path.join(frontend_dir, "_next", "static", "chunks")
                for chunk_file in glob.glob(os.path.join(chunks_dir, "*.js")):
                    chunk_name = os.path.basename(chunk_file)
                    # Check if this chunk contains module 78280 (JobRouterClient)
                    with open(chunk_file, 'r', encoding='utf-8') as cf:
                        chunk_content = cf.read(500)  # Module ID is near the start
                        if ",78280," in chunk_content:
                            # Inject this chunk script if not already present
                            script_tag = f'<script src="/_next/static/chunks/{chunk_name}" async=""></script>'
                            if chunk_name not in html_content:
                                # Insert before closing </head>
                                html_content = html_content.replace('</head>', f'{script_tag}</head>')
                            break

                return HTMLResponse(content=html_content, media_type="text/html")
            # Fallback to jobs index
            jobs_html = os.path.join(frontend_dir, "app", "jobs", "index.html")
            if os.path.exists(jobs_html):
                return FileResponse(jobs_html, media_type="text/html")
            raise HTTPException(status_code=404, detail="Review page not found")

        # Job routes - serve the jobs page HTML for client-side routing
        @self.app.get("/app/jobs/{path:path}")
        async def serve_jobs_routes(path: str):
            """Serve jobs index.html for all /app/jobs/* routes (SPA routing)."""
            jobs_html = os.path.join(frontend_dir, "app", "jobs", "index.html")
            if os.path.exists(jobs_html):
                return FileResponse(jobs_html, media_type="text/html")
            raise HTTPException(status_code=404, detail="Jobs page not found")

        # Other app routes - serve the app index.html
        @self.app.get("/app/{path:path}")
        async def serve_app_routes(path: str):
            """Serve app index.html for other /app/* routes."""
            app_html = os.path.join(frontend_dir, "app", "index.html")
            if os.path.exists(app_html):
                return FileResponse(app_html, media_type="text/html")
            # Fallback to root index.html
            index_html = os.path.join(frontend_dir, "index.html")
            if os.path.exists(index_html):
                return FileResponse(index_html, media_type="text/html")
            raise HTTPException(status_code=404, detail="Frontend not found")

        # Favicon
        @self.app.get("/favicon.ico")
        async def serve_favicon():
            favicon_path = os.path.join(frontend_dir, "favicon.ico")
            if os.path.exists(favicon_path):
                return FileResponse(favicon_path)
            raise HTTPException(status_code=404, detail="Not found")

    def _register_routes(self) -> None:
        """Register API routes."""
        # Legacy routes (for backward compatibility with old frontend)
        self.app.add_api_route("/api/correction-data", self.get_correction_data, methods=["GET"])
        self.app.add_api_route("/api/complete", self.complete_review, methods=["POST"])
        self.app.add_api_route("/api/preview-video", self.generate_preview_video, methods=["POST"])
        self.app.add_api_route("/api/preview-video/{preview_hash}", self.get_preview_video, methods=["GET"])
        self.app.add_api_route("/api/audio/{audio_hash}", self.get_audio, methods=["GET"])
        self.app.add_api_route("/api/ping", self.ping, methods=["GET"])
        self.app.add_api_route("/api/handlers", self.update_handlers, methods=["POST"])
        self.app.add_api_route("/api/add-lyrics", self.add_lyrics, methods=["POST"])

        # Instrumental audio streaming routes (for combined review)
        self.app.add_api_route("/api/audio/instrumental/{stem_type}", self.get_instrumental_audio, methods=["GET"])

        # Cloud-compatible routes (for unified Next.js frontend)
        # These use the same handlers but with cloud-style URL patterns
        # Job ID is always "local" for local CLI mode
        self.app.add_api_route("/api/jobs/{job_id}", self.get_local_job, methods=["GET"])
        self.app.add_api_route("/api/jobs/{job_id}/corrections", self.get_correction_data, methods=["GET"])
        self.app.add_api_route("/api/jobs/{job_id}/corrections", self.submit_corrections, methods=["POST"])
        self.app.add_api_route("/api/jobs/{job_id}/corrections", self.complete_review, methods=["PUT"])
        self.app.add_api_route("/api/jobs/{job_id}/preview-video", self.generate_preview_video, methods=["POST"])
        self.app.add_api_route("/api/jobs/{job_id}/handlers", self.update_handlers_cloud, methods=["PATCH"])
        self.app.add_api_route("/api/jobs/{job_id}/lyrics", self.add_lyrics, methods=["POST"])
        self.app.add_api_route("/api/jobs/{job_id}/annotations", self.post_annotation, methods=["POST"])

        # Review-specific routes (used by combined review UI)
        self.app.add_api_route("/api/review/{job_id}/correction-data", self.get_correction_data, methods=["GET"])
        self.app.add_api_route("/api/review/{job_id}/complete", self.complete_review, methods=["POST"])
        self.app.add_api_route("/api/jobs/{job_id}/complete-review", self.complete_review, methods=["POST"])

        # Agentic AI v1 endpoints (contract-compliant scaffolds)
        self.app.add_api_route("/api/v1/correction/agentic", self.post_correction_agentic, methods=["POST"])
        self.app.add_api_route("/api/v1/correction/session/{session_id}", self.get_correction_session_v1, methods=["GET"])
        self.app.add_api_route("/api/v1/feedback", self.post_feedback_v1, methods=["POST"])
        self.app.add_api_route("/api/v1/models", self.get_models_v1, methods=["GET"])
        self.app.add_api_route("/api/v1/models", self.put_models_v1, methods=["PUT"])
        self.app.add_api_route("/api/v1/metrics", self.get_metrics_v1, methods=["GET"])

        # Annotation endpoints
        self.app.add_api_route("/api/v1/annotations", self.post_annotation, methods=["POST"])
        self.app.add_api_route("/api/v1/annotations/{audio_hash}", self.get_annotations_by_song, methods=["GET"])
        self.app.add_api_route("/api/v1/annotations/stats", self.get_annotation_stats, methods=["GET"])

        # Tenant config endpoint (returns default config for local mode)
        self.app.add_api_route("/api/tenant/config", self.get_tenant_config, methods=["GET"])

        # Review-prefixed routes for frontend compatibility
        self.app.add_api_route("/api/review/{job_id}/preview-video", self.generate_preview_video, methods=["POST"])
        # Wrapper for get_preview_video that accepts job_id (but ignores it)
        async def get_preview_video_with_job_id(job_id: str, preview_hash: str):
            return await self.get_preview_video(preview_hash)
        self.app.add_api_route("/api/review/{job_id}/preview-video/{preview_hash}", get_preview_video_with_job_id, methods=["GET"])

        # Instrumental review data endpoints
        self.app.add_api_route("/api/jobs/{job_id}/instrumental-analysis", self.get_instrumental_analysis, methods=["GET"])
        self.app.add_api_route("/api/jobs/{job_id}/waveform-data", self.get_waveform_data, methods=["GET"])

        # Instrumental audio streaming
        self.app.add_api_route("/api/jobs/{job_id}/audio-stream/backing_vocals", self.get_backing_vocals_audio, methods=["GET"])
        self.app.add_api_route("/api/jobs/{job_id}/audio-stream/clean_instrumental", self.get_clean_audio, methods=["GET"])
        self.app.add_api_route("/api/jobs/{job_id}/audio-stream/with_backing", self.get_with_backing_audio, methods=["GET"])

    async def get_correction_data(self):
        """Get the correction data including instrumental options."""
        data = self.correction_result.to_dict()

        # Include instrumental review data as top-level fields (frontend expects these)
        if self.instrumental_options:
            # Add audio_url to each option for frontend streaming
            options_with_urls = []
            for opt in self.instrumental_options:
                opt_with_url = dict(opt)
                if opt.get("id") == "clean":
                    opt_with_url["audio_url"] = "/api/audio/instrumental/clean"
                elif opt.get("id") == "with_backing":
                    opt_with_url["audio_url"] = "/api/audio/instrumental/with_backing"
                options_with_urls.append(opt_with_url)
            data["instrumental_options"] = options_with_urls

        if self.backing_vocals_analysis:
            data["backing_vocals_analysis"] = self.backing_vocals_analysis

        return data

    async def get_tenant_config(self):
        """Get tenant configuration for local mode.

        Returns a default tenant config that enables all features
        for local CLI mode.
        """
        return {
            "tenant": None,
            "is_default": True
        }

    async def get_local_job(self, job_id: str):
        """Get mock job data for local mode.

        This endpoint returns job-like data that the unified frontend expects.
        In local mode, the job_id is always "local".
        """
        metadata = self.correction_result.metadata or {}
        return {
            "job_id": job_id,
            "status": "awaiting_review",
            "progress": 50,
            "created_at": None,
            "updated_at": None,
            "artist": metadata.get("artist", "Local Artist"),
            "title": metadata.get("title", "Local Title"),
            "user_email": "local@localhost",
            "audio_hash": metadata.get("audio_hash", "local"),
        }

    async def get_instrumental_analysis(self, job_id: str):
        """Return instrumental analysis data for selection UI."""
        if not self.backing_vocals_analysis:
            raise HTTPException(404, "No backing vocals analysis available")

        return {
            "has_original": False,  # Not supported in local mode
            "analysis": self.backing_vocals_analysis,
            "audio_urls": {
                "backing_vocals": f"/api/jobs/{job_id}/audio-stream/backing_vocals" if self.backing_vocals_path else None,
                "clean": f"/api/jobs/{job_id}/audio-stream/clean_instrumental" if self.clean_instrumental_path else None,
                "with_backing": f"/api/jobs/{job_id}/audio-stream/with_backing" if self.with_backing_path else None,
            },
            "has_uploaded_instrumental": False,  # Not supported in local mode
        }

    async def get_waveform_data(self, job_id: str):
        """Generate waveform visualization data."""
        if not self.backing_vocals_path or not os.path.exists(self.backing_vocals_path):
            raise HTTPException(404, "No backing vocals audio available")

        # Import here to avoid circular dependency
        from pydub import AudioSegment
        import numpy as np

        try:
            # Load audio with pydub
            audio = AudioSegment.from_file(self.backing_vocals_path)

            # Get raw audio data
            samples = np.array(audio.get_array_of_samples())

            # If stereo, average the channels
            if audio.channels == 2:
                samples = samples.reshape((-1, 2)).mean(axis=1)

            # Calculate number of samples per point (aim for ~1000 points)
            num_points = 1000
            chunk_size = len(samples) // num_points

            # Generate amplitude peaks
            peaks = []
            for i in range(num_points):
                start = i * chunk_size
                end = min(start + chunk_size, len(samples))
                if start < len(samples):
                    chunk = samples[start:end]
                    # Get peak amplitude for this chunk
                    peak = np.abs(chunk).max() if len(chunk) > 0 else 0
                    peaks.append(float(peak))

            # Normalize to 0-1 range
            max_peak = max(peaks) if peaks else 1
            if max_peak > 0:
                peaks = [p / max_peak for p in peaks]

            duration = len(audio) / 1000.0  # Convert ms to seconds

            return {
                "duration_seconds": duration,
                "duration": duration,  # Backward compat
                "amplitudes": peaks,
                "sample_rate": audio.frame_rate,
            }
        except Exception as e:
            self.logger.error(f"Failed to generate waveform: {e}")
            raise HTTPException(500, f"Failed to generate waveform: {str(e)}")

    async def get_backing_vocals_audio(self, job_id: str):
        """Stream backing vocals audio file."""
        if not self.backing_vocals_path or not os.path.exists(self.backing_vocals_path):
            raise HTTPException(404, "Backing vocals audio not found")

        return FileResponse(
            self.backing_vocals_path,
            media_type="audio/flac",
            filename=os.path.basename(self.backing_vocals_path)
        )

    async def get_clean_audio(self, job_id: str):
        """Stream clean instrumental audio file."""
        if not self.clean_instrumental_path or not os.path.exists(self.clean_instrumental_path):
            raise HTTPException(404, "Clean instrumental audio not found")

        return FileResponse(
            self.clean_instrumental_path,
            media_type="audio/flac",
            filename=os.path.basename(self.clean_instrumental_path)
        )

    async def get_with_backing_audio(self, job_id: str):
        """Stream with-backing instrumental audio file."""
        if not self.with_backing_path or not os.path.exists(self.with_backing_path):
            raise HTTPException(404, "With-backing instrumental audio not found")

        return FileResponse(
            self.with_backing_path,
            media_type="audio/flac",
            filename=os.path.basename(self.with_backing_path)
        )

    async def update_handlers_cloud(self, job_id: str, enabled_handlers: List[str] = Body(...)):
        """Cloud-compatible handler update endpoint (PATCH method).

        This wraps the existing update_handlers method with cloud-style routing.
        """
        return await self.update_handlers(enabled_handlers)

    # ------------------------------
    # API v1: Agentic AI scaffolds
    # ------------------------------

    @property
    def _session_store(self) -> Dict[str, Dict[str, Any]]:
        if not hasattr(self, "__session_store"):
            self.__session_store = {}
        return self.__session_store  # type: ignore[attr-defined]

    @property
    def _feedback_store(self) -> Dict[str, Dict[str, Any]]:
        if not hasattr(self, "__feedback_store"):
            self.__feedback_store = {}
        return self.__feedback_store  # type: ignore[attr-defined]

    @property
    def _model_registry(self) -> Dict[str, Dict[str, Any]]:
        if not hasattr(self, "__model_registry"):
            # Seed with a few placeholders
            models: Dict[str, Dict[str, Any]] = {}
            # Local models via Ollama
            if is_ollama_available():
                for m in get_ollama_models():
                    mid = m.get("model") or m.get("name") or "ollama-unknown"
                    models[mid] = {
                        "id": mid,
                        "name": mid,
                        "type": "local",
                        "available": True,
                        "responseTimeMs": 0,
                        "costPerToken": 0.0,
                        "accuracy": 0.0,
                    }
            # Cloud placeholders
            for mid in ["anthropic/claude-4-sonnet", "gpt-5", "gemini-2.5-pro"]:
                if mid not in models:
                    models[mid] = {
                        "id": mid,
                        "name": mid,
                        "type": "cloud",
                        "available": False,
                        "responseTimeMs": 0,
                        "costPerToken": 0.0,
                        "accuracy": 0.0,
                    }
            self.__model_registry = models
        return self.__model_registry  # type: ignore[attr-defined]

    async def post_correction_agentic(self, request: Dict[str, Any] = Body(...)):
        """POST /api/v1/correction/agentic
        Minimal scaffold: validates required fields and returns a stub response.
        """
        start_time = time.time()
        if not isinstance(request, dict):
            raise HTTPException(status_code=400, detail="Invalid request body")

        if "transcriptionData" not in request or "audioFileHash" not in request:
            raise HTTPException(status_code=400, detail="Missing required fields: transcriptionData, audioFileHash")

        session_id = str(uuid.uuid4())
        session_record = {
            "id": session_id,
            "audioFileHash": request.get("audioFileHash"),
            "sessionType": "FULL_CORRECTION",
            "aiModelConfig": {"model": (request.get("modelPreferences") or [None])[0]},
            "totalCorrections": 0,
            "acceptedCorrections": 0,
            "humanModifications": 0,
            "sessionDurationMs": 0,
            "accuracyImprovement": 0.0,
            "startedAt": None,
            "completedAt": None,
            "status": "IN_PROGRESS",
        }
        self._session_store[session_id] = session_record
        if self._store:
            try:
                self._store.put_session(session_id, json.dumps(session_record))
            except Exception:
                pass

        # Simulate provider availability based on model preferences
        preferred = (request.get("modelPreferences") or ["unknown"])[0]
        model_entry = self._model_registry.get(preferred)
        if model_entry and not model_entry.get("available", False):
            # Service unavailable → return 503 with fallback details
            from fastapi.responses import JSONResponse
            if self._metrics:
                self._metrics.record_session(preferred, int((time.time() - start_time) * 1000), fallback_used=True)
            content = {
                "corrections": [],
                "fallbackReason": f"Model {preferred} unavailable",
                "originalSystemUsed": "rule-based",
                "processingTimeMs": int((time.time() - start_time) * 1000),
            }
            lf_record(self._langfuse, "post_correction_agentic_fallback", {"model": preferred, **content})
            return JSONResponse(status_code=503, content=content)

        response = {
            "sessionId": session_id,
            "corrections": [],
            "processingTimeMs": int((time.time() - start_time) * 1000),
            "modelUsed": preferred,
            "fallbackUsed": False,
            "accuracyEstimate": 0.0,
        }
        if self._metrics:
            self._metrics.record_session(preferred, response["processingTimeMs"], fallback_used=False)
        lf_record(self._langfuse, "post_correction_agentic", {"model": preferred, **response})
        return response

    async def get_correction_session_v1(self, session_id: str):
        data = self._session_store.get(session_id)
        if not data:
            raise HTTPException(status_code=404, detail="Session not found")
        return data

    async def post_feedback_v1(self, request: Dict[str, Any] = Body(...)):
        if not isinstance(request, dict):
            raise HTTPException(status_code=400, detail="Invalid request body")
        required = ["aiCorrectionId", "reviewerAction", "reasonCategory"]
        if any(k not in request for k in required):
            raise HTTPException(status_code=400, detail="Missing required feedback fields")

        feedback_id = str(uuid.uuid4())
        record = {**request, "id": feedback_id}
        self._feedback_store[feedback_id] = record
        if self._store:
            try:
                self._store.put_feedback(feedback_id, request.get("sessionId"), json.dumps(record))
            except Exception:
                pass
        if self._metrics:
            self._metrics.record_feedback()
        return {"feedbackId": feedback_id, "recorded": True, "learningDataUpdated": False}

    async def get_models_v1(self):
        return {"models": list(self._model_registry.values())}

    async def put_models_v1(self, config: Dict[str, Any] = Body(...)):
        if not isinstance(config, dict) or "modelId" not in config:
            raise HTTPException(status_code=400, detail="Invalid model configuration")
        mid = config["modelId"]
        entry = self._model_registry.get(mid, {
            "id": mid,
            "name": mid,
            "type": "cloud",
            "available": False,
            "responseTimeMs": 0,
            "costPerToken": 0.0,
            "accuracy": 0.0,
        })
        if "enabled" in config:
            entry["available"] = bool(config["enabled"]) or entry.get("available", False)
        if "priority" in config:
            entry["priority"] = config["priority"]
        if "configuration" in config and isinstance(config["configuration"], dict):
            entry["configuration"] = config["configuration"]
        self._model_registry[mid] = entry
        return {"status": "ok"}

    async def get_metrics_v1(self, timeRange: str = "day", sessionId: Optional[str] = None):
        if self._metrics:
            return self._metrics.snapshot(time_range=timeRange, session_id=sessionId)
        # Fallback if metrics unavailable
        return {"timeRange": timeRange, "totalSessions": len(self._session_store), "averageAccuracy": 0.0, "errorReduction": 0.0, "averageProcessingTime": 0, "modelPerformance": {}, "costSummary": {}, "userSatisfaction": 0.0}
    
    # ------------------------------
    # Annotation endpoints
    # ------------------------------
    
    async def post_annotation(self, annotation_data: Dict[str, Any] = Body(...)):
        """Save a correction annotation."""
        if not self._annotation_store or not CorrectionAnnotation:
            raise HTTPException(status_code=501, detail="Annotation system not available")
        
        try:
            # Validate and create annotation
            annotation = CorrectionAnnotation.model_validate(annotation_data)
            
            # Save to store
            success = self._annotation_store.save_annotation(annotation)
            
            if success:
                return {"status": "success", "annotation_id": annotation.annotation_id}
            else:
                raise HTTPException(status_code=500, detail="Failed to save annotation")
                
        except Exception as e:
            self.logger.error(f"Failed to save annotation: {e}")
            raise HTTPException(status_code=400, detail=str(e))
    
    async def get_annotations_by_song(self, audio_hash: str):
        """Get all annotations for a specific song."""
        if not self._annotation_store:
            raise HTTPException(status_code=501, detail="Annotation system not available")
        
        try:
            annotations = self._annotation_store.get_annotations_by_song(audio_hash)
            return {
                "audio_hash": audio_hash,
                "count": len(annotations),
                "annotations": [a.model_dump() for a in annotations]
            }
        except Exception as e:
            self.logger.error(f"Failed to get annotations: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    async def get_annotation_stats(self):
        """Get aggregated statistics from all annotations."""
        if not self._annotation_store:
            raise HTTPException(status_code=501, detail="Annotation system not available")
        
        try:
            stats = self._annotation_store.get_statistics()
            return stats.model_dump()
        except Exception as e:
            self.logger.error(f"Failed to get annotation statistics: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    def _update_correction_result(self, base_result: CorrectionResult, updated_data: Dict[str, Any]) -> CorrectionResult:
        """Update a CorrectionResult with new correction data."""
        return CorrectionOperations.update_correction_result_with_data(base_result, updated_data)

    async def submit_corrections(self, request_body: Dict[str, Any] = Body(...)):
        """Submit corrections without completing the review (intermediate save).

        This is called when the user proceeds from lyrics review to instrumental review.
        The corrections are saved but the review is not marked as complete yet.
        """
        try:
            # Extract the corrections data from the wrapper
            corrections_data = request_body.get("corrections", request_body)

            self.logger.info("Saving corrections (intermediate step before instrumental review)")

            # Store corrections for later application (don't update correction_result yet)
            # We'll apply them when complete_review is called with the instrumental selection
            self.pending_corrections = corrections_data
            self.corrections_saved = True  # Flag to indicate corrections are ready

            return {"status": "success", "message": "Corrections saved"}
        except Exception as e:
            self.logger.error(f"Failed to save corrections: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    async def complete_review(self, updated_data: Dict[str, Any] = Body(...)):
        """Complete the review process (final submission with instrumental selection)."""
        try:
            # Extract instrumental selection if present
            instrumental_selection = updated_data.pop("instrumental_selection", None)
            if instrumental_selection:
                self.instrumental_selection = instrumental_selection
                self.logger.info(f"Instrumental selection: {instrumental_selection}")

            # Apply pending corrections if they were saved earlier
            if self.pending_corrections:
                self.logger.info("Applying pending corrections from lyrics review")
                self.correction_result = self._update_correction_result(self.correction_result, self.pending_corrections)
                self.pending_corrections = None  # Clear after applying
            elif updated_data:
                # Fallback: apply corrections from request body if no pending corrections
                self.correction_result = self._update_correction_result(self.correction_result, updated_data)

            # Store instrumental selection in correction result metadata
            if self.instrumental_selection:
                if not self.correction_result.metadata:
                    self.correction_result.metadata = {}
                self.correction_result.metadata["instrumental_selection"] = self.instrumental_selection

            self.review_completed = True
            return {"status": "success", "job_status": "completed", "message": "Review completed"}
        except Exception as e:
            self.logger.error(f"Failed to complete review: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    async def ping(self):
        """Simple ping endpoint for testing."""
        return {"status": "ok"}

    async def get_audio(self, audio_hash: str):
        """Stream the audio file."""
        try:
            if (
                not self.audio_filepath
                or not os.path.exists(self.audio_filepath)
                or not self.correction_result.metadata
                or self.correction_result.metadata.get("audio_hash") != audio_hash
            ):
                raise FileNotFoundError("Audio file not found")

            return FileResponse(self.audio_filepath, media_type="audio/mpeg", filename=os.path.basename(self.audio_filepath))
        except Exception as e:
            raise HTTPException(status_code=404, detail="Audio file not found")

    async def get_instrumental_audio(self, stem_type: str):
        """Stream instrumental audio files.

        Args:
            stem_type: One of "clean", "with_backing", or "backing_vocals"
        """
        # Map stem type to file path
        path_map = {
            "clean": self.clean_instrumental_path,
            "with_backing": self.with_backing_path,
            "backing_vocals": self.backing_vocals_path,
        }

        audio_path = path_map.get(stem_type)
        if not audio_path or not os.path.exists(audio_path):
            raise HTTPException(status_code=404, detail=f"Instrumental audio not found: {stem_type}")

        # Determine content type based on file extension
        ext = os.path.splitext(audio_path)[1].lower()
        content_types = {
            ".flac": "audio/flac",
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
            ".m4a": "audio/mp4",
        }
        content_type = content_types.get(ext, "application/octet-stream")

        return FileResponse(audio_path, media_type=content_type, filename=os.path.basename(audio_path))

    async def generate_preview_video(self, updated_data: Dict[str, Any] = Body(...)):
        """Generate a preview video with the current corrections."""
        # Check if preview video generation is allowed (disabled with --no-video)
        if not self.output_config.allow_preview_video:
            raise HTTPException(status_code=400, detail="Preview video generation disabled (--no-video flag set)")

        try:
            # Use shared operation for preview generation
            result = CorrectionOperations.generate_preview_video(
                correction_result=self.correction_result,
                updated_data=updated_data,
                output_config=self.output_config,
                audio_filepath=self.audio_filepath,
                logger=self.logger
            )
            
            # Store the path for later retrieval
            if not hasattr(self, "preview_videos"):
                self.preview_videos = {}
            self.preview_videos[result["preview_hash"]] = result["video_path"]

            return {"status": "success", "preview_hash": result["preview_hash"]}

        except Exception as e:
            self.logger.error(f"Failed to generate preview video: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    async def get_preview_video(self, preview_hash: str):
        """Stream the preview video."""
        try:
            if not hasattr(self, "preview_videos") or preview_hash not in self.preview_videos:
                raise FileNotFoundError("Preview video not found")

            video_path = self.preview_videos[preview_hash]
            if not os.path.exists(video_path):
                raise FileNotFoundError("Preview video file not found")

            return FileResponse(
                video_path,
                media_type="video/mp4",
                filename=os.path.basename(video_path),
                headers={
                    "Accept-Ranges": "bytes",
                    "Content-Disposition": "inline",
                    "Cache-Control": "no-cache",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except Exception as e:
            self.logger.error(f"Failed to stream preview video: {str(e)}")
            raise HTTPException(status_code=404, detail="Preview video not found")

    async def update_handlers(self, enabled_handlers: List[str] = Body(...)):
        """Update enabled correction handlers and rerun correction."""
        try:
            # Use shared operation for handler updates
            self.correction_result = CorrectionOperations.update_correction_handlers(
                correction_result=self.correction_result,
                enabled_handlers=enabled_handlers,
                cache_dir=self.output_config.cache_dir,
                logger=self.logger
            )

            return {"status": "success", "data": self.correction_result.to_dict()}
        except Exception as e:
            self.logger.error(f"Failed to update handlers: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    def _create_lyrics_data_from_text(self, text: str, source: str) -> LyricsData:
        """Create LyricsData object from plain text lyrics."""
        self.logger.info(f"Creating LyricsData for source '{source}'")

        # Split text into lines and create segments
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        self.logger.info(f"Found {len(lines)} non-empty lines in input text")

        segments = []
        for i, line in enumerate(lines):
            # Split line into words
            word_texts = line.strip().split()
            words = []

            for j, word_text in enumerate(word_texts):
                word = Word(
                    id=f"manual_{source}_word_{i}_{j}",  # Create unique ID for each word
                    text=word_text,
                    start_time=0.0,  # Placeholder timing
                    end_time=0.0,
                    confidence=1.0,  # Reference lyrics are considered ground truth
                    created_during_correction=False,
                )
                words.append(word)

            segments.append(
                LyricsSegment(
                    id=f"manual_{source}_{i}",
                    text=line,
                    words=words,  # Now including the word objects
                    start_time=0.0,  # Placeholder timing
                    end_time=0.0,
                )
            )

        # Create metadata
        self.logger.info("Creating metadata for LyricsData")
        metadata = LyricsMetadata(
            source=source,
            track_name=self.correction_result.metadata.get("title", "") or "",
            artist_names=self.correction_result.metadata.get("artist", "") or "",
            is_synced=False,
            lyrics_provider="manual",
            lyrics_provider_id="",
            album_name=None,
            duration_ms=None,
            explicit=None,
            language=None,
            provider_metadata={},
        )
        self.logger.info(f"Created metadata: {metadata}")

        lyrics_data = LyricsData(segments=segments, metadata=metadata, source=source)
        self.logger.info(f"Created LyricsData with {len(segments)} segments and {sum(len(s.words) for s in segments)} total words")

        return lyrics_data

    async def add_lyrics(self, data: Dict[str, str] = Body(...)):
        """Add new lyrics source and rerun correction."""
        try:
            source = data.get("source", "").strip()
            lyrics_text = data.get("lyrics", "").strip()

            self.logger.info(f"Received request to add lyrics source '{source}' with {len(lyrics_text)} characters")

            # Use shared operation for adding lyrics source
            self.correction_result = CorrectionOperations.add_lyrics_source(
                correction_result=self.correction_result,
                source=source,
                lyrics_text=lyrics_text,
                cache_dir=self.output_config.cache_dir,
                logger=self.logger
            )

            return {"status": "success", "data": self.correction_result.to_dict()}

        except ValueError as e:
            # Convert ValueError to HTTPException for API consistency
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            self.logger.error(f"Failed to add lyrics: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    def start(self) -> CorrectionResult:
        """Start the review server and wait for completion."""
        # Generate audio hash if audio file exists
        if self.audio_filepath and os.path.exists(self.audio_filepath):
            with open(self.audio_filepath, "rb") as f:
                audio_hash = hashlib.md5(f.read()).hexdigest()
            if not self.correction_result.metadata:
                self.correction_result.metadata = {}
            self.correction_result.metadata["audio_hash"] = audio_hash

        server = None
        server_thread = None
        sock = None

        # Get port from environment variable (default 8000)
        port = int(os.environ.get("LYRICS_REVIEW_PORT", "8000"))

        try:
            # Check port availability
            while True:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    # Port is in use, get process info
                    process_info = ""
                    if os.name != "nt":  # Unix-like systems
                        try:
                            process_info = os.popen(f"lsof -i:{port}").read().strip()
                        except:
                            pass

                    self.logger.warning(
                        f"Port {port} is in use. Waiting for it to become available...\n"
                        f"Process using port {port}:\n{process_info}\n"
                        f"To manually free the port, you can run: lsof -ti:{port} | xargs kill -9"
                    )
                    sock.close()
                    time.sleep(30)
                else:
                    sock.close()
                    break

            # Start server
            config = uvicorn.Config(self.app, host="127.0.0.1", port=port, log_level="error")
            server = uvicorn.Server(config)
            server_thread = Thread(target=server.run, daemon=True)
            server_thread.start()
            time.sleep(0.5)  # Reduced wait time

            # Open browser to the Next.js review UI
            # The frontend will automatically detect local mode and skip auth
            browser_url = f"http://localhost:{port}/app/jobs/local/review"
            self.logger.info(f"Opening review UI: {browser_url}")
            webbrowser.open(browser_url)

            while not self.review_completed:
                time.sleep(0.1)

            return self.correction_result

        except KeyboardInterrupt:
            self.logger.info("Received interrupt, shutting down server...")
            raise
        except Exception as e:
            self.logger.error(f"Error during review server operation: {e}")
            raise
        finally:
            # Comprehensive cleanup
            if sock:
                try:
                    sock.close()
                except:
                    pass

            if server:
                server.should_exit = True

            if server_thread and server_thread.is_alive():
                server_thread.join(timeout=1)

            # Force cleanup any remaining server resources
            try:
                import multiprocessing.resource_tracker

                multiprocessing.resource_tracker._resource_tracker = None
            except:
                pass
