"""
Integration test for the combined review flow (lyrics + instrumental selection).

This test starts the ReviewServer with test data and uses Playwright to:
1. Navigate through the lyrics review UI
2. Proceed to instrumental selection
3. Submit the combined review
4. Verify the server processes the submission correctly

Run with:
    poetry run pytest tests/integration/test_combined_review_flow.py -v
"""

import os
import pytest
import tempfile
import time
import threading
import logging
import json
from typing import Optional
from pathlib import Path

# Skip if playwright not installed
pytest.importorskip("playwright")

from playwright.sync_api import sync_playwright, expect

from karaoke_gen.lyrics_transcriber.types import CorrectionResult, LyricsSegment, Word
from karaoke_gen.lyrics_transcriber.core.config import OutputConfig
from karaoke_gen.lyrics_transcriber.review.server import ReviewServer


def create_test_correction_result() -> CorrectionResult:
    """Create a minimal CorrectionResult for testing."""
    # Create test segments with words
    words1 = [
        Word(id="w1", text="Hello", start_time=0.0, end_time=0.5, confidence=0.95),
        Word(id="w2", text="world", start_time=0.5, end_time=1.0, confidence=0.92),
        Word(id="w3", text="test", start_time=1.0, end_time=1.5, confidence=0.88),
    ]
    words2 = [
        Word(id="w4", text="Second", start_time=2.0, end_time=2.5, confidence=0.96),
        Word(id="w5", text="line", start_time=2.5, end_time=3.0, confidence=0.97),
    ]

    segment1 = LyricsSegment(
        id="seg1",
        text="Hello world test",
        start_time=0.0,
        end_time=1.5,
        words=words1,
    )
    segment2 = LyricsSegment(
        id="seg2",
        text="Second line",
        start_time=2.0,
        end_time=3.0,
        words=words2,
    )

    return CorrectionResult(
        original_segments=[segment1, segment2],
        corrected_segments=[segment1, segment2],
        corrections=[],
        reference_lyrics={},
        anchor_sequences=[],
        gap_sequences=[],
        resized_segments=[],
        corrections_made=0,
        confidence=0.9,
        metadata={
            "audio_hash": "test_hash_123",
            "artist": "Test Artist",
            "title": "Test Song",
        },
        correction_steps=[],
        word_id_map={},
        segment_id_map={},
    )


def create_test_audio_file(path: str) -> str:
    """Create a minimal test audio file (silence)."""
    # Create a minimal WAV file (8kHz mono, 1 second of silence)
    import struct

    sample_rate = 8000
    duration = 1  # seconds
    num_samples = sample_rate * duration

    with open(path, 'wb') as f:
        # WAV header
        f.write(b'RIFF')
        file_size = 44 + num_samples * 2 - 8
        f.write(struct.pack('<I', file_size))
        f.write(b'WAVE')
        f.write(b'fmt ')
        f.write(struct.pack('<I', 16))  # Subchunk1Size
        f.write(struct.pack('<H', 1))   # AudioFormat (PCM)
        f.write(struct.pack('<H', 1))   # NumChannels
        f.write(struct.pack('<I', sample_rate))  # SampleRate
        f.write(struct.pack('<I', sample_rate * 2))  # ByteRate
        f.write(struct.pack('<H', 2))   # BlockAlign
        f.write(struct.pack('<H', 16))  # BitsPerSample
        f.write(b'data')
        f.write(struct.pack('<I', num_samples * 2))  # Subchunk2Size
        # Write silence
        for _ in range(num_samples):
            f.write(struct.pack('<h', 0))

    return path


class ReviewServerFixture:
    """Fixture to manage ReviewServer lifecycle for testing."""

    def __init__(self, port: int = 8765):
        self.port = port
        self.server: Optional[ReviewServer] = None
        self.server_thread: Optional[threading.Thread] = None
        self.temp_dir: Optional[tempfile.TemporaryDirectory] = None
        self.logger = logging.getLogger(__name__)

    def start(self) -> str:
        """Start the review server and return the base URL."""
        # Create temp directory for test files
        self.temp_dir = tempfile.TemporaryDirectory()
        temp_path = self.temp_dir.name

        # Create test audio file
        audio_path = os.path.join(temp_path, "test_audio.wav")
        create_test_audio_file(audio_path)

        # Create test instrumental files (just copy the audio for testing)
        clean_instrumental = os.path.join(temp_path, "clean_instrumental.wav")
        with_backing = os.path.join(temp_path, "with_backing.wav")
        backing_vocals = os.path.join(temp_path, "backing_vocals.wav")

        for path in [clean_instrumental, with_backing, backing_vocals]:
            create_test_audio_file(path)

        # Create output config
        output_config = OutputConfig(
            output_dir=temp_path,
            cache_dir=os.path.join(temp_path, ".cache"),
            output_styles_json="",  # Not needed for this test
            render_video=False,
            generate_cdg=False,
            generate_plain_text=False,
            generate_lrc=False,
        )
        os.makedirs(output_config.cache_dir, exist_ok=True)

        # Create correction result
        correction_result = create_test_correction_result()

        # Set up instrumental options
        instrumental_options = [
            {"id": "clean", "label": "Clean Instrumental", "audio_path": clean_instrumental},
            {"id": "with_backing", "label": "With Backing Vocals", "audio_path": with_backing},
        ]

        backing_vocals_analysis = {
            "has_audible_content": True,
            "total_duration_seconds": 1.0,
            "audible_segments": [],
            "recommended_selection": "with_backing",
            "total_audible_duration_seconds": 0.5,
            "audible_percentage": 50.0,
        }

        # Create server
        self.server = ReviewServer(
            correction_result=correction_result,
            output_config=output_config,
            audio_filepath=audio_path,
            logger=self.logger,
            instrumental_options=instrumental_options,
            backing_vocals_analysis=backing_vocals_analysis,
            clean_instrumental_path=clean_instrumental,
            with_backing_path=with_backing,
            backing_vocals_path=backing_vocals,
        )

        # Set environment variable for port
        os.environ["LYRICS_REVIEW_PORT"] = str(self.port)

        # Start server in background thread (don't open browser)
        import uvicorn
        config = uvicorn.Config(
            self.server.app,
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
        )
        server_instance = uvicorn.Server(config)

        self.server_thread = threading.Thread(target=server_instance.run, daemon=True)
        self.server_thread.start()

        # Wait for server to start
        time.sleep(1.0)

        return f"http://127.0.0.1:{self.port}"

    def stop(self):
        """Stop the review server."""
        if self.temp_dir:
            self.temp_dir.cleanup()
        # Server thread is daemon, will stop with test

    @property
    def review_completed(self) -> bool:
        """Check if review was completed."""
        return self.server.review_completed if self.server else False

    @property
    def instrumental_selection(self) -> Optional[str]:
        """Get the instrumental selection made during review."""
        return self.server.instrumental_selection if self.server else None

    def reset_state(self):
        """Reset server state for tests that modify it."""
        if self.server:
            self.server.review_completed = False
            self.server.instrumental_selection = None


@pytest.fixture(scope="module")
def review_server():
    """Pytest fixture for ReviewServer - module-scoped to avoid port conflicts."""
    fixture = ReviewServerFixture(port=8765)
    base_url = fixture.start()
    yield fixture, base_url
    fixture.stop()


@pytest.mark.integration
def test_combined_review_flow_lyrics_loads(review_server):
    """Test that the lyrics review page loads correctly."""
    fixture, base_url = review_server

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Capture console logs for debugging
        console_logs = []
        page.on("console", lambda msg: console_logs.append(f"{msg.type}: {msg.text}"))

        try:
            # Navigate to lyrics review page
            page.goto(f"{base_url}/app/jobs/local/review", wait_until="networkidle")

            # Wait a bit longer for JS to execute
            time.sleep(2)

            # Debug: capture page content
            print(f"Page URL: {page.url}")
            print(f"Page title: {page.title()}")
            page.screenshot(path="/tmp/test_lyrics_loads.png")
            print(f"Console logs: {console_logs[:10]}")

            # Check if there's a loading state or error
            page_text = page.locator("body").inner_text()
            print(f"Page text (first 500 chars): {page_text[:500]}")

            # Should show the lyrics review UI
            expect(page.locator("text=Lyrics Transcription Review")).to_be_visible(timeout=15000)

            # Should show our test lyrics
            expect(page.locator("text=Hello").first).to_be_visible(timeout=5000)
            expect(page.locator("text=world").first).to_be_visible()

        finally:
            browser.close()


@pytest.mark.integration
def test_combined_review_flow_instrumental_loads(review_server):
    """Test that the instrumental review page loads correctly."""
    fixture, base_url = review_server

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            # Navigate directly to instrumental review page
            page.goto(f"{base_url}/app/jobs/local/instrumental", wait_until="networkidle")

            # Should show the instrumental review UI
            expect(page.locator("text=Instrumental Review").first).to_be_visible(timeout=10000)

            # Should show instrumental options (use test-id for specific elements)
            expect(page.get_by_test_id("selection-option-clean")).to_be_visible(timeout=5000)
            expect(page.get_by_test_id("selection-option-with_backing")).to_be_visible()

        finally:
            browser.close()


@pytest.mark.integration
def test_combined_review_flow_navigate_to_instrumental(review_server):
    """Test navigating from lyrics review to instrumental review."""
    fixture, base_url = review_server

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            # Start at lyrics review
            page.goto(f"{base_url}/app/jobs/local/review", wait_until="networkidle")

            # Wait for page to load
            expect(page.locator("text=Lyrics Transcription Review")).to_be_visible(timeout=10000)

            # First click "Preview Video" to open the modal
            preview_button = page.locator("button:has-text('Preview Video')")
            expect(preview_button).to_be_visible(timeout=5000)
            preview_button.click()

            # Wait for modal to appear, then click "Proceed to Instrumental Review"
            proceed_button = page.locator("button:has-text('Proceed to Instrumental Review')")
            expect(proceed_button).to_be_visible(timeout=10000)
            proceed_button.click()

            # Should navigate to instrumental review
            page.wait_for_url(f"**/app/jobs/local/instrumental", timeout=10000)

            # Instrumental review should show
            expect(page.locator("text=Instrumental Review").first).to_be_visible(timeout=10000)

        finally:
            browser.close()


@pytest.mark.integration
def test_combined_review_flow_submit(review_server):
    """Test completing the full combined review flow."""
    fixture, base_url = review_server
    fixture.reset_state()  # Reset from any previous test

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            # Go directly to instrumental review (simulating user navigated there)
            page.goto(f"{base_url}/app/jobs/local/instrumental", wait_until="networkidle")

            # Wait for page to load
            expect(page.locator("text=Instrumental Review").first).to_be_visible(timeout=10000)

            # Select "Clean Instrumental" option (use test-id for specific element)
            clean_option = page.get_by_test_id("selection-option-clean")
            expect(clean_option).to_be_visible(timeout=5000)
            clean_option.click()

            # Find and click submit button
            submit_button = page.locator("button:has-text('Confirm & Continue')")
            expect(submit_button).to_be_visible(timeout=5000)
            submit_button.click()

            # Should show success message
            expect(page.locator("text=Selection Submitted")).to_be_visible(timeout=10000)

            # Give server time to process
            time.sleep(1)

            # Verify server received the selection
            assert fixture.review_completed, "Review should be marked as completed"
            assert fixture.instrumental_selection == "clean", f"Expected 'clean' but got '{fixture.instrumental_selection}'"

        finally:
            browser.close()


@pytest.mark.integration
def test_combined_review_flow_full_journey(review_server):
    """Test the complete user journey: lyrics review -> instrumental selection -> submit."""
    fixture, base_url = review_server
    fixture.reset_state()  # Reset from any previous test

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            # Step 1: Start at lyrics review
            page.goto(f"{base_url}/app/jobs/local/review", wait_until="networkidle")
            expect(page.locator("text=Lyrics Transcription Review")).to_be_visible(timeout=10000)

            # Step 2: Click "Preview Video" to open the modal
            preview_button = page.locator("button:has-text('Preview Video')")
            expect(preview_button).to_be_visible(timeout=5000)
            preview_button.click()

            # Step 2b: Click "Proceed to Instrumental Review" in the modal
            proceed_button = page.locator("button:has-text('Proceed to Instrumental Review')")
            expect(proceed_button).to_be_visible(timeout=10000)
            proceed_button.click()

            # Step 3: Wait for instrumental review page
            page.wait_for_url(f"**/app/jobs/local/instrumental", timeout=10000)
            expect(page.locator("text=Instrumental Review").first).to_be_visible(timeout=10000)

            # Step 4: Select instrumental option (use test-id for specific element)
            with_backing = page.get_by_test_id("selection-option-with_backing")
            expect(with_backing).to_be_visible(timeout=5000)
            with_backing.click()

            # Step 5: Submit
            submit_button = page.locator("button:has-text('Confirm & Continue')")
            expect(submit_button).to_be_visible(timeout=5000)
            submit_button.click()

            # Step 6: Verify success
            expect(page.locator("text=Selection Submitted")).to_be_visible(timeout=10000)

            # Give server time to process
            time.sleep(1)

            # Verify server state
            assert fixture.review_completed, "Review should be completed"
            assert fixture.instrumental_selection == "with_backing", \
                f"Expected 'with_backing' but got '{fixture.instrumental_selection}'"

        finally:
            browser.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
