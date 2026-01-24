#!/usr/bin/env python3
"""
Review Test Fixtures - Visual review of anchor/gap results for production test data.

This script runs the anchor sequence finder on each test fixture and serves
the results through the existing lyrics review UI for visual inspection.

Usage:
    python scripts/review_test_fixtures.py [--port PORT]

Controls:
    - Navigate between fixtures using the on-screen buttons
    - Press 'q' in the terminal to quit
"""

import argparse
import json
import logging
import os
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from karaoke_gen.lyrics_transcriber.types import (
    AnchorSequence,
    CorrectionResult,
    GapSequence,
    LyricsData,
    LyricsMetadata,
    LyricsSegment,
    ScoredAnchor,
    TranscriptionData,
    TranscriptionResult,
    Word,
)
from karaoke_gen.lyrics_transcriber.correction.anchor_sequence import AnchorSequenceFinder


# Path to fixture data
FIXTURES_DIR = Path(__file__).parent.parent / "tests" / "fixtures" / "anchor_sequence_real_data"


def setup_logging() -> logging.Logger:
    """Set up logging."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    return logging.getLogger(__name__)


def load_fixture(job_dir: Path) -> Optional[dict]:
    """Load a fixture from a job directory."""
    corrections_path = job_dir / "corrections.json"
    if not corrections_path.exists():
        return None

    with open(corrections_path) as f:
        return json.load(f)


def get_fixture_metadata(job_dir: Path) -> dict:
    """Get metadata for a fixture."""
    metadata_path = job_dir / "metadata.json"
    if metadata_path.exists():
        with open(metadata_path) as f:
            return json.load(f)
    return {"artist": "Unknown", "title": "Unknown", "job_id": job_dir.name}


def reconstruct_transcription_result(data: dict) -> Tuple[str, TranscriptionResult]:
    """Reconstruct TranscriptionResult from corrections.json data."""
    segments = []
    all_words = []

    for seg_data in data.get("original_segments", []):
        words = [Word.from_dict(w) for w in seg_data.get("words", [])]
        segment = LyricsSegment(
            id=seg_data["id"],
            text=seg_data["text"],
            words=words,
            start_time=seg_data.get("start_time", 0.0),
            end_time=seg_data.get("end_time", 0.0),
        )
        segments.append(segment)
        all_words.extend(words)

    transcribed_text = " ".join(w.text for w in all_words)

    transcription_data = TranscriptionData(
        segments=segments,
        words=all_words,
        text=transcribed_text,
        source="audioshake",
        metadata=None,
    )

    transcription_result = TranscriptionResult(
        name="audioshake",
        priority=1,
        result=transcription_data,
    )

    return transcribed_text, transcription_result


def reconstruct_references(data: dict) -> Dict[str, LyricsData]:
    """Reconstruct reference lyrics from corrections.json data."""
    references = {}

    ref_lyrics = data.get("reference_lyrics", {})
    for source, ref_data in ref_lyrics.items():
        if not ref_data:
            continue

        segments = []
        for seg_data in ref_data.get("segments", []):
            words = [Word.from_dict(w) for w in seg_data.get("words", [])]
            segment = LyricsSegment(
                id=seg_data["id"],
                text=seg_data["text"],
                words=words,
                start_time=seg_data.get("start_time") or 0.0,
                end_time=seg_data.get("end_time") or 0.0,
            )
            segments.append(segment)

        meta_data = ref_data.get("metadata", {})
        metadata = LyricsMetadata(
            source=source,
            track_name=meta_data.get("track_name", ""),
            artist_names=meta_data.get("artist_names", ""),
            album_name=meta_data.get("album_name"),
            duration_ms=meta_data.get("duration_ms"),
            explicit=meta_data.get("explicit"),
            language=meta_data.get("language"),
            is_synced=meta_data.get("is_synced", False),
            lyrics_provider=meta_data.get("lyrics_provider"),
            lyrics_provider_id=meta_data.get("lyrics_provider_id"),
            provider_metadata=meta_data.get("provider_metadata", {}),
        )

        lyrics_data = LyricsData(
            segments=segments,
            metadata=metadata,
            source=source,
        )
        references[source] = lyrics_data

    return references


def run_anchor_finder_on_fixture(
    fixture_data: dict,
    cache_dir: str,
    logger: logging.Logger,
) -> Tuple[List[ScoredAnchor], List[GapSequence]]:
    """Run the anchor finder on fixture data and return anchors and gaps."""
    transcribed_text, transcription_result = reconstruct_transcription_result(fixture_data)
    references = reconstruct_references(fixture_data)

    if not references:
        return [], []

    finder = AnchorSequenceFinder(
        cache_dir=cache_dir,
        min_sequence_length=3,
        min_sources=1,
        timeout_seconds=120,
        logger=logger,
    )

    anchors = finder.find_anchors(
        transcribed=transcribed_text,
        references=references,
        transcription_result=transcription_result,
    )

    gaps = finder.find_gaps(
        transcribed=transcribed_text,
        anchors=anchors,
        references=references,
        transcription_result=transcription_result,
    )

    return anchors, gaps


def build_correction_data(
    fixture_data: dict,
    anchors: List[ScoredAnchor],
    gaps: List[GapSequence],
    metadata: dict,
) -> dict:
    """Build correction data in the format expected by the review UI."""
    # Convert anchors to dict format
    anchor_dicts = []
    for scored_anchor in anchors:
        anchor_dict = scored_anchor.anchor.to_dict()
        anchor_dict["phrase_score"] = scored_anchor.phrase_score.to_dict()
        anchor_dicts.append(anchor_dict)

    # Convert gaps to dict format
    gap_dicts = [gap.to_dict() for gap in gaps]

    return {
        "original_segments": fixture_data.get("original_segments", []),
        "reference_lyrics": fixture_data.get("reference_lyrics", {}),
        "anchor_sequences": anchor_dicts,
        "gap_sequences": gap_dicts,
        "resized_segments": fixture_data.get("original_segments", []),
        "corrections_made": 0,
        "confidence": 0.0,
        "corrections": [],
        "corrected_segments": fixture_data.get("original_segments", []),
        "metadata": {
            "track_name": metadata.get("title", "Unknown"),
            "artist_names": metadata.get("artist", "Unknown"),
            "handlers": {},
        },
        "correction_steps": [],
        "word_id_map": {},
        "segment_id_map": {},
    }


class FixtureReviewServer:
    """Server for reviewing test fixture anchor/gap results."""

    def __init__(self, port: int, logger: logging.Logger):
        self.port = port
        self.logger = logger
        self.app = FastAPI()
        self.cache_dir = tempfile.mkdtemp()

        # Load all fixtures
        self.fixtures = self._load_all_fixtures()
        self.current_index = 0
        self.processed_fixtures: Dict[int, dict] = {}

        self._configure_cors()
        self._register_routes()
        self._mount_frontend()

    def _load_all_fixtures(self) -> List[Tuple[Path, dict, dict]]:
        """Load all fixture directories with their data and metadata."""
        fixtures = []
        for item in sorted(FIXTURES_DIR.iterdir()):
            if item.is_dir():
                data = load_fixture(item)
                if data and data.get("reference_lyrics"):
                    metadata = get_fixture_metadata(item)
                    fixtures.append((item, data, metadata))
        return fixtures

    def _configure_cors(self) -> None:
        """Configure CORS middleware."""
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Add request logging middleware
        @self.app.middleware("http")
        async def log_requests(request, call_next):
            self.logger.info(f"📥 {request.method} {request.url.path}")
            response = await call_next(request)
            self.logger.info(f"📤 {request.method} {request.url.path} -> {response.status_code}")
            return response

    def _mount_frontend(self) -> None:
        """Mount the Next.js frontend static files."""
        try:
            from karaoke_gen.nextjs_frontend import get_nextjs_assets_dir, is_nextjs_frontend_available

            if not is_nextjs_frontend_available():
                self.logger.warning("Next.js frontend not available, using fallback HTML")
                self._frontend_dir = None
                return

            frontend_dir = str(get_nextjs_assets_dir())
            self.logger.info(f"Using Next.js frontend from {frontend_dir}")
            self._frontend_dir = frontend_dir

            nextjs_static = os.path.join(frontend_dir, "_next")
            if os.path.exists(nextjs_static):
                self.app.mount("/_next", StaticFiles(directory=nextjs_static), name="nextjs_static")

            self.app.mount("/static", StaticFiles(directory=frontend_dir), name="frontend_static")
        except Exception as e:
            self.logger.warning(f"Could not load Next.js frontend: {e}")
            self._frontend_dir = None

    def _get_processed_fixture(self, index: int) -> dict:
        """Get processed fixture data, computing if necessary."""
        if index not in self.processed_fixtures:
            path, data, metadata = self.fixtures[index]
            self.logger.info(f"Processing fixture: {metadata.get('artist', 'Unknown')} - {metadata.get('title', 'Unknown')}")

            anchors, gaps = run_anchor_finder_on_fixture(data, self.cache_dir, self.logger)
            correction_data = build_correction_data(data, anchors, gaps, metadata)

            # Add metrics summary
            total_words = sum(len(seg.get("words", [])) for seg in data.get("original_segments", []))
            words_in_anchors = sum(len(a.get("transcribed_word_ids", [])) for a in correction_data["anchor_sequences"])
            words_in_gaps = sum(len(g.get("transcribed_word_ids", [])) for g in correction_data["gap_sequences"])
            single_word_gaps = sum(1 for g in correction_data["gap_sequences"] if len(g.get("transcribed_word_ids", [])) == 1)

            correction_data["_metrics"] = {
                "total_words": total_words,
                "words_in_anchors": words_in_anchors,
                "words_in_gaps": words_in_gaps,
                "anchor_coverage_pct": round(words_in_anchors / total_words * 100, 1) if total_words > 0 else 0,
                "total_anchors": len(correction_data["anchor_sequences"]),
                "total_gaps": len(correction_data["gap_sequences"]),
                "single_word_gaps": single_word_gaps,
            }

            self.processed_fixtures[index] = correction_data

        return self.processed_fixtures[index]

    def _register_routes(self) -> None:
        """Register API routes."""
        from fastapi import Form, Query

        @self.app.get("/")
        async def root():
            """Redirect to review page."""
            return HTMLResponse(content=self._get_navigation_html(), status_code=200)

        @self.app.get("/app/jobs/local/review")
        async def serve_local_review():
            """Serve the review page."""
            if self._frontend_dir:
                local_review_html = os.path.join(self._frontend_dir, "app", "jobs", "local", "review", "index.html")
                if os.path.exists(local_review_html):
                    return FileResponse(local_review_html, media_type="text/html")
            # Fallback to navigation page
            return HTMLResponse(content=self._get_navigation_html(), status_code=200)

        # Legacy route for backwards compatibility
        @self.app.get("/api/correction-data")
        async def get_correction_data_legacy():
            """Get correction data for current fixture (legacy route)."""
            if not self.fixtures:
                raise HTTPException(status_code=404, detail="No fixtures found")
            return self._get_processed_fixture(self.current_index)

        # Route that the frontend actually uses
        @self.app.get("/api/review/{job_id}/correction-data")
        async def get_correction_data(job_id: str):
            """Get correction data for current fixture."""
            if not self.fixtures:
                raise HTTPException(status_code=404, detail="No fixtures found")
            return self._get_processed_fixture(self.current_index)

        # Cloud-compatible routes (used by Next.js frontend)
        @self.app.get("/api/jobs/{job_id}/corrections")
        async def get_corrections(job_id: str):
            """Get correction data (cloud-compatible route)."""
            if not self.fixtures:
                raise HTTPException(status_code=404, detail="No fixtures found")
            return self._get_processed_fixture(self.current_index)

        @self.app.get("/api/jobs/{job_id}")
        async def get_job(job_id: str):
            """Get mock job data for local mode."""
            if not self.fixtures:
                raise HTTPException(status_code=404, detail="No fixtures found")
            _, _, metadata = self.fixtures[self.current_index]
            return {
                "job_id": "local",
                "status": "awaiting_review",
                "progress": 100,
                "artist": metadata.get("artist", "Unknown"),
                "title": metadata.get("title", "Unknown"),
                "audio_hash": "fixture-review",
            }

        @self.app.get("/api/fixtures")
        async def list_fixtures():
            """List all fixtures with metadata."""
            fixtures_list = []
            for i, (path, data, metadata) in enumerate(self.fixtures):
                # Get metrics if already processed
                metrics = {}
                if i in self.processed_fixtures:
                    metrics = self.processed_fixtures[i].get("_metrics", {})

                fixtures_list.append({
                    "index": i,
                    "name": f"{metadata.get('artist', 'Unknown')} - {metadata.get('title', 'Unknown')}",
                    "job_id": metadata.get("job_id", path.name),
                    "is_current": i == self.current_index,
                    "is_processed": i in self.processed_fixtures,
                    "metrics": metrics,
                })
            return {"fixtures": fixtures_list, "current_index": self.current_index}

        @self.app.get("/api/fixtures/navigate/{direction}")
        async def navigate_fixture_get(direction: str):
            """Navigate to next/previous fixture (GET version)."""
            return self._do_navigate(direction)

        @self.app.post("/api/fixtures/navigate")
        async def navigate_fixture_post(direction: str = Form(default="next")):
            """Navigate to next/previous fixture (POST version)."""
            return self._do_navigate(direction)

        @self.app.post("/api/complete")
        async def complete_review():
            """Handle review completion (no-op for fixture review)."""
            return {"status": "ok", "message": "Fixture review mode - changes not saved"}

        @self.app.put("/api/jobs/{job_id}/corrections")
        async def save_corrections(job_id: str):
            """Handle save corrections (no-op for fixture review)."""
            return {"status": "ok", "message": "Fixture review mode - changes not saved"}

        @self.app.get("/api/audio/{audio_hash}")
        async def get_audio(audio_hash: str):
            """No audio available for fixture review."""
            raise HTTPException(status_code=404, detail="Audio not available in fixture review mode")

        @self.app.get("/api/review/{job_id}/audio/{audio_hash}")
        async def get_audio_review(job_id: str, audio_hash: str):
            """No audio available for fixture review."""
            raise HTTPException(status_code=404, detail="Audio not available in fixture review mode")

    def _do_navigate(self, direction: str) -> dict:
        """Perform navigation to next/previous fixture."""
        if direction == "next":
            self.current_index = (self.current_index + 1) % len(self.fixtures)
        elif direction == "prev":
            self.current_index = (self.current_index - 1) % len(self.fixtures)
        elif direction.isdigit():
            idx = int(direction)
            if 0 <= idx < len(self.fixtures):
                self.current_index = idx
        return {"current_index": self.current_index}

    def _get_navigation_html(self) -> str:
        """Generate navigation HTML page."""
        fixtures_html = ""
        for i, (path, data, metadata) in enumerate(self.fixtures):
            name = f"{metadata.get('artist', 'Unknown')} - {metadata.get('title', 'Unknown')}"
            current_class = "current" if i == self.current_index else ""

            # Get metrics if processed
            metrics_html = ""
            if i in self.processed_fixtures:
                m = self.processed_fixtures[i].get("_metrics", {})
                metrics_html = f"""<div class="metrics">Coverage: {m.get('anchor_coverage_pct', 0)}% | Gaps: {m.get('total_gaps', 0)} | 1-word: {m.get('single_word_gaps', 0)}</div>"""

            fixtures_html += f"""
                <div class="fixture-item {current_class}" onclick="selectFixture({i})">
                    <div>{i + 1}. {name}</div>
                    {metrics_html}
                </div>
            """

        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Fixture Review - Anchor/Gap Analysis</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 0; display: flex; height: 100vh; }}
                .sidebar {{ width: 350px; min-width: 350px; background: #f5f5f5; padding: 15px; overflow-y: auto; border-right: 1px solid #ddd; }}
                .main-content {{ flex: 1; display: flex; flex-direction: column; }}
                h1 {{ color: #333; font-size: 1.3em; margin: 0 0 10px 0; }}
                .nav-buttons {{ margin: 10px 0; display: flex; gap: 8px; }}
                .nav-buttons button {{ padding: 8px 16px; cursor: pointer; font-size: 14px; border: 1px solid #ddd; background: white; border-radius: 4px; }}
                .nav-buttons button:hover {{ background: #e8e8e8; }}
                .fixtures-list {{ border: 1px solid #ddd; border-radius: 8px; overflow: hidden; background: white; }}
                .fixture-item {{ padding: 8px 12px; border-bottom: 1px solid #eee; cursor: pointer; font-size: 13px; }}
                .fixture-item:hover {{ background: #f0f0f0; }}
                .fixture-item.current {{ background: #e0f0ff; font-weight: bold; }}
                .fixture-item .metrics {{ color: #666; font-size: 11px; margin-top: 2px; }}
                .metrics-summary {{ background: white; padding: 10px; border-radius: 8px; margin: 10px 0; font-size: 13px; border: 1px solid #ddd; }}
                .review-frame {{ flex: 1; border: none; width: 100%; }}
                .header-bar {{ padding: 8px 15px; background: #333; color: white; display: flex; justify-content: space-between; align-items: center; }}
                .header-bar h2 {{ margin: 0; font-size: 14px; font-weight: normal; }}
                .header-bar button {{ padding: 6px 12px; cursor: pointer; font-size: 12px; background: #555; border: none; color: white; border-radius: 4px; }}
                .header-bar button:hover {{ background: #666; }}
            </style>
        </head>
        <body>
            <div class="sidebar">
                <h1>Fixture Review</h1>
                <p style="font-size: 12px; color: #666; margin: 0 0 10px 0;">Review anchor sequence finder results</p>

                <div class="nav-buttons">
                    <button onclick="navigate('prev')">&larr; Prev</button>
                    <button onclick="navigate('next')">Next &rarr;</button>
                </div>

                <div id="current-fixture" class="metrics-summary">
                    <strong>Loading...</strong>
                </div>

                <h3 style="font-size: 12px; margin: 15px 0 8px 0; color: #666;">All Fixtures ({len(self.fixtures)})</h3>
                <div class="fixtures-list">
                    {fixtures_html}
                </div>
            </div>

            <div class="main-content">
                <div class="header-bar">
                    <h2 id="frame-title">Loading lyrics review...</h2>
                    <button onclick="window.open('/app/jobs/local/review', '_blank')">Open in New Tab</button>
                </div>
                <iframe id="review-frame" class="review-frame" src="/app/jobs/local/review"></iframe>
            </div>

            <script>
                const TOTAL_FIXTURES = {len(self.fixtures)};

                async function navigate(direction) {{
                    await fetch('/api/fixtures/navigate/' + direction);
                    loadCurrentFixture();
                    document.getElementById('review-frame').src = '/app/jobs/local/review?' + Date.now();
                }}

                async function selectFixture(index) {{
                    await fetch('/api/fixtures/navigate/' + index);
                    loadCurrentFixture();
                    document.getElementById('review-frame').src = '/app/jobs/local/review?' + Date.now();
                }}

                async function loadCurrentFixture() {{
                    const resp = await fetch('/api/fixtures');
                    const data = await resp.json();
                    const current = data.fixtures.find(f => f.is_current);

                    // Update current fixture display
                    if (current) {{
                        let html = '<strong>' + (current.index + 1) + '/' + TOTAL_FIXTURES + ': ' + current.name + '</strong>';
                        if (current.metrics.anchor_coverage_pct !== undefined) {{
                            html += '<br><span style="color: #666;">Coverage: ' + current.metrics.anchor_coverage_pct + '% | Gaps: ' + current.metrics.total_gaps + ' | 1-word: ' + current.metrics.single_word_gaps + '</span>';
                        }}
                        document.getElementById('current-fixture').innerHTML = html;
                        document.getElementById('frame-title').textContent = current.name;
                    }}

                    // Update fixture list highlighting
                    document.querySelectorAll('.fixture-item').forEach((el, idx) => {{
                        el.classList.toggle('current', idx === current.index);
                    }});
                }}

                // Load on page load
                loadCurrentFixture();
            </script>
        </body>
        </html>
        """

    def start(self) -> None:
        """Start the review server."""
        if not self.fixtures:
            self.logger.error("No valid fixtures found!")
            return

        self.logger.info(f"Found {len(self.fixtures)} fixtures to review")

        # Pre-process first fixture
        self.logger.info("Pre-processing first fixture...")
        self._get_processed_fixture(0)

        # Print startup info
        print("\n" + "=" * 60)
        print("  FIXTURE REVIEW SERVER")
        print("=" * 60)
        print(f"\n  Navigation page: http://localhost:{self.port}/")
        print(f"  Lyrics review:   http://localhost:{self.port}/app/jobs/local/review")
        print(f"\n  Fixtures loaded: {len(self.fixtures)}")
        print("\n  Controls (on navigation page):")
        print("    - Click fixture name to select")
        print("    - Use Previous/Next buttons to navigate")
        print("    - Click 'Open Full Review UI' for detailed view")
        print("\n  Press Ctrl+C to stop the server")
        print("=" * 60 + "\n")

        # Open browser
        def open_browser():
            time.sleep(1)
            webbrowser.open(f"http://localhost:{self.port}")

        threading.Thread(target=open_browser, daemon=True).start()

        # Run server
        uvicorn.run(self.app, host="127.0.0.1", port=self.port, log_level="warning")


def main():
    parser = argparse.ArgumentParser(description="Review test fixtures with the lyrics review UI")
    parser.add_argument("--port", type=int, default=8765, help="Port to run the server on")
    args = parser.parse_args()

    logger = setup_logging()

    if not FIXTURES_DIR.exists():
        logger.error(f"Fixtures directory not found: {FIXTURES_DIR}")
        sys.exit(1)

    server = FixtureReviewServer(port=args.port, logger=logger)
    server.start()


if __name__ == "__main__":
    main()
